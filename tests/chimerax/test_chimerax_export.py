"""ChimeraX exporter contracts: camera conversion, ss/color hints and bundle validity.

Unit tests use fake ChimeraX objects. The integration test runs real headless
ChimeraX when available (MOL_BLENDER_CHIMERAX or the default macOS app path).
"""
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from chimerax_bridge import export  # noqa: E402


def renderer_camera(view):
    """The renderer's _compute_camera_spec plus preserve_framing, without bpy."""
    v = view["pymol_view"]
    c2w = [[v[0], v[1], v[2]], [v[3], v[4], v[5]], [v[6], v[7], v[8]]]
    eye = [v[12 + i] - sum(c2w[i][j] * v[9 + j] for j in range(3)) for i in range(3)]
    w, h = view["viewport_px"]
    fov = view["fov_deg"] if 5 <= view["fov_deg"] <= 120 else 50.0
    out = {"eye": eye, "axes": c2w, "clip": (v[15], v[16])}
    if view["orthoscopic"]:
        out["ortho_width"] = 2 * abs(v[11]) * math.tan(math.radians(fov) / 2) * w / h
    else:
        out["horizontal_fov"] = math.degrees(2 * math.atan(math.tan(math.radians(fov) / 2) * w / h))
    return out


def rotation(ax, ay):
    ca, sa, cb, sb = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay)
    rx = [[1, 0, 0], [0, ca, -sa], [0, sa, ca]]
    ry = [[cb, 0, sb], [0, 1, 0], [-sb, 0, cb]]
    return [[sum(rx[i][k] * ry[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def place(rot, eye):
    return [rot[i] + [eye[i]] for i in range(3)]


def view_doc(pymol_view, fov, ortho, size):
    return {"pymol_view": pymol_view, "fov_deg": fov, "orthoscopic": ortho, "viewport_px": list(size)}


def test_identity_perspective_camera_is_pymol_layout():
    view, fov = export.camera_to_pymol_view(
        place([[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0, 0, 50]), window_size=(640, 480), orthographic=False,
        field_of_view=30, center_of_rotation=[0, 0, 0], near_far=(40, 60))
    assert view[:15] == [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, -50, 0, 0, 0]
    # ChimeraX's horizontal angle becomes PyMOL's vertical one; sign marks perspective.
    assert fov == pytest.approx(math.degrees(2 * math.atan(math.tan(math.radians(15)) * 0.75)))
    assert view[17] == -fov
    assert 0 < view[15] < 40 and view[16] > 60


@pytest.mark.parametrize("size", [(640, 480), (480, 900)])
def test_perspective_round_trips_through_renderer_math(size):
    rot, eye = rotation(0.4, -1.1), [12.0, -3.0, 80.0]
    view, fov = export.camera_to_pymol_view(
        place(rot, eye), window_size=size, orthographic=False, field_of_view=30,
        center_of_rotation=[5.0, 7.0, -2.0], near_far=(50, 120))
    cam = renderer_camera(view_doc(view, fov, False, size))
    assert cam["eye"] == pytest.approx(eye)
    assert cam["axes"] == [pytest.approx(row) for row in rot]
    assert cam["horizontal_fov"] == pytest.approx(30)


def test_orthographic_keeps_width_and_puts_geometry_in_front():
    rot, eye = rotation(-0.7, 2.0), [0.0, 10.0, 5.0]
    # The eye is inside the molecule (near < 0): orthographic depth is free, so it moves back.
    view, fov = export.camera_to_pymol_view(
        place(rot, eye), window_size=(800, 600), orthographic=True, field_width=67.2,
        center_of_rotation=[0, 0, 0], near_far=(-20, 40))
    assert view[17] == fov == export.ORTHO_FOV_DEG
    cam = renderer_camera(view_doc(view, fov, True, (800, 600)))
    assert cam["ortho_width"] == pytest.approx(67.2)
    assert cam["axes"] == [pytest.approx(row) for row in rot]
    forward = [-rot[i][2] for i in range(3)]
    shift = [cam["eye"][i] - eye[i] for i in range(3)]
    lateral = [shift[i] - sum(shift[k] * forward[k] for k in range(3)) * forward[i] for i in range(3)]
    assert lateral == pytest.approx([0, 0, 0], abs=1e-9)
    assert cam["clip"][0] > 0 and cam["clip"][1] > cam["clip"][0]


def test_unrepresentable_field_of_view_is_explicit():
    with pytest.raises(ValueError, match="outside the renderer"):
        export.camera_to_pymol_view(place(rotation(0, 0), [0, 0, 9]), window_size=(2000, 200),
                                    orthographic=False, field_of_view=30, center_of_rotation=[0, 0, 0],
                                    near_far=(1, 20))


class Atom:
    def __init__(self, name, color, visible=False):
        self.name, self.color, self.visible = name, color, visible


class Residue:
    def __init__(self, name, number, ss=0, polymer=1, ins="", atoms=("CA",), ribbon=True,
                 ribbon_color=(0, 255, 0, 255), atom_color=(255, 0, 0, 255), visible=False):
        self.name, self.number, self.ss_type, self.polymer_type, self.insertion_code = name, number, ss, polymer, ins
        self.ribbon_display, self.ribbon_color = ribbon, ribbon_color
        self.atoms = [Atom(a, atom_color, visible) for a in atoms]


def test_secondary_structure_hints_follow_pymol_rules():
    pairs = [(Residue("ALA", 10, 1, ins="A"), "a"), (Residue("GLY", 10, 2, ins="B"), "a"),
             (Residue("PTR", 11, 1), "B"), (Residue("UNL", 12, 1, polymer=0), "B"),
             (Residue("MSE", 13, 1), "B"), (Residue("DA", 1, 0, polymer=2), "N")]
    hints = export.collect_ss_hints(pairs, hetero={("B", "11"), ("B", "12"), ("B", "13")})
    # Phosphoresidues survive as HETATM; other HETATM, ligands and nucleic acids do not.
    assert hints["by_chain_resi"] == {"a:10A": "H", "a:10B": "S", "B:11": "H"}
    assert hints["counts"] == {"H": 2, "S": 1}


def test_color_hints_use_what_the_viewer_shows():
    shown = Residue("ALA", 10, atoms=("CA", "CB"))
    shown.atoms[1].visible = True  # side chain drawn as sticks keeps its atom color
    hidden = Residue("HOH", 5, polymer=0, ribbon=False, atoms=("O",), atom_color=(0, 0, 255, 255))
    hints = export.collect_color_hints([(shown, "A"), (hidden, "W")], hetero={("W", "5")})
    assert hints["atoms"] == {"A:10:ALA:CA": "#00FF00", "A:10:ALA:CB": "#FF0000", "W:5:HOH:O": "#0000FF"}
    assert hints["classes"] == {"polymer": "#00FF00", "ligand": "#0000FF"}
    assert hints["chain"]["W"] == "#0000FF"


CHIMERAX_CIF = """data_x
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.label_atom_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_seq_id
_atom_site.auth_asym_id
_atom_site.auth_seq_id
_atom_site.pdbx_PDB_ins_code
ATOM   1 CA  ALA A 1 A 2   ?
HETATM 2 "O5'" PTR D 1 D 0  ?
HETATM 3 O   HOH W . W 52  B
#
"""


def test_hetero_residues_read_the_exported_records():
    assert export.hetero_residues(CHIMERAX_CIF) == {("D", "0"), ("W", "52B")}


def test_exporter_does_not_import_the_renderer():
    code = ("import sys; sys.path.insert(0, %r); from chimerax_bridge import export; export.shared(); "
            "print(sorted(m for m in sys.modules if m.split('.')[0] == 'mol_blender'))") % str(ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"


def test_render_command_reaches_configured_cli(tmp_path, monkeypatch):
    sys.modules.pop("chimerax_bridge.molblender_cmd", None)
    from chimerax_bridge import molblender_cmd
    (tmp_path / "config.json").write_text(json.dumps({"local_python": "/venv/python"}))
    monkeypatch.setenv("MOL_BLENDER_HOME", str(tmp_path))
    monkeypatch.setenv("PYTHONHOME", "/Applications/ChimeraX.app/python")
    command = molblender_cmd.render_command(Path("b.json"), Path("o.png"), preset="mint_sky_assembly",
                                            width=800, height=600, rough=True)
    assert command[:4] == ["/venv/python", "-m", "mol_blender.cli", "render"]
    assert ["--preset", "mint_sky_assembly"] == command[command.index("--preset"):][:2]
    assert "--rough" in command and command[command.index("--width") + 1] == "800"
    env = molblender_cmd.renderer_environment()
    assert "PYTHONHOME" not in env and env["PYTHONPATH"] == str(ROOT)


def chimerax_executable():
    candidates = [os.environ.get("MOL_BLENDER_CHIMERAX"), shutil.which("ChimeraX"), shutil.which("chimerax")]
    candidates += sorted(Path("/Applications").glob("ChimeraX*.app/Contents/bin/ChimeraX"), reverse=True)
    return next((str(c) for c in candidates if c and Path(c).exists()), None)


@pytest.mark.skipif(chimerax_executable() is None, reason="ChimeraX not installed")
def test_real_chimerax_export_is_a_valid_bundle(tmp_path):
    from mol_blender.core import PAYLOAD_KEYS, validate_payload_document
    script = tmp_path / "export.py"
    out = tmp_path / "bundle.json"
    script.write_text(
        "from chimerax.core.commands import run\n"
        f"run(session, 'open {ROOT / 'chimerax_bridge' / 'molblender_cmd.py'}')\n"
        f"run(session, 'open {ROOT / 'examples' / '1sps.cif'}')\n"
        f"run(session, 'open {ROOT / 'examples' / '2trx.cif'}')\n"
        "run(session, 'windowsize 640 480')\n"
        f"run(session, 'molblender export {out}')\n")
    subprocess.run([chimerax_executable(), "--nogui", "--offscreen", "--exit", "--silent", "--script", str(script)],
                   check=True, capture_output=True, timeout=300)
    payload = json.loads(out.read_text())
    validate_payload_document(payload)
    assert set(payload) <= PAYLOAD_KEYS
    assert set(payload["view"]) == {"pymol_view", "fov_deg", "orthoscopic", "viewport_px"}
    # Two models become one data block; colliding chain IDs are renamed.
    assert payload["cif_text"].count("\ndata_") + payload["cif_text"].startswith("data_") == 1
    mapping = payload["provenance"]["chain_mapping"]
    assert len(set(mapping.values())) == len(mapping)
    assert payload["ss_hints"]["by_chain_resi"] and payload["color_hints"]["atoms"]
