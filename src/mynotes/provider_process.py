"""Owned Windows provider processes and bounded reads from anonymous pipes."""
from __future__ import annotations

import os
import queue
import selectors
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from typing import BinaryIO, Hashable

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def _win_function(name, result, *arguments):
        function = getattr(_kernel32, name)
        function.restype = result
        function.argtypes = arguments
        return function

    class _BasicJobLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedJobLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicJobLimits),
            ("IoInfo", ctypes.c_ulonglong * 6),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _ThreadEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    _create_job = _win_function("CreateJobObjectW", wintypes.HANDLE, ctypes.c_void_p, wintypes.LPCWSTR)
    _set_job = _win_function("SetInformationJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.INT,
                            ctypes.c_void_p, wintypes.DWORD)
    _assign_job = _win_function("AssignProcessToJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE)
    _close_handle = _win_function("CloseHandle", wintypes.BOOL, wintypes.HANDLE)
    _thread_snapshot = _win_function("CreateToolhelp32Snapshot", wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)
    _first_thread = _win_function("Thread32First", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(_ThreadEntry))
    _next_thread = _win_function("Thread32Next", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(_ThreadEntry))
    _open_thread = _win_function("OpenThread", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _resume = _win_function("ResumeThread", wintypes.DWORD, wintypes.HANDLE)

    def _resume_suspended_process(pid: int) -> None:
        # Popen closes the primary thread handle. With CREATE_SUSPENDED, its
        # initial thread cannot run or create another thread before this lookup.
        snapshot = _thread_snapshot(0x04, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = _first_thread(snapshot, ctypes.byref(entry))
            while found and entry.th32OwnerProcessID != pid:
                entry.dwSize = ctypes.sizeof(entry)
                found = _next_thread(snapshot, ctypes.byref(entry))
            if not found:
                raise OSError("The suspended provider process has no primary thread")
            thread = _open_thread(0x02, False, entry.th32ThreadID)  # THREAD_SUSPEND_RESUME
            if not thread:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                count = _resume(thread)
                if count == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                if count != 1:
                    raise OSError("The provider did not have exactly one pending resume")
            finally:
                _close_handle(thread)
        finally:
            _close_handle(snapshot)


class WindowsJob:
    """Run a Windows process tree with kill-on-close ownership."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("A Windows job requires native Windows")
        self._handle = _create_job(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedJobLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _set_job(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def start(self, command: list[str], *, console: bool = False, **options) -> subprocess.Popen:
        flags = 0x04  # CREATE_SUSPENDED
        if not console:
            flags |= subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen(command, creationflags=flags, **options)
        try:
            # The supported runtime is CPython. Use its existing process
            # handle, so assignment can never target a reused numeric PID.
            if not _assign_job(self._handle, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            _resume_suspended_process(process.pid)
        except BaseException:
            process.kill()
            self.close()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            process.wait(timeout=5)
            raise
        return process

    def close(self) -> None:
        if self._handle and not _close_handle(self._handle):
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = None

    def __enter__(self) -> WindowsJob:
        return self

    def __exit__(self, *_exception) -> None:
        self.close()


class SelectablePipes:
    """Keep Unix pipe reads on the native selector path."""

    def __init__(self, pipes: Mapping[Hashable, BinaryIO]) -> None:
        self._selector = selectors.DefaultSelector()
        for name, pipe in pipes.items():
            self._selector.register(pipe, selectors.EVENT_READ, name)

    @property
    def active(self):
        return self._selector.get_map()

    def read(self, timeout: float) -> list[tuple[Hashable, bytes]]:
        chunks = []
        for key, _ in self._selector.select(timeout):
            chunk = os.read(key.fd, 65536)
            if chunk:
                chunks.append((key.data, chunk))
            else:
                self._selector.unregister(key.fileobj)
        return chunks

    def close(self) -> None:
        self._selector.close()


class ThreadedPipes:
    """Drain subprocess pipes on Windows, where selectors only accept sockets."""

    def __init__(self, pipes: Mapping[Hashable, BinaryIO]) -> None:
        self._queue: queue.Queue[tuple[Hashable, bytes | OSError]] = queue.Queue(maxsize=32)
        self._stop = threading.Event()
        self.active = set(pipes)
        self._threads = [
            threading.Thread(target=self._read, args=(name, pipe), daemon=True)
            for name, pipe in pipes.items()
        ]
        for thread in self._threads:
            thread.start()

    def _emit(self, name: Hashable, data: bytes | OSError) -> None:
        while not self._stop.is_set():
            try:
                self._queue.put((name, data), timeout=0.05)
                return
            except queue.Full:
                continue

    def _read(self, name: Hashable, pipe: BinaryIO) -> None:
        try:
            while not self._stop.is_set():
                chunk = os.read(pipe.fileno(), 65536)
                self._emit(name, chunk)
                if not chunk:
                    return
        except OSError as error:
            self._emit(name, error)

    def read(self, timeout: float) -> list[tuple[Hashable, bytes]]:
        deadline = time.monotonic() + timeout
        while self.active:
            try:
                name, data = self._queue.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                break
            if isinstance(data, OSError):
                self.active.discard(name)
                raise data
            if data:
                return [(name, data)]
            self.active.discard(name)
        return []

    def close(self) -> None:
        # Call after closing the job, which releases writers holding pipe ends.
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=0.5)


def write_windows_pipe(pipe: BinaryIO, data: bytes, *, command: list[str], deadline: float,
                       check_cancelled: Callable[[], None] | None = None) -> None:
    """Bound blocking pipe writes; closing the job releases a stopped writer."""
    result: queue.Queue[OSError | None] = queue.Queue(maxsize=1)
    descriptor = os.dup(pipe.fileno())

    def write() -> None:
        pending = memoryview(data)
        try:
            while pending:
                written = os.write(descriptor, pending)
                if not written:
                    raise OSError("The provider stdin pipe accepted no data")
                pending = pending[written:]
        except OSError as error:
            failure = error
        else:
            failure = None
        finally:
            os.close(descriptor)
        result.put(failure)

    writer = threading.Thread(target=write, daemon=True)
    try:
        writer.start()
    except BaseException:
        os.close(descriptor)
        raise
    while True:
        if check_cancelled is not None:
            check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, 0)
        try:
            error = result.get(timeout=min(remaining, 0.25))
        except queue.Empty:
            continue
        if error is not None:
            raise error
        return


def run_windows_job(command: list[str], *, timeout: float | None, cwd=None, env=None,
                    input_text: str | None = None, console: bool = False,
                    check_cancelled: Callable[[], None] | None = None) -> subprocess.CompletedProcess:
    """Run an executable directly, cancelling its owned tree on every exit."""
    if check_cancelled is not None:
        check_cancelled()
    with ExitStack() as stack:
        job = stack.enter_context(WindowsJob())
        stdin = None if console else subprocess.DEVNULL
        if input_text is not None:
            # Feeding an anonymous pipe can block before communicate's timeout
            # starts on Windows. A rewound file also lets the provider read EOF.
            stdin = stack.enter_context(tempfile.TemporaryFile())
            stdin.write(input_text.encode("utf-8"))
            stdin.seek(0)
        process = job.start(
            command, cwd=cwd, env=env, console=console, stdin=stdin, bufsize=0,
            stdout=None if console else subprocess.PIPE, stderr=None if console else subprocess.PIPE,
        )
        deadline = time.monotonic() + timeout if timeout is not None else None
        try:
            while True:
                if check_cancelled is not None:
                    check_cancelled()
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    stdout, stderr = process.communicate(timeout=min(0.25, remaining) if remaining is not None else 0.25)
                    break
                except subprocess.TimeoutExpired:
                    if process.poll() is not None:
                        # A provider that has already exited may have left
                        # descendants holding its exact stdout/stderr handles.
                        job.close()
                        stdout, stderr = process.communicate(timeout=5)
                        break
        except BaseException as error:
            job.close()
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired as drain_error:
                stdout, stderr = drain_error.output, drain_error.stderr
            error.output, error.stderr = stdout, stderr
            raise
        finally:
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
        return subprocess.CompletedProcess(
            command, process.returncode,
            stdout.decode("utf-8", errors="replace") if stdout is not None else None,
            stderr.decode("utf-8", errors="replace") if stderr is not None else None,
        )
