import json
import subprocess
from pathlib import Path

import pytest

from mynotes import onboarding
from mynotes.workspace import Workspace


@pytest.fixture
def machine(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("MYNOTES_HOME", str(tmp_path / "runtime"))
    workspace = Workspace(tmp_path / "workspace")
    return workspace


def test_machine_defaults_leave_profile_pending_and_preserve(machine):
    config = onboarding.prepare_conversation(machine, "codex")
    assert config["onboarding"]["next_group"] == "personal"
    assert config["providers"]["configured"]["codex"] == {}
    config["personal"]["institution"] = "Keep me"
    config["style"]["quality"] = "approfondito"
    machine.configuration_store().save(config)
    resumed = onboarding.prepare_conversation(machine, "codex")
    assert resumed["personal"]["institution"] == "Keep me"
    assert resumed["style"]["quality"] == "approfondito"
    assert resumed["courses"] == []


def test_first_course_minimal_idempotent(machine):
    onboarding.prepare_conversation(machine, "codex")
    from mynotes.preferences import save_background
    save_background(machine, "I study finance and need clear explanations.")
    first = onboarding.add_first_course(machine, "Quantitative Finance", "it")
    second = onboarding.add_first_course(machine, "Quantitative Finance", "it")
    assert first == second
    settings = machine.configuration_store().load()
    assert len(settings["courses"]) == 1
    assert settings["onboarding"]["status"] == "complete"
    assert first["languages"]["notes"] == "it"
    assert first["folders"]["handout"].endswith("Handouts")


def test_agent_missing_has_official_install_action(monkeypatch):
    monkeypatch.setattr(onboarding.shutil, "which", lambda _, **kwargs: None)
    check = onboarding.provider_check("claude")
    assert check["status"] == "missing"
    assert check["install_url"] == onboarding.INSTALL_URLS["claude"]


def test_auth_detection_does_not_send_generation(monkeypatch):
    monkeypatch.setattr(onboarding.shutil, "which", lambda _: "/test/codex")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if "--help" in command:
            text = "--add-dir --cd --output-schema --output-last-message --json --ephemeral --sandbox --skip-git-repo-check"
        else:
            text = "Logged in using ChatGPT" if "login" in command else "codex 1.0"
        return subprocess.CompletedProcess(command, 0, text, "")
    monkeypatch.setattr(onboarding.subprocess, "run", run)
    assert onboarding.provider_check("codex")["status"] == "ready"
    assert calls == [["/test/codex", "--version"], ["/test/codex", "--help"], ["/test/codex", "exec", "--help"], ["/test/codex", "login", "status"]]


def test_agy_auth_remains_unverified(monkeypatch):
    monkeypatch.setattr(onboarding.shutil, "which", lambda _: "/test/agy")
    monkeypatch.setattr(onboarding.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "--agent --mode --add-dir --prompt-interactive --print --output-format --json-schema" if "--help" in cmd else "agy 1.0", ""))
    assert onboarding.provider_check("agy")["status"] == "unverified"


def test_doctor_read_only_and_capture_unverified(machine, monkeypatch):
    onboarding.prepare_conversation(machine, "codex")
    before = machine.configuration_path.read_bytes()
    monkeypatch.setattr(onboarding.shutil, "which", lambda _, **kwargs: None)
    report = onboarding.doctor(machine)
    assert machine.configuration_path.read_bytes() == before
    assert not report["ready"]
    assert report["checks"]["capture"]["status"] == "optional"
    assert report["checks"]["transcription"]["execution_verified"] is False


def test_capture_confirmation_requires_real_test_and_listening(machine):
    machine.initialize()
    with pytest.raises(ValueError, match="Listen"):
        onboarding.confirm_capture_audio(machine, Path("/test/audio.m4a"), audible=False)
    with pytest.raises(ValueError, match="completed microphone test"):
        onboarding.confirm_capture_audio(machine, Path("/test/audio.m4a"), audible=True)


@pytest.mark.parametrize("provider", ["codex", "claude", "agy"])
def test_old_provider_missing_capabilities_requires_update_not_login(monkeypatch, provider):
    monkeypatch.setattr(onboarding.shutil, "which", lambda _: f"/test/{provider}")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "--help --version" if "--help" in command else "old 0.1", "")
    monkeypatch.setattr(onboarding.subprocess, "run", run)
    check = onboarding.provider_check(provider)
    assert check["status"] == "incompatible"
    assert check["compatibility_verified"] is False
    assert check["missing_capabilities"]
    assert "Update" in check["action"]
    assert not any("auth" in cmd or "login" in cmd for cmd in calls)
    assert all("--help" in cmd or "--version" in cmd for cmd in calls)


def test_partial_help_flag_does_not_satisfy_required_capability(monkeypatch):
    monkeypatch.setattr(onboarding.shutil, "which", lambda _: "/test/agy")
    monkeypatch.setattr(onboarding.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "--agent-old --mode --add-dir --prompt-interactive --print --output-format --json-schema" if "--help" in cmd else "1.0", ""))
    check = onboarding.provider_check("agy")
    assert check["status"] == "incompatible"
    assert check["missing_capabilities"] == ["--agent"]


def test_compatible_claude_without_auth_requires_sign_in(monkeypatch):
    monkeypatch.setattr(onboarding.shutil, "which", lambda _: "/test/claude")
    def run(command, **kwargs):
        if "--help" in command:
            text = "--print --output-format --permission-mode --no-session-persistence --json-schema --append-system-prompt-file --add-dir"
        elif "auth" in command:
            text = '{"loggedIn": false}'
        else:
            text = "Claude 2.0"
        return subprocess.CompletedProcess(command, 0, text, "")
    monkeypatch.setattr(onboarding.subprocess, "run", run)
    check = onboarding.provider_check("claude")
    assert check["status"] == "login_required"
    assert check["compatibility_verified"] is True



def test_repeated_chat_preparation_does_not_create_history(machine):
    onboarding.prepare_conversation(machine, "codex")
    before = list(machine.configuration_history_dir.glob("*.json"))
    settings = machine.configuration_path.read_bytes()
    for _ in range(20):
        onboarding.prepare_conversation(machine)
    assert list(machine.configuration_history_dir.glob("*.json")) == before
    assert machine.configuration_path.read_bytes() == settings


def test_setup_launch_propagates_selected_provider_to_native(machine, monkeypatch):
    calls = []
    monkeypatch.setattr(onboarding.subprocess, "run", lambda cmd,**kw: calls.append(cmd) or subprocess.CompletedProcess(cmd,0))
    assert onboarding.setup_cli(machine, "claude") == 0
    assert "MYNOTES_PROVIDER=claude" in calls[-1]
    assert f"MYNOTES_WORKSPACE={machine.root}" in calls[-1]


def test_concurrent_course_additions_preserve_both_courses(machine):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    onboarding.prepare_conversation(machine, "codex")
    barrier = threading.Barrier(2)
    def add(name):
        barrier.wait(timeout=5)
        return onboarding.add_first_course(machine, name)
    with ThreadPoolExecutor(max_workers=2) as pool:
        added = list(pool.map(add, ["Alpha", "Beta"]))
    assert {course["name"] for course in added} == {"Alpha", "Beta"}
    assert {course["name"] for course in machine.configuration_store().load()["courses"]} == {"Alpha", "Beta"}


def test_atomic_update_exception_leaves_settings_and_lock_usable(machine):
    from mynotes.configuration import ConfigurationError
    onboarding.prepare_conversation(machine, "codex")
    store = machine.configuration_store()
    before = machine.configuration_path.read_bytes()
    def invalid(settings):
        settings["providers"]["default"] = "unsupported"
        return settings
    with pytest.raises(ConfigurationError):
        store.update(invalid)
    assert machine.configuration_path.read_bytes() == before
    updated = store.update(lambda settings: {**settings, "personal": {**settings["personal"], "institution": "Updated"}})
    assert updated["personal"]["institution"] == "Updated"


def test_concurrent_process_course_additions_preserve_both(machine, tmp_path, monkeypatch):
    import os
    import sys
    import time
    monkeypatch.setenv("MYNOTES_USER_HOME", str(tmp_path / "user"))
    onboarding.prepare_conversation(machine, "codex")
    gate = tmp_path / "start"
    code = ('import sys,time;from pathlib import Path;from mynotes.onboarding import add_first_course;'
            'from mynotes.workspace import Workspace;'
            'Path(sys.argv[3]).write_text("ready");'
            '\nwhile not Path(sys.argv[4]).exists():time.sleep(.01)\n'
            'add_first_course(Workspace.from_value(sys.argv[1]),sys.argv[2])')
    children = []
    try:
        for name in ("Alpha", "Beta"):
            ready = tmp_path / (name + ".ready")
            children.append(subprocess.Popen([sys.executable, "-c", code, str(machine.root), name, str(ready), str(gate)], env=dict(os.environ), stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        deadline = time.monotonic() + 10
        while len(list(tmp_path.glob("*.ready"))) != 2 and time.monotonic() < deadline:
            time.sleep(.01)
        assert len(list(tmp_path.glob("*.ready"))) == 2
        gate.write_text("start")
        for process in children:
            _, error = process.communicate(timeout=10)
            assert process.returncode == 0, error
    finally:
        for process in children:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
    assert {course["name"] for course in machine.configuration_store().load()["courses"]} == {"Alpha", "Beta"}


def test_failed_setup_undo_restores_only_preferences_preserving_courses(machine):
    onboarding.prepare_conversation(machine, "codex")
    onboarding.add_first_course(machine, "Alpha")
    original = machine.configuration_store().load()
    receipt = onboarding.prepare_setup_preferences(machine, "claude")
    assert set(receipt["before"]) == {"providers", "paths", "onboarding"}
    assert machine.configuration_store().load()["providers"]["default"] == "claude"
    assert onboarding.restore_setup_preferences(machine, receipt) is True
    restored = machine.configuration_store().load()
    assert restored["providers"] == original["providers"]
    assert restored["courses"] == original["courses"]


def test_failed_setup_undo_preserves_later_user_edits_and_courses(machine):
    onboarding.prepare_conversation(machine, "codex")
    onboarding.add_first_course(machine, "Alpha")
    receipt = onboarding.prepare_setup_preferences(machine, "claude")
    onboarding.add_first_course(machine, "Beta")
    edited = machine.configuration_path.read_bytes()
    assert onboarding.restore_setup_preferences(machine, receipt) is False
    assert machine.configuration_path.read_bytes() == edited
    assert {course["name"] for course in machine.configuration_store().load()["courses"]} == {"Alpha", "Beta"}


@pytest.mark.parametrize("payload", [[], None, {"execution_proof": []}, {"execution_proof": None}, {"model_cache": []}, {"model_cache": None}, {"argmax": 1}, {"soffice": {}}])
def test_doctor_malformed_registry_is_readonly_and_actionable(machine, monkeypatch, payload):
    onboarding.prepare_conversation(machine, "codex")
    registry = onboarding.runtime_home() / "tools.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps(payload))
    original = registry.read_bytes()
    monkeypatch.setattr(onboarding.shutil, "which", lambda *args,**kwargs: None)
    report = onboarding.doctor(machine)
    assert not report["ready"]
    assert report["managed_registry_error"]
    for component in ("transcription", "word"):
        assert report["checks"][component]["status"] == "missing"
        assert "repair" in report["checks"][component]["action"]
    assert registry.read_bytes() == original
    assert onboarding.managed_tools() == {}


@pytest.mark.parametrize("payload", [[], None, {"path": []}, {"path": None}])
def test_doctor_malformed_capture_evidence_is_readonly(machine, monkeypatch, payload):
    onboarding.prepare_conversation(machine, "codex")
    evidence = machine.state_dir / "capture/audio-readiness.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text(json.dumps(payload))
    original = evidence.read_bytes()
    monkeypatch.setattr(onboarding.shutil, "which", lambda *args,**kwargs: None)
    report = onboarding.doctor(machine)
    assert report["checks"]["capture"]["status"] == "optional"
    assert report["checks"]["capture"]["evidence_error"]
    assert "test" in report["checks"]["capture"]["action"]
    assert evidence.read_bytes() == original


@pytest.mark.parametrize("payload", [[], None, {"loggedIn": "yes"}])
def test_claude_malformed_auth_status_remains_unverified(monkeypatch, payload):
    monkeypatch.setattr(onboarding.shutil, "which", lambda *args,**kwargs: "/test/claude")
    def run(command, **kwargs):
        if "--help" in command:
            text = "--print --output-format --permission-mode --no-session-persistence --json-schema --append-system-prompt-file --add-dir"
        elif "auth" in command:
            text = json.dumps(payload)
        else:
            text = "Claude 2.0"
        return subprocess.CompletedProcess(command, 0, text, "")
    monkeypatch.setattr(onboarding.subprocess, "run", run)
    result = onboarding.provider_check("claude")
    assert result["status"] == "unverified"
    assert result["authentication_error"]
    assert result["compatibility_verified"] is True


@pytest.mark.parametrize("combined,expected", [
    ("--append-system-prompt[-file],", "login_required"),
    ("--append-system-prompt", "incompatible"),
    ("--append-system-prompt[-files],", "incompatible"),
])
def test_claude_current_hidden_flag_help_spelling(combined, expected, monkeypatch):
    monkeypatch.setattr(onboarding.shutil, "which", lambda *args, **kwargs: "/test/claude")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if "--help" in command:
            # Observed Claude Code 2.1.296 help: the -file option is hidden,
            # but described in the --bare section with this combined spelling.
            text = "--print --output-format --permission-mode --no-session-persistence --json-schema --add-dir " + combined
            return subprocess.CompletedProcess(command, 0, text, "")
        if "auth" in command:
            return subprocess.CompletedProcess(command, 1, '{"loggedIn": false, "authMethod": "none"}', "")
        return subprocess.CompletedProcess(command, 0, "2.1.296 (Claude Code)", "")
    monkeypatch.setattr(onboarding.subprocess, "run", run)
    result = onboarding.provider_check("claude")
    assert result["status"] == expected
    if expected == "login_required":
        assert result["compatibility_verified"] is True
        assert calls[-1] == ["/test/claude", "auth", "status"]
    else:
        assert result["missing_capabilities"] == ["--append-system-prompt-file"]
        assert not any("auth" in command for command in calls)
