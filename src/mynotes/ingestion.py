"""Recording discovery and conservative filename normalization.

The module only trusts course codes and aliases stored in the private
workspace.  Unknown or ambiguous names are reported for review and are never
renamed automatically.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .utils import atomic_json_write, file_sha256
from .workspace import Workspace


class IngestionError(RuntimeError):
    """Raised when configured recording inputs cannot be inspected safely."""


@dataclass(frozen=True)
class Recording:
    path: str
    status: str
    lecture_id: str | None = None
    course_code: str | None = None
    lesson_number: int | None = None
    canonical_name: str | None = None
    sha256: str | None = None
    reason: str | None = None

    def payload(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def _name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _alias_index(configuration: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for course in configuration["courses"]:
        for value in (course["code"], course["name"], *course["aliases"]):
            key = _name_key(value)
            if key:
                result.setdefault(key, set()).add(course["code"])
    return result


def parse_recording_name(path: Path, configuration: dict[str, Any]) -> tuple[str, int] | None:
    """Resolve one filename using only configured codes and unambiguous aliases."""
    codes = {course["code"] for course in configuration["courses"]}
    canonical = re.fullmatch(r"(?P<course>[A-Za-z0-9]+)_L(?P<number>\d{1,3})", path.stem)
    if canonical and canonical["course"].upper() in codes:
        return canonical["course"].upper(), int(canonical["number"])

    compact = _name_key(path.stem)
    aliases = _alias_index(configuration)
    matches: list[tuple[int, str, int]] = []
    for alias, course_codes in aliases.items():
        if len(course_codes) != 1 or not compact.startswith(alias):
            continue
        remainder = compact[len(alias) :]
        lesson = re.fullmatch(r"(?:l|lecture|lesson|lezione)?0*(\d{1,3})", remainder)
        if lesson:
            matches.append((len(alias), next(iter(course_codes)), int(lesson.group(1))))
    if not matches:
        return None
    longest = max(length for length, _, _ in matches)
    resolved = {(code, number) for length, code, number in matches if length == longest}
    return next(iter(resolved)) if len(resolved) == 1 else None


def is_stable(path: Path, stable_seconds: int, *, now: float | None = None) -> bool:
    stat = path.stat()
    return stat.st_size > 0 and ((time.time() if now is None else now) - stat.st_mtime) >= stable_seconds


def hydrate(path: Path) -> None:
    """Force cloud-backed content to be readable before accepting its hash."""
    try:
        with path.open("rb") as handle:
            while handle.read(1024 * 1024):
                pass
    except OSError as error:
        raise IngestionError(f"Recording is not locally readable: {path}") from error


def discover_recordings(
    workspace: Workspace,
    configuration: dict[str, Any],
    *,
    apply_renames: bool = False,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Inventory configured recording folders and optionally apply safe renames."""
    extensions = set(configuration["ingestion"]["audio_extensions"])
    stable_seconds = int(configuration["ingestion"]["stable_seconds"])
    results: list[Recording] = []
    claimed: dict[str, Path] = {}
    duplicates: set[str] = set()
    candidates: list[tuple[Path, str, int, Path]] = []

    for raw_directory in configuration["paths"]["recording_dirs"]:
        directory = Path(raw_directory)
        if not directory.is_dir():
            results.append(Recording(str(directory), "MISSING_DIRECTORY", reason="Configured recording folder does not exist"))
            continue
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.name.startswith(".") or path.suffix.casefold() not in extensions:
                continue
            resolved = parse_recording_name(path, configuration)
            if resolved is None:
                results.append(Recording(str(path), "NEEDS_REVIEW", reason="Filename does not identify exactly one configured course and lesson"))
                continue
            course_code, lesson_number = resolved
            lecture_id = f"{course_code}-L{lesson_number:02d}"
            target = path.with_name(f"{course_code}_L{lesson_number:02d}{path.suffix.casefold()}")
            if lecture_id in claimed:
                duplicates.add(lecture_id)
                results.append(Recording(str(path), "NEEDS_REVIEW", lecture_id, course_code, lesson_number, target.name, reason=f"Duplicate recording also found at {claimed[lecture_id]}"))
                continue
            claimed[lecture_id] = path
            candidates.append((path, course_code, lesson_number, target))

    for path, course_code, lesson_number, target in candidates:
        lecture_id = f"{course_code}-L{lesson_number:02d}"
        if lecture_id in duplicates:
            results.append(Recording(str(path), "NEEDS_REVIEW", lecture_id, course_code, lesson_number, target.name, reason="More than one recording resolves to this lecture"))
            continue
        if not is_stable(path, stable_seconds, now=now):
            results.append(Recording(str(path), "WAITING_DOWNLOAD", lecture_id, course_code, lesson_number, target.name, reason=f"File must remain unchanged for {stable_seconds} seconds"))
            continue
        if path != target and target.exists():
            results.append(Recording(str(path), "NEEDS_REVIEW", lecture_id, course_code, lesson_number, target.name, reason="Canonical target already exists"))
            continue
        accepted = path
        status = "DISCOVERED"
        if path != target:
            status = "RENAME_AVAILABLE"
            if apply_renames:
                path.rename(target)
                accepted = target
                status = "RENAMED"
        hydrate(accepted)
        results.append(Recording(str(accepted), status, lecture_id, course_code, lesson_number, target.name, file_sha256(accepted)))

    payload = [item.payload() for item in sorted(results, key=lambda item: (item.lecture_id or "", item.path))]
    atomic_json_write(workspace.state_dir / "recordings.json", {"recordings": payload})
    return payload


def recording_for_lecture(inventory: list[dict[str, Any]], lecture_id: str) -> Path | None:
    accepted = [
        Path(item["path"])
        for item in inventory
        if item.get("lecture_id") == lecture_id and item.get("status") in {"DISCOVERED", "RENAMED", "RENAME_AVAILABLE"}
    ]
    if len(accepted) > 1:
        raise IngestionError(f"More than one accepted recording exists for {lecture_id}")
    return accepted[0] if accepted else None
