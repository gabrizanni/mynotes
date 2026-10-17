"""Local, durable transcription queue. No provider or note generation calls."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

from .utils import atomic_json_write, file_sha256


def root(workspace):
    return workspace.state_dir / "transcription-queue"


@contextmanager
def database(workspace, *, readonly=False):
    path = root(workspace) / "queue.sqlite3"
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    else:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        conn = sqlite3.connect(path, timeout=10)
        os.chmod(path, 0o600)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, recording_id TEXT NOT NULL, cache_key TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, pid INTEGER, error TEXT, result TEXT, UNIQUE(recording_id, cache_key))")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS jobs_input_settings ON jobs(cache_key)")
        conn.execute("CREATE TABLE IF NOT EXISTS recording_aliases (recording_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL)")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        if not readonly:
            conn.commit()
    except BaseException:
        if not readonly:
            conn.rollback()
        raise
    finally:
        conn.close()


def asr_directory(workspace, recording):
    key = ("_recordings/" + recording["id"]) if recording.get("storage_recording_id") else recording.get("lecture_id") or ("_recordings/" + recording["id"])
    return workspace.runs_dir / key / "asr"


def profile(workspace, recording):
    from .configuration import course_template
    from .sourcepack import transcription_course_context
    if recording.get("course_code") in {c["code"] for c in workspace.configuration_store().load()["courses"]}:
        if recording.get("lecture_id"):
            course = transcription_course_context(workspace, recording["lecture_id"])
        else:
            course = deepcopy(next(c for c in workspace.configuration_store().load()["courses"] if c["code"] == recording["course_code"]))
    else:
        course = course_template()
        course.update(name=recording["title"])
        course["transcription"]["language"] = "auto"
    if recording.get("language"):
        course["transcription"]["language"] = recording["language"]
    return course


def library(workspace, *, query=None, date=None, course=None):
    """Read configured folders only; no renames, ASR, hashing large audio or writes."""
    from .capture import CaptureStore, read_json
    from .ingestion import parse_recording_name, is_stable
    settings = workspace.configuration_store().load()
    store = CaptureStore(workspace)
    from .capture_associations import registry, apply
    links = registry(store)["recordings"]
    dirs = {Path(p).expanduser() for p in settings["paths"]["recording_dirs"]}
    dirs.add(Path(store.config()["outputDirectory"]))
    items = {}
    course_codes = {c["code"] for c in settings["courses"]}
    runtime = read_json(store.runtime_path, {})
    active_ids = set()
    if _alive(runtime.get("pid")):
        sessions = list(runtime.get("sessions") or [])
        if runtime.get("currentSession"):
            sessions.append(runtime["currentSession"])
        for current in sessions:
            if current.get("status") in {"RECORDING", "STARTED_LATE", "EXPORTING"}:
                event = current.get("event") or {}
                active_ids.update(filter(None, (event.get("recordingID"), event.get("id"))))
    for directory in dirs:
        if not directory.is_dir():
            continue
        for sidecar in sorted(directory.glob("*.capture.json")):
            try:
                session = read_json(sidecar)
                if session.get("isTest"):
                    continue
                event = apply(session["event"], links)
                audio = Path(session.get("outputPath") or str(sidecar)[:-13])
                if audio.resolve().parent != directory.resolve():
                    continue
                code, number = event.get("courseCode"), event.get("lessonNumber")
                if code is not None and (not isinstance(code, str) or not re.fullmatch(r"[A-Z0-9]+", code) or (number is not None and (type(number) is not int or not 1 <= number <= 999))):
                    continue
                lecture = f"{code}-L{number:02d}" if code and type(number) is int else None
                identifier = event.get("recordingID") or hashlib.sha256(str(audio.resolve()).encode()).hexdigest()[:32]
                if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
                    continue
                items[str(audio.resolve())] = {"id": identifier, "path": str(audio), "title": event.get("title") or audio.stem,
                    "course_code": code, "lecture_id": lecture, "storage_recording_id": event.get("recordingID"), "event_id": event.get("id"), "source_id": event.get("sourceID"), "duration": session.get("durationSeconds"),
                    "language": event.get("transcriptionLanguage"),
                    "created_at": event.get("startsAt", ""), "timezone": event.get("timezone", "UTC"), "partial": session.get("partial") is True,
                    "capture_status": session.get("status"), "capture_reason": session.get("reason", ""),
                    "audio_available": audio.is_file(),
                    "stable": audio.is_file() and session.get("status") in {"COMPLETED", "PARTIAL"}
                        and identifier not in active_ids and event.get("id") not in active_ids
                        and is_stable(audio, settings["ingestion"]["stable_seconds"]),
                    "unknown_course": code is not None and code not in course_codes}
            except (OSError, ValueError, KeyError, TypeError):
                continue
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.name.startswith('.') or path.suffix.casefold() not in settings['ingestion']['audio_extensions'] or str(path.resolve()) in items:
                continue
            resolved = parse_recording_name(path, settings)
            if resolved is None:
                continue
            code, number = resolved
            items[str(path.resolve())] = {'id': hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:32],
                'path': str(path), 'title': path.stem, 'course_code': code, 'lecture_id': f'{code}-L{number:02d}',
                'duration': None, 'created_at': '', 'partial': False,
                'audio_available': True, 'stable': is_stable(path, settings['ingestion']['stable_seconds'])}
    counts = {}
    for item in items.values():
        if item["lecture_id"]:
            counts[item["lecture_id"]] = counts.get(item["lecture_id"], 0) + 1
    labels = _read(root(workspace) / "labels.json", {})
    for item in items.values():
        item["title"] = labels.get(item["id"], item["title"])
        item["warnings"], item["blocking_reasons"] = [], []
        if item["partial"]:
            manual = item.get("capture_reason") == "Stopped manually"
            reason = item.get("capture_reason", "").casefold()
            recovered = "recover" in reason or "interruption" in reason
            late = "late" in reason
            code = "MANUAL_STOP" if manual else "RECOVERED_AUDIO" if recovered else "STARTED_LATE" if late else "PARTIAL_COVERAGE"
            message = ("Registrazione terminata manualmente." if manual else
                "Recuperata dopo un’interruzione: potrebbe mancare una parte della lezione." if recovered else
                "Registrazione avviata in ritardo: manca l’inizio della lezione." if late else
                "La registrazione copre solo una parte della lezione; l’audio salvato può essere trascritto.")
            item["warnings"].append({"code": code, "message": message})
        if item.get("unknown_course"):
            item["warnings"].append({"code": "UNKNOWN_COURSE", "message": "Corso non configurato: puoi trascrivere e associarlo in seguito."})
        elif not item.get("course_code"):
            item["warnings"].append({"code": "COURSE_UNASSIGNED", "message": "Corso da associare; puoi già trascrivere."})
        if counts.get(item["lecture_id"], 0) > 1:
            item["warnings"].append({"code": "DUPLICATE_LECTURE", "message": "Più audio indicano questa lezione: seleziona la registrazione da usare."})
        if counts.get(item["lecture_id"], 0) > 1 and not item.get("storage_recording_id"):
            item["blocking_reasons"].append({"code": "AMBIGUOUS_LEGACY_LECTURE", "message": "Più file usano la stessa destinazione storica: scegli la sorgente prima di trascrivere."})
        state = "NOT_STARTED" if item["stable"] else "WAITING"
        if not item["audio_available"]:
            item["blocking_reasons"].append({"code": "AUDIO_UNAVAILABLE", "message": "File audio non disponibile. Ripristina il file prima di trascrivere."})
        elif not item["stable"]:
            item["blocking_reasons"].append({"code": "AUDIO_NOT_FINAL", "message": "Audio ancora in registrazione, salvataggio o aggiornamento. Attendi che il salvataggio termini."})
        path = asr_directory(workspace, item) / "transcript.json"
        if path.is_file():
            try:
                from .asr import validate_transcript, segments_digest
                saved = json.loads(path.read_text())
                if (saved.get("status") == "COMPLETED" and not validate_transcript(saved, saved.get("audio_duration_seconds"))
                        and saved.get("segments_sha256") == segments_digest(saved.get("segments"))):
                    if item["stable"]:
                        state = "COMPLETED" if Path(item["path"]).stat().st_mtime <= path.stat().st_mtime else "OUTDATED"
                        if state == "COMPLETED":
                            if path.with_suffix(".txt").is_file():
                                item["transcript_text"] = str(path.with_suffix(".txt"))
                            item["transcript_json"] = str(path)
                        else:
                            item["blocking_reasons"].append({"code": "AUDIO_CHANGED", "message": "L’audio è cambiato dopo la trascrizione. Verifica la sorgente prima di ritrascrivere."})
                else:
                    state = "NEEDS_REVIEW"
                    item["blocking_reasons"].append({"code": "TRANSCRIPT_INVALID", "message": "La trascrizione salvata non supera i controlli di integrità. Controlla il risultato prima di riutilizzarlo."})
            except (OSError, ValueError, TypeError):
                state = "NEEDS_REVIEW"
                item["blocking_reasons"].append({"code": "TRANSCRIPT_UNREADABLE", "message": "La trascrizione salvata non è leggibile. Controlla il file prima di riprovare."})
        try:
            detail = _read(asr_directory(workspace, item) / "status.json", {})
        except (OSError, ValueError):
            detail = {"status": "NEEDS_REVIEW", "error": "Lo stato della trascrizione non è leggibile."}
        if state == "NOT_STARTED" and detail.get("status") in {"RUNNING", "FAILED", "INTERRUPTED", "NEEDS_REVIEW", "COMPLETED"}:
            state = detail["status"]
            if state == "RUNNING" and not _alive(detail.get("pid")):
                state = "INTERRUPTED"
            if state == "COMPLETED":
                state = "NEEDS_REVIEW"
                detail = {**detail, "error": "Il risultato della trascrizione completata non è più disponibile. Ripristina i file salvati."}
            if state in {"FAILED", "INTERRUPTED", "NEEDS_REVIEW"}:
                message = str(detail.get("error") or "La trascrizione è stata interrotta prima del completamento.").strip()[:600]
                item["warnings"].append({"code": "TRANSCRIPTION_FAILED", "message": message})
                if state == "NEEDS_REVIEW":
                    item["blocking_reasons"].append({"code": "ASR_RESULT_INVALID", "message": message})
        item["transcription_status"] = state
        # Existing clients retain the old aggregate status; new clients use the
        # independent transcription state and explicit availability information.
        item["status"] = "NEEDS_REVIEW" if state == "NOT_STARTED" and (item["partial"] or item.get("unknown_course") or counts.get(item["lecture_id"], 0) > 1) else state
    # The explicit enqueue action records duplicate identities; polling never hashes audio.
    if (root(workspace) / "queue.sqlite3").exists():
        with database(workspace, readonly=True) as conn:
            jobs = {row["recording_id"]: row for row in conn.execute("SELECT * FROM jobs ORDER BY created")}
            aliases = {}
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='recording_aliases'").fetchone():
                aliases = {row["recording_id"]: row for row in conn.execute("SELECT a.*,j.status,j.result,j.pid,j.error FROM recording_aliases a JOIN jobs j ON j.id=a.job_id")}
            for item in items.values():
                if item["transcription_status"] not in {"NOT_STARTED", "RUNNING", "FAILED", "INTERRUPTED", "NEEDS_REVIEW"}:
                    continue
                alias = aliases.get(item["id"])
                stat = Path(item["path"]).stat() if item["audio_available"] else None
                if alias and stat and (stat.st_mtime_ns, stat.st_size) == (alias["mtime_ns"], alias["size"]):
                    job = alias
                else:
                    job = jobs.get(item["id"])
                if not job or job["status"] == "CANCELLED":
                    continue
                job_state = "INTERRUPTED" if job["status"] == "RUNNING" and not _alive(job["pid"]) else job["status"]
                if job_state == "COMPLETED" and job["result"]:
                    result = json.loads(job["result"])
                    # A completed alias is only a pointer. Validate its small
                    # transcript metadata as well; audio hashes are checked on use.
                    candidate = Path(result.get("transcript_json") or "")
                    try:
                        saved = json.loads(candidate.read_text())
                        from .asr import validate_transcript, segments_digest
                        valid = (saved.get("status") == "COMPLETED" and not validate_transcript(saved, saved.get("audio_duration_seconds"))
                            and saved.get("segments_sha256") == segments_digest(saved.get("segments")))
                    except (OSError, ValueError, TypeError):
                        valid = False
                    if valid:
                        if result.get("transcript_text") and Path(result["transcript_text"]).is_file():
                            item["transcript_text"] = result["transcript_text"]
                        item["transcript_json"] = str(candidate)
                    else:
                        job_state = "NEEDS_REVIEW"
                        item["blocking_reasons"].append({"code": "TRANSCRIPT_INVALID", "message": "La trascrizione collegata non supera i controlli di integrità."})
                if job_state in {"QUEUED", "RUNNING", "COMPLETED"}:
                    item["warnings"] = [w for w in item["warnings"] if w["code"] != "TRANSCRIPTION_FAILED"]
                    item["blocking_reasons"] = [r for r in item["blocking_reasons"] if r["code"] != "ASR_RESULT_INVALID"]
                item["transcription_status"] = job_state
                if job is alias or job_state not in {"QUEUED", "RUNNING"}:
                    item["status"] = job_state
                if job["error"] or job_state == "NEEDS_REVIEW":
                    message = "Trascrizione non riuscita: " + str(job["error"] or "Il risultato non supera i controlli di integrità.").strip()[:600]
                    item["warnings"].append({"code": "TRANSCRIPTION_FAILED", "message": message})
                    if job_state == "NEEDS_REVIEW":
                        item["blocking_reasons"].append({"code": "ASR_RESULT_INVALID", "message": message})
    for item in items.values():
        if item["transcription_status"] in {"QUEUED", "RUNNING"}:
            item["blocking_reasons"].append({"code": "TRANSCRIPTION_ACTIVE", "message": "La registrazione è già in coda o in trascrizione."})
        item["can_transcribe"] = item["stable"] and not item["blocking_reasons"] and item["transcription_status"] != "COMPLETED"
    values = items.values()
    if query:
        from .capture_calendar import contains
        values = [r for r in values if contains(r["title"], query)]
    if date:
        from datetime import date as date_type
        from zoneinfo import ZoneInfo
        from .capture import timestamp
        date_type.fromisoformat(date)
        values = [r for r in values if r["created_at"] and timestamp(r["created_at"]).astimezone(ZoneInfo(r.get("timezone") or "UTC")).date().isoformat() == date]
    if course:
        values = [r for r in values if r.get("course_code") == course.upper()]
    return sorted(values, key=lambda r: (r["created_at"], r["path"]))


def _read(path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return deepcopy(default)


def _alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def settings(workspace):
    return _read(root(workspace) / "settings.json", {"parallel": 1, "paused": False})


def status(workspace):
    jobs = []
    labels = _read(root(workspace) / "labels.json", {})
    from .capture_associations import registry
    from .capture import CaptureStore
    links = registry(CaptureStore(workspace))["recordings"]
    if (root(workspace) / "queue.sqlite3").exists():
        with database(workspace, readonly=True) as conn:
            for row in conn.execute("SELECT * FROM jobs ORDER BY created, id"):
                job = dict(row); item = json.loads(job.pop("payload")); job.update(title=labels.get(job["recording_id"], item["recording"]["title"]), course_code=item["recording"].get("course_code"))
                job["course_code"] = links.get(job["recording_id"], {}).get("courseCode", job["course_code"])
                job["created_at"] = item["recording"].get("created_at", "")
                raw_result = job.pop("result", None)
                if raw_result:
                    result = json.loads(raw_result)
                    job["outputs"] = {key: result[key] for key in ("transcript_json", "transcript_text") if key in result}
                if job["status"] == "RUNNING":
                    if not _alive(job["pid"]):
                        job["status"] = "INTERRUPTED"
                    detail = _read(asr_directory(workspace, item["recording"]) / "status.json", {})
                    job["phase"], job["elapsed_seconds"] = detail.get("phase"), detail.get("elapsed_seconds")
                jobs.append(job)
    lease = _read(root(workspace) / "ui.json", {})
    closed = (root(workspace) / "closed" / str(lease.get("session", "none"))).is_file()
    return {"status": "OK", **settings(workspace), "ui_open": lease.get("expires_at", 0) > time.time() and not closed, "jobs": jobs,
            "wait_reason": _read(root(workspace) / "wait.json", {}).get("reason")}


def enqueue(workspace, parallel=1, *, recording_id=None):
    from .asr import cache_identity, validate_transcript, segments_digest, write_transcript_text
    if type(parallel) is not int or not 1 <= parallel <= 4:
        raise ValueError("Choose between 1 and 4 simultaneous recordings")
    recordings = library(workspace)
    if recording_id is not None:
        recordings = [item for item in recordings if item["id"] == recording_id]
        if not recordings:
            raise ValueError("Registrazione non trovata")
    configure(workspace, parallel=parallel)
    added, skipped = 0, []
    for item in recordings:
        state = item["transcription_status"]
        if state != "COMPLETED" and not item["can_transcribe"]:
            skipped.append({"recording_id": item["id"], "reason": state, "blocking_reasons": item["blocking_reasons"]}); continue
        if state == "COMPLETED" and item.get("storage_recording_id"):
            from .capture_associations import validated_transcript
            existing = validated_transcript(workspace, item)
            if existing:
                if not existing[0].with_suffix(".txt").exists():
                    write_transcript_text(existing[0].with_suffix(".txt"), existing[1])
                continue
        if state in {"FAILED", "INTERRUPTED"}:
            with database(workspace) as conn:
                conn.execute("BEGIN IMMEDIATE")
                previous = conn.execute("SELECT * FROM jobs WHERE recording_id=? ORDER BY created DESC LIMIT 1", (item["id"],)).fetchone()
                if previous is None:
                    previous = conn.execute("SELECT j.* FROM recording_aliases a JOIN jobs j ON j.id=a.job_id WHERE a.recording_id=?", (item["id"],)).fetchone()
                if previous and (previous["status"] in {"FAILED", "INTERRUPTED"} or previous["status"] == "RUNNING" and not _alive(previous["pid"])):
                    # Explicit retry retains the exact profile selected when this
                    # job was first enqueued, even after course assignment.
                    conn.execute("UPDATE jobs SET status='QUEUED',pid=NULL,error=NULL,updated=? WHERE id=?", (time.time(), previous["id"]))
                    added += 1
                    continue
        course = profile(workspace, item)
        sha, signature, key, _ = cache_identity(workspace, course, Path(item["path"]))
        transcript = asr_directory(workspace, item) / "transcript.json"
        if transcript.is_file():
            saved = _read(transcript, {})
            normalized = Path(saved.get("normalized_audio_path", ""))
            if (saved.get("cache_key") == key and saved.get("status") == "COMPLETED"
                    and not validate_transcript(saved, saved.get("audio_duration_seconds"))
                    and saved.get("segments_sha256") == segments_digest(saved.get("segments"))
                    and normalized.is_file() and file_sha256(normalized) == saved.get("normalized_audio_sha256")):
                if not transcript.with_suffix(".txt").exists():
                    write_transcript_text(transcript.with_suffix(".txt"), saved)
                continue
            skipped.append({"recording_id": item["id"], "reason": "EXISTING_TRANSCRIPT_NEEDS_REVIEW"}); continue
        now = time.time()
        with database(workspace) as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Identical input/settings at another path are aliases, not two ASR runs.
            duplicate = conn.execute("SELECT id,status FROM jobs WHERE cache_key=?", (key,)).fetchone()
            if duplicate:
                stat = Path(item['path']).stat()
                conn.execute("INSERT OR REPLACE INTO recording_aliases VALUES (?,?,?,?)", (item['id'], duplicate['id'], stat.st_mtime_ns, stat.st_size))
                if duplicate['status'] == 'CANCELLED':
                    conn.execute("UPDATE jobs SET status='QUEUED',recording_id=?,payload=?,updated=?,error=NULL WHERE id=?", (item['id'], json.dumps({'recording':item,'audio_sha256':sha,'settings_hash':signature,'course_profile':course}), now, duplicate['id']))
                    added += 1
                continue
            cur = conn.execute("INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, item["id"], key,
                json.dumps({"recording": item, "audio_sha256": sha, "settings_hash": signature, "course_profile": course}), "QUEUED", now, now, None, None, None))
            added += cur.rowcount
    return {"status": "QUEUED", "added": added, "skipped": skipped, "queue": status(workspace)}


def configure(workspace, *, parallel=None, paused=None):
    with database(workspace) as conn:
        conn.execute("BEGIN IMMEDIATE")
        value = settings(workspace)
        if parallel is not None:
            if type(parallel) is not int or not 1 <= parallel <= 4:
                raise ValueError("Choose between 1 and 4 simultaneous recordings")
            value["parallel"] = parallel
        if paused is not None:
            value["paused"] = bool(paused)
        atomic_json_write(root(workspace) / "settings.json", value)
        atomic_json_write(workspace.state_dir / "asr-concurrency.json", {"parallel": value["parallel"]})
    return {"status": "PAUSED" if value["paused"] else "RESUMED", **value}


def ui_lease(workspace, session, *, opened=True):
    session = str(uuid.UUID(session))
    root(workspace).mkdir(parents=True, exist_ok=True, mode=0o700)
    current = _read(root(workspace) / "ui.json", {})
    tombstone = root(workspace) / "closed" / session
    if opened and tombstone.is_file():
        return {"status": "CLOSED"}
    if opened or current.get("session") == session:
        if not opened:
            tombstone.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tombstone.touch(mode=0o600)
        atomic_json_write(root(workspace) / "ui.json", {"session": session, "expires_at": time.time() + 15 if opened else 0, "closed": not opened})
    return {"status": "OK"}


def _worker_command(workspace, action, identifier=None):
    return [sys.executable, "-m", "mynotes", "transcription-queue", action, "--workspace", str(workspace.root)] + (["--job", identifier] if identifier else [])


def launch_runner(workspace):
    root(workspace).mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root(workspace) / "runner.log").open("ab") as log:
        subprocess.Popen(_worker_command(workspace, "run"), stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)


def run_job(workspace, identifier, *, engines=None):
    from .asr import ASRBusyError, ASRReviewError, ASRError, ArgmaxEngine, cache_identity, transcribe_audio
    try:
        os.nice(10)
    except OSError:
        pass
    with database(workspace) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
        if row is None or row["status"] != "RUNNING" or row["pid"] != os.getpid():
            raise ValueError("Queue worker does not own this job")
        payload = json.loads(row["payload"])
    item, outcome, result, error = payload["recording"], "FAILED", None, None
    try:
        course = payload.get("course_profile") or profile(workspace, item)
        sha, signature, key, registry = cache_identity(workspace, course, Path(item["path"]), engines=engines)
        if sha != payload["audio_sha256"] or signature != payload["settings_hash"]:
            raise ValueError("Sorgente o impostazioni cambiate: aggiungi nuovamente la registrazione alla coda")
        if engines is None:
            for name in ("primary_engine", "secondary_engine", "extra_pass_engine"):
                engine = registry.get(course["transcription"].get(name))
                if name == "primary_engine" and engine is None:
                    raise ValueError("Completa il setup della trascrizione locale")
                if isinstance(engine, ArgmaxEngine) and not any((p / "AudioEncoder.mlmodelc").is_dir() for p in engine.model_dir.rglob("*" + course["transcription"][name.replace("engine", "model")])):
                    raise ValueError("Completa il download del modello nel setup prima di trascrivere")
        if item.get("lecture_id") and not item.get("storage_recording_id") and not item.get("language"):
            from .transcription import run_transcription
            result = run_transcription(workspace, item["lecture_id"], Path(item["path"]), engines=engines)
        else:
            manifest = transcribe_audio(workspace, item.get("lecture_id") or item["id"], course, Path(item["path"]),
                                        recording_id=item["id"] if item.get("storage_recording_id") or not item.get("lecture_id") else None, engines=engines)
            directory = asr_directory(workspace, item)
            result = {"status": manifest["status"], "transcript_json": str(directory / "transcript.json"),
                      "transcript_text": str(directory / "transcript.txt"), "segments": len(manifest["segments"])}
        outcome = "COMPLETED"
    except ASRBusyError as e:
        outcome, error = "QUEUED", str(e)
    except ASRReviewError as e:
        outcome, error = "NEEDS_REVIEW", str(e)
    except (KeyboardInterrupt, SystemExit) as e:
        outcome, error = "INTERRUPTED", str(e)
    except (ValueError, OSError, ASRError) as e:
        outcome, error = "FAILED", str(e)
    finally:
        with database(workspace) as conn:
            conn.execute("UPDATE jobs SET status=?, updated=?, error=?, result=?, pid=NULL WHERE id=?", (outcome, time.time(), error, json.dumps(result) if result else None, identifier))
    return {"status": outcome, "job": identifier, **(result or {}), **({"message": error} if error else {})}


def run(workspace, *, tick=1):
    from .capture import CaptureStore, capture_status
    directory = root(workspace)
    directory.mkdir(parents=True, exist_ok=True)
    children = {}
    pressure_checked, pressure_hold = 0, False
    with (directory / "runner.lock").open("a+") as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        while True:
            for key, child in list(children.items()):
                if child.poll() is not None:
                    del children[key]
                    with database(workspace) as conn:
                        conn.execute("UPDATE jobs SET status='INTERRUPTED',error='Worker stopped before completion',pid=NULL,updated=? WHERE id=? AND status='RUNNING'", (time.time(), key))
            snapshot = status(workspace)
            ready = snapshot["ui_open"] and not snapshot["paused"]
            if not ready and not children:
                return
            recorder = capture_status(CaptureStore(workspace))
            blocked = recorder["running"] is not False and (recorder["runtime"].get("recording") or recorder["runtime"].get("exporting"))
            if time.monotonic() - pressure_checked >= 15:
                pressure_checked = time.monotonic()
                try:
                    import re
                    output = subprocess.run(["/usr/bin/memory_pressure", "-Q"], capture_output=True, text=True, timeout=3)
                    match = re.search(r"free percentage:\s*(\d+)%", output.stdout)
                    pressure_hold = bool(match and int(match[1]) < 8)
                except (OSError, subprocess.TimeoutExpired):
                    pressure_hold = False
            reason = "In attesa della fine della registrazione o del salvataggio audio" if blocked else "In attesa di memoria disponibile" if pressure_hold else "In pausa" if snapshot["paused"] else "In attesa dell’apertura di MyNotes" if not snapshot["ui_open"] else None
            atomic_json_write(directory / "wait.json", {"reason": reason})
            blocked = blocked or pressure_hold
            if ready and not blocked and len(children) < snapshot["parallel"]:
                pending = next((j for j in snapshot["jobs"] if j["status"] == "QUEUED" and (not j.get("error") or time.time() - j["updated"] >= 10)), None)
                if pending:
                    with database(workspace) as conn:
                        conn.execute("BEGIN IMMEDIATE")
                        # The child waits for the parent to commit its claim.
                        child = subprocess.Popen(_worker_command(workspace, "worker", pending["id"]), stdin=subprocess.DEVNULL,
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                        conn.execute("UPDATE jobs SET status='RUNNING',pid=?,updated=? WHERE id=? AND status='QUEUED'", (child.pid, time.time(), pending["id"]))
                        children[pending["id"]] = child
                elif not children:
                    return
            time.sleep(tick)


def add_parser(subparsers):
    parser = subparsers.add_parser("transcription-queue", help="local recording transcription queue")
    commands = parser.add_subparsers(dest="queue_command", required=True)
    for action in ("library", "enqueue", "status", "pause", "resume", "configure", "heartbeat", "close", "retry", "remove", "rename", "run", "worker"):
        command = commands.add_parser(action)
        command.add_argument("--workspace")
        command.add_argument("--json", action="store_true")
        if action == "library":
            command.add_argument("--query")
            command.add_argument("--date", help="event date in its calendar timezone, YYYY-MM-DD")
            command.add_argument("--course")
        if action in {"enqueue", "configure"}:
            command.add_argument("--parallel", type=int, default=1)
        if action == "enqueue":
            target = command.add_mutually_exclusive_group(required=True)
            target.add_argument("--all-untranscribed", action="store_true")
            target.add_argument("--recording", help="enqueue only this recording ID")
        if action in {"heartbeat", "close"}:
            command.add_argument("--session", required=True)
        if action in {"worker", "retry", "remove"}:
            command.add_argument("--job", required=True)
        if action == "rename":
            command.add_argument("--recording", required=True)
            command.add_argument("--title", required=True)


def run_cli(args):
    from .workspace import Workspace
    ws, action = Workspace.from_value(args.workspace), args.queue_command
    if not ws.is_initialized():
        raise ValueError("Initialize MyNotes first")
    if action == "library":
        value = {"status": "OK", "recordings": library(ws, query=args.query, date=args.date, course=args.course)}
    elif action == "enqueue":
        value = enqueue(ws, args.parallel, recording_id=getattr(args, "recording", None)); launch_runner(ws)
    elif action == "status":
        value = status(ws)
    elif action in {"pause", "resume", "configure"}:
        value = configure(ws, **({"parallel": args.parallel} if action == "configure" else {"paused": action == "pause"}))
        if action == "resume":
            launch_runner(ws)
    elif action in {"heartbeat", "close"}:
        value = ui_lease(ws, args.session, opened=action == "heartbeat")
        if action == "heartbeat" and (root(ws) / "queue.sqlite3").exists():
            launch_runner(ws)
    elif action == "rename":
        title = args.title.strip()
        if not title or len(title) > 120 or any(ord(c) < 32 for c in title):
            raise ValueError("Il titolo deve contenere 1–120 caratteri stampabili")
        if not any(r["id"] == args.recording for r in library(ws)):
            raise ValueError("Registrazione non trovata")
        with database(ws) as conn:
            conn.execute("BEGIN IMMEDIATE")
            labels = _read(root(ws) / "labels.json", {})
            labels[args.recording] = title
            atomic_json_write(root(ws) / "labels.json", labels)
        value = {"status": "OK"}
    elif action in {"retry", "remove"}:
        with database(ws) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status,pid FROM jobs WHERE id=?", (args.job,)).fetchone()
            if row is None or (row["status"] == "RUNNING" and _alive(row["pid"])):
                raise ValueError("Finish the active job before changing it")
            conn.execute("UPDATE jobs SET status=?,error=NULL,pid=NULL,updated=? WHERE id=?", ("QUEUED" if action == "retry" else "CANCELLED", time.time(), args.job))
        value = {"status": "OK"}
        if action == "retry":
            launch_runner(ws)
    elif action == "worker":
        value = run_job(ws, args.job)
    else:
        run(ws); value = {"status": "STOPPED"}
    print(json.dumps(value, ensure_ascii=False))
    return 0
