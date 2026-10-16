import json
from pathlib import Path

import pytest

from mynotes.asr import ASRBusyError, ASRError, ASRReviewError
from mynotes.cli import main


def test_status_does_not_initialize_or_transcribe(tmp_path, monkeypatch, capsys):
    root = tmp_path / "absent"
    monkeypatch.setattr("mynotes.transcription.run_transcription", lambda *a, **k: pytest.fail("status must not start ASR"))
    assert main(["transcription-status", "OPS-L02", "--workspace", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_STARTED"
    assert not root.exists()


@pytest.mark.parametrize("error,code,status", [(ASRBusyError("busy", {"phase":"processing"}),3,"BUSY"),
                                             (ASRReviewError("review", [], Path("raw.json")),4,"NEEDS_REVIEW"),
                                             (ASRError("engine failed"),2,"FAILED")])
def test_transcription_errors_remain_machine_readable(tmp_path, monkeypatch, capsys, error, code, status):
    def fail(*args, **kwargs): raise error
    monkeypatch.setattr("mynotes.transcription.run_transcription", fail)
    assert main(["transcribe", "OPS-L02", "--workspace", str(tmp_path), "--json"]) == code
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status
    if status == "BUSY": assert result["current"]["phase"] == "processing"


def test_direct_transcription_has_no_general_workflow_checks(tmp_path, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr("mynotes.cli.status", lambda *a: pytest.fail("no general status"))
    monkeypatch.setattr("mynotes.onboarding.doctor", lambda *a, **k: pytest.fail("no general doctor"))
    monkeypatch.setattr("mynotes.transcription.run_transcription", lambda *a, **k: seen.append((a,k)) or {"status":"COMPLETED"})
    assert main(["transcribe", "ops-l02", "--workspace", str(tmp_path), "--audio", str(tmp_path/'selected.wav'), "--json"]) == 0
    assert seen[0][0][1] == "OPS-L02" and seen[0][1]["audio"] == tmp_path/'selected.wav'
    assert seen[0][1]["progress"] is True


def test_study_plan_requires_no_output_or_generation(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("mynotes.study.plan_study", lambda *a, **k: {"status":"STUDY_PLAN"})
    monkeypatch.setattr("mynotes.study.generate_study_candidate", lambda *a, **k: pytest.fail("no generation while planning"))
    assert main(["study", "OPS", "--lessons", "6-10", "--workspace", str(tmp_path), "--plan-only"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "STUDY_PLAN"


def test_mismatched_scope_never_generates(tmp_path, monkeypatch, capsys):
    manifest = tmp_path/'manifest.json'
    manifest.write_text(json.dumps({"lessons":[1,2],"purpose":"quiz"}))
    monkeypatch.setattr("mynotes.study.generate_study_candidate", lambda *a, **k: pytest.fail("wrong scope must stop"))
    assert main(["study", "OPS", "--lessons", "6-10", "--manifest", str(manifest), "--output", str(tmp_path/'new.docx'), "--workspace", str(tmp_path)]) == 2
    assert "differ" in capsys.readouterr().out


def test_study_schema_is_available_without_workspace(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MYNOTES_WORKSPACE",str(tmp_path/'absent'))
    assert main(["study-schema", "--json"]) == 0
    assert "sections" in json.loads(capsys.readouterr().out)["properties"]
    assert not (tmp_path/'absent').exists()


def test_prepare_busy_is_structured_without_retry(tmp_path, monkeypatch, capsys):
    transcript = tmp_path/'transcript.json'
    transcript.write_text('{"segments":[]}')
    def busy(*a, **k): raise ASRBusyError("slot already owned", {"phase":"processing"})
    monkeypatch.setattr("mynotes.cli.build_source_pack",busy)
    assert main(["prepare","OPS-L01","--workspace",str(tmp_path/'private'),"--transcript",str(transcript)]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "BUSY"
