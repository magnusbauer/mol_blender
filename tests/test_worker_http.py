from fastapi.testclient import TestClient
from blender_render.worker_http import create_worker
from test_jobs_api import payload
from test_execution_envelope import worker_request


def test_host_cancellation_interrupts_before_waiting_for_socket_lock(monkeypatch):
    import threading
    from blender_render.service import HostBackend
    from blender_render import render_server
    entered, interrupted, cancelled = (threading.Event() for _ in range(3))
    socket_lock = threading.Lock()
    class Worker:
        def interrupt(self):
            interrupted.set()
        def stop(self):
            with socket_lock:
                pass
    def blocked_render(**kwargs):
        with socket_lock:
            entered.set()
            assert interrupted.wait(2), 'Cancellation waited for the render socket lock'
        return {'status':'cancelled'}
    monkeypatch.setattr(render_server, '_process_render_request', blocked_render)
    backend = HostBackend.__new__(HostBackend)
    backend.worker, backend.timeout = Worker(), 600
    results = []
    task = threading.Thread(target=lambda:results.append(backend.render({}, 'a'*32, cancelled.is_set)))
    task.start()
    assert entered.wait(1)
    cancelled.set()
    task.join(timeout=1)
    assert not task.is_alive() and results == [{'status':'cancelled'}]
    backend.stop()


def test_worker_auth_idempotence_and_process_cleanup(tmp_path, payload):
    stopped = []
    roots = []
    class Backend:
        def __init__(self, root):
            roots.append(root)
        def render(self, payload, identifier, cancelled):
            assert payload['keep_temp'] is False
            return {'status':'completed','png_base64':'test'}
        def stop(self):
            stopped.append(True)
    with TestClient(create_worker('x'*40, backend_factory=Backend)) as client:
        assert client.get('/health').status_code == 401
        headers={'Authorization':'Bearer '+'x'*40}
        job='a'*32
        assert client.post('/render/'+job,json=worker_request(payload, job),headers=headers).status_code == 200
        assert stopped == [True] and not roots[0].exists()
        assert client.post('/render/'+job,json=worker_request(payload, job),headers=headers).status_code == 409
        cancelled='b'*32
        assert client.post('/cancel/'+cancelled,headers=headers).status_code == 200
        assert client.post('/render/'+cancelled,json=worker_request(payload, job),headers=headers).status_code == 409


def test_handoff_uses_one_routing_session_and_never_retries_post(payload, monkeypatch, caplog):
    import httpx
    import pytest
    from blender_render.engine import engine_identity, ExecutionUnknown
    from blender_render.worker_http import HTTPBackend
    calls, headers = [], []
    class Client:
        def __init__(self, **kwargs): headers.append(kwargs['headers'])
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, **kwargs):
            calls.append('health')
            return httpx.Response(200, json={'controller_declaration':engine_identity()})
        def post(self, url, **kwargs):
            calls.append('render')
            return httpx.Response(503, text='untrusted response data must not enter logs')
    monkeypatch.setattr(httpx, 'Client', Client)
    job = 'a'*32
    with pytest.raises(ExecutionUnknown):
        HTTPBackend('https://worker.invalid', 'secret-token-value').render(worker_request(payload, job), job, lambda:False)
    assert calls == ['health','render']
    assert headers[0]['Modal-Session-ID'] == job
    assert 'status=503 submitted=True' in caplog.text
    assert 'secret-token-value' not in caplog.text
    assert 'untrusted response data' not in caplog.text


def test_consumer_blocked_job_does_not_look_queued(monkeypatch):
    from blender_render import consumer
    monkeypatch.setattr(consumer.consumer_records, 'status_metadata', lambda job:None)
    result = consumer.compact_job({'job_id':'a'*32,'status':'queued',
        'condition':'execution_unknown','poll_after_seconds':25,'next_action':'inspect_execution'}, 'shared')
    assert result['status'] == 'blocked'
    assert result['poll_after_seconds'] is None
    assert result['next_action'] == 'inspect_execution'


from test_d2_consumer import consumer

def test_health_separates_retained_jobs_from_runnable_queue(consumer):
    from blender_render.api import create_app
    from test_jobs_api import TOKEN
    from test_d2_review import submit
    service, _, _ = consumer
    job = submit(service)
    service.store.block(job['job_id'], 'execution_unknown', 'dispatch')
    with TestClient(create_app(service, token=TOKEN)) as client:
        health = client.get('/health', headers={'Authorization':'Bearer '+TOKEN}).json()
        assert health['queue_depth'] == 0 and health['running'] == 0
        assert health['blocked_jobs'] == 1
    row = service.store.db.execute('SELECT state,blocked_condition FROM jobs WHERE id=?',(job['job_id'],)).fetchone()
    assert tuple(row) == ('queued', 'execution_unknown')
