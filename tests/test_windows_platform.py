"""Real Windows boundary checks, without requiring Blender."""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from mol_blender.platform_support import open_regular_windows, owned_process, remove_tree

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows native boundaries')


def test_capture_rejects_directory_and_reads_unicode_path(tmp_path):
    path = tmp_path / '结构 文件.bcif'
    path.write_bytes(b'ccd')
    with open_regular_windows(path) as stream:
        assert stream.read() == b'ccd'
    with pytest.raises((ValueError, OSError)):
        open_regular_windows(tmp_path)


def test_readonly_capture_cleanup(tmp_path):
    root = tmp_path / 'capture'
    root.mkdir()
    path = root / 'source.py'
    path.write_text('pass')
    path.chmod(0o400)
    remove_tree(root)
    assert not root.exists()


def test_owned_process_stops_descendant(tmp_path):
    pidfile = tmp_path / 'child.pid'
    child = 'import time; time.sleep(120)'
    parent = ('import subprocess,sys,time; from pathlib import Path; '
              'p=subprocess.Popen([sys.executable,"-c",sys.argv[2]]); '
              'Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(120)')
    with owned_process([sys.executable, '-c', parent, str(pidfile), child]) as process:
        deadline = time.monotonic() + 10
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert pidfile.exists()
        pid = int(pidfile.read_text())
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x100000, False, pid)
    if handle:
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        try:
            assert kernel.WaitForSingleObject(handle, 5000) == 0
        finally:
            kernel.CloseHandle(handle)
    assert process.returncode is not None


def test_rejects_symbolic_link_when_privilege_available(tmp_path):
    target = tmp_path/'ccd'
    target.write_bytes(b'ccd')
    link = tmp_path/'link'
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip('Creating symlinks requires Windows developer mode or privilege')
    with pytest.raises(ValueError):
        open_regular_windows(link)
