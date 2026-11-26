"""Linux Capture worker: a Python port of the Windows recorder loop.

The worker reads the same configuration, schedule and request files as the
native recorders and writes the same responses, sessions and camelCase
``runtime.json``, so the CLI, the dashboard and the apps share one contract.
``pw-record`` (PipeWire) or ``ffmpeg -f pulse`` streams raw 48 kHz mono PCM
to stdout; the worker writes it into staged WAV segments that rotate every
ten minutes. A systemd user service keeps the worker running, the way a
LaunchAgent does on macOS and a scheduled task does on Windows.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import os
import re
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, TypedDict
from zoneinfo import ZoneInfo

from . import file_lock
from .capture import CaptureError, CaptureStore, capture_status, iso, read_json, runtime_home, timestamp
from .platform_paths import linux_config_home
from .utils import atomic_json_write
from .workspace import Workspace

UNIT_NAME = "mynotes-capture.service"
CAPABILITIES = ("standalone-recordings-v1", "calendar-recordings-v1")
SAMPLE_RATE = 48_000
CHANNELS = 1
SAMPLE_WIDTH = 2
SEGMENT_SECONDS = 600
FLUSH_SECONDS = 1.0
TICK_SECONDS = 0.5
STATUS_SECONDS = 4
SYNC_MINUTES = 5
STOP_TIMEOUT_SECONDS = 10
# A recorder still running this long after Popen has opened its source. One
# that exits sooner (no device, no PipeWire session) fails the attempt once,
# instead of being relaunched on every tick for the rest of the lesson.
STARTUP_CHECK_SECONDS = 0.5
STDERR_TAIL_BYTES = 2048
EXPORT_TIMEOUT_SECONDS = 30 * 60
SUSPEND_GAP_SECONDS = 2.0
REQUEST_LIFETIME = timedelta(minutes=1)
REQUEST_CLOCK_SKEW = timedelta(seconds=5)
LATE_START = timedelta(seconds=5)
DOTNET_EPOCH_TICKS = 621_355_968_000_000_000
# Lecture audio and its metadata are private: owner-only folders and files.
PRIVATE_DIRECTORY = 0o700
PRIVATE_FILE = 0o600
# Opt-in so tests and foreground debugging never show desktop notifications.
NOTIFY_ENV = "MYNOTES_CAPTURE_NOTIFY"
SOURCE_ENV = "MYNOTES_CAPTURE_SOURCE"
RECORDER_ENV = "MYNOTES_CAPTURE_RECORDER"
GUID = re.compile(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
SHORT_TITLE = re.compile(r"^([A-Z][A-Z0-9]{1,15})[ _-]+(?:L[ _-]*)?([0-9]{1,3})$")
COURSE = re.compile(r"^[A-Z0-9]+$")
RECORDING = re.compile(r"^[0-9a-f]{32}$")
LESSON_FIELDS = ("id", "sourceID", "courseCode", "lessonNumber", "startsAt", "endsAt", "recordAt", "stopAt",
                 "timezone", "title", "eligibility", "reason", "recordingID", "transcriptionLanguage")


class Lesson(TypedDict):
    """The scheduled-event fields the native workers read (Contracts.cs)."""
    id: str
    sourceID: str
    courseCode: str | None
    lessonNumber: int | None
    startsAt: str
    endsAt: str
    recordAt: str
    stopAt: str
    timezone: str
    title: str
    eligibility: str
    reason: str
    recordingID: str | None
    transcriptionLanguage: str | None


class CaptureSession(TypedDict):
    schemaVersion: int
    id: str
    event: Lesson
    directory: str
    actualStart: str
    actualStop: str | None
    status: str
    partial: bool
    reason: str
    segments: list[str]
    outputPath: str | None
    destinationDirectory: str
    durationSeconds: float | None
    requestID: str | None
    isTest: bool
    exportSHA256: str | None
    exportTemporaryPath: str | None


class CaptureBusyError(OSError):
    """The installer holds the update lease; retry once it is released."""


def _log(message: str, **details: Any) -> None:
    # The journal keeps these lines; never include audio or calendar contents.
    suffix = " — " + ", ".join(f"{key}={value}" for key, value in details.items()) if details else ""
    print(f"[CaptureLinux] {message}{suffix}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- lessons

def lesson(raw: Any) -> Lesson:
    """Project an event onto the worker contract, rejecting missing fields."""
    if not isinstance(raw, dict):
        raise ValueError("Invalid recording details")
    for key in ("id", "sourceID", "startsAt", "endsAt", "recordAt", "stopAt", "timezone", "title", "eligibility"):
        if not isinstance(raw.get(key), str):
            raise ValueError("Invalid recording details")
    for key in ("startsAt", "endsAt", "recordAt", "stopAt"):
        timestamp(raw[key])
    result = {key: raw.get(key) for key in LESSON_FIELDS}
    result["reason"] = str(raw.get("reason") or "")
    if result["lessonNumber"] is not None and type(result["lessonNumber"]) is not int:
        raise ValueError("Invalid lesson number")
    return result  # type: ignore[return-value]


def legacy_stem(item: Lesson) -> str:
    number = item["lessonNumber"]
    if item["recordingID"] is None:
        return f"{item['courseCode'] or ''}_L{number:02d}" if number is not None else f"{item['courseCode'] or ''}_L"
    slug = "".join(char if char.isascii() and char.isalnum() else "-" for char in item["title"][:64])
    return f"{int(timestamp(item['startsAt']).timestamp())}-{slug}-{item['recordingID']}"


def output_stem(item: Lesson) -> str:
    match = SHORT_TITLE.match(item["title"].strip())
    if match:
        number = int(match[2])
        if 1 <= number <= 999 and item["lessonNumber"] in (None, number):
            return f"{match[1]}_L{number:02d}"
    if item["courseCode"] is not None and item["lessonNumber"] is not None:
        return f"{item['courseCode']}_L{item['lessonNumber']:02d}"
    return legacy_stem(item)


def valid_window(item: Lesson, courses: list[str]) -> bool:
    if not item["id"].strip() or not item["title"].strip() or len(item["title"]) > 8192:
        return False
    if item["courseCode"] is not None and (not COURSE.match(item["courseCode"]) or item["courseCode"] not in courses):
        return False
    if item["lessonNumber"] is not None and not 1 <= item["lessonNumber"] <= 999:
        return False
    if item["recordingID"] is None:
        if item["courseCode"] is None or item["lessonNumber"] is None:
            return False
    elif not RECORDING.match(item["recordingID"]):
        return False
    try:
        ZoneInfo(item["timezone"])
    except (KeyError, ValueError):
        return False
    record_at, starts, ends, stop_at = (timestamp(item[key]) for key in ("recordAt", "startsAt", "endsAt", "stopAt"))
    return (record_at <= starts < ends <= stop_at and ends - starts <= timedelta(hours=12)
            and stop_at - record_at <= timedelta(hours=14))


def is_current(item: Lesson, now: datetime) -> bool:
    return item["eligibility"] == "SCHEDULED" and timestamp(item["recordAt"]) <= now < timestamp(item["endsAt"])


def can_resume(previous: Lesson, item: Lesson) -> bool:
    same_times = all(timestamp(previous[key]) == timestamp(item[key]) for key in ("recordAt", "startsAt", "endsAt", "stopAt"))
    return (previous["id"] == item["id"] and previous["recordingID"] == item["recordingID"]
            and previous["courseCode"] == item["courseCode"] and previous["lessonNumber"] == item["lessonNumber"]
            and same_times and output_stem(previous).casefold() == output_stem(item).casefold())


# ------------------------------------------------------------- privacy

def private_directory(path: Path) -> Path:
    """Create a folder as 0700, and tighten one this user already owns."""
    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIRECTORY)
    details = path.stat()
    if details.st_uid == os.getuid() and stat.S_IMODE(details.st_mode) != PRIVATE_DIRECTORY:
        path.chmod(PRIVATE_DIRECTORY)
    return path


def create_private_file(path: Path):
    """Exclusive 0600 creation; an existing file is never reused or truncated."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE)
    return os.fdopen(descriptor, "wb")


# ------------------------------------------------------------- recording

def segment_name(now_ns: int | None = None) -> str:
    """Same 19-digit UTC-tick prefix as the Windows recorder, so order is stable."""
    ticks = (time.time_ns() if now_ns is None else now_ns) // 100 + DOTNET_EPOCH_TICKS
    return f"{ticks:019d}-{uuid.uuid4().hex}.wav"


class WavSegmentWriter:
    """PCM WAV whose header is rewritten on every flush, like NAudio's writer."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data_bytes = 0
        self._handle = create_private_file(path)
        self._write_header()

    def _write_header(self) -> None:
        block = CHANNELS * SAMPLE_WIDTH
        header = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + self.data_bytes, b"WAVE", b"fmt ", 16, 1,
                             CHANNELS, SAMPLE_RATE, SAMPLE_RATE * block, block, SAMPLE_WIDTH * 8,
                             b"data", self.data_bytes)
        self._handle.seek(0)
        self._handle.write(header)
        self._handle.seek(44 + self.data_bytes)

    def write(self, frames: bytes) -> None:
        self._handle.write(frames)
        self.data_bytes += len(frames)

    def flush(self) -> None:
        self._write_header()
        self._handle.flush()

    def close(self) -> None:
        if self._handle.closed:
            return
        try:
            self.flush()
            os.fsync(self._handle.fileno())
        finally:
            self._handle.close()


def recorder_command(source: str | None = None, *, which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """Raw s16le mono 48 kHz PCM on stdout from PipeWire, else PulseAudio via FFmpeg."""
    source = os.environ.get(SOURCE_ENV) if source is None else source
    forced = os.environ.get(RECORDER_ENV, "")
    if forced not in {"", "pw-record", "ffmpeg"}:
        raise CaptureError(f"{RECORDER_ENV} must be pw-record or ffmpeg")
    pipewire = which("pw-record") if forced in {"", "pw-record"} else None
    if pipewire:
        command = [pipewire, "--raw", "--rate", str(SAMPLE_RATE), "--channels", str(CHANNELS), "--format", "s16"]
        return command + (["--target", source] if source else []) + ["-"]
    ffmpeg = which("ffmpeg") if forced in {"", "ffmpeg"} else None
    if ffmpeg:
        return [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "pulse", "-i", source or "default",
                "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"]
    raise CaptureError("Install PipeWire (pw-record) or FFmpeg with PulseAudio input to record audio")


class MicrophoneRecording:
    """Port of Audio.cs: one recorder process, staged into rotating WAV segments."""

    def __init__(self, folder: Path, command: list[str], *, segment_seconds: float = SEGMENT_SECONDS) -> None:
        self.folder = folder
        self.command = command
        self.segment_bytes = max(SAMPLE_WIDTH, int(segment_seconds * SAMPLE_RATE) * CHANNELS * SAMPLE_WIDTH)
        self.error: Exception | None = None
        self._process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._stderr_tail = b""
        self._writer: WavSegmentWriter | None = None
        self._gate = threading.Lock()
        self._stopping = False
        private_directory(folder)

    @property
    def completed(self) -> bool:
        """True once the recorder ended on its own (device lost, crash, write error)."""
        return self._reader is not None and not self._reader.is_alive() and not self._stopping

    def start(self) -> None:
        """Start the recorder; CaptureError if it exits within STARTUP_CHECK_SECONDS."""
        self._process = subprocess.Popen(self.command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, start_new_session=True)
        self._stderr_reader = threading.Thread(target=self._keep_stderr, name="capture-recorder-stderr", daemon=True)
        self._stderr_reader.start()
        self._reader = threading.Thread(target=self._read, name="capture-reader", daemon=True)
        self._reader.start()
        try:
            code = self._process.wait(timeout=STARTUP_CHECK_SECONDS)
        except subprocess.TimeoutExpired:
            return
        self._stopping = True  # Not a lost device: this attempt never started.
        self._reader.join(STOP_TIMEOUT_SECONDS)
        self._stderr_reader.join(STOP_TIMEOUT_SECONDS)
        detail = self._stderr_tail.decode("utf-8", errors="replace").strip().splitlines()
        message = f"The recorder {Path(self.command[0]).name} exited at once with status {code}"
        raise CaptureError(message + (f": {detail[-1]}" if detail else ""))

    def _keep_stderr(self) -> None:
        """Drain the recorder's stderr so it never blocks; keep the end for error messages."""
        assert self._process is not None and self._process.stderr is not None
        try:
            while chunk := self._process.stderr.read1(4096):
                self._stderr_tail = (self._stderr_tail + chunk)[-STDERR_TAIL_BYTES:]
        except (OSError, ValueError):
            pass

    def _read(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        pending = b""
        flushed = time.monotonic()
        frame = CHANNELS * SAMPLE_WIDTH
        try:
            while chunk := self._process.stdout.read1(65536):
                pending += chunk
                usable = len(pending) - len(pending) % frame
                data, pending = pending[:usable], pending[usable:]
                with self._gate:
                    while data:
                        if self._writer is None:
                            # Each attempt stages distinct files; never overwrite a segment.
                            self._writer = WavSegmentWriter(self.folder / segment_name())
                        room = self.segment_bytes - self._writer.data_bytes
                        self._writer.write(data[:room])
                        data = data[room:]
                        if self._writer.data_bytes >= self.segment_bytes:
                            self._close_segment()
                    if self._writer is not None and time.monotonic() - flushed >= FLUSH_SECONDS:
                        self._writer.flush()
                        flushed = time.monotonic()
        except OSError as error:
            self.error = error
            self._terminate()
        finally:
            with self._gate:
                try:
                    self._close_segment()
                except OSError as error:
                    self.error = self.error or error
        code = self._process.wait()
        if code and not self._stopping and self.error is None:
            self.error = OSError(f"Recorder exited with status {code}")

    def _close_segment(self) -> None:
        segment, self._writer = self._writer, None
        if segment is not None:
            segment.close()

    def _terminate(self) -> None:
        if self._process is not None and self._process.poll() is None:
            try:
                os.killpg(self._process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def stop(self, timeout: float = STOP_TIMEOUT_SECONDS) -> Exception | None:
        """Ask the recorder to finish, keep the valid prefix, report any failure."""
        self._stopping = True
        if self._process is None or self._reader is None:
            return self.error
        if self._process.poll() is None:
            try:
                os.killpg(self._process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
        self._reader.join(timeout)
        if self._reader.is_alive():
            self._terminate()
            self._reader.join(timeout)
            self.error = self.error or TimeoutError("The recorder did not stop in time")
        return self.error

    def dispose(self) -> None:
        self._stopping = True
        self._terminate()
        for reader in (self._reader, self._stderr_reader):
            if reader is not None:
                reader.join(STOP_TIMEOUT_SECONDS)
        if self._process is not None:
            for stream in (self._process.stdout, self._process.stderr):
                if stream is not None:
                    stream.close()


# ---------------------------------------------------------------- helpers

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def stage_segments(session: CaptureSession) -> list[str]:
    folder = Path(session["directory"]) if session["directory"] else None
    if folder is None or not folder.is_dir():
        return []
    return sorted(str(path) for path in folder.glob("*.wav") if path.is_file() and path.stat().st_size > 44)


def publish_without_overwrite(temporary: Path, destination: Path) -> None:
    try:
        os.link(temporary, destination)
    except FileExistsError:
        raise
    except OSError:
        # File systems without hard links; the worker is the only writer here.
        if destination.exists():
            raise FileExistsError(destination)
        os.rename(temporary, destination)
        return
    temporary.unlink()


def suspend_offset() -> float:
    """Grows by the time spent suspended: BOOTTIME counts sleep, MONOTONIC does not."""
    return time.clock_gettime(time.CLOCK_BOOTTIME) - time.clock_gettime(time.CLOCK_MONOTONIC)


def _started(session: CaptureSession) -> datetime:
    try:
        return timestamp(session["actualStart"])
    except (KeyError, TypeError, ValueError):
        return datetime.min.replace(tzinfo=UTC)


def notify(summary: str, body: str) -> None:
    if os.environ.get(NOTIFY_ENV) != "1":
        return
    sender = shutil.which("notify-send")
    if not sender:
        return
    try:
        subprocess.run([sender, "--app-name=MyNotes Capture", summary, body], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        _log("Notification unavailable", error=type(error).__name__)


class BackgroundCommand:
    """A Python child the loop polls without blocking (export or calendar sync)."""

    def __init__(self, command: list[str], env: dict[str, str], *, timeout: float | None = None,
                 capture: bool = False) -> None:
        self.result: subprocess.CompletedProcess | None = None
        self.error: Exception | None = None
        self._thread = threading.Thread(target=self._run, args=(command, env, timeout, capture), daemon=True)
        self._thread.start()

    def _run(self, command, env, timeout, capture) -> None:
        output = subprocess.PIPE if capture else subprocess.DEVNULL
        try:
            self.result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                         text=True, timeout=timeout, start_new_session=True, check=False)
        except subprocess.TimeoutExpired:
            self.error = TimeoutError("Capture export exceeded the time limit")
        except OSError as error:
            self.error = error

    @property
    def done(self) -> bool:
        return not self._thread.is_alive()

    def wait(self) -> None:
        self._thread.join()


def export_seconds(job: BackgroundCommand) -> float:
    if job.error is not None:
        raise job.error if isinstance(job.error, OSError) else OSError(str(job.error))
    result = job.result
    try:
        seconds = float((result.stdout or "").strip()) if result else math.nan
    except ValueError:
        seconds = math.nan
    if result is None or result.returncode != 0 or not math.isfinite(seconds) or seconds <= 0:
        raise OSError("Audio conversion failed; staged recording preserved for recovery")
    return seconds


# ----------------------------------------------------------------- worker

class Worker:
    """Port of Worker.cs; every public file keeps the native field names."""

    def __init__(self, workspace: Path, capture_home: Path, *,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 recorder: Callable[[Path], MicrophoneRecording] | None = None,
                 suspend_probe: Callable[[], float] = suspend_offset) -> None:
        self.workspace = workspace
        self.capture_home = capture_home
        self.state_directory = workspace / "state" / "capture"
        self.config_path = workspace / "config" / "capture.json"
        self.schedule_path = self.state_directory / "schedule.json"
        self.request_directory = self.state_directory / "requests"
        self.response_directory = self.state_directory / "responses"
        self.session_directory = self.state_directory / "sessions"
        self.clock = clock
        self.recorder_factory = recorder or (lambda folder: MicrophoneRecording(folder, recorder_command()))
        self.suspend_probe = suspend_probe
        self.sessions: dict[str, CaptureSession] = {}
        self.exports: dict[str, tuple[CaptureSession, BackgroundCommand, Callable[[bool], None] | None]] = {}
        self.attempted: set[str] = set()
        self.automatic_recovery_attempted: set[str] = set()
        self.blocked: dict[str, str] = {}
        self.current: CaptureSession | None = None
        self.recorder: MicrophoneRecording | None = None
        self.update_lease = None
        self.calendar_sync: BackgroundCommand | None = None
        self.next_sync = datetime.min.replace(tzinfo=UTC)
        self.next_status = datetime.min.replace(tzinfo=UTC)
        self.title = "Capture"
        self.detail = ""
        self.permission = "NOT_DETERMINED"
        self.interrupted = False
        self.cancelled = threading.Event()
        self.config = read_json(self.config_path)
        self.schedule = read_json(self.schedule_path)
        self._validate_state(self.config, self.schedule)
        self._rebuild_blocked(self.config, self.schedule)
        self._last_suspend = suspend_probe()
        self.instance = self._acquire(capture_home / "app.lock")
        try:
            for directory in (self.state_directory, self.request_directory, self.response_directory, self.session_directory):
                private_directory(directory)
            self._load_sessions()
        except BaseException:
            self.instance.close()
            raise

    # -- lifecycle

    @staticmethod
    def _acquire(path: Path):
        private_directory(path.parent)
        lease = path.open("a+b")
        try:
            file_lock.flock(lease, file_lock.LOCK_EX | file_lock.LOCK_NB)
        except BlockingIOError as error:
            lease.close()
            raise CaptureBusyError(f"Another process holds {path.name}") from error
        return lease

    def run(self) -> None:
        _log("Worker started", pid=os.getpid())
        try:
            while not self.cancelled.is_set():
                if not self.tick():
                    break
                self.cancelled.wait(TICK_SECONDS)
        finally:
            self.shutdown()

    def tick(self) -> bool:
        """One 500 ms iteration; False once Capture is disabled and idle."""
        now = self.clock()
        offset = self.suspend_probe()
        if offset - self._last_suspend > SUSPEND_GAP_SECONDS:
            self.interrupted = True
        self._last_suspend = offset
        if self.interrupted:
            self.interrupted = False
            self.stop(now, "System sleep interrupted recording", interrupt=True)
        valid = self._reload()
        self.finish_exports()
        if self.current is not None:
            if self.recorder is not None and self.recorder.completed:
                self.stop(now, "Microphone recording interrupted", interrupt=True)
            elif now >= timestamp(self.current["event"]["stopAt"]):
                self.stop(now, "Scheduled end")
        if valid and not self.config["enabled"] and self.current is None and not self.exports:
            return False
        if valid and self.config["enabled"]:
            self.handle_requests(now)
            self.reconcile_elapsed(now)
            if self.current is None and not self.config["paused"]:
                self.start_scheduled(now)
            self.recover_automatically(now)
            self.sync_calendar(now)
        if now >= self.next_status:
            self.persist_runtime(now)
        return True

    def shutdown(self) -> None:
        self.stop(self.clock(), "Capture shut down", interrupt=True)
        for _session, job, _done in list(self.exports.values()):
            job.wait()
        self.finish_exports()
        if self.calendar_sync is not None:
            self.calendar_sync.wait()
        self.title = "Capture stopped"
        self.persist_runtime(self.clock(), stopped=True)
        self._release_update_lease()
        self.instance.close()
        _log("Worker stopped")

    # -- state

    @staticmethod
    def _validate_state(config: Any, schedule: Any) -> None:
        if (not isinstance(config, dict) or not isinstance(schedule, dict) or config.get("schemaVersion") != 1
                or schedule.get("schemaVersion") != 1 or not isinstance(config.get("courseCodes"), list)
                or not isinstance(schedule.get("events"), list)
                or not Path(str(config.get("stageDirectory", ""))).is_absolute()
                or not Path(str(config.get("outputDirectory", ""))).is_absolute()):
            raise ValueError("Unsupported Capture configuration or schedule")

    def _reload(self) -> bool:
        try:
            config, schedule = read_json(self.config_path), read_json(self.schedule_path)
            self._validate_state(config, schedule)
            if config.get("revision") != self.config.get("revision") or schedule.get("revision") != self.schedule.get("revision"):
                self._rebuild_blocked(config, schedule)
            self.config, self.schedule = config, schedule
            return True
        except (OSError, ValueError) as error:
            self.announce("Capture configuration unavailable", str(error))
            return False

    def _events(self, schedule: dict | None = None) -> list[Lesson]:
        events = []
        for raw in (schedule or self.schedule)["events"]:
            try:
                events.append(lesson(raw))
            except ValueError:
                continue
        return events

    def _rebuild_blocked(self, config: dict, schedule: dict) -> None:
        self.blocked.clear()
        eligible = [item for item in self._events(schedule) if item["eligibility"] == "SCHEDULED"]
        counts: dict[str, int] = {}
        for item in eligible:
            counts[item["id"]] = counts.get(item["id"], 0) + 1
        for identity, count in counts.items():
            if count > 1:
                self.blocked[identity] = "Duplicate calendar event"
        for item in eligible:
            if not valid_window(item, config["courseCodes"]):
                self.blocked[item["id"]] = "Invalid recording details or time window"
        valid = sorted((item for item in eligible if item["id"] not in self.blocked), key=lambda item: timestamp(item["startsAt"]))
        stems: dict[str, list[Lesson]] = {}
        for item in valid:
            stems.setdefault(output_stem(item).casefold(), []).append(item)
        for group in stems.values():
            if len(group) > 1:
                for item in group:
                    self.blocked[item["id"]] = "Duplicate recording filename"
        for index, first in enumerate(valid):
            for second in valid[index + 1:]:
                if timestamp(second["startsAt"]) >= timestamp(first["endsAt"]):
                    break
                self.blocked[first["id"]] = self.blocked[second["id"]] = "Overlapping lectures"

    def _load_sessions(self) -> None:
        for path in self.session_directory.glob("*.json"):
            try:
                session = read_json(path)
                if (not isinstance(session, dict) or session.get("schemaVersion") != 1
                        or not GUID.fullmatch(str(session.get("id"))) or session["id"] != path.stem):
                    continue
                session["event"] = lesson(session.get("event"))
                self.sessions[session["id"]] = session
                if session["status"] in {"RECORDING", "EXPORTING"} or (session["status"] == "FAILED" and session.get("exportSHA256")):
                    output = session.get("outputPath")
                    if (session.get("exportSHA256") and output and Path(output).is_file()
                            and sha256(Path(output)).casefold() == session["exportSHA256"].casefold()):
                        self._finalize_export(session)
                        continue
                    self._save({**session, "status": "INTERRUPTED", "partial": True,
                                "actualStop": session.get("actualStop") or iso(self.clock()),
                                "reason": "Capture restarted during recording or export; staged audio retained",
                                "segments": stage_segments(session)})
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.announce("A Capture session could not be restored", str(error))

    def _save(self, session: CaptureSession) -> CaptureSession:
        atomic_json_write(self.session_directory / f"{session['id']}.json", session)
        self.sessions[session["id"]] = session
        return session

    def respond(self, request_id: str, status: str, message: str, session: CaptureSession | None = None) -> None:
        atomic_json_write(self.response_directory / f"{request_id}.json", {
            "status": status, "requestID": request_id, "message": message,
            "sessionID": session["id"] if session else None, "outputPath": session.get("outputPath") if session else None,
        })
        (self.request_directory / f"{request_id}.json").unlink(missing_ok=True)

    def announce(self, message: str, details: str) -> None:
        self.title, self.detail = message, details
        self.next_status = datetime.min.replace(tzinfo=UTC)

    def persist_runtime(self, now: datetime, *, stopped: bool = False) -> None:
        self.next_status = now + timedelta(seconds=STATUS_SECONDS)
        events = self._events()
        atomic_json_write(self.state_directory / "runtime.json", {
            "schemaVersion": 1, "pid": 0 if stopped else os.getpid(), "updatedAt": iso(now),
            "recording": self.current is not None, "exporting": bool(self.exports),
            "microphonePermission": self.permission, "title": self.title, "detail": self.detail,
            "currentSession": self.current,
            "sessions": sorted(self.sessions.values(), key=_started, reverse=True),
            "capabilities": list(CAPABILITIES),
            "scheduleIssues": [f"{item['title']}: {self.blocked[item['id']]}" for item in events if item["id"] in self.blocked],
            "blockedEvents": dict(self.blocked),
        })

    # -- leases

    def _ensure_update_lease(self) -> None:
        if self.update_lease is not None:
            return
        try:
            self.update_lease = self._acquire(self.capture_home / "update.lock")
        except CaptureBusyError as error:
            raise CaptureBusyError("Capture is blocked during an application update") from error

    def _release_update_lease(self) -> None:
        if self.current is not None or self.exports or self.update_lease is None:
            return
        self.update_lease.close()
        self.update_lease = None

    # -- outputs

    def has_output(self, item: Lesson, folder: str | None = None) -> bool:
        folder = folder or self.config["outputDirectory"]
        for name in (output_stem(item), legacy_stem(item)):
            if any((Path(folder) / f"{name}{suffix}").exists() for suffix in (".m4a", ".wav")):
                return True
        return any(s["event"]["id"] == item["id"] and s.get("outputPath") and Path(s["outputPath"]).exists()
                   for s in self.sessions.values())

    def exporting(self, event_id: str) -> bool:
        return any(session["event"]["id"] == event_id for session, _job, _done in self.exports.values())

    # -- recording

    def start_scheduled(self, now: datetime) -> None:
        candidates = sorted((item for item in self._events() if is_current(item, now) and item["id"] not in self.blocked),
                            key=lambda item: timestamp(item["recordAt"]))
        item = next((item for item in candidates if item["id"] not in self.attempted
                     and not self.has_output(item) and not self.exporting(item["id"])), None)
        if item is None:
            return
        try:
            self.begin(item, now)
        except (OSError, ValueError, RuntimeError) as error:
            # The installer holds the lease only briefly; retry once it is released.
            if not isinstance(error, CaptureBusyError):
                self.attempted.add(item["id"])
            self.announce("Recording could not start", str(error))

    def begin(self, item: Lesson, now: datetime, request_id: str | None = None, *, is_test: bool = False) -> None:
        if self.current is not None:
            raise RuntimeError("A recording is already active")
        if ((not is_test and (not valid_window(item, self.config["courseCodes"]) or item["id"] in self.blocked))
                or not is_current(item, now) or (not is_test and (self.has_output(item) or self.exporting(item["id"])))):
            raise RuntimeError("Recording is unavailable or existing audio was retained")
        self._ensure_update_lease()
        try:
            interrupted = [s for s in self.sessions.values()
                           if s["event"]["id"] == item["id"] and s["status"] == "INTERRUPTED" and not s["isTest"]]
            if len(interrupted) == 1 and can_resume(interrupted[0]["event"], item) and not is_test:
                self.current = {**interrupted[0], "actualStop": None, "status": "RECORDING", "partial": True,
                                "reason": "Resumed after interruption; audio contains a gap", "requestID": request_id}
            else:
                identity = str(uuid.uuid4())
                late = not is_test and now > timestamp(item["startsAt"]) + LATE_START
                reason = ("New attempt after a calendar change or ambiguity; earlier audio retained" if interrupted
                          else "Started late; beginning of the lesson was not recorded" if late else "")
                stage_root = private_directory(Path(self.config["stageDirectory"]))
                stage = (private_directory(stage_root / "tests") if is_test else stage_root) / identity
                self.current = {
                    "schemaVersion": 1, "id": identity, "event": item, "directory": str(stage), "actualStart": iso(now),
                    "actualStop": None, "status": "RECORDING", "partial": late or bool(interrupted), "reason": reason,
                    "segments": [], "outputPath": None, "destinationDirectory": self.config["outputDirectory"],
                    "durationSeconds": None, "requestID": request_id, "isTest": is_test,
                    "exportSHA256": None, "exportTemporaryPath": None,
                }
            self._save(self.current)
            self.recorder = self.recorder_factory(Path(self.current["directory"]))
            self.recorder.start()
            self.announce("Microphone test" if is_test else "Recording", item["title"])
            _log("Recording started", session=self.current["id"], test=is_test)
            notify("Test del microfono" if is_test else "Registrazione in corso", item["title"])
            self.persist_runtime(now)
        except (OSError, ValueError, CaptureError) as error:
            if self.recorder is not None:
                self.recorder.dispose()
            self.recorder = None
            failed, self.current = self.current, None
            if isinstance(error, (FileNotFoundError, CaptureError)):
                self.permission = "UNAVAILABLE"
            try:
                if failed is not None:
                    self._save({**failed, "status": "FAILED", "actualStop": iso(now), "partial": True, "reason": str(error)})
            finally:
                self._release_update_lease()
            raise

    def stop(self, now: datetime, reason: str, *, interrupt: bool = False, request_id: str | None = None) -> None:
        session, microphone = self.current, self.recorder
        if session is None or microphone is None:
            if request_id is not None:
                self.respond(request_id, "REJECTED", "No active recording")
            return
        try:
            error = microphone.stop()
            if error is not None:
                interrupt, reason = True, str(error)
        finally:
            microphone.dispose()
            self.recorder = self.current = None
        segments = stage_segments(session)
        if segments:
            self.permission = "AUTHORIZED"
        early = not session["isTest"] and now < timestamp(session["event"]["endsAt"]) - timedelta(seconds=5)
        if session["reason"].strip():
            reason = session["reason"] + "; " + reason
        session = {**session, "actualStop": iso(now), "partial": session["partial"] or interrupt or early,
                   "reason": reason, "segments": segments, "requestID": request_id or session["requestID"]}
        _log("Recording stopped", session=session["id"], segments=len(segments), interrupted=interrupt)
        if interrupt or not segments:
            session = self._save({**session, "status": "INTERRUPTED" if interrupt else "FAILED", "partial": True})
            self._release_update_lease()
            notify("Registrazione interrotta", session["event"]["title"])
            if request_id is not None:
                self.respond(request_id, session["status"], "No microphone audio was captured" if not segments else reason, session)
            return
        notify("Registrazione terminata", session["event"]["title"])
        try:
            self.queue_export(session, request_id)
        except OSError as error:
            session = self._save({**session, "status": "FAILED", "partial": True, "reason": str(error)})
            self._release_update_lease()
            if request_id is not None:
                self.respond(request_id, session["status"], str(error), session)
            self.announce("Audio export unavailable", str(error))

    # -- export

    def queue_export(self, value: CaptureSession, request_id: str | None = None,
                     done: Callable[[bool], None] | None = None) -> None:
        self._ensure_update_lease()
        try:
            destination = (Path(value["directory"]) / "microphone-test.m4a" if value["isTest"]
                           else Path(value["destinationDirectory"]) / f"{output_stem(value['event'])}.m4a")
            if destination.exists() or (not value["isTest"] and self.has_output(value["event"], value["destinationDirectory"])):
                raise FileExistsError("Existing output retained")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.parent / f".capture-{value['id']}-{uuid.uuid4().hex}.tmp"
            session = self._save({**value, "status": "EXPORTING", "segments": stage_segments(value),
                                  "requestID": request_id or value["requestID"], "outputPath": str(destination),
                                  "exportSHA256": None, "exportTemporaryPath": str(temporary)})
            self.exports[session["id"]] = (session, self._start_export(session["id"]), done)
            if self.current is None:
                self.announce("Saving audio", value["event"]["title"])
            self.persist_runtime(self.clock())
        except BaseException:
            self._release_update_lease()
            raise

    def _python(self) -> list[str]:
        python = str(self.config.get("pythonExecutable") or "")
        if not Path(python).is_absolute():
            raise OSError("Capture requires an absolute Python executable")
        return [python, "-I", "-X", "utf8", "-m"]

    def _child_env(self) -> dict[str, str]:
        return dict(os.environ, MYNOTES_CAPTURE_HOME=str(self.capture_home))

    def _start_export(self, session_id: str) -> BackgroundCommand:
        command = [*self._python(), "mynotes.capture_export", "export", "--workspace", str(self.workspace), "--session", session_id]
        return BackgroundCommand(command, self._child_env(), timeout=EXPORT_TIMEOUT_SECONDS, capture=True)

    def finish_exports(self) -> None:
        for key, (session, job, done) in [(k, v) for k, v in self.exports.items() if v[1].done]:
            success = False
            try:
                temporary = Path(session["exportTemporaryPath"])
                duration = export_seconds(job)
                session = self._save({**session, "durationSeconds": duration, "exportSHA256": sha256(temporary)})
                publish_without_overwrite(temporary, Path(session["outputPath"]))
                session = self._finalize_export(session)
                success = True
            except (OSError, ValueError) as error:
                # Keep the digest: startup reconciles a move whose metadata write failed.
                session = self._save({**session, "status": "FAILED", "partial": True, "reason": str(error)})
            finally:
                del self.exports[key]
                self._release_update_lease()
            _log("Export finished", session=session["id"], success=success)
            if session["requestID"] and done is None:
                self.respond(session["requestID"], session["status"], "Audio saved" if success else session["reason"], session)
            if done is not None:
                done(success)
            if self.current is None:
                self.announce("Audio saved" if success else "Audio export failed",
                              session["event"]["title"] if success else session["reason"])

    def _finalize_export(self, session: CaptureSession) -> CaptureSession:
        final = {**session, "status": "PARTIAL" if session["partial"] else "COMPLETED", "exportTemporaryPath": None}
        if final["isTest"]:
            atomic_json_write(Path(f"{final['outputPath']}.capture.json"), final)
        return self._save(final)

    # -- requests

    def handle_requests(self, now: datetime) -> None:
        for path in sorted(self.request_directory.glob("*.json")):
            request_id = path.stem
            if not GUID.fullmatch(request_id):
                continue
            if (self.response_directory / f"{request_id}.json").exists():
                path.unlink(missing_ok=True)
                continue
            if any(session["requestID"] == request_id for session, _job, _done in self.exports.values()):
                continue
            try:
                self._handle_request(path, request_id, now)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, CaptureError) as error:
                self.respond(request_id, "REJECTED", str(error))

    def _handle_request(self, path: Path, request_id: str, now: datetime) -> None:
        request = read_json(path)
        try:
            created, expires = timestamp(request["createdAt"]), timestamp(request["expiresAt"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Expired or invalid request") from error
        if (request.get("id") != request_id or request.get("schemaVersion") != 1 or expires < now
                or created > now + REQUEST_CLOCK_SKEW or expires < created or expires - created > REQUEST_LIFETIME):
            raise ValueError("Expired or invalid request")
        previous = next((s for s in self.sessions.values() if s["requestID"] == request_id), None)
        if previous is not None:
            self.respond(request_id, previous["status"], "Previously accepted action", previous)
            return
        payload = request.get("payload") or {}
        action = request.get("action")
        if action == "start" and payload.get("event") is not None:
            item = lesson(payload["event"])
            if timestamp(item["stopAt"]) <= now + timedelta(hours=12):
                self.begin(item, now, request_id)
                self.respond(request_id, "RECORDING", "Recording started", self.current)
                return
        elif action == "test":
            end = iso(now + timedelta(minutes=1))
            test: Lesson = {"id": str(uuid.uuid4()), "sourceID": "test", "courseCode": "TEST", "lessonNumber": 1,
                            "startsAt": iso(now), "endsAt": end, "recordAt": iso(now), "stopAt": end, "timezone": "UTC",
                            "title": "Microphone test", "eligibility": "SCHEDULED", "reason": "",
                            "recordingID": None, "transcriptionLanguage": None}
            self.begin(test, now, request_id, is_test=True)
            self.respond(request_id, "RECORDING", "60-second microphone test started", self.current)
            return
        elif action == "stop":
            self.stop(now, "Stopped manually", request_id=request_id)
            return
        elif action == "recover":
            self.recover_requested(payload.get("sessionID"), request_id)
            return
        self.respond(request_id, "REJECTED", "Unknown action or invalid recording details")

    # -- recovery

    def recovery_candidates(self) -> list[CaptureSession]:
        return [s for s in self.sessions.values()
                if s["status"] in {"INTERRUPTED", "FAILED"} and not s["isTest"] and not self.exporting(s["event"]["id"])
                and not self.has_output(s["event"], s["destinationDirectory"]) and stage_segments(s)]

    @staticmethod
    def _by_stem(sessions: list[CaptureSession]) -> dict[str, list[CaptureSession]]:
        groups: dict[str, list[CaptureSession]] = {}
        for session in sessions:
            groups.setdefault(output_stem(session["event"]).casefold(), []).append(session)
        return groups

    def recover_requested(self, requested: str | None, request_id: str) -> None:
        if self.current is not None:
            raise RuntimeError("Stop the recording before recovering staged audio")
        eligible = [s for s in self.recovery_candidates() if requested is None or s["id"] == requested]
        if not eligible:
            raise RuntimeError("No eligible staged session")
        if requested is None and any(len(group) != 1 for group in self._by_stem(eligible).values()):
            raise RuntimeError("Multiple attempts exist; specify --session")
        state = {"remaining": len(eligible), "successes": 0}

        def finished(session_id: str) -> Callable[[bool], None]:
            def done(ok: bool) -> None:
                state["successes"] += int(ok)
                state["remaining"] -= 1
                if state["remaining"] == 0:
                    self.respond(request_id, "PARTIAL" if state["successes"] == len(eligible) else "FAILED",
                                 f"Recovered {state['successes']}/{len(eligible)} attempts",
                                 self.sessions[session_id] if len(eligible) == 1 else None)
            return done

        for session in eligible:
            done = finished(session["id"])
            try:
                # A single recovery keeps a durable request association of its own.
                self.queue_export({**session, "partial": True, "reason": "Recovered staged audio; verify coverage", "requestID": None},
                                  request_id if len(eligible) == 1 else None, done)
            except OSError as error:
                self.announce("Recovery failed", str(error))
                done(False)
        # A grouped request stays in flight until the export callbacks answer it.
        if len(eligible) > 1 and state["remaining"] > 0:
            atomic_json_write(self.response_directory / f"{request_id}.json", {
                "status": "EXPORTING", "requestID": request_id, "message": "Recovering staged audio",
                "sessionID": None, "outputPath": None})
            (self.request_directory / f"{request_id}.json").unlink(missing_ok=True)

    def recover_automatically(self, now: datetime) -> None:
        current_event = self.current["event"]["id"] if self.current else None
        candidates = [s for s in self.recovery_candidates()
                      if timestamp(s["event"]["stopAt"]) <= now and s["event"]["id"] != current_event]
        for group in self._by_stem(candidates).values():
            if len(group) != 1 or group[0]["id"] in self.automatic_recovery_attempted:
                continue
            session = group[0]
            try:
                self.queue_export({**session, "partial": True, "reason": "Recovered after interruption; verify coverage", "requestID": None})
                self.automatic_recovery_attempted.add(session["id"])
            except OSError as error:
                if not isinstance(error, CaptureBusyError):
                    self.automatic_recovery_attempted.add(session["id"])
                self.announce("Recovery unavailable", str(error))

    def reconcile_elapsed(self, now: datetime) -> None:
        for item in self._events():
            if item["eligibility"] != "SCHEDULED" or timestamp(item["endsAt"]) > now or item["id"] in self.blocked:
                continue
            if any(s["event"]["id"] == item["id"] for s in self.sessions.values()):
                continue
            exists = self.has_output(item)
            self._save({"schemaVersion": 1, "id": str(uuid.uuid4()), "event": item, "directory": "",
                        "actualStart": iso(now), "actualStop": iso(now), "status": "EXISTING" if exists else "MISSED",
                        "partial": not exists, "reason": "Existing output retained" if exists else "Recording window elapsed",
                        "segments": [], "outputPath": None, "destinationDirectory": self.config["outputDirectory"],
                        "durationSeconds": None, "requestID": None, "isTest": False,
                        "exportSHA256": None, "exportTemporaryPath": None})

    # -- calendar

    def sync_calendar(self, now: datetime) -> None:
        if self.calendar_sync is not None and self.calendar_sync.done:
            result = self.calendar_sync.result
            if self.calendar_sync.error is not None or result is None or result.returncode != 0:
                self.announce("Calendar update unsuccessful", "Check the calendar source in MyNotes")
            self.calendar_sync = None
        if self.calendar_sync is not None or now < self.next_sync or not self.config.get("sources"):
            return
        self.next_sync = now + timedelta(minutes=SYNC_MINUTES)
        try:
            command = [*self._python(), "mynotes", "capture", "sync", "--workspace", str(self.workspace), "--if-due", "--json"]
            self.calendar_sync = BackgroundCommand(command, self._child_env())
        except OSError as error:
            self.announce("Calendar sync unavailable", str(error))


# --------------------------------------------------------- systemd service

def executable() -> Path:
    """The recorder's identity for audio-readiness evidence: this worker module."""
    return Path(__file__).resolve()


def unit_path() -> Path:
    return linux_config_home() / "systemd" / "user" / UNIT_NAME


def _systemd_quote(value: str) -> str:
    if any(char in value for char in "\n\r\0"):
        raise CaptureError("Capture paths cannot contain line breaks")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{escaped}"'


def unit_text(python: Path, workspace: Path, home: Path) -> str:
    if not python.is_absolute() or not workspace.is_absolute() or not home.is_absolute():
        raise CaptureError("Capture needs absolute Python, workspace and runtime paths")
    command = [str(python), "-I", "-X", "utf8", "-m", "mynotes.capture_linux",
               "--workspace", str(workspace), "--capture-home", str(home)]
    return "\n".join((
        "# Written by mynotes capture enable; removed by mynotes capture disable.",
        "[Unit]",
        "Description=MyNotes Capture lecture recorder",
        "After=pipewire.service pipewire-pulse.service",
        "",
        "[Service]",
        "Type=simple",
        "ExecStart=" + " ".join(_systemd_quote(part) for part in command),
        "Environment=" + _systemd_quote(f"{NOTIFY_ENV}=1"),
        "Restart=on-failure",
        "RestartSec=30",
        # Stop only the worker; it ends the recorder and finishes exports itself.
        "KillMode=mixed",
        "TimeoutStopSec=120",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ))


def _systemctl(*arguments: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["systemctl", "--user", *arguments], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CaptureError(f"Could not run systemctl: {error}") from error


def _require(result: subprocess.CompletedProcess, action: str) -> None:
    if result.returncode:
        reason = (result.stderr or result.stdout or "").strip() or f"exit code {result.returncode}"
        raise CaptureError(f"systemd could not {action} MyNotes Capture: {reason}")


def recorder_available() -> bool:
    try:
        recorder_command()
    except CaptureError:
        return False
    return True


def installed() -> bool:
    """The Linux recorder is installed once its user service exists and can record."""
    return unit_path().is_file() and recorder_available()


def _write_unit(text: str) -> None:
    path = unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _remove_unit(systemctl: Callable[..., subprocess.CompletedProcess]) -> None:
    if unit_path().exists():
        systemctl("disable", "--now", UNIT_NAME)
        unit_path().unlink(missing_ok=True)
    systemctl("daemon-reload")


def set_enabled(store: CaptureStore, enabled: bool, *,
                systemctl: Callable[..., subprocess.CompletedProcess] = _systemctl) -> dict:
    """Install or remove the systemd user service; mirrors the Windows task flow."""
    home = private_directory(runtime_home().resolve())
    active_path = home / "active.json"
    workspace = store.workspace.root.resolve()
    with (home / "update.lock").open("a+b") as lease:
        try:
            file_lock.flock(lease, file_lock.LOCK_EX | file_lock.LOCK_NB)
        except BlockingIOError as error:
            raise CaptureError("Finish the active recording, audio export or application update first") from error
        active = read_json(active_path)
        if active and Path(active["workspace"]).resolve() != workspace:
            raise CaptureError("Disable Capture in its active workspace before switching workspaces")
        if enabled and (not store.config_path.exists() or not store.schedule_path.exists()):
            raise CaptureError("Configure Capture and import or synchronize a calendar first")
        state = capture_status(store)
        if enabled and active and store.config()["enabled"] and state["running"] is True:
            return {"status": "ENABLED", "alreadyRunning": True}
        if not enabled:
            with store.lock():
                config = store.config()
                if not active and not config["enabled"] and not unit_path().exists():
                    return {"status": "DISABLED"}
                _remove_unit(systemctl)
                config["enabled"] = False
                store.save_config(config)
            active_path.unlink(missing_ok=True)
            _log("Service disabled")
            return {"status": "DISABLED"}
        if not recorder_available():
            raise CaptureError("Install PipeWire (pw-record) or FFmpeg with PulseAudio input to record audio")
        _write_unit(unit_text(Path(sys.executable), workspace, home))
        previously_enabled = store.config()["enabled"]
        try:
            _require(systemctl("daemon-reload"), "reload")
            with store.lock():
                config = store.config()
                config["enabled"] = True
                store.save_config(config)
            atomic_json_write(active_path, {"workspace": str(workspace)})
            _require(systemctl("enable", UNIT_NAME), "register")
            _require(systemctl("restart", UNIT_NAME), "start")
        except (OSError, CaptureError):
            if not active:
                _remove_unit(systemctl)
                active_path.unlink(missing_ok=True)
            with store.lock():
                config = store.config()
                config["enabled"] = bool(active) and previously_enabled
                store.save_config(config)
            raise
    _log("Service enabled", unit=str(unit_path()))
    return {"status": "ENABLED", "audioReadinessVerified": False}


# ------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MyNotes Capture worker for Linux")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--capture-home", required=True, type=Path)
    arguments = parser.parse_args(argv)
    if not arguments.workspace.is_absolute() or not arguments.capture_home.is_absolute():
        print("Expected absolute --workspace and --capture-home paths", file=sys.stderr)
        return 2
    try:
        worker = Worker(Workspace.from_value(arguments.workspace).root, arguments.capture_home)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 1

    def cancel(_signum, _frame) -> None:
        worker.cancelled.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, cancel)
    worker.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
