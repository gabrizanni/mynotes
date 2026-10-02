"""Public command-line entry point for MyNotes configuration."""

from __future__ import annotations

import argparse
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
    merge_course,
    merge_group_answers,
    provider_availability,
)
from .workspace import Workspace
from .workflow import WorkflowError, generate_candidate, process, status
from .asr import ASRError
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


def _interactive_group(group: str, configuration: dict[str, Any]) -> dict[str, Any]:
    """Ask a small, provider-free batch of questions and return its answers."""
    if group == "personal":
        return {
            "interface_language": input("Interface language [en]: ").strip() or "en",
            "institution": input("Institution (or 'none'): ").strip() or None,
            "study_program": input("Study program (optional): ").strip() or None,
            "study_period": input("Study period (optional): ").strip() or None,
            "primary_goal": input("Primary goal [exam/understanding/exercises/work/personal]: ").strip()
            or "understanding",
        }
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
        return {
            "quality": input("Default quality [standard]: ").strip() or "standard",
            "freeform_preferences": input("Additional style preferences (optional): ").strip(),
        }
    if group == "providers":
        provider = input("Default provider [codex/agy/none]: ").strip().lower() or "none"
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
    init = subparsers.add_parser("init", help="initialize an empty private workspace")
    init.add_argument("--workspace", help="private workspace path")
    configure = subparsers.add_parser("configure", help="run or resume configuration")
    configure.add_argument("--workspace", help="private workspace path")
    configure.add_argument("--answers", help="JSON answers for one onboarding group")
    configure.add_argument("--group", choices=ONBOARDING_GROUPS, help="onboarding group for --answers or interactive entry")
    configure.add_argument("--instruction", help="natural-language preference to persist safely")
    configure.add_argument("--course", help="course code for a persistent instruction")
    configure.add_argument("--create-missing-dirs", action="store_true", help="create displayed configured folders")
    course = subparsers.add_parser("course", help="add or edit a configured course")
    course_subparsers = course.add_subparsers(dest="course_command", required=True)
    for name, help_text in (("add", "add a course from JSON"), ("edit", "replace a course from JSON")):
        command = course_subparsers.add_parser(name, help=help_text)
        command.add_argument("--workspace", help="private workspace path")
        command.add_argument("--file", help="course JSON file; omit for guided questions")
        if name == "edit":
            command.add_argument("code", help="course code to replace")
        command.add_argument("--create-missing-dirs", action="store_true")
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
            choices=("codex", "agy"),
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
    prepare.add_argument("--material", action="append", default=[], help="PDF, PPTX, DOCX, text, or notebook; repeatable")
    prepare.add_argument("--personal-notes", help="DOCX, Markdown, or text containing the lesson block")
    prepare.add_argument("--web-snapshot", action="append", default=[], help="local snapshot of a linked online source; repeatable")
    generate = subparsers.add_parser("generate", help="generate a reviewable candidate from a prepared source pack")
    generate.add_argument("lecture_id", help="COURSE-LNN")
    generate.add_argument("--workspace", help="private workspace path")
    generate.add_argument("--source-pack", help="prepared source-pack JSON; defaults to the lecture run")
    generate.add_argument("--provider", choices=("codex", "agy"), help="provider override")
    generate.add_argument("--quality", default="standard", help="base, standard, approfondito, or revisione")
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
    for provider in ("codex", "agy"):
        natural = subparsers.add_parser(provider, help=f"run a simple natural-language request with {provider}")
        natural.add_argument("message", nargs="*", help='omit to open a chat, or say e.g. "genera ABC-L01"')
        natural.add_argument("--workspace", help="private workspace path")
        natural.add_argument("--source-pack", help="prepared source-pack JSON; defaults to the lecture run")
    doctor = subparsers.add_parser("doctor", help="inspect a private workspace without changing it")
    doctor.add_argument("--workspace", help="private workspace path")
    migrate = subparsers.add_parser("migrate", help="inventory or copy a legacy installation")
    migrate_subparsers = migrate.add_subparsers(dest="migrate_command", required=True)
    legacy = migrate_subparsers.add_parser("legacy", help="migrate a legacy local installation")
    legacy.add_argument("--source", help="legacy project directory (required for --dry-run)")
    legacy.add_argument("--workspace", help="new private workspace path")
    choice = legacy.add_mutually_exclusive_group(required=True)
    choice.add_argument("--dry-run", action="store_true")
    choice.add_argument("--apply-manifest", help="exact reviewed manifest produced by --dry-run")
    integrate = subparsers.add_parser("integrate", help="export a platform integration without private data")
    integrate.add_argument("platform", choices=("codex", "agy"))
    integrate.add_argument("--destination", default=".", help="directory receiving only the integration instructions")
    integrate.add_argument("--action", choices=("install", "update", "remove"), default="install")
    status_command = subparsers.add_parser("status", help="show workflow state from a second terminal")
    status_command.add_argument("--workspace", help="private workspace path")
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
    workspace = _workspace(arguments)
    store = workspace.configuration_store()
    configuration = store.load()
    course = _load_json(arguments.file) if arguments.file else _interactive_course_entry()
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


def _interactive_course_entry() -> dict[str, Any]:
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
    course["folders"]["style_reference"] = input("Reference notes DOCX/PDF (optional): ").strip()
    course["transcription"]["accent"] = input("Accent/speech characteristics (optional): ").strip()
    course["transcription"]["vocabulary"] = [item.strip() for item in input("Technical vocabulary, comma-separated (optional): ").split(",") if item.strip()]
    persistent = input("Persistent note preferences for this course (optional): ").strip()
    course["preferences"]["persistent_instructions"] = [persistent] if persistent else []
    return course


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
    if audio is None:
        inventory = discover_recordings(workspace, configuration)
        audio = recording_for_lecture(inventory, arguments.lecture_id.upper())
    pack = build_source_pack(
        workspace, arguments.lecture_id.upper(), audio=audio,
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
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _natural_request(arguments: argparse.Namespace) -> int:
    if not arguments.message:
        workspace = _workspace(arguments)
        return launch_chat(workspace, workspace.configuration_store().load(), arguments.command)
    message = " ".join(arguments.message).strip()
    lecture = next(iter(re.findall(r"\b([A-Za-z0-9]+-L\d{1,3})\b", message)), "")
    if not lecture:
        raise WorkflowError('Include one lecture id, e.g. mynotes agy "genera ABC-L01"')
    lower = message.casefold()
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
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "init":
            workspace = _workspace(arguments)
            print(f"Initialized private workspace: {workspace.root}")
            print("Run mynotes configure to begin resumable onboarding.")
            return 0
        if arguments.command == "configure":
            return _configure(arguments)
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
        if arguments.command == "generate":
            return _generate(arguments)
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
        if arguments.command in {"codex", "agy"}:
            return _natural_request(arguments)
        if arguments.command == "migrate":
            return _migrate(arguments)
        if arguments.command == "integrate":
            output = install_integration(arguments.platform, Path(arguments.destination), action=arguments.action)
            print(json.dumps(output, ensure_ascii=False, sort_keys=True))
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
            workspace = Workspace.from_value(arguments.workspace)
            for line in workspace.diagnostics():
                print(line)
            return 0 if workspace.is_initialized() else 1
        if arguments.command is None:
            workspace = Workspace.from_value()
            workspace.initialize()
            configuration = workspace.configuration_store().load()
            if configuration["onboarding"]["status"] != "complete":
                return _configure(argparse.Namespace(workspace=None, answers=None, group=None, instruction=None, course=None, create_missing_dirs=False))
            print("Configuration is complete. Use mynotes chat to inspect the selected workspace and provider.")
            return 0
    except EOFError:
        print("Onboarding paused. Saved groups can be resumed with mynotes configure.")
        return 1
    except (ConfigurationError, WorkflowError, PublicationError, ChatError, MigrationError, IntegrationError, IngestionError, ASRError, ExtractionError, SourcePackError, OSError, ValueError) as error:
        print(f"mynotes: {error}")
        return 2
    parser.error(f"unsupported command: {arguments.command}")
    return 2
