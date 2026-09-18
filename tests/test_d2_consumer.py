"""D2 consumer contracts: real selector/store/resolver; fake leaf compute only."""
import asyncio
import copy
import json
import threading
import time

import pytest
from fastmcp import Client as MCPClient
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

from blender_render import mcp_server, presets, selection
from blender_render.client import ClientError
from blender_render.api import Downloads
from blender_render.jobs import JobError, JobStore
from blender_render.service import RenderService, prepare_options
from test_jobs_api import Compute, payload
from test_gateway_recovery import pair


CANONICAL_TOOLS = {'render_structure', 'render_status', 'render_result', 'cancel_render',
                   'list_presets', 'preset_details', 'list_recent_renders', 'recover_render',
                   'recover_transfer', 'available_quota'}
COMPATIBILITY_TOOLS = {'import_structure', 'submit_render', 'list_structures', 'delete_structure'}


@pytest.fixture
def consumer(tmp_path, payload, monkeypatch):
    store = JobStore(tmp_path / 'store')
    conversions, downloads = [], []
    def convert(data, suffix):
        conversions.append((data, suffix))
        return copy.deepcopy(payload)
    service = RenderService(store, Compute(), converter=convert)
    monkeypatch.setattr(mcp_server, 'owner', lambda: 'alice')
    def download(*args, **kwargs):
        downloads.append(True)
        return b'public molecular fixture'
    monkeypatch.setattr('blender_render.uploads.download_file', download)
    yield service, conversions, downloads
    service.store.close()


def server(service, **kwargs):
    return mcp_server.create_http(service, auth=StaticTokenVerifier(tokens={}),
        downloads=Downloads('x' * 48, 'https://render.example.org'),
        converter=service.converter, **kwargs)


def attachment():
    return {'download_url': 'https://example.org/public.cif', 'file_id': 'public-file',
            'file_name': 'public.cif'}


@pytest.mark.parametrize('compatibility', [False, True])
def test_default_and_opt_in_discovery(consumer, compatibility):
    service, conversions, downloads = consumer
    async def run():
        async with MCPClient(server(service, compatibility=compatibility)) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            assert set(tools) == CANONICAL_TOOLS | (COMPATIBILITY_TOOLS if compatibility else set())
            tool = tools['render_structure']
            assert tool.meta['openai/fileParams'] == ['file']
            assert 'request' in tool.inputSchema['properties']
            schema = json.dumps(tool.inputSchema)
            assert 'request_key' in schema and 'visual_brief' in schema
            assert 'style_doc' not in schema and 'rerender' not in schema
            catalog = (await client.call_tool('list_presets', {})).structured_content
            assert catalog == presets.compact_catalog()
            assert len(catalog['entries']) == 14
            assert 'style_doc' not in json.dumps(catalog)
    asyncio.run(run())
    assert not conversions and not downloads and not service.backend.calls
    assert service.store.recent('alice')['jobs'] == []


@pytest.mark.parametrize('authorized', [False, True])
def test_hard_refusal_precedes_file_download_and_admission(consumer, authorized):
    service, conversions, downloads = consumer
    async def run():
        async with MCPClient(server(service)) as client:
            response = (await client.call_tool('render_structure', {'file': attachment(), 'request': {
                'request_key': 'intent-hard', 'preset': 'pastel',
                'hard_requirements': {'representations': ['wire']},
                'allow_low_fit_approximation': authorized}})).structured_content
            assert response['accepted'] is False and response['job_id'] is None
            assert response['preset_decision']['hard_requirements_satisfied'] is False
            assert response['render_spec_sha256'] is None
            assert response['next_action'] == 'revise_requirements_or_new_preset'
    asyncio.run(run())
    assert not downloads and not conversions and not service.backend.calls
    assert not service.store.recent('alice')['jobs']
    assert service.store.quota('alice')['reserved_usd'] == 0


def test_two_phase_decision_exact_snapshot_and_recovery_without_file(consumer, payload, monkeypatch):
    service, conversions, downloads = consumer
    request = {'request_key': 'intent-recovery', 'preset': 'pastel', 'revision': '1',
               'hard_requirements': {'width': 720}, 'visual_brief': {'palette': ['#010203']}}
    async def run():
        async with MCPClient(server(service)) as client:
            first = (await client.call_tool('render_structure', {'file': attachment(), 'request': request})).structured_content
            assert first['accepted'] and first['stage'] == 'conversion_pending'
            assert first['render_spec_sha256'] is None
            assert first['preset_decision']['preset_id'] == 'builtin/preset_1_interface'
            assert first['preset_decision']['fit'] == 'medium'
            assert not conversions and not service.backend.calls
            assert 'stage_history' not in first and 'submitted_by' not in first
            service._execute(service.store.claim())
            state = (await client.call_tool('render_status', {'job_id': first['job_id']})).structured_content
            expected = prepare_options({'preset': 'preset_1_interface', 'width': 720, 'save_blend': False,
                                        'transparent_background': False},
                                       payload, public=True, timeout=600)
            assert state['status'] == 'done'
            assert state['render_spec_sha256'] == expected['render_spec']['sha256']
            assert state['selection_phase'] == 'bound'
            def forbidden(*args, **kwargs):
                pytest.fail('Recovery must not rerank or read an expired attachment')
            monkeypatch.setattr(selection, 'select_preset', forbidden)
            monkeypatch.setattr('blender_render.uploads.download_file', forbidden)
            expired = {**attachment(), 'download_url': 'https://example.org/expired'}
            retry = (await client.call_tool('render_structure', {'file': expired, 'request': request})).structured_content
            recovery = (await client.call_tool('recover_render', {'request': request})).structured_content
            assert retry['job_id'] == recovery['job_id'] == first['job_id']
            assert retry['reused'] and recovery['reused']
            assert retry['preset_decision'] == recovery['preset_decision'] == state['preset_decision']
            changed = {**request, 'visual_brief': {'palette': ['#020304']}}
            result = await client.call_tool('recover_render', {'request': changed}, raise_on_error=False)
            assert result.is_error and 'different request' in str(result.content)
            assert service.store.recent('other')['jobs'] == []
            assert len(conversions) == len(downloads) == len(service.backend.calls) == 1
    asyncio.run(run())


def test_phase_b_active_color_validation_prevents_backend(consumer):
    service, conversions, downloads = consumer
    # D1 permits this color consumer provisionally, but the real input has no
    # applicable atom/class/chain color hints. Do not dispatch an ignored edit.
    request = {'request_key': 'color-context', 'preset': 'vivid_binding_site', 'color_follow': True,
               'overrides': {'materials': {'ligand': {'base_color': '#123456'}}}}
    async def run():
        async with MCPClient(server(service)) as client:
            first = (await client.call_tool('render_structure', {'file': attachment(), 'request': request})).structured_content
            assert first['accepted']
            service._execute(service.store.claim())
            state = (await client.call_tool('render_status', {'job_id': first['job_id']})).structured_content
            assert state['status'] == 'failed'
            assert state['render_spec_sha256'] is None
            assert state['selection_phase'] == 'refused'
            assert state['backend_render_started'] is False
    asyncio.run(run())
    assert len(conversions) == len(downloads) == 1 and not service.backend.calls


@pytest.mark.parametrize('bad', [
    {'request_key': ''}, {'request_key': 'x' * 129}, {'request_key': '/private/file'},
    {'request_key': 'please render this'}, {'request_key': 'https://example.org/?token=secret'},
    {'revision': True}, {'revision': '01'}, {'format_version': True}, {'available_capabilities': []},
    {'policy': {'public': False}}, {'visual_brief': {'notes': 'secret'}},
    {'visual_brief': {'palette': ['https://example.org/reference.png']}},
    {'overrides': {'output': {'filename': 'private.png'}}}, {'style_doc': {}},
])
def test_invalid_consumer_requests_do_not_admit(consumer, bad):
    service, conversions, downloads = consumer
    async def run():
        async with MCPClient(server(service)) as client:
            result = await client.call_tool('render_structure', {'file': attachment(), 'request': {
                'request_key': 'valid-key', 'preset': 'pastel', **bad}}, raise_on_error=False)
            assert result.is_error
    asyncio.run(run())
    assert not service.store.recent('alice')['jobs']
    assert not downloads and not conversions and not service.backend.calls


@pytest.mark.parametrize('authorized', [False, True])
def test_soft_low_fit_requires_explicit_permission(consumer, authorized):
    service, conversions, downloads = consumer
    request = {'request_key': 'soft-low', 'preset': 'pastel',
               'visual_brief': {'material_family': 'directional_ink'},
               'allow_low_fit_approximation': authorized}
    async def run():
        async with MCPClient(server(service)) as client:
            response = (await client.call_tool('render_structure', {'file': attachment(), 'request': request})).structured_content
            assert response['accepted'] is authorized
            decision = response['preset_decision']
            assert decision['fit'] == 'low' and decision['new_preset_recommended']
            assert decision['approximation_authorized'] is authorized
            if authorized:
                service._execute(service.store.claim())
                assert service.store.get('alice', response['job_id'])['render_spec_sha256']
            else:
                assert response['next_action'] == 'authorize_approximation_or_new_preset'
    asyncio.run(run())
    assert len(downloads) == len(conversions) == len(service.backend.calls) == int(authorized)


@pytest.mark.parametrize('trusted', [False, True])
def test_hosted_and_trusted_custom_style_schemas_and_execution(consumer, payload, trusted):
    from blender_render import spec
    service, conversions, downloads = consumer
    style = spec.builtin_definition('pastel').style_doc
    request = {'request_key': 'custom', 'style_doc': style, 'width': 640}
    async def run():
        async with MCPClient(server(service, trusted=trusted)) as client:
            tool = next(tool for tool in await client.list_tools() if tool.name == 'render_structure')
            assert ('style_doc' in json.dumps(tool.inputSchema)) is trusted
            if not trusted:
                assert (await client.call_tool('render_structure', {'file': attachment(), 'request': request}, raise_on_error=False)).is_error
                return
            result = (await client.call_tool('render_structure', {'file': attachment(), 'request': request})).structured_content
            assert result['style_source'] == 'custom' and result['preset_decision'] is None
            assert result['render_spec_sha256'] is None
            service._execute(service.store.claim())
            state = service.store.get('alice', result['job_id'])
            expected = prepare_options({'style_doc': style, 'width': 640, 'save_blend': False,
                'transparent_background': False}, payload, public=False, timeout=600)
            assert state['status'] == 'done' and state['preset_decision'] is None
            assert state['render_spec_sha256'] == expected['render_spec']['sha256']
            bad = {**request, 'preset': 'pastel', 'request_key': 'ambiguous'}
            assert (await client.call_tool('render_structure', {'file': attachment(), 'request': bad}, raise_on_error=False)).is_error
    asyncio.run(run())
    assert len(downloads) == len(conversions) == len(service.backend.calls) == int(trusted)


def test_server_policy_and_capabilities_cannot_be_relaxed(consumer, monkeypatch):
    from blender_render import engine
    service, conversions, downloads = consumer
    service.selection_policy = selection.SelectionPolicy(allowed_presets=['green_paint'])
    with pytest.raises(JobError, match='policy'):
        service.consumer_preflight('alice', {'request_key': 'policy', 'preset': 'pastel'})
    plan = service.consumer_preflight('alice', {'request_key': 'auto'})
    assert plan['decision'].preset_id == 'builtin/dried_paint_green'
    monkeypatch.setattr(engine, 'CAPABILITIES', [])
    request = {'request_key': 'caps', 'preset': 'green_paint', 'allow_low_fit_approximation': True}
    plan = service.consumer_preflight('alice', request)
    assert not plan['decision'].execution_compatible
    assert plan['decision'].resolved_spec_sha256 is None
    assert not downloads and not conversions and not service.backend.calls
    assert not service.store.recent('alice')['jobs']


def test_auto_identity_is_frozen_and_only_final_options_bind(consumer, monkeypatch):
    from blender_render import service as service_module
    service, conversions, downloads = consumer
    real_select, calls = selection.select_preset, []
    def select(request, **kwargs):
        calls.append((request.to_dict(), kwargs.get('bundle')))
        return real_select(request, **kwargs)
    monkeypatch.setattr(selection, 'select_preset', select)
    request = {'request_key': 'automatic', 'visual_brief': {'material_family': 'dried_paint'},
               'samples': 24, 'rough': True}
    plan = service.consumer_preflight('alice', request)
    assert calls[0][0]['preset'] is None and calls[0][1] is None
    chosen = plan['decision'].preset_id
    job = service.admit_consumer_file('alice', b'public', '.cif', 'public.cif', service.converter, plan)
    final_resolutions = []
    real_prepare = service_module.prepare_options
    def prepare(*args, **kwargs):
        resolved = real_prepare(*args, **kwargs)
        final_resolutions.append(copy.deepcopy(resolved))
        return resolved
    monkeypatch.setattr(service_module, 'prepare_options', prepare)
    service._execute(service.store.claim())
    assert len(calls) == 2 and len(final_resolutions) == 1
    assert calls[1][0]['preset'] == chosen and calls[1][0]['revision'] == '1'
    assert calls[1][1]['version'] == '1.0' and 'cif_text' in calls[1][1]
    state = service.store.get('alice', job['job_id'])
    assert state['render_spec_sha256'] == final_resolutions[0]['render_spec']['sha256']
    assert final_resolutions[0]['style_doc']['output']['samples'] == 12
    assert state['backend_render_started'] is True


def test_phase_b_stricter_capability_policy_updates_decision(consumer):
    service, conversions, downloads = consumer
    plan = service.consumer_preflight('alice', {'request_key': 'late-caps', 'preset': 'pastel',
                                               'allow_low_fit_approximation': True})
    job = service.admit_consumer_file('alice', b'public', '.cif', 'public.cif', service.converter, plan)
    service.selection_policy = selection.SelectionPolicy(allowed_capabilities=[])
    service._execute(service.store.claim())
    state = service.store.get('alice', job['job_id'])
    assert state['status'] == 'failed' and state['selection_phase'] == 'refused'
    assert state['preset_decision']['fit'] == 'low'
    assert not state['preset_decision']['execution_compatible']
    assert state['render_spec_sha256'] is None and not state['backend_render_started']
    assert not service.backend.calls


def test_decision_survives_restart_cleanup_and_is_immutable(consumer, monkeypatch):
    from blender_render import consumer as contract
    service, conversions, downloads = consumer
    request = {'request_key': 'durable', 'preset': 'pastel', 'visual_brief': {'palette': ['#010203']}}
    plan = service.consumer_preflight('alice', request)
    job = service.admit_consumer_file('alice', b'public', '.cif', 'private-name.cif', service.converter, plan)
    service._execute(service.store.claim())
    original = service.store.get('alice', job['job_id'])
    encoded = service.store.db.execute('SELECT consumer_metadata FROM jobs').fetchone()[0]
    assert all(text not in encoded for text in ('private-name', 'cif_text', 'data_test', 'https:', '#010203'))
    assert 'overrides' not in json.loads(encoded)['preset_decision']
    with pytest.raises(JobError, match='immutable'):
        service.store.bind_consumer_decision(job['job_id'], contract.metadata(None,
            previous=json.loads(encoded), phase='refused', error='selection_invalid'))
    root = service.store.root
    service.store.close()
    restored = JobStore(root)
    try:
        replacement = RenderService(restored, Compute(), converter=service.converter)
        monkeypatch.setattr(selection, 'select_preset', lambda *a, **k: pytest.fail('Recovery reranked'))
        recovered = replacement.recover_render('alice', request)
        assert recovered['job_id'] == job['job_id']
        assert recovered['preset_decision'] == original['preset_decision']
        assert replacement.recover_render('other', request) is None
        assert restored.result('alice', job['job_id'], inventory=True)['preset_decision'] == original['preset_decision']
        assert not replacement.backend.calls
    finally:
        # Fixture owns the replacement connection.
        service.store = restored


def test_old_database_additive_consumer_migration(tmp_path, payload):
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    job = service.submit('alice', service.import_payload('alice', payload)['input_id'], {}, 'old', operation_key='old')
    before = dict(store.db.execute('SELECT * FROM jobs').fetchone())
    store.db.execute('ALTER TABLE jobs DROP COLUMN consumer_metadata')
    store.close()
    restored = JobStore(tmp_path)
    try:
        after = dict(restored.db.execute('SELECT * FROM jobs').fetchone())
        assert after == before and after['consumer_metadata'] is None
        assert restored.get('alice', job['job_id'])['preset_decision'] is None
    finally:
        restored.close()


@pytest.mark.parametrize('name', ['pastel', 'preset_1_interface', 'builtin/preset_1_interface'])
def test_canonical_request_identity_and_defensive_copy(name):
    from blender_render import consumer as contract
    supplied = {'request_key': 'canonical', 'preset': name, 'revision': 1,
                'hard_requirements': {'content': ['protein', 'protein']}}
    original = copy.deepcopy(supplied)
    normalized = contract.normalize_request(supplied)
    assert supplied == original
    assert normalized['preset'] == 'builtin/preset_1_interface' and normalized['revision'] == '1'
    expected = contract.normalize_request({'request_key': 'changed-transport', 'preset': 'pastel',
        'revision': '1', 'hard_requirements': {'content': ['protein']}})
    assert contract.identity(normalized) == contract.identity(expected)
    supplied['hard_requirements']['content'].append('nucleic')
    assert normalized['hard_requirements']['content'] == ['protein']


@pytest.mark.parametrize('condition', ['restart_required', 'compatibility_blocked', 'execution_unknown', 'recovery_required'])
def test_blocked_status_is_read_once_and_never_polls_or_recovers(consumer, condition):
    service, conversions, downloads = consumer
    plan = service.consumer_preflight('alice', {'request_key': 'blocked'})
    job = service.admit_consumer_file('alice', b'public', '.cif', 'public.cif', service.converter, plan)
    service.store.block(job['job_id'], condition, 'admitted')
    reads = []
    def read(_budget):
        reads.append(True)
        return service.store.get('alice', job['job_id'])
    result = asyncio.run(
        mcp_server.wait_for_status(read, 25, job_id=job['job_id'])
    )
    assert result['condition'] == condition and reads == [True]
    assert not conversions and not service.backend.calls


def test_zero_wait_bounds_the_initial_status_transport():
    release = threading.Event()
    budgets = []

    def blocked_read(budget):
        budgets.append(budget)
        release.wait(5)
        return {'job_id': 'existing', 'status': 'queued'}

    async def run():
        try:
            return await mcp_server.wait_for_status(
                blocked_read, 0, job_id='existing'
            )
        finally:
            release.set()

    started = time.monotonic()
    with pytest.raises(ClientError) as caught:
        asyncio.run(run())

    assert caught.value.code == 'operation_timeout'
    assert caught.value.job_id == 'existing'
    assert caught.value.next_action == 'render_status'
    assert time.monotonic() - started < 1
    assert len(budgets) == 1
    assert 0 < budgets[0] <= mcp_server._IMMEDIATE_STATUS_TIMEOUT_SECONDS + .001


def test_stdio_default_trust_schemas_do_not_connect(monkeypatch):
    monkeypatch.setattr('blender_render.client.configured_client', lambda *a, **k: pytest.fail('Tool discovery connected'))
    monkeypatch.setattr('blender_render.config.load_config', lambda: {'profiles': {'hosted': {'kind': 'http'}}})
    async def run():
        for profile in ('local', 'hosted'):
            async with MCPClient(mcp_server.create_stdio(profile)) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                assert set(tools) == CANONICAL_TOOLS
                assert ('style_doc' in json.dumps(tools['render_structure'].inputSchema)) is (profile == 'local')
                assert 'rerender' not in json.dumps(tools['render_structure'].inputSchema)
                assert (await client.call_tool('list_presets', {})).structured_content == presets.compact_catalog()
    asyncio.run(run())


def test_client_recovery_precedes_missing_local_path_and_is_owner_scoped(consumer, tmp_path):
    from fastapi.testclient import TestClient
    from blender_render.api import create_app
    from blender_render.client import Client, ClientError
    from test_jobs_api import TOKEN, SECOND_TOKEN
    service, conversions, downloads = consumer
    http = TestClient(create_app(service, token=TOKEN, tokens={TOKEN: 'alice', SECOND_TOKEN: 'other'}))
    class InProcess(Client):
        def request(self, method, path, body=None, **kwargs):
            response = http.request(method, path, headers={'Authorization': 'Bearer ' + self.token},
                **({'content': body} if kwargs.get('raw') else {'json': body}))
            if response.status_code >= 400:
                raise ClientError(response.json()['error'], response.status_code, response.json().get('code'))
            return response.json()
    client = InProcess('http://127.0.0.1', TOKEN)
    path = tmp_path / 'public.cif'
    path.write_bytes(b'public')
    request = {'request_key': 'local-once', 'preset': 'pastel'}
    try:
        job = client.render_consumer_file(path, request)
        path.unlink()
        assert client.render_consumer_file(path, request)['job_id'] == job['job_id']
        assert client.recover_render(request)['job_id'] == job['job_id']
        assert InProcess('http://127.0.0.1', SECOND_TOKEN).recover_render(request) is None
        with pytest.raises(ClientError) as error:
            client.recover_render({**request, 'visual_brief': {'composition': 'depth_of_field'}})
        assert error.value.code == 'operation_conflict'
        assert not conversions and not service.backend.calls
    finally:
        http.close()


def test_stdio_lost_response_retry_uses_existing_job(consumer, tmp_path, monkeypatch):
    from blender_render.client import Client
    from blender_render import consumer as contract
    service, conversions, downloads = consumer
    path = tmp_path / 'public.cif'
    path.write_bytes(b'public')
    class InProcess(Client):
        def recover_render(self, request):
            return service.recover_render('alice', request, trusted=True)
        def request(self, method, route, body=None, **kwargs):
            if route == '/v1/consumer/preflight':
                plan = service.consumer_preflight('alice', body, trusted=True)
                assert contract.executable(plan['decision'])
                return {'ready': True}
            assert route.startswith('/v1/render-files/')
            size = int.from_bytes(body[:4], 'big')
            request = json.loads(body[4:4 + size])['consumer_request']
            plan = service.consumer_preflight('alice', request, trusted=True)
            return service.admit_consumer_file('alice', body[4 + size:], '.cif', 'public.cif', service.converter, plan)
    connection = InProcess('http://127.0.0.1', 'fake')
    monkeypatch.setattr('blender_render.client.configured_client', lambda *a, **k: connection)
    async def run():
        async with MCPClient(mcp_server.create_stdio()) as client:
            args = {'path': str(path), 'request': {'request_key': 'handoff'}}
            first = (await client.call_tool('render_structure', args)).structured_content
            path.unlink()
            monkeypatch.setattr(selection, 'select_preset', lambda *a, **k: pytest.fail('Retry reranked'))
            retry = (await client.call_tool('render_structure', args)).structured_content
            assert retry['job_id'] == first['job_id'] and retry['reused']
    asyncio.run(run())
    assert not conversions and not service.backend.calls


def test_configured_non_executable_never_starts_controller_or_reads_path(tmp_path, monkeypatch):
    from blender_render import client
    def absent(*args, **kwargs):
        try:
            raise FileNotFoundError('No cached controller')
        except FileNotFoundError as exc:
            raise RuntimeError('No existing controller') from exc
    monkeypatch.setattr(client.ConfiguredClient, 'recover_render', absent)
    monkeypatch.setattr(client, '_connect_client', lambda *a, **k: pytest.fail('Selection refusal started a controller'))
    request = {'request_key': 'refused', 'preset': 'pastel', 'hard_requirements': {'representations': ['wire']}}
    result = client.configured_client('local', admission=True).render_consumer_file(tmp_path / 'absent.cif', request)
    assert not result['accepted'] and result['render_spec_sha256'] is None


def test_existing_controller_connection_failure_never_becomes_new_submit(monkeypatch):
    from blender_render import client
    def lost(*a, **k):
        raise client.ClientError('Lost recovery response')
    monkeypatch.setattr(client.ConfiguredClient, 'recover_render', lost)
    monkeypatch.setattr(client, '_connect_client', lambda *a, **k: pytest.fail('Ambiguous retry started a service'))
    with pytest.raises(client.ClientError, match='Lost recovery'):
        client.configured_client('local').render_consumer_file('not-read.cif', {'request_key': 'ambiguous'})


def test_stdio_partial_result_keeps_verified_scene_without_png(monkeypatch):
    class Partial:
        def download(self, job_id, output):
            return {'job_id': job_id, 'outputs': {'render.blend': 'verified-scene.blend'},
                    'complete': False, 'missing_required': ['render.png'], 'artifacts': [],
                    'error_code': 'artifact_unavailable', 'next_action': 'recover_verified_local_copy_or_stop',
                    'manifest': {'private_notes': 'not part of compact result'}}
        def status(self, job_id):
            return {'preset_decision': None, 'style_source': None, 'render_spec_sha256': 'a' * 64}
    monkeypatch.setattr('blender_render.client.configured_client', lambda *a, **k: Partial())
    async def run():
        async with MCPClient(mcp_server.create_stdio()) as client:
            result = await client.call_tool('render_result', {'job_id': 'existing', 'output_path': 'out.png'})
            assert not result.structured_content['complete']
            assert result.structured_content['outputs'] == {'render.blend': 'verified-scene.blend'}
            assert result.structured_content['missing_required'] == ['render.png']
            assert not any(item.type == 'image' for item in result.content)
            assert 'private_notes' not in str(result.content)
    asyncio.run(run())


@pytest.mark.parametrize('hints', [None, {'chain': {'Z': '#FFFFFF'}}])
def test_requested_color_follow_needs_applicable_input_hints(consumer, payload, hints):
    service, conversions, downloads = consumer
    if hints is not None:
        payload['color_hints'] = hints
    request = {'request_key': 'follow-only', 'preset': 'pastel', 'color_follow': True}
    plan = service.consumer_preflight('alice', request)
    job = service.admit_consumer_file('alice', b'public', '.cif', 'public.cif', service.converter, plan)
    service._execute(service.store.claim())
    state = service.store.get('alice', job['job_id'])
    assert state['status'] == 'failed' and state['selection_phase'] == 'refused'
    assert state['render_spec_sha256'] is None and not state['backend_render_started']
    assert not service.backend.calls


@pytest.mark.parametrize('mix', [0, .65, 1])
def test_color_follow_with_hints_preserves_exact_resolver_semantics(consumer, payload, mix):
    from blender_render import consumer as contract
    service, conversions, downloads = consumer
    payload['view']['color_hints'] = {'chain': {'A': '#AA1234'}}
    request = {'request_key': 'follow-active', 'preset': 'pastel', 'color_follow': True,
               'overrides': {'color_follow': {'mix': mix}}}
    plan = service.consumer_preflight('alice', request)
    expected = prepare_options(contract.render_options(plan['request']), payload, public=True, timeout=600)
    job = service.admit_consumer_file('alice', b'public', '.cif', 'public.cif', service.converter, plan)
    service._execute(service.store.claim())
    state = service.store.get('alice', job['job_id'])
    assert state['status'] == 'done' and state['render_spec_sha256'] == expected['render_spec']['sha256']
    assert expected['style_doc']['color_follow']['mix'] == mix


def test_catalog_change_after_admission_cannot_bind_a_different_decision(consumer):
    service, conversions, downloads = consumer
    plan = service.consumer_preflight('alice', {'request_key': 'older-catalog', 'preset': 'pastel'})
    job = service.admit_consumer_file('alice', b'public', '.cif', 'public.cif', service.converter, plan)
    # A queued record from an older installed catalog retains its prior digest.
    old = json.loads(service.store.db.execute('SELECT consumer_metadata FROM jobs').fetchone()[0])
    old['preset_decision']['catalog_digest'] = '0' * 64
    old['preselection_decision']['catalog_digest'] = '0' * 64
    old['style_identity']['catalog_digest'] = '0' * 64
    service.store.db.execute('UPDATE jobs SET consumer_metadata=?', (json.dumps(old),))
    service._execute(service.store.claim())
    state = service.store.get('alice', job['job_id'])
    assert state['status'] == 'failed' and state['selection_phase'] == 'refused'
    assert state['render_spec_sha256'] is None and not service.backend.calls
    assert state['selection_error'] == 'catalog_changed'


def test_stdio_verified_offline_result_does_not_require_status(monkeypatch):
    from blender_render.client import ClientError
    class Offline:
        def download(self, job_id, output):
            return {'job_id': job_id, 'outputs': {'render.blend': 'verified.blend'},
                    'complete': False, 'missing_required': ['render.png'], 'artifacts': [],
                    'error_code': 'artifact_unavailable', 'next_action': 'recover_verified_local_copy_or_stop',
                    'preset_decision': None, 'render_spec_sha256': 'a' * 64}
        def status(self, job_id):
            raise ClientError('Controller offline after verified receipt recovery')
    monkeypatch.setattr('blender_render.client.configured_client', lambda *a, **k: Offline())
    async def run():
        async with MCPClient(mcp_server.create_stdio()) as client:
            result = await client.call_tool('render_result', {'job_id': 'retained', 'output_path': 'out.png'})
            assert result.structured_content['outputs'] == {'render.blend': 'verified.blend'}
            assert result.structured_content['render_spec_sha256'] == 'a' * 64
    asyncio.run(run())


def test_filtered_catalog_declares_full_digest_scope(consumer):
    service, conversions, downloads = consumer
    service.selection_policy = selection.SelectionPolicy(allowed_presets=['green_paint'])
    async def run():
        async with MCPClient(server(service)) as client:
            catalog = (await client.call_tool('list_presets', {})).structured_content
            assert catalog['digest'] == presets.compact_catalog()['digest']
            assert catalog['digest_scope'] == 'installed_catalog' and catalog['entries_filtered']
            assert [row['qualified_id'] for row in catalog['entries']] == ['builtin/dried_paint_green']
    asyncio.run(run())
    assert not downloads and not conversions and not service.backend.calls


def test_real_receipt_retains_decision_during_offline_recovery(consumer, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from blender_render.api import create_app
    from blender_render.client import ClientError
    from test_result_recovery import LocalClient
    from test_jobs_api import TOKEN
    service, conversions, downloads = consumer
    plan = service.consumer_preflight('alice', {'request_key': 'receipt', 'preset': 'pastel'})
    job = service.admit_consumer_file('alice', b'public', '.cif', 'public.cif', service.converter, plan)
    service._execute(service.store.claim())
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path / 'receipts'))
    http = TestClient(create_app(service, token=TOKEN, tokens={TOKEN: 'alice'}))
    try:
        connection = LocalClient(http)
        first = connection.download(job['job_id'], tmp_path / 'out.png')
        assert first['complete'] and first['preset_decision'] == service.store.get('alice', job['job_id'])['preset_decision']
        def offline(*args, **kwargs):
            raise ClientError('Connection unavailable')
        connection.request = offline
        monkeypatch.setattr(selection, 'select_preset', lambda *a, **k: pytest.fail('Offline recovery selected a preset'))
        second = connection.download(job['job_id'], tmp_path / 'out.png')
        assert second['complete']
        assert second['preset_decision'] == first['preset_decision']
        assert second['render_spec_sha256'] == first['render_spec_sha256']
        assert len(service.backend.calls) == 1
    finally:
        http.close()


@pytest.mark.parametrize('scene', [False, True])
def test_canonical_scene_output_is_explicit_and_result_is_compact(consumer, scene):
    from test_result_recovery import SceneCompute
    service, conversions, downloads = consumer
    service.backend = SceneCompute()
    async def run():
        async with MCPClient(server(service)) as client:
            result = (await client.call_tool('render_structure', {'file': attachment(),
                'request': {'request_key': 'scene-output', 'preset': 'pastel', 'save_blend': scene}})).structured_content
            service._execute(service.store.claim())
            output = (await client.call_tool('render_result', {'job_id': result['job_id']})).structured_content
            assert output['complete']
            assert 'manifest' not in output
            assert ('render.blend' in {artifact['name'] for artifact in output['artifacts']}) is scene
            assert output['preset_decision']['preset_id'] == 'builtin/preset_1_interface'
            assert output['render_spec_sha256']
    asyncio.run(run())


def test_transfer_tool_resumes_only_the_same_child(pair, monkeypatch):
    service, leaf, identifier, events, failures = pair
    monkeypatch.setattr(mcp_server, 'owner', lambda: 'alice')
    failures['download'] = True
    service._execute(service.store.claim())
    assert service.store.get('alice', identifier)['status'] == 'transfer_pending'
    async def run():
        async with MCPClient(server(service)) as client:
            result = (await client.call_tool('recover_transfer', {'job_id': identifier})).structured_content
            assert result['job_id'] == identifier and result['status'] == 'queued'
    asyncio.run(run())
    service._execute(service.store.claim())
    assert service.store.get('alice', identifier)['status'] == 'done'
    assert len(leaf.backend.calls) == 1
    assert sum(path == '/internal/v1/resolved' for _, path in events) == 1


def test_custom_recovery_does_not_revalidate_current_style_schema(consumer, monkeypatch):
    from blender_render import core, spec
    service, conversions, downloads = consumer
    request = {'request_key': 'custom-recovery', 'style_doc': spec.builtin_definition('pastel').style_doc}
    plan = service.consumer_preflight('alice', request, trusted=True)
    job = service.admit_consumer_file('alice', b'public', '.cif', 'public.cif', service.converter, plan)
    service._execute(service.store.claim())
    monkeypatch.setattr(core, 'validate_style_document', lambda *a, **k: pytest.fail('Recovery validated current styles'))
    assert service.recover_render('alice', request, trusted=True)['job_id'] == job['job_id']
    assert service.store.result('alice', job['job_id'], inventory=True)['style_source'] == 'custom'
