"""Real local persistence and CLI contracts for the Capture interface."""
import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from mynotes import capture, capture_dashboard as dashboard
from mynotes.cli import main
from mynotes.utils import atomic_json_write
from test_capture import store, event, subscription, feed, component


def future(number=5, **extra):
    start=datetime.now(UTC)+timedelta(days=2)
    return {'courseCode':'MATH','lessonNumber':number,'startsAt':capture.iso(start),'endsAt':capture.iso(start+timedelta(hours=1)),
            'timezone':'UTC','title':'Special lesson',**extra}


def test_dashboard_does_not_configure_enable_sync_or_expose_link(store, monkeypatch):
    capture.subscribe(store,subscription(label='Teaching calendar'))
    before=store.config_path.read_bytes()
    monkeypatch.setattr(capture,'download',lambda *_:pytest.fail('dashboard must not download'))
    monkeypatch.setattr(capture.subprocess,'run',lambda *_a,**_k:pytest.fail('dashboard must not launch'))
    result=dashboard.dashboard(store)
    assert result['schemaVersion']==1 and not result['enabled'] and not result['running']
    assert result['sources'][0]['label']=='Teaching calendar'
    assert 'private-token' not in json.dumps(result) and 'https://' not in json.dumps(result)
    assert store.config_path.read_bytes()==before


def test_special_lesson_survives_static_reimport_and_course_defaults(store):
    added=dashboard.add_lesson(store,future())
    capture.import_schedule(store,[event()])
    capture.import_schedule(store,[event(2)])
    capture.configure(store,{'recordAfterSeconds':60})
    special=next(e for e in store.schedule()['events'] if e['id']==added['eventID'])
    assert special['eligibility']=='SCHEDULED' and special['sourceID']=='special'
    assert dashboard.dashboard(store)['events'][-1]['special']


def test_schedule_changes_notify_native_only_after_publication(store, monkeypatch):
    capture.import_schedule(store, [event()])
    save_config = store.save_config
    observed = []
    def notify(config):
        observed.append(store.schedule()['events'][0]['title'])
        save_config(config)
    monkeypatch.setattr(store, 'save_config', notify)
    revision = store.config()['revision']
    dashboard.edit_lesson(store, 'static:MATH-L01', {'title': 'Local'})
    assert observed == ['Local'] and store.config()['revision'] == revision + 1
    dashboard.reset_lesson(store, 'static:MATH-L01')
    assert observed[-1] != 'Local' and store.config()['revision'] == revision + 2
    dashboard.dashboard(store)
    assert store.config()['revision'] == revision + 2
    capture.subscribe(store, subscription())
    observed.clear()
    monkeypatch.setattr(capture, 'download', lambda *_: (feed(component()), {}))
    revision = store.config()['revision']
    capture.sync(store)
    assert len(observed) == 1 and store.config()['revision'] == revision + 1
    capture.sync(store, if_due=True)
    assert len(observed) == 1 and store.config()['revision'] == revision + 1


def test_override_survives_calendar_refresh_and_reset_uses_latest_provider(store,monkeypatch):
    capture.subscribe(store,subscription())
    monkeypatch.setattr(capture,'download',lambda *_:(feed(component()),{}))
    capture.sync(store)
    original=store.schedule()['events'][0]
    dashboard.edit_lesson(store,original['id'],{'startsAt':'2026-10-12T12:00:00Z','endsAt':'2026-10-12T13:00:00Z','title':'Local title','recordingEnabled':False})
    monkeypatch.setattr(capture,'download',lambda *_:(feed(component(start='20261012T110000Z',end='20261012T120000Z')),{}))
    capture.sync(store)
    effective=store.schedule()['events'][0]
    assert effective['startsAt']=='2026-10-12T12:00:00Z' and effective['title']=='Local title' and effective['eligibility']=='CANCELLED'
    assert dashboard.dashboard(store)['events'][0]['localChanges']
    dashboard.reset_lesson(store,original['id'])
    restored=store.schedule()['events'][0]
    assert restored['startsAt']=='2026-10-12T11:00:00Z' and restored['eligibility']=='SCHEDULED'
    assert not dashboard.dashboard(store)['events'][0]['localChanges']


@pytest.mark.parametrize('kind',['cancelled','disappeared'])
def test_local_edit_cannot_resurrect_provider_cancellation_or_disappearance(store,monkeypatch,kind):
    capture.subscribe(store,subscription())
    monkeypatch.setattr(capture,'download',lambda *_:(feed(component()),{}));capture.sync(store)
    identity=store.schedule()['events'][0]['id']
    dashboard.edit_lesson(store,identity,{'recordingEnabled':True,'title':'Local'})
    value=feed(component(extra='STATUS:CANCELLED\r\n')) if kind=='cancelled' else feed()
    monkeypatch.setattr(capture,'download',lambda *_:(value,{}));capture.sync(store)
    assert store.schedule()['events'][0]['eligibility']==('CANCELLED' if kind=='cancelled' else 'NEEDS_REVIEW')


def test_only_requested_calendar_is_downloaded_and_unknown_id_is_rejected(store,monkeypatch):
    capture.subscribe(store,subscription())
    capture.subscribe(store,subscription(sourceID='other',url='https://other.example.invalid/feed'))
    calls=[]
    monkeypatch.setattr(capture,'download',lambda source,_:(calls.append(source['sourceID']) or feed(component()),{}))
    result=capture.sync(store,source_id='teaching')
    assert calls==['teaching'] and len(result['sources'])==1
    with pytest.raises(capture.CaptureError,match='not found'):capture.sync(store,source_id='missing')


def test_sync_all_refreshes_every_calendar(store, monkeypatch):
    capture.subscribe(store, subscription())
    capture.subscribe(store, subscription(sourceID='other', url='https://other.example.invalid/feed'))
    calls = []
    monkeypatch.setattr(capture, 'download', lambda source, _: (calls.append(source['sourceID']) or feed(component()), {}))
    result = capture.sync(store)
    assert calls == ['teaching', 'other']
    assert [r['sourceID'] for r in result['sources']] == calls
    assert set(capture.read_json(store.directory / 'sync.json')['sources']) == set(calls)


def test_failed_or_unchanged_sync_preserves_overrides(store,monkeypatch):
    capture.subscribe(store,subscription());monkeypatch.setattr(capture,'download',lambda *_:(feed(component()),{}));capture.sync(store)
    dashboard.edit_lesson(store,store.schedule()['events'][0]['id'],{'title':'Local'})
    monkeypatch.setattr(capture,'download',lambda *_:(None,{}));capture.sync(store)
    assert store.schedule()['events'][0]['title']=='Local'
    def fail(*_):raise OSError('unavailable')
    monkeypatch.setattr(capture,'download',fail);assert capture.sync(store)['status']=='SYNC_FAILED'
    assert store.schedule()['events'][0]['title']=='Local'


def test_unsubscribe_cancels_only_its_events_and_preserves_specials(store,monkeypatch):
    capture.subscribe(store,subscription());monkeypatch.setattr(capture,'download',lambda *_:(feed(component()),{}));capture.sync(store)
    dashboard.add_lesson(store,future());dashboard.unsubscribe(store,'teaching')
    assert not store.config()['sources']
    assert next(e for e in store.schedule()['events'] if e['sourceID']=='teaching')['eligibility']=='CANCELLED'
    assert next(e for e in store.schedule()['events'] if e['sourceID']=='special')['eligibility']=='SCHEDULED'


@pytest.mark.parametrize('patch',[{'startsAt':'2026-10-12T12:00:00Z'}, {'recordingEnabled':'false'}, {'title':''}, {'lessonNumber':5}, {'timezone':'Unknown/Zone'}])
def test_invalid_edit_leaves_schedule_and_overrides_untouched(store,patch):
    capture.import_schedule(store,[event()]);before=store.schedule_path.read_bytes()
    with pytest.raises((capture.CaptureError,ValueError)):dashboard.edit_lesson(store,'static:MATH-L01',patch)
    assert store.schedule_path.read_bytes()==before
    assert not (store.directory/'overrides.json').exists()


def test_edit_validation_uses_authoritative_association_for_historical_source_code(store):
    recording_id = 'c' * 32
    old = capture.normalize_event({**event(), 'recordingID': recording_id, 'title': 'MATH 1'}, {'MATH'}, 'teaching')
    old['courseCode'] = 'OBSOLETE'
    atomic_json_write(store.directory / 'associations.json', {'schemaVersion': 1, 'recordings': {
        recording_id: {'courseCode': 'MATH', 'lessonNumber': 1, 'associationState': 'resolved'}}})
    store.save_schedule([old], store.config())
    dashboard.edit_lesson(store, old['id'], {'title': 'Local title'})
    effective = store.schedule()['events'][0]
    assert effective['title'] == 'Local title' and effective['courseCode'] == 'MATH'
    assert store.source_events()[0]['courseCode'] == 'OBSOLETE'


def test_edit_rechecks_overlap_and_can_resolve_it(store):
    capture.import_schedule(store,[event(),event(2,'2026-10-12T12:00:00Z','2026-10-12T13:00:00Z')])
    dashboard.edit_lesson(store,'static:MATH-L02',{'startsAt':'2026-10-12T10:30:00Z','endsAt':'2026-10-12T11:30:00Z'})
    assert all(e['eligibility']=='CONFLICT' for e in store.schedule()['events'])
    dashboard.reset_lesson(store,'static:MATH-L02')
    assert all(e['eligibility']=='SCHEDULED' for e in store.schedule()['events'])


@pytest.mark.parametrize('action',['edit','reset','unsubscribe'])
def test_active_recording_cannot_be_changed(store,monkeypatch,action):
    capture.subscribe(store,subscription());monkeypatch.setattr(capture,'download',lambda *_:(feed(component()),{}));capture.sync(store)
    identity=store.schedule()['events'][0]['id']
    atomic_json_write(store.runtime_path,{'pid':os.getpid(),'updatedAt':capture.iso(datetime.now(UTC)), 'recording':True,'currentSession':{'event':{'id':identity}}})
    with pytest.raises(capture.CaptureError,match='Stop and save'):
        if action=='edit':dashboard.edit_lesson(store,identity,{'title':'Changed'})
        elif action=='reset':dashboard.reset_lesson(store,identity)
        else:dashboard.unsubscribe(store,'teaching')


def test_duplicate_number_past_start_and_bad_title_are_rejected(store):
    dashboard.add_lesson(store,future())
    with pytest.raises(capture.CaptureError,match='already exist'):dashboard.add_lesson(store,future())
    with pytest.raises(capture.CaptureError,match='future'):dashboard.add_lesson(store,event())
    with pytest.raises(capture.CaptureError,match='title'):dashboard.add_lesson(store,future(6,title=''))


def test_dashboard_cli_uses_json_contract_and_add_edit_reset_commands(store,tmp_path,capsys):
    root=str(store.workspace.root);payload=tmp_path/'lesson.json';payload.write_text(json.dumps(future()))
    assert main(['capture','add-lesson','--workspace',root,'--file',str(payload)])==0
    identity=json.loads(capsys.readouterr().out)['eventID']
    payload.write_text(json.dumps({'title':'Edited'}))
    assert main(['capture','edit-lesson','--workspace',root,'--event',identity,'--file',str(payload)])==0;capsys.readouterr()
    assert main(['capture','dashboard','--workspace',root])==0
    value=json.loads(capsys.readouterr().out);assert value['events'][0]['title']=='Edited'
    assert main(['capture','reset-lesson','--workspace',root,'--event',identity])==0


def test_dashboard_response_polling_keeps_ack_and_skips_corrupt_response(store):
    response=store.directory/'responses';response.mkdir(exist_ok=True)
    atomic_json_write(response/'one.json',{'requestID':'a'*32,'status':'RECORDING','message':'Started'})
    (response/'bad.json').write_text('not json')
    atomic_json_write(response/'empty.json',{})
    value=dashboard.dashboard(store)
    assert value['responses']==[{'requestID':'a'*32,'status':'RECORDING','message':'Started'}]


def test_concurrent_calendar_refresh_and_local_edit_both_survive(store,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    capture.subscribe(store,subscription())
    monkeypatch.setattr(capture,'download',lambda *_:(feed(component()),{}));capture.sync(store)
    identity=store.schedule()['events'][0]['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs=[pool.submit(capture.sync,store),pool.submit(dashboard.edit_lesson,store,identity,{'title':'Concurrent local'})]
        for job in jobs:job.result(timeout=10)
    assert store.schedule()['events'][0]['title']=='Concurrent local'
