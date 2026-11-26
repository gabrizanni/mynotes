"""`library.transcript` and `notes.publish` through a real `mynotes serve --stdio`.

The transcript path always comes from MyNotes' records: a caller can name a
lesson or a recording, never a file. Reads are capped and never cut a UTF-8
character.
"""
import json

import pytest

from mynotes import serve_transcript
from mynotes.serve_methods import BY_NAME, build_argv
from serve_helpers import COURSE, Client, build_workspace, cli, environment

TEXT = "[00:00:01–00:00:05] PROFESSOR: Buongiorno, oggi parliamo di perché.\n"


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    monkeypatch = pytest.MonkeyPatch()
    tmp_path = tmp_path_factory.mktemp("transcript")
    workspace = build_workspace(tmp_path, monkeypatch)
    env = environment(tmp_path)
    managed = tmp_path / "managed"
    assert cli(env, "transcription-queue", "folder", "--path", str(managed), "--workspace", str(workspace.root)).returncode == 0
    texts = managed / "Trascrizioni"
    texts.mkdir(parents=True)
    (texts / f"{COURSE}-L01.txt").write_text(TEXT, encoding="utf-8")
    (texts / f"{COURSE}-L03.txt").write_bytes("è".encode() * (1024 * 1024 + 7))  # 2 MiB + 14 bytes
    imported = tmp_path / "elsewhere" / "lesson two.txt"
    imported.parent.mkdir()
    imported.write_text("Imported lesson two.\n", encoding="utf-8")
    prepared = cli(env, "prepare", f"{COURSE}-L02", "--transcript", str(imported), "--workspace", str(workspace.root))
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    secret = tmp_path / "secret.txt"
    secret.write_text("not a transcript", encoding="utf-8")
    client = Client(workspace.root, env)
    yield client, workspace, tmp_path
    assert client.close() == 0
    monkeypatch.undo()


def test_a_lesson_transcript_is_read_from_the_library(served):
    client, _, tmp_path = served
    data = client.call("library.transcript", {"lectureId": f"{COURSE.lower()}-l01"})["data"]
    assert data == {"path": str(tmp_path / "managed/Trascrizioni" / f"{COURSE}-L01.txt"), "text": TEXT,
                    "truncated": False, "encoding": "utf-8", "size": len(TEXT.encode())}


def test_a_transcript_imported_into_the_source_pack_is_found(served):
    client, _, tmp_path = served
    data = client.call("library.transcript", {"lectureId": f"{COURSE}-L02"})["data"]
    assert data["path"] == str(tmp_path / "elsewhere" / "lesson two.txt")
    assert data["text"] == "Imported lesson two.\n" and data["truncated"] is False


def test_reads_are_capped_at_2_mib_by_default_without_cutting_a_character(served):
    client, _, _ = served
    data = client.call("library.transcript", {"lectureId": f"{COURSE}-L03"})["data"]
    assert data["truncated"] is True and data["size"] == 2 * 1024 * 1024 + 14
    assert len(data["text"].encode()) == 2 * 1024 * 1024 and set(data["text"]) == {"è"}
    small = client.call("library.transcript", {"lectureId": f"{COURSE}-L03", "maxBytes": 5})["data"]
    assert small["text"] == "èè" and small["truncated"] is True  # 5 bytes hold two whole characters
    whole = client.call("library.transcript", {"lectureId": f"{COURSE}-L01", "maxBytes": len(TEXT.encode())})["data"]
    assert whole["text"] == TEXT and whole["truncated"] is False


@pytest.mark.parametrize("params,code", [
    ({"lectureId": "../../secret"}, -32602),
    ({"lectureId": f"{COURSE}-L01/../../../secret"}, -32602),
    ({"lectureId": f"../{COURSE}-L01"}, -32602),
    ({"recordingId": "../../secret.txt"}, -32009),
    ({"recordingId": "/etc/passwd"}, -32009),
    ({"lectureId": f"{COURSE}-L01", "path": "/etc/passwd"}, -32602),
    ({"lectureId": f"{COURSE}-L01", "recordingId": "x"}, -32602),
    ({}, -32602),
    ({"lectureId": f"{COURSE}-L01", "maxBytes": 0}, -32602),
    ({"lectureId": f"{COURSE}-L01", "maxBytes": 8 * 1024 * 1024 + 1}, -32602),
    ({"lectureId": f"{COURSE}-L01", "maxBytes": "10"}, -32602),
    ({"recordingId": "r" * 201}, -32602),
])
def test_callers_name_lessons_and_recordings_never_files(served, params, code):
    client, _, _ = served
    error = client.request("library.transcript", params)["error"]
    assert error["code"] == code
    assert "not a transcript" not in json.dumps(error)


def test_a_lesson_without_transcript_is_not_found(served):
    client, _, _ = served
    error = client.request("library.transcript", {"lectureId": f"{COURSE}-L09"})["error"]
    assert error == {"code": -32009, "message": "No transcript yet", "data": {"lectureId": f"{COURSE}-L09"}}


def test_library_transcript_is_a_fast_server_method_without_command():
    method = BY_NAME["library.transcript"]
    assert method.lane == "fast" and method.command == () and method.direct is not None
    assert all(param.server for param in method.params)


def test_recording_candidates_come_from_library_rows(tmp_path):
    exported, legacy = tmp_path / "exported.txt", tmp_path / "asr" / "transcript.json"
    view = {"recordings": [{"id": "rec-1", "export_text": str(exported), "transcript_json": str(legacy)}],
            "transcripts": [{"id": "t-1", "recording_id": "rec-2", "path": str(tmp_path / "linked.txt")}], "lessons": []}
    assert serve_transcript.recording_candidates(view, "rec-1") == [str(exported), str(legacy)]
    assert serve_transcript.recording_candidates(view, "rec-2") == [str(tmp_path / "linked.txt")]
    assert serve_transcript.recording_candidates(view, str(exported)) is None
    legacy.parent.mkdir()
    legacy.with_suffix(".txt").write_text("from the speech engine", encoding="utf-8")
    assert serve_transcript._readable(str(legacy)) == legacy.with_suffix(".txt")  # JSON is read through its TXT


def test_decoding_reports_the_encoding():
    assert serve_transcript.decode("﻿ciao".encode(), truncated=False) == ("ciao", "utf-8-sig")
    assert serve_transcript.decode("perch\xe9".encode("latin-1"), truncated=False) == ("perché", "latin-1")
    assert serve_transcript.decode("aè".encode()[:2], truncated=True) == ("a", "utf-8")


def test_notes_publish_runs_the_publish_command_as_a_job(served):
    client, workspace, tmp_path = served
    method = BY_NAME["notes.publish"]
    assert method.lane == "job"
    candidate = str(tmp_path / "candidate.docx")
    assert build_argv(method, {"lecture": f"{COURSE}-L01", "candidate": candidate}, str(workspace.root))[0] == [
        "publish", f"{COURSE}-L01", f"--candidate={candidate}", "--workspace", str(workspace.root)]
    started = client.call("notes.publish", {"lecture": f"{COURSE}-L01"})
    assert started["job"]["method"] == "notes.publish" and started["job"]["state"] == "RUNNING"
    finished = client.notification("job.finished", timeout=60, where=lambda params: params["jobId"] == started["jobId"])
    # The real command, unchanged: a lesson without a validated Word candidate is a plain failure.
    assert finished["state"] == "FAILED" and finished["exitCode"] == 2
    assert finished["error"]["code"] == -32001
    assert finished["error"]["message"] == "No validated Word candidate exists; run mynotes build first"
    assert client.request("notes.publish", {})["error"]["code"] == -32602


def test_lesson_process_imports_a_transcript_through_the_job_lane(served):
    client, _, tmp_path = served
    text = tmp_path / "elsewhere" / "lesson four.txt"
    text.write_text("Lesson four, read from a text file.\n", encoding="utf-8")
    started = client.call("lesson.process", {"lectureId": f"{COURSE}-L04", "transcript": str(text)})
    stages = []
    progress = client.notification("job.progress", timeout=60, where=lambda params: params["jobId"] == started["jobId"]
                                   and stages.append(params["stage"]) is None and params["stage"] == "PREPARING")
    assert progress["transcript"] == "imported" and stages[0] == "CHECKING"
    finished = client.notification("job.finished", timeout=60, where=lambda params: params["jobId"] == started["jobId"])
    # No agent is configured in the test workspace: the notes step stops before any provider call.
    assert finished["state"] == "FAILED" and finished["error"]["code"] == -32001
    assert finished["error"]["message"].startswith("No provider is selected")
    assert client.call("library.transcript", {"lectureId": f"{COURSE}-L04"})["data"]["path"] == str(text)
    refused = client.request("lesson.process", {"lectureId": f"{COURSE}-L04", "transcript": str(text), "audio": "a.wav"})
    assert refused["error"]["code"] == -32602
