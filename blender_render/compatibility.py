"""Version-1 compatibility evidence. No free text, paths, or execution policy.

This stdlib-only wire format is informational: callers retain responsibility for
condition classification and execution certainty. It must never trigger repair.
"""
import json
import re

REASONS = frozenset(('engine_build_mismatch', 'engine_protocol_mismatch', 'engine_schema_mismatch',
    'capability_missing', 'runtime_version_mismatch', 'runtime_dependency_missing', 'ccd_missing',
    'ccd_checksum_mismatch', 'ccd_activation_failed', 'worker_ipc_unavailable',
    'worker_startup_failed', 'worker_readiness_failed', 'unknown_compatibility'))
PHASES = frozenset(('negotiation', 'startup', 'readiness', 'dispatch', 'unknown'))
COMPONENTS = frozenset(('engine', 'runtime', 'ccd', 'worker', 'controller', 'unknown'))
LOG_NAMES = ('worker.stdout.log', 'worker.stderr.log', 'allocation.log')
DEPENDENCIES = frozenset(('bpy', 'blender', 'python', 'molecularnodes', 'numpy', 'biotite', 'jsonschema'))
VERSION_KEYS = DEPENDENCIES - {'bpy'}
CAPABILITIES = frozenset(('representation.cartoon', 'representation.ribbon', 'representation.surface',
    'representation.sticks', 'representation.spheres', 'representation.wire',
    'representation.surface_smoothing', 'representation.nucleic_base_thickness',
    'representation.wire_mesh', 'material.clay', 'material.dried_paint', 'material.frosted_grid',
    'material.color_gradient', 'material.fog_glow', 'material.attribute_color',
    'material.uniform_polymer_color', 'material.edge_highlight', 'geometry.edge_rounding',
    'geometry.element_palette', 'world.directional_gradient', 'world.separate_illumination',
    'camera.depth_of_field', 'camera.reference_framing', 'animation.camera_orbit', 'encoding.ffmpeg'))
MESSAGES = {
    'engine_build_mismatch':'Worker engine build does not match the admitted execution.',
    'engine_protocol_mismatch':'Worker engine protocol is incompatible.',
    'engine_schema_mismatch':'Worker engine schema is incompatible.',
    'capability_missing':'Worker lacks a required rendering capability.',
    'runtime_version_mismatch':'Worker runtime versions do not match the runtime lock.',
    'runtime_dependency_missing':'Worker runtime dependency is unavailable.',
    'ccd_missing':'Worker CCD data is unavailable.',
    'ccd_checksum_mismatch':'Worker CCD checksum does not match the runtime lock.',
    'ccd_activation_failed':'Worker CCD activation could not be verified.',
    'worker_ipc_unavailable':'Worker IPC storage is unavailable.',
    'worker_startup_failed':'Worker startup failed.',
    'worker_readiness_failed':'Worker readiness could not be verified.',
    'unknown_compatibility':'Execution requires intervention; compatibility details are unavailable.',
}


def _unknown():
    return dict(version=1, reason='unknown_compatibility', phase='unknown', component='unknown',
                before_execution=False, expected={}, observed={}, log_refs=[])


def _version(value):
    return isinstance(value, str) and len(value) <= 32 and re.fullmatch(r'[0-9]{1,6}(?:\.[0-9]{1,6}){0,3}', value)


def _evidence(value):
    if type(value) is not dict or len(value) > 32:
        raise ValueError
    out = {}
    for key, item in value.items():
        if key in ('engine_build', 'ccd_sha256'):
            valid = isinstance(item, str) and re.fullmatch('[0-9a-f]{64}', item)
        elif key in ('manifest_version', 'protocol_version'):
            valid = type(item) is int and 0 <= item <= 65535
        elif key == 'exit_code':
            valid = type(item) is int and -255 <= item <= 255
        elif key in ('ccd_captured', 'ccd_activated', 'base_available', 'bind_configured'):
            valid = type(item) is bool
        elif key == 'base_kind':
            valid = item in ('slurm_tmpdir', 'tmp') if isinstance(item, str) else False
        elif key == 'socket_path_bytes':
            valid = type(item) is int and 0 <= item <= 100
        elif key == 'dependency':
            valid = isinstance(item, str) and item in DEPENDENCIES
        elif key in ('bundle_versions', 'schema_versions', 'capabilities'):
            valid = type(item) is list and len(item) <= 32 and all(
                isinstance(v, str) and (v in CAPABILITIES if key == 'capabilities' else _version(v)) for v in item)
        elif key == 'versions':
            valid = type(item) is dict and len(item) <= len(VERSION_KEYS) and all(
                k in VERSION_KEYS and _version(v) for k,v in item.items())
        else:
            continue
        if not valid:
            raise ValueError
        out[key] = item
    return out


def sanitize(value):
    """Drop unknown keys; replace invalid typed values with a fixed unknown."""
    try:
        if type(value) is not dict or type(value.get('version')) is not int or value['version'] != 1:
            raise ValueError
        out = _unknown()
        for key, allowed in (('reason', REASONS), ('phase', PHASES), ('component', COMPONENTS)):
            if not isinstance(value.get(key), str) or value[key] not in allowed:
                raise ValueError
            out[key] = value[key]
        if type(value.get('before_execution')) is not bool:
            raise ValueError
        out['before_execution'] = value['before_execution']
        for key in ('expected', 'observed'):
            out[key] = _evidence(value.get(key, {}))
        refs = value.get('log_refs', [])
        if type(refs) is not list or len(refs) > 3:
            raise ValueError
        names = set()
        for ref in refs:
            if (type(ref) is not dict or not isinstance(ref.get('name'), str)
                    or ref['name'] not in LOG_NAMES or ref['name'] in names
                    or (ref.get('exists') is not None and type(ref['exists']) is not bool)):
                raise ValueError
            names.add(ref['name'])
            out['log_refs'].append({'name':ref['name'], 'exists':ref.get('exists')})
        encoded = json.dumps(out, allow_nan=False)
        if len(encoded.encode()) > 4096:
            raise ValueError
        return json.loads(encoded)  # Own copies; no caller mutation after validation.
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return _unknown()


def make(reason='unknown_compatibility', *, phase='unknown', component='unknown',
         before_execution=False, expected=None, observed=None, log_refs=None):
    return sanitize(dict(version=1, reason=reason, phase=phase, component=component,
        before_execution=before_execution, expected=expected or {}, observed=observed or {}, log_refs=log_refs or []))


def from_exception(exc, *, before_execution=False):
    result = sanitize(getattr(exc, 'diagnostic', None))
    # The trusted handler supplies certainty. Diagnostic input never determines it.
    result['before_execution'] = bool(before_execution)
    return result


def message(value):
    return MESSAGES[sanitize(value)['reason']]


def runtime_diagnostic(observation, required, *, phase='readiness'):
    """Project private runtime observations, deliberately excluding CCD paths."""
    if not isinstance(observation, dict) or not isinstance(required, dict):
        return make()
    versions = observation.get('versions', {})
    if not isinstance(versions, dict) or not versions and not isinstance(observation.get('ccd'), dict):
        return make()
    missing = next((k for k in sorted(VERSION_KEYS) if k in versions and versions[k] is None), None)
    expected = {'versions':{k:v for k,v in required.items() if k in VERSION_KEYS}}
    observed = {'versions':{k:v for k,v in versions.items() if k in VERSION_KEYS and v is not None}}
    reason, component = 'runtime_version_mismatch', 'runtime'
    if missing:
        reason = 'runtime_dependency_missing'; observed['dependency'] = missing
    elif not any(versions.get(k) != required.get(k) for k in versions):
        ccd = observation.get('ccd', {})
        component = 'ccd'
        expected = {'ccd_sha256': required['ccd_sha256']} if required.get('ccd_sha256') else {}
        observed = {k:v for k,v in {'ccd_sha256':ccd.get('sha256'),
            'ccd_captured':ccd.get('captured'), 'ccd_activated':ccd.get('activated')}.items() if v is not None}
        reason = ('ccd_missing' if not ccd.get('sha256') else
                  'ccd_checksum_mismatch' if ccd['sha256'] != required.get('ccd_sha256') else 'ccd_activation_failed')
    return make(reason, phase=phase, component=component, before_execution=True, expected=expected, observed=observed)


def dependency_diagnostic(exc, *, phase='startup'):
    name = getattr(exc, 'name', None)
    name = name.split('.')[0] if isinstance(name, str) else None
    return make('runtime_dependency_missing', phase=phase, component='runtime', before_execution=True,
                observed={'dependency':name} if name in DEPENDENCIES else {})
