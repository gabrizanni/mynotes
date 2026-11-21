"""Lesson lifecycle uses synthetic local audio; no account, provider or user data."""
from copy import deepcopy
import json
from pathlib import Path
import wave
import pytest

from mynotes import lesson_registry as lessons, audio_sources as sources, capture, transcription_queue as queue
from mynotes.capture_courses import add_course
from test_recording_library import setup, audio
from test_capture import event


def course(ws):
    return add_course(ws, 'Example course')['course']['code']


def test_old_number_does_not_capture_new_audio_after_renumber(setup):
    ws, folder, _ = setup
    code = course(ws)
    store = capture.CaptureStore(ws)
    capture.configure(store, {})
    capture.import_schedule(store, [event(courseCode=code, lessonNumber=12, title=f'{code} 12')])
    original = lessons.listing(ws)[0]
    lessons.renumber(ws, [{'id': original['id'], 'number': 13}])
    audio(folder, f'{code}_L12.wav')
    rows = lessons.listing(ws)
    current = next(row for row in rows if row['id'] == original['id'])
    assert current['number'] == 13
    assert current['recording_ids'] == []
    assert current['title'] == 'Lezione 13'
    assert any(row['recording_ids'] and row['number'] is None for row in rows)


def test_calendar_filename_alone_cannot_claim_recording_day(setup):
    ws, folder, _ = setup
    code = course(ws)
    store = capture.CaptureStore(ws)
    capture.configure(store, {})
    capture.import_schedule(store, [event(courseCode=code)])
    audio(folder, f'{code}_L01.wav')
    item = queue.library(ws)[0]
    assert item['lecture_id'] is None
    assert item['association_conflict']
    from mynotes.recording_library import save
    item['recorded_at'] = store.source_events()[0]['startsAt']
    save(ws, item)
    assert queue.library(ws)[0]['lecture_id'] == f'{code}-L01'


def test_unified_update_preview_and_stale_commit(setup):
    ws, folder, _ = setup
    value, _ = make(ws, folder)
    row = next(iter(value['lessons'].values()))
    patch = {'number': 4, 'date': '2026-11-06T18:00:00+05:30',
             'end': '2026-11-06T19:30:00+05:30', 'timezone': 'Asia/Kolkata'}
    payload = {'action': 'update', 'id': row['id'], 'patch': patch}
    preview = lessons.update_lesson(ws, {**payload, 'preview': True})
    assert not lessons.path(ws).exists()
    lessons.update_lesson(ws, {**payload, 'token': preview['token']})
    updated = lessons.listing(ws)[0]
    assert updated['id'] == row['id'] and updated['number'] == 4
    assert updated['date'] == patch['date']
    with pytest.raises(ValueError, match='cambiati'):
        lessons.update_lesson(ws, {**payload, 'token': preview['token']})


def test_unified_conflict_swap_is_atomic(setup):
    ws, folder, _ = setup
    value, _ = make(ws, folder)
    code = next(iter(value['lessons'].values()))['course']
    audio(folder, f'{code}_L02.wav')
    rows = sorted(lessons.listing(ws), key=lambda row: row['number'])
    payload = {'action': 'update', 'id': rows[0]['id'], 'patch': {'number': 2}}
    assert lessons.update_lesson(ws, {**payload, 'preview': True})['status'] == 'CONFLICT'
    payload['conflict'] = 'swap'
    preview = lessons.update_lesson(ws, {**payload, 'preview': True})
    lessons.update_lesson(ws, {**payload, 'token': preview['token']})
    numbers = {row['id']: row['number'] for row in lessons.listing(ws)}
    assert numbers[rows[0]['id']] == 2 and numbers[rows[1]['id']] == 1


def test_detach_audio_survives_rescan(setup):
    ws, folder, _ = setup
    value, _ = make(ws, folder)
    row = next(iter(value['lessons'].values()))
    lessons.save(ws, value)
    recording = row['recording_ids'][0]
    lessons.move_audio(ws, recording, None)
    for _ in range(2):
        current = next(row for row in lessons.listing(ws) if recording in row['recording_ids'])
        assert current['course'] is None and current['number'] is None


def test_manual_past_lesson_can_be_created_and_cancelled(setup):
    ws, _, _ = setup
    payload = {'action': 'create', 'patch': {'course': course(ws), 'number': 10,
               'date': '2026-10-03T18:00:00+05:30', 'title': 'Lezione recuperata'}}
    preview = lessons.update_lesson(ws, {**payload, 'preview': True})
    result = lessons.update_lesson(ws, {**payload, 'new_id': preview['new_id'], 'token': preview['token']})
    lessons.action(ws, {'id': result['id'], 'action': 'cancelled'})
    assert lessons.listing(ws)[0]['cancelled']


def test_renaming_managed_copy_preserves_source_identity_and_original(setup, tmp_path):
    ws, _, _ = setup
    source = audio(tmp_path / 'Outside')
    result = sources.import_audio(ws, [str(source)], course=course(ws), lesson=1)
    row = lessons.listing(ws)[0]
    payload = {'action': 'update', 'id': row['id'], 'patch': {'number': 2}}
    preview = lessons.update_lesson(ws, {**payload, 'preview': True})
    paths = [move['path'] for move in preview['renames']]
    assert paths and str(source) not in paths
    lessons.update_lesson(ws, {**payload, 'token': preview['token'], 'rename_paths': paths})
    current = queue.library(ws)[0]
    assert current['id'] == result['imported'][0]['recording_id']
    assert source.exists()
    assert Path(current['path']).name.startswith(row['course'] + '-L02')
    assert not lessons.load(ws)['lessons'][row['id']]['pending_renames']


def test_merge_retains_both_recordings_and_one_visible_lesson(setup):
    ws, folder, _ = setup
    value, _ = make(ws, folder)
    code = next(iter(value['lessons'].values()))['course']
    audio(folder, f'{code}_L02.wav')
    rows = sorted(lessons.listing(ws), key=lambda row: row['number'])
    payload = {'action': 'update', 'id': rows[0]['id'], 'patch': {'number': 2}, 'conflict': 'merge'}
    preview = lessons.update_lesson(ws, {**payload, 'preview': True})
    lessons.update_lesson(ws, {**payload, 'token': preview['token']})
    visible = [row for row in lessons.listing(ws) if not row['hidden']]
    assert len(visible) == 1 and len(visible[0]['recording_ids']) == 2


def test_interrupted_rename_can_resume_without_losing_recording(setup, tmp_path, monkeypatch):
    from mynotes import recording_library
    ws, _, _ = setup
    source = audio(tmp_path / 'Outside')
    sources.import_audio(ws, [str(source)], course=course(ws), lesson=1)
    row = lessons.listing(ws)[0]
    payload = {'action': 'update', 'id': row['id'], 'patch': {'number': 2}}
    preview = lessons.update_lesson(ws, {**payload, 'preview': True})
    move = recording_library._move_original
    def interrupted(*args):
        raise OSError('Simulated interruption')
    monkeypatch.setattr(recording_library, '_move_original', interrupted)
    with pytest.raises(OSError, match='interruption'):
        lessons.update_lesson(ws, {**payload, 'token': preview['token'],
                                  'rename_paths': [item['path'] for item in preview['renames']]})
    assert lessons.load(ws)['lessons'][row['id']]['pending_renames']
    monkeypatch.setattr(recording_library, '_move_original', move)
    lessons.resume_renames(ws, row['id'])
    assert source.exists() and len(queue.library(ws)) == 1
    assert not lessons.load(ws)['lessons'][row['id']]['pending_renames']


def test_verified_transcript_unblocks_new_number_with_new_source_revision(setup):
    from test_recording_library import transcribe
    ws, folder, _ = setup
    value, _ = make(ws, folder)
    row = next(iter(value['lessons'].values()))
    transcribe(ws, row['recording_ids'][0])
    row = lessons.listing(ws)[0]
    lessons.renumber(ws, [{'id': row['id'], 'number': 2}])
    current = lessons.listing(ws)[0]
    assert current['revision_required']
    lessons.action(ws, {'id': row['id'], 'action': 'review-sources', 'transcript': current['transcript_ids'][0]})
    lessons.assert_generation_allowed(ws, row['course'], 2)
    assert lessons.selected_transcript(ws, lessons.listing(ws)[0])['lecture_id'].endswith('L02')
    assert lessons.source_revision(ws, f"{row['course']}-L02")


def test_course_profile_reads_current_effective_preferences_without_writing(setup):
    ws, _, _ = setup
    code = course(ws)
    other = add_course(ws, 'Other course')['course']['code']
    store = ws.configuration_store()
    cfg = store.load()
    cfg['style']['profile']['writing_style'] = 'detailed'
    cfg['style']['freeform_preferences'] = ''
    cfg['style']['note_instructions'] = {'examples': 'Usa esempi', 'overview': 'Aggiungi un riepilogo'}
    item = next(item for item in cfg['courses'] if item['code'] == code)
    item['style']['image_limit'] = 2
    item['context'] = 'Testo originale'
    item['preferences']['note_instructions'] = {'examples': 'Usa esempi numerici'}
    store.save(cfg)
    before = store.load()
    result = lessons.profile(ws, code)
    assert result['course']['context'] == 'Testo originale'
    assert result['notes']['effective'] == {'examples': 'Usa esempi numerici', 'overview': 'Aggiungi un riepilogo'}
    assert result['notes']['own'] == {'examples': 'Usa esempi numerici'}
    assert result['notes']['style']['effective']['writing_style'] == 'detailed'
    assert result['notes']['style']['effective']['image_limit'] == 2
    assert lessons.profile(ws, other)['notes']['effective']['examples'] == 'Usa esempi'
    assert store.load() == before


def test_cli_enqueues_only_explicit_ready_sources_across_two_courses(setup, monkeypatch, capsys):
    from mynotes.cli import main

    ws, folder, _ = setup
    codes = (course(ws), add_course(ws, 'Other course')['course']['code'])
    lessons_by_course = [(codes[0], 1), (codes[0], 2), (codes[0], 3), (codes[1], 1), (codes[1], 2)]
    for index, (code, number) in enumerate(lessons_by_course):
        path = folder / f'{code}_L{number:02d}.wav'
        with wave.open(str(path), 'wb') as out:
            out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            out.writeframes(bytes([index, 0]) * 64000)
    ids = [item['id'] for item in queue.library(ws, course=codes[0])]
    assert len(ids) == 3
    assert queue.enqueue(ws, recording_id=ids[0])['added'] == 1
    monkeypatch.setattr(queue, 'launch_runner', lambda _: None)
    flags = [part for identifier in ids + ['missing', ids[1]] for part in ('--ready-recording', identifier)]
    assert main(['transcription-queue', 'enqueue', '--workspace', str(ws.root), *flags]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['added'] == 2
    assert {item['reason'] for item in result['skipped']} == {'QUEUED', 'SOURCE_MISSING'}
    assert {job['recording_id'] for job in queue.status(ws)['jobs']} == set(ids)
    assert [item['transcription_status'] for item in queue.library(ws, course=codes[1])] == ['NOT_STARTED'] * 2


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


def test_failed_single_file_trash_releases_lesson_without_hiding_it(setup, tmp_path):
    ws, _, _ = setup
    external = audio(tmp_path / 'outside')
    sources.import_audio(ws, [str(external)], course=course(ws), lesson=1)
    row = lessons.listing(ws)[0]
    managed = Path(lessons.inventory(ws, row)[0])
    before = managed.read_bytes()
    lessons.action(ws, {'id': row['id'], 'action': 'delete-file', 'path': str(managed)})
    with pytest.raises(ValueError, match='annullare'):
        lessons.action(ws, {'id': row['id'], 'action': 'cancel-file-trash', 'path': str(external)})
    lessons.action(ws, {'id': row['id'], 'action': 'cancel-file-trash', 'path': str(managed)})
    current = lessons.load(ws)['lessons'][row['id']]
    assert not current['hidden']
    assert 'pending_trash' not in current and 'pending_trash_mode' not in current
    assert managed.read_bytes() == before


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
