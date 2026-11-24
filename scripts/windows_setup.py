"""Explicit, repeatable Windows renderer setup and reversible PyMOL integration."""
import argparse
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
BEGIN = '# >>> mol-blender Windows renderer >>>'
END = '# <<< mol-blender Windows renderer <<<'


def run(command, **kwargs):
    return subprocess.run(list(map(str, command)), check=True, **kwargs)


def discover_pymol():
    candidates = []
    env = os.environ.get('CONDA_PREFIX')
    if env:
        candidates.append(Path(env) / 'python.exe')
    executable = shutil.which('pymol')
    if executable:
        candidates.append(Path(executable).parent.parent / 'python.exe')
    conda = shutil.which('conda')
    if conda:
        try:
            result = run([conda, 'env', 'list', '--json'], capture_output=True, text=True, timeout=30)
            candidates.extend(Path(p)/'python.exe' for p in json.loads(result.stdout)['envs'])
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    # Common user-owned and all-users conda locations, without assuming an env name.
    for base in (Path.home(), Path(os.environ.get('ProgramData', 'C:/ProgramData'))):
        for name in ('miniforge3', 'miniconda3', 'anaconda3'):
            candidates.extend((base/name/'envs').glob('*/python.exe'))
    matches = []
    for candidate in dict.fromkeys(candidates):
        if candidate.is_file():
            result = subprocess.run([str(candidate), '-c', 'import pymol2'], capture_output=True, timeout=30)
            if result.returncode == 0:
                matches.append(candidate)
    if len(matches) != 1:
        raise ValueError('Specify -PymolPython with the interpreter containing pymol2. Detected: ' +
                         ', '.join(map(str, matches)))
    return matches[0]


def update_startup(path, root, *, uninstall=False):
    """Replace only our marked block. Keep unrelated user code and byte encoding."""
    original = path.read_bytes() if path.exists() else b''
    # PyMOL startup is Python source; require UTF-8 rather than corrupting legacy code.
    text = original.decode('utf-8-sig')
    newline = '\r\n' if '\r\n' in text else '\n'
    if text.count(BEGIN) != text.count(END) or text.count(BEGIN) > 1:
        raise ValueError('Malformed mol-blender startup markers; preserve file and inspect manually')
    if BEGIN in text:
        start, end = text.index(BEGIN), text.index(END) + len(END)
        if end < start:
            raise ValueError('Reversed mol-blender startup markers')
        if text[end:end+len(newline)] == newline:
            end += len(newline)
        if '# preceding user code had no final newline' in text[start:end] and text[:start].endswith(newline):
            start -= len(newline)
        text = text[:start] + text[end:]
    # Migrate only the exact hook used by the initial installation.
    legacy_lines = ('# mol-blender Windows renderer', 'import runpy',
                    "runpy.run_path(r'" + str(root/'scripts/pymol_windows_startup.py') + "')")
    legacy_pattern = r'(?m)^' + r'\r?\n'.join(re.escape(line) for line in legacy_lines) + r'\r?\n'
    text = re.sub(legacy_pattern, '', text)
    if not uninstall:
        hook = repr(str(root / 'scripts/pymol_windows_startup.py'))
        block = newline.join((BEGIN, 'import runpy as _mol_blender_runpy',
                              f'_mol_blender_runpy.run_path({hook})', END, ''))
        # A leading newline is part of the managed block when user code has no final newline.
        if text and not text.endswith(('\n', '\r')):
            block = BEGIN + newline + '# preceding user code had no final newline' + newline + block[len(BEGIN + newline):]
            text += newline
        text += block
    encoded = text.encode('utf-8')
    if original.startswith(b'\xef\xbb\xbf'):
        encoded = b'\xef\xbb\xbf' + encoded
    if encoded == original:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = path.with_name(path.name + '.mol-blender-' + stamp + '.bak')
        backup.write_bytes(original)
    temporary = path.with_name(path.name + '.mol-blender.tmp')
    temporary.write_bytes(encoded)
    os.replace(temporary, path)
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pymol-python', type=Path)
    parser.add_argument('--blender', type=Path)
    parser.add_argument('--startup-file', type=Path, default=Path.home()/'.pymolrc.py')
    parser.add_argument('--skip-runtime', action='store_true', help='Validate and reuse an existing configured runtime')
    parser.add_argument('--uninstall', action='store_true', help='Remove only this startup integration; keep runtimes and outputs')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('This installer targets Windows x64')
    if args.uninstall:
        backup = update_startup(args.startup_file, ROOT, uninstall=True)
        print('Startup integration removed. Runtimes and user outputs retained. Backup:', backup)
        return
    python = (args.pymol_python or discover_pymol()).absolute()
    run([python, '-X', 'utf8', '-c',
         'import pymol2; p=pymol2.PyMOL(); p.start(); print(p.cmd.get_version()); p.stop()'])
    home = ROOT/'.runtime'
    os.environ['MOL_BLENDER_HOME'] = str(home)
    os.environ['PYTHONUTF8'] = '1'
    from mol_blender.config import load_config, save_config
    from mol_blender.local import runtime_settings
    config = load_config()
    config.update(local_python=sys.executable, pymol_python=str(python))
    if args.blender:
        config['blender'] = str(args.blender.absolute())
    if not args.skip_runtime:
        from mol_blender.runtime_install import install
        config.update(install(config.get('blender', ''), root=home/'runtime'))
        config['runtime_paths'] = [config['runtime_site']]
        config['ccd'] = str(Path(config['runtime_site'])/'biotite/structure/info/components.bcif')
    save_config(config)
    settings = runtime_settings()
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(map(str, settings['runtime_paths'])),
               PRESS_BIOTITE_CCD_PATH=str(settings['ccd']), MPLCONFIGDIR=str(home/'matplotlib'))
    run([settings['blender'], '--background', '--factory-startup', '--python-use-system-env',
         '--python-exit-code', '1', '--python', ROOT/'mol_blender/runtime_probe.py'], env=env)
    run([settings['blender'], '--background', '--factory-startup', '--python-use-system-env',
         '--python-exit-code', '1', '--python', ROOT/'mol_blender/runtime_initialize.py'], env=env)
    backup = update_startup(args.startup_file, ROOT)
    print('Configured. Restart PyMOL and run mol_blender. Startup backup:', backup)
    print('Runtime probes passed; verify rendering with scripts/verify_windows_pymol.py.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('Windows setup failed:', error, file=sys.stderr)
        raise SystemExit(1)
