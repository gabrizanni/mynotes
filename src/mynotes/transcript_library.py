"""User TXT files are the source of truth, independently of original audio."""
from __future__ import annotations

from datetime import datetime, UTC
import hashlib
import json
import os
from pathlib import Path

from .utils import atomic_json_write
from .recording_library import directory, folder, locked, records, save


def index(ws):
    try:
        return json.loads((directory(ws) / 'transcripts.json').read_text())
    except FileNotFoundError:
        return {}


def scan(ws, *, persist=False):
    base = folder(ws)
    # Do not prune private metadata when a volume/cloud directory is unavailable.
    if not base.exists():
        from .recording_library import options
        if not options(ws).get('folder'): return []
    list(base.iterdir())
    home = base / 'Trascrizioni'
    known = index(ws)
    paths = {v['path']: v for v in known.values()}
    inodes = {tuple(v['file_identity']): v for v in known.values() if v.get('file_identity')}
    from .capture import CaptureStore
    from .capture_associations import registry
    links = registry(CaptureStore(ws))['recordings']
    audio = {r.get('export_text'): r for r in records(ws).values() if r.get('export_text')}
    courses = {c['code'] for c in ws.configuration_store().load()['courses']}
    found = {}
    if home.exists():
        def walk(parent):
            for p in parent.iterdir():
                if p.is_symlink() or p.name.startswith('.'):
                    continue
                if p.is_dir():
                    yield from walk(p)
                elif p.is_file() and p.suffix.casefold() == '.txt':
                    yield p
        for p in sorted(walk(home)):
            stat = p.stat()
            old = paths.get(str(p)) or {}
            moved = inodes.get((stat.st_dev, stat.st_ino))
            if not old and moved and not Path(moved['path']).exists():
                old = moved
            recording = audio.get(str(p), {})
            identifier = old.get('id') or hashlib.sha256(str(p).encode()).hexdigest()[:32]
            first = p.relative_to(home).parts[0]
            code = first if first in courses else None
            item = {**old, 'id': identifier, 'path': str(p), 'title': p.stem,
                    'course_code': code, 'status': 'COMPLETED',
                    'file_identity': [stat.st_dev, stat.st_ino],
                    'created_at': old.get('created_at') or datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                    'recording_id': old.get('recording_id') or recording.get('id'),
                    'transcript_json': old.get('transcript_json') or recording.get('transcript_json'),
                    'lecture_id': old.get('lecture_id') or recording.get('lecture_id'),
                    'association_state': links.get(old.get('recording_id') or recording.get('id'), {}).get('associationState', old.get('association_state', recording.get('association_state', 'unresolved')))}
            if code is None:
                item.update(lecture_id=None, association_state='unresolved')
            elif old.get('course_code') and old['course_code'] != code:
                item.update(lecture_id=None, association_state='resolved')
            found[identifier] = item
    if persist:
        atomic_json_write(directory(ws) / 'transcripts.json', found)
        for item in found.values():
            _references(ws, item)
    return list(found.values())


def _references(ws, item):
    """Keep native/agent links current after Finder moves or renames."""
    from .transcription_queue import database, root
    identifier = item.get('recording_id')
    if not identifier or not (root(ws) / 'queue.sqlite3').exists():
        return
    recording = records(ws).get(identifier)
    if recording and recording.get('export_text') != item['path'] and Path(recording.get('export_text') or '/nonexistent').is_file():
        return
    if recording:
        recording.update(export_text=item['path'], transcript_name=Path(item['path']).name,
                         transcript_id=item['id'])
        save(ws, recording)
    with database(ws) as conn:
        for row in conn.execute('SELECT id,result FROM jobs WHERE recording_id=? AND result IS NOT NULL', (identifier,)).fetchall():
            result = json.loads(row['result'])
            if result.get('export_text') != item['path']:
                result['export_text'] = item['path']
                conn.execute('UPDATE jobs SET result=? WHERE id=?', (json.dumps(result), row['id']))


def unique(path):
    candidate, number = Path(path), 2
    while candidate.exists():
        candidate = path.with_name(f'{path.stem} ({number}){path.suffix}')
        number += 1
    return candidate


def _move(source, target):
    if source == target:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive publication; never overwrite an edited file or a same-name TXT.
    while True:
        target = unique(target)
        try:
            os.link(source, target)
            break
        except FileExistsError:
            continue
    source.unlink()
    return target


@locked
def rename(ws, identifier, name):
    value = name.strip()
    if value.casefold().endswith('.txt'):
        value = value[:-4]
    if not value or value in {'.', '..'} or len(value) > 180 or any(ord(c) < 32 or c in '/:' for c in value) or value.startswith('.'):
        raise ValueError('Scegli un nome di file valido, senza barre o caratteri di controllo')
    items = {x['id']: x for x in scan(ws, persist=True)}
    if identifier not in items:
        raise ValueError('Trascrizione non trovata nella cartella')
    item = items[identifier]
    source = Path(item['path'])
    target = _move(source, source.with_name(value + '.txt'))
    item.update(path=str(target), title=target.stem)
    items[identifier] = item
    atomic_json_write(directory(ws) / 'transcripts.json', items)
    _references(ws, item)
    return {'status': 'OK', 'transcript': item}


@locked
def assign(ws, assignments):
    items = {x['id']: x for x in scan(ws, persist=True)}
    courses = {c['code'] for c in ws.configuration_store().load()['courses']}
    for row in assignments:
        if not isinstance(row, dict) or set(row) - {'transcriptID','courseCode','lessonNumber','associationState','reason','alternatives'}:
            raise ValueError('Campi associazione TXT non validi')
        if row.get('transcriptID') not in items or row.get('courseCode') not in courses:
            raise ValueError('Scegli una trascrizione esistente e un corso configurato')
        if row.get('lessonNumber') is not None and (type(row['lessonNumber']) is not int or not 1 <= row['lessonNumber'] <= 999):
            raise ValueError('Numero lezione non valido')
        if row.get('associationState', 'resolved') not in {'resolved', 'estimated'}:
            raise ValueError('Associazione non valida')
        if not isinstance(row.get('alternatives', []), list) or any(c not in courses for c in row.get('alternatives', [])):
            raise ValueError('Alternative corso non valide')
        if row.get('associationState') == 'estimated' and not row.get('reason'):
            raise ValueError('Indica il motivo dell’associazione stimata')
    for row in assignments:
        item = items[row['transcriptID']]
        if item.get('association_state') == 'resolved' and row.get('associationState') == 'estimated':
            continue
        code, number = row['courseCode'], row.get('lessonNumber')
        lecture = f'{code}-L{number:02d}' if number is not None else None
        if lecture and any(x['id'] != item['id'] and x.get('lecture_id') == lecture for x in items.values()):
            lecture = None  # Both files remain independent sources.
        source = Path(item['path'])
        target = _move(source, folder(ws) / 'Trascrizioni' / code / source.name)
        item.update(path=str(target), course_code=code, lecture_id=lecture,
                    association_state=row.get('associationState', 'resolved'),
                    association_reason=row.get('reason'), association_alternatives=row.get('alternatives', []))
        _references(ws, item)
        if item.get('recording_id'):
            from .capture import CaptureStore
            from .capture_associations import registry
            store = CaptureStore(ws)
            links = registry(store)
            links['recordings'][item['recording_id']] = {'courseCode': code,
                'lessonNumber': number if lecture else None, 'associationState': item['association_state'],
                'reason': row.get('reason'), 'alternatives': row.get('alternatives', [])}
            atomic_json_write(store.directory / 'associations.json', links)
    atomic_json_write(directory(ws) / 'transcripts.json', items)
    return {'status': 'ASSIGNED', 'transcriptIDs': [r['transcriptID'] for r in assignments]}
