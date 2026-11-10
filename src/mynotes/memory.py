"""One private preference store, with explicit scope and optimistic edits.

Markdown is a view, never another writable source of truth. Legacy free-form
fields stay in place and are exposed with content identifiers so they can be
replaced or removed without an AI migration.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from .configuration import ConfigurationError, effective_style, instruction_map, normalize_style_profile


class MemoryConflict(ConfigurationError):
    """The caller must reread preferences before trying a different edit."""


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _course(settings, code):
    course = next((c for c in settings.get("courses", []) if c["code"] == code.upper()), None)
    if course is None:
        raise ConfigurationError(f"Course not found: {code}")
    return course


def _validate_scope(scope, area, code):
    if scope not in {"global", "course"} or area not in {"notes", "dialogue"}:
        raise ConfigurationError("Choose scope global/course and area notes/dialogue")
    if (scope == "course") != bool(code):
        raise ConfigurationError("Course scope requires --course; global scope excludes it")


def _container(settings, code, area):
    if code:
        return _course(settings, code)["preferences"]
    return settings["style"] if area == "notes" else settings["personal"]


def _field(area):
    return "note_instructions" if area == "notes" else "dialogue_instructions"


def _legacy(settings, code):
    return (_course(settings, code)["preferences"].get("persistent_instructions", []) if code else
            [settings["style"].get("freeform_preferences", "")])


def _legacy_key(text, code=None):
    # The single global legacy block keeps its identity when edited, so a
    # course override remains attached to it. Course legacy items have no
    # descendants and can safely use content identities.
    return "legacy-" + fingerprint(text)[:24] if code else "legacy-global"


def _rules(settings, code, area):
    rules = {}
    if area == "notes":
        for text in _legacy(settings, code):
            if text.strip():
                rules[_legacy_key(text, code)] = text
    rules.update(_container(settings, code, area).get(_field(area), {}))
    return rules


def snapshot(settings, *, scope="global", area="notes", course=None):
    _validate_scope(scope, area, course)
    code = course.upper() if course else None
    own = _rules(settings, code, area)
    inherited = _rules(settings, None, area) if code else {}
    effective = {**inherited, **own}
    data = {"scope": scope, "area": area, "course": code, "inherited": inherited,
            "own": own, "effective": {key: text for key, text in effective.items() if text.strip()}}
    if area == "notes":
        data["style"] = {"inherited": effective_style(settings) if code else {},
                         "own": deepcopy(_course(settings, code)["style"] if code else settings["style"]["profile"]),
                         "effective": effective_style(settings, code)}
        data["languages"] = deepcopy(_course(settings, code)["languages"]) if code else {}
    data["revision"] = fingerprint(data)
    return data


def render_markdown(data):
    lines = [f"# MyNotes memory · {data['area']} · {data['course'] or 'global'}",
             "", f"Revision: `{data['revision']}`"]
    for name in ("inherited", "own", "effective"):
        lines += ["", f"## {name.capitalize()}"]
        lines += [f"- `{key}`: {text or '(suppressed for this scope)'}" for key, text in data[name].items()] or ["(none)"]
    if "style" in data:
        for name, values in data["style"].items():
            lines += ["", f"## Style · {name}", "", "```json", json.dumps(values, ensure_ascii=False, indent=2), "```"]
        lines += ["", "## Languages", json.dumps(data["languages"], ensure_ascii=False)]
    return "\n".join(lines) + "\n"


def _write_legacy(settings, code, texts):
    texts = [text for text in texts if text.strip()]
    if code:
        _course(settings, code)["preferences"]["persistent_instructions"] = list(dict.fromkeys(texts))
    else:
        settings["style"]["freeform_preferences"] = "\n".join(texts)


def edit(settings, *, scope, area, course=None, expected_revision=None,
         set_rules=None, remove=(), style=None, inherit=(), reset=False):
    """Apply a validated scoped edit to the caller's locked, current settings."""
    before = snapshot(settings, scope=scope, area=area, course=course)
    if expected_revision is not None and before["revision"] != expected_revision:
        raise MemoryConflict("MEMORY_CONFLICT: preferences changed; reread memory before saving")
    code = before["course"]
    set_rules = instruction_map(set_rules or {})
    if any(not isinstance(key, str) for key in remove):
        raise ConfigurationError("Removed instructions must be identifiers")
    if set(set_rules) & set(remove):
        raise ConfigurationError("Cannot set and remove the same instruction")
    if area != "notes" and (style is not None or inherit):
        raise ConfigurationError("Structured style belongs to notes, not dialogue")
    patch = normalize_style_profile(style, partial=True) if style is not None else {}
    allowed = normalize_style_profile({})
    if set(inherit) - set(allowed) or (inherit and not code):
        raise ConfigurationError("--inherit requires course scope and editable style fields")
    if set(inherit) & set(patch):
        raise ConfigurationError("Cannot set and inherit the same style field")
    target = _container(settings, code, area)
    if reset:
        target.pop(_field(area), None)
        if area == "notes":
            _write_legacy(settings, code, [])
            if code:
                for key in allowed:
                    _course(settings, code)["style"].pop(key, None)
            else:
                settings["style"]["profile"] = normalize_style_profile({})
    else:
        rules = target.get(_field(area), {}).copy()
        legacy = list(_legacy(settings, code)) if area == "notes" else []
        for key in remove:
            if key not in before["own"]:
                raise ConfigurationError(f"No own instruction: {key}; override an inherited rule instead")
            rules.pop(key, None)
            legacy = [text for text in legacy if _legacy_key(text, code) != key]
        for key, text in set_rules.items():
            matched = any(_legacy_key(old, code) == key for old in legacy)
            if matched:
                rules.pop(key, None)
                legacy = [text if _legacy_key(old, code) == key else old for old in legacy]
            else:
                if key.startswith("legacy-") and key not in before["inherited"] and key not in before["own"]:
                    raise ConfigurationError(f"Unknown legacy instruction: {key}")
                rules[key] = text
        if rules:
            target[_field(area)] = rules
        else:
            target.pop(_field(area), None)
        if area == "notes":
            _write_legacy(settings, code, legacy)
    if area == "notes":
        profile = _course(settings, code)["style"] if code else settings["style"]["profile"]
        profile.update(patch)
        for key in inherit:
            profile.pop(key, None)
    return settings


def update_memory(workspace, *, expected_revision, **options):
    if not expected_revision:
        raise ConfigurationError("Read memory first and supply --expected-revision")
    result = workspace.configuration_store().update(
        lambda settings: edit(settings, expected_revision=expected_revision, **options))
    return snapshot(result, scope=options["scope"], area=options["area"], course=options.get("course"))


def append_legacy(settings, text, course=None):
    """Compatibility for explicit configure/course input; never infer scope from prose."""
    if not isinstance(text, str) or not text.strip():
        raise ConfigurationError("Instruction cannot be empty")
    text = text.strip()
    texts = list(_legacy(settings, course))
    if text not in texts and "\n" + text + "\n" not in "\n" + "\n".join(texts) + "\n":
        texts.append(text)
    _write_legacy(settings, course, texts)
    return settings


def writing_configuration(settings, code, *, instructions="", image_limit=None):
    """Shared snapshot for note writers; dialogue never enters paid identities."""
    if not isinstance(instructions, str):
        raise ConfigurationError("Lesson instructions must be text")
    course = generation_course(_course(settings, code))
    style = effective_style(settings, code)
    rules = snapshot(settings, scope="course", area="notes", course=code)["effective"]
    style["freeform_preferences"] = "\n".join(dict.fromkeys(rules.values()))
    style["notes_language"] = course["languages"]["notes"]
    if settings.get("personal", {}).get("background"):
        style["learner_background"] = settings["personal"]["background"]
    style["question_language"] = str(settings.get("personal", {}).get("interface_language") or course["languages"]["notes"])
    if instructions.strip():
        style["lesson_instructions"] = instructions.strip()
    if image_limit is not None:
        if type(image_limit) is not int or image_limit < 0:
            raise ConfigurationError("image_limit must be a non-negative integer")
        style["image_limit"] = image_limit
    return course, style


def generation_course(course):
    course = deepcopy(course)
    # Note rules are already resolved in the writing style; original answers are
    # provenance, not a second authoritative preference set. Keep the academic
    # context available but explicitly label its role in the provider prompt.
    course["preferences"] = {}
    return course


def candidate_style(settings, code, candidate):
    """Reuse saved editorial choices while keeping Word design code-owned.

    Call only after the normal candidate/provenance validation. Historical
    candidates without a snapshot retain the previous current-style behavior.
    Paths and publication permissions are always resolved from live settings.
    """
    profile = effective_style(settings, code)
    saved = candidate.get("effective_style")
    if saved is not None:
        if not isinstance(saved, dict):
            raise ConfigurationError("Candidate style snapshot must be an object")
        allowed = normalize_style_profile({})
        profile.update(normalize_style_profile({key: value for key, value in saved.items() if key in allowed}, partial=True))
        if "notes_language" in saved:
            if not isinstance(saved["notes_language"], str) or not saved["notes_language"].strip():
                raise ConfigurationError("Candidate note language must be text")
            profile["notes_language"] = saved["notes_language"]
    return profile
