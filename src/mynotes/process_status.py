"""Probe process liveness without sending a termination signal."""
from __future__ import annotations

import errno
import os

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _open_process = _kernel.OpenProcess
    _open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _open_process.restype = wintypes.HANDLE
    _wait = _kernel.WaitForSingleObject
    _wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _wait.restype = wintypes.DWORD
    _close = _kernel.CloseHandle
    _close.argtypes = [wintypes.HANDLE]
    _close.restype = wintypes.BOOL

    def probe_process(pid: int) -> None:
        if not 0 < pid <= 0xFFFFFFFF:
            raise ProcessLookupError(errno.ESRCH, "Invalid process ID")
        handle = _open_process(0x00100000, False, pid)  # SYNCHRONIZE only
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # ERROR_INVALID_PARAMETER: no process with that ID
                raise ProcessLookupError(errno.ESRCH, "Process does not exist")
            raise ctypes.WinError(error)
        try:
            status = _wait(handle, 0)
            if status == 0:  # WAIT_OBJECT_0: process has exited
                raise ProcessLookupError(errno.ESRCH, "Process has exited")
            if status != 258:  # WAIT_TIMEOUT: the process is still running
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _close(handle)
else:
    def probe_process(pid: int) -> None:
        os.kill(pid, 0)
