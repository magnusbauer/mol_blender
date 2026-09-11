"""Local preset persistence and agent-authored visual briefs; no compute or network."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from .core import load_and_validate_style_json, validate_style_document
from .presets import catalog, preset_id, preset_path, required_capabilities
from .spec import PresetDefinition, resolve_options, style_digest, validate_camera_policy


def _local_path(identifier: str, root: Path) -> Path:
    if not isinstance(identifier, str) or not re.fullmatch(r"(?:user|project)/[a-z0-9][a-z0-9_-]{0,79}", identifier):
        raise ValueError("Local preset ID must be user/name or project/name (lowercase letters, digits, _ or -)")
    path = root / (identifier + ".json")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Local preset path escapes catalog directory")
    return path


def inspect_preset(identifier: str, root: Path) -> dict:
    if identifier.startswith(("user/", "project/")):
        doc = json.loads(_local_path(identifier, root).read_text())
        if not isinstance(doc, dict) or type(doc.get("format")) is not int or doc["format"] not in (1, 2) or doc.get("id") != identifier:
            raise ValueError("Local preset identity/format mismatch")
        required = {"format", "id", "revision", "description", "style_doc", "sha256", "required_capabilities", "provenance"}
        if doc["format"] == 2:
            required.add("camera_policy")
        if set(doc) != required:
            raise ValueError("Invalid local preset envelope fields; missing=" + str(sorted(required - set(doc)))
                             + ", unsupported=" + str(sorted(set(doc) - required)))
        if (type(doc["revision"]) is not int or doc["revision"] < 1
                or not isinstance(doc["description"], str) or not isinstance(doc["provenance"], dict)
                or not isinstance(doc["required_capabilities"], list)
                or not all(isinstance(item, str) for item in doc["required_capabilities"])):
            raise ValueError("Invalid local preset envelope metadata")
        validate_style_document(doc["style_doc"])
        if doc.get("sha256") != style_digest(doc["style_doc"]):
            raise ValueError("Saved preset digest mismatch; validate and save the edited JSON again")
        if doc["required_capabilities"] != required_capabilities(doc["style_doc"]):
            raise ValueError("Saved preset capability requirements mismatch")
        if doc["format"] == 1:
            doc.update(camera_policy="authored", camera_policy_source="legacy_format_1")
        validate_camera_policy(doc["camera_policy"], doc["style_doc"])
        return {**doc, "revision": str(doc["revision"])}
    name = preset_id(identifier)
    return {"format": 1, "id": "builtin/" + name, "revision": "1",
            "style_doc": load_and_validate_style_json(preset_path(name)),
            "description": next(row["description"] for row in catalog() if row["id"] == name)}


def resolve_authored(options: dict, payload: dict, root: Path) -> dict:
    """Local catalog lookup only; all option semantics belong to the shared resolver."""
    options = copy.deepcopy(options)
    name = options.get("preset", "")
    local = None
    if "style_doc" not in options and name.startswith(("user/", "project/")):
        local = inspect_preset(name, root)
    definition = None
    if local:
        source = {
            "kind": name.split("/")[0], "id": name, "revision": local["revision"],
            "sha256": local["sha256"], "camera_policy": local["camera_policy"],
        }
        if "camera_policy_source" in local:
            source["camera_policy_source"] = local["camera_policy_source"]
        definition = PresetDefinition(name, local["style_doc"], source, local["camera_policy"])
    return resolve_options(options, payload, preset_definition=definition)


def save_preset(identifier: str, style: dict, root: Path, *, description: str,
                provenance: dict | None = None, replace: bool = False,
                camera_policy: str = "preserve_input") -> dict:
    validate_style_document(style)
    validate_camera_policy(camera_policy, style)
    if not isinstance(description, str) or (provenance is not None and not isinstance(provenance, dict)):
        raise ValueError("Preset description must be text and provenance must be an object")
    path = _local_path(identifier, root)
    previous = inspect_preset(identifier, root) if path.exists() else None
    if previous and not replace:
        raise ValueError("Preset already exists; use --replace to create the next revision")
    doc = {"format": 2, "camera_policy": camera_policy, "id": identifier, "revision": int(previous["revision"]) + 1 if previous else 1,
           "description": description, "style_doc": copy.deepcopy(style), "sha256": style_digest(style),
           "required_capabilities": required_capabilities(style), "provenance": provenance or {}}
    data = json.dumps(doc, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".preset-")
    temp = Path(name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(data)
        if replace:
            os.replace(temp, path)
        else:
            # Atomic, no-clobber publication, including concurrent writers.
            os.link(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return {**doc, "revision": str(doc["revision"]), "path": str(path)}


def list_presets(root: Path) -> list[dict]:
    entries = catalog()
    for namespace in ("user", "project"):
        for path in sorted((root / namespace).glob("*.json")):
            doc = inspect_preset(namespace + "/" + path.stem, root)
            entries.append({key: doc[key] for key in ("id", "revision", "description", "required_capabilities", "camera_policy")})
    return entries


def reference_brief(image: Path, observations: dict, *, bundle: dict | None = None,
                    allow_low_fit_approximation: bool = False) -> dict:
    """Validate PNG provenance, then delegate all preset/fit decisions to D1.

    The caller records visible descriptors after inspection. No image model,
    hidden shader inference, pixel transport or reference-only spec hash.
    """
    from PIL import Image
    from . import engine, selection
    fields = {"representations", "material_family", "palette", "background", "lighting",
              "edges", "composition", "projection", "ligand_treatment", "required_capabilities", "overrides", "notes", "base_preset"}
    if not isinstance(observations, dict):
        raise ValueError("Visual brief must be an object")
    unknown = set(observations) - fields
    if unknown:
        raise ValueError("Unsupported visual brief fields: " + ", ".join(sorted(unknown)))
    for key in ("representations", "palette", "required_capabilities"):
        if not isinstance(observations.get(key), list) or not all(isinstance(v, str) for v in observations[key]):
            raise ValueError("Visual brief requires a string list: " + key)
    if not observations["representations"] or not observations.get("material_family"):
        raise ValueError("Visual brief requires observable representations and material_family")
    for key in ("notes", "base_preset"):
        if key in observations and not isinstance(observations[key], str):
            raise ValueError("Visual brief field must be text: " + key)
    visual = {key:value for key,value in observations.items()
              if key not in {"notes", "base_preset", "overrides", "required_capabilities"}}
    request = selection.SelectionRequest(preset=observations.get("base_preset") or None,
        visual_brief=visual, hard_requirements={"required_capabilities":observations["required_capabilities"]},
        overrides=observations.get("overrides", {}), allow_low_fit_approximation=allow_low_fit_approximation)
    # One byte snapshot for both hash and PNG validation; do not serialize Pillow
    # metadata, image paths, URLs, or pixels into either returned record.
    import io
    data = Path(image).read_bytes()
    with Image.open(io.BytesIO(data)) as png:
        if png.format != "PNG":
            raise ValueError("Reference must be a PNG")
        dimensions = list(png.size)
        png.verify()
    decision = selection.select_preset(request, available_capabilities=engine.CAPABILITIES,
                                       policy=selection.SelectionPolicy(), bundle=bundle)
    brief = {"reference": {"sha256":hashlib.sha256(data).hexdigest(), "dimensions":dimensions},
             "observation_source":"developer image inspection", **copy.deepcopy(observations)}
    return {"visual_brief":brief, "preset_decision":decision.to_dict()}
