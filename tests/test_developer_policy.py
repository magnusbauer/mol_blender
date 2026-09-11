"""B regression coverage: named defaults, captured bytes and snapshot validation."""
import copy
import json
from pathlib import Path

import pytest

from blender_render import authoring, core, developer
from blender_render.presets import preset_path
from blender_render.spec import resolve_options


@pytest.fixture
def style():
    return core.load_and_validate_style_json(preset_path("pastel"))


@pytest.fixture
def bundle():
    return {"version": "1.0", "cif_text": "data_test\nloop_\n_atom_site.group_PDB\n_atom_site.id\nATOM 1\n",
            "view": {"pymol_view": [1,0,0,0,1,0,0,0,1,0,0,-50,0,0,0,1,100,0],
                     "viewport_px": [900,500], "fov_deg": 20, "orthoscopic": False}, "dss": {"ran": True}}


@pytest.mark.parametrize("namespace", ["user", "project"])
@pytest.mark.parametrize("policy", ["preserve_input", "authored"])
@pytest.mark.parametrize("preserve", [None, True, False])
def test_named_policy_parity(style, bundle, tmp_path, namespace, policy, preserve):
    name = namespace + "/pastel"
    authoring.save_preset(name, style, tmp_path, description="Clone", camera_policy=policy)
    options = {} if preserve is None else {"preserve_view": preserve}
    local = authoring.resolve_authored({"preset": name, **options}, bundle, tmp_path)
    builtin = resolve_options({"preset": "pastel", "preserve_view": policy == "preserve_input", **options}, bundle)
    assert local["style_doc"] == builtin["style_doc"]
    assert local["render_spec"]["sha256"] == builtin["render_spec"]["sha256"]
    assert local["render_spec"]["preset_source"]["camera_policy"] == policy
    assert local["preset"] == name


def test_save_default_and_explicit_camera_win(style, bundle, tmp_path):
    saved = authoring.save_preset("user/pastel", style, tmp_path, description="Clone")
    assert saved["format"] == 2 and saved["camera_policy"] == "preserve_input"
    edits = {"camera": {"mode": "override", "direction_source": "preset", "yaw_deg": 30}}
    for preserve in (True, False):
        named = authoring.resolve_authored({"preset": "user/pastel", "preserve_view": preserve, "overrides": edits}, bundle, tmp_path)
        builtin = resolve_options({"preset": "pastel", "preserve_view": preserve, "overrides": edits}, bundle)
        assert named["style_doc"] == builtin["style_doc"]
    full = authoring.resolve_authored({"preset": "user/missing", "style_doc": style, "preserve_view": True}, bundle, tmp_path)
    assert full["style_doc"]["camera"] == style["camera"]


def test_dof_cannot_be_silently_discarded(style, bundle, tmp_path):
    style["camera"].update(mode="override", projection="PERSP", depth_of_field={"focus_distance_angstrom": 82})
    with pytest.raises(ValueError, match="cannot discard"):
        authoring.save_preset("user/macro", style, tmp_path, description="Macro")
    assert not (tmp_path / "user").exists()
    authoring.save_preset("user/macro", style, tmp_path, description="Macro", camera_policy="authored")
    with pytest.raises(ValueError, match="cannot discard"):
        authoring.resolve_authored({"preset": "user/macro", "preserve_view": True}, bundle, tmp_path)
    explicit = authoring.resolve_authored({"preset": "user/macro", "preserve_view": True,
        "overrides": {"camera": {"depth_of_field": {"f_stop": 8}}}}, bundle, tmp_path)
    assert explicit["style_doc"]["camera"]["depth_of_field"]["f_stop"] == 8


def test_legacy_read_and_explicit_replacement(style, bundle, tmp_path):
    saved = authoring.save_preset("user/old", style, tmp_path, description="Old", camera_policy="authored")
    path = Path(saved.pop("path"))
    saved = json.loads(path.read_text())  # Stored revisions remain integers.
    saved.pop("camera_policy")
    saved["format"] = 1
    raw = json.dumps(saved).encode()
    path.write_bytes(raw)
    inspected = authoring.inspect_preset("user/old", tmp_path)
    assert inspected["camera_policy"] == "authored"
    assert inspected["camera_policy_source"] == "legacy_format_1"
    old = authoring.resolve_authored({"preset": "user/old"}, bundle, tmp_path)
    assert old["style_doc"] == resolve_options({"style_doc": style}, bundle)["style_doc"]
    assert path.read_bytes() == raw
    upgraded = authoring.save_preset("user/old", style, tmp_path, description="Updated", replace=True)
    assert upgraded["format"] == 2 and upgraded["revision"] == "2"
    assert upgraded["camera_policy"] == "preserve_input"


def test_capture_does_not_use_parent_validator_and_is_immutable(style, bundle, tmp_path, monkeypatch):
    authoring.save_preset("user/pastel", style, tmp_path / "catalog", description="Clone")
    original = (tmp_path / "catalog/user/pastel.json").read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail("Parent interpreted the preset")
    with monkeypatch.context() as scoped:
        scoped.setattr(authoring, "inspect_preset", forbidden)
        scoped.setattr(authoring, "validate_style_document", forbidden)
        captured = developer.capture_preset("user/pastel", tmp_path / "catalog", tmp_path / "snapshot")
    (tmp_path / "catalog/user/pastel.json").write_text("later edit")
    assert (tmp_path / "snapshot/user/pastel.json").read_bytes() == original
    assert captured["sha256"] == developer.digest_file(tmp_path / "snapshot/user/pastel.json")
    assert authoring.resolve_authored({"preset": "user/pastel"}, bundle, tmp_path / "snapshot")["style_doc"] == resolve_options({"preset": "pastel"}, bundle)["style_doc"]


@pytest.mark.parametrize("identifier", ["user/../../outside", "project/../x", "user/x/y", "user/UPPER"])
def test_capture_rejects_unsafe_identifier(tmp_path, identifier):
    with pytest.raises(ValueError, match="Local preset ID"):
        developer.capture_preset(identifier, tmp_path, tmp_path / "copy")


def test_capture_rejects_catalog_escape(tmp_path):
    (tmp_path / "catalog").mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "catalog/user").symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(ValueError, match="escapes catalog"):
        developer.capture_preset("user/name", tmp_path / "catalog", tmp_path / "copy")


@pytest.mark.parametrize("change,match", [
    (lambda d: [], "identity/format"),
    (lambda d: {**d, "id": "user/wrong"}, "identity/format"),
    (lambda d: {**d, "sha256": "wrong"}, "digest mismatch"),
    (lambda d: {k:v for k,v in d.items() if k != "camera_policy"}, "camera_policy"),
    (lambda d: {**d, "camera_policy": "guess"}, "camera_policy"),
    (lambda d: {**d, "revision": True}, "metadata"),
])
def test_invalid_envelope(style, tmp_path, change, match):
    saved = authoring.save_preset("user/test", style, tmp_path, description="Test")
    path = Path(saved.pop("path"))
    saved = json.loads(path.read_text())  # Stored revisions remain integers.
    path.write_text(json.dumps(change(saved)))
    with pytest.raises(ValueError, match=match):
        authoring.inspect_preset("user/test", tmp_path)


def test_preview_flag_defaults():
    required = ["preview", "bundle", "--output", "out", "--blender", "blender", "--runtime-path", "runtime", "--ccd", "ccd"]
    assert developer.parser().parse_args(required).preserve_view is None
    assert developer.parser().parse_args([*required, "--preserve-view"]).preserve_view is True
    assert developer.parser().parse_args([*required, "--no-preserve-view"]).preserve_view is False
    assert developer.parser().parse_args(["save", "user/x", "style", "--description", "x"]).camera_policy == "preserve_input"
