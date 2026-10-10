from __future__ import annotations

import json
from zipfile import ZipFile

from mynotes.cli import main
from mynotes.configuration import (
    ConfigurationError,
    course_template,
    effective_style,
    merge_group_answers,
    provider_availability,
)
import pytest
from mynotes.utils import file_sha256
from mynotes.workspace import Workspace


def course(code: str = "QNT9") -> dict:
    value = course_template()
    value.update(
        {
            "code": code,
            "name": "Quantitative Methods",
            "aliases": ["quant methods"],
            "kind": "quantitative",
        }
    )
    value["folders"].update(
        {
            "materials": "/tmp/My Notes/Materiali",
            "personal_notes": "/tmp/My Notes/Appunti",
            "handout": "/tmp/My Notes/Dispense",
        }
    )
    return value


def save_group(workspace: Workspace, group: str, answers: dict) -> dict:
    store = workspace.configuration_store()
    updated = merge_group_answers(store.load(), group, answers)
    return store.save(updated)


def test_interrupted_onboarding_resumes_without_losing_answers(tmp_path) -> None:
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()

    first = save_group(
        workspace,
        "personal",
        {
            "interface_language": "it",
            "institution": None,
            "study_program": "MSc",
            "study_period": "2026",
            "primary_goal": "exam",
        },
    )
    assert first["onboarding"] == {
        "completed_groups": ["personal"],
        "next_group": "paths",
        "status": "in_progress",
    }

    resumed = workspace.configuration_store().load()
    assert resumed["personal"]["interface_language"] == "it"
    save_group(workspace, "paths", {"recording_dirs": ["/tmp/My Notes/Recordings"]})
    save_group(workspace, "courses", {"courses": []})
    save_group(workspace, "style", {"quality": "standard", "freeform_preferences": "Prefer examples."})
    completed = save_group(
        workspace,
        "providers",
        {
            "default": "agy",
            "configured": {"agy": {"model": "gemini-3.8-flash-high", "effort": "high"}},
        },
    )
    assert completed["onboarding"]["status"] == "complete"
    assert completed["personal"]["study_program"] == "MSc"


def test_course_unknown_to_code_can_be_added_with_accented_and_spaced_paths(tmp_path) -> None:
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()
    course_payload = course()
    course_file = tmp_path / "course.json"
    course_file.write_text(json.dumps(course_payload), encoding="utf-8")

    assert main(["course", "add", "--workspace", str(workspace.root), "--file", str(course_file)]) == 0
    stored = workspace.configuration_store().load()["courses"][0]
    assert stored["code"] == "QNT9"
    assert stored["folders"]["materials"].endswith("My Notes/Materiali")
    assert stored["aliases"] == ["quant methods"]


def test_configuration_changes_are_backed_up_and_restorable(tmp_path) -> None:
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()
    initial_hash = file_sha256(workspace.configuration_path)
    save_group(workspace, "paths", {"recording_dirs": ["/tmp/one"]})
    changed_hash = file_sha256(workspace.configuration_path)
    assert changed_hash != initial_hash
    history = workspace.configuration_store().history()
    assert history
    workspace.configuration_store().restore(history[0].name)
    assert file_sha256(workspace.configuration_path) == initial_hash


def test_course_change_does_not_regenerate_or_modify_existing_run_artifacts(tmp_path) -> None:
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()
    store = workspace.configuration_store()
    configuration = store.load()
    configuration["courses"] = [course()]
    store.save(configuration)
    existing_run = workspace.runs_dir / "QNT9-L01" / "transcript.json"
    existing_run.parent.mkdir(parents=True)
    existing_run.write_text('{"preserved": true}', encoding="utf-8")
    original_hash = file_sha256(existing_run)

    updated = store.load()
    updated["courses"][0]["style"]["examples"] = "more"
    store.save(updated)
    assert file_sha256(existing_run) == original_hash


def test_course_edit_merges_only_requested_fields(tmp_path) -> None:
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()
    configuration = workspace.configuration_store().load()
    configured_course = course()
    configured_course["assessment"] = "Written exam"
    configuration["courses"] = [configured_course]
    workspace.configuration_store().save(configuration)
    update_file = tmp_path / "update.json"
    update_file.write_text(json.dumps({"style": {"examples": "more"}}), encoding="utf-8")

    assert main(["course", "edit", "--workspace", str(workspace.root), "--file", str(update_file), "QNT9"]) == 0
    stored = workspace.configuration_store().load()["courses"][0]
    assert stored["assessment"] == "Written exam"
    assert stored["style"]["examples"] == "more"


def test_natural_language_persistence_is_course_scoped_and_lesson_request_is_not(tmp_path, capsys) -> None:
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()
    configuration = workspace.configuration_store().load()
    configuration["courses"] = [course()]
    workspace.configuration_store().save(configuration)

    assert main(
        [
            "configure",
            "--workspace",
            str(workspace.root),
            "--course",
            "QNT9",
            "--instruction",
            "Da ora in poi usa più esempi in questo corso.",
        ]
    ) == 0
    assert "più esempi" in workspace.configuration_store().load()["courses"][0]["preferences"]["persistent_instructions"][0]
    before = file_sha256(workspace.configuration_path)
    assert main(
        [
            "configure",
            "--workspace",
            str(workspace.root),
            "--instruction",
            "Per questa lezione usa più esempi.",
        ]
    ) == 0
    assert file_sha256(workspace.configuration_path) == before
    assert "Saved nowhere" in capsys.readouterr().out


def test_provider_detection_reports_unavailable_without_substitution(monkeypatch) -> None:
    monkeypatch.setattr("mynotes.configuration.shutil.which", lambda _: None)
    status = provider_availability("agy")
    assert status == {"provider": "agy", "available": False, "detail": "local launcher not detected"}


def test_coded_style_inheritance_and_course_edit_preserve_other_courses(tmp_path) -> None:
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()
    configuration = workspace.configuration_store().load()
    operations = course("OPS7")
    operations["aliases"] = ["operations"]
    configuration["courses"] = [course("QNT9"), operations]
    workspace.configuration_store().save(configuration)

    global_file = tmp_path / "global-style.json"
    global_file.write_text('{"writing_style":"concise","image_policy":"none"}', encoding="utf-8")
    assert main(["style", "edit", "--workspace", str(workspace.root), "--file", str(global_file)]) == 0
    course_file = tmp_path / "course-style.json"
    course_file.write_text('{"list_density":"list_first","image_policy":"explanatory"}', encoding="utf-8")
    assert main(["style", "edit", "--workspace", str(workspace.root), "--course", "QNT9", "--file", str(course_file)]) == 0

    saved = workspace.configuration_store().load()
    assert effective_style(saved, "QNT9")["image_policy"] == "explanatory"
    assert effective_style(saved, "QNT9")["list_density"] == "list_first"
    assert effective_style(saved, "OPS7")["image_policy"] == "none"
    assert effective_style(saved, "OPS7")["writing_style"] == "concise"
    assert saved["courses"][0]["style"] == {"list_density": "list_first", "image_policy": "explanatory"}

    assert main(["style", "edit", "--workspace", str(workspace.root), "--course", "QNT9", "--reset"]) == 0
    reset = workspace.configuration_store().load()
    assert effective_style(reset, "QNT9")["image_policy"] == "none"
    assert reset["courses"][0]["style"] == {}


def test_legacy_reference_path_is_removed_without_reading_document(tmp_path) -> None:
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()
    configuration = workspace.configuration_store().load()
    legacy = course()
    legacy["folders"]["style_reference"] = "/path/that/does/not/exist/external-reference.docx"
    configuration["courses"] = [legacy]
    workspace.configuration_path.write_text(json.dumps(configuration), encoding="utf-8")

    normalized = workspace.configuration_store().load()
    assert "style_reference" not in normalized["courses"][0]["folders"]
    workspace.configuration_store().save(normalized)
    assert "external-reference.docx" not in workspace.configuration_path.read_text(encoding="utf-8")
    assert effective_style(normalized, "QNT9")["heading_color"] == "0F4761"


def test_style_edit_rejects_invalid_values_without_changing_config(tmp_path) -> None:
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()
    original = file_sha256(workspace.configuration_path)
    bad = tmp_path / "bad.json"
    bad.write_text('{"question_count":7}', encoding="utf-8")
    assert main(["style", "edit", "--workspace", str(workspace.root), "--file", str(bad)]) == 2
    assert file_sha256(workspace.configuration_path) == original
    with pytest.raises(ConfigurationError, match="Course not found"):
        effective_style(workspace.configuration_store().load(), "MISSING")


def test_onboarding_style_group_applies_course_choices_after_global(tmp_path) -> None:
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()
    configuration = workspace.configuration_store().load()
    configuration["courses"] = [course()]
    workspace.configuration_store().save(configuration)

    completed = save_group(workspace, "style", {
        "profile": {"writing_style": "detailed", "image_policy": "none", "recap_language": "it"},
        "course_profiles": {"QNT9": {"image_policy": "explanatory", "list_density": "prose_first"}},
    })
    assert completed["style"]["profile"]["writing_style"] == "detailed"
    assert effective_style(completed, "QNT9")["image_policy"] == "explanatory"
    assert effective_style(completed, "QNT9")["recap_language"] == "it"
    assert effective_style(completed)["image_policy"] == "none"


def test_style_preview_creates_synthetic_word_document(tmp_path, capsys) -> None:
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()
    destination = tmp_path / "style-preview.docx"

    assert main(["style", "preview", "--workspace", str(workspace.root), "--output", str(destination)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["preview"] == str(destination)
    with ZipFile(destination) as document:
        word = document.read("word/document.xml").decode("utf-8")
        assert "external-reference" not in word
        assert "style_reference" not in word


def test_first_run_questions_offer_course_style_after_global_style(monkeypatch, tmp_path) -> None:
    workspace = Workspace.from_value(tmp_path / "private")
    monkeypatch.setenv("MYNOTES_WORKSPACE", str(workspace.root))
    stages = (
        ["it", "none", "MSc", "2026", "understanding"],
        [""],
        ["1", "Quantitative Methods", "QNT9", "", "en", "en", "mixed", "", "", ""],
        ["", "", "detailed", "balanced", "essential", "5", "yes", "it",
         "yes", "", "list_first", "", "", "", "en"],
        ["none"],
    )
    for answers in stages:
        iterator = iter(answers)
        monkeypatch.setattr("builtins.input", lambda _prompt: next(iterator))
        assert main([]) == 0
    saved = workspace.configuration_store().load()
    assert saved["onboarding"]["status"] == "complete"
    assert effective_style(saved, "QNT9")["writing_style"] == "detailed"
    assert effective_style(saved, "QNT9")["list_density"] == "list_first"
    assert effective_style(saved, "QNT9")["recap_language"] == "en"
    assert effective_style(saved)["recap_language"] == "it"
