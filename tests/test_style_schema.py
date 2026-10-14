from __future__ import annotations

import json
from pathlib import Path

import pytest

from blender_render import core

PACKAGE = Path(core.__file__).resolve().parent

from blender_render.bridge_runner import (
    CAMERA_KEYS,
    COLOR_FOLLOW_KEYS,
    LIGHT_KEYS,
    MATERIAL_KEYS,
    OUTPUT_KEYS,
    RENDERER_KEYS,
    REQUIRED_STYLE_TOP_KEYS,
    STYLE_KEYS,
    StyleValidationError,
    STYLE_TOP_LEVEL_OPTIONAL_KEYS,
    WORLD_KEYS,
    validate_style_document,
)


def _example_style() -> dict:
    path = PACKAGE / "example_full_custom.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _preset_style() -> dict:
    path = PACKAGE / "style_preset_1_opaque_pastel.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_valid_full_custom_style_passes_validation():
    style = _example_style()
    validated = validate_style_document(style)
    assert validated["version"] == "1.0"
    assert validated["styles"]


def test_portable_preset_style_passes_validation():
    style = _preset_style()
    validated = validate_style_document(style)
    assert validated["output"]["filename"] == "01_opaque_pastel_ses_interface.png"
    assert any(entry.get("name") == "chain_a_cartoon" for entry in validated["styles"])


def test_ptr_style_uses_native_sticks_contract():
    style = _preset_style()
    ptr_styles = [entry for entry in style["styles"] if entry.get("name") == "ligand_ptr_sticks"]
    assert ptr_styles
    ptr_style = ptr_styles[0]
    assert ptr_style["style"] == "sticks"
    assert ptr_style["sticks_radius"] > 0
    assert "sticks_backend" not in ptr_style
    assert "sticks_fallback_radius_scale" not in ptr_style
    assert "sticks_fallback_atom_scale" not in ptr_style
    assert "sticks_fallback_bond_resolution" not in ptr_style

    validated = validate_style_document(style)
    validated_ptr = [entry for entry in validated["styles"] if entry.get("name") == "ligand_ptr_sticks"][0]
    assert validated_ptr["style"] == "sticks"
    assert validated_ptr["sticks_radius"] > 0


def test_preset_1_covers_polymer_nucleic_ligand_ion_water():
    style = _preset_style()
    style_names = {entry.get("name", "") for entry in style.get("styles", []) if isinstance(entry, dict)}
    assert "chain_a_cartoon" in style_names
    assert "nucleic_cartoon" in style_names
    assert "ligand_ptr_sticks" in style_names
    assert "ions_spheres" in style_names
    assert "water_spheres" in style_names


@pytest.mark.parametrize(
    "preset_path",
    sorted(PACKAGE.glob("style_preset_*.json")),
)
def test_all_presets_use_supported_key_set_only(preset_path: Path):
    style = json.loads(preset_path.read_text(encoding="utf-8"))
    validate_style_document(style)

    allowed_top = set(REQUIRED_STYLE_TOP_KEYS) | set(STYLE_TOP_LEVEL_OPTIONAL_KEYS)
    assert set(style.keys()) <= allowed_top
    assert set(style["output"].keys()) <= set(OUTPUT_KEYS)
    assert set(style["renderer"].keys()) <= set(RENDERER_KEYS)
    assert set(style["camera"].keys()) <= set(CAMERA_KEYS)
    assert set(style["world"].keys()) <= set(WORLD_KEYS)
    if "color_follow" in style:
        assert set(style["color_follow"].keys()) <= set(COLOR_FOLLOW_KEYS)
    for light in style["lights"]:
        assert set(light.keys()) <= set(LIGHT_KEYS)
    for material in style["materials"].values():
        assert set(material.keys()) <= set(MATERIAL_KEYS)
    for style_entry in style["styles"]:
        assert set(style_entry.keys()) <= set(STYLE_KEYS)
        assert style_entry.get("style") != "ball_and_stick"
        assert "sticks_backend" not in style_entry
        assert "sticks_fallback_radius_scale" not in style_entry
        assert "sticks_fallback_atom_scale" not in style_entry
        assert "sticks_fallback_bond_resolution" not in style_entry


def test_missing_required_top_level_keys_fails_with_actionable_error():
    style = _example_style()
    del style["lights"]

    with pytest.raises(StyleValidationError) as exc:
        validate_style_document(style)

    message = str(exc.value)
    assert "Missing required top-level keys" in message
    assert "lights" in message


def test_invalid_selection_and_style_entries_fail_validation():
    style = _example_style()
    style["styles"][0]["selection"] = {"chain": 12}
    style["styles"][0]["material"] = "missing_material"
    style["styles"][0]["style"] = "not_supported"

    with pytest.raises(StyleValidationError) as exc:
        validate_style_document(style)

    message = str(exc.value)
    assert "styles[0].selection.chain" in message
    assert "styles[0].material" in message
    assert "styles[0].style" in message


def test_ligand_cutout_and_proximity_keys_validate():
    style = _example_style()
    style["styles"][0]["selection"]["within_ligand_radius"] = 6.0
    style["styles"][0]["selection"]["exclude_within_ligand_radius"] = 2.0
    style["styles"][0]["selection"]["expand_to_residue"] = True
    style["styles"][0]["ligand_cutout_radius"] = 3.0
    style["styles"][0]["ligand_cutout_offset"] = 0.15

    validated = validate_style_document(style)

    selection = validated["styles"][0]["selection"]
    assert selection["within_ligand_radius"] == 6.0
    assert selection["exclude_within_ligand_radius"] == 2.0
    assert selection["expand_to_residue"] is True
    assert validated["styles"][0]["ligand_cutout_radius"] == 3.0
    assert validated["styles"][0]["ligand_cutout_offset"] == 0.15


def test_invalid_ligand_cutout_and_proximity_values_fail_validation():
    style = _example_style()
    style["styles"][0]["selection"]["within_ligand_radius"] = -1
    style["styles"][0]["selection"]["exclude_within_ligand_radius"] = "near"
    style["styles"][0]["selection"]["expand_to_residue"] = "yes"
    style["styles"][0]["ligand_cutout_radius"] = -2
    style["styles"][0]["ligand_cutout_offset"] = "offset"

    with pytest.raises(StyleValidationError) as exc:
        validate_style_document(style)

    message = str(exc.value)
    assert "styles[0].selection.within_ligand_radius" in message
    assert "styles[0].selection.exclude_within_ligand_radius" in message
    assert "styles[0].selection.expand_to_residue" in message
    assert "styles[0].ligand_cutout_radius" in message
    assert "styles[0].ligand_cutout_offset" in message


def test_plane_cutout_keys_validate_with_preset_frame():
    style = _example_style()
    style["styles"][0]["ligand_cutout_mode"] = "planes"
    style["styles"][0]["ligand_cutout_frame_mode"] = "preset"
    style["styles"][0]["ligand_cutout_frame_axes"] = {
        "x": [1.0, 0.0, 0.0],
        "y": [0.0, 1.0, 0.0],
        "z": [0.0, 0.0, 1.0],
    }
    style["styles"][0]["ligand_cutout_planes_enabled"] = {
        "left": True,
        "right": True,
        "top": True,
        "bottom": True,
        "back": True,
    }
    style["styles"][0]["ligand_cutout_plane_scales"] = {
        "left": 1.2,
        "right": 1.0,
        "top": 1.1,
        "bottom": 0.9,
        "back": 0.8,
    }
    style["styles"][0]["ligand_cutout_seal_mode"] = "boundary_fill"
    style["styles"][0]["ligand_cutout_seal_depth_scale"] = 0.3
    style["styles"][0]["ligand_cutout_seal_region_scale"] = 2.5

    validated = validate_style_document(style)
    cutout = validated["styles"][0]
    assert cutout["ligand_cutout_mode"] == "planes"
    assert cutout["ligand_cutout_frame_mode"] == "preset"
    assert cutout["ligand_cutout_planes_enabled"]["left"] is True
    assert cutout["ligand_cutout_plane_scales"]["back"] == 0.8


def test_plane_cutout_preset_mode_requires_axes():
    style = _example_style()
    style["styles"][0]["ligand_cutout_mode"] = "planes"
    style["styles"][0]["ligand_cutout_frame_mode"] = "preset"
    style["styles"][0].pop("ligand_cutout_frame_axes", None)

    with pytest.raises(StyleValidationError) as exc:
        validate_style_document(style)

    assert "ligand_cutout_frame_axes" in str(exc.value)


def test_plane_cutout_invalid_plane_keys_and_seal_values_fail_validation():
    style = _example_style()
    style["styles"][0]["ligand_cutout_mode"] = "planes"
    style["styles"][0]["ligand_cutout_planes_enabled"] = {"left": True, "front": True}
    style["styles"][0]["ligand_cutout_plane_scales"] = {"left": 1.0, "right": -1.0}
    style["styles"][0]["ligand_cutout_seal_mode"] = "invalid_mode"
    style["styles"][0]["ligand_cutout_seal_depth_scale"] = 0

    with pytest.raises(StyleValidationError) as exc:
        validate_style_document(style)

    message = str(exc.value)
    assert "ligand_cutout_planes_enabled" in message
    assert "ligand_cutout_plane_scales.right" in message
    assert "ligand_cutout_seal_mode" in message
    assert "ligand_cutout_seal_depth_scale" in message


@pytest.mark.parametrize("threads", [0, -1, 65, 2.5, True, "2"])
def test_invalid_render_thread_limit_is_rejected(threads):
    style = _example_style()
    style["renderer"]["threads"] = threads
    with pytest.raises(StyleValidationError):
        validate_style_document(style)


def test_cpu_smoke_options_resolve_exact_limits():
    from blender_render.spec import resolve_options
    options = json.loads((Path(__file__).resolve().parents[1] / "examples/cpu-smoke-options.json").read_text())
    payload = {"view": {"pymol_view": [1,0,0,0,1,0,0,0,1,0,0,-50,0,0,0,1,100,0],
                        "viewport_px": [800,600], "fov_deg":20, "orthoscopic":False}}
    style = resolve_options(options, payload)["style_doc"]
    assert {k: style["output"][k] for k in ("width", "height", "samples")} == {"width":128, "height":128, "samples":4}
    assert style["renderer"]["device"] == "CPU"
    assert style["renderer"]["require_gpu"] is False
    assert style["renderer"]["threads"] == 2
