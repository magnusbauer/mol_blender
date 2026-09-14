from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

import blender_render.remote_client as remote_client


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]):
        self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._data


@pytest.mark.parametrize('explicit', [False, True])
def test_legacy_client_uses_async_api_without_resubmission(monkeypatch, explicit):
    from blender_render.client import Client
    calls = []
    def request(self, method, path, body=None, *, binary=False):
        calls.append((method, path, body))
        assert self.url == 'http://127.0.0.1:9000'
        if path == '/v1/structures': return {'input_id':'input'}
        if path == '/v1/jobs': return {'job_id':'job'}
        if path == '/v1/jobs/job': return {'status':'done', 'queue_position':0}
        if path == '/v1/jobs/job/result':
            return {'manifest':{}, 'artifacts':[{'name':'render.png'}]}
        if path == '/v1/jobs/job/artifacts/render.png':
            assert binary
            return b'pngdata'
        raise AssertionError(path)
    monkeypatch.setattr(Client, 'request', request)

    result = remote_client.submit_render(
        host="127.0.0.1",
        port=9000,
        payload={"view": {"pymol_view": [0.0] * 18}, "cif_text": "data_test\n", "pdb_text": "ATOM 1"},
        preset="preset_1_interface",
        timeout=33,
        **({'color_follow': False, 'raw_view': False, 'save_blend': False} if explicit else {}),
    )

    assert result["status"] == "completed"
    submits = [body for method, path, body in calls if path == '/v1/jobs']
    assert len(submits) == 1
    assert submits[0]['options']['preset'] == 'preset_1_interface'
    for key in ('color_follow', 'raw_view', 'save_blend'):
        if explicit:
            assert submits[0]['options'][key] is False
        else:
            assert key not in submits[0]['options']
    assert base64.b64decode(result['png_base64']) == b'pngdata'


def test_write_png_response(tmp_path: Path):
    output = tmp_path / "render.png"
    doc = {"png_base64": base64.b64encode(b"abc").decode("ascii")}

    written = remote_client.write_png_response(response_doc=doc, output_png=output)

    assert written == output
    assert output.read_bytes() == b"abc"


def test_decode_png_invalid_payload_raises():
    with pytest.raises(remote_client.RemoteRenderError):
        remote_client.decode_png_bytes({"png_base64": "###notbase64###"})


@pytest.mark.parametrize('state', ['delivered', 'deleted'])
def test_wait_stops_when_privacy_cleanup_removed_result(monkeypatch, state):
    from blender_render.client import Client, ClientError
    client = Client('http://127.0.0.1:9000', 'unused')
    monkeypatch.setattr(
        client,
        'status',
        lambda _, **_kwargs: {'status':state, 'queue_position':0},
    )
    monkeypatch.setattr('blender_render.client.time.sleep', lambda _: pytest.fail('Terminal job must not keep polling'))
    with pytest.raises(ClientError, match=state):
        client.wait('job')


def test_wait_bounds_terminal_result_by_same_deadline(monkeypatch):
    from blender_render.client import Client, ClientError

    client = Client('http://127.0.0.1:9000', 'unused', timeout=30)
    calls = []

    def request(method, path, body=None, **kwargs):
        calls.append((path, client.timeout))
        if path == '/v1/jobs/job':
            return {'status': 'done', 'queue_position': 0}
        if path == '/v1/jobs/job/result':
            time.sleep(.03)
            return {'job_id': 'job'}
        raise AssertionError(path)

    monkeypatch.setattr(client, 'request', request)
    with pytest.raises(ClientError) as caught:
        client.wait('job', timeout=.01)

    assert caught.value.code == 'operation_timeout'
    assert caught.value.job_id == 'job'
    assert caught.value.next_action == 'render_result'
    assert [path for path, _ in calls] == [
        '/v1/jobs/job', '/v1/jobs/job/result'
    ]
    assert all(0 < timeout <= .011 for _, timeout in calls)
    assert client.timeout == 30


def test_wait_can_return_terminal_status_without_result_read(monkeypatch):
    from blender_render.client import Client

    client = Client('http://127.0.0.1:9000', 'unused')
    calls = []

    def request(method, path, body=None, **kwargs):
        calls.append(path)
        assert path == '/v1/jobs/job'
        return {'job_id': 'job', 'status': 'done', 'queue_position': 0}

    monkeypatch.setattr(client, 'request', request)
    result = client.wait('job', timeout=1, fetch_result=False)

    assert result['status'] == 'done'
    assert calls == ['/v1/jobs/job']
