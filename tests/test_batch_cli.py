"""The batch interface preserves selections and never generates during planning."""
import json

import pytest

from mynotes.cli import _lesson_numbers, main
from mynotes.configuration import course_template
from mynotes.utils import atomic_json_write
from mynotes.workflow import WorkflowError
from mynotes.workspace import Workspace


def configured(tmp_path):
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()
    configuration = workspace.configuration_store().load()
    course = course_template()
    course.update(code="OPS", name="Operations")
    configuration["courses"] = [course]
    configuration["providers"].update(default="codex", configured={"codex": {}})
    workspace.configuration_store().save(configuration)
    return workspace


def test_numbers_are_numeric_ordered_and_deduplicated():
    assert _lesson_numbers("10,3-5,1,4") == [1, 3, 4, 5, 10]


@pytest.mark.parametrize("value", ["", "0", "7-2", "100", "1-100", "-2", "1,,2", "first", "1-2-3"])
def test_invalid_selection_never_reaches_sources(value):
    with pytest.raises(WorkflowError):
        _lesson_numbers(value)


def test_plan_only_missing_sources_does_not_prepare_or_call_provider(tmp_path, monkeypatch, capsys):
    workspace = configured(tmp_path)
    before = {str(p.relative_to(workspace.root)): p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    monkeypatch.setattr("mynotes.cli.build_source_pack", lambda *a, **kw: pytest.fail("planning must not prepare"))
    monkeypatch.setattr("mynotes.batch.invoke_provider", lambda *a, **kw: pytest.fail("planning must not generate"))
    assert main(["generate-batch", "OPS", "--workspace", str(workspace.root), "--lessons", "1-3", "--plan-only"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "NEEDS_PREPARATION" and len(report["sources"]) == 3
    assert before == {str(p.relative_to(workspace.root)): p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}


def test_negative_image_cap_rejected_before_preparation(tmp_path, monkeypatch):
    workspace = configured(tmp_path)
    monkeypatch.setattr("mynotes.cli.build_source_pack", lambda *a, **kw: pytest.fail("invalid cap must not prepare"))
    assert main(["generate-batch", "OPS", "--workspace", str(workspace.root), "--lessons", "1", "--image-limit", "-1"]) == 2


def test_resume_keeps_saved_run_choices_without_repeating_lessons(tmp_path, monkeypatch, capsys):
    from mynotes.batch import _save
    workspace = configured(tmp_path)
    batch_id = "OPS-0123456789ab"
    manifest = {"batch_id": batch_id, "course_code": "OPS", "lectures": ["OPS-L01", "OPS-L02"],
                "requested_model": {"provider": "agy", "quality": "approfondito"},
                "selection": {"slide": {"owner": "OPS-L01", "status": "taught"}}, "budgets": {},
                "request_options": {"instructions": "Explain the assumptions", "image_limit": 2}}
    _save(workspace.runs_dir / "_batches" / batch_id / "manifest.json", manifest)
    monkeypatch.setattr("mynotes.cli._batch_preparation", lambda *a, **kw: [])
    calls = []
    monkeypatch.setattr("mynotes.batch.generate_batch", lambda *a, **kw: calls.append((a, kw)) or {"status": "CANDIDATE_READY"})
    assert main(["generate-batch", "OPS", "--workspace", str(workspace.root), "--resume", batch_id]) == 0
    assert calls[0][0][2] == [1, 2]
    assert calls[0][1]["provider"] == "agy" and calls[0][1]["quality"] == "approfondito"
    assert calls[0][1]["image_limit"] == 2 and calls[0][1]["selection"] == manifest["selection"]


def test_batch_resume_rejects_path_traversal(tmp_path):
    workspace = configured(tmp_path)
    assert main(["generate-batch", "OPS", "--workspace", str(workspace.root), "--resume", "../../foreign"]) == 2


def test_models_cli_reads_without_creating_workspace(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MYNOTES_WORKSPACE", str(tmp_path / "absent"))
    monkeypatch.setattr("mynotes.models.catalog", lambda *a, **kw: {"provider": "codex", "choices": []})
    assert main(["models", "--provider", "codex", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["provider"] == "codex"
    assert not (tmp_path / "absent").exists()


def test_batch_provider_quota_is_actionable_cli_error(tmp_path, monkeypatch, capsys):
    from mynotes.providers import ProviderQuotaError
    workspace = configured(tmp_path)
    monkeypatch.setattr("mynotes.cli._batch_preparation", lambda *a, **kw: [])
    def paused(*args, **kwargs):
        raise ProviderQuotaError("Batch paused: provider quota exhausted")
    monkeypatch.setattr("mynotes.batch.generate_batch", paused)
    assert main(["generate-batch", "OPS", "--workspace", str(workspace.root), "--lessons", "1-2"]) == 2
    assert "quota exhausted" in capsys.readouterr().out
