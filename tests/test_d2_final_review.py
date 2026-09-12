"""Second D2 review: real MCP/client/API boundaries and captured public logs."""
import asyncio
import copy
from contextlib import contextmanager
import json
import logging
from pathlib import Path
import traceback

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client as MCPClient
from PIL import Image as PILImage

from blender_render import client as clients, mcp_server, selection
from blender_render.api import create_app
from blender_render.client import Client, ClientError
from blender_render.engine import EngineMismatch, ExecutionUnknown, RestartRequired
from blender_render.jobs import JobError
from test_d2_consumer import consumer, payload, server, attachment
from test_d2_review import call, record, submit
from test_jobs_api import TOKEN


class InProcessClient(Client):
    """Replace only HTTP transport, preserving Client and ConfiguredClient logic."""
    def __init__(self, api):
        super().__init__('http://127.0.0.1', TOKEN)
        self.api, self.calls = api, []

    def request(self, method, path, body=None, **kwargs):
        self.calls.append((method, path, copy.deepcopy(body)))
        response = self.api.request(method, path, headers={'Authorization': 'Bearer ' + self.token},
            **({'content': body} if kwargs.get('raw') else {'json': body}))
        if response.status_code >= 400:
            data = response.json()
            raise ClientError(data.get('error'), response.status_code, data.get('code'))
        return response.content if kwargs.get('binary') else response.json()


@contextmanager
def http_client(service):
    # Exercise HTTP transport without starting the service's background dispatcher.
    api = TestClient(create_app(service, token=TOKEN, tokens={TOKEN: 'alice'}))
    try:
        yield api
    finally:
        api.close()


@pytest.fixture
def connection(consumer, monkeypatch, tmp_path):
    service, _, _ = consumer
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path / 'home'))
    with http_client(service) as api:
        connection = InProcessClient(api)
        monkeypatch.setattr(clients, '_connect_client', lambda *a, **k: connection)
        yield connection


RESTRICTIONS = [
    ({}, {'overrides': {'output': {'raw_view': True}}}, 'selection_incompatible'),
    ({'allowed_presets': ['green_paint']}, {}, 'selection_incompatible'),
    ({'allowed_capabilities': []}, {}, 'capability_unavailable'),
    ({'allow_scene': False}, {'save_blend': True}, 'selection_incompatible'),
    ({'max_width': 100}, {'width': 101}, 'selection_incompatible'),
    ({'max_height': 100}, {'height': 101}, 'selection_incompatible'),
    ({'max_samples': 10}, {'samples': 11}, 'selection_incompatible'),
    ({}, {'width': 4097}, 'selection_incompatible'),
    ({}, {'height': 4097}, 'selection_incompatible'),
    ({}, {'samples': 513}, 'selection_incompatible'),
]


@pytest.mark.parametrize('policy,options,category', RESTRICTIONS)
def test_final_policy_restricted_stdio(consumer, connection, payload, policy, options, category, tmp_path):
    service, conversions, _ = consumer
    source = service.import_payload('alice', payload)['input_id']
    service.selection_policy = selection.SelectionPolicy(**policy)
    factory = mcp_server.create_stdio(trusted=False, compatibility=True)
    normal = record(call(factory, 'render_structure', {'path': str(tmp_path / 'absent.cif'),
        'request': {'request_key': 'normal', 'preset': 'pastel', **options}}))
    compatible = record(call(factory, 'submit_render', {'input_id': source, 'idempotency_key': 'compatible',
        'options': {'preset': 'pastel', **options}}))
    assert normal.get('error_category') == compatible.get('error_category') == category
    assert not service.store.recent('alice')['jobs']
    assert service.store.quota('alice')['reserved_usd'] == 0
    assert not conversions and not service.backend.calls


def test_final_policy_persists_restriction_through_exact_context(consumer, connection, payload, tmp_path):
    service, conversions, _ = consumer
    payload['view']['viewport_px'] = [100, 200]
    path = tmp_path / 'public.cif'
    path.write_bytes(b'public')
    factory = mcp_server.create_stdio(trusted=False)
    first = record(call(factory, 'render_structure', {'path': str(path),
        'request': {'request_key': 'exact', 'preset': 'pastel', 'width': 4096}}))
    assert first['accepted']
    options = json.loads(service.store.db.execute('SELECT options FROM jobs').fetchone()[0])
    assert options['consumer_public'] is True
    service._execute(service.store.claim())
    state = record(call(factory, 'render_status', {'job_id': first['job_id'], 'wait_seconds': 0}))
    assert state['error_category'] == 'selection_incompatible'
    assert not service.backend.calls and len(conversions) == 1
    envelopes = [b for _, p, b in connection.calls if p == '/v1/consumer/preflight']
    assert envelopes[0]['consumer_policy'] == {'format_version': 1, 'public': True}


def test_final_policy_permitted_compatibility(consumer, connection, payload):
    service, conversions, _ = consumer
    source = service.import_payload('alice', payload)['input_id']
    result = record(call(mcp_server.create_stdio(trusted=False, compatibility=True), 'submit_render',
        {'input_id': source, 'idempotency_key': 'allowed', 'options': {'preset': 'pastel', 'samples': 12}}))
    assert result['accepted'] and result['selection_phase'] == 'bound'
    service._execute(service.store.claim())
    assert service.store.get('alice', result['job_id'])['status'] == 'done'
    assert not conversions and len(service.backend.calls) == 1


@pytest.mark.parametrize('mode', ['hosted', 'stdio'])
@pytest.mark.parametrize('failure', ['disappearance', 'consumption', 'decoder', 'encoder', 'success'])
def test_final_delivery_acquisition_and_presentation(consumer, connection, monkeypatch, tmp_path, caplog, mode, failure):
    service, _, _ = consumer
    job = submit(service)
    service._execute(service.store.claim())
    png = service.store.artifact_root(job['job_id']) / 'render.png'
    original = png.read_bytes()
    inventory_reads, artifact_reads = [], []
    inventory, artifact = service.store.result, service.artifact
    def read_inventory(*a, **kw):
        result = inventory(*a, **kw)
        inventory_reads.append(True)
        if len(inventory_reads) == 1 and (mode == 'hosted' and failure == 'disappearance' or failure == 'consumption'):
            if failure == 'disappearance':
                png.unlink()
            else:
                service.store.artifact_delivered('alice', job['job_id'], 'render.png')
        return result
    def read_artifact(*a, **kw):
        artifact_reads.append(a[2])
        return artifact(*a, **kw)
    monkeypatch.setattr(service.store, 'result', read_inventory)
    monkeypatch.setattr(service, 'artifact', read_artifact)
    if mode == 'stdio' and failure == 'disappearance':
        download = connection.download
        def disappear(*a, **kw):
            result = download(*a, **kw)
            Path(result['outputs']['render.png']).unlink()
            return result
        monkeypatch.setattr(connection, 'download', disappear)
    def fail(*a, **kw):
        raise RuntimeError('PRIVATE_PREVIEW_SENTINEL')
    if failure == 'decoder':
        monkeypatch.setattr(PILImage, 'open', fail)
    elif failure == 'encoder':
        monkeypatch.setattr(PILImage.Image, 'save', fail)
    factory = server(service) if mode == 'hosted' else mcp_server.create_stdio(trusted=True)
    arguments = {'job_id': job['job_id']}
    if mode == 'stdio':
        arguments['output_path'] = str(tmp_path / 'delivered.png')
    result = call(factory, 'render_result', arguments)
    data = record(result)
    assert not result.is_error and 'PRIVATE_PREVIEW_SENTINEL' not in public_text(result, caplog)
    if failure in {'disappearance', 'consumption'}:
        assert data['complete'] is False and 'render.png' in data['missing_required']
        assert data['error_category'] in {'artifact_unavailable', 'artifact_expired'}
        assert data['next_action'] != 'done' and data['presentation_status'] != 'fallback_links'
        if mode == 'hosted':
            assert 'render.png' not in [a['name'] for a in data['artifacts']]
            assert 'preview_url' not in data
        else:
            assert 'render.png' not in data['outputs']
        assert 'manifest.json' in ([a['name'] for a in data['artifacts']] if mode == 'hosted' else data['outputs'])
    else:
        assert data['complete']
        assert data['presentation_status'] == ('inline_preview' if failure == 'success' else 'fallback_links')
        assert data['presentation_error'] == (None if failure == 'success' else 'presentation_failed')
        assert bool([c for c in result.content if c.type == 'image']) is (failure == 'success')
        delivered = png if mode == 'hosted' else Path(data['outputs']['render.png'])
        assert delivered.read_bytes() == original
        assert service.store.get('alice', job['job_id'])['status'] == ('done' if mode == 'hosted' else 'delivered')
    if mode == 'hosted':
        assert len(inventory_reads) == 1 and artifact_reads.count('render.png') == 1
        assert not connection.calls
    else:
        assert sum(p.endswith('/result?inventory=true') for _, p, _ in connection.calls) == 1
        assert sum(m == 'GET' and '/artifacts/' in p for m, p, _ in connection.calls) == 2
        assert sum(p.endswith('/ack') for _, p, _ in connection.calls) == (1 if failure == 'consumption' else 2)
    assert len(service.backend.calls) == 1


def public_text(result, caplog):
    return str(result) + caplog.text + '\n'.join(''.join(traceback.format_exception(*r.exc_info))
        for r in caplog.records if r.exc_info)


CONDITIONS = [('compute_unavailable', JobError), ('capability_unavailable', JobError),
    ('cancelled', JobError), ('invalid_structure', ValueError), ('restart_required', RestartRequired),
    ('compatibility_blocked', EngineMismatch), ('execution_unknown', ExecutionUnknown)]


@pytest.mark.parametrize('mode', ['hosted', 'trusted', 'compatibility'])
@pytest.mark.parametrize('category,exception', CONDITIONS)
def test_final_converter_categories(consumer, monkeypatch, caplog, mode, category, exception):
    service, conversions, _ = consumer
    sentinel = 'PRIVATE_CONVERTER_SENTINEL'
    def convert(*a, **kw):
        conversions.append(True)
        if exception is JobError:
            raise exception(sentinel, category, 422)
        raise exception(sentinel)
    service.converter = convert
    caplog.set_level(logging.INFO)
    factory = server(service, trusted=mode == 'trusted', compatibility=mode == 'compatibility')
    if mode == 'compatibility':
        result = call(factory, 'import_structure', {'file': attachment()})
    else:
        initial = record(call(factory, 'render_structure', {'file': attachment(),
            'request': {'request_key': 'converter', 'preset': 'pastel'}}))
        service._execute(service.store.claim())
        result = call(factory, 'render_status', {'job_id': initial['job_id'], 'wait_seconds': 0})
    assert record(result)['error_category'] == category
    assert sentinel not in public_text(result, caplog)
    assert len(conversions) == 1 and not service.backend.calls


@pytest.mark.parametrize('mode', ['hosted', 'trusted', 'compatibility'])
def test_final_converter_absence(consumer, caplog, mode):
    service, conversions, _ = consumer
    service.converter = None
    factory = server(service, trusted=mode == 'trusted', compatibility=mode == 'compatibility')
    args = {'file': attachment()}
    if mode != 'compatibility':
        args['request'] = {'request_key': 'absent', 'preset': 'pastel'}
    result = call(factory, 'import_structure' if mode == 'compatibility' else 'render_structure', args)
    assert record(result)['error_category'] == 'compute_unavailable'
    assert not service.store.recent('alice')['jobs'] and not conversions and not service.backend.calls


@pytest.mark.parametrize('case', ['preset', 'malformed', 'filename', 'url', 'path', 'override', 'converter'])
@pytest.mark.parametrize('log_level', [logging.INFO, logging.DEBUG], ids=['info', 'debug'])
def test_final_logs_are_sanitized(consumer, connection, monkeypatch, caplog, case, log_level, tmp_path):
    from dataclasses import replace
    service, _, _ = consumer
    service.store.policy = replace(service.store.policy, public=case != 'path')
    sentinel = 'PRIVATE_' + case.upper() + '_SENTINEL'
    factory = server(service, compatibility=True)
    tool, category = 'render_structure', 'selection_invalid'
    args = {'file': attachment(), 'request': {'request_key': 'safe', 'preset': 'pastel'}}
    if case == 'preset':
        args['request']['preset'] = sentinel + '.cif'
    elif case == 'malformed':
        args['request']['request_key'] = '/private/' + sentinel
    elif case == 'filename':
        args['file']['file_name'] = sentinel + '.blend'
        category = 'format_unknown'
    elif case == 'url':
        args['file']['download_url'] = 'https://example.org/' + sentinel
        def fail(*a, **kw): raise RuntimeError(sentinel)
        monkeypatch.setattr('blender_render.uploads.download_file', fail)
        category = 'attachment_unavailable'
    elif case == 'path':
        factory = mcp_server.create_stdio(trusted=False)
        args = {'path': str(tmp_path / (sentinel + '.cif')), 'request': args['request']}
        category = 'attachment_unavailable'
    elif case == 'override':
        args['request']['overrides'] = {sentinel: '/private/' + sentinel}
    else:
        tool, category = 'import_structure', 'compute_unavailable'
        args = {'file': {**attachment(), 'file_name': sentinel + '.cif'}}
        def fail(*a, **kw): raise JobError(sentinel, category, 503)
        service.converter = fail
        factory = server(service, compatibility=True)
    caplog.set_level(log_level)
    for logger in ('fastmcp', 'fastmcp.server.server', 'mcp.server.lowlevel.server'):
        caplog.set_level(log_level, logger=logger)
    result = call(factory, tool, args)
    assert sentinel not in public_text(result, caplog)
    assert record(result)['error_category'] == category
    async def raising():
        async with MCPClient(factory) as client:
            with pytest.raises(Exception) as error:
                await client.call_tool(tool, args)
            assert sentinel not in ''.join(traceback.format_exception(error.value)) + str(vars(error.value))
    asyncio.run(raising())
    assert sentinel not in caplog.text
    assert not service.backend.calls


@pytest.mark.parametrize('trusted', [False, True])
@pytest.mark.parametrize('condition', ['compute_unavailable', 'capability_unavailable', 'cancelled',
    'restart_required', 'compatibility_blocked', 'execution_unknown', 'invalid_structure', 'wrapped_cancelled', 'absent'])
def test_final_converter_stdio(consumer, monkeypatch, tmp_path, caplog, trusted, condition):
    service, conversions, _ = consumer
    sentinel = 'PRIVATE_STDIO_CONVERTER_SENTINEL'
    def convert(*args, **kwargs):
        conversions.append(True)
        if condition == 'wrapped_cancelled':
            try:
                raise JobError(sentinel, 'cancelled', 422)
            except JobError as exc:
                raise JobError(sentinel, 'invalid_structure', 422) from exc
        exception = dict(CONDITIONS)[condition]
        if exception is JobError:
            raise exception(sentinel, condition, 422)
        raise exception(sentinel)
    service.converter = None if condition == 'absent' else convert
    path = tmp_path / (sentinel + '.cif')
    path.write_bytes(b'public')
    caplog.set_level(logging.INFO)
    with http_client(service) as api:
        connection = InProcessClient(api)
        monkeypatch.setattr(clients, '_connect_client', lambda *a, **k: connection)
        factory = mcp_server.create_stdio(trusted=trusted)
        result = call(factory, 'render_structure', {'path': str(path),
            'request': {'request_key': 'stdio', 'preset': 'pastel'}})
        if condition != 'absent':
            initial = record(result)
            assert initial['accepted']
            service._execute(service.store.claim())
            result = call(factory, 'render_status', {'job_id': initial['job_id'], 'wait_seconds': 0})
        expected = 'compute_unavailable' if condition == 'absent' else ('cancelled' if condition == 'wrapped_cancelled' else condition)
        assert record(result)['error_category'] == expected
        assert sentinel not in public_text(result, caplog)
        if expected == 'cancelled':
            assert record(result)['status'] == 'cancelled'
    assert not service.backend.calls and len(conversions) == (0 if condition == 'absent' else 1)


@pytest.mark.parametrize('condition', ['compute_unavailable', 'capability_unavailable', 'cancelled', 'absent'])
def test_final_converter_stdio_compatibility_upload(consumer, monkeypatch, tmp_path, caplog, condition):
    service, conversions, _ = consumer
    sentinel = 'PRIVATE_COMPATIBILITY_FILE_SENTINEL'
    def convert(*args, **kwargs):
        conversions.append(True)
        raise JobError(sentinel, condition, 422)
    service.converter = None if condition == 'absent' else convert
    path = tmp_path / (sentinel + '.cif')
    path.write_bytes(b'public')
    monkeypatch.setattr('blender_render.config.load_config', lambda: {'profiles': {'files': {'kind': 'http', 'upload_files': True}}})
    caplog.set_level(logging.INFO)
    with http_client(service) as api:
        connection = InProcessClient(api)
        monkeypatch.setattr(clients, '_connect_client', lambda *a, **k: connection)
        factory = mcp_server.create_stdio('files', trusted=False, compatibility=True)
        result = call(factory, 'import_structure', {'path': str(path)})
        assert record(result)['error_category'] == ('compute_unavailable' if condition == 'absent' else condition)
        assert sentinel not in public_text(result, caplog)
    assert not service.backend.calls and len(conversions) == (0 if condition == 'absent' else 1)
