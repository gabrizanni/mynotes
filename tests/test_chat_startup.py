from __future__ import annotations

from pathlib import Path

import pytest

from mynotes.chat import ChatError, launch_chat
from mynotes.cli import main
from mynotes.integrations import install_integration, install_shared_chat_rules
from mynotes.workspace import Workspace
from mynotes.context import CORE, instruction_module, task_context


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
    assert "do not run commands, inspect files" in prompt.lower()
    assert "`mynotes doctor`" in CORE
    assert "`mynotes status`" in CORE
    assert "context --activity" in CORE
    assert instruction_module("notes") not in prompt
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
    prompt += CORE + task_context(workspace.configuration_store().load(), "notes")["instructions"]
    assert "`mynotes doctor` and `mynotes status`" in prompt
    assert "the task's direct command for transcription" in prompt
    assert "manual desktop and microsoft word checks belong to the user" in prompt.lower()
    assert "updates are automatic with safety backups unless course publication mode" in prompt.lower()
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
    assert "Manual desktop and Microsoft Word checks belong to the user" in agent
    assert (Path(installed["path"]) / "skills" / "mynotes" / "SKILL.md").is_file()


def test_codex_chat_scopes_writes_and_receives_shared_instructions(tmp_path, monkeypatch):
    workspace, configuration = _setup(tmp_path)
    configuration["providers"]["configured"]["codex"] = {
        "model": "gpt-6.1-terra", "effort": "medium",
    }
    monkeypatch.setattr("mynotes.chat.shutil.which", lambda _: "/bin/codex")
    calls = []

    def run(command, **options):
        calls.append((command, options))
        # Codex rejects --add-dir under inherited read-only permissions.
        assert "--add-dir" not in command
        assert command[command.index("--sandbox") + 1] == "workspace-write"
        return type("Done", (), {"returncode": 0})()

    monkeypatch.setattr("mynotes.chat.subprocess.run", run)
    assert launch_chat(workspace, configuration, "codex", "Check my preferences") == 0
    command, options = calls[0]
    assert command[command.index("-C") + 1] == str(workspace.root)
    assert command[command.index("--model") + 1] == "gpt-6.1-terra"
    assert 'model_reasoning_effort="medium"' in command
    assert not any("dangerously" in flag for flag in command)
    assert CORE not in command[-1]
    assert CORE in (workspace.root / "AGENTS.md").read_text()
    assert "load the relevant activity context first" in command[-1]
    assert options["env"]["MYNOTES_CHAT_ACTIVE"] == "1"
    assert options["env"]["MYNOTES_WORKSPACE"] == str(workspace.root)
    integration = install_integration("agy", tmp_path / "comparison")
    agent = (Path(integration["path"]) / "agents/mynotes.md").read_text()
    assert CORE in agent


def test_all_providers_share_rules_and_startup_prompt(tmp_path, monkeypatch):
    workspace, configuration = _setup(tmp_path)
    configuration["personal"]["interface_language"] = "it"
    prior_integration = install_integration("agy", workspace.root)
    agent = Path(prior_integration["path"]) / "agents/mynotes.md"
    assert CORE in agent.read_text()
    calls = []
    monkeypatch.setattr("mynotes.chat.shutil.which", lambda name: f"/bin/{name}")
    monkeypatch.setattr("mynotes.chat.subprocess.run", lambda command, **kwargs:
                        calls.append(command) or type("Done", (), {"returncode": 0})())

    assert launch_chat(workspace, configuration, "codex") == 0
    assert launch_chat(workspace, configuration, "agy") == 0
    assert launch_chat(workspace, configuration, "claude") == 0
    assert calls[0][-1] == calls[1][-1] == calls[2][-1]
    assert "Ciao! Sono pronto ad aiutarti con MyNotes." in calls[0][-1]
    assert CORE not in calls[0][-1]
    assert CORE in (workspace.root / "AGENTS.md").read_text()
    agent_text = agent.read_text()
    assert CORE not in agent_text
    assert "AGENTS.md" in agent_text
    assert "commandExecutionPolicy: sandbox" in agent_text
    assert "--mode" in calls[1] and "accept-edits" in calls[1]
    assert calls[2][calls[2].index("--append-system-prompt-file") + 1] == str(workspace.root / "AGENTS.md")
    assert calls[2][calls[2].index("--permission-mode") + 1] == "acceptEdits"


@pytest.mark.parametrize("name", ["AGENTS.md", "AGENTS.override.md", "GEMINI.md"])
def test_custom_workspace_rules_keep_legacy_startup(tmp_path, monkeypatch, name):
    workspace, configuration = _setup(tmp_path)
    custom = workspace.root / name
    custom.write_text("# Local guidance\n")
    calls = []
    monkeypatch.setattr("mynotes.chat.shutil.which", lambda provider: f"/bin/{provider}")
    monkeypatch.setattr("mynotes.chat.subprocess.run", lambda command, **kwargs:
                        calls.append(command) or type("Done", (), {"returncode": 0})())

    assert launch_chat(workspace, configuration, "codex") == 0
    assert launch_chat(workspace, configuration, "agy") == 0
    assert launch_chat(workspace, configuration, "claude") == 0
    assert custom.read_text() == "# Local guidance\n"
    assert CORE in calls[0][-1]
    assert CORE in (workspace.root / ".agents/plugins/mynotes/agents/mynotes.md").read_text()
    fallback = workspace.root / ".claude/skills/mynotes/SKILL.md"
    assert calls[2][calls[2].index("--append-system-prompt-file") + 1] == str(fallback)
    assert CORE in fallback.read_text()


def test_shared_rules_upgrade_and_preserve_local_edits(tmp_path, monkeypatch):
    assert install_shared_chat_rules(tmp_path)
    rules = tmp_path / "AGENTS.md"
    monkeypatch.setattr("mynotes.integrations.CORE", "New common rules.\n")
    assert install_shared_chat_rules(tmp_path)
    assert rules.read_text().endswith("New common rules.\n")

    edited = rules.read_text() + "Personal rule.\n"
    rules.write_text(edited)
    assert not install_shared_chat_rules(tmp_path)
    assert rules.read_text() == edited


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
    assert "do not start" in CORE and "pending-course questionnaires before completing" in CORE
    prompt += task_context(workspace.configuration_store().load(), "notes")["instructions"]
    assert "Optional course context never" in prompt
    assert "no extra approval" in prompt and "review mode" in prompt


def test_installed_integrations_share_operational_priority(tmp_path):
    for platform in ("codex", "agy", "claude"):
        installed = install_integration(platform, tmp_path)
        for name in (["agents/mynotes.md", "skills/mynotes/SKILL.md"] if platform == "agy" else ["SKILL.md"]):
            content = (Path(installed["path"]) / name).read_text()
            assert "context --activity" in content
            assert len(content.split()) < 900
    notes = instruction_module("notes")
    assert "After completing the operational request" in notes
    assert "pending-course questionnaires before completing" in CORE
