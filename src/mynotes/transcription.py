"""Direct transcription and read-only status; independent from AI generation."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .asr import ASRError, ASRBusyError, transcribe_audio
from .ingestion import discover_recordings, hydrate, is_stable, recording_for_lecture
from .sourcepack import transcription_course_context
from .utils import atomic_json_write
from .workflow import _generation_lock, parse_lecture_id, WorkflowError
from .workspace import Workspace


def transcription_status(workspace: Workspace, lecture_id: str) -> dict:
    """Observe persisted state without creating files or invoking a tool."""
    lecture_id = lecture_id.upper()
    parse_lecture_id(lecture_id)
    directory = workspace.runs_dir / lecture_id / "asr"
    try:
        state = json.loads((directory / "status.json").read_text())
        if not isinstance(state, dict) or state.get("lecture_id") != lecture_id:
            raise ValueError("Invalid status")
    except FileNotFoundError:
        return {"lecture_id": lecture_id, "status": "NOT_STARTED"}
    except (OSError, ValueError):
        return {"lecture_id": lecture_id, "status": "NEEDS_REVIEW", "error": "Unreadable transcription checkpoint"}
    if state.get("status") == "RUNNING":
        try:
            os.kill(int(state["pid"]), 0)
        except (ProcessLookupError, ValueError, KeyError, TypeError):
            state = {**state, "status": "INTERRUPTED", "error": "Transcription process no longer exists"}
        except PermissionError:
            pass
    return state


def run_transcription(workspace: Workspace, lecture_id: str, audio: Path | None = None,
                      progress: bool = False, *, engines=None) -> dict:
    """One direct invocation with minimal ASR checks and a single lesson lock."""
    lecture_id = lecture_id.upper()
    parse_lecture_id(lecture_id)
    configuration = workspace.configuration_store().load()
    preparation_state = None
    try:
        with _generation_lock(workspace, lecture_id):
            preparation_state = {"schema_version": 1, "lecture_id": lecture_id, "pid": os.getpid(), "started_at": time.time(), "updated_at": time.time(), "status": "RUNNING", "phase": "preparing_context"}
            atomic_json_write(workspace.runs_dir / lecture_id / "asr" / "status.json", preparation_state)
            if progress:
                import sys
                print(f"Transcription {lecture_id}: preparing context", file=sys.stderr, flush=True)
            if audio is None:
                inventory = discover_recordings(workspace, configuration, lecture_id=lecture_id, persist=False)
                audio = recording_for_lecture(inventory, lecture_id)
                if audio is None:
                    reasons = "; ".join(item.get("reason", item.get("status", "")) for item in inventory)
                    raise ASRError(f"AUDIO_UNAVAILABLE: no unambiguous stable audio for {lecture_id}. {reasons}")
            else:
                audio = Path(audio).expanduser()
                if not audio.is_file() or not is_stable(audio, int(configuration["ingestion"]["stable_seconds"])):
                    raise ASRError("AUDIO_UNAVAILABLE: supplied audio is missing, empty or still changing")
                hydrate(audio)
            course = transcription_course_context(workspace, lecture_id)
            manifest = transcribe_audio(workspace, lecture_id, course, audio, engines=engines,
                                        progress=progress, _lecture_locked=True)
            directory = workspace.runs_dir / lecture_id / "asr"
            # A source pack is a fixed snapshot; record its staleness without changing it.
            pack_path = directory.parent / "source_pack.json"
            if pack_path.is_file():
                try:
                    pack = json.loads(pack_path.read_text())
                    prior = pack.get("transcription") or {}
                    if prior.get("cache_key") != manifest["cache_key"]:
                        atomic_json_write(directory.parent / "source_pack-stale.json", {
                            "reason": "Transcription changed; run prepare before generating notes",
                            "transcription_cache_key": manifest["cache_key"], "updated_at": time.time()})
                except (OSError, ValueError, TypeError):
                    pass
            return {"lecture_id": lecture_id, "status": manifest["status"],
                    "cache_reused": manifest["cache_reused"], "attempt_id": manifest.get("attempt_id"),
                    "transcript_json": str(directory / "transcript.json"),
                    "transcript_text": str(directory / "transcript.txt"),
                    "segments": len(manifest["segments"]), "audio_duration_seconds": manifest.get("audio_duration_seconds")}
    except WorkflowError as error:
        raise ASRBusyError(f"BUSY: {error}", transcription_status(workspace, lecture_id)) from error
    except BaseException as error:
        if preparation_state is not None:
            current = transcription_status(workspace, lecture_id)
            if current.get("phase") == "preparing_context":
                preparation_state.update(status="BUSY" if isinstance(error, ASRBusyError) else "INTERRUPTED" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "FAILED", phase="stopped", updated_at=time.time(), error=str(error))
                atomic_json_write(workspace.runs_dir / lecture_id / "asr" / "status.json", preparation_state)
        raise
