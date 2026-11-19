"""ChimeraX scene export to the same data-only bundle the PyMOL exporter writes.

Runs inside ChimeraX's bundled Python. It imports no renderer module: the
stdlib-only CIF helpers come from mol_blender/scene/export.py loaded by path,
as co_pymol does. ChimeraX modules are imported lazily so the conversion
logic can be tested without ChimeraX.
"""
from __future__ import annotations

import importlib.util
import math
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from importlib.machinery import PathFinder
from pathlib import Path
from typing import Any

PHOSPHO = {"PTR", "TPO", "SEP"}
# chimerax.atomic.Residue polymer types and ss types.
PT_NONE, PT_AMINO = 0, 1
SS_CODES = {0: "L", 1: "H", 2: "S"}
# The renderer replaces a field of view outside [5, 120] degrees with 50.
ORTHO_FOV_DEG = 30.0
MIN_FOV_DEG, MAX_FOV_DEG = 5.0, 120.0
MIN_FRONT = 0.5


@lru_cache(maxsize=1)
def shared():
    """The renderer's standalone PyMOL exporter, for its stdlib CIF helpers only."""
    path = Path(__file__).resolve().parents[1] / "mol_blender" / "scene"
    spec = PathFinder.find_spec("export", [str(path)])
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the mol-blender scene exporter from " + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- camera -----------------------------------------------------------------

def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _world_to_camera(rotation, eye, point):
    """R_c2w^T (point - eye): camera-space coordinates of a scene point."""
    d = _sub(point, eye)
    return [_dot([rotation[0][j], rotation[1][j], rotation[2][j]], d) for j in range(3)]


def camera_to_pymol_view(position, *, window_size, orthographic, field_of_view=None,
                         field_width=None, center_of_rotation, near_far):
    """Convert a ChimeraX camera to the 18-value PyMOL view the renderer consumes.

    position: ChimeraX camera Place as 3x4 rows (camera-to-scene; looks down -z,
    y up, like PyMOL and Blender). field_of_view is ChimeraX's horizontal angle
    in degrees; field_width is the orthographic horizontal extent in scene units.
    near_far: ChimeraX near/far distances from the camera along the view axis.

    Returns (pymol_view, fov_deg), where fov_deg is the vertical angle PyMOL
    and the renderer's preserve_framing path expect.
    """
    rotation = [[float(position[i][j]) for j in range(3)] for i in range(3)]
    eye = [float(position[i][3]) for i in range(3)]
    forward = [-rotation[i][2] for i in range(3)]
    width, height = (float(v) for v in window_size)
    if width <= 0 or height <= 0:
        raise ValueError("ChimeraX window size must be positive")
    near, far = (float(v) for v in near_far)
    pad = max(2.0, 0.05 * max(far - near, 0.0))
    if orthographic:
        if not field_width or field_width <= 0:
            raise ValueError("Orthographic camera needs a positive field width")
        # Orthographic depth is free: keep everything in front of the eye, then
        # place the rotation origin where the renderer's ortho scale,
        # 2*|origin depth|*tan(fov/2), equals the vertical field height.
        shift = max(0.0, (MIN_FRONT + pad) - near)
        eye = [eye[i] - shift * forward[i] for i in range(3)]
        near, far = near + shift, far + shift
        fov = ORTHO_FOV_DEG
        depth = (float(field_width) * height / width) / (2.0 * math.tan(math.radians(fov) / 2.0))
        origin = [eye[i] + depth * forward[i] for i in range(3)]
    else:
        if not field_of_view or not 0 < field_of_view < 180:
            raise ValueError("Perspective camera needs a field of view between 0 and 180 degrees")
        half = math.radians(float(field_of_view)) / 2.0
        fov = math.degrees(2.0 * math.atan(math.tan(half) * height / width))
        if not MIN_FOV_DEG <= fov <= MAX_FOV_DEG:
            raise ValueError(f"Vertical field of view {fov:.1f} deg is outside the renderer's "
                             f"{MIN_FOV_DEG:g}-{MAX_FOV_DEG:g} deg range; adjust ChimeraX's camera fieldOfView")
        origin = [float(v) for v in center_of_rotation]
    origin_camera = _world_to_camera(rotation, eye, origin)
    front = max(MIN_FRONT, near - pad)
    rear = max(front + 1.0, far + pad)
    # R_w2c column-major == R_c2w row-major.
    flat = [rotation[i][j] for i in range(3) for j in range(3)]
    view = flat + origin_camera + origin + [front, rear, fov if orthographic else -fov]
    return [float(v) for v in view], fov


def collect_view_payload(session) -> dict[str, Any]:
    view = session.main_view
    camera = view.camera
    name = getattr(camera, "name", "")
    if name not in ("mono", "orthographic"):
        raise ValueError(f"Unsupported ChimeraX camera {name!r}; use `camera mono` or `camera ortho`")
    orthographic = name == "orthographic"
    window_size = [int(v) for v in view.window_size]
    position = camera.position.matrix.tolist()
    near_far = view.near_far_distances(camera, None)
    pymol_view, fov = camera_to_pymol_view(
        position, window_size=window_size, orthographic=orthographic,
        field_of_view=None if orthographic else camera.field_of_view,
        field_width=getattr(camera, "field_width", None) if orthographic else None,
        center_of_rotation=[float(v) for v in view.center_of_rotation], near_far=near_far)
    return {"pymol_view": pymol_view, "fov_deg": fov, "orthoscopic": orthographic,
            "viewport_px": window_size}


# --- hints ------------------------------------------------------------------

def _rgb_hex(rgba) -> str:
    return shared()._rgb_to_hex(tuple(float(c) / 255.0 for c in list(rgba)[:3]))


def residue_id(residue) -> str:
    return f"{int(residue.number)}{str(residue.insertion_code or '').strip()}"


def hetero_residues(cif_text: str) -> set[tuple[str, str]]:
    """(chain, resi) of HETATM records in the exported CIF, which is what the renderer loads.

    ChimeraX has no per-atom HETATM flag; reading its own export keeps the
    ligand class and ss filtering identical to PyMOL's `type == 'HETATM'`.
    """
    import shlex
    headers, hetero, reading = [], set(), False
    for raw in cif_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower() == "loop_":
            headers, reading = [], False
            continue
        if line.startswith("_atom_site."):
            headers.append(line.split(".", 1)[1].split()[0])
            reading = True
            continue
        if reading and line.startswith(("_", "data_")):
            reading = False
            continue
        if reading and line.startswith("HETATM"):
            try:
                parts = shlex.split(line, posix=True)
            except ValueError:
                parts = line.split()
            field = dict(zip(headers, parts))
            chain = field.get("auth_asym_id") or field.get("label_asym_id", "")
            ins = field.get("pdbx_PDB_ins_code", "?")
            resi = (field.get("auth_seq_id") or field.get("label_seq_id", "")) + ("" if ins in ("?", ".") else ins)
            hetero.add((chain, resi))
    return hetero


def collect_ss_hints(residues, hetero=frozenset()) -> dict[str, Any]:
    """residues: [(residue, exported chain id)]. Protein ss, skipping HETATM except phosphoresidues.

    counts are per atom, as PyMOL's iterate-based exporter counts them.
    """
    by_chain_resi, counts = {}, Counter()
    for residue, chain in residues:
        resi = residue_id(residue)
        phospho = str(residue.name).upper() in PHOSPHO
        if (chain, resi) in hetero and not phospho:
            continue
        if int(residue.polymer_type) != PT_AMINO and not phospho:
            continue
        code = SS_CODES.get(int(residue.ss_type))
        if code is None:
            continue
        key = f"{chain}:{resi}"
        previous = by_chain_resi.get(key)
        if previous in {"H", "S"} and code == "L":
            continue
        by_chain_resi[key] = code
        counts[code] += len(residue.atoms)
    if not by_chain_resi:
        return {}
    return {"source": "chimerax", "by_chain_resi": by_chain_resi, "counts": dict(counts)}


def effective_atom_color(atom, residue):
    """The color a viewer sees for an atom: its own if drawn, else its residue's ribbon."""
    if bool(atom.visible):
        return atom.color
    if bool(residue.ribbon_display):
        return residue.ribbon_color
    return atom.color


def collect_color_hints(residues, hetero=None) -> dict[str, Any]:
    """Majority colors per chain and class, plus per-atom colors, like the PyMOL exporter.

    ligand = HETATM in the exported CIF (PyMOL's rule); without that set,
    non-polymer residues.
    """
    nucleic = shared().NUCLEIC_RES_NAMES
    chain_counts: dict[str, Counter] = defaultdict(Counter)
    class_counts: dict[str, Counter] = defaultdict(Counter)
    atoms = {}
    for residue, chain in residues:
        resn = str(residue.name).strip().upper()
        resi = residue_id(residue)
        is_ligand = ((chain, resi) in hetero) if hetero is not None else int(residue.polymer_type) == PT_NONE
        category = "ligand" if is_ligand else "nucleic" if resn in nucleic else "polymer"
        for atom in residue.atoms:
            color = _rgb_hex(effective_atom_color(atom, residue))
            chain_counts[str(chain).strip() or "_"][color] += 1
            class_counts[category][color] += 1
            atoms[f"{chain}:{resi}:{residue.name}:{atom.name}"] = color
    pick = lambda counter: counter.most_common(1)[0][0]
    if not chain_counts:
        return {}
    return {"chain": {k: pick(v) for k, v in chain_counts.items()},
            "classes": {k: pick(v) for k, v in class_counts.items()},
            "atoms": atoms, "source": "chimerax"}


def collect_polar_contacts(session, structures, chain_of) -> list:
    """Interchain H-bonds between phosphoresidues and other protein residues."""
    if not any(str(r.name).upper() in PHOSPHO for s in structures for r in s.residues):
        return []
    from chimerax.hbonds import find_hbonds
    pairs = set()
    # The `hbonds` command's default relaxation (0.4 A, 20 deg), i.e. what ChimeraX users see.
    for donor, acceptor in find_hbonds(session, structures, dist_slop=0.4, angle_slop=20.0, status=False):
        left, right = donor.residue, acceptor.residue
        a, b = str(left.name).upper(), str(right.name).upper()
        if (a in PHOSPHO) == (b in PHOSPHO):
            continue
        if int(left.polymer_type) != PT_AMINO or int(right.polymer_type) != PT_AMINO:
            continue
        if chain_of[left] == chain_of[right]:
            continue
        keys = [f"{chain_of[res]}:{residue_id(res)}:{res.name}:{atom.name}"
                for res, atom in ((left, donor), (right, acceptor))]
        pairs.add(tuple(sorted(keys)))
    if len(pairs) > 256:
        raise ValueError("Too many phosphoresidue polar contacts; narrow the exported models")
    return [list(pair) for pair in sorted(pairs)]


# --- structure --------------------------------------------------------------

def displayed_structures(session):
    from chimerax.atomic import AtomicStructure
    return [m for m in session.models.list(type=AtomicStructure) if m.visible]


def ignored_models(session, structures):
    from chimerax.atomic import Structure
    from chimerax.map import Volume
    skipped = []
    for model in session.models.list():
        if isinstance(model, Volume) and model.visible:
            skipped.append(f"{model.atomspec} {model.name} (volume: maps are not part of the bundle)")
        elif isinstance(model, Structure) and model.visible and model not in structures:
            skipped.append(f"{model.atomspec} {model.name} (not an atomic structure)")
    return skipped


def _model_spec(models) -> str:
    # Explicit ids: concise_model_spec returns '' when the models are all models.
    return " | ".join(m.atomspec for m in models)


def _save_texts(session, model, directory: Path) -> tuple[str, str]:
    from chimerax.core.commands import run
    cif, pdb = directory / "export.cif", directory / "export.pdb"
    spec = model.atomspec
    run(session, f'save "{cif}" models {spec}', log=False)
    cif_text = cif.read_text()
    pdb_text = ""
    if all(len(str(c)) == 1 for c in set(model.residues.chain_ids)):
        run(session, f'save "{pdb}" models {spec}', log=False)
        pdb_text = pdb.read_text()
    return cif_text, pdb_text


def _matrix4(place) -> list[float]:
    rows = place.matrix.tolist()
    return [float(v) for row in rows for v in row] + [0.0, 0.0, 0.0, 1.0]


def build_render_payload(session, structures=None, *, include_color_hints=True, dssp=False) -> dict[str, Any]:
    """Export displayed atomic structures and the camera without changing the session.

    Like the PyMOL exporter: all atoms of visible models (representations are the
    preset's job), coordinates in scene space, one CIF data block with colliding
    chains renamed, secondary structure assigned before export and restored after.
    """
    from chimerax.core.commands import run
    structures = list(structures) if structures else displayed_structures(session)
    if not structures:
        raise ValueError("No displayed atomic structures to export")
    spec = _model_spec(structures)
    saved_ss = [(s.residues, s.residues.ss_types.copy(), s.residues.ss_ids.copy()) for s in structures]
    camera_place = session.main_view.camera.position
    combined = None
    dss = {"selection": spec, "source": "chimerax"}
    try:
        unassigned = [s for s in structures if not getattr(s, "ss_assigned", True)]
        if dssp or unassigned:
            run(session, "dssp " + (spec if dssp else _model_spec(unassigned)), log=False)
            dss["ran"] = True
            dss["method"] = "dssp" if dssp else "dssp_unassigned_only"
        else:
            dss["ran"] = False
            dss["method"] = "existing"
        view = collect_view_payload(session)
        with tempfile.TemporaryDirectory(prefix="chimerax-blender-") as temporary:
            directory = Path(temporary)
            if len(structures) == 1:
                target = structures[0]
                pairs = [(r, r.chain_id) for r in target.residues]
            else:
                # ChimeraX writes one CIF data block per model; loaders read the
                # first. combine renames colliding chains and keeps scene coordinates.
                combined = run(session, f"combine {spec} name blender_export", log=False)
                target = combined
                source = [r for s in structures for r in s.residues]
                if len(source) != len(combined.residues):
                    raise RuntimeError("combine changed the residue count; cannot map chains")
                pairs = [(r, c.chain_id) for r, c in zip(source, combined.residues)]
            cif_text, pdb_text = _save_texts(session, target, directory)
        helpers = shared()
        if not helpers._looks_like_cif_atom_site(cif_text):
            raise RuntimeError("ChimeraX CIF export contained no atom records")
        chain_of = {residue: chain for residue, chain in pairs}
        mapping = {}
        if combined is not None:
            for s in structures:
                for residue in s.residues:
                    mapping.setdefault(f"{s.name}:{residue.chain_id}", chain_of[residue])
        names = [s.name for s in structures]
        keys = [n if names.count(n) == 1 else f"{n} {s.atomspec}" for n, s in zip(names, structures)]
        provenance = {
            "host": "chimerax",
            "enabled_objects": keys,
            "object_transforms": {k: _matrix4(s.scene_position) for k, s in zip(keys, structures)},
            "object_states": {k: int(s.active_coordset_id) for k, s in zip(keys, structures)},
            "model_ids": {k: s.atomspec for k, s in zip(keys, structures)},
        }
        if mapping:
            provenance["chain_mapping"] = mapping
        skipped = ignored_models(session, structures)
        if skipped:
            provenance["ignored_models"] = skipped
        try:
            from chimerax.core import version
            provenance["chimerax_version"] = str(version)
        except Exception:
            pass
        payload = {
            "version": "1.0",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "selection": spec,
            "state": -1,
            "run_id": uuid.uuid4().hex[:12],
            "structure_summary": helpers._summarize_cif_text(cif_text),
            "view": view,
            "dss": dss,
        }
        hetero = hetero_residues(cif_text)
        ss_hints = collect_ss_hints(pairs, hetero)
        if ss_hints:
            payload["ss_hints"] = ss_hints
        elif any(int(r.polymer_type) == PT_AMINO for r, _ in pairs):
            raise RuntimeError("Protein present but no secondary-structure metadata was exported")
        payload["cif_text"] = cif_text
        if pdb_text and helpers._looks_like_pdb_atoms(pdb_text):
            payload["pdb_text"] = pdb_text
        elif combined is not None:
            provenance["pdb_fallback_unavailable"] = "Namespaced multi-model chain IDs require CIF"
        if include_color_hints:
            hints = collect_color_hints(pairs, hetero)
            if hints:
                payload["color_hints"] = hints
        contacts = collect_polar_contacts(session, structures, chain_of)
        if contacts:
            payload["polar_contacts"] = contacts
        payload["provenance"] = provenance
        return payload
    finally:
        if combined is not None:
            session.models.close([combined])
        for residues, ss_types, ss_ids in saved_ss:
            residues.ss_types = ss_types
            residues.ss_ids = ss_ids
        session.main_view.camera.position = camera_place
