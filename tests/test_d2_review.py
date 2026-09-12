"""Regressions for the independent D2 review; real MCP and durable boundaries."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path

import pytest
from fastmcp import Client as MCPClient

from blender_render import consumer as contract, mcp_server, selection
from blender_render.jobs import JobError, JobStore
from test_d2_consumer import consumer, server, attachment
from test_jobs_api import payload


SENTINEL = 'PRIVATE_REVIEW_SENTINEL'


def call(factory, name, arguments):
    async def run():
        async with MCPClient(factory) as client:
            return await client.call_tool(name, arguments, raise_on_error=False)
    return asyncio.run(run())


def record(result):
    if result.structured_content is not None:
        return result.structured_content
    for item in result.content:
        text = getattr(item, 'text', '')
        try:
            return json.loads(text[text.index('{'):])
        except (ValueError, TypeError):
            pass
    return {}


def submit(service, request=None):
    result = call(server(service), 'render_structure', {'file': attachment(),
        'request': request or {'request_key': 'review', 'preset': 'pastel'}})
    return record(result)


POLICIES = [
    ({'allowed_presets': ['builtin/dried_paint_green']}, {}, 'selection_incompatible'),
    ({'allowed_capabilities': []}, {}, 'capability_unavailable'),
    ({'allow_scene': False}, {'save_blend': True}, 'selection_incompatible'),
    ({'max_width': 100}, {'width': 101}, 'selection_incompatible'),
    ({'max_height': 100}, {'height': 101}, 'selection_incompatible'),
    ({'max_samples': 10}, {'samples': 11}, 'selection_incompatible'),
    ({}, {'overrides': {'output': {'raw_view': True}}}, 'selection_incompatible'),
    ({}, {'visual_brief': {'material_family': 'directional_ink'}}, 'selection_incompatible'),
    ({}, {'hard_requirements': {'representations': ['wire']},
          'allow_low_fit_approximation': True}, 'selection_incompatible'),
]


@pytest.mark.parametrize('compatibility', [False, True])
@pytest.mark.parametrize('policy,options,category', POLICIES)
def test_review_policy(consumer, payload, compatibility, policy, options, category):
    service, conversions, downloads = consumer
    source = service.import_payload('alice', payload)['input_id']
    service.selection_policy = selection.SelectionPolicy(**policy)
    factory = server(service, compatibility=compatibility)
    if compatibility:
        result = call(factory, 'submit_render', {'input_id': source,
            'idempotency_key': 'restricted', 'options': {'preset': 'pastel', **options}})
    else:
        result = call(factory, 'render_structure', {'file': attachment(),
            'request': {'request_key': 'restricted', 'preset': 'pastel', **options}})
    assert record(result).get('error_category') == category
    assert service.store.recent('alice')['jobs'] == []
    assert service.store.quota('alice')['reserved_usd'] == 0
    assert conversions == downloads == service.backend.calls == []


@pytest.mark.parametrize('mode', ['hosted', 'trusted', 'compatibility'])
@pytest.mark.parametrize('bad', [
    {'request_key': '/private/' + SENTINEL + '.cif'},
    {'preset': SENTINEL + '.cif'},
    {'revision': SENTINEL + '.cif'},
    {'visual_brief': {'material_family': '/private/' + SENTINEL}},
    {'overrides': {'private_path': '/private/' + SENTINEL}},
])
def test_review_errors(consumer, mode, bad):
    service, conversions, downloads = consumer
    request = {'request_key': 'safe-key', 'preset': 'pastel', **bad}
    if mode == 'trusted':
        factory = mcp_server.create_stdio(trusted=True)
        arguments = {'path': '/private/' + SENTINEL + '.cif', 'request': request}
    else:
        factory = server(service, compatibility=mode == 'compatibility')
        arguments = {'file': {**attachment(), 'file_name': SENTINEL + '.cif'}, 'request': request}
    result = call(factory, 'render_structure', arguments)
    assert SENTINEL not in str(result)
    assert record(result).get('error_category') == 'selection_invalid'
    assert not conversions and not downloads and not service.backend.calls


@pytest.mark.parametrize('code', ['attachment_unavailable', 'invalid_structure', 'operation_conflict',
    'admission_refused', 'compute_unavailable', 'cancelled', 'artifact_unavailable'])
def test_review_errors_preserve_codes(consumer, monkeypatch, code):
    service, _, _ = consumer
    def fail(*args, **kwargs):
        raise JobError('/private/' + SENTINEL + '.cif', code, 422)
    monkeypatch.setattr(service.store, 'get', fail)
    result = call(server(service), 'render_status', {'job_id': 'existing', 'wait_seconds': 0})
    assert SENTINEL not in str(result)
    assert record(result).get('error_category') == code
    assert record(result).get('code') == code


@pytest.mark.parametrize('mode', ['hosted', 'stdio'])
def test_review_preview(consumer, monkeypatch, tmp_path, mode):
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path / 'receipts'))
    from fastapi.testclient import TestClient
    from blender_render.api import create_app
    from test_result_recovery import LocalClient
    from test_jobs_api import TOKEN
    service, _, _ = consumer
    job = submit(service)
    service._execute(service.store.claim())
    png = service.store.artifact_root(job['job_id']) / 'render.png'
    original = png.read_bytes()
    inventory_calls = []
    original_result = service.store.result
    def inventory(*args, **kwargs):
        inventory_calls.append(True)
        return original_result(*args, **kwargs)
    monkeypatch.setattr(service.store, 'result', inventory)
    def fail(*args):
        raise RuntimeError(SENTINEL)
    monkeypatch.setattr(mcp_server, 'image_preview', fail)
    api = TestClient(create_app(service, token=TOKEN, tokens={TOKEN: 'alice'}))
    client = LocalClient(api)
    monkeypatch.setattr('blender_render.client.configured_client', lambda *a, **k: client)
    factory = server(service) if mode == 'hosted' else mcp_server.create_stdio(trusted=True)
    args = {'job_id': job['job_id']}
    if mode == 'stdio':
        args['output_path'] = str(tmp_path / 'delivered.png')
    result = call(factory, 'render_result', args)
    assert not result.is_error
    data = record(result)
    assert data['presentation_status'] == 'fallback_links'
    assert data['presentation_error'] == 'presentation_failed'
    assert data['complete']
    assert SENTINEL not in str(result)
    if mode == 'hosted':
        assert data['preview_url'] and data['artifacts']
        assert png.read_bytes() == original
        assert len(inventory_calls) == 1
        assert service.store.get('alice', job['job_id'])['status'] == 'done'
    else:
        assert Path(data['outputs']['render.png']).read_bytes() == original
        assert len([c for c in client.calls if c[1].endswith('/ack')]) == 2
        assert len([c for c in client.calls if c[0] == 'GET' and '/artifacts/' in c[1]]) == 2
        assert service.store.get('alice', job['job_id'])['status'] == 'delivered'
    assert len(service.backend.calls) == 1
    api.close()


@pytest.mark.parametrize('outcome', ['queued', 'done', 'conversion', 'backend', 'cancelled', 'refused'])
def test_review_status(consumer, monkeypatch, outcome):
    service, _, _ = consumer
    request = {'request_key': 'status', 'preset': 'pastel'}
    if outcome == 'refused':
        request.update(preset='vivid_binding_site', color_follow=True,
            overrides={'materials': {'ligand': {'base_color': '#123456'}}})
    initial = submit(service, request)
    expected = initial['preset_decision']
    def fail(*args, **kwargs):
        raise ValueError(SENTINEL)
    if outcome == 'conversion':
        service.converter = fail
    if outcome == 'backend':
        monkeypatch.setattr(service.backend, 'render', fail)
    if outcome == 'cancelled':
        service.store.cancel('alice', initial['job_id'])
    elif outcome != 'queued':
        service._execute(service.store.claim())
    state = record(call(server(service), 'render_status', {'job_id': initial['job_id'], 'wait_seconds': 0}))
    assert {'queue_position', 'expires_at', 'error_category', 'error_message', 'style_identity'} <= state.keys()
    assert state['style_identity']['preset_id'] == expected['preset_id']
    assert state['preselection_decision'] == expected
    assert SENTINEL not in str(state)
    if outcome in {'conversion', 'backend', 'cancelled', 'refused'}:
        assert state['error_category'] == {'conversion': 'invalid_structure', 'backend': 'render_failed',
            'cancelled': 'cancelled', 'refused': 'selection_invalid'}[outcome]
    if outcome == 'refused':
        assert state['render_spec_sha256'] is None and state['backend_render_started'] is False
        assert not service.backend.calls


@pytest.mark.parametrize('brief', [
    {'palette': [SENTINEL + '.cif']},
    {'material_family': '/private/' + SENTINEL},
    {'lighting': 'https://example.org/' + SENTINEL},
    {'composition': 'confidential patient observation ' + SENTINEL},
    {'edges': 'token=' + SENTINEL},
    {'representations': ['cartoon'] * 65},
    {'background': 'x' * 513},
    {'palette': ['#123456'] * 12000},
])
def test_review_descriptors(consumer, brief):
    service, conversions, downloads = consumer
    result = submit(service, {'request_key': 'unsafe', 'preset': 'pastel', 'visual_brief': brief})
    assert result.get('error_category') == 'selection_invalid'
    assert not service.store.recent('alice')['jobs']
    assert not conversions and not downloads
    dump = '\n'.join(service.store.db.iterdump())
    assert SENTINEL not in dump


@pytest.mark.parametrize('corruption', ['unknown', 'type', 'digest', 'oversized', 'truncated'])
def test_review_metadata(consumer, corruption):
    service, conversions, _ = consumer
    job = submit(service)
    raw = service.store.db.execute('SELECT consumer_metadata FROM jobs WHERE id=?', (job['job_id'],)).fetchone()[0]
    data = json.loads(raw)
    if corruption == 'unknown':
        data['private_path'] = '/private/' + SENTINEL
    elif corruption == 'type':
        data['preset_decision'] = {'private_path': '/private/' + SENTINEL}
    elif corruption == 'digest':
        data['preset_decision']['catalog_digest'] = SENTINEL
    elif corruption == 'oversized':
        data['preset_decision']['reasons'] = ['x' * 500] * 200
    encoded = '{' if corruption == 'truncated' else json.dumps(data)
    service.store.db.execute('UPDATE jobs SET consumer_metadata=? WHERE id=?', (encoded, job['job_id']))
    service.store.db.commit()
    result = call(server(service), 'render_status', {'job_id': job['job_id'], 'wait_seconds': 0})
    assert SENTINEL not in str(result)
    state = record(result)
    assert state.get('condition') == 'metadata_invalid'
    assert state.get('error_category') == 'metadata_invalid'
    pending = service.store.claim()
    if pending:
        service._execute(pending)
    assert not service.backend.calls and not conversions


def test_review_metadata_accounting(consumer):
    service, _, _ = consumer
    submit(service)
    before = service.store.storage_bytes(service.store.db)
    raw = service.store.db.execute('SELECT consumer_metadata FROM jobs').fetchone()[0]
    service.store.db.execute('UPDATE jobs SET consumer_metadata=NULL')
    assert before - service.store.storage_bytes(service.store.db) == len(raw.encode())


@pytest.mark.parametrize('trusted,compatibility', [(False, False), (True, False), (False, True), (True, True)])
def test_review_widget(consumer, trusted, compatibility):
    service, _, _ = consumer
    async def run():
        factory = server(service, trusted=trusted, compatibility=compatibility)
        assert type(factory)._read_resource_mcp is mcp_server.FastMCP._read_resource_mcp
        async with MCPClient(factory) as client:
            assert mcp_server.PREVIEW_URI not in [str(resource.uri) for resource in await client.list_resources()]
    asyncio.run(run())


def test_review_discovery_evidence():
    # The independent executable probe must attest the real discovered surfaces.
    import runpy
    probe = Path(__file__).with_name('d2_determinism_probe.py').read_text()
    assert 'discover_surfaces' in probe
    import d2_discovery
    records = asyncio.run(d2_discovery.discover_surfaces())
    assert set(records) == {'hosted_default', 'trusted_default', 'hosted_compatibility',
        'trusted_compatibility', 'legacy_widget'}
    assert d2_discovery.digests(records) == d2_discovery.EXPECTED_DIGESTS
    for surface in records.values():
        for tool in surface['tools']:
            assert {'name', 'description', 'annotations', 'inputSchema', 'outputSchema', '_meta'} <= tool.keys()
