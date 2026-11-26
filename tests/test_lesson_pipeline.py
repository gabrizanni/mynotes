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
from test_capture_queue import audio as capture_audio, ws  # noqa: F401 (ws is a fixture)
from test_publication import fake_render
from test_transcription_reliability import Fake


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
    def prepare_audio(workspace, lecture_id, audio=None, **kwargs):
        transcribe(workspace, lecture_id, audio)
        return {"lecture_id": lecture_id, "availability": {"transcript": True},
                "transcription": {"status": "COMPLETED", "segments": [{}, {}, {}]}}
    monkeypatch.setattr("mynotes.cli._notes_preparation", prepare)
    monkeypatch.setattr("mynotes.transcription.run_transcription", transcribe)
    monkeypatch.setattr("mynotes.sourcepack.build_source_pack", prepare_audio)
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


def test_explicit_audio_listed_by_the_library_is_bound_to_the_lesson(ws, monkeypatch):
    # The real case: a student links a course folder, its Recordings folder lists
    # the file as an unassigned library recording, then asks for one lesson.
    from mynotes import asr
    from mynotes.capture_courses import add_course
    path, _ = capture_audio(ws, "Faraday candle part 1")
    engine = Fake()
    monkeypatch.setattr(asr, "default_engines", lambda *args: {"argmax": engine, "whispercpp": engine})
    code = add_course(ws, "Chemistry")["course"]["code"]
    notes = []
    def run_notes(workspace, course, numbers, **kwargs):
        notes.append((course, numbers))
        return {"status": "COMPLETED", "provider_invoked": True}
    monkeypatch.setattr("mynotes.notes_run.run_notes", run_notes)
    status, result = process_lesson(ws, f"{code}-L01", audio=path)
    assert status == 0, result
    assert notes == [(code, [1])] and engine.calls == 1
    pack = json.loads((ws.runs_dir / f"{code}-L01" / "source_pack.json").read_text())
    assert pack["availability"]["transcript"] is True
    assert [Path(s["path"]) for s in pack["sources"] if s.get("role") == "recording"] == [path]


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


def _pack_sources(workspace, lecture):
    pack = json.loads((workspace.runs_dir / lecture / "source_pack.json").read_text())
    return [{key: source[key] for key in ("path", "role", "sha256")} for source in pack["sources"]], pack["availability"]


def test_a_transcript_is_imported_like_prepare_and_never_transcribed(ops, monkeypatch, tmp_path, capsys):
    text = tmp_path / "lesson one.txt"
    text.write_text("Today: the supply chain, from the order to the delivery.\n", encoding="utf-8")
    assert main(["prepare", "OPS-L01", "--transcript", str(text), "--workspace", str(ops.root)]) == 0
    capsys.readouterr()
    prepared = _pack_sources(ops, "OPS-L01")
    (ops.runs_dir / "OPS-L01" / "source_pack.json").unlink()
    monkeypatch.setattr("mynotes.transcription.run_transcription", lambda *a, **k: pytest.fail("a transcript needs no ASR"))
    notes = []
    monkeypatch.setattr("mynotes.notes_run.run_notes", lambda workspace, course, numbers, **kwargs: notes.append(
        (course, numbers)) or {"status": "AWAITING_APPROVAL", "provider_invoked": True})
    events = []
    code, result = process_lesson(ops, "ops-l01", transcript=text, progress=record(events))
    assert code == 0 and result["status"] == "AWAITING_APPROVAL" and notes == [("OPS", [1])]
    assert result["transcription"] == {"status": "IMPORTED", "lecture_id": "OPS-L01", "path": str(text),
                                       "source_pack": str(ops.runs_dir / "OPS-L01" / "source_pack.json")}
    assert events[:2] == [("CHECKING", {"lecture_id": "OPS-L01"}),
                          ("PREPARING", {"lecture_id": "OPS-L01", "transcript": "imported"})]
    assert _pack_sources(ops, "OPS-L01") == prepared  # The same source pack as `prepare --transcript`.
    assert any(source["role"] == "imported_transcript" and source["path"] == str(text) for source in prepared[0])


def test_an_unusable_transcript_stops_before_notes(ops, monkeypatch, tmp_path, capsys):
    from mynotes.sourcepack import build_source_pack
    calls = fake_steps(monkeypatch, pending_before=[])
    monkeypatch.setattr("mynotes.sourcepack.build_source_pack", build_source_pack)  # The real import rejects the file.
    empty = tmp_path / "empty.txt"
    empty.write_text("   \n", encoding="utf-8")
    assert main(["lesson", "process", "OPS-L01", "--transcript", str(empty), "--workspace", str(ops.root), "--json"]) == 2
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["status"] == "FAILED" and result["stage"] == "PREPARING" and not result["provider_invoked"]
    assert result["transcription"]["message"] == "Transcript text is empty"
    assert [json.loads(line)["stage"] for line in captured.err.splitlines()] == ["CHECKING", "PREPARING"]
    assert calls["asr"] == [] and calls["notes"] == []


def test_audio_and_transcript_exclude_each_other(ops, tmp_path, capsys):
    with pytest.raises(SystemExit) as stopped:
        main(["lesson", "process", "OPS-L01", "--audio", "a.wav", "--transcript", "a.txt", "--workspace", str(ops.root)])
    assert stopped.value.code == 2 and "not allowed with argument" in capsys.readouterr().err
    from mynotes.workflow import WorkflowError
    with pytest.raises(WorkflowError, match="not both"):
        process_lesson(ops, "OPS-L01", audio=tmp_path / "a.wav", transcript=tmp_path / "a.txt")


def test_lesson_process_rpc_passes_the_transcript(tmp_path):
    from mynotes.serve_methods import BY_NAME, RpcError, build_argv, validate
    method = BY_NAME["lesson.process"]
    params = validate(method, {"lectureId": "OPS-L01", "transcript": str(tmp_path / "t.txt")})
    assert build_argv(method, params, str(tmp_path))[0] == [
        "lesson", "process", "OPS-L01", "--transcript", str(tmp_path / "t.txt"), "--workspace", str(tmp_path), "--json"]
    with pytest.raises(RpcError) as refused:
        validate(method, {"lectureId": "OPS-L01", "audio": "a.wav", "transcript": "a.txt"})
    assert refused.value.code == -32602 and refused.value.message == "Give at most one of: audio, transcript, resume"
    for both in ({"resume": "OPS-0123456789ab", "regenerate": True}, {"resume": "OPS-0123456789ab", "transcript": "a.txt"}):
        with pytest.raises(RpcError):
            validate(method, {"lectureId": "OPS-L01", **both})
    params = validate(method, {"lectureId": "OPS-L01", "regenerate": True})
    assert build_argv(method, params, str(tmp_path))[0][3:5] == ["--regenerate", "--workspace"]


def test_after_a_failed_paid_attempt_regenerate_is_the_explicit_way_out(ops, monkeypatch):
    from mynotes.providers import ProviderError
    from mynotes.workflow import WorkflowError
    monkeypatch.setattr(pub, "_render_and_validate", fake_render)
    monkeypatch.setattr(bd, "_render_and_validate", fake_render)
    monkeypatch.setattr("mynotes.transcription.run_transcription", lambda *a, **k: pytest.fail("prepared lesson needs no ASR"))

    class Failed:
        calls = 0

        def generate(self, request):
            self.calls += 1
            raise ProviderError("Interrupted stream")

    failed = Failed()
    code, first = process_lesson(ops, "OPS-L01", provider_adapter=failed)
    assert code == 2 and first["status"] == "FAILED" and failed.calls == 1
    adapter = Recorded()
    # An ordinary rerun (and a resume of that batch) never repeats a paid attempt that already started.
    for retry in ({}, {"resume": first["notes"]["batch_id"]}):
        code, refused = process_lesson(ops, "OPS-L01", provider_adapter=adapter, **retry)
        assert code == 2 and refused["status"] == "FAILED" and adapter.calls == []
        assert "no automatic whole-lesson restart" in refused["notes"]["problem"]["message"]
    with pytest.raises(WorkflowError, match="--resume continues a saved notes job"):
        process_lesson(ops, "OPS-L01", resume="OPS-0123456789ab", regenerate=True)
    events = []
    code, result = process_lesson(ops, "OPS-L01", regenerate=True, provider_adapter=adapter, progress=record(events))
    assert code == 0, result
    assert result["status"] in {"COMPLETED", "COMPLETED_WITH_WARNINGS"} and len(adapter.calls) == 1
    assert Path(result["notes"]["publication"]["output"]).is_file()


def test_resume_and_regenerate_reach_the_notes_step(ops, monkeypatch, capsys):
    calls = fake_steps(monkeypatch, pending_before=[])
    assert main(["lesson", "process", "OPS-L01", "--resume", "OPS-0123456789ab", "--workspace", str(ops.root), "--json"]) == 0
    assert main(["lesson", "process", "OPS-L01", "--regenerate", "--workspace", str(ops.root), "--json"]) == 0
    assert [(kwargs["resume"], kwargs["regenerate"]) for _, _, kwargs in calls["notes"]] == [("OPS-0123456789ab", False), (None, True)]
    with pytest.raises(SystemExit):
        main(["lesson", "process", "OPS-L01", "--resume", "B", "--regenerate", "--workspace", str(ops.root)])
    assert "not allowed with argument" in capsys.readouterr().err
