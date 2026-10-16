from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mynotes.chat import launch_chat
from mynotes.cli import main
from mynotes.integrations import install_integration
from mynotes.providers import (
    AgyAdapter, ClaudeAdapter, CodexAdapter, ProviderError, ProviderQuotaError,
    ProviderRequest, RequestedModel, invoke_provider,
)
from mynotes.workspace import Workspace
from mynotes.workflow import resolve_model


def request(tmp_path, provider, model="", effort=""):
    return ProviderRequest("OPS-L01", {"name": "Operations"}, {},
                           RequestedModel(provider, model, effort, "standard"), run_dir=tmp_path / "run")


@pytest.mark.parametrize("provider,adapter", [("codex", CodexAdapter), ("agy", AgyAdapter), ("claude", ClaudeAdapter)])
def test_defaults_omit_overrides_and_do_not_certify_unreported_settings(tmp_path, monkeypatch, provider, adapter):
    monkeypatch.setattr("mynotes.providers.shutil.which", lambda _: f"/bin/{provider}")
    captured = []
    def run(command, **kwargs):
        captured.append((command, kwargs))
        if provider == "codex":
            Path(command[command.index("--output-last-message") + 1]).write_text('{"title":"test"}')
            output = '{"type":"turn.completed","usage":{"output_tokens":3}}\n'
        else:
            output = json.dumps({"status": "SUCCESS", "subtype": "success", "structured_output": {"title": "test"}})
        return subprocess.CompletedProcess(command, 0, output, "")
    monkeypatch.setattr("mynotes.providers._run_agy_worker" if provider == "agy" else "mynotes.providers.subprocess.run", run)
    response = invoke_provider(request(tmp_path, provider), adapter())
    command = captured[0][0]
    assert "--model" not in command and "--effort" not in command
    assert not any("model_reasoning_effort" in item for item in command)
    assert response.model == response.effort == ""
    saved = json.loads(Path(response.attempt_path).read_text())
    assert saved["requested"]["model"] == ""
    assert saved["stdout"]


def test_claude_structured_transport_preserves_actual_model_usage_and_requested_override(tmp_path, monkeypatch):
    monkeypatch.setattr("mynotes.providers.shutil.which", lambda _: "/bin/claude")
    commands = []
    envelope = {"type": "result", "subtype": "success", "is_error": False,
                "structured_output": {"title": "test"}, "modelUsage": {"actual-model-id": {"inputTokens": 2}},
                "usage": {"output_tokens": 4}, "total_cost_usd": 0.002}
    def run(command, **kwargs):
        commands.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, json.dumps(envelope), "")
    monkeypatch.setattr("mynotes.providers.subprocess.run", run)
    response = ClaudeAdapter().generate(request(tmp_path, "claude", "requested-alias", "high"))
    command, options = commands[0]
    assert command[command.index("--model") + 1] == "requested-alias"
    assert command[command.index("--effort") + 1] == "high"
    assert "--json-schema" in command and "--no-session-persistence" in command
    assert command[command.index("--permission-mode") + 1] == "plan"
    assert options["cwd"] == str((tmp_path / "run").resolve())
    assert response.model == "actual-model-id" and response.effort == ""
    assert response.usage["total_cost_usd"] == 0.002
    attempt = json.loads(Path(response.attempt_path).read_text())
    assert attempt["requested"]["model"] == "requested-alias"
    assert attempt["envelope"] == envelope


@pytest.mark.parametrize("returncode,output,error", [
    (1, "", "quota reached"),
    (0, '{"subtype":"error_during_execution","is_error":true,"error":"rate limit"}', ""),
])
def test_claude_quota_is_saved_and_never_accepted(tmp_path, monkeypatch, returncode, output, error):
    monkeypatch.setattr("mynotes.providers.shutil.which", lambda _: "/bin/claude")
    monkeypatch.setattr("mynotes.providers.subprocess.run", lambda command, **_: subprocess.CompletedProcess(command, returncode, output, error))
    with pytest.raises(ProviderQuotaError):
        ClaudeAdapter().generate(request(tmp_path, "claude"))
    attempts = list((tmp_path / "run" / "provider-attempts").glob("*.json"))
    assert len(attempts) == 1 and json.loads(attempts[0].read_text())["error"]


@pytest.mark.parametrize("output", ["not JSON", "[]", '{"subtype":"success","result":"free prose"}'])
def test_claude_malformed_transport_preserves_failure_attempt(tmp_path, monkeypatch, output):
    monkeypatch.setattr("mynotes.providers.shutil.which", lambda _: "/bin/claude")
    monkeypatch.setattr("mynotes.providers.subprocess.run", lambda command, **_: subprocess.CompletedProcess(command, 0, output, ""))
    with pytest.raises(ProviderError):
        ClaudeAdapter().generate(request(tmp_path, "claude"))
    attempt = next((tmp_path / "run" / "provider-attempts").glob("*.json"))
    assert json.loads(attempt.read_text())["stdout"] == output


@pytest.mark.parametrize("provider", ["codex", "agy", "claude"])
def test_chat_launches_defaults_in_private_workspace_with_managed_instructions(tmp_path, monkeypatch, provider):
    monkeypatch.delenv("MYNOTES_CHAT_ACTIVE", raising=False)
    workspace = Workspace.from_value(tmp_path / "private"); workspace.initialize()
    configuration = workspace.configuration_store().load()
    configuration["providers"]["default"] = provider
    configuration["providers"]["configured"][provider] = {}
    monkeypatch.setattr("mynotes.chat.shutil.which", lambda _: f"/bin/{provider}")
    calls = []
    monkeypatch.setattr("mynotes.chat.subprocess.run", lambda command, **kwargs: calls.append((command, kwargs)) or subprocess.CompletedProcess(command, 0))
    assert launch_chat(workspace, configuration, None) == 0
    command, options = calls[0]
    assert options["cwd"] == workspace.root
    assert options["env"]["MYNOTES_WORKSPACE"] == str(workspace.root)
    assert str(Path.home() / ".local/bin") in options["env"]["PATH"]
    if provider == "claude":
        assert "--model" not in command and "--effort" not in command
    else:
        expected_model = "gpt-5.6-terra" if provider == "codex" else "gemini-3.8-flash-high"
        assert command[command.index("--model") + 1] == expected_model
        if provider == "codex":
            assert 'model_reasoning_effort="high"' in command
        else:
            assert command[command.index("--effort") + 1] == "high"
    assert "three-line welcome" in command[-1]
    assert "Do not ask course names or lesson numbers yet" in command[-1]
    root = workspace.root / (".claude/skills" if provider == "claude" else ".agents/plugins" if provider == "agy" else ".agents/skills") / "mynotes"
    assert root.is_dir()
    if provider == "claude":
        assert command[command.index("--append-system-prompt-file") + 1] == str(root / "SKILL.md")


def test_claude_integration_is_managed_and_protects_edits(tmp_path):
    from mynotes.integrations import IntegrationError
    installed = install_integration("claude", tmp_path)
    skill = Path(installed["path"]) / "SKILL.md"
    assert "course add --name" in skill.read_text()
    skill.write_text("user edit")
    with pytest.raises(IntegrationError, match="edited locally"):
        install_integration("claude", tmp_path)


def test_bare_mynotes_opens_selected_agent_before_courses_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = Workspace.from_value(tmp_path / "private"); workspace.initialize()
    monkeypatch.setenv("MYNOTES_WORKSPACE", str(workspace.root))
    configuration = workspace.configuration_store().load()
    configuration["providers"]["default"] = "claude"
    configuration["providers"]["configured"]["claude"] = {}
    workspace.configuration_store().save(configuration)
    calls = []
    monkeypatch.setattr("mynotes.cli.launch_chat", lambda workspace, configuration, provider: calls.append(configuration) or 0)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("bare launch must not ask the old questionnaire"))
    assert main([]) == 0
    assert calls[0]["providers"]["default"] == "claude"
    assert calls[0]["courses"] == []
    assert resolve_model(calls[0], None, "standard").model == ""


def test_simple_course_cli_uses_defaults_and_preserves_sources(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MYNOTES_HOME", str(tmp_path / "managed"))
    workspace = Workspace.from_value(tmp_path / "private"); workspace.initialize()
    materials = tmp_path / "existing materials"; materials.mkdir()
    original = materials / "slide.txt"; original.write_text("preserve this")
    recordings = tmp_path / "existing recordings"; recordings.mkdir()
    assert main(["course", "add", "--workspace", str(workspace.root), "--name", "Operations Analytics",
                 "--notes-language", "it", "--materials", str(materials), "--recordings", str(recordings)]) == 0
    course = json.loads(capsys.readouterr().out)
    assert course["name"] == "Operations Analytics"
    assert course["languages"]["notes"] == "it"
    assert course["folders"]["materials"] == str(materials)
    assert original.read_text() == "preserve this"
    configuration = workspace.configuration_store().load()
    assert configuration["style"]["quality"] == "standard"
    assert str(recordings) in configuration["paths"]["recording_dirs"]
    assert main(["course", "add", "--workspace", str(workspace.root), "--name", "Operations Analytics"]) == 0
    assert len(workspace.configuration_store().load()["courses"]) == 1


def test_doctor_json_is_read_only_and_exposes_actionable_component_states(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MYNOTES_HOME", str(tmp_path / "managed"))
    workspace = Workspace.from_value(tmp_path / "uninitialized")
    assert main(["doctor", "--json", "--workspace", str(workspace.root)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ready"] is False
    assert set(result["checks"]) == {"installation", "agent", "transcription", "word", "capture"}
    assert all(check["action"] for check in result["checks"].values())
    assert not workspace.root.exists()


def test_setup_cli_dispatches_the_selected_workspace_and_provider(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("mynotes.onboarding.setup_cli", lambda workspace, provider=None: calls.append((workspace.root, provider)) or 0)
    assert main(["setup", "--workspace", str(tmp_path / "private"), "--provider", "claude"]) == 0
    assert calls == [((tmp_path / "private").resolve(), "claude")]


@pytest.mark.parametrize("message", ["Configura il mio corso", "Che cosa manca a OPS-L01?"])
def test_provider_alias_routes_non_generation_request_to_chat(tmp_path, monkeypatch, message):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    calls = []
    monkeypatch.setattr("mynotes.cli.launch_chat", lambda workspace, configuration, provider, message=None: calls.append((provider, message)) or 0)
    monkeypatch.setattr("mynotes.cli._generate", lambda *_args, **_kwargs: pytest.fail("read-only or setup requests must not generate"))
    assert main(["claude", message, "--workspace", str(tmp_path / "private")]) == 0
    assert calls == [("claude", message)]


def test_doctor_provider_override_does_not_change_saved_selection(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MYNOTES_HOME", str(tmp_path / "managed"))
    workspace = Workspace.from_value(tmp_path / "private"); workspace.initialize()
    configuration = workspace.configuration_store().load()
    configuration["providers"]["default"] = "codex"
    configuration["providers"]["configured"]["codex"] = {}
    workspace.configuration_store().save(configuration)
    original = workspace.configuration_path.read_bytes()
    observed = []
    def check(provider):
        observed.append(provider)
        return {"status": "ready", "provider": provider, "action": "Open the chat."}
    monkeypatch.setattr("mynotes.onboarding.provider_check", check)
    assert main(["doctor", "--json", "--workspace", str(workspace.root), "--provider", "claude"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["checks"]["agent"]["provider"] == "claude"
    assert observed == ["claude"]
    assert workspace.configuration_path.read_bytes() == original


def test_source_exports_match_current_managed_generic_instructions():
    from mynotes.integrations import CODEX_SKILL, AGY_AGENT, _verify_managed
    repository = Path(__file__).resolve().parents[1]
    for relative in (".agents/skills/mynotes", ".agents/plugins/mynotes"):
        root = repository / relative
        _verify_managed(root, json.loads((root / ".mynotes-integration.json").read_text()))
    assert (repository / ".agents/skills/mynotes/SKILL.md").read_text() == CODEX_SKILL
    assert (repository / ".agents/plugins/mynotes/agents/mynotes.md").read_text() == AGY_AGENT
    assert "explicit user approval" in CODEX_SKILL and "explicit user approval" in AGY_AGENT
