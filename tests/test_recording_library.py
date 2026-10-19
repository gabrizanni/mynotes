"""End-to-end original/catalogue/export lifecycle using synthetic local audio."""
import json
import os
from pathlib import Path
import uuid
import wave

import pytest

from mynotes import asr, capture, capture_associations, recording_library as library, transcription_queue as queue
from mynotes.capture_courses import add_course
from mynotes.transcription import run_transcription
from mynotes.utils import atomic_json_write, file_sha256
from mynotes.workspace import Workspace
from test_transcription_reliability import Fake


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('MYNOTES_CAPTURE_HOME', str(tmp_path / 'native-private'))
    ws = Workspace(tmp_path / 'private'); ws.initialize()
    cfg = ws.configuration_store().load();cfg['ingestion']['stable_seconds'] = 0;ws.configuration_store().save(cfg)
    monkeypatch.setattr('mynotes.onboarding.managed_tools', lambda: {})
    monkeypatch.setattr('mynotes.onboarding.documents_root', lambda: tmp_path / 'documents')
    folder = tmp_path / 'Original audio'
    library.set_folder(ws, folder)
    engine = Fake();engine.name = 'argmax'
    monkeypatch.setattr(asr, 'default_engines', lambda *a: {'argmax': engine})
    return ws, folder, engine


def audio(folder, name='A lecture from my phone.wav'):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(b'\0\0' * 64000)
    return path


def transcribe(ws, identifier):
    queue.enqueue(ws, recording_id=identifier)
    job = next(j for j in queue.status(ws)['jobs'] if j['recording_id'] == identifier)
    with queue.database(ws) as conn:conn.execute("UPDATE jobs SET status='RUNNING',pid=? WHERE id=?", (os.getpid(), job['id']))
    return queue.run_job(ws, job['id'])


def test_arbitrary_name_visible_readonly_and_stable_identity_after_finder_rename(setup):
    ws, folder, _ = setup
    source = audio(folder)
    item = queue.library(ws)[0]
    assert item['lecture_id'] is None and 'enqueue' in item['actions']
    library.sync(ws)
    renamed = source.with_name('Renamed original.wav');source.rename(renamed)
    new = queue.library(ws)[0]
    assert new['id'] == item['id'] and new['path'] == str(renamed) and new['title'] == 'Renamed original'
    library.sync(ws);renamed.unlink()
    assert queue.library(ws) == []
    library.sync(ws)
    assert library.records(ws) == {}


def test_only_archives_scanned_not_backups_or_transcription_folder(setup):
    ws, folder, _ = setup
    audio(folder);audio(folder / 'processate', 'Earlier.wav');audio(folder / 'processed' / 'DEMO', 'Historical.wav')
    audio(folder / 'PC backups', 'Backup.wav');audio(folder / 'Trascrizioni', 'Ignored.wav')
    items = queue.library(ws)
    assert len(items) == 3
    assert {r['collection'] for r in items} == {'inbox', 'processed', 'legacy'}


def test_valid_asr_exports_txt_and_moves_original_byte_identical(setup):
    ws, folder, engine = setup
    source = audio(folder);digest = file_sha256(source)
    identifier = queue.library(ws)[0]['id']
    result = transcribe(ws, identifier)
    assert result['status'] == 'COMPLETED' and result['archive_status'] == 'COMPLETED'
    assert not source.exists() and file_sha256(folder / 'processate' / source.name) == digest
    item = queue.library(ws)[0]
    assert item['id'] == identifier and item['transcription_status'] == 'COMPLETED'
    assert Path(item['export_text']).parent == folder / 'Trascrizioni' / 'Dubbi'
    exported = Path(item['export_text']).read_text().splitlines()
    assert exported[0] == 'Timestamp indicativi. Trascrizione automatica.'
    assert exported[1].startswith('[00:00:00')
    assert Path(result['transcript_json']).is_relative_to(ws.runs_dir)
    assert all(p.suffix not in {'.json', '.log'} for p in folder.rglob('*') if p.is_file())
    assert engine.calls == 1


def test_timestamp_warning_saves_text_and_archives_original(setup):
    ws, folder, engine = setup
    source = audio(folder);identifier = queue.library(ws)[0]['id'];engine.end = 999
    result=transcribe(ws, identifier)
    assert result['status'] == 'COMPLETED' and result['archive_status'] == 'COMPLETED'
    assert not source.exists() and Path(result['export_text']).is_file()
    assert json.loads(Path(result['transcript_json']).read_text())['segments'][0]['timestamp_unavailable']


def test_cancelled_old_failure_does_not_poison_reenqueue(setup):
    ws, folder, engine = setup
    source=audio(folder);identifier=queue.library(ws)[0]['id']
    original=engine.transcribe
    engine.transcribe=lambda *a,**k:(_ for _ in ()).throw(asr.ASRError('primary unavailable'))
    assert transcribe(ws,identifier)['status']=='FAILED'
    job=queue.status(ws)['jobs'][0]
    with queue.database(ws) as conn:conn.execute("UPDATE jobs SET status='QUEUED' WHERE id=?",(job['id'],))
    queue.change_job(ws,job['id'],'remove')
    item=queue.library(ws)[0]
    assert item['transcription_status']=='NOT_STARTED' and 'enqueue' in item['actions']
    engine.transcribe=original
    assert transcribe(ws,identifier)['status']=='COMPLETED'
    assert not source.exists()


def test_export_regeneration_and_reassignment_without_asr(setup):
    ws, folder, engine=setup
    audio(folder);identifier=queue.library(ws)[0]['id'];transcribe(ws,identifier)
    item=queue.library(ws)[0];old=Path(item['export_text']);old.unlink()
    library.finish(ws,item,item['transcript_json']);assert old.is_file()
    code=add_course(ws,'Statistics')['course']['code']
    capture_associations.assign(capture.CaptureStore(ws),{'assignments':[{'recordingID':identifier,'courseCode':code}]})
    item=queue.library(ws)[0];library.finish(ws,item,item['transcript_json'])
    updated=queue.library(ws)[0]
    assert Path(updated['export_text']).parent.name==code and not old.exists()
    assert len(list((folder/'Trascrizioni').rglob('*.txt')))==1 and engine.calls==1


def test_archive_failure_retries_only_move(setup,monkeypatch):
    ws, folder, engine=setup
    source=audio(folder);identifier=queue.library(ws)[0]['id']
    move=library._move_original
    monkeypatch.setattr(library,'_move_original',lambda *a:(_ for _ in ()).throw(PermissionError('Fixture write denied')))
    result=transcribe(ws,identifier)
    assert result['status']=='COMPLETED' and result['archive_status']=='PENDING' and source.exists()
    monkeypatch.setattr(library,'_move_original',move)
    item=queue.library(ws)[0]
    assert library.finish(ws,item,item['transcript_json'])['archive_status']=='COMPLETED'
    assert engine.calls==1 and not source.exists()


def test_collision_never_overwrites_an_original(setup):
    ws, folder, _=setup
    source=audio(folder);occupied=audio(folder/'processate',source.name);occupied.write_bytes(b'older unrelated original')
    identifier=next(r for r in queue.library(ws) if r['collection']=='inbox')['id']
    transcribe(ws,identifier)
    assert occupied.read_bytes()==b'older unrelated original'
    moved=next(r for r in queue.library(ws) if r['id']==identifier)
    assert Path(moved['path']).name!=occupied.name and not source.exists()


def test_direct_chat_transcription_uses_same_finalizer(setup):
    ws, folder, engine=setup
    original=audio(folder);item=queue.library(ws)[0]
    result=run_transcription(ws,recording_id=item['id'],engines={'argmax':engine})
    assert result['status']=='COMPLETED' and not original.exists()
    assert queue.library(ws)[0]['export_text']


def test_modified_export_is_preserved(setup):
    ws,folder,engine=setup
    audio(folder);identifier=queue.library(ws)[0]['id'];transcribe(ws,identifier)
    item=queue.library(ws)[0];export=Path(item['export_text']);export.write_text('My own annotations')
    result=library.finish(ws,item,item['transcript_json'])
    assert result['archive_status']=='COMPLETED' and export.read_text()=='My own annotations' and engine.calls==1


def test_missing_relinked_file_retains_identity_and_rejects_wrong_bytes(setup):
    ws,folder,_=setup
    source=audio(folder);identifier=queue.library(ws)[0]['id'];transcribe(ws,identifier)
    item=queue.library(ws)[0];Path(item['path']).unlink()
    wrong=audio(folder,'Wrong.wav');wrong.write_bytes(wrong.read_bytes()+b'bad')
    with pytest.raises(ValueError,match='non corrisponde'):library.relink(ws,identifier,wrong)


def test_crash_after_move_can_finish_without_another_engine(setup,monkeypatch):
    ws,folder,engine=setup
    source=audio(folder);identifier=queue.library(ws)[0]['id'];move=library._move_original
    def interrupted(*args):
        move(*args)
        raise SystemExit('Simulated crash after filesystem move')
    monkeypatch.setattr(library,'_move_original',interrupted)
    assert transcribe(ws,identifier)['status']=='INTERRUPTED'
    assert not source.exists()
    monkeypatch.setattr(library,'_move_original',move)
    item=next(r for r in queue.library(ws) if r['id']==identifier)
    assert library.finish(ws,item,item['transcript_json'])['archive_status']=='COMPLETED'
    assert engine.calls==1


def test_native_private_session_without_sidecar_is_visible(setup):
    ws,folder,_=setup
    path=audio(folder);identifier=uuid.uuid4().hex
    atomic_json_write(ws.state_dir/'capture/sessions/native.json',{'status':'COMPLETED','outputPath':str(path),'event':{'id':'native','recordingID':identifier,'title':'Native recording'}})
    item=queue.library(ws)[0]
    assert item['id']==identifier and item['title']=='Native recording' and item['storage_recording_id']==identifier


def test_folder_change_backs_up_and_migrates_original_with_metadata(setup):
    ws,folder,_=setup
    source=audio(folder);digest=file_sha256(source);item=queue.library(ws)[0];library.sync(ws)
    queue.enqueue(ws,recording_id=item['id']);job=queue.status(ws)['jobs'][0];queue.change_job(ws,job['id'],'remove')
    target=folder.parent/'New user folder'
    result=library.set_folder(ws,target)
    assert result['moved']==1 and not source.exists()
    assert file_sha256(target/source.name)==digest
    assert len(list(Path(result['backup']).rglob('*.wav')))==1
    new=queue.library(ws)[0]
    assert new['id']==item['id'] and new['path']==str(target/source.name) and 'enqueue' in new['actions']


def test_fresh_restart_executes_engine_even_with_valid_cache(setup):
    ws,folder,engine=setup
    audio(folder);identifier=queue.library(ws)[0]['id'];transcribe(ws,identifier)
    job=queue.status(ws)['jobs'][0];queue.change_job(ws,job['id'],'restart')
    with queue.database(ws) as conn:conn.execute("UPDATE jobs SET status='RUNNING',pid=? WHERE id=?",(os.getpid(),job['id']))
    assert queue.run_job(ws,job['id'])['status']=='COMPLETED' and engine.calls==2


def test_recording_does_not_block_other_queued_audio(setup,monkeypatch):
    from datetime import datetime,UTC
    ws,folder,_=setup
    audio(folder);identifier=queue.library(ws)[0]['id'];queue.enqueue(ws,recording_id=identifier)
    queue.ui_lease(ws,str(uuid.uuid4()))
    atomic_json_write(capture.CaptureStore(ws).runtime_path,{'pid':os.getpid(),'updatedAt':datetime.now(UTC).isoformat(),'recording':True,'exporting':False,'currentSession':{'status':'RECORDING','event':{'id':'another','recordingID':uuid.uuid4().hex}}})
    launched=[]
    class Child:
        pid=99999999
        def poll(self):
            queue.ui_lease(ws,json.loads((queue.root(ws)/'ui.json').read_text())['session'],opened=False)
            with queue.database(ws) as conn:conn.execute("UPDATE jobs SET status='COMPLETED',pid=NULL WHERE id=?",(launched[0],))
            return 0
    def start(command,**kw):
        launched.append(command[-1])
        return Child()
    monkeypatch.setattr(queue.subprocess,'Popen',start)
    monkeypatch.setattr(queue.subprocess,'run',lambda *a,**kw:type('Pressure',(),{'stdout':'free percentage: 80%'})())
    queue.run(ws,tick=0)
    assert len(launched)==1


def test_cloud_placeholder_is_visible_without_hydration(setup,monkeypatch):
    ws,folder,_=setup
    path=audio(folder)
    monkeypatch.setattr(library,'local_audio',lambda p:False)
    monkeypatch.setattr('mynotes.ingestion.hydrate',lambda p:pytest.fail('Polling must not download audio'))
    item=queue.library(ws)[0]
    assert item['path']==str(path) and not item['can_transcribe'] and item['actions']==['download']


def test_two_external_files_for_same_lesson_get_independent_storage(setup):
    ws,folder,_=setup
    code=add_course(ws,'Statistics')['course']['code']
    audio(folder,code+'_L01.wav');audio(folder,code+' lecture 1.wav')
    items=queue.library(ws)
    assert len(items)==2 and len({r['id'] for r in items})==2
    assert all(r['storage_recording_id']==r['id'] and r['can_transcribe'] for r in items)


def test_prepare_first_archives_original_and_reuses_bound_transcript(setup):
    from mynotes.sourcepack import build_source_pack
    ws,folder,engine=setup
    code=add_course(ws,'Statistics')['course']['code']
    path=audio(folder,code+'_L01.wav')
    item=queue.library(ws)[0]
    pack=build_source_pack(ws,code+'-L01',audio=path,engines={'argmax':engine})
    assert not path.exists() and pack['transcription']['capture_recording_id']==item['id']
    assert run_transcription(ws,recording_id=item['id'],engines={'argmax':engine})['cache_reused'] and engine.calls==1


def test_old_worker_cannot_overwrite_newer_queue_claim(setup):
    ws,folder,engine=setup
    audio(folder);identifier=queue.library(ws)[0]['id'];queue.enqueue(ws,recording_id=identifier)
    job=queue.status(ws)['jobs'][0]
    def superseded():
        with queue.database(ws) as conn:
            row=conn.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()
            payload=json.loads(row['payload']);payload['_claim']='newer-attempt'
            conn.execute("UPDATE jobs SET status='QUEUED',pid=NULL,payload=? WHERE id=?",(json.dumps(payload),job['id']))
    engine.hook=superseded
    with queue.database(ws) as conn:conn.execute("UPDATE jobs SET status='RUNNING',pid=? WHERE id=?",(os.getpid(),job['id']))
    queue.run_job(ws,job['id'])
    assert queue.status(ws)['jobs'][0]['status']=='QUEUED'


def test_recovery_after_link_before_source_unlink(setup,monkeypatch):
    ws,folder,engine=setup
    source=audio(folder);identifier=queue.library(ws)[0]['id']
    def linked(source,destination,digest):
        Path(destination).parent.mkdir(exist_ok=True);os.link(source,destination)
        raise SystemExit('Simulated crash after exclusive link')
    move=library._move_original;monkeypatch.setattr(library,'_move_original',linked)
    transcribe(ws,identifier)
    assert source.exists()
    monkeypatch.setattr(library,'_move_original',move)
    library.sync(ws)
    items=queue.library(ws)
    assert len(items)==1 and items[0]['id']==identifier and not source.exists()
    assert library.finish(ws,items[0],items[0]['transcript_json'])['archive_status']=='COMPLETED' and engine.calls==1


def test_reusing_filename_after_archive_does_not_merge_different_originals(setup):
    ws,folder,_=setup
    source=audio(folder);first=queue.library(ws)[0]['id'];transcribe(ws,first)
    source=audio(folder);source.write_bytes(source.read_bytes()+b'different new original')
    library.sync(ws)
    items=queue.library(ws)
    assert len(items)==2 and len({r['id'] for r in items})==2
    assert next(r for r in items if r['path']==str(source))['id']!=first


def test_duplicate_contents_archive_both_originals_with_one_asr(setup):
    ws,folder,engine=setup
    audio(folder,'First.wav');audio(folder,'Second.wav')
    queue.enqueue(ws)
    job=queue.status(ws)['jobs'][0]
    with queue.database(ws) as conn:conn.execute("UPDATE jobs SET status='RUNNING',pid=? WHERE id=?",(os.getpid(),job['id']))
    queue.run_job(ws,job['id'])
    assert engine.calls==1 and len(list((folder/'processate').glob('*.wav')))==2
    assert len(list((folder/'Trascrizioni').rglob('*.txt')))==2


def test_later_duplicate_reuses_transcript_without_engine(setup):
    ws,folder,engine=setup
    audio(folder,'First.wav');first=queue.library(ws)[0]['id'];transcribe(ws,first)
    second=audio(folder,'Later.wav');identifier=next(r for r in queue.library(ws) if r['path']==str(second))['id']
    result=queue.enqueue(ws,recording_id=identifier)
    assert result['added']==0 and result['completed_reused']==1 and engine.calls==1 and not second.exists()
    assert all(r['transcription_status']=='COMPLETED' for r in queue.library(ws))


def test_legacy_private_metadata_keeps_storage_and_language_after_migration(setup):
    ws,folder,engine=setup
    code=add_course(ws,'Statistics')['course']['code']
    source=audio(folder,code+'_L07.wav');identifier=__import__('hashlib').sha256(str(source.resolve()).encode()).hexdigest()[:32]
    atomic_json_write(ws.state_dir/'capture/sessions/legacy.json',{'status':'PARTIAL','partial':True,'reason':'Stopped manually','outputPath':str(source),'event':{'id':'legacy','courseCode':code,'lessonNumber':7,'transcriptionLanguage':'it'}})
    item=queue.library(ws)[0];assert item['storage_recording_id'] is None
    new=folder.parent/'Changed originals';library.set_folder(ws,new)
    result=run_transcription(ws,recording_id=identifier,engines={'argmax':engine})
    manifest=json.loads(Path(result['transcript_json']).read_text())
    assert manifest['language']=='it' and manifest['lecture_id']==code+'-L07'
    assert Path(result['transcript_json'])==ws.runs_dir/(code+'-L07')/'asr/transcript.json'


def test_confirmed_native_save_is_ready_without_external_copy_delay(setup):
    ws,folder,_=setup
    cfg=ws.configuration_store().load();cfg['ingestion']['stable_seconds']=90;ws.configuration_store().save(cfg)
    native=audio(folder,'Native.wav');external=audio(folder,'Copy in progress.wav')
    identifier=uuid.uuid4().hex
    atomic_json_write(ws.state_dir/'capture/sessions/final.json',{'status':'COMPLETED','outputPath':str(native),'event':{'id':'native','recordingID':identifier,'title':'Native'}})
    items=queue.library(ws)
    assert next(r for r in items if r['path']==str(native))['can_transcribe']
    assert not next(r for r in items if r['path']==str(external))['can_transcribe']


def test_native_finder_rename_prefers_existing_file_over_stale_session_path(setup):
    ws,folder,_=setup
    original=audio(folder,'Z Native.wav');identifier=uuid.uuid4().hex
    atomic_json_write(ws.state_dir/'capture/sessions/final.json',{'status':'COMPLETED','outputPath':str(original),'event':{'id':'native','recordingID':identifier,'title':'Native'}})
    library.sync(ws)
    renamed=original.with_name('A renamed native.wav');original.rename(renamed)
    item=queue.library(ws)[0]
    assert item['id']==identifier and item['path']==str(renamed) and item['audio_available']


def test_completed_queue_job_exposes_txt_and_archive_recovery(setup):
    ws,folder,_=setup
    audio(folder);identifier=queue.library(ws)[0]['id'];transcribe(ws,identifier)
    job=queue.status(ws)['jobs'][0]
    assert job['actions']==['finish'] and Path(job['export_text']).is_file() and job['archive_status']=='COMPLETED'
    assert Path(job['outputs']['transcript_text']).is_file()


def test_relink_refuses_non_audio_without_changing_catalogue(setup):
    ws,folder,_=setup
    source=audio(folder);item=queue.library(ws)[0];library.sync(ws);source.unlink()
    wrong=folder/'Notes.txt';wrong.write_text('not audio')
    with pytest.raises(ValueError,match='audio supportato'):library.relink(ws,item['id'],wrong)
    assert library.records(ws)[item['id']]['path']==str(source)
