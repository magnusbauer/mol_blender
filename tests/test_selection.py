"""D1 library contracts; application interpreters run under c3_boundary_guard."""
import copy
import dataclasses
import json
import os
from pathlib import Path

import pytest

from blender_render import authoring, core, engine, presets, spec


@pytest.fixture
def api():
    from blender_render import selection
    return selection


@pytest.fixture
def bundle():
    return {"version": "1.0", "cif_text": "data_test\nloop_\n_atom_site.group_PDB\n_atom_site.id\nATOM 1\n",
            "view": {"pymol_view": [1,0,0,0,1,0,0,0,1,0,0,-50,0,0,0,1,100,0],
                     "viewport_px": [800,600], "fov_deg": 20, "orthoscopic": False}, "dss": {"ran": True}}


def select(api, *, capabilities=None, policy=None, bundle=None, **request):
    return api.select_preset(api.SelectionRequest(**request),
        available_capabilities=engine.CAPABILITIES if capabilities is None else capabilities,
        policy=api.SelectionPolicy() if policy is None else policy, bundle=bundle)


@pytest.mark.parametrize('name', ['preset_1_interface', 'pastel', 'builtin/preset_1_interface'])
@pytest.mark.parametrize('revision', [None, 1, '1'])
def test_explicit_identity_revision_and_no_context(api, name, revision):
    decision = select(api, preset=name, revision=revision)
    assert decision.preset_id == 'builtin/preset_1_interface'
    assert decision.preset_revision == '1' and decision.fit == 'high'
    assert decision.hard_requirements_satisfied and decision.execution_compatible
    assert decision.resolved_spec_sha256 is None
    assert not decision.approximation_authorized


@pytest.mark.parametrize('options', [{'preset':'unknown'}, {'preset':'pastel', 'revision':'2'}, {'revision':'1'}])
def test_explicit_selection_never_silently_replaces(api, options):
    with pytest.raises(api.SelectionError):
        select(api, **options)


def test_default_ties_ignore_enumeration(api, monkeypatch):
    original = presets.catalog
    a = select(api)
    monkeypatch.setattr(presets, 'catalog', lambda: list(reversed(original())))
    b = select(api)
    assert a.to_json() == b.to_json()
    assert a.preset_id == 'builtin/' + presets.DEFAULT_PRESET
    policy = api.SelectionPolicy(allowed_presets=['pastel_duplex_rounded', 'pastel_duplex_cartoon'])
    assert select(api, policy=policy).preset_id == 'builtin/pastel_duplex_cartoon'


def test_lexicographic_selection_and_medium_approximation(api):
    decision = select(api, visual_brief={'material_family':'dried_paint', 'representations':['cartoon']})
    assert decision.preset_id == 'builtin/dried_paint_green' and decision.fit == 'high'
    medium = select(api, preset='pastel', visual_brief={'palette':['#010203']})
    assert medium.fit == 'medium' and medium.execution_compatible
    assert any('approximation' in reason.lower() for reason in medium.reasons)
    assert medium.unmatched_features == ['visual.palette']
    assert not medium.new_preset_recommended


def test_policy_exclusion_and_compatibility(api):
    with pytest.raises(api.SelectionError, match='policy'):
        select(api, policy=api.SelectionPolicy(allowed_presets=[]))
    with pytest.raises(api.SelectionError, match='policy'):
        select(api, preset='pastel', policy=api.SelectionPolicy(allowed_presets=['green_paint']))
    a = select(api, policy=api.SelectionPolicy(allowed_presets=['green_paint']))
    assert a.preset_id == 'builtin/dried_paint_green'
    b = select(api, preset='pastel', capabilities=[])
    assert b.fit == 'low' and b.hard_requirements_satisfied and not b.execution_compatible
    c = select(api, hard_requirements={'save_blend':True}, policy=api.SelectionPolicy(allow_scene=False))
    assert c.fit == 'low' and c.hard_requirements_satisfied and not c.execution_compatible


@pytest.mark.parametrize('authorized', [False, True])
def test_low_fit_bundle_requires_explicit_compatible_approximation(api, bundle, authorized):
    decision = select(api, preset='pastel', bundle=bundle,
        visual_brief={'material_family':'directional_ink'}, allow_low_fit_approximation=authorized)
    assert decision.fit == 'low' and decision.new_preset_recommended
    assert decision.hard_requirements_satisfied and decision.execution_compatible
    assert decision.approximation_authorized is authorized
    assert decision.route == ('preset-overrides' if authorized else 'developer/new-preset')
    assert (decision.resolved_spec_sha256 is not None) is authorized


@pytest.mark.parametrize('kind', ['hard', 'capability', 'policy'])
def test_approximation_cannot_bypass_requirements(api, bundle, kind):
    kwargs = {'hard_requirements':{'representations':['wire']}} if kind == 'hard' else (
        {'capabilities':[]} if kind == 'capability' else
        {'hard_requirements':{'width':5000}, 'policy':api.SelectionPolicy(public=True)})
    d = select(api, preset='pastel', bundle=bundle, allow_low_fit_approximation=True, **kwargs)
    assert d.fit == 'low' and not d.execution_compatible and d.approximation_authorized
    assert d.resolved_spec_sha256 is None and d.route == 'developer/new-preset'


def test_real_context_exact_resolver_hash_and_output_requirements(api, bundle):
    edits = {'camera':{'yaw_deg':31}, 'materials':{'inner_chain_a':{'base_color':'#ABCDEF'}}}
    hard = {'width':720, 'height':480, 'save_blend':True, 'transparent_background':True, 'camera_policy':'authored'}
    request = dict(preset='pastel', overrides=edits, hard_requirements=hard)
    decision = select(api, bundle=bundle, **request)
    expected = spec.resolve_options({'preset':'preset_1_interface', 'overrides':edits,
        'width':720, 'height':480, 'save_blend':True, 'transparent_background':True, 'preserve_view':False}, bundle)
    assert decision.fit == 'high'
    assert decision.resolved_spec_sha256 == expected['render_spec']['sha256']
    assert decision.to_json() == select(api, bundle=copy.deepcopy(bundle), **request).to_json()
    a = select(api, bundle=bundle)
    changed = copy.deepcopy(bundle); changed['view']['viewport_px'] = [800,400]
    assert a.resolved_spec_sha256 != select(api, bundle=changed).resolved_spec_sha256
    # The resolver hashes the RenderSpec, not the bundle or saved camera bytes.
    changed = copy.deepcopy(bundle); changed['view']['fov_deg'] = 35
    assert select(api, bundle=changed).resolved_spec_sha256 == spec.resolve_options({}, changed)['render_spec']['sha256']
    with pytest.raises(core.PayloadValidationError):
        select(api, bundle={})


@pytest.mark.parametrize('edits', [
    {'camera':{'anchor_model_center':True}}, {'camera':{'unknown':1}},
    {'output':{'width':False}}, {'output':{'filename':'ignored.png'}},
    {'materials':{'unused':{'base_color':'#123456'}}}, {'overrides':{}},
    {'lights':[{'type':'AREA', 'energy':float('nan')}]},
])
def test_dead_or_invalid_edits_are_errors_not_low_fit(api, edits):
    with pytest.raises((ValueError, core.StyleValidationError)):
        select(api, preset='pastel', overrides=edits)


def test_small_edits_and_extensive_replacement(api):
    style = spec.builtin_definition('green_paint').style_doc
    small = {'geometry':{'secondary_structure_palette':{'sheet':'#D7A9E3'}}}
    assert select(api, preset='green_paint', overrides=small).fit == 'high'
    replace = {'styles':copy.deepcopy(style['styles'])}
    replace['styles'][0]['selection'] = {'chain':'A'}
    assert select(api, preset='green_paint', overrides=replace).fit == 'low'
    assert select(api, preset='green_paint', overrides={'styles':style['styles']}).fit == 'high'
    style = spec.builtin_definition('pastel').style_doc
    used = sorted({s['material'] for s in style['styles']})[:3]
    assert select(api, preset='pastel', overrides={'materials':{m:{'roughness':.123} for m in used}}).fit == 'low'


def test_unused_material_and_capability_addition(api):
    with pytest.raises(ValueError, match='materials used'):
        select(api, overrides={'materials':{'no_such_material':{'base_color':'#ABCDEF'}}})
    d = select(api, preset='pastel', hard_requirements={'required_capabilities':['camera.depth_of_field']})
    assert not d.hard_requirements_satisfied and d.fit == 'low'
    assert 'camera.depth_of_field' in d.unmatched_features
    assert d.unsupported_requirements == []
    edits = {'camera':{'mode':'override','projection':'PERSP','depth_of_field':{'focus_distance_angstrom':100,'f_stop':2}}}
    d = select(api, preset='pastel', overrides=edits)
    assert d.fit == 'low' and d.execution_compatible


@pytest.mark.parametrize('constructor,kwargs', [
    ('SelectionRequest', {'extra':1}), ('SelectionRequest', {'overrides':[]}),
    ('SelectionRequest', {'allow_low_fit_approximation':1}), ('SelectionRequest', {'revision':True}),
    ('SelectionRequest', {'visual_brief':{'extra':'text'}}),
    ('SelectionRequest', {'hard_requirements':{'width':True}}),
    ('SelectionRequest', {'visual_brief':{'representations':'cartoon'}}),
    ('SelectionPolicy', {'public':1}), ('SelectionPolicy', {'allowed_presets':'pastel'}),
])
def test_validated_dataclasses(api, constructor, kwargs):
    with pytest.raises((TypeError, ValueError)):
        getattr(api, constructor)(**kwargs)


def test_defensive_copies_and_safe_reasons(api):
    overrides = {'materials':{'inner_chain_a':{'base_color':'#ABCDEF'}}}
    brief = {'palette':['#ABCDEF']}
    req = api.SelectionRequest(preset='pastel', overrides=overrides, visual_brief=brief)
    overrides['materials']['inner_chain_a']['base_color'] = '#000000'
    brief['palette'].append('#000000')
    d = api.select_preset(req, available_capabilities=engine.CAPABILITIES, policy=api.SelectionPolicy())
    assert d.fit == 'high' and d.overrides['materials']['inner_chain_a']['base_color'] == '#ABCDEF'
    doc = d.to_dict(); doc['overrides'].clear()
    assert d.overrides
    secret = 'https://private.invalid/image.png?signature=secret'
    d = select(api, visual_brief={'material_family':secret})
    assert secret not in d.to_json()
    assert dataclasses.is_dataclass(d)


def test_catalog_snapshot_stable_and_complete(monkeypatch):
    snap = presets.compact_catalog()
    assert snap['format_version'] == 1 and len(snap['entries']) == 14
    assert snap['digest'] == spec.style_digest({k:v for k,v in snap.items() if k != 'digest'})
    assert snap['schema_digest'] == spec.style_digest(json.loads(Path(core.__file__).with_name('style.schema.json').read_text()))
    old = presets.catalog
    def reordered():
        rows = list(reversed(old()))
        for row in rows:
            for key in ('aliases','required_capabilities','representations','supported_content','palette'):
                row[key] = list(reversed(row[key]))
        return rows
    monkeypatch.setattr(presets, 'catalog', reordered)
    assert presets.compact_catalog() == snap
    for row in snap['entries']:
        assert row['revision'] == '1'
        assert row['shared_host_status'] == 'eligible_under_current_policy'
        assert row['default_camera_policy'] == 'preserve_input'
        assert row['style_digest'] == spec.style_digest(spec.builtin_definition(row['id']).style_doc)
        assert row['editable_controls'] and row['supported_content']
        assert not {'path','timestamp','engine_build','style_doc'} & row.keys()


def test_reference_delegates_safe_provenance_and_context(api, tmp_path, bundle, monkeypatch):
    from PIL import Image, PngImagePlugin
    path = tmp_path/'private-reference.png'
    meta = PngImagePlugin.PngInfo(); meta.add_text('signed_url','https://secret.invalid')
    Image.new('RGB',(12,13)).save(path, pnginfo=meta)
    observations = {'representations':['cartoon','sticks'], 'material_family':'clay',
        'palette':['#AACCDD'], 'required_capabilities':[], 'base_preset':'pastel',
        'overrides':{'materials':{'inner_chain_a':{'base_color':'#AACCDD'}}}}
    calls = []; real = api.select_preset
    def capture(*args, **kwargs):
        calls.append(kwargs); return real(*args, **kwargs)
    monkeypatch.setattr(api, 'select_preset', capture)
    a = authoring.reference_brief(path, observations)
    b = authoring.reference_brief(path, observations, bundle=bundle)
    assert a['preset_decision']['resolved_spec_sha256'] is None
    assert b['preset_decision']['resolved_spec_sha256'] is not None
    assert calls[0]['available_capabilities'] == engine.CAPABILITIES
    assert set(a['visual_brief']['reference']) == {'sha256','dimensions'}
    assert a['visual_brief']['reference']['dimensions'] == [12,13]
    assert str(path) not in json.dumps(a) and 'https://secret.invalid' not in json.dumps(a)


def test_local_public_revisions_are_strings_disk_unchanged(tmp_path):
    style = spec.builtin_definition().style_doc
    saved = authoring.save_preset('user/clone', style, tmp_path, description='Clone')
    assert saved['revision'] == '1'
    assert json.loads((tmp_path/'user/clone.json').read_text())['revision'] == 1
    assert authoring.inspect_preset('user/clone', tmp_path)['revision'] == '1'
    assert authoring.list_presets(tmp_path)[-1]['revision'] == '1'
    resolved = authoring.resolve_authored({'preset':'user/clone'}, {}, tmp_path)
    assert resolved['render_spec']['preset_source']['revision'] == '1'
    assert authoring.save_preset('user/clone', style, tmp_path, description='Next', replace=True)['revision'] == '2'


def test_d1_installed_import_origins():
    from blender_render import selection
    if os.environ.get('C3_INSTALLED_SITE'):
        for module in (selection, authoring, presets, spec, core):
            assert Path(module.__file__).is_relative_to(Path(os.environ['C3_INSTALLED_SITE']))


def test_supported_content_filter_and_material_rank(api):
    policy = api.SelectionPolicy(allowed_presets=['vivid_binding_site', 'pastel'])
    d = select(api, policy=policy, hard_requirements={'content':['nucleic']})
    assert d.preset_id == 'builtin/preset_1_interface' and d.hard_requirements_satisfied
    d = select(api, preset='vivid_binding_site', hard_requirements={'content':['nucleic']})
    assert d.fit == 'low' and not d.hard_requirements_satisfied
    # A material match outranks a soft background match, independent of order.
    d = select(api, visual_brief={'material_family':'dried_paint', 'background':'black'})
    assert d.preset_id == 'builtin/dried_paint_green' and d.fit == 'medium'


def test_stricter_override_and_capability_policy(api, bundle):
    for policy in (api.SelectionPolicy(allowed_override_sections=[]),
                   api.SelectionPolicy(max_width=100),
                   api.SelectionPolicy(allowed_capabilities=[])):
        d = select(api, preset='pastel', policy=policy, bundle=bundle,
                   hard_requirements={'width':720}, allow_low_fit_approximation=True)
        assert d.hard_requirements_satisfied and not d.execution_compatible
        assert d.route == 'developer/new-preset' and d.resolved_spec_sha256 is None


@pytest.mark.parametrize('camera_policy,edits,mode', [
    ('preserve_input', {'yaw_deg':20}, 'override'),
    ('authored', {'mode':'pymol'}, 'pymol'),
])
@pytest.mark.parametrize('authorized', [False, True])
@pytest.mark.parametrize('with_bundle', [False, True])
def test_camera_hard_requirement_does_not_override_resolver_precedence(
        api, bundle, camera_policy, edits, mode, authorized, with_bundle):
    context = bundle if with_bundle else None
    overrides = {'camera':edits}
    resolved = spec.resolve_options({'preset':'pastel', 'overrides':overrides,
                                    'preserve_view':camera_policy == 'preserve_input'}, context or {})
    assert resolved['style_doc']['camera']['mode'] == mode
    assert all(resolved['style_doc']['camera'][key] == value for key,value in edits.items())
    d = select(api, preset='pastel', bundle=context, hard_requirements={'camera_policy':camera_policy},
               overrides=overrides, allow_low_fit_approximation=authorized)
    assert d.overrides == overrides
    assert not d.hard_requirements_satisfied and not d.execution_compatible
    assert d.fit == 'low' and d.resolved_spec_sha256 is None
    assert d.route == 'developer/new-preset'
    assert 'camera.' + camera_policy in d.unmatched_features


@pytest.mark.parametrize('identifier', sorted(presets.PRESET_STYLE_FILENAMES))
def test_shipped_static_eligibility_under_unchanged_resolver(api, identifier, bundle):
    d = select(api, preset=identifier, bundle=bundle, policy=api.SelectionPolicy(public=True))
    assert d.execution_compatible
    assert d.resolved_spec_sha256 == spec.resolve_options({'preset':identifier}, bundle, public=True)['render_spec']['sha256']


def test_array_replacement_and_no_visual_assignments(api, bundle):
    base = spec.builtin_definition('pastel').style_doc
    edits = {'lights':[base['lights'][0]]}
    d = select(api, preset='pastel', bundle=bundle, overrides=edits, allow_low_fit_approximation=True)
    assert d.fit == 'low' and d.execution_compatible
    assert d.resolved_spec_sha256 == spec.resolve_options({'preset':'pastel','overrides':edits}, bundle)['render_spec']['sha256']
    descriptive = select(api, visual_brief={'palette':['#123456'], 'material_family':'uninstalled_shader'})
    assert descriptive.overrides == {} and descriptive.fit == 'low'


def test_recorded_reference_aliases_and_focus_remain_inspectable(api, tmp_path):
    from PIL import Image
    image = tmp_path/'reference.png'; Image.new('RGB',(10,10)).save(image)
    obs = {'representations':['cartoon','sticks'], 'material_family':'clay',
           'palette':['#729ECC'], 'background':'Pale blue gray', 'lighting':'Soft highlights',
           'edges':'Smooth ribbons and round sticks',
           'composition':'One compact protein and a highlighted ligand', 'ligand_treatment':'Pink sticks',
           'required_capabilities':[], 'base_preset':'pastel',
           'overrides':{'materials':{'inner_chain_a':{'base_color':'#729ECC'}}}}
    high = authoring.reference_brief(image, obs)
    assert high['preset_decision']['fit'] == 'high'
    assert high['visual_brief']['background'] == 'Pale blue gray'
    obs['required_capabilities'] = ['camera.depth_of_field']
    obs['composition'] = 'Selective focus within a compact protein; other depths are soft'
    low = authoring.reference_brief(image, obs)['preset_decision']
    assert low['fit'] == 'low' and low['route'] == 'developer/new-preset'
    assert low['unsupported_requirements'] == []
    assert low['resolved_spec_sha256'] is None


def test_invalid_png_and_unknown_reference_fields(tmp_path):
    from PIL import Image
    image = tmp_path/'reference.jpg'; Image.new('RGB',(10,10)).save(image)
    obs = {'representations':['cartoon'], 'material_family':'clay', 'palette':[], 'required_capabilities':[]}
    with pytest.raises(ValueError, match='PNG'):
        authoring.reference_brief(image, obs)
    obs['signed_url'] = 'https://secret.invalid'
    with pytest.raises(ValueError, match='Unsupported visual brief'):
        authoring.reference_brief(image, obs)


@pytest.mark.parametrize('changes', [
    {'execution_compatible':False}, {'format_version':2}, {'preset_revision':1},
    {'approximation_authorized':1}, {'route':'developer/new-preset'}, {'extra':'ignored'},
])
def test_decision_validation(api, changes):
    doc = select(api).to_dict(); doc.update(changes)
    with pytest.raises((TypeError, ValueError)):
        api.PresetDecision(**doc)


@pytest.mark.parametrize('name', ['reference_01', 'reference_08'])
def test_existing_authored_camera_is_small(api, name):
    d = select(api, preset=name, hard_requirements={'camera_policy':'authored'})
    assert d.fit == 'high'


def test_visual_projection_is_an_exact_soft_descriptor(api, bundle):
    d = select(api, preset='reference_01', hard_requirements={'camera_policy':'authored'},
               visual_brief={'projection':'orthographic'})
    assert d.fit == 'high'
    d = select(api, preset='pastel', bundle=bundle, visual_brief={'projection':'perspective'})
    assert d.fit == 'high'
    d = select(api, preset='pastel', bundle=bundle, visual_brief={'projection':'ORTHO'})
    assert d.fit == 'medium' and d.unmatched_features == ['visual.projection']
    assert d.overrides == {}  # The observation does not invent a camera edit.
    assert select(api, preset='pastel', visual_brief={'projection':'PERSP'}).fit == 'medium'


def test_new_material_model_is_low_and_capabilities_still_gate_execution(api, bundle):
    paint = spec.builtin_definition('green_paint').style_doc['materials']['dried_paint']['paint']
    edits = {'materials':{'inner_chain_a':{'paint':paint}}}
    d = select(api, preset='pastel', overrides=edits, bundle=bundle, allow_low_fit_approximation=True)
    assert d.fit == 'low' and d.execution_compatible and d.resolved_spec_sha256
    d = select(api, preset='pastel', overrides=edits, bundle=bundle, allow_low_fit_approximation=True,
               capabilities=set(engine.CAPABILITIES)-{'material.dried_paint'})
    assert not d.execution_compatible and d.resolved_spec_sha256 is None


@pytest.mark.parametrize('edits', [
    {'camera':{'projection':'ORTHO'}}, {'camera':{'roll_deg':15}},
    {'camera':{'lens_mm':70}}, {'camera':{'mode':'override','direction_source':'preset','yaw_offset_deg':15}},
    {'camera':{'mode':'override','fit':False,'fit_margin':1.5}},
    {'world':{'top_color':'#123456'}},
])
def test_mode_inactive_overrides_are_rejected(api, edits):
    with pytest.raises(ValueError, match='inactive'):
        select(api, preset='green_paint', overrides=edits)


def test_camera_mode_controls_consumption_and_projection_fit(api, bundle):
    d = select(api, preset='green_paint', bundle=bundle,
               overrides={'camera':{'mode':'override','projection':'PERSP'}},
               visual_brief={'projection':'perspective'})
    assert d.execution_compatible
    # This preset has no authored projection capability, so adding it is low fit.
    assert d.fit == 'low'
    d = select(api, preset='green_paint', overrides={'camera':{'override_intrinsics':True,'lens_mm':70}})
    assert d.fit == 'high'
    d = select(api, preset='reference_01', bundle=bundle, overrides={'camera':{'mode':'pymol'}},
               visual_brief={'projection':'perspective'})
    assert d.fit == 'high'  # Inherited authored ORTHO is inactive in pymol mode.


@pytest.mark.parametrize('authorized', [False, True])
def test_review_content_intersection_never_authorizes_protein(api, bundle, authorized):
    entry = copy.deepcopy(spec.builtin_definition('pastel').style_doc['styles'][0])
    entry['selection'] = {'polymer':True, 'nucleic':True}
    d = select(api, preset='pastel', bundle=bundle, overrides={'styles':[entry]},
               hard_requirements={'content':['protein']}, allow_low_fit_approximation=authorized)
    assert not d.hard_requirements_satisfied
    assert not d.execution_compatible and d.fit == 'low'
    assert d.resolved_spec_sha256 is None and d.route == 'developer/new-preset'
    assert 'content.protein' in d.unmatched_features


@pytest.mark.parametrize('selections,required,satisfied', [
    ([{'polymer':True, 'query':'polymer and not nucleic'}], ['protein'], True),
    ([{'polymer':True, 'nucleic':True}], ['nucleic'], True),
    ([{'query':'polymer and not nucleic'}, {'nucleic':True}], ['protein','nucleic'], True),
    ([{'polymer':True, 'ligand':True}], ['protein'], False),
    ([{'polymer':True, 'query':'unknown selection'}], ['protein'], False),
    ([{'polymer':True, 'chain':'A'}], ['protein'], False),
    ([{'polymer':True, 'nucleic':False}], ['protein'], True),
])
def test_review_content_intersections_and_style_union(api, bundle, selections, required, satisfied):
    entries = []
    for index, selection in enumerate(selections):
        entry = copy.deepcopy(spec.builtin_definition('pastel').style_doc['styles'][0])
        entry.update(name='content_' + str(index), selection=selection)
        entries.append(entry)
    d = select(api, preset='pastel', bundle=bundle, overrides={'styles':entries},
               hard_requirements={'content':required}, allow_low_fit_approximation=True)
    assert d.hard_requirements_satisfied is satisfied
    assert d.execution_compatible is satisfied
    assert (d.resolved_spec_sha256 is not None) is satisfied


@pytest.mark.parametrize('consumer', ['selection', 'reference'])
@pytest.mark.parametrize('authorized', [False, True])
def test_review_inactive_paint_color_is_validation_error(api, bundle, tmp_path, consumer, authorized):
    edits = {'materials':{'dried_paint':{'base_color':'#123456'}}}
    with pytest.raises(core.StyleValidationError, match='inactive'):
        if consumer == 'selection':
            select(api, preset='green_paint', bundle=bundle, overrides=edits,
                   visual_brief={'palette':['#123456']}, allow_low_fit_approximation=authorized)
        else:
            from PIL import Image
            image = tmp_path/'paint.png'; Image.new('RGB',(10,10)).save(image)
            authoring.reference_brief(image, {'base_preset':'green_paint',
                'representations':['cartoon'], 'material_family':'dried_paint',
                'palette':['#123456'], 'required_capabilities':[], 'overrides':edits},
                bundle=bundle, allow_low_fit_approximation=authorized)


def test_review_paint_descriptors_ignore_legacy_siblings_and_use_active_tint(api, bundle):
    original = spec.builtin_definition('green_paint').style_doc
    legacy = copy.deepcopy(original)
    legacy['materials']['dried_paint']['base_color'] = '#123456'
    core.validate_style_document(legacy)
    assert presets.style_descriptors(legacy) == presets.style_descriptors(original)
    tint = [0.2, 0.3, 0.4]
    edits = {'materials':{'dried_paint':{'paint':{'dark_paint_tint':tint}}}}
    resolved = spec.resolve_options({'preset':'green_paint', 'overrides':edits}, bundle)
    descriptor = 'linear_rgb:[0.2,0.3,0.4]'
    assert descriptor not in presets.style_descriptors(original)['palette']
    assert descriptor in presets.style_descriptors(resolved['style_doc'])['palette']
    d = select(api, preset='green_paint', bundle=bundle, overrides=edits,
               visual_brief={'palette':[descriptor]})
    assert d.fit == 'high' and d.unmatched_features == []
    assert d.resolved_spec_sha256 == resolved['render_spec']['sha256']


def test_review_paint_compositor_glow_remains_active(api, bundle):
    # The existing schema permits disabling glow without an emission_color;
    # enabling it still requires that field and does not change paint dispatch.
    edits = {'materials':{'dried_paint':{'glow':{'strength':0, 'threshold':1, 'size':6}}}}
    d = select(api, preset='green_paint', bundle=bundle, overrides=edits,
               allow_low_fit_approximation=True)
    assert d.execution_compatible and d.resolved_spec_sha256


@pytest.mark.parametrize('preset,material', [('vivid_binding_site','ligand'), ('neon_duplex_hero','pink')])
@pytest.mark.parametrize('consumer', ['selection', 'reference'])
@pytest.mark.parametrize('authorized', [False, True])
def test_review_nonpaint_replaced_base_color_is_inactive(api, bundle, tmp_path, preset, material, consumer, authorized):
    edits = {'materials':{material:{'base_color':'#123456'}}}
    with pytest.raises(core.StyleValidationError, match='inactive'):
        if consumer == 'selection':
            select(api, preset=preset, bundle=bundle, overrides=edits,
                   visual_brief={'palette':['#123456']}, allow_low_fit_approximation=authorized)
        else:
            from PIL import Image
            image = tmp_path/'pigment.png'; Image.new('RGB',(10,10)).save(image)
            authoring.reference_brief(image, {'base_preset':preset, 'representations':['cartoon'],
                'material_family':'clay', 'palette':['#123456'], 'required_capabilities':[], 'overrides':edits},
                bundle=bundle, allow_low_fit_approximation=authorized)


@pytest.mark.parametrize('field,value', [('ao_distance',.7), ('rim_blend',.7), ('rim_power',4),
                                        ('emission_color','#123456')])
def test_review_nonpaint_inactive_branch_controls(api, bundle, field, value):
    with pytest.raises(core.StyleValidationError, match='inactive'):
        select(api, preset='vivid_binding_site', bundle=bundle,
               overrides={'materials':{'pocket':{field:value}}}, allow_low_fit_approximation=True)


@pytest.mark.parametrize('preset,material', [('vivid_binding_site','ligand'), ('neon_duplex_hero','pink')])
def test_review_nonpaint_descriptors_exclude_replaced_base(preset, material):
    style = spec.builtin_definition(preset).style_doc
    before = presets.style_descriptors(style)
    style['materials'][material]['base_color'] = '#123456'
    core.validate_style_document(style)
    assert presets.style_descriptors(style) == before


@pytest.mark.parametrize('fallback', ['shadow_color','highlight_color'])
def test_review_nonpaint_base_color_fallback_descriptors(fallback):
    style = spec.builtin_definition('vivid_binding_site').style_doc
    material = style['materials']['ligand']
    material['base_color'] = '#123456'
    del material[fallback]
    core.validate_style_document(style)
    assert '#123456' in presets.style_descriptors(style)['palette']


@pytest.mark.parametrize('consumer', ['edge_highlight', 'color_follow', 'chain_palette'])
def test_review_nonpaint_secondary_base_consumers_remain_supported(api, bundle, consumer):
    edits = {'materials':{'ligand':{'base_color':'#123456'}}}
    if consumer == 'edge_highlight':
        edits['materials']['ligand']['edge_highlight'] = {'power':2, 'color':'#FFFFFF', 'strength':1}
    elif consumer == 'color_follow':
        edits['color_follow'] = {'enabled':True}
        bundle['view']['color_hints'] = {'atoms':{'A:1:ALA:CA':'#FFFFFF'}}
    else:
        entries = spec.builtin_definition('vivid_binding_site').style_doc['styles']
        entry = next(s for s in entries if s['material'] == 'ligand')
        entry.update(name='chain_a_cartoon', selection={'polymer':True}, color_by_chain=True)
        edits['styles'] = entries
    d = select(api, preset='vivid_binding_site', bundle=bundle, overrides=edits,
               visual_brief={'palette':['#123456']}, allow_low_fit_approximation=True)
    assert d.execution_compatible and d.resolved_spec_sha256
    assert 'visual.palette' not in d.unmatched_features


def test_review_nonpaint_enabled_clay_and_emission_controls(api, bundle):
    edits = {'materials':{'pocket':{'ao_strength':.2, 'ao_distance':.7, 'rim_strength':.1,
        'rim_blend':.7, 'rim_power':4, 'emission_color':'#123456', 'emission_strength':1}}}
    d = select(api, preset='vivid_binding_site', bundle=bundle, overrides=edits)
    assert d.fit == 'high' and d.execution_compatible and d.resolved_spec_sha256


@pytest.mark.parametrize('authorized', [False, True])
@pytest.mark.parametrize('hints', [None, {}, {'classes':{'polymer':'#FFFFFF'}},
                                  {'chain':{'Z':'#FFFFFF'}}, {'classes':{'ligand':'invalid'}}])
def test_review_color_follow_requires_applicable_bundle_hints(api, bundle, authorized, hints):
    if hints is not None:
        bundle['view']['color_hints'] = hints
    edits = {'color_follow':{'enabled':True}, 'materials':{'ligand':{'base_color':'#123456'}}}
    before = copy.deepcopy(bundle)
    with pytest.raises(core.StyleValidationError, match='inactive'):
        select(api, preset='vivid_binding_site', bundle=bundle, overrides=edits,
               visual_brief={'palette':['#123456']}, allow_low_fit_approximation=authorized)
    assert bundle == before


@pytest.mark.parametrize('source', ['view', 'bundle'])
@pytest.mark.parametrize('kind', ['atoms', 'classes', 'chain'])
def test_review_color_follow_supported_bundle_hint_paths(api, bundle, source, kind):
    edits = {'color_follow':{'enabled':True}, 'materials':{'ligand':{'base_color':'#123456'}}}
    hints = {kind: {'atoms':{'A:1:ALA:CA':'#FFFFFF'}, 'classes':{'ligand':'#FFFFFF'},
                    'chain':{'a':'#FFFFFF'}}[kind]}
    if kind == 'chain':
        entries = spec.builtin_definition('vivid_binding_site').style_doc['styles']
        next(s for s in entries if s['material'] == 'ligand')['selection']['chain'] = 'A'
        edits['styles'] = entries
    (bundle['view'] if source == 'view' else bundle)['color_hints'] = hints
    before = copy.deepcopy(bundle)
    d = select(api, preset='vivid_binding_site', bundle=bundle, overrides=edits,
               visual_brief={'palette':['#123456']}, allow_low_fit_approximation=True)
    assert d.execution_compatible and 'visual.palette' not in d.unmatched_features
    assert d.resolved_spec_sha256 == spec.resolve_options(
        {'preset':'vivid_binding_site', 'overrides':edits}, bundle)['render_spec']['sha256']
    assert bundle == before


@pytest.mark.parametrize('authorized', [False, True])
def test_review_color_follow_top_level_hints_preserve_payload_precedence(api, bundle, authorized):
    bundle['view']['color_hints'] = {'atoms':{'A:1:ALA:CA':'#FFFFFF'}}
    bundle['color_hints'] = {}
    with pytest.raises(core.StyleValidationError, match='inactive'):
        select(api, preset='vivid_binding_site', bundle=bundle,
               overrides={'color_follow':{'enabled':True}, 'materials':{'ligand':{'base_color':'#123456'}}},
               allow_low_fit_approximation=authorized)


@pytest.mark.parametrize('authorized', [False, True])
def test_review_color_follow_missing_hints_rejected_through_reference(bundle, tmp_path, authorized):
    from PIL import Image
    image = tmp_path/'pigment.png'; Image.new('RGB',(10,10)).save(image)
    with pytest.raises(core.StyleValidationError, match='inactive'):
        authoring.reference_brief(image, {'base_preset':'vivid_binding_site',
            'representations':['cartoon'], 'material_family':'clay', 'palette':['#123456'],
            'required_capabilities':[], 'overrides':{'color_follow':{'enabled':True},
                'materials':{'ligand':{'base_color':'#123456'}}}},
            bundle=bundle, allow_low_fit_approximation=authorized)


@pytest.mark.parametrize('atom_hints', [False, True])
def test_review_color_follow_descriptors_respect_known_context(bundle, atom_hints):
    style = spec.builtin_definition('vivid_binding_site').style_doc
    style['color_follow'] = {'enabled':True}
    style['materials']['ligand']['base_color'] = '#123456'
    if atom_hints:
        bundle['view']['color_hints'] = {'atoms':{'A:1:ALA:CA':'#FFFFFF'}}
    before = copy.deepcopy(style)
    assert ('#123456' in presets.style_descriptors(style, bundle=bundle)['palette']) is atom_hints
    assert '#123456' in presets.style_descriptors(style)['palette']  # static tendency only
    assert style == before


@pytest.mark.parametrize('authorized', [False, True])
def test_review_color_follow_full_mix_cannot_activate_base(api, bundle, authorized):
    bundle['view']['color_hints'] = {'atoms':{'A:1:ALA:CA':'#FFFFFF'}}
    with pytest.raises(core.StyleValidationError, match='inactive'):
        select(api, preset='vivid_binding_site', bundle=bundle,
               overrides={'color_follow':{'enabled':True, 'mix':1},
                          'materials':{'ligand':{'base_color':'#123456'}}},
               allow_low_fit_approximation=authorized)
