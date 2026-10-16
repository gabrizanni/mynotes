"""Public command-line entry point for MyNotes configuration."""

from __future__ import annotations

import argparse
import os
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .configuration import (
    ONBOARDING_GROUPS,
    ConfigurationError,
    clean_path,
    configured_directories,
    course_template,
    effective_style,
    merge_course,
    merge_group_answers,
    normalize_style_profile,
    provider_availability,
)
from .workspace import Workspace
from .workflow import WorkflowError, generate_candidate, process, status
from .providers import ProviderError
from .asr import ASRError, ASRBusyError
from .extraction import ExtractionError
from .ingestion import IngestionError, discover_recordings, recording_for_lecture
from .sourcepack import SourcePackError, build_source_pack
from .migration import MigrationError, apply_manifest, build_manifest, load_manifest, save_manifest
from .integrations import IntegrationError, install_integration
from .chat import ChatError, launch_chat
from .publication import (
    PublicationError,
    build_word_candidate,
    publication_history,
    publish_word_candidate,
    recover_publications,
    restore_backup,
)


def _workspace(arguments: argparse.Namespace) -> Workspace:
    workspace = Workspace.from_value(getattr(arguments, "workspace", None))
    workspace.initialize()
    return workspace


def _load_json(path: str) -> dict[str, Any]:
    with Path(path).expanduser().open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ConfigurationError("The supplied JSON must be an object")
    return payload


def _write_directories(configuration: dict[str, Any]) -> None:
    directories = list(configured_directories(configuration))
    if not directories:
        print("No configured external directories to create.")
        return
    print("Creating configured directories:")
    for directory in directories:
        print(f"- {directory}")
        directory.mkdir(parents=True, exist_ok=True)


def _prompt_style_profile(current: dict[str, Any], *, changed_only: bool = False) -> dict[str, Any]:
    """Ask about editorial choices, keeping all visual rules code-owned."""
    fields = (
        ("writing_style", "Writing [clear_study/concise/detailed]"),
        ("list_density", "Lists [balanced/prose_first/list_first]"),
        ("image_policy", "Images [essential/none/explanatory]"),
        ("question_count", "Maximum study questions [1-5]"),
        ("recap", "End-of-lesson recap [yes/no]"),
        ("recap_language", "Recap language [it/en/same_as_notes]"),
    )
    result: dict[str, Any] = {}
    for key, label in fields:
        existing = current[key]
        shown = "yes" if existing is True else "no" if existing is False else str(existing)
        raw = input(f"{label} [{shown}]: ").strip()
        if not raw:
            if not changed_only:
                result[key] = existing
            continue
        if key == "question_count":
            try:
                value: Any = int(raw)
            except ValueError as error:
                raise ConfigurationError("Maximum study questions must be 1 to 5") from error
        elif key == "recap":
            if raw.casefold() not in {"yes", "y", "no", "n", "si", "sì"}:
                raise ConfigurationError("Recap must be yes or no")
            value = raw.casefold() in {"yes", "y", "si", "sì"}
        else:
            value = raw.lower()
        if not changed_only or value != existing:
            result[key] = value
    return normalize_style_profile(result, partial=changed_only)


def _interactive_group(group: str, configuration: dict[str, Any]) -> dict[str, Any]:
    """Ask a small, provider-free batch of questions and return its answers."""
    if group == "personal":
        language = input("Lingua / Language [it/en]: ").strip() or configuration["personal"]["interface_language"]
        question = "Raccontami brevemente cosa studi, qual è il tuo background e come vorresti usare MyNotes (Invio per saltare): " if language == "it" else "Tell me briefly what you study, your background and how you would like to use MyNotes (Enter to skip): "
        background = input(question).strip()
        return {"interface_language": language, "background": background, "background_confirmed": True}
    if group == "paths":
        raw = input("Recording folders, comma-separated (optional): ").strip()
        return {"recording_dirs": [clean_path(item) for item in raw.split(",") if item.strip()]}
    if group == "courses":
        count = int(input("Number of courses to add now [0]: ").strip() or "0")
        courses = []
        for number in range(count):
            course = course_template()
            print(f"Course {number + 1}/{count}")
            course["name"] = input("Name: ").strip()
            course["code"] = input("Short code (letters/numbers): ").strip()
            aliases = input("Aliases, comma-separated (optional): ").strip()
            course["aliases"] = [item.strip() for item in aliases.split(",") if item.strip()]
            course["languages"]["lesson"] = input("Lesson language [en]: ").strip() or "en"
            course["languages"]["notes"] = input("Notes language [en]: ").strip() or "en"
            course["kind"] = input("Kind [mixed]: ").strip() or "mixed"
            for field, label in (
                ("materials", "Materials folder (optional)"),
                ("personal_notes", "Personal notes folder (optional)"),
                ("handout", "Cumulative handout folder (optional)"),
            ):
                course["folders"][field] = input(f"{label}: ").strip()
            courses.append(course)
        return {"courses": courses}
    if group == "style":
        quality = input(f"Default quality [{configuration['style']['quality']}]: ").strip() or configuration["style"]["quality"]
        freeform_answer = input("Additional style preferences (blank keeps current, '-' clears): ").strip()
        freeform = ("" if freeform_answer == "-" else freeform_answer) if freeform_answer else configuration["style"]["freeform_preferences"]
        profile = _prompt_style_profile(effective_style(configuration))
        selected_global = effective_style({"style": {"profile": profile}})
        course_profiles: dict[str, dict[str, Any]] = {}
        for course in configuration["courses"]:
            code = course["code"]
            if input(f"Customize style for {code} beyond global choices? [y/N]: ").strip().lower() in {"y", "yes", "s", "si", "sì"}:
                course_profiles[code] = _prompt_style_profile(selected_global, changed_only=True)
        return {
            "quality": quality,
            "freeform_preferences": freeform,
            "profile": profile,
            "course_profiles": course_profiles,
        }
    if group == "providers":
        provider = input("Default provider [codex/agy/claude/none]: ").strip().lower() or "none"
        if provider == "none":
            return {"default": None, "configured": {}}
        model = input("Preferred model (optional): ").strip()
        effort = input("Preferred effort (optional): ").strip()
        return {
            "default": provider,
            "configured": {provider: {key: value for key, value in {"model": model, "effort": effort}.items() if value}},
        }
    raise ConfigurationError(f"Unknown onboarding group: {group}")


def _apply_instruction(configuration: dict[str, Any], instruction: str, course_code: str | None) -> tuple[dict[str, Any], str]:
    """Persist only clearly persistent preferences; lesson requests stay scoped."""
    normalized = instruction.strip()
    if not normalized:
        raise ConfigurationError("Instruction cannot be empty")
    lower = normalized.casefold()
    if "questa lezione" in lower or "this lesson" in lower:
        return configuration, "Saved nowhere: this is a lesson-scoped instruction for a future run."
    requested_code = course_code.upper() if course_code else None
    if requested_code is None:
        matches = [course["code"] for course in configuration["courses"] if course["code"].casefold() in lower]
        requested_code = matches[0] if len(matches) == 1 else None
    if ("da ora in poi" in lower or "from now on" in lower) and requested_code:
        for course in configuration["courses"]:
            if course["code"] == requested_code:
                instructions = course["preferences"]["persistent_instructions"]
                if normalized not in instructions:
                    instructions.append(normalized)
                return configuration, f"Stored persistent preference for course {requested_code}."
        raise ConfigurationError(f"Course not found: {requested_code}")
    preferences = configuration["style"]["freeform_preferences"]
    separator = "\n" if preferences else ""
    configuration["style"]["freeform_preferences"] = preferences + separator + normalized
    return configuration, "Stored a personal style preference; no folders, provider, or quota policy changed."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mynotes",
        description="MyNotes keeps application resources and private study data separate.",
    )
    parser.add_argument("--version", action="version", version=f"mynotes {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    from .capture import add_parser
    add_parser(subparsers)
    init = subparsers.add_parser("init", help="initialize an empty private workspace")
    init.add_argument("--workspace", help="private workspace path")
    setup = subparsers.add_parser("setup", help="open resumable MyNotes setup")
    setup.add_argument("--workspace", help="private workspace path")
    setup.add_argument("--provider", choices=("codex", "agy", "claude"), help="selected agent")
    configure = subparsers.add_parser("configure", help="run or resume configuration")
    configure.add_argument("--workspace", help="private workspace path")
    configure.add_argument("--answers", help="JSON answers for one onboarding group")
    configure.add_argument("--group", choices=ONBOARDING_GROUPS, help="onboarding group for --answers or interactive entry")
    configure.add_argument("--instruction", help="natural-language preference to persist safely")
    configure.add_argument("--course", help="course code for a persistent instruction")
    configure.add_argument("--create-missing-dirs", action="store_true", help="create displayed configured folders")
    preferences = subparsers.add_parser("preferences", help="save one model for chat and notes, and the conversation language")
    preferences.add_argument("--workspace", help="private workspace path")
    preferences.add_argument("--provider", required=True, choices=("codex", "agy", "claude"))
    preferences.add_argument("--model", required=True, help="model identifier; empty text uses the agent default")
    preferences.add_argument("--effort", help="supported effort; empty text restores the agent default")
    preferences.add_argument("--preset", help="preset id from mynotes models; saves model and effort together")
    preferences.add_argument("--language", choices=("it", "en"))
    models = subparsers.add_parser("models", help="inspect discovered models and visible presets without generation")
    models.add_argument("--provider", required=True, choices=("codex", "agy", "claude"))
    models.add_argument("--saved", default="", help="retain a saved model even if absent from the current catalog")
    models.add_argument("--saved-effort", default="", help="effort saved with the retained model")
    models.add_argument("--refresh", action="store_true", help="refresh the supported provider catalog without generation")
    models.add_argument("--json", action="store_true", help="machine-readable catalog")
    profile = subparsers.add_parser("profile", help="save the learner's single free-form background answer")
    profile.add_argument("--workspace", help="private workspace path")
    background = profile.add_mutually_exclusive_group(required=True)
    background.add_argument("--background", help="the user's answer, quoted as one argument")
    background.add_argument("--skip", action="store_true", help="acknowledge this step without requiring an answer")
    style = subparsers.add_parser("style", help="inspect, edit, or preview the coded note style")
    style_subparsers = style.add_subparsers(dest="style_command", required=True)
    for name, help_text in (
        ("show", "show the effective style"),
        ("edit", "change global defaults or one course's overrides"),
        ("preview", "build a synthetic Word preview without source documents"),
    ):
        command = style_subparsers.add_parser(name, help=help_text)
        command.add_argument("--workspace", help="private workspace path")
        command.add_argument("--course", help="course code; omit for global defaults")
        if name == "edit":
            command.add_argument("--file", help="JSON object with style fields; omit for guided questions")
            command.add_argument("--reset", action="store_true", help="clear course overrides or reset global defaults")
        if name == "preview":
            command.add_argument("--output", help="destination .docx; defaults to private workspace/previews")
    course = subparsers.add_parser("course", help="add or edit a configured course")
    course_subparsers = course.add_subparsers(dest="course_command", required=True)
    for action in ("pending", "defer-context"):
        pending = course_subparsers.add_parser(action)
        pending.add_argument("--workspace")
        pending.add_argument("--json", action="store_true")
        if action == "defer-context":
            pending.add_argument("code")
    for name, help_text in (("add", "add a course from JSON"), ("edit", "replace a course from JSON")):
        command = course_subparsers.add_parser(name, help=help_text)
        command.add_argument("--workspace", help="private workspace path")
        command.add_argument("--file", help="course JSON file; omit for guided questions")
        if name == "add":
            command.add_argument("--defer-context", action="store_true")
            command.add_argument("--json", action="store_true")
            command.add_argument("--name", help="create a first course with default folders and style")
            command.add_argument("--notes-language", default="en")
            command.add_argument("--materials", help="existing materials folder")
            command.add_argument("--recordings", help="existing recordings folder")
            command.add_argument("--context-answer", help="original free-form answer to the two course questions")
            command.add_argument("--settings-json", help="interpreted course context and style as one JSON object")
        if name == "edit":
            command.add_argument("code", help="course code to replace")
        command.add_argument("--create-missing-dirs", action="store_true")
    link = course_subparsers.add_parser("link", help="connect a supplied course folder or Finder alias in one step")
    link.add_argument("code", help="configured course code")
    link.add_argument("path", help="course folder or Finder alias; quote paths with spaces")
    link.add_argument("--workspace", help="private workspace path")
    link.add_argument("--notes", help="explicit personal-notes folder if selection is ambiguous")
    link.add_argument("--recordings", help="explicit recordings folder if selection is ambiguous")
    link.add_argument("--dry-run", action="store_true", help="inspect without changing configuration")
    link.add_argument("--json", action="store_true", help="return resolved paths and bounded inventory as JSON")
    config = subparsers.add_parser("config", help="inspect or restore private configuration")
    config_subparsers = config.add_subparsers(dest="config_command", required=True)
    show = config_subparsers.add_parser("show", help="show current configuration")
    show.add_argument("--workspace", help="private workspace path")
    show.add_argument("--json", action="store_true", help="machine-readable output")
    history = config_subparsers.add_parser("history", help="list configuration backups")
    history.add_argument("--workspace", help="private workspace path")
    restore = config_subparsers.add_parser("restore", help="restore a configuration backup")
    restore.add_argument("--workspace", help="private workspace path")
    restore.add_argument("name", help="backup filename shown by config history")
    for name, help_text in (("process", "publish a lecture from local text sources"), ("redo", "replace one published lecture from local text sources")):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("lecture_id", help="COURSE-LNN")
        command.add_argument("--workspace", help="private workspace path")
        command.add_argument("--transcript", required=True, help="local UTF-8 transcript text")
        command.add_argument("--materials", help="optional local UTF-8 material text")
        command.add_argument("--personal-notes", help="optional local UTF-8 personal notes")
        command.add_argument("--web-source", help="optional local UTF-8 snapshot of an online source")
        command.add_argument(
            "--provider",
            choices=("codex", "agy", "claude"),
            help="override the configured provider; no simulated provider is available in normal runs",
        )
        command.add_argument("--quality", default="standard", help="base, standard, approfondito, or revisione")
        command.add_argument(
            "--accept-current-word",
            action="store_true",
            help="explicitly rebase on a Word file changed since the last MyNotes publication",
        )
    discover = subparsers.add_parser("discover", help="inventory configured recording folders without renaming files")
    discover.add_argument("--workspace", help="private workspace path")
    normalize = subparsers.add_parser("normalize-recordings", help="apply only unambiguous canonical recording names")
    normalize.add_argument("--workspace", help="private workspace path")
    normalize.add_argument("--apply", action="store_true", help="perform safe renames; otherwise report only")
    prepare = subparsers.add_parser("prepare", help="build a validated local source pack for COURSE-LNN")
    prepare.add_argument("lecture_id", help="COURSE-LNN")
    prepare.add_argument("--workspace", help="private workspace path")
    prepare.add_argument("--audio", help="recording path; otherwise use the configured recording inventory")
    prepare.add_argument("--transcript", help="existing normalized transcript to reuse without ASR")
    prepare.add_argument("--material", action="append", default=[], help="PDF, PPT/PPTX, XLSX, DOCX, text, or notebook; repeatable")
    prepare.add_argument("--personal-notes", help="DOCX, Markdown, or text containing the lesson block")
    prepare.add_argument("--web-snapshot", action="append", default=[], help="local snapshot of a linked online source; repeatable")
    transcribe = subparsers.add_parser("transcribe", help="transcribe only the selected lecture with validated timestamped results")
    from .transcription_queue import add_parser as add_queue_parser
    add_queue_parser(subparsers)
    transcribe.add_argument("lecture_id", help="COURSE-LNN")
    transcribe.add_argument("--workspace", help="private workspace path")
    transcribe.add_argument("--audio", help="specific recording; otherwise resolve only this lecture")
    transcribe.add_argument("--json", action="store_true", help="structured result; progress goes to stderr")
    transcription_status = subparsers.add_parser("transcription-status", help="read one transcription's progress without starting work")
    transcription_status.add_argument("lecture_id", help="COURSE-LNN")
    transcription_status.add_argument("--workspace", help="private workspace path")
    transcription_status.add_argument("--json", action="store_true")
    study = subparsers.add_parser("study", help="create a standalone source-bound quiz guide with the shared Word renderer")
    study.add_argument("course", help="configured course code")
    study.add_argument("--lessons", required=True, help="ordered lesson range, for example 6-10")
    study.add_argument("--purpose", choices=("quiz",), default="quiz")
    study.add_argument("--output", help="new candidate DOCX path; existing files are preserved")
    study.add_argument("--workspace", help="private workspace path")
    study.add_argument("--manifest", help="reviewed complete source/topic manifest")
    study.add_argument("--candidate", help="reviewed structured content JSON; internal working format")
    study.add_argument("--provider", choices=("codex", "agy", "claude"))
    study.add_argument("--quality", default="standard")
    study.add_argument("--plan-only", action="store_true", help="local inventory only, no generation")
    study_schema = subparsers.add_parser("study-schema", help="read the structured study contract without exploring implementation files")
    study_schema.add_argument("--json", action="store_true")
    study_verify = subparsers.add_parser("study-verify", help="render and record checks actually performed on a study Word candidate")
    study_verify.add_argument("candidate", help="existing study candidate DOCX")
    study_verify.add_argument("--no-render", action="store_true", help="reuse a previously verified render")
    study_verify.add_argument("--editorial-reviewed", action="store_true", help="record a complete editorial review actually performed")
    study_verify.add_argument("--visual-reviewed", action="store_true", help="record actual inspection of every rendered page")
    study_verify.add_argument("--word-verified", action="store_true", help="record actual opening and verification in Microsoft Word")
    generate = subparsers.add_parser("generate", help="generate a reviewable candidate from a prepared source pack")
    generate.add_argument("lecture_id", help="COURSE-LNN")
    generate.add_argument("--workspace", help="private workspace path")
    generate.add_argument("--source-pack", help="prepared source-pack JSON; defaults to the lecture run")
    generate.add_argument("--provider", choices=("codex", "agy", "claude"), help="provider override")
    generate.add_argument("--quality", default="standard", help="base, standard, approfondito, or revisione")
    generate.add_argument("--instructions", default="", help="temporary lesson instructions; does not save course preferences")
    generate.add_argument("--image-limit", type=int, help="maximum inserted images for this request; zero inserts none")
    batch = subparsers.add_parser("generate-batch", help="coordinate ordered lessons with shared slide ownership; candidates only")
    batch.add_argument("course", help="configured course code")
    batch.add_argument("--workspace", help="private workspace path")
    batch.add_argument("--lessons", help="lesson numbers/ranges, for example 1-6 or 1,3-5")
    batch.add_argument("--provider", choices=("codex", "agy", "claude"))
    batch.add_argument("--quality", help="base, standard, approfondito, or revisione; defaults to standard")
    batch.add_argument("--selection", help="JSON mapping canonical slide IDs to reviewed owners and inline anchors")
    batch.add_argument("--budgets", help="JSON with verified selected-model context/output limits")
    batch.add_argument("--instructions", help="temporary instructions for this batch")
    batch.add_argument("--image-limit", type=int, help="maximum inserted images per lesson for this request")
    batch.add_argument("--plan-only", action="store_true", help="inspect existing packs without writes or provider calls")
    batch.add_argument("--resume", metavar="BATCH_ID", help="resume a saved batch without repeating its lesson selection")
    batch.add_argument("--build-word", action="store_true", help="build each validated lesson as a Word candidate before advancing")
    batch.add_argument("--stop-after", type=int, help="pause for review after this lesson without changing the selected batch")
    batch_status = subparsers.add_parser("batch-status", help="read saved batch progress without generation or state changes")
    batch_status.add_argument("batch_id")
    batch_status.add_argument("--workspace")
    batch_status.add_argument("--json", action="store_true")
    build = subparsers.add_parser("build", help="build and render-validate a Word candidate without publishing it")
    build.add_argument("lecture_id", help="COURSE-LNN")
    build.add_argument("--workspace", help="private workspace path")
    publish = subparsers.add_parser("publish", help="atomically publish a validated Word candidate")
    publish.add_argument("lecture_id", help="COURSE-LNN")
    publish.add_argument("--workspace", help="private workspace path")
    publish.add_argument("--candidate", help="specific validated candidate DOCX")
    publish.add_argument("--accept-current-word", action="store_true", help="accept an explicitly reviewed external Word change")
    word_history = subparsers.add_parser("history", help="list restorable cumulative-Word backups")
    word_history.add_argument("course", nargs="?", help="optional course code")
    word_history.add_argument("--workspace", help="private workspace path")
    restore_word = subparsers.add_parser("restore", help="restore one exact cumulative-Word backup")
    restore_word.add_argument("backup_id", help="backup id shown by mynotes history")
    restore_word.add_argument("--workspace", help="private workspace path")
    recover = subparsers.add_parser("recover", help="classify interrupted Word publication transactions safely")
    recover.add_argument("--workspace", help="private workspace path")
    for provider in ("codex", "agy", "claude"):
        natural = subparsers.add_parser(provider, help=f"run a simple natural-language request with {provider}")
        natural.add_argument("message", nargs="*", help='omit to open a chat, or say e.g. "genera ABC-L01"')
        natural.add_argument("--workspace", help="private workspace path")
        natural.add_argument("--source-pack", help="prepared source-pack JSON; defaults to the lecture run")
    doctor = subparsers.add_parser("doctor", help="inspect a private workspace without changing it")
    doctor.add_argument("--workspace", help="private workspace path")
    doctor.add_argument("--json", action="store_true", help="component readiness and repair actions")
    doctor.add_argument("--provider", choices=("codex", "agy", "claude"), help="inspect another agent without changing the selected default")
    migrate = subparsers.add_parser("migrate", help="inventory or copy a legacy installation")
    migrate_subparsers = migrate.add_subparsers(dest="migrate_command", required=True)
    legacy = migrate_subparsers.add_parser("legacy", help="migrate a legacy local installation")
    legacy.add_argument("--source", help="legacy project directory (required for --dry-run)")
    legacy.add_argument("--workspace", help="new private workspace path")
    choice = legacy.add_mutually_exclusive_group(required=True)
    choice.add_argument("--dry-run", action="store_true")
    choice.add_argument("--apply-manifest", help="exact reviewed manifest produced by --dry-run")
    integrate = subparsers.add_parser("integrate", help="export a platform integration without private data")
    integrate.add_argument("platform", choices=("codex", "agy", "claude"))
    integrate.add_argument("--destination", default=".", help="directory receiving only the integration instructions")
    integrate.add_argument("--action", choices=("install", "update", "remove"), default="install")
    status_command = subparsers.add_parser("status", help="show workflow state from a second terminal")
    status_command.add_argument("--workspace", help="private workspace path")
    usage = subparsers.add_parser("usage", help="inspect all local provider attempts and reuse for one lecture")
    usage.add_argument("lecture_id", nargs="?", help="COURSE-LNN")
    usage.add_argument("--batch", metavar="BATCH_ID", help="count shared attempts once across the batch")
    usage.add_argument("--workspace", help="private workspace path")
    usage.add_argument("--json", action="store_true", help="machine-readable provider reported counters")
    chat = subparsers.add_parser("chat", help="open a real provider conversation grounded in MyNotes")
    chat.add_argument("--workspace", help="private workspace path")
    chat.add_argument("--provider", help="configured provider")
    chat.add_argument("message", nargs="?", help="optional natural-language request")
    return parser


def _configure(arguments: argparse.Namespace) -> int:
    workspace = _workspace(arguments)
    store = workspace.configuration_store()
    configuration = store.load()
    if arguments.instruction:
        updated, message = _apply_instruction(configuration, arguments.instruction, arguments.course)
        if not message.startswith("Saved nowhere"):
            store.save(updated)
        print(message)
        return 0
    group = arguments.group or configuration["onboarding"]["next_group"]
    if group is None:
        print("Onboarding is complete. Use mynotes config show or mynotes course edit.")
        return 0
    answers = _load_json(arguments.answers) if arguments.answers else _interactive_group(group, configuration)
    updated = merge_group_answers(configuration, group, answers)
    saved = store.save(updated)
    if group == "providers" and saved["providers"]["default"]:
        availability = provider_availability(saved["providers"]["default"])
        print(f"Provider {availability['provider']}: {availability['detail']}")
    if arguments.create_missing_dirs:
        _write_directories(saved)
    print(f"Saved {group}. Next onboarding group: {saved['onboarding']['next_group'] or 'complete'}.")
    return 0


def _course(arguments: argparse.Namespace) -> int:
    if arguments.course_command in {"pending", "defer-context"}:
        from .capture_courses import pending_courses, defer_context
        ws = Workspace.from_value(arguments.workspace)
        value = {"courses": pending_courses(ws)} if arguments.course_command == "pending" else defer_context(ws, arguments.code)
        print(json.dumps(value, ensure_ascii=False))
        return 0
    if arguments.course_command == "link":
        from .course_links import link_course_folder
        result = link_course_folder(Workspace.from_value(arguments.workspace), arguments.code, Path(arguments.path),
            dry_run=arguments.dry_run, notes=Path(arguments.notes) if arguments.notes else None,
            recordings=Path(arguments.recordings) if arguments.recordings else None)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["status"] != "NEEDS_SELECTION" else 1
    workspace = _workspace(arguments)
    if arguments.course_command == "add" and arguments.name:
        if arguments.defer_context:
            if arguments.file or arguments.settings_json or arguments.context_answer or arguments.materials or arguments.recordings:
                raise ConfigurationError("Name-only course creation cannot replace course settings or folders")
            from .capture_courses import add_course
            print(json.dumps(add_course(workspace, arguments.name), ensure_ascii=False))
            return 0
        if arguments.file:
            raise ConfigurationError("Choose --name or --file, not both")
        from .onboarding import add_first_course
        course_settings = json.loads(arguments.settings_json) if arguments.settings_json is not None else None
        if arguments.settings_json is not None and not isinstance(course_settings, dict):
            raise ConfigurationError("Course settings must be a JSON object")
        result = add_first_course(workspace, arguments.name, notes_language=arguments.notes_language,
                                 materials=arguments.materials, recordings=arguments.recordings,
                                 context_answer=arguments.context_answer,
                                 course_settings=course_settings)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if arguments.course_command == "add" and (arguments.context_answer is not None or arguments.settings_json is not None):
        raise ConfigurationError("Use --name with --context-answer or --settings-json")
    store = workspace.configuration_store()
    configuration = store.load()
    course = _load_json(arguments.file) if arguments.file else _interactive_course_entry(configuration)
    if arguments.course_command == "add":
        configuration["courses"].append(course)
    else:
        code = arguments.code.upper()
        positions = [index for index, item in enumerate(configuration["courses"]) if item["code"] == code]
        if not positions:
            raise ConfigurationError(f"Course not found: {code}")
        configuration["courses"][positions[0]] = merge_course(configuration["courses"][positions[0]], course)
    saved = store.save(configuration)
    if arguments.create_missing_dirs:
        _write_directories(saved)
    print(f"Course configuration saved: {arguments.course_command}.")
    return 0


def _interactive_course_entry(configuration: dict[str, Any]) -> dict[str, Any]:
    course = course_template()
    course["code"] = input("Course code: ").strip().upper()
    course["name"] = input("Course name: ").strip()
    course["kind"] = input("Kind [mixed/quantitative/theoretical/cases/programming]: ").strip() or "mixed"
    course["languages"]["lesson"] = input("Lecture language [en]: ").strip() or "en"
    course["languages"]["notes"] = input("Notes language [en]: ").strip() or "en"
    course["assessment"] = input("Assessment/exam format (optional): ").strip()
    course["preparation"] = input("What should the notes optimize for? (optional): ").strip()
    course["instructors"] = [item.strip() for item in input("Instructors, comma-separated (optional): ").split(",") if item.strip()]
    course["difficult_topics"] = [item.strip() for item in input("Difficult topics, comma-separated (optional): ").split(",") if item.strip()]
    course["folders"]["materials"] = input("Materials folder: ").strip()
    course["folders"]["personal_notes"] = input("Personal notes folder: ").strip()
    course["folders"]["handout"] = input("Cumulative Word folder: ").strip()
    course["folders"]["syllabus"] = input("Syllabus path/folder (optional): ").strip()
    course["transcription"]["accent"] = input("Accent/speech characteristics (optional): ").strip()
    course["transcription"]["vocabulary"] = [item.strip() for item in input("Technical vocabulary, comma-separated (optional): ").split(",") if item.strip()]
    persistent = input("Persistent note preferences for this course (optional): ").strip()
    course["preferences"]["persistent_instructions"] = [persistent] if persistent else []
    if input("Customize writing, lists, images and recap for this course? [y/N]: ").strip().lower() in {"y", "yes", "s", "si", "sì"}:
        course["style"] = _prompt_style_profile(effective_style(configuration), changed_only=True)
    return course


def _style(arguments: argparse.Namespace) -> int:
    workspace = _workspace(arguments)
    store = workspace.configuration_store()
    configuration = store.load()
    code = arguments.course.upper() if arguments.course else None
    resolved = effective_style(configuration, code)
    if arguments.style_command == "show":
        print(json.dumps({"scope": code or "global", "effective": resolved}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if arguments.style_command == "preview":
        from .publication import preview_style_document

        name = f"{code or 'global'}-style-preview.docx"
        output = Path(arguments.output).expanduser() if arguments.output else workspace.root / "previews" / name
        generated = preview_style_document(resolved, output)
        print(json.dumps({"scope": code or "global", "preview": str(generated)}, ensure_ascii=False))
        return 0
    if arguments.file and arguments.reset:
        raise ConfigurationError("Choose --file or --reset, not both")
    if arguments.reset:
        patch: dict[str, Any] = {}
    elif arguments.file:
        patch = normalize_style_profile(_load_json(arguments.file), partial=True)
    else:
        patch = _prompt_style_profile(resolved, changed_only=code is not None)
    if code is None:
        configuration["style"]["profile"] = (
            normalize_style_profile({}) if arguments.reset else
            normalize_style_profile({**configuration["style"].get("profile", {}), **patch})
        )
    else:
        course = next(item for item in configuration["courses"] if item["code"] == code)
        if arguments.reset:
            for key in ("writing_style", "list_density", "image_policy", "question_count", "recap", "recap_language"):
                course["style"].pop(key, None)
        else:
            global_profile = effective_style(configuration)
            for key, value in patch.items():
                if value == global_profile[key]:
                    course["style"].pop(key, None)
                else:
                    course["style"][key] = value
    saved = store.save(configuration)
    print(json.dumps({"scope": code or "global", "effective": effective_style(saved, code)}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _config(arguments: argparse.Namespace) -> int:
    workspace = _workspace(arguments)
    store = workspace.configuration_store()
    if arguments.config_command == "show":
        configuration = store.load()
        if arguments.json:
            print(json.dumps(configuration, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"workspace: {workspace.root}")
            print(f"onboarding: {configuration['onboarding']['status']}")
            print(f"courses: {', '.join(course['code'] for course in configuration['courses']) or 'none'}")
            print(f"default provider: {configuration['providers']['default'] or 'none'}")
        return 0
    if arguments.config_command == "history":
        for entry in store.history():
            print(entry.name)
        return 0
    store.restore(arguments.name)
    print(f"Restored configuration backup: {arguments.name}")
    return 0


def _process(arguments: argparse.Namespace) -> int:
    workspace = _workspace(arguments)
    result = process(
        workspace,
        arguments.lecture_id,
        Path(arguments.transcript).expanduser(),
        materials=Path(arguments.materials).expanduser() if arguments.materials else None,
        personal_notes=Path(arguments.personal_notes).expanduser() if arguments.personal_notes else None,
        web_source=Path(arguments.web_source).expanduser() if arguments.web_source else None,
        provider=arguments.provider,
        quality=arguments.quality,
        redo=arguments.command == "redo",
        accept_current_word=arguments.accept_current_word,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _migrate(arguments: argparse.Namespace) -> int:
    workspace = _workspace(arguments)
    if arguments.dry_run:
        if not arguments.source:
            raise MigrationError("--source is required for migration dry-run")
        manifest = build_manifest(Path(arguments.source), workspace)
        manifest_path = save_manifest(workspace, manifest)
        result: dict[str, Any] = {"manifest": str(manifest_path), "manifest_id": manifest["manifest_id"], "entries": len(manifest["entries"]), "mode": "dry-run"}
    else:
        manifest = load_manifest(Path(arguments.apply_manifest), workspace)
        result = {"manifest": str(Path(arguments.apply_manifest).expanduser()), "manifest_id": manifest["manifest_id"], "entries": len(manifest["entries"]), "mode": "apply"}
        result.update(apply_manifest(manifest))
    print(json.dumps(result, sort_keys=True))
    return 0


def _discover(arguments: argparse.Namespace, *, apply_renames: bool) -> int:
    workspace = _workspace(arguments)
    result = discover_recordings(workspace, workspace.configuration_store().load(), apply_renames=apply_renames)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _prepare(arguments: argparse.Namespace) -> int:
    workspace = _workspace(arguments)
    configuration = workspace.configuration_store().load()
    audio = Path(arguments.audio).expanduser() if arguments.audio else None
    if audio is None and not arguments.transcript:
        inventory = discover_recordings(workspace, configuration, lecture_id=arguments.lecture_id.upper(), persist=False)
        audio = recording_for_lecture(inventory, arguments.lecture_id.upper())
    pack = build_source_pack(
        workspace, arguments.lecture_id.upper(), audio=audio,
        transcript_path=Path(arguments.transcript).expanduser() if arguments.transcript else None,
        materials=[Path(value).expanduser() for value in arguments.material],
        personal_notes=Path(arguments.personal_notes).expanduser() if arguments.personal_notes else None,
        web_snapshots=[Path(value).expanduser() for value in arguments.web_snapshot],
    )
    print(json.dumps({"lecture_id": pack["lecture_id"], "source_pack": str(workspace.runs_dir / pack["lecture_id"] / "source_pack.json"), "availability": pack["availability"]}, ensure_ascii=False))
    return 0


def _generate(arguments: argparse.Namespace, *, provider: str | None = None, quality: str | None = None) -> int:
    workspace = _workspace(arguments)
    result = generate_candidate(
        workspace, arguments.lecture_id, source_pack_path=Path(arguments.source_pack).expanduser() if arguments.source_pack else None,
        provider=provider or arguments.provider, quality=quality or arguments.quality,
        instructions=getattr(arguments, "instructions", ""), image_limit=getattr(arguments, "image_limit", None),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _transcription(arguments: argparse.Namespace) -> int:
    from .transcription import run_transcription, transcription_status
    from .asr import ASRBusyError, ASRReviewError
    workspace = Workspace.from_value(arguments.workspace)
    try:
        if arguments.command == "transcription-status":
            result = transcription_status(workspace, arguments.lecture_id.upper())
        else:
            result = run_transcription(workspace, arguments.lecture_id.upper(),
                                       audio=Path(arguments.audio).expanduser() if arguments.audio else None,
                                       progress=True)
    except ASRBusyError as error:
        result = {"status": "BUSY", "lecture_id": arguments.lecture_id.upper(), "message": str(error),
                  "current": getattr(error, "state", {})}
        print(json.dumps(result, ensure_ascii=False))
        return 3
    except ASRReviewError as error:
        result = {"status": "NEEDS_REVIEW", "lecture_id": arguments.lecture_id.upper(),
                  "message": str(error), "issues": getattr(error, "issues", []),
                  "raw_path": str(getattr(error, "path", ""))}
        print(json.dumps(result, ensure_ascii=False))
        return 4
    except (ASRError, ConfigurationError, SourcePackError, IngestionError, WorkflowError, OSError, ValueError) as error:
        result = {"status": "FAILED", "lecture_id": arguments.lecture_id.upper(), "message": str(error)}
        print(json.dumps(result, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _study(arguments: argparse.Namespace) -> int:
    from .study import StudyError, parse_lessons, plan_study, build_study_candidate, generate_study_candidate
    workspace = Workspace.from_value(arguments.workspace)
    if arguments.plan_only:
        result = plan_study(workspace, arguments.course.upper(), arguments.lessons, purpose=arguments.purpose)
    elif not arguments.output:
        raise StudyError("Supply a new .docx output path")
    elif not arguments.manifest:
        raise StudyError("A complete reviewed source manifest is required; first use study --plan-only")
    else:
        manifest = _load_json(arguments.manifest)
        if parse_lessons(manifest.get("lessons", [])) != parse_lessons(arguments.lessons):
            raise StudyError("Requested lessons differ from the reviewed study manifest")
        if manifest.get("purpose", "quiz") != arguments.purpose:
            raise StudyError("Requested purpose differs from the reviewed study manifest")
    if not arguments.plan_only and arguments.candidate:
        result = build_study_candidate(workspace, arguments.course.upper(),
                                       Path(arguments.candidate).expanduser(), Path(arguments.manifest).expanduser(),
                                       Path(arguments.output).expanduser())
    elif not arguments.plan_only:
        result = generate_study_candidate(workspace, arguments.course.upper(),
                                         Path(arguments.manifest).expanduser(), Path(arguments.output).expanduser(),
                                         provider=arguments.provider, quality=arguments.quality)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _lesson_numbers(value: str) -> list[int]:
    """Parse explicit numeric selections before opening any source or provider."""
    numbers: set[int] = set()
    for part in value.split(","):
        match = re.fullmatch(r"\s*(\d{1,2})(?:\s*-\s*(\d{1,2}))?\s*", part)
        if not match:
            raise WorkflowError("Use lesson numbers such as 1-6 or 1,3-5")
        start, end = int(match[1]), int(match[2] or match[1])
        if not 1 <= start <= end <= 99:
            raise WorkflowError("Lesson ranges must be increasing and within 1-99")
        numbers.update(range(start, end + 1))
    return sorted(numbers)


def _batch_preparation(workspace: Workspace, lectures: list[str], *, plan_only: bool) -> list[dict[str, Any]]:
    """Preserve explicitly selected sources when refreshing changed packs."""
    from .utils import file_sha256
    configuration = workspace.configuration_store().load()
    pending: list[dict[str, Any]] = []
    inventory = None
    for lecture in lectures:
        path = workspace.runs_dir / lecture / "source_pack.json"
        prior = _load_json(str(path)) if path.exists() else None
        changed = not prior or any(
            not Path(source["path"]).is_file() or file_sha256(Path(source["path"])) != source["sha256"]
            for source in prior.get("sources", [])
        )
        missing_slide_evidence = bool(prior and any(
            visual.get("page") and not visual.get("evidence_id") for visual in prior.get("visuals", [])
        ))
        if not changed and not missing_slide_evidence:
            continue
        reason = "missing" if not prior else "changed sources" if changed else "slide inventory requires preparation"
        pending.append({"lecture_id": lecture, "reason": reason})
        if plan_only:
            continue
        sources = prior.get("sources", []) if prior else []
        by_role: dict[str, list[Path]] = {}
        for source in sources:
            by_role.setdefault(source.get("role", "lecture_material"), []).append(Path(source["path"]))
        audio = next(iter(by_role.get("recording", [])), None)
        transcript = next(iter(by_role.get("imported_transcript", [])), None)
        if audio is None and transcript is None:
            if inventory is None:
                inventory = discover_recordings(workspace, configuration)
            audio = recording_for_lecture(inventory, lecture)
        build_source_pack(workspace, lecture, audio=audio, transcript_path=transcript,
                          materials=by_role.get("lecture_material", []) + by_role.get("syllabus", []),
                          personal_notes=next(iter(by_role.get("personal_notes", [])), None),
                          web_snapshots=by_role.get("web_snapshot", []))
    return pending


def _generate_batch(arguments: argparse.Namespace) -> int:
    from .batch import generate_batch, load_batch_manifest
    if arguments.image_limit is not None and arguments.image_limit < 0:
        raise WorkflowError("image_limit must be a non-negative integer")
    workspace = Workspace.from_value(arguments.workspace)
    saved: dict[str, Any] = {}
    if arguments.resume:
        if not re.fullmatch(r"[A-Z0-9]+-[0-9a-f]{12}", arguments.resume):
            raise WorkflowError("Use a batch ID returned by generate-batch")
        saved = load_batch_manifest(workspace, arguments.resume)
        if saved.get("course_code") != arguments.course.upper():
            raise WorkflowError("Resume batch belongs to a different course")
    if arguments.lessons:
        numbers = _lesson_numbers(arguments.lessons)
    elif saved:
        numbers = [int(lecture.rsplit("-L", 1)[1]) for lecture in saved["lectures"]]
    else:
        raise WorkflowError("Supply --lessons or --resume BATCH_ID")
    lectures = [f"{arguments.course.upper()}-L{number:02d}" for number in numbers]
    if arguments.stop_after is not None and arguments.stop_after not in numbers:
        raise WorkflowError('--stop-after must be one of the selected lessons')
    pending = _batch_preparation(workspace, lectures, plan_only=arguments.plan_only)
    if arguments.plan_only and pending:
        print(json.dumps({"status": "NEEDS_PREPARATION", "lectures": lectures, "sources": pending,
                          "provider_invoked": False, "published_to_word": False}, ensure_ascii=False, indent=2))
        return 0
    options = saved.get("request_options", {})
    budget_values = _load_json(arguments.budgets) if arguments.budgets else saved.get("budgets")
    if budget_values is not None:
        from .batch import BatchBudget
        from dataclasses import fields
        if set(budget_values) - {field.name for field in fields(BatchBudget)}:
            raise WorkflowError("Unknown batch budget field; use documented selected-model limits")
    result = generate_batch(
        workspace, arguments.course.upper(), numbers,
        provider=arguments.provider or saved.get("requested_model", {}).get("provider"),
        quality=arguments.quality or saved.get("requested_model", {}).get("quality", "standard"),
        selection=_load_json(arguments.selection) if arguments.selection else saved.get("selection"),
        budgets=budget_values,
        instructions=arguments.instructions if arguments.instructions is not None else options.get("instructions", ""),
        image_limit=arguments.image_limit if arguments.image_limit is not None else options.get("image_limit"),
        plan_only=arguments.plan_only, batch_id=arguments.resume, build_word=arguments.build_word,
        repair_missing=bool(arguments.resume or arguments.build_word),
        pause_after=f'{arguments.course.upper()}-L{arguments.stop_after:02d}' if arguments.stop_after is not None else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _natural_request(arguments: argparse.Namespace) -> int:
    workspace = _workspace(arguments)
    from .onboarding import prepare_conversation
    configuration = workspace.configuration_store().load()
    if arguments.command not in configuration["providers"]["configured"] or not configuration["providers"].get("default"):
        configuration = prepare_conversation(workspace, arguments.command)
    if not arguments.message:
        return launch_chat(workspace, configuration, arguments.command)
    message = " ".join(arguments.message).strip()
    lecture = next(iter(re.findall(r"\b([A-Za-z0-9]+-L\d{1,3})\b", message)), "")
    lower = message.casefold()
    # Keep the existing generation shortcut. Other requests enter the conversation
    # rather than accidentally generating a lecture mentioned in a question.
    generation = re.search(r"\b(genera|generate|crea|create|prepare|prepara)\b", lower)
    if not lecture or not generation:
        return launch_chat(workspace, configuration, arguments.command, message)
    if any(token in lower for token in ("revisione", "full review", "full-review")):
        quality = "revisione"
    elif any(token in lower for token in ("approfondito", "deep", "scoped review", "scoped-review")):
        quality = "approfondito"
    elif any(token in lower for token in ("base", "veloce", "fast")):
        quality = "base"
    else:
        quality = "standard"
    arguments.lecture_id = lecture.upper()
    return _generate(arguments, provider=arguments.command, quality=quality)


def main(argv: Sequence[str] | None = None) -> int:
    if os.environ.get("MYNOTES_GENERATION_WORKER") == "1":
        print("Note-writing workers cannot invoke MyNotes. Return the structured notes from the supplied evidence; do not coordinate another workflow.")
        return 3
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "capture":
            from .capture import run_cli
            return run_cli(arguments)
        if arguments.command == "setup":
            from .onboarding import setup_cli
            return setup_cli(Workspace.from_value(arguments.workspace), provider=arguments.provider)
        if arguments.command == "init":
            workspace = _workspace(arguments)
            print(f"Initialized private workspace: {workspace.root}")
            print("Run mynotes configure to begin resumable onboarding.")
            return 0
        if arguments.command == "configure":
            return _configure(arguments)
        if arguments.command == "preferences":
            from .preferences import save_preferences
            print(json.dumps(save_preferences(Workspace.from_value(arguments.workspace), arguments.provider, arguments.model, arguments.language,
                                              effort=arguments.effort, preset=arguments.preset), ensure_ascii=False))
            return 0
        if arguments.command == "models":
            from .models import catalog
            print(json.dumps(catalog(arguments.provider, saved=arguments.saved, saved_effort=arguments.saved_effort,
                                     refresh=arguments.refresh), ensure_ascii=False, indent=2))
            return 0
        if arguments.command == "profile":
            from .preferences import save_background
            print(json.dumps(save_background(Workspace.from_value(arguments.workspace), arguments.background or "", skip=arguments.skip), ensure_ascii=False))
            return 0
        if arguments.command == "style":
            return _style(arguments)
        if arguments.command == "course":
            return _course(arguments)
        if arguments.command == "config":
            return _config(arguments)
        if arguments.command in {"process", "redo"}:
            return _process(arguments)
        if arguments.command == "discover":
            return _discover(arguments, apply_renames=False)
        if arguments.command == "normalize-recordings":
            return _discover(arguments, apply_renames=arguments.apply)
        if arguments.command == "prepare":
            return _prepare(arguments)
        if arguments.command == "transcription-queue":
            from .transcription_queue import run_cli as run_queue
            return run_queue(arguments)
        if arguments.command in {"transcribe", "transcription-status"}:
            return _transcription(arguments)
        if arguments.command == "study":
            from .study import StudyError
            try:
                return _study(arguments)
            except StudyError as error:
                print(f"mynotes: {error}")
                return 2
        if arguments.command == "study-schema":
            from .study import study_schema
            print(json.dumps(study_schema(), ensure_ascii=False, indent=2))
            return 0
        if arguments.command == "study-verify":
            from .study import StudyError, finalize_study_candidate
            try:
                result = finalize_study_candidate(Path(arguments.candidate).expanduser(), render=not arguments.no_render,
                                                 editorial_reviewed=arguments.editorial_reviewed,
                                                 visual_reviewed=arguments.visual_reviewed, word_verified=arguments.word_verified)
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
                return 0
            except StudyError as error:
                print(f"mynotes: {error}")
                return 2
        if arguments.command == "generate":
            return _generate(arguments)
        if arguments.command == "generate-batch":
            return _generate_batch(arguments)
        if arguments.command == "batch-status":
            from .batch_recovery import batch_status
            result=batch_status(Workspace.from_value(arguments.workspace),arguments.batch_id)
            if arguments.json:print(json.dumps(result,ensure_ascii=False,indent=2))
            else:
                print(f"Batch {result['batch_id']}: {result['status']}")
                for lesson in result['lessons']:print(f"{lesson['lecture_id']}: {lesson['status']}")
                if result.get('problem'):print(json.dumps(result['problem'],ensure_ascii=False))
            return 0
        if arguments.command == "build":
            print(json.dumps(build_word_candidate(_workspace(arguments), arguments.lecture_id), ensure_ascii=False, sort_keys=True))
            return 0
        if arguments.command == "publish":
            candidate = Path(arguments.candidate).expanduser() if arguments.candidate else None
            print(json.dumps(publish_word_candidate(_workspace(arguments), arguments.lecture_id, candidate, accept_current_word=arguments.accept_current_word), ensure_ascii=False, sort_keys=True))
            return 0
        if arguments.command == "history":
            print(json.dumps(publication_history(_workspace(arguments), arguments.course), ensure_ascii=False, indent=2))
            return 0
        if arguments.command == "restore":
            print(json.dumps(restore_backup(_workspace(arguments), arguments.backup_id), ensure_ascii=False, sort_keys=True))
            return 0
        if arguments.command == "recover":
            print(json.dumps(recover_publications(_workspace(arguments)), ensure_ascii=False, indent=2))
            return 0
        if arguments.command in {"codex", "agy", "claude"}:
            return _natural_request(arguments)
        if arguments.command == "migrate":
            return _migrate(arguments)
        if arguments.command == "integrate":
            output = install_integration(arguments.platform, Path(arguments.destination), action=arguments.action)
            print(json.dumps(output, ensure_ascii=False, sort_keys=True))
            return 0
        if arguments.command == "usage":
            from .usage import batch_usage, lecture_usage
            if bool(arguments.batch) == bool(arguments.lecture_id):
                raise WorkflowError("Supply one lecture ID or --batch BATCH_ID")
            workspace = Workspace.from_value(arguments.workspace)
            report = batch_usage(workspace, arguments.batch) if arguments.batch else lecture_usage(workspace, arguments.lecture_id)
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if arguments.command == "status":
            workspace = _workspace(arguments)
            print(json.dumps(status(workspace), ensure_ascii=False))
            return 0
        if arguments.command == "chat":
            workspace = _workspace(arguments)
            configuration = workspace.configuration_store().load()
            return launch_chat(workspace, configuration, arguments.provider, arguments.message)
        if arguments.command == "doctor":
            from .onboarding import doctor
            workspace = Workspace.from_value(arguments.workspace)
            report = doctor(workspace, provider=arguments.provider)
            if arguments.json:
                print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
                return 0 if report["ready"] else 1
            for line in workspace.diagnostics():
                print(line)
            for name, check in report["checks"].items():
                print(f"{name}: {check['status']} — {check.get('action', '')}")
            # Optional recording/rendering components should not block read-only
            # course/style tasks. The agent checks the components needed by a task.
            return 0 if workspace.is_initialized() else 1
        if arguments.command is None:
            workspace = Workspace.from_value()
            from .onboarding import prepare_conversation, setup_cli
            workspace.initialize()
            configuration = workspace.configuration_store().load()
            if not configuration["providers"].get("default"):
                return setup_cli(workspace)
            configuration = prepare_conversation(workspace)
            return launch_chat(workspace, configuration, None)
    except EOFError:
        print("Onboarding paused. Saved groups can be resumed with mynotes configure.")
        return 1
    except ASRBusyError as error:
        print(json.dumps({"status": "BUSY", "message": str(error), "current": error.state}, ensure_ascii=False))
        return 3
    except (ConfigurationError, WorkflowError, ProviderError, PublicationError, ChatError, MigrationError, IntegrationError, IngestionError, ASRError, ExtractionError, SourcePackError, OSError, ValueError) as error:
        print(f"mynotes: {error}")
        return 2
    parser.error(f"unsupported command: {arguments.command}")
    return 2
