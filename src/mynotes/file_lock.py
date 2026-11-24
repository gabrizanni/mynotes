"""Shared and exclusive locks for MyNotes' dedicated lease files."""
from __future__ import annotations

import os

if os.name == "nt":
    import ctypes
    import errno
    import msvcrt
    from ctypes import wintypes

    LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN = 1, 2, 4, 8

    class _Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _lock = _kernel.LockFileEx
    _lock.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                      wintypes.DWORD, ctypes.POINTER(_Overlapped)]
    _lock.restype = wintypes.BOOL
    _unlock = _kernel.UnlockFileEx
    _unlock.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                        ctypes.POINTER(_Overlapped)]
    _unlock.restype = wintypes.BOOL

    def flock(file, operation: int) -> None:
        fd = file if isinstance(file, int) else file.fileno()
        handle = msvcrt.get_osfhandle(fd)
        offset = _Overlapped()
        # Lock the first byte even when the empty lease file is shorter than that.
        if operation == LOCK_UN:
            if not _unlock(handle, 0, 1, 0, ctypes.byref(offset)):
                raise ctypes.WinError(ctypes.get_last_error())
            return
        flags = (2 if operation & LOCK_EX else 0) | (1 if operation & LOCK_NB else 0)
        if not _lock(handle, flags, 0, 1, 0, ctypes.byref(offset)):
            error = ctypes.get_last_error()
            if error == 33:  # ERROR_LOCK_VIOLATION
                raise BlockingIOError(errno.EAGAIN, "MyNotes lease is held")
            raise ctypes.WinError(error)

else:
    import fcntl

    LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN = fcntl.LOCK_SH, fcntl.LOCK_EX, fcntl.LOCK_NB, fcntl.LOCK_UN

    def flock(file, operation: int) -> None:
        fcntl.flock(file, operation)
