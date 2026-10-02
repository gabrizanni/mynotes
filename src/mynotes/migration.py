"""Read-only-first, repeatable migration from a legacy local installation."""
from __future__ import annotations

import json
import hashlib
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .utils import atomic_json_write, file_sha256, now_compact_utc
from .workspace import Workspace


class MigrationError(RuntimeError):
    pass


INVENTORY_DIRS = ("config", "state", "runs", "archives", "templates")
SKIP_NAMES = {"workflow.lock", ".DS_Store"}
SKIP_SUFFIXES = {".tmp", ".part", ".lock", ".wal", ".shm"}


def _kind(path: Path) -> str:
    if path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        return "sqlite"
    if path.suffix.lower() == ".docx":
        return "word"
    if "transcript" in path.name.lower():
        return "transcript"
    return "artifact"


def build_manifest(source: Path, workspace: Workspace) -> dict[str, Any]:
    source = source.expanduser().resolve()
    if not (source / "config").is_dir():
        raise MigrationError("Legacy source must contain a config directory")
    entries = []
    for directory in INVENTORY_DIRS:
        root = source / directory
        if not root.is_dir():
            continue
        for item in sorted(path for path in root.rglob("*") if path.is_file() and path.name not in SKIP_NAMES and path.suffix.casefold() not in SKIP_SUFFIXES):
            relative = item.relative_to(source)
            destination = workspace.migrations_dir / "legacy" / relative
            entries.append(
                {
                    "source": str(item),
                    "destination": str(destination),
                    "relative_path": str(relative),
                    "kind": _kind(item),
                    "sha256": file_sha256(item),
                    "action": "copy",
                }
            )
    manifest = {
        "schema_version": 1,
        "created_at": now_compact_utc(),
        "source": str(source),
        "destination_workspace": str(workspace.root),
        "capture_state": "excluded",
        "entries": entries,
        "warnings": [
            "External recordings, slides, personal notebooks, and final handouts are referenced in place and are not copied.",
            "Apply creates copies only under the new private workspace and never deletes the legacy source.",
            "Run only when legacy writers are stopped; active capture state is deliberately excluded.",
        ],
    }
    manifest["manifest_id"] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return manifest


def save_manifest(workspace: Workspace, manifest: dict[str, Any]) -> Path:
    workspace.migrations_dir.mkdir(parents=True, exist_ok=True)
    path = workspace.migrations_dir / f"legacy-manifest-{manifest['created_at']}.json"
    atomic_json_write(path, manifest)
    return path


def _copy_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as read_connection, sqlite3.connect(destination) as write_connection:
        read_connection.backup(write_connection)


def load_manifest(path: Path, workspace: Workspace | None = None) -> dict[str, Any]:
    try:
        manifest = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MigrationError(f"Cannot read reviewed migration manifest: {path}") from error
    supplied = manifest.pop("manifest_id", None)
    actual = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    manifest["manifest_id"] = supplied
    if supplied != actual:
        raise MigrationError("Migration manifest changed after dry-run review")
    if workspace and Path(manifest.get("destination_workspace", "")).resolve() != workspace.root:
        raise MigrationError("Migration manifest targets a different private workspace")
    return manifest


def _write_conversion_index(manifest: dict[str, Any]) -> None:
    workspace = Workspace.from_value(manifest["destination_workspace"])
    root = workspace.migrations_dir / "legacy"
    courses: list[dict[str, Any]] = []
    course_file = root / "config" / "courses.json"
    if course_file.is_file():
        try:
            raw = json.loads(course_file.read_text(encoding="utf-8"))
            values = list(raw.values()) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
            for item in values:
                if isinstance(item, dict):
                    courses.append({key: item.get(key) for key in ("code", "name", "kind", "folders") if item.get(key) is not None})
        except (OSError, json.JSONDecodeError):
            pass
    lectures = sorted({Path(entry["relative_path"]).parts[1] for entry in manifest["entries"] if Path(entry["relative_path"]).parts[:1] == ("runs",) and len(Path(entry["relative_path"]).parts) > 2})
    path_map = {entry["source"]: {"copy": entry["destination"], "sha256": entry["sha256"], "kind": entry["kind"]} for entry in manifest["entries"]}
    converted = workspace.migrations_dir / "converted"; converted.mkdir(parents=True, exist_ok=True)
    atomic_json_write(converted / "path-map.json", path_map)
    atomic_json_write(converted / "legacy-summary.json", {"manifest_id": manifest["manifest_id"], "courses": courses, "lectures": lectures, "capture_state": "excluded", "cutover_required": True})
def apply_manifest(manifest: dict[str, Any]) -> dict[str, int]:
    copied = skipped = 0
    for entry in manifest["entries"]:
        source = Path(entry["source"])
        destination = Path(entry["destination"])
        if not source.is_file() or file_sha256(source) != entry["sha256"]:
            raise MigrationError(f"Legacy file changed since dry run: {source}")
        source_marker = destination.with_name(destination.name + ".mynotes-source-sha256")
        already_copied = (
            destination.is_file()
            and (
                file_sha256(destination) == entry["sha256"]
                or (entry["kind"] == "sqlite" and source_marker.is_file() and source_marker.read_text(encoding="utf-8") == entry["sha256"])
            )
        )
        if already_copied:
            skipped += 1
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if entry["kind"] == "sqlite":
            _copy_sqlite(source, destination)
            source_marker.write_text(entry["sha256"], encoding="utf-8")
        else:
            shutil.copy2(source, destination)
        copied += 1
    _write_conversion_index(manifest)
    return {"copied": copied, "skipped": skipped}
