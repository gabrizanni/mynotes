"""Lesson lifecycle uses synthetic local audio; no account, provider or user data."""
from copy import deepcopy
import json
from pathlib import Path
import pytest

from mynotes import lesson_registry as lessons, audio_sources as sources, capture, transcription_queue as queue
from mynotes.capture_courses import add_course
from test_recording_library import setup, audio
from test_capture import event


def course(ws):
    return add_course(ws, 'Example course')['course']['code']


def make(ws, folder, number=1):
    code = course(ws)
    file = audio(folder, f'{code}_L{number:02d}.wav')
    from mynotes.recording_library import sync
    sync(ws)
    return lessons.snapshot(ws), file


def test_readonly_projection_has_stable_identity_before_and_after_save(setup):
    ws, folder, _ = setup
    value, file = make(ws, folder)
    first = next(iter(value['lessons'].values()))
    assert not lessons.path(ws).exists()
    assert lessons.listing(ws)[0]['id'] == first['id']
    lessons.save(ws, value)
    assert queue.library(ws)[0]['lesson_key'] == first['id']
    assert lessons.listing(ws)[0]['id'] == first['id']


def test_renumber_preview_no_write_and_stale_commit_rejected(setup):
    ws, folder, _ = setup
    value, file = make(ws, folder)
    row = next(iter(value['lessons'].values()))
    changes = [{'id': row['id'], 'number': 4, 'before': 1}]
    lessons.renumber(ws, changes, preview=True)
    assert not lessons.path(ws).exists()
    lessons.renumber(ws, changes)
    projected = queue.library(ws)[0]
    assert projected['lecture_id'].endswith('-L04') and file.exists()
    with pytest.raises(ValueError, match='cambiata'): lessons.renumber(ws, changes)
    assert lessons.listing(ws)[0]['id'] == row['id']


def test_bulk_shift_checks_all_collisions_before_saving(setup):
    ws, folder, _ = setup
    value, _ = make(ws, folder)
    code = course(ws); audio(folder, f'{code}_L02.wav')
    rows = sorted(lessons.listing(ws), key=lambda r:r['number'])
    with pytest.raises(ValueError, match='occupata'):
        lessons.renumber(ws, [{'id': rows[0]['id'], 'number':2}])
    assert not lessons.path(ws).exists()
    lessons.renumber(ws,[{'id': r['id'], 'number':r['number']+1} for r in rows])
    assert sorted(r['number'] for r in lessons.listing(ws)) == [2,3]


def test_hidden_lesson_survives_rescan_and_restore(setup):
    ws, folder, _ = setup
    value, file = make(ws,folder); row = next(iter(value['lessons'].values()))
    lessons.action(ws, {'id':row['id'], 'action':'hide'})
    assert file.exists() and lessons.listing(ws)[0]['hidden']
    assert not queue.library(ws)[0]['can_transcribe']
    from mynotes.recording_library import sync
    sync(ws)
    assert lessons.listing(ws)[0]['hidden']
    lessons.action(ws, {'id':row['id'],'action':'restore'})
    assert not lessons.listing(ws)[0]['hidden']


def test_not_recorded_is_past_event_without_audio_and_reversible(setup):
    ws, folder, _ = setup; code = course(ws)
    store = capture.CaptureStore(ws); capture.configure(store,{})
    capture.import_schedule(store,[event(courseCode=code,startsAt='2020-01-01T08:00:00Z',endsAt='2020-01-01T09:00:00Z')])
    row = lessons.listing(ws)[0]
    lessons.action(ws,{'id':row['id'],'action':'not-recorded'})
    assert lessons.listing(ws)[0]['not_recorded']
    original = audio(folder/'outside');sources.import_audio(ws,[str(original)],event_id=row['event_ids'][0])
    assert not next(r for r in lessons.listing(ws) if r['id']==row['id'])['not_recorded']
    with pytest.raises(ValueError,match='ha un audio'):
        lessons.action(ws,{'id':row['id'],'action':'not-recorded'})


def test_new_competing_audio_invalidates_prior_preference(setup,tmp_path):
    ws, folder, _ = setup
    value, _ = make(ws, folder); row=next(iter(value['lessons'].values()))
    lessons.save(ws,value)
    original=queue.library(ws)[0];sources.choose(ws,original['id'],'prefer-audio')
    external=audio(tmp_path/'outside');data=bytearray(external.read_bytes());data[-2:]=b'\x02\x00';external.write_bytes(data)
    result=lessons.import_items(ws,[{'path':str(external),'target':row['id']}])
    found=queue.library(ws)
    assert len({i['lesson_key'] for i in found})==1
    assert all(i['source_selection_required'] and not i['can_transcribe'] for i in found)
    sources.choose(ws,result['imported'][0]['recording_id'],'prefer-audio')
    assert sum(i['source_selected'] for i in queue.library(ws))==1


def test_import_without_number_never_inherits_filename_number(setup,tmp_path):
    ws, folder, _ = setup; code=course(ws)
    external=audio(tmp_path/'outside',f'{code}_L99.wav')
    sources.import_audio(ws,[str(external)],course=code)
    item=queue.library(ws)[0]
    assert item['course_code']==code and item['lecture_id'] is None


def test_moving_audio_preserves_target_identity_and_legacy_associate(setup,tmp_path):
    ws, folder, _ = setup
    value,_=make(ws,folder);lessons.save(ws,value);target=lessons.listing(ws)[0]
    external=audio(tmp_path/'outside');data=bytearray(external.read_bytes());data[-2:]=b'\x03\x00';external.write_bytes(data)
    result=sources.import_audio(ws,[str(external)])
    identifier=result['imported'][0]['recording_id']
    sources.associate(ws,identifier,course=target['course'],lesson=target['number'])
    assert next(i for i in queue.library(ws) if i['id']==identifier)['lesson_key']==target['id']


def test_delete_inventory_does_not_include_originals_or_shared_word(setup,tmp_path):
    ws,folder,_=setup;external=audio(tmp_path/'outside');code=course(ws)
    sources.import_audio(ws,[str(external)],course=code,lesson=1)
    row=lessons.listing(ws)[0]; notebook=folder/'Shared.docx';notebook.write_bytes(b'shared')
    files=lessons.inventory(ws,row)
    assert str(external) not in files and str(notebook) not in files
    with pytest.raises(ValueError,match='cambiati'):
        lessons.action(ws,{'id':row['id'],'action':'delete','files':[]})
    lessons.action(ws,{'id':row['id'],'action':'delete','files':files})
    assert external.exists() and notebook.exists()
    assert lessons.load(ws)['lessons'][row['id']]['pending_trash']==files
    with pytest.raises(ValueError,match='Completa'):
        lessons.action(ws,{'id':row['id'],'action':'restore'})
    lessons.trash_completed(ws,row['id'],files)
    lessons.action(ws,{'id':row['id'],'action':'restore'})


def test_renumbered_artifacts_block_generation_and_keep_receipts(setup):
    ws,folder,_=setup;value,_=make(ws,folder);row=next(iter(value['lessons'].values()))
    run=ws.runs_dir/f"{row['course']}-L01";run.mkdir(parents=True);receipt=run/'receipt.json';receipt.write_text('{}')
    lessons.renumber(ws,[{'id':row['id'],'number':2}])
    assert receipt.read_text()=='{}'
    with pytest.raises(ValueError,match='rinumerata'):
        lessons.assert_generation_allowed(ws,row['course'],2)


def test_group_edit_rejects_active_transcription(setup):
    ws,folder,_=setup;value,_=make(ws,folder);row=next(iter(value['lessons'].values()))
    queue.enqueue(ws,recording_id=row['recording_ids'][0])
    with pytest.raises(ValueError,match='coda'):
        lessons.renumber(ws,[{'id':row['id'],'number':2}])


def test_calendar_removal_and_renumber_survive_schedule_refresh(setup):
    ws,folder,_=setup;code=course(ws);store=capture.CaptureStore(ws);capture.configure(store,{})
    raw=event(courseCode=code);capture.import_schedule(store,[raw]);row=lessons.listing(ws)[0]
    lessons.renumber(ws,[{'id':row['id'],'number':4}])
    capture.import_schedule(store,[raw])
    assert store.schedule()['events'][0]['lessonNumber']==4
    lessons.action(ws,{'id':row['id'],'action':'hide'})
    capture.import_schedule(store,[raw])
    assert store.schedule()['events'][0]['eligibility']=='CANCELLED'


def test_external_audio_discovered_in_collection_is_never_bulk_deleted(setup):
    ws, folder, _ = setup
    value, original = make(ws, folder)
    row = next(iter(value['lessons'].values()))
    assert original.exists() and str(original) not in lessons.inventory(ws, row)


def test_resume_delete_uses_pending_files_after_partial_completion(setup,tmp_path):
    ws, folder, _ = setup; code = course(ws)
    a = audio(tmp_path/'a'); b = audio(tmp_path/'b')
    data=bytearray(b.read_bytes());data[-2:]=b'\x09\x00';b.write_bytes(data)
    lessons.import_items(ws,[{'path':str(a),'course':code,'lesson':1},{'path':str(b),'course':code,'lesson':1}])
    row=next(r for r in lessons.listing(ws) if r['number']==1)
    files=lessons.action(ws,{'id':row['id'],'action':'inventory'})['files']
    assert len(files)==2
    lessons.action(ws,{'id':row['id'],'action':'delete','files':files})
    Path(files[0]).unlink()
    lessons.trash_completed(ws,row['id'],[files[0]])
    remaining=lessons.action(ws,{'id':row['id'],'action':'inventory'})['files']
    assert remaining == [files[1]]
    assert lessons.action(ws,{'id':row['id'],'action':'delete','files':remaining})['files']==remaining


def test_repeated_renumber_preserves_review_and_blocks_original_number(setup):
    ws, folder, _ = setup
    value, _ = make(ws,folder);row=next(iter(value['lessons'].values()))
    run=ws.runs_dir/f"{row['course']}-L01";run.mkdir(parents=True)
    lessons.renumber(ws,[{'id':row['id'],'number':2}])
    lessons.renumber(ws,[{'id':row['id'],'number':3}])
    assert lessons.load(ws)['lessons'][row['id']]['revision_required']
    for number in (1,3):
        with pytest.raises(ValueError): lessons.assert_generation_allowed(ws,row['course'],number)


def test_restore_original_number_clears_review_guard(setup):
    ws, folder, _ = setup
    value, _ = make(ws, folder); row = next(iter(value['lessons'].values()))
    (ws.runs_dir / f"{row['course']}-L01").mkdir(parents=True)
    lessons.renumber(ws,[{'id':row['id'],'number':2}])
    lessons.renumber(ws,[{'id':row['id'],'number':1}])
    assert not lessons.load(ws)['lessons'][row['id']]['revision_required']
    lessons.assert_generation_allowed(ws,row['course'],1)


def test_single_file_retry_keeps_lesson_visible(setup,tmp_path):
    ws,folder,_=setup;source=audio(tmp_path/'outside');code=course(ws)
    sources.import_audio(ws,[str(source)],course=code,lesson=1)
    row=lessons.listing(ws)[0];files=lessons.inventory(ws,row)
    lessons.action(ws,{'id':row['id'],'action':'delete-file','path':files[0]})
    lessons.action(ws,{'id':row['id'],'action':'delete','files':files})
    assert not lessons.load(ws)['lessons'][row['id']]['hidden']


def test_renumber_keeps_original_material_number(setup):
    ws,folder,_=setup;value,_=make(ws,folder);row=next(iter(value['lessons'].values()))
    lessons.renumber(ws,[{'id':row['id'],'number':5}])
    assert lessons.source_number(ws,row['course'],5)==1


def test_structure_edit_checks_generation_lease(setup):
    import fcntl
    ws,folder,_=setup;value,_=make(ws,folder);row=next(iter(value['lessons'].values()))
    run=ws.runs_dir/f"{row['course']}-L01";run.mkdir(parents=True)
    with (run/'.generation.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        with pytest.raises(ValueError,match='pubblicazione attiva'):
            lessons.renumber(ws,[{'id':row['id'],'number':4}])


def test_cli_read_and_edit_use_stable_lesson_ids(setup,tmp_path,capsys):
    from mynotes.cli import main
    ws,folder,_=setup;value,_=make(ws,folder)
    assert main(['transcription-queue','lessons','--workspace',str(ws.root),'--json'])==0
    row=json.loads(capsys.readouterr().out)['lessons'][0]
    payload=tmp_path/'change.json';payload.write_text(json.dumps({'action':'renumber','changes':[{'id':row['id'],'number':7}]}))
    assert main(['transcription-queue','lesson-edit','--workspace',str(ws.root),'--file',str(payload),'--json'])==0
    assert json.loads(capsys.readouterr().out)['status']=='OK'
    assert lessons.load(ws)['lessons'][row['id']]['number']==7


def test_collection_date_matches_saved_calendar_edit_without_mutation(setup):
    from mynotes import capture_dashboard
    ws,folder,_=setup;code=course(ws);store=capture.CaptureStore(ws);capture.configure(store,{})
    capture.import_schedule(store,[event(courseCode=code,startsAt='2026-10-31T12:30:00Z',endsAt='2026-10-31T14:00:00Z',timezone='Asia/Kolkata')])
    before=lessons.snapshot(ws);row=next(iter(before['lessons'].values()));lessons.save(ws,before)
    capture_dashboard.edit_lesson(store,row['event_ids'][0],{'startsAt':'2026-11-06T12:30:00Z','endsAt':'2026-11-06T14:00:00Z','title':'Updated lecture'})
    original=lessons.path(ws).read_bytes()
    effective=lessons.snapshot(ws)['lessons'][row['id']]
    assert effective['date']=='2026-11-06T12:30:00Z'
    assert effective['title']=='Updated lecture'
    assert lessons.path(ws).read_bytes()==original
    assert store.source_events()[0]['startsAt']=='2026-10-31T12:30:00Z'
