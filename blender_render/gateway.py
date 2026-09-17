"""Lab-owned OAuth gateway: private structures never transit the shared service."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import signal
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .client import configured_client, ConnectionIntention
from .engine import protect, RestartRequired
from .jobs import JobError


class GatewayBackend:
    """Forward the durable lab queue to a configured worker, preserving retries."""
    paid = False

    def __init__(self, store, profile):
        self.store, self.profile, self.active = store, profile, None
        self.progress = None

    def set_progress(self, callback):
        self.progress = callback

    def render(self, request, identifier, cancelled):
        from .execution import request_snapshot
        from .delivery import _recover
        from .telemetry import measure
        from .engine import EngineMismatch
        with self.store.lock:
            row = dict(self.store.db.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone())
        child_id = row['remote_id']
        completed = row['remote_completed_at'] is not None
        owner = row['owner']
        protect('transfer' if child_id or row['remote_phase'] else 'submission')
        client = configured_client(self.profile, intention=(ConnectionIntention.ATTACH_EXISTING
            if child_id or row['remote_phase'] else ConnectionIntention.START_OR_ALLOCATE), controller_owner=owner)
        key = 'gateway-' + identifier
        sink = lambda record: self.store.add_timings(identifier, [record])
        try:
            if not child_id:
                if row['remote_phase']:
                    # Lost submission response: look up the admitted child only.
                    job = client.request('GET', '/internal/v1/operations/' + key)
                else:
                    if cancelled():
                        raise RuntimeError('Lab render cancelled before child submission')
                    self.store.remote_checkpoint(identifier, 'child_observation_pending')
                    envelope = {**request_snapshot(request), 'controller_id':'controller',
                                'owner_id':owner, 'operation_id':key}
                    with measure(sink, 'allocation', 'gateway'):
                        protect('submission')
                        job = client.request('POST', '/internal/v1/resolved', envelope)
                child_id = job['job_id']
                self.store.set_remote_id(identifier, child_id)
                # Submission permission ends when a child is known. Every
                # subsequent repair is attachment to that allocation only.
                client = configured_client(self.profile, intention=ConnectionIntention.ATTACH_EXISTING,
                                           controller_owner=owner)
            self.active = client, child_id, completed
            deadline = time.monotonic() + request.get('job_timeout_seconds', 1800)
            recovery_requested = False
            while not completed:
                # Observe completion before cancellation so a late cancellation
                # cannot turn delivery failure into a child render failure.
                state = client.status(child_id)
                if state.get('status') in {'done', 'delivered', 'expired'}:
                    completed = True
                    self.store.remote_checkpoint(identifier, 'child_transfer_pending', completed=True)
                    self.active = client, child_id, True
                    break
                if state['status'] in {'failed', 'cancelled', 'deleted'}:
                    raise ChildRenderFailed('Child render ' + state['status'])
                if state['status'] == 'transfer_pending':
                    if recovery_requested:
                        raise TransferPending('child_observation_pending')
                    protect('transfer')
                    client.request('POST', '/v1/jobs/' + child_id + '/recover-transfer')
                    recovery_requested = True
                if cancelled():
                    client.cancel(child_id)
                    raise ChildRenderFailed('Child render cancelled')
                if time.monotonic() >= deadline:
                    raise TransferPending('child_observation_pending')
                if self.progress and state.get('stage') in {'waiting_for_compute', 'scene_build', 'rendering'}:
                    self.progress(identifier, state['stage'])
                time.sleep(.5)
            if cancelled():
                raise TransferPending('child_transfer_pending')
            root = self.store.root / 'child-results' / identifier
            protect('transfer')
            with measure(sink, 'transfer', 'gateway', row['transfer_attempt'] + 1):
                result = _recover(client, child_id, root / 'render.png', root / 'receipt.json',
                                  before_ack=self.store.sync, strict_ack=True,
                                  before_action=lambda:protect('transfer'),
                                  attempt=row['transfer_attempt'] + 1,
                                  on_child_timings=lambda records:self.store.import_child_timings(identifier, child_id, records),
                                  expected_execution={'job_id':child_id, 'bundle_sha256':request['bundle_sha256'],
                                      'render_spec_sha256':request['render_spec']['sha256'],
                                      'engine_build':request['expected_engine']['engine_build']})
            self.store.add_timings(identifier, result.get('delivery_timings', []))
            if not result['complete']:
                raise TransferPending('child_transfer_pending')
            response = {'status':'completed', 'manifest':result['manifest'], 'runtime_seconds':None, 'timings':[]}
            from .artifacts import FIELDS
            for name, key in FIELDS.items():
                if name in result['outputs']:
                    response[key] = base64.b64encode(Path(result['outputs'][name]).read_bytes()).decode()
            from .animation import required_names
            if any(FIELDS[name] not in response for name in required_names(request.get('style_doc', {}))):
                raise TransferPending('child_transfer_pending')
            return response
        except RestartRequired:
            raise
        except EngineMismatch as exc:
            raise TransferPending('execution_incompatible') from exc
        except ChildRenderFailed:
            raise
        except Exception as exc:
            raise TransferPending('child_transfer_pending' if completed else 'child_observation_pending') from exc
        finally:
            self.active = None

    def stop(self):
        if self.active:
            client, child_id, completed = self.active
            if not completed:
                try:
                    state = client.status(child_id)
                    if state['status'] in {'queued', 'running'}:
                        client.cancel(child_id)
                except Exception:
                    # A failed observation retains the child identity; it is
                    # never permission to launch a replacement.
                    pass


class TransferPending(RuntimeError):
    def __init__(self, phase):
        self.phase = phase
        super().__init__('Child operation retained; recover observation or transfer')


class ChildRenderFailed(RuntimeError):
    pass


class ApptainerConverter:
    """Short-lived, networkless converter with no host home or credentials.

    The host must permit Apptainer's network and PID isolation. Fail closed
    when isolation is unavailable; never retry conversion on the host Python.
    """
    def __init__(self, image, *, executable='apptainer'):
        self.image = str(Path(image).expanduser().resolve())
        self.executable = executable
        if not Path(self.image).is_file():
            raise ValueError('Configure an existing converter SIF image')
        self.lock = threading.Lock()

    def __call__(self, data, suffix, *, selection='(all)'):
        if suffix not in {'.pse','.cif','.mmcif','.pdb'} or len(data) > 50 * 1024**2:
            raise JobError('Invalid molecular upload')
        with self.lock, tempfile.TemporaryDirectory(prefix='blender-convert-') as directory:
            root = Path(directory)
            (root / ('input' + suffix)).write_bytes(data)
            seal = protect('conversion')
            seal.capture(root/'engine'/'blender_render')
            command = [self.executable, 'exec', '--containall', '--cleanenv', '--no-eval',
                       '--net', '--network', 'none', '--no-home', '--no-mount', 'hostfs,bind-paths,home,cwd',
                       '--writable-tmpfs', '--bind', str(root) + ':/work', '--pwd', '/work',
                       '--env', 'PYTHONPATH=/work/engine',
                       self.image, '/usr/bin/python3', '/work/engine/blender_render/export.py',
                       '/work/input' + suffix, '/work/payload.json', '--selection', selection]
            protect('conversion')
            process = subprocess.Popen(command, cwd=directory, env={'PATH':os.defpath, 'QT_QPA_PLATFORM':'offscreen'},
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                process.wait(timeout=90)
                output = root / 'payload.json'
                if process.returncode:
                    raise JobError('Isolated conversion failed. Check the file and Apptainer isolation support.')
                # Treat the converter's output as hostile, including symlinks
                # and a writer racing the size check. Never follow a link from
                # the sandbox's bind mount into the gateway host filesystem.
                try:
                    descriptor = os.open(output, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(descriptor, 'rb') as stream:
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                            raise ValueError('Output is not a regular file')
                        payload = stream.read(50 * 1024**2 + 1)
                    if len(payload) > 50 * 1024**2:
                        raise ValueError('Output exceeds size limit')
                    return json.loads(payload)
                except (OSError, ValueError) as exc:
                    raise JobError('The isolated converter did not produce a valid structure.') from exc
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def serve_gateway(args):
    import uvicorn
    from .api import create_app, Downloads
    from .auth import github_provider
    from .jobs import JobStore, Policy
    from .mcp_server import create_http
    from .runtime import file_lock
    from .service import RenderService
    from .client import Client
    if args.host not in {'127.0.0.1','::1','localhost'}:
        raise ValueError('Run the gateway behind a TLS reverse proxy on loopback')
    if not args.public_url.startswith('https://'):
        raise ValueError('The lab OAuth gateway requires its public HTTPS URL')
    if not args.allowed_user:
        raise ValueError('Specify at least one permitted GitHub numeric user ID with --allowed-user')
    if any(not user.isdigit() for user in args.allowed_user):
        raise ValueError('Use stable numeric GitHub user IDs in the lab allowlist')
    credentials = json.loads(args.credentials.expanduser().read_text())
    required = ('GITHUB_CLIENT_ID','GITHUB_CLIENT_SECRET','BLENDER_RENDER_ADMIN_TOKEN',
                'BLENDER_RENDER_DOWNLOAD_KEY','BLENDER_RENDER_ENCRYPTION_KEY','BLENDER_RENDER_JWT_KEY')
    if any(not credentials.get(key) for key in required):
        raise ValueError('The gateway credentials file is incomplete')
    with file_lock(args.root / 'service.lock', blocking=False):
        store = JobStore(args.root, Policy(max_job_seconds=args.job_timeout))
        backend = GatewayBackend(store, args.profile)
        service = RenderService(store, backend)
        provider = github_provider(store, client_id=credentials['GITHUB_CLIENT_ID'], client_secret=credentials['GITHUB_CLIENT_SECRET'],
            base_url=args.public_url, encryption_key=credentials['BLENDER_RENDER_ENCRYPTION_KEY'],
            jwt_key=credentials['BLENDER_RENDER_JWT_KEY'], allowed_users=set(args.allowed_user))
        downloads = Downloads(credentials['BLENDER_RENDER_DOWNLOAD_KEY'], args.public_url)
        converter = ApptainerConverter(args.converter_image)
        mcp = create_http(service, auth=provider, downloads=downloads, converter=converter)
        app = create_app(service, token=credentials['BLENDER_RENDER_ADMIN_TOKEN'], public_url=args.public_url,
            signing_key=credentials['BLENDER_RENDER_DOWNLOAD_KEY'], auth_provider=provider, mcp_server=mcp, converter=converter, tokens={})
        server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level='warning'))
        app.state.shutdown = lambda: setattr(server, 'should_exit', True)
        try:
            server.run()
        finally:
            service.stop()
            store.close()
