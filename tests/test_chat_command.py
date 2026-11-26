"""`launch_chat` argv is byte-identical after extracting `chat_command` and `startup_prompt`.

`tests/fixtures/chat/launch-argv.json` was recorded from `launch_chat` before the
refactor (same matrix as below). Regenerate it only for an intended change:
`MYNOTES_RECORD_CHAT_ARGV=1 uv run pytest tests/test_chat_command.py`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mynotes.chat import launch_chat
from mynotes.workspace import Workspace

GOLDEN = Path(__file__).parent / "fixtures" / "chat" / "launch-argv.json"

# (name, provider, message, configuration change, user-owned rules file)
CASES = [
    ("codex-greeting", "codex", None, None, None),
    ("codex-request", "codex", "Prepara OPS-L03", None, None),
    ("claude-greeting", "claude", None, None, None),
    ("claude-request", "claude", "Quiz me on lessons 1-4", None, None),
    ("agy-request", "agy", "Check my preferences", None, None),
    ("agy-greeting-it", "agy", None, "italian", None),
    ("codex-first-run", "codex", None, "first-run", None),
    ("claude-first-run-it", "claude", None, "first-run-it", None),
    ("agy-course-questions", "agy", None, "background-only", None),
    ("codex-roles", "codex", "Write notes for OPS-L02", "roles", None),
    ("claude-legacy-rules", "claude", None, None, "AGENTS.override.md"),
    ("codex-legacy-rules", "codex", "Undo the last update", None, "GEMINI.md"),
    ("agy-legacy-rules", "agy", None, None, "AGENTS.md"),
    ("claude-dialogue", "claude", None, "dialogue", None),
]


def _configure(workspace: Workspace, change: str | None) -> dict:
    configuration = workspace.configuration_store().load()
    configuration["onboarding"]["status"] = "complete"
    configuration["courses"] = [{"code": "OPS", "name": "Operations"}, {"code": "STAT", "name": "Statistics"}]
    configuration["providers"]["configured"] = {
        "agy": {"model": "test-model", "effort": "high"},
        "codex": {"model": "test-codex", "effort": "medium"},
        "claude": {"model": "test-claude", "effort": "low"},
    }
    if change == "italian":
        configuration["personal"]["interface_language"] = "it"
    if change in {"first-run", "first-run-it"}:
        configuration["courses"] = []
        configuration["onboarding"]["status"] = "pending"
        if change == "first-run-it":
            configuration["personal"]["interface_language"] = "it"
    if change == "background-only":
        configuration["courses"] = []
        configuration["personal"]["background"] = "Synthetic background"
    if change == "roles":
        configuration["providers"]["roles"] = {"notes": {"provider": "claude", "model": "test-claude", "effort": "low"}}
    if change == "dialogue":
        configuration["personal"]["dialogue_instructions"] = {"tone": "Answer in two sentences."}
    return configuration


def _argv(tmp_path: Path, monkeypatch, case) -> list[str]:
    name, provider, message, change, rules = case
    root = tmp_path / name
    workspace = Workspace.from_value(root / "private")
    workspace.initialize()
    if rules:
        (workspace.root / rules).write_text("# Local guidance\n", encoding="utf-8")
    configuration = _configure(workspace, change)
    calls = []
    monkeypatch.delenv("MYNOTES_CHAT_ACTIVE", raising=False)
    monkeypatch.setattr("mynotes.chat.shutil.which", lambda selected, path=None: f"/opt/agents/{selected}")
    monkeypatch.setattr("mynotes.chat.subprocess.run",
                        lambda command, **options: calls.append(command) or type("Done", (), {"returncode": 0})())
    assert launch_chat(workspace, configuration, provider, message) == 0
    assert len(calls) == 1
    text = json.dumps(calls[0], ensure_ascii=False).replace(json.dumps(str(workspace.root))[1:-1], "<WORKSPACE>")
    return json.loads(text)


@pytest.mark.skipif(os.name == "nt", reason="the golden argv is the POSIX launch path")
def test_launch_chat_argv_matches_the_pre_refactor_golden(tmp_path, monkeypatch):
    recorded = {case[0]: _argv(tmp_path, monkeypatch, case) for case in CASES}
    if os.environ.get("MYNOTES_RECORD_CHAT_ARGV") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(recorded, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(recorded) == set(golden)
    for name, argv in recorded.items():
        assert argv == golden[name], name
