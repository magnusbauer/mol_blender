"""Warm dispatch preserves the HTTP job lifecycle; real GPU checks are separate."""
from dataclasses import replace
import hashlib
import json
import threading

import pytest
from starlette.testclient import TestClient

from blender_render import local
from blender_render.cluster import cli, warm_service
from blender_render.cluster.config import Limits
from blender_render.cluster.jsonio import atomic_json
from blender_render.cluster.server import create_app
from blender_render.cluster.slurm import scheduler_for
from blender_render.cluster.store import Store, Quota
from blender_render.cluster.warm import Warm
from test_cluster_jobs import config, request
from test_warm_worker import until


@pytest.fixture
def warm_config(config):
    return replace(config, scheduler='warm', limits=Limits(max_active_total=1), gpus=1)


@pytest.fixture
def backend(warm_config, monkeypatch):
    scheduler = Warm(warm_config)
    store = Store(warm_config, scheduler, scheduler.identity)
    dispatcher = warm_service.Dispatcher(warm_config.spool, scheduler.key, 90)
    release = threading.Event()
    calls = []
    monkeypatch.setattr(warm_service.warm_worker, 'start', lambda **kw: {'id': 'same-worker'})
    monkeypatch.setattr(warm_service.warm_worker, 'status', lambda: {'id': 'same-worker', 'pid': 42})

    def render(payload, **kwargs):
        calls.append(kwargs)
        assert release.wait(5)
        path = kwargs['output']
        path.parent.mkdir()
        path.write_bytes(b'verified-image')
        return {'outputs': {'render.png': str(path)}}

    monkeypatch.setattr(local, 'render_payload', render)
    monkeypatch.setattr(scheduler, '_start', lambda: dispatcher.handle({'id': scheduler.key, 'action': 'status'}))
    monkeypatch.setattr(scheduler, 'rpc', lambda action, **kw:
                        dispatcher.handle({'id': scheduler.key, 'action': action, **kw}))
    monkeypatch.setattr(scheduler, 'unit_properties', lambda unit: {'LoadState': 'loaded', 'ActiveState': 'active'})
    yield scheduler, store, dispatcher, release, calls
    release.set()
    until(lambda: dispatcher.active is None)


def test_same_http_api_repeats_with_one_dispatcher_and_authorized_downloads(backend, warm_config):
    scheduler, store, dispatcher, release, calls = backend
    token = 'a' * 43
    atomic_json(warm_config.tokens_file, {'users': {'alice': hashlib.sha256(token.encode()).hexdigest()}})
    headers = {'Authorization': 'Bearer ' + token}
    with TestClient(create_app(warm_config, scheduler=scheduler)) as client:
        assert client.get('/v1/info').status_code == 401
        assert client.get('/v1/info', headers=headers).json()['scheduler'] == 'warm'
        first = client.post('/v1/jobs', headers=headers, json=request()).json()
        assert first['worker_id'] == scheduler.unit(first['job_id'])
        assert client.post('/v1/jobs', headers=headers, json=request('2' * 32)).status_code == 429
        release.set()
        until(lambda: dispatcher.active is None)
        url = '/v1/jobs/' + first['job_id']
        assert client.get(url, headers=headers).json()['status'] == 'completed'
        assert client.get(url + '/artifacts/render.png', headers=headers).content == b'verified-image'
        assert client.post('/v1/jobs', headers=headers, json=request()).json()['job_id'] == first['job_id']
        second = client.post('/v1/jobs', headers=headers, json=request('2' * 32)).json()
        until(lambda: dispatcher.active is None)
        assert second['job_id'] != first['job_id']
        assert client.get('/v1/jobs/' + second['job_id'], headers=headers).json()['status'] == 'completed'
    assert len(calls) == 2 and all(call['warm'] for call in calls)


def test_lost_ack_recovers_same_job_after_gateway_restart(backend, monkeypatch):
    scheduler, store, dispatcher, release, calls = backend
    original = scheduler.rpc
    def lose_response(action, **kwargs):
        original(action, **kwargs)
        raise ConnectionError('lost acknowledgement')
    monkeypatch.setattr(scheduler, 'rpc', lose_response)
    first = store.submit('alice', request())
    assert first['status'] == 'submission_unknown'
    assert (store.directory(first['job_id']) / 'started.json').is_file()
    monkeypatch.setattr(scheduler, 'rpc', original)
    restarted = Store(scheduler.config, scheduler, scheduler.identity)
    recovered = restarted.reconcile(first['job_id'])
    assert recovered['status'] == 'running'
    assert restarted.submit('alice', request())['job_id'] == first['job_id']
    assert len(calls) == 1
    with pytest.raises(Quota):
        restarted.submit('alice', request('2' * 32))


def test_container_loss_does_not_replay_started_job(backend, monkeypatch):
    scheduler, store, dispatcher, release, calls = backend
    first = store.submit('alice', request())
    replacement = warm_service.Dispatcher(scheduler.config.spool, scheduler.key, 90)
    monkeypatch.setattr(scheduler, 'rpc', lambda action, **kw:
                        replacement.handle({'id': scheduler.key, 'action': action, **kw}))
    assert store.status('alice', first['job_id'])['status'] == 'failed'
    with pytest.raises(RuntimeError, match='interrupted'):
        replacement.handle({'id': scheduler.key, 'action': 'submit', 'job_id': first['job_id']})
    assert len(calls) == 1


def test_cancellation_stops_own_service_and_never_a_later_job(backend, monkeypatch):
    scheduler, store, dispatcher, release, calls = backend
    first = store.submit('alice', request())
    commands = []
    def stop(command):
        commands.append(command)
        monkeypatch.setattr(scheduler, 'status', lambda: {'status': 'stopped'})
    monkeypatch.setattr(scheduler, 'run', stop)
    assert store.cancel('alice', first['job_id'])['status'] == 'cancelling'
    assert store.status('alice', first['job_id'])['status'] == 'cancelled'
    assert commands == [[scheduler.config.systemctl, '--user', 'stop', scheduler.service]]
    monkeypatch.setattr(scheduler, 'status', lambda: {'status': 'busy', 'job_id': 'b' * 32})
    scheduler.cancel(first['worker_id'])
    assert len(commands) == 1
    with pytest.raises(ValueError, match='worker ID'):
        scheduler.cancel('unrelated.service')


def test_render_failure_retires_service(backend, monkeypatch):
    scheduler, store, dispatcher, release, calls = backend
    def fail(*args, **kwargs):
        raise RuntimeError('render failed')
    monkeypatch.setattr(local, 'render_payload', fail)
    first = store.submit('alice', request())
    until(dispatcher.stopping.is_set)
    assert store.status('alice', first['job_id'])['status'] == 'failed'
    assert 'render failed' in (store.directory(first['job_id']) / 'worker-error.log').read_text()


@pytest.mark.parametrize('message', [
    {'id': 'wrong', 'action': 'status'},
    {'id': 'key', 'action': 'exec', 'command': 'anything'},
    {'id': 'key', 'action': 'submit', 'job_id': '../outside'},
    {'id': 'key', 'action': 'submit', 'job_id': 'a' * 32, 'path': '/outside'},
])
def test_private_control_accepts_only_job_ids(tmp_path, message):
    with pytest.raises(ValueError):
        warm_service.Dispatcher(tmp_path, 'key', 90).handle(message)


def test_warm_launch_is_persistent_bounded_and_environment_isolated(warm_config, monkeypatch):
    scheduler = scheduler_for(warm_config)
    assert isinstance(scheduler, Warm)
    launched = []
    monkeypatch.setattr(scheduler, 'unit_properties', lambda unit: {'LoadState': 'not-found'})
    monkeypatch.setattr(scheduler, 'status', lambda: {'status': 'ready'})
    monkeypatch.setenv('APPTAINERENV_SECRET', 'private-token')
    monkeypatch.setattr(scheduler, 'run', lambda command: launched.append(command))
    scheduler.start()
    command, = launched
    assert '--property=RuntimeMaxSec=infinity' in command
    assert '--property=MemoryMax=16384M' in command
    assert '--property=CPUQuota=800%' in command
    assert '--property=KillMode=control-group' in command
    assert '--property=Restart=no' in command
    assert str(warm_config.spool) + ':/job' in command
    assert '--walltime' in command and command[-1] == str(warm_config.walltime_seconds)
    assert 'blender_render.cluster.warm_service' in command
    assert 'private-token' not in json.dumps(command)


def test_active_configuration_change_requires_explicit_stop(warm_config, monkeypatch):
    first = Warm(warm_config)
    changed = Warm(replace(warm_config, gpu_index=1))
    assert first.service == changed.service and first.key != changed.key
    monkeypatch.setattr(changed, 'unit_properties', lambda unit: {'LoadState': 'loaded'})
    dispatcher = warm_service.Dispatcher(warm_config.spool, first.key, 90)
    monkeypatch.setattr(changed, 'status', lambda: dispatcher.handle({'id': changed.key, 'action': 'status'}))
    monkeypatch.setattr(changed, 'run', lambda command: pytest.fail('must not replace a live service'))
    with pytest.raises(ValueError, match='policy changed'):
        changed.start()


def test_admin_stop_requires_force_when_busy(warm_config, monkeypatch):
    scheduler = Warm(warm_config)
    monkeypatch.setattr(scheduler, 'status', lambda: {'status': 'busy'})
    monkeypatch.setattr(scheduler, 'unit_properties', lambda unit: {'LoadState': 'loaded'})
    commands = []
    monkeypatch.setattr(scheduler, 'run', lambda command: commands.append(command))
    with pytest.raises(ValueError, match='--force'):
        scheduler.stop()
    assert not commands
    scheduler.stop(force=True)
    assert commands == [[warm_config.systemctl, '--user', 'stop', scheduler.service]]


def test_warm_policy_and_cli_validation(warm_config, tmp_path):
    with pytest.raises(ValueError, match='max_active_total'):
        replace(warm_config, limits=Limits())
    with pytest.raises(ValueError, match='30 seconds'):
        replace(warm_config, limits=Limits(max_active_total=1, render_timeout=1))
    path = tmp_path / 'server.json'
    path.write_text(json.dumps({**vars(warm_config), 'limits': vars(warm_config.limits)}, default=str))
    args = cli.parser().parse_args(['warm', 'status', '--force', '--config', str(path)])
    with pytest.raises(ValueError, match='only to warm stop'):
        cli.execute(args)
