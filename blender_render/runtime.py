"""Local service lifecycle and verified runtime discovery."""
from __future__ import annotations

import errno
import fcntl
import math
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from .config import VERSION, config_root, load_config, save_config


@contextmanager
def file_lock(path, *, blocking=True, deadline=None):
    """Lock a private file, optionally bounded by an absolute monotonic deadline."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a") as handle:
        if deadline is None or not blocking:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        else:
            deadline = float(deadline)
            if not math.isfinite(deadline):
                raise ValueError("file lock deadline must be finite")
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                        raise
                    remaining = float(deadline) - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"Timed out waiting for file lock: {path}") from None
                    time.sleep(min(0.05, remaining))
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def write_private(path, data):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w") as stream:
        os.chmod(temp, 0o600)
        stream.write(data)
    os.replace(temp, path)


def controller_root(worker_profile=''):
    if worker_profile:
        import re
        if not re.fullmatch(r'[A-Za-z0-9_-]+', worker_profile):
            raise ValueError('Invalid worker profile name')
        return config_root() / 'controllers' / worker_profile
    return config_root() / 'local'


def existing_local(*, worker_profile=''):
    """Read cached connection only. Never start, repair, probe or allocate."""
    try:
        return json.loads((controller_root(worker_profile) / 'session.json').read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError('No existing controller; read-only access cannot start it.') from exc


def ensure_local(*, blender="", image="", attach_only=False, worker_profile='', pymol_python='', **ignored):
    from .engine import protect
    seal = protect('startup')
    root = controller_root(worker_profile)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_file = root / "session.json"
    from .client import Client
    from .engine import engine_identity, require_compatible
    with file_lock(root / "startup.lock"):
        try:
            state = json.loads(state_file.read_text())
            token = Path(state["token_file"]).read_text().strip()
            health = Client(state["url"], token, 2).request("GET", "/v1/capabilities")
            if state.get("version") != VERSION:
                raise RuntimeError("A previous service version is running. Run blender-render service stop before upgrading.")
            try:
                require_compatible(engine_identity(), health.get('controller_declaration'), [])
            except ValueError as exc:
                raise RuntimeError('Existing controller build differs; drain and stop it before upgrading') from exc
            if image and state.get("image", "") != image:
                raise RuntimeError("Local service uses a different runtime. Stop it before switching Apptainer images.")
            if pymol_python and state.get('pymol_python', '') != pymol_python:
                raise RuntimeError('Local controller uses a different PyMOL runtime. Stop it before switching interpreters.')
            return state
        except RuntimeError as exc:
            from .client import ClientError
            if not isinstance(exc, ClientError):
                raise
        except (OSError, ValueError, KeyError):
            pass
        if attach_only:
            raise RuntimeError("No ready local session was found.")
        config = load_config()
        python = config.get("service_python", sys.executable)
        import tempfile
        captured = seal.capture(Path(tempfile.mkdtemp(prefix='engine-', dir=root))/'blender_render')
        env = os.environ.copy()
        env['PYTHONPATH'] = str(captured.parent) + os.pathsep + env.get('PYTHONPATH', '')
        protect('startup')
        check = subprocess.run([python, "-c", "import fastapi, uvicorn; import blender_render"],
                               cwd=captured.parent, env=env, capture_output=True)
        if check.returncode:
            raise RuntimeError("Install the service extra and run setup: python3.11 -m pip install 'pymol-plugin-bridge[server]'; blender-render setup")
        token_file = root / "token"
        if not token_file.exists():
            write_private(token_file, secrets.token_urlsafe(32))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        state = {"url": f"http://127.0.0.1:{port}", "token_file": str(token_file), "version": VERSION,
                 "session_id": secrets.token_hex(12), "started_at": time.time(), "image": image,
                 'pymol_python': pymol_python}
        command = [python, "-m", "blender_render.cli", "serve", "--port", str(port), "--root", str(root), "--token-file", str(token_file), "--idle-timeout", "900"]
        executable = blender or config.get("blender", "")
        if executable:
            command += ["--blender", executable]
        if image:
            command += ["--image", image]
        if worker_profile:
            command += ['--worker-profile', worker_profile]
        if pymol_python:
            command += ['--pymol-python', pymol_python]
        env["BLENDER_RENDER_SESSION_ID"] = state["session_id"]
        state['engine_root'] = str(captured.parent)
        protect('startup')
        with (root / "service.log").open("a") as log:
            process = subprocess.Popen(command, cwd=captured.parent, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        state["pid"] = process.pid
        write_private(state_file, json.dumps(state))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Local service exited. See {root / 'service.log'}")
            try:
                Client(state["url"], token_file.read_text().strip(), 1).request("GET", "/health")
                return state
            except Exception:
                time.sleep(.2)
        process.terminate()
        raise RuntimeError(f"Local service startup timed out. See {root / 'service.log'}")


class LocalFileConverter:
    """Deferred conversion of the authenticated local user's trusted files.

    Never install this converter on a public upload service; those require the
    existing isolated converter. Uses the same export as the former CLI path.
    """
    def __init__(self, python=''):
        self.python = python

    def __call__(self, data, suffix, *, selection='(all)'):
        import tempfile
        if suffix not in {'.pse', '.cif', '.mmcif', '.pdb'}:
            raise ValueError('Unsupported molecular file')
        with tempfile.TemporaryDirectory(prefix='blender-admitted-export-') as root:
            path = Path(root) / ('structure' + suffix)
            path.write_bytes(data)
            return export_file(path, python=self.python, selection=selection)


def export_file(path, *, python="", selection="(all)", process_threads=None):
    import tempfile
    from .engine import protect
    from .config import process_thread_environment
    thread_environment = process_thread_environment(process_threads)
    seal = protect('conversion')
    config = load_config()
    executable = python or config.get("pymol_python", sys.executable)
    with tempfile.TemporaryDirectory(prefix="blender-export-") as temp:
        destination = Path(temp) / "payload.json"
        captured = seal.capture(Path(temp)/'blender_render')
        env = os.environ.copy()
        env.update(thread_environment)
        env['PYTHONPATH'] = str(captured.parent) + os.pathsep + env.get('PYTHONPATH', '')
        protect('conversion')
        result = subprocess.run([executable, str(captured/'export.py'), str(Path(path).expanduser().resolve()), str(destination), "--selection", selection],
                                cwd=captured.parent, env=env, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError("PyMOL export failed. Configure --pymol-python with an interpreter containing pymol2.\n" + result.stderr[-3000:])
        return json.loads(destination.read_text())


def diagnose(blender="", *, probe=False):
    from .core import _detect_host_blender_executable
    blender = blender or load_config().get("blender") or _detect_host_blender_executable()
    if not probe:
        from .config import runtime_versions
        return {'blender_executable': blender, 'exists': Path(blender).is_file() if blender else False,
                'expected_versions': runtime_versions(), 'runtime_verified': False,
                'limitation': 'Read-only inspection; use explicit setup to execute a runtime probe.'}
    env = os.environ.copy()
    site = load_config().get("runtime_site", str(config_root() / "runtime" / "site"))
    env["PYTHONPATH"] = site + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([blender, "--background", "--python-use-system-env", "--python-exit-code", "1", "--python", str(Path(__file__).with_name("runtime_probe.py"))], env=env, capture_output=True, text=True, timeout=90)
    prefix = "BLENDER_RENDER_RUNTIME="
    lines = [line[len(prefix):] for line in result.stdout.splitlines() if line.startswith(prefix)]
    if result.returncode or not lines:
        raise RuntimeError("Runtime is incomplete. Run blender-render setup --install-runtime.\n" + result.stderr[-1500:])
    versions = json.loads(lines[-1])
    from .config import runtime_versions
    lock = runtime_versions()
    drift = {key: value for key, value in versions.items() if value != lock[key]}
    return {"blender_executable": blender, "versions": versions, "matches_lock": not drift, "mismatches": drift}
