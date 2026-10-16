from __future__ import annotations

import json
import os
import fcntl
import hashlib
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mynotes import capture
from mynotes.cli import main
from mynotes.configuration import course_template
from mynotes.ingestion import discover_recordings, recording_for_lecture
from mynotes.utils import atomic_json_write
from mynotes.workspace import Workspace


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MYNOTES_CAPTURE_HOME", str(tmp_path / "local-runtime"))
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()
    config = workspace.configuration_store().load()
    course = course_template()
    course.update(code="MATH", name="Mathematics", aliases=["mathematics course"])
    config["courses"] = [course]
    workspace.configuration_store().save(config)
    result = capture.CaptureStore(workspace)
    capture.configure(result, {})
    return result


def event(number=1, start="2026-10-12T10:00:00Z", end="2026-10-12T11:00:00Z", **values):
    return {"courseCode": "MATH", "lessonNumber": number, "startsAt": start, "endsAt": end, **values}


def subscription(**values):
    return {"sourceID": "teaching", "url": "https://calendar.example.invalid/private-token",
            "timezone": "Europe/Rome", "periodStart": "2026-10-01T00:00:00Z", "periodEnd": "2026-12-01T00:00:00Z",
            "mappings": [{"match": "Math", "courseCode": "MATH", "firstLesson": 1}], **values}


def feed(*components):
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Capture Test//EN\r\n" + "".join(components) + "END:VCALENDAR\r\n").encode()


def component(uid="one", start="20261012T100000Z", end="20261012T110000Z", title="Math", extra=""):
    return f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTART:{start}\r\nDTEND:{end}\r\nSUMMARY:{title}\r\n{extra}END:VEVENT\r\n"


def test_configuration_disabled_local_staging_and_preserves_input_directories(store):
    config = store.config()
    assert not config["enabled"] and config["refreshHours"] == 6
    assert "local-runtime" in config["stageDirectory"]
    settings = store.workspace.configuration_store().load()
    assert config["outputDirectory"] in settings["paths"]["recording_dirs"]
    capture.configure(store, {"refreshHours": 1})
    assert len(settings["paths"]["recording_dirs"]) == 1
    assert list((store.directory / "history").glob("*capture.json"))


def test_import_consecutive_clips_margin_without_conflict(store):
    capture.import_schedule(store, [event(), event(2, "2026-10-12T11:00:00Z", "2026-10-12T12:00:00Z")])
    events = store.schedule()["events"]
    assert events[0]["stopAt"] == events[1]["startsAt"]
    assert all(e["eligibility"] == "SCHEDULED" for e in events)


def test_overlaps_and_duplicate_lessons_block_only_affected_events(store):
    capture.import_schedule(store, [event(), event(2, "2026-10-12T10:30:00Z", "2026-10-12T12:00:00Z"),
                                    event(3, "2026-10-13T10:00:00Z", "2026-10-13T11:00:00Z")])
    assert [e["eligibility"] for e in store.schedule()["events"]] == ["CONFLICT", "CONFLICT", "SCHEDULED"]


def test_static_reimport_keeps_identity_and_cancels_removed_events(store):
    capture.import_schedule(store, [event(), event(2, "2026-10-13T10:00:00Z", "2026-10-13T11:00:00Z")])
    capture.import_schedule(store, [event(start="2026-10-14T10:00:00Z", end="2026-10-14T11:00:00Z")])
    by_number = {e["lessonNumber"]: e for e in store.schedule()["events"]}
    assert by_number[2]["eligibility"] == "CANCELLED"
    assert by_number[1]["id"] == "static:MATH-L01"


def test_invalid_static_import_leaves_previous_schedule(store):
    capture.import_schedule(store, [event()])
    old = store.schedule_path.read_bytes()
    with pytest.raises(capture.CaptureError):
        capture.import_schedule(store, [event(), event()])
    assert store.schedule_path.read_bytes() == old


def test_cannot_renumber_existing_identity(store):
    capture.import_schedule(store, [event(id="official")])
    with pytest.raises(capture.CaptureError, match="renumbered"):
        capture.import_schedule(store, [event(2, id="official")])


@pytest.mark.parametrize("raw", [event(courseCode="UNKNOWN"), event(0), event(-1), event(True),
                                  event(start="2026-10-12T10:00:00", end="2026-10-12T11:00:00"),
                                  event(end="2026-10-12T09:00:00Z")])
def test_invalid_event_rejected(store, raw):
    with pytest.raises(capture.CaptureError):
        capture.import_schedule(store, [raw])


def test_dst_offset_and_ambiguous_local_time(store):
    raw = event(start="2026-10-25T02:30:00", end="2026-10-25T03:30:00", timezone="Europe/Rome")
    with pytest.raises(capture.CaptureError, match="Ambiguous"):
        capture.import_schedule(store, [raw])
    capture.import_schedule(store, [event(start="2026-10-25T02:30:00+02:00", end="2026-10-25T03:30:00+01:00")])
    assert store.schedule()["events"][0]["startsAt"] == "2026-10-25T00:30:00Z"


def test_ics_single_event_move_is_stable():
    source = subscription()
    first = capture.calendar_events(feed(component()), source, [], {"MATH"})
    moved = capture.calendar_events(feed(component(start="20261013T100000Z", end="20261013T110000Z")), source, first, {"MATH"})
    assert len(moved) == 1 and moved[0]["id"] == first[0]["id"]
    assert moved[0]["lessonNumber"] == 1 and moved[0]["startsAt"] != first[0]["startsAt"]


def test_recurring_override_keeps_occurrence_identity():
    source = subscription()
    recurring = component(extra="RRULE:FREQ=DAILY;COUNT=3\r\n")
    first = capture.calendar_events(feed(recurring), source, [], {"MATH"})
    override = component(start="20261013T120000Z", end="20261013T130000Z", extra="RECURRENCE-ID:20261013T100000Z\r\n")
    second = capture.calendar_events(feed(recurring, override), source, first, {"MATH"})
    assert len(first) == len(second) == 3
    a = next(e for e in first if e["startsAt"] == "2026-10-13T10:00:00Z")
    b = next(e for e in second if e["startsAt"] == "2026-10-13T12:00:00Z")
    assert a["id"] == b["id"] and a["lessonNumber"] == b["lessonNumber"]


def test_cancellation_and_disappearance_are_distinct():
    source = subscription()
    old = capture.calendar_events(feed(component()), source, [], {"MATH"})
    missing = capture.calendar_events(feed(), source, old, {"MATH"})
    cancelled = capture.calendar_events(feed(component(extra="STATUS:CANCELLED\r\n")), source, old, {"MATH"})
    assert missing[0]["eligibility"] == "NEEDS_REVIEW"
    assert cancelled[0]["eligibility"] == "CANCELLED"


def test_exception_removes_only_one_occurrence():
    source = subscription()
    recurring = component(extra="RRULE:FREQ=DAILY;COUNT=3\r\n")
    old = capture.calendar_events(feed(recurring), source, [], {"MATH"})
    cancellation = component(start="20261013T100000Z", end="20261013T110000Z", extra="RECURRENCE-ID:20261013T100000Z\r\nSTATUS:CANCELLED\r\n")
    result = capture.calendar_events(feed(recurring, cancellation), source, old, {"MATH"})
    assert sum(e["eligibility"] == "CANCELLED" for e in result) == 1
    assert sum(e["eligibility"] == "SCHEDULED" for e in result) == 2


def test_ics_ambiguous_mapping_is_not_automatically_recorded():
    source = subscription(mappings=[{"match": "Math", "courseCode": "MATH", "firstLesson": 1},
                                    {"match": "Math", "courseCode": "MATH", "firstLesson": 1}])
    assert capture.calendar_events(feed(component()), source, [], {"MATH"})[0]["eligibility"] == "NEEDS_REVIEW"


def test_sync_conditional_unchanged_failure_and_due_check(store, monkeypatch):
    capture.subscribe(store, subscription())
    now = datetime(2026, 10, 11, tzinfo=UTC)
    calls = []
    def fetch(source, metadata):
        calls.append(metadata)
        return (feed(component()) if not metadata.get("etag") else None), {"etag": "version-one"}
    monkeypatch.setattr(capture, "download", fetch)
    assert capture.sync(store, now=now)["sources"][0]["status"] == "UPDATED"
    assert capture.sync(store, if_due=True, now=now + timedelta(hours=1))["sources"] == []
    assert capture.sync(store, now=now + timedelta(hours=6))["sources"][0]["status"] == "UNCHANGED"
    saved = store.schedule()["events"]
    def broken(*args):
        raise OSError("https://calendar.example.invalid/private-token")
    monkeypatch.setattr(capture, "download", broken)
    result = capture.sync(store, now=now + timedelta(hours=12))
    assert result["status"] == "SYNC_FAILED"
    assert "private-token" not in json.dumps(result)
    assert store.schedule()["events"] == saved


def test_invalid_feed_preserves_validators_and_last_good_schedule(store, monkeypatch):
    capture.subscribe(store, subscription())
    monkeypatch.setattr(capture, "download", lambda *args: (feed(component()), {"etag": "valid"}))
    capture.sync(store)
    saved = store.schedule()["events"]
    monkeypatch.setattr(capture, "download", lambda *args: (b"not a calendar", {"etag": "bad"}))
    assert capture.sync(store)["status"] == "SYNC_FAILED"
    assert store.schedule()["events"] == saved
    assert capture.read_json(store.directory / "sync.json")["sources"]["teaching"]["etag"] == "valid"


def test_request_pending_does_not_claim_execution(store):
    result = capture.request(store, "stop", wait_seconds=0)
    assert result["status"] == "PENDING"
    request = capture.read_json(store.directory / "requests" / (result["requestID"] + ".json"))
    assert request["expiresAt"] > request["createdAt"]
    assert capture.capture_status(store)["pendingRequests"] == [result["requestID"]]


def test_status_requires_live_process_and_fresh_heartbeat(store):
    atomic_json_write(store.runtime_path, {"pid": os.getpid(), "updatedAt": capture.iso(datetime.now(UTC)), "recording": True})
    assert capture.capture_status(store)["recording"]
    atomic_json_write(store.runtime_path, {"pid": os.getpid(), "updatedAt": "2020-01-01T00:00:00Z", "recording": True})
    assert not capture.capture_status(store)["recording"]


def test_status_process_probe_denied_is_unknown_instead_of_stopped(store, monkeypatch):
    atomic_json_write(store.runtime_path, {"pid": 123, "updatedAt": capture.iso(datetime.now(UTC)), "recording": True})
    def denied(*args):
        raise PermissionError("Process probes unavailable")
    monkeypatch.setattr(os, "kill", denied)
    result = capture.capture_status(store)
    assert result["running"] is None and result["recording"] is None
    assert result["processCheck"] == "UNAVAILABLE"


def test_cli_manual_request_and_persistent_pause(store, capsys):
    root = str(store.workspace.root)
    assert main(["capture", "pause", "--workspace", root]) == 0
    assert store.config()["paused"]
    assert main(["capture", "start", "--workspace", root, "--course", "MATH", "--lesson", "8", "--duration-minutes", "60", "--wait-seconds", "0"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert json.loads(lines[-1])["status"] == "PENDING"
    paths = list((store.directory / "requests").glob("*.json"))
    request = capture.read_json(paths[0])
    assert request["payload"]["event"]["lessonNumber"] == 8
    assert main(["capture", "resume", "--workspace", root]) == 0
    assert not store.config()["paused"]


def test_partial_audio_metadata_survives_discovery(store):
    directory = Path(store.config()["outputDirectory"])
    audio = directory / "MATH_L01.m4a"
    audio.write_bytes(b"fixture audio")
    os.utime(audio, (1, 1))
    atomic_json_write(audio.with_suffix(".m4a.capture.json"), {"partial": True, "reason": "Interrupted", "durationSeconds": 12})
    configuration = store.workspace.configuration_store().load()
    result = discover_recordings(store.workspace, configuration)
    assert result[0]["partial"] is True and "Interrupted" in result[0]["reason"]
    assert recording_for_lecture(result, "MATH-L01") == audio


@pytest.mark.parametrize("patch", [{"refreshHours": 0}, {"recordAfterSeconds": -1}, {"stageDirectory": "/tmp/elsewhere"}, {"enabled": True}])
def test_config_invalid_or_unsafe_fields_rejected(store, patch):
    with pytest.raises(capture.CaptureError):
        capture.configure(store, patch)


def test_installer_rollback_guard_reenters_script_from_any_directory(tmp_path):
    root = Path(__file__).resolve().parents[1]
    app_home = tmp_path / "application"
    version = app_home / "versions/0.2.0"
    executable = version / "venv/bin/mynotes"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    result = subprocess.run(["zsh", str(root / "scripts/install-mynotes.sh"), "--app-home", str(app_home),
                             "--prefix", str(tmp_path / "prefix"), "--python", sys.executable, "--rollback", "0.2.0"], cwd=tmp_path,
                            env=dict(os.environ, MYNOTES_CAPTURE_HOME=str(tmp_path / "runtime")), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (app_home / "current").resolve() == version
    assert "Rolled back" in result.stdout


def test_installer_lease_defers_update_without_running_child(tmp_path):
    home = tmp_path / "runtime"
    home.mkdir()
    helper = Path(__file__).resolve().parents[1] / "scripts/capture_install_guard.py"
    with (home / "update.lock").open("a+") as lease:
        fcntl.flock(lease.fileno(), fcntl.LOCK_EX)
        result = subprocess.run([sys.executable, str(helper), sys.executable, "-c", "raise SystemExit(42)"],
                                env=dict(os.environ, MYNOTES_CAPTURE_HOME=str(home)), capture_output=True, text=True)
    assert result.returncode == 2
    assert "recording or exporting" in result.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS release installer")
def test_failed_dependency_install_removes_partial_version_and_preserves_current(tmp_path):
    root = Path(__file__).resolve().parents[1]
    release = tmp_path / "release"
    scripts = release / "scripts"
    scripts.mkdir(parents=True)
    for name in ("install-mynotes.sh", "capture_install_guard.py"):
        shutil.copy2(root / "scripts" / name, scripts / name)
    (release / "dist").mkdir()
    (release / "dist/mynotes-0.2.0-py3-none-any.whl").write_bytes(b"fixture")
    (release / "pyproject.toml").write_text('[project]\nversion="0.2.0"\n')
    (release / "uv.lock").write_text("fixture")
    relative = "capture/output/MyNotes Capture.app/Contents/MacOS/MyNotesCapture"
    executable = release / relative
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture")
    (release / "release-manifest.json").write_text(json.dumps({"files": {relative: hashlib.sha256(executable.read_bytes()).hexdigest()}}))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text("#!/bin/sh\nexit 23\n")
    uv.chmod(0o755)
    app_home = tmp_path / "application"
    previous = app_home / "versions/previous"
    previous.mkdir(parents=True)
    (app_home / "current").symlink_to(previous)
    result = subprocess.run(["zsh", str(scripts / "install-mynotes.sh"), "--app-home", str(app_home),
                             "--prefix", str(tmp_path / "prefix"), "--python", sys.executable], cwd=tmp_path,
                            env=dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
                                     MYNOTES_CAPTURE_HOME=str(tmp_path / "runtime"), UV_CACHE_DIR=str(tmp_path / "cache")),
                            capture_output=True, text=True)
    assert result.returncode == 23, result.stderr
    assert not (app_home / "versions/0.2.0").exists()
    assert (app_home / "current").resolve() == previous
