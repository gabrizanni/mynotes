"""Linux Capture worker contract with a fake recorder; no microphone is opened."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import time
import uuid
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux Capture worker")

from mynotes import capture, capture_linux, onboarding  # noqa: E402
from mynotes.utils import atomic_json_write  # noqa: E402
from mynotes.workspace import Workspace  # noqa: E402

# Streams a 440 Hz tone as raw s16le mono 48 kHz until SIGINT, or for a fixed
# number of chunks in "finite" mode to simulate a lost device.
FAKE_RECORDER = r"""
import math, signal, struct, sys, time
signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
chunk = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * n / 48000))) for n in range(960))
remaining = int(sys.argv[1]) if len(sys.argv) > 1 else -1
while remaining:
    sys.stdout.buffer.write(chunk)
    sys.stdout.buffer.flush()
    remaining -= 1
    time.sleep(0.005)
sys.exit(3)
"""
SEGMENT = re.compile(r"[0-9]{19}-[0-9a-f]{32}\.wav")


def fake_recorder(*arguments: str, segment_seconds: float = 0.2):
    return lambda folder: capture_linux.MicrophoneRecording(
        folder, [sys.executable, "-c", FAKE_RECORDER, *arguments], segment_seconds=segment_seconds)


class Clock:
    def __init__(self):
        self.now = datetime.now(UTC)

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MYNOTES_CAPTURE_HOME", str(tmp_path / "private Capture"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv(capture_linux.NOTIFY_ENV, raising=False)
    workspace = Workspace.from_value(tmp_path / "corsi & lezioni")
    workspace.initialize()
    result = capture.CaptureStore(workspace)
    capture.configure(result, {})
    config = result.config()
    config["enabled"] = True
    result.save_config(config)
    result.save_schedule([], result.config())
    return result


def make_worker(store, clock, recorder=None, suspend=lambda: 0.0):
    return capture_linux.Worker(store.workspace.root, capture.runtime_home(), clock=clock,
                                recorder=recorder or fake_recorder(), suspend_probe=suspend)


def settle(worker, *, seconds=20.0):
    deadline = time.monotonic() + seconds
    worker.tick()
    while worker.exports and time.monotonic() < deadline:
        time.sleep(0.05)
        worker.tick()
    assert not worker.exports, "export did not finish"


def response(store, request_id):
    return json.loads((store.directory / "responses" / f"{request_id}.json").read_text())


def session(store, session_id):
    return json.loads((store.directory / "sessions" / f"{session_id}.json").read_text())


def standalone_event(now, minutes=30):
    raw = {"recordingID": uuid.uuid4().hex, "title": "Ripasso libero", "startsAt": now, "endsAt": now + timedelta(minutes=minutes)}
    event = capture.normalize_event(raw, set(), "manual")
    event["recordAt"], event["stopAt"] = event["startsAt"], event["endsAt"]
    return event


def test_output_names_match_the_native_workers():
    base = {"id": "x", "sourceID": "s", "startsAt": "2026-03-02T09:00:00Z", "endsAt": "2026-03-02T10:00:00Z",
            "recordAt": "2026-03-02T09:00:00Z", "stopAt": "2026-03-02T10:00:00Z", "timezone": "UTC",
            "eligibility": "SCHEDULED", "reason": "", "transcriptionLanguage": None}
    course = capture_linux.lesson({**base, "courseCode": "ECON", "lessonNumber": 3, "title": "Economia", "recordingID": None})
    assert capture_linux.output_stem(course) == "ECON_L03"
    short = capture_linux.lesson({**base, "courseCode": "ECON", "lessonNumber": 3, "title": "FIN L3", "recordingID": None})
    assert capture_linux.output_stem(short) == "FIN_L03"
    free = capture_linux.lesson({**base, "courseCode": None, "lessonNumber": None, "title": "Città & note",
                                 "recordingID": "a" * 32})
    assert capture_linux.output_stem(free) == f"1772442000-Citt----note-{'a' * 32}"
    assert capture_linux.valid_window(course, ["ECON"]) and not capture_linux.valid_window(course, ["OTHER"])


def test_segment_header_is_valid_while_recording(tmp_path):
    writer = capture_linux.WavSegmentWriter(tmp_path / capture_linux.segment_name())
    writer.write(b"\x01\x00" * 4800)
    writer.flush()
    with wave.open(str(writer.path), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getnframes()) == (48_000, 1, 4800)
    writer.write(b"\x02\x00" * 4800)
    writer.close()
    with wave.open(str(writer.path), "rb") as audio:
        assert audio.getnframes() == 9600
    assert SEGMENT.fullmatch(writer.path.name)


def test_recording_rotates_segments_and_keeps_every_frame(tmp_path):
    recording = fake_recorder(segment_seconds=0.05)(tmp_path / "stage")
    recording.start()
    time.sleep(0.6)
    assert not recording.completed
    assert recording.stop() is None
    recording.dispose()
    segments = sorted((tmp_path / "stage").glob("*.wav"))
    assert len(segments) >= 2 and all(SEGMENT.fullmatch(path.name) for path in segments)
    frames = []
    for path in segments:
        with wave.open(str(path), "rb") as audio:
            frames.append(audio.getnframes())
    assert all(count == 2400 for count in frames[:-1]) and 0 < frames[-1] <= 2400


def test_lost_recorder_is_reported_as_completed_with_an_error(tmp_path):
    recording = fake_recorder("5")(tmp_path / "stage")
    recording.start()
    deadline = time.monotonic() + 10
    while not recording.completed and time.monotonic() < deadline:
        time.sleep(0.02)
    assert recording.completed
    assert "status 3" in str(recording.stop())
    recording.dispose()


def test_microphone_test_records_exports_and_confirms(store):
    clock = Clock()
    worker = make_worker(store, clock)
    answer = capture.request(store, "test", wait_seconds=0)
    worker.tick()
    started = response(store, answer["requestID"])
    assert started["status"] == "RECORDING"
    runtime = json.loads(store.runtime_path.read_text())
    assert runtime["recording"] is True and runtime["currentSession"]["isTest"] is True
    assert set(runtime["capabilities"]) == {"standalone-recordings-v1", "calendar-recordings-v1"}
    assert {"schemaVersion", "updatedAt", "microphonePermission", "scheduleIssues", "blockedEvents"} <= runtime.keys()
    assert capture.capture_status(store)["running"] is True
    time.sleep(0.4)
    clock.advance(minutes=1, seconds=1)
    settle(worker)
    record = session(store, started["sessionID"])
    assert record["status"] == "COMPLETED" and record["durationSeconds"] > 0
    audio = Path(record["outputPath"])
    assert audio.name == "microphone-test.m4a" and Path(f"{audio}.capture.json").is_file()
    assert onboarding.confirm_capture_audio(store.workspace, audio, audible=True)["validated"] is True
    assert json.loads(store.runtime_path.read_text())["microphonePermission"] == "AUTHORIZED"
    worker.shutdown()
    assert json.loads(store.runtime_path.read_text())["pid"] == 0


def test_manual_start_and_stop_saves_a_partial_recording(store):
    clock = Clock()
    worker = make_worker(store, clock)
    event = standalone_event(clock.now)
    started = capture.request(store, "start", {"event": event}, wait_seconds=0)
    worker.tick()
    assert response(store, started["requestID"])["status"] == "RECORDING"
    time.sleep(0.4)
    stopped = capture.request(store, "stop", wait_seconds=0)
    settle(worker)
    answer = response(store, stopped["requestID"])
    assert answer["status"] == "PARTIAL", answer
    record = session(store, answer["sessionID"])
    assert Path(record["outputPath"]).parent == Path(store.config()["outputDirectory"])
    assert Path(record["outputPath"]).name == capture_linux.output_stem(record["event"]) + ".m4a"
    assert Path(record["outputPath"]).is_file() and record["reason"].endswith("Stopped manually")
    assert not list(Path(record["outputPath"]).parent.glob(".capture-*.tmp"))
    worker.shutdown()


def test_lost_device_interrupts_then_recovers_after_the_window(store):
    clock = Clock()
    worker = make_worker(store, clock, recorder=fake_recorder("20"))
    event = standalone_event(clock.now, minutes=1)
    started = capture.request(store, "start", {"event": event}, wait_seconds=0)
    worker.tick()
    identity = response(store, started["requestID"])["sessionID"]
    deadline = time.monotonic() + 10
    while worker.current is not None and time.monotonic() < deadline:
        time.sleep(0.05)
        worker.tick()
    assert session(store, identity)["status"] == "INTERRUPTED"
    clock.advance(minutes=2)
    settle(worker)
    recovered = session(store, identity)
    assert recovered["status"] == "PARTIAL" and "Recovered after interruption" in recovered["reason"]
    worker.shutdown()


def test_resume_from_suspend_interrupts_the_recording(store):
    clock = Clock()
    offsets = iter([0.0, 0.0, 60.0])
    worker = make_worker(store, clock, suspend=lambda: next(offsets, 60.0))
    started = capture.request(store, "start", {"event": standalone_event(clock.now)}, wait_seconds=0)
    worker.tick()
    identity = response(store, started["requestID"])["sessionID"]
    time.sleep(0.3)
    worker.tick()
    record = session(store, identity)
    assert record["status"] == "INTERRUPTED" and "sleep" in record["reason"]
    worker.shutdown()


def test_invalid_requests_are_rejected_and_disable_ends_the_loop(store):
    clock = Clock()
    worker = make_worker(store, clock)
    expired = uuid.uuid4().hex
    atomic_json_write(store.directory / "requests" / f"{expired}.json", {
        "schemaVersion": 1, "id": expired, "action": "test", "payload": {},
        "createdAt": capture.iso(clock.now - timedelta(minutes=5)), "expiresAt": capture.iso(clock.now - timedelta(minutes=4))})
    unknown = capture.request(store, "dance", wait_seconds=0)
    worker.tick()
    assert response(store, expired) == {"status": "REJECTED", "requestID": expired, "message": "Expired or invalid request",
                                        "sessionID": None, "outputPath": None}
    assert response(store, unknown["requestID"])["status"] == "REJECTED"
    assert not list((store.directory / "requests").glob("*.json"))
    config = store.config()
    config["enabled"] = False
    store.save_config(config)
    assert worker.tick() is False
    worker.shutdown()


def test_restart_marks_unfinished_sessions_interrupted_and_lease_is_exclusive(store):
    clock = Clock()
    identity = str(uuid.uuid4())
    stage = Path(store.config()["stageDirectory"]) / identity
    event = standalone_event(clock.now)
    atomic_json_write(store.directory / "sessions" / f"{identity}.json", {
        "schemaVersion": 1, "id": identity, "event": event, "directory": str(stage), "actualStart": capture.iso(clock.now),
        "actualStop": None, "status": "RECORDING", "partial": False, "reason": "", "segments": [], "outputPath": None,
        "destinationDirectory": store.config()["outputDirectory"], "durationSeconds": None, "requestID": None,
        "isTest": False, "exportSHA256": None, "exportTemporaryPath": None})
    worker = make_worker(store, clock)
    assert session(store, identity)["status"] == "INTERRUPTED"
    with pytest.raises(capture_linux.CaptureBusyError):
        make_worker(store, clock)
    worker.shutdown()


def test_recorder_prefers_pipewire_then_pulse_through_ffmpeg(monkeypatch):
    monkeypatch.delenv(capture_linux.RECORDER_ENV, raising=False)
    tools = {"pw-record": "/usr/bin/pw-record", "ffmpeg": "/usr/bin/ffmpeg"}
    command = capture_linux.recorder_command("lecture.monitor", which=tools.get)
    assert command[:2] == ["/usr/bin/pw-record", "--raw"] and command[-3:] == ["--target", "lecture.monitor", "-"]
    assert ["--rate", "48000"] == command[2:4] and "s16" in command
    fallback = capture_linux.recorder_command("", which={"ffmpeg": "/usr/bin/ffmpeg"}.get)
    assert fallback[0] == "/usr/bin/ffmpeg" and fallback[fallback.index("-i") + 1] == "default"
    assert fallback[-3:] == ["-f", "s16le", "-"]
    monkeypatch.setenv(capture_linux.RECORDER_ENV, "ffmpeg")
    assert capture_linux.recorder_command("", which=tools.get)[0] == "/usr/bin/ffmpeg"
    with pytest.raises(capture.CaptureError, match="PipeWire"):
        capture_linux.recorder_command("", which=lambda _: None)


def test_notifications_are_opt_in(monkeypatch):
    calls = []
    monkeypatch.setattr(capture_linux.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(capture_linux.subprocess, "run", lambda command, **kwargs: calls.append(command))
    monkeypatch.delenv(capture_linux.NOTIFY_ENV, raising=False)
    capture_linux.notify("Registrazione in corso", "Lezione")
    assert calls == []
    monkeypatch.setenv(capture_linux.NOTIFY_ENV, "1")
    capture_linux.notify("Registrazione in corso", "Lezione")
    assert calls == [["/usr/bin/notify-send", "--app-name=MyNotes Capture", "Registrazione in corso", "Lezione"]]


def test_service_unit_quotes_paths_and_mirrors_the_launch_agent(tmp_path):
    workspace = tmp_path / 'corsi "A" & 100% $HOME'
    text = capture_linux.unit_text(Path("/opt/mynotes/venv/bin/python"), workspace, tmp_path / "Capture")
    exec_start = next(line for line in text.splitlines() if line.startswith("ExecStart="))
    assert exec_start.startswith('ExecStart="/opt/mynotes/venv/bin/python" "-I" "-X" "utf8" "-m" "mynotes.capture_linux"')
    assert '\\"A\\" & 100%% $$HOME' in exec_start
    for line in ("Restart=on-failure", "RestartSec=30", "KillMode=mixed", "WantedBy=default.target",
                 'Environment="MYNOTES_CAPTURE_NOTIFY=1"'):
        assert line in text.splitlines()
    with pytest.raises(capture.CaptureError):
        capture_linux.unit_text(Path("/usr/bin/python3"), tmp_path / "bad\nname", tmp_path)
    with pytest.raises(capture.CaptureError):
        capture_linux.unit_text(Path("python3"), workspace, tmp_path)


def fake_systemctl(fail=None):
    calls = []

    def run(*arguments):
        calls.append(arguments)
        failed = fail is not None and arguments[0] == fail
        return subprocess.CompletedProcess(["systemctl", "--user", *arguments], int(failed), "", "unavailable" if failed else "")
    return run, calls


def test_enable_installs_and_starts_the_user_service(store, monkeypatch):
    monkeypatch.setattr(capture_linux, "recorder_available", lambda: True)
    run, calls = fake_systemctl()
    assert capture_linux.set_enabled(store, True, systemctl=run) == {"status": "ENABLED", "audioReadinessVerified": False}
    assert calls == [("daemon-reload",), ("enable", "mynotes-capture.service"), ("restart", "mynotes-capture.service")]
    unit = capture_linux.unit_path()
    assert unit == Path(store.workspace.root.parent / "xdg/systemd/user/mynotes-capture.service")
    assert f'"{sys.executable}"' in unit.read_text() and str(store.workspace.root.resolve()) in unit.read_text()
    assert json.loads((capture.runtime_home() / "active.json").read_text())["workspace"] == str(store.workspace.root.resolve())
    assert capture.standalone_supported(store) is True

    calls.clear()
    assert capture_linux.set_enabled(store, False, systemctl=run) == {"status": "DISABLED"}
    assert calls == [("disable", "--now", "mynotes-capture.service"), ("daemon-reload",)]
    assert not unit.exists() and not (capture.runtime_home() / "active.json").exists()
    assert store.config()["enabled"] is False
    assert capture.standalone_supported(store) is False


def test_failed_service_start_rolls_back(store, monkeypatch):
    monkeypatch.setattr(capture_linux, "recorder_available", lambda: True)
    config = store.config()
    config["enabled"] = False
    store.save_config(config)
    run, calls = fake_systemctl(fail="restart")
    with pytest.raises(capture.CaptureError, match="could not start"):
        capture_linux.set_enabled(store, True, systemctl=run)
    assert calls[-2:] == [("disable", "--now", "mynotes-capture.service"), ("daemon-reload",)]
    assert not capture_linux.unit_path().exists()
    assert store.config()["enabled"] is False
    assert not (capture.runtime_home() / "active.json").exists()


def test_enable_is_refused_while_the_recorder_holds_the_update_lease(store, monkeypatch):
    from mynotes import file_lock
    monkeypatch.setattr(capture_linux, "recorder_available", lambda: True)
    home = capture.runtime_home()
    home.mkdir(parents=True, exist_ok=True)
    with (home / "update.lock").open("a+b") as lease:
        file_lock.flock(lease, file_lock.LOCK_EX)
        with pytest.raises(capture.CaptureError, match="Finish the active recording"):
            capture_linux.set_enabled(store, True, systemctl=fake_systemctl()[0])


def test_install_guard_restarts_the_systemd_service(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("capture_install_guard", Path(__file__).parents[1] / "scripts/capture_install_guard.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(guard, "LINUX", True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    home = tmp_path / "Capture"
    home.mkdir()
    (home / "active.json").write_text(json.dumps({"workspace": str(tmp_path / "workspace")}))
    unit = tmp_path / "xdg/systemd/user/mynotes-capture.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\n")
    monkeypatch.setenv("MYNOTES_CAPTURE_HOME", str(home))
    monkeypatch.setattr(sys, "argv", ["guard", sys.executable, "-c", "raise SystemExit(0)"])
    calls = []
    monkeypatch.setattr(guard.subprocess, "run", lambda command, **kwargs: calls.append(command) or subprocess.CompletedProcess(command, 0))
    assert guard.main() == 0
    assert calls == [["systemctl", "--user", "stop", "mynotes-capture.service"],
                     ["systemctl", "--user", "start", "mynotes-capture.service"]]


def test_scheduled_lessons_start_alone_and_elapsed_ones_are_reported(store, monkeypatch, tmp_path):
    monkeypatch.setenv("MYNOTES_USER_HOME", str(tmp_path / "user"))
    first = onboarding.add_first_course(store.workspace, "Economia")["code"]
    second = onboarding.add_first_course(store.workspace, "Finanza")["code"]
    now = datetime.now(UTC).replace(microsecond=0)
    at = lambda **delta: capture.iso(now + timedelta(**delta))  # noqa: E731
    capture.import_schedule(store, {"events": [
        {"courseCode": first, "lessonNumber": 1, "title": "Economia", "startsAt": at(hours=-3), "endsAt": at(hours=-2)},
        {"courseCode": first, "lessonNumber": 2, "title": "Economia", "startsAt": at(minutes=-1), "endsAt": at(minutes=30)},
        {"courseCode": first, "lessonNumber": 3, "title": "FIN L3", "startsAt": at(days=1), "endsAt": at(days=1, hours=1)},
        {"courseCode": second, "lessonNumber": 3, "title": "FIN L3", "startsAt": at(days=2), "endsAt": at(days=2, hours=1)},
    ]})
    config = store.config()
    config["enabled"] = True
    store.save_config(config)
    worker = make_worker(store, Clock())
    worker.tick()
    assert worker.current is not None and worker.current["event"]["lessonNumber"] == 2
    assert worker.current["partial"] is True and "Started late" in worker.current["reason"]
    sessions = [json.loads(path.read_text()) for path in (store.directory / "sessions").glob("*.json")]
    assert [s["event"]["lessonNumber"] for s in sessions if s["status"] == "MISSED"] == [1]
    runtime = json.loads(store.runtime_path.read_text())
    assert sorted(runtime["scheduleIssues"]) == ["FIN L3: Duplicate recording filename"] * 2
    worker.shutdown()
    assert session(store, runtime["currentSession"]["id"])["status"] == "INTERRUPTED"


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_recordings_and_their_folders_are_owner_only(store):
    previous = os.umask(0o022)
    try:
        clock = Clock()
        stage_root = Path(store.config()["stageDirectory"])
        stage_root.chmod(0o755)
        worker = make_worker(store, clock)
        started = capture.request(store, "start", {"event": standalone_event(clock.now)}, wait_seconds=0)
        worker.tick()
        record = session(store, response(store, started["requestID"])["sessionID"])
        deadline = time.monotonic() + 10
        while not list(Path(record["directory"]).glob("*.wav")) and time.monotonic() < deadline:
            time.sleep(0.05)
        segments = list(Path(record["directory"]).glob("*.wav"))
        assert segments and all(mode(path) == 0o600 for path in segments)
        capture.request(store, "stop", wait_seconds=0)
        settle(worker)
        audio = Path(session(store, record["id"])["outputPath"])
        assert audio.is_file() and mode(audio) == 0o600
        for folder in (capture.runtime_home(), stage_root, Path(record["directory"]), store.directory,
                       store.directory / "requests", store.directory / "responses", store.directory / "sessions"):
            assert mode(folder) == 0o700, folder
        assert all(mode(path) == 0o600 for path in (store.directory / "sessions").glob("*.json"))
        worker.shutdown()
    finally:
        os.umask(previous)
