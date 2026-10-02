"""Portable deterministic helpers shared by later MyNotes workflows."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Return the content hash used to protect future migration artifacts."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json_write(path: Path, payload: Any) -> None:
    """Persist JSON atomically so an interrupted write cannot corrupt settings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def now_compact_utc() -> str:
    """Return a collision-resistant, filename-safe UTC timestamp."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
