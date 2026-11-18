"""Gateway preflight: configuration, image, container and scheduler checks without rendering."""
import json
import os
from pathlib import Path
import re
import tempfile

from .jsonio import digest_file, one_line

# Provisional floor. The job flags (--containall, --no-mount hostfs,bind-paths,cwd,
# --cleanenv) passed real acceptance on a newer Apptainer; the exact oldest
# compatible release has not been established on real hardware.
MINIMUM_APPTAINER = (1, 1)


def python_matches_lock(version):
    """The container's Python must match runtime.lock.json, the package's runtime
    contract, at the same major.minor precision blender_runtime checks."""
    from ..config import runtime_versions
    locked = runtime_versions()['python']
    return isinstance(version, str) and (version == locked or version.startswith(locked + '.'))


def apptainer_version(output):
    """Parse `apptainer --version` / `singularity --version` output."""
    # Wrappers and modules may print WARNING/INFO lines first; find the version line.
    match = re.search(r'^\s*(apptainer|singularity-ce|singularity)\s+version\s+v?(\d+)\.(\d+)(?:\.(\d+))?',
                      output or '', re.IGNORECASE | re.MULTILINE)
    if not match:
        raise ValueError('Could not determine the container runtime version from: ' + one_line(output, 200))
    product = match.group(1).lower()
    version = '.'.join(part or '0' for part in match.group(2, 3, 4))
    minimum = '.'.join(map(str, MINIMUM_APPTAINER))
    warnings = []
    if product == 'apptainer':
        if (int(match.group(2)), int(match.group(3))) < MINIMUM_APPTAINER:
            raise ValueError(f'Apptainer {version} is too old; {minimum} or newer is required')
    else:
        warnings.append(f'{product} {version} is untested; Apptainer {minimum} or newer is the supported runtime')
    return {'product': product, 'version': version, 'raw': one_line(output, 200)}, warnings


def run(config, scheduler, identity):
    if digest_file(config.image) != config.image_sha256:
        raise ValueError('SIF checksum mismatch')
    for name in config.executables:
        if not os.access(getattr(config, name), os.X_OK):
            raise ValueError(name + ' executable is missing')
    if not config.spool.is_dir():
        raise ValueError('Create the private shared spool ' + str(config.spool) + ' before running doctor')
    result = scheduler.run_checked([config.apptainer, '--version'], config.apptainer + ' --version')
    runtime, warnings = apptainer_version((result.stdout or '') + '\n' + (result.stderr or ''))
    with tempfile.TemporaryDirectory(prefix='blender-cluster-check-') as temporary:
        result = scheduler.run_checked(scheduler.container_command(Path(temporary)) + ['--check'],
                                       'Container identity check')
    try:
        check = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError('Container identity check printed no JSON: '
                         + one_line(result.stdout or result.stderr)) from exc
    if check.get('engine_build') != identity or check.get('platform') != 'Linux':
        raise ValueError('Gateway and container must use the same renderer build on Linux')
    if not python_matches_lock(check.get('python')):
        raise ValueError('Container Python ' + str(check.get('python')) + ' does not match runtime.lock.json')
    # Same shared filesystem as real jobs, so site job_submit rules on
    # --chdir/--output see the real request shape.
    with tempfile.TemporaryDirectory(prefix='.doctor-', dir=config.spool) as temporary:
        dry_run = scheduler.dry_run(Path(temporary))
    return {**check, 'status': 'configured', 'image_sha256': config.image_sha256,
            'runtime': config.runtime_root,
            'apptainer': runtime, 'scheduler': config.scheduler,
            ('slurm_dry_run' if config.scheduler == 'slurm' else 'workstation_check'): dry_run,
            'warnings': warnings,
            'render_verified': False}
