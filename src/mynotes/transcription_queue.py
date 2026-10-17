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
        conn.execute("CREATE TABLE IF NOT EXISTS recordings (id TEXT PRIMARY KEY, metadata TEXT NOT NULL)")
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
    from .recording_library import discover
    for item in discover(workspace):
        if item['id'] in active_ids or item.get('event_id') in active_ids:
            item['stable'] = False
        items[item['path']] = item
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
            item["blocking_reasons"].append({"code": "AUDIO_UNAVAILABLE", "message": "Audio presente nel cloud: scaricalo sul Mac prima di trascrivere." if Path(item['path']).is_file() else "File audio non disponibile. Ricollega il file prima di trascrivere."})
        elif not item["stable"]:
            item["blocking_reasons"].append({"code": "AUDIO_NOT_FINAL", "message": "Audio ancora in registrazione, salvataggio o aggiornamento. Attendi che il salvataggio termini."})
        path = Path(item.get("transcript_json") or asr_directory(workspace, item) / "transcript.json")
        if path.is_file():
            try:
                from .asr import validate_transcript, segments_digest
                saved = json.loads(path.read_text())
                if (saved.get("status") == "COMPLETED" and not validate_transcript(saved, saved.get("audio_duration_seconds"))
                        and saved.get("segments_sha256") == segments_digest(saved.get("segments"))):
                    if item["stable"]:
                        audio_stat = Path(item['path']).stat()
                        verified = item.get('verified_audio') == [audio_stat.st_mtime_ns, audio_stat.st_size] and item.get('audio_sha256') == saved.get('audio_sha256')
                        state = "COMPLETED" if verified or audio_stat.st_mtime <= path.stat().st_mtime else "OUTDATED"
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
                item["warnings"].append({"code": "TRANSCRIPTION_FAILED", "message": "Il motore ha prodotto timestamp non validi. " + message.removeprefix("NEEDS_REVIEW: engine output has invalid timestamps: ") + " Puoi riprovare da zero; l’audio è conservato." if 'timestamp' in message.casefold() else message})
                if state == "NEEDS_REVIEW":
                    item["blocking_reasons"].append({"code": "ASR_RESULT_INVALID", "message": message})
        if detail.get('review_path') and Path(detail['review_path']).is_file():
            item['diagnostic_path'] = detail['review_path']
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
                aliases = {row["recording_id"]: row for row in conn.execute("SELECT a.*,j.id AS id,j.status,j.result,j.pid,j.error FROM recording_aliases a JOIN jobs j ON j.id=a.job_id")}
            for item in items.values():
                if item["transcription_status"] not in {"NOT_STARTED", "RUNNING", "FAILED", "INTERRUPTED", "NEEDS_REVIEW"}:
                    continue
                alias = aliases.get(item["id"])
                stat = Path(item["path"]).stat() if item["audio_available"] else None
                if alias and stat and (stat.st_mtime_ns, stat.st_size) == (alias["mtime_ns"], alias["size"]):
                    job = alias
                else:
                    job = jobs.get(item["id"])
                if not job:
                    continue
                item['job_id'] = job['id']
                if job["status"] == "CANCELLED":
                    if item["transcription_status"] != "COMPLETED":
                        item["transcription_status"] = item["status"] = "NOT_STARTED" if item["stable"] else "WAITING"
                        item["warnings"] = [w for w in item["warnings"] if w["code"] != "TRANSCRIPTION_FAILED"]
                        item["blocking_reasons"] = [r for r in item["blocking_reasons"] if r["code"] not in {"ASR_RESULT_INVALID", "TRANSCRIPT_INVALID", "TRANSCRIPT_UNREADABLE"}]
                    continue
                job_state = ("INTERRUPTED" if job["status"] == "RUNNING" else "CANCELLED") if job["status"] in {"RUNNING", "CANCELLING"} and not _alive(job["pid"]) else job["status"]
                if job_state == 'CANCELLED': job_state = 'NOT_STARTED' 
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
                    item["warnings"].append({"code": "TRANSCRIPTION_FAILED", "message": "Il motore ha prodotto timestamp non validi. " + message.removeprefix("NEEDS_REVIEW: engine output has invalid timestamps: ") + " Puoi riprovare da zero; l’audio è conservato." if 'timestamp' in message.casefold() else message})
                    if job_state == "NEEDS_REVIEW":
                        item["blocking_reasons"].append({"code": "ASR_RESULT_INVALID", "message": message})
    queue_state = status(workspace)
    for item in items.values():
        state = item['transcription_status']
        jobs_for_item = [j for j in queue_state['jobs'] if j['recording_id'] == item['id']]
        job = jobs_for_item[-1] if jobs_for_item else None
        item['job_id'] = item.get('job_id') or (job['id'] if job else None)
        structural = [r for r in item['blocking_reasons'] if r['code'] not in {'ASR_RESULT_INVALID', 'TRANSCRIPT_INVALID', 'TRANSCRIPT_UNREADABLE', 'AUDIO_CHANGED'}]
        usable = item['stable'] and not structural
        item['can_transcribe'] = bool(usable and state not in {'COMPLETED', 'QUEUED', 'RUNNING', 'CANCELLING'})
        actions = []
        if usable and state == 'NOT_STARTED': actions.append('enqueue')
        if usable and state in {'FAILED', 'INTERRUPTED'}: actions.append('retry')
        if usable and state in {'NEEDS_REVIEW', 'OUTDATED'}: actions.append('restart')
        if item.get('job_id') and state == 'QUEUED': actions.append('remove')
        if item.get('job_id') and state == 'RUNNING': actions.append('cancel')
        if state == 'COMPLETED': actions.append('finish')
        if not item['audio_available']: actions.append('download' if Path(item['path']).is_file() else 'relink')
        if usable and state not in {'QUEUED', 'RUNNING', 'CANCELLING'} and item.get('storage_recording_id'):
            actions.append('associate')
        item['actions'] = actions
        item['queue_position'] = job.get('queue_position') if job else None
        item['wait_reason'] = queue_state.get('wait_reason') if state == 'QUEUED' else None
        item['status'] = state if state != 'NOT_STARTED' else item['status']
        item['warnings'] = list({(w['code'], w['message']): w for w in item['warnings']}.values())
        item['blocking_reasons'] = structural
        if state in {'QUEUED', 'RUNNING'}:
            item['can_transcribe'] = False
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
    from .recording_library import records
    catalogue = records(workspace)
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
                if job['status'] == 'CANCELLING' and not _alive(job['pid']):
                    job['status'] = 'CANCELLED'
                try:
                    detail = _read(asr_directory(workspace, item['recording']) / 'status.json', {})
                except (OSError, ValueError):
                    detail = {}
                if detail.get('review_path') and Path(detail['review_path']).is_file(): job['diagnostic_path'] = detail['review_path']
                if job["status"] == "RUNNING":
                    if not _alive(job["pid"]): job["status"] = "INTERRUPTED"
                    job["phase"], job["elapsed_seconds"] = detail.get("phase"), detail.get("elapsed_seconds")
                job['actions'] = ({'QUEUED': ['remove'], 'RUNNING': ['cancel'], 'FAILED': ['retry', 'restart'], 'INTERRUPTED': ['retry', 'restart'], 'NEEDS_REVIEW': ['restart'], 'COMPLETED': ['finish']}).get(job['status'], [])
                current_recording = catalogue.get(job['recording_id'], {})
                for key in ('export_text', 'archive_status', 'archive_error'):
                    if key in current_recording: job[key] = current_recording[key]
                jobs.append(job)
    lease = _read(root(workspace) / "ui.json", {})
    closed = (root(workspace) / "closed" / str(lease.get("session", "none"))).is_file()
    for position, job in enumerate((j for j in jobs if j['status'] == 'QUEUED'), 1):
        job['queue_position'] = position
    current_settings = settings(workspace)
    ui_open = lease.get('expires_at', 0) > time.time() and not closed
    wait = _read(root(workspace) / 'wait.json', {}).get('reason')
    reason = ('In pausa' if current_settings['paused'] else 'In attesa dell’apertura di MyNotes' if not ui_open else 'Slot di trascrizione occupati' if any(j['status'] == 'QUEUED' and j.get('error') for j in jobs) else wait if wait in {'In attesa di memoria disponibile', 'Slot di trascrizione occupati'} else None)
    return {'status': 'OK', **current_settings, 'ui_open': ui_open, 'jobs': jobs, 'wait_reason': reason}


def enqueue(workspace, parallel=1, *, recording_id=None):
    from .asr import cache_identity, validate_transcript, segments_digest, write_transcript_text
    if type(parallel) is not int or not 1 <= parallel <= 4:
        raise ValueError("Choose between 1 and 4 simultaneous recordings")
    from .recording_library import sync
    recordings = library(workspace)
    if recording_id is not None:
        recordings = [item for item in recordings if item["id"] == recording_id]
        if not recordings:
            raise ValueError("Registrazione non trovata")
    sync(workspace)
    configure(workspace, parallel=parallel)
    added, skipped, reuse_archives = 0, [], []
    for item in recordings:
        state = item["transcription_status"]
        if recording_id is None and (item.get('collection', 'inbox') != 'inbox' or state not in {'NOT_STARTED', 'COMPLETED'}):
            continue
        if state != "COMPLETED" and not item["can_transcribe"]:
            skipped.append({"recording_id": item["id"], "reason": state, "blocking_reasons": item["blocking_reasons"]}); continue
        if state == "COMPLETED" and item.get("storage_recording_id"):
            from .capture_associations import validated_transcript
            existing = validated_transcript(workspace, item)
            if existing:
                if not existing[0].with_suffix(".txt").exists():
                    write_transcript_text(existing[0].with_suffix(".txt"), existing[1])
                continue
        if state in {'FAILED', 'INTERRUPTED', 'NEEDS_REVIEW', 'OUTDATED'}:
            previous = next((j for j in reversed(status(workspace)['jobs']) if j['recording_id'] == item['id']), None)
            if previous:
                change_job(workspace, previous['id'], 'restart' if state in {'NEEDS_REVIEW', 'OUTDATED'} else 'retry')
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
            # Preserve the invalid candidate; ASR quarantines it before a new attempt.
            pass
        now = time.time()
        with database(workspace) as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Identical input/settings at another path are aliases, not two ASR runs.
            duplicate = conn.execute("SELECT id,status,result FROM jobs WHERE cache_key=?", (key,)).fetchone()
            if duplicate:
                stat = Path(item['path']).stat()
                conn.execute("INSERT OR REPLACE INTO recording_aliases VALUES (?,?,?,?)", (item['id'], duplicate['id'], stat.st_mtime_ns, stat.st_size))
                if duplicate['status'] == 'COMPLETED' and duplicate['result']:
                    reuse_archives.append((item, json.loads(duplicate['result'])['transcript_json']))
                if duplicate['status'] == 'CANCELLED':
                    conn.execute("UPDATE jobs SET status='QUEUED',recording_id=?,payload=?,updated=?,error=NULL WHERE id=?", (item['id'], json.dumps({'recording':item,'audio_sha256':sha,'settings_hash':signature,'course_profile':course}), now, duplicate['id']))
                    added += 1
                continue
            cur = conn.execute("INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, item["id"], key,
                json.dumps({"recording": item, "audio_sha256": sha, "settings_hash": signature, "course_profile": course}), "QUEUED", now, now, None, None, None))
            added += cur.rowcount
    from .recording_library import finish
    for item, path in reuse_archives:
        finish(workspace, item, path)
    return {"status": "QUEUED", "added": added, "completed_reused": len(reuse_archives), "skipped": skipped, "queue": status(workspace)}


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
    from .recording_library import resolve_path
    item['path'] = str(resolve_path(workspace, item['path']))
    try:
        course = payload.get("course_profile") or profile(workspace, item)
        sha, signature, key, registry = cache_identity(workspace, course, Path(item["path"]), engines=engines)
        timing_upgrade = False
        if sha == payload["audio_sha256"] and signature != payload["settings_hash"] and getattr(registry.get("argmax"), "timestamp_mode", None) == "segment":
            # An admitted 0.2.15 job keeps its frozen source/model/glossary.
            # Accept only the exact prior signature, before timing policy existed.
            legacy_signature = cache_identity(workspace, course, Path(item["path"]), engines=engines, legacy_timing=True)[1]
            timing_upgrade = legacy_signature == payload["settings_hash"]
        if sha != payload["audio_sha256"] or (signature != payload["settings_hash"] and not timing_upgrade):
            raise ValueError("Sorgente o impostazioni cambiate: aggiungi nuovamente la registrazione alla coda")
        if engines is None:
            for name in ("primary_engine", "secondary_engine", "extra_pass_engine"):
                engine = registry.get(course["transcription"].get(name))
                if name == "primary_engine" and engine is None:
                    raise ValueError("Completa il setup della trascrizione locale")
                if isinstance(engine, ArgmaxEngine) and not any((p / "AudioEncoder.mlmodelc").is_dir() for p in engine.model_dir.rglob("*" + course["transcription"][name.replace("engine", "model")])):
                    raise ValueError("Completa il download del modello nel setup prima di trascrivere")
        manifest = transcribe_audio(workspace, item.get('lecture_id') or item['id'], course, Path(item['path']),
            recording_id=item['id'] if item.get('storage_recording_id') or not item.get('lecture_id') else None,
            engines=engines, force=payload.get('force', False))
        directory = asr_directory(workspace, item)
        result = {'status': manifest['status'], 'transcript_json': str(directory / 'transcript.json'),
            'transcript_text': str(directory / 'transcript.txt'), 'segments': len(manifest['segments']),
            **({'timing_policy_upgrade': {'from': 'legacy_word', 'to': 'segment'}} if timing_upgrade else {}),
            **{k: manifest[k] for k in ('archive_status', 'archive_error', 'current_audio_path', 'export_text') if k in manifest}}
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
            conn.execute("UPDATE jobs SET status=CASE WHEN status='CANCELLING' THEN 'CANCELLED' ELSE ? END, updated=?, error=?, result=?, pid=NULL WHERE id=? AND status IN ('RUNNING','CANCELLING') AND pid=? AND payload=?", (outcome, time.time(), error, json.dumps(result) if result else None, identifier, os.getpid(), row['payload']))
    if outcome == 'COMPLETED' and result:
        from .recording_library import discover, finish
        with database(workspace, readonly=True) as conn:
            committed = conn.execute("SELECT status FROM jobs WHERE id=?", (identifier,)).fetchone()
            ids = {r['recording_id'] for r in conn.execute('SELECT recording_id FROM recording_aliases WHERE job_id=?', (identifier,))}
        if committed and committed['status'] == 'COMPLETED':
            for alias in discover(workspace):
                if alias['id'] in ids and alias['id'] != item['id']:
                    finish(workspace, alias, result['transcript_json'])
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
            with database(workspace) as conn:
                for row in conn.execute("SELECT id,status,pid FROM jobs WHERE status IN ('RUNNING','CANCELLING')").fetchall():
                    if not _alive(row['pid']):
                        conn.execute('UPDATE jobs SET status=?,pid=NULL,updated=? WHERE id=? AND pid IS ?', ('CANCELLED' if row['status'] == 'CANCELLING' else 'INTERRUPTED', time.time(), row['id'], row['pid']))
            snapshot = status(workspace)
            ready = snapshot["ui_open"] and not snapshot["paused"]
            if not ready and not children:
                return
            recorder = capture_status(CaptureStore(workspace))
            blocked = False  # Independent stable audio may transcribe while the microphone records.
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
                        owned = conn.execute("SELECT status FROM jobs WHERE id=?", (pending['id'],)).fetchone()
                        if not owned or owned['status'] != 'QUEUED':
                            continue
                        # The child waits for the parent to commit its claim.
                        child = subprocess.Popen(_worker_command(workspace, "worker", pending["id"]), stdin=subprocess.DEVNULL,
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                        conn.execute("UPDATE jobs SET status='RUNNING',pid=?,updated=? WHERE id=? AND status='QUEUED'", (child.pid, time.time(), pending["id"]))
                        children[pending["id"]] = child
                elif not children:
                    return
            time.sleep(tick)


def change_job(workspace, identifier, action):
    """Serialize user transitions with runner claims; keep attempt files intact."""
    import signal
    from .recording_library import resolve_path
    pid = None
    with database(workspace) as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('Lavoro non trovato')
        live = row['pid'] and _alive(row['pid'])
        if action == 'remove' and row['status'] != 'QUEUED':
            raise ValueError('Solo un lavoro in attesa può essere rimosso dalla coda')
        if action in {'retry', 'restart'} and live:
            raise ValueError('Attendi che la trascrizione attiva termini')
        payload = json.loads(row['payload'])
        if action in {'retry', 'restart'}:
            item = payload['recording']
            item['path'] = str(resolve_path(workspace, item['path']))
            from .ingestion import is_stable
            if not Path(item['path']).is_file() or not is_stable(Path(item['path']), workspace.configuration_store().load()['ingestion']['stable_seconds']):
                raise ValueError('Audio mancante o ancora in aggiornamento')
            from .asr import cache_identity
            course = payload.get('course_profile') or profile(workspace, item)
            sha, signature, key, _ = cache_identity(workspace, course, Path(item['path']))
            if sha != payload['audio_sha256']:
                raise ValueError('L’audio è cambiato: aggiungilo come nuova sorgente')
            payload.update(course_profile=course, settings_hash=signature, force=action == 'restart', _claim=uuid.uuid4().hex)
            conn.execute("UPDATE jobs SET status='QUEUED',payload=?,cache_key=?,error=NULL,pid=NULL,updated=? WHERE id=?", (json.dumps(payload), key, time.time(), identifier))
        else:
            pid = row['pid'] if action == 'cancel' and live else None
            state = 'CANCELLING' if pid else 'CANCELLED'
            conn.execute('UPDATE jobs SET status=?,error=NULL,updated=? WHERE id=?', (state, time.time(), identifier))
    if pid:
        # The worker's SIGTERM handler tears down only its own dedicated ASR group.
        try: os.kill(pid, signal.SIGTERM)
        except ProcessLookupError: pass
    return {'status': 'OK', 'message': 'Interruzione richiesta; audio conservato.' if pid else 'Registrazione rimossa dalla coda; audio conservato.' if action == 'remove' else 'Nuovo tentativo aggiunto alla coda.'}


def add_parser(subparsers):
    parser = subparsers.add_parser("transcription-queue", help="local recording transcription queue")
    commands = parser.add_subparsers(dest="queue_command", required=True)
    for action in ("library", "enqueue", "status", "pause", "resume", "configure", "heartbeat", "close", "retry", "restart", "remove", "cancel", "finish", "folder", "relink", "download", "associate", "rename", "run", "worker"):
        command = commands.add_parser(action)
        command.add_argument("--workspace")
        command.add_argument("--json", action="store_true")
        if action == "library":
            command.add_argument("--sync", action="store_true")
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
        if action in {"worker", "retry", "restart", "remove", "cancel"}:
            command.add_argument("--job", required=True)
        if action in {'folder', 'relink'}:
            command.add_argument('--path', required=True)
        if action in {'finish', 'relink', 'download', 'associate'}:
            command.add_argument('--recording', required=True)
        if action == 'associate':
            command.add_argument('--course', required=True)
            command.add_argument('--lesson', type=int)
        if action == "rename":
            command.add_argument("--recording", required=True)
            command.add_argument("--title", required=True)


def run_cli(args):
    from .workspace import Workspace
    ws, action = Workspace.from_value(args.workspace), args.queue_command
    if not ws.is_initialized():
        raise ValueError("Initialize MyNotes first")
    if action == "library":
        from .recording_library import sync, folder, options
        if getattr(args, 'sync', False): sync(ws)
        value = {"status": "OK", "recordings": library(ws, query=args.query, date=args.date, course=args.course), "folder": str(folder(ws)), "folder_configured": bool(options(ws).get("folder"))}
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
    elif action in {'retry', 'restart', 'remove', 'cancel'}:
        value = change_job(ws, args.job, action)
        if action in {'retry', 'restart'}: launch_runner(ws)
    elif action == 'folder':
        from .recording_library import set_folder
        value = set_folder(ws, args.path)
    elif action == 'download':
        from .ingestion import hydrate
        from .recording_library import sync
        item = next((r for r in library(ws) if r['id'] == args.recording), None)
        if not item or not Path(item['path']).is_file(): raise ValueError('Audio non trovato: ripristinalo dal Finder')
        hydrate(Path(item['path'])); sync(ws)
        value = {'status': 'OK', 'message': 'Audio disponibile sul Mac.'}
    elif action == 'associate':
        from .capture_associations import assign
        from .capture import CaptureStore
        value = assign(CaptureStore(ws), {'assignments': [{'recordingID': args.recording, 'courseCode': args.course, 'lessonNumber': args.lesson}]})
    elif action == 'relink':
        from .recording_library import relink
        value = relink(ws, args.recording, args.path)
    elif action == 'finish':
        from .recording_library import finish
        item = next((r for r in library(ws) if r['id'] == args.recording), None)
        if not item: raise ValueError('Registrazione non trovata')
        value = {'status': 'OK', **finish(ws, item, item.get('transcript_json') or asr_directory(ws, item) / 'transcript.json')}
        if value.get('archive_status') == 'PENDING':
            value.update(status='DEFERRED', message=value.get('archive_error'))
    elif action == "worker":
        value = run_job(ws, args.job)
    else:
        run(ws); value = {"status": "STOPPED"}
    print(json.dumps(value, ensure_ascii=False))
    return 0
