"""Safe D2 receipt facts, readable without importing or verifying an engine.

Retrieving existing artifacts must not require the current selector or runtime.
This validates transport types only; it never ranks or re-evaluates a decision.
"""
import copy
import json
import re

from .consumer_errors import MESSAGES

MAX_METADATA_BYTES = 8192
REASONS = frozenset({
    'Explicit preset retained',
    'Ranked compatible representations, material, palette, lighting and composition',
    'Existing design with small supported edits',
    'Approximation: some requested visual properties are unmatched',
    'Closest base; a dedicated preset is recommended',
    'Low-fit approximation explicitly requested; compatibility gates still apply',
})
SELECTION_ERRORS = {None, 'selection_invalid', 'selection_refused', 'catalog_changed',
                    'color_follow_unavailable', 'resolution_invalid', 'capability_unavailable'}


class MetadataError(ValueError):
    """Conservative durable-state integrity failure, never a catalog lookup."""


def _fail():
    raise MetadataError('Stored consumer metadata requires operator inspection')


def _encode(value):
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if len(encoded.encode()) > MAX_METADATA_BYTES:
            _fail()
        return encoded
    except (ValueError, TypeError, RecursionError):
        _fail()


def validate_decision(decision):
    booleans = {'new_preset_recommended', 'hard_requirements_satisfied', 'execution_compatible', 'approximation_authorized'}
    lists = {'reasons', 'unmatched_features', 'unsupported_requirements'}
    fields = {'format_version', 'catalog_digest', 'preset_id', 'preset_revision', 'fit', 'route'} | booleans | lists
    if not isinstance(decision, dict) or set(decision) != fields:
        _fail()
    if (type(decision['format_version']) is not int or decision['format_version'] != 1
        or not _digest(decision['catalog_digest']) or not _preset(decision['preset_id'])
        or not _revision(decision['preset_revision'])
        or decision['fit'] not in ('high', 'medium', 'low')
        or any(type(decision[key]) is not bool for key in booleans)):
        _fail()
    executable = (decision['hard_requirements_satisfied'] and decision['execution_compatible']
                  and (decision['fit'] != 'low' or decision['approximation_authorized']))
    if (decision['route'] != ('preset-overrides' if executable else 'developer/new-preset')
        or decision['new_preset_recommended'] != (decision['fit'] == 'low')
        or (not decision['hard_requirements_satisfied'] and decision['execution_compatible'])
        or (not decision['execution_compatible'] and decision['fit'] != 'low')):
        _fail()
    for key in lists:
        items = decision[key]
        if not isinstance(items, list) or len(items) > 64 or any(type(item) is not str for item in items):
            _fail()
        if key == 'reasons':
            if not items or any(item not in REASONS for item in items):
                _fail()
        elif any(not re.fullmatch(r'[a-z][a-z0-9_.-]{0,95}', item) for item in items):
            _fail()
    _encode(decision)
    return copy.deepcopy(decision)


def _preset(value):
    return isinstance(value, str) and re.fullmatch(r'builtin/[a-z][a-z0-9_-]{0,127}', value)


def _revision(value):
    return isinstance(value, str) and re.fullmatch('[1-9][0-9]{0,8}', value)


def validate_metadata(value):
    _encode(value)
    fields = {'format_version', 'style_source', 'style_identity', 'request_fingerprint',
        'preselection_decision', 'preset_decision', 'selection_phase', 'backend_render_started',
        'selection_error', 'error_category'}
    if not isinstance(value, dict) or set(value) != fields:
        _fail()
    if (type(value['format_version']) is not int or value['format_version'] != 1
        or value['style_source'] not in ('builtin', 'custom')
        or value['selection_phase'] not in ('preselection', 'bound', 'refused')
        or type(value['backend_render_started']) is not bool
        or not _digest(value['request_fingerprint'])
        or value['selection_error'] not in tuple(SELECTION_ERRORS)
        or value['error_category'] not in (None, *MESSAGES)):
        _fail()
    identity = value['style_identity']
    if not isinstance(identity, dict):
        _fail()
    if value['style_source'] == 'builtin':
        if set(identity) != {'preset_id', 'preset_revision', 'catalog_digest'}:
            _fail()
        if not _preset(identity['preset_id']) or not _revision(identity['preset_revision']) or not _digest(identity['catalog_digest']):
            _fail()
        first = validate_decision(value['preselection_decision'])
        if first['route'] != 'preset-overrides' or any(first[k] != identity[k] for k in identity):
            _fail()
        if value['preset_decision'] is not None:
            final = validate_decision(value['preset_decision'])
            if any(final[k] != identity[k] for k in identity):
                _fail()
            if value['selection_phase'] != 'refused' and final['route'] != 'preset-overrides':
                _fail()
        elif value['selection_phase'] != 'refused':
            _fail()
        if value['selection_phase'] == 'preselection' and value['preset_decision'] != first:
            _fail()
    elif (set(identity) != {'custom_style_sha256'} or not _digest(identity['custom_style_sha256'])
          or value['preselection_decision'] is not None or value['preset_decision'] is not None):
        _fail()
    if (value['backend_render_started'] and value['selection_phase'] != 'bound'
        or value['selection_phase'] == 'refused' and value['selection_error'] is None
        or value['selection_phase'] != 'refused' and value['selection_error'] is not None):
        _fail()
    return copy.deepcopy(value)


def dumps(value):
    return _encode(validate_metadata(value))


def loads(raw):
    if raw is None:
        return None  # Pre-D2 jobs keep their existing lifecycle.
    if not isinstance(raw, str) or len(raw.encode()) > MAX_METADATA_BYTES:
        _fail()
    try:
        return validate_metadata(json.loads(raw))
    except (ValueError, TypeError, KeyError, RecursionError):
        _fail()


def validate_transition(previous, proposed):
    previous, proposed = validate_metadata(previous), validate_metadata(proposed)
    if previous['selection_phase'] != 'preselection' or proposed['selection_phase'] not in ('bound', 'refused'):
        _fail()
    for key in ('style_source', 'style_identity', 'request_fingerprint', 'preselection_decision'):
        if previous[key] != proposed[key]:
            _fail()


def _digest(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)


def result_metadata(value):
    fields = ('preset_decision', 'style_source', 'selection_phase', 'render_spec_sha256',
              'style_identity', 'preselection_decision')
    result = {key: copy.deepcopy(value.get(key)) for key in fields}
    digest = result['render_spec_sha256']
    if digest is not None and not _digest(digest):
        raise ValueError('Invalid result specification identity')
    source, decision, phase = result['style_source'], result['preset_decision'], result['selection_phase']
    status_metadata(value)
    if source is None:
        if decision is not None or phase is not None:
            raise ValueError('Result decision requires its style source')
        return result  # Pre-D2 job/receipt.
    if source not in {'builtin', 'custom'} or phase != 'bound':
        raise ValueError('Result requires a bound consumer configuration')
    if source == 'custom':
        if decision is not None:
            raise ValueError('Custom result cannot claim a built-in decision')
        return result
    validate_decision(decision)
    return result


def status_metadata(value):
    """Validate safe presentation fields again, including remote/receipt reads."""
    fields = ('style_source', 'style_identity', 'preselection_decision', 'preset_decision',
              'selection_phase', 'backend_render_started', 'selection_error')
    data = {key: copy.deepcopy(value.get(key)) for key in fields}
    _encode(data)
    if (data['backend_render_started'] is not None and type(data['backend_render_started']) is not bool
        or data['selection_error'] not in tuple(SELECTION_ERRORS)):
        _fail()
    source = data['style_source']
    if source is None:
        if any(data[key] is not None for key in ('style_identity', 'preselection_decision', 'preset_decision', 'selection_phase')):
            _fail()
        return data
    if source not in ('builtin', 'custom') or data['selection_phase'] not in ('preselection', 'bound', 'refused'):
        _fail()
    if (data['backend_render_started'] and data['selection_phase'] != 'bound'
        or data['selection_phase'] == 'refused' and data['selection_error'] is None):
        _fail()
    identity = data['style_identity']
    if not isinstance(identity, dict):
        _fail()
    if source == 'custom':
        if (set(identity) != {'custom_style_sha256'} or not _digest(identity['custom_style_sha256'])
            or data['preselection_decision'] is not None or data['preset_decision'] is not None):
            _fail()
    else:
        if set(identity) != {'preset_id', 'preset_revision', 'catalog_digest'}:
            _fail()
        first = validate_decision(data['preselection_decision'])
        if first['route'] != 'preset-overrides' or any(first[k] != identity[k] for k in identity):
            _fail()
        if data['preset_decision'] is not None:
            final = validate_decision(data['preset_decision'])
            if any(final[k] != identity[k] for k in identity):
                _fail()
            if data['selection_phase'] != 'refused' and final['route'] != 'preset-overrides':
                _fail()
            if data['selection_phase'] == 'preselection' and first != final:
                _fail()
        elif data['selection_phase'] != 'refused':
            _fail()
    return data
