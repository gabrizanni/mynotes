"""Local execution clocks and idle-sleep protection; no network changes."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import os
import subprocess
import sys
import time


class AttemptStart(float):
    def __new__(cls):
        value = super().__new__(cls, time.monotonic())
        value.wall = time.time()
        return value


def timestamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def continuous_seconds() -> float:
    if sys.platform != "darwin":
        return time.monotonic()
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    class Timebase(ctypes.Structure):
        _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]
    base = Timebase()
    library.mach_timebase_info(ctypes.byref(base))
    library.mach_continuous_time.restype = ctypes.c_uint64
    return library.mach_continuous_time() * base.numer / base.denom / 1e9


class SleepMonitor:
    def __init__(self):
        self.active = time.monotonic()
        self.continuous = continuous_seconds()

    def poll(self) -> float:
        active, continuous = time.monotonic(), continuous_seconds()
        slept = max(0., continuous - self.continuous - (active - self.active))
        self.active, self.continuous = active, continuous
        return slept if slept > 5 else 0.


@contextmanager
def prevent_idle_sleep():
    """Only this job's assertion; does not override lid/manual sleep."""
    process = None
    if sys.platform == "darwin" and os.path.isfile("/usr/bin/caffeinate"):
        process = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        yield
    finally:
        if process is not None:
            process.terminate()
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
