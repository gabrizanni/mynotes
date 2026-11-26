"""`lesson process`: stage order, reuse of existing transcripts and exit codes.

Transcription and the notes step are replaced by fakes or by the repository's
synthetic provider adapter and renderer; no agent or speech engine runs.
"""
import json
import time
from pathlib import Path

import pytest

from mynotes import batch_documents as bd, publication as pub
from mynotes.asr import ASRBusyError, ASRError, ASRReviewError
from mynotes.cli import main
from mynotes.lesson_pipeline import process_lesson
from mynotes.utils import atomic_json_write
from test_batch_generation import Adapter, workspace_and_deck
from test_publication import fake_render


@pytest.fixture
def ops(tmp_path):
    workspace, _, _ = workspace_and_deck(tmp_path, lessons=1, slides=2)
    return workspace


def record(events):
    return lambda stage, detail: events.append((stage, detail))


def fake_steps(monkeypatch, *, pending_before, pending_after=(), transcription=None, notes_status="COMPLETED"):
    calls = {"prepare": 0, "asr": [], "notes": []}
    def prepare(workspace, lectures, contiguous=False):
        calls["prepare"] += 1
        return list(pending_before if calls["prepare"] == 1 else pending_after)
    def transcribe(workspace, lecture_id=None, audio=None, progress=False, **kwargs):
        calls["asr"].append((lecture_id, audio, progress))
        if isinstance(transcription, BaseException):
            raise transcription
        return transcription or {"lecture_id": lecture_id, "status": "COMPLETED", "segments": 3}
    def notes(workspace, course, numbers, **kwargs):
        calls["notes"].append((course, numbers, kwargs))
        kwargs["progress"]("GENERATING", {})
        kwargs["progress"](notes_status, {})
        return {"status": notes_status, "batch_id": f"{course}-0123456789ab", "provider_invoked": True}
    monkeypatch.setattr("mynotes.cli._notes_preparation", prepare)
    monkeypatch.setattr("mynotes.transcription.run_transcription", transcribe)
    monkeypatch.setattr("mynotes.notes_run.run_notes", notes)
    return calls


MISSING = [{"lecture_id": "OPS-L01", "reason": "MISSING_TRANSCRIPT", "provider_invoked": False, "asr_started": False}]


def test_existing_transcript_is_reused_without_transcription(ops, monkeypatch):
    calls = fake_steps(monkeypatch, pending_before=[])
    events = []
    code, result = process_lesson(ops, "ops-l01", quality="base", progress=record(events))
    assert code == 0 and result["status"] == "COMPLETED" and result["transcription"] == {"status": "REUSED"}
    assert [stage for stage, _ in events] == ["CHECKING", "PREPARING", "GENERATING", "COMPLETED"]
    assert calls["asr"] == [] and calls["notes"][0][:2] == ("OPS", [1]) and calls["notes"][0][2]["quality"] == "base"


def test_missing_transcript_is_transcribed_then_prepared(ops, monkeypatch):
    calls = fake_steps(monkeypatch, pending_before=MISSING, pending_after=[])
    events = []
    code, result = process_lesson(ops, "OPS-L01", progress=record(events))
    assert code == 0 and result["transcription"]["status"] == "COMPLETED"
    stages = [stage for stage, _ in events]
    assert stages[:2] == ["CHECKING", "TRANSCRIBING"] and stages[-3:] == ["PREPARING", "GENERATING", "COMPLETED"]
    assert calls["asr"] == [("OPS-L01", None, False)] and calls["prepare"] == 2


def test_explicit_audio_always_transcribes_first(ops, monkeypatch, tmp_path):
    calls = fake_steps(monkeypatch, pending_before=[], pending_after=[])
    audio = tmp_path / "lesson.wav"
    assert process_lesson(ops, "OPS-L01", audio=audio)[0] == 0
    assert calls["asr"] == [("OPS-L01", audio, False)] and calls["prepare"] == 1


@pytest.mark.parametrize("error,code,status", [(ASRBusyError("busy", {"phase": "primary_pass"}), 3, "BUSY"),
                                               (ASRReviewError("review", [], Path("raw.json")), 4, "NEEDS_REVIEW"),
                                               (ASRError("engine failed"), 2, "FAILED")])
def test_transcription_outcomes_keep_transcribe_exit_codes(ops, monkeypatch, capsys, error, code, status):
    calls = fake_steps(monkeypatch, pending_before=MISSING, transcription=error)
    assert main(["lesson", "process", "OPS-L01", "--workspace", str(ops.root), "--json"]) == code
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["status"] == status and result["stage"] == "TRANSCRIBING" and not result["provider_invoked"]
    assert calls["notes"] == []
    stages = [json.loads(line)["stage"] for line in captured.err.splitlines()]
    assert stages == ["CHECKING", "TRANSCRIBING"]


def test_unfinished_notes_and_missing_sources_exit_2(ops, monkeypatch):
    fake_steps(monkeypatch, pending_before=[], notes_status="STOPPED")
    assert process_lesson(ops, "OPS-L01")[0] == 2
    calls = fake_steps(monkeypatch, pending_before=MISSING, pending_after=MISSING)
    code, result = process_lesson(ops, "OPS-L01")
    assert code == 2 and result["status"] == "NEEDS_TRANSCRIPT" and result["asr_started"] is True
    assert calls["notes"] == []
    ambiguous = [{"lecture_id": "OPS-L01", "reason": "AMBIGUOUS_TRANSCRIPT", "paths": ["a.txt", "b.txt"]}]
    calls = fake_steps(monkeypatch, pending_before=ambiguous)
    code, result = process_lesson(ops, "OPS-L01")
    assert code == 2 and result["status"] == "NEEDS_TRANSCRIPT" and calls["asr"] == []


def test_unknown_course_or_lesson_is_a_plain_failure(ops, capsys):
    assert main(["lesson", "process", "XYZ-L01", "--workspace", str(ops.root)]) == 2
    assert capsys.readouterr().out == "mynotes: Course not found: XYZ\n"
    assert main(["lesson", "process", "OPS-1", "--workspace", str(ops.root)]) == 2
    assert capsys.readouterr().out.startswith("mynotes: Identificativo lezione non valido")


def test_transcription_phase_is_reported_while_it_runs(ops, monkeypatch):
    def transcribe(workspace, lecture_id=None, audio=None, progress=False, **kwargs):
        status = workspace.runs_dir / lecture_id / "asr" / "status.json"
        import os
        atomic_json_write(status, {"lecture_id": lecture_id, "status": "RUNNING", "phase": "primary_pass",
                                   "elapsed_seconds": 4.0, "pid": os.getpid()})
        time.sleep(1.6)
        return {"lecture_id": lecture_id, "status": "COMPLETED"}
    fake_steps(monkeypatch, pending_before=MISSING, pending_after=[])
    monkeypatch.setattr("mynotes.transcription.run_transcription", transcribe)
    events = []
    assert process_lesson(ops, "OPS-L01", progress=record(events))[0] == 0
    assert ("TRANSCRIBING", {"lecture_id": "OPS-L01", "phase": "primary_pass", "elapsed_seconds": 4.0}) in events


class Recorded(Adapter):
    """The synthetic adapter, saving an attempt receipt like a real provider call (as in test_notes_run)."""

    def generate(self, request):
        import subprocess
        from mynotes.providers import AttemptStart, ProviderResponse, _save_attempt
        result = super().generate(request)
        attempt = _save_attempt(request, command=["synthetic"], started=AttemptStart(),
                                completed=subprocess.CompletedProcess(["synthetic"], 0, json.dumps(result.notes), ""))
        return ProviderResponse(result.notes, result.provider, result.model, result.effort, attempt_path=str(attempt))


def test_whole_lesson_with_synthetic_provider_and_renderer(ops, monkeypatch):
    monkeypatch.setattr(pub, "_render_and_validate", fake_render)
    monkeypatch.setattr(bd, "_render_and_validate", fake_render)
    monkeypatch.setattr("mynotes.transcription.run_transcription", lambda *a, **k: pytest.fail("prepared lesson needs no ASR"))
    adapter = Recorded()
    events = []
    code, result = process_lesson(ops, "OPS-L01", provider_adapter=adapter, progress=record(events))
    assert code == 0, result
    assert result["status"] in {"COMPLETED", "COMPLETED_WITH_WARNINGS"} and len(adapter.calls) == 1
    stages = [stage for stage, _ in events]
    assert stages[:2] == ["CHECKING", "PREPARING"]
    assert stages.index("GENERATING") < stages.index("BUILDING_WORD") < stages.index("UPDATING_WORD")
    assert Path(result["notes"]["publication"]["output"]).is_file()
