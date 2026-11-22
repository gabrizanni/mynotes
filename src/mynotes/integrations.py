"""Managed provider entrypoints; task instructions are loaded on demand."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
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
CHAT_SKILL = SKILL_HEADER + """The MyNotes launcher supplies the common coordination rules.
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
AGY_AGENT_HEADER = """---
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

"""
AGY_AGENT = AGY_AGENT_HEADER + CORE
AGY_SHARED_AGENT = AGY_AGENT_HEADER + "Follow the shared MyNotes instructions loaded from the workspace AGENTS.md.\n"

_RULES_MARKER = "<!-- MyNotes managed chat instructions: "


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def install_shared_chat_rules(workspace: Path) -> bool:
    """Publish the common entrypoint without replacing user-owned instructions."""
    path = workspace / "AGENTS.md"
    alternatives = (workspace / "AGENTS.override.md", workspace / "GEMINI.md")
    if any(item.exists() or item.is_symlink() for item in alternatives) or path.is_symlink():
        return False

    body = "# MyNotes\n\n" + CORE
    content = f"{_RULES_MARKER}{_sha(body)} -->\n{body}"
    replacing = path.exists()
    if replacing:
        if not path.is_file():
            return False
        try:
            previous = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            return False
        if previous == content:
            return True
        header, _, previous_body = previous.partition("\n")
        if header != f"{_RULES_MARKER}{_sha(previous_body)} -->":
            return False

    handle = NamedTemporaryFile(dir=workspace, prefix=".mynotes-rules-", delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content.encode("utf-8"))
        if replacing:
            if path.is_symlink() or not path.is_file():
                return False
            current = path.read_bytes()
            if current != previous.encode("utf-8"):
                return current == content.encode("utf-8")
            os.replace(temporary, path)
        else:
            try:
                # Make simultaneous first launches safe without clobbering a new file.
                os.link(temporary, path)
            except FileExistsError:
                return not path.is_symlink() and path.is_file() and path.read_bytes() == content.encode("utf-8")
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _layout(platform: str, target: Path, *, shared_chat_rules: bool) -> tuple[Path, dict[Path, str]]:
    if platform == "codex":
        root = target / ".agents" / "skills" / "mynotes"
        return root, {Path("SKILL.md"): CHAT_SKILL}
    if platform == "claude":
        root = target / ".claude" / "skills" / "mynotes"
        return root, {Path("SKILL.md"): CHAT_SKILL if shared_chat_rules else CLAUDE_SKILL}
    if platform == "agy":
        root = target / ".agents" / "plugins" / "mynotes"
        plugin = {"$schema": "https://antigravity.google/schemas/v1/plugin.json", "name": "mynotes", "description": "Local MyNotes coordination without private data."}
        agent = AGY_SHARED_AGENT if shared_chat_rules else AGY_AGENT
        return root, {Path("plugin.json"): json.dumps(plugin, indent=2) + "\n", Path("skills/mynotes/SKILL.md"): AGY_SKILL, Path("agents/mynotes.md"): agent}
    raise IntegrationError("Supported integrations are codex, agy and claude")


def _manifest(platform: str, files: dict[Path, str]) -> dict[str, Any]:
    return {"schema_version": 1, "platform": platform, "files": {str(path): _sha(content) for path, content in files.items()}}


def _verify_managed(root: Path, manifest: dict[str, Any]) -> None:
    for relative, expected in manifest.get("files", {}).items():
        path = root / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise IntegrationError(f"Managed integration was edited locally; refusing to overwrite/remove: {path}")


def install_integration(platform: str, destination: Path, *, action: str = "install", shared_chat_rules: bool = False) -> dict[str, Any]:
    target = destination.expanduser().resolve()
    root, files = _layout(platform, target, shared_chat_rules=shared_chat_rules)
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
