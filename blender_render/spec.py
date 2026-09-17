"""Resolve existing style JSON once before admission; backends consume its snapshot.

Objects merge recursively, arrays replace. Full JSON is authoritative. For named
presets the saved pose is the default unless catalog policy selects the authored
camera. Explicit camera JSON wins over preserve_view, which wins over catalog
policy. preserve_view=False selects the preset camera plus edits. Explicit output values
beat viewport defaults. Convenience options beat JSON; resource policy runs last.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

from . import core
from .presets import DEFAULT_PRESET, PRESET_STYLE_FILENAMES, preset_id, required_capabilities

OPTION_KEYS = frozenset({"preset", "style_doc", "overrides", "rough", "raw_view", "color_follow", "save_blend",
                        "width", "height", "samples", "transparent_background", "preserve_view", "keep_temp", "animation", "duration", "fps", "rock_angle", "gif"})
_FLAGS = ("rough", "raw_view", "color_follow", "save_blend", "transparent_background", "preserve_view", "keep_temp")


@dataclass(frozen=True)
class PresetDefinition:
    """Library-only catalog input, never an accepted render-request field."""
    identifier: str
    style_doc: dict
    source: dict
    camera_policy: str = "preserve_input"


def validate_camera_policy(policy: str, style: dict) -> None:
    if policy not in ("preserve_input", "authored"):
        raise ValueError("camera_policy must be preserve_input or authored")
    if policy == "preserve_input" and "depth_of_field" in style.get("camera", {}):
        raise ValueError("preserve_input cannot discard camera.depth_of_field; use the authored camera policy")


def builtin_definition(identifier=None) -> PresetDefinition:
    identifier = preset_id(identifier or DEFAULT_PRESET)
    return PresetDefinition(identifier,
        core.load_and_validate_style_json(core._default_preset_style(identifier)),
        {"kind": "builtin", "id": identifier, "revision": "1", "source_json": PRESET_STYLE_FILENAMES[identifier]})


def style_digest(style: dict) -> str:
    return hashlib.sha256(json.dumps(style, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _merge(base: dict, overrides: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _check_options(options: dict) -> None:
    if not isinstance(options, dict):
        raise ValueError("Render options must be an object.")
    unknown = set(options) - OPTION_KEYS
    if unknown:
        raise ValueError("Unsupported render options: " + ", ".join(sorted(unknown)))
    for key in _FLAGS:
        if key in options and not isinstance(options[key], bool):
            raise ValueError(key + " must be a boolean")
    for key in ("width", "height", "samples"):
        if key in options and (type(options[key]) is not int or options[key] < 1):
            raise ValueError(key + " must be a positive integer")
    for key in ("style_doc", "overrides"):
        if key in options and not isinstance(options[key], dict):
            raise ValueError(key + " must be an object")


def resolve_options(options: dict, payload: dict, *, public: bool = False, timeout: int = 600,
                    preset_definition: PresetDefinition | None = None, allow_animation: bool = False) -> dict:
    """Return the existing worker request shape plus immutable-spec metadata.

    No files, processes, services or allocations are created here. The caller
    owns input validation; the current normalized export payload is the bundle.
    """
    _check_options(options)
    options = copy.deepcopy(options)
    custom = "style_doc" in options
    if custom:
        identifier, style, source, policy = "", options["style_doc"], {"kind": "full_json"}, "authored"
    elif preset_definition is not None:
        identifier = preset_definition.identifier
        if options.get("preset") != identifier:
            raise ValueError("Preset definition identity does not match requested preset")
        style = copy.deepcopy(preset_definition.style_doc)
        source = copy.deepcopy(preset_definition.source)
        policy = preset_definition.camera_policy
        if policy not in ("preserve_input", "authored"):
            raise ValueError("camera_policy must be preserve_input or authored")
    else:
        definition = builtin_definition(options.get('preset'))
        identifier, style, source, policy = definition.identifier, definition.style_doc, definition.source, definition.camera_policy
    core.validate_style_document(style)
    overrides = options.get("overrides", {})
    # Validate the authored document before convenience or policy normalization
    # can hide an invalid/unknown value.
    style = _merge(style, overrides)
    camera_edits = overrides.get("camera", {})
    if isinstance(camera_edits, dict) and {"yaw_deg", "pitch_deg"} & camera_edits.keys():
        # An explicit absolute angle selects its consumer, rather than inheriting
        # a saved-view direction which would silently ignore the angle.
        if "direction_source" not in camera_edits:
            style["camera"]["direction_source"] = "preset"
        if "mode" not in camera_edits:
            style["camera"]["mode"] = "override"
    if overrides:
        core.validate_style_document(style)
    explicit_output = set(style["output"]) if custom else set(overrides.get("output", {}))
    explicit_output.update(key for key in ("width", "height", "samples", "save_blend") if key in options)
    explicit_camera = custom or bool(overrides.get("camera"))
    output = style["output"]
    preserve = options.get("preserve_view", policy == "preserve_input") and not explicit_camera
    if preserve:
        validate_camera_policy("preserve_input", style)
        viewport = payload.get("view", {}).get("viewport_px", [output["width"], output["height"]])
        if (not isinstance(viewport, list) or len(viewport) != 2
                or not all(type(v) in (int, float) and 0 < v <= 32768 for v in viewport)):
            raise ValueError("Invalid PyMOL viewport dimensions.")
        width = options.get("width", output["width"] if "width" in explicit_output or not public else 2048)
        height = options.get("height", output["height"] if "height" in explicit_output else max(1, round(width * viewport[1] / viewport[0])))
        if public and not {"width", "height"} & explicit_output:
            scale = min(1, 2048 / max(width, height))
            width, height = max(1, round(width * scale)), max(1, round(height * scale))
        output.update(width=width, height=height)
        style["camera"] = {"mode": "pymol", "world_scale": 0.01, "preserve_framing": True}
    for key in ("width", "height", "samples", "save_blend", "raw_view"):
        if key in options:
            output[key] = options[key]
    output.setdefault("save_blend", False)
    output.setdefault("raw_view", False)
    if "transparent_background" in options:
        style["renderer"]["transparent_background"] = options["transparent_background"]
    if "color_follow" in options:
        style = core._apply_color_follow_override(style, options["color_follow"])
    from .animation import resolve as resolve_animation
    animation = resolve_animation(options, output)
    if public and animation and not allow_animation:
        raise ValueError("Shared hosted animation is not enabled")
    # Validate the requested resources before rough-mode performance reductions.
    if public:
        if output.get("raw_view"):
            raise ValueError("The public service supports ray-traced still images only.")
        if options.get("keep_temp"):
            raise ValueError("Public jobs always clean temporary files.")
        if max(output["width"], output["height"]) > 4096 or output["samples"] > 512:
            raise ValueError("Free renders are limited to 4096 pixels per dimension and 512 samples.")
        if style["renderer"]["engine"] != "CYCLES":
            raise ValueError("Public renders use Cycles.")
        style["renderer"].update(device="GPU", compute_device_type="AUTO", require_gpu=True)
    if options.get("rough"):
        style = core._apply_rough_style_overrides(style)
    # Transport controls destinations; a style can never choose server paths.
    style["output"].update(filename="render.png", format="PNG", blend_filename="render.blend")
    core.validate_style_document(style)
    return {
        "preset": identifier, "style_doc": style, "output_name": "render.png", "job_timeout_seconds": timeout,
        "keep_temp": options.get("keep_temp", False), "save_blend": style["output"]["save_blend"],
        # Already consumed into JSON. Legacy worker/gateway adapters must not
        # apply rough twice or re-enable options after resolution.
        "rough": False, "raw_view": style["output"]["raw_view"],
        "color_follow": style.get("color_follow", {}).get("enabled", False),
        "render_spec": {"schema_version": style["version"], "sha256": style_digest(style),
                        "preset_source": source, "required_capabilities": required_capabilities(style)},
    }
