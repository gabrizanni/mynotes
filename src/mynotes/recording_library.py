"""Original audio catalogue and recoverable user-facing exports.

Only explicit sync/finish/configure commands write. Polling library() stays read-only.
Technical metadata and ASR attempts never belong in the user's audio folders.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import fcntl
from copy import deepcopy
from datetime import datetime, UTC
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from .utils import atomic_json_write, file_sha256


def directory(ws):
    return ws.state_dir / 'recording-library'


_in_operation = ContextVar('recording_library_operation', default=False)


def locked(function):
    @wraps(function)
    def run(ws, *args, **kwargs):
        if _in_operation.get():
            return function(ws, *args, **kwargs)
        directory(ws).mkdir(parents=True, exist_ok=True, mode=0o700)
        with (directory(ws) / 'operation.lock').open('a+') as lease:
            fcntl.flock(lease, fcntl.LOCK_EX)
            token = _in_operation.set(True)
            try: return function(ws, *args, **kwargs)
            finally: _in_operation.reset(token)
    return run


def options(ws):
    try:
        return json.loads((directory(ws) / 'settings.json').read_text())
    except FileNotFoundError:
        return {}


def folder(ws):
    from .capture import CaptureStore
    return Path(options(ws).get('folder') or CaptureStore(ws).config()['outputDirectory'])


def records(ws):
    from .transcription_queue import database, root
    if not (root(ws) / 'queue.sqlite3').exists():
        return {}
    with database(ws, readonly=True) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='recordings'").fetchone():
            return {}
        return {r['id']: json.loads(r['metadata']) for r in conn.execute('SELECT * FROM recordings')}


def save(ws, item):
    from .transcription_queue import database
    value = {k: v for k, v in item.items() if k not in {'warnings', 'blocking_reasons', 'actions', 'can_transcribe', 'status', 'transcription_status', 'stable', 'audio_available'}}
    with database(ws) as conn:
        conn.execute('INSERT INTO recordings VALUES (?,?) ON CONFLICT(id) DO UPDATE SET metadata=excluded.metadata WHERE recordings.metadata != excluded.metadata', (item['id'], json.dumps(value, sort_keys=True)))


def roots(ws):
    from .capture import CaptureStore
    values = [folder(ws), *(Path(p).expanduser() for p in ws.configuration_store().load()['paths']['recording_dirs']), Path(CaptureStore(ws).config()['outputDirectory'])]
    return list(dict.fromkeys(p.resolve() for p in values))


def collection(ws, path):
    for base in roots(ws):
        if path.is_relative_to(base / 'processate'):
            return 'processed'
        if path.is_relative_to(base / 'processed'):
            return 'legacy'
    return 'inbox'


def allowed(ws, path):
    p = path.resolve()
    return any(p.parent == base or p.is_relative_to(base / 'processate') or p.is_relative_to(base / 'processed') for base in roots(ws))


def local_audio(path):
    try:
        state = Path(path).stat()
        # Darwin sys/stat.h: SF_DATALESS marks cloud placeholders. Reading
        # bytes during polling would hydrate them; only explicit download does.
        return Path(path).is_file() and not (sys.platform == 'darwin' and getattr(state, 'st_flags', 0) & 0x40000000)
    except OSError:
        return False


def discover(ws):
    """Merge actual files, native private sessions and persisted missing entries."""
    from .capture import CaptureStore
    from .capture_associations import registry, apply
    from .ingestion import parse_recording_name, is_stable
    config = ws.configuration_store().load()
    store = CaptureStore(ws)
    known = records(ws)
    by_path = {x['path']: x for x in known.values()}
    for x in known.values():
        if x.get('pending_destination') and not Path(x['path']).exists():
            by_path[x['pending_destination']] = x
    by_inode = {}
    for x in known.values():
        if x.get('file_identity'):
            by_inode.setdefault(tuple(x['file_identity']), []).append(x)
    paths, sessions = set(), {}
    sidecars = []
    for base in roots(ws):
        if not base.is_dir():
            continue
        paths.update(base.iterdir())
        sidecars.extend(base.glob('*.capture.json'))
        for name in ('processate', 'processed'):
            archive = base / name
            if archive.is_dir():
                paths.update(archive.rglob('*'))
                sidecars.extend(archive.rglob('*.capture.json'))
    # A private session replaces the historical sidecar emitted next to audio.
    for p in [*sidecars, *sorted((store.directory / 'sessions').glob('*.json'))]:
        try:
            s = json.loads(p.read_text())
            audio = Path(s.get('outputPath') or str(p)[:-13]).resolve()
            if s.get('isTest') or not allowed(ws, audio):
                continue
            if p in sidecars and p.parent.resolve() != audio.parent:
                continue
            sid = (s.get('event') or {}).get('recordingID') or hashlib.sha256(str(audio).encode()).hexdigest()[:32]
            if sid in known and known[sid]['path'] != str(audio):
                continue
            s['_private_session'] = p.parent == store.directory / 'sessions'
            sessions[str(audio)] = s
            paths.add(audio)
        except (OSError, ValueError, TypeError):
            continue
    links = registry(store)['recordings']
    codes = {c['code'] for c in config['courses']}
    found = {}
    for p in sorted(paths):
        if p.name.startswith('.') or p.is_symlink() or p.suffix.casefold() not in config['ingestion']['audio_extensions'] or not allowed(ws, p):
            continue
        if p.exists() and not p.is_file():
            continue
        p = p.resolve()
        if any(x.get('pending_destination') == str(p) and x['path'] != str(p) and Path(x['path']).exists() for x in known.values()):
            continue
        try:
            stat = p.stat()
        except OSError:
            stat = None
        old = by_path.get(str(p))
        if old is None and stat:
            matches = [v for v in by_inode.get((stat.st_dev, stat.st_ino), []) if not Path(v['path']).exists()]
            if len(matches) == 1:
                old = matches[0]
        session = sessions.get(str(p), {})
        event = session.get('event') or {}
        identifier = (old or {}).get('id') or event.get('recordingID') or hashlib.sha256(str(p).encode()).hexdigest()[:32]
        if not re.fullmatch('[a-f0-9]{32}', str(identifier)):
            continue
        if old is None and identifier in known and known[identifier]['path'] != str(p) and Path(known[identifier]['path']).exists() and stat:
            # A newly copied original may reuse an already-archived filename.
            identifier = hashlib.sha256(f'{p}\0{stat.st_dev}\0{stat.st_ino}\0{stat.st_mtime_ns}'.encode()).hexdigest()[:32]
        event = apply({**event, 'recordingID': identifier}, links)
        resolved = parse_recording_name(p, config)
        code = event.get('courseCode', (old or {}).get('course_code', resolved[0] if resolved else None))
        number = event.get('lessonNumber')
        lecture = f'{code}-L{number:02d}' if code and type(number) is int and 1 <= number <= 999 else (old or {}).get('lecture_id')
        if not lecture and resolved and code == resolved[0]:
            lecture = f'{code}-L{resolved[1]:02d}'
        if identifier in links:
            number = links[identifier].get('lessonNumber')
            lecture = f'{code}-L{number:02d}' if type(number) is int else None
        historical = bool(lecture and (ws.runs_dir / lecture / 'asr').exists())
        storage = (old or {}).get('storage_recording_id') if old else (session.get('event') or {}).get('recordingID') or (None if historical or (session and lecture and not (session.get('event') or {}).get('recordingID')) or (resolved and not options(ws).get('folder')) else identifier)
        item = {**(old or {}), 'id': identifier, 'path': str(p), 'original_path': (old or {}).get('original_path', str(p)),
            'title': (old or {}).get('title') or event.get('title') or p.stem, 'course_code': code, 'lecture_id': lecture,
            'storage_recording_id': storage, 'event_id': event.get('id', (old or {}).get('event_id')), 'source_id': event.get('sourceID', (old or {}).get('source_id')),
            'duration': session.get('durationSeconds', (old or {}).get('duration')), 'language': event.get('transcriptionLanguage', (old or {}).get('language')),
            'created_at': event.get('startsAt') or (old or {}).get('created_at') or (datetime.fromtimestamp(stat.st_mtime, UTC).isoformat() if stat else ''),
            'timezone': event.get('timezone', (old or {}).get('timezone', 'UTC')), 'partial': session.get('partial', (old or {}).get('partial', False)),
            'capture_status': session.get('status', (old or {}).get('capture_status')), 'capture_reason': session.get('reason', (old or {}).get('capture_reason', '')),
            'collection': collection(ws, p), 'audio_available': bool(stat and local_audio(p)), 'stable': bool(stat and local_audio(p) and (not session or session.get('status') in {'COMPLETED', 'PARTIAL'}) and (session.get('_private_session') or is_stable(p, config['ingestion']['stable_seconds']))),
            'unknown_course': code is not None and code not in codes,
            'association_state': links.get(identifier, {}).get('associationState', 'resolved' if code in codes else 'unresolved'),
            'association_reason': links.get(identifier, {}).get('reason'),
            'association_alternatives': links.get(identifier, {}).get('alternatives', []),
            'association_conflict': links.get(identifier, {}).get('conflict')}
        if stat:
            item['file_identity'] = [stat.st_dev, stat.st_ino]
        if identifier not in found or item['audio_available'] or not found[identifier]['audio_available']:
            found[identifier] = item
    for identifier, old in known.items():
        if identifier not in found:
            found[identifier] = {**old, 'audio_available': False, 'stable': False}
    return list(found.values())


@locked
def sync(ws):
    for item in records(ws).values():
        target = item.get('pending_destination')
        if target and item.get('move_sha256') and Path(target).exists() and item['path'] != target:
            _move_original(item['path'], target, item['move_sha256'])
            item.update(path=target, collection=collection(ws, Path(target)))
            save(ws, item)
    for item in discover(ws):
        save(ws, item)
    return {'status': 'OK'}


def resolve_path(ws, path):
    value = str(Path(path).resolve())
    return Path(next((x['path'] for x in records(ws).values() if value in {x['path'], x.get('original_path'), x.get('move_source')}), value))


def _move_original(source, destination, digest):
    """Exclusive publication, verified before unlink; safe after interruption."""
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        try:
            os.link(source, destination)
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.mynotes-', delete=False) as out:
                temporary = Path(out.name)
                with source.open('rb') as audio:
                    shutil.copyfileobj(audio, out)
                out.flush(); os.fsync(out.fileno())
            try:
                if file_sha256(temporary) != digest:
                    raise ValueError('La copia audio non corrisponde all’originale')
                os.link(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
    if file_sha256(destination) != digest or (source.exists() and file_sha256(source) != digest):
        raise ValueError('Audio modificato: spostamento sospeso, originali conservati')
    if source != destination:
        source.unlink(missing_ok=True)


@locked
def finish(ws, item, manifest_path):
    """Export TXT, then archive original. Failure never invalidates valid ASR."""
    from .asr import validate_transcript, segments_digest, write_transcript_text
    if not options(ws).get('folder') or not allowed(ws, Path(item['path'])):
        return {}
    saved = json.loads(Path(manifest_path).read_text())
    if saved.get('status') != 'COMPLETED' or validate_transcript(saved, saved.get('audio_duration_seconds')) or saved.get('segments_sha256') != segments_digest(saved.get('segments')):
        raise ValueError('Trascrizione non valida: originale conservato')
    sync(ws)
    current = records(ws).get(item['id'], item)
    current['transcript_json'] = str(manifest_path)
    current['archive_status'] = 'PENDING'
    save(ws, current)
    try:
        source = Path(current['path'])
        digest = saved['audio_sha256']
        pending = current.get('pending_destination')
        actual = Path(pending) if not source.exists() and pending else source
        if not actual.is_file() or file_sha256(actual) != digest:
            raise ValueError('L’originale non corrisponde alla trascrizione')
        private_text = Path(manifest_path).with_suffix('.txt')
        if not private_text.is_file():
            write_transcript_text(private_text, saved)
        codes = {c['code'] for c in ws.configuration_store().load()['courses']}
        course = current.get('course_code') if current.get('course_code') in codes else 'Dubbi'
        if not re.fullmatch(r'[A-Z0-9]+|Dubbi', course):
            raise ValueError('Corso non valido')
        target = folder(ws) / 'Trascrizioni' / course / (Path(current['original_path']).stem + '-' + item['id'][:8] + '.txt')
        target.parent.mkdir(parents=True, exist_ok=True)
        previous = current.get('export_text')
        if previous and previous != str(target) and Path(previous).is_file() and current.get('export_sha256') and file_sha256(Path(previous)) != current['export_sha256']:
            raise ValueError('La copia TXT precedente è stata modificata: conservata; ricollocazione da completare')
        if target.exists() and str(target) != previous and target.read_bytes() != private_text.read_bytes():
            raise ValueError('Esiste una copia TXT diversa: controllala prima di esportare')
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.mynotes-', delete=False) as out:
            temporary = Path(out.name); out.write(private_text.read_bytes()); out.flush(); os.fsync(out.fileno())
        try:
            if target.exists() and str(target) == previous and current.get('export_sha256') and file_sha256(target) != current['export_sha256']:
                raise ValueError('La copia TXT è stata modificata: conservata senza sovrascriverla')
            # Initial publication is exclusive. Subsequent updates own this path.
            if target.exists():
                if str(target) == previous:
                    os.replace(temporary, target)
            else:
                os.link(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        current.update(export_text=str(target), export_sha256=file_sha256(target))
        save(ws, current)
        if previous and previous != str(target):
            prior = Path(previous)
            if prior.is_file() and prior.is_relative_to(folder(ws) / 'Trascrizioni') and file_sha256(prior) == current['export_sha256']:
                prior.unlink()
        # Historical processed folders are already archived; retain their structure.
        if current.get('collection') != 'legacy':
            destination = Path(pending) if pending else folder(ws) / 'processate' / source.name
            if not pending and destination.exists() and destination != source:
                destination = destination.with_name(source.stem + '-' + item['id'] + source.suffix)
                if destination.exists():
                    raise ValueError('Destinazione audio già occupata; nessun file sovrascritto')
            current.update(pending_destination=str(destination), move_source=str(source), move_sha256=digest)
            save(ws, current)
            _move_original(source, destination, digest)
            current.update(path=str(destination), collection='processed', file_identity=[destination.stat().st_dev, destination.stat().st_ino])
        verified = Path(current['path']).stat()
        current.update(archive_status='COMPLETED', archive_error=None, audio_sha256=digest, duration=saved.get('audio_duration_seconds', current.get('duration')), verified_audio=[verified.st_mtime_ns, verified.st_size])
        save(ws, current)
        return {'current_audio_path': current['path'], 'export_text': str(target), 'archive_status': 'COMPLETED'}
    except (OSError, ValueError, KeyError) as e:
        current.update(archive_status='PENDING', archive_error=str(e))
        save(ws, current)
        return {'archive_status': 'PENDING', 'archive_error': str(e), 'current_audio_path': current['path']}


def finish_audio(ws, audio, manifest_path):
    if not options(ws).get('folder'):
        return {}
    matches = [x for x in discover(ws) if x['path'] == str(Path(audio).resolve())]
    if len(matches) != 1:
        return {}
    return finish(ws, matches[0], manifest_path)


@locked
def set_folder(ws, path):
    from .capture import CaptureStore, configure, capture_status
    from .transcription_queue import status, _alive
    state = status(ws)
    native = capture_status(CaptureStore(ws))
    if native['running'] is not False and (native['runtime'].get('recording') or native['runtime'].get('exporting')):
        raise ValueError('Attendi la fine della registrazione prima di cambiare cartella')
    if any(j['status'] in {'RUNNING', 'CANCELLING'} and _alive(j.get('pid')) for j in state['jobs']):
        raise ValueError('Attendi la fine delle trascrizioni prima di cambiare cartella')
    direct = ws.state_dir / 'asr-active.json'
    if direct.is_file():
        active = json.loads(direct.read_text())
        if active.get('status') == 'RUNNING' and _alive(active.get('pid')):
            raise ValueError('Attendi la fine della trascrizione prima di cambiare cartella')
    path = Path(path).expanduser().resolve()
    if path == ws.root or path.is_relative_to(ws.root):
        raise ValueError('Scegli una cartella utente esterna al workspace privato')
    sync(ws)
    old_folder = folder(ws)
    moving = [x for x in discover(ws) if x['audio_available'] and x.get('collection') == 'inbox' and Path(x['path']).parent != path]
    backup = None
    if moving or old_folder != path:
        import uuid
        backup = directory(ws) / 'migrations' / uuid.uuid4().hex
        backup.mkdir(parents=True, mode=0o700)
        for config in (ws.configuration_path, ws.config_dir / 'capture.json', directory(ws) / 'settings.json'):
            if config.exists(): shutil.copy2(config, backup / (config.parent.name + '-' + config.name))
        inventory = []
        for item in moving:
            original = Path(item['path']); digest = file_sha256(original)
            destination = backup / 'originals' / item['id'] / original.name
            destination.parent.mkdir(parents=True, mode=0o700)
            shutil.copy2(original, destination)
            if file_sha256(destination) != digest: raise ValueError('Backup audio non verificato')
            sidecar = Path(str(original) + '.capture.json')
            if sidecar.is_file(): shutil.copy2(sidecar, destination.parent / sidecar.name)
            inventory.append({'id': item['id'], 'path': str(original), 'sha256': digest, 'backup': str(destination)})
        atomic_json_write(backup / 'inventory.json', inventory)
    configure(CaptureStore(ws), {'outputDirectory': str(path)})
    atomic_json_write(directory(ws) / 'settings.json', {'folder': str(path)})
    for item in moving:
        source = Path(item['path']); digest = file_sha256(source)
        destination = path / source.name
        if destination.exists(): destination = path / (source.stem + '-' + item['id'] + source.suffix)
        if destination.exists(): raise ValueError('Destinazione occupata; audio e backup conservati')
        item.update(pending_destination=str(destination), move_source=str(source), move_sha256=digest)
        save(ws, item)
        _move_original(source, destination, digest)
        item.update(path=str(destination), collection='inbox', pending_destination=None, file_identity=[destination.stat().st_dev, destination.stat().st_ino])
        save(ws, item)
        # Session metadata was persisted in the catalogue; remove only its copied historical sidecar.
        Path(str(source) + '.capture.json').unlink(missing_ok=True)
    sync(ws)
    return {'status': 'OK', 'folder': str(path), 'moved': len(moving), 'backup': str(backup) if backup else None}


@locked
def relink(ws, identifier, path):
    from .transcription_queue import status
    if any(j['recording_id'] == identifier and j['status'] in {'RUNNING', 'QUEUED'} for j in status(ws)['jobs']):
        raise ValueError('Rimuovi il lavoro dalla coda prima di ricollegare l’audio')
    item = records(ws).get(identifier)
    path = Path(path).expanduser().resolve()
    if path.suffix.casefold() not in ws.configuration_store().load()['ingestion']['audio_extensions']:
        raise ValueError('Scegli un file audio supportato')
    if not item or Path(item['path']).exists() or not path.is_file() or not allowed(ws, path):
        raise ValueError('Scegli l’audio mancante nella raccolta configurata')
    manifest = Path(item.get('transcript_json') or ws.runs_dir / '_recordings' / identifier / 'asr/transcript.json')
    if manifest.is_file() and json.loads(manifest.read_text()).get('audio_sha256') != file_sha256(path):
        raise ValueError('Il file scelto non corrisponde all’originale trascritto')
    item.update(path=str(path), file_identity=[path.stat().st_dev, path.stat().st_ino])
    save(ws, item)
    return {'status': 'OK'}
