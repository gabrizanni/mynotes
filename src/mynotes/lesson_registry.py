"""Persistent lesson identities and local lifecycle; no provider calls.

Projection is read-only. Explicit commands reconcile legacy sources under the
library lock and save a versioned registry with a recoverable previous copy.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, UTC
from pathlib import Path
import json
import uuid

from .recording_library import locked
from .utils import atomic_json_write


def path(ws):
    return ws.state_dir / 'recording-library/lessons.json'


def load(ws):
    try:
        value = json.loads(path(ws).read_text())
    except FileNotFoundError:
        return {'schemaVersion': 1, 'lessons': {}}
    if value.get('schemaVersion') != 1:
        raise ValueError('Versione del registro lezioni non supportata')
    return value


def save(ws, value):
    previous = load(ws)
    if previous == value:
        return
    if path(ws).exists():
        backup = path(ws).parent / 'lesson-history' / (uuid.uuid4().hex + '.json')
        atomic_json_write(backup, previous)
    atomic_json_write(path(ws), value)


def _legacy_key(item):
    return item.get('legacy_lesson_key') or item.get('lesson_key') or ('event:' + item['event_id'] if item.get('event_id') else
        'lesson:' + item['lecture_id'] if item.get('lecture_id') else 'audio:' + item['id'])


def snapshot(ws, audio=None, transcripts=None):
    from .capture import CaptureStore
    from .audio_sources import project
    from .recording_library import discover
    from .transcript_library import scan
    store = CaptureStore(ws)
    from .capture_associations import project_events as association_events
    events = association_events(store, store.source_events())
    # The effective schedule includes local date/title/timezone overrides.
    # Keep source identity and association provenance, but display the same
    # saved occurrence as the editor and native recorder.
    effective = {event['id']: event for event in store.schedule()['events']}
    for event in events:
        saved = effective.get(event['id'], {})
        event.update({key: saved[key] for key in ('startsAt', 'endsAt', 'timezone', 'title') if key in saved})
    if audio is None:
        audio = project(ws, discover(ws))
    if transcripts is None:
        transcripts = scan(ws)
    value = deepcopy(load(ws))
    rows = value['lessons']
    by_alias = {alias: row for row in rows.values() for alias in row['aliases']}
    by_audio = {key: row for row in rows.values() for key in row['recording_ids']}
    by_text = {key: row for row in rows.values() for key in row['transcript_ids']}
    def ensure(key, course=None, number=None, title='', date=None):
        row = by_alias.get(key)
        if row is None:
            identifier = 'lesson-' + uuid.uuid5(uuid.NAMESPACE_URL, key).hex
            row = {'id': identifier, 'aliases': [key], 'course': course, 'number': number,
                   'title': title, 'date': date, 'recording_ids': [], 'transcript_ids': [],
                   'event_ids': [], 'not_recorded': False, 'hidden': False,
                   'revision_required': False}
            rows[identifier] = row; by_alias[key] = row
        return row
    event_lectures = {}
    for event in events:
        code, number = event.get('courseCode'), event.get('lessonNumber')
        if code and number:
            event_lectures.setdefault(f'{code}-L{number:02d}', []).append(event)
        row = ensure('event:' + event['id'], code, number, event['title'], event['startsAt'])
        row['title'], row['date'] = event['title'], event['startsAt']
        if event['id'] not in row['event_ids']: row['event_ids'].append(event['id'])
    # Link a unique legacy lecture alias to its calendar lesson; ambiguity stays separate.
    for lecture, matches in event_lectures.items():
        if len(matches) == 1:
            row = by_alias['event:' + matches[0]['id']]
            alias = 'lesson:' + lecture
            if alias not in by_alias:
                row['aliases'].append(alias); by_alias[alias] = row
    for item in audio:
        row = by_audio.get(item['id']) or by_alias.get(_legacy_key(item))
        if row is None and item.get('course_code') and item.get('lecture_id'):
            number = int(item['lecture_id'].rsplit('-L', 1)[1])
            matches = [r for r in rows.values() if not r['hidden'] and r['course'] == item['course_code'] and r['number'] == number]
            if len(matches) == 1: row = matches[0]
        if row is None:
            lecture = item.get('lecture_id')
            number = int(lecture.rsplit('-L', 1)[1]) if lecture else None
            row = ensure(_legacy_key(item), item.get('course_code'), number, item['title'], item.get('created_at'))
        if item['id'] not in row['recording_ids']: row['recording_ids'].append(item['id'])
        by_audio[item['id']] = row
    for item in transcripts:
        row = by_text.get(item['id']) or by_audio.get(item.get('recording_id'))
        lecture = item.get('lecture_id')
        if row is None:
            row = ensure('lesson:' + lecture if lecture else 'text:' + item['id'], item.get('course_code'),
                         int(lecture.rsplit('-L', 1)[1]) if lecture else None, item['title'], item.get('created_at'))
        if item['id'] not in row['transcript_ids']: row['transcript_ids'].append(item['id'])
    return value


def project_audio(ws, audio):
    # Never call snapshot here: snapshot uses the legacy audio projection.
    rows = load(ws)['lessons'].values()
    bindings = {key: row for row in rows for key in row['recording_ids']}
    for item in audio:
        row = bindings.get(item['id'])
        if not row:
            item['legacy_lesson_key'] = item['lesson_key']
            item['lesson_key'] = 'lesson-' + uuid.uuid5(uuid.NAMESPACE_URL, item['lesson_key']).hex
            continue
        item['legacy_lesson_key'] = item['lesson_key']
        item['lesson_key'] = row['id']
        item['course_code'] = row['course']
        item['lecture_id'] = f"{row['course']}-L{row['number']:02d}" if row['course'] and row['number'] else None
        if row['hidden'] or row['not_recorded'] or row['revision_required'] or row.get('pending_trash'):
            item['can_transcribe'] = False
            item.setdefault('blocking_reasons', []).append({'code': 'LESSON_STATE', 'message': 'Lezione rimossa, non registrata o rinumerata: verifica il corso prima di trascrivere.'})
    return audio


def project_events(ws, events):
    rows = load(ws)['lessons'].values()
    bindings = {key: row for row in rows for key in row['event_ids']}
    for event in events:
        row = bindings.get(event['id'])
        if not row: continue
        event.update(courseCode=row['course'], lessonNumber=row['number'])
        if row['hidden']:
            event.update(eligibility='CANCELLED', reason='Lezione rimossa localmente', reasonCode='LOCAL_EXCLUSION')
    return events


def listing(ws, audio=None, transcripts=None):
    value = snapshot(ws, audio, transcripts)
    return list(value['lessons'].values())


def _idle(ws, rows):
    from .audio_sources import _idle as idle_audio
    from .capture import CaptureStore
    from .capture_dashboard import _assert_editable
    identifiers = {key for row in rows for key in row['recording_ids']}
    idle_audio(ws, identifiers)
    store = CaptureStore(ws)
    for row in rows:
        for event in row['event_ids']: _assert_editable(store, event)
    import fcntl
    for state_file in ws.runs_dir.glob('_batches/*/notes-run.json'):
        try: state = json.loads(state_file.read_text())
        except (OSError, ValueError): continue
        if any(str(lecture).split('-L')[0] in {r['course'] for r in rows} for lecture in state.get('lectures', [])):
            lease = state_file.parent / '.notes.lock'
            if lease.exists():
                with lease.open('r') as handle:
                    try: fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    except BlockingIOError: raise ValueError('Generazione attiva: attendi la fine del lavoro')
    # Generation workers hold this same course's batch journal while running.
    # Inspect status files below instead of assuming UI idleness means no work.
    for row in rows:
        if row['course'] and row['number']:
            run = ws.runs_dir / f"{row['course']}-L{row['number']:02d}"
            lease = run / '.generation.lock'
            if lease.exists():
                with lease.open('r') as handle:
                    try: fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    except BlockingIOError: raise ValueError('Generazione o pubblicazione attiva: attendi la fine del lavoro')
            for status_file in run.glob('**/status.json') if run.exists() else []:
                try: state = json.loads(status_file.read_text())
                except (OSError, ValueError): continue
                from .transcription_queue import _alive
                if state.get('status') in {'RUNNING', 'CANCELLING'} and _alive(state.get('pid')):
                    raise ValueError('Interrompi il lavoro attivo prima di modificare la lezione')


def _publish_calendar(ws):
    from .capture import CaptureStore
    store = CaptureStore(ws)
    if store.schedule_path.exists():
        store.save_schedule(store.source_events(), store.config())


@locked
def renumber(ws, changes, *, preview=False):
    if not isinstance(changes, list) or not changes or len(changes) > 999:
        raise ValueError('Indica le lezioni da rinumerare')
    value = snapshot(ws); rows = value['lessons']; seen = set(); result = []; originals = []
    for change in changes:
        identifier, number = change.get('id'), change.get('number')
        if identifier not in rows or identifier in seen or type(number) is not int or not 1 <= number <= 999:
            raise ValueError('Lezione o numero non valido (1–999)')
        row = rows[identifier]
        if 'before' in change and change['before'] != row['number']: raise ValueError('La numerazione è cambiata: aggiorna l’anteprima')
        if row['hidden'] or not row['course']: raise ValueError('Ripristina e associa la lezione prima di rinumerarla')
        seen.add(identifier)
        originals.append(deepcopy(row))
        result.append({'id': identifier, 'before': row['number'], 'after': number})
        row['number'] = number
    claims = {}
    for row in rows.values():
        if row['hidden'] or row['number'] is None or not row['course']: continue
        key = (row['course'], row['number'])
        if key in claims and (row['id'] in seen or claims[key] in seen): raise ValueError(f"Lezione {row['number']} già occupata nel corso {row['course']}. Sposta gli audio nella lezione esistente oppure includila nella rinumerazione.")
        claims.setdefault(key, row['id'])
    _idle(ws, [*originals, *[rows[key] for key in seen]])
    if not preview:
        for change in result:
            if change['before'] != change['after']:
                row = rows[change['id']]
                # Old artifacts are immutable. Prevent accidental reuse/publication
                # under a different number until the course is explicitly reviewed.
                row.setdefault('source_number', change['before'])
                old = ws.runs_dir / f"{row['course']}-L{change['before']:02d}" if change['before'] else None
                destination = ws.runs_dir / f"{row['course']}-L{change['after']:02d}"
                if row['revision_required'] and row.get('review_original') == change['after']:
                    row['revision_required'] = False
                    row.pop('review_original', None)
                elif (old and old.exists()) or destination.exists() or row['revision_required']:
                    if not row['revision_required']: row['review_original'] = change['before']
                    row['revision_required'] = True
        save(ws, value); _publish_calendar(ws)
    return {'status': 'OK', 'changes': result, 'message': 'Numerazione verificata.' if preview else 'Numerazione aggiornata. I documenti pubblicati restano invariati.'}


@locked
def move_audio(ws, recording, target):
    value = snapshot(ws); rows = value['lessons']
    if target not in rows or rows[target]['hidden']: raise ValueError('Lezione di destinazione non disponibile')
    sources = [row for row in rows.values() if recording in row['recording_ids']]
    if not sources: raise ValueError('Audio non trovato')
    destination = rows[target]; _idle(ws, [*sources, destination])
    for row in sources:
        row['recording_ids'].remove(recording)
        if not row['recording_ids'] and not row['transcript_ids'] and not row['event_ids']: row['hidden'] = True
    destination['recording_ids'].append(recording); destination['not_recorded'] = False
    # Transcripts follow their recording; standalone transcripts retain their binding.
    from .transcript_library import scan
    for transcript in scan(ws):
        if transcript.get('recording_id') == recording:
            for row in rows.values():
                if transcript['id'] in row['transcript_ids']: row['transcript_ids'].remove(transcript['id'])
            destination['transcript_ids'].append(transcript['id'])
    save(ws, value)
    from .audio_sources import policy, save_policy
    choices = policy(ws); choices['preferred'].pop(target, None)
    choices.setdefault('require_choice', []).append(target); save_policy(ws, choices)
    return {'status': 'OK', 'message': 'Audio spostato. Scegli la sorgente da usare.'}


def inventory(ws, row):
    from .recording_library import records, allowed, folder, discover
    from .transcript_library import scan
    from .audio_sources import policy as source_policy
    imports = source_policy(ws)['imports']
    known = records(ws); known.update({i['id']: i for i in discover(ws)}); candidates = []
    for key in row['recording_ids']:
        item = known.get(key, {})
        for field in ('path', 'export_text'):
            # Files copied by import and generated transcripts are managed;
            # audio discovered in a user folder remains the user's original.
            if field == 'path' and key not in imports and not item.get('capture_status'):
                continue
            if item.get(field): candidates.append(Path(item[field]))
    candidates += [Path(t['path']) for t in scan(ws) if t['id'] in row['transcript_ids']]
    # Only catalogued managed files; never originals, Word notebooks or arbitrary folders.
    base = folder(ws).resolve(); files = []
    for candidate in dict.fromkeys(candidates):
        if candidate.is_symlink() or not candidate.is_file(): continue
        resolved = candidate.resolve()
        if allowed(ws, candidate) or resolved.is_relative_to(base / 'Trascrizioni'):
            shared = any(candidate == Path(item.get(field, '/nonexistent')) for key, item in known.items()
                         if key not in row['recording_ids'] for field in ('path', 'export_text'))
            if not shared: files.append(str(candidate))
    rows = load(ws)['lessons'].values()
    owned_runs = [ws.runs_dir / '_recordings' / key for key in row['recording_ids']]
    aliases = [a.removeprefix('lesson:') for a in row['aliases'] if a.startswith('lesson:')]
    for lecture in aliases:
        if not any(other['id'] != row['id'] and 'lesson:' + lecture in other['aliases'] for other in rows):
            owned_runs.append(ws.runs_dir / lecture)
    for run in owned_runs:
        if not run.exists() or run.is_symlink(): continue
        for item in run.rglob('*'):
            if item.is_file() and not item.is_symlink() and not any(parent.is_symlink() for parent in item.parents) and item.resolve().is_relative_to(ws.runs_dir.resolve()):
                files.append(str(item))
    return sorted(set(files))


@locked
def action(ws, payload):
    value = snapshot(ws); rows = value['lessons']; identifier = payload.get('id'); action = payload.get('action')
    if identifier not in rows: raise ValueError('Lezione non trovata')
    row = rows[identifier]
    if action == 'inventory': return {'status': 'OK', 'files': row.get('pending_trash') or inventory(ws, row)}
    if action == 'cancel-file-trash':
        requested = payload.get('path')
        if (not isinstance(requested, str) or row.get('pending_trash_mode') != 'file'
                or row.get('pending_trash') != [requested] or not Path(requested).is_file()):
            raise ValueError('Verifica lo stato del file prima di annullare lo spostamento nel Cestino')
        row.pop('pending_trash')
        row.pop('pending_trash_mode')
        save(ws, value)
        return {'status': 'OK', 'message': 'Spostamento annullato. Il file è ancora nella raccolta.'}
    _idle(ws, [row])
    if action == 'not-recorded':
        from .recording_library import records
        from .recording_library import discover
        known = records(ws); known.update({i['id']: i for i in discover(ws)})
        if any(Path(known.get(key, {}).get('path', '/nonexistent')).is_file() for key in row['recording_ids']):
            raise ValueError('La lezione ha un audio. Rimuovilo prima di segnarla non registrata.')
        if not row.get('date') or datetime.fromisoformat(row['date'].replace('Z', '+00:00')) > datetime.now(UTC):
            raise ValueError('Puoi segnare non registrata soltanto una lezione passata')
        row['not_recorded'] = True
    elif action == 'recorded': row['not_recorded'] = False
    elif action == 'delete-file':
        requested = payload.get('path')
        if row.get('pending_trash'): raise ValueError('Completa prima l’eliminazione in corso')
        if requested not in inventory(ws, row): raise ValueError('File non disponibile o condiviso: aggiorna la lezione')
        row['pending_trash'] = [requested]
        row['pending_trash_mode'] = 'file'
    elif action == 'hide': row['hidden'] = True
    elif action == 'restore':
        if row.get('pending_trash'): raise ValueError('Completa l’eliminazione dei file prima di ripristinare la voce')
        if row['course'] and row['number'] and any(not other['hidden'] and other['id'] != row['id'] and (other['course'], other['number']) == (row['course'], row['number']) for other in rows.values()):
            raise ValueError('Il numero della lezione è occupato; libera il numero prima di ripristinare')
        row['hidden'] = False
    elif action == 'delete':
        files = row.get('pending_trash') or inventory(ws, row)
        expected = payload.get('files')
        if expected != files: raise ValueError('I file sono cambiati. Riapri il riepilogo prima di eliminare.')
        if row.get('pending_trash_mode') != 'file':
            row['hidden'] = True
            row['pending_trash_mode'] = 'lesson'
        row['pending_trash'] = files
    else: raise ValueError('Azione lezione non valida')
    from .recording_library import sync
    sync(ws)
    save(ws, value); _publish_calendar(ws)
    return {'status': 'OK', 'files': row.get('pending_trash', []), 'message': 'Stato della lezione aggiornato.'}


@locked
def trash_completed(ws, identifier, completed):
    value = load(ws); row = value['lessons'][identifier]
    row['pending_trash'] = [p for p in row.get('pending_trash', []) if p not in completed]
    if not row['pending_trash']: row.pop('pending_trash_mode', None)
    save(ws, value)
    return {'status': 'OK', 'remaining': row['pending_trash']}


def profile(ws, code):
    course = next((c for c in ws.configuration_store().load()['courses'] if c['code'] == code), None)
    if not course: raise ValueError('Corso non trovato')
    return {'status': 'OK', 'course': course}


@locked
def import_items(ws, items):
    from .audio_sources import import_audio, inspect_audio
    from .capture import CaptureStore
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError('Scegli da 1 a 100 file audio')
    codes = {c['code'] for c in ws.configuration_store().load()['courses']}
    events = {e['id'] for e in CaptureStore(ws).source_events()}
    value = snapshot(ws)
    for item in items:
        inspect_audio(ws, item['path'])
        if item.get('target'):
            target = value['lessons'].get(item['target'])
            if not target or target['hidden']: raise ValueError('Lezione di destinazione non disponibile')
            _idle(ws, [target])
        if item.get('event_id') and item['event_id'] not in events: raise ValueError('Lezione non trovata')
        if item.get('course') and item['course'] not in codes: raise ValueError('Corso non trovato')
        number = item.get('lesson')
        if number is not None and (not item.get('course') or type(number) is not int or not 1 <= number <= 999):
            raise ValueError('Numero della lezione non valido')
    results = []
    for item in items:
        target = value['lessons'].get(item.get('target'))
        event_id = item.get('event_id')
        if target: event_id = target['event_ids'][0] if target['event_ids'] else None
        result = import_audio(ws, [item['path']], event_id=event_id,
                              course=target['course'] if target else item.get('course'),
                              lesson=target['number'] if target else item.get('lesson'))
        for imported in result['imported']:
            if target and not imported['duplicate']:
                current = snapshot(ws)
                row = next(r for r in current['lessons'].values() if imported['recording_id'] in r['recording_ids'])
                if row['id'] != target['id']: move_audio(ws, imported['recording_id'], target['id'])
            results.append(imported)
    return {'status': 'OK', 'imported': results, 'message': f'{len(results)} audio riconosciuti; i duplicati non sono stati copiati.'}


def assert_generation_allowed(ws, code, number):
    for row in load(ws)['lessons'].values():
        active = row['course'] == code and row['number'] == number
        historical = row['revision_required'] and f'lesson:{code}-L{number:02d}' in row['aliases']
        if active or historical:
            if row['hidden'] or row['revision_required']:
                raise ValueError('Lezione rimossa o rinumerata con risultati esistenti: verifica i collegamenti del corso prima di generare.')


def inspect_imports(ws, paths):
    from .audio_sources import inspect_audio
    from .recording_library import discover
    from .utils import file_sha256
    files = [inspect_audio(ws, p) for p in paths]
    existing = discover(ws); by_hash = {}; names = set()
    sizes = {f['size'] for f in files}
    for item in existing:
        p = Path(item['path']); names.add(p.name)
        names.add(item.get('original_name', p.name))
        if item['audio_available'] and p.stat().st_size in sizes:
            by_hash[file_sha256(p)] = item['id']
    for item in files:
        digest = file_sha256(Path(item['path']))
        item['duplicate'] = digest in by_hash
        item['name_collision'] = item['name'] in names
        by_hash[digest] = 'incoming'; names.add(item['name'])
    return files


def rebind_audio(ws, identifier, course, number, event_id):
    """Keep the source's durable membership consistent with the legacy command."""
    if not path(ws).exists(): return
    value = snapshot(ws); rows = value['lessons']
    current = next((r for r in rows.values() if identifier in r['recording_ids']), None)
    if not current: return
    target = next((r for r in rows.values() if not r['hidden'] and
                   (event_id and event_id in r['event_ids'] or course and number and r['course'] == course and r['number'] == number)), None)
    if target and target['id'] == current['id']: return
    if not target and not current['event_ids'] and current['recording_ids'] == [identifier]:
        current.update(course=course, number=number)
        save(ws, value); return
    if not target:
        key = 'event:' + event_id if event_id else f'lesson:{course}-L{number:02d}' if course and number else 'unassigned:' + identifier
        target = {'id': 'lesson-' + uuid.uuid5(uuid.NAMESPACE_URL, key).hex, 'aliases': [key],
                  'course': course, 'number': number, 'title': current['title'], 'date': current['date'],
                  'recording_ids': [], 'transcript_ids': [], 'event_ids': [event_id] if event_id else [],
                  'not_recorded': False, 'hidden': False, 'revision_required': False}
        rows[target['id']] = target
    save(ws, value)
    move_audio(ws, identifier, target['id'])


def source_number(ws, code, number):
    """Keep original material labels usable after an explicit renumber."""
    row = next((r for r in load(ws)['lessons'].values() if not r['hidden'] and r['course'] == code and r['number'] == number), {})
    return row.get('source_number') or number
