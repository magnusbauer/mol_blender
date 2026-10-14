"""Actual Blender/PyMOL execution; never enabled by ordinary pytest collection."""
import json
from pathlib import Path

import pytest
from PIL import Image


def test_public_fixture_cpu_render(request, tmp_path):
    config = request.config.getoption("--cpu-runtime-config")
    if not config:
        pytest.skip("Pass --cpu-runtime-config to authorize the local public-fixture render")
    from support.cpu_smoke import run_cpu_smoke
    output = Path(request.config.getoption("--cpu-output") or tmp_path / "preview")
    result = run_cpu_smoke(checkout=Path(__file__).resolve().parents[2],
                           runtime_config=config, output=output)
    with Image.open(output / "render.png") as image:
        assert image.format == "PNG"
        assert image.size == (128, 128)
    manifest = json.loads((output / "manifest.json").read_text())
    assert result["status"] == "completed"
    assert manifest["cycles_device"]["device"] == "CPU"
    assert manifest["render_threads"] == {"mode": "FIXED", "count": 2}
    assert manifest["resolved_preset"]["output"]["samples"] == 4
    assert manifest["structure_source"] == "cif"
    assert manifest["dss_ran"] is True
    assert manifest["polymer_backend"] == "mn_cartoon"
    assert manifest["ligand_backend"] == "mn_native_sticks"
    assert manifest["ligand_atom_spheres"] is False
    assert result["execution"]["process_threads"] == 2
    command = result["worker"]["command"]
    assert command[command.index("--threads") + 1] == "2"
