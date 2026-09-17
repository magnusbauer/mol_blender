"""Verified local result receipts; no rendering or compute admission."""
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

from .config import config_root
from .artifacts import MEDIA, destination


def _atomic(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _bound_request_timeout(client, deadline, job_id):
    if deadline is None:
        return
    remaining = float(deadline) - time.monotonic()
    if remaining <= 0:
        from .client import ClientError
        raise ClientError(
            'Result recovery deadline expired; reconnect with result and do not resubmit.',
            408,
            'operation_timeout',
            job_id=job_id,
            next_action='render_result',
        )
    if hasattr(client, 'timeout'):
        client.timeout = min(float(client.timeout), remaining)


def recover_result(client, job_id, output, *, deadline=None):
    from .client import ClientError
    from .runtime import file_lock
    scope = hashlib.sha256((client.url + '\0' + client.token + '\0' + job_id).encode()).hexdigest()
    receipt_path = config_root() / 'result-receipts' / (scope + '.json')
    try:
        with file_lock(receipt_path.with_suffix('.lock'), deadline=deadline):
            return _recover(client, job_id, output, receipt_path, deadline=deadline)
    except TimeoutError:
        raise ClientError(
            'Result recovery deadline expired; reconnect with result and do not resubmit.',
            408,
            'operation_timeout',
            job_id=job_id,
            next_action='render_result',
        ) from None


def _inventory_identity(items):
    """C2 immutable artifact fields; availability/ack state may change."""
    if not isinstance(items, list):
        raise ValueError('Invalid artifact inventory')
    identities = {}
    for item in items:
        if not isinstance(item, dict) or item.get('name') not in MEDIA:
            raise ValueError('Invalid artifact name')
        name = item['name']
        if name in identities:
            raise ValueError('Duplicate artifact identity')
        identities[name] = {key:item.get(key) for key in ('sha256', 'size', 'mime_type', 'expires_at')}
        identities[name]['required'] = item.get('required', name in {'render.png', 'render.blend'})
    return identities


def _recover(client, job_id, output, receipt_path, *, before_ack=None, strict_ack=False, expected_execution=None,
             on_child_timings=None, attempt=1, before_action=None, deadline=None):
    from .client import ClientError
    started = time.monotonic()
    output = Path(output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Check destination writability before any potentially consuming request.
    with tempfile.TemporaryFile(dir=output.parent):
        pass
    try:
        receipt = json.loads(receipt_path.read_text())
    except FileNotFoundError:
        receipt = {'version': 2, 'job_id': job_id, 'artifacts': [], 'outputs': {}}
    if receipt.get('job_id') != job_id:
        raise ValueError('Receipt belongs to a different child operation')
    if receipt.get('consumer_metadata') is not None:
        from .consumer_records import result_metadata
        receipt['consumer_metadata'] = result_metadata(receipt['consumer_metadata'])
    # Additive compatibility with earlier C3 receipts: their outer job_id was
    # already bound, while execution_identity contained only the three digests.
    if expected_execution is not None and isinstance(receipt.get('execution_identity'), dict):
        receipt['execution_identity'].setdefault('job_id', job_id)
    base = '/v1/jobs/' + quote(job_id, safe='')
    manifest = {}
    timings = []
    try:
        _bound_request_timeout(client, deadline, job_id)
        if before_action: before_action()
        result = client.request('GET', base + '/result?inventory=true')
        if result.get('delivery_policy') != 'explicit_ack_v1':
            raise ValueError('Service does not support recoverable delivery; no render was submitted')
        if 'expected_artifacts' not in result:
            raise ValueError('Service lacks expected artifact inventory')
        if result.get('job_id') != job_id:
            raise ValueError('Result belongs to a different child operation')
        if expected_execution is not None:
            observed = {key: result.get(key) for key in expected_execution}
            if observed != expected_execution:
                raise ValueError('Child execution identity mismatch; artifacts were not acknowledged')
            if receipt.get('execution_identity') not in (None, observed):
                raise ValueError('Previously bound execution identity changed')
            receipt['execution_identity'] = observed
        incoming = _inventory_identity(result['expected_artifacts'])
        previous = _inventory_identity(receipt['artifacts'])
        if previous and (incoming != previous or receipt.get('inventory_complete', False) and not result['inventory_complete']):
            raise ValueError('Previously bound artifact inventory changed')
        from .consumer_records import result_metadata
        metadata = result_metadata(result)
        if receipt.get('consumer_metadata') is not None and receipt['consumer_metadata'] != metadata:
            raise ValueError('Previously bound result decision or specification changed')
        receipt['consumer_metadata'] = metadata
        receipt['artifacts'] = [dict(item, expected=True) for item in result['expected_artifacts']]
        receipt['inventory_complete'] = result['inventory_complete']
        receipt['version'] = 2
        manifest = result.get('manifest', {})
        from .telemetry import sanitize
        timings = sanitize(result.get('timings', []))
        if on_child_timings is not None:
            receipt['timing_integrity_conflict'] = on_child_timings(timings)
    except ClientError as exc:
        if exc.code == 'operation_timeout':
            raise
        if not receipt['artifacts']:
            raise
        # Offline recovery still requires an earlier complete inventory and
        # independently verified local bytes. Never infer a shrinking inventory.
        for item in receipt['artifacts']:
            item['availability'] = 'unknown'
    except OSError:
        if not receipt['artifacts']:
            raise
        # Offline recovery still requires an earlier complete inventory and
        # independently verified local bytes. Never infer a shrinking inventory.
        for item in receipt['artifacts']:
            item['availability'] = 'unknown'
    if expected_execution is not None and receipt.get('execution_identity') != expected_execution:
        raise ValueError('Recovered receipt execution identity mismatch')
    destinations = {}
    for item in receipt['artifacts']:
        name = item['name']
        if name not in MEDIA:
            raise ClientError('Unexpected artifact name')
        destinations[name] = destination(output, name)
    if len(set(destinations.values())) != len(destinations):
        raise ClientError('Output paths overlap; choose a PNG filename')
    _atomic(receipt_path, json.dumps(receipt).encode())
    paths, errors = {}, {}
    for item in receipt['artifacts']:
        _bound_request_timeout(client, deadline, job_id)
        name, path = item['name'], destinations[item['name']]
        item['verified_locally'] = False
        item['checksum_failure'] = False
        item['required'] = item.get('required', name in {'render.png', 'render.blend'})
        try:
            verified = False
            # Reconstruct a moved destination from previously verified output.
            for candidate in (path, Path(receipt['outputs'].get(name, str(path)))):
                if candidate.is_file():
                    raw = candidate.read_bytes()
                    if len(raw) == item['size'] and hashlib.sha256(raw).hexdigest() == item['sha256']:
                        verified = True
                        if candidate != path:
                            _atomic(path, raw)
                        break
                    item['checksum_failure'] = True
            if not verified:
                if item.get('availability') != 'available' or item.get('sha256') is None or item.get('size') is None:
                    raise ClientError('artifact_unavailable: expected artifact has no verified local copy and is unavailable remotely')
                _bound_request_timeout(client, deadline, job_id)
                if before_action: before_action()
                raw = client.request('GET', base + '/artifacts/' + name + '?delivery=ack', binary=True)
                if len(raw) != item['size'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
                    item['checksum_failure'] = True
                    raise ClientError('Artifact checksum does not match')
                _atomic(path, raw)
            item['verified_locally'] = True
            paths[name] = str(path)
            receipt['outputs'][name] = str(path)
            # Persist verification before acknowledgement permits server deletion.
            _atomic(receipt_path, json.dumps(receipt).encode())
            if before_ack:
                before_ack()
            try:
                _bound_request_timeout(client, deadline, job_id)
                if before_action: before_action()
                client.request('POST', base + '/artifacts/' + name + '/ack', {'sha256': item['sha256']})
            except (ClientError, OSError) as exc:
                # Output is safe locally; server retains it until ack or TTL.
                if strict_ack:
                    errors[name] = 'Artifact acknowledgement pending'
        except ClientError as exc:
            if exc.code == 'operation_timeout':
                raise
            errors[name] = str(exc)
        except OSError as exc:
            errors[name] = str(exc)
    _atomic(receipt_path, json.dumps(receipt).encode())
    if 'manifest.json' in paths:
        manifest = json.loads(Path(paths['manifest.json']).read_text())
    missing = [a['name'] for a in receipt['artifacts'] if a['required'] and not a['verified_locally']]
    if 'render.png' not in paths and 'render.png' not in missing:
        missing.insert(0, 'render.png')
    required_errors = any(a['required'] and a['name'] in errors for a in receipt['artifacts'])
    complete = receipt.get('inventory_complete', False) and not missing and not (strict_ack and required_errors)
    if not receipt.get('inventory_complete', False):
        errors['inventory'] = 'artifact_unavailable: original required inventory could not be reconstructed'
    from .telemetry import record, sanitize
    delivery_timings = [record('delivery', 'gateway' if strict_ack else 'client', time.monotonic()-started,
                               'executed' if complete else 'interrupted', attempt)]
    timings = sanitize(timings + delivery_timings)
    receipt['timings'] = timings
    _atomic(receipt_path, json.dumps(receipt).encode())
    return {**receipt.get('consumer_metadata', {}), 'job_id': job_id, 'outputs': paths, 'manifest': manifest,
            'delivery_receipt': {'verified': complete, 'artifacts': [a['name'] for a in receipt['artifacts'] if a['name'] in paths]},
            'timings': timings,
            'delivery_timings': delivery_timings,
            'timing_integrity_conflict': receipt.get('timing_integrity_conflict', False),
            'complete': complete, 'errors': errors, 'missing_required': missing,
            'error_code': None if complete else 'artifact_unavailable',
            'artifacts': receipt['artifacts'],
            'next_action': 'done' if complete else 'recover_verified_local_copy_or_stop'}
