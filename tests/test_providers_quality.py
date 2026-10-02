from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mynotes.configuration import course_template
from mynotes.cli import main
from mynotes.providers import AgyAdapter, CodexAdapter, ProviderError, ProviderQuotaError, ProviderRequest, ProviderResponse, RequestedModel, _agy_schema, _quota_error, notes_schema
from mynotes.utils import atomic_json_write
from mynotes.workflow import WorkflowError, generate_candidate, status
from mynotes.workspace import Workspace


def configured_workspace(tmp_path: Path) -> Workspace:
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()
    configuration = workspace.configuration_store().load()
    course = course_template()
    course.update({"code": "OPS7", "name": "Operations Analytics", "kind": "mixed"})
    configuration["courses"] = [course]
    configuration["providers"]["default"] = "codex"
    configuration["providers"]["configured"] = {
        "codex": {"model": "gpt-test", "effort": "medium"},
        "agy": {"model": "gemini-test", "effort": "high"},
    }
    workspace.configuration_store().save(configuration)
    return workspace


def source_pack(workspace: Workspace, *, visual: bool = False) -> Path:
    run = workspace.runs_dir / "OPS7-L01"
    pack = {
        "schema_version": 1, "lecture_id": "OPS7-L01",
        "course": {"code": "OPS7", "name": "Operations Analytics", "lesson_number": 1},
        "availability": {"audio": False, "transcript": False, "materials": True, "personal_notes": True, "visual_candidates": visual, "missing_audio": True},
        "evidence": [
            {"id": "S001", "kind": "slide", "content": "Inventory has a cost trade-off.", "source_file": "slides.pdf", "page": 1},
            {"id": "H001", "kind": "personal_note", "content": "Explain the service-level trade-off.", "source_file": "notes.docx", "paragraph": 1},
        ],
        "visuals": ([{"evidence_id": "S001", "page": 1, "visual_decision": "candidate", "reason": "chart", "alignment": {"status": "unavailable"}}] if visual else []),
        "directives": [], "sources": [], "glossary": [],
    }
    path = run / "source_pack.json"
    atomic_json_write(path, pack)
    return path


def valid_notes(*, visual: bool = False) -> dict:
    return {
        "title": "Inventory trade-off", "summary": "A concise explanation.", "bullets": ["Inventory balances cost and service."],
        "table": [], "formula": None,
        "sections": [{"heading": "Trade-off", "level": 3, "body": "Service has a cost.", "bullets": ["Hold enough stock."], "evidence_ids": ["S001", "H001"]}],
        "evidence_coverage": [
            {"evidence_id": "S001", "disposition": "used", "reason": "Core material"},
            {"evidence_id": "H001", "disposition": "used", "reason": "User emphasis"},
        ],
        "visual_decisions": ([{"evidence_id": "S001", "decision": "include", "reason": "The chart conveys the trade-off."}] if visual else []),
        "model_knowledge": [],
    }


class CandidateProvider:
    def __init__(self, *, visual: bool = False) -> None:
        self.calls: list[str] = []
        self.visual = visual

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request.phase)
        return ProviderResponse(valid_notes(visual=self.visual), request.requested.provider, request.requested.model, request.requested.effort, {"total_tokens": 10})


@pytest.mark.parametrize("quality, expected", [("base", 1), ("standard", 1), ("approfondito", 2), ("revisione", 3)])
def test_candidate_quality_levels_have_deliberate_phase_counts(tmp_path, quality, expected) -> None:
    workspace = configured_workspace(tmp_path)
    source_pack(workspace)
    adapter = CandidateProvider()
    result = generate_candidate(workspace, "OPS7-L01", quality=quality, provider_adapter=adapter)
    assert result["status"] == "CANDIDATE_READY"
    assert len(adapter.calls) == expected
    assert not list(workspace.root.rglob("*.docx"))
    assert status(workspace)[0]["status"] == "CANDIDATE_READY"


def test_identical_candidate_is_reused_without_another_provider_call(tmp_path) -> None:
    workspace = configured_workspace(tmp_path)
    source_pack(workspace)
    adapter = CandidateProvider()
    assert generate_candidate(workspace, "OPS7-L01", provider_adapter=adapter)["status"] == "CANDIDATE_READY"
    assert generate_candidate(workspace, "OPS7-L01", provider_adapter=adapter)["status"] == "SKIPPED"
    assert adapter.calls == ["writing"]


def test_successful_candidate_replaces_an_earlier_failed_candidate_status(tmp_path) -> None:
    workspace = configured_workspace(tmp_path)
    source_pack(workspace)
    class FailingProvider:
        def generate(self, request):
            raise ProviderError("transport failure")
    with pytest.raises(WorkflowError):
        generate_candidate(workspace, "OPS7-L01", provider_adapter=FailingProvider())
    assert status(workspace)[0]["status"] == "FAILED"
    assert generate_candidate(workspace, "OPS7-L01", provider_adapter=CandidateProvider())["status"] == "CANDIDATE_READY"
    assert status(workspace)[0]["status"] == "CANDIDATE_READY"


def test_candidate_rejects_missing_evidence_before_word_publication(tmp_path) -> None:
    workspace = configured_workspace(tmp_path)
    source_pack(workspace)
    class MissingCoverage(CandidateProvider):
        def generate(self, request):
            value = valid_notes()
            value["evidence_coverage"] = value["evidence_coverage"][:1]
            return ProviderResponse(value, "codex", "gpt-test", "medium")
    with pytest.raises(WorkflowError, match="omits required evidence coverage"):
        generate_candidate(workspace, "OPS7-L01", provider_adapter=MissingCoverage())
    assert not list(workspace.root.rglob("*.docx"))
    assert status(workspace)[0]["status"] == "FAILED"


def test_codex_adapter_uses_structured_read_only_cli_and_logs_attempt(tmp_path, monkeypatch) -> None:
    run = tmp_path / "run"
    request = ProviderRequest("OPS7-L01", {"name": "Ops"}, {}, RequestedModel("codex", "gpt-test", "medium", "standard"), run_dir=run)
    captured: list[str] = []
    monkeypatch.setattr("mynotes.providers.shutil.which", lambda _: "/usr/local/bin/codex")
    def fake_run(command, **kwargs):
        captured.extend(command)
        final = Path(command[command.index("--output-last-message") + 1])
        atomic_json_write(final, valid_notes())
        return subprocess.CompletedProcess(command, 0, '{"type":"turn.completed","usage":{"total_tokens":42}}\n', "")
    monkeypatch.setattr("mynotes.providers.subprocess.run", fake_run)
    response = CodexAdapter().generate(request)
    assert "--sandbox" in captured and captured[captured.index("--sandbox") + 1] == "read-only"
    assert "--output-schema" in captured and 'model_reasoning_effort="medium"' in captured
    assert response.usage["total_tokens"] == 42
    assert Path(response.attempt_path).is_file()


def test_agy_non_success_and_quota_are_never_accepted(tmp_path, monkeypatch) -> None:
    request = ProviderRequest("OPS7-L01", {"name": "Ops"}, {}, RequestedModel("agy", "gemini-test", "high", "standard"), run_dir=tmp_path / "run")
    monkeypatch.setattr("mynotes.providers.shutil.which", lambda _: "/usr/local/bin/agy")
    monkeypatch.setattr("mynotes.providers.subprocess.run", lambda command, **kwargs: subprocess.CompletedProcess(command, 1, '{"status":"ERROR"}', "quota reached"))
    with pytest.raises(ProviderQuotaError):
        AgyAdapter().generate(request)
    assert list((tmp_path / "run" / "provider-attempts").glob("*.json"))


def test_agy_adapter_reads_success_envelope_and_exact_flags(tmp_path, monkeypatch) -> None:
    request = ProviderRequest("OPS7-L01", {"name": "Ops"}, {}, RequestedModel("agy", "gemini-test", "high", "standard"), run_dir=tmp_path / "run")
    captured: list[str] = []
    monkeypatch.setattr("mynotes.providers.shutil.which", lambda _: "/usr/local/bin/agy")
    def fake_run(command, **kwargs):
        captured.extend(command)
        return subprocess.CompletedProcess(command, 0, json.dumps({"status": "SUCCESS", "structured_output": valid_notes(), "usage": {"total_tokens": 9}}), "")
    monkeypatch.setattr("mynotes.providers.subprocess.run", fake_run)
    response = AgyAdapter().generate(request)
    assert captured[captured.index("--model") + 1] == "gemini-test"
    assert captured[captured.index("--effort") + 1] == "high"
    assert captured[-2] == "--print" and "Return exactly one JSON object" in captured[-1]
    assert response.usage["total_tokens"] == 9


def test_agy_schema_avoids_numeric_enum_and_coverage_is_not_a_quota_error() -> None:
    schema = _agy_schema(notes_schema())
    level = schema["properties"]["sections"]["items"]["properties"]["level"]
    assert level["type"] == "integer" and "enum" not in level
    assert _quota_error("evidence_coverage is missing") is False
    assert _quota_error("quota reached") is True


def test_natural_terminal_alias_extracts_lecture_and_quality(tmp_path, monkeypatch, capsys) -> None:
    workspace = configured_workspace(tmp_path)
    source_pack(workspace)
    adapter = CandidateProvider()
    monkeypatch.setattr("mynotes.providers.default_adapter", lambda _: adapter)
    exit_code = main(["agy", "genera", "OPS7-L01", "in", "modo", "approfondito", "--workspace", str(workspace.root)])
    result = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert result["quality"] == "approfondito"
    assert adapter.calls == ["writing", "scoped_review"]
