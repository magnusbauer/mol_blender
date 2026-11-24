"""Platform-specific process ownership, file capture and cleanup."""
from contextlib import contextmanager
import os
import shutil
import signal
import stat
import subprocess


def remove_tree(path):
    def writable(function, target, error):
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        function(target)
    shutil.rmtree(path, onerror=writable)


def open_regular_windows(path):
    """Open the file itself, rejecting reparse points using the opened handle."""
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        class AttributeTag(ctypes.Structure):
            _fields_ = [('attributes', wintypes.DWORD), ('tag', wintypes.DWORD)]
        info = AttributeTag()
        if not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & (0x400 | 0x10):
            raise ValueError('CCD must be a regular file, not a reparse point or directory')
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        handle = None  # The CRT descriptor now owns the Windows handle.
        return os.fdopen(descriptor, 'rb')
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)


@contextmanager
def owned_process(*args, **kwargs):
    """Own descendants with a Windows Job Object or a POSIX process group."""
    job = None
    assigned = False
    if os.name != 'nt':
        kwargs['start_new_session'] = True
    else:
        kwargs['creationflags'] = kwargs.get('creationflags', 0) | 0x4  # CREATE_SUSPENDED
    process = subprocess.Popen(*args, **kwargs)
    try:
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
            kernel.CreateJobObjectW.restype = wintypes.HANDLE
            kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            job = kernel.CreateJobObjectW(None, None)
            handle = kernel.OpenProcess(0x101, False, process.pid)
            try:
                if not job or not handle or not kernel.AssignProcessToJobObject(job, handle):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                if handle:
                    kernel.CloseHandle(handle)
            assigned = True
            # Resume the suspended primary thread using documented Toolhelp APIs.
            class ThreadEntry(ctypes.Structure):
                _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD),
                            ('tid', wintypes.DWORD), ('pid', wintypes.DWORD),
                            ('priority', wintypes.LONG), ('delta', wintypes.LONG),
                            ('flags', wintypes.DWORD)]
            kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
            kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            kernel.Thread32First.argtypes = kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
            kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenThread.restype = wintypes.HANDLE
            kernel.ResumeThread.argtypes = [wintypes.HANDLE]
            kernel.ResumeThread.restype = wintypes.DWORD
            snapshot = kernel.CreateToolhelp32Snapshot(4, 0)
            if snapshot == wintypes.HANDLE(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            resumed = False
            try:
                entry = ThreadEntry()
                entry.size = ctypes.sizeof(entry)
                more = kernel.Thread32First(snapshot, ctypes.byref(entry))
                while more:
                    if entry.pid == process.pid:
                        thread = kernel.OpenThread(2, False, entry.tid)
                        if not thread:
                            raise ctypes.WinError(ctypes.get_last_error())
                        try:
                            if kernel.ResumeThread(thread) == 0xffffffff:
                                raise ctypes.WinError(ctypes.get_last_error())
                            resumed = True
                        finally:
                            kernel.CloseHandle(thread)
                    more = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            finally:
                kernel.CloseHandle(snapshot)
            if not resumed:
                raise OSError('Unable to find suspended render thread')
        yield process
    finally:
        if os.name == 'nt' and job:
            kernel.TerminateJobObject(job, 1)
            kernel.CloseHandle(job)
            if not assigned and process.poll() is None:
                process.kill()
        elif process.poll() is None:
            if os.name == 'nt':
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        process.wait()
