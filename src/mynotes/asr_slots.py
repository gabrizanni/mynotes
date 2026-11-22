"""Crash-safe, bounded ASR admission shared by direct and queued requests."""
from contextlib import contextmanager
from . import file_lock
import json


def _active(workspace):
    try:
        return json.loads((workspace.state_dir / "asr-active.json").read_text())
    except (OSError, ValueError):
        return {}


@contextmanager
def admission(workspace):
    from .asr import ASRBusyError
    root = workspace.state_dir
    root.mkdir(parents=True, exist_ok=True)
    legacy = (root / ".asr.lock").open("a+")
    slot = None
    try:
        # Older runtimes hold this exclusively; new runtimes share it while
        # occupying independent slots. Neither generation may over-admit the other.
        try:
            file_lock.flock(legacy, file_lock.LOCK_SH | file_lock.LOCK_NB)
        except BlockingIOError as error:
            raise ASRBusyError("BUSY: a legacy transcription is running", _active(workspace)) from error
        with (root / ".asr-admission.lock").open("a+") as gate:
            file_lock.flock(gate, file_lock.LOCK_EX)
            try:
                setting = json.loads((root / "asr-concurrency.json").read_text())
                limit = setting["parallel"]
                if type(limit) is not int or not 1 <= limit <= 4:
                    raise ValueError("Invalid ASR admission limit")
            except FileNotFoundError:
                limit = 1
            held, free = 0, []
            for number in range(4):
                handle = (root / f".asr-slot-{number}.lock").open("a+")
                try:
                    file_lock.flock(handle, file_lock.LOCK_EX | file_lock.LOCK_NB)
                    free.append(handle)
                except BlockingIOError:
                    held += 1
                    handle.close()
            if held < limit and free:
                slot = free.pop(0)
            for handle in free:
                handle.close()
        if slot is None:
            raise ASRBusyError("BUSY: all configured transcription slots are occupied", _active(workspace))
        yield
    finally:
        if slot is not None:
            slot.close()
        legacy.close()
