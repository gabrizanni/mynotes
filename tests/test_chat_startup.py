from __future__ import annotations

from pathlib import Path

import pytest

from mynotes.chat import ChatError, launch_chat
from mynotes.cli import main
from mynotes.integrations import install_integration
from mynotes.workspace import Workspace


def _setup(tmp_path: Path):
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()
    configuration = workspace.configuration_store().load()
    configuration["onboarding"]["status"] = "complete"
    configuration["courses"] = [{"code": "OPS", "name": "Operations"}]
    configuration["providers"]["configured"] = {"agy": {"model": "test-model", "effort": "high"}}
    return workspace, configuration


def test_empty_chat_uses_startup_context_without_eager_checks(tmp_path, monkeypatch) -> None:
    workspace, configuration = _setup(tmp_path)
    calls = []
    monkeypatch.setattr("mynotes.chat.shutil.which", lambda _: "/usr/bin/agy")
    monkeypatch.setattr(
        "mynotes.chat.subprocess.run",
        lambda command, **kwargs: calls.append((command, kwargs)) or type("Done", (), {"returncode": 0})(),
    )

    assert launch_chat(workspace, configuration, "agy") == 0
    assert len(calls) == 1
    command, options = calls[0]
    prompt = command[command.index("--prompt-interactive") + 1]
    assert "courses=OPS" in prompt
    assert "onboarding=complete" in prompt
    assert "briefly greet the user, then wait" in prompt
    assert "do not run commands, inspect files" in prompt
    assert "the command tool to run" in prompt
    assert "`mynotes doctor`" in prompt
    assert "`mynotes status`" in prompt
    assert options["env"]["MYNOTES_WORKSPACE"] == str(workspace.root)
    assert options["env"]["MYNOTES_CHAT_ACTIVE"] == "1"


def test_concrete_chat_request_retains_checks_and_recursion_guard(tmp_path, monkeypatch) -> None:
    workspace, configuration = _setup(tmp_path)
    calls = []
    monkeypatch.setattr("mynotes.chat.shutil.which", lambda _: "/usr/bin/agy")
    monkeypatch.setattr(
        "mynotes.chat.subprocess.run",
        lambda command, **kwargs: calls.append(command) or type("Done", (), {"returncode": 0})(),
    )
    assert launch_chat(workspace, configuration, "agy", "Prepara OPS-L03") == 0
    prompt = calls[0][calls[0].index("--prompt-interactive") + 1]
    assert "Prepara OPS-L03" in prompt
    assert "For note generation run `mynotes doctor` and `mynotes status`" in prompt
    assert "the task's direct command for transcription" in prompt
    assert "validate and visually inspect the word candidate" in prompt.lower()
    assert "updates are automatic with safety backups unless the course publication mode is review" in prompt
    assert "mynotes undo CODE" in prompt
    assert "--notes-file" in prompt
    assert "show it to the user and obtain their approval before publication" not in prompt

    monkeypatch.setenv("MYNOTES_CHAT_ACTIVE", "1")
    with pytest.raises(ChatError, match="recursive launch"):
        launch_chat(workspace, configuration, "agy")


def test_agy_agent_is_self_contained_without_fragile_skill_dependency(tmp_path) -> None:
    installed = install_integration("agy", tmp_path)
    agent = (Path(installed["path"]) / "agents" / "mynotes.md").read_text(encoding="utf-8")
    frontmatter = agent.split("---", 2)[1]
    assert "skills:" not in frontmatter
    assert "run_command" in frontmatter
    assert "`mynotes doctor`" in agent
    assert "`mynotes status`" in agent
    assert "visually review the Word" in agent
    assert (Path(installed["path"]) / "skills" / "mynotes" / "SKILL.md").is_file()


def test_prepare_reuses_transcript_without_recording_discovery(tmp_path, monkeypatch) -> None:
    transcript = tmp_path / "OPS-L03.json"
    transcript.write_text('{"segments": []}', encoding="utf-8")
    seen = []
    monkeypatch.setattr(
        "mynotes.cli.discover_recordings",
        lambda *_: pytest.fail("recording discovery must not run for an imported transcript"),
    )
    monkeypatch.setattr(
        "mynotes.cli.build_source_pack",
        lambda workspace, lecture_id, **kwargs: seen.append((lecture_id, kwargs)) or {
            "lecture_id": lecture_id,
            "availability": {"transcript": True},
        },
    )
    assert main(["prepare", "OPS-L03", "--workspace", str(tmp_path / "private"), "--transcript", str(transcript)]) == 0
    assert seen[0][0] == "OPS-L03"
    assert seen[0][1]["transcript_path"] == transcript
    assert seen[0][1]["audio"] is None


def test_cli_and_chat_do_not_change_process_parts_policy(tmp_path, monkeypatch):
    import os
    monkeypatch.delenv("MYNOTES_AGY_PARTS", raising=False)
    assert main(["init", "--workspace", str(tmp_path / "cli")]) == 0
    assert "MYNOTES_AGY_PARTS" not in os.environ
    workspace, configuration = _setup(tmp_path)
    monkeypatch.setattr("mynotes.chat.shutil.which", lambda _: "/usr/bin/agy")
    calls = []
    monkeypatch.setattr("mynotes.chat.subprocess.run", lambda command, **kwargs:
                        calls.append((command, kwargs)) or type("Done", (), {"returncode": 0})())
    configuration["courses"][0]["context_setup"] = "pending"
    launch_chat(workspace, configuration, "agy", "Prepara OPS-L03")
    assert "MYNOTES_AGY_PARTS" not in os.environ
    prompt = calls[0][0][calls[0][0].index("--prompt-interactive") + 1]
    assert "do not start pending-course questionnaires before completing" in prompt
    assert "Optional course context never" in prompt
    assert "no extra approval" in prompt and "review mode" in prompt


def test_installed_integrations_share_operational_priority(tmp_path):
    for platform in ("codex", "agy", "claude"):
        installed = install_integration(platform, tmp_path)
        for name in (["agents/mynotes.md", "skills/mynotes/SKILL.md"] if platform == "agy" else ["SKILL.md"]):
            content = (Path(installed["path"]) / name).read_text()
            assert "do not start pending-course questionnaires before completing" in content
            assert "After completing the operational request" in content
