"""Name-only Capture courses, without resetting interactive onboarding."""
from __future__ import annotations

import re
import unicodedata

from .configuration import ConfigurationError, normalize_course


def _name_key(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def pending_courses(workspace) -> list[dict]:
    return [{"code": c["code"], "name": c["name"]} for c in workspace.configuration_store().load()["courses"]
            if c.get("context_setup") == "pending"]


def add_course(workspace, name: str) -> dict:
    from .onboarding import documents_root, managed_tools
    name = name.strip()
    if not name or len(name) > 120 or any(ord(c) < 32 for c in name):
        raise ConfigurationError("Course name must contain 1–120 printable characters")
    selected = {}

    def edit(settings):
        existing = next((c for c in settings["courses"] if _name_key(c["name"]) == _name_key(name)), None)
        if existing:
            selected.update(course=existing, created=False)
            return settings
        ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
        base = re.sub(r"[^A-Z0-9]", "", ascii_name.upper())[:12] or "COURSE"
        used = {v.upper() for c in settings["courses"] for v in [c["code"], *c["aliases"]]}
        code, suffix = base, 2
        while code in used:
            code, suffix = f"{base}{suffix}", suffix + 1
        root = documents_root() / code
        folders = {"materials": "Materials", "personal_notes": "Personal Notes", "handout": "Handouts"}
        course = normalize_course({"code": code, "name": name, "context_setup": "pending",
                                   "folders": {k: str(root / v) for k, v in folders.items()}})
        # Language remains unknown until the learner supplies it; capture works now.
        course["transcription"]["language"] = "auto"
        tools = managed_tools()
        course["transcription"]["tools"].update({k: tools[k] for k in ("argmax", "model_cache") if isinstance(tools.get(k), str)})
        for folder in folders.values():
            (root / folder).mkdir(parents=True, exist_ok=True)
        settings["courses"].append(course)
        selected.update(course=course, created=True)
        return settings

    workspace.configuration_store().update(edit)
    return {"status": "ADDED" if selected["created"] else "EXISTS", **selected}


def defer_context(workspace, code: str) -> dict:
    def edit(settings):
        course = next((c for c in settings["courses"] if c["code"] == code.upper()), None)
        if course is None:
            raise ConfigurationError("Unknown course")
        if course.get("context_setup") == "pending":
            course["context_setup"] = "deferred"
        return settings
    workspace.configuration_store().update(edit)
    return {"status": "DEFERRED", "code": code.upper()}
