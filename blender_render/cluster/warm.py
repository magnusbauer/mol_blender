"""Administrator-selected persistent Apptainer worker behind the existing job API."""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import subprocess
import time

from .jsonio import digest_file, json_bytes
from .protocol import job_id
from .slurm import SubmissionUnknown
from .workstation import Workstation
from ..warm_protocol import request


class Warm(Workstation):
    def __init__(self, config):
        super().__init__(config)
        from ..engine import generate_manifest
        self.identity = generate_manifest(Path(__file__).resolve().parents[1])['engine_build']
        policy = {name: str(getattr(config, name)) for name in
                  ('image', 'image_sha256', 'runtime_home', 'cpus', 'memory_mb',
                   'gpus', 'gpu_index', 'walltime_seconds')}
        self.key = hashlib.sha256(json_bytes({**policy, 'engine_build': self.identity})).hexdigest()
        self.root = config.spool / '.warm'
        self.service = self.prefix + 'warm.service'

    @contextmanager
    def locked(self):
        for path in (self.config.spool, self.root):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
                raise ValueError('Warm spool directories must be private and owned by the service account')
        with (self.root / 'control.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def rpc(self, action, **fields):
        # Linux procfs lets even a long host spool path address the short socket
        # bound inside /job. No process-wide chdir or public TCP listener.
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            return request({'socket': f'/proc/self/fd/{fd}/control.sock', 'id': self.key},
                           action, timeout=15, **fields)
        except (ValueError, RuntimeError) as exc:
            raise OSError(str(exc)) from exc
        finally:
            os.close(fd)

    def status(self):
        values = self.unit_properties(self.service)
        if values.get('LoadState') == 'not-found' or values.get('ActiveState') in ('inactive', 'failed'):
            return {'status': 'stopped', 'unit': self.service}
        try:
            return {**self.rpc('status'), 'unit': self.service}
        except (FileNotFoundError, ConnectionRefusedError):
            return {'status': 'starting', 'unit': self.service}

    def start(self):
        with self.locked():
            return self._start()

    def _start(self):
        deadline = time.monotonic() + 220
        launched = False
        while time.monotonic() < deadline:
            values = self.unit_properties(self.service)
            if values.get('LoadState') == 'not-found':
                if launched:
                    raise OSError('Warm service exited during startup; inspect .warm/worker.log')
                if digest_file(self.config.image) != self.config.image_sha256:
                    raise ValueError('SIF checksum mismatch')
                command = self.container_command(self.config.spool, module='blender_render.cluster.warm_service')
                command += ['--key', self.key, '--engine-build', self.identity,
                            '--walltime', str(self.config.walltime_seconds)]
                # The service stays up while idle. A per-request watchdog inside
                # it applies walltime; CPU and memory bounds cover the entire tree.
                self.run(self.service_command(self.root, self.service, command, walltime='infinity'))
                launched = True
            state = self.status()
            if state['status'] in ('ready', 'busy'):
                return state
            if state['status'] == 'stopped' and launched:
                raise OSError('Warm service exited during startup; inspect .warm/worker.log')
            time.sleep(.2)  # Bounded service readiness, not a job polling loop.
        raise TimeoutError('Warm service startup timed out; inspect .warm/worker.log')

    def stop(self, *, force=False):
        with self.locked():
            if not force:
                state = self.status()
                if state['status'] != 'stopped' and state['status'] != 'ready':
                    raise ValueError('Warm service is busy or starting; use --force to interrupt it')
            if self.unit_properties(self.service).get('LoadState') != 'not-found':
                self.run([self.config.systemctl, '--user', 'stop', self.service])
            return {'status': 'stopped', 'unit': self.service}

    def submit(self, directory, identifier):
        identifier = job_id(identifier)
        if Path(directory) != self.config.spool / identifier:
            raise ValueError('Warm jobs must be in the configured spool')
        with self.locked():
            try:
                self._start()
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                # No submit message has been sent, so no render can have started.
                raise OSError('Warm service could not start: ' + str(exc)) from exc
            try:
                self.rpc('submit', job_id=identifier)
            except (OSError, ValueError, RuntimeError) as exc:
                raise SubmissionUnknown('Warm submission was not confirmed; reconcile the existing job') from exc
        return self.unit(identifier)

    def identifier(self, execution_id):
        return self.checked_id(execution_id)[len(self.prefix):-len('.service')]

    def state(self, execution_id):
        identifier = self.identifier(execution_id)
        if (self.config.spool / identifier / 'result.json').is_file():
            return 'COMPLETED'
        try:
            state = self.status()
        except RuntimeError as exc:
            raise OSError(str(exc)) from exc
        if state['status'] == 'starting':
            return 'UNKNOWN'
        return 'RUNNING' if state.get('job_id') == identifier else 'FAILED'

    def cancel(self, execution_id):
        identifier = self.identifier(execution_id)
        # Store serializes admission/cancellation and admits only one active job.
        # Check its identity as well: a stale receipt cannot stop a later job.
        with self.locked():
            state = self.status()
            if state.get('job_id') == identifier or state['status'] in ('starting', 'stopping'):
                self.run([self.config.systemctl, '--user', 'stop', self.service])

    def find_submission(self, identifier, created_at):
        directory = self.config.spool / job_id(identifier)
        if any((directory / name).exists() for name in ('started.json', 'result.json')):
            return self.unit(identifier)
        return self.unit(identifier) if self.status().get('job_id') == identifier else None
