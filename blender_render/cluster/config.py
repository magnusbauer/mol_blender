"""Administrator-owned service settings, separate from local render configuration."""
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import ClassVar

from .jsonio import read_json


@dataclass(frozen=True)
class Limits:
    max_request_bytes: int = 20 * 1024**2
    max_pixels: int = 4096 * 4096
    max_samples: int = 512
    max_artifact_bytes: int = 1024**3
    max_active_per_user: int = 4
    max_active_total: int = 32
    max_retained_per_user: int = 32
    max_spool_bytes: int = 50 * 1024**3
    retention_seconds: int = 86400
    render_timeout: int = 3600

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in vars(self).values()):
            raise ValueError('All cluster limits must be positive integers')
        # Clients build Limits from what the server advertises, so a site may lower the
        # transfer caps but never raise them past the defaults every client accepts.
        for name in ('max_request_bytes', 'max_artifact_bytes'):
            if getattr(self, name) > self.__dataclass_fields__[name].default:
                raise ValueError(name + ' cannot exceed the protocol default')


@dataclass(frozen=True)
class ServerConfig:
    spool: Path
    image: Path
    image_sha256: str
    tokens_file: Path | None = None
    authentication: str = 'token'
    scheduler: str = 'slurm'
    apptainer: str = '/usr/bin/apptainer'
    systemd_run: str = '/usr/bin/systemd-run'
    systemctl: str = '/usr/bin/systemctl'
    sbatch: str = '/usr/bin/sbatch'
    squeue: str = '/usr/bin/squeue'
    sacct: str = '/usr/bin/sacct'
    scancel: str = '/usr/bin/scancel'
    srun: str = '/usr/bin/srun'
    partition: str = ''
    account: str = ''
    qos: str = ''
    cpus: int = 8
    memory_mb: int = 16384
    gpus: int = 0
    gpu_index: int = 0
    # Site-specific Slurm GPU/node selection. The defaults keep --gres=gpu:1.
    gpu_request: str = 'gres'
    gpu_type: str = ''
    constraint: str = ''
    walltime_seconds: int = 3900
    runtime_home: Path | None = None
    limits: Limits = field(default_factory=Limits)

    EXECUTABLES: ClassVar[tuple] = ('apptainer', 'sbatch', 'squeue', 'sacct', 'scancel', 'srun')
    WORKSTATION_EXECUTABLES: ClassVar[tuple] = ('apptainer', 'systemd_run', 'systemctl')
    DEFAULT_RUNTIME: ClassVar[str] = '/opt/studio-runtime'

    @property
    def executables(self):
        return self.WORKSTATION_EXECUTABLES if self.scheduler != 'slurm' else self.EXECUTABLES

    @property
    def execution_id_key(self):
        return 'worker_id' if self.scheduler != 'slurm' else 'slurm_id'

    @property
    def runtime_root(self):
        """The Studio runtime inside the image (or bound from runtime_home)."""
        return str(self.runtime_home or self.DEFAULT_RUNTIME)

    def __post_init__(self):
        # Reject JSON numbers/lists before any regex or Path call, so the
        # administrator is told which value is wrong rather than 'unknown field'.
        for name in ('authentication', 'scheduler', 'image_sha256', *self.EXECUTABLES,
                     'systemd_run', 'systemctl', 'partition', 'account',
                     'qos', 'gpu_request', 'gpu_type', 'constraint'):
            if not isinstance(getattr(self, name), str):
                raise ValueError(name + ' must be a string')
        for name in ('spool', 'image', 'tokens_file', 'runtime_home'):
            value = getattr(self, name)
            if value is not None and not isinstance(value, (str, Path)):
                raise ValueError(name + ' must be a path string')
        if self.authentication not in ('token', 'none'):
            raise ValueError('authentication must be token or none')
        if self.scheduler not in ('slurm', 'workstation', 'warm'):
            raise ValueError('scheduler must be slurm, workstation or warm')
        if self.authentication == 'token' and self.tokens_file is None:
            raise ValueError('Token authentication requires tokens_file')
        # Commas and colons would split Apptainer --bind specifications.
        for name in ('spool', 'image', 'tokens_file', 'runtime_home'):
            if getattr(self, name) is None:
                continue
            path = Path(getattr(self, name)).expanduser()
            if not path.is_absolute() or any(c in str(path) for c in '\n\r,:'):
                raise ValueError(name + ' must be an absolute path without newline, comma or colon')
            object.__setattr__(self, name, path.resolve())
        if not re.fullmatch('[0-9a-f]{64}', self.image_sha256):
            raise ValueError('Pin the SIF image_sha256 before serving')
        for name in (*self.EXECUTABLES, 'systemd_run', 'systemctl'):
            if not Path(getattr(self, name)).is_absolute():
                raise ValueError(name + ' must be an absolute executable path')
        for name in ('partition', 'account', 'qos'):
            value = getattr(self, name)
            if value and not re.fullmatch('[A-Za-z0-9_.:-]{1,100}', value):
                raise ValueError('Invalid Slurm ' + name)
        for name in ('cpus', 'memory_mb', 'walltime_seconds'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(name + ' must be positive')
        if type(self.gpus) is not int or self.gpus not in (0, 1):
            raise ValueError('Request zero or one GPU per render')
        if type(self.gpu_index) is not int or self.gpu_index < 0:
            raise ValueError('gpu_index must be a nonnegative integer')
        if self.scheduler == 'slurm' and self.gpu_index:
            raise ValueError('gpu_index is only supported by the workstation scheduler')
        if self.gpu_request not in ('gres', 'gpus'):
            raise ValueError('gpu_request must be gres or gpus')
        if self.gpu_type and not re.fullmatch('[A-Za-z0-9_.-]{1,64}', self.gpu_type):
            raise ValueError('Invalid gpu_type; use the GPU type name reported by sinfo')
        if self.gpu_type and not self.gpus:
            raise ValueError('gpu_type requires gpus: 1')
        # Slurm feature expressions such as a100|h100 or gpu&ib; no spaces,
        # brackets, counts or shell syntax.
        if len(self.constraint) > 200 or self.constraint and not re.fullmatch(
                r'[A-Za-z0-9_.-]+(?:[&|][A-Za-z0-9_.-]+)*', self.constraint):
            raise ValueError('Invalid Slurm constraint; use feature names joined by & or |')
        if self.walltime_seconds < self.limits.render_timeout + 60:
            raise ValueError('Execution walltime must exceed render_timeout by at least 60 seconds')
        if self.scheduler in ('workstation', 'warm'):
            if self.limits.max_active_total != 1:
                raise ValueError('Workstation limits.max_active_total must be 1')
            if self.scheduler == 'warm' and self.limits.render_timeout < 30:
                raise ValueError('Warm render_timeout must be at least 30 seconds')
            if any((self.partition, self.account, self.qos, self.constraint, self.gpu_type)) or self.gpu_request != 'gres':
                raise ValueError('Slurm resource selectors cannot be used with the workstation scheduler')
            # systemd expands percent specifiers even in otherwise literal paths.
            for name in ('spool', 'image', 'runtime_home', *self.executables):
                if '%' in str(getattr(self, name)):
                    raise ValueError('Workstation ' + name + ' cannot contain percent specifiers')

    @classmethod
    def load(cls, path):
        doc = read_json(path, 64 * 1024)
        if not isinstance(doc, dict):
            raise ValueError('Server config must be an object')
        try:
            return cls(**{**doc, 'limits': Limits(**doc.get('limits', {}))})
        except TypeError as exc:
            raise ValueError('Unknown or missing server configuration field') from exc
