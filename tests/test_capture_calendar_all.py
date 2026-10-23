"""Complete feeds, independent identity, and delayed assignment end to end."""
from copy import deepcopy
from datetime import UTC, datetime
import json
import os
from pathlib import Path

import pytest

from mynotes import capture, capture_associations as links, capture_dashboard, transcription_queue as q
from mynotes.configuration import course_template
from mynotes.ingestion import discover_recordings, recording_for_lecture
from mynotes.sourcepack import build_source_pack
from mynotes.utils import atomic_json_write
from test_capture import store, subscription, feed, component
from test_capture_queue import ws, audio, fake_registry, claim


def source(**changes):
    return subscription(importMode='all_timed',mappings=[],**changes)


def capable(store, monkeypatch):
    monkeypatch.setattr(capture,'calendar_recordings_supported',lambda _:True)


def test_complete_feed_in_empty_workspace_and_native_gate(store,monkeypatch):
    settings=store.workspace.configuration_store().load();settings['courses']=[];store.workspace.configuration_store().save(settings)
    with pytest.raises(capture.CaptureError,match='Aggiorna'):
        capture.subscribe(store,source())
    capable(store,monkeypatch)
    capture.subscribe(store,source())
    monkeypatch.setattr(capture,'download',lambda *_:(feed(component(title='Unknown subject 3')),{}))
    assert capture.sync(store)['status']=='SYNCED'
    event=store.schedule()['events'][0]
    assert event['courseCode'] is None and event['lessonNumber'] is None
    assert event['eligibility']=='SCHEDULED' and len(event['recordingID'])==32
    assert not store.config()['enabled']


def test_recognition_boundaries_ambiguity_official_and_missing_numbers():
    details=[{'code':'OPS','name':'Operations Planning','aliases':[]},{'code':'FINV','name':'Finance and Investing','aliases':['FI&NV']}]
    data=feed(component(uid='a',title='OPS 17'),component(uid='b',title='FI&NV 1',start='20261013T100000Z',end='20261013T110000Z'),component(uid='c',title='OPS workshop',start='20261014T100000Z',end='20261014T110000Z'),component(uid='d',title='XOPS Lecture 2',start='20261015T100000Z',end='20261015T110000Z'),component(uid='e',title='OPS + FI&NV Lecture 5',start='20261016T100000Z',end='20261016T110000Z'))
    report={};events=capture.calendar_events(data,source(),[],{'OPS','FINV'},course_details=details,report=report)
    by={e['calendarUID']:e for e in events}
    assert (by['a']['courseCode'],by['a']['lessonNumber'])==('OPS',17)
    assert (by['b']['courseCode'],by['b']['lessonNumber'])==('FINV',1)
    assert by['c']['courseCode']=='OPS' and by['c']['lessonNumber'] is None
    assert by['d']['courseCode'] is None
    assert by['e']['associationState']=='ambiguous' and by['e']['eligibility']=='SCHEDULED'
    assert report['recognizedCourses']==2 and report['pending']==2


def test_invalid_and_all_day_events_do_not_remove_valid_events():
    all_day='BEGIN:VEVENT\r\nUID:holiday\r\nDTSTART;VALUE=DATE:20261012\r\nDTEND;VALUE=DATE:20261013\r\nSUMMARY:Holiday\r\nEND:VEVENT\r\n'
    missing_end='BEGIN:VEVENT\r\nUID:bad\r\nDTSTART:20261012T120000Z\r\nSUMMARY:Missing end\r\nEND:VEVENT\r\n'
    report={};events=capture.calendar_events(feed(component(),all_day,missing_end),source(),[],set(),report=report)
    assert len(events)==1
    assert report['excluded']==2 and report['imported']==1


def test_id_survives_title_time_changes_long_titles_and_cancellation():
    old=capture.calendar_events(feed(component(title='Unlisted Lecture 4')),source(),[],set())
    new=capture.calendar_events(feed(component(title='A'*900,start='20261013T100000Z',end='20261013T110000Z')),source(),old,set())
    assert new[0]['id']==old[0]['id'] and new[0]['recordingID']==old[0]['recordingID']
    assert len(new[0]['title'])==900
    cancelled=capture.calendar_events(feed(component(extra='STATUS:CANCELLED\r\n')),source(),new,set())
    assert cancelled[0]['eligibility']=='CANCELLED'
    assert capture.calendar_events(feed(),source(),old,set())[0]['eligibility']=='NEEDS_REVIEW'


def test_recurring_override_and_same_title_distinct_ids():
    base=component(extra='RRULE:FREQ=DAILY;COUNT=3\r\n')
    old=capture.calendar_events(feed(base),source(),[],set())
    moved=component(start='20261013T120000Z',end='20261013T130000Z',extra='RECURRENCE-ID:20261013T100000Z\r\n')
    new=capture.calendar_events(feed(base,moved),source(),old,set())
    assert {e['recordingID'] for e in new}=={e['recordingID'] for e in old}
    assert len(capture.calendar_events(feed(component(uid='a'),component(uid='b')),source(),[],set()))==2


def test_no_number_does_not_create_duplicate_course_conflict():
    events=capture.calendar_events(feed(component(uid='a',title='MATH'),component(uid='b',title='MATH',start='20261013T100000Z',end='20261013T110000Z')),source(),[],{'MATH'})
    assert all(e['eligibility']=='SCHEDULED' for e in capture.validate_windows(events,{'recordBeforeSeconds':0,'recordAfterSeconds':120}))


def test_legacy_conversion_preserves_destination_and_exclusion(store,monkeypatch):
    capable(store,monkeypatch);capture.subscribe(store,subscription())
    monkeypatch.setattr(capture,'download',lambda *_:(feed(component()),{'etag':'old'}))
    capture.sync(store);old=store.schedule()['events'][0]
    capture_dashboard.edit_lesson(store,old['id'],{'recordingEnabled':False})
    capture.subscribe(store,source())
    assert not capture.read_json(store.directory/'sync.json')['sources']
    capture.sync(store);new=store.schedule()['events'][0]
    assert new['id']==old['id'] and 'recordingID' not in new and new['lessonNumber']==old['lessonNumber']
    assert new['eligibility']=='CANCELLED'


def test_association_after_transcription_reuses_exact_result_in_prepare(ws,monkeypatch):
    path,identifier=audio(ws,'OPS Lecture 17');engine=fake_registry(monkeypatch)
    assert q.enqueue(ws)['added']==1
    job=q.status(ws)['jobs'][0]['id'];claim(ws,job)
    assert q.run_job(ws,job)['status']=='COMPLETED'
    before=q.library(ws)[0];transcript=Path(before['transcript_json']);original=transcript.read_bytes();calls=engine.calls
    from mynotes.capture_courses import add_course
    course=add_course(ws,'Operations Planning')['course']['code']
    store=capture.CaptureStore(ws)
    assert links.assign(store,{'assignments':[{'recordingID':identifier,'courseCode':course,'lessonNumber':17}]})['status']=='ASSIGNED'
    item=q.library(ws,query='OPS',date='2026-01-01',course=course)[0]
    assert item['id']==identifier and item['lecture_id']==course+'-L17' and item['status']=='COMPLETED'
    assert q.enqueue(ws)['added']==0 and engine.calls==calls
    inventory=discover_recordings(ws,ws.configuration_store().load(),lecture_id=course+'-L17',persist=False)
    assert recording_for_lecture(inventory,course+'-L17')==path
    pack=build_source_pack(ws,course+'-L17',audio=path)
    assert pack['transcription']['capture_recording_id']==identifier
    assert pack['transcription']['original_asr']['settings_hash']==json.loads(original)['settings_hash']
    assert engine.calls==calls and transcript.read_bytes()==original and path.exists()
    assert q.library(ws,query='Other')==[]


def test_queued_profile_is_preserved_after_assignment(ws,monkeypatch):
    path,identifier=audio(ws);engine=fake_registry(monkeypatch)
    q.enqueue(ws);job=q.status(ws)['jobs'][0]['id']
    from mynotes.capture_courses import add_course
    code=add_course(ws,'Different subject')['course']['code']
    assert links.assign(capture.CaptureStore(ws),{'assignments':[{'recordingID':identifier,'courseCode':code}]})['status']=='ASSIGNED'
    claim(ws,job)
    assert q.run_job(ws,job)['status']=='COMPLETED'


def test_active_job_defers_assignment_and_bad_batch_is_atomic(ws,monkeypatch):
    _,identifier=audio(ws);fake_registry(monkeypatch);q.enqueue(ws)
    from mynotes.capture_courses import add_course
    code=add_course(ws,'Subject')['course']['code'];store=capture.CaptureStore(ws)
    with pytest.raises(capture.CaptureError):
        links.assign(store,{'assignments':[{'recordingID':identifier,'courseCode':code},{'recordingID':'missing','courseCode':code}]})
    assert not (store.directory/'associations.json').exists()
    claim(ws,q.status(ws)['jobs'][0]['id'])
    assert links.assign(store,{'assignments':[{'recordingID':identifier,'courseCode':code}]})['status']=='DEFERRED'
    assert not (store.directory/'associations.json').exists()


def test_pending_includes_free_manual_recordings_for_later_linking(ws):
    _,identifier=audio(ws,'Interview')
    items=links.pending(capture.CaptureStore(ws))['items']
    assert len(items)==1 and items[0]['recordingID']==identifier and items[0]['associationState']=='unresolved'


def test_saved_rule_survives_sync_and_invalidates_old_http_validators(store,monkeypatch):
    capable(store,monkeypatch);capture.subscribe(store,source())
    monkeypatch.setattr(capture,'download',lambda *_:(feed(component(title='New subject 9')),{'etag':'cached'}))
    capture.sync(store);event=store.schedule()['events'][0]
    assert len(links.pending(store)['items'])==1
    links.assign(store,{'assignments':[{'recordingID':event['recordingID'],'courseCode':'MATH','lessonNumber':9,'rule':{'match':'New subject'}}]})
    assert not capture.read_json(store.directory/'sync.json')['sources']
    assert store.schedule()['events'][0]['recordingID']==event['recordingID']
    assert not links.pending(store)['items']
    monkeypatch.setattr(capture,'download',lambda *_:(feed(component(title='New subject 9'),component(uid='second',title='New subject workshop',start='20261013T100000Z',end='20261013T110000Z')),{}))
    capture.sync(store)
    assert all(e['courseCode']=='MATH' for e in store.schedule()['events'])


def test_association_rejects_duplicate_lesson_before_any_registry_write(ws,monkeypatch):
    from mynotes.capture_courses import add_course
    code=add_course(ws,'Example')['course']['code']
    _,first=audio(ws,'First');_,second=audio(ws,'Second')
    store=capture.CaptureStore(ws)
    with pytest.raises(capture.CaptureError,match='already'):
        links.assign(store,{'assignments':[{'recordingID':first,'courseCode':code,'lessonNumber':1},{'recordingID':second,'courseCode':code,'lessonNumber':1}]})
    assert not (store.directory/'associations.json').exists()


def test_stale_worker_does_not_prevent_assignment(ws,monkeypatch):
    _,identifier=audio(ws);fake_registry(monkeypatch);q.enqueue(ws)
    from mynotes.capture_courses import add_course
    code=add_course(ws,'Example')['course']['code']
    with q.database(ws) as conn:
        conn.execute("UPDATE jobs SET status='RUNNING',pid=99999999")
    assert links.assign(capture.CaptureStore(ws),{'assignments':[{'recordingID':identifier,'courseCode':code}]})['status']=='ASSIGNED'


def test_cli_pending_assign_and_library_filters(ws,monkeypatch,capsys,tmp_path):
    from mynotes.cli import main
    from mynotes.capture_courses import add_course
    _,identifier=audio(ws,'Example Lecture 8');code=add_course(ws,'Example')['course']['code']
    payload=tmp_path/'assignment.json';payload.write_text(json.dumps({'assignments':[{'recordingID':identifier,'courseCode':code}]}))
    assert main(['capture','assign','--workspace',str(ws.root),'--file',str(payload),'--json'])==0
    assert json.loads(capsys.readouterr().out)['status']=='ASSIGNED'
    assert main(['transcription-queue','library','--workspace',str(ws.root),'--query','Example','--course',code,'--date','2026-01-01','--json'])==0
    assert json.loads(capsys.readouterr().out)['recordings'][0]['lecture_id']==code+'-L08'


def test_date_filter_uses_event_timezone(ws):
    path,identifier=audio(ws)
    sidecar=Path(str(path)+'.capture.json');session=json.loads(sidecar.read_text())
    session['event'].update(startsAt='2026-01-01T20:00:00Z',timezone='Asia/Kolkata')
    atomic_json_write(sidecar,session)
    assert q.library(ws,date='2026-01-01')==[]
    assert q.library(ws,date='2026-01-02')[0]['id']==identifier


def test_mapped_calendar_can_coexist_with_numberless_complete_calendar():
    numberless=capture.calendar_events(feed(component(title='MATH workshop')),source(),[],{'MATH'})
    mapped=capture.calendar_events(feed(component(uid='legacy')),subscription(),numberless,{'MATH'})
    assert mapped[0]['lessonNumber']==1


def test_token_matching_normalizes_punctuation_and_underscores():
    from mynotes.capture_calendar import contains
    assert contains('Operations_Planning — Lesson 1','operations planning')
    assert not contains('Microoperations','operations')


def test_new_course_catalog_refreshes_recognition_even_when_feed_is_unchanged(store,monkeypatch):
    capable(store,monkeypatch);capture.subscribe(store,source())
    observed=[]
    def download(_,metadata):
        observed.append(metadata)
        return (None,metadata) if metadata.get('etag') else (feed(component(title='New Subject Lecture 2')),{'etag':'same-feed'})
    monkeypatch.setattr(capture,'download',download)
    capture.sync(store);capture.sync(store)
    assert observed[1]['etag']=='same-feed'
    assert store.schedule()['events'][0]['courseCode'] is None
    settings=store.workspace.configuration_store().load();settings['courses'][0]['aliases'].append('New Subject');store.workspace.configuration_store().save(settings)
    capture.sync(store)
    assert observed[2]=={} and store.schedule()['events'][0]['courseCode']=='MATH'


def test_obsolete_inferred_course_cannot_turn_valid_times_into_error():
    details = [{'code': 'MATH', 'name': 'Mathematics', 'aliases': []}]
    data = feed(component(title='MATH 13'))
    old = capture.calendar_events(data, source(), [], {'MATH'}, course_details=details)
    old[0].update(courseCode='OLDCODE', eligibility='NEEDS_REVIEW', reason='Calendar event has invalid times')
    report = {}
    new = capture.calendar_events(data, source(), old, {'MATH'}, course_details=details, report=report)
    assert new[0]['courseCode'] == 'MATH'
    assert new[0]['eligibility'] == 'SCHEDULED'
    assert new[0]['recordingID'] == old[0]['recordingID']
    assert new[0]['lessonNumber'] == 13
    assert report['excluded'] == 0 and report['imported'] == 1


def test_current_recognition_refreshes_but_explicit_association_wins(store, monkeypatch):
    capable(store, monkeypatch)
    capture.subscribe(store, source())
    monkeypatch.setattr(capture, 'download', lambda *_: (feed(component(title='Unknown Lecture 7')), {}))
    capture.sync(store)
    old = store.schedule()['events'][0]
    links.assign(store, {'assignments': [{'recordingID': old['recordingID'], 'courseCode': 'MATH', 'lessonNumber': 9}]})
    monkeypatch.setattr(capture, 'download', lambda *_: (feed(component(title='Other Lecture 1', start='20261013T100000Z', end='20261013T110000Z')), {}))
    capture.sync(store)
    new = store.schedule()['events'][0]
    assert new['id'] == old['id'] and new['recordingID'] == old['recordingID']
    assert (new['courseCode'], new['lessonNumber']) == ('MATH', 9)
    assert new['eligibility'] == 'SCHEDULED'


def test_legacy_stale_course_is_projected_before_sync_validation_without_identity_change(store, monkeypatch):
    capable(store, monkeypatch)
    capture.subscribe(store, source())
    old = capture.normalize_event({'id': 'teaching:one:single', 'courseCode': 'OBSOLETE', 'lessonNumber': 13,
        'title': 'MATH 13', 'startsAt': '2026-10-12T10:00:00Z', 'endsAt': '2026-10-12T11:00:00Z',
        'calendarUID': 'one', 'originalOccurrence': '2026-10-12T10:00:00Z',
        'eligibility': 'NEEDS_REVIEW', 'reason': 'Calendar event has invalid times'}, {'OBSOLETE'}, 'teaching')
    atomic_json_write(store.directory / 'associations.json', {'schemaVersion': 1, 'recordings': {
        old['id']: {'courseCode': 'MATH', 'lessonNumber': 13, 'associationState': 'resolved'}}})
    store.save_schedule([old], store.config())
    assert store.schedule()['events'][0]['courseCode'] == 'MATH'
    assert store.schedule()['events'][0]['eligibility'] == 'NEEDS_REVIEW'
    monkeypatch.setattr(capture, 'download', lambda *_: (feed(component(title='MATH 13')), {}))
    for _ in range(2):
        assert capture.sync(store)['status'] == 'SYNCED'
        new = store.schedule()['events'][0]
        assert new['id'] == old['id'] and 'recordingID' not in new
        assert (new['courseCode'], new['lessonNumber']) == ('MATH', 13)
        assert new['eligibility'] == 'SCHEDULED' and not new['reason']
        assert capture.read_json(store.directory / 'sync.json')['sources']['teaching']['summary']['excluded'] == 0


@pytest.mark.parametrize('replacement,code', [
    ('BEGIN:VEVENT\r\nUID:one\r\nDTSTART:20261012T100000Z\r\nSUMMARY:MATH\r\nEND:VEVENT\r\n', 'MISSING_END'),
    (component(end='20261012T090000Z'), 'INVALID_END'),
    (component(end='20261013T110000Z'), 'INVALID_DURATION'),
    ('BEGIN:VEVENT\r\nUID:one\r\nDTSTART;VALUE=DATE:20261012\r\nDTEND;VALUE=DATE:20261013\r\nSUMMARY:MATH\r\nEND:VEVENT\r\n', 'ALL_DAY_EVENT'),
])
def test_invalid_occurrence_has_accurate_diagnostic_and_does_not_block_other_events(replacement, code):
    old = capture.calendar_events(feed(component()), source(), [], {'MATH'})
    report = {}
    new = capture.calendar_events(feed(replacement, component(uid='good', start='20261014T100000Z', end='20261014T110000Z')), source(), old, {'MATH'}, report=report)
    by = {e['calendarUID']: e for e in new}
    assert by['good']['eligibility'] == 'SCHEDULED'
    assert by['one']['eligibility'] == 'NEEDS_REVIEW'
    assert by['one']['reasonCode'] == code
    assert report['issues'][0]['code'] == code
    assert report['excluded'] == 1


def test_cancelled_then_absent_event_stays_cancelled():
    old = capture.calendar_events(feed(component(extra='STATUS:CANCELLED\r\n')), source(), [], {'MATH'})
    for _ in range(2):
        old = capture.calendar_events(feed(), source(), old, {'MATH'})
        assert old[0]['eligibility'] == 'CANCELLED'
        assert old[0]['reasonCode'] == 'CALENDAR_CANCELLED'


def test_bad_recurrence_does_not_block_unrelated_event():
    report = {}
    data = feed(component(extra='RRULE:FREQ=NOTAFREQUENCY\r\n'), component(uid='good', start='20261014T100000Z', end='20261014T110000Z'))
    new = capture.calendar_events(data, source(), [], {'MATH'}, report=report)
    assert len(new) == 1 and new[0]['calendarUID'] == 'good'
    assert report['excluded'] == 1
    assert report['issues'][0]['code'] == 'INVALID_RECURRENCE'


def test_invalid_recurring_override_never_restores_master_occurrence():
    base = component(extra='RRULE:FREQ=DAILY;COUNT=3\r\n')
    old = capture.calendar_events(feed(base), source(), [], {'MATH'})
    invalid = component(start='20261013T120000Z', end='20261013T110000Z', extra='RECURRENCE-ID:20261013T100000Z\r\n')
    report = {}
    new = capture.calendar_events(feed(base, invalid), source(), old, {'MATH'}, report=report)
    by = {e['originalOccurrence']: e for e in new}
    assert len(by) == 3
    assert by['2026-10-13T10:00:00Z']['eligibility'] == 'NEEDS_REVIEW'
    assert by['2026-10-13T10:00:00Z']['reasonCode'] == 'INVALID_END'
    assert by['2026-10-12T10:00:00Z']['eligibility'] == 'SCHEDULED'
    assert by['2026-10-14T10:00:00Z']['eligibility'] == 'SCHEDULED'
    assert report['excluded'] == 1
    fresh = capture.calendar_events(feed(base, invalid), source(), [], {'MATH'})
    assert len(fresh) == 2


@pytest.mark.parametrize('times', ['', 'DTSTART:20261013T100000Z\r\n'])
def test_minimal_recurring_cancellation_is_a_tombstone_not_a_time_error(times):
    base = component(extra='RRULE:FREQ=DAILY;COUNT=3\r\n')
    old = capture.calendar_events(feed(base), source(), [], {'MATH'})
    cancellation = ('BEGIN:VEVENT\r\nUID:one\r\nRECURRENCE-ID:20261013T100000Z\r\n'
                    'STATUS:CANCELLED\r\n' + times + 'END:VEVENT\r\n')
    report = {}
    new = capture.calendar_events(feed(base, cancellation), source(), old, {'MATH'}, report=report)
    by = {e['originalOccurrence']: e for e in new}
    assert by['2026-10-13T10:00:00Z']['eligibility'] == 'CANCELLED'
    assert by['2026-10-13T10:00:00Z']['reasonCode'] == 'CALENDAR_CANCELLED'
    assert by['2026-10-12T10:00:00Z']['eligibility'] == 'SCHEDULED'
    assert by['2026-10-14T10:00:00Z']['eligibility'] == 'SCHEDULED'
    assert report['excluded'] == 0 and report['issues'] == []


def test_catalogue_change_reprojects_immediately_but_failed_refresh_obeys_backoff(store, monkeypatch):
    capable(store, monkeypatch)
    capture.subscribe(store, source())
    calls = []
    def download(_, metadata):
        calls.append(metadata)
        if len(calls) == 1:
            return feed(component(title='New Subject Lecture 2')), {'etag': 'cached'}
        raise OSError('offline')
    monkeypatch.setattr(capture, 'download', download)
    capture.sync(store)
    settings = store.workspace.configuration_store().load()
    settings['courses'][0]['aliases'].append('New Subject')
    store.workspace.configuration_store().save(settings)
    assert capture.sync(store, if_due=True)['status'] == 'SYNC_FAILED'
    assert len(calls) == 2 and calls[1] == {}
    assert capture.sync(store, if_due=True)['sources'] == []
    assert len(calls) == 2


def test_source_rule_change_invalidates_http_304_and_updates_inferred_association(store, monkeypatch):
    capable(store, monkeypatch)
    capture.subscribe(store, source())
    calls = []
    def download(_, metadata):
        calls.append(metadata)
        return (None, metadata) if metadata.get('etag') else (feed(component(title='New Subject Lecture 2')), {'etag': 'cached'})
    monkeypatch.setattr(capture, 'download', download)
    capture.sync(store)
    capture.sync(store)
    assert calls[1]['etag'] == 'cached'
    config = store.config()
    config['sources'][0]['mappings'] = [{'match': 'New Subject', 'courseCode': 'MATH'}]
    store.save_config(config)
    capture.sync(store, if_due=True)
    assert calls[2] == {}
    assert store.schedule()['events'][0]['courseCode'] == 'MATH'


def test_unconditional_304_cannot_claim_changed_catalogue_was_applied(store, monkeypatch):
    capable(store, monkeypatch)
    capture.subscribe(store, source())
    monkeypatch.setattr(capture, 'download', lambda *_: (feed(component(title='New Subject Lecture 2')), {'etag': 'cached'}))
    capture.sync(store)
    before = store.schedule()['events']
    settings = store.workspace.configuration_store().load()
    settings['courses'][0]['aliases'].append('New Subject')
    store.workspace.configuration_store().save(settings)
    monkeypatch.setattr(capture, 'download', lambda *_: (None, {}))
    assert capture.sync(store)['status'] == 'SYNC_FAILED'
    assert store.schedule()['events'] == before
