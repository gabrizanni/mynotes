"""Validated, versioned private configuration and resumable onboarding."""

from __future__ import annotations

import copy
import fcntl
import json
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .utils import atomic_json_write, now_compact_utc

ONBOARDING_GROUPS = ("personal", "paths", "courses", "style", "providers")
COURSE_CODE_RE = re.compile(r"^[A-Z0-9]+$")
COURSE_KINDS = {"quantitative", "theoretical", "cases", "programming", "mixed"}
QUALITY_LEVELS = {"base", "standard", "approfondito", "revisione"}
SUPPORTED_PROVIDERS = {"codex", "agy", "claude"}
STYLE_PROFILE_DEFAULTS: dict[str, Any] = {
    "writing_style": "clear_study",
    "list_density": "balanced",
    "image_policy": "essential",
    "image_limit": None,
    "question_count": 5,
    "recap": True,
    "recap_language": "it",
    # The Word design is specified in code, without a reference document.
    "page_size": "A4",
    "margin_cm": 1.5,
    "body_font": "Aptos",
    "heading_font": "Aptos Display",
    "body_pt": 11,
    "body_after_pt": 3,
    "body_line_spacing": 1.15,
    "lesson_heading_pt": 16,
    "section_heading_pt": 14,
    "subsection_heading_pt": 12,
    "heading_before_pt": 8,
    "heading_after_pt": 4,
    "heading_color": "0F4761",
    "accent_color": "ED7D31",
    "question_color": "4F81BD",
    "caption_color": "595959",
    "page_numbers": True,
    "page_number_position": "center",
}
EDITABLE_STYLE_FIELDS = {
    "writing_style": {"clear_study", "concise", "detailed"},
    "list_density": {"balanced", "prose_first", "list_first"},
    "image_policy": {"essential", "none", "explanatory"},
    "recap_language": {"it", "en", "same_as_notes"},
}


class ConfigurationError(ValueError):
    """Raised when private configuration does not meet the stable contract."""


def clean_path(value: str) -> str:
    """Normalize pasted or Finder-quoted paths without requiring they exist."""
    stripped = value.strip()
    if len(stripped) > 1 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        stripped = stripped[1:-1]
    return str(Path(stripped).expanduser())


def provider_availability(provider: str) -> dict[str, str | bool]:
    """Report local launcher detection; never silently selects another provider."""
    if provider not in SUPPORTED_PROVIDERS:
        raise ConfigurationError(f"Unsupported provider: {provider}")
    executable = shutil.which(provider)
    return {
        "provider": provider,
        "available": executable is not None,
        "detail": executable or "local launcher not detected",
    }


def course_template() -> dict[str, Any]:
    return {
        "code": "",
        "name": "",
        "aliases": [],
        "languages": {"lesson": "en", "notes": "en"},
        "kind": "mixed",
        "instructors": [],
        "modules": [],
        "assessment": "",
        "context": "",
        "context_setup": "not_requested",
        "preparation": "",
        "difficult_topics": [],
        "folders": {
            "materials": "",
            "personal_notes": "",
            "handout": "",
            "syllabus": "",
        },
        # Exact per-lesson sources take precedence over filename discovery.
        # Paths remain private workspace configuration, never bundled with code.
        "lesson_sources": {},
        "files": {"personal_notes": "", "handout": ""},
        "files_explained": False,
        "publication": {"mode": "automatic"},
        "style": {},
        "preferences": {"persistent_instructions": []},
        "web_access": "disabled",
        "transcription": {
            "language": "en",
            "vocabulary": [],
            "accent": "",
            "primary_engine": "argmax",
            "primary_model": "large-v3-v20240930_626MB",
            "secondary_engine": None,
            "secondary_model": "v2",
            "extra_pass_engine": None,
            "extra_pass_model": "mlx-community/whisper-large-v3-mlx",
            "agreement_threshold": 0.72,
            "diarization_engine": None,
            "professor_reference": "",
            "tools": {}
        },
    }


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigurationError(f"{field} must be a list of text values")
    return [item.strip() for item in value if item.strip()]


def normalize_style_profile(value: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """Validate user-editable style fields; visual rules remain code-owned."""
    if not isinstance(value, dict):
        raise ConfigurationError("Style profile must be an object")
    allowed = set(EDITABLE_STYLE_FIELDS) | {"question_count", "recap", "image_limit"}
    unknown = set(value) - allowed
    if unknown:
        raise ConfigurationError(f"Unknown style profile field: {', '.join(sorted(unknown))}")
    result = {} if partial else {key: STYLE_PROFILE_DEFAULTS[key] for key in allowed}
    result.update(value)
    for key, choices in EDITABLE_STYLE_FIELDS.items():
        if key in result and (not isinstance(result[key], str) or result[key] not in choices):
            raise ConfigurationError(f"Style {key} must be one of: {', '.join(sorted(choices))}")
    if "question_count" in result and (
        type(result["question_count"]) is not int or not 1 <= result["question_count"] <= 5
    ):
        raise ConfigurationError("Style question_count must be an integer between 1 and 5")
    if "recap" in result and type(result["recap"]) is not bool:
        raise ConfigurationError("Style recap must be true or false")
    if "image_limit" in result and result["image_limit"] is not None and (
        type(result["image_limit"]) is not int or result["image_limit"] < 0
    ):
        raise ConfigurationError("Style image_limit must be a non-negative integer or null")
    return result


def effective_style(configuration: dict[str, Any], course_code: str | None = None) -> dict[str, Any]:
    """Resolve the built-in study design, global choices, then course overrides."""
    profile = copy.deepcopy(STYLE_PROFILE_DEFAULTS)
    global_style = configuration.get("style", {})
    profile.update(normalize_style_profile(global_style.get("profile", {}), partial=True))
    if course_code is not None:
        code = course_code.upper()
        course = next((item for item in configuration.get("courses", []) if item["code"] == code), None)
        if course is None:
            raise ConfigurationError(f"Course not found: {code}")
        overrides = {key: value for key, value in course.get("style", {}).items()
                     if key in EDITABLE_STYLE_FIELDS or key in {"question_count", "recap", "image_limit"}}
        profile.update(normalize_style_profile(overrides, partial=True))
    return profile


def normalize_course(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError("A course must be an object")
    course = copy.deepcopy(course_template())
    for key, item in value.items():
        if key in course and isinstance(course[key], dict) and isinstance(item, dict):
            course[key].update(item)
        elif key in course:
            course[key] = item
    course["code"] = str(course["code"]).upper().strip()
    if not COURSE_CODE_RE.fullmatch(course["code"]):
        raise ConfigurationError("Course code must contain only uppercase letters and numbers")
    course["name"] = str(course["name"]).strip()
    if not course["name"]:
        raise ConfigurationError("Course name is required")
    if not isinstance(course["context"], str):
        raise ConfigurationError("Course context must be text")
    if course["context_setup"] not in {"not_requested", "pending", "deferred", "complete"}:
        raise ConfigurationError("Invalid course context setup state")
    if course["context"].strip() and course["context_setup"] in {"pending", "deferred"}:
        course["context_setup"] = "complete"
    if "speaker_focus" in course and course["speaker_focus"] not in {"lecture", "professor"}:
        raise ConfigurationError("speaker_focus must be lecture or professor")
    course["aliases"] = _string_list(course["aliases"], "course aliases")
    course["instructors"] = _string_list(course["instructors"], "course instructors")
    course["modules"] = _string_list(course["modules"], "course modules")
    course["difficult_topics"] = _string_list(course["difficult_topics"], "course difficult topics")
    if course["kind"] not in COURSE_KINDS:
        raise ConfigurationError(f"Course kind must be one of: {', '.join(sorted(COURSE_KINDS))}")
    if not isinstance(course["languages"], dict) or not all(
        isinstance(course["languages"].get(field), str) and course["languages"][field].strip()
        for field in ("lesson", "notes")
    ):
        raise ConfigurationError("Course languages must include non-empty lesson and notes values")
    if not isinstance(course["folders"], dict):
        raise ConfigurationError("Course folders must be an object")
    # Legacy style-reference paths are ignored and removed on the next save;
    # generating notes never reads an external style document.
    course["folders"].pop("style_reference", None)
    for field in course_template()["folders"]:
        path = course["folders"].get(field, "")
        if not isinstance(path, str):
            raise ConfigurationError(f"Course folder {field} must be text")
        course["folders"][field] = clean_path(path) if path.strip() else ""
    file_settings = course.get("files")
    if not isinstance(file_settings, dict):
        raise ConfigurationError("Course files must be an object")
    for role in ("personal_notes", "handout"):
        value = file_settings.get(role, "")
        if not isinstance(value, str):
            raise ConfigurationError("Course file paths must be text")
        if value.strip():
            from .note_files import file_path
            file_settings[role] = str(file_path(value, handout=role == "handout"))
        else:
            file_settings[role] = ""
    if file_settings["personal_notes"] and file_settings["personal_notes"] == file_settings["handout"]:
        raise ConfigurationError("Personal notebook and final handout must be different files")
    if type(course.get("files_explained")) is not bool:
        raise ConfigurationError("Course files_explained must be boolean")
    if not isinstance(course.get("publication"), dict) or course["publication"].get("mode") not in {"automatic", "review"}:
        raise ConfigurationError("Publication mode must be automatic or review")
    lesson_sources = course.get("lesson_sources")
    if not isinstance(lesson_sources, dict):
        raise ConfigurationError("Course lesson_sources must be an object")
    normalized_sources: dict[str, dict[str, Any]] = {}
    for lesson, sources in lesson_sources.items():
        try:
            number = int(str(lesson).removeprefix("L").removeprefix("l"))
        except ValueError as error:
            raise ConfigurationError(f"Invalid lesson_sources key: {lesson}") from error
        if number < 1 or number > 999 or not isinstance(sources, dict):
            raise ConfigurationError(f"Invalid lesson_sources entry: {lesson}")
        materials = _string_list(sources.get("materials", []), "lesson materials")
        personal_notes = sources.get("personal_notes", "")
        if not isinstance(personal_notes, str):
            raise ConfigurationError("Lesson personal_notes must be a path or empty text")
        normalized_sources[str(number)] = {
            "materials": list(dict.fromkeys(clean_path(path) for path in materials)),
            "personal_notes": clean_path(personal_notes) if personal_notes.strip() else "",
        }
    course["lesson_sources"] = normalized_sources
    if not isinstance(course["style"], dict) or not isinstance(course["preferences"], dict):
        raise ConfigurationError("Course style and preferences must be objects")
    normalize_style_profile(
        {key: value for key, value in course["style"].items()
         if key in EDITABLE_STYLE_FIELDS or key in {"question_count", "recap", "image_limit"}},
        partial=True,
    )
    course["preferences"]["persistent_instructions"] = _string_list(
        course["preferences"].get("persistent_instructions", []),
        "course persistent instructions",
    )
    if course["web_access"] not in {"disabled", "linked_resources"}:
        raise ConfigurationError("Course web access must be disabled or linked_resources")
    if not isinstance(course["transcription"], dict):
        raise ConfigurationError("Course transcription settings must be an object")
    if not isinstance(course["transcription"].get("language"), str):
        raise ConfigurationError("Course transcription language must be text")
    course["transcription"]["vocabulary"] = _string_list(
        course["transcription"].get("vocabulary", []), "course transcription vocabulary"
    )
    transcription = course["transcription"]
    for field in (
        "primary_engine", "primary_model", "secondary_model", "extra_pass_model",
        "accent", "professor_reference",
    ):
        if not isinstance(transcription.get(field), str):
            raise ConfigurationError(f"Course transcription {field} must be text")
    for field in ("secondary_engine", "extra_pass_engine", "diarization_engine"):
        if transcription.get(field) is not None and not isinstance(transcription[field], str):
            raise ConfigurationError(f"Course transcription {field} must be text or null")
    threshold = transcription.get("agreement_threshold")
    if not isinstance(threshold, (int, float)) or not 0 <= float(threshold) <= 1:
        raise ConfigurationError("Course transcription agreement_threshold must be between 0 and 1")
    transcription["agreement_threshold"] = float(threshold)
    if not isinstance(transcription.get("tools"), dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in transcription["tools"].items()
    ):
        raise ConfigurationError("Course transcription tools must map names to paths")
    transcription["professor_reference"] = (
        clean_path(transcription["professor_reference"])
        if transcription["professor_reference"].strip()
        else ""
    )
    return course


def merge_course(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Apply a course edit without dropping fields the user did not mention."""
    if not isinstance(patch, dict):
        raise ConfigurationError("Course edit must be an object")
    merged = copy.deepcopy(existing)
    for key, value in patch.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return normalize_course(merged)


def validate_configuration(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized copy or fail before anything is written."""
    if not isinstance(raw, dict):
        raise ConfigurationError("Configuration must be an object")
    configuration = copy.deepcopy(raw)
    if configuration.get("workspace_schema_version") != 2:
        raise ConfigurationError("Unsupported workspace configuration version")
    onboarding = configuration.get("onboarding")
    if not isinstance(onboarding, dict) or onboarding.get("status") not in {
        "not_started",
        "in_progress",
        "complete",
    }:
        raise ConfigurationError("Invalid onboarding state")
    completed = _string_list(onboarding.get("completed_groups", []), "completed onboarding groups")
    if any(group not in ONBOARDING_GROUPS for group in completed):
        raise ConfigurationError("Invalid completed onboarding group")
    onboarding["completed_groups"] = list(dict.fromkeys(completed))
    next_group = onboarding.get("next_group")
    if next_group is not None and next_group not in ONBOARDING_GROUPS:
        raise ConfigurationError("Invalid next onboarding group")
    personal = configuration.get("personal")
    if not isinstance(personal, dict) or not isinstance(personal.get("interface_language"), str):
        raise ConfigurationError("Personal interface language is required")
    if personal.get("primary_goal") not in {"exam", "understanding", "exercises", "work", "personal"}:
        raise ConfigurationError("Invalid primary goal")
    if not isinstance(personal.get("background", ""), str):
        raise ConfigurationError("Personal background must be text")
    if type(personal.get("background_confirmed", False)) is not bool:
        raise ConfigurationError("Background confirmation must be true or false")
    paths = configuration.get("paths")
    if not isinstance(paths, dict):
        raise ConfigurationError("Paths must be an object")
    paths["recording_dirs"] = [clean_path(path) for path in _string_list(paths.get("recording_dirs", []), "recording_dirs")]
    ingestion = configuration.setdefault(
        "ingestion", {"stable_seconds": 90, "audio_extensions": [".m4a", ".wav", ".mp3", ".caf"]}
    )
    if not isinstance(ingestion, dict):
        raise ConfigurationError("Ingestion settings must be an object")
    stable_seconds = ingestion.get("stable_seconds", 90)
    if not isinstance(stable_seconds, int) or stable_seconds < 0:
        raise ConfigurationError("Ingestion stable_seconds must be a non-negative integer")
    ingestion["stable_seconds"] = stable_seconds
    extensions = _string_list(
        ingestion.get("audio_extensions", [".m4a", ".wav", ".mp3", ".caf"]),
        "ingestion audio_extensions",
    )
    ingestion["audio_extensions"] = [value.casefold() if value.startswith(".") else f".{value.casefold()}" for value in extensions]
    style = configuration.get("style")
    if not isinstance(style, dict) or style.get("quality") not in QUALITY_LEVELS:
        raise ConfigurationError("Invalid default quality")
    if not isinstance(style.get("freeform_preferences"), str):
        raise ConfigurationError("Style preferences must be text")
    # Old version-2 workspaces remain loadable; the profile is filled with
    # deterministic defaults instead of consulting any external Word file.
    style["profile"] = normalize_style_profile(style.get("profile", {}))
    providers = configuration.get("providers")
    if not isinstance(providers, dict) or not isinstance(providers.get("configured"), dict):
        raise ConfigurationError("Providers must include a configured object")
    selected = providers.get("default")
    if selected is not None and selected not in SUPPORTED_PROVIDERS:
        raise ConfigurationError("Default provider must be codex, agy, claude, or null")
    if providers.get("quota_behavior") not in {"pause_and_resume", "stop"}:
        raise ConfigurationError("Invalid quota behavior")
    for provider, options in providers["configured"].items():
        if provider not in SUPPORTED_PROVIDERS or not isinstance(options, dict):
            raise ConfigurationError("Invalid configured provider")
        if "model" in options and not isinstance(options["model"], str):
            raise ConfigurationError("Provider model must be text")
        if "effort" in options and not isinstance(options["effort"], str):
            raise ConfigurationError("Provider effort must be text")
    courses = configuration.get("courses")
    if not isinstance(courses, list):
        raise ConfigurationError("Courses must be a list")
    normalized_courses = [normalize_course(course) for course in courses]
    codes = [course["code"] for course in normalized_courses]
    if len(codes) != len(set(codes)):
        raise ConfigurationError("Course codes must be unique")
    names = [course["code"].casefold() for course in normalized_courses]
    names.extend(alias.casefold() for course in normalized_courses for alias in course["aliases"])
    if len(names) != len(set(names)):
        raise ConfigurationError("Course aliases must not collide with any code or alias")
    configuration["courses"] = normalized_courses
    return configuration


def merge_group_answers(configuration: dict[str, Any], group: str, answers: dict[str, Any]) -> dict[str, Any]:
    """Apply one onboarding group, retaining answers from an interrupted run."""
    if group not in ONBOARDING_GROUPS:
        raise ConfigurationError(f"Unknown onboarding group: {group}")
    if not isinstance(answers, dict):
        raise ConfigurationError("Onboarding answers must be an object")
    updated = copy.deepcopy(configuration)
    if group == "courses":
        updated["courses"] = answers.get("courses", [])
    elif group == "style":
        style_answers = {key: value for key, value in answers.items() if key != "course_profiles"}
        updated["style"].update(style_answers)
        course_profiles = answers.get("course_profiles", {})
        if not isinstance(course_profiles, dict):
            raise ConfigurationError("Course style choices must be an object")
        for code, patch in course_profiles.items():
            if not isinstance(code, str):
                raise ConfigurationError("Course style code must be text")
            target = next((item for item in updated["courses"] if item["code"] == code.upper()), None)
            if target is None:
                raise ConfigurationError(f"Course not found: {code}")
            target["style"].update(normalize_style_profile(patch, partial=True))
    else:
        updated[group].update(answers)
    completed = updated["onboarding"]["completed_groups"]
    if group not in completed:
        completed.append(group)
    remaining = [item for item in ONBOARDING_GROUPS if item not in completed]
    updated["onboarding"]["next_group"] = remaining[0] if remaining else None
    updated["onboarding"]["status"] = "in_progress" if remaining else "complete"
    return validate_configuration(updated)


@dataclass
class ConfigurationStore:
    """Persist one workspace configuration with immutable prior versions."""

    path: Path
    history_dir: Path

    def load(self) -> dict[str, Any]:
        with self.path.open(encoding="utf-8") as handle:
            return validate_configuration(json.load(handle))

    def _persist_locked(self, normalized: dict[str, Any]) -> None:
        """Write history and settings while the caller owns settings.lock."""
        if self.path.exists():
            snapshot = self.history_dir / f"{now_compact_utc()}-{uuid.uuid4().hex[:8]}-settings.json"
            self.history_dir.mkdir(parents=True, exist_ok=True)
            atomic_json_write(snapshot, json.loads(self.path.read_text(encoding="utf-8")))
        atomic_json_write(self.path, normalized)

    def save(self, configuration: dict[str, Any]) -> dict[str, Any]:
        """Replace an explicitly supplied full configuration under the write lock."""
        normalized = self._resolve_relative_paths(validate_configuration(configuration))
        lock_path = self.path.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                self._persist_locked(normalized)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return normalized

    def update(self, transform: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        """Apply an edit to the latest settings in one atomic read-modify-write.

        The callback must not call save/update on this store: it already owns
        the non-reentrant file lock. Unchanged edits do not create history.
        """
        lock_path = self.path.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                current = self.load()
                edited = transform(copy.deepcopy(current))
                normalized = self._resolve_relative_paths(validate_configuration(edited))
                if normalized != current:
                    self._persist_locked(normalized)
                return normalized
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _resolve_relative_paths(self, configuration: dict[str, Any]) -> dict[str, Any]:
        base = self.path.parent.parent
        result = copy.deepcopy(configuration)
        def resolve(value: str) -> str:
            path = Path(value).expanduser()
            return str((base / path).resolve()) if value and not path.is_absolute() else str(path)
        result["paths"]["recording_dirs"] = [resolve(item) for item in result["paths"]["recording_dirs"]]
        for course in result["courses"]:
            for key, value in course["folders"].items():
                course["folders"][key] = resolve(value) if value else ""
            reference = course["transcription"].get("professor_reference", "")
            course["transcription"]["professor_reference"] = resolve(reference) if reference else ""
        return result

    def history(self) -> list[Path]:
        return sorted(self.history_dir.glob("*-settings.json")) if self.history_dir.is_dir() else []

    def restore(self, name: str) -> dict[str, Any]:
        candidate = self.history_dir / Path(name).name
        if candidate.parent != self.history_dir or not candidate.is_file():
            raise ConfigurationError("Configuration history entry not found")
        with candidate.open(encoding="utf-8") as handle:
            return self.save(json.load(handle))


def configured_directories(configuration: dict[str, Any]) -> Iterable[Path]:
    for path in configuration["paths"]["recording_dirs"]:
        yield Path(path)
    for course in configuration["courses"]:
        for path in course["folders"].values():
            if path:
                yield Path(path)
