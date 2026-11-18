"""Private container-side dispatch to the existing worker; no queue or HTTP API.

systemd owns this process and every Blender child. Exiting, cancellation or a
watchdog expiry retires the whole container. Job identity/results remain in the
existing spool; interrupted work is never replayed on startup.
"""
import argparse
import fcntl
import os
from pathlib import Path
import shutil
import signal
import socketserver
import tempfile
import threading

from . import worker
from .protocol import job_id
from .. import warm_worker
from ..warm_protocol import receive, send


class Dispatcher:
    def __init__(self, spool, key, walltime):
        self.spool, self.key, self.walltime = spool, key, walltime
        self.lock = threading.Lock()
        self.active = None
        self.stopping = threading.Event()

    def handle(self, message):
        if message.get('id') != self.key:
            raise ValueError('Warm service source or policy changed; stop it before starting the new configuration')
        action = message.get('action')
        expected = {'id', 'action', 'job_id'} if action == 'submit' else {'id', 'action'}
        if set(message) != expected or action not in ('status', 'submit'):
            raise ValueError('Invalid warm service command')
        with self.lock:
            if action == 'status':
                session = warm_worker.status()
                return {'status': 'stopping' if self.stopping.is_set() else 'busy' if self.active else 'ready',
                        'job_id': self.active, 'warm_worker': {name: session.get(name) for name in
                                                           ('id', 'pid', 'blender_pid', 'completed')}}
            identifier = job_id(message['job_id'])
            directory = self.spool / identifier
            if directory.is_symlink() or not directory.is_dir() or not (directory / 'job.json').is_file():
                raise ValueError('Unknown warm job')
            if self.stopping.is_set():
                raise RuntimeError('Warm service is stopping; the job was not replayed')
            if self.active == identifier or (directory / 'result.json').exists():
                return {'accepted': identifier}
            if self.active:
                raise RuntimeError('Warm service is busy')
            if (directory / 'started.json').exists():
                raise RuntimeError('Previous execution was interrupted; no automatic rerender')
            accepted = threading.Event()
            self.active = identifier
            threading.Thread(target=self.render, args=(directory, accepted), daemon=True).start()
        # Ack only after the existing worker wrote started.json (or a terminal
        # result). A lost response is recovered through these same records.
        if not accepted.wait(10):
            raise TimeoutError('Warm job acceptance is not confirmed; reconcile the existing job')
        return {'accepted': identifier}

    def render(self, directory, accepted):
        watchdog = threading.Timer(self.walltime, lambda: os._exit(124))
        watchdog.daemon = True
        watchdog.start()
        succeeded = False
        try:
            # A prior worker may have retired itself. Any new worker's snapshot
            # must live in service scratch, never a soon-to-be-deleted job scratch.
            warm_worker.start(idle_timeout=0)
            succeeded = worker.run(directory, warm=True, accepted=accepted) == 0
        finally:
            watchdog.cancel()
            with self.lock:
                self.active = None
                if not succeeded:
                    self.stopping.set()
            accepted.set()


def serve(spool, key, identity, walltime):
    from ..config import load_config
    from ..engine import generate_manifest
    from .jsonio import atomic_json
    os.umask(0o077)
    if generate_manifest(Path(__file__).resolve().parents[1])['engine_build'] != identity:
        raise ValueError('Gateway and image renderer builds differ')
    root = spool / '.warm'
    root.mkdir(mode=0o700, exist_ok=True)
    with (root / 'service.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # systemd has reaped the previous container before starting this one.
        for old in root.glob('session-*'):
            if old.is_dir() and not old.is_symlink():
                shutil.rmtree(old)
        settings = load_config()  # Read the baked runtime before changing config home.
        with tempfile.TemporaryDirectory(prefix='session-', dir=root) as scratch:
            os.environ['BLENDER_RENDER_HOME'] = str(Path(scratch) / 'config')
            os.environ['TMPDIR'] = scratch
            tempfile.tempdir = scratch
            (Path(scratch) / 'config').mkdir(mode=0o700)
            atomic_json(Path(scratch) / 'config/config.json', settings)
            dispatcher = Dispatcher(spool, key, walltime)
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, lambda *_: dispatcher.stopping.set())
            socket_path = root / 'control.sock'
            socket_path.unlink(missing_ok=True)

            class Handler(socketserver.BaseRequestHandler):
                def handle(self):
                    self.request.settimeout(15)
                    try:
                        result = dispatcher.handle(receive(self.request))
                    except Exception as exc:
                        result = {'error': str(exc)}
                    try:
                        send(self.request, result)
                    except OSError:
                        pass  # The accepted job continues after a lost response.

            # Control requests are short; rendering runs on just one worker thread.
            class Server(socketserver.UnixStreamServer):
                timeout = .2

            try:
                warm_worker.start(idle_timeout=0)
                with Server(str(socket_path), Handler) as server:
                    socket_path.chmod(0o600)
                    while not dispatcher.stopping.is_set():
                        server.handle_request()
            finally:
                socket_path.unlink(missing_ok=True)
                warm_worker.stop(force=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key', required=True)
    parser.add_argument('--engine-build', required=True)
    parser.add_argument('--walltime', type=int, required=True)
    args = parser.parse_args()
    serve(Path('/job'), args.key, args.engine_build, args.walltime)


if __name__ == '__main__':
    main()
