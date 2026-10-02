"""Managed, privacy-safe Codex and Agy integrations."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


class IntegrationError(RuntimeError):
    pass


CODEX_SKILL = """---
name: mynotes
description: Coordinate MyNotes course setup, evidence preparation, note generation, Word validation and safe publication.
---
# MyNotes

Use the installed `mynotes` command and the workspace in `MYNOTES_WORKSPACE`.
Run `mynotes doctor` and `mynotes status` first. For a new lecture use the real
pipeline: `prepare`, `generate`, `build`, then `publish`. Building is candidate-only;
publishing requires a validated rendered DOCX. Never invent a provider success,
silently drop evidence, overwrite an externally edited Word, or launch `mynotes chat`
from inside this session. Use `redo` only when the user explicitly requests a rewrite.
Private recordings, notes, paths and credentials must remain in the private workspace.
"""

AGY_SKILL = CODEX_SKILL

AGY_AGENT = """---
name: mynotes
description: MyNotes agent for safe local course setup, note creation and Word publication.
mainAgent: true
subagent: false
commandExecutionPolicy: sandbox
skills:
  - skills/mynotes
---
# MyNotes agent

Follow the MyNotes skill. Start with `mynotes doctor` and `mynotes status`.
Use prepare -> generate -> build -> publish and report each state separately.
Never recursively start another conversational launcher. Keep all personal data
inside the selected private workspace.
"""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _layout(platform: str, target: Path) -> tuple[Path, dict[Path, str]]:
    if platform == "codex":
        root = target / ".agents" / "skills" / "mynotes"
        return root, {Path("SKILL.md"): CODEX_SKILL}
    if platform == "agy":
        root = target / ".agents" / "plugins" / "mynotes"
        plugin = {"$schema": "https://antigravity.google/schemas/v1/plugin.json", "name": "mynotes", "description": "Local MyNotes coordination without private data."}
        return root, {Path("plugin.json"): json.dumps(plugin, indent=2) + "\n", Path("skills/mynotes/SKILL.md"): AGY_SKILL, Path("agents/mynotes.md"): AGY_AGENT}
    raise IntegrationError("Supported integrations are codex and agy")


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
