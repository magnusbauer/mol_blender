"""D2 structured consumer transport over D1; no file I/O or execution.

The request key binds the first molecular input. Recovery compares normalized
render intent, never a mutable attachment URL or a reopened local file.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from . import core, engine, presets, selection
from .jobs import JobError
from . import consumer_errors, consumer_records, consumer_transport

SELECTION_FIELDS = ('preset', 'revision', 'hard_requirements', 'visual_brief',
                    'overrides', 'allow_low_fit_approximation')
CONTROLS = ('width', 'height', 'samples', 'transparent_background', 'save_blend', 'color_follow', 'rough', 'animation', 'duration', 'fps', 'rock_angle', 'gif')
KEY_PATTERN = r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}'


def request_key(value):
    if (not isinstance(value, str) or not re.fullmatch(KEY_PATTERN, value)
            or value.startswith(('sk-', 'ghp_', 'github_pat_', 'Bearer_'))):
        raise JobError('request_key must be an opaque 1–128 character identifier using letters, digits, _ or -')
    return value


def _safe_strings(value):
    """Exclude transport/reference data, without interpreting visual prose."""
    if isinstance(value, str):
        if (len(value) > 512 or any(ord(c) < 32 for c in value)
                or any(c in value for c in ('/', '\\', '?', '@', '='))):
            raise JobError('Structured observations and edits cannot contain paths, URLs or reference metadata')
    elif isinstance(value, dict):
        for key, item in value.items():
            _safe_strings(key)
            _safe_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _safe_strings(item)


def normalize_request(value, *, trusted=False):
    """Strict canonical identity, with no selection or capability lookup."""
    try:
        if not isinstance(value, dict):
            raise ValueError('Consumer request must be an object')
        allowed = {'format_version', 'request_key', 'rerender', *SELECTION_FIELDS, *CONTROLS}
        if trusted:
            allowed.add('style_doc')
        if set(value) - allowed:
            raise ValueError('Unsupported consumer request fields')
        consumer_transport.bounded_json(value)
        value = consumer_transport.descriptors(selection._json_copy(value))
        key = request_key(value.get('request_key'))
        if type(value.get('rerender', False)) is not bool:
            raise ValueError('rerender must be a boolean')
        version = value.get('format_version', 1)
        if type(version) is not int or version != 1:
            raise ValueError('Unsupported consumer request format')
        if value.get('revision') is not None and (type(value['revision']) not in (str, int)
                or not re.fullmatch(r'[1-9][0-9]{0,8}', str(value['revision']))):
            raise ValueError('Invalid bounded revision')
        selected = selection.SelectionRequest(**{k: value[k] for k in SELECTION_FIELDS if k in value})
        canonical = selected.to_dict()
        if selected.preset is not None:
            canonical['preset'] = 'builtin/' + presets.preset_id(selected.preset)
            canonical['revision'] = selected.revision or '1'
        elif selected.revision is not None:
            raise ValueError('A revision requires a preset')
        _safe_strings(canonical['overrides'])
        controls = {k: value[k] for k in CONTROLS if k in value and value[k] is not None}
        from .spec import _check_options
        _check_options(controls)
        from .animation import CONTROLS as MOTION_CONTROLS, resolve as resolve_motion
        motion = {k: controls[k] for k in MOTION_CONTROLS if k in controls}
        if motion:
            if motion.get('animation', 'none') != 'none':
                # Small interactive movies by default; explicit quality wins.
                controls.setdefault('width', canonical['hard_requirements']['width'] or canonical['overrides'].get('output', {}).get('width', 960))
                controls.setdefault('height', canonical['hard_requirements']['height'] or canonical['overrides'].get('output', {}).get('height', 540))
                controls.setdefault('samples', canonical['overrides'].get('output', {}).get('samples', 48))
                motion.setdefault('fps', 24)
                motion.setdefault('duration', 6 if motion['animation'] == '360' else 3)
                motion.setdefault('gif', True)
            resolve_motion(motion, {'width': controls.get('width', 960), 'height': controls.get('height', 540)})
        hard, edits = canonical['hard_requirements'], canonical['overrides']
        # Hard requirements and explicit output controls cannot disagree. The
        # resolver still owns precedence between convenience controls and JSON.
        for name in ('width', 'height', 'save_blend', 'transparent_background'):
            if name in controls:
                if hard[name] is not None and hard[name] != controls[name]:
                    raise ValueError('Output controls conflict with hard requirements')
                hard[name] = controls[name]
        if hard['save_blend'] is None:
            hard['save_blend'] = edits.get('output', {}).get('save_blend', False)
        if 'samples' in controls:
            edits.setdefault('output', {})['samples'] = controls['samples']
        if 'color_follow' in controls:
            existing = edits.get('color_follow', {}).get('enabled')
            if existing is not None and existing != controls['color_follow']:
                raise ValueError('color_follow conflicts with explicit overrides')
            edits.setdefault('color_follow', {})['enabled'] = controls['color_follow']
        rough = controls.get('rough', False)
        # Existing rough mode halves resolution. Never silently weaken an
        # explicit hard resolution; users can instead request fewer samples.
        if rough and (hard['width'] is not None or hard['height'] is not None):
            raise ValueError('rough cannot be combined with hard output dimensions')
        canonical = selection.SelectionRequest(**canonical).to_dict()
        result = {'format_version': 1, 'request_key': key, **canonical, 'rough': rough}
        result.update(motion)
        # Preserve existing default request fingerprints across the upgrade.
        if value.get('rerender', False):
            result['rerender'] = True
        if 'style_doc' in value:
            if (selected.preset is not None or selected.revision is not None
                    or selected.visual_brief != selection.VisualBrief()
                    or selected.allow_low_fit_approximation
                    or any(getattr(selected.hard_requirements, k) for k in
                           ('content', 'representations', 'required_capabilities', 'camera_policy'))):
                raise ValueError('Custom style cannot be combined with preset selection requirements')
            _safe_strings(value['style_doc'])
            if not isinstance(value['style_doc'], dict):
                raise ValueError('style_doc must be a JSON object')
            result['style_doc'] = value['style_doc']
        consumer_transport.bounded_json(result)
        return result
    except (ValueError, TypeError, KeyError, AttributeError, core.RunnerError) as exc:
        if isinstance(exc, JobError):
            raise
        raise JobError('The structured render request is invalid.', 'selection_invalid', 422) from exc


def identity(request):
    intent = {k: v for k, v in request.items() if k != 'request_key'}
    return hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def server_policy(public, stricter=None):
    data = stricter.to_dict() if stricter is not None else {}
    data['public'] = public or data.get('public', False)
    if data['public']:
        for key, limit in (('max_width', 4096), ('max_height', 4096), ('max_samples', 512)):
            data[key] = min(data.get(key) or limit, limit)
    return selection.SelectionPolicy(**data)


def adapter_public(policy):
    """A consumer transport can restrict server trust, never grant it."""
    if policy is None:
        return False  # Existing direct consumer clients retain their server policy.
    if (not isinstance(policy, dict) or set(policy) != {'format_version', 'public'}
            or type(policy['format_version']) is not int or policy['format_version'] != 1
            or type(policy['public']) is not bool):
        raise JobError('Invalid consumer policy envelope.', 'selection_invalid', 422)
    return policy['public']


def trusted_profile(profile):
    if profile in {'local', 'apptainer'}:
        return True
    from .config import load_config
    return load_config().get('profiles', {}).get(profile, {}).get('kind') == 'hpc'


def safe_decision(decision):
    if decision is None:
        return None
    # Edits remain in the private, short-lived preparation snapshot. Normal
    # status/recovery needs only safe selection facts, never style/user text.
    return {k: v for k, v in decision.to_dict().items() if k not in {'overrides', 'resolved_spec_sha256'}}


def metadata(decision, *, request=None, fingerprint=None, previous=None, custom=False,
             phase='preselection', backend_started=False, error=None):
    summary = safe_decision(decision)
    if previous is not None:
        previous = validate_metadata(previous)
        style_identity = previous['style_identity']
        first = previous['preselection_decision']
        fingerprint = previous['request_fingerprint']
        custom = previous['style_source'] == 'custom'
    else:
        if request is None or fingerprint is None:
            raise JobError('Consumer metadata requires its canonical request identity')
        style_identity = ({'custom_style_sha256': hashlib.sha256(json.dumps(request['style_doc'], sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()} if custom else
            {key: summary[key] for key in ('preset_id', 'preset_revision', 'catalog_digest')})
        first = summary
    return validate_metadata({'format_version': 1, 'style_source': 'custom' if custom else 'builtin',
        'style_identity': style_identity, 'preselection_decision': first, 'request_fingerprint': fingerprint,
        'selection_phase': phase, 'backend_render_started': backend_started,
        'preset_decision': summary, 'selection_error': error,
        'error_category': consumer_errors.category(error) if error else None})


def validate_metadata(value):
    return consumer_records.validate_metadata(value)


def validate_transition(previous, proposed):
    return consumer_records.validate_transition(previous, proposed)


def executable(decision):
    return decision is None or decision.route == 'preset-overrides'


def render_options(request):
    options = {'overrides': copy.deepcopy(request['overrides']), 'rough': request['rough']}
    options.update({'style_doc': copy.deepcopy(request['style_doc'])} if 'style_doc' in request
                   else {'preset': presets.preset_id(request['preset'])})
    from .animation import CONTROLS as MOTION_CONTROLS
    options.update({k: request[k] for k in MOTION_CONTROLS if k in request})
    hard = request['hard_requirements']
    for key in ('width', 'height', 'save_blend', 'transparent_background'):
        if hard[key] is not None:
            options[key] = hard[key]
    if hard['camera_policy'] is not None:
        options['preserve_view'] = hard['camera_policy'] == 'preserve_input'
    return options


def preselect(request, policy, *, bundle=None):
    """D1 owns ranking and fit. Phase B receives the frozen explicit identity."""
    if set(request['hard_requirements']['required_capabilities']) - set(engine.CAPABILITIES):
        raise JobError('A required capability is unavailable', 'capability_unavailable', 422)
    if 'style_doc' in request:
        from .spec import _merge
        core.validate_style_document(request['style_doc'])
        style = _merge(request['style_doc'], request['overrides'])
        core.validate_style_document(style)
        if policy.public:
            raise JobError('Custom style requires a trusted private service')
        selection._validate_active_edits(request['overrides'], style, bundle=bundle)
        if set(request['overrides'].get('materials', {})) - {entry['material'] for entry in style['styles']}:
            raise JobError('Custom overrides must name used materials')
        required = set(presets.required_capabilities(style))
        if required - set(engine.CAPABILITIES) or (policy.allowed_capabilities is not None
                and required - set(policy.allowed_capabilities)):
            raise JobError('Custom style requires unavailable or policy-disallowed capabilities', 'capability_unavailable', 422)
        if policy.allowed_override_sections is not None and set(request['overrides']) - set(policy.allowed_override_sections):
            raise JobError('Custom style overrides exceed server policy', 'selection_refused', 422)
        for name in ('width', 'height', 'samples'):
            value = request['hard_requirements'].get(name) or style['output'][name]
            ceiling = getattr(policy, 'max_' + name)
            if ceiling is not None and value > ceiling:
                raise JobError('Custom output exceeds server policy', 'selection_refused', 422)
        if not policy.allow_scene and request['hard_requirements']['save_blend']:
            raise JobError('Scene output is disallowed by server policy', 'selection_refused', 422)
        return None
    return selection.select_preset(selection.SelectionRequest(**{k: request[k] for k in SELECTION_FIELDS}),
        available_capabilities=engine.CAPABILITIES, policy=policy, bundle=bundle)


def freeze_request(request, decision):
    frozen = copy.deepcopy(request)
    if decision is not None:
        frozen.update(preset=decision.preset_id, revision=decision.preset_revision)
    return frozen


def validate_bundle_controls(resolved, bundle):
    """D2's explicit color-follow request needs applicable exported hints.

    D1's base-color helper already mirrors renderer hint precedence and target
    matching. For this availability check only, retain a base contribution on a
    copy so a legitimate full color-follow mix is not mistaken for no hints.
    No color is assigned and the resolved snapshot is never changed.
    """
    if not resolved['color_follow']:
        return
    inspection = copy.deepcopy(resolved['style_doc'])
    inspection['color_follow']['mix'] = 0
    if not any(presets._color_follow_uses_base_color(inspection, entry['material'], bundle)
               for entry in inspection['styles']):
        raise JobError('Color following has no applicable input hints; no backend render started',
                       'color_follow_unavailable', 422)


def non_admission(decision, profile):
    soft = decision.hard_requirements_satisfied and decision.execution_compatible
    return {**consumer_errors.error_record(refusal_category(decision)), 'accepted': False, 'job_id': None, 'status': 'not_admitted', 'stage': 'selection',
            'profile': profile, 'style_source': 'builtin', 'preset_decision': safe_decision(decision),
            'render_spec_sha256': None, 'next_action': 'authorize_approximation_or_new_preset' if soft
            else 'revise_requirements_or_new_preset', 'poll_after_seconds': None, 'reused': False}


def refusal_category(decision):
    unsupported = decision.unsupported_requirements
    if 'policy.capabilities' in unsupported or set(unsupported) & set(engine.CAPABILITIES):
        return 'capability_unavailable'
    return 'selection_incompatible'


def compact_job(job, profile):
    try:
        consumer_records.status_metadata(job)
    except consumer_records.MetadataError:
        return {**consumer_errors.error_record('metadata_invalid'), 'condition': 'metadata_invalid',
                'status': 'blocked', 'job_id': None, 'poll_after_seconds': None}
    keys = ('job_id', 'status', 'stage', 'preset_decision', 'style_source', 'selection_phase',
            'backend_render_started', 'selection_error', 'render_spec_sha256', 'next_action', 'poll_after_seconds',
            'reused', 'condition', 'result_available', 'queue_position', 'expires_at',
            'style_identity', 'preselection_decision', 'error_category', 'error_message')
    result = {key: job.get(key) for key in keys}
    result.update(accepted=True, profile=profile)
    code = result['condition'] or result['error_category']
    if not code:
        code = {'failed': 'render_failed', 'cancelled': 'cancelled', 'expired': 'artifact_expired',
                'delivered': 'delivered', 'deleted': 'deleted', 'transfer_pending': 'transfer_pending'}.get(result['status'])
    result['error_category'] = consumer_errors.category(code) if code else None
    result['error_message'] = consumer_errors.MESSAGES[result['error_category']] if code else None
    if result['condition']:
        result['status'] = 'blocked'
    if result['condition'] or result['status'] == 'transfer_pending':
        result['poll_after_seconds'] = None
    return result
