from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mynotes.configuration import course_template
from mynotes.cli import main
from mynotes.providers import AgyAdapter, CodexAdapter, ProviderError, ProviderQuotaError, ProviderRequest, ProviderResponse, RequestedModel, _agy_schema, _context, _prompt, _quota_error, notes_schema
from mynotes.utils import atomic_json_write
from mynotes.workflow import WorkflowError, _validate_candidate_notes, _validate_integer_table_totals, _validate_numeric_table_support, generate_candidate, status


def test_integer_total_row_rejects_arithmetic_disagreement() -> None:
    rows = [["Period", "Demand", "Filled", "Short"], ["1", "7", "6", "1"], ["2", "9", "8", "1"], ["Total", "16", "13", "3"]]
    with pytest.raises(WorkflowError, match="column 3"):
        _validate_integer_table_totals(rows)
    rows[-1] = ["Total", "16", "14", "2"]
    _validate_integer_table_totals(rows)


def test_numeric_table_cannot_use_qualitative_note_as_numeric_evidence() -> None:
    block = {"rows": [["Phase", "Threshold", "Margin"], ["1", "400", "95"], ["2", "1000", "80"]], "evidence_ids": ["H001"]}
    evidence = {"H001": {"content": "Allocate the 1500 units to the highest marginal profit first."}}
    with pytest.raises(WorkflowError, match="lacks numeric values"):
        _validate_numeric_table_support(block, evidence)
    evidence["H001"]["content"] = "First 400 units earn 95, then the next 600 earn 80."
    _validate_numeric_table_support(block, evidence)
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
        "model_knowledge": [], "review_questions": [],
        "blocks": [
            {"kind": "overview", "items": ["Inventory trade-off", "Service level"], "evidence_ids": ["S001"]},
            {"kind": "paragraph", "text": "Stock balances cost and service.", "evidence_ids": ["S001", "H001"]},
            *([{"kind": "visual", "visual_id": "S001", "evidence_ids": ["S001"]}] if visual else []),
            {"kind": "qa", "question": "Why hold stock?", "answer": "To support service levels.", "evidence_ids": ["S001", "H001"]},
            {"kind": "recap", "text": "Balance service and cost.", "evidence_ids": ["S001"]},
        ],
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


def test_style_change_invalidates_candidate_cache(tmp_path) -> None:
    workspace = configured_workspace(tmp_path)
    source_pack(workspace)
    adapter = CandidateProvider()
    generate_candidate(workspace, "OPS7-L01", provider_adapter=adapter)
    configuration = workspace.configuration_store().load()
    configuration["style"]["freeform_preferences"] = "Explain in simple English."
    workspace.configuration_store().save(configuration)
    assert generate_candidate(workspace, "OPS7-L01", provider_adapter=adapter)["status"] == "CANDIDATE_READY"
    assert adapter.calls == ["writing", "writing"]


def test_generation_passes_effective_course_style_to_provider(tmp_path) -> None:
    workspace = configured_workspace(tmp_path)
    source_pack(workspace)
    configuration = workspace.configuration_store().load()
    configuration["style"]["profile"] = {"writing_style": "detailed", "image_policy": "none", "question_count": 3}
    configuration["courses"][0]["style"] = {"image_policy": "essential", "question_count": 1}
    workspace.configuration_store().save(configuration)

    class StyleCapture(CandidateProvider):
        def __init__(self):
            super().__init__()
            self.style: dict | None = None

        def generate(self, request):
            self.style = request.style
            return super().generate(request)

    adapter = StyleCapture()
    assert generate_candidate(workspace, "OPS7-L01", provider_adapter=adapter)["status"] == "CANDIDATE_READY"
    assert adapter.style is not None
    assert adapter.style["writing_style"] == "detailed"
    assert adapter.style["image_policy"] == "essential"
    assert adapter.style["question_count"] == 1


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


def test_new_ordered_candidate_rejects_uninspected_included_visual(tmp_path) -> None:
    workspace = configured_workspace(tmp_path)
    source_pack(workspace, visual=True)
    class Uninspected(CandidateProvider):
        def generate(self, request):
            value = valid_notes(visual=True)
            return ProviderResponse(value, "codex", "gpt-test", "medium")
    with pytest.raises(WorkflowError, match="confirmed as inspected"):
        generate_candidate(workspace, "OPS7-L01", provider_adapter=Uninspected())


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


def test_agy_grants_only_lecture_folder_for_source_pack(tmp_path, monkeypatch) -> None:
    run = tmp_path / "OPS7-L01"
    run.mkdir()
    request = ProviderRequest(
        "OPS7-L01", {"name": "Ops"}, {}, RequestedModel("agy", "gemini-test", "high", "standard"),
        source_pack={"lecture_id": "OPS7-L01", "course": {"name": "Ops"}, "evidence": [], "visuals": []}, run_dir=run,
    )
    captured: list[str] = []
    invocation: dict[str, object] = {}
    monkeypatch.setattr("mynotes.providers.shutil.which", lambda _: "/usr/local/bin/agy")
    def fake_run(command, **kwargs):
        captured.extend(command)
        invocation.update(kwargs)
        return subprocess.CompletedProcess(command, 0, json.dumps({"status": "SUCCESS", "structured_output": valid_notes()}), "")
    monkeypatch.setattr("mynotes.providers.subprocess.run", fake_run)
    AgyAdapter().generate(request)
    assert captured[captured.index("--add-dir") + 1] == str(run.resolve())
    assert invocation["cwd"] == str(run.resolve())


def test_agy_schema_avoids_numeric_enum_and_coverage_is_not_a_quota_error() -> None:
    schema = _agy_schema(notes_schema())
    level = schema["properties"]["sections"]["items"]["properties"]["level"]
    assert level["type"] == "integer" and "enum" not in level
    assert _quota_error("evidence_coverage is missing") is False
    assert _quota_error("quota reached") is True


def test_prompt_context_preserves_style_speaker_and_visual_path(tmp_path) -> None:
    image = tmp_path / "chart.png"
    request = ProviderRequest(
        "OPS7-L01", {"name": "Operations", "style": {"focus": "worked examples"}, "preferences": {"persistent_instructions": ["brief recap"]}},
        {}, RequestedModel("agy", "gemini-test", "high", "standard"),
        source_pack={"lecture_id": "OPS7-L01", "course": {"name": "Operations"}, "evidence": [{"id": "T1000", "kind": "transcript", "speaker_role": "Student", "content": "Could the cost fall?"}],
                     "visuals": [{"evidence_id": "S001", "image_path": str(image), "visual_decision": "candidate"}]},
        style={"recap": True, "freeform_preferences": "simple English"},
    )
    context = _context(request)
    assert "worked examples" in context and "brief recap" in context
    assert "simple English" in context and "speaker=Student" in context
    assert str(image) in context
    assert notes_schema()["properties"]["blocks"]["items"]["properties"]["visual_id"]["pattern"].endswith("{3,}$")


def test_editorial_prompt_is_self_contained_and_reflects_course_style() -> None:
    request = ProviderRequest(
        "OPS7-L01", {"name": "Operations", "languages": {"notes": "it"}}, {},
        RequestedModel("agy", "gemini-test", "high", "standard"),
        style={"writing_style": "detailed", "list_density": "prose_first", "image_policy": "none", "question_count": 2, "recap": False},
    )
    prompt = _prompt(request)
    assert "configured notes language (it)" in prompt
    assert "detailed, connected explanations" in prompt
    assert "Prefer connected paragraphs" in prompt
    assert "Do not include images" in prompt
    assert "between one and 2 qa blocks" in prompt
    assert "Word adds the numbering" in prompt
    assert "Do not add a recap block" in prompt
    assert "review_questions" in notes_schema()["required"]
    assert "[[term:technical term]]" in prompt and "**bold**" in prompt
    assert "source-supported technical terms" in prompt
    assert "ordinary black emphasis" in prompt and "fixed density" in prompt
    assert "bold orange throughout the narrative" in prompt
    assert "do not turn an ordinary set of concepts or bullet points into a table" in prompt
    assert {"overview", "qa"} <= set(notes_schema()["properties"]["blocks"]["items"]["properties"]["kind"]["enum"])
    same_language = _prompt(ProviderRequest(
        "OPS7-L01", {"name": "Operations", "languages": {"notes": "en"}}, {},
        RequestedModel("agy", "gemini-test", "high", "standard"),
        style={"recap": True, "recap_language": "same_as_notes", "question_language": "it", "freeform_preferences": "Use simple terms."},
    ))
    assert "one concise recap block in en" in same_language
    assert "retain their original wording and language" in same_language
    assert "additional question and answer in it" in same_language
    assert "Do not add a 'Question 1' prefix" in same_language
    assert "Use simple terms." in same_language


@pytest.mark.parametrize("phase", ["scoped_review", "evidence_audit", "editorial_review"])
def test_review_prompts_preserve_source_supported_term_markers(phase: str) -> None:
    request = ProviderRequest(
        "OPS7-L01", {"name": "Operations"}, {},
        RequestedModel("agy", "gemini-test", "high", "approfondito"), phase=phase,
    )
    prompt = _prompt(request)
    assert "[[term:...]]" in prompt
    assert "Preserve" in prompt or "Keep concise" in prompt


def test_editorial_blocks_validate_structure_and_course_question_limit(tmp_path) -> None:
    workspace = configured_workspace(tmp_path)
    pack = json.loads(source_pack(workspace).read_text(encoding="utf-8"))
    candidate = valid_notes()
    candidate["blocks"] = [
        {"kind": "overview", "items": ["Inventory trade-off", "Service level"], "evidence_ids": ["S001"]},
        {"kind": "paragraph", "text": "Stock balances cost and service.", "evidence_ids": ["S001"]},
        {"kind": "qa", "question": "Why hold stock?", "answer": "To support service levels.", "evidence_ids": ["S001", "H001"]},
        {"kind": "recap", "text": "Balance service and cost.", "evidence_ids": ["S001"]},
    ]
    style = {"question_count": 1, "recap": True, "image_policy": "none"}
    assert _validate_candidate_notes(candidate, "OPS7-L01", pack, style=style)["review_questions"] == []
    candidate["blocks"][0]["items"][0] = "1. Inventory trade-off"
    with pytest.raises(WorkflowError, match="omit written numbers"):
        _validate_candidate_notes(candidate, "OPS7-L01", pack, style=style)
    candidate["blocks"][0]["items"][0] = "Inventory trade-off"
    candidate["blocks"].insert(3, {"kind": "qa", "question": "What is the cost?", "answer": "Holding cost.", "evidence_ids": ["S001"]})
    with pytest.raises(WorkflowError, match="between one and 1 qa blocks"):
        _validate_candidate_notes(candidate, "OPS7-L01", pack, style=style)
    candidate["blocks"] = candidate["blocks"][1:3] + candidate["blocks"][4:]
    with pytest.raises(WorkflowError, match="start with one overview"):
        _validate_candidate_notes(candidate, "OPS7-L01", pack, style=style)


def test_new_generation_cannot_omit_editorial_blocks_but_saved_legacy_can(tmp_path) -> None:
    workspace = configured_workspace(tmp_path)
    pack = json.loads(source_pack(workspace).read_text(encoding="utf-8"))
    legacy = valid_notes()
    legacy.pop("blocks")
    assert _validate_candidate_notes(legacy, "OPS7-L01", pack)["lecture_id"] == "OPS7-L01"
    with pytest.raises(WorkflowError, match="start with one overview"):
        _validate_candidate_notes(legacy, "OPS7-L01", pack, style={"question_count": 5, "recap": True})


def test_editorial_blocks_reject_broken_technical_term_markup(tmp_path) -> None:
    workspace = configured_workspace(tmp_path)
    pack = json.loads(source_pack(workspace).read_text(encoding="utf-8"))
    candidate = valid_notes()
    candidate["blocks"] = [
        {"kind": "overview", "items": ["Inventory"], "evidence_ids": ["S001"]},
        {"kind": "paragraph", "text": "The [[term:safety stock is useful.", "evidence_ids": ["S001"]},
        {"kind": "qa", "question": "Why?", "answer": "Service.", "evidence_ids": ["S001"]},
        {"kind": "recap", "text": "Inventory balances cost and service.", "evidence_ids": ["S001"]},
    ]
    with pytest.raises(WorkflowError, match="malformed technical-term marker"):
        _validate_candidate_notes(candidate, "OPS7-L01", pack)


def test_editorial_heading_rejects_written_section_number(tmp_path) -> None:
    workspace = configured_workspace(tmp_path)
    pack = json.loads(source_pack(workspace).read_text(encoding="utf-8"))
    candidate = valid_notes()
    candidate["blocks"].insert(1, {"kind": "heading", "text": "1. Inventory", "level": 3, "evidence_ids": ["S001"]})
    with pytest.raises(WorkflowError, match="omit written section numbers"):
        _validate_candidate_notes(candidate, "OPS7-L01", pack, style={"question_count": 5, "recap": True})


def test_editorial_title_duplicate_is_removed_without_changing_provider_record(tmp_path) -> None:
    workspace = configured_workspace(tmp_path)
    pack = json.loads(source_pack(workspace).read_text(encoding="utf-8"))
    candidate = valid_notes()
    candidate["sections"][0]["level"] = 2
    candidate["blocks"].insert(1, {"kind": "heading", "text": candidate["title"], "level": 2, "evidence_ids": ["S001"]})
    normalized = _validate_candidate_notes(candidate, "OPS7-L01", pack, style={"question_count": 5, "recap": True})
    assert all(block.get("text") != candidate["title"] for block in normalized["blocks"])
    assert candidate["blocks"][1]["level"] == 2


def test_redundant_exclusion_of_a_preexcluded_visual_is_ignored(tmp_path) -> None:
    workspace = configured_workspace(tmp_path)
    pack = json.loads(source_pack(workspace).read_text(encoding="utf-8"))
    pack["visuals"] = [{"evidence_id": "S001", "visual_decision": "exclude"}]
    candidate = valid_notes()
    candidate["visual_decisions"] = [{"evidence_id": "S001", "decision": "exclude", "reason": "Text-only slide"}]
    normalized = _validate_candidate_notes(candidate, "OPS7-L01", pack)
    assert normalized["visual_decisions"] == []
    assert len(candidate["visual_decisions"]) == 1


def test_formula_block_uses_identically_labeled_top_level_equation(tmp_path) -> None:
    workspace = configured_workspace(tmp_path)
    pack = json.loads(source_pack(workspace).read_text(encoding="utf-8"))
    candidate = valid_notes()
    candidate["formulas"] = [{"label": "Expected margin", "expression": "M=R-W"}]
    candidate["blocks"].insert(2, {"kind": "formula", "label": "Expected margin", "evidence_ids": ["S001"]})
    normalized = _validate_candidate_notes(candidate, "OPS7-L01", pack, style={"question_count": 5, "recap": True})
    assert normalized["blocks"][2]["expression"] == "M=R-W"
    assert "expression" not in candidate["blocks"][2]


def test_long_technical_generation_requires_a_narrative_term(tmp_path) -> None:
    workspace = configured_workspace(tmp_path)
    pack = json.loads(source_pack(workspace).read_text(encoding="utf-8"))
    candidate = valid_notes()
    long_explanation = "Inventory policy balances holding cost and service after demand uncertainty is measured. " * 70
    candidate["blocks"].insert(2, {"kind": "paragraph", "text": long_explanation, "evidence_ids": ["S001"]})
    candidate["blocks"].insert(3, {"kind": "formula", "label": "Reorder point", "expression": "R=D L", "evidence_ids": ["S001"]})
    with pytest.raises(WorkflowError, match="Long technical candidate needs"):
        _validate_candidate_notes(candidate, "OPS7-L01", pack, style={"question_count": 5, "recap": True})
    # A saved pre-contract candidate is still readable, while a new candidate
    # needs only a genuine key term, not a marker-density target.
    assert _validate_candidate_notes(candidate, "OPS7-L01", pack)["lecture_id"] == "OPS7-L01"
    candidate["blocks"][1]["text"] = "[[term:Safety stock]] **balances cost and service**."
    assert _validate_candidate_notes(candidate, "OPS7-L01", pack, style={"question_count": 5, "recap": True})["lecture_id"] == "OPS7-L01"


def test_black_emphasis_is_distinct_from_bold_orange_terms(tmp_path) -> None:
    workspace = configured_workspace(tmp_path)
    pack = json.loads(source_pack(workspace).read_text())
    candidate = valid_notes()
    candidate["blocks"][1]["text"] = "[[term:Safety stock]] **Safety stock** balances cost and service. " + "Demand is uncertain. " * 170
    candidate["blocks"].insert(2, {"kind": "formula", "label": "Reorder point", "expression": "R=D L", "evidence_ids": ["S001"]})
    style = {"question_count": 5, "recap": True}
    with pytest.raises(WorkflowError, match="ordinary black"):
        _validate_candidate_notes(candidate, "OPS7-L01", pack, style=style)
    # Writing remains reviewable and saved legacy candidates still build.
    assert _validate_candidate_notes(candidate, "OPS7-L01", pack, style=style, final_phase=False)
    assert _validate_candidate_notes(candidate, "OPS7-L01", pack)
    candidate["blocks"][1]["text"] += " **Additional stock has a cost.**"
    assert _validate_candidate_notes(candidate, "OPS7-L01", pack, style=style)


def test_second_phase_can_repair_missing_ordinary_emphasis(tmp_path) -> None:
    workspace = configured_workspace(tmp_path)
    source_pack(workspace)

    class ReviewProvider:
        calls: list[str] = []

        def generate(self, request):
            self.calls.append(request.phase)
            candidate = valid_notes()
            candidate["blocks"][1]["text"] = "[[term:Safety stock]] balances cost and service. " + "Demand is uncertain. " * 170
            if request.phase == "scoped_review":
                candidate["blocks"][1]["text"] += " **Additional stock has a cost.**"
            candidate["blocks"].insert(2, {"kind": "formula", "label": "Reorder point", "expression": "R=D L", "evidence_ids": ["S001"]})
            return ProviderResponse(candidate, request.requested.provider, request.requested.model, request.requested.effort, {})

    adapter = ReviewProvider()
    result = generate_candidate(workspace, "OPS7-L01", quality="approfondito", provider_adapter=adapter)
    diagnostics = json.loads(Path(result["candidate"]).read_text())["editorial_diagnostics"]
    assert adapter.calls == ["writing", "scoped_review"]
    assert diagnostics[0]["missing"] is True
    assert diagnostics[1]["missing"] is False


def test_table_term_is_valid_but_does_not_hide_unmarked_explanations(tmp_path) -> None:
    workspace = configured_workspace(tmp_path)
    pack = json.loads(source_pack(workspace).read_text(encoding="utf-8"))
    candidate = valid_notes()
    candidate["blocks"].insert(2, {"kind": "paragraph", "text": "Demand uncertainty affects the reorder point and the required inventory buffer. " * 60, "evidence_ids": ["S001"]})
    candidate["blocks"].insert(3, {"kind": "table", "rows": [["Term", "Meaning"], ["[[term:Safety stock]]", "Inventory buffer"]], "evidence_ids": ["S001"]})
    with pytest.raises(WorkflowError, match="Long technical candidate needs"):
        _validate_candidate_notes(candidate, "OPS7-L01", pack, style={"question_count": 5, "recap": True})


def test_long_nontechnical_lesson_does_not_require_orange_terms(tmp_path) -> None:
    workspace = configured_workspace(tmp_path)
    pack = json.loads(source_pack(workspace).read_text(encoding="utf-8"))
    candidate = valid_notes()
    candidate["blocks"].insert(2, {"kind": "paragraph", "text": "A careful explanation can use everyday language. " * 80, "evidence_ids": ["S001"]})
    assert _validate_candidate_notes(candidate, "OPS7-L01", pack, style={"question_count": 5, "recap": True})["lecture_id"] == "OPS7-L01"


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
