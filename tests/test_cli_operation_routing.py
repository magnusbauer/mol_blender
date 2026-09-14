"""C1 CLI closure: in-process admission, fake compute, no live boundaries."""
import json
import socket
import subprocess
import urllib.request
from types import SimpleNamespace

import pytest

from blender_render import cli, client, config, gateway, hpc, runtime
from blender_render.jobs import JobStore
from blender_render.service import RenderService
from test_jobs_api import Compute, payload

_ensure_local = runtime.ensure_local
_connect_hpc = hpc.connect


@pytest.fixture(autouse=True)
def no_live_boundaries(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail('Unmocked process/network/allocation boundary attempted')
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path / 'home'))
    for name in ('Popen', 'run'):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket.socket, 'bind', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', forbidden)
    monkeypatch.setattr(runtime, 'ensure_local', forbidden)
    monkeypatch.setattr(runtime, 'export_file', forbidden)
    monkeypatch.setattr(hpc, 'connect', forbidden)
    monkeypatch.setattr(hpc, 'remote', forbidden)
    monkeypatch.setattr(gateway, 'configured_client', forbidden)
    from blender_render.render_worker_manager import PersistentBlenderWorker
    monkeypatch.setattr(PersistentBlenderWorker, 'ensure_started', forbidden)


class AdmissionClient(client.Client):
    """Exercise production file framing against the real durable service."""
    def __init__(self, service):
        self.service, self.events = service, []
        self.lose_response = False
        self.wait_options = None
        self.download_deadline = None

    def request(self, method, path, body=None, **kwargs):
        assert method == 'POST' and path.startswith('/v1/render-files/')
        size = int.from_bytes(body[:4], 'big')
        envelope = json.loads(body[4:4 + size])
        self.events.append(('admit',))
        job = self.service.render_file('cli', body[4 + size:], '.cif', 'public.cif',
            self.service.converter, envelope.pop('options'), **envelope)
        if self.lose_response:
            self.lose_response = False
            raise KeyboardInterrupt('Response lost after durable commit')
        return job

    def wait(self, identifier, **kwargs):
        self.events.append(('wait', identifier))
        self.wait_options = kwargs
        assert self.service.store.get('cli', identifier)['status'] == 'queued'
        self.service._execute(self.service.store.claim())
        return self.service.store.get('cli', identifier)

    def download(self, identifier, output, *, deadline=None):
        self.events.append(('download', identifier))
        self.download_deadline = deadline
        result = self.service.store.result('cli', identifier)
        return {'job_id': identifier, 'outputs': {'render.png': str(output)}, 'manifest': result['manifest']}


@pytest.fixture
def admission(monkeypatch, tmp_path, payload):
    converted = []
    def convert(data, suffix, **kwargs):
        converted.append((data, suffix, kwargs))
        return payload
    store = JobStore(tmp_path / 'queue')
    service = RenderService(store, Compute(), converter=convert)
    connection = AdmissionClient(service)
    monkeypatch.setattr(client, 'configured_client', lambda *a, **kw: connection)
    path = tmp_path / 'public.cif'
    path.write_text(payload['cif_text'])
    yield connection, service, converted, path
    service.stop()
    store.close()


def run(*args):
    return cli.execute(cli.parser().parse_args(list(args)))


def test_no_wait_one_admission_before_conversion(admission, capsys):
    connection, service, converted, path = admission
    job = run('render', str(path), '--no-wait', '--selection', 'chain A')
    assert connection.events == [('admit',)]
    assert job['job_id'] == job['operation_id']
    assert service.store.get('cli', job['job_id'])['status'] == 'queued'
    assert not converted and not service.backend.calls
    assert capsys.readouterr().err == 'Job ' + job['job_id'] + '\n'


def test_wait_uses_admitted_handle(admission):
    connection, service, converted, path = admission
    result = run('render', str(path), '--selection', 'chain A')
    identifier = result['job_id']
    assert connection.events == [('admit',), ('wait', identifier), ('download', identifier)]
    assert connection.wait_options['fetch_result'] is False
    assert connection.wait_options['deadline'] == connection.download_deadline
    assert service.backend.calls == [identifier]
    assert converted[0][2] == {'selection': 'chain A'}


def test_lost_response_restart_changed_retry_key_one_operation(admission):
    connection, service, converted, path = admission
    connection.lose_response = True
    with pytest.raises(KeyboardInterrupt):
        run('render', str(path), '--no-wait', '--operation-key', 'intent', '--idempotency-key', 'first')
    first = service.store.recent('cli')['jobs'][0]['job_id']
    # Fresh argument parsing and a new client instance model CLI restart.
    replacement = AdmissionClient(service)
    connection.request = replacement.request
    second = run('render', str(path), '--no-wait', '--operation-key', 'intent', '--idempotency-key', 'changed')
    assert second['job_id'] == first
    assert len(service.store.recent('cli')['jobs']) == 1
    assert not converted and not service.backend.calls
    additional = run('render', str(path), '--no-wait', '--operation-key', 'additional', '--rerender')
    assert additional['job_id'] != first


def test_json_bundle_has_only_one_submission(monkeypatch, tmp_path, payload):
    path = tmp_path / 'payload.json'
    path.write_text(json.dumps(payload))
    events = []
    fake = SimpleNamespace(
        upload=lambda doc, name: events.append(('upload', doc)) or {'input_id': 'input'},
        submit=lambda identifier, options, key, **kw: events.append(('submit', identifier)) or {'job_id': 'job'})
    monkeypatch.setattr(client, 'configured_client', lambda *a, **kw: fake)
    assert run('render', str(path), '--no-wait') == {'job_id': 'job'}
    assert events == [('upload', payload), ('submit', 'input')]


def test_repeat_requires_key_before_admission(admission):
    connection, _, _, path = admission
    with pytest.raises(ValueError, match='requires'):
        run('render', str(path), '--rerender')
    assert not connection.events


@pytest.mark.parametrize('profile', ['local', 'lab'])
def test_service_status_absent_never_starts(monkeypatch, profile):
    monkeypatch.setattr(config, 'load_config', lambda: {'profiles': {'lab': {'kind': 'hpc'}}})
    assert run('service', 'status', '--profile', profile) == {'status': 'stopped'}


@pytest.mark.parametrize('command', [('quota',), ('status', 'job'), ('result', 'job'), ('structures', 'list')])
@pytest.mark.parametrize('profile', ['local', 'lab'])
def test_read_only_absent_never_starts(monkeypatch, command, profile):
    monkeypatch.setattr(config, 'load_config', lambda: {'profiles': {'lab': {'kind': 'hpc'}}})
    with pytest.raises(RuntimeError, match='existing controller'):
        run(*command, '--profile', profile)


def test_catalog_diagnostics_and_construction_are_inert(monkeypatch):
    monkeypatch.setattr(config, 'load_config', lambda: {})
    monkeypatch.setattr(cli, 'load_config', lambda: {})
    client.configured_client('local')
    client.configured_client('lab', admission=True)
    assert run('presets')['catalog']
    assert run('doctor')['runtime_verified'] is False


@pytest.mark.parametrize('profile', ['local', 'lab'])
def test_explicit_start_only_reaches_selected_mock_start(monkeypatch, tmp_path, profile):
    token = tmp_path / 'token'
    token.write_text('fake-token')
    connection = {'url': 'http://127.0.0.1:1234', 'token_file': str(token)}
    calls = []
    monkeypatch.setattr(config, 'load_config', lambda: {'profiles': {'lab': {'kind': 'hpc'}}})
    if profile == 'local':
        monkeypatch.setattr(runtime, 'ensure_local', lambda **kw: calls.append(('local-start', kw)) or connection)
    else:
        monkeypatch.setattr(hpc, 'connect', lambda name, doc, **kw: calls.append(('hpc-start', name)) or connection)
    def health(self, method, path, **kwargs):
        assert (method, path) == ('GET', '/health')
        calls.append(('health',))
        return {'status': 'ready', 'session_id': 'compatible'}
    monkeypatch.setattr(client.Client, 'request', health)
    assert run('service', 'start', '--profile', profile) == {'status': 'ready', 'session_id': 'compatible'}
    assert calls == ([('local-start', {}), ('health',)] if profile == 'local'
                     else [('hpc-start', 'lab'), ('health',)])


def test_hpc_cli_admission_attaches_outer_controller_before_any_allocation(monkeypatch, tmp_path):
    token = tmp_path / 'token'
    token.write_text('fake')
    state = {'url': 'http://127.0.0.1:1234', 'token_file': str(token)}
    calls = []
    monkeypatch.setattr(config, 'load_config', lambda: {'profiles': {'lab': {'kind': 'hpc'}}})
    monkeypatch.setattr(runtime, 'ensure_local', lambda **kw: calls.append(('controller', kw)) or state)
    monkeypatch.setattr(runtime, 'existing_local', lambda **kw: calls.append(('attach', kw)) or state)
    monkeypatch.setattr(client.Client, 'request', lambda self, *a, **kw: {'job_id': 'outer'})
    connection = client.configured_client('lab', admission=True)
    assert not calls
    assert connection.request('POST', '/v1/render-files/cif', b'fake', raw=True)['job_id'] == 'outer'
    connection.status('outer')
    assert calls == [('controller', {'worker_profile': 'lab'}), ('attach', {'worker_profile': 'lab'})]
    # hpc.connect/remote remain forbidden, including on status.


def test_deferred_local_converter_preserves_interpreter_and_selection(monkeypatch, payload):
    calls = []
    def export(path, **kwargs):
        assert path.read_bytes() == b'public'
        calls.append((path, kwargs))
        return payload
    monkeypatch.setattr(runtime, 'export_file', export)
    converter = runtime.LocalFileConverter('configured-python')
    assert not calls
    assert converter(b'public', '.cif', selection='chain A') == payload
    assert calls[0][1] == {'python': 'configured-python', 'selection': 'chain A'}
    assert not calls[0][0].exists()


def test_controller_launch_carries_deferred_profile_and_converter(monkeypatch):
    commands = []
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0))
    monkeypatch.setattr(subprocess, 'Popen', lambda command, **kw:
        commands.append(command) or SimpleNamespace(pid=123, poll=lambda: None))
    class Probe:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def bind(self, *args): pass
        def getsockname(self): return ('127.0.0.1', 1234)
    monkeypatch.setattr(socket, 'socket', Probe)
    monkeypatch.setattr(client.Client, 'request', lambda *a, **kw: {'status': 'ready'})
    state = _ensure_local(worker_profile='lab', pymol_python='configured-python')
    assert len(commands) == 1
    assert commands[0][2:4] == ['blender_render.cli', 'serve']
    assert commands[0][-4:] == ['--worker-profile', 'lab', '--pymol-python', 'configured-python']
    assert state == runtime.existing_local(worker_profile='lab')
    assert not (runtime.controller_root() / 'session.json').exists()


def test_explicit_hpc_cold_start_recovers_token_after_status(monkeypatch):
    profile = {'kind': 'hpc', 'host': 'fake-login', 'remote_python': 'python',
               'remote_root': '/fake', 'registry': '/fake/registry', 'image': '/fake.sif',
               'version': 2, 'transport':'ssh', 'launcher': {'kind':'slurm_sbatch', 'arguments': []}}
    from blender_render.engine import engine_identity
    monkeypatch.setattr(config, 'load_config', lambda: {'profiles': {'lab': profile}})
    calls = []
    ready = {'status': 'ready', 'session_id': 'session', 'node': 'fake-gpu', 'user': 'fake', 'port': 1234}
    def remote(profile, action, **kwargs):
        calls.append((action, kwargs))
        if action == 'identity':
            return {'python_supported': True, 'engine': engine_identity()}
        if action == 'start':
            return {'status': 'allocating'}
        if action == 'status':
            return dict(ready)
        assert kwargs == {}
        return {**ready, 'token': 'fake-secret', 'controller_token':'controller-secret'}
    monkeypatch.setattr(hpc, 'remote', remote)
    monkeypatch.setattr(hpc, 'connect', _connect_hpc)
    monkeypatch.setattr(hpc.time, 'sleep', lambda *a: None)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: SimpleNamespace(pid=123, poll=lambda: None))
    class Probe:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def bind(self, *args): pass
        def getsockname(self): return ('127.0.0.1', 1234)
    monkeypatch.setattr(socket, 'socket', Probe)
    def request(self, method, path, **kwargs):
        assert self.token == 'fake-secret'
        return {'status': 'ok', 'controller_declaration': engine_identity()}
    monkeypatch.setattr(client.Client, 'request', request)
    assert run('service', 'start', '--profile', 'lab')['status'] == 'ok'
    assert calls == [('identity', {}), ('start', {}), ('status', {}), ('attach', {})]


def test_serve_wires_admission_before_fake_gateway_dispatch(monkeypatch, tmp_path, payload):
    import uvicorn
    import threading
    from blender_render import service as service_module
    events = []
    token = tmp_path / 'token'
    token.write_text('x' * 43)
    monkeypatch.setattr(runtime, 'export_file', lambda *a, **kw: events.append('conversion') or payload)
    class Backend(Compute):
        def __init__(self, store, profile):
            super().__init__()
            self.store = store
            assert profile == 'lab'
        def render(self, request, identifier, cancelled):
            assert self.store.get('cli', identifier)['stage'] == 'waiting_for_compute'
            events.append('fake-allocation')
            return super().render(request, identifier, cancelled)
    monkeypatch.setattr(gateway, 'GatewayBackend', Backend)
    monkeypatch.setattr(service_module, 'HostBackend', lambda *a, **kw: pytest.fail('Host Blender backend selected'))
    class Server:
        should_exit = True
        def __init__(self, conf): self.service = conf.app.state.service
        def run(self):
            # This is a fake server, no socket, lifespan or dispatcher started.
            connection = AdmissionClient(self.service)
            monkeypatch.setattr(client, 'configured_client', lambda *a, **kw: connection)
            path = tmp_path / 'public.cif'
            path.write_text(payload['cif_text'])
            job = run('render', str(path), '--profile', 'lab', '--no-wait')
            assert events == []
            assert self.service.store.get('cli', job['job_id'])['status'] == 'queued'
            events.append('handle-returned')
            self.service._execute(self.service.store.claim())
            assert self.service.store.get('cli', job['job_id'])['status'] == 'done'
    monkeypatch.setattr(uvicorn, 'Server', Server)
    monkeypatch.setattr(threading.Thread, 'start', lambda self: None)
    run('serve', '--root', str(tmp_path / 'controller'), '--token-file', str(token), '--worker-profile', 'lab')
    assert events == ['handle-returned', 'conversion', 'fake-allocation']
