"""Deterministic preset decisions, with no vision, admission or execution side effects.

Visual descriptors are exact normalized values (see docs/preset-selection.md).
Only explicit hard output requirements generate resolver options. A spec hash
identifies the resolver's JSON in a supplied context, never live readiness.
"""
from __future__ import annotations

import copy
from collections.abc import Collection
from dataclasses import asdict, dataclass, field
import json
import math
import re

from . import core, presets, spec


class SelectionError(ValueError):
    """Invalid identity/revision or no policy-permitted candidate."""


# Exact historical observation aliases, not prose/substring interpretation.
DESCRIPTOR_ALIASES = {
    'material_family': {'dried paint': 'dried_paint'},
    'projection': {'orthographic':'ORTHO', 'ortho':'ORTHO', 'perspective':'PERSP', 'persp':'PERSP'},
    'background': {'black': '#000000', 'white': '#FFFFFF', 'pale blue gray': '#C6D6DF'},
    'lighting': {'soft highlights': 'area', 'soft': 'area'},
    'edges': {'smooth ribbons and round sticks': 'smooth', 'rounded ribbons': 'rounded'},
    'composition': {'one compact protein and a highlighted ligand': 'structure',
                    'selective focus within a compact protein; other depths are soft': 'depth_of_field'},
    'ligand_treatment': {'pink sticks': 'sticks', 'native sticks': 'sticks'},
}
_TOKEN = re.compile(r'[a-z][a-z0-9_.-]{0,127}')
_SECTIONS = frozenset(core.REQUIRED_STYLE_TOP_KEYS) | frozenset(core.STYLE_TOP_LEVEL_OPTIONAL_KEYS)


def _json_copy(value):
    def check(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if isinstance(item, dict) and all(type(k) is str for k in item):
            for v in item.values():
                check(v)
            return
        if isinstance(item, list):
            for v in item:
                check(v)
            return
        raise ValueError('Expected finite JSON values with string object keys')
    check(value)
    return copy.deepcopy(value)


def _strings(value, name, *, tokens=False):
    if isinstance(value, (str, bytes, dict)) or not isinstance(value, Collection):
        raise ValueError(name + ' must be a collection of strings')
    if not all(isinstance(v, str) and v and (not tokens or _TOKEN.fullmatch(v)) for v in value):
        raise ValueError(name + ' must contain valid strings')
    return tuple(sorted(set(value)))


def _boolean(value, name, *, optional=False):
    if not (optional and value is None) and type(value) is not bool:
        raise ValueError(name + ' must be a boolean')


def _positive(value, name):
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError(name + ' must be a positive integer')


def _descriptor(name, value):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + ' must be nonempty text')
    value = value.strip().lower()
    if re.fullmatch('#[0-9a-f]{6}', value):
        return value.upper()
    return DESCRIPTOR_ALIASES.get(name, {}).get(value, value)


def _execution_permitted(*, hard_requirements_satisfied, execution_compatible,
                         fit, approximation_authorized):
    """One route/hash gate: authorization relaxes visual fit only."""
    return (hard_requirements_satisfied and execution_compatible
            and (fit in {'high', 'medium'} or approximation_authorized))


class _Serializable:
    def to_dict(self):
        # JSON-compatible lists and a fresh copy on every export.
        return json.loads(self.to_json())

    def to_json(self):
        return json.dumps(asdict(self), sort_keys=True, separators=(',', ':'), allow_nan=False)


@dataclass(frozen=True)
class HardRequirements(_Serializable):
    content: tuple[str, ...] = ()
    representations: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    transparent_background: bool | None = None
    save_blend: bool | None = None
    width: int | None = None
    height: int | None = None
    camera_policy: str | None = None

    def __post_init__(self):
        for key in ('content', 'representations', 'required_capabilities'):
            object.__setattr__(self, key, _strings(getattr(self, key), key, tokens=True))
        for key in ('transparent_background', 'save_blend'):
            _boolean(getattr(self, key), key, optional=True)
        for key in ('width', 'height'):
            _positive(getattr(self, key), key)
        if self.camera_policy not in (None, 'preserve_input', 'authored'):
            raise ValueError('camera_policy must be preserve_input or authored')


@dataclass(frozen=True)
class VisualBrief(_Serializable):
    representations: tuple[str, ...] = ()
    material_family: str | None = None
    palette: tuple[str, ...] = ()
    background: str | None = None
    lighting: str | None = None
    edges: str | None = None
    composition: str | None = None
    projection: str | None = None
    ligand_treatment: str | None = None

    def __post_init__(self):
        object.__setattr__(self, 'representations', _strings(self.representations, 'representations', tokens=True))
        palette = _strings(self.palette, 'palette')
        object.__setattr__(self, 'palette', tuple(sorted({_descriptor('palette', v) for v in palette})))
        for key in ('material_family', 'background', 'lighting', 'edges', 'composition', 'projection', 'ligand_treatment'):
            object.__setattr__(self, key, _descriptor(key, getattr(self, key)))


def _record(value, cls):
    if isinstance(value, cls):
        return cls(**value.to_dict())
    if not isinstance(value, dict):
        raise ValueError(cls.__name__ + ' must be an object')
    return cls(**value)


@dataclass(frozen=True)
class SelectionRequest(_Serializable):
    preset: str | None = None
    revision: str | int | None = None
    hard_requirements: HardRequirements = field(default_factory=HardRequirements)
    visual_brief: VisualBrief = field(default_factory=VisualBrief)
    overrides: dict = field(default_factory=dict)
    allow_low_fit_approximation: bool = False

    def __post_init__(self):
        if self.preset is not None and (not isinstance(self.preset, str) or not self.preset):
            raise ValueError('preset must be nonempty text')
        if self.revision is not None:
            if type(self.revision) is int and self.revision > 0:
                object.__setattr__(self, 'revision', str(self.revision))
            elif not isinstance(self.revision, str) or not re.fullmatch('[1-9][0-9]*', self.revision):
                raise ValueError('revision must be a positive integer or its string form')
        object.__setattr__(self, 'hard_requirements', _record(self.hard_requirements, HardRequirements))
        object.__setattr__(self, 'visual_brief', _record(self.visual_brief, VisualBrief))
        _boolean(self.allow_low_fit_approximation, 'allow_low_fit_approximation')
        if not isinstance(self.overrides, dict):
            raise ValueError('overrides must be an object')
        object.__setattr__(self, 'overrides', _json_copy(self.overrides))
        output = self.overrides.get('output', {})
        if isinstance(output, dict) and {'filename', 'blend_filename', 'format'} & output.keys():
            raise ValueError('Output filenames and format are transport-owned, not selection overrides')


@dataclass(frozen=True)
class SelectionPolicy(_Serializable):
    """Optional stricter caller policy; defaults add no admission allowlist."""
    allowed_presets: tuple[str, ...] | None = None
    allowed_override_sections: tuple[str, ...] | None = None
    allowed_capabilities: tuple[str, ...] | None = None
    public: bool = False
    allow_scene: bool = True
    max_width: int | None = None
    max_height: int | None = None
    max_samples: int | None = None

    def __post_init__(self):
        for key in ('public', 'allow_scene'):
            _boolean(getattr(self, key), key)
        for key in ('max_width', 'max_height', 'max_samples'):
            _positive(getattr(self, key), key)
        if self.allowed_presets is not None:
            names = _strings(self.allowed_presets, 'allowed_presets')
            object.__setattr__(self, 'allowed_presets', tuple(sorted({'builtin/' + presets.preset_id(v) for v in names})))
        if self.allowed_override_sections is not None:
            sections = _strings(self.allowed_override_sections, 'allowed_override_sections')
            if set(sections) - _SECTIONS:
                raise ValueError('Unknown allowed_override_sections')
            object.__setattr__(self, 'allowed_override_sections', sections)
        if self.allowed_capabilities is not None:
            object.__setattr__(self, 'allowed_capabilities', _strings(self.allowed_capabilities, 'allowed_capabilities', tokens=True))


@dataclass(frozen=True)
class PresetDecision(_Serializable):
    format_version: int
    catalog_digest: str
    preset_id: str
    preset_revision: str
    fit: str
    reasons: list[str]
    overrides: dict
    unmatched_features: list[str]
    unsupported_requirements: list[str]
    new_preset_recommended: bool
    route: str
    resolved_spec_sha256: str | None
    hard_requirements_satisfied: bool
    execution_compatible: bool
    approximation_authorized: bool

    def __post_init__(self):
        if type(self.format_version) is not int or self.format_version != 1:
            raise ValueError('Unsupported decision format')
        if self.fit not in ('high', 'medium', 'low') or self.route not in ('preset-overrides', 'developer/new-preset'):
            raise ValueError('Invalid decision fit or route')
        for key in ('catalog_digest', 'resolved_spec_sha256'):
            value = getattr(self, key)
            if key == 'resolved_spec_sha256' and value is None:
                continue
            if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
                raise ValueError('Invalid decision digest')
        if not isinstance(self.preset_revision, str) or not re.fullmatch('[1-9][0-9]*', self.preset_revision):
            raise ValueError('Invalid public revision')
        if self.preset_id != 'builtin/' + presets.preset_id(self.preset_id):
            raise ValueError('Decision must use a qualified canonical ID')
        for key in ('new_preset_recommended', 'hard_requirements_satisfied', 'execution_compatible', 'approximation_authorized'):
            _boolean(getattr(self, key), key)
        if self.execution_compatible and not self.hard_requirements_satisfied:
            raise ValueError('Execution compatibility requires hard requirements')
        if self.fit != 'low' and not self.execution_compatible:
            raise ValueError('High and medium fit require execution compatibility')
        executable = _execution_permitted(hard_requirements_satisfied=self.hard_requirements_satisfied,
            execution_compatible=self.execution_compatible, fit=self.fit,
            approximation_authorized=self.approximation_authorized)
        if self.route != ('preset-overrides' if executable else 'developer/new-preset'):
            raise ValueError('Decision route does not satisfy compatibility gates')
        if self.resolved_spec_sha256 is not None and not executable:
            raise ValueError('Non-executable decision cannot have a resolved hash')
        if self.new_preset_recommended != (self.fit == 'low'):
            raise ValueError('Low fit requires a new preset recommendation')
        for key in ('reasons', 'unmatched_features', 'unsupported_requirements'):
            values = _strings(getattr(self, key), key)
            object.__setattr__(self, key, list(values))
        if not isinstance(self.overrides, dict):
            raise ValueError('Decision overrides must be an object')
        object.__setattr__(self, 'overrides', _json_copy(self.overrides))


def _options(request, identifier):
    options = {'preset': identifier, 'overrides': request.overrides}
    hard = request.hard_requirements
    for key in ('transparent_background', 'save_blend', 'width', 'height'):
        value = getattr(hard, key)
        if value is not None:
            options[key] = value
    if hard.camera_policy is not None:
        options['preserve_view'] = hard.camera_policy == 'preserve_input'
    return options


def _differences(base, value, path=()):
    if isinstance(base, dict) and isinstance(value, dict):
        result = []
        for key in sorted(base.keys() | value.keys()):
            if key not in base or key not in value:
                if isinstance(base.get(key, {}), dict) and isinstance(value.get(key, {}), dict):
                    result.extend(_differences(base.get(key, {}), value.get(key, {}), path + (key,)))
                else:
                    result.append(path + (key,))
            else:
                result.extend(_differences(base[key], value[key], path + (key,)))
        return result
    return [] if base == value else [path]


def _models(style):
    features = {'paint', 'frosted', 'color_gradient', 'color_attribute', 'glow', 'edge_highlight'}
    return {name: set(presets.active_material_controls(style['materials'][name], style=style, material_name=name)) & features
            for name in {s['material'] for s in style['styles']}}


def _extent(base, proposed, authored):
    paths = _differences(base, proposed)
    changed_materials = {p[1] for p in paths if p[0] == 'materials'}
    # Selecting an already-authored camera stays within the base's declared
    # envelope even when the default saved-view resolution omits its controls.
    new_capabilities = set(presets.required_capabilities(proposed)) - set(presets.required_capabilities(authored))
    extensive = bool(new_capabilities) or _models(base) != _models(proposed) or len(changed_materials) > 2
    for path in paths:
        if path[0] == 'styles':
            extensive = True
        if path[0] == 'geometry' and (len(path) < 2 or path[1] not in {'secondary_structure_palette', 'element_palette'}):
            extensive = True
        if path[0] == 'lights' and [s['type'] for s in base['lights']] != [s['type'] for s in proposed['lights']]:
            extensive = True
    return extensive, len(paths)


def _selection_content(selection):
    """Conservative class intersection matching _selection_indices_from_cfg.

    The renderer's polymer mask includes protein and nucleic content. False
    flags add no filter. Only exact existing class queries are understood here;
    unknown queries, chain/residue/proximity filters cannot establish coverage
    without atom inspection. This is not a general selection-query parser.
    """
    flags = {'polymer': {'protein', 'nucleic'}, 'nucleic': {'nucleic'}, 'ligand': {'ligand'}}
    queries = {**flags, 'polymer and not nucleic': {'protein'}}
    if set(selection) - (flags.keys() | {'query'}):
        return set()
    constraints = [classes for flag, classes in flags.items() if selection.get(flag) is True]
    query = selection.get('query', '').strip().lower()
    if query:
        if query not in queries:
            return set()
        constraints.append(queries[query])
    return set.intersection(*(set(classes) for classes in constraints)) if constraints else set()


def _content(row, original, proposed):
    # Unchanged shipped selections retain their documented coverage. Within a
    # replacement entry conditions intersect; separate rendered entries union.
    if [s['selection'] for s in original['styles']] == [s['selection'] for s in proposed['styles']]:
        return set(row['supported_content'])
    return set().union(*(_selection_content(s['selection']) for s in proposed['styles']))


def _effective_camera_policy(camera):
    """Classify the effective camera after normal resolver precedence.

    Input pose with altered intrinsics/framing/scale is not full preservation
    or an authored camera; neither hard policy can be asserted for that mix.
    Inherited mode-inactive fields do not affect this classification.
    """
    if camera.get('mode', 'pymol') == 'override':
        return 'authored'
    if (camera.get('world_scale', .01) == .01 and camera.get('preserve_framing', False)
            and not camera.get('override_intrinsics', False)):
        return 'preserve_input'
    return None


def _validate_active_edits(overrides, proposed, *, bundle=None):
    """Reject explicit controls whose existing renderer mode has no consumer.

    Inherited authored defaults remain valid resolver inputs. This check concerns
    caller edits only; it never switches a mode or changes resolver precedence.
    """
    camera = proposed['camera']
    camera_edits = set(overrides.get('camera', {}))
    if camera.get('mode', 'pymol') == 'override':
        inactive = set()
        if camera.get('direction_source', 'preset') != 'pymol':
            inactive.update({'yaw_offset_deg', 'pitch_offset_deg'})
        if not camera.get('fit', False):
            inactive.add('fit_margin')
        inactive &= camera_edits
    else:
        active = {'mode', 'world_scale', 'preserve_framing', 'override_intrinsics'}
        if camera.get('override_intrinsics', False):
            active.update({'lens_mm', 'shift_x', 'shift_y'})
        inactive = camera_edits - active
    if inactive:
        raise ValueError('Camera overrides are inactive in the selected mode: ' + ', '.join(sorted(inactive)))
    world_edits = set(overrides.get('world', {}))
    inactive = ({'color'} if proposed['world'].get('background_type', 'solid') == 'gradient'
                else {'top_color', 'bottom_color', 'gradient_direction'}) & world_edits
    if inactive:
        raise ValueError('Background overrides are inactive in the selected mode: ' + ', '.join(sorted(inactive)))
    for name, edits in overrides.get('materials', {}).items():
        active = presets.active_material_controls(proposed['materials'][name], style=proposed,
                                                 material_name=name, bundle=bundle)
        inactive = set(edits) - set(active)
        if inactive:
            raise core.StyleValidationError('Material overrides are inactive in the selected model: '
                                            + ', '.join(sorted(inactive)))


def _evaluate(request, row, available, policy, context):
    options = _options(request, row['id'])
    original = spec.builtin_definition(row['id']).style_doc
    # Always validate before policy/resource normalization or ranking can mask an
    # invalid field. Resolver errors are validation failures, never low-fit edits.
    resolved = spec.resolve_options(options, context)
    proposed = resolved['style_doc']
    _validate_active_edits(request.overrides, proposed, bundle=context or None)
    used = {s['material'] for s in proposed['styles']}
    material_edits = request.overrides.get('materials', {})
    if set(material_edits) - used:
        raise ValueError('Overrides must name materials used by the selected base preset')
    baseline = spec.resolve_options({'preset':row['id']}, context)['style_doc']
    paths = _differences(baseline, proposed)
    policy_missing = set()
    if policy.allowed_override_sections is not None and {p[0] for p in paths} - set(policy.allowed_override_sections):
        policy_missing.add('policy.override_sections')
    if policy.public:
        try:
            resolved = spec.resolve_options(options, context, public=True)
            proposed = resolved['style_doc']
        except ValueError:
            policy_missing.add('policy.public')
    for key in ('width', 'height', 'samples'):
        ceiling = getattr(policy, 'max_' + key)
        if ceiling is not None and proposed['output'][key] > ceiling:
            policy_missing.add('policy.' + key)
    if not policy.allow_scene and proposed['output']['save_blend']:
        policy_missing.add('policy.save_blend')
    required = set(presets.required_capabilities(proposed))
    hard = request.hard_requirements
    missing = set(hard.required_capabilities) - required
    content_missing = set(hard.content) - _content(row, original, proposed)
    representation_missing = set(hard.representations) - {s['style'] for s in proposed['styles']}
    missing.update('content.' + v for v in content_missing)
    missing.update('representation.' + v for v in representation_missing)
    if hard.camera_policy is not None and hard.camera_policy != _effective_camera_policy(proposed['camera']):
        missing.add('camera.' + hard.camera_policy)
    capability_missing = (required | set(hard.required_capabilities)) - available
    if policy.allowed_capabilities is not None and required - set(policy.allowed_capabilities):
        policy_missing.add('policy.capabilities')
    compatible = not (missing or capability_missing or policy_missing)
    descriptors = presets.style_descriptors(proposed, bundle=context or None)
    visual = request.visual_brief
    visual_reps_missing = set(visual.representations) - set(descriptors['representations'])
    unmatched = set(missing)
    if visual_reps_missing:
        unmatched.add('visual.representations')
    family_mismatch = visual.material_family is not None and visual.material_family != row['material_family']
    if family_mismatch:
        unmatched.add('visual.material_family')
    soft = set()
    for key in ('background', 'lighting', 'edges', 'composition', 'ligand_treatment'):
        value = getattr(visual, key)
        if value is not None and value not in descriptors[key]:
            soft.add('visual.' + key)
    if visual.projection is not None:
        projection = (proposed['camera'].get('projection')
                      if proposed['camera'].get('mode') == 'override' else None)
        if projection is None and type(context.get('view', {}).get('orthoscopic')) is bool:
            projection = 'ORTHO' if context['view']['orthoscopic'] else 'PERSP'
        if visual.projection != projection:
            soft.add('visual.projection')
    if set(visual.palette) - set(descriptors['palette']):
        soft.add('visual.palette')
    unmatched.update(soft)
    extensive, edits = _extent(baseline, proposed, original)
    if extensive:
        unmatched.add('design.extensive_replacement')
    unmatched.update(capability_missing)
    unmatched.update(policy_missing)
    low = not compatible or bool(visual_reps_missing) or family_mismatch or extensive
    fit = 'low' if low else 'medium' if soft else 'high'
    # Compatibility filters precede a lexicographic rank; never a weighted sum.
    rank = (not compatible,
            len(content_missing) + len(representation_missing) + len(visual_reps_missing),
            (int(family_mismatch), int('visual.edges' in soft), int('visual.ligand_treatment' in soft)),
            sum('visual.' + key in soft for key in ('palette', 'background', 'lighting')),
            sum('visual.' + key in soft for key in ('projection', 'composition')), edits,
            row['id'] != presets.DEFAULT_PRESET, row['qualified_id'])
    return {'row':row, 'fit':fit, 'unmatched':sorted(unmatched),
            'unsupported':sorted(capability_missing | policy_missing), 'hard':not missing,
            'compatible':compatible, 'resolved':resolved, 'rank':rank}


def select_preset(request: SelectionRequest, *, available_capabilities: Collection[str],
                  policy: SelectionPolicy, bundle: dict | None = None) -> PresetDecision:
    """Select among fourteen installed built-ins and optionally resolve real context.

    User/project storage continues through authoring.resolve_authored; it is not
    an automatic candidate source in D1. Callers supply capability declarations.
    """
    if not isinstance(request, SelectionRequest) or not isinstance(policy, SelectionPolicy):
        raise TypeError('select_preset requires SelectionRequest and SelectionPolicy')
    request = SelectionRequest(**request.to_dict())
    policy = SelectionPolicy(**policy.to_dict())
    available = set(_strings(available_capabilities, 'available_capabilities', tokens=True))
    context = {} if bundle is None else _json_copy(bundle)
    if bundle is not None:
        if not isinstance(context, dict):
            raise ValueError('bundle must be an object')
        core.validate_payload_document(context)
    snapshot = presets.compact_catalog()
    rows = snapshot['entries']
    if request.preset is not None:
        try:
            identifier = presets.preset_id(request.preset)
        except ValueError as exc:
            raise SelectionError('Unknown explicit preset') from exc
        rows = [row for row in rows if row['id'] == identifier]
        if request.revision is not None and request.revision != rows[0]['revision']:
            raise SelectionError('Explicit preset revision mismatch')
    elif request.revision is not None:
        raise SelectionError('A revision requires an explicit preset')
    if policy.allowed_presets is not None:
        rows = [row for row in rows if row['qualified_id'] in policy.allowed_presets]
    if not rows:
        raise SelectionError('No presets permitted by selection policy')
    candidates, errors = [], []
    for row in rows:
        try:
            candidates.append(_evaluate(request, row, available, policy, context))
        except (ValueError, core.StyleValidationError) as exc:
            errors.append(exc)
    if not candidates:
        # Deterministic validation failure, including an override referring to no
        # used material in any candidate. Explicit presets are never substituted.
        raise errors[0]
    chosen = min(candidates, key=lambda candidate: candidate['rank'])
    fit, row = chosen['fit'], chosen['row']
    executable = _execution_permitted(hard_requirements_satisfied=chosen['hard'],
        execution_compatible=chosen['compatible'], fit=fit,
        approximation_authorized=request.allow_low_fit_approximation)
    reasons = ['Explicit preset retained' if request.preset else 'Ranked compatible representations, material, palette, lighting and composition']
    reasons.append({'high':'Existing design with small supported edits',
                    'medium':'Approximation: some requested visual properties are unmatched',
                    'low':'Closest base; a dedicated preset is recommended'}[fit])
    if fit == 'low' and request.allow_low_fit_approximation:
        reasons.append('Low-fit approximation explicitly requested; compatibility gates still apply')
    return PresetDecision(format_version=1, catalog_digest=snapshot['digest'],
        preset_id=row['qualified_id'], preset_revision=str(row['revision']), fit=fit,
        reasons=reasons, overrides=request.overrides, unmatched_features=chosen['unmatched'],
        unsupported_requirements=chosen['unsupported'], new_preset_recommended=fit == 'low',
        route='preset-overrides' if executable else 'developer/new-preset',
        resolved_spec_sha256=chosen['resolved']['render_spec']['sha256'] if executable and bundle is not None else None,
        hard_requirements_satisfied=chosen['hard'], execution_compatible=chosen['compatible'],
        approximation_authorized=request.allow_low_fit_approximation)
