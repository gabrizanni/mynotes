"""Durable source choices and transactional imports, independent of scan metadata."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from .utils import atomic_json_write
from .recording_library import locked


def policy(ws):
    path = ws.state_dir / 'recording-library/audio-sources.json'
    if not path.exists():
        return {'schemaVersion': 1, 'preferred': {}, 'excluded': [], 'imports': {}}
    value = json.loads(path.read_text())
    if value.get('schemaVersion') != 1:
        raise ValueError('Versione del registro audio non supportata')
    return value


def save_policy(ws, value):
    atomic_json_write(ws.state_dir / 'recording-library/audio-sources.json', value)


def project(ws, items):
    """Read-only: never hash, hydrate, rename or persist during UI polling."""
    from .capture import CaptureStore
    value = policy(ws)
    events = CaptureStore(ws).source_events()
    by_lesson = defaultdict(list)
    by_event = {e['id']: e for e in events}
    for event in events:
        if event.get('courseCode') and event.get('lessonNumber'):
            by_lesson[f"{event['courseCode']}-L{event['lessonNumber']:02d}"].append(event)
    groups = defaultdict(list)
    for item in items:
        binding = value.get('bindings', {}).get(item['id'])
        if binding is not None:
            item.update(binding, association_source='explicit', association_state='resolved' if binding.get('course_code') else 'unresolved', association_conflict=None)
        imported = value['imports'].get(item['id'], {})
        if imported:
            for key in ('duration', 'origin', 'original_name', 'imported_from'):
                if key in imported: item[key] = imported[key]
            # A later explicit association always outranks import-time suggestions.
            if imported.get('event_id') and item.get('association_source') != 'explicit':
                event = by_event.get(imported['event_id'])
                if event:
                    item.update(event_id=event['id'], course_code=event.get('courseCode'),
                                lecture_id=f"{event['courseCode']}-L{event['lessonNumber']:02d}" if event.get('courseCode') and event.get('lessonNumber') else None,
                                created_at=event['startsAt'], timezone=event['timezone'])
        item.setdefault('original_name', Path(item['path']).name)
        item.setdefault('origin', 'MyNotes' if item.get('capture_status') else 'File esterno')
        matches = by_lesson.get(item.get('lecture_id'), [])
        event_id = item.get('event_id')
        if len(matches) == 1 and (not event_id or event_id == matches[0]['id']):
            event_id = matches[0]['id']
        key = 'event:' + event_id if event_id else ('lesson:' + item['lecture_id'] if item.get('lecture_id') and len(matches) < 2 else 'audio:' + item['id'])
        item['lesson_key'] = key
        item['source_excluded'] = item['id'] in value['excluded']
        groups[key].append(item)
    for key, group in groups.items():
        candidates = [i for i in group if not i['source_excluded']]
        preferred = value['preferred'].get(key)
        chosen = preferred if preferred else candidates[0]['id'] if len(candidates) == 1 else None
        available_ids = {i['id'] for i in candidates}
        needs_choice = bool(candidates and (chosen is None or chosen not in available_ids))
        for item in group:
            item.update(source_selected=item['id'] == chosen and not item['source_excluded'], source_selection_required=needs_choice,
                        source_count=len(group))
            if not item['source_selected']:
                reason = ('Audio escluso dalla trascrizione.' if item['source_excluded'] else
                          'Scegli quale audio usare per questa lezione.' if needs_choice else 'Audio alternativo: viene utilizzata un’altra sorgente.')
                item['can_transcribe'] = False
                item.setdefault('blocking_reasons', []).append({'code': 'SOURCE_SELECTION', 'message': reason})
                item['actions'] = [a for a in item.get('actions', []) if a not in {'enqueue', 'retry', 'restart', 'recover'}]
    return items


def _items(ws):
    from .recording_library import discover, records
    known = records(ws)
    known.update({i['id']: i for i in discover(ws)})
    return project(ws, list(known.values()))


def _idle(ws, identifiers):
    from .transcription_queue import status, asr_directory, _alive
    from .recording_library import records
    for item in records(ws).values():
        if item['id'] not in identifiers: continue
        checkpoint = asr_directory(ws, item) / 'status.json'
        if checkpoint.exists():
            state = json.loads(checkpoint.read_text())
            if state.get('status') in {'RUNNING', 'CANCELLING'} and _alive(state.get('pid')):
                raise ValueError('Interrompi la trascrizione attiva prima di cambiare la sorgente audio.')
    if any(j['recording_id'] in identifiers and j['status'] in {'QUEUED', 'RUNNING', 'CANCELLING'} for j in status(ws)['jobs']):
        raise ValueError('Rimuovi dalla coda o interrompi la trascrizione prima di cambiare la sorgente audio.')


@locked
def choose(ws, identifier, action):
    from .recording_library import allowed, sync
    sync(ws)
    items = _items(ws)
    item = next((i for i in items if i['id'] == identifier), None)
    if not item: raise ValueError('Audio non trovato nella raccolta')
    related = {i['id'] for i in items if i['lesson_key'] == item['lesson_key']}
    _idle(ws, related)
    value = policy(ws)
    was_excluded = identifier in value['excluded']
    if action == 'prefer-audio':
        if not Path(item['path']).is_file(): raise ValueError('Ripristina o importa l’audio prima di selezionarlo')
        value['preferred'][item['lesson_key']] = identifier
        value['excluded'] = [i for i in value['excluded'] if i != identifier]
    elif action in {'exclude-audio', 'prepare-trash'}:
        if identifier not in value['excluded']: value['excluded'].append(identifier)
    elif action == 'restore-audio':
        value['excluded'] = [i for i in value['excluded'] if i != identifier]
    else: raise ValueError('Operazione audio non supportata')
    if action == 'prepare-trash':
        path = Path(item['path'])
        if not path.is_file() or path.is_symlink() or not allowed(ws, path):
            raise ValueError('L’audio non è disponibile nella raccolta gestita')
    save_policy(ws, value)
    return {'status': 'OK', 'recording_id': identifier, 'path': item['path'], 'was_excluded': was_excluded}


def inspect_audio(ws, source):
    from .ingestion import is_stable
    from .recording_library import local_audio
    path = Path(source).expanduser()
    if path.is_symlink() or not path.is_file(): raise ValueError('Scegli un file audio locale, non un collegamento')
    if path.suffix.casefold() not in ws.configuration_store().load()['ingestion']['audio_extensions']:
        raise ValueError('Formato audio non supportato')
    if not local_audio(path): raise ValueError('Scarica il file dal cloud prima di importarlo')
    if not is_stable(path, ws.configuration_store().load()['ingestion']['stable_seconds']):
        raise ValueError('Il file è ancora in copia o è stato modificato da poco. Attendi e riprova.')
    import av
    try:
        with av.open(str(path)) as container:
            if not container.streams.audio: raise ValueError('Il file non contiene una traccia audio')
            stream = container.streams.audio[0]
            duration = float(container.duration / av.time_base) if container.duration else float(stream.duration * stream.time_base) if stream.duration else None
    except (av.error.FFmpegError, OSError) as exc:
        raise ValueError('Il file audio non è leggibile o è incompleto') from exc
    stat = path.stat()
    return {'path': str(path.resolve()), 'name': path.name, 'duration': duration, 'size': stat.st_size,
            'mtime_ns': stat.st_mtime_ns, 'device': stat.st_dev, 'inode': stat.st_ino}


@locked
def import_audio(ws, paths, *, event_id=None, course=None, lesson=None):
    from .capture import CaptureStore
    from .recording_library import folder, discover, sync
    from .utils import file_sha256
    if not isinstance(paths, list) or not paths or len(paths) > 100: raise ValueError('Scegli da 1 a 100 file audio')
    store = CaptureStore(ws)
    if event_id and not any(e['id'] == event_id for e in store.source_events()): raise ValueError('Lezione non trovata')
    codes = {c['code'] for c in ws.configuration_store().load()['courses']}
    if course and (course not in codes or lesson is not None and (type(lesson) is not int or not 1 <= lesson <= 999)):
        raise ValueError('Associazione corso/lezione non valida')
    inputs = [inspect_audio(ws, p) for p in paths]
    destination = folder(ws); destination.mkdir(parents=True, exist_ok=True)
    value = policy(ws); existing = discover(ws); results = []
    hashes = {}
    # Hash only during an explicit import. Never trust an old hash after a file changes.
    candidate_sizes = {item['size'] for item in inputs}
    for item in existing:
        p = Path(item['path'])
        if item['audio_available'] and p.stat().st_size in candidate_sizes:
            hashes.setdefault(file_sha256(p), item)
    for source in inputs:
        path = Path(source['path'])
        before = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev) != (source['size'], source['mtime_ns'], source['inode'], source['device']):
            raise ValueError('L’audio è cambiato durante la verifica. Riprova.')
        descriptor, temporary = tempfile.mkstemp(prefix='.import-', dir=destination)
        temporary = Path(temporary); published = None
        try:
            digest = hashlib.sha256()
            with os.fdopen(descriptor, 'wb') as out, path.open('rb') as original:
                for block in iter(lambda: original.read(1024 * 1024), b''):
                    digest.update(block); out.write(block)
                out.flush(); os.fsync(out.fileno())
            after = path.stat()
            if (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev) != (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev):
                raise ValueError('L’audio è cambiato durante la copia. L’originale è conservato.')
            os.utime(temporary, ns=(before.st_atime_ns, before.st_mtime_ns))
            sha = digest.hexdigest()
            if sha in hashes:
                item = hashes[sha]
                results.append({'recording_id': item['id'], 'duplicate': True, 'name': source['name']})
                # A duplicate never silently changes its existing association.
                continue
            stem = path.stem.strip('. ').encode('utf-8')[:140].decode('utf-8', errors='ignore') or 'Audio'
            published = destination / (stem + '--' + uuid.uuid4().hex[:12] + path.suffix.casefold())
            # Exclusive publication: a concurrent name collision cannot overwrite audio.
            try:
                os.link(temporary, published)
            except OSError as error:
                import errno
                if error.errno not in {errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP, errno.EXDEV}: raise
                # Some cloud volumes forbid hard links. Reserve exclusively and
                # keep its mtime recent until copying/fsync is complete.
                descriptor = os.open(published, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                identity = os.fstat(descriptor)
                try:
                    with os.fdopen(descriptor, 'wb') as target, temporary.open('rb') as original:
                        shutil.copyfileobj(original, target, 1024 * 1024)
                        target.flush(); os.fsync(target.fileno())
                    os.utime(published, ns=(before.st_atime_ns, before.st_mtime_ns))
                except BaseException:
                    if published.exists() and published.stat().st_ino == identity.st_ino: published.unlink()
                    raise
            identifier = hashlib.sha256(str(published.resolve()).encode()).hexdigest()[:32]
            metadata = {'duration': source['duration'], 'origin': 'Importato', 'original_name': source['name'], 'imported_from': str(path)}
            if event_id: metadata['event_id'] = event_id
            value['imports'][identifier] = metadata
            save_policy(ws, value)
            if course:
                associate(ws, identifier, course=course, lesson=lesson)
                value = policy(ws)
            sync(ws)
            item = next(i for i in discover(ws) if i['id'] == identifier)
            hashes[sha] = item
            results.append({'recording_id': identifier, 'duplicate': False, 'name': source['name']})
        except BaseException:
            # Once published, retain the complete copy even if metadata/sync fails.
            # It remains discoverable as an unassigned audio on the next scan.
            raise
        finally:
            temporary.unlink(missing_ok=True)
    duplicates = sum(item['duplicate'] for item in results)
    notes = [{'message': f'{duplicates} file già presenti: sono stati riconosciuti senza creare copie o cambiare le associazioni.'}] if duplicates else []
    return {'status': 'OK', 'imported': results, 'message': f"{len(results)} audio riconosciuti nella raccolta.", 'notices': notes}


@locked
def associate(ws, identifier, *, event_id=None, course=None, lesson=None):
    """Associate independent sources without claiming exclusive lesson ownership."""
    from .recording_library import sync
    from .capture import CaptureStore
    sync(ws)
    items = _items(ws)
    item = next((i for i in items if i['id'] == identifier), None)
    if not item: raise ValueError('Audio non trovato nella raccolta')
    events = CaptureStore(ws).source_events()
    if event_id:
        event = next((e for e in events if e['id'] == event_id), None)
        if not event: raise ValueError('Lezione non trovata')
        course, lesson = event.get('courseCode'), event.get('lessonNumber')
    codes = {c['code'] for c in ws.configuration_store().load()['courses']}
    if course is not None and course not in codes: raise ValueError('Scegli un corso configurato')
    if lesson is not None and (not course or type(lesson) is not int or not 1 <= lesson <= 999): raise ValueError('Numero della lezione non valido')
    lecture_id = f'{course}-L{lesson:02d}' if course and lesson is not None else None
    if not item.get('storage_recording_id') and item.get('lecture_id') != lecture_id:
        raise ValueError('Questa registrazione storica conserva la destinazione originale. Importa una nuova sorgente per un’altra lezione.')
    related = {i['id'] for i in items if i['lesson_key'] == item['lesson_key'] or lecture_id and i.get('lecture_id') == lecture_id or event_id and i.get('event_id') == event_id}
    _idle(ws, related)
    value = policy(ws)
    value.setdefault('bindings', {})[identifier] = {'event_id': event_id, 'course_code': course, 'lecture_id': lecture_id}
    save_policy(ws, value)
    sync(ws)
    return {'status': 'OK', 'message': 'Associazione salvata. Scegli la sorgente da trascrivere se la lezione ha più audio.'}
