from __future__ import annotations

import subprocess
import uuid
import wave
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from mynotes import capture, capture_windows
from mynotes.utils import atomic_json_write
from mynotes.workspace import Workspace


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MYNOTES_CAPTURE_HOME", str(tmp_path / "private Capture"))
    workspace = Workspace.from_value(tmp_path / "corsi & lezioni")
    workspace.initialize()
    result = capture.CaptureStore(workspace)
    capture.configure(result, {})
    return result


def test_task_identity_and_paths_are_scoped_to_current_user(tmp_path):
    sid = "S-1-5-21-100-101-102-1001"
    binary = tmp_path / "MyNotes program" / "MyNotes.Capture.exe"
    workspace = tmp_path / "corsi & lezioni"
    home = tmp_path / "private Capture"
    xml = ET.fromstring(capture_windows.task_xml(sid, binary, workspace, home))
    ns = {"t": capture_windows.TASK_NAMESPACE}
    assert capture_windows.task_name(sid) == f"MyNotes Capture {sid}"
    assert xml.findtext("t:Principals/t:Principal/t:UserId", namespaces=ns) == sid
    assert xml.findtext("t:Principals/t:Principal/t:LogonType", namespaces=ns) == "InteractiveToken"
    assert xml.findtext("t:Principals/t:Principal/t:RunLevel", namespaces=ns) == "LeastPrivilege"
    assert xml.findtext("t:Actions/t:Exec/t:Command", namespaces=ns) == str(binary)
    assert xml.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns) == (
        f'--workspace "{workspace}" --capture-home "{home}"'
    )


def test_failed_task_start_rolls_back_config(store, monkeypatch, tmp_path):
    binary = tmp_path / "MyNotes.Capture.exe"
    binary.touch()
    monkeypatch.setenv("MYNOTES_CAPTURE_EXE", str(binary))
    monkeypatch.setattr(capture_windows, "_user_sid", lambda: "S-1-5-21-100-101-102-1001")
    commands = []

    def fake_task(action, name, *arguments):
        commands.append(action)
        return subprocess.CompletedProcess([action, name, *arguments], int(action == "/Run"), "", "unavailable")

    monkeypatch.setattr(capture_windows, "_task", fake_task)
    with pytest.raises(capture.CaptureError, match="could not start"):
        capture_windows.set_enabled(store, True)
    assert commands == ["/Create", "/Run", "/Delete"]
    assert store.config()["enabled"] is False
    assert not (capture.runtime_home() / "active.json").exists()


def test_export_converts_multiple_wavs_and_preserves_final_output(store):
    session_id = str(uuid.uuid4())
    stage = store.directory / session_id
    stage.mkdir()
    destination = stage / "microphone-test.m4a"
    temporary = stage / (f".capture-{session_id}-" + "1" * 32 + ".tmp")
    destination.write_bytes(b"original")
    for index, rate in enumerate((16_000, 48_000)):
        filename = stage / f"{index:019d}-{uuid.uuid4().hex}.wav"
        with wave.open(str(filename), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            audio.writeframes(b"\x00\x08" * (rate // 2))
    atomic_json_write(store.directory / "sessions" / f"{session_id}.json", {
        "id": session_id, "status": "EXPORTING", "directory": str(stage),
        "outputPath": str(destination), "exportTemporaryPath": str(temporary),
    })

    assert capture_windows.export(store, session_id) == pytest.approx(1.0, rel=0.01)
    assert capture_windows.valid_audio(temporary)
    assert destination.read_bytes() == b"original"
    with pytest.raises(FileExistsError):
        capture_windows.export(store, session_id)
    assert capture_windows.valid_audio(temporary)
