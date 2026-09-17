"""Internal immutable execution envelope. Consumer options never enter here."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import PurePosixPath

from .engine import engine_identity, require_compatible
from .spec import style_digest

TRANSIENT = {'run_id', 'generated_at_utc', 'timings', 'operation_id', 'transport'}
V1_TRANSIENT = frozenset(TRANSIENT)
REQUEST_KEYS = {'preset', 'style_doc', 'output_name', 'job_timeout_seconds', 'keep_temp',
                'save_blend', 'rough', 'raw_view', 'color_follow', 'render_spec'}


def canonical_bundle(payload):
    bundle = copy.deepcopy({k: v for k, v in payload.items() if k not in TRANSIENT})
    if isinstance(bundle.get('provenance'), dict):
        for key in ('filename', 'path', 'url', 'generated_at_utc'):
            bundle['provenance'].pop(key, None)
    return bundle


def bundle_digest(payload):
    return style_digest(canonical_bundle(payload))


def freeze_execution(payload, request):
    return {'version': 1, 'bundle': canonical_bundle(payload), 'bundle_sha256': bundle_digest(payload),
            'request': copy.deepcopy(request), 'expected_engine': engine_identity()}


def validate_snapshot_for_execution(snapshot, *, public=False, allow_animation=False, max_atoms=10000000, max_bytes=50*1024**2, timeout=600):
    from .service import validate_input
    from .core import validate_style_document
    from .presets import required_capabilities
    if not isinstance(snapshot, dict) or set(snapshot) != {'version', 'bundle', 'bundle_sha256', 'request', 'expected_engine'} or snapshot['version'] != 1:
        raise ValueError('Unsupported resolved envelope version or fields')
    bundle = snapshot['bundle']
    if not isinstance(bundle, dict) or TRANSIENT & bundle.keys():
        raise ValueError('Transient metadata in immutable bundle')
    validated = validate_input(bundle, max_atoms=max_atoms, max_bytes=max_bytes)
    if canonical_bundle(validated) != bundle or bundle_digest(bundle) != snapshot['bundle_sha256']:
        raise ValueError('Resolved bundle digest or normalization mismatch')
    request = snapshot['request']
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        raise ValueError('Mixed resolved/unresolved request or unsupported fields')
    style = validate_style_document(copy.deepcopy(request['style_doc']))
    metadata = request['render_spec']
    if (not isinstance(metadata, dict) or set(metadata) != {'schema_version', 'sha256', 'preset_source', 'required_capabilities'}
            or metadata['sha256'] != style_digest(style) or metadata['schema_version'] != style['version']
            or metadata['required_capabilities'] != required_capabilities(style)
            or not isinstance(metadata['preset_source'], dict) or not metadata['preset_source'].get('kind')):
        raise ValueError('Resolved RenderSpec digest, provenance or capabilities mismatch')
    provenance = metadata['preset_source']
    kind = provenance['kind']
    if kind == 'full_json':
        valid = provenance == {'kind':'full_json'} and request['preset'] == ''
    elif kind == 'builtin':
        from .presets import PRESET_STYLE_FILENAMES
        valid = (set(provenance) == {'kind','id','revision','source_json'} and provenance.get('id') == request['preset']
                 and provenance.get('revision') == '1' and provenance.get('id') in PRESET_STYLE_FILENAMES
                 and provenance.get('source_json') == PRESET_STYLE_FILENAMES.get(provenance.get('id')))
    elif kind in {'user','project'}:
        valid = (set(provenance) <= {'kind','id','revision','sha256','camera_policy','camera_policy_source'}
                 and provenance.get('id') == request['preset'] and type(provenance.get('revision')) is int
                 and isinstance(provenance.get('sha256'), str) and len(provenance['sha256']) == 64)
    else:
        valid = False
    if not valid:
        raise ValueError('Invalid preset provenance')
    output = style['output']
    if (request['rough'] is not False or request['save_blend'] != output['save_blend']
            or request['raw_view'] != output['raw_view']
            or request['color_follow'] != style.get('color_follow', {}).get('enabled', False)
            or request['output_name'] != 'render.png' or output['filename'] != 'render.png'
            or output['blend_filename'] != 'render.blend' or output['format'] != 'PNG'
            or type(request['keep_temp']) is not bool):
        raise ValueError('Resolved request conflicts with immutable specification')
    if type(request['job_timeout_seconds']) not in (int, float) or not 0 < request['job_timeout_seconds'] <= timeout:
        raise ValueError('Resolved request exceeds receiving timeout policy')
    if public and not allow_animation and output.get('animation', {}).get('mode', 'none') != 'none':
        raise ValueError('Shared hosted animation is not enabled')
    if public and (request['keep_temp'] or output['raw_view'] or max(output['width'], output['height']) > 4096
                   or output['samples'] > 512 or style['renderer']['engine'] != 'CYCLES'
                   or style['renderer'].get('device') != 'GPU' or style['renderer'].get('require_gpu') is not True):
        raise ValueError('Resolved request conflicts with receiving resource/security policy')
    require_compatible(snapshot['expected_engine'], engine_identity(), metadata['required_capabilities'])
    if snapshot['expected_engine'] != engine_identity():
        from .engine import EngineMismatch
        raise EngineMismatch('Resolved engine manifest does not match the exact installed build declaration')
    if style['version'] not in snapshot['expected_engine']['schema_versions'] or bundle['version'] not in snapshot['expected_engine']['bundle_versions']:
        raise ValueError('Unsupported engine schema or bundle version')
    return copy.deepcopy(snapshot)


class StoredSnapshotIntegrityError(ValueError):
    """Retain the admitted operation for recovery; never substitute execution."""


def validate_stored_snapshot_integrity(snapshot):
    """Decode the immutable version-1 protocol, not a current RenderSpec.

    Only use with a trusted durable operation row. Owner/operation/child binding
    comes from that row and authenticated retrieval, not caller-supplied claims.
    These canonical encodings and field rules must remain stable across builds.
    """
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

    def sha(value):
        return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)

    def strings(value):
        return isinstance(value, list) and all(isinstance(s, str) and s for s in value) and len(set(value)) == len(value)

    try:
        if (not isinstance(snapshot, dict) or set(snapshot) != {'version', 'bundle', 'bundle_sha256', 'request', 'expected_engine'}
                or type(snapshot['version']) is not int or snapshot['version'] != 1):
            raise ValueError('Unsupported stored envelope')
        bundle, request, original = snapshot['bundle'], snapshot['request'], snapshot['expected_engine']
        if not isinstance(bundle, dict) or V1_TRANSIENT & bundle.keys():
            raise ValueError('Invalid stored bundle')
        provenance = bundle.get('provenance')
        if isinstance(provenance, dict) and {'filename', 'path', 'url', 'generated_at_utc'} & provenance.keys():
            raise ValueError('Noncanonical stored bundle')
        if digest(bundle) != snapshot['bundle_sha256']:
            raise ValueError('Stored bundle checksum mismatch')
        if not isinstance(request, dict) or set(request) != {
                'preset', 'style_doc', 'output_name', 'job_timeout_seconds', 'keep_temp',
                'save_blend', 'rough', 'raw_view', 'color_follow', 'render_spec'}:
            raise ValueError('Invalid stored request')
        spec, metadata = request['style_doc'], request['render_spec']
        if (not isinstance(spec, dict) or not isinstance(metadata, dict) or set(metadata) != {
                'schema_version', 'sha256', 'preset_source', 'required_capabilities'}
                or digest(spec) != metadata['sha256'] or metadata['schema_version'] != spec['version']
                or not strings(metadata['required_capabilities'])
                or not isinstance(metadata['preset_source'], dict) or not isinstance(metadata['preset_source'].get('kind'), str)):
            raise ValueError('Invalid stored RenderSpec identity')
        if not isinstance(original, dict) or set(original) != {
                'manifest_version', 'protocol_version', 'bundle_versions', 'schema_versions',
                'engine_build', 'capabilities', 'runtime_requirements', 'files'}:
            raise ValueError('Invalid original engine declaration')
        files = original['files']
        if (original['manifest_version'] != 1 or original['protocol_version'] != 1
                or not isinstance(files, dict) or not files or not isinstance(original['runtime_requirements'], dict)
                or not all(strings(original[key]) for key in ('bundle_versions', 'schema_versions', 'capabilities'))):
            raise ValueError('Unsupported original engine protocol')
        for path, checksum in files.items():
            if (not isinstance(path, str) or not path or '\\' in path or PurePosixPath(path).is_absolute()
                    or '..' in PurePosixPath(path).parts or PurePosixPath(path).as_posix() != path or not sha(checksum)):
                raise ValueError('Invalid original engine inventory')
        # Engine-manifest v1 uses JSON's default separators, unlike bundle/spec.
        if hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest() != original['engine_build']:
            raise ValueError('Original engine build checksum mismatch')
        if (spec['version'] not in original['schema_versions'] or bundle['version'] not in original['bundle_versions']
                or not set(metadata['required_capabilities']) <= set(original['capabilities'])):
            raise ValueError('Stored identity conflicts with original engine')
        if type(request['save_blend']) is not bool or type(request['job_timeout_seconds']) not in (int, float):
            raise ValueError('Invalid stored retrieval controls')
        digest(snapshot)  # Reject non-JSON/nonfinite data anywhere in the envelope.
        return copy.deepcopy(snapshot)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise StoredSnapshotIntegrityError('Stored execution integrity requires recovery; no new execution is authorized') from exc


def validate_snapshot(snapshot, *, for_execution=True, **policy):
    """Compatibility boundary; new callers should select their intention explicitly."""
    if for_execution:
        return validate_snapshot_for_execution(snapshot, **policy)
    return validate_stored_snapshot_integrity(snapshot)


def scoped_owner(controller, owner):
    return 'controller:' + hashlib.sha256(json.dumps([controller, owner]).encode()).hexdigest()


def dispatch_request(snapshot, controller, owner, operation):
    return {**copy.deepcopy(snapshot['request']), 'payload': copy.deepcopy(snapshot['bundle']),
            'bundle_sha256': snapshot['bundle_sha256'], 'expected_engine': copy.deepcopy(snapshot['expected_engine']),
            'execution_context': {'version': 1, 'controller_id': controller, 'owner_id': owner, 'operation_id': operation}}


def request_snapshot(request):
    return {'version': 1, 'bundle': request['payload'], 'bundle_sha256': request['bundle_sha256'],
            'request': {k: v for k, v in request.items() if k not in {'payload', 'bundle_sha256', 'expected_engine', 'execution_context'}},
            'expected_engine': request['expected_engine']}
