"""Milestone A contracts. Fake worker assertions are not visual-render evidence."""
import asyncio
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from blender_render import cli, core, rendering
from blender_render.presets import catalog, preset_id
from blender_render.service import prepare_options
from blender_render.spec import resolve_options, style_digest


@pytest.fixture
def bundle():
    return {"version": "1.0", "cif_text": "data_test\nloop_\n_atom_site.group_PDB\n_atom_site.id\nATOM 1\n",
            "view": {"pymol_view": [1,0,0,0,1,0,0,0,1,0,0,-50,0,0,0,1,100,0],
                     "viewport_px": [800,600], "fov_deg": 20, "orthoscopic": False}, "dss": {"ran": True}}


def preset(name="preset_1_interface"):
    return core.load_and_validate_style_json(core._default_preset_style(name))


def surface_style():
    style = preset()
    # Deliberately retain the old output filename, plus adapter default preset.
    style["styles"] = [{"name": "surface", "style": "surface", "selection": {"polymer": True},
                        "material": next(iter(style["materials"])), "quality": 2}]
    return style


@pytest.mark.parametrize("name", ["preset_1_interface", "dried_paint_green"])
def test_full_json_and_preset_overrides_are_equivalent(name, bundle):
    style = preset(name)
    material = next(iter(style["materials"]))
    camera = {"mode": "override", "direction_source": "preset", "yaw_deg": 31,
              "pitch_deg": 12, "lens_mm": 71, "distance_scale": 2.4, "preserve_framing": False}
    lights = [copy.deepcopy(style["lights"][0])]
    lights[0]["energy"] = 123
    edits = {"camera": camera, "materials": {material: {"base_color": "#ABCDEF"}},
             "lights": lights, "output": {"width": 720, "height": 480, "samples": 16, "save_blend": True}}
    full = copy.deepcopy(style)
    full["camera"].update(camera)
    full["materials"][material]["base_color"] = "#ABCDEF"
    full["lights"] = lights
    full["output"].update(edits["output"])
    before = copy.deepcopy(edits)
    a = resolve_options({"style_doc": full}, bundle)
    b = resolve_options({"preset": name, "overrides": edits}, bundle)
    assert a["style_doc"] == b["style_doc"]
    assert a["render_spec"]["sha256"] == b["render_spec"]["sha256"]
    assert len(b["style_doc"]["lights"]) == 1  # arrays replace, never merge by index
    assert b["render_spec"]["preset_source"]["id"] == name
    assert a["preset"] == "" and a["render_spec"]["preset_source"] == {"kind": "full_json"}
    assert edits == before and full["output"]["filename"] == style["output"]["filename"]


def test_saved_pose_default_and_explicit_camera_precedence(bundle):
    default = resolve_options({}, bundle)["style_doc"]
    assert default["camera"] == {"mode": "pymol", "world_scale": .01, "preserve_framing": True}
    assert default["output"]["height"] == round(default["output"]["width"] * .75)
    assert resolve_options({"preserve_view": False}, bundle)["style_doc"]["camera"] == preset()["camera"]
    document = preset()
    document["camera"].update(mode="override", direction_source="preset", lens_mm=85, yaw_deg=46, shift_x=.2)
    # Even explicitly passing the preservation default cannot erase explicit JSON.
    custom = resolve_options({"style_doc": document, "preserve_view": True}, bundle)
    assert custom["style_doc"]["camera"] == document["camera"]
    assert custom["style_doc"]["output"]["height"] == document["output"]["height"]
    partial = resolve_options({"overrides": {"camera": {"yaw_deg": 46, "lens_mm": 85}}}, bundle)
    assert partial["style_doc"]["camera"]["mode"] == "override"
    assert partial["style_doc"]["camera"]["yaw_deg"] == 46
    assert partial["style_doc"]["camera"]["lens_mm"] == 85


def test_output_precedence_and_policy(bundle):
    request = resolve_options({"overrides": {"output": {"width": 720, "height": 300}}, "width": 800}, bundle)
    assert request["style_doc"]["output"]["width"] == 800
    assert request["style_doc"]["output"]["height"] == 300
    custom = preset()
    custom["camera"].update(mode="override", direction_source="preset", yaw_deg=41)
    public = resolve_options({"style_doc": custom}, bundle, public=True)
    assert public["style_doc"]["camera"] == custom["camera"]
    assert public["style_doc"]["renderer"]["require_gpu"] is True
    with pytest.raises(ValueError, match="4096"):
        resolve_options({"rough": True, "overrides": {"output": {"width": 4097}}}, bundle, public=True)
    custom["output"]["raw_view"] = True
    with pytest.raises(ValueError, match="ray-traced"):
        resolve_options({"style_doc": custom}, bundle, public=True)


@pytest.mark.parametrize("options,match", [
    ({"script": "ignored.py"}, "Unsupported render options: script"),
    ({"overrides": {"camera": {"unsupported": 42}}}, "unsupported"),
    ({"overrides": {"materials": {"unexpected": {"not_a_shader": True}}}}, "not_a_shader"),
    ({"overrides": {"styles": [{"name": "bad", "style": "legacy_ball_and_stick", "material": "none", "selection": {}}]}}, "legacy_ball_and_stick"),
    ({"overrides": {"output": {"width": 0}}, "width": 100}, "width"),
    ({"overrides": []}, "overrides must be an object"),
    ({"width": True}, "positive integer"),
    ({"preserve_view": "false"}, "boolean"),
])
def test_invalid_options_fail_before_dispatch(options, match, bundle, tmp_path, monkeypatch):
    class NeverWorker:
        def submit_render(self, **kwargs):
            pytest.fail("Invalid specification reached worker")
    monkeypatch.setattr(core, "TMP_BRIDGE_ROOT", tmp_path)
    with pytest.raises((ValueError, core.StyleValidationError), match=match):
        rendering._process_render_request(request_id="bad", request_doc={"payload": bundle, **options},
                                          state=rendering.RenderServerState(NeverWorker()))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("mutate,match", [
    (lambda s: s["styles"][0].update(material="missing"), "material"),
    (lambda s: s["styles"][0].update(selection={"residue_range": [5, 2]}), "start must be <= end"),
    (lambda s: s["styles"][0].update(ligand_cutout_frame_mode="preset"), "frame_axes"),
    (lambda s: s["styles"][0].update(selection={"chain": "   "}), "chain"),
    (lambda s: s["materials"].update({" ": {}}), "match"),
    (lambda s: s["camera"].update(lens_mm=float("nan")), "finite"),
])
def test_additional_validation_is_preserved(mutate, match):
    style = surface_style()
    mutate(style)
    with pytest.raises(core.StyleValidationError, match=match):
        core.validate_style_document(style)


def test_catalog_retains_identifiers_and_describes_real_capabilities():
    entries = catalog()
    assert len(entries) == 14
    assert {entry["id"] for entry in entries} == set(core.PRESET_STYLE_FILENAMES)
    for entry in entries:
        assert preset_id(entry["qualified_id"]) == entry["id"]
        assert entry["source"] == "builtin" and entry["revision"] == "1"
        for alias in entry["aliases"]:
            assert preset_id(alias) == entry["id"]
        core.validate_style_document(preset(entry["id"]))
    green = next(entry for entry in entries if entry["id"] == "dried_paint_green")
    assert {"material.dried_paint", "geometry.edge_rounding"} <= set(green["required_capabilities"])
    assert preset_id("pastel") == "preset_1_interface"
    with pytest.raises(ValueError, match="Unknown preset"):
        preset_id("user/pastel")  # no implicit collision/shadowing


class CaptureWorker:
    def __init__(self):
        self.styles = []

    def submit_render(self, **kwargs):
        style = json.loads(Path(kwargs["style_json"]).read_text())
        self.styles.append(style)
        output = Path(kwargs["output_png"])
        output.write_bytes(b"PNG fixture")
        if style["output"].get("save_blend"):
            (output.parent / style["output"]["blend_filename"]).write_bytes(b"scene fixture")
        return {"runtime_seconds": 0}


@pytest.mark.parametrize("save_scene", [False, True])
def test_worker_consumes_exact_snapshot_and_scene_opt_in(save_scene, bundle, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "TMP_BRIDGE_ROOT", tmp_path)
    options = {"preset": "dried_paint_green", "rough": True, "color_follow": True,
               "overrides": {"camera": {"mode": "override", "yaw_deg": 37, "lens_mm": 76}}, "save_blend": save_scene}
    request = prepare_options(options, bundle, public=False, timeout=120)
    snapshot = copy.deepcopy(request["style_doc"])
    options["overrides"]["camera"]["yaw_deg"] = 90
    worker = CaptureWorker()
    result = rendering._process_render_request(request_id="snapshot", request_doc={"payload": bundle, **request},
                                               state=rendering.RenderServerState(worker))
    assert worker.styles == [snapshot]  # rough/color/camera are not applied again
    assert snapshot["output"]["width"] == 1024
    assert result["manifest"]["render_spec"]["sha256"] == style_digest(snapshot)
    assert result["manifest"]["resolved_preset"] == snapshot
    assert ("blend_base64" in result) is save_scene
    assert not Path(result["temp_dir"]).exists()
    request["style_doc"]["camera"]["yaw_deg"] += 1
    with pytest.raises(core.RunnerError, match="hash mismatch"):
        rendering._process_render_request(request_id="tampered", request_doc={"payload": bundle, **request},
                                          state=rendering.RenderServerState(worker))
    assert len(worker.styles) == 1


def test_gateway_resolution_does_not_apply_rough_or_raw_view_twice(bundle):
    outer = resolve_options({"rough": True, "raw_view": True}, bundle)
    forwarded = {k: outer[k] for k in ("style_doc", "rough", "raw_view", "color_follow", "save_blend")}
    forwarded["preserve_view"] = False
    inner = resolve_options(forwarded, bundle)
    assert inner["style_doc"] == outer["style_doc"]
    assert inner["render_spec"]["sha256"] == outer["render_spec"]["sha256"]


@pytest.mark.parametrize("options,expected", [({}, False), ({"save_blend": True}, True),
    ({"overrides": {"output": {"save_blend": True}}}, True),
    ({"overrides": {"output": {"save_blend": True}}, "save_blend": False}, False)])
def test_scene_request_precedence(options, expected, bundle):
    resolved = resolve_options(options, bundle)
    assert resolved["style_doc"]["output"]["format"] == "PNG"
    assert resolved["save_blend"] is expected


def test_reference_gates_follow_representation_not_names():
    style = surface_style()
    assert not core._is_preset_1_gate_target(preset="preset_1_interface",
        style_json_path="style_preset_1_opaque_pastel.json", style_doc=style)
    reference = preset()
    reference["output"]["filename"] = "custom.png"
    assert core._is_preset_1_gate_target(preset="", style_json_path="other.json", style_doc=reference)
    with pytest.raises(core.RunnerError, match="cartoon secondary structure"):
        core._enforce_preset_1_backend_gates(manifest={"structure_source": "cif", "dss_ran": True,
            "polymer_backend": "none", "ligand_backend": "mn_native_sticks", "ligand_atom_spheres": False},
            manifest_path=Path("unused.json"))


@pytest.mark.parametrize("adapter", ["cli", "pymol"])
def test_custom_surface_through_adapter_to_worker(adapter, bundle, tmp_path, monkeypatch):
    import blender_render.client
    from blender_render import pymol_bridge_plugin as plugin
    monkeypatch.setattr(plugin, "_PACKAGE_ROOT", str(tmp_path / "isolated-package"))
    monkeypatch.setattr(core, "TMP_BRIDGE_ROOT", tmp_path / "runs")
    monkeypatch.setenv("BLENDER_RENDER_HOME", str(tmp_path / "config"))
    document = surface_style()
    document["output"]["save_blend"] = True  # JSON itself explicitly requests scene
    document["camera"].update(mode="override", direction_source="preset", yaw_deg=21, lens_mm=83)
    style_file = tmp_path / "style_preset_1_opaque_pastel.json"
    style_file.write_text(json.dumps(document))
    overrides = tmp_path / "overrides.json"
    overrides.write_text(json.dumps({"camera": {"pitch_deg": 14}}))
    worker, resolved = CaptureWorker(), []
    class Client:
        def upload(self, *args): return {"input_id": "input"}
        def submit(self, identifier, options, *args, **kwargs):
            request = prepare_options(options, bundle, public=False, timeout=120)
            resolved.append(request)
            self.result = rendering._process_render_request(request_id="surface", request_doc={"payload": bundle, **request},
                                                            state=rendering.RenderServerState(worker))
            return {"job_id": "job"}
        def wait(self, *args, **kwargs): pass
        def download(self, job, output):
            Path(output).write_bytes(b"PNG fixture")
            return {"outputs": {"render.png": str(output)}}
    client = Client()
    monkeypatch.setattr(blender_render.client, "configured_client", lambda *a, **k: client)
    if adapter == "cli":
        source = tmp_path / "bundle.json"
        source.write_text(json.dumps(bundle))
        cli.execute(cli.parser().parse_args(["render", str(source), "--style-json", str(style_file),
            "--overrides-json", str(overrides), "--preserve-view", "-o", str(tmp_path / "out.png")]))
    else:
        monkeypatch.setitem(sys.modules, "pymol", SimpleNamespace(cmd=SimpleNamespace(load_png=lambda *a, **k: None)))
        monkeypatch.setattr(plugin, "build_render_payload", lambda *a, **k: bundle)
        plugin.blender_render(style_json=str(style_file), overrides_json=str(overrides), preserve_view=1, output_dir=str(tmp_path))
    assert resolved[0]["preset"] == ""
    assert worker.styles[0]["styles"] == document["styles"]
    assert worker.styles[0]["camera"] == {**document["camera"], "pitch_deg": 14}
    assert worker.styles[0]["output"]["save_blend"] is True
    assert "blend_base64" in client.result


def test_omitted_and_explicit_defaults_have_one_specification(bundle):
    defaults = {"preset": "preset_1_interface", "rough": False, "raw_view": False,
                "color_follow": False, "save_blend": False, "keep_temp": False, "preserve_view": True}
    assert resolve_options({}, bundle) == resolve_options(defaults, bundle)


def test_absolute_angle_override_selects_consuming_camera(bundle):
    options = {"preset": "pastel", "preserve_view": True,
               "overrides": {"camera": {"yaw_deg": 30, "pitch_deg": 12}}}
    resolved = resolve_options(options, bundle)
    camera = resolved["style_doc"]["camera"]
    assert camera["direction_source"] == "preset" and camera["mode"] == "override"
    full = preset()
    full["camera"].update(direction_source="preset", yaw_deg=30, pitch_deg=12)
    assert resolve_options({"style_doc": full}, bundle)["style_doc"] == resolved["style_doc"]
    options["overrides"]["camera"]["direction_source"] = "pymol"
    with pytest.raises(core.StyleValidationError, match="direction_source=preset"):
        resolve_options(options, bundle)
    full["camera"]["direction_source"] = "pymol"
    with pytest.raises(core.StyleValidationError, match="direction_source=preset"):
        resolve_options({"style_doc": full}, bundle)


@pytest.mark.parametrize("enabled", [True, False])
def test_color_follow_explicit_false_and_gateway_parity(bundle, enabled):
    style = preset()
    style["color_follow"] = {"enabled": True, "mix": 0.37}
    outer = resolve_options({"style_doc": style, "color_follow": enabled}, bundle)
    assert outer["style_doc"]["color_follow"] == {"enabled": enabled, "mix": 0.37}
    forwarded = {k: outer[k] for k in ("style_doc", "rough", "raw_view", "color_follow", "save_blend")}
    inner = resolve_options(forwarded, bundle)
    assert inner["render_spec"]["sha256"] == outer["render_spec"]["sha256"]
    assert style["color_follow"]["enabled"] is True


@pytest.mark.parametrize("value", [True, False])
def test_dead_camera_control_is_rejected(value, bundle):
    with pytest.raises(core.StyleValidationError, match="anchor_model_center"):
        resolve_options({"overrides": {"camera": {"anchor_model_center": value}}}, bundle)


@pytest.mark.parametrize("transport", ["stdio", "http"])
@pytest.mark.parametrize("scene", [False, True])
def test_mcp_options_reach_shared_resolver(transport, scene, bundle, monkeypatch, tmp_path):
    from fastmcp import Client as MCPClient
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
    from blender_render.api import Downloads
    import blender_render.client
    import blender_render.mcp_server as mcp
    from blender_render.jobs import JobStore
    from blender_render.service import RenderService
    from test_jobs_api import Compute, payload as molecular_payload
    store = JobStore(tmp_path / 'store')
    service = RenderService(store, Compute())
    source = service.import_payload('test-owner', molecular_payload.__wrapped__())['input_id']
    if transport == "stdio":
        connection = SimpleNamespace(submit_consumer=lambda input_id, request, **kw:
            service.submit_consumer('test-owner', input_id, request, trusted=True, **kw))
        monkeypatch.setattr(blender_render.client, "configured_client", lambda *a, **k: connection)
        server = mcp.create_stdio(compatibility=True)
    else:
        monkeypatch.setattr(mcp, "owner", lambda: "test-owner")
        server = mcp.create_http(service, auth=StaticTokenVerifier(tokens={}), downloads=Downloads("x" * 48, ""), converter=None, compatibility=True)
    options = {"preset": "green_paint", "width": 720, "height": 480, "samples": 16}
    if scene:
        options["save_blend"] = True
    async def run():
        async with MCPClient(server) as client:
            await client.call_tool("submit_render", {"input_id": source, "idempotency_key": "contract", "options": options})
    asyncio.run(run())
    rows = store.db.execute('SELECT execution FROM jobs').fetchall()
    assert len(rows) == 1 and not service.backend.calls
    request = json.loads(rows[0][0])['request']
    store.close()
    assert request["preset"] == "dried_paint_green"
    assert request["save_blend"] is scene
    assert request["style_doc"]["output"]["save_blend"] is scene
    assert request["render_spec"]["sha256"] == style_digest(request["style_doc"])
