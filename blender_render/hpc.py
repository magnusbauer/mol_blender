"""Mac/desktop cluster adapter. All GPU service traffic uses an SSH tunnel."""
from __future__ import annotations
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

from .config import config_root
from .runtime import file_lock, write_private


def validate_profile(profile):
    if profile.get('version') != 2 or not isinstance(profile.get('launcher'), dict):
        raise ValueError('HPC profile migration required: set version=2 and an explicit launcher (slurm_sbatch or ipd_qlogin)')
    launcher = profile['launcher']
    if set(launcher) != {'kind', 'arguments'} or launcher['kind'] not in {'slurm_sbatch', 'ipd_qlogin'}:
        raise ValueError('Unsupported HPC launcher configuration')
    supported = {('slurm_sbatch','slurm'), ('slurm_sbatch','ssh'),
                 ('ipd_qlogin','slurm'), ('ipd_qlogin','ssh')}
    if not isinstance(profile.get('transport'), str) or (launcher['kind'], profile['transport']) not in supported:
        raise ValueError('Unsupported or missing HPC launcher/transport combination; choose slurm or ssh explicitly')
    if 'allocation' in profile:
        raise ValueError('HPC profile migration required: move allocation to launcher.arguments')
    if not isinstance(launcher['arguments'], list) or any(not isinstance(x, str) or '\x00' in x or '\n' in x for x in launcher['arguments']):
        raise ValueError('launcher.arguments must be an argv list')
    if any(x == '--wrap' or x.startswith('--wrap=') for x in launcher['arguments']):
        raise ValueError('Launcher arguments cannot replace the worker script with --wrap')
    for key in ("host", "remote_python", "remote_root", "image", "registry"):
        if not isinstance(profile.get(key), str) or not profile[key].strip() or "\n" in profile[key] or "\x00" in profile[key]:
            raise ValueError(f"HPC profile requires {key}")
    if profile["host"].startswith("-") or not re.fullmatch(r"[A-Za-z0-9_.@:-]+", profile["host"]):
        raise ValueError("Invalid SSH host")
    if not isinstance(profile.get("allocation", []), list) or any(not isinstance(x, str) or "\x00" in x for x in profile.get("allocation", [])):
        raise ValueError("allocation must be a list of SLURM arguments")
    return profile


def remote(profile, action, **options):
    validate_profile(profile)
    from .engine import remote_bootstrap, protect
    if action == 'start':
        from .engine import engine_identity
        options.setdefault('expected_engine', profile.get('expected_engine') or engine_identity())
    passive = action in {'status', 'stop', 'ipc_evidence'}
    code = remote_bootstrap(Path(profile['remote_root'])/'blender_render', check=not passive,
                            administrative=action in {'status', 'stop', 'attach'})
    if not passive: protect('submission' if action == 'start' else 'transfer')
    command = shlex.join([profile["remote_python"], '-c', code, action])
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", profile["host"], command],
        input=json.dumps({"profile": profile, **options}), capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError("Cluster session failed: " + result.stderr[-2000:])
    return json.loads(result.stdout)


def connect(name, profile, *, intention=None, attach_only=None, timeout=600, **ignored):
    from .client import ConnectionIntention, connection_intention
    intention = connection_intention(intention, attach_only=attach_only) or ConnectionIntention.START_OR_ALLOCATE
    validate_profile(profile)
    if intention == ConnectionIntention.INSPECT_CACHED:
        return existing_connection(name)
    attach_only = intention in {
        ConnectionIntention.ATTACH_EXISTING,
        ConnectionIntention.SUBMIT_EXISTING,
    }
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("Invalid profile name")
    root = config_root() / "hpc" / name
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    from .client import Client
    from .engine import engine_identity, require_compatible, EngineMismatch
    expected = profile.get('expected_engine') or engine_identity()
    if not attach_only:
        declaration = remote(profile, 'identity')
        if declaration.get('python_supported') is not True:
            raise EngineMismatch('Unsupported remote Python capabilities; explicit setup required')
        if profile.get('expected_commit') and (declaration.get('commit') != profile['expected_commit'] or declaration.get('tracked_clean') is not True):
            raise RuntimeError('Remote checkout must be clean at the reviewed commit before allocation')
        require_compatible(expected, declaration.get('engine'), [])
    def negotiate(health):
        from .engine import require_retrieval
        if intention == ConnectionIntention.ATTACH_EXISTING:
            require_retrieval(health)
        else:
            require_compatible(expected, health.get('controller_declaration'), [])
    with file_lock(root / "attach.lock"):
        try:
            previous = json.loads((root / "connection.json").read_text())
            current = remote(profile, "status")
            if current.get("status") != "ready" or current.get("session_id") != previous.get("session_id"):
                raise RuntimeError("Cached allocation ended")
            token = Path(previous["token_file"]).read_text().strip()
            if not previous.get('controller_token_file'):
                raise RuntimeError('Cached connection lacks controller credentials; attach again')
            health = Client(previous["url"], token, timeout=2).request("GET", "/v1/capabilities")
            negotiate(health)
            return previous
        except EngineMismatch:
            raise
        except (OSError, ValueError, RuntimeError):
            pass
        start = remote(profile, 'attach' if attach_only else 'start')
        deadline = time.monotonic() + timeout
        recoveries = 0
        while start["status"] != "ready":
            if start['status'] in {'submission_unknown', 'unavailable'}:
                raise RuntimeError('Allocation outcome unknown; inspect the existing allocation before starting again')
            if start["status"] in {"failed", "absent"}:
                if attach_only or recoveries >= 1:
                    raise RuntimeError(start.get("error", "No ready GPU session. Use mode='local' or check the cluster profile."))
                recoveries += 1
                start = remote(profile, 'start')
                continue
            if time.monotonic() >= deadline:
                raise RuntimeError("GPU allocation is still pending. Reconnect to the same profile; use mode='local' to render now.")
            print("Waiting for interactive GPU allocation...", file=sys.stderr)
            time.sleep(3)
            start = remote(profile, 'attach' if attach_only else 'start') if start['status'] == 'stopping' else remote(profile, 'status')
        if 'token' not in start:
            # Read-only status deliberately omits credentials. Only this
            # explicit connection flow requests them, without allocating again.
            attached = remote(profile, 'attach')
            if (attached.get('status') != 'ready' or not attached.get('token')
                    or attached.get('session_id') != start.get('session_id')):
                raise RuntimeError('GPU session changed before authenticated attach; reconnect explicitly.')
            start = attached
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            local_port = probe.getsockname()[1]
        # A login-host forward to node:port cannot reach the node's loopback.
        # Terminate SSH on the GPU node itself, via ProxyJump on the login host.
        node = start["node"]
        user = start["user"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", node) or not re.fullmatch(r"[A-Za-z0-9_.-]+", user):
            raise RuntimeError("Invalid node identity in cluster registry")
        command = ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=3", "-J", profile["host"], "-L", f"127.0.0.1:{local_port}:127.0.0.1:{int(start['port'])}", f"{user}@{node}"]
        if profile.get("transport") == "slurm":
            from .engine import script_code
            proxy_code = script_code('hpc_proxy.py')
            remote_command = ["srun", "--jobid=" + str(start["job_id"]), "--overlap", "-N1", "-n1", profile["remote_python"],
                '-c', proxy_code, "stdio", "--port", str(start["port"])]
            if start.get('cluster'):
                remote_command[1:1] = ['--clusters=' + start['cluster']]
            ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=20", profile["host"], shlex.join(remote_command)]
            write_private(root / "proxy-command.json", json.dumps(ssh))
            # Pass the proxy's SSH argv by value too; no mutable command file
            # may redirect an already protected tunnel to another destination.
            command = [sys.executable, '-c', proxy_code, "listen", "--port", str(local_port), "--command-json", json.dumps(ssh)]
        token_path = root / "token"
        write_private(token_path, start.pop("token"))
        controller_token_path = root / 'controller-token'
        if not start.get('controller_token'):
            raise RuntimeError('Worker lacks separate controller authentication; explicit upgrade required')
        write_private(controller_token_path, start.pop('controller_token'))
        with (root / "tunnel.log").open("a") as log:
            from .engine import protect
            protect('transfer')
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        connection = {**start, "url":f"http://127.0.0.1:{local_port}", "token_file":str(token_path), "tunnel_pid":process.pid}
        connection['controller_token_file'] = str(controller_token_path)
        for _ in range(50):
            try:
                health = Client(connection["url"], token_path.read_text(), 1).request("GET", "/v1/capabilities")
                negotiate(health)
                connection['cached_observation'] = health
                connection['observed_at'] = time.time()
                write_private(root / "connection.json", json.dumps(connection))
                print("Interactive GPU session ready", file=sys.stderr)
                return connection
            except EngineMismatch:
                process.terminate()
                raise
            except Exception:
                if process.poll() is not None:
                    break
                time.sleep(.2)
        process.terminate()
        raise RuntimeError(f"SSH tunnel could not reach the GPU service; inspect {root / 'tunnel.log'}. Use mode='local' as fallback.")


def existing_connection(name):
    """An unavailable cached tunnel is reported, never repaired by a read."""
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name in {'.', '..'}:
        raise ValueError('Invalid profile name')
    try:
        return json.loads((config_root() / 'hpc' / name / 'connection.json').read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError('No existing HPC connection; read-only operations cannot allocate or create a tunnel.') from exc


def cached_observation(name):
    from .engine import sanitized_observation
    try:
        state = existing_connection(name)
    except (RuntimeError, ValueError):
        return None
    result = sanitized_observation(state.get('cached_observation'))
    if result is None:
        return None
    observed = state.get('observed_at')
    if type(observed) in (int, float) and 0 <= observed <= time.time():
        result['observed_at'] = observed
        result['age_seconds'] = max(0, time.time()-observed)
    result['source'] = 'connection_cache'
    return result


def stop(name, profile):
    return remote(profile, "stop")
