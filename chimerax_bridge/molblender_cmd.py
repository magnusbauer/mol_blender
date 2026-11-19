"""ChimeraX commands for mol-blender. Load with `open /path/to/molblender_cmd.py`.

  molblender [models] [preset NAME] [output PATH] [width W] [height H] [rough true|false]
             [dssp true|false] [transparent true|false] [timeout SECONDS]
  molblender export PATH [models] [dssp true|false]

Exports the current scene to the renderer's data-only bundle and renders it
with the configured mol-blender CLI in a separate process. ChimeraX's Python
never imports the renderer.
"""
from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ChimeraX sets __file__ to this script for `open file.py` and `runscript`.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from chimerax_bridge.export import build_render_payload  # noqa: E402


def config_root() -> Path:
    """Same resolution as mol_blender.config.config_root, without importing it."""
    return Path(os.environ.get("MOL_BLENDER_HOME", str(Path.home() / ".config" / "mol-blender"))).expanduser()


def renderer_command() -> list[str]:
    config_file = config_root() / "config.json"
    config = json.loads(config_file.read_text()) if config_file.exists() else {}
    python = config.get("local_python")
    if python:
        return [python, "-m", "mol_blender.cli"]
    executable = shutil.which("mol-blender")
    if executable:
        return [executable]
    raise RuntimeError("No renderer configured: run `mol-blender setup` (sets local_python) "
                       "or put mol-blender on PATH")


def renderer_environment() -> dict:
    """Like mol_blender.local_bridge.controller_environment: never leak ChimeraX's Python."""
    env = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__", "PYTHONNOUSERSITE"):
        env.pop(name, None)
    env["PYTHONPATH"] = str(_REPO_ROOT)
    return env


def render_command(bundle: Path, output: Path, *, preset=None, width=None, height=None,
                   rough=None, transparent=None, timeout=600.0) -> list[str]:
    command = renderer_command() + ["render", str(bundle), "-o", str(output), "--timeout", str(float(timeout))]
    if preset:
        command += ["--preset", preset]
    if width:
        command += ["--width", str(int(width))]
    if height:
        command += ["--height", str(int(height))]
    if rough is not None:
        command.append("--rough" if rough else "--no-rough")
    if transparent is not None:
        command.append("--transparent" if transparent else "--no-transparent")
    return command


def default_output(run_id: str) -> Path:
    root = Path(tempfile.gettempdir()) / "mol-blender-chimerax"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"render-{run_id}.png"


def _structures(models):
    return list(models) if models else None


def _log_image(session, path: Path) -> None:
    try:
        from PIL import Image
        image = Image.open(path)
        image.thumbnail((512, 512))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data = base64.b64encode(buffer.getvalue()).decode("ascii")
        session.logger.info(f'<img src="data:image/png;base64,{data}"><br>'
                            f'<a href="file://{path}">{path}</a>', is_html=True)
    except Exception:
        session.logger.info(f"Rendered PNG: {path}")


def molblender_export(session, path, models=None, dssp=False):
    payload = build_render_payload(session, _structures(models), dssp=dssp)
    target = Path(path).expanduser().resolve()
    target.write_text(json.dumps(payload, allow_nan=False))
    for note in payload.get("provenance", {}).get("ignored_models", []):
        session.logger.warning("molblender: skipped " + note)
    session.logger.info(f"molblender bundle: {target}")
    return str(target)


def molblender(session, models=None, preset=None, output=None, width=None, height=None,
               rough=None, dssp=False, transparent=None, timeout=600.0):
    payload = build_render_payload(session, _structures(models), dssp=dssp)
    for note in payload.get("provenance", {}).get("ignored_models", []):
        session.logger.warning("molblender: skipped " + note)
    target = Path(output).expanduser().resolve() if output else default_output(payload["run_id"])
    target = target.with_suffix(".png")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="chimerax-blender-") as temporary:
        bundle = Path(temporary) / "bundle.json"
        bundle.write_text(json.dumps(payload, allow_nan=False))
        command = render_command(bundle, target, preset=preset, width=width, height=height,
                                 rough=rough, transparent=transparent, timeout=timeout)
        session.logger.status("molblender: rendering with Blender...")
        # Blocks ChimeraX until Blender finishes, like PyMOL's mol_blender command.
        result = subprocess.run(command, env=renderer_environment(), capture_output=True, text=True)
    if result.returncode:
        from chimerax.core.errors import UserError
        raise UserError("mol-blender render failed. Run `mol-blender setup` in the render "
                        "environment.\n" + result.stderr[-4000:])
    try:
        summary = json.loads(result.stdout)
    except ValueError:
        summary = {}
    _log_image(session, target)
    return summary or str(target)


def register_commands(logger):
    from chimerax.core.commands import (BoolArg, CmdDesc, FloatArg, PositiveIntArg, SaveFileNameArg,
                                        StringArg, register)
    from chimerax.atomic import AtomicStructuresArg
    render_desc = CmdDesc(
        optional=[("models", AtomicStructuresArg)],
        keyword=[("preset", StringArg), ("output", SaveFileNameArg), ("width", PositiveIntArg),
                 ("height", PositiveIntArg), ("rough", BoolArg), ("dssp", BoolArg),
                 ("transparent", BoolArg), ("timeout", FloatArg)],
        synopsis="Render the current scene with mol-blender")
    export_desc = CmdDesc(
        required=[("path", SaveFileNameArg)],
        optional=[("models", AtomicStructuresArg)],
        keyword=[("dssp", BoolArg)],
        synopsis="Write the current scene as a mol-blender bundle JSON")
    register("molblender", render_desc, molblender, logger=logger)
    register("molblender export", export_desc, molblender_export, logger=logger)


if "session" in globals():
    register_commands(globals()["session"].logger)
    globals()["session"].logger.info("Registered molblender and molblender export")
