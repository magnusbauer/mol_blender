"""A persisted secret must not override the funded worker deadline on upgrade."""
import json
import sys
from types import SimpleNamespace
import pytest
from blender_render import deployment


@pytest.mark.parametrize('animation, expected', [('1', '1800'), ('0', '600')])
def test_upgrade_replaces_stale_deadline_secret(tmp_path, monkeypatch, animation, expected):
    root = tmp_path / 'deployments' / 'blender-render-beta'
    root.mkdir(parents=True)
    credentials = dict(BLENDER_RENDER_ADMIN_TOKEN='x'*48,
        BLENDER_RENDER_JOB_TIMEOUT='600' if animation == '1' else '1800',
        BLENDER_RENDER_PROXY_KEY='test-proxy', GITHUB_CLIENT_ID='test', GITHUB_CLIENT_SECRET='test')
    path = root / 'credentials.json'
    path.write_text(json.dumps(credentials))
    monkeypatch.setattr(deployment, 'config_root', lambda:tmp_path)
    monkeypatch.setattr(deployment, 'load_config', lambda:{'modal_workspace':'test'})
    monkeypatch.setenv('BLENDER_RENDER_ENABLE_ANIMATION', animation)
    monkeypatch.setattr(deployment.subprocess, 'check_output', lambda *a,**k:'[]')
    captured = []
    class StopBeforePublishing(Exception): pass
    def create(name, values, **kwargs):
        captured.append(dict(values))
        raise StopBeforePublishing()
    monkeypatch.setitem(sys.modules, 'modal', SimpleNamespace(
        Secret=SimpleNamespace(objects=SimpleNamespace(create=create))))
    with pytest.raises(StopBeforePublishing):
        deployment.deploy(staging_user='123')
    assert captured[0]['BLENDER_RENDER_JOB_TIMEOUT'] == expected
    assert json.loads(path.read_text())['BLENDER_RENDER_JOB_TIMEOUT'] == expected
