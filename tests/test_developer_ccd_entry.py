"""Execute the developer Blender entrypoint with only runtime boundaries faked."""
import hashlib
import importlib
import importlib.metadata
import json
import os
import stat
from pathlib import Path
import runpy
import sys
from types import ModuleType, SimpleNamespace

import pytest

from mol_blender import engine


@pytest.mark.parametrize('outcome', ['success', 'render_failure', 'manifest_failure', 'source_mutation', 'wrong_pin'])
@pytest.mark.parametrize('environment', ['private', 'caller'])
def test_developer_entry_owns_ccd_through_render_and_evidence(tmp_path, monkeypatch, outcome, environment):
    root = tmp_path/'preview'
    package = root/'source'/'mol_blender'
    engine.stage_package(Path(engine.__file__).parent, package)
    data = b'public CCD fixture'
    locked_hash = hashlib.sha256(data).hexdigest()
    lock = json.loads((package/'runtime.lock.json').read_text())
    lock['ccd_sha256'] = locked_hash
    (package/'runtime.lock.json').write_text(json.dumps(lock))
    original = tmp_path/'caller-runtime'/'components.bcif'
    original.parent.mkdir()
    original.write_bytes(data)
    private = root/'components.bcif'
    private.write_bytes(data if outcome != 'wrong_pin' else b'different CCD')
    binary = tmp_path/'fake-blender'
    binary.write_bytes(b'injected Blender boundary')
    manifest_path = root/'manifest.json'
    events, captured = [], []

    # Fresh package namespace models the real snapshot process without invoking
    # a child interpreter. Real package modules load from the staged inventory.
    owned = lambda name:name == 'mol_blender' or name.startswith('mol_blender.')
    previous = {name:module for name,module in sys.modules.items() if owned(name)}
    for name in previous: sys.modules.pop(name)
    monkeypatch.setattr(sys, 'path', [str(package.parent), *sys.path])
    monkeypatch.setenv('MOL_BLENDER_DEV_CONFIG', str(root/'preview.json'))
    monkeypatch.setenv('PRESS_BIOTITE_CCD_PATH', str(private if environment == 'private' else original))
    try:
        developer = importlib.import_module('mol_blender.developer')
        worker_ccd = importlib.import_module('mol_blender.worker_ccd')
        expected = developer.source_files(package)
        cfg = {'identity':{'files':expected, 'sha256':developer.source_digest(expected)},
               'root':str(root), 'ccd':str(private), 'ccd_source':str(original),
               'ccd_sha256':developer.digest_file(private)}
        (root/'preview.json').write_text(json.dumps(cfg))
        ccd = ModuleType('biotite.structure.info.ccd')
        ccd._CCD_FILE = original
        def activate(path):
            path = Path(path)
            assert path != original and path != private and path.is_relative_to(root)
            assert path.read_bytes() == data
            if os.name == 'nt':
                assert not path.stat().st_mode & stat.S_IWRITE
            else:
                assert path.stat().st_mode & 0o777 == 0o400
            captured.append(path)
            ccd._CCD_FILE = path
            events.append('activate')
        ccd.set_ccd_path = activate
        for name in ('bpy', 'molecularnodes', 'biotite', 'biotite.structure', 'biotite.structure.info', 'numpy'):
            module = ModuleType(name)
            module.__path__ = []
            runtime_path = tmp_path/(name+'.py')
            runtime_path.write_text('# injected runtime boundary\n')
            module.__file__ = str(runtime_path)
            module.__version__ = lock.get(name, 'unused')
            monkeypatch.setitem(sys.modules, name, module)
        sys.modules['bpy'].app = SimpleNamespace(version_string=lock['blender'], binary_path=str(binary))
        sys.modules['biotite.structure.info'].ccd = ccd
        monkeypatch.setitem(sys.modules, 'biotite.structure.info.ccd', ccd)
        version = importlib.metadata.version
        monkeypatch.setattr(importlib.metadata, 'version', lambda name:lock['molecularnodes'] if name == 'molecularnodes' else version(name))

        render = ModuleType('mol_blender.blender_custom_render')
        render.__file__ = str(package/'blender_custom_render.py')
        render._parse_argv = lambda argv:SimpleNamespace(run_manifest=str(manifest_path))
        def invoke(args):
            evidence = worker_ccd.require()
            assert evidence['verified'] and evidence['path'] == str(ccd._CCD_FILE)
            assert ccd._CCD_FILE not in (private, original)
            assert ccd._CCD_FILE.read_bytes() == data
            events.append('render')
            if outcome == 'render_failure': raise RuntimeError('injected render failure')
            manifest_path.write_text(json.dumps({'render_marker':'preserved'}))
            if outcome == 'source_mutation': (package/'changed.py').write_text('# changed source')
        render._render = invoke
        sys.modules['mol_blender.blender_custom_render'] = render
        # The entrypoint may read only cfg's private CCD, never the caller file.
        read_bytes = Path.read_bytes
        def read(path):
            if path == original: pytest.fail('Developer entry reopened the caller CCD')
            return read_bytes(path)
        monkeypatch.setattr(Path, 'read_bytes', read)
        write_text = Path.write_text
        def write(path, text, *a, **k):
            if path == manifest_path and 'development_source' in text:
                assert worker_ccd.require()['verified']
                events.append('evidence')
                if outcome == 'manifest_failure': raise OSError('injected evidence failure')
            return write_text(path, text, *a, **k)
        monkeypatch.setattr(Path, 'write_text', write)
        if outcome == 'success':
            runpy.run_path(str(package/'developer_blender.py'), run_name='__main__')
            manifest = json.loads(manifest_path.read_text())
            assert manifest['render_marker'] == 'preserved'
            evidence = manifest['development_source']
            assert evidence['sha256'] == cfg['identity']['sha256'] and evidence['ccd_sha256'] == locked_hash
            assert evidence['runtime_versions'] == {key:lock[key] for key in ('blender','python','molecularnodes','numpy','biotite')}
            assert evidence['blender_binary_sha256'] == hashlib.sha256(binary.read_bytes()).hexdigest()
            assert 'mol_blender.worker_ccd' in evidence['loaded_modules']
            assert all(Path(item['path']).is_relative_to(package) for item in evidence['loaded_modules'].values())
            assert events == ['activate', 'render', 'evidence']
        else:
            message = {'render_failure':'injected render failure', 'manifest_failure':'injected evidence failure',
                       'source_mutation':'Source snapshot changed', 'wrong_pin':'CCD.*runtime.lock'}[outcome]
            with pytest.raises((RuntimeError, OSError), match=message):
                runpy.run_path(str(package/'developer_blender.py'), run_name='__main__')
        if outcome == 'wrong_pin': assert events == [] and not captured
        else: assert len(captured) == 1 and not captured[0].exists()
        assert not list(root.glob('worker-ccd-*'))
        assert worker_ccd.observation()['verified'] is False
        assert read_bytes(original) == data
        assert read_bytes(private) == (data if outcome != 'wrong_pin' else b'different CCD')
    finally:
        if 'worker_ccd' in locals(): worker_ccd.close()
        for name in list(sys.modules):
            if owned(name): sys.modules.pop(name)
        sys.modules.update(previous)
