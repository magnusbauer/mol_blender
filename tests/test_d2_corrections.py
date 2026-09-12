"""Additional boundary matrices for the eight D2 corrections."""
import asyncio
import copy
import json
from pathlib import Path

import pytest
from fastmcp import Client as MCPClient

from blender_render import consumer_records, consumer_transport, mcp_server, selection
from blender_render.jobs import JobError, JobStore
from test_d2_consumer import consumer, server, attachment
from test_d2_review import call, record, submit, SENTINEL
from test_jobs_api import payload


def test_stdio_compatibility_uses_authoritative_service_admission(consumer, payload, monkeypatch):
    from fastapi.testclient import TestClient
    from blender_render.api import create_app
    from test_result_recovery import LocalClient
    from test_jobs_api import TOKEN
    from blender_render.client import ClientError
    class CodedLocalClient(LocalClient):
        def request(self, method, path, body=None, **kwargs):
            self.calls.append((method, path))
            result = self.api.request(method, path, json=body,
                headers={'Authorization': 'Bearer ' + self.token})
            problem = result.json()
            if result.status_code >= 400:
                raise ClientError(problem.get('error'), result.status_code, problem.get('code'))
            return problem
    service, conversions, _ = consumer
    source = service.import_payload('alice', payload)['input_id']
    service.selection_policy = selection.SelectionPolicy(allowed_presets=['green_paint'])
    with TestClient(create_app(service, token=TOKEN, tokens={TOKEN: 'alice'})) as api:
        connection = CodedLocalClient(api)
        monkeypatch.setattr('blender_render.client.configured_client', lambda *a, **k: connection)
        result = call(mcp_server.create_stdio(trusted=True, compatibility=True), 'submit_render',
            {'input_id': source, 'idempotency_key': 'restricted', 'options': {'preset': 'pastel'}})
        assert result.is_error and not service.store.recent('alice')['jobs']
        assert record(result)['code'] == 'selection_policy_refused'
        assert record(result)['error_category'] == 'selection_incompatible'
        assert connection.calls == [('POST', '/v1/consumer/submit')]
        assert not conversions and not service.backend.calls


@pytest.mark.parametrize('suffix,category', [('.blend', 'format_unknown'), ('.cif', 'attachment_unavailable')])
def test_stdio_local_access_errors_are_safe(consumer, tmp_path, monkeypatch, suffix, category):
    from blender_render.client import Client
    service, _, _ = consumer
    class PreflightClient(Client):
        def request(self, method, path, body=None, **kwargs):
            if path == '/v1/consumer/recover':
                return {'job': None}
            assert path == '/v1/consumer/preflight'
            service.consumer_preflight('alice', body, trusted=True)
            return {'ready': True}
    connection = PreflightClient('http://127.0.0.1', 'test')
    monkeypatch.setattr('blender_render.client.configured_client', lambda *a, **k: connection)
    result = call(mcp_server.create_stdio(trusted=True), 'render_structure',
        {'path': str(tmp_path / (SENTINEL + suffix)), 'request': {'request_key': 'local', 'preset': 'pastel'}})
    assert record(result)['error_category'] == category and SENTINEL not in str(result)
    assert not service.store.recent('alice')['jobs']


@pytest.mark.parametrize('kind', ['custom', 'preset'])
def test_refusal_accounting_cleanup_and_identity_reopen(consumer, monkeypatch, kind):
    from dataclasses import replace
    from blender_render import spec
    service, conversions, _ = consumer
    service.store.policy = replace(service.store.policy, public=True)
    if kind == 'custom':
        # Trusted custom styles require the existing private admission policy.
        service.store.policy = replace(service.store.policy, public=False)
        request = {'request_key': 'refusal', 'style_doc': spec.builtin_definition('pastel').style_doc,
                   'color_follow': True}
        plan = service.consumer_preflight('alice', request, trusted=True)
    else:
        request = {'request_key': 'refusal', 'preset': 'pastel', 'color_follow': True}
        plan = service.consumer_preflight('alice', request)
    job = service.admit_consumer_file('alice', b'bounded', '.cif', 'public.cif', service.converter, plan)
    first = service.store.get('alice', job['job_id'])
    service._execute(service.store.claim())
    row = dict(service.store.db.execute('SELECT * FROM jobs').fetchone())
    assert row['state'] == 'failed' and row['rendered'] == row['charged'] == row['gpu_seconds'] == 0
    assert row['remote_id'] is None and json.loads(row['options']) == {}
    assert service.store.db.execute('SELECT COUNT(*) FROM inputs').fetchone()[0] == 0
    assert service.store.quota('alice')['reserved_usd'] == 0
    root = service.store.root
    service.store.close()
    service.store = JobStore(root, policy=service.store.policy)
    monkeypatch.setattr(selection, 'select_preset', lambda *a, **k: pytest.fail('Recovery reranked'))
    state = record(call(server(service), 'render_status', {'job_id': job['job_id'], 'wait_seconds': 0}))
    assert state['style_identity'] == first['style_identity']
    assert state['preselection_decision'] == first['preselection_decision']
    assert state['error_category'] == 'selection_incompatible' and state['render_spec_sha256'] is None
    assert state['backend_render_started'] is False
    assert service.recover_render('alice', request, trusted=kind == 'custom')['job_id'] == job['job_id']
    assert len(conversions) == 1 and not service.backend.calls


@pytest.mark.parametrize('compatibility', [False, True])
def test_permitted_policy_approximation_and_explicit_import(consumer, compatibility):
    service, conversions, downloads = consumer
    factory = server(service, compatibility=compatibility)
    options = {'preset': 'pastel', 'visual_brief': {'material_family': 'directional_ink'},
               'allow_low_fit_approximation': True, 'samples': 12}
    if compatibility:
        imported = record(call(factory, 'import_structure', {'file': attachment()}))
        assert len(conversions) == len(downloads) == 1
        result = call(factory, 'submit_render', {'input_id': imported['input_id'],
            'idempotency_key': 'allowed', 'options': options})
    else:
        result = call(factory, 'render_structure', {'file': attachment(),
            'request': {'request_key': 'allowed', **options}})
    data = record(result)
    assert data['accepted'] and data['preset_decision']['fit'] == 'low'
    assert data['preset_decision']['approximation_authorized']
    assert data['selection_phase'] == ('bound' if compatibility else 'preselection')
    service._execute(service.store.claim())
    state = service.store.get('alice', data['job_id'])
    assert state['status'] == 'done' and state['style_identity'] == data['style_identity']
    assert len(service.backend.calls) == len(conversions) == len(downloads) == 1


@pytest.mark.parametrize('mode', ['hosted', 'trusted', 'compatibility'])
@pytest.mark.parametrize('code,category', [('daily_quota', 'admission_refused'),
    ('budget_exhausted', 'admission_refused'), ('file_unavailable', 'attachment_unavailable'),
    ('expired', 'artifact_expired'), ('restart_required', 'restart_required'),
    ('execution_unknown', 'execution_unknown'), ('compatibility_blocked', 'compatibility_blocked'),
    ('transfer_pending', 'transfer_pending')])
def test_actual_mcp_wrapped_lifecycle_codes(consumer, monkeypatch, mode, code, category):
    service, _, _ = consumer
    def fail(*args, **kwargs):
        raise JobError('/private/' + SENTINEL, code, 409)
    monkeypatch.setattr(service.store, 'get', fail)
    if mode == 'trusted':
        from types import SimpleNamespace
        monkeypatch.setattr('blender_render.client.configured_client', lambda *a, **k: SimpleNamespace(status=fail))
        factory = mcp_server.create_stdio(trusted=True)
    else:
        factory = server(service, compatibility=mode == 'compatibility')
    result = call(factory, 'render_status', {'job_id': 'existing', 'wait_seconds': 0})
    assert SENTINEL not in str(result)
    assert record(result)['error_category'] == category and record(result)['code'] == code


@pytest.mark.parametrize('failure', ['attachment', 'format', 'file_argument', 'compatibility_key'])
def test_actual_mcp_transport_errors(consumer, monkeypatch, failure):
    service, conversions, _ = consumer
    args = {'request': {'request_key': 'new', 'preset': 'pastel'}, 'file': attachment()}
    args['file']['download_url'] = 'https://example.org/' + SENTINEL
    name, category = 'render_structure', 'selection_invalid'
    if failure == 'attachment':
        def fail(*a, **k):
            raise RuntimeError('https://example.org/' + SENTINEL)
        monkeypatch.setattr('blender_render.uploads.download_file', fail)
        category = 'attachment_unavailable'
    elif failure == 'format':
        args['file']['file_name'] = SENTINEL + '.blend'
        category = 'format_unknown'
    elif failure == 'file_argument':
        args['file']['file_name'] = {'private': SENTINEL}
    else:
        name = 'submit_render'
        args = {'input_id': SENTINEL, 'idempotency_key': '/private/' + SENTINEL}
    factory = server(service, compatibility=True)
    result = call(factory, name, args)
    assert SENTINEL not in str(result)
    assert record(result)['error_category'] == category
    async def raising():
        async with MCPClient(factory) as client:
            with pytest.raises(Exception) as error:
                await client.call_tool(name, args)
            assert SENTINEL not in str(error.value)
    asyncio.run(raising())
    assert not service.store.recent('alice')['jobs'] and not conversions


@pytest.mark.parametrize('condition', ['transfer_pending', 'restart_required', 'compatibility_blocked',
                                     'execution_unknown', 'recovery_required'])
def test_compact_blocked_lifecycle_is_actionable(consumer, condition):
    service, _, _ = consumer
    job = submit(service)
    if condition == 'transfer_pending':
        service.store.db.execute("UPDATE jobs SET state='transfer_pending',remote_phase='download' WHERE id=?", (job['job_id'],))
        service.store.db.commit()
    else:
        service.store.block(job['job_id'], condition, 'admitted')
    data = record(call(server(service), 'render_status', {'job_id': job['job_id'], 'wait_seconds': 0}))
    assert data['error_category'] == condition
    assert data['poll_after_seconds'] is None and data['next_action'] != 'render_structure'
    assert data['style_identity'] == job['style_identity']
    assert {'queue_position', 'expires_at'} <= data.keys()
    assert not service.backend.calls


@pytest.mark.parametrize('outcome', ['expiry', 'loss', 'deleted'])
def test_compact_artifact_lifecycle(consumer, outcome):
    service, _, _ = consumer
    job = submit(service)
    service._execute(service.store.claim())
    if outcome == 'expiry':
        service.store.db.execute('UPDATE jobs SET result_expires=1 WHERE id=?', (job['job_id'],))
        service.store.db.commit()
    elif outcome == 'loss':
        (service.store.artifact_root(job['job_id']) / 'render.png').unlink()
    else:
        service.store.delete_result('alice', job['job_id'])
    data = record(call(server(service), 'render_status', {'job_id': job['job_id'], 'wait_seconds': 0}))
    assert data['error_category'] == {'expiry': 'artifact_expired', 'loss': 'artifact_unavailable', 'deleted': 'deleted'}[outcome]
    assert data['result_available'] is False and data['style_identity'] == job['style_identity']
    assert len(service.backend.calls) == 1


@pytest.mark.parametrize('bad', ['count', 'aggregate', 'depth', 'override_size', 'capability'])
def test_transport_bounds_before_storage(consumer, bad):
    service, conversions, downloads = consumer
    request = {'request_key': 'bounded', 'preset': 'pastel'}
    if bad == 'count':
        request['hard_requirements'] = {'content': ['protein'] * 33}
    elif bad == 'aggregate':
        request['hard_requirements'] = {'content': ['protein'] * 32, 'representations': ['cartoon'] * 32}
        request['visual_brief'] = {'edges': 'smooth'}
    elif bad == 'depth':
        nested = {}
        for _ in range(14):
            nested = {'nested': nested}
        request['overrides'] = nested
    elif bad == 'override_size':
        request['overrides'] = {'materials': {str(i): SENTINEL * 50 for i in range(60)}}
    else:
        request['hard_requirements'] = {'required_capabilities': ['private.unavailable']}
    result = submit(service, request)
    assert result['error_category'] == ('capability_unavailable' if bad == 'capability' else 'selection_invalid')
    assert not service.store.recent('alice')['jobs'] and not conversions and not downloads
    assert SENTINEL not in '\n'.join(service.store.db.iterdump())


def test_canonical_transport_survives_phase_b(consumer):
    service, _, _ = consumer
    job = submit(service, {'request_key': 'canonical', 'preset': 'green_paint', 'visual_brief': {
        'material_family': 'dried paint', 'palette': ['#abcdef', 'linear_rgb:[0.1,0.2,0.3]'],
        'edges': 'rounded ribbons', 'projection': 'orthographic', 'lighting': 'soft highlights',
        'background': 'black', 'composition': 'structure', 'ligand_treatment': 'native sticks'}})
    row = service.store.db.execute('SELECT options FROM jobs WHERE id=?', (job['job_id'],)).fetchone()
    brief = json.loads(row[0])['consumer_request']['visual_brief']
    assert brief['material_family'] == 'dried_paint' and brief['edges'] == 'rounded'
    assert brief['palette'] == ['#ABCDEF', 'linear_rgb:[0.1,0.2,0.3]']
    assert brief['background'] == '#000000' and brief['lighting'] == 'area'
    service._execute(service.store.claim())
    state = service.store.get('alice', job['job_id'])
    assert state['status'] == 'done' and state['style_identity'] == job['style_identity']


@pytest.mark.parametrize('field,value', [
    ('format_version', True), ('style_source', []), ('selection_error', {}),
    ('request_fingerprint', 'bad'), ('style_identity', {'private_path': '/private/' + SENTINEL}),
    ('backend_render_started', 'yes'), ('unknown', SENTINEL)])
def test_metadata_reopen_corruption_is_safe(consumer, monkeypatch, field, value):
    service, conversions, _ = consumer
    job = submit(service)
    raw = json.loads(service.store.db.execute('SELECT consumer_metadata FROM jobs').fetchone()[0])
    raw[field] = value
    service.store.db.execute('UPDATE jobs SET consumer_metadata=?', (json.dumps(raw),))
    service.store.db.commit()
    root = service.store.root
    service.store.close()
    service.store = JobStore(root)
    monkeypatch.setattr(selection, 'select_preset', lambda *a, **k: pytest.fail('Read reranked'))
    status = record(call(server(service), 'render_status', {'job_id': job['job_id'], 'wait_seconds': 0}))
    assert status['condition'] == 'metadata_invalid' and SENTINEL not in str(status)
    with pytest.raises(JobError) as error:
        service.store.get('bob', job['job_id'])
    assert error.value.code == 'not_found'
    assert service.store.claim() is None
    assert not conversions and not service.backend.calls


def test_metadata_write_limits_and_transition(consumer):
    service, _, _ = consumer
    job = submit(service)
    raw = consumer_records.loads(service.store.db.execute('SELECT consumer_metadata FROM jobs').fetchone()[0])
    assert len(consumer_records.dumps(raw).encode()) <= consumer_records.MAX_METADATA_BYTES == 8192
    for field in ('preset_decision', 'preselection_decision'):
        invalid = copy.deepcopy(raw)
        invalid[field]['execution_compatible'] = False
        with pytest.raises(consumer_records.MetadataError):
            consumer_records.dumps(invalid)
    invalid = copy.deepcopy(raw)
    invalid['preset_decision']['reasons'] = ['Explicit preset retained'] * 1000
    with pytest.raises(consumer_records.MetadataError):
        consumer_records.dumps(invalid)
    service._execute(service.store.claim())
    bound = consumer_records.loads(service.store.db.execute('SELECT consumer_metadata FROM jobs').fetchone()[0])
    consumer_records.validate_transition(raw, bound)
    changed = copy.deepcopy(bound)
    changed['request_fingerprint'] = '0' * 64
    with pytest.raises(consumer_records.MetadataError):
        consumer_records.validate_transition(raw, changed)
    with pytest.raises(consumer_records.MetadataError):
        consumer_records.validate_transition(bound, bound)
    assert json.loads(service.store.db.execute('SELECT options FROM jobs').fetchone()[0]) == {}


def test_discovery_digest_covers_contract_mutations():
    from d2_discovery import discover_surfaces, digests
    original = asyncio.run(discover_surfaces())
    baseline = digests(original)
    for field in ('description', 'annotations', 'inputSchema', 'outputSchema', '_meta'):
        changed = copy.deepcopy(original)
        changed['hosted_default']['tools'][0][field] = {'changed': True}
        assert digests(changed) != baseline
    for field in ('tools', 'resources'):
        changed = copy.deepcopy(original)
        changed['legacy_widget'][field] = list(reversed(changed['legacy_widget'][field])) if field == 'tools' else []
        assert digests(changed) != baseline


def test_deferred_d2_missing_metadata_is_not_a_legacy_job(consumer):
    service, conversions, _ = consumer
    job = submit(service)
    service.store.db.execute('UPDATE jobs SET consumer_metadata=NULL')
    service.store.db.commit()
    data = record(call(server(service), 'render_status', {'job_id': job['job_id'], 'wait_seconds': 0}))
    assert data['condition'] == 'metadata_invalid'
    assert service.store.claim() is None
    assert not conversions and not service.backend.calls


def test_converter_unavailable_keeps_compute_category(consumer):
    service, _, _ = consumer
    job = submit(service)
    service.converter = None
    service._execute(service.store.claim())
    data = record(call(server(service), 'render_status', {'job_id': job['job_id'], 'wait_seconds': 0}))
    assert data['error_category'] == 'compute_unavailable'
    assert data['style_identity'] == job['style_identity']
    assert not service.backend.calls


def test_compatibility_handoff_after_input_cleanup(consumer, payload, monkeypatch):
    service, conversions, _ = consumer
    source = service.import_payload('alice', payload)['input_id']
    factory = server(service, compatibility=True)
    arguments = {'input_id': source, 'idempotency_key': 'original', 'options': {'preset': 'pastel'}}
    first = record(call(factory, 'submit_render', arguments))
    service._execute(service.store.claim())
    assert service.store.db.execute('SELECT COUNT(*) FROM inputs').fetchone()[0] == 0
    monkeypatch.setattr(selection, 'select_preset', lambda *a, **k: pytest.fail('Handoff reranked'))
    second = record(call(factory, 'submit_render', {**arguments, 'idempotency_key': 'handoff'}))
    assert second['job_id'] == first['job_id'] and second['reused']
    assert second['style_identity'] == first['style_identity']
    assert not conversions and len(service.backend.calls) == 1
