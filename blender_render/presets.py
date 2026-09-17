"""Small built-in catalog; IDs and filenames retain the existing public contract."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

# Existing preset identifiers stay stable; reference styles use distinct identifiers.
_PRESETS = (
    ("preset_1_interface", "style_preset_1_opaque_pastel.json", "01_opaque_pastel_ses_interface.png",
     "Soft opaque pastel cartoons with native ligand sticks.", ("pastel",), "clay"),
    ("preset_2_frosted_blue", "style_preset_2_frosted_blue.json", "02_saturated_blue_core_frosted_shell.png",
     "Saturated blue cartoon core with a frosted surface shell.", ("frosted_blue",), "frosted"),
    ("preset_3_black_tubes", "style_preset_3_black_tubes.json", "03_pastel_tube_cartoon_black.png",
     "Pastel ribbons and native sticks on black.", ("black_tubes",), "clay"),
    ("preset_4_milky_green", "style_preset_4_milky_green.json", "04_milky_white_green_shell_cyan_dna.png",
     "Milky green surface, protein ribbons and cyan nucleic acids.", ("milky_green",), "milky"),
    ("preset_5_black_interface", "style_preset_5_black_interface.json", "05_black_background_interface.png",
     "Cartoon and ligand interface on a black background.", ("black_interface",), "clay"),
    ("dried_paint_green", "style_preset_dried_paint_green.json", "dried_paint_green.png",
     "Textured matte green paint with rounded ribbon edges.", ("green_paint", "dried-paint-green"), "dried_paint"),
    ('powder_blue_rounded', 'style_preset_powder_blue_rounded.json', 'reference_01.png',
     'Powder blue rounded protein with pink nucleic acid on black.', ('reference_01',), 'clay'),
    ('cyan_mint_cutaway', 'style_preset_cyan_mint_cutaway.json', 'reference_02.png',
     'Cyan and mint surfaces with clean pocket cuts and intact interior ribbons.', ('reference_02',), 'clay'),
    ('pastel_duplex_rounded', 'style_preset_pastel_duplex_rounded.json', 'reference_03.png',
     'Rounded green and blue proteins with pink DNA on black.', ('reference_03',), 'clay'),
    ('pastel_duplex_cartoon', 'style_preset_pastel_duplex_cartoon.json', 'reference_04.png',
     'Thin blue and green secondary-structure ribbons with pink DNA.', ('reference_04',), 'clay'),
    ('vivid_binding_site', 'style_preset_vivid_binding_site.json', 'reference_05.png',
     'Macro ligand view with vivid element colors and teal cartoon context.', ('reference_05',), 'clay'),
    ('ivory_lime_frosted', 'style_preset_ivory_lime_frosted.json', 'reference_06.png',
     'Ivory frosted grid shell, lime core and cyan nucleic acids.', ('reference_06',), 'frosted'),
    ('mint_sky_assembly', 'style_preset_mint_sky_assembly.json', 'reference_07.png',
     'Mint and sky protein assembly with purple and pink nucleic acids.', ('reference_07',), 'clay'),
    ('neon_duplex_hero', 'style_preset_neon_duplex_hero.json', 'reference_08.png',
     'Green protein and gradient nucleic acids against a pink-cyan background.', ('reference_08',), 'clay'),
)
DEFAULT_PRESET = "preset_1_interface"
PRESET_STYLE_FILENAMES = {row[0]: row[1] for row in _PRESETS}
PRESET_OUTPUT_FILENAMES = {row[0]: row[2] for row in _PRESETS}
_ALIASES = {alias: row[0] for row in _PRESETS for alias in (row[0], "builtin/" + row[0], *row[4])}


def preset_id(name: str) -> str:
    if not isinstance(name, str) or name not in _ALIASES:
        raise ValueError(f"Unknown preset: {name!r}. Expected one of: {', '.join(PRESET_STYLE_FILENAMES)}")
    return _ALIASES[name]


def preset_path(name: str) -> Path:
    return Path(__file__).with_name(PRESET_STYLE_FILENAMES[preset_id(name)])


def required_capabilities(style: dict) -> list[str]:
    """Requirements of this JSON, not a claim about a running worker's runtime."""
    required = {"representation." + entry["style"] for entry in style["styles"]}
    required.update("material.dried_paint" if "paint" in material else "material.clay" for material in style["materials"].values())
    if style.get("geometry", {}).get("edge_rounding", {}).get("radius_angstrom", 0) > 0:
        required.add("geometry.edge_rounding")
    for material in style["materials"].values():
        for field, capability in (("frosted", "material.frosted_grid"), ("color_gradient", "material.color_gradient"), ("color_attribute", "material.attribute_color"), ("glow", "material.fog_glow"), ("edge_highlight", "material.edge_highlight")):
            if field in material:
                required.add(capability)
    if "element_palette" in style.get("geometry", {}):
        required.add("geometry.element_palette")
    world = style.get("world", {})
    if "gradient_direction" in world:
        required.add("world.directional_gradient")
    if "ambient_color" in world or "ambient_strength" in world:
        required.add("world.separate_illumination")
    camera = style.get("camera", {})
    if "depth_of_field" in camera:
        required.add("camera.depth_of_field")
    if any(key in camera for key in ("fit", "roll_deg", "ortho_scale_angstrom", "projection")):
        required.add("camera.reference_framing")
    if any("surface_scale_radius" in entry or "surface_relaxation_steps" in entry for entry in style["styles"]):
        required.add("representation.surface_smoothing")
    if any("wireframe_width_angstrom" in entry for entry in style["styles"]):
        required.add("representation.wire_mesh")
    if any(entry.get("color_by_chain") is False for entry in style["styles"]):
        required.add("material.uniform_polymer_color")
    if any("nucleic_base_scale" in entry for entry in style["styles"]):
        required.add("representation.nucleic_base_thickness")
    cfg = style.get('output', {}).get('animation', {})
    if cfg.get('mode') in ('rock', '360'):
        required.update(('animation.camera_orbit', 'encoding.ffmpeg'))
    return sorted(required)


def catalog() -> list[dict]:
    entries = []
    for identifier, filename, _, description, aliases, family in _PRESETS:
        from .engine import resource_bytes
        style = json.loads(resource_bytes(preset_path(identifier)))
        from .spec import style_digest
        descriptors = style_descriptors(style)
        entries.append({
            **descriptors,
            "id": identifier, "qualified_id": "builtin/" + identifier, "revision": "1",
            "source": "builtin", "source_json": filename, "aliases": list(aliases),
            "description": description, "material_family": family,
            "representations": sorted({entry["style"] for entry in style["styles"]}),
            "schema": "style.schema.json", "schema_version": style["version"],
            "required_capabilities": required_capabilities(style),
            "defaults": {"preserve_view": True, "save_blend": False},
            "supported_content": _supported_content(identifier),
            "default_camera_policy": "preserve_input",
            "authored_camera": {key: copy.deepcopy(style["camera"][key]) for key in
                ("mode", "direction_source", "projection", "fit", "focus_style", "preserve_framing")
                if key in style["camera"]},
            "style_digest": style_digest(style),
            "editable_controls": copy.deepcopy(EDITABLE_CONTROLS),
            "shared_host_status": "eligible_under_current_policy",
        })
    return copy.deepcopy(entries)


CATALOG_FORMAT_VERSION = 1
# Shared schema references, not a second schema or a hosted admission allowlist.
EDITABLE_CONTROLS = {
    'renderer': 'style.schema.json#/properties/renderer',
    'atom_colors': 'style.schema.json#/properties/color_follow',
    'output': 'style.schema.json#/properties/output',
    'camera': 'style.schema.json#/properties/camera',
    'palette': 'style.schema.json#/properties/geometry/properties/secondary_structure_palette',
    'background': 'style.schema.json#/properties/world',
    'lighting': 'style.schema.json#/properties/lights',
    'materials': 'style.schema.json#/properties/materials',
    'geometry': 'style.schema.json#/properties/geometry',
    'representations': 'style.schema.json#/properties/styles',
}


def _chain_palette_uses_base_color(style, name):
    """Ask the existing pure palette resolver whether this is an anchor input.

    This dependency check operates on copies only; it never assigns render
    colors. It avoids reimplementing the renderer's anchor-selection rules.
    """
    from .chain_palette import (_resolve_polymer_chain_anchor_colors,
                                selection_chain_constraints, style_targets_polymer)
    materials = style['materials']
    if not any(style_targets_polymer(entry) and entry.get('color_by_chain', True)
               and not selection_chain_constraints(entry['selection'])[0]
               and not materials[entry['material']].get('paint') for entry in style['styles']):
        return False
    changed = copy.deepcopy(materials)
    current = changed[name].get('base_color', '').upper()
    changed[name]['base_color'] = '#000000' if current != '#000000' else '#FFFFFF'
    return (_resolve_polymer_chain_anchor_colors(style, materials)
            != _resolve_polymer_chain_anchor_colors(style, changed))


def _color_follow_uses_base_color(style, name, bundle):
    """Check the existing color-follow consumers without assigning any colors.

    Reference/catalog metadata describes configured tendencies. A supplied bundle
    can rule out those consumers: mirror payload hint precedence and the exact
    chain/class targeting in _apply_color_follow_to_materials().
    """
    config = style.get('color_follow', {})
    if not config.get('enabled', False):
        return False
    if bundle is None:
        return True
    hints = bundle.get('view', {}).get('color_hints')
    if isinstance(bundle.get('color_hints'), dict):
        hints = bundle['color_hints']
    if not isinstance(hints, dict) or config.get('mix', .65) >= 1:
        return False
    if hints.get('atoms'):
        return True
    chains = hints.get('chain') if isinstance(hints.get('chain'), dict) else {}
    classes = hints.get('classes') if isinstance(hints.get('classes'), dict) else {}
    mode = config.get('mode', 'auto').strip().lower()
    for entry in style['styles']:
        if entry['material'] != name:
            continue
        selection = entry['selection']
        query = str(selection.get('query', ''))
        if mode in {'auto', 'chain'}:
            chain = selection.get('chain', [])
            ids = chain.split(',') if isinstance(chain, str) else chain
            ids = [str(value).strip().upper() for value in ids if str(value).strip()]
            ids += [value.upper() for value in re.findall(r'\bchain\s*=?\s*([A-Za-z0-9_]+)', query, re.I)]
            for chain_id in ids:
                value = chains.get(chain_id) or chains.get(chain_id.lower())
                if isinstance(value, str) and re.fullmatch(r'#[0-9A-Fa-f]{6}', value):
                    return True
        if mode in {'auto', 'class'}:
            # Renderer category precedence is intentionally distinct from hard
            # content coverage; this only identifies an existing color consumer.
            category = next((key for key in ('ligand', 'polymer', 'nucleic') if selection.get(key)), None)
            if category is None:
                query = query.lower()
                category = ('ligand' if 'ligand' in query else 'polymer' if 'polymer' in query else
                            'nucleic' if 'nucleic' in query and 'not nucleic' not in query else 'polymer')
            value = classes.get(category)
            if isinstance(value, str) and re.fullmatch(r'#[0-9A-Fa-f]{6}', value):
                return True
    return False


def active_material_controls(material: dict, *, style=None, material_name=None, bundle=None) -> dict:
    """D1 view of controls consumed by the existing material dispatch.

    _create_material() passes only ``paint`` to make_paint_material() and
    returns. ``glow`` is independently read by _configure_glow_compositor().
    Keep legacy documents intact; this view is for validation and descriptors,
    not a renderer rewrite or a replacement schema.
    """
    if material.get('paint'):
        return {key: copy.deepcopy(value) for key, value in material.items()
                if key in {'paint', 'glow'}}
    active = copy.deepcopy(material)
    clay = bool({'shadow_color', 'highlight_color', 'ao_strength', 'rim_strength'} & material.keys())
    edge = bool(material.get('edge_highlight'))
    emission = material.get('emission_strength', 0) > 0 or edge
    if not clay:
        for key in ('ao_distance', 'rim_blend', 'rim_power'):
            active.pop(key, None)
    if not emission:
        active.pop('emission_color', None)
    # Attribute/gradient pigment replaces the base-color socket. Keep the value
    # only if a shadow/highlight/emission fallback or a runtime color path reads it.
    if material.get('color_attribute') or material.get('color_gradient'):
        fallback = ((clay and not {'shadow_color', 'highlight_color'} <= material.keys())
                    or (emission and 'emission_color' not in material
                        and (edge or 'highlight_color' not in material)))
        secondary = (style is not None and (_color_follow_uses_base_color(style, material_name, bundle)
                     or (material_name is not None and _chain_palette_uses_base_color(style, material_name))))
        if not fallback and not secondary:
            active.pop('base_color', None)
    return active


def style_descriptors(style: dict, *, bundle=None) -> dict:
    """Exact JSON tendencies only; no rendered appearance or live input claim."""
    used = {entry['material'] for entry in style['styles']}
    palette = set()

    def colors(value):
        if isinstance(value, str) and len(value) == 7 and value.startswith('#'):
            palette.add(value.upper())
        elif isinstance(value, dict):
            for item in value.values():
                colors(item)
        elif isinstance(value, list):
            for item in value:
                colors(item)

    for name in used:
        material = active_material_controls(style['materials'][name], style=style, material_name=name, bundle=bundle)
        for key in ('base_color', 'color_gradient'):
            colors(material.get(key))
        if material.get('paint'):
            # Exact shader tint, not an inferred pixel color. Preserve linear
            # RGB values (including HDR) instead of quantizing them to hex.
            tint = [float(v) for v in material['paint']['dark_paint_tint']]
            palette.add('linear_rgb:' + json.dumps(tint, separators=(',', ':'), allow_nan=False))
    geometry = style.get('geometry', {})
    colors(geometry.get('secondary_structure_palette'))
    colors(geometry.get('element_palette'))
    world = style['world']
    background = {world.get('background_type', 'solid')}
    if world.get('background_type') == 'gradient':
        if 'top_color' in world and world['top_color'] == world.get('bottom_color'):
            background = {'solid', world['top_color'].upper()}
    elif 'color' in world:
        background.add(world['color'].upper())
    if style['renderer'].get('transparent_background'):
        background = {'transparent'}
    camera = style['camera']
    composition = {'structure'}
    if camera.get('mode') == 'override' and camera.get('focus_style'):
        composition = {'focus_style'}
    if 'depth_of_field' in camera:
        composition.add('depth_of_field')
    edges = {'smooth' if all(s.get('shade_smooth', False) for s in style['styles']) else 'mixed'}
    if geometry.get('edge_rounding', {}).get('radius_angstrom', 0) > 0 or any(s.get('peptide_rounded') for s in style['styles']):
        edges.add('rounded')
    return {'representations': sorted({s['style'] for s in style['styles']}),
            'palette': sorted(palette), 'background': sorted(background),
            'lighting': sorted({light['type'].lower() for light in style['lights']}),
            'edges': sorted(edges), 'composition': sorted(composition),
            'ligand_treatment': ['sticks'] if any(s['style'] == 'sticks' for s in style['styles']) else []}


def _supported_content(identifier: str) -> list[str]:
    # Authored content coverage of the shipped selections, not atom inspection.
    content = {'protein', 'ligand'}
    if identifier not in {'cyan_mint_cutaway', 'vivid_binding_site'}:
        content.add('nucleic')
    if identifier.startswith('preset_') or identifier == 'dried_paint_green':
        content.update({'ion', 'water'})
    return sorted(content)


def compact_catalog() -> dict:
    """Versioned, location-independent snapshot of installed built-in metadata."""
    from .engine import resource_bytes
    from .spec import style_digest
    keys = ('id', 'qualified_id', 'revision', 'aliases', 'description', 'supported_content',
            'representations', 'material_family', 'palette', 'background', 'lighting', 'edges',
            'composition', 'ligand_treatment', 'default_camera_policy', 'authored_camera',
            'required_capabilities', 'style_digest', 'editable_controls', 'shared_host_status')
    entries = []
    for row in catalog():
        entry = {key: copy.deepcopy(row[key]) for key in keys}
        for key, value in entry.items():
            if isinstance(value, list):
                entry[key] = sorted(set(value))
        entries.append(entry)
    snapshot = {'format_version': CATALOG_FORMAT_VERSION,
                'schema_digest': style_digest(json.loads(resource_bytes(Path(__file__).with_name('style.schema.json')))),
                'entries': sorted(entries, key=lambda row: row['qualified_id'])}
    return {**snapshot, 'digest': style_digest(snapshot)}
