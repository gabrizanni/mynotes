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


def transcription_status(workspace: Workspace, lecture_id: str | None = None, *, recording_id: str | None = None) -> dict:
    """Observe persisted state without creating files or invoking a tool."""
    if recording_id is None and lecture_id is not None and workspace.is_initialized():
        from .transcription_queue import library
        candidates = [item for item in library(workspace) if item.get("storage_recording_id") and item.get("lecture_id") == lecture_id.upper()]
        if len(candidates) > 1:
            raise ASRError("AMBIGUOUS_RECORDING: più registrazioni indicano questa lezione; seleziona il recording ID")
        if candidates:
            recording_id = candidates[0]["id"]
    if recording_id is not None:
        from .transcription_queue import library, asr_directory
        matches = [item for item in library(workspace) if item["id"] == recording_id]
        if not matches:
            raise ASRError("RECORDING_NOT_FOUND: registrazione non trovata")
        item = matches[0]
        directory = asr_directory(workspace, item)
        identity = "_recordings/" + recording_id if item.get("storage_recording_id") else item["lecture_id"]
    else:
        lecture_id = lecture_id.upper()
        parse_lecture_id(lecture_id)
        directory = workspace.runs_dir / lecture_id / "asr"
        identity = lecture_id
    try:
        state = json.loads((directory / "status.json").read_text())
        if not isinstance(state, dict) or state.get("lecture_id") != identity:
            raise ValueError("Invalid status")
    except FileNotFoundError:
        return {"lecture_id": identity, **({"recording_id": recording_id} if recording_id else {}), "status": item["transcription_status"] if recording_id else "NOT_STARTED"}
    except (OSError, ValueError):
        return {"lecture_id": identity, **({"recording_id": recording_id} if recording_id else {}), "status": "NEEDS_REVIEW", "error": "Unreadable transcription checkpoint"}
    if state.get("status") == "RUNNING":
        try:
            os.kill(int(state["pid"]), 0)
        except (ProcessLookupError, ValueError, KeyError, TypeError):
            state = {**state, "status": "INTERRUPTED", "error": "Transcription process no longer exists"}
        except PermissionError:
            pass
    return {**state, **({"recording_id": recording_id} if recording_id else {})}


def _run_lecture_transcription(workspace: Workspace, lecture_id: str, audio: Path | None = None,
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
            from .transcription_queue import profile
            course = profile(workspace, {'course_code': parse_lecture_id(lecture_id)[0], 'lecture_id': lecture_id, 'title': lecture_id})
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


def _recording_result(item, manifest, path, *, reused=False):
    return {"recording_id": item["id"], "lecture_id": item.get("lecture_id"),
            **{k: manifest[k] for k in ('current_audio_path', 'export_text', 'archive_status', 'archive_error') if k in manifest},
            "status": manifest["status"], "cache_reused": reused or manifest.get("cache_reused", False),
            "attempt_id": manifest.get("attempt_id"), "transcript_json": str(path),
            "transcript_text": str(path.with_suffix(".txt")), "segments": len(manifest["segments"]),
            "audio_duration_seconds": manifest.get("audio_duration_seconds"), "warnings": item.get("warnings", [])}


def _run_recording_transcription(workspace, item, *, engines=None, progress=False):
    """Use the Capture identity and frozen queued profile, without a window lease."""
    from . import transcription_queue as queue
    from .capture_associations import validated_transcript
    from .asr import write_transcript_text
    from .utils import file_sha256
    if not item["stable"]:
        messages = "; ".join(row["message"] for row in item.get("blocking_reasons", []))
        raise ASRError("AUDIO_UNAVAILABLE: " + (messages or "Audio non ancora disponibile"))
    state = transcription_status(workspace, recording_id=item["id"])
    if state.get("status") == "RUNNING" and queue._alive(state.get("pid")):
        raise ASRBusyError("BUSY: la registrazione è già in trascrizione", state)
    existing = validated_transcript(workspace, item)
    if existing:
        path, manifest = existing
        normalized = manifest.get("normalized_audio_path")
        if normalized or manifest.get("normalized_audio_sha256"):
            if not normalized or not Path(normalized).is_file() or file_sha256(Path(normalized)) != manifest.get("normalized_audio_sha256"):
                raise ASRError("TRANSCRIPT_INVALID: l’audio normalizzato della trascrizione non supera i controlli di integrità")
        if not path.with_suffix(".txt").exists():
            write_transcript_text(path.with_suffix(".txt"), manifest)
        from .recording_library import finish
        manifest.update(finish(workspace, item, path))
        return _recording_result(item, manifest, path, reused=True)
    if item.get("blocking_reasons") and any(row["code"] != "TRANSCRIPTION_ACTIVE" for row in item["blocking_reasons"]):
        raise ASRError("TRANSCRIPTION_BLOCKED: " + "; ".join(row["message"] for row in item["blocking_reasons"]))
    # An explicitly requested job may run with the window closed. Claim it in the
    # same queue transaction used by the runner; never recalculate its ASR profile.
    queued = None
    if (queue.root(workspace) / "queue.sqlite3").exists():
        with queue.database(workspace, readonly=True) as conn:
            pending = conn.execute("SELECT id FROM jobs WHERE recording_id=? AND status='QUEUED' ORDER BY created DESC LIMIT 1", (item['id'],)).fetchone()
        if pending:
            queue.refresh_queued_association(workspace, pending['id'])
    if (queue.root(workspace) / "queue.sqlite3").exists():
        with queue.database(workspace) as conn:
            conn.execute("BEGIN IMMEDIATE")
            queued = conn.execute("SELECT * FROM jobs WHERE recording_id=? ORDER BY created DESC LIMIT 1", (item["id"],)).fetchone()
            if queued is None:
                queued = conn.execute("SELECT j.* FROM recording_aliases a JOIN jobs j ON j.id=a.job_id WHERE a.recording_id=?", (item["id"],)).fetchone()
            if queued and queued["status"] == "RUNNING" and queue._alive(queued["pid"]):
                raise ASRBusyError("BUSY: una trascrizione attiva possiede già questa registrazione", {"job": queued["id"], "status": "RUNNING"})
            if queued and (queued["status"] in {"QUEUED", "FAILED", "INTERRUPTED"} or queued["status"] == "RUNNING" and not queue._alive(queued["pid"])):
                conn.execute("UPDATE jobs SET status='RUNNING',pid=?,updated=?,error=NULL WHERE id=?", (os.getpid(), time.time(), queued["id"]))
            else:
                queued = None
    if queued:
        result = queue.run_job(workspace, queued["id"], engines=engines)
        if result["status"] == "QUEUED":
            raise ASRBusyError(result.get("message", "BUSY: trascrizione in attesa"), {"job": queued["id"]})
        if result["status"] != "COMPLETED":
            from .asr import ASRReviewError
            if result["status"] == "NEEDS_REVIEW":
                raise ASRReviewError(result.get("message", "Trascrizione da controllare"), [], queue.asr_directory(workspace, item) / "status.json")
            raise ASRError(result.get("message", "Trascrizione non riuscita"))
        return {**result, "recording_id": item["id"], "warnings": item.get("warnings", [])}
    course = queue.profile(workspace, item)
    manifest = transcribe_audio(workspace, item.get("lecture_id") or item["id"], course, Path(item["path"]),
                                engines=engines, progress=progress, recording_id=item["id"] if item.get('storage_recording_id') else None)
    path = queue.asr_directory(workspace, item) / 'transcript.json' 
    return _recording_result(item, manifest, path)


def run_transcription(workspace: Workspace, lecture_id: str | None = None, audio: Path | None = None,
                      progress: bool = False, *, engines=None, recording_id: str | None = None) -> dict:
    """Resolve managed audio before choosing storage, preserving its ASR identity."""
    from .transcription_queue import library
    if recording_id is not None and (lecture_id is not None or audio is not None):
        raise ASRError("Choose one lecture or one recording ID")
    if lecture_id is not None:
        lecture_id = lecture_id.upper()
        parse_lecture_id(lecture_id)
    records = library(workspace)
    if recording_id is not None:
        matches = [item for item in records if item["id"] == recording_id]
        if not matches:
            raise ASRError("RECORDING_NOT_FOUND: registrazione non trovata")
    elif audio is not None:
        matches = [item for item in records if item.get("storage_recording_id") and Path(item["path"]).resolve() == Path(audio).expanduser().resolve()]
    else:
        matches = [item for item in records if item.get("storage_recording_id") and item.get("lecture_id") == lecture_id]
        if len(matches) > 1:
            raise ASRError("AMBIGUOUS_RECORDING: più registrazioni indicano questa lezione; seleziona il recording ID")
    if not matches and audio is None and lecture_id is not None:
        from .recording_library import options
        if options(workspace).get('folder'):
            matches = [r for r in records if r.get('lecture_id') == lecture_id]
            if len(matches) > 1: raise ASRError('AMBIGUOUS_RECORDING: seleziona una registrazione per questa lezione')
    if matches and (matches[0].get("storage_recording_id") or recording_id is not None or audio is None):
        return _run_recording_transcription(workspace, matches[0], engines=engines, progress=progress)
    if recording_id is not None:
        item = matches[0]
        if not item.get("lecture_id"):
            raise ASRError("La registrazione non identifica una lezione")
        lecture_id, audio = item["lecture_id"], Path(item["path"])
    if lecture_id is None:
        raise ASRError("Supply a lecture or recording ID")
    if audio is None:
        archived = [r for r in records if r.get('lecture_id') == lecture_id and r.get('collection') in {'processed', 'legacy'}]
        if len(archived) == 1:
            audio = Path(archived[0]['path'])
    return _run_lecture_transcription(workspace, lecture_id, audio, progress, engines=engines)
