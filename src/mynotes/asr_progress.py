"""Per-attempt stopwatch and liveness; a heartbeat is not inference progress."""
from __future__ import annotations
import os
import sys
import threading
import time
from .utils import atomic_json_write


def continuous_time():
    # Darwin CLOCK_MONOTONIC_RAW includes sleep; mach_absolute_time does not.
    if sys.platform == "darwin":
        return time.clock_gettime(time.CLOCK_MONOTONIC_RAW)
    if hasattr(time, "CLOCK_BOOTTIME"):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    return time.monotonic()


class AttemptProgress:
    def __init__(self, workspace, directory, attempt_dir, state, *, progress=False):
        self.workspace, self.directory, self.attempt_dir = workspace, directory, attempt_dir
        self.state = state
        self.progress = progress
        self.started = state.get("started_clock", continuous_time())
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)

    def _write(self):
        now = continuous_time()
        self.state.update(updated_at=time.time(), updated_clock=now,
                          elapsed_seconds=round(max(0, now-self.started), 3))
        atomic_json_write(self.attempt_dir / "status.json", self.state)
        atomic_json_write(self.directory / "status.json", self.state)
        # Compatibility only. Parallel dashboards read per-attempt state.
        atomic_json_write(self.workspace.state_dir / "asr-active.json", self.state)

    def __call__(self, phase=None, **changes):
        with self.lock:
            if self.state.get("finished_at"):
                return
            # A subprocess heartbeat must not replace normalization/repair phase.
            if phase == "processing":
                phase = None
            if phase == "completed":
                phase = "finalizing"
                changes.pop("status", None)
            self.state.update(changes)
            if phase is not None:
                self.state["phase"] = phase
            self._write()
            if self.progress and phase:
                print(f"Transcription {self.state['lecture_id']}: {phase} ({self.state['elapsed_seconds']:.0f}s elapsed)", file=sys.stderr, flush=True)

    def _heartbeat(self):
        while not self.stop.wait(2):
            self()

    def start(self):
        self("accepted")
        self.thread.start()
        return self

    def finish(self, status, **changes):
        self.stop.set()
        with self.lock:
            self.state.update(changes, status=status, phase="completed" if status == "COMPLETED" else "stopped",
                              engine_running=False)
            self._write()
            self.state["finished_at"] = self.state["updated_at"]
            self.state["finished_clock"] = self.state["updated_clock"]
            # Freeze the final value; do not recalculate on later polls.
            atomic_json_write(self.attempt_dir / "status.json", self.state)
            atomic_json_write(self.directory / "status.json", self.state)
            atomic_json_write(self.workspace.state_dir / "asr-active.json", self.state)
        if self.thread.is_alive():
            self.thread.join(timeout=3)
