"""Shared waiting behavior against durable jobs; no external runtime actions."""
from types import SimpleNamespace

import pytest

from blender_render import client, cli, pymol_bridge_plugin
from test_c3_operation_blocks import admitted
from test_jobs_api import payload


CONDITIONS = {
    'restart_required':'restart_service',
    'compatibility_blocked':'restore_exact_engine',
    'execution_unknown':'inspect_execution',
    'recovery_required':'recover_operation',
}


@pytest.mark.parametrize('condition,next_action', CONDITIONS.items())
@pytest.mark.parametrize('adapter', ['direct', 'cli', 'pymol'])
def test_wait_reports_block_and_later_uses_same_operation(tmp_path, payload, monkeypatch,
                                                        condition, next_action, adapter, capsys):
    monkeypatch.setattr(
        pymol_bridge_plugin, '_PACKAGE_ROOT', str(tmp_path / 'isolated-package')
    )
    store, service, source, job = admitted(tmp_path/'store', payload)
    identifier = job['job_id']
    store.block(identifier, condition, 'dispatch')
    before = dict(store.db.execute('SELECT * FROM jobs').fetchone())
    requests = []
    class Connection(client.Client):
        def __init__(self): pass
        def request(self, method, path, body=None, **kwargs):
            requests.append((method, path))
            if method == 'POST' and path == '/v1/structures': return {'input_id':source}
            if method == 'POST' and (path == '/v1/jobs' or path.startswith('/v1/render-files/')):
                return job  # Inject the already durably admitted handle.
            if (method, path) == ('GET', '/v1/jobs/'+identifier): return store.get('a', identifier)
            if (method, path) == ('GET', '/v1/jobs/'+identifier+'/result'): return store.result('a', identifier)
            pytest.fail('Unexpected action: '+method+' '+path)
    connection = Connection()
    monkeypatch.setattr(client, 'configured_client', lambda *a, **k:connection)
    monkeypatch.setattr(client.time, 'sleep', lambda *a:pytest.fail('Blocked client kept polling'))
    if adapter == 'cli':
        path = tmp_path/'public.cif'
        path.write_text(payload['cif_text'])
        assert cli.main(['render', str(path)]) == 1
        error = capsys.readouterr().err
        for text in (identifier, condition, next_action): assert text in error
    else:
        monkeypatch.setattr(pymol_bridge_plugin, 'build_render_payload', lambda *a, **k:payload)
        with pytest.raises(client.ClientError) as caught:
            if adapter == 'direct': connection.wait(identifier)
            else: pymol_bridge_plugin.bridge_render(_self=SimpleNamespace())
        exc = caught.value
        assert (exc.status, exc.code, exc.job_id, exc.next_action) == (409, condition, identifier, next_action)
        for text in (identifier, condition, next_action): assert text in str(exc)
    assert sum(method == 'GET' for method, _ in requests) == 1
    assert not service.backend.calls
    assert dict(store.db.execute('SELECT * FROM jobs').fetchone()) == before
    # Model explicit operator resolution, never automatic clearing by Client.wait.
    store.db.execute('UPDATE jobs SET blocked_condition=NULL WHERE id=?', (identifier,))
    service._execute(store.claim())
    prior_requests = list(requests)
    result = connection.wait(identifier)
    assert result['job_id'] == identifier and service.backend.calls == [identifier]
    assert requests[len(prior_requests):] == [('GET', '/v1/jobs/'+identifier), ('GET', '/v1/jobs/'+identifier+'/result')]
    assert store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1
    store.close()
