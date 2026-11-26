"""One lesson from audio to updated notes: `mynotes lesson process COURSE-LNN`.

Stages, reported through `progress(stage, detail)`:

1. CHECKING: the lesson ID and course, then the existing preparation, which
   reuses a transcript already on disk (never starting transcription itself).
2. TRANSCRIBING: only when no usable transcript exists, or `audio` is given.
   A side thread reports the persisted transcription phase every second.
   A given `transcript` skips this stage: it is imported exactly as
   `prepare --transcript` imports it (`audio` and `transcript` exclude each
   other).
3. PREPARING: the source pack, from the new or imported transcript.
4. The `notes` stages (GENERATING, BUILDING_WORD, UPDATING_WORD, ...).

Exit codes follow `transcribe` and `notes`: 0 completed (or awaiting
approval), 2 not completed, 3 BUSY, 4 NEEDS_REVIEW. Errors raised by the notes
step propagate to the CLI, which reports them as `notes` does.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .workspace import Workspace

Progress = Callable[[str, dict[str, Any]], None]
TRANSCRIPT_REASONS = {"MISSING_TRANSCRIPT", "UNBOUND_CACHED_TRANSCRIPT"}
NOTES_DONE = {"COMPLETED", "COMPLETED_WITH_WARNINGS", "AWAITING_APPROVAL"}


def _watch_transcription(workspace: Workspace, lecture_id: str, emit: Progress, stop: threading.Event) -> None:
    from .transcription import transcription_status
    last = None
    while not stop.wait(1.0):
        try:
            state = transcription_status(workspace, lecture_id)
        except Exception:  # A status read must never stop the transcription.
            continue
        current = (state.get("status"), state.get("phase"), round(state.get("elapsed_seconds") or 0))
        if current != last and state.get("status") == "RUNNING":
            last = current
            emit("TRANSCRIBING", {"lecture_id": lecture_id, "phase": state.get("phase"),
                                  "elapsed_seconds": state.get("elapsed_seconds")})


def _transcribe(workspace: Workspace, lecture_id: str, audio: Path | None, emit: Progress, engines=None) -> tuple[int, dict]:
    """Run transcription like `transcribe --json`; returns (exit code, result)."""
    from .asr import ASRBusyError, ASRError, ASRReviewError
    from .configuration import ConfigurationError
    from .ingestion import IngestionError
    from .sourcepack import SourcePackError
    from .transcription import run_transcription
    from .workflow import WorkflowError
    identity = {"lecture_id": lecture_id}
    emit("TRANSCRIBING", {"lecture_id": lecture_id, "phase": "starting"})
    stop = threading.Event()
    watcher = threading.Thread(target=_watch_transcription, args=(workspace, lecture_id, emit, stop), daemon=True)
    watcher.start()
    try:
        result = run_transcription(workspace, lecture_id, audio=audio, progress=False,
                                   **({"engines": engines} if engines is not None else {}))
    except ASRBusyError as error:
        return 3, {"status": "BUSY", **identity, "message": str(error), "current": getattr(error, "state", {})}
    except ASRReviewError as error:
        return 4, {"status": "NEEDS_REVIEW", **identity, "message": str(error), "issues": getattr(error, "issues", []),
                   "raw_path": str(getattr(error, "path", ""))}
    except (ASRError, ConfigurationError, SourcePackError, IngestionError, WorkflowError, OSError, ValueError) as error:
        return 2, {"status": "FAILED", **identity, "message": str(error)}
    finally:
        stop.set()
        watcher.join(timeout=5)
    return 0, result


def _import_transcript(workspace: Workspace, lecture_id: str, transcript: Path, emit: Progress) -> tuple[int, dict]:
    """Build the source pack from `transcript` as `prepare --transcript` does; returns (exit code, result)."""
    from .ingestion import IngestionError
    from .sourcepack import SourcePackError, build_source_pack
    emit("PREPARING", {"lecture_id": lecture_id, "transcript": "imported"})
    try:
        build_source_pack(workspace, lecture_id, audio=None, transcript_path=transcript)
    except (SourcePackError, IngestionError, OSError, ValueError) as error:
        return 2, {"status": "FAILED", "lecture_id": lecture_id, "message": str(error)}
    return 0, {"status": "IMPORTED", "lecture_id": lecture_id, "path": str(transcript),
               "source_pack": str(workspace.runs_dir / lecture_id / "source_pack.json")}


def process_lesson(workspace: Workspace, lecture_id: str, *, audio: Path | None = None, transcript: Path | None = None,
                   provider: str | None = None, quality: str | None = None, instructions: str | None = None,
                   progress: Progress | None = None, engines=None, provider_adapter=None) -> tuple[int, dict[str, Any]]:
    """Run the four stages for one lesson; returns (exit code, result)."""
    from .cli import _notes_preparation
    from .workflow import WorkflowError, parse_lecture_id
    emit = progress or (lambda *_: None)
    lecture = lecture_id.upper()
    course, number = parse_lecture_id(lecture)
    if audio is not None and transcript is not None:
        raise WorkflowError("Give an audio file or a transcript, not both")
    emit("CHECKING", {"lecture_id": lecture})
    if not any(item["code"] == course for item in workspace.configuration_store().load()["courses"]):
        raise WorkflowError(f"Course not found: {course}")
    result: dict[str, Any] = {"lecture_id": lecture, "provider_invoked": False}
    if transcript is not None:
        code, imported = _import_transcript(workspace, lecture, transcript, emit)
        result["transcription"] = imported
        if code:
            return code, {**result, "status": imported["status"], "stage": "PREPARING"}
        pending = _notes_preparation(workspace, [lecture])
        if pending:
            return 2, {**result, "status": "NEEDS_TRANSCRIPT", "stage": "PREPARING",
                       "lessons_needing_preparation": pending, "asr_started": False}
        return _write_notes(workspace, course, number, result, provider=provider, quality=quality,
                            instructions=instructions, emit=emit, provider_adapter=provider_adapter)
    pending = [] if audio is not None else _notes_preparation(workspace, [lecture])
    transcribed = audio is not None or (pending and all(item["reason"] in TRANSCRIPT_REASONS for item in pending))
    if transcribed:
        code, transcription = _transcribe(workspace, lecture, audio, emit, engines)
        result["transcription"] = transcription
        if code:
            return code, {**result, "status": transcription["status"], "stage": "TRANSCRIBING"}
        emit("PREPARING", {"lecture_id": lecture})
        pending = _notes_preparation(workspace, [lecture])
    else:
        result["transcription"] = {"status": "REUSED"} if not pending else {"status": "NOT_STARTED"}
        emit("PREPARING", {"lecture_id": lecture, "transcript": "existing" if not pending else "unavailable"})
    if pending:
        return 2, {**result, "status": "NEEDS_TRANSCRIPT", "stage": "PREPARING", "lessons_needing_preparation": pending,
                   "asr_started": bool(transcribed)}
    return _write_notes(workspace, course, number, result, provider=provider, quality=quality,
                        instructions=instructions, emit=emit, provider_adapter=provider_adapter)


def _write_notes(workspace: Workspace, course: str, number: int, result: dict[str, Any], *, provider, quality,
                 instructions, emit: Progress, provider_adapter) -> tuple[int, dict[str, Any]]:
    from .notes_run import run_notes
    notes = run_notes(workspace, course, [number], provider=provider, quality=quality, instructions=instructions,
                      progress=emit, **({"provider_adapter": provider_adapter} if provider_adapter is not None else {}))
    result.update(status=notes["status"], notes=notes, provider_invoked=notes.get("provider_invoked"))
    return (0 if notes["status"] in NOTES_DONE else 2), result
