"""Shared contracts for reference styles; actual Blender checks are recorded separately."""
import copy
import json
from pathlib import Path

import pytest

from blender_render import core
from blender_render.chain_palette import expand_polymer_styles_for_multichain_palette
from blender_render.presets import required_capabilities


def base():
    return json.loads(Path(core.__file__).with_name('style_preset_1_opaque_pastel.json').read_text())


def test_uniform_polymer_color_survives_multichain_expansion():
    style = {'styles': [{'name': 'uniform', 'style': 'cartoon', 'selection': {'polymer': True},
                         'material': 'blue', 'color_by_chain': False}]}
    materials = {'blue': {'base_color': '#80CAEC'}}
    original = copy.deepcopy(style)
    styles, resolved, _ = expand_polymer_styles_for_multichain_palette(
        style_doc=style, materials_cfg=materials, protein_chain_ids=['AP1', 'BP1', 'CP1'])
    assert styles == style['styles']
    assert resolved == materials
    assert style == original


@pytest.mark.parametrize('section,key,value,match', [
    ('world', 'gradient_direction', [0, 0], 'nonzero'),
    ('world', 'gradient_direction', [1], 'too short'),
    ('world', 'ambient_strength', -1, 'minimum'),
    ('camera', 'ortho_scale_angstrom', 0, 'minimum'),
    ('camera', 'fit_margin', .5, 'minimum'),
])
def test_invalid_reference_controls_fail_before_render(section, key, value, match):
    style = base();style[section][key] = value
    with pytest.raises(core.StyleValidationError, match=match):
        core.validate_style_document(style)


def test_unsupported_frosted_fields_are_rejected():
    style = base();style['materials']['test'] = {'frosted': {'fake_grid': True}}
    with pytest.raises(core.StyleValidationError, match='fake_grid'):
        core.validate_style_document(style)


def test_element_attribute_requires_palette():
    style = base();style['materials']['test'] = {'color_attribute': 'ElementColor'}
    with pytest.raises(core.StyleValidationError, match='requires geometry.element_palette'):
        core.validate_style_document(style)
    style['geometry'] = {'element_palette': {'C': '#58A9ED', 'O': '#FF007F'}}
    core.validate_style_document(style)
    assert 'geometry.element_palette' in required_capabilities(style)


def test_gradient_cannot_hide_an_explicit_color_attribute():
    style = base();style['materials']['test'] = {'color_attribute': 'Color',
        'color_gradient': {'colors': ['#FF0000', '#0000FF'], 'direction': [1,0,0]}}
    with pytest.raises(core.StyleValidationError, match='choose color_attribute or color_gradient'):
        core.validate_style_document(style)


def test_frosted_opacity_order_is_semantic():
    style = base();style['materials']['test'] = {'frosted': {
        'noise_scale': 5, 'opacity_min': .8, 'opacity_max': .2, 'grid_scale': 30,
        'grid_width': .01, 'grid_strength': .4, 'grid_color': '#FFFFFF'}}
    with pytest.raises(core.StyleValidationError, match='opacity_min must be <= opacity_max'):
        core.validate_style_document(style)


REFERENCE_IDS = [
    'powder_blue_rounded', 'cyan_mint_cutaway', 'pastel_duplex_rounded',
    'pastel_duplex_cartoon', 'vivid_binding_site', 'ivory_lime_frosted',
    'mint_sky_assembly', 'neon_duplex_hero',
]


@pytest.mark.parametrize('identifier', REFERENCE_IDS)
def test_reference_presets_share_json_override_and_output_contracts(identifier):
    from blender_render.presets import preset_path, preset_id
    from blender_render.spec import resolve_options
    style = json.loads(preset_path(identifier).read_text())
    assert style['output']['save_blend'] is False
    assert style['output']['format'] == 'PNG'
    assert preset_id('reference_%02d' % (REFERENCE_IDS.index(identifier)+1)) == identifier
    edits = {'camera': {'mode': 'override', 'roll_deg': 17, 'projection': 'ORTHO'},
             'output': {'width': 720, 'height': 480}}
    full = copy.deepcopy(style)
    full['camera'].update(edits['camera'])
    full['output'].update(edits['output'])
    a = resolve_options({'style_doc': full}, {})
    b = resolve_options({'preset': identifier, 'overrides': edits}, {})
    assert a['style_doc'] == b['style_doc']
    assert a['render_spec']['sha256'] == b['render_spec']['sha256']
    scene = resolve_options({'style_doc': full, 'save_blend': True}, {})
    assert scene['save_blend'] is True and a['save_blend'] is False


def test_cutaway_preset_only_cuts_isolated_surfaces():
    from blender_render.presets import preset_path
    style = json.loads(preset_path('cyan_mint_cutaway').read_text())
    for entry in style['styles']:
        if entry['style'] == 'surface':
            assert entry['ligand_cutout_isolate'] is True
            assert entry['ligand_cutout_selection'] is False
            assert entry['ligand_cutout_frame_mode'] == 'camera'
            assert entry['ligand_cutout_plane_scales']['back'] > 0
            assert entry['ligand_cutout_plane_scales']['bottom'] < entry['ligand_cutout_plane_scales']['top']
        else:
            assert 'ligand_cutout_radius' not in entry


def test_cutaway_pocket_residues_are_complete_and_element_colored():
    from blender_render.presets import preset_path
    style = json.loads(preset_path('cyan_mint_cutaway').read_text())
    pocket = next(entry for entry in style['styles'] if entry['name'] == 'pocket_sidechains')
    assert pocket['style'] == 'sticks'
    assert pocket['selection']['within_ligand_radius'] == 5.0
    assert pocket['selection']['expand_to_residue'] is True
    assert pocket['color_blur'] is False
    assert style['materials'][pocket['material']]['color_attribute'] == 'Color'
    palette = style['geometry']['element_palette']
    assert len({palette[element] for element in ('C', 'N', 'O', 'S')}) == 4


def test_reference_materials_keep_glow_and_glass_controls_explicit():
    from blender_render.presets import preset_path, required_capabilities
    cutaway = json.loads(preset_path('cyan_mint_cutaway').read_text())
    ligand = cutaway['materials']['ligand']
    assert ligand['edge_highlight']['color'] == '#FFE5F7'
    assert ligand['edge_highlight']['strength'] > ligand['emission_strength']
    assert ligand['glow']['threshold'] < 1.0
    assert 'material.fog_glow' in required_capabilities(cutaway)
    glass = json.loads(preset_path('ivory_lime_frosted').read_text())
    assert glass['materials']['shell']['transmission'] >= 0.7
    assert glass['materials']['shell']['frosted']['coordinates'] == 'angstrom'
    assert glass['materials']['pink']['transmission'] > 0.5
    wires = [s for s in glass['styles'] if 'wireframe_width_angstrom' in s]
    assert {s['style'] for s in wires} == {'surface', 'cartoon'}
    assert all(s['wireframe_width_angstrom'] > 0 for s in wires)
    assert 'representation.wire_mesh' in required_capabilities(glass)


@pytest.mark.parametrize('edits,match', [
    ({'power':0}, 'minimum'), ({'strength':-1}, 'minimum'),
    ({'color':'white'}, 'edge_highlight.color'), ({'spots':True}, 'spots'),
])
def test_invalid_edge_highlights_fail_before_render(edits, match):
    style = base()
    style['materials']['test'] = {'edge_highlight':{'power':2, 'color':'#FFFFFF', 'strength':1}}
    style['materials']['test']['edge_highlight'].update(edits)
    with pytest.raises(core.StyleValidationError, match=match):
        core.validate_style_document(style)


def test_wire_mesh_requires_width_and_positive_spacing():
    style = base()
    entry = style['styles'][0]
    entry['wireframe_spacing_angstrom'] = 1.0
    with pytest.raises(core.StyleValidationError, match='requires wireframe_width_angstrom'):
        core.validate_style_document(style)
    entry['wireframe_width_angstrom'] = 0
    with pytest.raises(core.StyleValidationError, match='minimum'):
        core.validate_style_document(style)
    entry['wireframe_width_angstrom'] = .02
    entry['wireframe_spacing_angstrom'] = 0
    with pytest.raises(core.StyleValidationError, match='minimum'):
        core.validate_style_document(style)
