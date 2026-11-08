"""Source selection and import protect originals and immutable job inputs."""
import json
import os
from pathlib import Path
import shutil
import pytest
from mynotes import audio_sources as sources, recording_library as library, transcription_queue as queue, capture
from test_recording_library import setup, audio, transcribe
from test_capture import event
from mynotes.capture_courses import add_course


def test_import_copies_inspects_and_deduplicates_without_overwrite(setup, tmp_path):
    ws, folder, _ = setup
    source = audio(tmp_path / 'Outside')
    original = source.read_bytes()
    result = sources.import_audio(ws, [str(source)])
    item = queue.library(ws)[0]
    assert result['imported'][0]['recording_id'] == item['id']
    assert item['origin'] == 'Importato' and item['duration'] == 4
    assert item['can_transcribe'] and item['source_selected']
    assert Path(item['path']) != source and source.read_bytes() == original
    again = sources.import_audio(ws, [str(source)])
    assert again['imported'][0]['duplicate'] and len(queue.library(ws)) == 1
    assert not list(folder.glob('.import-*'))


def test_distinct_homonymous_audio_never_overwrites(setup, tmp_path):
    ws, folder, _ = setup
    first = audio(tmp_path / 'First'); second = audio(tmp_path / 'Second')
    content = bytearray(second.read_bytes()); content[-2:] = b'\x01\x00'; second.write_bytes(content)
    sources.import_audio(ws, [str(first), str(second)])
    values = queue.library(ws)
    assert len(values) == 2 and len({i['path'] for i in values}) == 2
    assert first.exists() and second.exists()


def paired(ws, folder):
    code = add_course(ws, 'Mathematics')['course']['code']
    store = capture.CaptureStore(ws)
    capture.configure(store, {})
    capture.import_schedule(store, [event(courseCode=code)])
    a = audio(folder, f'{code}_L01.wav');b = audio(folder, f'{code}-L01.wav')
    content = bytearray(b.read_bytes());content[-2:] = b'\x02\x00';b.write_bytes(content)
    library.sync(ws)
    return queue.library(ws)


def test_ambiguous_sources_require_choice_for_single_and_bulk_enqueue(setup):
    ws, folder, _ = setup
    values = paired(ws, folder)
    assert len({i['lesson_key'] for i in values}) == 1
    assert all(i['source_selection_required'] and not i['can_transcribe'] for i in values)
    assert queue.enqueue(ws)['added'] == 0
    assert queue.enqueue(ws, recording_id=values[0]['id'])['added'] == 0
    sources.choose(ws, values[1]['id'], 'prefer-audio')
    assert queue.enqueue(ws)['added'] == 1
    assert queue.status(ws)['jobs'][0]['recording_id'] == values[1]['id']
    with pytest.raises(ValueError, match='coda'):
        sources.choose(ws, values[0]['id'], 'prefer-audio')


def test_choice_and_exclusion_survive_scans_and_catalogue_rebuild(setup):
    ws, folder, _ = setup
    a, b = paired(ws, folder)
    sources.choose(ws, a['id'], 'prefer-audio')
    sources.choose(ws, b['id'], 'exclude-audio')
    with queue.database(ws) as conn:conn.execute('DELETE FROM recordings')
    library.sync(ws)
    by_id = {i['id']:i for i in queue.library(ws)}
    assert by_id[a['id']]['source_selected'] and by_id[b['id']]['source_excluded']
    sources.choose(ws, a['id'], 'exclude-audio')
    assert all(not i['source_selected'] for i in queue.library(ws))
    sources.choose(ws, a['id'], 'restore-audio')
    assert next(i for i in queue.library(ws) if i['id']==a['id'])['source_selected']


def test_import_into_calendar_lesson_joins_existing_source(setup, tmp_path):
    ws, folder, _ = setup
    a, b = paired(ws, folder)
    sources.choose(ws, a['id'], 'prefer-audio')
    source = audio(tmp_path / 'External')
    content=bytearray(source.read_bytes());content[-2:]=b'\x03\x00';source.write_bytes(content)
    event_id = capture.CaptureStore(ws).source_events()[0]['id']
    result = sources.import_audio(ws, [str(source)], event_id=event_id)
    imported = next(i for i in queue.library(ws) if i['id']==result['imported'][0]['recording_id'])
    assert imported['lesson_key'] == a['lesson_key'] and imported['lecture_id'] == a['lecture_id']
    assert not imported['source_selected']


def test_prepare_trash_excludes_without_deleting_audio_or_transcript(setup):
    ws, folder, _ = setup
    original = audio(folder);library.sync(ws);item=queue.library(ws)[0]
    result=sources.choose(ws,item['id'],'prepare-trash')
    assert Path(result['path']).exists() and not result['was_excluded']
    assert queue.enqueue(ws)['added']==0
    sources.choose(ws,item['id'],'restore-audio')
    assert queue.library(ws)[0]['can_transcribe']


def test_import_rejects_invalid_symlink_or_still_copying_inputs(setup, tmp_path):
    ws, folder, _ = setup
    broken=tmp_path/'bad.wav';broken.write_bytes(b'incomplete')
    with pytest.raises(ValueError,match='leggibile'):sources.import_audio(ws,[str(broken)])
    source=audio(tmp_path/'External');link=tmp_path/'alias.wav';link.symlink_to(source)
    with pytest.raises(ValueError,match='collegamento'):sources.import_audio(ws,[str(link)])
    cfg=ws.configuration_store().load();cfg['ingestion']['stable_seconds']=60;ws.configuration_store().save(cfg)
    with pytest.raises(ValueError,match='copia'):sources.import_audio(ws,[str(source)])
    assert source.exists() and not list(folder.glob('.import-*'))


def test_direct_transcription_respects_preferred_source(setup):
    from mynotes.transcription import run_transcription
    from mynotes.asr import ASRError
    ws, folder, engine = setup
    a, b = paired(ws, folder)
    sources.choose(ws, a['id'], 'prefer-audio')
    with pytest.raises(ASRError, match='SOURCE_SELECTION'):
        run_transcription(ws, recording_id=b['id'], engines={'argmax': engine})
    assert engine.calls == 0


def test_import_failure_cleans_temporary_copy_and_preserves_original(setup,tmp_path,monkeypatch):
    ws,folder,_=setup
    source=audio(tmp_path/'Outside');before=source.read_bytes()
    def fail(_): raise OSError('Synthetic disk failure')
    monkeypatch.setattr(os,'fsync',fail)
    with pytest.raises(OSError,match='disk failure'):sources.import_audio(ws,[str(source)])
    assert source.read_bytes()==before and not list(folder.glob('.import-*'))
    assert not queue.library(ws)


def test_cloud_audio_is_visible_but_not_imported_until_downloaded(setup,tmp_path,monkeypatch):
    ws,folder,_=setup
    source=audio(tmp_path/'Cloud')
    monkeypatch.setattr(library,'local_audio',lambda path:False)
    with pytest.raises(ValueError,match='cloud'):sources.import_audio(ws,[str(source)])
    assert source.exists()


def test_associate_external_source_to_occupied_lesson_keeps_both(setup):
    ws,folder,_=setup
    a,b=paired(ws,folder)
    extra=audio(folder,'Another phone recording.wav');library.sync(ws)
    candidate=next(i for i in queue.library(ws) if i['path']==str(extra))
    sources.associate(ws,candidate['id'],course=a['course_code'],lesson=1)
    values=queue.library(ws)
    assert len(values)==3 and len({i['lesson_key'] for i in values})==1
    assert all(i['source_selection_required'] for i in values)
    sources.choose(ws,candidate['id'],'prefer-audio')
    assert next(i for i in queue.library(ws) if i['id']==candidate['id'])['source_selected']
    assert all(Path(i['path']).exists() for i in values)


def test_selected_source_is_used_for_note_preparation_lookup(setup):
    from mynotes.capture_associations import linked_recording
    ws,folder,_=setup
    a,b=paired(ws,folder)
    sources.choose(ws,b['id'],'prefer-audio')
    assert linked_recording(ws,b['lecture_id'])['id']==b['id']


def test_import_on_volume_without_hardlink_support(setup,tmp_path,monkeypatch):
    import errno
    ws,folder,_=setup;source=audio(tmp_path/'Outside')
    def no_links(*args,**kwargs):raise OSError(errno.EOPNOTSUPP,'Synthetic cloud volume')
    monkeypatch.setattr(os,'link',no_links)
    result=sources.import_audio(ws,[str(source)])
    item=queue.library(ws)[0]
    assert result['imported'][0]['recording_id']==item['id'] and Path(item['path']).read_bytes()==source.read_bytes()
    assert item['can_transcribe'] and source.exists()


def test_lecture_cli_uses_explicit_external_source_among_alternatives(setup):
    from mynotes.transcription import run_transcription, transcription_status
    ws, folder, engine = setup
    a, b = paired(ws, folder)
    sources.choose(ws, b['id'], 'prefer-audio')
    result = run_transcription(ws, lecture_id=b['lecture_id'], engines={'argmax': engine})
    assert result['recording_id'] == b['id'] and engine.calls == 1
    assert transcription_status(ws, b['lecture_id'])['recording_id'] == b['id']


def test_readonly_poll_does_not_hash_audio_with_changed_metadata(setup, monkeypatch):
    ws, folder, _ = setup
    audio(folder); library.sync(ws)
    identifier = queue.library(ws)[0]['id']
    result = transcribe(ws, identifier)
    assert result['status'] == 'COMPLETED'
    item = queue.library(ws)[0]; path = Path(item['path']); stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 3_000_000))
    def no_hash(*args): raise AssertionError('Polling must not read full audio')
    monkeypatch.setattr(queue, 'file_sha256', no_hash)
    monkeypatch.setattr(library, 'file_sha256', no_hash)
    observed = queue.library(ws)[0]
    assert observed['transcription_status'] == 'OUTDATED'
    assert Path(observed['export_text']).is_file() and path.is_file()
