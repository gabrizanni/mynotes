"""Private, authoritative metadata links. Audio and ASR outputs never move."""
from __future__ import annotations

import copy
from pathlib import Path

from . import capture
from .utils import atomic_json_write, file_sha256


def registry(store):
    return capture.read_json(store.directory / "associations.json", {"schemaVersion": 1, "recordings": {}})


def apply(event, links):
    item = copy.deepcopy(event)
    link = links.get(item.get("recordingID")) or links.get(item.get("id"))
    if link:
        item.update({k: link.get(k) for k in ("courseCode", "lessonNumber")})
        item["associationState"] = "resolved"
    return item


def project_events(store, events):
    links = registry(store)["recordings"]
    return [apply(e, links) for e in events]


def pending(store):
    from .transcription_queue import library
    recordings = library(store.workspace)
    by_id = {r['id']: r for r in recordings}
    items = []
    known = set()
    for event in project_events(store, store.schedule()["events"]):
        identifier = event.get("recordingID")
        if event.get("sourceID") not in {"manual", "special", "static"} and (not event.get("courseCode") or event.get("lessonNumber") is None):
            recording = by_id.get(identifier, {})
            items.append({**event, "recording": recording or None})
            known.add(identifier)
    for recording in recordings:
        if recording.get('source_id') not in {None, 'manual', 'special', 'static'} and recording['id'] not in known and (not recording.get('course_code') or not recording.get('lecture_id')):
            items.append({"recordingID": recording['id'], "title": recording['title'], "startsAt": recording['created_at'],
                          "courseCode": recording.get('course_code'), "recording": recording})
    return {"status": "OK", "items": items}


def assign(store, payload):
    from . import transcription_queue as queue
    rows = payload.get("assignments") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise capture.CaptureError("Supply a nonempty assignments array")
    with store.lock():
        # Holding the queue transaction prevents a queued worker from starting mid-assignment.
        with queue.database(store.workspace) as conn:
            conn.execute("BEGIN IMMEDIATE")
            events = store.source_events()
            recordings = queue.library(store.workspace)
            targets = {e.get('recordingID') or e['id']: e for e in events}
            for item in recordings:
                targets.setdefault(item['id'], {"recordingID": item['id'], "id": item.get('event_id'), "sourceID": item.get('source_id'), "title": item['title'], "courseCode": item.get('course_code'),
                    "lessonNumber": int(item['lecture_id'].rsplit('-L',1)[1]) if item.get('lecture_id') else None})
            links = registry(store)
            courses = {c['code'] for c in store.workspace.configuration_store().load()['courses']}
            config = store.config()
            updated, rules = [], []
            state = capture.capture_status(store)
            active_events = {s.get('event',{}).get('id') for s in state['runtime'].get('sessions',[]) if s.get('status') in {'RECORDING','STARTED_LATE','EXPORTING'}}
            current = state['runtime'].get('currentSession') or {}
            if state['runtime'].get('recording') or state['runtime'].get('exporting'):
                active_events.add(current.get('event',{}).get('id'))
            if state['running'] is False:
                active_events.clear()
            running_ids = {r['recording_id'] for r in conn.execute("SELECT recording_id,pid FROM jobs WHERE status='RUNNING'") if queue._alive(r['pid'])}
            running_ids.update(r['recording_id'] for r in conn.execute("SELECT a.recording_id,j.pid FROM recording_aliases a JOIN jobs j ON j.id=a.job_id WHERE j.status='RUNNING'") if queue._alive(r['pid']))
            for row in rows:
                if not isinstance(row,dict) or set(row)-{'recordingID','eventID','courseCode','lessonNumber','rule'}:
                    raise capture.CaptureError('Invalid assignment fields')
                identifier = row.get('recordingID') or row.get('eventID')
                if not isinstance(identifier, str) or not identifier:
                    raise capture.CaptureError('Supply a recordingID or eventID string')
                event = targets.get(identifier)
                if event is None:
                    event = next((e for e in events if e['id']==identifier),None)
                if event is None:
                    raise capture.CaptureError('Recording or event not found')
                key = event.get('recordingID') or event['id']
                asr_state = capture.read_json(queue.asr_directory(store.workspace, {'id':key, 'storage_recording_id':event.get('recordingID'), 'lecture_id':None})/'status.json', {})
                direct_running = asr_state.get('status') == 'RUNNING' and queue._alive(asr_state.get('pid'))
                if event.get('id') in active_events or key in running_ids or direct_running:
                    return {'status':'DEFERRED','message':'Termina e salva la registrazione o trascrizione attiva prima di associarla.'}
                code = row.get('courseCode')
                number = row.get('lessonNumber',links['recordings'].get(key,event).get('lessonNumber'))
                if number is None and 'lessonNumber' not in row:
                    from .capture_calendar import official_number
                    detail = next((c for c in store.workspace.configuration_store().load()['courses'] if c['code'] == code),None)
                    number = official_number(event.get('title',''), detail)
                if not isinstance(code, str) or code not in courses or (number is not None and (type(number) is not int or not 1<=number<=999)):
                    raise capture.CaptureError('Choose a configured course and an optional valid lesson number')
                if not event.get('recordingID') and (code,number)!=(event.get('courseCode'),event.get('lessonNumber')):
                    raise capture.CaptureError('Legacy lesson destinations cannot be reassigned; use an independent recording')
                links['recordings'][key]={'courseCode':code,'lessonNumber':number}
                updated.append(key)
                if row.get('rule'):
                    rule=row['rule']
                    if not isinstance(rule,dict) or set(rule)!={'match'} or not isinstance(rule['match'],str) or not rule['match'].strip():
                        raise capture.CaptureError('A saved rule needs a nonempty title match')
                    source=next((s for s in config['sources'] if s['sourceID']==event.get('sourceID')),None)
                    if not source or source.get('importMode','mapped')!='all_timed':
                        raise capture.CaptureError('Rules require a complete calendar source')
                    rules.append((source,{'match':rule['match'].strip(),'courseCode':code}))
            projected=[apply(e,links['recordings']) for e in events]
            capture.validate_windows(projected,config)
            claims={}
            for event in projected:
                if event.get('courseCode') and event.get('lessonNumber') is not None:
                    claim=(event['courseCode'],event['lessonNumber'])
                    if claim in claims and claims[claim]!=event['id']:
                        raise capture.CaptureError('Another recording already identifies this course and lesson')
                    claims[claim]=event['id']
            for item in recordings:
                link=links['recordings'].get(item['id'])
                if not link or link.get('lessonNumber') is None:
                    continue
                claim=(link['courseCode'],link['lessonNumber'])
                event=targets.get(item['id'],{})
                owner=event.get('id',item['id'])
                if claim in claims and claims[claim]!=owner:
                    raise capture.CaptureError('Another recording already identifies this course and lesson')
                claims[claim]=owner
            atomic_json_write(store.directory/'associations.json',links)
            for source,rule in rules:
                source['mappings']=[m for m in source.get('mappings',[]) if m['match']!=rule['match']]+[rule]
            if rules:
                path = store.directory / 'sync.json'
                metadata = capture.read_json(path, {'sources': {}})
                for source, _ in rules:
                    metadata['sources'].pop(source['sourceID'], None)
                atomic_json_write(path, metadata)
            store.save_schedule(events,config)
            store.save_config(config)
            return {'status':'ASSIGNED','recordingIDs':updated}


def linked_recording(workspace, lecture_id):
    from .transcription_queue import library
    matches=[r for r in library(workspace) if r.get('lecture_id')==lecture_id]
    if len(matches)>1:
        raise capture.CaptureError('More than one recording identifies this lecture')
    return matches[0] if matches else None


def validated_transcript(workspace, recording):
    """Reuse original ASR provenance, independent of newly associated course settings."""
    from .asr import validate_transcript, segments_digest
    from .transcription_queue import asr_directory
    path=Path(recording.get('transcript_json') or asr_directory(workspace,recording)/'transcript.json')
    if not path.is_file():
        return None
    saved=capture.read_json(path)
    if (saved.get('status')!='COMPLETED' or validate_transcript(saved,saved.get('audio_duration_seconds'))
        or saved.get('segments_sha256')!=segments_digest(saved.get('segments'))
        or saved.get('audio_sha256')!=file_sha256(Path(recording['path']))):
        raise capture.CaptureError('Existing transcript does not match this audio or failed validation')
    return path,saved
