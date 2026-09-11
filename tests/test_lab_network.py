import email.message
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from blender_render.client import Client, ClientError
from blender_render import lab_transport

spec = importlib.util.spec_from_file_location('lab_network_proxy', Path(__file__).parents[1] / 'scripts/lab_network_proxy.py')
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)


def test_network_identity_is_explicit_and_scoped_to_workstation():
    networks = [proxy.lab_network('10.159.116.0/22')]
    assert proxy.peer_owner('10.159.117.0', networks) == 'network:10.159.117.0'
    for address in ('10.159.120.1', '192.168.1.2', '127.0.0.1', '8.8.8.8'):
        with pytest.raises(PermissionError):
            proxy.peer_owner(address, networks)
    for cidr in ('0.0.0.0/0', '8.8.8.0/24', '100.64.0.0/16', '::/0'):
        with pytest.raises(ValueError):
            proxy.lab_network(cidr)


@pytest.mark.parametrize('path', ['/v1/admin/stop', '/internal/v1/resolved', '/v1/files/pse',
                                  '/v1/render-files/pse', '/render', '/%76%31/jobs',
                                  'http://elsewhere/v1/jobs', '//elsewhere/v1/jobs'])
def test_operator_and_unsandboxed_conversion_routes_are_blocked(path):
    assert not proxy.allowed_route('POST', path)


@pytest.fixture
def relay(monkeypatch):
    calls = []
    class Upstream:
        def __init__(self, *args, **kwargs):calls.append(('create', args))
        def connect(self):calls.append(('connect',))
        def request(self, *args, **kwargs):calls.append(('request', args, kwargs))
        def getresponse(self):
            return SimpleNamespace(status=200, getheader=lambda key: None, read=lambda count: b'')
        def close(self):calls.append(('close',))
    monkeypatch.setattr(proxy.http.client, 'HTTPConnection', Upstream)
    handler = object.__new__(proxy.Handler)
    handler.server = SimpleNamespace(allowed_networks=[proxy.lab_network('10.159.116.0/22')],
        token='controller-secret-' * 3, upstream_port=12345, max_body=1024, start_command=['start'],
        ensure_upstream=lambda: calls.append(('start',)))
    handler.client_address = ('10.159.117.0', 5555)
    handler.command, handler.path = 'POST', '/v1/jobs'
    handler.headers = email.message.Message()
    handler.headers['Content-Length'] = '2'
    handler.rfile, handler.wfile = io.BytesIO(b'{}'), io.BytesIO()
    handler.send_response = lambda status: calls.append(('response', status))
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    return handler, calls, Upstream


def test_client_cannot_spoof_owner_credential_or_forwarded_address(relay):
    handler, calls, _ = relay
    for key in ('Authorization', 'X-Render-Owner', 'X-Forwarded-For', 'Forwarded', 'Host', 'Cookie'):
        handler.headers[key] = 'attacker-value'
    handler.relay()
    headers = next(item[2]['headers'] for item in calls if item[0] == 'request')
    assert headers['X-Render-Owner'] == 'network:10.159.117.0'
    assert headers['Authorization'] == 'Bearer ' + handler.server.token
    assert not any('attacker' in value for value in headers.values())
    assert 'X-Forwarded-For' not in headers


def test_denied_source_cannot_start_or_contact_renderer(relay):
    handler, calls, _ = relay
    handler.client_address = ('192.168.5.8', 5555)
    handler.headers['X-Forwarded-For'] = '10.159.117.0'
    handler.relay()
    assert calls == [('response', 403)]


@pytest.mark.parametrize('invalid', ['duplicate', 'chunked', 'negative', 'large', 'truncated'])
def test_ambiguous_or_unbounded_bodies_do_not_reach_renderer(relay, invalid):
    handler, calls, _ = relay
    if invalid == 'duplicate':handler.headers['Content-Length'] = '2'
    elif invalid == 'chunked':handler.headers['Transfer-Encoding'] = 'chunked'
    else:handler.headers.replace_header('Content-Length', {'negative':'-1', 'large':'1025', 'truncated':'8'}[invalid])
    handler.relay()
    assert not any(item[0] in {'create', 'start', 'request'} for item in calls)
    assert calls[0][1] in (400, 413)


def test_no_replay_after_ambiguous_submission(relay, monkeypatch):
    handler, calls, upstream = relay
    def fail(self):raise ConnectionResetError()
    monkeypatch.setattr(upstream, 'getresponse', fail)
    handler.relay()
    assert sum(item[0] == 'request' for item in calls) == 1
    assert not any(item[0] == 'start' for item in calls)
    assert ('response', 503) in calls


def test_read_cannot_restart_but_unsent_submission_can(relay, monkeypatch):
    handler, calls, upstream = relay
    def connect(self):
        calls.append(('connect',))
        if not any(item[0] == 'start' for item in calls):raise ConnectionRefusedError()
    monkeypatch.setattr(upstream, 'connect', connect)
    handler.command, handler.path = 'GET', '/health'
    handler.headers.replace_header('Content-Length', '0')
    handler.relay()
    assert not any(item[0] == 'start' for item in calls)
    handler.command, handler.path = 'POST', '/v1/jobs'
    handler.relay()
    assert sum(item[0] == 'start' for item in calls) == 1
    assert sum(item[0] == 'request' for item in calls) == 1


def test_private_token_cannot_be_world_readable_or_symlink(tmp_path):
    token = tmp_path / 'token'
    token.write_text('a' * 40)
    token.chmod(0o600)
    assert proxy.private_token(token) == 'a' * 40
    token.chmod(0o644)
    with pytest.raises(ValueError):proxy.private_token(token)
    alias = tmp_path / 'alias'
    alias.symlink_to(token)
    with pytest.raises(OSError):proxy.private_token(alias)


def test_lab_http_is_explicit_and_never_sends_tokens(monkeypatch):
    with pytest.raises(ClientError):Client('http://lab:8768', '')
    with pytest.raises(ClientError):Client('http://lab:8768', 'secret', lab_http=True)
    client = Client('http://lab:8768', '', lab_http=True)
    assert any(isinstance(handler, lab_transport.LabHTTPHandler) for handler in client.opener.handlers)
    assert not any(getattr(handler, 'proxies', {}) for handler in client.opener.handlers)


def test_configured_lab_profile_uses_shared_client_without_inherited_token(monkeypatch):
    from blender_render import config, client
    monkeypatch.setenv('BLENDER_RENDER_TOKEN', 'an-unrelated-private-credential')
    monkeypatch.setattr(config, 'load_config', lambda: {'profiles': {'lab': {
        'kind': 'lab_http', 'url': 'http://lab:8768', 'private': True}}})
    connection = client._connect_client('lab', read_only=True)
    assert connection.token == ''
    with pytest.raises(ClientError, match='controller dispatch'):
        client._connect_client('lab', controller_owner='someone')


def test_pymol_lab_mode_uses_the_explicit_transport(monkeypatch):
    from blender_render import client, pymol_bridge_plugin as plugin
    calls = []
    monkeypatch.setenv('BLENDER_RENDER_TOKEN', 'an-unrelated-private-credential')
    monkeypatch.setattr(client, 'Client', lambda *args, **kwargs: calls.append((args, kwargs)))
    def stop(*args, **kwargs):raise RuntimeError('stop before export')
    monkeypatch.setattr(plugin, 'build_render_payload', stop)
    with pytest.raises(RuntimeError, match='stop before export'):
        plugin.bridge_render(mode='lab', server_host='lab', server_port=8768, _self=object())
    assert calls == [(('http://lab:8768', ''), {'lab_http': True})]


def test_lab_dns_is_checked_and_connection_pins_the_checked_ip(monkeypatch):
    sockets = []
    def resolved(host, port, *args):
        return [(2, 1, 6, '', ('10.159.117.0', port))]
    monkeypatch.setattr(lab_transport.socket, 'getaddrinfo', resolved)
    monkeypatch.setattr(lab_transport.socket, 'socket', lambda *args:
        SimpleNamespace(connect=lambda address:sockets.append(address), settimeout=lambda value:None))
    lab_transport.private_connection(('lab', 8768), 2)
    assert sockets == [('10.159.117.0', 8768)]
    monkeypatch.setattr(lab_transport.socket, 'getaddrinfo', lambda *args:
        [(2, 1, 6, '', ('10.159.117.0', 8768)), (2, 1, 6, '', ('8.8.8.8', 8768))])
    with pytest.raises(OSError):lab_transport.private_connection(('lab', 8768), 2)
    assert len(sockets) == 1
