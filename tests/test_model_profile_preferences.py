from pathlib import Path
import json
import subprocess

import pytest

from mynotes.chat import launch_chat
from mynotes.cli import main
from mynotes.configuration import ConfigurationError
from mynotes.onboarding import add_first_course, prepare_conversation, prepare_setup_preferences, restore_setup_preferences
from mynotes.preferences import save_background, save_preferences
from mynotes.workspace import Workspace
from mynotes.workflow import resolve_model


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MYNOTES_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("MYNOTES_USER_HOME", str(tmp_path / "user"))
    monkeypatch.delenv("MYNOTES_CHAT_ACTIVE", raising=False)
    return Workspace(tmp_path / "private")


@pytest.mark.parametrize("provider", ["codex", "agy", "claude"])
def test_saved_model_reaches_both_chat_and_notes_and_default_clears_override(workspace, monkeypatch, provider):
    prepare_conversation(workspace, provider)
    save_preferences(workspace, provider, "chosen-model", "it")
    save_background(workspace, "Studio economia; voglio capire i concetti.")
    settings = workspace.configuration_store().load()
    assert resolve_model(settings, provider, "standard").model == "chosen-model"
    calls = []
    monkeypatch.setattr("mynotes.chat.shutil.which", lambda *args, **kwargs: "/bin/" + provider)
    monkeypatch.setattr("mynotes.chat.subprocess.run", lambda cmd, **kwargs: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    launch_chat(workspace, settings, provider)
    assert calls[0][calls[0].index("--model") + 1] == "chosen-model"
    assert "Studio economia" in calls[0][-1]
    assert "Do not repeat the background question" in calls[0][-1]
    save_preferences(workspace, provider, "", "it")
    settings = workspace.configuration_store().load()
    assert resolve_model(settings, provider, "standard").model == ""
    launch_chat(workspace, settings, provider)
    assert "--model" not in calls[-1]


def test_provider_switch_keeps_each_choice_and_unrelated_user_data(workspace):
    prepare_conversation(workspace, "codex")
    save_background(workspace, "Original wording")
    course = add_first_course(workspace, "Quantitative Finance", "it")
    save_preferences(workspace, "codex", "first-model", "it")
    save_preferences(workspace, "claude", "sonnet", "en")
    settings = prepare_conversation(workspace, "codex")
    assert settings["personal"]["background"] == "Original wording"
    assert settings["providers"]["configured"]["codex"]["model"] == "first-model"
    assert settings["providers"]["configured"]["claude"]["model"] == "sonnet"
    assert settings["courses"] == [course]
    assert settings["onboarding"]["status"] == "complete"


def test_old_empty_setup_prompts_background_but_existing_course_is_preserved(workspace):
    settings = prepare_conversation(workspace, "codex")
    settings["onboarding"]["completed_groups"].append("personal")
    settings["onboarding"]["next_group"] = "courses"
    workspace.configuration_store().save(settings)
    repaired = prepare_conversation(workspace)
    assert repaired["onboarding"]["next_group"] == "personal"
    add_first_course(workspace, "Existing course")
    assert prepare_conversation(workspace)["onboarding"]["status"] == "complete"


def test_skip_is_persistent_and_does_not_erase_profile(workspace):
    prepare_conversation(workspace, "codex")
    save_background(workspace, "", skip=True)
    assert prepare_conversation(workspace)["onboarding"]["next_group"] == "courses"
    save_background(workspace, "Preserve me")
    save_background(workspace, "", skip=True)
    assert workspace.configuration_store().load()["personal"]["background"] == "Preserve me"


def test_cli_preferences_and_background_preserve_original_wording(workspace, capsys):
    prepare_conversation(workspace, "codex")
    assert main(["preferences", "--workspace", str(workspace.root), "--provider", "codex", "--model", "a-model", "--language", "it"]) == 0
    assert json.loads(capsys.readouterr().out)["model"] == "a-model"
    assert main(["profile", "--workspace", str(workspace.root), "--background", "Studio matematica. Preferisco esempi!"]) == 0
    assert json.loads(capsys.readouterr().out)["background"] == "Studio matematica. Preferisco esempi!"
    assert workspace.configuration_store().load()["onboarding"]["next_group"] == "courses"


def test_first_prompt_has_three_lines_and_one_question_before_courses(workspace, monkeypatch):
    settings = prepare_conversation(workspace, "codex")
    save_preferences(workspace, "codex", "", "it")
    calls = []
    monkeypatch.setattr("mynotes.chat.shutil.which", lambda *args, **kwargs: "/bin/codex")
    monkeypatch.setattr("mynotes.chat.subprocess.run", lambda cmd, **kwargs: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    launch_chat(workspace, workspace.configuration_store().load(), "codex")
    prompt = calls[0][-1]
    assert "Benvenuto in MyNotes" in prompt
    assert "qual è il tuo background" in prompt
    assert "Do not ask course names or lesson numbers yet" in prompt
    assert "ask the first course name" not in prompt
    assert "do not run tools" in prompt


@pytest.mark.parametrize("model", ["--help", "invalid model", "model\nother", "a" * 201])
def test_invalid_model_does_not_write(workspace, model):
    prepare_conversation(workspace, "codex")
    before = workspace.configuration_path.read_bytes()
    with pytest.raises(ConfigurationError):
        save_preferences(workspace, "codex", model)
    assert workspace.configuration_path.read_bytes() == before


def test_setup_undo_does_not_remove_a_new_background_answer(workspace):
    prepare_conversation(workspace, "codex")
    receipt = prepare_setup_preferences(workspace, "claude")
    save_background(workspace, "Answer saved after setup")
    assert not restore_setup_preferences(workspace, receipt)
    assert workspace.configuration_store().load()["personal"]["background"] == "Answer saved after setup"


@pytest.mark.parametrize("provider,model", [("codex", "gpt-5.6-terra"), ("agy", "gemini-3.8-flash-medium"), ("claude", "saved-claude-model")])
def test_saved_effort_reaches_chat_and_notes_without_changing_model(workspace, monkeypatch, provider, model):
    prepare_conversation(workspace, provider)
    save_preferences(workspace, provider, model, effort="high")
    settings = workspace.configuration_store().load()
    requested = resolve_model(settings, provider, "standard")
    assert (requested.model, requested.effort) == (model, "high")
    calls = []
    monkeypatch.setattr("mynotes.chat.shutil.which", lambda *args, **kwargs: "/bin/" + provider)
    monkeypatch.setattr("mynotes.chat.subprocess.run", lambda cmd, **kwargs: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    launch_chat(workspace, settings, provider)
    assert calls[0][calls[0].index("--model") + 1] == model
    if provider == "codex":
        assert 'model_reasoning_effort="high"' in calls[0]
    else:
        assert calls[0][calls[0].index("--effort") + 1] == "high"
    # Older callers not supplying effort preserve the user's explicit effort.
    save_preferences(workspace, provider, model, "it")
    assert workspace.configuration_store().load()["providers"]["configured"][provider]["effort"] == "high"
