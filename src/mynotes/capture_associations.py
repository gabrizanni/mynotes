"""Private, authoritative metadata links; ASR provenance stays stable after archival."""
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
        item["associationState"] = link.get("associationState", "resolved")
    return item


def project_events(store, events):
    links = registry(store)["recordings"]
    return [apply(e, links) for e in events]


def association_candidates(recording, courses):
    """Rank local evidence for the agent; never treat a name score as identity."""
    import re
    from difflib import SequenceMatcher
    def words(text):
        return set(re.findall(r'[a-z0-9]+', str(text).casefold()))
    title = str(recording.get('title', '')) + ' ' + Path(recording.get('original_path') or recording.get('path') or '').stem
    tokens = words(title)
    # Bounded academic text is data only, not executable guidance.
    text_tokens = set()
    transcript = recording.get('transcript_json')
    if recording.get('path', '').casefold().endswith('.txt'):
        try:
            text_tokens = words(Path(recording['path']).read_text(encoding='utf-8')[:32000])
        except (OSError, UnicodeError):
            pass
    if transcript and not text_tokens:
        try:
            import json
            saved = json.loads(Path(transcript).read_text())
            text_tokens = words(' '.join(str(s.get('text', '')) for s in saved.get('segments', []))[:32000])
        except (OSError, ValueError, TypeError):
            pass
    ranked = []
    for course in courses:
        names = [course['code'], course['name'], *course.get('aliases', [])]
        reasons, score = [], 0.0
        if course['code'].casefold() in tokens:
            score += 10; reasons.append('Codice corso nel nome')
        for name in names[1:]:
            overlap = words(name) & tokens
            if overlap:
                score += 2*len(overlap)/max(1, len(words(name))); reasons.append('Nome o alias coerente')
        if recording.get('course_code') == course['code']:
            score += 8; reasons.append('Associazione o calendario esistente')
        topic = words(course['name']) - {'and','of','the','in','to'}
        overlap = topic & text_tokens
        if overlap:
            score += len(overlap)/max(1,len(topic)); reasons.append('Termini nel testo')
        if score:
            ranked.append({'courseCode':course['code'], 'name':course['name'], 'score':round(score,3), 'reasons':list(dict.fromkeys(reasons))})
    return sorted(ranked, key=lambda r:(-r['score'],r['courseCode']))


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
        if recording['id'] not in known and (recording.get('association_state') in {'unresolved', 'estimated'} or not recording.get('lecture_id')):
            items.append({"recordingID": recording['id'], "title": recording['title'], "startsAt": recording['created_at'],
                          "courseCode": recording.get('course_code'), "recording": recording})
    courses = store.workspace.configuration_store().load()['courses']
    for item in items:
        recording = item.get('recording') or item
        item['candidateCourses'] = association_candidates(recording, courses)
        item['associationState'] = recording.get('association_state', 'unresolved')
    from .recording_library import options
    if options(store.workspace).get('folder'):
        from .transcript_library import scan
        represented = {x.get('recordingID') for x in items}
        for transcript in scan(store.workspace):
            if transcript['course_code'] is None or transcript.get('association_state') == 'estimated' or not transcript.get('lecture_id'):
                # TXT-only entries must survive deletion of the original audio.
                if transcript.get('recording_id') in represented:
                    items = [i for i in items if i.get('recordingID') != transcript['recording_id']]
                items.append({'transcriptID': transcript['id'], **({'recordingID': transcript['recording_id']} if transcript.get('recording_id') else {}), 'title': transcript['title'],
                              'path': transcript['path'], 'startsAt': transcript['created_at'],
                              'courseCode': transcript.get('course_code'), 'recording': transcript,
                              'candidateCourses': association_candidates(transcript, courses),
                              'associationState': transcript.get('association_state', 'unresolved')})
    return {"status": "OK", "items": items}


def assign(store, payload):
    from . import transcription_queue as queue
    rows = payload.get("assignments") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise capture.CaptureError("Supply a nonempty assignments array")
    if any('transcriptID' in row for row in rows):
        if not all('transcriptID' in row for row in rows):
            raise capture.CaptureError('Invia le associazioni TXT separatamente da quelle audio/calendario')
        from .transcript_library import assign as assign_transcripts
        with store.lock():
            return assign_transcripts(store.workspace, rows)
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
                if not isinstance(row,dict) or set(row)-{'recordingID','eventID','courseCode','lessonNumber','rule','associationState','reason','alternatives'}:
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
                association_state = row.get('associationState', 'resolved')
                if association_state not in {'resolved', 'estimated'}:
                    raise capture.CaptureError('Choose resolved or estimated associationState')
                reason, alternatives = row.get('reason'), row.get('alternatives', [])
                if association_state == 'estimated' and (not isinstance(reason, str) or not reason.strip()):
                    raise capture.CaptureError('An estimated association requires a reason')
                if reason is not None and (not isinstance(reason, str) or len(reason)>1000):
                    raise capture.CaptureError('Invalid association reason')
                if not isinstance(alternatives, list) or any(not isinstance(x,str) or x not in courses for x in alternatives):
                    raise capture.CaptureError('Alternatives must identify configured courses')
                previous = links['recordings'].get(key, {})
                if previous and previous.get('associationState', 'resolved') == 'resolved' and association_state == 'estimated':
                    continue  # Explicit user choices always override estimates.
                occupied = number is not None and (
                    any(e.get('id') != event.get('id') and e.get('recordingID') != key and e.get('courseCode') == code and e.get('lessonNumber') == number
                        for e in project_events(store, events)) or
                    any(r['id'] != key and r.get('lecture_id') == f'{code}-L{number:02d}' for r in recordings))
                link = {'courseCode':code,'lessonNumber':None if occupied else number,
                        'associationState':association_state, 'reason':reason, 'alternatives':alternatives}
                if occupied:
                    link.update(proposedLessonNumber=number, conflict='La lezione ha già una sorgente: registrazione conservata separatamente.')
                history = list(previous.get('history', []))
                if previous:
                    history.append({k:v for k,v in previous.items() if k != 'history'})
                link['history'] = history
                links['recordings'][key] = link
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
            result = {'status':'ASSIGNED','recordingIDs':updated}
    # File export runs outside both control/queue transactions. Association never reruns ASR.
    from .recording_library import finish
    for item in queue.library(store.workspace):
        if item['id'] in updated and item.get('transcription_status') == 'COMPLETED' and item.get('transcript_json'):
            finish(store.workspace, item, item['transcript_json'])
    return result


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
