"""Startup editing must preserve users' unrelated settings and restore exact bytes."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('windows_setup', Path(__file__).parents[1]/'scripts/windows_setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
@pytest.mark.parametrize('bom', [b'', b'\xef\xbb\xbf'])
def test_repeat_install_and_uninstall_preserve_user_code(tmp_path, newline, bom):
    path = tmp_path/'.pymolrc.py'
    original = bom + ('# user settings' + newline + 'x = "结构"' + newline).encode()
    path.write_bytes(original)
    backup = setup.update_startup(path, tmp_path)
    assert backup.read_bytes() == original
    installed = path.read_bytes()
    assert setup.update_startup(path, tmp_path) is None
    assert path.read_bytes() == installed
    setup.update_startup(path, tmp_path, uninstall=True)
    assert path.read_bytes() == original


def test_new_user_and_malformed_markers(tmp_path):
    path = tmp_path/'.pymolrc.py'
    setup.update_startup(path, tmp_path)
    assert setup.BEGIN in path.read_text(encoding='utf-8')
    path.write_text(setup.BEGIN)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        setup.update_startup(path, tmp_path)
    assert path.read_bytes() == original


def test_retarget_existing_block_and_preserve_later_edits(tmp_path):
    path = tmp_path/'startup.py'
    path.write_text('user_setting = 1\n')
    setup.update_startup(path, tmp_path/'old')
    with path.open('a') as stream:
        stream.write('later_setting = 2\n')
    setup.update_startup(path, tmp_path/'new')
    content = path.read_text()
    assert content.count(setup.BEGIN) == 1 and 'old' not in content
    setup.update_startup(path, tmp_path/'new', uninstall=True)
    assert path.read_text() == 'user_setting = 1\nlater_setting = 2\n'


def test_preserves_missing_final_newline(tmp_path):
    path = tmp_path/'startup.py'
    path.write_bytes(b'x = 1')
    setup.update_startup(path, tmp_path)
    assert setup.update_startup(path, tmp_path) is None
    setup.update_startup(path, tmp_path, uninstall=True)
    assert path.read_bytes() == b'x = 1'


def test_migrates_legacy_hook_with_mixed_line_endings(tmp_path):
    path = tmp_path/'startup.py'
    legacy = "# mol-blender Windows renderer\nimport runpy\nrunpy.run_path(r'" + str(tmp_path/'scripts/pymol_windows_startup.py') + "')\n"
    path.write_bytes(('user = 1\r\n' + legacy + '\r\n').encode())
    setup.update_startup(path, tmp_path)
    text = path.read_text()
    assert 'import runpy\n' not in text
    assert text.count('pymol_windows_startup.py') == 1
