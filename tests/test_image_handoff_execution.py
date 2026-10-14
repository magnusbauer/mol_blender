"""Shared boundaries introduced by the CPU/image-handoff workflow."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from blender_render import config, core, runtime
from blender_render.api import Downloads, create_app


@pytest.mark.parametrize('origin', [
    'http://example.org', 'https://example.org/path', 'https://u:p@example.org',
    'https://example.org?token=value', 'https://example.org#fragment',
    'https://example.org:invalid', 'https://example.org:99999', 'https://exa mple.org',
])
def test_origin_rejected_by_rest_without_mcp(origin):
    with pytest.raises(ValueError, match='HTTPS origin'):
        create_app(None, token='x' * 48, public_url=origin)


def test_origin_normalization_and_relative_downloads():
    assert Downloads('x' * 48, 'https://example.org/').base_url == 'https://example.org'
    assert Downloads('x' * 48, '').url('owner', 'job', 'render.png').startswith('/download/')


def test_bad_cli_origin_fails_before_opening_credentials_or_store(tmp_path):
    from blender_render.cli import parser, serve
    args = parser().parse_args(['serve', '--root', str(tmp_path), '--token-file',
                               str(tmp_path / 'missing-token'), '--public-url', 'http://example.org'])
    with pytest.raises(ValueError, match='HTTPS origin'):
        serve(args)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('limit', [0, -1, 65, True, 2.5, '2'])
def test_export_rejects_bad_process_limit_before_execution(limit):
    with pytest.raises(ValueError, match='process_threads'):
        runtime.export_file('unused.cif', process_threads=limit)


def test_shared_export_captures_source_and_bounds_child_threads(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv('OMP_NUM_THREADS', '7')
    monkeypatch.setattr(runtime, 'load_config', lambda: {})
    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert Path(command[1]).parent == Path(kwargs['cwd']) / 'blender_render'
        assert Path(command[1]).read_bytes() == Path(runtime.__file__).with_name('export.py').read_bytes()
        Path(command[3]).write_text(json.dumps({'cif_text': 'public fixture'}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runtime.subprocess, 'run', run)
    result = runtime.export_file(tmp_path / 'public.cif', python='selected-pymol', process_threads=2)
    command, kwargs = calls[0]
    assert len(calls) == 1
    assert command[0] == 'selected-pymol'
    assert all(kwargs['env'][key] == '2' for key in (
        'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'))
    assert runtime.os.environ['OMP_NUM_THREADS'] == '7'
    assert not Path(command[3]).exists()
    assert result == {'cif_text': 'public fixture'}


@pytest.mark.parametrize('apptainer', [False, True])
def test_worker_process_limits_are_frozen_until_restart(tmp_path, monkeypatch, apptainer):
    from blender_render.render_worker_manager import PersistentBlenderWorker
    settings = {'process_threads': 2}
    monkeypatch.setattr(config, 'load_config', lambda: settings)
    worker = PersistentBlenderWorker(root_dir=tmp_path, blender_exe='blender',
        custom_script=Path(core.__file__).with_name('blender_custom_render.py'),
        use_apptainer=apptainer, apptainer_image='runtime.sif')
    settings['process_threads'] = 8
    worker._captured_package = Path(core.__file__).parent
    worker._ipc = SimpleNamespace(directory=tmp_path)
    worker.socket_path = tmp_path / 'worker.sock'
    monkeypatch.setattr(worker, '_resolve_blender_executable', lambda: 'blender')
    monkeypatch.setattr(core, '_resolve_apptainer_executable', lambda: 'apptainer')
    command = worker._build_command()
    assert command[command.index('--threads') + 1] == '2'
    env = worker._subprocess_env()
    assert env['OMP_NUM_THREADS'] == env['OPENBLAS_NUM_THREADS'] == '2'
    if apptainer:
        assert env['APPTAINERENV_OMP_NUM_THREADS'] == '2'
        assert env['APPTAINERENV_OPENBLAS_NUM_THREADS'] == '2'


def test_default_worker_process_configuration_does_not_force_threads():
    assert config.blender_thread_arguments(None) == []
    assert config.process_thread_environment(None) == {}
