"""User-visible preference lifecycles, not just prompt-string snapshots."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import subprocess
from threading import Barrier

import pytest

from mynotes.chat import launch_chat
from mynotes.cli import main
from mynotes.configuration import ConfigurationError, course_template, merge_course
from mynotes.context import CORE, instruction_module, task_context
from mynotes.memory import MemoryConflict, fingerprint, snapshot, update_memory, writing_configuration
from mynotes.workspace import Workspace


@pytest.fixture
def workspace(tmp_path):
    ws = Workspace(tmp_path / "private")
    ws.initialize()
    settings = ws.configuration_store().load()
    for code in ("OPS", "STAT"):
        course = course_template()
        course.update(code=code, name=code)
        settings["courses"].append(course)
    settings["providers"]["default"] = "codex"
    ws.configuration_store().save(settings)
    return ws


def read(ws, area="notes", code=None):
    return snapshot(ws.configuration_store().load(), scope="course" if code else "global", area=area, course=code)


def put(ws, area="notes", code=None, **options):
    return update_memory(ws, scope="course" if code else "global", area=area, course=code,
                         expected_revision=read(ws, area, code)["revision"], **options)


def test_named_rules_inherit_override_suppress_forget_and_reset(workspace):
    put(workspace, set_rules={"examples": "Use examples.", "flow": "Use connected prose."}, style={"writing_style": "detailed"})
    local = put(workspace, code="OPS", set_rules={"examples": "Use numerical examples.", "flow": ""}, style={"writing_style": "concise", "image_limit": 2})
    assert local["inherited"]["examples"] == "Use examples."
    assert local["effective"]["examples"] == "Use numerical examples."
    assert "flow" not in local["effective"]
    assert read(workspace, code="STAT")["effective"]["examples"] == "Use examples."
    assert local["style"]["effective"]["writing_style"] == "concise"
    forgotten = put(workspace, code="OPS", remove=["examples"], inherit=["writing_style"])
    assert forgotten["effective"]["examples"] == "Use examples."
    assert forgotten["style"]["effective"]["writing_style"] == "detailed"
    reset = put(workspace, code="OPS", reset=True)
    assert reset["own"] == {}
    assert reset["style"]["own"] == {}
    assert reset["style"]["effective"]["image_limit"] is None


def test_idempotence_history_and_stale_revision_preserve_latest(workspace):
    before = workspace.configuration_store().load()
    stale = read(workspace)["revision"]
    current = put(workspace, set_rules={"examples": "Use examples."})
    history = workspace.configuration_store().history()
    put(workspace, set_rules={"examples": "Use examples."})
    assert workspace.configuration_store().history() == history
    assert read(workspace)["revision"] == current["revision"]
    with pytest.raises(MemoryConflict, match="MEMORY_CONFLICT"):
        update_memory(workspace, scope="global", area="notes", expected_revision=stale,
                      set_rules={"examples": "Stale edit"})
    assert read(workspace)["effective"]["examples"] == "Use examples."
    workspace.configuration_store().restore(history[-1].name)
    assert workspace.configuration_store().load() == before


def test_concurrent_writers_cannot_silently_overwrite_each_other(workspace):
    revision = read(workspace)["revision"]
    barrier = Barrier(2)
    def save(text):
        barrier.wait()
        try:
            update_memory(workspace, scope="global", area="notes", expected_revision=revision, set_rules={"examples": text})
            return "saved"
        except MemoryConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, ["First", "Second"])) == ["conflict", "saved"]


def test_course_revision_includes_inherited_values_but_not_unrelated_dialogue(workspace):
    revision = read(workspace, code="OPS")["revision"]
    put(workspace, area="dialogue", set_rules={"questions": "Ask one question at a time."})
    assert read(workspace, code="OPS")["revision"] == revision
    put(workspace, set_rules={"examples": "Use examples."})
    with pytest.raises(MemoryConflict):
        update_memory(workspace, scope="course", course="OPS", area="notes", expected_revision=revision,
                      set_rules={"examples": "Numerical examples"})


def test_legacy_rules_remain_editable_without_model_migration(workspace):
    old = read(workspace)
    key = next(iter(old["own"]))
    updated = put(workspace, set_rules={key: "A replacement preference."})
    assert list(updated["own"].values()) == ["A replacement preference."]
    key = next(iter(updated["own"]))
    put(workspace, remove=[key])
    assert read(workspace)["effective"] == {}
    assert main(["configure", "--workspace", str(workspace.root), "--course", "OPS", "--instruction", "Use numerical examples."]) == 0
    assert main(["configure", "--workspace", str(workspace.root), "--course", "OPS", "--instruction", "Use numerical examples."]) == 0
    settings = workspace.configuration_store().load()
    assert settings["courses"][0]["preferences"]["persistent_instructions"] == ["Use numerical examples."]
    assert settings["style"]["freeform_preferences"] == ""


def test_course_override_stays_attached_when_legacy_global_text_changes(workspace):
    key = next(iter(read(workspace)["own"]))
    put(workspace, code="OPS", set_rules={key: "Use the course rule."})
    put(workspace, set_rules={key: "A new general preference."})
    assert read(workspace, code="OPS")["effective"][key] == "Use the course rule."
    _, style = writing_configuration(workspace.configuration_store().load(), "OPS")
    assert style["freeform_preferences"] == "Use the course rule."
    put(workspace, remove=[key])
    put(workspace, code="OPS", set_rules={key: "The course rule can still change."})
    assert read(workspace, code="OPS")["effective"][key] == "The course rule can still change."


def test_repeated_multiline_legacy_preference_is_not_appended_twice(workspace):
    args = ["configure", "--workspace", str(workspace.root), "--instruction", "Use examples.\nExplain every step."]
    assert main(args) == 0
    before = workspace.configuration_path.read_bytes()
    assert main(args) == 0
    assert workspace.configuration_path.read_bytes() == before


def test_partial_legacy_course_update_preserves_unmentioned_rules(workspace):
    course = workspace.configuration_store().load()["courses"][0]
    course["preferences"]["persistent_instructions"] = ["Use examples."]
    course["preferences"]["note_instructions"] = {"depth": "Detailed"}
    merged = merge_course(course, {"preferences": {"persistent_instructions": ["Few figures.", "Use examples."],
                                                  "note_instructions": {"questions": "Applied questions"}}})
    assert merged["preferences"]["persistent_instructions"] == ["Use examples.", "Few figures."]
    assert merged["preferences"]["note_instructions"] == {"depth": "Detailed", "questions": "Applied questions"}


def test_notes_snapshot_excludes_all_dialogue_and_keeps_run_options_temporary(workspace):
    put(workspace, set_rules={"examples": "Use numerical examples."})
    before = writing_configuration(workspace.configuration_store().load(), "OPS")
    put(workspace, area="dialogue", set_rules={"questions": "Ask one question at a time."})
    put(workspace, area="dialogue", code="OPS", set_rules={"questions": "Ask together for this course."})
    settings = workspace.configuration_store().load()
    assert fingerprint(writing_configuration(settings, "OPS")) == fingerprint(before)
    context = task_context(settings, "notes", "OPS")
    assert context["memory"]["dialogue"]["effective"]["questions"] == "Ask together for this course."
    _, temporary = writing_configuration(settings, "OPS", instructions="Only this lesson: explain all steps.", image_limit=0)
    assert temporary["lesson_instructions"].startswith("Only this lesson")
    assert temporary["image_limit"] == 0
    assert "Ask together" not in json.dumps(temporary)
    assert "lesson_instructions" not in read(workspace, code="OPS")


@pytest.mark.parametrize("options", [dict(scope="course", area="notes"), dict(scope="global", area="notes", course="OPS"),
                                      dict(scope="global", area="invalid"), dict(scope="course", area="notes", course="MISSING")])
def test_ambiguous_or_unknown_scope_never_writes(workspace, options):
    before = workspace.configuration_path.read_bytes()
    with pytest.raises(ConfigurationError):
        update_memory(workspace, expected_revision="stale", **options)
    assert workspace.configuration_path.read_bytes() == before


def test_cli_markdown_json_edits_context_and_reset_are_local(workspace, monkeypatch, capsys):
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("No provider needed"))
    base = ["--workspace", str(workspace.root), "--scope", "global", "--area", "dialogue"]
    assert main(["memory", "show", *base, "--json"]) == 0
    initial = json.loads(capsys.readouterr().out)
    assert main(["memory", "update", *base, "--expected-revision", initial["revision"], "--set", "questions", "One at a time.", "--json"]) == 0
    saved = json.loads(capsys.readouterr().out)
    assert main(["memory", "show", *base]) == 0
    assert "# MyNotes memory" in capsys.readouterr().out
    assert main(["context", "--workspace", str(workspace.root), "--activity", "course", "--course", "OPS", "--json"]) == 0
    context = json.loads(capsys.readouterr().out)
    assert context["memory"]["dialogue"]["effective"]["questions"] == "One at a time."
    assert main(["memory", "reset", *base, "--expected-revision", initial["revision"]]) == 2
    assert "MEMORY_CONFLICT" in capsys.readouterr().out
    assert main(["memory", "reset", *base, "--expected-revision", saved["revision"], "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["effective"] == {}


@pytest.mark.parametrize("provider", ["codex", "agy", "claude"])
@pytest.mark.parametrize("stage", ["empty", "profile", "course"])
def test_startup_is_small_and_never_reads_source_libraries(tmp_path, monkeypatch, provider, stage):
    ws = Workspace(tmp_path / "private"); ws.initialize()
    settings = ws.configuration_store().load()
    if stage != "empty":
        settings["personal"].update(background="I study economics.", background_confirmed=True)
    if stage == "course":
        course = course_template(); course.update(code="OPS", name="Operations")
        settings["courses"] = [course]
    settings["personal"]["dialogue_instructions"] = {"questions": "Ask one question at a time."}
    ws.configuration_store().save(settings)
    def forbidden(*a, **k):
        pytest.fail("Startup must not enumerate recordings, transcripts or Capture associations")
    for path in ("mynotes.capture_associations.pending", "mynotes.transcription_queue.library", "mynotes.transcript_library.scan", "mynotes.recording_library.discover"):
        monkeypatch.setattr(path, forbidden)
    monkeypatch.setattr("mynotes.chat.shutil.which", lambda *a, **k: "/synthetic/" + provider)
    monkeypatch.delenv("MYNOTES_CHAT_ACTIVE", raising=False)
    calls = []
    monkeypatch.setattr("mynotes.chat.subprocess.run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    launch_chat(ws, settings, provider)
    prompt = calls[0][-1]
    assert "Ask one question at a time." in prompt
    assert len(prompt.split()) < 1000
    assert "candidateCourses" not in prompt
    assert instruction_module("notes") not in prompt
    # Count the actual configured instruction channel, not just the argv string.
    if provider == "claude":
        from pathlib import Path
        core = Path(calls[0][calls[0].index("--append-system-prompt-file") + 1]).read_text()
    elif provider == "agy":
        core = (ws.root / ".agents/plugins/mynotes/agents/mynotes.md").read_text()
    else:
        core = (ws.root / ".agents/skills/mynotes/SKILL.md").read_text()
    assert (prompt + core).count(CORE) == 1
    assert len((prompt + core).split()) < 1000


def test_context_read_does_not_initialize_missing_workspace(tmp_path):
    ws = Workspace(tmp_path / "missing")
    assert main(["context", "--activity", "notes", "--workspace", str(ws.root)]) == 2
    assert not ws.root.exists()


def test_standalone_provider_can_bootstrap_core_once(workspace, capsys):
    args = ["context", "--workspace", str(workspace.root), "--activity", "preferences", "--json"]
    assert main([*args, "--include-core"]) == 0
    assert json.loads(capsys.readouterr().out)["common_instructions"] == CORE
    assert main(args) == 0
    assert "common_instructions" not in json.loads(capsys.readouterr().out)


def test_simple_folder_inventory_is_distinct_from_preparing_notes():
    assert 'mynotes inventory PATH' in CORE
    assert 'Do not load notes' in CORE
    assert 'For read-only discovery' in instruction_module('sources')
    assert 'If a required transcript is missing' in instruction_module('sources')


@pytest.mark.parametrize("patch", ["null", "[]", '{"image_limit":true}', '{"writing_style":"invented"}'])
def test_invalid_style_patch_is_rejected_without_writing(workspace, capsys, patch):
    before = workspace.configuration_path.read_bytes()
    assert main(["memory", "update", "--workspace", str(workspace.root), "--scope", "global", "--area", "notes",
                 "--expected-revision", read(workspace)["revision"], "--style-json", patch]) == 2
    assert workspace.configuration_path.read_bytes() == before
