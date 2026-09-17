#!/usr/bin/env python3
"""Shared preset validation, structure staging, and Blender command construction."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import math
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

try:
    from cutout_planes import PLANE_NAMES
except Exception:  # pragma: no cover - package import fallback
    from blender_render.cutout_planes import PLANE_NAMES

MACOS_STANDARD_BLENDER_EXE = "/Applications/Blender.app/Contents/MacOS/Blender"
DEFAULT_BLENDER_EXE = MACOS_STANDARD_BLENDER_EXE
DEFAULT_APPTAINER_BLENDER_EXE = "/usr/local/bin/blender"
DEFAULT_INTERACTIVE_LAUNCHER = "qlogin -p gpu --gres=gpu:a4000:1 --mem=64g -c 4"


def _default_session_root() -> Path:
    override = os.environ.get("PYMOL_BRIDGE_SESSION_ROOT", "").strip()
    if override:
        return Path(override).expanduser().absolute()
    # Must be visible from login + compute nodes for persistent interactive IPC.
    return (Path.home() / ".pymol_bridge" / "sessions").absolute()


def _default_tmp_bridge_root() -> Path:
    override = os.environ.get("PYMOL_BRIDGE_TMP_ROOT", "").strip()
    if override:
        return Path(override).expanduser().absolute()
    user = (os.environ.get("USER") or os.environ.get("LOGNAME") or "").strip()
    if not user:
        try:
            user = f"uid_{os.getuid()}"
        except Exception:
            user = "default"
    return Path(tempfile.gettempdir()) / "pymol_bridge" / user


SESSION_ROOT = _default_session_root()
TMP_BRIDGE_ROOT = _default_tmp_bridge_root()

from jsonschema import Draft202012Validator
from blender_render.presets import PRESET_OUTPUT_FILENAMES, PRESET_STYLE_FILENAMES, preset_path

STYLE_SCHEMA_PATH = Path(__file__).resolve().with_name("style.schema.json")

# Derive accepted keys from the packaged schema instead of duplicating them.
from blender_render.engine import resource_bytes
_SCHEMA = json.loads(resource_bytes(STYLE_SCHEMA_PATH))
_STYLE_VALIDATOR = Draft202012Validator(_SCHEMA)
REQUIRED_STYLE_TOP_KEYS = tuple(_SCHEMA["required"])
STYLE_TOP_LEVEL_OPTIONAL_KEYS = set(_SCHEMA["properties"]) - set(REQUIRED_STYLE_TOP_KEYS)
OUTPUT_KEYS = set(_SCHEMA["properties"]["output"]["properties"])
RENDERER_KEYS = set(_SCHEMA["properties"]["renderer"]["properties"])
CAMERA_KEYS = set(_SCHEMA["properties"]["camera"]["properties"])
WORLD_KEYS = set(_SCHEMA["properties"]["world"]["properties"])
LIGHT_KEYS = set(_SCHEMA["properties"]["lights"]["items"]["properties"])
MATERIAL_KEYS = set(_SCHEMA["properties"]["materials"]["additionalProperties"]["properties"])
STYLE_KEYS = set(_SCHEMA["properties"]["styles"]["items"]["properties"])
COLOR_FOLLOW_KEYS = set(_SCHEMA["properties"]["color_follow"]["properties"])


class RunnerError(RuntimeError):
    """Base runtime error for bridge runner."""


class PayloadValidationError(RunnerError):
    """Invalid PyMOL payload."""


class StyleValidationError(RunnerError):
    """Invalid style JSON."""


def _default_custom_script() -> Path:
    return Path(__file__).resolve().parent / "blender_custom_render.py"


def _default_preset_style(preset: str) -> Path:
    try:
        return preset_path(preset)
    except ValueError as exc:
        raise RunnerError(str(exc)) from exc


def _new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def _now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off", ""}:
        return False
    raise argparse.ArgumentTypeError(f"Expected boolean value, got: {value!r}")


def _default_remote_python_executable() -> str:
    override = os.environ.get("PYMOL_BRIDGE_REMOTE_PYTHON", "").strip()
    if override:
        return override
    if shutil.which("python3"):
        return "python3"
    if shutil.which("python"):
        return "python"
    return sys.executable


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(resource_bytes(path))
    except FileNotFoundError as exc:
        raise RunnerError(f"JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RunnerError(f"Could not parse JSON file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RunnerError(f"Expected JSON object in {path}, got {type(payload).__name__}.")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _ensure_tmp_run_dir(run_id: str) -> Path:
    TMP_BRIDGE_ROOT.mkdir(parents=True, exist_ok=True)
    run_dir = TMP_BRIDGE_ROOT / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _expand_path_no_resolve(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return (Path.cwd() / path).absolute()


def _looks_like_cif_atom_site(text: str) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    has_data_block = any(line.lower().startswith("data_") for line in lines)
    has_loop = any(line.lower() == "loop_" for line in lines)
    has_atom_headers = any(line.lower().startswith("_atom_site.") for line in lines)
    has_atom_rows = any(line.startswith("ATOM") or line.startswith("HETATM") for line in lines)
    if has_atom_headers:
        return True
    return bool(has_atom_rows and (has_data_block or has_loop))


def _looks_like_pdb_atoms(text: str) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    return any(line.startswith(("ATOM", "HETATM")) for line in text.splitlines())


def validate_payload_document(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []

    cif_text = payload.get("cif_text")
    pdb_text = payload.get("pdb_text")

    cif_valid = _looks_like_cif_atom_site(cif_text) if isinstance(cif_text, str) else False
    pdb_valid = _looks_like_pdb_atoms(pdb_text) if isinstance(pdb_text, str) else False

    if not (cif_valid or pdb_valid):
        errors.append(
            "Payload is missing usable structure text. "
            "Provide 'cif_text' with atom-site data and/or 'pdb_text' with ATOM/HETATM records."
        )

    view = payload.get("view")
    if not isinstance(view, dict):
        errors.append("'view' must be an object with a 'pymol_view' list.")
    else:
        pymol_view = view.get("pymol_view")
        if not isinstance(pymol_view, list) or len(pymol_view) != 18:
            errors.append("'view.pymol_view' must contain exactly 18 floats from cmd.get_view().")
        else:
            for idx, value in enumerate(pymol_view):
                try:
                    parsed = float(value)
                except Exception:
                    errors.append(f"'view.pymol_view[{idx}]' is not numeric: {value!r}")
                    continue
                if not math.isfinite(parsed):
                    errors.append(f"'view.pymol_view[{idx}]' is not finite: {value!r}")

        fov_deg = view.get("fov_deg")
        if fov_deg is not None:
            try:
                if float(fov_deg) <= 0:
                    errors.append("'view.fov_deg' must be > 0 when provided.")
            except Exception:
                errors.append("'view.fov_deg' must be numeric when provided.")

        orthoscopic = view.get("orthoscopic")
        if orthoscopic is not None and not isinstance(orthoscopic, bool):
            errors.append("'view.orthoscopic' must be a boolean when provided.")

        viewport = view.get("viewport_px")
        if viewport is not None:
            if (
                not isinstance(viewport, (list, tuple))
                or len(viewport) != 2
                or not all(isinstance(item, (int, float)) for item in viewport)
            ):
                errors.append("'view.viewport_px' must be [width, height] when provided.")

    if errors:
        bullet = "\n".join(f"- {item}" for item in errors)
        raise PayloadValidationError(f"Payload validation failed:\n{bullet}")
    return payload


def _style_error_path(parts) -> str:
    path = ""
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else ("." if path else "") + str(part)
    return path or "style"


def validate_style_document(style_doc: dict[str, Any]) -> dict[str, Any]:
    """One structural validator; only cross-field semantics are checked below."""
    if not isinstance(style_doc, dict):
        raise StyleValidationError("Style document must be an object")
    try:
        json.dumps(style_doc, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise StyleValidationError("Style values must be finite JSON values") from exc
    errors = []
    for error in _STYLE_VALIDATOR.iter_errors(style_doc):
        # Keep the established actionable error paths for compatibility callers.
        if error.validator == "required" and not error.absolute_path:
            errors.append("Missing required top-level keys: " + error.message)
        else:
            errors.append(_style_error_path(error.absolute_path) + ": " + error.message)

    materials = style_doc.get("materials", {})
    styles = style_doc.get("styles", [])
    for idx, style in enumerate(styles if isinstance(styles, list) else []):
        if not isinstance(style, dict):
            continue
        prefix = f"styles[{idx}]"
        if "nucleic_base_scale" in style and style.get("style") != "cartoon":
            errors.append(f"{prefix}.nucleic_base_scale requires cartoon style")
        if "wireframe_spacing_angstrom" in style and "wireframe_width_angstrom" not in style:
            errors.append(f"{prefix}.wireframe_spacing_angstrom requires wireframe_width_angstrom")
        reference = style.get("material")
        if isinstance(reference, str) and isinstance(materials, dict) and reference not in materials:
            errors.append(f"{prefix}.material references '{reference}', which is not defined in materials")
        for key in ("selection", "fallback_selection", "ligand_cutout_selection"):
            selection = style.get(key)
            bounds = selection.get("residue_range") if isinstance(selection, dict) else None
            if (isinstance(bounds, list) and len(bounds) == 2
                    and all(isinstance(v, (int, float)) for v in bounds) and bounds[0] > bounds[1]):
                errors.append(f"{prefix}.{key}.residue_range start must be <= end")
        if (style.get("ligand_cutout_mode", "planes") == "planes"
                and style.get("ligand_cutout_frame_mode") == "preset"
                and "ligand_cutout_frame_axes" not in style):
            errors.append(f"{prefix}.ligand_cutout_frame_axes is required when ligand_cutout_frame_mode is preset")
    camera = style_doc.get("camera", {})
    if isinstance(camera, dict):
        if {"yaw_deg", "pitch_deg"} & camera.keys() and (
                camera.get("mode") != "override" or camera.get("direction_source", "preset") != "preset"):
            errors.append("camera.yaw_deg/pitch_deg require mode=override and direction_source=preset; use yaw_offset_deg/pitch_offset_deg for PyMOL-relative rotation")
    if isinstance(camera, dict) and "depth_of_field" in camera:
        if camera.get("mode") != "override" or camera.get("projection") != "PERSP":
            errors.append("camera.depth_of_field requires camera.mode=override and camera.projection=PERSP")
        output = style_doc.get("output", {})
        if isinstance(output, dict) and output.get("raw_view"):
            errors.append("camera.depth_of_field is unsupported with output.raw_view")
    world = style_doc.get("world", {})
    gradients = [("world.gradient_direction", world.get("gradient_direction"))] if isinstance(world, dict) else []
    geometry = style_doc.get("geometry", {})
    for name, material in (materials.items() if isinstance(materials, dict) else []):
        if not isinstance(material, dict):
            continue
        gradient = material.get("color_gradient")
        if isinstance(gradient, dict):
            gradients.append((f"materials.{name}.color_gradient.direction", gradient.get("direction")))
        frosted = material.get("frosted")
        if isinstance(frosted, dict):
            low, high = frosted.get("opacity_min"), frosted.get("opacity_max")
            if isinstance(low, (int, float)) and isinstance(high, (int, float)) and low > high:
                errors.append(f"materials.{name}.frosted.opacity_min must be <= opacity_max")
        if material.get("color_attribute") == "ElementColor" and not (isinstance(geometry, dict) and geometry.get("element_palette")):
            errors.append(f"materials.{name}.color_attribute ElementColor requires geometry.element_palette")
        if material.get("color_attribute") and gradient:
            errors.append(f"materials.{name}: choose color_attribute or color_gradient, not both")
        glow = material.get("glow")
        if isinstance(glow, dict) and float(glow.get("strength", 0)) > 0 and "emission_color" not in material:
            errors.append(f"materials.{name}.glow requires emission_color")
    for path, direction in gradients:
        if isinstance(direction, list) and direction and all(isinstance(v, (int, float)) for v in direction) and not any(direction):
            errors.append(f"{path} must be nonzero")
    output = style_doc.get("output", {})
    if not errors and isinstance(output, dict) and "animation" in output:
        from .animation import normalize
        try:
            normalize(output["animation"], output)
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise StyleValidationError("Style JSON validation failed:\n" + "\n".join("- " + e for e in errors))
    return style_doc


def load_and_validate_style_json(path: Path) -> dict[str, Any]:
    return validate_style_document(_load_json(path))


def _ensure_suffix_png(name: str) -> str:
    if name.lower().endswith(".png"):
        return name
    return f"{name}.png"


def _resolve_output_png(
    *,
    output_dir: str,
    output_name: str,
    preset: str | None,
    style_doc: dict[str, Any] | None,
) -> Path:
    if output_dir:
        out_dir = _expand_path_no_resolve(output_dir)
    else:
        out_dir = Path.cwd() / "renders" / _now_stamp()
    out_dir.mkdir(parents=True, exist_ok=True)

    if output_name:
        filename = _ensure_suffix_png(output_name)
    elif preset is not None:
        filename = PRESET_OUTPUT_FILENAMES[preset]
    else:
        style_filename = None
        if style_doc is not None:
            output = style_doc.get("output")
            if isinstance(output, dict):
                style_filename = output.get("filename")
        if isinstance(style_filename, str) and style_filename.strip():
            filename = _ensure_suffix_png(style_filename.strip())
        else:
            filename = "custom_render.png"

    return out_dir / filename


def _materialize_payload_bundle(payload: dict[str, Any], run_id: str) -> tuple[Path, Path, Path, Path | None, Path]:
    temp_root = _ensure_tmp_run_dir(run_id)
    bundle_dir = temp_root / "bundle"
    structures_dir = bundle_dir / "structures"
    structures_dir.mkdir(parents=True, exist_ok=True)

    pdb_text = payload.get("pdb_text")
    cif_text = payload.get("cif_text")
    cif_path: Path | None = None
    pdb_path: Path | None = None
    if isinstance(cif_text, str) and _looks_like_cif_atom_site(cif_text):
        cif_path = structures_dir / "structure.cif"
        cif_path.write_text(cif_text, encoding="utf-8")
    if isinstance(pdb_text, str) and _looks_like_pdb_atoms(pdb_text):
        pdb_path = structures_dir / "structure.pdb"
        pdb_path.write_text(pdb_text, encoding="utf-8")

    if cif_path is None and pdb_path is None:
        raise RunnerError(
            "Payload did not contain usable structure text. "
            "Expected CIF atom-site data or PDB ATOM/HETATM records."
        )
    structure_path = cif_path if cif_path is not None else pdb_path
    fallback_path = pdb_path if (cif_path is not None and pdb_path is not None) else None

    view_path = bundle_dir / "view.json"
    view_doc = dict(payload["view"])
    color_hints = payload.get("color_hints")
    if isinstance(color_hints, dict):
        view_doc["color_hints"] = color_hints
    ss_hints = payload.get("ss_hints")
    if isinstance(ss_hints, dict):
        view_doc["ss_hints"] = ss_hints
    view_path.write_text(json.dumps(view_doc, indent=2), encoding="utf-8")

    payload_path = temp_root / "payload.json"
    payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return temp_root, bundle_dir, structure_path, fallback_path, view_path


def _run_subprocess(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, env=env)
    except FileNotFoundError as exc:
        missing = exc.filename or (command[0] if command else "<unknown>")
        raise RunnerError(
            "Executable not found while launching command.\n"
            f"Missing executable: {missing}\n"
            f"Command: {' '.join(shlex.quote(item) for item in command)}\n"
            "If you want container execution, set --use-apptainer 1 and provide --apptainer-image."
        ) from exc


def _parse_last_json_object(stdout: str) -> dict[str, Any]:
    lines = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            parsed = json.loads(line)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise RunnerError("Could not parse JSON result from interactive/local runner output.")


def _resolve_apptainer_executable() -> str:
    candidates: list[str] = []
    resolved = shutil.which("apptainer")
    if resolved:
        candidates.append(resolved)
    candidates.extend(("/usr/bin/apptainer", "/usr/local/bin/apptainer"))

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)

    checked = ", ".join(candidates) if candidates else "<none>"
    raise RunnerError(
        "Apptainer executable was not found. "
        f"Checked: {checked}. "
        "Install apptainer or run without --use-apptainer."
    )


def _blender_executable_exists(path_value: str) -> bool:
    if not path_value:
        return False
    path = Path(path_value).expanduser()
    if path.is_file():
        return True
    if not path.is_absolute():
        resolved = shutil.which(path_value)
        if resolved and Path(resolved).is_file():
            return True
    return False


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _detect_host_blender_executable() -> str:
    candidates: list[str | None] = []
    if _is_macos():
        candidates.append(MACOS_STANDARD_BLENDER_EXE)
    candidates.extend(
        [
            shutil.which("blender"),
            "/usr/bin/blender",
            "/usr/local/bin/blender",
            "/opt/blender/blender",
        ]
    )
    for candidate in candidates:
        if not candidate:
            continue
        if _blender_executable_exists(candidate):
            return str(Path(candidate).expanduser())
    return ""


def _normalize_apptainer_blender_executable(blender_exe: str) -> str:
    raw = str(blender_exe or "").strip()
    if not raw:
        return DEFAULT_APPTAINER_BLENDER_EXE

    expanded = str(Path(raw).expanduser())
    known_container_paths = {
        "blender",
        DEFAULT_APPTAINER_BLENDER_EXE,
        "/bin/blender",
        "/opt/blender/blender",
    }
    if raw in known_container_paths or expanded in known_container_paths:
        return raw

    # Host-absolute Blender paths usually don't exist in the container.
    if expanded.startswith("/Applications/") or expanded.endswith(".app/Contents/MacOS/Blender"):
        return DEFAULT_APPTAINER_BLENDER_EXE
    if expanded.startswith("/"):
        return DEFAULT_APPTAINER_BLENDER_EXE
    return raw


def _apptainer_bind_args(paths: list[Path]) -> list[str]:
    args: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if path.is_file():
            candidate = path.parent
        else:
            candidate = path
        if not candidate.exists():
            continue
        resolved = _expand_path_no_resolve(candidate)
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        args.extend(["--bind", f"{key}:{key}:rw"])
    return args


def build_local_blender_command(
    *,
    blender_exe: str,
    preset: str | None,
    structure_path: Path,
    view_json_path: Path,
    style_json_path: Path | None,
    output_png: Path,
    custom_script: Path,
    run_manifest_path: Path | None = None,
    use_apptainer: bool,
    apptainer_image: str,
) -> list[str]:
    if use_apptainer and _is_macos():
        raise RunnerError("Apptainer execution is not supported on macOS. Use host Blender instead.")
    if use_apptainer:
        blender_exe = _normalize_apptainer_blender_executable(blender_exe)

    if style_json_path is None:
        raise RunnerError("JSON preset/style is required. Provide --style-json or --preset.")
    command = [
        blender_exe,
        "--background",
        "--python",
        str(custom_script),
        "--",
        "--structure",
        str(structure_path),
        "--view-json",
        str(view_json_path),
        "--style-json",
        str(style_json_path),
        "--output-png",
        str(output_png),
    ]
    if run_manifest_path is not None:
        command.extend(["--run-manifest", str(run_manifest_path)])

    if use_apptainer:
        if not apptainer_image:
            raise RunnerError("--use-apptainer was enabled but --apptainer-image is empty.")
        apptainer_exe = _resolve_apptainer_executable()
        bind_paths = [
            output_png.parent,
            structure_path.parent,
            view_json_path.parent,
            custom_script.parent,
            Path.cwd(),
            Path.home(),
        ]
        if style_json_path is not None:
            bind_paths.append(style_json_path.parent)

        bind_args = _apptainer_bind_args(bind_paths)
        command = [
            apptainer_exe,
            "exec",
            "--nv",
            *bind_args,
            str(Path(apptainer_image).expanduser()),
        ] + command

    return command


def _debug_process_text(result: subprocess.CompletedProcess[str]) -> str:
    return f"stdout:\n{(result.stdout or '').strip()}\nstderr:\n{(result.stderr or '').strip()}"


def _tail_text(value: str, max_chars: int = 10000) -> str:
    text = value or ""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _extract_gpu_info(stdout: str, stderr: str) -> list[str]:
    lines: list[str] = []
    for source in ((stdout or "").splitlines(), (stderr or "").splitlines()):
        for line in source:
            stripped = line.strip()
            if "Bridge Cycles GPU:" in stripped or "Bridge Custom Cycles GPU:" in stripped:
                lines.append(stripped)
    return lines[-12:]


def _apply_rough_style_overrides(style_doc: dict[str, Any]) -> dict[str, Any]:
    rough_doc = json.loads(json.dumps(style_doc))
    output = rough_doc.get("output")
    if not isinstance(output, dict):
        return rough_doc

    width = output.get("width")
    height = output.get("height")
    samples = output.get("samples")
    try:
        width_i = int(width)
    except Exception:
        width_i = 1600
    try:
        height_i = int(height)
    except Exception:
        height_i = 900
    try:
        samples_i = int(samples)
    except Exception:
        samples_i = 128

    output["width"] = max(320, width_i // 2)
    output["height"] = max(240, height_i // 2)
    output["samples"] = max(8, min(32, max(1, samples_i // 2)))
    rough_doc["output"] = output
    return rough_doc


def _apply_color_follow_override(style_doc: dict[str, Any], enabled: bool) -> dict[str, Any]:
    if not enabled and "color_follow" not in style_doc:
        return style_doc
    styled = json.loads(json.dumps(style_doc))
    cfg = styled.get("color_follow")
    if not isinstance(cfg, dict):
        cfg = {}
    cfg["enabled"] = enabled
    styled["color_follow"] = cfg
    return styled


def _apply_raw_view_override(style_doc: dict[str, Any], enabled: bool) -> dict[str, Any]:
    if not enabled:
        return style_doc
    styled = json.loads(json.dumps(style_doc))
    output = styled.get("output")
    if not isinstance(output, dict):
        output = {}
    output["raw_view"] = True
    styled["output"] = output
    return styled


def _apply_save_blend_override(style_doc: dict[str, Any], enabled: bool, blend_output: str) -> dict[str, Any]:
    if not enabled and not str(blend_output or "").strip():
        return style_doc
    styled = json.loads(json.dumps(style_doc))
    output = styled.get("output")
    if not isinstance(output, dict):
        output = {}
    if enabled:
        output["save_blend"] = True
    blend_value = str(blend_output or "").strip()
    if blend_value:
        output["save_blend"] = True
        output["blend_filename"] = blend_value
    styled["output"] = output
    return styled


def _resolve_output_blend(output_png: Path, style_doc: dict[str, Any] | None) -> Path | None:
    if not isinstance(style_doc, dict):
        return None
    output = style_doc.get("output")
    if not isinstance(output, dict):
        return None
    if not bool(output.get("save_blend", False)):
        return None

    configured = str(output.get("blend_filename", "") or "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_absolute():
            return configured_path
        return output_png.parent / configured_path
    return output_png.with_suffix(".blend")


def _style_entries(style_doc: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(style_doc, dict):
        return []
    styles = style_doc.get("styles")
    if not isinstance(styles, list):
        return []
    return [entry for entry in styles if isinstance(entry, dict)]


def _resolve_sticks_backend_name(style_doc: dict[str, Any] | None) -> str:
    for style_entry in _style_entries(style_doc):
        if str(style_entry.get("style", "")).strip().lower() == "sticks":
            return "mn_native_sticks"
    return "none"


def _resolve_polymer_cartoon_backend_name(style_doc: dict[str, Any] | None) -> str:
    for style_entry in _style_entries(style_doc):
        if str(style_entry.get("style", "")).strip().lower() != "cartoon":
            continue
        selection = style_entry.get("selection")
        if isinstance(selection, dict):
            if bool(selection.get("polymer")):
                return "mn_cartoon"
            query = str(selection.get("query", "")).strip().lower()
            if "polymer" in query:
                return "mn_cartoon"
    return "none"


def _style_entry_targets_ligand(style_entry: dict[str, Any]) -> bool:
    style_name = str(style_entry.get("name", "")).strip().lower()
    if "ligand" in style_name:
        return True
    selection = style_entry.get("selection")
    if not isinstance(selection, dict):
        return False
    if bool(selection.get("ligand")):
        return True
    query = str(selection.get("query", "")).strip().lower()
    if "ligand" in query:
        return True
    if "resn ptr" in query:
        return True
    return False


def _resolve_ligand_backend_name(style_doc: dict[str, Any] | None) -> str:
    for style_entry in _style_entries(style_doc):
        style_kind = str(style_entry.get("style", "")).strip().lower()
        if style_kind != "sticks":
            continue
        if _style_entry_targets_ligand(style_entry):
            return "mn_native_sticks"
    return "none"


def _style_source_from_path(path: Path) -> str:
    suffix = path.suffix.strip().lower()
    if suffix == ".cif":
        return "cif"
    if suffix == ".pdb":
        return "pdb"
    return suffix.lstrip(".") or "unknown"


def _write_run_manifest(path: Path, payload: dict[str, Any], *, chosen_structure_path: Path, style_doc: dict[str, Any] | None) -> None:
    dss_doc = payload.get("dss")
    dss_ran = bool(isinstance(dss_doc, dict) and dss_doc.get("ran", False))
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = _load_json(path)
            if isinstance(loaded, dict):
                existing = loaded
        except Exception:
            existing = {}
    polymer_backend = str(existing.get("polymer_backend", "")).strip()
    if not polymer_backend:
        polymer_backend = _resolve_polymer_cartoon_backend_name(style_doc)
    ligand_backend = str(existing.get("ligand_backend", "")).strip()
    if not ligand_backend:
        ligand_backend = _resolve_ligand_backend_name(style_doc)
    ligand_atom_spheres_value = existing.get("ligand_atom_spheres")
    if not isinstance(ligand_atom_spheres_value, bool):
        ligand_atom_spheres_value = False
    manifest: dict[str, Any] = dict(existing)
    manifest.update(
        {
        "structure_source": _style_source_from_path(chosen_structure_path),
        "chosen_structure_file": chosen_structure_path.name,
        "chosen_structure_path": str(chosen_structure_path),
        "dss_ran": dss_ran,
        "polymer_backend": polymer_backend,
        "ligand_backend": ligand_backend,
        "ligand_atom_spheres": bool(ligand_atom_spheres_value),
        # Backward-compatible aliases.
        "sticks_backend": ligand_backend if ligand_backend else _resolve_sticks_backend_name(style_doc),
        "polymer_cartoon_backend": polymer_backend,
        "generated_at_utc": _utc_now_iso(),
        }
    )
    if isinstance(dss_doc, dict):
        manifest["dss"] = dss_doc
    manifest["input_provenance"] = payload.get("provenance", {})
    manifest["structure_summary"] = payload.get("structure_summary", {})
    manifest["input_camera"] = payload.get("view", {})
    manifest["secondary_structure"] = payload.get("ss_hints", {}).get("counts", {})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _is_preset_1_gate_target(*, preset: str | None, style_json_path: str | None, style_doc: dict[str, Any] | None) -> bool:
    # Compatibility guard for the reference representation, never for a name
    # or output filename. Colors/materials/camera can change without losing it;
    # a different representation must not inherit the reference's requirements.
    if not isinstance(style_doc, dict):
        return False
    def layout(doc):
        return [(entry.get("style"), entry.get("selection")) for entry in _style_entries(doc)]
    reference = _load_json(_default_preset_style("preset_1_interface"))
    return layout(style_doc) == layout(reference)


def _enforce_preset_1_backend_gates(*, manifest: dict[str, Any], manifest_path: Path) -> None:
    structure_source = str(manifest.get("structure_source", "")).strip().lower()
    dss_ran = bool(manifest.get("dss_ran", False))
    polymer_backend = str(manifest.get("polymer_backend", "")).strip().lower()
    ligand_backend = str(manifest.get("ligand_backend", "")).strip().lower()
    ligand_atom_spheres = bool(manifest.get("ligand_atom_spheres", True))

    errors: list[str] = []
    if structure_source != "cif":
        errors.append(f"structure source must be CIF-first; got structure_source={structure_source or '<empty>'}")
    if not dss_ran:
        errors.append("secondary-structure assignment must run before export (dss_ran=false)")
    if polymer_backend != "mn_cartoon":
        errors.append(f"polymer backend must be cartoon secondary structure; got polymer_backend={polymer_backend or '<empty>'}")
    if ligand_backend != "mn_native_sticks":
        errors.append(f"ligand backend must be non-legacy native sticks; got ligand_backend={ligand_backend or '<empty>'}")
    if ligand_atom_spheres:
        errors.append("ligand atom spheres must be disabled; got ligand_atom_spheres=true")

    if errors:
        bullet = "\n".join(f"- {item}" for item in errors)
        raise RunnerError(
            "Preset gate failure for preset_1_interface.\n"
            f"{bullet}\n"
            f"Manifest: {manifest_path}"
        )
