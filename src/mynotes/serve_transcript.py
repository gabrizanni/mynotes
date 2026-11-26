"""`library.transcript`: the text of one lesson's or recording's transcript.

The caller names a recording or a lesson, never a file. The path always comes
from MyNotes' own records, in this order:

- lesson: the transcripts the lesson registry links to it, the text of its
  recordings, transcripts the library assigns to it or that carry its name
  (the rule `notes` uses to find a transcript), the transcript imported into
  its source pack (`prepare --transcript`, `lesson process --transcript`), then
  its own speech-recognition folder;
- recording: its exported text, its transcript text, then transcripts the
  library links to it.

A normalized `.json` transcript is read through its `.txt` sibling. At most
`max_bytes` bytes are read (2 MiB unless asked, 8 MiB at most): `truncated`
says whether the file is longer, and a multi-byte character is never cut.
Text that is not UTF-8 is decoded as Latin-1 and `encoding` says so.
Read-only: nothing is written, scanned with persistence or hashed.
"""
from __future__ import annotations

import codecs
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .serve_methods import COMMAND_FAILED, INVALID_PARAMS, NOT_FOUND, RpcError

DEFAULT_MAX_BYTES = 2 * 1024 * 1024
LIMIT_MAX_BYTES = 8 * 1024 * 1024
MAX_RECORDING_ID = 200


def _library(workspace: str) -> dict[str, Any]:
    """The `library.list` view: recordings, lessons and transcripts, as the app sees them."""
    from .transcription_queue import library_view
    from .workspace import Workspace
    ws = Workspace.from_value(workspace)
    if not ws.is_initialized():
        raise RpcError(COMMAND_FAILED, "Initialize MyNotes first")
    return library_view(ws)


def _runs(workspace: str) -> Path:
    from .workspace import Workspace
    return Workspace.from_value(workspace).runs_dir


def _lecture(value: str) -> tuple[str, str, int]:
    from .workflow import WorkflowError, parse_lecture_id
    try:
        code, number = parse_lecture_id(value)
    except WorkflowError as error:
        raise RpcError(INVALID_PARAMS, str(error)) from None
    return f"{code}-L{number:02d}", code, number


def _text_paths(recording: dict) -> list[str]:
    return [value for value in (recording.get("export_text"), recording.get("transcript_text"),
                                recording.get("transcript_json")) if isinstance(value, str) and value]


def _source_pack_transcript(runs: Path, lecture: str) -> list[str]:
    import json
    try:
        pack = json.loads((runs / lecture / "source_pack.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    sources = pack.get("sources") if isinstance(pack, dict) else None
    return [source["path"] for source in sources or []
            if isinstance(source, dict) and source.get("role") == "imported_transcript" and isinstance(source.get("path"), str)]


def _named_for(workspace: str, code: str, number: int) -> Callable[[str], bool]:
    """True for a transcript file named after this lesson, as `notes` recognizes it."""
    from .ingestion import parse_recording_name
    from .workspace import Workspace
    configuration = Workspace.from_value(workspace).configuration_store().load()

    def named(path: str) -> bool:
        try:
            return parse_recording_name(Path(path), configuration) == (code, number)
        except (ValueError, TypeError):
            return False
    return named


def lecture_candidates(view: dict, runs: Path, lecture: str, code: str, number: int,
                       named: Callable[[str], bool] = lambda _path: False) -> list[str]:
    recordings = {row["id"]: row for row in view.get("recordings", []) if isinstance(row, dict) and "id" in row}
    transcripts = {row["id"]: row for row in view.get("transcripts", []) if isinstance(row, dict) and "id" in row}
    lesson = next((row for row in view.get("lessons", [])
                   if isinstance(row, dict) and row.get("course") == code and row.get("number") == number), {})
    paths = [transcripts[key]["path"] for key in lesson.get("transcript_ids", []) if key in transcripts]
    linked = list(lesson.get("recording_ids", [])) + [key for key, row in recordings.items() if row.get("lecture_id") == lecture]
    for key in linked:
        paths += _text_paths(recordings.get(key, {}))
    paths += [row["path"] for row in transcripts.values() if row.get("lecture_id") == lecture or (
        not row.get("lecture_id") and row.get("course_code") in (None, code) and named(row["path"]))]
    paths += _source_pack_transcript(runs, lecture)
    paths.append(str(runs / lecture / "asr" / "transcript.txt"))
    return paths


def recording_candidates(view: dict, recording_id: str) -> list[str] | None:
    """None when the library knows no recording or transcript with this id."""
    recording = next((row for row in view.get("recordings", []) if isinstance(row, dict) and row.get("id") == recording_id), None)
    linked = [row["path"] for row in view.get("transcripts", [])
              if isinstance(row, dict) and row.get("recording_id") == recording_id and isinstance(row.get("path"), str)]
    if recording is None and not linked:
        return None
    return _text_paths(recording or {}) + linked


def _readable(value: str) -> Path | None:
    path = Path(value).expanduser()
    if path.suffix.casefold() == ".json":
        path = path.with_suffix(".txt")
    return path if path.is_file() else None


def decode(raw: bytes, *, truncated: bool) -> tuple[str, str]:
    """Text and encoding; a truncated read drops an incomplete trailing UTF-8 sequence."""
    encoding = "utf-8"
    if raw.startswith(codecs.BOM_UTF8):
        raw, encoding = raw[len(codecs.BOM_UTF8):], "utf-8-sig"
    try:
        return codecs.getincrementaldecoder("utf-8")().decode(raw, final=not truncated), encoding
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def read_text(path: Path, max_bytes: int) -> dict[str, Any]:
    with path.open("rb") as handle:
        raw = handle.read(max_bytes)
        truncated = bool(handle.read(1))
        size = path.stat().st_size
    text, encoding = decode(raw, truncated=truncated)
    return {"path": str(path), "text": text, "truncated": truncated, "encoding": encoding, "size": size}


def _max_bytes(value: Any) -> int:
    if value is None:
        return DEFAULT_MAX_BYTES
    if not 1 <= value <= LIMIT_MAX_BYTES:
        raise RpcError(INVALID_PARAMS, f"maxBytes must be between 1 and {LIMIT_MAX_BYTES}")
    return value


def transcript(workspace: str, params: dict) -> dict[str, Any]:
    """Direct handler of `library.transcript` (validated params: exactly one id)."""
    limit = _max_bytes(params.get("maxBytes"))
    recording_id = params.get("recordingId")
    if recording_id is not None:
        if len(recording_id) > MAX_RECORDING_ID:
            raise RpcError(INVALID_PARAMS, "recordingId is too long")
        paths = recording_candidates(_library(workspace), recording_id)
        if paths is None:
            raise RpcError(NOT_FOUND, "Recording not found", {"recordingId": recording_id})
        subject = {"recordingId": recording_id}
    else:
        lecture, code, number = _lecture(params["lectureId"])
        paths = lecture_candidates(_library(workspace), _runs(workspace), lecture, code, number,
                                   _named_for(workspace, code, number))
        subject = {"lectureId": lecture}
    for value in paths:
        path = _readable(value)
        if path is None:
            continue
        try:
            return read_text(path, limit)
        except OSError:
            continue  # Moved or unreadable since the library listed it: try the next record.
    raise RpcError(NOT_FOUND, "No transcript yet", subject)
