"""Run the existing Apptainer worker as a bounded Linux systemd user service.

The gateway's job record and worker result remain authoritative. A deterministic
unit name allows reconciliation after a lost launch response or gateway restart;
no PID files, shell commands, separate queue or Blender launcher are involved.
"""
import hashlib
import os
import platform
import pwd
import re
import subprocess

from .protocol import job_id
from .slurm import Slurm, SubmissionUnknown


class Workstation(Slurm):
    def __init__(self, config):
        super().__init__(config)
        namespace = hashlib.sha256(str(config.spool).encode()).hexdigest()[:12]
        self.prefix = 'blender-render-' + namespace + '-'

    def environment(self):
        user = pwd.getpwuid(os.getuid())
        return {'PATH': self.search_path(), 'LANG': 'C.UTF-8', 'HOME': user.pw_dir,
                'USER': user.pw_name, 'LOGNAME': user.pw_name}

    def run(self, command):
        env = self.environment()
        runtime = '/run/user/' + str(os.getuid())
        env.update(XDG_RUNTIME_DIR=runtime, DBUS_SESSION_BUS_ADDRESS='unix:path=' + runtime + '/bus')
        return subprocess.run(command, env=env, capture_output=True, text=True, timeout=30, check=True)

    def unit(self, identifier):
        return self.prefix + job_id(identifier) + '.service'

    def checked_id(self, value):
        if not isinstance(value, str) or not re.fullmatch(re.escape(self.prefix) + '[0-9a-f]{32}\\.service', value):
            raise ValueError('Invalid workstation worker ID for this spool')
        return value

    def submit(self, directory, identifier):
        unit = self.unit(identifier)
        command = self.service_command(directory, unit, self.container_command(directory) + ['/job'],
                                       walltime=self.config.walltime_seconds)
        try:
            self.run(command)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            # Even a nonzero response can follow an accepted D-Bus start request.
            raise SubmissionUnknown('Workstation launch was not confirmed; reconcile the existing job') from exc
        return unit

    def service_command(self, directory, unit, executable, *, walltime):
        c = self.config
        env = self.environment()
        env.update(APPTAINERENV_OMP_NUM_THREADS=str(c.cpus), APPTAINERENV_OPENBLAS_NUM_THREADS=str(c.cpus))
        if c.gpus:
            env['APPTAINERENV_CUDA_VISIBLE_DEVICES'] = str(c.gpu_index)
        # env -i also clears the user manager's environment: service credentials,
        # Python paths and unrelated APPTAINER_* settings never enter the worker.
        command = [c.systemd_run, '--user', '--quiet', '--collect', '--service-type=exec',
                   '--expand-environment=no', '--unit=' + unit,
                   '--property=Restart=no', '--property=KillMode=control-group',
                   '--property=TimeoutStopSec=10s', '--property=UMask=0077',
                   '--property=RuntimeMaxSec=' + str(walltime),
                   '--property=MemoryMax=' + str(c.memory_mb) + 'M',
                   '--property=CPUQuota=' + str(c.cpus * 100) + '%',
                   '--property=WorkingDirectory=' + str(directory),
                   '--property=StandardOutput=append:' + str(directory / 'worker.log'),
                   '--property=StandardError=inherit', '/usr/bin/env', '-i',
                   *[key + '=' + value for key, value in env.items()],
                   *executable]
        return command

    def properties(self, identifier):
        return self.unit_properties(self.checked_id(identifier))

    def unit_properties(self, unit):
        command = [self.config.systemctl, '--user', 'show', unit,
                   '--property=LoadState,ActiveState,SubState,Result']
        try:
            output = self.run(command).stdout
        except subprocess.CalledProcessError as exc:
            output = exc.stdout or ''
            if 'LoadState=not-found' not in output.splitlines():
                raise
        return dict(line.split('=', 1) for line in output.splitlines() if '=' in line)

    def state(self, identifier):
        values = self.properties(identifier)
        if values.get('LoadState') == 'not-found':
            # --collect removes stopped units. Store still requires result.json
            # before declaring success, and allows its usual propagation grace.
            return 'COMPLETED'
        if values.get('ActiveState') == 'failed':
            return 'FAILED'
        if values.get('ActiveState') == 'deactivating':
            return 'COMPLETING'
        if values.get('ActiveState') == 'active':
            return 'RUNNING'
        if values.get('ActiveState') == 'activating':
            return 'PENDING'
        if values.get('ActiveState') == 'inactive':
            return 'COMPLETED' if values.get('Result') == 'success' else 'FAILED'
        return 'UNKNOWN'

    def cancel(self, identifier):
        if self.properties(identifier).get('LoadState') == 'not-found':
            return
        try:
            self.run([self.config.systemctl, '--user', 'stop', self.checked_id(identifier)])
        except subprocess.CalledProcessError:
            # Completion can collect the unit between show and stop.
            if self.properties(identifier).get('LoadState') != 'not-found':
                raise

    def find_submission(self, identifier, created_at):
        unit = self.unit(identifier)
        return unit if self.properties(unit).get('LoadState') == 'loaded' else None

    def dry_run(self, directory):
        """Read-only preflight; never creates a unit or starts a renderer."""
        if platform.system() != 'Linux':
            raise ValueError('The workstation scheduler requires Linux and a systemd user manager')
        version = self.run_checked([self.config.systemd_run, '--version'], 'systemd version').stdout
        match = re.match(r'systemd (\d+)', version)
        if not match or int(match.group(1)) < 254:
            raise ValueError('The workstation scheduler requires systemd 254 or newer')
        manager = self.run_checked([self.config.systemctl, '--user', 'show', '--property=Version'],
                                   'systemd user manager').stdout
        if not manager.startswith('Version='):
            raise ValueError('The systemd user manager did not report its version')
        return {'available': True, 'systemd': int(match.group(1)), 'gpu_index': self.config.gpu_index,
                'gpus': self.config.gpus, 'resource_limits_verified': False}
