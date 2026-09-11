import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from blender_render import authoring, core, developer
from blender_render.presets import preset_path, required_capabilities
from blender_render.spec import resolve_options, style_digest


@pytest.fixture
def style():
    doc = core.load_and_validate_style_json(preset_path("pastel"))
    doc["camera"].update(mode="override", projection="PERSP", fit=True,
                         depth_of_field={"focus_distance_angstrom": 100, "f_stop": 0.5})
    return doc


@pytest.mark.parametrize("field,value", [("focus_distance_angstrom", 0), ("focus_distance_angstrom", 1000001),
    ("focus_distance_angstrom", "100"), ("f_stop", 0), ("f_stop", 129), ("f_stop", True), ("aperture", 2)])
def test_invalid_focus_controls(style, field, value):
    style["camera"]["depth_of_field"][field] = value
    with pytest.raises(core.StyleValidationError, match="camera.depth_of_field"):
        resolve_options({"style_doc": style}, {})


@pytest.mark.parametrize("change", ["orthographic", "saved_pose", "viewport", "missing_distance"])
def test_unsupported_focus_combinations(style, change):
    if change == "orthographic":
        style["camera"]["projection"] = "ORTHO"
    elif change == "saved_pose":
        style["camera"]["mode"] = "pymol"
    elif change == "viewport":
        style["output"]["raw_view"] = True
    else:
        del style["camera"]["depth_of_field"]["focus_distance_angstrom"]
    with pytest.raises(core.StyleValidationError, match="depth_of_field"):
        resolve_options({"style_doc": style}, {})


def test_focus_resolution_scene_opt_in_and_legacy(style):
    overrides = {"camera": style["camera"]}
    full = resolve_options({"style_doc": style}, {})
    named = resolve_options({"preset": "pastel", "overrides": overrides}, {})
    assert full["style_doc"] == named["style_doc"]
    assert "camera.depth_of_field" in full["render_spec"]["required_capabilities"]
    assert full["save_blend"] is False
    assert resolve_options({"style_doc": style, "save_blend": True}, {})["save_blend"] is True
    for preset in ("pastel", "green_paint"):
        legacy = resolve_options({"preset": preset}, {})
        assert "camera.depth_of_field" not in legacy["render_spec"]["required_capabilities"]
        assert "depth_of_field" not in legacy["style_doc"]["camera"]


def test_invalid_output_shape_with_depth_of_field(style):
    style["output"] = []
    with pytest.raises(core.StyleValidationError, match="output"):
        core.validate_style_document(style)


def test_snapshot_tracks_python_and_assets(tmp_path):
    package = tmp_path / "checkout/blender_render"
    package.mkdir(parents=True)
    (package / "blender_custom_render.py").write_text("value = 1\n")
    (package / "style.json").write_text('{}')
    first = developer.snapshot_source(package.parent, tmp_path / "first")
    (package / "blender_custom_render.py").write_text("value = 2\n")
    second = developer.snapshot_source(package.parent, tmp_path / "second")
    assert first["sha256"] != second["sha256"]
    assert (tmp_path / "first/blender_render/blender_custom_render.py").read_text() == "value = 1\n"
    (package / "style.json").write_text('{"changed":true}')
    third = developer.snapshot_source(package.parent, tmp_path / "third")
    assert second["sha256"] != third["sha256"]


def test_preview_default_source_is_working_checkout_not_installed_package(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = developer.parser().parse_args(["preview", "bundle.json", "--output", "preview", "--blender", "blender",
                                         "--runtime-path", "runtime", "--ccd", "components.bcif"])
    assert args.checkout == tmp_path


def test_snapshot_rejects_concurrent_edit_and_symlink(tmp_path, monkeypatch):
    package = tmp_path / "checkout/blender_render"
    package.mkdir(parents=True)
    script = package / "blender_custom_render.py"
    script.write_text("value = 1\n")
    original = developer.shutil.copyfile
    def changing_copy(src, dst):
        result = original(src, dst)
        script.write_text("value = 2\n")
        return result
    monkeypatch.setattr(developer.shutil, "copyfile", changing_copy)
    with pytest.raises(RuntimeError, match="changed while snapshotting"):
        developer.snapshot_source(package.parent, tmp_path / "snapshot")
    (package / "linked.py").symlink_to(script)
    with pytest.raises(ValueError, match="symlinks"):
        developer.source_files(package)


def test_isolated_state_does_not_inherit_service_or_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "must-not-be-copied")
    monkeypatch.setenv("PYTHONPATH", "/old/checkout")
    monkeypatch.setenv("BLENDER_RENDER_HOME", "/production")
    env = developer.isolated_environment(tmp_path)
    assert "MODAL_TOKEN_SECRET" not in env and "PYTHONPATH" not in env
    assert env["BLENDER_RENDER_HOME"] == str(tmp_path / "config")
    assert env["HOME"] == str(tmp_path / "home")
    assert env["BLENDER_USER_RESOURCES"] == str(tmp_path / "blender-resources")


def test_save_restart_reuse_and_revision(style, tmp_path):
    saved = authoring.save_preset("user/macro", style, tmp_path, camera_policy="authored", description="Local macro focus")
    command = [sys.executable, "-B", "-m", "blender_render.cli", "dev", "--catalog-dir", str(tmp_path),
               "inspect", "user/macro"]
    # A second interpreter reads persisted bytes; no in-process registry state.
    restarted = json.loads(subprocess.check_output(command, text=True))
    assert restarted == {k: v for k, v in saved.items() if k != "path"}
    local = authoring.resolve_authored({"preset": "user/macro"}, {}, tmp_path)
    full = resolve_options({"style_doc": style}, {})
    assert local["style_doc"] == full["style_doc"]
    assert local["render_spec"]["preset_source"]["id"] == "user/macro"
    with pytest.raises(ValueError, match="already exists"):
        authoring.save_preset("user/macro", style, tmp_path, camera_policy="authored", description="Again")
    style["camera"]["depth_of_field"]["f_stop"] = 4
    new = authoring.save_preset("user/macro", style, tmp_path, camera_policy="authored", description="Revision", replace=True)
    assert new["revision"] == "2" and new["sha256"] != saved["sha256"]
    assert len(authoring.list_presets(tmp_path)) == 15


@pytest.mark.parametrize("identifier", ["builtin/pastel", "macro", "user/../macro", "user/a/b", "project/../../x"])
def test_local_namespace_is_explicit(style, tmp_path, identifier):
    with pytest.raises(ValueError, match="Local preset ID"):
        authoring.save_preset(identifier, style, tmp_path, camera_policy="authored", description="Invalid")


def test_preset_tamper_and_user_project_collision(style, tmp_path):
    authoring.save_preset("user/macro", style, tmp_path, camera_policy="authored", description="User")
    authoring.save_preset("project/macro", style, tmp_path, camera_policy="authored", description="Project")
    assert authoring.inspect_preset("project/macro", tmp_path)["description"] == "Project"
    path = tmp_path / "user/macro.json"
    doc = json.loads(path.read_text())
    doc["style_doc"]["camera"]["depth_of_field"]["f_stop"] = 5
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="digest mismatch"):
        authoring.inspect_preset("user/macro", tmp_path)


def test_reference_routes_and_never_sends_pixels(tmp_path):
    from PIL import Image
    image = tmp_path / "reference.png"
    Image.new("RGB", (12, 12), (170, 200, 220)).save(image)
    observations = {"representations": ["cartoon", "sticks"], "material_family": "clay",
                    "palette": ["#AACCDD"], "required_capabilities": [],
                    "overrides": {"materials": {"inner_chain_a": {"base_color": "#AACCDD"}}}}
    high = authoring.reference_brief(image, observations)
    assert high["preset_decision"]["route"] == "preset-overrides"
    chosen = core.load_and_validate_style_json(preset_path(high["preset_decision"]["preset_id"]))
    assert "inner_chain_a" in {entry["material"] for entry in chosen["styles"]}
    assert high["visual_brief"]["reference"]["sha256"] == developer.digest_file(image)
    assert "pixels" not in json.dumps(high) and str(image) not in json.dumps(high)
    observations["required_capabilities"] = ["camera.depth_of_field", "material.directional_ink_hatching"]
    low = authoring.reference_brief(image, observations)["preset_decision"]
    assert low["fit"] == "low" and low["new_preset_recommended"]
    assert low["unsupported_requirements"] == ["material.directional_ink_hatching"]
    assert low["route"] == "developer/new-preset"
    observations["overrides"] = {"camera": {"not_implemented": 1}}
    with pytest.raises(core.StyleValidationError, match="not_implemented"):
        authoring.reference_brief(image, observations)
    observations["overrides"] = {"materials": {"unused": {"base_color": "#123456"}}}
    with pytest.raises(ValueError, match="materials used"):
        authoring.reference_brief(image, observations)
