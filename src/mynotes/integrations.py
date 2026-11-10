"""Managed provider entrypoints; task instructions are loaded on demand."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from .context import CORE, instruction_module


class IntegrationError(RuntimeError):
    pass


# Compatibility imports for integrations/tests; never concatenate these at launch.
TASK_PRIORITY_GUIDANCE = instruction_module("notes")
NOTE_FILES_GUIDANCE = instruction_module("publication")
CAPTURE_AGENT_GUIDANCE = instruction_module("capture")
FIRST_PROFILE_GUIDANCE = instruction_module("profile")
FIRST_COURSE_GUIDANCE = instruction_module("course")
NOTE_GENERATION_GUIDANCE = instruction_module("notes")
TRANSCRIPTION_GUIDANCE = instruction_module("transcription")

SKILL_HEADER = """---
name: mynotes
description: Coordinate MyNotes courses, preferences, evidence, notes and safe Word delivery.
---
# MyNotes

"""
CLAUDE_SKILL = SKILL_HEADER + CORE
CODEX_SKILL = SKILL_HEADER + """The MyNotes launcher supplies the common coordination rules.
If MYNOTES_CHAT_ACTIVE is not set, include --include-core in the first context
command to bootstrap a session started directly in the provider.
For each new task use `mynotes context --activity ACTIVITY` with --course CODE
when known. Follow the returned instructions and current memory. A review-mode
publication needs explicit user approval. Do not load unrelated modules or
repeat answered onboarding. At an empty launch greet and wait.
"""
AGY_SKILL = SKILL_HEADER + """The selected mynotes agent supplies the common coordination rules.
If using only this skill outside the MyNotes agent, include --include-core in
the first context command to load the common coordination rules once.
For an operational task load `mynotes context --activity ACTIVITY` with
--course CODE when known, then follow that task module and current memory.
Do not load unrelated modules, repeat onboarding, or call a provider for context.
"""
AGY_AGENT = """---
name: mynotes
description: MyNotes agent for private course setup, preferences and safe Word delivery.
mainAgent: true
subagent: false
commandExecutionPolicy: sandbox
tools:
  - run_command
  - view_file
  - grep_search
  - list_dir
  - find_by_name
  - write_to_file
  - replace_file_content
  - read_url_content
  - search_web
---
# MyNotes agent

""" + CORE


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _layout(platform: str, target: Path) -> tuple[Path, dict[Path, str]]:
    if platform == "codex":
        root = target / ".agents" / "skills" / "mynotes"
        return root, {Path("SKILL.md"): CODEX_SKILL}
    if platform == "claude":
        root = target / ".claude" / "skills" / "mynotes"
        return root, {Path("SKILL.md"): CLAUDE_SKILL}
    if platform == "agy":
        root = target / ".agents" / "plugins" / "mynotes"
        plugin = {"$schema": "https://antigravity.google/schemas/v1/plugin.json", "name": "mynotes", "description": "Local MyNotes coordination without private data."}
        return root, {Path("plugin.json"): json.dumps(plugin, indent=2) + "\n", Path("skills/mynotes/SKILL.md"): AGY_SKILL, Path("agents/mynotes.md"): AGY_AGENT}
    raise IntegrationError("Supported integrations are codex, agy and claude")


def _manifest(platform: str, files: dict[Path, str]) -> dict[str, Any]:
    return {"schema_version": 1, "platform": platform, "files": {str(path): _sha(content) for path, content in files.items()}}


def _verify_managed(root: Path, manifest: dict[str, Any]) -> None:
    for relative, expected in manifest.get("files", {}).items():
        path = root / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise IntegrationError(f"Managed integration was edited locally; refusing to overwrite/remove: {path}")


def install_integration(platform: str, destination: Path, *, action: str = "install") -> dict[str, Any]:
    target = destination.expanduser().resolve(); root, files = _layout(platform, target)
    manifest_path = root / ".mynotes-integration.json"; existing = None
    if manifest_path.is_file():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise IntegrationError("Integration manifest is invalid") from error
    if action == "install" and root.exists() and not existing:
        expected_paths = set(files)
        actual_paths = {path.relative_to(root) for path in root.rglob("*") if path.is_file() and path.name != ".DS_Store"}
        exact = actual_paths == expected_paths and all(hashlib.sha256((root / relative).read_bytes()).hexdigest() == _sha(content) for relative, content in files.items())
        if exact:
            existing = _manifest(platform, files)
        else:
            raise IntegrationError(f"Refusing to replace an unmanaged integration directory: {root}")
    if action in {"update", "remove"} and not existing:
        raise IntegrationError("No managed integration is installed at this destination")
    if existing:
        _verify_managed(root, existing)
    if action == "remove":
        shutil.rmtree(root)
        return {"status": "REMOVED", "platform": platform, "path": str(root)}
    for relative, content in files.items():
        output = root / relative; output.parent.mkdir(parents=True, exist_ok=True); output.write_text(content, encoding="utf-8")
    manifest = _manifest(platform, files); manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "UPDATED" if existing else "INSTALLED", "platform": platform, "path": str(root), "files": len(files)}
