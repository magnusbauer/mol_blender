"""Local file export and runtime diagnostics; no background service."""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path
from .config import config_root, load_config

def export_file(path, *, python="", selection="(all)"):
    import tempfile
    from .engine import protect
    seal = protect('conversion')
    config = load_config()
    executable = python or config.get("pymol_python", sys.executable)
    with tempfile.TemporaryDirectory(prefix="blender-export-") as temp:
        destination = Path(temp) / "payload.json"
        captured = seal.capture(Path(temp)/'mol_blender')
        env = os.environ.copy()
        env['PYTHONPATH'] = str(captured.parent) + os.pathsep + env.get('PYTHONPATH', '')
        protect('conversion')
        result = subprocess.run([executable, '-X', 'utf8', str(captured/'scene/export.py'), str(Path(path).expanduser().resolve()), str(destination), "--selection", selection],
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
    prefix = "MOL_BLENDER_RUNTIME="
    lines = [line[len(prefix):] for line in result.stdout.splitlines() if line.startswith(prefix)]
    if result.returncode or not lines:
        raise RuntimeError("Runtime is incomplete. Run mol-blender setup --install-runtime.\n" + result.stderr[-1500:])
    versions = json.loads(lines[-1])
    from .config import runtime_versions
    lock = runtime_versions()
    drift = {key: value for key, value in versions.items() if value != lock[key]}
    return {"blender_executable": blender, "versions": versions, "matches_lock": not drift, "mismatches": drift}
