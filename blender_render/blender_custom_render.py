#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Magnus Bauer
# SPDX-License-Identifier: GPL-3.0-or-later
"""Custom Blender render pipeline driven by style JSON."""

from __future__ import annotations

import argparse
import colorsys
import importlib.resources
import json
import math
import os
import re
import shlex
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Any

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from blender_render.chain_palette import expand_polymer_styles_for_multichain_palette
except Exception:  # pragma: no cover - package import fallback
    from blender_render.chain_palette import expand_polymer_styles_for_multichain_palette

try:
    from blender_render.ligand_selection import (
        coordinates_from_atom_array,
        expand_mask_to_residues,
        ligand_reference_mask,
        proximity_mask_within_radius,
    )
except Exception:  # pragma: no cover - package import fallback
    from blender_render.ligand_selection import (
        coordinates_from_atom_array,
        expand_mask_to_residues,
        ligand_reference_mask,
        proximity_mask_within_radius,
    )

try:
    from blender_render.cutout_planes import (
        DEFAULT_PLANE_ENABLED,
        resolve_frame_axes,
        resolve_plane_enabled,
        resolve_plane_scales,
    )
except Exception:  # pragma: no cover - package import fallback
    from blender_render.cutout_planes import (
        DEFAULT_PLANE_ENABLED,
        resolve_frame_axes,
        resolve_plane_enabled,
        resolve_plane_scales,
    )


def _configure_molecularnodes_import_path() -> None:
    global _mn_path_configured
    if globals().get("_mn_path_configured"):
        return
    _mn_path_configured = True
    raw = str(os.getenv("PRESS_MN_SITE_PACKAGES", "") or "").strip()
    if not raw:
        return
    paths = [item.strip() for item in raw.split(os.pathsep) if item.strip()]
    if not paths:
        return
    inserted = False
    for path_text in reversed(paths):
        path = str(Path(path_text).expanduser())
        if not path:
            continue
        if path in sys.path:
            try:
                sys.path.remove(path)
            except Exception:
                pass
        sys.path.insert(0, path)
        inserted = True
    if not inserted:
        return
    # Ensure a preloaded addon copy does not shadow an explicit override path.
    for prefix in ("molecularnodes", "databpy"):
        for module_name in list(sys.modules.keys()):
            if module_name == prefix or module_name.startswith(prefix + "."):
                sys.modules.pop(module_name, None)




from blender_render.core import StyleValidationError, validate_style_document
from blender_render.engine import resource_bytes


NUCLEIC_RES_NAMES = ("DA", "DT", "DG", "DC", "A", "U", "G", "C")
SOLVENT_RES_NAMES = ("HOH", "WAT", "DOD", "SOL", "TIP3", "TIP3P")


def _parse_argv(argv: list[str]) -> argparse.Namespace:
    if "--" in argv:
        tail = argv[argv.index("--") + 1 :]
    else:
        tail = argv[1:]

    parser = argparse.ArgumentParser(description="Custom Blender renderer for CIF + PyMOL camera payload.")
    parser.add_argument("--structure", default="")
    parser.add_argument("--cif", default="")
    parser.add_argument("--view-json", required=True)
    parser.add_argument("--style-json", required=True)
    parser.add_argument("--output-png", required=True)
    parser.add_argument("--run-manifest", default="")
    parsed = parser.parse_args(tail)
    if not str(parsed.structure).strip():
        parsed.structure = str(parsed.cif or "").strip()
    if not str(parsed.structure).strip():
        parser.error("Expected --structure <path> (or legacy --cif <path>).")
    return parsed


def _hex_to_rgba(hex_color: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    raw = hex_color.strip().lstrip("#")
    if len(raw) != 6:
        raise ValueError(f"Expected #RRGGBB color, got {hex_color!r}")
    return (
        int(raw[0:2], 16) / 255.0,
        int(raw[2:4], 16) / 255.0,
        int(raw[4:6], 16) / 255.0,
        max(0.0, min(1.0, alpha)),
    )


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    r, g, b, _ = _hex_to_rgba(hex_color, alpha=1.0)
    return (r, g, b)


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    r = max(0.0, min(1.0, float(rgb[0])))
    g = max(0.0, min(1.0, float(rgb[1])))
    b = max(0.0, min(1.0, float(rgb[2])))
    return f"#{int(round(r * 255.0)):02X}{int(round(g * 255.0)):02X}{int(round(b * 255.0)):02X}"


def _mix_rgb(left: tuple[float, float, float], right: tuple[float, float, float], mix: float) -> tuple[float, float, float]:
    w = max(0.0, min(1.0, float(mix)))
    return (
        left[0] * (1.0 - w) + right[0] * w,
        left[1] * (1.0 - w) + right[1] * w,
        left[2] * (1.0 - w) + right[2] * w,
    )


def _adjust_hsv(rgb: tuple[float, float, float], sat_delta: float, value_delta: float) -> tuple[float, float, float]:
    h, s, v = colorsys.rgb_to_hsv(*rgb)
    s = max(0.0, min(1.0, s + float(sat_delta)))
    v = max(0.0, min(1.0, v + float(value_delta)))
    return colorsys.hsv_to_rgb(h, s, v)


def _shade_from_base(rgb: tuple[float, float, float], factor: float) -> tuple[float, float, float]:
    f = max(0.0, min(1.0, float(factor)))
    return (rgb[0] * f, rgb[1] * f, rgb[2] * f)


def _highlight_from_base(rgb: tuple[float, float, float], factor: float) -> tuple[float, float, float]:
    f = max(0.0, float(factor))
    return (
        min(1.0, rgb[0] + (1.0 - rgb[0]) * f),
        min(1.0, rgb[1] + (1.0 - rgb[1]) * f),
        min(1.0, rgb[2] + (1.0 - rgb[2]) * f),
    )


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected object JSON at {path}, got {type(data).__name__}")
    return data


def _strip_wrapping_quotes(value: str) -> str:
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def _split_cif_fields(line: str) -> list[str]:
    text = str(line).strip()
    if not text:
        return []
    try:
        return shlex.split(text, posix=True)
    except Exception:
        return text.split()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except Exception:
        return default


def _ensure_molecularnodes_ready(mn: Any) -> None:
    if hasattr(bpy.types.Object, "mn"):
        return
    register_fn = getattr(mn, "register", None)
    if callable(register_fn):
        register_fn()
    if not hasattr(bpy.types.Object, "mn"):
        raise RuntimeError(
            "Molecular Nodes did not initialize correctly: bpy.types.Object is missing 'mn' property."
        )








def _array_as_upper(values: Any, count: int) -> np.ndarray:
    raw = np.asarray(values, dtype=object).astype(str)
    if raw.shape[0] != count:
        return np.char.upper(raw)
    cleaned = np.array([_strip_wrapping_quotes(item) for item in raw.tolist()], dtype=object)
    return np.char.upper(cleaned.astype(str))


def _safe_bool_mask(values: Any, count: int) -> np.ndarray:
    try:
        mask = np.asarray(values, dtype=bool)
        if mask.shape[0] != count:
            raise ValueError("mask length mismatch")
        return mask
    except Exception:
        return np.zeros(count, dtype=bool)


def _query_term_flags(lowered_text: str, token: str) -> tuple[bool, bool]:
    pattern = re.compile(rf"(?:(\bnot\b|!)\s*)?\b{re.escape(token)}\b", flags=re.IGNORECASE)
    positive = False
    negative = False
    for match in pattern.finditer(lowered_text):
        if match.group(1):
            negative = True
        else:
            positive = True
    return positive, negative


def _semantic_masks(atom_array: Any, count: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    hetero = _safe_bool_mask(getattr(atom_array, "hetero", np.zeros(count, dtype=bool)), count)
    res_name_upper = _array_as_upper(getattr(atom_array, "res_name", np.array([""] * count, dtype=object)), count)
    solvent_mask = np.isin(res_name_upper, [item.upper() for item in SOLVENT_RES_NAMES])

    amino_mask = np.zeros(count, dtype=bool)
    nucleic_mask = np.isin(res_name_upper, [item.upper() for item in NUCLEIC_RES_NAMES])

    try:
        import biotite.structure as bs  # type: ignore

        amino_mask = _safe_bool_mask(bs.filter_amino_acids(atom_array), count)
        nucleic_mask = nucleic_mask | _safe_bool_mask(bs.filter_nucleotides(atom_array), count)
    except Exception:
        pass

    polymer_mask = amino_mask | nucleic_mask | (~hetero)
    if not polymer_mask.any():
        polymer_mask = amino_mask | nucleic_mask
    if not polymer_mask.any():
        polymer_mask = ~solvent_mask

    ligand_mask = hetero & ~polymer_mask
    if not ligand_mask.any():
        ligand_mask = (~(amino_mask | nucleic_mask)) & ~solvent_mask
    if not ligand_mask.any() and hetero.any():
        ligand_mask = hetero & ~solvent_mask

    return polymer_mask, nucleic_mask, ligand_mask


def _query_to_indices(
    atom_array: Any,
    query: str,
    *,
    chain_upper_override: np.ndarray | None = None,
) -> list[int] | None:
    text = str(query or "").strip()
    if not text:
        return None

    count = int(atom_array.array_length()) if hasattr(atom_array, "array_length") else len(atom_array)
    mask = np.ones(count, dtype=bool)
    polymer_mask, nucleic_mask, ligand_mask = _semantic_masks(atom_array, count)
    res_name_upper = _array_as_upper(getattr(atom_array, "res_name", np.array([""] * count, dtype=object)), count)
    if chain_upper_override is not None and len(chain_upper_override) == count:
        chain_upper = chain_upper_override
    else:
        chain_upper = _array_as_upper(getattr(atom_array, "chain_id", np.array([""] * count, dtype=object)), count)
    res_ids = np.asarray(getattr(atom_array, "res_id", np.full(count, -1, dtype=int))).astype(int)

    lowered = text.lower()
    polymer_pos, polymer_neg = _query_term_flags(lowered, "polymer")
    ligand_pos, ligand_neg = _query_term_flags(lowered, "ligand")
    nucleic_pos, nucleic_neg = _query_term_flags(lowered, "nucleic")

    if polymer_pos:
        mask &= polymer_mask
    if polymer_neg:
        mask &= ~polymer_mask
    if ligand_pos:
        mask &= ligand_mask
    if ligand_neg:
        mask &= ~ligand_mask
    if nucleic_pos:
        mask &= nucleic_mask
    if nucleic_neg:
        mask &= ~nucleic_mask

    chain_include: list[str] = []
    chain_exclude: list[str] = []
    for match in re.finditer(r"(?:(\bnot\b|!)\s*)?\bchain\s*=?\s*([A-Za-z0-9_,]+)", text, flags=re.IGNORECASE):
        values = [item.strip().upper() for item in match.group(2).split(",") if item.strip()]
        if match.group(1):
            chain_exclude.extend(values)
        else:
            chain_include.extend(values)
    if chain_include:
        mask &= np.isin(chain_upper, chain_include)
    if chain_exclude:
        mask &= ~np.isin(chain_upper, chain_exclude)

    resn_include: list[str] = []
    resn_exclude: list[str] = []
    for match in re.finditer(r"(?:(\bnot\b|!)\s*)?\bresn\s*=?\s*([A-Za-z0-9_,]+)", text, flags=re.IGNORECASE):
        values = [item.strip().upper() for item in match.group(2).split(",") if item.strip()]
        if match.group(1):
            resn_exclude.extend(values)
        else:
            resn_include.extend(values)
    if resn_include:
        mask &= np.isin(res_name_upper, resn_include)
    if resn_exclude:
        mask &= ~np.isin(res_name_upper, resn_exclude)

    resi_match = re.search(r"\bresi\s*=?\s*([0-9]+)\s*-\s*([0-9]+)", text, flags=re.IGNORECASE)
    if resi_match:
        start = int(resi_match.group(1))
        end = int(resi_match.group(2))
        lo, hi = (start, end) if start <= end else (end, start)
        mask &= (res_ids >= lo) & (res_ids <= hi)
    else:
        resi_single = re.search(r"\bresi\s*=?\s*([0-9]+)\b", text, flags=re.IGNORECASE)
        if resi_single:
            target = int(resi_single.group(1))
            mask &= res_ids == target

    return np.where(mask)[0].astype(int).tolist()


def _atom_array_count(atom_array: Any) -> int:
    try:
        return int(atom_array.array_length()) if hasattr(atom_array, "array_length") else len(atom_array)
    except Exception:
        return 0


def _get_atom_array(source: Any) -> Any:
    array = getattr(source, "atom_array", None)
    if array is None:
        array = getattr(source, "array", None)
    if array is not None and hasattr(array, "shape") and len(array.shape) > 1:
        return array[0]
    return array


def _auth_chain_override_for_structure(structure_path: Path, count: int) -> np.ndarray | None:
    if count <= 0:
        return None
    if str(structure_path.suffix).strip().lower() != ".cif":
        return None
    auth_chain_ids = _extract_auth_chain_ids_from_cif(structure_path)
    if not auth_chain_ids:
        return None
    auth_upper = np.asarray([str(item).strip().upper() for item in auth_chain_ids], dtype=object).astype(str)
    if auth_upper.shape[0] != count:
        return None
    if not bool(np.any(np.array([item not in {"", ".", "?"} for item in auth_upper], dtype=bool))):
        return None
    return auth_upper


def _chain_upper_for_selection(load_result: Any, atom_array: Any, count: int, structure_path: Path) -> np.ndarray:
    chain_getter = getattr(load_result, "_chain_upper", None)
    if callable(chain_getter):
        try:
            chain_values = np.asarray(chain_getter(count), dtype=object).astype(str)
            if chain_values.shape[0] == count:
                return chain_values
        except Exception:
            pass

    bridge_override = getattr(load_result, "_bridge_chain_override_upper", None)
    if isinstance(bridge_override, np.ndarray) and bridge_override.shape[0] == count:
        return bridge_override

    chain_upper = _array_as_upper(
        getattr(atom_array, "chain_id", np.array([""] * count, dtype=object)),
        count,
    )
    current_values = {str(item).strip().upper() for item in chain_upper.tolist() if str(item).strip()}
    if current_values.issubset({"", ".", "?"}):
        auth_override = _auth_chain_override_for_structure(structure_path, count)
        if auth_override is not None:
            return auth_override
    return chain_upper


def _selection_numeric_radius(selection_cfg: dict[str, Any], key: str) -> float | None:
    if key not in selection_cfg:
        return None
    try:
        return max(0.0, float(selection_cfg.get(key)))
    except Exception:
        return None


def _ligand_reference_mask_for_features(
    *,
    atom_array: Any,
    polymer_mask: np.ndarray,
    semantic_ligand_mask: np.ndarray,
) -> np.ndarray:
    strict_mask = ligand_reference_mask(
        atom_array=atom_array,
        polymer_mask=polymer_mask,
        solvent_res_names=SOLVENT_RES_NAMES,
    )
    if bool(np.any(strict_mask)):
        return strict_mask
    if bool(np.any(semantic_ligand_mask)):
        return np.asarray(semantic_ligand_mask, dtype=bool)
    return strict_mask


def _ligand_proximity_mask(
    atom_array: Any,
    *,
    polymer_mask: np.ndarray,
    semantic_ligand_mask: np.ndarray,
    radius: float,
    query_mask: np.ndarray | None = None,
) -> np.ndarray:
    count = int(polymer_mask.shape[0])
    coords = coordinates_from_atom_array(atom_array)
    if coords is None or coords.shape[0] != count:
        return np.zeros(count, dtype=bool)
    ref_mask = _ligand_reference_mask_for_features(
        atom_array=atom_array,
        polymer_mask=polymer_mask,
        semantic_ligand_mask=semantic_ligand_mask,
    )
    return proximity_mask_within_radius(
        coords,
        ref_mask,
        radius=max(0.0, float(radius)),
        query_mask=query_mask,
        chunk_size=512,
    )


def _selection_indices_from_cfg(
    load_result: Any,
    selection_cfg: dict[str, Any],
    *,
    structure_path: Path,
) -> list[int] | None:
    atom_array = _get_atom_array(load_result)
    if atom_array is None:
        return None

    count = _atom_array_count(atom_array)
    if count <= 0:
        return []

    chain_upper = _chain_upper_for_selection(load_result, atom_array, count, structure_path)
    query_value = selection_cfg.get("query")
    mask = np.ones(count, dtype=bool)
    has_filter = False
    if isinstance(query_value, str) and query_value.strip():
        query_indices = _query_to_indices(
            atom_array,
            query_value,
            chain_upper_override=chain_upper,
        )
        if query_indices is not None:
            query_mask = np.zeros(count, dtype=bool)
            for idx in query_indices:
                if 0 <= int(idx) < count:
                    query_mask[int(idx)] = True
            mask &= query_mask
            has_filter = True

    polymer_mask, nucleic_mask, ligand_mask = _semantic_masks(atom_array, count)
    if bool(selection_cfg.get("polymer", False)):
        mask &= polymer_mask
        has_filter = True
    if bool(selection_cfg.get("nucleic", False)):
        mask &= nucleic_mask
        has_filter = True
    if bool(selection_cfg.get("ligand", False)):
        mask &= ligand_mask
        has_filter = True

    chain_value = selection_cfg.get("chain")
    chain_ids: list[str] = []
    if isinstance(chain_value, str):
        chain_ids = [item.strip().upper() for item in chain_value.split(",") if item.strip()]
    elif isinstance(chain_value, (list, tuple)):
        chain_ids = [str(item).strip().upper() for item in chain_value if str(item).strip()]
    if chain_ids:
        mask &= np.isin(chain_upper, chain_ids)
        has_filter = True

    residue_range = selection_cfg.get("residue_range")
    res_ids = np.asarray(getattr(atom_array, "res_id", np.full(count, -1, dtype=int))).astype(int)
    if isinstance(residue_range, (list, tuple)) and len(residue_range) == 2:
        try:
            start = int(residue_range[0])
            end = int(residue_range[1])
            lo, hi = (start, end) if start <= end else (end, start)
            mask &= (res_ids >= lo) & (res_ids <= hi)
            has_filter = True
        except Exception:
            pass

    within_ligand_radius = _selection_numeric_radius(selection_cfg, "within_ligand_radius")
    if within_ligand_radius is not None:
        mask &= _ligand_proximity_mask(
            atom_array,
            polymer_mask=polymer_mask,
            semantic_ligand_mask=ligand_mask,
            radius=within_ligand_radius,
        )
        has_filter = True

    exclude_within_ligand_radius = _selection_numeric_radius(selection_cfg, "exclude_within_ligand_radius")
    if exclude_within_ligand_radius is not None:
        mask &= ~_ligand_proximity_mask(
            atom_array,
            polymer_mask=polymer_mask,
            semantic_ligand_mask=ligand_mask,
            radius=exclude_within_ligand_radius,
        )
        has_filter = True

    if bool(selection_cfg.get("expand_to_residue", False)):
        mask = expand_mask_to_residues(mask, chain_upper, res_ids)
        has_filter = True

    if not has_filter:
        return None
    return np.where(mask)[0].astype(int).tolist()


def _selection_to_named_attribute(
    load_result: Any,
    indices: list[int] | None,
    *,
    style_name: str,
) -> Any:
    if indices is None:
        return None
    if not hasattr(load_result, "store_named_attribute"):
        return indices

    atom_array = _get_atom_array(load_result)
    if atom_array is None:
        return indices
    count = _atom_array_count(atom_array)
    if count <= 0:
        return indices

    mask = np.zeros(count, dtype=bool)
    for idx in indices:
        if 0 <= int(idx) < count:
            mask[int(idx)] = True

    style_token = re.sub(r"[^A-Za-z0-9_]+", "_", str(style_name or "style")).strip("_") or "style"
    attr_name = f"bridge_sel_{style_token.lower()}_{uuid.uuid4().hex[:8]}"
    try:
        load_result.store_named_attribute(mask, name=attr_name, atype="BOOLEAN", domain="POINT")
        return attr_name
    except Exception:
        pass
    try:
        load_result.store_named_attribute(mask, name=attr_name)
        return attr_name
    except Exception:
        return indices


def _normalized_amino_mask(atom_array: Any, count: int) -> np.ndarray:
    try:
        import biotite.structure as bs  # type: ignore

        return _safe_bool_mask(bs.filter_amino_acids(atom_array), count)
    except Exception:
        res_name_upper = _array_as_upper(getattr(atom_array, "res_name", np.array([""] * count, dtype=object)), count)
        return np.isin(
            res_name_upper,
            [
                "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
                "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
                "PTR", "TPO", "SEP",
            ],
        )


def _normalize_sec_struct_values(values: Any, amino_mask: np.ndarray) -> np.ndarray:
    count = int(amino_mask.shape[0])
    sec = np.zeros(count, dtype=int)
    raw = np.asarray(values, dtype=object)
    if raw.shape[0] != count:
        return sec

    parsed_numeric = False
    try:
        numeric = raw.astype(float)
        finite = np.isfinite(numeric)
        if bool(np.any(finite)):
            clipped = np.clip(np.rint(numeric), 0, 3).astype(int)
            sec[finite] = clipped[finite]
            parsed_numeric = True
    except Exception:
        parsed_numeric = False

    if not parsed_numeric:
        text = np.char.upper(raw.astype(str))
        helix_mask = np.isin(text, ["1", "H", "A", "G", "I"])
        sheet_mask = np.isin(text, ["2", "S", "E", "B"])
        loop_mask = np.isin(text, ["3", "L", "C", "T"])
        sec[helix_mask] = 1
        sec[sheet_mask] = 2
        sec[loop_mask] = 3

    sec[~amino_mask] = 0
    sec[np.asarray(amino_mask, dtype=bool) & (sec == 0)] = 3
    return sec








def _extract_auth_chain_ids_from_cif(structure_path: Path) -> list[str] | None:
    # Prefer a lightweight text parse so chain recovery works even when
    # optional parser APIs differ across MolecularNodes/biotite versions.
    try:
        lines = structure_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        lines = []

    headers: list[str] = []
    reading_atom_loop = False
    parsed_chain_ids: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.lower() == "loop_":
            headers = []
            reading_atom_loop = False
            continue

        if line.startswith("_atom_site."):
            reading_atom_loop = True
            headers.append(line.split(".", 1)[1].strip())
            continue

        if reading_atom_loop and headers and line.startswith("_"):
            reading_atom_loop = False
            headers = []

        if reading_atom_loop and headers:
            parts = _split_cif_fields(line)
            if not parts or parts[0].upper() not in {"ATOM", "HETATM"}:
                continue
            header_idx = {name: idx for idx, name in enumerate(headers)}
            auth_idx = header_idx.get("auth_asym_id")
            label_idx = header_idx.get("label_asym_id")
            chain_idx = auth_idx if auth_idx is not None else label_idx
            if chain_idx is None or chain_idx >= len(parts):
                continue
            chain_id = _strip_wrapping_quotes(str(parts[chain_idx])).strip().upper()
            if chain_id and chain_id not in {".", "?"}:
                parsed_chain_ids.append(chain_id)

    if parsed_chain_ids:
        return parsed_chain_ids

    try:
        import biotite.structure.io.pdbx as pdbx  # type: ignore
    except Exception:
        return None

    try:
        cif_file = pdbx.CIFFile.read(str(structure_path))
        keys = list(cif_file.keys())
        if not keys:
            return None
        block = cif_file[keys[0]]
        if "atom_site" not in block:
            return None
        atom_site = block["atom_site"]
        if "auth_asym_id" not in atom_site:
            return None
        values = atom_site["auth_asym_id"].as_array(str)
        return [_strip_wrapping_quotes(str(item)).strip().upper() for item in values]
    except Exception:
        return None





def _apply_auth_chain_override(load_result: Any, structure_path: Path) -> None:
    atom_array = _get_atom_array(load_result)
    if atom_array is None:
        return
    count = _atom_array_count(atom_array)
    if count <= 0:
        return

    chain_upper = _array_as_upper(
        getattr(atom_array, "chain_id", np.array([""] * count, dtype=object)),
        count,
    )
    current_values = {str(item).strip().upper() for item in chain_upper.tolist() if str(item).strip()}
    if not current_values.issubset({"", ".", "?"}):
        return

    auth_override = _auth_chain_override_for_structure(structure_path, count)
    if auth_override is None:
        return
    try:
        setattr(load_result, "_bridge_chain_override_upper", auth_override)
    except Exception:
        pass

    patched = False
    try:
        atom_array.chain_id = auth_override
        patched = True
    except Exception:
        patched = False
    if not patched:
        try:
            categories = list(atom_array.get_annotation_categories()) if hasattr(atom_array, "get_annotation_categories") else []
            if "chain_id" in categories:
                try:
                    atom_array.del_annotation("chain_id")
                except Exception:
                    pass
            atom_array.set_annotation("chain_id", auth_override)
            patched = True
        except Exception:
            patched = False
    if patched and _env_bool("PRESS_DEBUG_CHAIN_PATCH", False):
        print("Bridge Custom: patched molecule chain_id from CIF auth_asym_id")


def _cycles_compute_options(cycles_prefs) -> list[str]:
    try:
        prop = cycles_prefs.bl_rna.properties.get("compute_device_type")
        if prop is None:
            return []
        return [str(item.identifier).upper() for item in prop.enum_items]
    except Exception:
        return []


def _configure_cycles_gpu_for_custom(scene: bpy.types.Scene, renderer: dict[str, Any]) -> None:
    requested = str(renderer.get("compute_device_type", "AUTO")).upper()
    want_gpu = scene.cycles.device == "GPU"
    addon = bpy.context.preferences.addons.get("cycles")
    candidates = ["METAL", "CUDA", "OPTIX", "HIP", "ONEAPI"] if requested == "AUTO" else [requested]
    selected, devices = None, []
    if addon and want_gpu:
        prefs = addon.preferences
        for candidate in candidates:
            try:
                prefs.compute_device_type = candidate
                prefs.get_devices()
                available = [d for d in prefs.devices if d.type == candidate]
                if not available:
                    continue
                for device in prefs.devices:
                    device.use = device.type == candidate
                selected = candidate
                devices = [{"name":d.name,"type":d.type} for d in available]
                break
            except (TypeError, RuntimeError):
                continue
    if want_gpu and not devices:
        if bool(renderer.get("require_gpu", _env_bool("PRESS_REQUIRE_GPU", False))):
            raise RuntimeError("No usable GPU found for the requested Cycles backend")
        scene.cycles.device = "CPU"
    info = {"device":scene.cycles.device,"compute_type":selected,"devices":devices}
    scene["bridge_device_info"] = json.dumps(info)
    print("Cycles device: " + json.dumps(info), flush=True)






def _ensure_biotite_ccd_database(*, before_execution: bool = False) -> None:
    from blender_render.worker_ccd import require
    require(before_execution=before_execution)


def _load_molecule_with_fallback(structure_path: Path, object_name: str):
    _configure_molecularnodes_import_path()
    import molecularnodes as mn
    _ensure_molecularnodes_ready(mn)
    return mn.Molecule.load(str(structure_path), name=object_name)



def _resolve_loaded_object(load_result: Any, object_name: str, *, allow_missing: bool = False) -> bpy.types.Object | None:
    if load_result is not None:
        if hasattr(load_result, "type") and hasattr(load_result, "name"):
            return load_result
        if hasattr(load_result, "object") and getattr(load_result, "object") is not None:
            return getattr(load_result, "object")

    obj = bpy.data.objects.get(object_name)
    if obj is None and not allow_missing:
        raise RuntimeError(f"Molecule object {object_name!r} was not created.")
    return obj


def _clear_scene_lights() -> None:
    for obj in list(bpy.data.objects):
        if obj.type == "LIGHT":
            bpy.data.objects.remove(obj, do_unlink=True)


def _configure_renderer(scene: bpy.types.Scene, style_doc: dict[str, Any]) -> None:
    output = style_doc["output"]
    renderer = style_doc["renderer"]

    engine = str(renderer.get("engine", "CYCLES")).upper()
    if engine == "EEVEE":
        engine = "BLENDER_EEVEE"

    scene.render.engine = engine
    scene.render.resolution_x = int(output["width"])
    scene.render.resolution_y = int(output["height"])
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = str(output.get("format", "PNG")).upper()
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.render.film_transparent = bool(renderer.get("transparent_background", False))
    if isinstance(renderer.get("exposure"), (int, float)):
        scene.view_settings.exposure = float(renderer["exposure"])
    if isinstance(renderer.get("gamma"), (int, float)):
        scene.view_settings.gamma = float(renderer["gamma"])
    if isinstance(renderer.get("view_transform"), str):
        scene.view_settings.view_transform = str(renderer["view_transform"])
    if isinstance(renderer.get("look"), str):
        scene.view_settings.look = str(renderer["look"])

    if engine == "CYCLES" and hasattr(scene, "cycles"):
        scene.cycles.samples = int(output.get("samples", 128))
        scene.cycles.device = str(renderer.get("device", "GPU")).upper()
        scene.cycles.use_adaptive_sampling = bool(renderer.get("adaptive_sampling", True))
        scene.cycles.adaptive_threshold = float(renderer.get("adaptive_threshold", 0.01))
        try:
            scene.cycles.seed = int(renderer.get("seed", 0))
        except Exception:
            pass
        if hasattr(scene.cycles, "use_animated_seed"):
            try:
                scene.cycles.use_animated_seed = False
            except Exception:
                pass
        # Some host Blender builds are compiled without OIDN/OptiX denoiser.
        # Keep denoising disabled by default unless style explicitly asks for it.
        denoise_enabled = bool(renderer.get("denoise", False))
        if hasattr(scene.cycles, "use_denoising"):
            try:
                scene.cycles.use_denoising = denoise_enabled
            except Exception:
                pass
        if hasattr(scene.cycles, "use_preview_denoising"):
            try:
                scene.cycles.use_preview_denoising = denoise_enabled
            except Exception:
                pass
        for view_layer in getattr(scene, "view_layers", []):
            cycles_layer = getattr(view_layer, "cycles", None)
            if cycles_layer is None:
                continue
            if hasattr(cycles_layer, "use_denoising"):
                try:
                    cycles_layer.use_denoising = denoise_enabled
                except Exception:
                    pass
        _configure_cycles_gpu_for_custom(scene, renderer)


def _configure_raw_viewport_mode(scene: bpy.types.Scene) -> None:
    # Emulate Blender's live viewport appearance instead of Cycles/EEVEE ray tracing.
    scene.render.engine = "BLENDER_WORKBENCH"
    display = getattr(scene, "display", None)
    shading = getattr(display, "shading", None) if display is not None else None
    if shading is None:
        return
    try:
        shading.type = "SOLID"
    except Exception:
        pass
    try:
        shading.light = "STUDIO"
    except Exception:
        pass
    try:
        shading.color_type = "MATERIAL"
    except Exception:
        pass
    try:
        shading.show_cavity = True
    except Exception:
        pass
    try:
        shading.show_object_outline = False
    except Exception:
        pass


def _render_raw_view(scene: bpy.types.Scene) -> None:
    _configure_raw_viewport_mode(scene)
    try:
        bpy.ops.render.opengl(animation=False, write_still=True, view_context=False)
        return
    except TypeError:
        # Blender API signatures vary slightly by release.
        try:
            bpy.ops.render.opengl(write_still=True)
            return
        except Exception:
            pass
    except Exception:
        pass
    # Fallback: still non-raytraced output via Workbench render pipeline.
    bpy.ops.render.render(animation=False, write_still=True)


def _set_world(scene: bpy.types.Scene, world_cfg: dict[str, Any]) -> None:
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
    world = scene.world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputWorld")
    out.location = (480, 0)
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.location = (280, 0)
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    bg.inputs["Strength"].default_value = float(world_cfg.get("strength", 1.0))

    background_type = str(world_cfg.get("background_type", "solid")).lower()
    if background_type == "gradient":
        top_color = _hex_to_rgba(str(world_cfg.get("top_color", "#DDEAF5")))
        bottom_color = _hex_to_rgba(str(world_cfg.get("bottom_color", "#C9D9E8")))

        coord = nt.nodes.new("ShaderNodeTexCoord")
        coord.location = (-540, 0)
        separate = nt.nodes.new("ShaderNodeSeparateXYZ")
        separate.location = (-320, 0)
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.location = (-80, 0)

        ramp.color_ramp.elements[0].position = 0.0
        ramp.color_ramp.elements[0].color = bottom_color
        ramp.color_ramp.elements[1].position = 1.0
        ramp.color_ramp.elements[1].color = top_color

        nt.links.new(coord.outputs["Window"], separate.inputs["Vector"])
        direction = world_cfg.get("gradient_direction", [0.0, 1.0])
        from blender_render.reference_effects import directional_factor
        factor = directional_factor(nt, coord.outputs["Window"], [*direction, 0.0])
        nt.links.new(factor, ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
    else:
        color = _hex_to_rgba(str(world_cfg.get("color", "#DDEAF5")))
        bg.inputs["Color"].default_value = color

    if "ambient_color" in world_cfg or "ambient_strength" in world_cfg:
        ambient = nt.nodes.new("ShaderNodeBackground")
        ambient.name = "Ambient illumination"
        ambient.inputs["Color"].default_value = _hex_to_rgba(world_cfg.get("ambient_color", "#FFFFFF"))
        ambient.inputs["Strength"].default_value = float(world_cfg.get("ambient_strength", 1.0))
        light_path = nt.nodes.new("ShaderNodeLightPath")
        mix = nt.nodes.new("ShaderNodeMixShader")
        nt.links.new(light_path.outputs["Is Camera Ray"], mix.inputs[0])
        nt.links.new(ambient.outputs["Background"], mix.inputs[1])
        nt.links.new(bg.outputs["Background"], mix.inputs[2])
        nt.links.new(mix.outputs[0], out.inputs["Surface"])


def _configure_glow_compositor(scene: bpy.types.Scene, style_doc: dict[str, Any]) -> None:
    """Add one optional fog-glow pass for materials that explicitly request it."""
    glows = [material.get("glow") for material in style_doc.get("materials", {}).values()
             if isinstance(material, dict) and isinstance(material.get("glow"), dict)]
    if not glows:
        return
    strength = max(float(item.get("strength", 0.0)) for item in glows)
    if strength <= 0:
        return
    threshold = min(float(item.get("threshold", 1.0)) for item in glows)
    size = max(2, min(9, max(int(item.get("size", 6)) for item in glows)))
    tree = getattr(scene, "compositing_node_group", None)
    if tree is None:
        try:
            tree = bpy.data.node_groups.new("Blender Render compositor", "CompositorNodeTree")
            scene.compositing_node_group = tree
        except Exception:
            scene.use_nodes = True
            tree = scene.node_tree
    tree.nodes.clear()
    layers = tree.nodes.new("CompositorNodeRLayers")
    glare = tree.nodes.new("CompositorNodeGlare")
    if hasattr(glare, "glare_type"):
        glare.glare_type = "FOG_GLOW"
    if hasattr(glare, "quality"):
        glare.quality = "HIGH"
    if glare.inputs.get("Quality") is not None:
        glare.inputs["Quality"].default_value = "High"
    if glare.inputs.get("Type") is not None:
        glare.inputs["Type"].default_value = "Fog Glow"
    if glare.inputs.get("Threshold") is not None:
        glare.inputs["Threshold"].default_value = threshold
    elif hasattr(glare, "threshold"):
        glare.threshold = threshold
    if glare.inputs.get("Strength") is not None:
        glare.inputs["Strength"].default_value = strength
    if hasattr(glare, "size"):
        glare.size = max(6, size)
    if glare.inputs.get("Size") is not None:
        # JSON retains the legacy logarithmic pixel scale. Modern Blender
        # takes a fraction of the image; passing 6 directly covers the image.
        dimension = max(scene.render.resolution_x, scene.render.resolution_y)
        glare.inputs["Size"].default_value = min(1.0, (2.0 ** size) / dimension)
    tree.links.new(layers.outputs["Image"], glare.inputs["Image"])
    try:
        output = tree.nodes.new("CompositorNodeComposite")
    except Exception:
        if not any(socket.name == "Image" and socket.in_out == "OUTPUT" for socket in tree.interface.items_tree):
            tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        output = tree.nodes.new("NodeGroupOutput")
    tree.links.new(glare.outputs["Image"], output.inputs[0])


def _add_lights(
    scene: bpy.types.Scene,
    lights_cfg: list[dict[str, Any]],
    *,
    target: Vector | None = None,
    radius: float | None = None,
    pymol_up_axis: Vector | None = None,
    yaw_offset_deg: float = 0.0,
) -> None:
    _clear_scene_lights()
    if target is None:
        target = Vector((0.0, 0.0, 0.0))
    effective_radius = max(1e-6, float(radius if radius is not None else 1.0))

    for idx, cfg in enumerate(lights_cfg):
        light_type = str(cfg.get("type", "AREA")).upper()
        light_name = str(cfg.get("name", f"BridgeLight_{idx + 1}"))
        light_data = bpy.data.lights.new(name=light_name, type=light_type)
        light_obj = bpy.data.objects.new(light_name, light_data)
        scene.collection.objects.link(light_obj)

        light_data.energy = float(cfg.get("energy", 1000.0))
        if cfg.get("normalize_energy", False):
            light_data.energy *= effective_radius ** 2
        if hasattr(light_data, "size"):
            if "size_scale" in cfg:
                size_scale = float(cfg.get("size_scale", 1.0))
                min_size = float(cfg.get("min_size", 0.0))
                light_data.size = max(min_size, effective_radius * size_scale)
            elif "size" in cfg:
                light_data.size = float(cfg["size"])
        if "color" in cfg:
            light_data.color = _hex_to_rgba(str(cfg["color"]), alpha=1.0)[:3]

        relative_vec = cfg.get("relative_vector")
        if isinstance(relative_vec, (list, tuple)) and len(relative_vec) == 3:
            dist_scale = float(cfg.get("distance_scale", 1.0))
            rel = Vector((float(relative_vec[0]), float(relative_vec[1]), float(relative_vec[2])))
            if cfg.get("camera_space", False):
                rel = scene.camera.matrix_world.to_3x3() @ rel
                if light_type == "AREA":
                    light_data.shape = "DISK"
            rotate_with_pymol_yaw = bool(cfg.get("rotate_with_pymol_yaw", False))
            if (
                rotate_with_pymol_yaw
                and pymol_up_axis is not None
                and pymol_up_axis.length > 1e-8
                and abs(float(yaw_offset_deg)) > 1e-8
            ):
                rel = Matrix.Rotation(
                    math.radians(float(yaw_offset_deg)),
                    3,
                    pymol_up_axis.normalized(),
                ) @ rel
            light_obj.location = target + (rel * (effective_radius * dist_scale))
            look_target = bool(cfg.get("look_at_target", True))
        else:
            location = cfg.get("location", [0.0, 0.0, 5.0])
            light_obj.location = Vector((float(location[0]), float(location[1]), float(location[2])))
            look_target = bool(cfg.get("look_at_target", False))

        rotation = cfg.get("rotation_deg")
        if isinstance(rotation, (list, tuple)) and len(rotation) == 3:
            light_obj.rotation_euler = Vector(
                (
                    math.radians(float(rotation[0])),
                    math.radians(float(rotation[1])),
                    math.radians(float(rotation[2])),
                )
            )
        elif look_target:
            delta = target - light_obj.location
            if delta.length > 1e-8:
                light_obj.rotation_euler = delta.to_track_quat("-Z", "Y").to_euler()


def _set_bsdf_input(node: bpy.types.ShaderNodeBsdfPrincipled, names: tuple[str, ...], value: Any) -> None:
    for name in names:
        sock = node.inputs.get(name)
        if sock is not None:
            sock.default_value = value
            return


def _create_material(name: str, cfg: dict[str, Any]) -> bpy.types.Material:
    if cfg.get("paint"):
        from blender_render.paint import make_paint_material
        material = make_paint_material(cfg["paint"])
        material.name = name
        return material
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.use_nodes = True
    nt = material.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (380, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (120, 0)

    base_color = str(cfg.get("base_color", "#A9C7E7"))
    shadow_color = str(cfg.get("shadow_color", base_color))
    highlight_color = str(cfg.get("highlight_color", base_color))
    ao_strength = float(cfg.get("ao_strength", 0.0))
    rim_strength = float(cfg.get("rim_strength", 0.0))
    alpha = float(cfg.get("alpha", 1.0))
    use_clay = (
        "shadow_color" in cfg
        or "highlight_color" in cfg
        or "ao_strength" in cfg
        or "rim_strength" in cfg
    )
    if use_clay:
        ao = nt.nodes.new("ShaderNodeAmbientOcclusion")
        ao.location = (-500, -140)
        if ao.inputs.get("Distance") is not None:
            ao.inputs["Distance"].default_value = float(cfg.get("ao_distance", 0.95))

        one_minus_ao = nt.nodes.new("ShaderNodeMath")
        one_minus_ao.location = (-320, -140)
        one_minus_ao.operation = "SUBTRACT"
        one_minus_ao.inputs[0].default_value = 1.0
        nt.links.new(ao.outputs["AO"], one_minus_ao.inputs[1])

        ao_mul = nt.nodes.new("ShaderNodeMath")
        ao_mul.location = (-140, -140)
        ao_mul.operation = "MULTIPLY"
        ao_mul.inputs[1].default_value = ao_strength
        nt.links.new(one_minus_ao.outputs["Value"], ao_mul.inputs[0])

        mix_shadow = nt.nodes.new("ShaderNodeMixRGB")
        mix_shadow.name = "Clay shadow"
        mix_shadow.location = (20, 80)
        mix_shadow.blend_type = "MIX"
        mix_shadow.inputs["Color1"].default_value = _hex_to_rgba(base_color)
        mix_shadow.inputs["Color2"].default_value = _hex_to_rgba(shadow_color)
        nt.links.new(ao_mul.outputs["Value"], mix_shadow.inputs["Fac"])

        layer_weight = nt.nodes.new("ShaderNodeLayerWeight")
        layer_weight.location = (-500, 120)
        if layer_weight.inputs.get("Blend") is not None:
            layer_weight.inputs["Blend"].default_value = float(cfg.get("rim_blend", 0.25))

        inv_facing = nt.nodes.new("ShaderNodeMath")
        inv_facing.location = (-320, 120)
        inv_facing.operation = "SUBTRACT"
        inv_facing.inputs[0].default_value = 1.0
        nt.links.new(layer_weight.outputs["Facing"], inv_facing.inputs[1])

        rim_pow = nt.nodes.new("ShaderNodeMath")
        rim_pow.location = (-140, 120)
        rim_pow.operation = "POWER"
        rim_pow.inputs[1].default_value = float(cfg.get("rim_power", 2.8))
        nt.links.new(inv_facing.outputs["Value"], rim_pow.inputs[0])

        rim_mul = nt.nodes.new("ShaderNodeMath")
        rim_mul.location = (20, 120)
        rim_mul.operation = "MULTIPLY"
        rim_mul.inputs[1].default_value = rim_strength
        nt.links.new(rim_pow.outputs["Value"], rim_mul.inputs[0])

        mix_rim = nt.nodes.new("ShaderNodeMixRGB")
        mix_rim.location = (180, 20)
        mix_rim.blend_type = "MIX"
        mix_rim.inputs["Color2"].default_value = _hex_to_rgba(highlight_color)
        nt.links.new(mix_shadow.outputs["Color"], mix_rim.inputs["Color1"])
        nt.links.new(rim_mul.outputs["Value"], mix_rim.inputs["Fac"])
        nt.links.new(mix_rim.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        bsdf.inputs["Base Color"].default_value = _hex_to_rgba(base_color, alpha=1.0)

    emission_color = cfg.get("emission_color")
    emission_strength = float(cfg.get("emission_strength", 0.0))
    if not isinstance(emission_color, str):
        emission_color = highlight_color

    _set_bsdf_input(bsdf, ("Roughness",), float(cfg.get("roughness", 0.45)))
    _set_bsdf_input(bsdf, ("Specular IOR Level", "Specular"), float(cfg.get("specular", 0.25)))
    _set_bsdf_input(bsdf, ("Transmission Weight", "Transmission"), float(cfg.get("transmission", 0.0)))
    _set_bsdf_input(bsdf, ("Coat Weight", "Coat"), float(cfg.get("coat", 0.0)))
    _set_bsdf_input(bsdf, ("Alpha",), alpha)
    if emission_strength > 0:
        _set_bsdf_input(bsdf, ("Emission Color", "Emission"), _hex_to_rgba(str(emission_color), 1.0))
        _set_bsdf_input(bsdf, ("Emission Strength",), emission_strength)

    from blender_render.reference_effects import apply_material_effects
    apply_material_effects(nt, bsdf, cfg, _hex_to_rgba)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    if hasattr(material, "blend_method"):
        material.blend_method = "HASHED" if alpha < 0.999 else "OPAQUE"
    if hasattr(material, "shadow_method"):
        material.shadow_method = "NONE" if alpha < 0.999 else "OPAQUE"

    return material


def _build_materials(materials_cfg: dict[str, dict[str, Any]]) -> dict[str, bpy.types.Material]:
    return {name: _create_material(name, cfg) for name, cfg in materials_cfg.items()}


def _apply_material_fallback(obj: bpy.types.Object, material: bpy.types.Material) -> None:
    data = getattr(obj, "data", None)
    if data is None:
        return
    materials = getattr(data, "materials", None)
    if materials is None:
        return
    if len(materials) == 0:
        materials.append(material)
    else:
        for idx in range(len(materials)):
            materials[idx] = material


def _object_modifier_names(obj: bpy.types.Object | None) -> list[str]:
    if obj is None:
        return []
    names: list[str] = []
    for modifier in getattr(obj, "modifiers", []):
        name = str(getattr(modifier, "name", "")).strip()
        if name:
            names.append(name)
    return names


def _set_numeric_socket_default(socket: Any, value: float) -> bool:
    try:
        current = socket.default_value
    except Exception:
        return False
    try:
        if isinstance(current, (int, float)):
            socket.default_value = float(value)
            return True
        if isinstance(current, (list, tuple)) and current:
            updated = list(current)
            updated[0] = float(value)
            socket.default_value = type(current)(updated)
            return True
    except Exception:
        return False
    return False


def _is_sticks_atom_sphere_socket_name(socket_name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(socket_name).strip().lower()).strip()
    if not normalized:
        return False
    tokens = set(normalized.split())
    if tokens.intersection({"sphere", "spheres", "ball", "balls", "vdw"}):
        return True
    if tokens.intersection({"atom", "atoms"}):
        if tokens.intersection({"radius", "scale", "size"}):
            return True
        if tokens.intersection({"show", "display", "enable", "enabled", "visible", "visibility"}):
            return True
    return False


def _tune_native_sticks_style(
    obj: bpy.types.Object,
    stick_radius: float | None,
    stick_quality: Any = None,
    stick_shade_smooth: Any = None,
    stick_color_blur: Any = None,
    disable_atom_spheres: bool = True,
    modifier_names: list[str] | None = None,
) -> int:
    """
    Molecular Nodes native sticks do not always honor radius/quality kwargs at
    create time. Apply the requested values directly on the GN style node.
    """
    changed = 0
    desired_quality = None
    if stick_quality is not None:
        try:
            quality = float(stick_quality)
            if math.isfinite(quality):
                desired_quality = max(1.0, quality)
        except Exception:
            desired_quality = None
    desired_shade_smooth = bool(stick_shade_smooth) if stick_shade_smooth is not None else None
    desired_color_blur = bool(stick_color_blur) if stick_color_blur is not None else None

    modifier_filter = {str(item) for item in modifier_names} if modifier_names else None
    debug_stick_sockets = _env_bool("PRESS_DEBUG_STICK_SOCKETS", False)

    for modifier in getattr(obj, "modifiers", []):
        if modifier_filter is not None and str(getattr(modifier, "name", "")) not in modifier_filter:
            continue
        if modifier.type != "NODES" or modifier.node_group is None:
            continue
        for node in modifier.node_group.nodes:
            node_name = str(getattr(node, "name", "")).strip().lower()
            if "stick" not in node_name:
                continue
            for socket in node.inputs:
                socket_name = str(getattr(socket, "name", "")).strip().lower()
                sphere_socket = _is_sticks_atom_sphere_socket_name(socket_name)
                if debug_stick_sockets:
                    print(
                        "Bridge Custom sticks socket: "
                        f"node={node_name} socket={socket_name} sphere_socket={int(sphere_socket)} "
                        f"value={getattr(socket, 'default_value', '<unreadable>')!r}"
                    )
                if disable_atom_spheres and sphere_socket:
                    default_value = getattr(socket, "default_value", None)
                    if isinstance(default_value, bool):
                        try:
                            socket.default_value = False
                            changed += 1
                        except Exception:
                            pass
                    elif isinstance(default_value, (int, float)):
                        if _set_numeric_socket_default(socket, 0.0):
                            changed += 1
                    continue
                if stick_radius is not None and "radius" in socket_name:
                    if _set_numeric_socket_default(socket, float(stick_radius)):
                        changed += 1
                    continue
                if desired_quality is not None and "quality" in socket_name:
                    if _set_numeric_socket_default(socket, float(desired_quality)):
                        changed += 1
                    continue
                if desired_shade_smooth is not None and "shade smooth" in socket_name:
                    try:
                        socket.default_value = desired_shade_smooth
                        changed += 1
                    except Exception:
                        pass
                    continue
                if desired_color_blur is not None and "color blur" in socket_name:
                    try:
                        socket.default_value = desired_color_blur
                        changed += 1
                    except Exception:
                        pass
                    continue
    return changed


def _socket_enables_feature(default_value: Any) -> bool:
    if isinstance(default_value, bool):
        return bool(default_value)
    if isinstance(default_value, (int, float)):
        try:
            return float(default_value) > 1e-6
        except Exception:
            return False
    if isinstance(default_value, (list, tuple)) and default_value:
        first = default_value[0]
        if isinstance(first, bool):
            return bool(first)
        if isinstance(first, (int, float)):
            try:
                return float(first) > 1e-6
            except Exception:
                return False
    return False


def _native_sticks_atom_spheres_enabled(
    obj: bpy.types.Object,
    *,
    modifier_names: list[str] | None = None,
) -> bool:
    modifier_filter = {str(item) for item in modifier_names} if modifier_names else None
    for modifier in getattr(obj, "modifiers", []):
        if modifier_filter is not None and str(getattr(modifier, "name", "")) not in modifier_filter:
            continue
        if modifier.type != "NODES" or modifier.node_group is None:
            continue
        for node in modifier.node_group.nodes:
            node_name = str(getattr(node, "name", "")).strip().lower()
            if "stick" not in node_name:
                continue
            for socket in node.inputs:
                socket_name = str(getattr(socket, "name", "")).strip().lower()
                if not _is_sticks_atom_sphere_socket_name(socket_name):
                    continue
                default_value = getattr(socket, "default_value", None)
                if _socket_enables_feature(default_value):
                    return True
    return False


def _assign_material_to_style_nodes(
    obj: bpy.types.Object,
    material: bpy.types.Material,
    *,
    modifier_names: list[str] | None = None,
) -> bool:
    assigned = 0
    modifier_filter = {str(item) for item in modifier_names} if modifier_names else None
    for modifier in obj.modifiers:
        if modifier_filter is not None and str(getattr(modifier, "name", "")) not in modifier_filter:
            continue
        if modifier.type != "NODES" or modifier.node_group is None:
            continue
        for node in modifier.node_group.nodes:
            for socket in getattr(node, "inputs", []):
                try:
                    socket_name = str(getattr(socket, "name", "")).strip().lower()
                    socket_type = str(getattr(socket, "type", "")).upper()
                    socket_id = str(getattr(socket, "bl_idname", "")).upper()
                    if "material" in socket_name or "MATERIAL" in socket_type or "MATERIAL" in socket_id:
                        socket.default_value = material
                        assigned += 1
                except Exception:
                    continue
        try:
            interface = getattr(modifier.node_group, "interface", None)
            items = list(getattr(interface, "items_tree", [])) if interface is not None else []
            for item in items:
                if str(getattr(item, "item_type", "")).upper() != "SOCKET":
                    continue
                if str(getattr(item, "in_out", "")).upper() not in {"INPUT", ""}:
                    continue
                socket_type = str(getattr(item, "socket_type", "")).upper()
                if "MATERIAL" not in socket_type:
                    continue
                identifier = str(getattr(item, "identifier", "")).strip()
                if not identifier:
                    continue
                modifier[identifier] = material
                assigned += 1
        except Exception:
            pass
    return assigned > 0


def _selector_call(selector: Any, names: tuple[str, ...], *args: Any) -> tuple[Any, bool]:
    for name in names:
        fn = getattr(selector, name, None)
        if not callable(fn):
            continue
        try:
            return fn(*args), True
        except Exception:
            continue
    return selector, False


def _build_selection(
    load_result: Any,
    selection_cfg: dict[str, Any],
    *,
    structure_path: Path,
    style_name: str,
) -> tuple[Any, list[int] | None]:
    indices = _selection_indices_from_cfg(
        load_result,
        selection_cfg,
        structure_path=structure_path,
    )
    if indices is not None:
        return _selection_to_named_attribute(
            load_result,
            indices,
            style_name=style_name,
        ), indices

    selector = getattr(load_result, "select", None)
    if selector is None:
        return None, None

    reset_fn = getattr(selector, "reset", None)
    if callable(reset_fn):
        selector = reset_fn()

    if selection_cfg.get("polymer"):
        selector, _ = _selector_call(selector, ("is_polymer", "polymer"))
    if selection_cfg.get("nucleic"):
        selector, _ = _selector_call(selector, ("is_nucleic", "is_nucleic_acid", "nucleic"))
    if selection_cfg.get("ligand"):
        selector, _ = _selector_call(selector, ("is_ligand", "ligand"))

    chain_value = selection_cfg.get("chain")
    if chain_value:
        chain_arg: Any = chain_value
        if isinstance(chain_value, str) and "," in chain_value:
            chain_arg = [item.strip() for item in chain_value.split(",") if item.strip()]
        selector, _ = _selector_call(selector, ("chain", "chain_id", "is_chain"), chain_arg)

    residue_range = selection_cfg.get("residue_range")
    if residue_range:
        start, end = int(residue_range[0]), int(residue_range[1])
        selector, ok = _selector_call(selector, ("residue_range", "res_id_range", "residue_id_range"), start, end)
        if not ok:
            values = list(range(start, end + 1 if end >= start else start + 1))
            selector, _ = _selector_call(selector, ("res_id", "residue_id"), values)

    return selector, None


def _is_native_mn_molecule(load_result: Any) -> bool:
    import molecularnodes as mn
    return isinstance(load_result, mn.Molecule)



def _native_style_from_cfg(style_cfg: dict[str, Any]) -> Any:
    import molecularnodes.nodes.styles as mn_styles

    style_kind = str(style_cfg.get("style", "spheres")).strip().lower()
    if style_kind == "cartoon":
        return mn_styles.StyleCartoon(
            quality=int(style_cfg.get("quality", 2)),
            peptide_dssp=bool(style_cfg.get("peptide_dssp", False)),
            peptide_cylinders=bool(style_cfg.get("peptide_cylinders", False)),
            peptide_arrows=bool(style_cfg.get("peptide_arrows", True)),
            peptide_rounded=bool(style_cfg.get("peptide_rounded", False)),
            peptide_smoothing=float(style_cfg.get("peptide_smoothing", 0.5)),
            peptide_thickness=float(style_cfg.get("thickness", 0.6)),
            peptide_width=float(style_cfg.get("width", 2.2)),
            peptide_loop_radius=float(style_cfg.get("loop_radius", 0.3)),
            nucleic_thickness=float(style_cfg.get("nucleic_thickness", 1.0)),
            nucleic_width=float(style_cfg.get("nucleic_width", 3.0)),
            nucleic_radius=float(style_cfg.get("nucleic_radius", 2.0)),
            backbone_shape=style_cfg.get("backbone_shape", "Cylinder"),
            base_shape=style_cfg.get("base_shape", "Rectangle"),
            base_realize=bool(style_cfg.get("base_realize", True)),
            color_blur=bool(style_cfg.get("color_blur", False)),
            shade_smooth=bool(style_cfg.get("shade_smooth", True)),
        )
    if style_kind == "sticks":
        sticks_radius = style_cfg.get("sticks_radius", style_cfg.get("radius", 0.2))
        sticks_quality = style_cfg.get("sticks_quality", style_cfg.get("quality", 3))
        return mn_styles.StyleSticks(
            quality=int(sticks_quality),
            radius=float(sticks_radius),
            color_blur=bool(style_cfg.get("color_blur", False)),
            shade_smooth=bool(style_cfg.get("sticks_shade_smooth", style_cfg.get("shade_smooth", True))),
        )
    if style_kind == "spheres":
        return mn_styles.StyleSpheres(
            radius=float(style_cfg.get("radius", 0.8)),
            subdivisions=int(style_cfg.get("subdivisions", 2)),
            shade_smooth=bool(style_cfg.get("shade_smooth", True)),
        )
    if style_kind == "ribbon":
        return mn_styles.StyleRibbon(
            quality=int(style_cfg.get("quality", 3)),
            backbone_radius=float(style_cfg.get("radius", 1.6)),
            backbone_width=float(style_cfg.get("width", 3.0)),
            backbone_thickness=float(style_cfg.get("thickness", 1.0)),
            color_blur=bool(style_cfg.get("color_blur", False)),
            shade_smooth=bool(style_cfg.get("shade_smooth", True)),
        )
    if style_kind == "surface":
        return mn_styles.StyleSurface(
            quality=int(style_cfg.get("quality", 3)),
            probe_size=float(style_cfg.get("probe_radius", 1.0)),
            scale_radius=float(style_cfg.get("surface_scale_radius", 1.5)),
            relaxation_steps=int(style_cfg.get("surface_relaxation_steps", 10)),
            shade_smooth=bool(style_cfg.get("shade_smooth", True)),
            color_blur=int(2 if bool(style_cfg.get("color_blur", False)) else 0),
        )
    if style_kind == "wire":
        return mn_styles.StyleDensityWire(
            wire_radius=float(style_cfg.get("radius", 1.0)),
            wire_resolution=int(style_cfg.get("quality", 3)),
        )
    return style_kind


def _add_style(load_result: Any, style_cfg: dict[str, Any], selection: Any,
               material: bpy.types.Material | None = None) -> Any:
    if not _is_native_mn_molecule(load_result):
        raise RuntimeError("Molecular Nodes 4.5.10 native molecule is required")
    before = {node.as_pointer() for node in load_result.tree.nodes}
    result = load_result.add_style(_native_style_from_cfg(style_cfg), selection=selection,
        color=None, name=style_cfg.get("name"), material=material)
    if "nucleic_base_scale" in style_cfg:
        from blender_render.reference_effects import scale_nucleic_bases
        branches = [node for node in load_result.tree.nodes if node.as_pointer() not in before
                    and getattr(getattr(node, "node_tree", None), "name", "").startswith("Style Cartoon")]
        if not branches:
            raise RuntimeError("Molecular Nodes cartoon branch missing for nucleic_base_scale")
        for branch in branches:
            scale_nucleic_bases(branch, float(style_cfg["nucleic_base_scale"]))
    return result



def _assign_style_material(
    *,
    load_result: Any,
    base_object: bpy.types.Object | None,
    style_result: Any,
    material: bpy.types.Material,
    modifier_names: list[str] | None = None,
) -> None:
    style_obj = getattr(style_result, "_object", None) if style_result is not None else None
    if style_obj is not None:
        if _assign_material_to_style_nodes(style_obj, material):
            return
        _apply_material_fallback(style_obj, material)
        return

    target = base_object or _resolve_loaded_object(load_result, "BridgeCustom", allow_missing=True)
    if target is None:
        return
    if modifier_names is not None:
        if modifier_names:
            _assign_material_to_style_nodes(target, material, modifier_names=modifier_names)
        return
    if not _assign_material_to_style_nodes(target, material):
        _apply_material_fallback(target, material)


def _style_category(style_cfg: dict[str, Any]) -> str:
    selection = style_cfg.get("selection")
    if isinstance(selection, dict):
        if bool(selection.get("ligand")):
            return "ligand"
        if bool(selection.get("polymer")):
            return "polymer"
        if bool(selection.get("nucleic")):
            return "nucleic"
        query = str(selection.get("query", "")).lower()
        if "ligand" in query:
            return "ligand"
        if "polymer" in query:
            return "polymer"
        if "nucleic" in query and "not nucleic" not in query:
            return "nucleic"
    return "polymer"


def _style_chain_candidates(style_cfg: dict[str, Any]) -> list[str]:
    selection = style_cfg.get("selection")
    if not isinstance(selection, dict):
        return []
    chain_value = selection.get("chain")
    chain_ids: list[str] = []
    if isinstance(chain_value, str):
        chain_ids.extend([item.strip() for item in chain_value.split(",") if item.strip()])
    elif isinstance(chain_value, (list, tuple)):
        chain_ids.extend([str(item).strip() for item in chain_value if str(item).strip()])
    query = str(selection.get("query", ""))
    chain_ids.extend(re.findall(r"\bchain\s*=?\s*([A-Za-z0-9_]+)", query, flags=re.IGNORECASE))
    return [item.upper() for item in chain_ids if item]


def _is_polymer_cartoon_style(style_cfg: dict[str, Any]) -> bool:
    if str(style_cfg.get("style", "")).strip().lower() != "cartoon":
        return False
    selection = style_cfg.get("selection")
    if isinstance(selection, dict):
        if bool(selection.get("polymer")):
            return True
        query = str(selection.get("query", "")).strip().lower()
        if "polymer" in query and "not polymer" not in query:
            return True
    style_name = str(style_cfg.get("name", "")).strip().lower()
    return style_name in {"chain_a_cartoon", "chain_b_cartoon", "polymer_other_cartoon"}


def _is_ligand_style(style_cfg: dict[str, Any]) -> bool:
    style_name = str(style_cfg.get("name", "")).strip().lower()
    if "ligand" in style_name:
        return True
    selection = style_cfg.get("selection")
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


def _protein_chain_ids(load_result: Any) -> list[str]:
    array = _get_atom_array(load_result)
    if array is None:
        return []
    try:
        count = int(array.array_length()) if hasattr(array, "array_length") else len(array)
    except Exception:
        return []
    if count <= 0:
        return []

    try:
        polymer_mask, nucleic_mask, _ = _semantic_masks(array, count)
        protein_mask = np.asarray(polymer_mask, dtype=bool) & ~np.asarray(nucleic_mask, dtype=bool)
    except Exception:
        return []

    if not bool(np.any(protein_mask)):
        return []

    chain_getter = getattr(load_result, "_chain_upper", None)
    bridge_override = getattr(load_result, "_bridge_chain_override_upper", None)
    if isinstance(bridge_override, np.ndarray) and bridge_override.shape[0] == count:
        chain_upper = np.asarray(bridge_override, dtype=object).astype(str)
    elif callable(chain_getter):
        try:
            chain_upper = np.asarray(chain_getter(count), dtype=object).astype(str)
        except Exception:
            chain_upper = _array_as_upper(getattr(array, "chain_id", np.array([""] * count, dtype=object)), count)
    else:
        chain_upper = _array_as_upper(getattr(array, "chain_id", np.array([""] * count, dtype=object)), count)

    seen: set[str] = set()
    ordered: list[str] = []
    for chain_id in chain_upper[protein_mask]:
        value = _strip_wrapping_quotes(str(chain_id)).strip().upper()
        if not value or value in {".", "?"}:
            continue
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _auto_chain_fallback_query(style_cfg: dict[str, Any], chain_ids: list[str]) -> str | None:
    if not chain_ids:
        return None

    style_name = str(style_cfg.get("name", "")).strip().lower()
    if not style_name:
        return None

    if "chain_a" in style_name:
        if len(chain_ids) >= 1:
            return f"polymer and not nucleic and chain {chain_ids[0]}"
        return None
    if "chain_b" in style_name:
        if len(chain_ids) >= 2:
            return f"polymer and not nucleic and chain {chain_ids[1]}"
        return None
    if "polymer_other" in style_name:
        remaining = chain_ids[2:]
        if remaining:
            joined = ",".join(remaining)
            return f"polymer and not nucleic and chain {joined}"
        return None
    return None


def _apply_color_follow_to_materials(style_doc: dict[str, Any], view_doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    materials_cfg = json.loads(json.dumps(style_doc.get("materials", {})))
    color_follow_cfg = style_doc.get("color_follow")
    if not isinstance(color_follow_cfg, dict):
        return materials_cfg
    if not bool(color_follow_cfg.get("enabled", False)):
        return materials_cfg

    color_hints = view_doc.get("color_hints")
    if not isinstance(color_hints, dict):
        return materials_cfg

    chain_hints = color_hints.get("chain")
    class_hints = color_hints.get("classes")
    if not isinstance(chain_hints, dict):
        chain_hints = {}
    if not isinstance(class_hints, dict):
        class_hints = {}

    mode = str(color_follow_cfg.get("mode", "auto")).strip().lower()
    mix = float(color_follow_cfg.get("mix", 0.65))
    sat_delta = float(color_follow_cfg.get("saturation_boost", 0.08))
    value_delta = float(color_follow_cfg.get("value_boost", 0.03))

    styles = style_doc.get("styles", [])
    if not isinstance(styles, list):
        return materials_cfg

    for style_cfg in styles:
        if not isinstance(style_cfg, dict):
            continue
        material_name = str(style_cfg.get("material", "")).strip()
        if not material_name or material_name not in materials_cfg:
            continue

        target_hex: str | None = None
        if mode in {"auto", "chain"}:
            for chain_id in _style_chain_candidates(style_cfg):
                value = chain_hints.get(chain_id) or chain_hints.get(chain_id.upper()) or chain_hints.get(chain_id.lower())
                if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                    target_hex = value
                    break

        if target_hex is None and mode in {"auto", "class"}:
            category = _style_category(style_cfg)
            value = class_hints.get(category)
            if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                target_hex = value

        if target_hex is None:
            continue

        material_cfg = materials_cfg.get(material_name)
        if not isinstance(material_cfg, dict):
            continue
        current_hex = str(material_cfg.get("base_color", "#A9C7E7"))
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", current_hex):
            current_hex = "#A9C7E7"

        base_rgb = _hex_to_rgb(current_hex)
        target_rgb = _hex_to_rgb(target_hex)
        mixed_rgb = _mix_rgb(base_rgb, target_rgb, mix=mix)
        tuned_rgb = _adjust_hsv(mixed_rgb, sat_delta=sat_delta, value_delta=value_delta)
        shadow_rgb = _shade_from_base(tuned_rgb, factor=0.72)
        highlight_rgb = _highlight_from_base(tuned_rgb, factor=0.20)

        material_cfg["base_color"] = _rgb_to_hex(tuned_rgb)
        material_cfg["shadow_color"] = _rgb_to_hex(shadow_rgb)
        material_cfg["highlight_color"] = _rgb_to_hex(highlight_rgb)
        if _env_bool("PRESS_COLOR_FOLLOW_LOG", False):
            print(
                "Bridge Custom: color-follow "
                f"material={material_name} target={target_hex} base={material_cfg['base_color']}"
            )

    return materials_cfg


def _camera_axis_map_matrix(axis_map: str, sx: float, sy: float, sz: float) -> Matrix:
    candidate = (axis_map or "XYZ").strip().upper()
    if len(candidate) != 3 or set(candidate) != {"X", "Y", "Z"}:
        candidate = "XYZ"
    axis_idx = {"X": 0, "Y": 1, "Z": 2}
    signs = (sx, sy, sz)
    rows = []
    for row_idx, axis_name in enumerate(candidate):
        row = [0.0, 0.0, 0.0]
        row[axis_idx[axis_name]] = signs[row_idx]
        rows.append(tuple(row))
    return Matrix(rows)


def _compute_camera_spec(view_doc: dict[str, Any]) -> tuple[list[list[float]], str, float, float | None, float, float]:
    pymol_view = view_doc.get("pymol_view")
    if not isinstance(pymol_view, list) or len(pymol_view) < 18:
        raise RuntimeError("view.json is missing a valid pymol_view list of 18 values.")

    values = [float(item) for item in pymol_view[:18]]
    if _env_bool("PRESS_PYMOL_VIEW_COLUMN_MAJOR", True):
        rot_world_to_cam = Matrix(
            (
                (values[0], values[3], values[6]),
                (values[1], values[4], values[7]),
                (values[2], values[5], values[8]),
            )
        )
    else:
        rot_world_to_cam = Matrix(
            (
                (values[0], values[1], values[2]),
                (values[3], values[4], values[5]),
                (values[6], values[7], values[8]),
            )
        )

    sx = -1.0 if _env_bool("PRESS_PYMOL_FLIP_X", False) else 1.0
    sy = -1.0 if _env_bool("PRESS_PYMOL_FLIP_Y", False) else 1.0
    sz = -1.0 if _env_bool("PRESS_PYMOL_FLIP_Z", False) else 1.0
    axis_map = os.getenv("PRESS_PYMOL_AXIS_MAP", "XYZ")
    cam_space_to_blender = _camera_axis_map_matrix(axis_map, sx, sy, sz)
    cam_to_world = rot_world_to_cam.transposed() @ cam_space_to_blender

    o_cam = Vector((values[9], values[10], values[11]))
    o_model = Vector((values[12], values[13], values[14]))
    cam_pos = o_model - (cam_to_world @ o_cam)

    matrix_world = [
        [cam_to_world[0][0], cam_to_world[0][1], cam_to_world[0][2], cam_pos.x],
        [cam_to_world[1][0], cam_to_world[1][1], cam_to_world[1][2], cam_pos.y],
        [cam_to_world[2][0], cam_to_world[2][1], cam_to_world[2][2], cam_pos.z],
        [0.0, 0.0, 0.0, 1.0],
    ]

    fov_raw = view_doc.get("fov_deg")
    try:
        fov_value = float(fov_raw) if fov_raw is not None else float(abs(values[17]))
    except Exception:
        fov_value = float(abs(values[17]))
    if not (5.0 <= fov_value <= 120.0):
        fov_value = 50.0

    front = max(1e-3, values[15])
    rear = max(front + 1e-3, values[16])
    ortho_raw = view_doc.get("orthoscopic")
    is_ortho = bool(ortho_raw) if ortho_raw is not None else values[17] <= 0.0
    ortho_scale = None
    if is_ortho:
        distance = max(1e-6, abs(values[11]))
        ortho_scale = 2.0 * distance * math.tan(math.radians(fov_value) * 0.5)

    return (matrix_world, "ORTHO" if is_ortho else "PERSP", fov_value, ortho_scale, front, rear)


def _ensure_camera(scene: bpy.types.Scene, style_doc: dict[str, Any], view_doc: dict[str, Any]) -> bpy.types.Object:
    matrix_world, camera_type, fov_deg, ortho_scale, clip_start, clip_end = _compute_camera_spec(view_doc)

    camera = scene.camera
    if camera is None or camera.type != "CAMERA":
        camera_data = bpy.data.cameras.new("PyMOL_Camera")
        camera = bpy.data.objects.new("PyMOL_Camera", camera_data)
        scene.collection.objects.link(camera)
        scene.camera = camera

    camera_cfg = style_doc.get("camera", {})
    world_scale = float(camera_cfg.get("world_scale", 0.0108))
    world_scale = max(1e-6, world_scale)

    matrix = Matrix(matrix_world)
    matrix.translation *= world_scale
    camera.matrix_world = matrix
    camera_data = camera.data
    camera_data.clip_start = max(1e-6, clip_start * world_scale)
    camera_data.clip_end = max(camera_data.clip_start + 1e-3, clip_end * world_scale)

    if camera_type == "ORTHO":
        camera_data.type = "ORTHO"
        if ortho_scale is not None:
            camera_data.ortho_scale = max(1e-6, ortho_scale * world_scale)
    else:
        camera_data.type = "PERSP"
        camera_data.angle = math.radians(fov_deg)

    if camera_cfg.get("preserve_framing", False):
        # PyMOL FOV is vertical; Blender's horizontal sensor expects the
        # corresponding horizontal extent, including for portrait viewports.
        viewport = view_doc.get("viewport_px", [scene.render.resolution_x, scene.render.resolution_y])
        aspect = float(viewport[0]) / float(viewport[1])
        camera_data.sensor_fit = "HORIZONTAL"
        if camera_type == "ORTHO":
            camera_data.ortho_scale *= aspect
        else:
            camera_data.angle = 2 * math.atan(math.tan(math.radians(fov_deg) / 2) * aspect)

    camera_mode = str(camera_cfg.get("mode", "pymol")).strip().lower()
    override_intrinsics = bool(camera_cfg.get("override_intrinsics", False))
    if camera_mode == "override" or override_intrinsics:
        if isinstance(camera_cfg.get("lens_mm"), (int, float)):
            camera_data.lens = float(camera_cfg["lens_mm"])
        if isinstance(camera_cfg.get("shift_x"), (int, float)):
            camera_data.shift_x = float(camera_cfg["shift_x"])
        if isinstance(camera_cfg.get("shift_y"), (int, float)):
            camera_data.shift_y = float(camera_cfg["shift_y"])

    return camera


def _point_camera_at(camera_obj: bpy.types.Object, target: Vector) -> None:
    direction = target - camera_obj.location
    if direction.length < 1e-8:
        return
    camera_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _orient_camera_with_up(camera_obj: bpy.types.Object, forward: Vector, up_hint: Vector) -> bool:
    if forward.length < 1e-8 or up_hint.length < 1e-8:
        return False
    fwd = forward.normalized()
    up = up_hint.normalized()
    z_axis = -fwd
    x_axis = up.cross(z_axis)
    if x_axis.length < 1e-8:
        return False
    x_axis.normalize()
    y_axis = z_axis.cross(x_axis)
    if y_axis.length < 1e-8:
        return False
    y_axis.normalize()
    rot = Matrix((x_axis, y_axis, z_axis)).transposed()
    camera_obj.matrix_world = Matrix.Translation(camera_obj.location) @ rot.to_4x4()
    return True


def _evaluated_mesh_stats(obj: Any) -> tuple[int, float]:
    if obj is None or str(getattr(obj, "type", "")).upper() != "MESH":
        return 0, 0.0
    eval_obj = None
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        eval_mesh = eval_obj.to_mesh()
    except Exception:
        return 0, 0.0

    try:
        vertices = getattr(eval_mesh, "vertices", None)
        if vertices is None:
            return 0, 0.0
        count = int(len(vertices))
        if count == 0:
            return 0, 0.0
        min_x = min(float(v.co.x) for v in vertices)
        max_x = max(float(v.co.x) for v in vertices)
        min_y = min(float(v.co.y) for v in vertices)
        max_y = max(float(v.co.y) for v in vertices)
        min_z = min(float(v.co.z) for v in vertices)
        max_z = max(float(v.co.z) for v in vertices)
        extent = max(max_x - min_x, max_y - min_y, max_z - min_z)
        return count, float(extent)
    except Exception:
        return 0, 0.0
    finally:
        if eval_obj is not None:
            try:
                eval_obj.to_mesh_clear()
            except Exception:
                pass



def _object_has_renderable_geometry(obj: bpy.types.Object) -> bool:
    if obj is None:
        return False
    if getattr(obj, "hide_render", False):
        return False
    try:
        if obj.hide_get():
            return False
    except Exception:
        pass
    obj_type = getattr(obj, "type", "")
    if obj_type not in {"MESH", "CURVE", "SURFACE"}:
        return False
    if obj_type == "MESH":
        eval_count, eval_extent = _evaluated_mesh_stats(obj)
        if eval_count <= 0 or eval_extent < 1e-8:
            return False
    return True


def _scene_bounds(objects: list[bpy.types.Object]) -> tuple[Vector, float]:
    all_corners: list[Vector] = []
    valid = [obj for obj in objects if _object_has_renderable_geometry(obj)]
    if not valid:
        valid = [obj for obj in bpy.data.objects if _object_has_renderable_geometry(obj)]
    if not valid:
        return Vector((0.0, 0.0, 0.0)), 1.0
    for obj in valid:
        try:
            all_corners.extend([obj.matrix_world @ Vector(corner) for corner in obj.bound_box])
        except Exception:
            continue
    if not all_corners:
        return Vector((0.0, 0.0, 0.0)), 1.0
    min_v = Vector((min(v.x for v in all_corners), min(v.y for v in all_corners), min(v.z for v in all_corners)))
    max_v = Vector((max(v.x for v in all_corners), max(v.y for v in all_corners), max(v.z for v in all_corners)))
    center = (min_v + max_v) * 0.5
    radius = max(1e-3, (max_v - min_v).length * 0.5)
    return center, radius


def _ligand_anchor_from_load_result(load_result: Any) -> Vector | None:
    atom_array = _get_atom_array(load_result)
    if atom_array is None:
        return None
    count = _atom_array_count(atom_array)
    if count <= 0:
        return None
    polymer_mask, _, semantic_ligand_mask = _semantic_masks(atom_array, count)
    ligand_mask = _ligand_reference_mask_for_features(
        atom_array=atom_array,
        polymer_mask=polymer_mask,
        semantic_ligand_mask=semantic_ligand_mask,
    )
    if not bool(np.any(ligand_mask)):
        if _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False):
            print("Bridge Custom: ligand anchor fallback found no ligand mask entries.")
        return None
    coords = coordinates_from_atom_array(atom_array)
    if coords is None or coords.shape[0] != count:
        if _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False):
            raw_coord = getattr(atom_array, "coord", None)
            raw_shape = getattr(raw_coord, "shape", None)
            print(
                "Bridge Custom: ligand anchor missing coordinate array "
                f"coord_type={type(raw_coord).__name__} "
                f"coord_shape={raw_shape} count={count}"
            )
        return None
    ligand_coords = coords[ligand_mask]
    finite = np.isfinite(ligand_coords).all(axis=1)
    if not bool(np.any(finite)):
        return None
    center = ligand_coords[finite].mean(axis=0)
    return Vector((float(center[0]), float(center[1]), float(center[2])))


def _ligand_cutout_axis(
    ligand_anchor: Vector,
    interface_center: Vector | None,
    camera_axis: Vector | None = None,
    *,
    prefer_camera: bool = False,
    flatten_z: bool = True,
) -> Vector:
    axis = Vector((0.0, 1.0, 0.0))
    ligand_axis = None
    if interface_center is not None:
        try:
            candidate_axis = ligand_anchor - interface_center
            if candidate_axis.length > 1e-8:
                ligand_axis = candidate_axis.normalized()
        except Exception:
            pass
    camera_norm = None
    if camera_axis is not None and camera_axis.length > 1e-8:
        try:
            camera_norm = camera_axis.normalized()
        except Exception:
            camera_norm = None

    if prefer_camera:
        if camera_norm is not None:
            axis = camera_norm
        elif ligand_axis is not None:
            axis = ligand_axis
    else:
        # Prefer interface-driven axis so cut direction tracks pocket geometry.
        # Camera-forward fallback can flip to an exterior-facing carve.
        if ligand_axis is not None:
            axis = ligand_axis
        elif camera_norm is not None:
            axis = camera_norm

    # Optional planar locking avoids front/back punches on near-front camera setups.
    if flatten_z:
        flat_axis = Vector((axis.x, axis.y, 0.0))
        if flat_axis.length > 1e-8:
            axis = flat_axis.normalized()
    return axis


def _indices_centroid_vector(load_result: Any, indices: list[int] | None) -> Vector | None:
    if not indices:
        return None
    atom_array = _get_atom_array(load_result)
    if atom_array is None:
        return None
    coords = coordinates_from_atom_array(atom_array)
    if coords is None:
        return None
    valid: list[int] = []
    count = int(coords.shape[0])
    for idx in indices:
        i = int(idx)
        if 0 <= i < count:
            valid.append(i)
    if not valid:
        return None
    subset = coords[np.asarray(valid, dtype=int)]
    finite = np.isfinite(subset).all(axis=1)
    if not bool(np.any(finite)):
        return None
    center = subset[finite].mean(axis=0)
    return Vector((float(center[0]), float(center[1]), float(center[2])))


def _map_atom_point_to_object_bounds(
    load_result: Any,
    target_obj: bpy.types.Object | None,
    point: Vector,
) -> Vector:
    if target_obj is None:
        return point.copy()
    atom_array = _get_atom_array(load_result)
    if atom_array is None:
        return point.copy()
    coords = coordinates_from_atom_array(atom_array)
    if coords is None or coords.size == 0:
        return point.copy()
    finite_rows = np.isfinite(coords).all(axis=1)
    if not bool(np.any(finite_rows)):
        return point.copy()
    finite_coords = coords[finite_rows]
    atom_min = finite_coords.min(axis=0)
    atom_max = finite_coords.max(axis=0)
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_obj = target_obj.evaluated_get(depsgraph)
    except Exception:
        eval_obj = target_obj
    try:
        bbox_corners = list(getattr(eval_obj, "bound_box", []) or [])
    except Exception:
        bbox_corners = []
    if not bbox_corners:
        return point.copy()
    try:
        world_corners = [eval_obj.matrix_world @ Vector(corner) for corner in bbox_corners]
    except Exception:
        world_corners = []
    if not world_corners:
        return point.copy()

    obj_min = np.array(
        [
            float(min(c.x for c in world_corners)),
            float(min(c.y for c in world_corners)),
            float(min(c.z for c in world_corners)),
        ],
        dtype=float,
    )
    obj_max = np.array(
        [
            float(max(c.x for c in world_corners)),
            float(max(c.y for c in world_corners)),
            float(max(c.z for c in world_corners)),
        ],
        dtype=float,
    )

    p = np.array([float(point.x), float(point.y), float(point.z)], dtype=float)
    mapped_np = np.zeros(3, dtype=float)
    for axis in range(3):
        atom_span = float(atom_max[axis] - atom_min[axis])
        obj_span = float(obj_max[axis] - obj_min[axis])
        if abs(atom_span) <= 1e-8:
            mapped_np[axis] = float(obj_min[axis])
        else:
            scale = obj_span / atom_span
            mapped_np[axis] = float(obj_min[axis]) + ((float(p[axis]) - float(atom_min[axis])) * scale)
    mapped = Vector((float(mapped_np[0]), float(mapped_np[1]), float(mapped_np[2])))
    if _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False):
        print(
            "Bridge Custom atom->object map: "
            f"target={getattr(target_obj, 'name', '<unknown>')} "
            f"atom_min=({atom_min[0]:.3f},{atom_min[1]:.3f},{atom_min[2]:.3f}) "
            f"atom_max=({atom_max[0]:.3f},{atom_max[1]:.3f},{atom_max[2]:.3f}) "
            f"obj_min=({obj_min[0]:.3f},{obj_min[1]:.3f},{obj_min[2]:.3f}) "
            f"obj_max=({obj_max[0]:.3f},{obj_max[1]:.3f},{obj_max[2]:.3f}) "
            f"point=({p[0]:.3f},{p[1]:.3f},{p[2]:.3f}) "
            f"mapped=({mapped_np[0]:.3f},{mapped_np[1]:.3f},{mapped_np[2]:.3f})"
        )
    return mapped


def _estimate_atom_to_object_scale(load_result: Any, target_obj: bpy.types.Object | None) -> float:
    if target_obj is None:
        return 1.0
    atom_array = _get_atom_array(load_result)
    if atom_array is None:
        return 1.0
    coords = coordinates_from_atom_array(atom_array)
    if coords is None or coords.size == 0:
        return 1.0
    finite_rows = np.isfinite(coords).all(axis=1)
    if not bool(np.any(finite_rows)):
        return 1.0
    finite_coords = coords[finite_rows]
    atom_min = finite_coords.min(axis=0)
    atom_max = finite_coords.max(axis=0)

    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_obj = target_obj.evaluated_get(depsgraph)
    except Exception:
        eval_obj = target_obj
    try:
        bbox_corners = list(getattr(eval_obj, "bound_box", []) or [])
    except Exception:
        bbox_corners = []
    if not bbox_corners:
        return 1.0
    try:
        world_corners = [eval_obj.matrix_world @ Vector(corner) for corner in bbox_corners]
    except Exception:
        return 1.0
    if not world_corners:
        return 1.0

    obj_min = np.array(
        [
            float(min(c.x for c in world_corners)),
            float(min(c.y for c in world_corners)),
            float(min(c.z for c in world_corners)),
        ],
        dtype=float,
    )
    obj_max = np.array(
        [
            float(max(c.x for c in world_corners)),
            float(max(c.y for c in world_corners)),
            float(max(c.z for c in world_corners)),
        ],
        dtype=float,
    )

    scales: list[float] = []
    for axis in range(3):
        atom_span = float(atom_max[axis] - atom_min[axis])
        obj_span = float(obj_max[axis] - obj_min[axis])
        if abs(atom_span) <= 1e-8:
            continue
        scales.append(obj_span / atom_span)
    if not scales:
        return 1.0
    scales_sorted = sorted(scales)
    return float(scales_sorted[len(scales_sorted) // 2])


def _ligand_cutout_box_params(
    *,
    ligand_anchor: Vector,
    radius: float,
    offset: float,
    interface_center: Vector | None,
    camera_axis: Vector | None = None,
    width_scale: float = 1.15,
    depth_scale: float = 0.45,
    height_scale: float = 1.15,
    prefer_camera: bool = False,
    flatten_z: bool = True,
) -> tuple[Vector, Vector, Vector, Vector, float, float, float] | None:
    axis_y = _ligand_cutout_axis(
        ligand_anchor,
        interface_center,
        camera_axis=camera_axis,
        prefer_camera=prefer_camera,
        flatten_z=flatten_z,
    )
    up_hint = Vector((0.0, 0.0, 1.0))
    if abs(axis_y.dot(up_hint)) > 0.95:
        up_hint = Vector((1.0, 0.0, 0.0))
    axis_x = up_hint.cross(axis_y)
    if axis_x.length < 1e-8:
        return None
    axis_x.normalize()
    axis_z = axis_y.cross(axis_x)
    if axis_z.length < 1e-8:
        return None
    axis_z.normalize()

    width_scale = max(0.05, float(width_scale))
    depth_scale = max(0.05, float(depth_scale))
    height_scale = max(0.05, float(height_scale))
    # Rectangular clip window around ligand; y-axis is the clip-depth direction.
    cut_half_width = max(0.01, float(radius) * width_scale)
    cut_half_depth = max(0.01, float(radius) * depth_scale)
    cut_half_height = max(0.01, float(radius) * height_scale)
    cut_center = ligand_anchor.copy()
    if abs(float(offset)) > 1e-6:
        cut_center += axis_y * float(offset)

    return (cut_center, axis_x, axis_y, axis_z, cut_half_width, cut_half_depth, cut_half_height)


def _orthonormal_frame_from_axis_y(axis_y: Vector, *, up_hint: Vector | None = None) -> tuple[Vector, Vector, Vector] | None:
    if axis_y.length <= 1e-8:
        return None
    y_axis = axis_y.normalized()
    up = up_hint.copy() if up_hint is not None else Vector((0.0, 0.0, 1.0))
    if up.length <= 1e-8:
        up = Vector((0.0, 0.0, 1.0))
    if abs(y_axis.dot(up.normalized())) > 0.95:
        up = Vector((1.0, 0.0, 0.0))
    x_axis = up.cross(y_axis)
    if x_axis.length <= 1e-8:
        return None
    x_axis.normalize()
    z_axis = y_axis.cross(x_axis)
    if z_axis.length <= 1e-8:
        return None
    z_axis.normalize()
    return (x_axis, y_axis, z_axis)


def _resolve_cutout_frame_axes(
    *,
    frame_mode: str,
    frame_axes_cfg: dict[str, Any] | None,
    ligand_anchor: Vector,
    interface_center: Vector | None,
    camera_axis: Vector | None,
    flatten_z: bool,
    camera_up: Vector | None = None,
    allow_preset_fallback: bool = True,
) -> tuple[Vector, Vector, Vector, str]:
    axis_mode = str(frame_mode or "preset").strip().lower()
    if axis_mode == "preset" and isinstance(frame_axes_cfg, dict):
        try:
            resolved = resolve_frame_axes(frame_axes_cfg)
            x_axis = Vector(resolved["x"])
            y_axis = Vector(resolved["y"])
            z_axis = Vector(resolved["z"])
            if x_axis.length > 1e-8 and y_axis.length > 1e-8 and z_axis.length > 1e-8:
                return (x_axis.normalized(), y_axis.normalized(), z_axis.normalized(), "preset")
        except Exception as exc:
            if not allow_preset_fallback:
                raise ValueError(f"Invalid ligand_cutout_frame_axes: {exc}") from exc
    elif axis_mode == "preset" and not allow_preset_fallback:
        raise ValueError("ligand_cutout_frame_axes are required when ligand_cutout_frame_mode='preset'.")

    if axis_mode not in {"interface", "camera"}:
        axis_mode = "interface"

    prefer_camera = axis_mode == "camera"
    if prefer_camera and camera_axis is not None:
        camera_axis = -camera_axis  # Carve toward the viewer, leaving the pocket behind.
    y_axis = _ligand_cutout_axis(
        ligand_anchor,
        interface_center,
        camera_axis=camera_axis,
        prefer_camera=prefer_camera,
        flatten_z=flatten_z,
    )
    frame = _orthonormal_frame_from_axis_y(y_axis, up_hint=camera_up if prefer_camera else None)
    if frame is not None:
        return (frame[0], frame[1], frame[2], axis_mode)

    fallback = _orthonormal_frame_from_axis_y(Vector((0.0, 1.0, 0.0)))
    if fallback is None:
        return (Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0)), Vector((0.0, 0.0, 1.0)), "world_fallback")
    return (fallback[0], fallback[1], fallback[2], "world_fallback")


def _vector_to_triplet(vec: Vector) -> tuple[float, float, float]:
    return (float(vec.x), float(vec.y), float(vec.z))


def _matrix_from_frame_axes(axis_x: Vector, axis_y: Vector, axis_z: Vector) -> Matrix:
    # Columns are local-frame basis vectors in world space.
    return Matrix((axis_x, axis_y, axis_z)).transposed()


def _delete_object_if_present(obj: bpy.types.Object | None) -> None:
    if obj is None:
        return
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:
        pass


def _create_plane_cutout_prism(
    *,
    cut_center: Vector,
    axis_x: Vector,
    axis_y: Vector,
    axis_z: Vector,
    radius_world: float,
    scene_radius: float,
    planes_enabled: dict[str, bool],
    plane_scales: dict[str, float],
) -> tuple[bpy.types.Object | None, dict[str, float]]:
    guard = max(0.5, max(float(radius_world), float(scene_radius)) * 8.0)
    front_extent = max(0.5, max(float(radius_world), float(scene_radius)) * 10.0)

    left = max(0.02, float(plane_scales.get("left", 1.0)) * float(radius_world))
    right = max(0.02, float(plane_scales.get("right", 1.0)) * float(radius_world))
    top = max(0.02, float(plane_scales.get("top", 1.0)) * float(radius_world))
    bottom = max(0.02, float(plane_scales.get("bottom", 1.0)) * float(radius_world))
    back = max(0.02, float(plane_scales.get("back", 1.0)) * float(radius_world))

    x_min = (-left) if bool(planes_enabled.get("left", True)) else (-guard)
    x_max = right if bool(planes_enabled.get("right", True)) else guard
    z_min = (-bottom) if bool(planes_enabled.get("bottom", True)) else (-guard)
    z_max = top if bool(planes_enabled.get("top", True)) else guard
    y_min = (-back) if bool(planes_enabled.get("back", True)) else (-guard)
    y_max = front_extent

    local_center = Vector(
        (
            float((x_min + x_max) * 0.5),
            float((y_min + y_max) * 0.5),
            float((z_min + z_max) * 0.5),
        )
    )
    half_extents = Vector(
        (
            max(0.02, float((x_max - x_min) * 0.5)),
            max(0.02, float((y_max - y_min) * 0.5)),
            max(0.02, float((z_max - z_min) * 0.5)),
        )
    )
    world_center = cut_center + (axis_x * local_center.x) + (axis_y * local_center.y) + (axis_z * local_center.z)

    try:
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=world_center)
    except Exception:
        return None, {}
    cutter = bpy.context.active_object
    if cutter is None or str(getattr(cutter, "type", "")).upper() != "MESH":
        return None, {}

    cutter.name = f"BridgePlanePrism_{uuid.uuid4().hex[:8]}"
    rotation = _matrix_from_frame_axes(axis_x, axis_y, axis_z).to_4x4()
    try:
        cutter.matrix_world = Matrix.Translation(world_center) @ rotation
    except Exception:
        pass
    try:
        cutter.scale = (half_extents.x, half_extents.y, half_extents.z)
    except Exception:
        pass
    try:
        cutter.hide_render = True
        cutter.hide_viewport = True
        cutter.display_type = "WIRE"
    except Exception:
        pass

    return cutter, {
        "x_min": float(x_min),
        "x_max": float(x_max),
        "y_min": float(y_min),
        "y_max": float(y_max),
        "z_min": float(z_min),
        "z_max": float(z_max),
        "front_extent": float(front_extent),
    }


def _create_halfspace_cutter_cube(
    *,
    center: Vector,
    remove_dir: Vector,
    tangent_extent: float,
    normal_extent: float,
) -> bpy.types.Object | None:
    if remove_dir.length <= 1e-8:
        return None
    try:
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=center)
    except Exception:
        return None
    cutter = bpy.context.active_object
    if cutter is None or str(getattr(cutter, "type", "")).upper() != "MESH":
        return None
    try:
        cutter.scale = (
            max(0.02, float(tangent_extent)),
            max(0.02, float(normal_extent) * 0.5),
            max(0.02, float(tangent_extent)),
        )
    except Exception:
        pass
    try:
        cutter.rotation_euler = remove_dir.normalized().to_track_quat("Y", "Z").to_euler()
    except Exception:
        pass
    cutter.name = f"BridgePlaneCut_{uuid.uuid4().hex[:8]}"
    try:
        cutter.hide_render = True
        cutter.hide_viewport = True
        cutter.display_type = "WIRE"
    except Exception:
        pass
    return cutter


def _join_objects_as_single_mesh(objects: list[bpy.types.Object]) -> bpy.types.Object | None:
    valid = [obj for obj in objects if obj is not None and str(getattr(obj, "type", "")).upper() == "MESH"]
    if not valid:
        return None
    if len(valid) == 1:
        return valid[0]
    try:
        bpy.ops.object.select_all(action="DESELECT")
    except Exception:
        pass
    for obj in valid:
        try:
            obj.select_set(True)
        except Exception:
            continue
    try:
        bpy.context.view_layer.objects.active = valid[0]
        bpy.ops.object.join()
    except Exception:
        return valid[0]
    merged = bpy.context.view_layer.objects.active
    if merged is None:
        return valid[0]
    merged.name = f"BridgePlaneCutUnion_{uuid.uuid4().hex[:8]}"
    return merged


def _add_boolean_difference_modifier(
    *,
    target_obj: bpy.types.Object,
    cutter_obj: bpy.types.Object,
) -> bpy.types.Modifier | None:
    if target_obj is None or cutter_obj is None:
        return None
    if str(getattr(target_obj, "type", "")).upper() != "MESH":
        return None
    if str(getattr(cutter_obj, "type", "")).upper() != "MESH":
        return None

    try:
        modifier = target_obj.modifiers.new(name=f"BridgePlaneCut_{uuid.uuid4().hex[:6]}", type="BOOLEAN")
        modifier.operation = "DIFFERENCE"
        modifier.object = cutter_obj
        if hasattr(modifier, "material_mode"):
            modifier.material_mode = "TRANSFER"
        if hasattr(modifier, "solver"):
            modifier.solver = "EXACT"
        if hasattr(modifier, "use_hole_tolerant"):
            try:
                modifier.use_hole_tolerant = True
            except Exception:
                pass
    except Exception:
        return None
    return modifier


def _apply_boolean_difference_and_apply(
    *,
    target_obj: bpy.types.Object,
    cutter_obj: bpy.types.Object,
) -> bool:
    modifier = _add_boolean_difference_modifier(target_obj=target_obj, cutter_obj=cutter_obj)
    if modifier is None:
        return False

    try:
        bpy.ops.object.select_all(action="DESELECT")
    except Exception:
        pass
    try:
        target_obj.select_set(True)
        bpy.context.view_layer.objects.active = target_obj
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    try:
        bpy.ops.object.modifier_apply(modifier=str(modifier.name))
    except Exception:
        return False
    return True


def _collect_boundary_edge_loops(mesh_obj: bpy.types.Object) -> list[list[bmesh.types.BMEdge]]:
    mesh_data = getattr(mesh_obj, "data", None)
    if mesh_data is None:
        return []
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh_data)
        bm.edges.ensure_lookup_table()
        boundary_edges = [edge for edge in bm.edges if bool(edge.is_boundary)]
        if not boundary_edges:
            return []
        remaining = set(boundary_edges)
        loops: list[list[bmesh.types.BMEdge]] = []
        while remaining:
            seed = remaining.pop()
            queue = [seed]
            component = [seed]
            while queue:
                edge = queue.pop()
                for vert in edge.verts:
                    for linked in vert.link_edges:
                        if linked in remaining and bool(linked.is_boundary):
                            remaining.remove(linked)
                            queue.append(linked)
                            component.append(linked)
            loops.append(component)
        return loops
    finally:
        bm.free()


def _ensure_material_slot_index(mesh_obj: bpy.types.Object, material: bpy.types.Material) -> int:
    data = getattr(mesh_obj, "data", None)
    if data is None:
        return 0
    materials = getattr(data, "materials", None)
    if materials is None:
        return 0
    for idx, mat in enumerate(materials):
        if mat == material:
            return int(idx)
    materials.append(material)
    return max(0, len(materials) - 1)


def _apply_boundary_fill_seal(
    *,
    target_obj: bpy.types.Object,
    ligand_anchor_world: Vector,
    axis_y_world: Vector,
    radius_world: float,
    seal_depth_scale: float,
    seal_region_scale: float,
    seal_material: bpy.types.Material,
) -> dict[str, Any]:
    info: dict[str, Any] = {
        "boundary_loops_total": 0,
        "boundary_loops_near_ligand": 0,
        "filled_faces": 0,
    }
    if target_obj is None or str(getattr(target_obj, "type", "")).upper() != "MESH":
        return info
    mesh_data = getattr(target_obj, "data", None)
    if mesh_data is None:
        return info

    material_index = _ensure_material_slot_index(target_obj, seal_material)
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh_data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        boundary_edges = [edge for edge in bm.edges if bool(edge.is_boundary)]
        if not boundary_edges:
            return info

        remaining = set(boundary_edges)
        components: list[list[bmesh.types.BMEdge]] = []
        while remaining:
            seed = remaining.pop()
            queue = [seed]
            component = [seed]
            while queue:
                edge = queue.pop()
                for vert in edge.verts:
                    for linked in vert.link_edges:
                        if linked in remaining and bool(linked.is_boundary):
                            remaining.remove(linked)
                            queue.append(linked)
                            component.append(linked)
            components.append(component)
        info["boundary_loops_total"] = len(components)

        region_radius = max(0.01, float(radius_world) * max(0.1, float(seal_region_scale)))
        selected_edges: list[bmesh.types.BMEdge] = []
        for component in components:
            verts = {vert for edge in component for vert in edge.verts}
            if not verts:
                continue
            center_local = Vector((0.0, 0.0, 0.0))
            for vert in verts:
                center_local += vert.co
            center_local /= max(1, len(verts))
            center_world = target_obj.matrix_world @ center_local
            if (center_world - ligand_anchor_world).length <= region_radius:
                selected_edges.extend(component)
                info["boundary_loops_near_ligand"] = int(info["boundary_loops_near_ligand"]) + 1
        if not selected_edges:
            return info

        fill_res = bmesh.ops.holes_fill(bm, edges=selected_edges, sides=0)
        new_faces = [face for face in fill_res.get("faces", []) if isinstance(face, bmesh.types.BMFace)]
        for face in new_faces:
            face.material_index = int(material_index)
        info["filled_faces"] = len(new_faces)
        if new_faces and float(seal_depth_scale) > 0:
            extrude_res = bmesh.ops.extrude_face_region(bm, geom=new_faces)
            extruded_faces = [elem for elem in extrude_res.get("geom", []) if isinstance(elem, bmesh.types.BMFace)]
            extruded_verts = [elem for elem in extrude_res.get("geom", []) if isinstance(elem, bmesh.types.BMVert)]
            axis_local = target_obj.matrix_world.inverted().to_3x3() @ axis_y_world
            if axis_local.length > 1e-8:
                axis_local.normalize()
                seal_depth = max(0.0005, float(radius_world) * float(seal_depth_scale))
                bmesh.ops.translate(bm, verts=extruded_verts, vec=(-axis_local * seal_depth))
            for face in extruded_faces:
                face.material_index = int(material_index)

        bm.normal_update()
        bm.to_mesh(mesh_data)
        mesh_data.update()
        return info
    finally:
        bm.free()


def _ensure_cutout_cap_material(
    *,
    cap_material: bpy.types.Material | None = None,
    cap_material_cfg: dict[str, Any] | None = None,
) -> bpy.types.Material:
    if cap_material is not None:
        return cap_material
    cfg_dict = cap_material_cfg if isinstance(cap_material_cfg, dict) else {}
    base_hex = str(cfg_dict.get("base_color", "#AAB6C2"))
    roughness = float(cfg_dict.get("roughness", 0.62))
    specular = float(cfg_dict.get("specular", 0.11))
    name = f"BridgeCutoutCap_{base_hex.replace('#', '').upper()}"
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nt = material.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (280, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (40, 0)
    _set_bsdf_input(bsdf, ("Base Color",), _hex_to_rgba(base_hex, alpha=1.0))
    _set_bsdf_input(bsdf, ("Roughness",), roughness)
    _set_bsdf_input(bsdf, ("Specular IOR Level", "Specular"), specular)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    if hasattr(material, "blend_method"):
        material.blend_method = "OPAQUE"
    if hasattr(material, "shadow_method"):
        material.shadow_method = "OPAQUE"
    return material


def _add_trimmed_cut_panel(
    *,
    target_obj: bpy.types.Object,
    center: Vector,
    axis_y: Vector,
    half_width: float,
    half_height: float,
    half_depth: float,
    cap_material: bpy.types.Material | None = None,
    cap_material_cfg: dict[str, Any] | None = None,
) -> bool:
    try:
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=center)
    except Exception:
        return False
    panel = bpy.context.active_object
    if panel is None or str(getattr(panel, "type", "")).upper() != "MESH":
        return False
    panel.name = f"BridgeCutoutCap_{uuid.uuid4().hex[:8]}"
    try:
        panel.scale = (
            max(0.006, float(half_width) * 1.06),
            max(0.0015, min(float(half_depth) * 0.035, 0.012)),
            max(0.006, float(half_height) * 1.06),
        )
    except Exception:
        pass
    if axis_y.length > 1e-8:
        try:
            panel.rotation_euler = axis_y.normalized().to_track_quat("Y", "Z").to_euler()
        except Exception:
            pass
    try:
        mat = _ensure_cutout_cap_material(cap_material=cap_material, cap_material_cfg=cap_material_cfg)
        data = getattr(panel, "data", None)
        materials = getattr(data, "materials", None) if data is not None else None
        if materials is not None:
            materials.clear()
            materials.append(mat)
    except Exception:
        pass
    try:
        trim_modifier = panel.modifiers.new(
            name=f"BridgeCutoutCapTrim_{uuid.uuid4().hex[:6]}",
            type="BOOLEAN",
        )
        trim_modifier.operation = "INTERSECT"
        trim_modifier.object = target_obj
        if hasattr(trim_modifier, "solver"):
            trim_modifier.solver = "EXACT"
        if hasattr(trim_modifier, "use_hole_tolerant"):
            try:
                trim_modifier.use_hole_tolerant = True
            except Exception:
                pass
        try:
            bpy.ops.object.select_all(action="DESELECT")
        except Exception:
            pass
        try:
            panel.select_set(True)
            bpy.context.view_layer.objects.active = panel
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass
        try:
            bpy.ops.object.modifier_apply(modifier=str(trim_modifier.name))
        except Exception:
            pass
    except Exception:
        pass
    return True

def _selection_indices_with_ligand_box_cutout(
    *,
    load_result: Any,
    base_indices: list[int] | None,
    ligand_anchor: Vector,
    radius: float,
    offset: float,
    interface_center: Vector | None,
    camera_axis: Vector | None = None,
    width_scale: float = 1.15,
    depth_scale: float = 0.45,
    height_scale: float = 1.15,
    one_sided: bool = False,
    prefer_camera: bool = False,
    flatten_z: bool = True,
) -> list[int] | None:
    atom_array = _get_atom_array(load_result)
    if atom_array is None:
        return base_indices

    count = _atom_array_count(atom_array)
    if count <= 0:
        return base_indices

    coords = coordinates_from_atom_array(atom_array)
    if coords is None or coords.shape[0] != count:
        return base_indices

    box = _ligand_cutout_box_params(
        ligand_anchor=ligand_anchor,
        radius=radius,
        offset=offset,
        interface_center=interface_center,
        camera_axis=camera_axis,
        width_scale=width_scale,
        depth_scale=depth_scale,
        height_scale=height_scale,
        prefer_camera=prefer_camera,
        flatten_z=flatten_z,
    )
    if box is None:
        return base_indices
    cut_center, axis_x, axis_y, axis_z, cut_half_width, cut_half_depth, cut_half_height = box

    center_np = np.array([cut_center.x, cut_center.y, cut_center.z], dtype=float)
    axis_x_np = np.array([axis_x.x, axis_x.y, axis_x.z], dtype=float)
    axis_y_np = np.array([axis_y.x, axis_y.y, axis_y.z], dtype=float)
    axis_z_np = np.array([axis_z.x, axis_z.y, axis_z.z], dtype=float)
    deltas = np.asarray(coords, dtype=float) - center_np[None, :]
    local_x = np.einsum("ij,j->i", deltas, axis_x_np, optimize=True)
    local_y = np.einsum("ij,j->i", deltas, axis_y_np, optimize=True)
    local_z = np.einsum("ij,j->i", deltas, axis_z_np, optimize=True)
    window_mask = (
        (np.abs(local_x) <= cut_half_width)
        & (np.abs(local_z) <= cut_half_height)
    )
    # Full prism removal gives a stronger rectangular cut than a shallow cavity.
    clip_depth = max(float(radius) * 6.0, cut_half_depth * 10.0)
    if one_sided:
        side_sign = 1.0 if float(offset) >= 0.0 else -1.0
        if side_sign >= 0.0:
            depth_mask = (local_y >= -1e-6) & (local_y <= clip_depth)
        else:
            depth_mask = (local_y <= 1e-6) & (local_y >= (-clip_depth))
    else:
        depth_mask = np.abs(local_y) <= clip_depth
    box_mask = window_mask & depth_mask

    if base_indices is None:
        base_mask = np.ones(count, dtype=bool)
    else:
        base_mask = np.zeros(count, dtype=bool)
        for idx in base_indices:
            if 0 <= int(idx) < count:
                base_mask[int(idx)] = True

    selected = np.where(base_mask & (~box_mask))[0].astype(int).tolist()
    return selected





def _apply_surface_ligand_cutout_planes(
    *,
    load_result: Any,
    target_obj: bpy.types.Object | None,
    cap_material: bpy.types.Material | None = None,
    cap_material_cfg: dict[str, Any] | None = None,
    anchor_load_result: Any | None = None,
    anchor_object: bpy.types.Object | None = None,
    ligand_anchor: Vector,
    radius: float,
    offset: float,
    mirror: bool,
    interface_center: Vector | None,
    camera_axis: Vector | None = None,
    camera_up: Vector | None = None,
    width_scale: float = 1.15,
    depth_scale: float = 0.45,
    height_scale: float = 1.15,
    flatten_z: bool = True,
    frame_mode: str = "preset",
    frame_axes_cfg: dict[str, Any] | None = None,
    frame_roll_deg: float = 0.0,
    planes_enabled_cfg: dict[str, Any] | None = None,
    plane_scales_cfg: dict[str, Any] | None = None,
    seal_mode: str = "boundary_fill",
    seal_depth_scale: float = 0.3,
    seal_region_scale: float = 2.0,
    runtime_info: dict[str, Any] | None = None,
) -> bool:
    if runtime_info is not None:
        runtime_info.update(
            {
                "ligand_cutout_mode": "planes",
                "ligand_cutout_frame_mode": None,
                "ligand_cutout_frame_axes": None,
                "ligand_cutout_planes_enabled": {},
                "ligand_cutout_plane_scales": {},
                "ligand_cutout_seal_mode": str(seal_mode or "boundary_fill").strip().lower(),
                "ligand_cutout_seal_depth_scale": float(seal_depth_scale),
                "ligand_cutout_seal_region_scale": float(seal_region_scale),
                "ligand_cutout_boundary_loops_total": 0,
                "ligand_cutout_boundary_loops_near_ligand": 0,
                "ligand_cutout_boundary_filled_faces": 0,
                "applied": False,
            }
        )
    if target_obj is None or str(getattr(target_obj, "type", "")).upper() != "MESH":
        return False
    if float(radius) <= 1e-6:
        return False

    anchor_result = anchor_load_result if anchor_load_result is not None else load_result
    anchor_target = anchor_object if anchor_object is not None else target_obj
    ligand_anchor_world = _map_atom_point_to_object_bounds(
        anchor_result,
        anchor_target,
        ligand_anchor,
    )
    scale = _estimate_atom_to_object_scale(anchor_result, anchor_target)
    radius_world = max(0.02, float(radius) * max(scale, 1e-6))
    offset_world = float(offset) * max(scale, 1e-6)

    frame_mode_norm = str(frame_mode or "preset").strip().lower()
    strict_preset = frame_mode_norm == "preset"
    axis_x, axis_y, axis_z, resolved_frame_mode = _resolve_cutout_frame_axes(
        frame_mode=frame_mode_norm,
        frame_axes_cfg=frame_axes_cfg,
        ligand_anchor=ligand_anchor_world,
        interface_center=interface_center,
        camera_axis=camera_axis,
        camera_up=camera_up,
        flatten_z=flatten_z,
        allow_preset_fallback=not strict_preset,
    )
    if frame_roll_deg:
        rotation = Matrix.Rotation(math.radians(frame_roll_deg), 3, axis_y)
        axis_x, axis_z = rotation @ axis_x, rotation @ axis_z
    cut_center = ligand_anchor_world.copy()
    if abs(offset_world) > 1e-6:
        cut_center += axis_y * float(offset_world)

    planes_enabled = resolve_plane_enabled(planes_enabled_cfg)
    plane_scales = resolve_plane_scales(
        plane_scales_cfg,
        width_scale=float(width_scale),
        depth_scale=float(depth_scale),
        height_scale=float(height_scale),
    )
    if runtime_info is not None:
        runtime_info["ligand_cutout_frame_mode"] = str(resolved_frame_mode)
        runtime_info["ligand_cutout_frame_axes"] = {
            "x": _vector_to_triplet(axis_x),
            "y": _vector_to_triplet(axis_y),
            "z": _vector_to_triplet(axis_z),
        }
        runtime_info["ligand_cutout_planes_enabled"] = {key: bool(planes_enabled.get(key, False)) for key in DEFAULT_PLANE_ENABLED}
        runtime_info["ligand_cutout_plane_scales"] = {key: float(plane_scales.get(key, 1.0)) for key in DEFAULT_PLANE_ENABLED}
    if not any(bool(planes_enabled.get(name, False)) for name in DEFAULT_PLANE_ENABLED):
        return False

    _, scene_radius = _scene_bounds([target_obj])
    cut_centers = [cut_center]
    if mirror and abs(float(offset_world)) > 1e-6:
        cut_centers.append(
            Vector(
                (
                    (ligand_anchor_world.x * 2.0) - cut_center.x,
                    (ligand_anchor_world.y * 2.0) - cut_center.y,
                    (ligand_anchor_world.z * 2.0) - cut_center.z,
                )
            )
        )

    cutters: list[bpy.types.Object] = []
    plane_bounds: dict[str, float] = {}
    for center in cut_centers:
        cutter, bounds = _create_plane_cutout_prism(
            cut_center=center,
            axis_x=axis_x,
            axis_y=axis_y,
            axis_z=axis_z,
            radius_world=radius_world,
            scene_radius=scene_radius,
            planes_enabled=planes_enabled,
            plane_scales=plane_scales,
        )
        if cutter is not None:
            cutters.append(cutter)
            if not plane_bounds:
                plane_bounds = dict(bounds)
    if not cutters:
        return False

    merged_cutter = _join_objects_as_single_mesh(cutters)
    if merged_cutter is None:
        for cutter in cutters:
            _delete_object_if_present(cutter)
        return False
    # New Boolean cut faces must inherit the surface pigment rather than
    # Blender's unassigned grey material.
    cut_material = _ensure_cutout_cap_material(cap_material=cap_material, cap_material_cfg=cap_material_cfg)
    merged_cutter.data.materials.clear()
    merged_cutter.data.materials.append(cut_material)
    has_geometry_nodes = False
    try:
        has_geometry_nodes = any(str(getattr(mod, "type", "")).upper() == "NODES" for mod in list(target_obj.modifiers))
    except Exception:
        has_geometry_nodes = False
    if has_geometry_nodes:
        modifier = _add_boolean_difference_modifier(target_obj=target_obj, cutter_obj=merged_cutter)
        applied = modifier is not None
    else:
        applied = _apply_boolean_difference_and_apply(target_obj=target_obj, cutter_obj=merged_cutter)
        _delete_object_if_present(merged_cutter)
    if not applied:
        _delete_object_if_present(merged_cutter)
        return False

    seal_mode_norm = str(seal_mode or "boundary_fill").strip().lower()
    if seal_mode_norm not in {"boundary_fill", "trimmed_panel", "none"}:
        seal_mode_norm = "boundary_fill"
    if has_geometry_nodes and seal_mode_norm == "boundary_fill":
        # Applying boundary-fill requires baking the procedural surface mesh, which
        # collapses Geometry Nodes styles. Keep procedural cut non-destructive.
        seal_mode_norm = "none"
    seal_info: dict[str, Any] = {}
    if seal_mode_norm == "boundary_fill":
        seal_material = _ensure_cutout_cap_material(cap_material=cap_material, cap_material_cfg=cap_material_cfg)
        seal_info = _apply_boundary_fill_seal(
            target_obj=target_obj,
            ligand_anchor_world=ligand_anchor_world,
            axis_y_world=axis_y,
            radius_world=radius_world,
            seal_depth_scale=max(0.01, float(seal_depth_scale)),
            seal_region_scale=max(0.1, float(seal_region_scale)),
            seal_material=seal_material,
        )
    elif seal_mode_norm == "trimmed_panel":
        back_depth = max(0.02, float(plane_scales.get("back", 1.0)) * radius_world)
        panel_center = cut_center - (axis_y * back_depth) if bool(planes_enabled.get("back", True)) else cut_center.copy()
        _add_trimmed_cut_panel(
            target_obj=target_obj,
            center=panel_center,
            axis_y=axis_y,
            half_width=max(
                max(0.02, float(plane_scales.get("left", 1.0)) * radius_world),
                max(0.02, float(plane_scales.get("right", 1.0)) * radius_world),
            ),
            half_height=max(
                max(0.02, float(plane_scales.get("top", 1.0)) * radius_world),
                max(0.02, float(plane_scales.get("bottom", 1.0)) * radius_world),
            ),
            half_depth=back_depth,
            cap_material=cap_material,
            cap_material_cfg=cap_material_cfg,
        )

    if runtime_info is not None:
        runtime_info["applied"] = True
        runtime_info["ligand_cutout_seal_mode"] = seal_mode_norm
        runtime_info["ligand_cutout_boundary_loops_total"] = int(seal_info.get("boundary_loops_total", 0))
        runtime_info["ligand_cutout_boundary_loops_near_ligand"] = int(seal_info.get("boundary_loops_near_ligand", 0))
        runtime_info["ligand_cutout_boundary_filled_faces"] = int(seal_info.get("filled_faces", 0))
        if plane_bounds:
            runtime_info["ligand_cutout_plane_bounds_local"] = plane_bounds
    return True


def _apply_surface_ligand_cutout(
    *,
    load_result: Any,
    target_obj: bpy.types.Object | None,
    cap_material: bpy.types.Material | None = None,
    cap_material_cfg: dict[str, Any] | None = None,
    anchor_load_result: Any | None = None,
    anchor_object: bpy.types.Object | None = None,
    ligand_anchor: Vector,
    radius: float,
    offset: float,
    mirror: bool,
    interface_center: Vector | None,
    camera_axis: Vector | None = None,
    camera_up: Vector | None = None,
    width_scale: float = 1.15,
    depth_scale: float = 0.45,
    height_scale: float = 1.15,
    one_sided: bool = True,
    prefer_camera: bool = False,
    flatten_z: bool = True,
    one_sided_shift_scale: float = 1.0,
    cap_mode: str = "panel",
    cutout_mode: str = "planes",
    frame_mode: str = "preset",
    frame_axes_cfg: dict[str, Any] | None = None,
    frame_roll_deg: float = 0.0,
    planes_enabled_cfg: dict[str, Any] | None = None,
    plane_scales_cfg: dict[str, Any] | None = None,
    seal_mode: str = "boundary_fill",
    seal_depth_scale: float = 0.3,
    seal_region_scale: float = 2.0,
    runtime_info: dict[str, Any] | None = None,
) -> bool:
    mode = str(cutout_mode or "planes").strip().lower()
    return _apply_surface_ligand_cutout_planes(
        load_result=load_result,
        target_obj=target_obj,
        cap_material=cap_material,
        cap_material_cfg=cap_material_cfg,
        anchor_load_result=anchor_load_result,
        anchor_object=anchor_object,
        ligand_anchor=ligand_anchor,
        radius=radius,
        offset=offset,
        mirror=mirror,
        interface_center=interface_center,
        camera_axis=camera_axis,
        camera_up=camera_up,
        width_scale=width_scale,
        depth_scale=depth_scale,
        height_scale=height_scale,
        flatten_z=flatten_z,
        frame_mode=frame_mode,
        frame_axes_cfg=frame_axes_cfg,
        frame_roll_deg=frame_roll_deg,
        planes_enabled_cfg=planes_enabled_cfg,
        plane_scales_cfg=plane_scales_cfg,
        seal_mode=seal_mode,
        seal_depth_scale=seal_depth_scale,
        seal_region_scale=seal_region_scale,
        runtime_info=runtime_info,
    )


def _apply_surface_wireframe(obj: bpy.types.Object, width_angstrom: float,
                             spacing_angstrom: float = 1.0,
                             camera_forward: Vector | None = None) -> None:
    """Trace the native surface edges on an isolated object, retaining its material."""
    # Uniform quad cells follow the curved volume rather than exposing the
    # triangulation as an irregular net. Only this isolated overlay is remeshed.
    remesh = obj.modifiers.new("Surface grid cells", "REMESH")
    remesh.mode = "VOXEL"
    remesh.voxel_size = spacing_angstrom * .01
    remesh.use_smooth_shade = True
    smooth = obj.modifiers.new("Smooth grid on surface", "SMOOTH")
    smooth.factor = 1.0
    smooth.iterations = 3
    if camera_forward is not None:
        # Glass must show one readable grid, not overlapping front/back nets.
        # Bake only the overlay's camera-facing cells, as with authored cuts.
        bpy.context.view_layer.update()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = bpy.data.meshes.new_from_object(evaluated)
        obj.modifiers.clear()
        obj.data = mesh
        bm = bmesh.new()
        bm.from_mesh(mesh)
        normal_matrix = obj.matrix_world.to_3x3().inverted().transposed()
        away = [face for face in bm.faces if (normal_matrix @ face.normal).dot(camera_forward) >= 0]
        bmesh.ops.delete(bm, geom=away, context='FACES')
        bm.to_mesh(mesh)
        bm.free()
    modifier = obj.modifiers.new("Fine molecular surface mesh", "WIREFRAME")
    modifier.thickness = width_angstrom * .01
    modifier.offset = 1.0
    modifier.use_replace = True
    modifier.use_even_offset = True
    modifier.use_boundary = True


def _override_camera_rotation(camera: bpy.types.Object, camera_cfg: dict[str, Any]) -> Matrix:
    """Resolve orientation once for both camera-relative geometry and the camera."""
    direction_source = str(camera_cfg.get("direction_source", "preset")).strip().lower()

    up_hint: Vector | None = None
    if direction_source == "pymol":
        quat = camera.matrix_world.to_quaternion()
        direction = quat @ Vector((0.0, 0.0, -1.0))
        up_hint = quat @ Vector((0.0, 1.0, 0.0))

        yaw_offset_deg = float(camera_cfg.get("yaw_offset_deg", 0.0))
        pitch_offset_deg = float(camera_cfg.get("pitch_offset_deg", 0.0))
        if up_hint.length > 1e-8 and abs(yaw_offset_deg) > 1e-6:
            direction = (Matrix.Rotation(math.radians(yaw_offset_deg), 3, up_hint.normalized()) @ direction).normalized()
        right_axis = direction.cross(up_hint) if up_hint.length > 1e-8 else Vector((0.0, 0.0, 0.0))
        if right_axis.length > 1e-8 and abs(pitch_offset_deg) > 1e-6:
            right_axis.normalize()
            rot = Matrix.Rotation(math.radians(pitch_offset_deg), 3, right_axis)
            direction = (rot @ direction).normalized()
            up_hint = (rot @ up_hint).normalized()
    else:
        yaw_deg = float(camera_cfg.get("yaw_deg", -88.0))
        pitch_deg = float(camera_cfg.get("pitch_deg", 8.0))
        yaw = math.radians(yaw_deg)
        pitch = math.radians(pitch_deg)
        direction = Vector((math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch)))

    if direction.length < 1e-8:
        direction = Vector((0.0, -1.0, 0.0))
    direction.normalize()
    rotation = direction.to_track_quat("-Z", "Y").to_matrix()
    if up_hint is not None and up_hint.cross(-direction).length > 1e-8:
        right = up_hint.cross(-direction).normalized()
        up = (-direction).cross(right).normalized()
        rotation = Matrix((right, up, -direction)).transposed()
    roll = float(camera_cfg.get("roll_deg", 0.0))
    if roll:
        rotation = rotation @ Matrix.Rotation(math.radians(roll), 3, "Z")
    return rotation


def _apply_override_camera(
    scene: bpy.types.Scene,
    camera: bpy.types.Object,
    camera_cfg: dict[str, Any],
    objects: list[bpy.types.Object],
    style_objects_by_name: dict[str, list[bpy.types.Object]] | None = None,
    style_centers_by_name: dict[str, Vector] | None = None,
) -> None:
    center, radius = _scene_bounds(objects)
    focus_style = str(camera_cfg.get("focus_style", "")).strip()
    if focus_style and style_objects_by_name:
        focus_objects = [
            obj for obj in style_objects_by_name.get(focus_style, []) if _object_has_renderable_geometry(obj)
        ]
        if focus_objects:
            center, _ = _scene_bounds(focus_objects)
    if focus_style and style_centers_by_name and focus_style in style_centers_by_name:
        center = style_centers_by_name[focus_style]
    distance = max(0.05, radius * float(camera_cfg.get("distance_scale", 3.1)))
    rotation = _override_camera_rotation(camera, camera_cfg)
    direction = rotation @ Vector((0, 0, -1))
    camera.matrix_world = Matrix.Translation(center - direction * distance) @ rotation.to_4x4()
    data = camera.data
    if "projection" in camera_cfg:
        data.type = camera_cfg["projection"]
    if isinstance(camera_cfg.get("lens_mm"), (int, float)):
        data.lens = float(camera_cfg["lens_mm"])
    if isinstance(camera_cfg.get("shift_x"), (int, float)):
        data.shift_x = float(camera_cfg["shift_x"])
    if isinstance(camera_cfg.get("shift_y"), (int, float)):
        data.shift_y = float(camera_cfg["shift_y"])
    if camera_cfg.get("fit", False):
        bpy.context.view_layer.update()
        inv = camera.matrix_world.inverted()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        projected = []
        for obj in {obj.as_pointer(): obj for obj in objects}.values():
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            try:
                if mesh and mesh.vertices:
                    coords = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
                    mesh.vertices.foreach_get("co", coords)
                    matrix = np.asarray(inv @ obj.matrix_world)
                    projected.append(np.einsum("ij,kj->ki", matrix[:3, :3], coords.reshape(-1, 3)) + matrix[:3, 3])
            finally:
                evaluated.to_mesh_clear()
        if projected:
            points = np.concatenate(projected)
            aspect = scene.render.resolution_x / scene.render.resolution_y
            margin = float(camera_cfg.get("fit_margin", 1.08))
            data.sensor_fit = "HORIZONTAL"
            limits = (.5 + data.shift_x, .5 - data.shift_x,
                      .5 / aspect + data.shift_y, .5 / aspect - data.shift_y)
            if min(limits) <= 0:
                raise ValueError("camera.fit requires shifts that keep the focus inside the frame")
            extent = np.maximum.reduce((points[:, 0] / limits[0], -points[:, 0] / limits[1],
                                        points[:, 1] / limits[2], -points[:, 1] / limits[3])) * margin
            if data.type == "ORTHO":
                data.ortho_scale = float(np.max(extent))
            else:
                tangent = math.tan(data.angle_x * .5)
                required = extent / (2 * tangent) + points[:, 2]
                delta = float(np.max(required))
                camera.location += camera.matrix_world.to_quaternion() @ Vector((0, 0, delta))
                distance += delta
    if "ortho_scale_angstrom" in camera_cfg:
        if data.type != "ORTHO":
            raise ValueError("camera.ortho_scale_angstrom requires camera.projection=ORTHO or an orthographic input view")
        data.ortho_scale = float(camera_cfg["ortho_scale_angstrom"]) * .01
        data.sensor_fit = "HORIZONTAL"
    if isinstance(camera_cfg.get("clip_start"), (int, float)):
        data.clip_start = max(1e-6, float(camera_cfg["clip_start"]))
    else:
        data.clip_start = max(1e-4, distance - radius * 3.0)
    if isinstance(camera_cfg.get("clip_end"), (int, float)):
        data.clip_end = max(data.clip_start + 1e-3, float(camera_cfg["clip_end"]))
    else:
        data.clip_end = max(data.clip_start + 1.0, distance + radius * 6.0)


def _configure_depth_of_field(camera: bpy.types.Object, camera_cfg: dict[str, Any]) -> None:
    """Opt-in photographic focus; MN molecular geometry uses 0.01 BU per angstrom.

    Focus distance is measured along the optical axis from the final camera,
    independently of the view-transfer world_scale and framing target.
    """
    cfg = camera_cfg.get("depth_of_field")
    camera.data.dof.use_dof = cfg is not None
    if cfg is None:
        return
    if camera.data.type != "PERSP":
        raise ValueError("camera.depth_of_field requires a perspective camera")
    camera.data.dof.focus_object = None
    camera.data.dof.focus_distance = float(cfg["focus_distance_angstrom"]) * 0.01
    camera.data.dof.aperture_fstop = float(cfg.get("f_stop", 2.8))


def _style_result_is_empty(style_result: Any) -> bool:
    if style_result is None:
        return True
    selection_indices = getattr(style_result, "_selection_indices", None)
    style_object = getattr(style_result, "_object", None)
    if isinstance(selection_indices, list) and not selection_indices and style_object is None:
        return True
    return False


def _add_style_with_tracking(
    *,
    load_result: Any,
    style_cfg: dict[str, Any],
    selection: Any,
    material: bpy.types.Material,
    base_object: bpy.types.Object | None,
) -> tuple[Any, list[str], bool]:
    tree = getattr(load_result, "tree", None)
    before_tree_nodes = -1
    if tree is not None:
        try:
            before_tree_nodes = len(getattr(tree, "nodes", []))
        except Exception:
            before_tree_nodes = -1
    before_modifiers = _object_modifier_names(base_object)
    style_result = _add_style(load_result, style_cfg, selection, material=material)
    after_modifiers = _object_modifier_names(base_object)
    added = [name for name in after_modifiers if name not in before_modifiers]
    tree_changed = False
    if before_tree_nodes >= 0:
        try:
            after_tree_nodes = len(getattr(tree, "nodes", []))
            tree_changed = after_tree_nodes > before_tree_nodes
        except Exception:
            tree_changed = False
    return style_result, added, tree_changed


def _resolve_blend_output_path(style_doc: dict[str, Any], output_png: Path) -> Path | None:
    output_cfg = style_doc.get("output")
    if not isinstance(output_cfg, dict):
        return None
    if not bool(output_cfg.get("save_blend", False)):
        return None

    filename = str(output_cfg.get("blend_filename", "") or "").strip()
    if not filename:
        return output_png.with_suffix(".blend")
    configured = Path(filename).expanduser()
    if configured.is_absolute():
        return configured
    return output_png.parent / configured


def _apply_pymol_ss_hints(load_result: Any, view_doc: dict[str, Any]) -> int:
    hints = view_doc.get("ss_hints")
    if not isinstance(hints, dict):
        return 0
    by_chain_resi = hints.get("by_chain_resi")
    if not isinstance(by_chain_resi, dict) or not by_chain_resi:
        return 0

    atom_array = _get_atom_array(load_result)
    if atom_array is None:
        return 0

    try:
        count = int(atom_array.array_length()) if hasattr(atom_array, "array_length") else len(atom_array)
    except Exception:
        return 0
    if count <= 0:
        return 0

    chain_upper_getter = getattr(load_result, "_chain_upper", None)
    bridge_override = getattr(load_result, "_bridge_chain_override_upper", None)
    if isinstance(bridge_override, np.ndarray) and bridge_override.shape[0] == count:
        chain_upper = np.asarray(bridge_override, dtype=object).astype(str)
    elif callable(chain_upper_getter):
        try:
            chain_upper = np.asarray(chain_upper_getter(count), dtype=object).astype(str)
        except Exception:
            chain_upper = _array_as_upper(getattr(atom_array, "chain_id", np.array([""] * count, dtype=object)), count)
    else:
        chain_upper = _array_as_upper(getattr(atom_array, "chain_id", np.array([""] * count, dtype=object)), count)

    try:
        res_ids = np.asarray(getattr(atom_array, "res_id", np.full(count, -1, dtype=int))).astype(int)
    except Exception:
        return 0

    amino_mask = _normalized_amino_mask(atom_array, count)
    try:
        existing_raw = np.asarray(getattr(atom_array, "sec_struct", np.zeros(count, dtype=int)), dtype=object)
    except Exception:
        existing_raw = np.zeros(count, dtype=int)
    sec_struct = _normalize_sec_struct_values(existing_raw, amino_mask)

    applied = 0
    for idx in range(count):
        if not bool(amino_mask[idx]):
            continue
        chain_id = _strip_wrapping_quotes(str(chain_upper[idx])).strip().upper()
        if not chain_id or chain_id in {".", "?"}:
            continue
        actual_chain = str(atom_array.chain_id[idx])
        insertion = str(getattr(atom_array, "ins_code", [""] * count)[idx]).strip()
        key = f"{actual_chain}:{int(res_ids[idx])}{insertion}"
        ss_code = str(by_chain_resi.get(key, "")).strip().upper()
        if ss_code == "H":
            sec_struct[idx] = 1
            applied += 1
        elif ss_code == "S":
            sec_struct[idx] = 2
            applied += 1
        elif ss_code == "L":
            sec_struct[idx] = 3
            applied += 1

    if applied <= 0:
        return 0
    try:
        categories = list(atom_array.get_annotation_categories()) if hasattr(atom_array, "get_annotation_categories") else []
        if "sec_struct" in categories:
            try:
                atom_array.del_annotation("sec_struct")
            except Exception:
                pass
        atom_array.set_annotation("sec_struct", sec_struct.astype(int))
        return applied
    except Exception:
        return 0


def _render(args: argparse.Namespace) -> tuple:
    from blender_render.engine import protect
    protect('render')
    _ensure_biotite_ccd_database(before_execution=True)
    import time
    import importlib.metadata
    from blender_render.operation_progress import record_stage, read_stages
    started = time.monotonic()
    style_path = Path(args.style_json).expanduser()
    if not style_path.is_absolute():
        style_path = (Path.cwd() / style_path).absolute()
    view_path = Path(args.view_json).expanduser()
    if not view_path.is_absolute():
        view_path = (Path.cwd() / view_path).absolute()
    structure_path = Path(args.structure).expanduser()
    if not structure_path.is_absolute():
        structure_path = (Path.cwd() / structure_path).absolute()
    output_png = Path(args.output_png).expanduser()
    if not output_png.is_absolute():
        output_png = (Path.cwd() / output_png).absolute()

    style_doc = validate_style_document(_load_json(style_path))
    view_doc = _load_json(view_path)
    record_stage(getattr(args, 'run_manifest', None), 'scene_build')

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    _configure_renderer(scene, style_doc)
    _set_world(scene, style_doc["world"])
    _configure_glow_compositor(scene, style_doc)
    camera = _ensure_camera(scene, style_doc, view_doc)
    pymol_up_axis = None
    try:
        pymol_up_axis = camera.matrix_world.to_quaternion() @ Vector((0.0, 1.0, 0.0))
    except Exception:
        pymol_up_axis = None

    ccd_retry_used = False

    def _load_with_ccd_retry(path: Path, object_name: str):
        nonlocal ccd_retry_used
        try:
            return _load_molecule_with_fallback(path, object_name)
        except Exception as exc:
            message = str(exc).lower()
            needs_ccd = (
                "components.bcif" in message
                or "ccd" in message
                or "chemical component" in message
            )
            if not needs_ccd or ccd_retry_used:
                raise
            _ensure_biotite_ccd_database()
            ccd_retry_used = True
            return _load_molecule_with_fallback(path, object_name)

    load_result = _load_with_ccd_retry(structure_path, "BridgeCustom")
    _apply_auth_chain_override(load_result, structure_path)
    ss_applied = _apply_pymol_ss_hints(load_result, view_doc)
    from blender_render.geometry import apply_atom_attributes, round_edges
    apply_atom_attributes(load_result, view_doc, style_doc)
    if ss_applied > 0 and _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False):
        print(f"Bridge Custom: applied PyMOL SS hints atoms={ss_applied} object=BridgeCustom")
    base_obj = _resolve_loaded_object(load_result, "BridgeCustom", allow_missing=True)
    ligand_anchor = _ligand_anchor_from_load_result(load_result)
    if ligand_anchor is None and _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False):
        print("Bridge Custom: ligand anchor not available; ligand cutout styles will be skipped.")

    protein_chain_ids = _protein_chain_ids(load_result)
    material_config = _apply_color_follow_to_materials(style_doc, view_doc)
    runtime_styles, material_config, runtime_chain_colors = expand_polymer_styles_for_multichain_palette(
        style_doc=style_doc,
        materials_cfg=material_config,
        protein_chain_ids=protein_chain_ids,
    )
    materials = _build_materials(material_config)
    if style_doc.get("color_follow", {}).get("enabled") and view_doc.get("color_hints", {}).get("atoms"):
        for name, material in materials.items():
            if material_config.get(name, {}).get("paint"):
                continue
            tree = material.node_tree
            attr = tree.nodes.new("ShaderNodeAttribute")
            attr.attribute_name = "Color"
            mix = tree.nodes.new("ShaderNodeMixRGB")
            mix.inputs[0].default_value = style_doc["color_follow"].get("mix", .65)
            mix.inputs[1].default_value = _hex_to_rgba(material_config[name]["base_color"])
            tree.links.new(attr.outputs["Color"], mix.inputs[2])
            # Feed the pigment into the existing clay AO/rim shader.
            shadow = tree.nodes.get("Clay shadow")
            bsdf = next(node for node in tree.nodes if node.type == "BSDF_PRINCIPLED")
            tree.links.new(mix.outputs["Color"], shadow.inputs[1] if shadow else bsdf.inputs["Base Color"])
    created_objects: list[bpy.types.Object] = []
    style_objects_by_name: dict[str, list[bpy.types.Object]] = {}
    style_centers_by_name: dict[str, Vector] = {}
    debug_styles = _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False)
    runtime_polymer_backend = "none"
    runtime_ligand_backend = "none"
    runtime_ligand_atom_spheres = False
    styled_geometry_applied = False
    surface_cutout_manifest_entries: list[dict[str, Any]] = []
    global_interface_center = _scene_bounds([base_obj] if base_obj is not None else [])[0]
    if debug_styles and base_obj is not None:
        try:
            base_center_dbg, base_radius_dbg = _scene_bounds([base_obj])
            print(
                "Bridge Custom base bounds: "
                f"center=({base_center_dbg.x:.3f},{base_center_dbg.y:.3f},{base_center_dbg.z:.3f}) "
                f"radius={base_radius_dbg:.3f}"
            )
        except Exception:
            pass
    camera_cfg = style_doc.get("camera", {})
    cutout_rotation = (_override_camera_rotation(camera, camera_cfg)
                       if camera_cfg.get("mode") == "override" else camera.matrix_world.to_3x3())
    cutout_camera_axis = cutout_rotation @ Vector((0, 0, -1))
    cutout_camera_up = cutout_rotation @ Vector((0, 1, 0))
    if debug_styles and runtime_chain_colors:
        print(f"Bridge Custom: runtime polymer chain colors {runtime_chain_colors}")

    for style_idx, style_cfg in enumerate(runtime_styles):
        style_name = str(style_cfg.get("name", f"style_{style_idx}"))
        style_kind_lower = str(style_cfg.get("style", "")).strip().lower()
        cutout_radius = 0.0
        cutout_offset = 0.0
        cutout_mirror = False
        cutout_isolate = True
        cutout_auto_side = False
        cutout_width_scale = 1.15
        cutout_depth_scale = 0.45
        cutout_height_scale = 1.15
        cutout_one_sided = True
        cutout_axis_mode = "interface"
        cutout_flatten_z = True
        cutout_prefer_camera = False
        cutout_one_sided_shift_scale = 1.0
        cutout_cap_mode = "panel"
        cutout_mode = "planes"
        cutout_frame_mode = "interface"
        cutout_frame_axes_cfg: dict[str, Any] | None = None
        cutout_planes_enabled_cfg: dict[str, Any] | None = None
        cutout_plane_scales_cfg: dict[str, Any] | None = None
        cutout_seal_mode = "boundary_fill"
        cutout_seal_depth_scale = 0.35
        cutout_seal_region_scale = 2.0
        if style_kind_lower == "surface":
            try:
                cutout_radius = float(style_cfg.get("ligand_cutout_radius", 0.0) or 0.0)
            except Exception:
                cutout_radius = 0.0
            try:
                cutout_offset = float(style_cfg.get("ligand_cutout_offset", 0.0) or 0.0)
            except Exception:
                cutout_offset = 0.0
            cutout_mirror = bool(style_cfg.get("ligand_cutout_mirror", False))
            cutout_isolate = bool(style_cfg.get("ligand_cutout_isolate", True))
            cutout_auto_side = bool(style_cfg.get("ligand_cutout_auto_side", False))
            cutout_one_sided = bool(style_cfg.get("ligand_cutout_one_sided", True))
            cutout_flatten_z = bool(style_cfg.get("ligand_cutout_flatten_z", True))
            try:
                cutout_width_scale = float(style_cfg.get("ligand_cutout_width_scale", 1.15) or 1.15)
            except Exception:
                cutout_width_scale = 1.15
            try:
                cutout_depth_scale = float(style_cfg.get("ligand_cutout_depth_scale", 0.45) or 0.45)
            except Exception:
                cutout_depth_scale = 0.45
            try:
                cutout_height_scale = float(style_cfg.get("ligand_cutout_height_scale", 1.15) or 1.15)
            except Exception:
                cutout_height_scale = 1.15
            axis_mode_value = str(style_cfg.get("ligand_cutout_axis_mode", "interface")).strip().lower()
            if axis_mode_value in {"interface", "camera"}:
                cutout_axis_mode = axis_mode_value
            cutout_prefer_camera = cutout_axis_mode == "camera"
            try:
                cutout_one_sided_shift_scale = float(
                    style_cfg.get("ligand_cutout_one_sided_shift_scale", 1.0) or 1.0
                )
            except Exception:
                cutout_one_sided_shift_scale = 1.0
            cutout_cap_mode_value = str(style_cfg.get("ligand_cutout_cap_mode", "panel")).strip().lower()
            if cutout_cap_mode_value in {"panel", "none"}:
                cutout_cap_mode = cutout_cap_mode_value
            cutout_mode_value = str(style_cfg.get("ligand_cutout_mode", "planes")).strip().lower()
            if cutout_mode_value in {"planes"}:
                cutout_mode = cutout_mode_value
            cutout_frame_axes_raw = style_cfg.get("ligand_cutout_frame_axes")
            if isinstance(cutout_frame_axes_raw, dict):
                cutout_frame_axes_cfg = cutout_frame_axes_raw
            frame_mode_raw = str(style_cfg.get("ligand_cutout_frame_mode", "") or "").strip().lower()
            if frame_mode_raw in {"preset", "interface", "camera"}:
                cutout_frame_mode = frame_mode_raw
            else:
                if cutout_frame_axes_cfg is not None:
                    cutout_frame_mode = "preset"
                elif cutout_axis_mode in {"interface", "camera"}:
                    # Backward-compatible mapping for old styles that only set ligand_cutout_axis_mode.
                    cutout_frame_mode = cutout_axis_mode
            cutout_prefer_camera = (cutout_axis_mode == "camera") or (cutout_frame_mode == "camera")
            raw_planes_enabled = style_cfg.get("ligand_cutout_planes_enabled")
            if isinstance(raw_planes_enabled, dict):
                cutout_planes_enabled_cfg = raw_planes_enabled
            raw_plane_scales = style_cfg.get("ligand_cutout_plane_scales")
            if isinstance(raw_plane_scales, dict):
                cutout_plane_scales_cfg = raw_plane_scales
            cutout_seal_mode_value = str(style_cfg.get("ligand_cutout_seal_mode", "boundary_fill")).strip().lower()
            if cutout_seal_mode_value in {"boundary_fill", "trimmed_panel", "none"}:
                cutout_seal_mode = cutout_seal_mode_value
            try:
                cutout_seal_depth_scale = float(style_cfg.get("ligand_cutout_seal_depth_scale", 0.35) or 0.35)
            except Exception:
                cutout_seal_depth_scale = 0.35
            try:
                cutout_seal_region_scale = float(style_cfg.get("ligand_cutout_seal_region_scale", 2.0) or 2.0)
            except Exception:
                cutout_seal_region_scale = 2.0

        isolate_surface_cutout = (
            cutout_isolate
            and style_kind_lower == "surface"
            and cutout_radius > 0
            and ligand_anchor is not None
        )

        style_load_result = load_result
        style_base_obj = base_obj
        isolate_wire = "wireframe_width_angstrom" in style_cfg
        if isolate_surface_cutout or isolate_wire:
            style_object_name = f"BridgeCustom_{style_idx}"
            style_load_result = _load_with_ccd_retry(structure_path, style_object_name)
            _apply_auth_chain_override(style_load_result, structure_path)
            ss_applied = _apply_pymol_ss_hints(style_load_result, view_doc)
            apply_atom_attributes(style_load_result, view_doc, style_doc)
            if ss_applied > 0 and debug_styles:
                print(f"Bridge Custom: applied PyMOL SS hints atoms={ss_applied} object={style_object_name}")
            style_base_obj = _resolve_loaded_object(style_load_result, style_object_name, allow_missing=True)

        selection_cfg = style_cfg.get("selection", {})
        selection, selection_indices = _build_selection(
            style_load_result,
            selection_cfg if isinstance(selection_cfg, dict) else {},
            structure_path=structure_path,
            style_name=style_name,
        )
        selected_center = _indices_centroid_vector(style_load_result, selection_indices)
        if selected_center is not None:
            style_centers_by_name[style_name] = selected_center * .01
        effective_cutout_offset = cutout_offset
        if (
            style_kind_lower == "surface"
            and cutout_radius > 0
            and ligand_anchor is not None
            and abs(cutout_offset) > 1e-6
            and cutout_auto_side
        ):
            style_center_for_cutout = _indices_centroid_vector(style_load_result, selection_indices)
            cutout_axis_ref = _ligand_cutout_axis(
                ligand_anchor,
                global_interface_center,
                camera_axis=cutout_camera_axis,
                prefer_camera=cutout_prefer_camera,
                flatten_z=cutout_flatten_z,
            )
            if style_center_for_cutout is not None and cutout_axis_ref.length > 1e-8:
                try:
                    rel = style_center_for_cutout - ligand_anchor
                    projected = float(rel.dot(cutout_axis_ref))
                    if abs(projected) > 1e-6:
                        side_sign = 1.0 if projected >= 0.0 else -1.0
                        effective_cutout_offset = abs(float(cutout_offset)) * side_sign
                except Exception:
                    effective_cutout_offset = cutout_offset
        if style_kind_lower == "surface":
            cutout_selection = bool(style_cfg.get("ligand_cutout_selection", False))
            if cutout_selection and cutout_radius > 0 and ligand_anchor is not None:
                cutout_center_ref = global_interface_center
                selection_width_scale = cutout_width_scale
                selection_depth_scale = cutout_depth_scale
                selection_height_scale = cutout_height_scale
                if cutout_mode == "planes":
                    try:
                        _resolved_plane_scales = resolve_plane_scales(
                            cutout_plane_scales_cfg,
                            width_scale=float(cutout_width_scale),
                            depth_scale=float(cutout_depth_scale),
                            height_scale=float(cutout_height_scale),
                        )
                        selection_width_scale = max(
                            float(_resolved_plane_scales.get("left", selection_width_scale)),
                            float(_resolved_plane_scales.get("right", selection_width_scale)),
                        )
                        selection_height_scale = max(
                            float(_resolved_plane_scales.get("top", selection_height_scale)),
                            float(_resolved_plane_scales.get("bottom", selection_height_scale)),
                        )
                        selection_depth_scale = float(_resolved_plane_scales.get("back", selection_depth_scale))
                    except Exception:
                        pass
                selection_indices = _selection_indices_with_ligand_box_cutout(
                    load_result=style_load_result,
                    base_indices=selection_indices,
                    ligand_anchor=ligand_anchor,
                    radius=cutout_radius,
                    offset=effective_cutout_offset,
                    interface_center=cutout_center_ref,
                    camera_axis=cutout_camera_axis,
                    width_scale=selection_width_scale,
                    depth_scale=selection_depth_scale,
                    height_scale=selection_height_scale,
                    one_sided=cutout_one_sided,
                    prefer_camera=cutout_prefer_camera,
                    flatten_z=cutout_flatten_z,
                )
                selection = _selection_to_named_attribute(
                    style_load_result,
                    selection_indices,
                    style_name=f"{style_name}_box_cutout",
                )

        material_name = style_cfg["material"]
        material = materials[material_name]
        material_cfg_for_cutout = material_config.get(material_name)
        if not isinstance(material_cfg_for_cutout, dict):
            material_cfg_for_cutout = None
        style_result, new_modifier_names, style_tree_changed = _add_style_with_tracking(
            load_result=style_load_result,
            style_cfg=style_cfg,
            selection=selection,
            material=material,
            base_object=style_base_obj,
        )
        used_fallback = False
        used_auto_chain_fallback = False
        style_empty = _style_result_is_empty(style_result) and not new_modifier_names and not style_tree_changed
        fallback_cfg = style_cfg.get("fallback_selection")
        if style_empty and isinstance(fallback_cfg, dict):
            fallback_selection, fallback_indices = _build_selection(
                style_load_result,
                fallback_cfg,
                structure_path=structure_path,
                style_name=f"{style_name}_fallback",
            )
            if fallback_indices is not None:
                selection_indices = fallback_indices
            style_result, fallback_modifiers, fallback_tree_changed = _add_style_with_tracking(
                load_result=style_load_result,
                style_cfg=style_cfg,
                selection=fallback_selection,
                material=material,
                base_object=style_base_obj,
            )
            for modifier_name in fallback_modifiers:
                if modifier_name not in new_modifier_names:
                    new_modifier_names.append(modifier_name)
            style_tree_changed = style_tree_changed or fallback_tree_changed
            used_fallback = True
            style_empty = _style_result_is_empty(style_result) and not new_modifier_names and not style_tree_changed
        if style_empty:
            chain_ids = _protein_chain_ids(style_load_result)
            auto_query = _auto_chain_fallback_query(style_cfg, chain_ids)
            if auto_query:
                auto_selection, auto_indices = _build_selection(
                    style_load_result,
                    {"query": auto_query},
                    structure_path=structure_path,
                    style_name=f"{style_name}_auto_chain",
                )
                if auto_indices is not None:
                    selection_indices = auto_indices
                style_result, auto_modifiers, auto_tree_changed = _add_style_with_tracking(
                    load_result=style_load_result,
                    style_cfg=style_cfg,
                    selection=auto_selection,
                    material=material,
                    base_object=style_base_obj,
                )
                for modifier_name in auto_modifiers:
                    if modifier_name not in new_modifier_names:
                        new_modifier_names.append(modifier_name)
                style_tree_changed = style_tree_changed or auto_tree_changed
                used_auto_chain_fallback = True
                style_empty = _style_result_is_empty(style_result) and not new_modifier_names and not style_tree_changed
        style_name_lower = str(style_cfg.get("name", "")).strip().lower()
        style_obj = getattr(style_result, "_object", None)
        if style_kind_lower == "surface":
            if cutout_radius > 0:
                if ligand_anchor is None:
                    if debug_styles:
                        print(
                            "Bridge Custom: skipping ligand cutout due to missing ligand anchor "
                            f"style={style_name}"
                        )
                else:
                    target_obj = None
                    if style_obj is not None and str(getattr(style_obj, "type", "")).upper() == "MESH":
                        target_obj = style_obj
                    elif style_base_obj is not None and str(getattr(style_base_obj, "type", "")).upper() == "MESH":
                        target_obj = style_base_obj
                    cutout_targets: list[bpy.types.Object] = []
                    if target_obj is not None:
                        cutout_targets.append(target_obj)
                    cutout_center_ref = global_interface_center
                    cutout_applied = False
                    for cut_target in cutout_targets:
                        cutout_runtime_info: dict[str, Any] = {
                            "style_name": str(style_name),
                            "target_object": str(getattr(cut_target, "name", "")),
                        }
                        try:
                            applied = _apply_surface_ligand_cutout(
                                load_result=style_load_result,
                                target_obj=cut_target,
                                cap_material_cfg=material_cfg_for_cutout,
                                anchor_load_result=load_result,
                                anchor_object=base_obj,
                                ligand_anchor=ligand_anchor,
                                radius=cutout_radius,
                                offset=effective_cutout_offset,
                                mirror=cutout_mirror,
                                interface_center=cutout_center_ref,
                                camera_axis=cutout_camera_axis,
                                camera_up=cutout_camera_up,
                                width_scale=cutout_width_scale,
                                depth_scale=cutout_depth_scale,
                                height_scale=cutout_height_scale,
                                one_sided=cutout_one_sided,
                                prefer_camera=cutout_prefer_camera,
                                flatten_z=cutout_flatten_z,
                                one_sided_shift_scale=cutout_one_sided_shift_scale,
                                cap_mode=cutout_cap_mode,
                                cutout_mode=cutout_mode,
                                frame_mode=cutout_frame_mode,
                                frame_axes_cfg=cutout_frame_axes_cfg,
                                frame_roll_deg=float(style_cfg.get("ligand_cutout_roll_deg", 0.0)),
                                planes_enabled_cfg=cutout_planes_enabled_cfg,
                                plane_scales_cfg=cutout_plane_scales_cfg,
                                seal_mode=cutout_seal_mode,
                                seal_depth_scale=cutout_seal_depth_scale,
                                seal_region_scale=cutout_seal_region_scale,
                                runtime_info=cutout_runtime_info,
                            )
                            cutout_applied = cutout_applied or bool(applied)
                            surface_cutout_manifest_entries.append(cutout_runtime_info)
                        except Exception as exc:
                            raise RuntimeError(
                                "Ligand surface cutout failed "
                                f"style={style_name} target={getattr(cut_target, 'name', '<unknown>')}: {exc}"
                            ) from exc
                    if debug_styles:
                        target_names = [str(getattr(obj, "name", "")) for obj in cutout_targets]
                        print(
                            "Bridge Custom: ligand cutout selection+boolean "
                            f"style={style_name} "
                            f"boolean_applied={int(bool(cutout_applied))} "
                            f"targets={target_names} "
                            f"radius={cutout_radius:.3f} offset={effective_cutout_offset:.3f} "
                            f"mirror={int(cutout_mirror)}"
                        )
        if isolate_wire:
            _apply_surface_wireframe(style_base_obj, style_cfg["wireframe_width_angstrom"],
                                    style_cfg.get("wireframe_spacing_angstrom", 1.0), cutout_camera_axis)
        if style_kind_lower == "sticks" and _is_ligand_style(style_cfg):
            runtime_ligand_backend = "mn_native_sticks"
        if _is_polymer_cartoon_style(style_cfg):
            runtime_polymer_backend = "mn_cartoon"
            require_geometry = False
            if style_name_lower == "chain_a_cartoon":
                require_geometry = len(protein_chain_ids) >= 1
            elif style_name_lower == "chain_b_cartoon":
                require_geometry = len(protein_chain_ids) >= 2
            elif style_name_lower == "polymer_other_cartoon":
                require_geometry = len(protein_chain_ids) >= 3
            elif len(protein_chain_ids) >= 1:
                require_geometry = True
            if require_geometry and style_empty:
                raise RuntimeError(
                    "Cartoon polymer style produced no geometry. "
                    f"style={style_cfg.get('name', '<unnamed>')} protein_chains={protein_chain_ids} "
                    "Secondary-structure assignment is required (run dss before export)."
                )
        if (
            style_kind_lower == "sticks"
            and _is_ligand_style(style_cfg)
            and style_obj is None
            and style_base_obj is not None
            and (new_modifier_names or style_tree_changed)
        ):
            tuned = _tune_native_sticks_style(
                style_base_obj,
                float(style_cfg.get("sticks_radius", style_cfg.get("radius"))) if style_cfg.get("sticks_radius", style_cfg.get("radius")) is not None else None,
                style_cfg.get("sticks_quality", style_cfg.get("quality")),
                style_cfg.get("sticks_shade_smooth", style_cfg.get("shade_smooth")),
                style_cfg.get("color_blur"),
                disable_atom_spheres=True,
                modifier_names=new_modifier_names if new_modifier_names else None,
            )
            if tuned > 0 and debug_styles:
                print(
                    "Bridge Custom: tuned native sticks modifiers "
                    f"(sockets={tuned}) style={style_name}"
                )
        if (
            style_kind_lower == "sticks"
            and _is_ligand_style(style_cfg)
            and style_obj is not None
        ):
            runtime_ligand_atom_spheres = (
                runtime_ligand_atom_spheres
                or _native_sticks_atom_spheres_enabled(style_obj)
            )
        elif (
            style_kind_lower == "sticks"
            and _is_ligand_style(style_cfg)
            and style_base_obj is not None
            and (new_modifier_names or style_tree_changed)
        ):
            runtime_ligand_atom_spheres = (
                runtime_ligand_atom_spheres
                or _native_sticks_atom_spheres_enabled(
                    style_base_obj,
                    modifier_names=new_modifier_names if new_modifier_names else None,
                )
            )
        if (
            _is_polymer_cartoon_style(style_cfg)
            and style_obj is not None
            and not _object_has_renderable_geometry(style_obj)
        ):
            raise RuntimeError(
                "Cartoon polymer style produced non-renderable geometry. "
                f"style={style_cfg.get('name', '<unnamed>')} "
                "Refusing tube/trace downgrade for preset fidelity."
            )
        if style_obj is not None:
            styled_geometry_applied = True
            created_objects.append(style_obj)
            style_name = str(style_cfg.get("name", "")).strip()
            if style_name:
                style_objects_by_name.setdefault(style_name, []).append(style_obj)
        elif (new_modifier_names or style_tree_changed) and style_base_obj is not None and style_base_obj not in created_objects:
            styled_geometry_applied = True
            created_objects.append(style_base_obj)
        _assign_style_material(
            load_result=style_load_result,
            base_object=style_base_obj,
            style_result=style_result,
            material=material,
            modifier_names=new_modifier_names,
        )
        if debug_styles:
            sel_indices = getattr(style_result, "_selection_indices", None)
            sel_count = len(sel_indices) if isinstance(sel_indices, list) else None
            if sel_count is None and isinstance(selection_indices, list):
                sel_count = len(selection_indices)
            obj_name = getattr(style_obj, "name", None) if style_obj is not None else None
            material_slots: list[str] = []
            if style_obj is not None:
                try:
                    for slot in getattr(style_obj, "material_slots", []):
                        mat = getattr(slot, "material", None)
                        if mat is not None and getattr(mat, "name", ""):
                            material_slots.append(str(mat.name))
                except Exception:
                    material_slots = []
            print(
                "Bridge Custom debug style: "
                f"idx={style_idx} "
                f"name={style_cfg.get('name')} "
                f"style={style_cfg.get('style')} "
                f"selection_count={sel_count} "
                f"fallback={int(used_fallback)} "
                f"auto_chain_fallback={int(used_auto_chain_fallback)} "
                f"selection={selection!r} "
                f"new_modifiers={new_modifier_names} "
                f"tree_changed={int(style_tree_changed)} "
                f"object={obj_name} "
                f"materials={material_slots}"
            )

    if not created_objects and base_obj is not None:
        if not styled_geometry_applied:
            raise RuntimeError(
                "Styles produced no geometry (no style objects/modifiers were created). "
                "Refusing fallback molecule display because preset fidelity would be invalid."
            )
        try:
            base_obj.hide_render = False
            base_obj.hide_set(False)
        except Exception:
            pass
        created_objects.append(base_obj)
    if not any(_object_has_renderable_geometry(obj) for obj in created_objects):
        raise RuntimeError("Styles produced no visible geometry; refusing a different representation.")
    rounded_objects = round_edges(created_objects, style_doc.get("geometry", {}).get("edge_rounding"))

    if debug_styles:
        for obj in created_objects:
            try:
                modifier_names = [str(mod.name) for mod in list(obj.modifiers)]
            except Exception:
                modifier_names = []
            print(
                "Bridge Custom created object: "
                f"name={getattr(obj, 'name', '<unknown>')} "
                f"type={getattr(obj, 'type', '<unknown>')} "
                f"modifiers={modifier_names}"
            )

    camera_cfg = style_doc.get("camera", {})
    if isinstance(camera_cfg, dict) and str(camera_cfg.get("mode", "pymol")).strip().lower() == "override":
        _apply_override_camera(
            scene,
            camera,
            camera_cfg,
            created_objects,
            style_objects_by_name=style_objects_by_name,
            style_centers_by_name=style_centers_by_name,
        )

    _configure_depth_of_field(camera, camera_cfg)
    light_target, light_radius = _scene_bounds(created_objects)
    yaw_offset_for_lights = 0.0
    if isinstance(camera_cfg, dict):
        try:
            yaw_offset_for_lights = float(camera_cfg.get("yaw_offset_deg", 0.0))
        except Exception:
            yaw_offset_for_lights = 0.0
    _add_lights(
        scene,
        style_doc["lights"],
        target=light_target,
        radius=light_radius,
        pymol_up_axis=pymol_up_axis,
        yaw_offset_deg=yaw_offset_for_lights,
    )

    output_png.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(output_png)

    output_cfg = style_doc.get("output")
    geometry_ready = time.monotonic()
    record_stage(getattr(args, 'run_manifest', None), 'rendering')
    raw_view = bool(isinstance(output_cfg, dict) and output_cfg.get("raw_view", False))
    if raw_view:
        _render_raw_view(scene)
    else:
        bpy.ops.render.render(animation=False, write_still=True)
    rendered = time.monotonic()
    blend_output = _resolve_blend_output_path(style_doc, output_png)
    if blend_output is not None:
        blend_output.parent.mkdir(parents=True, exist_ok=True)
        notice = bpy.data.texts.get("Blender Render - dependency licenses") or bpy.data.texts.new("Blender Render - dependency licenses")
        notice.clear()
        notice.write("Rendered with Blender 5.0.1 and Molecular Nodes 4.5.10.\n"
                     "Molecular Nodes assets and Blender API scripts retain GPL-3.0-or-later.\n"
                     "Source and build instructions: https://github.com/magnusbauer/blender_render\n"
                     "Upstream Molecular Nodes: https://github.com/BradyAJohnston/MolecularNodes\n\n")
        notice.write(resource_bytes(Path(__file__).with_name("GPL-3.0-or-later.txt")).decode())
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_output), copy=True)
        print(f"Custom blend saved: {blend_output}")
    run_manifest_raw = str(getattr(args, "run_manifest", "") or "").strip()
    if run_manifest_raw:
        run_manifest_path = Path(run_manifest_raw).expanduser()
        if not run_manifest_path.is_absolute():
            run_manifest_path = (Path.cwd() / run_manifest_path).absolute()
        try:
            manifest_doc = {}
            if run_manifest_path.is_file():
                manifest_doc = _load_json(run_manifest_path)
            manifest_doc["polymer_backend"] = runtime_polymer_backend
            manifest_doc["blender_version"] = bpy.app.version_string
            try:
                mn_version = importlib.metadata.version("molecularnodes")
            except importlib.metadata.PackageNotFoundError:
                import molecularnodes, tomllib
                mn_version = tomllib.loads(Path(molecularnodes.__file__).with_name("blender_manifest.toml").read_text())["version"]
            import biotite
            manifest_doc["runtime_versions"] = {"blender": bpy.app.version_string, "molecularnodes": mn_version,
                "python": ".".join(map(str, sys.version_info[:3])), "numpy": np.__version__, "biotite": biotite.__version__}
            manifest_doc["cycles_device"] = json.loads(scene.get("bridge_device_info", "{}"))
            from blender_render.telemetry import record, write_timings
            write_timings(run_manifest_path, [
                record('scene_build', 'worker', geometry_ready-started),
                record('rendering', 'worker', rendered-geometry_ready),
                record('artifact_preparation', 'worker', time.monotonic()-rendered)])
            manifest_doc["resolved_preset"] = style_doc
            manifest_doc["camera_matrix_world"] = [list(row) for row in camera.matrix_world]
            manifest_doc["camera_type"] = camera.data.type
            manifest_doc["camera_ortho_scale"] = camera.data.ortho_scale
            manifest_doc["camera_depth_of_field"] = {
                "enabled": camera.data.dof.use_dof,
                "focus_distance_angstrom": camera.data.dof.focus_distance / 0.01,
                "f_stop": camera.data.dof.aperture_fstop,
            }
            manifest_doc["resolution"] = [scene.render.resolution_x, scene.render.resolution_y]
            manifest_doc["rounded_objects"] = rounded_objects
            manifest_doc["ligand_backend"] = runtime_ligand_backend
            manifest_doc["ligand_atom_spheres"] = bool(runtime_ligand_atom_spheres)
            # Backward-compatible aliases.
            manifest_doc["sticks_backend"] = runtime_ligand_backend
            manifest_doc["polymer_cartoon_backend"] = runtime_polymer_backend
            manifest_doc["cartoon_polymer_chain_ids"] = protein_chain_ids
            if runtime_chain_colors:
                manifest_doc["polymer_chain_colors"] = runtime_chain_colors
            if surface_cutout_manifest_entries:
                manifest_doc["surface_ligand_cutouts"] = surface_cutout_manifest_entries
                chosen_cutout = next(
                    (entry for entry in surface_cutout_manifest_entries if bool(entry.get("applied", False))),
                    surface_cutout_manifest_entries[0],
                )
                for key in (
                    "ligand_cutout_mode",
                    "ligand_cutout_frame_mode",
                    "ligand_cutout_frame_axes",
                    "ligand_cutout_planes_enabled",
                    "ligand_cutout_plane_scales",
                    "ligand_cutout_seal_mode",
                    "ligand_cutout_seal_depth_scale",
                    "ligand_cutout_seal_region_scale",
                    "ligand_cutout_boundary_loops_total",
                    "ligand_cutout_boundary_loops_near_ligand",
                    "ligand_cutout_boundary_filled_faces",
                ):
                    if key in chosen_cutout:
                        manifest_doc[key] = chosen_cutout.get(key)
            run_manifest_path.parent.mkdir(parents=True, exist_ok=True)
            run_manifest_path.write_text(json.dumps(manifest_doc, indent=2), encoding="utf-8")
        except Exception as exc:
            raise RuntimeError("Required render manifest could not be written") from exc
    print(f"Custom render complete: {output_png}")
    return scene, camera, light_target


def main() -> None:
    parsed = _parse_argv(sys.argv)
    from blender_render.engine import protect
    protect('render')
    _configure_molecularnodes_import_path()
    from blender_render.worker_ccd import initialize
    capture = initialize()
    try:
        _render(parsed)
    finally:
        capture.close()


if __name__ == "__main__":
    main()
