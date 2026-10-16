"""Shared generation consumption is counted by invocation, never by lesson."""
import json
import subprocess
import time

from mynotes.cli import main
from mynotes.providers import ProviderRequest, RequestedModel, _save_attempt
from mynotes.usage import batch_usage
from mynotes.workspace import Workspace


def attempt(workspace, usage, *, error=None):
    root = workspace.runs_dir / "_batches" / "OPS-0123456789ab" / "version" / "group-000"
    request = ProviderRequest("OPS-L01", {}, {}, RequestedModel("codex", "test", "high", "standard"), run_dir=root)
    output = json.dumps({"usage": usage}) if usage is not None else "No counters"
    completed = subprocess.CompletedProcess(["/test/provider"], 1 if error else 0, output, "")
    return _save_attempt(request, command=["/test/provider"], completed=completed, started=time.monotonic(), error=error)


def test_shared_attempt_counted_once_and_readonly(tmp_path):
    workspace = Workspace.from_value(tmp_path / "private")
    path = attempt(workspace, {"input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 4})
    duplicate = path.parent / "writing-aaaaaaaaaaaa.json"
    duplicate.write_bytes(path.read_bytes())
    before = {str(p): p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    report = batch_usage(workspace, "OPS-0123456789ab")
    assert len(report["attempts"]) == 1
    assert report["providers"]["codex"]["reported_totals"] == {"input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 4}
    assert before == {str(p): p.read_bytes() for p in workspace.root.rglob("*") if p.is_file()}
    assert not workspace.database_path.exists()


def test_batch_failures_unknown_counters_and_conflicts(tmp_path):
    workspace = Workspace.from_value(tmp_path / "private")
    attempt(workspace, {"input_tokens": 7}, error="transport failure")
    attempt(workspace, None, error="interrupted")
    path = attempt(workspace, {"input_tokens": 3})
    altered = json.loads(path.read_text())
    altered["reported_usage"]["input_tokens"] = 30
    (path.parent / "writing-bbbbbbbbbbbb.json").write_text(json.dumps(altered))
    report = batch_usage(workspace, "OPS-0123456789ab")
    assert report["providers"]["codex"]["reported_totals"] == {"input_tokens": 7}
    assert report["providers"]["codex"]["failed"] == 2
    assert report["providers"]["codex"]["unknown_usage_attempts"] == 1 and report["partial"]


def test_batch_usage_cli_does_not_create_absent_workspace(tmp_path, capsys):
    absent = tmp_path / "absent"
    assert main(["usage", "--batch", "OPS-0123456789ab", "--workspace", str(absent), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["attempts"] == [] and not absent.exists()
