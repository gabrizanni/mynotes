"""Persistent lesson identities and local lifecycle; no provider calls.

Projection is read-only. Explicit commands reconcile legacy sources under the
library lock and save a versioned registry with a recoverable previous copy.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, UTC
from pathlib import Path
import json
import hashlib
import uuid
from zoneinfo import ZoneInfo

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


def lecture_key(row):
    if row.get('course') and row.get('number'):
        return f"{row['course']}-L{row['number']:02d}"
    return None


def same_recording_day(recorded_at, starts_at, timezone):
    try:
        zone = ZoneInfo(timezone or 'UTC')
        recorded = datetime.fromisoformat(recorded_at)
        starts = datetime.fromisoformat(starts_at)
        return recorded.tzinfo is not None and starts.tzinfo is not None and recorded.astimezone(zone).date() == starts.astimezone(zone).date()
    except (ValueError, TypeError, KeyError):
        return False


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
        row['title'] = row.get('title_override') or event['title']
        row['date'] = row.get('date_override') or event['startsAt']
        if event['id'] not in row['event_ids']: row['event_ids'].append(event['id'])
    # Link a unique legacy lecture alias to its calendar lesson; ambiguity stays separate.
    for lecture, matches in event_lectures.items():
        if len(matches) == 1:
            row = by_alias['event:' + matches[0]['id']]
            alias = 'lesson:' + lecture
            if alias not in by_alias:
                row['aliases'].append(alias); by_alias[alias] = row
    for item in audio:
        key = _legacy_key(item)
        row = by_audio.get(item['id']) or by_alias.get(key)
        lecture = item.get('lecture_id')
        if row and item['id'] not in by_audio and key.startswith('lesson:') and lecture_key(row) != key[7:]:
            row = None
            key = 'audio:' + item['id']
        if row is None and lecture and not key.startswith('audio:'):
            matches = [r for r in rows.values() if not r['hidden'] and lecture_key(r) == lecture]
            if len(matches) == 1: row = matches[0]
        if row is None:
            if key.startswith('audio:'):
                lecture = None
            number = int(lecture.rsplit('-L', 1)[1]) if lecture else None
            row = ensure(key, item.get('course_code'), number, item['title'], item.get('recorded_at') or item.get('created_at'))
        if item['id'] not in row['recording_ids']: row['recording_ids'].append(item['id'])
        by_audio[item['id']] = row
    for item in transcripts:
        row = by_audio.get(item.get('recording_id')) or by_text.get(item['id'])
        lecture = item.get('lecture_id')
        if row is None:
            matches = [r for r in rows.values() if lecture and not r['hidden'] and lecture_key(r) == lecture]
            row = matches[0] if len(matches) == 1 and item.get('association_source') == 'explicit' else None
        if row is None:
            row = ensure('text:' + item['id'], item.get('course_code'), None, item['title'], item.get('created_at'))
        if item['id'] not in row['transcript_ids']: row['transcript_ids'].append(item['id'])
    return value


def project_audio(ws, audio):
    # Never call snapshot here: snapshot uses the legacy audio projection.
    rows = load(ws)['lessons'].values()
    bindings = {key: row for row in rows for key in row['recording_ids']}
    aliases = {a: row for row in rows for a in row['aliases'] if a.startswith('lesson:')}
    for item in audio:
        row = bindings.get(item['id'])
        if not row:
            historical = aliases.get(item['lesson_key'])
            if historical and lecture_key(historical) != item['lesson_key'][7:]:
                item['association_conflict'] = 'Il numero nel nome appartiene a un collegamento storico. Scegli la lezione.'
                item['lecture_id'] = None
                item['lesson_key'] = 'audio:' + item['id']
            item['legacy_lesson_key'] = item['lesson_key']
            item['lesson_key'] = 'lesson-' + uuid.uuid5(uuid.NAMESPACE_URL, item['lesson_key']).hex
            continue
        item['legacy_lesson_key'] = item['lesson_key']
        item['lesson_key'] = row['id']
        item['course_code'] = row['course']
        item['lecture_id'] = lecture_key(row)
        item['event_id'] = next(iter(row['event_ids']), None)
        if row['hidden'] or row['not_recorded'] or row.get('cancelled') or row.get('pending_trash'):
            item['can_transcribe'] = False
            item['actions'] = [a for a in item.get('actions', []) if a not in {'enqueue', 'retry', 'restart', 'recover'}]
            item.setdefault('blocking_reasons', []).append({'code': 'LESSON_STATE', 'message': 'La lezione è rimossa, annullata o non registrata.'})
    return audio


def project_events(ws, events):
    rows = load(ws)['lessons'].values()
    bindings = {key: row for row in rows for key in row['event_ids']}
    retired = {key for row in rows for key in row.get('retired_event_ids', [])}
    for event in events:
        if event['id'] in retired:
            event.update(eligibility='CANCELLED', reason='Occorrenza riunita localmente', reasonCode='LOCAL_EXCLUSION')
            continue
        row = bindings.get(event['id'])
        if not row: continue
        event.update(courseCode=row['course'], lessonNumber=row['number'])
        for field, key in [('title', 'title_override'), ('startsAt', 'date_override'), ('endsAt', 'end_override'), ('timezone', 'timezone_override')]:
            if row.get(key): event[field] = row[key]
        if row['hidden'] or row.get('cancelled'):
            event.update(eligibility='CANCELLED', reason='Lezione rimossa localmente', reasonCode='LOCAL_EXCLUSION')
    return events


def listing(ws, audio=None, transcripts=None):
    value = snapshot(ws, audio, transcripts)
    return list(value['lessons'].values())


def _idle(ws, rows, *, allow_renames=False):
    if not allow_renames and any(row.get('pending_renames') for row in rows):
        raise ValueError('Completa la rinomina dei file prima di modificare la lezione')
    from .audio_sources import _idle as idle_audio
    from .capture import CaptureStore
    from .capture_dashboard import _assert_editable
    identifiers = {key for row in rows for key in row['recording_ids']}
    idle_audio(ws, identifiers)
    store = CaptureStore(ws)
    for row in rows:
        for event in row['event_ids']: _assert_editable(store, event)
    from . import file_lock
    for state_file in ws.runs_dir.glob('_batches/*/notes-run.json'):
        try: state = json.loads(state_file.read_text())
        except (OSError, ValueError): continue
        if any(str(lecture).split('-L')[0] in {r['course'] for r in rows} for lecture in state.get('lectures', [])):
            lease = state_file.parent / '.notes.lock'
            if lease.exists():
                with lease.open('r') as handle:
                    try: file_lock.flock(handle, file_lock.LOCK_SH | file_lock.LOCK_NB)
                    except BlockingIOError: raise ValueError('Generazione attiva: attendi la fine del lavoro')
    # Generation workers hold this same course's batch journal while running.
    # Inspect status files below instead of assuming UI idleness means no work.
    for row in rows:
        if row['course'] and row['number']:
            run = ws.runs_dir / f"{row['course']}-L{row['number']:02d}"
            lease = run / '.generation.lock'
            if lease.exists():
                with lease.open('r') as handle:
                    try: file_lock.flock(handle, file_lock.LOCK_SH | file_lock.LOCK_NB)
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
        with store.lock():
            config = store.config()
            store.save_schedule(store.source_events(), config)
            store.save_config(config)


def _revision(ws, old, row):
    before, after = lecture_key(old), lecture_key(row)
    if before == after:
        return
    row['sources_revision'] = uuid.uuid4().hex
    if before:
        alias = 'lesson:' + before
        if alias not in row['aliases']:
            row['aliases'].append(alias)
        row.setdefault('source_number', old['number'])
    if row.get('revision_required') and row.get('review_original') == row['number'] and old['course'] == row['course']:
        row['revision_required'] = False
        row.pop('review_original', None)
    elif row.get('revision_required') or row['transcript_ids'] or any((ws.runs_dir / key).exists() for key in (before, after) if key):
        row.setdefault('review_original', old['number'])
        row['revision_required'] = True


def _refresh_title(old, row):
    title = old['title']
    if old.get('title_override') or not row['number']:
        return
    if title.casefold() in {f"lezione {old['number']}", f"{old['course']} {old['number']}".casefold(), (lecture_key(old) or '').casefold()}:
        row.update(title=f"Lezione {row['number']}", title_override=f"Lezione {row['number']}")


def _token(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def rename_candidates(ws, row):
    from .recording_library import discover
    from .transcript_library import scan
    managed = set(inventory(ws, row))
    audio = [item for item in discover(ws) if item['id'] in row['recording_ids'] and item['path'] in managed]
    texts = [item for item in scan(ws) if item['id'] in row['transcript_ids'] and item['path'] in managed]
    result = []
    for kind, items in [('audio', audio), ('text', texts)]:
        for position, item in enumerate(items, start=1):
            source = Path(item['path'])
            target = source.with_name(f"{lecture_key(row)}-{position}{source.suffix}")
            if source != target:
                result.append({'kind': kind, 'id': item['id'], 'path': str(source), 'target': str(target)})
    return result


@locked
def resume_renames(ws, identifier):
    from .recording_library import records, save as save_audio, _move_original
    from .transcript_library import index, _references
    from .utils import file_sha256
    value = load(ws)
    row = value['lessons'][identifier]
    _idle(ws, [row], allow_renames=True)
    for move in list(row.get('pending_renames', [])):
        source, target = Path(move['path']), Path(move['target'])
        digest = move['sha256']
        if source.exists() and target.exists() and source.stat().st_ino != target.stat().st_ino:
            raise ValueError('Il nome destinazione è occupato. Libera il nome e riprendi la rinomina.')
        if not source.exists() and (not target.exists() or file_sha256(target) != digest):
            raise ValueError('File da rinominare non disponibile')
        if move['kind'] == 'audio':
            item = records(ws).get(move['id'])
            if item is None:
                raise ValueError('Registrazione non disponibile')
            item['pending_destination'] = str(target)
            save_audio(ws, item)
        if source.exists():
            _move_original(source, target, digest)
        if move['kind'] == 'audio':
            item.update(path=str(target), file_identity=[target.stat().st_dev, target.stat().st_ino])
            item.pop('pending_destination', None)
            save_audio(ws, item)
        else:
            known = index(ws)
            item = known[move['id']]
            item.update(path=str(target), title=target.stem, file_identity=[target.stat().st_dev, target.stat().st_ino])
            known[item['id']] = item
            atomic_json_write(path(ws).parent / 'transcripts.json', known)
            _references(ws, item)
        row['pending_renames'].remove(move)
        save(ws, value)
    return {'status': 'OK', 'message': 'Rinomina completata.'}


@locked
def update_lesson(ws, payload):
    value = snapshot(ws)
    rows = value['lessons']
    identifier = payload.get('id')
    creating = payload.get('action') == 'create'
    row = deepcopy(rows.get(identifier))
    if creating:
        identifier = payload.get('new_id') or 'lesson-' + uuid.uuid4().hex
        row = {'id': identifier, 'aliases': [], 'course': None, 'number': None,
               'title': '', 'date': None, 'recording_ids': [], 'transcript_ids': [],
               'event_ids': [], 'not_recorded': False, 'hidden': False,
               'revision_required': False, 'manual': True}
    if row is None or row['hidden']:
        raise ValueError('Lezione non disponibile')
    before = deepcopy(row)
    patch = payload.get('patch', {})
    if not isinstance(patch, dict) or set(patch) - {'course', 'number', 'title', 'date', 'end', 'timezone'}:
        raise ValueError('Campi lezione non validi')
    course = patch.get('course', row['course'])
    number = patch.get('number', row['number'])
    codes = {course['code'] for course in ws.configuration_store().load()['courses']}
    if course not in codes or type(number) is not int or not 1 <= number <= 999:
        raise ValueError('Scegli un corso e un numero da 1 a 999')
    row.update(course=course, number=number)
    _refresh_title(before, row)
    if 'title' in patch:
        title = patch['title'].strip()
        if not title or len(title) > 300 or any(ord(char) < 32 for char in title):
            raise ValueError('Inserisci un titolo da 1 a 300 caratteri')
        row.update(title=title, title_override=title)
    for field, override in [('date', 'date_override'), ('end', 'end_override'), ('timezone', 'timezone_override')]:
        if field in patch:
            row[override] = patch[field]
    if row.get('date_override'):
        row['date'] = row['date_override']
        starts = datetime.fromisoformat(row['date'])
        if starts.tzinfo is None:
            raise ValueError('La data deve includere il fuso orario')
    if row.get('end_override'):
        end = datetime.fromisoformat(row['end_override'])
        if end.tzinfo is None or not row['date'] or end <= datetime.fromisoformat(row['date']):
            raise ValueError('La fine deve essere successiva all’inizio')
    if row.get('timezone_override'):
        ZoneInfo(row['timezone_override'])
    conflicts = [other for other in rows.values() if other['id'] != identifier and not other['hidden']
                 and other['course'] == course and other['number'] == number]
    mode = payload.get('conflict', 'stop')
    affected = [before]
    if conflicts:
        other = conflicts[0]
        if len(conflicts) != 1 or mode not in {'swap', 'merge'}:
            return {'status': 'CONFLICT', 'conflicts': conflicts, 'message': 'Numero occupato: confronta le lezioni.'}
        _idle(ws, [other])
        affected.append(deepcopy(other))
        if mode == 'swap':
            if before['course'] != course or before['number'] is None:
                raise ValueError('Lo scambio richiede due lezioni numerate dello stesso corso')
            other['number'] = before['number']
            _revision(ws, affected[-1], other)
            _refresh_title(affected[-1], other)
        else:
            if row['event_ids'] and other['event_ids']:
                row.setdefault('retired_event_ids', []).extend(other['event_ids'])
            else:
                row['event_ids'] = list(dict.fromkeys(row['event_ids'] + other['event_ids']))
            for field in ('recording_ids', 'transcript_ids'):
                row[field] = list(dict.fromkeys(row[field] + other[field]))
                other[field] = []
            other.update(hidden=True, merged_into=identifier)
    _idle(ws, [before, row])
    token = _token([snapshot(ws), payload.get('patch'), mode])
    if payload.get('preview'):
        return {'status': 'OK', 'token': token, 'new_id': identifier, 'before': before, 'after': row, 'affected': affected,
                'renames': rename_candidates(ws, row)}
    if payload.get('token') != token:
        raise ValueError('I collegamenti sono cambiati. Aggiorna l’anteprima.')
    _revision(ws, before, row)
    selected = payload.get('rename_paths', [])
    candidates = rename_candidates(ws, row)
    if not isinstance(selected, list) or set(selected) - {move['path'] for move in candidates}:
        raise ValueError('Aggiorna la scelta dei file da rinominare')
    from .utils import file_sha256
    row['pending_renames'] = [{**move, 'sha256': file_sha256(Path(move['path']))} for move in candidates if move['path'] in selected]
    if any(Path(move['target']).exists() for move in row['pending_renames']):
        raise ValueError('Un nome destinazione è già occupato. Modifica la scelta dei file.')
    if row['pending_renames']:
        from .recording_library import sync
        sync(ws)
    rows[identifier] = row
    save(ws, value)
    _publish_calendar(ws)
    if row['pending_renames']:
        resume_renames(ws, identifier)
    return {'status': 'OK', 'id': identifier, 'message': 'Lezione aggiornata.'}


def selected_transcript(ws, row):
    from .transcript_library import scan
    texts = [text for text in scan(ws) if text['id'] in row['transcript_ids']]
    preferred = row.get('preferred_transcript')
    if preferred:
        return next((text for text in texts if text['id'] == preferred), None)
    return texts[0] if len(texts) == 1 else None


def source_revision(ws, lecture):
    rows = list(load(ws)['lessons'].values())
    matches = [row for row in rows if not row['hidden'] and lecture_key(row) == lecture]
    if len(matches) == 1:
        return matches[0].get('sources_revision')
    historical = [row for row in rows if 'lesson:' + lecture in row['aliases'] and row.get('sources_revision')]
    return _token([row['sources_revision'] for row in historical]) if historical else None


@locked
def renumber(ws, changes, *, preview=False, expected=None):
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
    token = _token([originals, result])
    if not preview:
        if expected is not None and expected != token:
            raise ValueError('I collegamenti sono cambiati. Aggiorna l’anteprima.')
        for old in originals:
            row = rows[old['id']]
            _revision(ws, old, row)
            _refresh_title(old, row)
        save(ws, value); _publish_calendar(ws)
    return {'status': 'OK', 'changes': result, 'token': token, 'message': 'Numerazione verificata.' if preview else 'Numerazione aggiornata.'}


@locked
def move_audio(ws, recording, target, *, preview=False, expected=None):
    value = snapshot(ws); rows = value['lessons']
    if target is not None and (target not in rows or rows[target]['hidden'] or rows[target].get('cancelled')):
        raise ValueError('Lezione di destinazione non disponibile')
    sources = [row for row in rows.values() if recording in row['recording_ids']]
    if not sources: raise ValueError('Audio non trovato')
    from .transcript_library import scan
    texts = [t for t in scan(ws) if t.get('recording_id') == recording]
    destination = rows.get(target)
    _idle(ws, [*sources, *([destination] if destination else [])])
    token = _token([sources, destination, texts])
    if preview:
        return {'status': 'OK', 'token': token, 'transcripts': [t['title'] for t in texts]}
    if expected is not None and expected != token:
        raise ValueError('I collegamenti sono cambiati. Aggiorna l’anteprima.')
    if destination in sources:
        return {'status': 'OK', 'message': 'L’audio è già collegato a questa lezione.'}
    if destination is None:
        key = 'audio:' + recording
        identifier = 'lesson-' + uuid.uuid5(uuid.NAMESPACE_URL, key).hex
        destination = rows.get(identifier) or {
            'id': identifier, 'aliases': [key], 'title': sources[0]['title'],
            'date': sources[0]['date'], 'recording_ids': [], 'transcript_ids': [], 'event_ids': [],
            'not_recorded': False, 'hidden': False, 'revision_required': False, 'course': None, 'number': None,
        }
        destination.update(hidden=False, course=None, number=None, detached=True)
        rows[identifier] = destination
        if destination in sources:
            save(ws, value)
            return {'status': 'OK', 'message': 'Audio scollegato. Lo trovi tra i file da associare.'}
    moved_texts = {t['id'] for t in texts}
    for row in sources:
        row['recording_ids'].remove(recording)
        row['transcript_ids'] = [key for key in row['transcript_ids'] if key not in moved_texts]
        row['sources_revision'] = uuid.uuid4().hex
        if row.get('preferred_transcript') in moved_texts:
            row.pop('preferred_transcript')
            row['sources_revision'] = uuid.uuid4().hex
        if not row['recording_ids'] and not row['transcript_ids'] and not row['event_ids'] and not row.get('manual'):
            row['hidden'] = True
    destination['recording_ids'].append(recording)
    destination['not_recorded'] = False
    destination['sources_revision'] = uuid.uuid4().hex
    destination['transcript_ids'] = list(dict.fromkeys([*destination['transcript_ids'], *sorted(moved_texts)]))
    save(ws, value)
    from .audio_sources import policy, save_policy
    choices = policy(ws)
    for row in [*sources, destination]:
        key = row['id']
        if key == destination['id'] or choices['preferred'].get(key) == recording:
            choices['preferred'].pop(key, None)
        required = choices.setdefault('require_choice', [])
        if key == destination['id'] and len(row['recording_ids']) > 1 and key not in required:
            required.append(key)
        if len(row['recording_ids']) <= 1 and key in required:
            required.remove(key)
    save_policy(ws, choices)
    return {'status': 'OK', 'message': 'Registrazione aggiornata.'}


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
        if not any(other['id'] != row['id'] and ('lesson:' + lecture in other['aliases'] or lecture_key(other) == lecture) for other in rows):
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
    if action in {'prefer-transcript', 'review-sources'}:
        transcript = payload.get('transcript')
        recording_review = action == 'review-sources' and payload.get('recording') in row['recording_ids']
        if transcript not in row['transcript_ids'] and not recording_review:
            raise ValueError('Scegli una trascrizione di questa lezione')
        from .transcript_library import scan
        if not recording_review and not any(text['id'] == transcript for text in scan(ws)):
            raise ValueError('Trascrizione non disponibile')
        if transcript:
            row['preferred_transcript'] = transcript
        row['sources_revision'] = uuid.uuid4().hex
        if action == 'review-sources':
            row['revision_required'] = False
            row.pop('review_original', None)
    elif action == 'move-transcript':
        transcript = payload.get('transcript')
        target = rows.get(payload.get('target'))
        from .transcript_library import scan
        text = next((text for text in scan(ws) if text['id'] == transcript), None)
        if text is None or text.get('recording_id') or target is None or target['hidden'] or transcript not in row['transcript_ids']:
            raise ValueError('Scegli un TXT indipendente e una lezione disponibile')
        _idle(ws, [target])
        row['transcript_ids'].remove(transcript)
        target['transcript_ids'] = list(dict.fromkeys(target['transcript_ids'] + [transcript]))
        if row.get('preferred_transcript') == transcript:
            row.pop('preferred_transcript')
        row['sources_revision'] = uuid.uuid4().hex
        target['sources_revision'] = uuid.uuid4().hex
    elif action in {'cancelled', 'uncancelled'}:
        row['cancelled'] = action == 'cancelled'
    elif action == 'not-recorded':
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
    from .memory import snapshot as preference_snapshot

    settings = ws.configuration_store().load()
    course = next((c for c in settings['courses'] if c['code'] == code), None)
    if not course: raise ValueError('Corso non trovato')
    return {'status': 'OK', 'course': course,
            'notes': preference_snapshot(settings, scope='course', area='notes', course=code)}


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
        if item.get('recorded_at'):
            if datetime.fromisoformat(item['recorded_at']).tzinfo is None:
                raise ValueError('Conferma la data con il suo fuso orario')
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
            if target and (not imported['duplicate'] or item.get('move_duplicate') is True):
                current = snapshot(ws)
                row = next(r for r in current['lessons'].values() if imported['recording_id'] in r['recording_ids'])
                if row['id'] != target['id']: move_audio(ws, imported['recording_id'], target['id'])
            if item.get('recorded_at') and not imported['duplicate']:
                from .audio_sources import policy, save_policy
                choices = policy(ws)
                choices['imports'][imported['recording_id']]['recorded_at'] = item['recorded_at']
                save_policy(ws, choices)
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
