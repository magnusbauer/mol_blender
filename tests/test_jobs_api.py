"""Security and lifecycle tests use fake compute; no external billing required."""
import base64
import io
import json
import threading
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from blender_render.api import Downloads, create_app
from blender_render.jobs import JobError, JobStore, Policy
from blender_render.service import RenderService, prepare_options, validate_input


TOKEN = "x" * 43
SECOND_TOKEN = "y" * 43


@pytest.fixture
def payload():
    cif = '''data_test
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.auth_seq_id
_atom_site.auth_asym_id
_atom_site.pdbx_PDB_model_num
ATOM 1 C CA . ALA A 1 1 0.0 0.0 0.0 1.0 10.0 1 A 1
'''
    return {"version": "1.0", "cif_text": cif, "dss": {"ran": True},
        "view": {"pymol_view": [1,0,0,0,1,0,0,0,1,0,0,-50,0,0,0,1,100,0], "viewport_px": [800,600], "fov_deg": 20, "orthoscopic": False}}


class Compute:
    def __init__(self, gate=None, fail=False):
        self.calls = []
        self.entered = threading.Event()
        self.gate, self.fail = gate, fail

    def render(self, request, job_id, cancelled):
        self.calls.append(job_id)
        self.entered.set()
        if self.gate:
            while not self.gate.wait(.005):
                if cancelled():
                    raise RuntimeError("Cancelled")
        if self.fail:
            raise RuntimeError("Worker died")
        buffer = io.BytesIO()
        Image.new("RGBA", (4, 3), (30, 100, 45, 0)).save(buffer, format="PNG")
        return {"status": "completed", "png_base64": base64.b64encode(buffer.getvalue()).decode(), "manifest": {}, "runtime_seconds": .01}

    def stop(self):
        if self.gate:
            self.gate.set()


def await_status(store, owner, identifier, desired):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        job = store.get(owner, identifier)
        if job["status"] == desired:
            return job
        time.sleep(.01)
    pytest.fail(str(job))


def test_concurrent_fifo_and_idempotent_retries(tmp_path, payload):
    store = JobStore(tmp_path)
    gate = threading.Event()
    backend = Compute(gate)
    service = RenderService(store, backend)
    source = service.import_payload("alice", payload)
    first = service.submit("alice", source["input_id"], {}, "first")
    replay = service.submit("alice", source["input_id"], {}, "first")
    second = service.submit("bob", service.import_payload("bob", payload)["input_id"], {}, "second")
    assert first["job_id"] == replay["job_id"]
    assert second["queue_position"] == 2
    service.start()
    try:
        await_status(store, "alice", first["job_id"], "running")
        assert backend.entered.wait(1)
        assert backend.calls == [first["job_id"]]
        gate.set()
        await_status(store, "bob", second["job_id"], "done")
        assert backend.calls == [first["job_id"], second["job_id"]]
    finally:
        service.stop()
        store.close()


def test_reserved_budget_prevents_dispatch_and_survives_restart(tmp_path, payload):
    policy = Policy(public=True, budget_usd=.31)
    store = JobStore(tmp_path, policy)
    compute = Compute()
    service = RenderService(store, compute)
    source = service.import_payload("alice", payload)
    with pytest.raises(JobError, match="exhausted"):
        service.submit("alice", source["input_id"], {}, "one")
    assert not compute.calls
    store.close()
    reopened = JobStore(tmp_path, replace(policy, budget_usd=530))
    with pytest.raises(JobError, match="exhausted"):
        reopened.submit("alice", source["input_id"], {}, "new")
    reopened.close()


def test_private_health_and_compatibility_routes_require_owner(tmp_path, payload):
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    with TestClient(create_app(service, token=TOKEN)) as client:
        assert client.get('/health').status_code == 401
        assert client.get('/health', headers={'Authorization':'Bearer '+TOKEN}).status_code == 200
        assert client.get('/status/unknown').status_code == 401
        assert client.get('/result/unknown').status_code == 401
        assert client.post('/stop').status_code == 401
    store.close()


def test_legacy_render_uses_shared_options_and_rejects_unknown(tmp_path, payload):
    store = JobStore(tmp_path)
    captured = []
    class Capture(Compute):
        def render(self, request, *args):
            captured.append(request)
            return super().render(request, *args)
    service = RenderService(store, Capture())
    headers = {'Authorization': 'Bearer ' + TOKEN}
    options = {'preset': 'pastel', 'keep_temp': True,
               'overrides': {'camera': {'yaw_deg': 30}, 'output': {'width': 321}}}
    with TestClient(create_app(service, token=TOKEN)) as client:
        result = client.post('/render', headers=headers, json={'payload': payload, **options})
        assert result.status_code == 200, result.text
        expected = prepare_options(options, payload, public=False, timeout=600)
        assert captured[0]['style_doc'] == expected['style_doc']
        assert captured[0]['keep_temp'] is True
        result = client.post('/render', headers=headers, json={'payload': payload, 'unknown_option': True})
        assert result.status_code == 400 and 'unknown_option' in result.text
        assert len(captured) == 1
    store.close()


def test_raw_file_upload_uses_converter_and_rejects_malformed_before_compute(tmp_path, payload):
    store = JobStore(tmp_path)
    compute = Compute()
    service = RenderService(store, compute)
    converted = []
    def convert(data, suffix):
        converted.append((data,suffix))
        if data == b'broken session':
            raise ValueError('internal converter path must stay private')
        return {**payload, 'cif_text':'data_empty\n'} if data == b'bad' else payload
    with TestClient(create_app(service, token=TOKEN, converter=convert)) as client:
        headers={'Authorization':'Bearer '+TOKEN,'Content-Type':'application/octet-stream'}
        assert client.post('/v1/files/pse', content=b'valid fixture', headers=headers).status_code == 200
        assert converted == [(b'valid fixture','.pse')]
        assert client.post('/v1/files/pse',content=b'bad',headers=headers).status_code == 400
        broken=client.post('/v1/files/pse',content=b'broken session',headers=headers)
        assert broken.status_code == 422
        assert 'internal converter path' not in broken.text
        assert client.post('/v1/files/blend',content=b'BLENDER',headers=headers).status_code == 422
        assert not compute.calls
    store.close()


def test_budget_shutdown_keeps_unexpired_inputs_until_their_retention_deadline(tmp_path, payload):
    from blender_render.budget import OperatingBudget
    now = [1780000000.]
    store = JobStore(tmp_path, Policy(public=True, ttl_seconds=100), clock=lambda:now[0])
    service = RenderService(store, Compute())
    source = service.import_payload('alice',payload)
    budget = OperatingBudget(store,cap_usd=0)
    stopped=[]
    waited=[]
    def wait(seconds):
        if now[0] < 1780000100:
            assert store.input('alice',source['input_id'])
        waited.append(seconds)
        now[0] += seconds
    budget.stop_unfunded(service,lambda:stopped.append(True),wait=wait)
    assert waited and stopped == [True]
    assert not list(store.inputs.iterdir())
    store.close()


def test_reservation_is_durable_before_claim(tmp_path, payload):
    snapshots = []
    store = JobStore(tmp_path, Policy(public=True), sync=lambda: snapshots.append("committed"))
    identifier = store.add_input("a", payload)["input_id"]
    store.submit("a", identifier, {}, "x")
    count = len(snapshots)
    assert store.quota("a")["reserved_usd"] == pytest.approx(.312)
    assert store.claim()
    assert len(snapshots) == count + 1
    store.close()


def test_volume_commit_failure_closes_admission(tmp_path, payload):
    store = JobStore(tmp_path)
    source = store.add_input("a", payload)
    def broken():
        raise OSError("storage disconnected")
    store.sync = broken
    with pytest.raises(OSError):
        store.submit("a", source["input_id"], {}, "one")
    assert not store.healthy
    with pytest.raises(JobError, match="unavailable"):
        store.claim()
    store.close()


def test_unknown_execution_is_never_replayed_or_refunded(tmp_path, payload):
    store = JobStore(tmp_path, Policy(public=True))
    source = store.add_input("a", payload)
    one = store.submit("a", source["input_id"], {}, "one")
    store.claim()
    store.checkpoint(one['job_id'], 'dispatch', dispatched=True)
    store.close()
    store = JobStore(tmp_path, Policy(public=True))
    service = RenderService(store, Compute())
    assert store.get("a", one["job_id"])["condition"] == "execution_unknown"
    reservation = store.db.execute('SELECT reserved,charged FROM jobs').fetchone()
    assert reservation['reserved'] == 312000 and reservation['charged'] == 0
    assert store.claim() is None
    store.close()


def test_owner_isolation_and_authenticated_artifacts(tmp_path, payload):
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    app = create_app(service, token=TOKEN, tokens={TOKEN:"a", SECOND_TOKEN:"b"})
    a, b = {"Authorization":"Bearer " + TOKEN}, {"Authorization":"Bearer " + SECOND_TOKEN}
    with TestClient(app) as client:
        assert client.post("/v1/structures", json={"payload":payload}).status_code == 401
        source = client.post("/v1/structures", json={"payload":payload}, headers=a).json()
        assert client.post("/v1/jobs", json={"input_id":source["input_id"], "idempotency_key":"bad"}, headers=b).status_code == 404
        job = client.post("/v1/jobs", json={"input_id":source["input_id"], "idempotency_key":"one"}, headers=a).json()
        await_status(store, "a", job["job_id"], "done")
        path = "/v1/jobs/" + job["job_id"]
        assert client.get(path, headers=b).status_code == 404
        assert client.get(path + "/artifacts/render.png", headers=b).status_code == 404
        result = client.get(path + "/result", headers=a).json()
        url = result["artifacts"][0]["download_url"]
        assert client.get(url).headers["content-type"] == "image/png"
        assert client.get(url[:-1] + "z").status_code == 401
        assert client.get(path + "/artifacts/secrets", headers=a).status_code == 404
    store.close()


def test_running_and_queued_cancellation(tmp_path, payload):
    store = JobStore(tmp_path)
    gate = threading.Event()
    service = RenderService(store, Compute(gate))
    source = service.import_payload("a", payload)
    first = service.submit("a", source["input_id"], {}, "one")
    second = service.submit("a", source["input_id"], {}, "two", rerender=True)
    assert store.cancel("a", second["job_id"])["status"] == "cancelled"
    service.start()
    try:
        await_status(store, "a", first["job_id"], "running")
        assert service.backend.entered.wait(1)
        store.cancel("a", first["job_id"])
        final = await_status(store, "a", first["job_id"], "cancelled")
        assert final['error'] == 'Render cancelled by a user or operator request.'
        assert final['next_action'] == 'stop'
        assert len(service.backend.calls) == 1
    finally:
        service.stop()
        store.close()


def test_daily_quota_credit_expiry_and_retention(tmp_path, payload):
    now = [1800000000.0]
    store = JobStore(tmp_path, Policy(public=True, ttl_seconds=10), clock=lambda: now[0])
    for i in range(10):
        source = store.add_input('a', payload)
        job = store.submit('a', source['input_id'], {}, str(i), rerender=True)
        store.claim(); store.finish(job['job_id'], seconds=600)
        assert not store.list_inputs('a')['structures']
    source = store.add_input('a', payload)
    with pytest.raises(JobError, match="daily"):
        store.submit('a', source['input_id'], {}, 'eleven', rerender=True)
    store.delete_input('a', source['input_id'])
    now[0] += 11
    store.cleanup()
    assert not list(store.inputs.iterdir())
    with pytest.raises(JobError, match="expired"):
        store.result('a', job['job_id'])
    now[0] = store.policy.expires_at
    with pytest.raises(JobError, match="expired"):
        store.add_input("a", payload)
    store.close()


def test_options_cannot_bypass_public_limits(payload):
    for options in ({"width":4097}, {"height":4097}, {"samples":513}, {"raw_view":True}, {"width":True}, {"script":"evil"}, [], {"rough":"false"}):
        with pytest.raises(JobError):
            prepare_options(options, payload, public=True, timeout=600)


def test_changed_retry_keys_reserve_and_dispatch_only_once(tmp_path, payload):
    from concurrent.futures import ThreadPoolExecutor
    store = JobStore(tmp_path, Policy(public=True))
    service = RenderService(store, Compute())
    source = service.import_payload('a', payload)
    settings = ({}, {'rough': False, 'raw_view': False, 'color_follow': False,
                     'preserve_view': True, 'keep_temp': False, 'preset': 'preset_1_interface'})
    with ThreadPoolExecutor(max_workers=10) as pool:
        jobs = list(pool.map(lambda i: service.submit('a', source['input_id'], settings[i % 2], 'retry-'+str(i)), range(10)))
    identifier = jobs[0]['job_id']
    assert {j['job_id'] for j in jobs} == {identifier}
    assert sum(not j['reused'] for j in jobs) == 1
    assert store.quota('a')['reserved_usd'] == pytest.approx(.312)
    assert store.quota('a')['renders_remaining_today'] == 9
    service.start()
    try:
        await_status(store, 'a', identifier, 'done')
        assert service.backend.calls == [identifier]
        assert store.list_inputs('a')['structures'] == []
        assert service.submit('a', source['input_id'], {}, 'lost-key-after-completion')['job_id'] == identifier
        assert service.submit('a', source['input_id'], settings[1], 'retry-1')['next_action'] == 'render_result'
        with pytest.raises(JobError) as exc:
            service.submit('b', source['input_id'], {}, 'retry-1')
        assert exc.value.status == 404
        with pytest.raises(JobError, match='different request'):
            service.submit('a', source['input_id'], {'rough': True}, 'retry-1')
    finally:
        service.stop()
        store.close()
    store = JobStore(tmp_path, Policy(public=True))
    service = RenderService(store, Compute())
    assert service.submit('a', source['input_id'], {}, 'lost-key-after-completion')['job_id'] == identifier
    assert store.claim() is None
    store.close()


def test_effective_settings_and_reimport_reuse_available_result(tmp_path, payload):
    store = JobStore(tmp_path, Policy(public=True))
    service = RenderService(store, Compute())
    source = service.import_payload('a', payload)
    first = service.submit('a', source['input_id'], {}, 'first')
    # Explicit output dimensions equal to the resolved defaults are identical.
    second = service.submit('a', source['input_id'], {'width': 2048, 'height': 1536}, 'second')
    assert first['job_id'] == second['job_id']
    service.start()
    try:
        await_status(store, 'a', first['job_id'], 'done')
        new_source = service.import_payload('a', {**payload, 'generated_at_utc': 'later'})
        assert new_source['input_id'] != source['input_id']
        replay = service.submit('a', new_source['input_id'], {}, 'new-import')
        assert replay['job_id'] == first['job_id'] and replay['reused']
        assert store.list_inputs('a')['structures'] == []
        assert service.submit('a', new_source['input_id'], {}, 'new-import')['job_id'] == first['job_id']
        assert service.submit('a', new_source['input_id'], {}, 'another-key')['job_id'] == first['job_id']
        assert service.backend.calls == [first['job_id']]
        for item in store.result('a', first['job_id'])['artifacts']:
            store.artifact_delivered('a', first['job_id'], item['name'])
        # The local adapter's import/submit pair must also stop after delivery.
        third_source = service.import_payload('a', payload)
        delivered = service.submit('a', third_source['input_id'], {}, 'after-download')
        assert delivered['job_id'] == first['job_id'] and delivered['next_action'] == 'stop'
        assert store.list_inputs('a')['structures'] == []
    finally:
        service.stop()
        store.close()


def test_intentional_rerender_different_settings_and_camera_are_distinct(tmp_path, payload):
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    source = service.import_payload('a', payload)
    first = service.submit('a', source['input_id'], {}, 'first')
    repeat = service.submit('a', source['input_id'], {}, 'explicit-repeat', rerender=True)
    assert repeat['job_id'] != first['job_id']
    assert service.submit('a', source['input_id'], {}, 'explicit-repeat', rerender=True)['job_id'] == repeat['job_id']
    changed = service.submit('a', source['input_id'], {'width': 1024}, 'changed-options')
    pose = {**payload, 'view': {**payload['view'], 'orthoscopic': True}}
    changed_input = service.import_payload('a', pose)
    moved = service.submit('a', changed_input['input_id'], {}, 'changed-camera')
    other = service.submit('b', service.import_payload('b', payload)['input_id'], {}, 'first')
    assert len({j['job_id'] for j in (first, repeat, changed, moved, other)}) == 5
    store.close()


def test_one_call_file_retries_do_not_reconvert_after_input_deletion(tmp_path, payload):
    from concurrent.futures import ThreadPoolExecutor
    now = [1800000000.0]
    store = JobStore(tmp_path, Policy(public=True), clock=lambda: now[0])
    service = RenderService(store, Compute())
    converted = []
    def converter(data, suffix):
        converted.append(data)
        return payload
    def render(_):
        return service.render_file('a', b'public fixture', '.cif', 'test.cif', converter, {})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(render, range(4)))
    identifier = results[0]['job_id']
    assert {r['job_id'] for r in results} == {identifier}
    assert converted == []  # Durable job precedes conversion.
    service.start()
    try:
        await_status(store, 'a', identifier, 'done')
        assert store.list_inputs('a')['structures'] == []
        assert render(None)['job_id'] == identifier
        for item in store.result('a', identifier)['artifacts']:
            store.artifact_delivered('a', identifier, item['name'])
        delivered = render(None)
        assert delivered['status'] == 'delivered' and delivered['next_action'] == 'stop'
        assert converted == [b'public fixture']
        assert service.backend.calls == [identifier]
    finally:
        service.stop()
    # Expiry is not authorization. Only an explicit repeat creates a new job.
    now[0] += 901
    store.cleanup()
    assert render(None)['job_id'] == identifier
    repeated = service.render_file('a', b'public fixture', '.cif', 'test.cif', converter, {},
                                   rerender=True, operation_key='additional-render')
    assert repeated['job_id'] != identifier
    assert len(converted) == 1
    store.close()


def test_old_submission_key_survives_hash_normalization_upgrade(tmp_path, payload):
    import hashlib
    store = JobStore(tmp_path)
    source = store.add_input('a', payload)
    old_hash = hashlib.sha256(b'{}').hexdigest()
    first = store.submit('a', source['input_id'], {}, 'old', client_hash=old_hash)
    store.claim(); store.finish(first['job_id'], result={'artifacts': []})
    service = RenderService(store, Compute())
    assert service.submit('a', source['input_id'], {}, 'old')['job_id'] == first['job_id']
    store.close()


def test_mcp_single_call_retry_status_and_native_result(tmp_path, payload, monkeypatch):
    import asyncio
    from fastmcp import Client as MCPClient
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
    import blender_render.mcp_server as mcp_server
    import blender_render.uploads as uploads
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    monkeypatch.setattr(mcp_server, 'owner', lambda: 'a')
    monkeypatch.setattr(uploads, 'download_file', lambda *a, **kw: b'fixture')
    conversions = []
    def convert(data, suffix):
        conversions.append(suffix)
        return payload
    server = mcp_server.create_http(service, auth=StaticTokenVerifier(tokens={}),
        downloads=Downloads('x'*48, 'https://render.example.org'), converter=convert)
    async def run():
        async with MCPClient(server) as client:
            args = {'file': {'download_url': 'https://example.org/test.cif', 'file_id': 'fixture', 'file_name': 'test.cif'},
                    'request': {'request_key': 'single-call', 'preset': 'dried_paint_green', 'transparent_background': True}}
            first = (await client.call_tool('render_structure', args)).structured_content
            service.start()
            state = (await client.call_tool('render_status', {'job_id': first['job_id'], 'wait_seconds': 1})).structured_content
            assert state['status'] == 'done' and state['next_action'] == 'render_result'
            repeated = (await client.call_tool('render_structure', args)).structured_content
            assert repeated['job_id'] == first['job_id'] and repeated['reused']
            result = await client.call_tool('render_result', {'job_id': first['job_id']})
            assert any(item.type == 'image' for item in result.content)
            assert not any(item.type == 'resource_link' for item in result.content)
            assert any(item.type == 'text' and '[Download PNG](https://' in item.text for item in result.content)
            assert any(item.type == 'text' and f"[View PNG preview]({result.structured_content['preview_url']})" in item.text for item in result.content)
            assert result.structured_content['preview_url'].startswith('https://render.example.org/')
            assert conversions == ['.cif'] and service.backend.calls == [first['job_id']]
    try:
        asyncio.run(run())
    finally:
        service.stop()
        store.close()


def test_uploads_have_no_count_quota_and_pending_retries_deduplicate(tmp_path, payload):
    from concurrent.futures import ThreadPoolExecutor
    store = JobStore(tmp_path, Policy(public=True))
    service = RenderService(store, Compute())
    converted = []
    def converter(data, suffix):
        converted.append(data)
        return {**payload, 'selection': data.decode()}
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: service.import_file('a', b'one', '.pse', 'icon.pse', converter), range(4)))
    assert len({r['input_id'] for r in responses}) == 1
    assert converted == [b'one']
    for i in range(12):
        service.import_file('a', str(i).encode(), '.pse', 'icon.pse', converter)
    quota = store.quota('a')
    assert quota['pending_uploads'] == 13
    assert quota['renders_remaining_today'] == 10
    assert quota['upload_slots_remaining'] is None
    assert quota['conversions_remaining_today'] is None
    assert quota['estimated_spent_usd'] == pytest.approx(.13)
    store.close()


def test_tester_exemption_keeps_project_budget_and_other_users_limited(tmp_path, payload):
    store = JobStore(tmp_path, Policy(public=True, quota_exempt_owners=('github:11480757',)))
    for i in range(12):
        source = store.add_input('github:11480757', payload)
        job = store.submit('github:11480757', source['input_id'], {}, str(i), rerender=True)
        store.claim(); store.finish(job['job_id'], seconds=10)
    assert store.quota('github:11480757')['renders_remaining_today'] is None
    assert store.quota('github:11480757')['testing_quota_exempt']
    assert store.quota('other')['renders_remaining_today'] == 10
    assert not store.quota('other')['testing_quota_exempt']
    assert store.quota('github:11480757')['estimated_spent_usd'] > 0
    with store.transaction() as db:
        db.execute("UPDATE settings SET value='0' WHERE key='budget_micro'")
    with pytest.raises(JobError, match='exhausted'):
        store.add_input('github:11480757', payload)
    store.close()


def test_quota_reserves_queued_slots_but_failure_and_cancel_do_not_count(tmp_path, payload):
    store = JobStore(tmp_path, Policy(public=True))
    source = store.add_input('a', payload)
    jobs = [store.submit('a', source['input_id'], {}, str(i), rerender=True) for i in range(10)]
    assert store.quota('a')['renders_remaining_today'] == 0
    with pytest.raises(JobError, match='daily'):
        store.submit('a', source['input_id'], {}, 'eleven', rerender=True)
    store.cancel('a', jobs[-1]['job_id'])
    store.claim(); store.finish(jobs[0]['job_id'], error='Worker failed', seconds=10)
    assert store.quota('a')['renders_remaining_today'] == 2
    store.close()


def test_input_privacy_delivery_and_submission_retry_after_deletion(tmp_path, payload):
    store = JobStore(tmp_path/'state', data_root=tmp_path/'ephemeral')
    service = RenderService(store, Compute())
    headers = {'Authorization':'Bearer '+TOKEN}
    with TestClient(create_app(service, token=TOKEN)) as client:
        source = client.post('/v1/structures', json={'payload':payload,'name':'icon.pse'}, headers=headers).json()
        body = {'input_id':source['input_id'],'options':{},'idempotency_key':'once'}
        job = client.post('/v1/jobs', json=body, headers=headers).json()
        await_status(store, 'local', job['job_id'], 'done')
        assert not store.inputs.joinpath(source['input_id']+'.json').exists()
        assert store.list_inputs('local')['structures'] == []
        assert client.post('/v1/jobs', json=body, headers=headers).json()['job_id'] == job['job_id']
        assert client.post('/v1/jobs', json={**body,'options':{'rough':True}}, headers=headers).status_code == 409
        row = store.db.execute('SELECT result,options FROM jobs WHERE id=?',(job['job_id'],)).fetchone()
        assert 'manifest' not in json.loads(row['result'])
        assert row['options'] == '{}'
        path = '/v1/jobs/'+job['job_id']
        result = client.get(path+'/result', headers=headers).json()
        for artifact in result['artifacts']:
            # Download managers may request chunks before fetching the complete
            # file. A partial response must not destroy the remaining bytes.
            partial = client.get(path+'/artifacts/'+artifact['name'], headers={**headers,'Range':'bytes=0-3'})
            assert partial.status_code == 206
            assert (store.artifacts/job['job_id']/artifact['name']).exists()
            response = client.get(path+'/artifacts/'+artifact['name'], headers=headers)
            assert response.status_code == 200
            assert not (store.artifacts/job['job_id']/artifact['name']).exists()
        assert store.get('local', job['job_id'])['status'] == 'delivered'
        assert client.get(path+'/result', headers=headers).status_code == 410
        assert not list(store.artifacts.iterdir())
        assert b'data_test' not in (tmp_path/'state'/'jobs.sqlite3').read_bytes()
    store.close()


def test_list_and_delete_are_owner_scoped_and_protect_active_inputs(tmp_path, payload):
    store = JobStore(tmp_path)
    source = store.add_input('a', payload, 'icon.pse')
    assert store.list_inputs('b')['structures'] == []
    assert not store.delete_input('b', source['input_id'])['deleted']
    job = store.submit('a', source['input_id'], {}, 'one')
    with pytest.raises(JobError, match='active'):
        store.delete_input('a', source['input_id'])
    store.cancel('a', job['job_id'])
    assert store.list_inputs('a')['structures'] == []
    store.close()


def test_result_deletion_removes_legacy_files_and_metadata_without_refund(tmp_path, payload):
    store = JobStore(tmp_path/'state', Policy(public=True), data_root=tmp_path/'temporary')
    source = store.add_input('a', payload)
    job = store.submit('a', source['input_id'], {'selection':'private_selection'}, 'once')
    identifier = job['job_id']
    with pytest.raises(JobError, match='active'):
        store.delete_result('a', identifier)
    store.claim()
    store.finish(identifier, result={'artifacts':[], 'bytes':6}, seconds=20)
    legacy = store.root/'artifacts'/identifier
    legacy.mkdir(parents=True)
    for name in ('render.png', 'render.blend', 'manifest.json'):
        (legacy/name).write_bytes(b'xx')
    with store.transaction() as db:
        db.execute('UPDATE jobs SET result=?,options=? WHERE id=?',
            (json.dumps({'manifest':{'selection':'legacy_private_selection'}}),
             json.dumps({'selection':'legacy_private_selection'}), identifier))
    before = store.quota('a')
    with pytest.raises(JobError) as exc:
        store.delete_result('b', identifier)
    assert exc.value.status == 404 and legacy.is_dir()
    assert store.delete_result('a', identifier) == {'job_id':identifier, 'deleted':True, 'files_deleted':3, 'bytes_deleted':6}
    assert not legacy.exists()
    assert store.quota('a') == before
    row = store.db.execute('SELECT result,options FROM jobs WHERE id=?',(identifier,)).fetchone()
    assert row['result'] is None and row['options'] == '{}'
    assert b'legacy_private_selection' not in (store.root/'jobs.sqlite3').read_bytes()
    assert not store.delete_result('a', identifier)['deleted']
    with pytest.raises(JobError) as exc:
        store.result('a', identifier)
    assert exc.value.status == 410
    store.close()


def test_authenticated_result_deletion_revokes_downloads(tmp_path, payload):
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    a, b = {'Authorization':'Bearer '+TOKEN}, {'Authorization':'Bearer '+SECOND_TOKEN}
    with TestClient(create_app(service, token=TOKEN, tokens={TOKEN:'a', SECOND_TOKEN:'b'})) as client:
        source = service.import_payload('a', payload)
        job = service.submit('a', source['input_id'], {}, 'once')
        await_status(store, 'a', job['job_id'], 'done')
        path = '/v1/jobs/'+job['job_id']+'/result'
        url = client.get(path, headers=a).json()['artifacts'][0]['download_url']
        assert client.delete(path).status_code == 401
        assert client.delete(path, headers=b).status_code == 404
        response = client.delete(path, headers=a)
        assert response.status_code == 200 and response.json()['files_deleted'] == 2
        assert client.get(url).status_code == 410
        assert not list(store.artifacts.iterdir())
    store.close()


def test_malformed_replay_keys_fail_before_database_or_dispatch(tmp_path, payload):
    store = JobStore(tmp_path)
    backend = Compute()
    service = RenderService(store, backend)
    source = service.import_payload('local', payload)
    headers = {'Authorization':'Bearer '+TOKEN}
    with TestClient(create_app(service, token=TOKEN)) as client:
        for key in (None, [], {}, 42, '', 'x'*129):
            response = client.post('/v1/jobs', headers=headers,
                json={'input_id':source['input_id'], 'idempotency_key':key})
            assert response.status_code == 400
        response = client.post('/v1/jobs', headers=headers,
            json={'input_id':[], 'idempotency_key':'valid'})
        assert response.status_code == 400
        assert not backend.calls
    store.close()


def test_abandoned_upload_cleanup_and_new_worker_restart_loses_no_privacy_data(tmp_path, payload):
    now = [1780000000.]
    store = JobStore(tmp_path/'state', data_root=tmp_path/'first', clock=lambda:now[0])
    source = store.add_input('a', payload)
    now[0] += 301
    store.cleanup()
    assert store.list_inputs('a')['structures'] == []
    source = store.add_input('a', payload)
    job = store.submit('a', source['input_id'], {}, 'one')
    store.close()
    restarted = JobStore(tmp_path/'state', data_root=tmp_path/'second', clock=lambda:now[0])
    service = RenderService(restarted, Compute())
    assert restarted.get('a', job['job_id'])['condition'] == 'recovery_required'
    assert restarted.db.execute('SELECT input_id FROM jobs WHERE id=?', (job['job_id'],)).fetchone()[0] == source['input_id']
    assert restarted.claim() is None
    assert service.backend.calls == []
    restarted.close()
    normal = prepare_options({"save_blend":True}, payload, public=True, timeout=600)
    assert normal["style_doc"]["output"]["width"] == 2048
    assert normal["style_doc"]["output"]["save_blend"] is True
    assert normal["style_doc"]["renderer"]["require_gpu"] is True


def test_malformed_payload_and_expiring_links(payload):
    for value in (None, {}, {**payload, "cif_text":"data_invalid"}):
        with pytest.raises(JobError):
            validate_input(value, max_atoms=100, max_bytes=100000)
    with pytest.raises(JobError) as exc:
        validate_input(payload, max_atoms=100, max_bytes=1)
    assert exc.value.status == 413
    clock = [100]
    links = Downloads(TOKEN, "https://example.org", clock=lambda: clock[0])
    signed = links.url("a", "job", "render.png", ttl=10).rsplit("/",1)[1]
    assert links.verify(signed) == ("a", "job", "render.png")
    clock[0] = 110
    with pytest.raises(JobError) as exc:
        links.verify(signed)
    assert exc.value.status == 410
