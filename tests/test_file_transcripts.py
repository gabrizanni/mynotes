"""Folder truth, independent TXT lifecycle and nonblocking ASR completion."""
import json
import os
from pathlib import Path

import pytest

from mynotes import asr, recording_library as lib, transcript_library as txt, transcription_queue as queue
from mynotes import capture, capture_associations as links
from mynotes.capture_courses import add_course
from mynotes.sourcepack import _import_transcript, build_source_pack
from mynotes.utils import file_sha256
from test_recording_library import setup, audio, transcribe


def external(folder, name='Lecture.txt', text='My manually edited lecture text'):
    p=folder/'Trascrizioni'/'Dubbi'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
    return p


def test_audio_folder_is_exclusive_and_deletions_prune_queue(setup):
    ws,folder,_=setup
    outside=folder.parent/'Other';audio(outside)
    cfg=ws.configuration_store().load();cfg['paths']['recording_dirs'].append(str(outside));ws.configuration_store().save(cfg)
    source=audio(folder);lib.sync(ws);item=queue.library(ws)[0]
    queue.enqueue(ws,recording_id=item['id']);source.unlink();lib.sync(ws)
    assert queue.library(ws)==[] and lib.records(ws)=={}
    assert queue.status(ws)['jobs'][0]['status']=='CANCELLED'
    assert (outside/'A lecture from my phone.wav').exists()


def test_deleted_audio_leaves_txt_and_agent_matching(setup):
    ws,folder,_=setup;source=audio(folder);item=queue.library(ws)[0];transcribe(ws,item['id'])
    current=queue.library(ws)[0];Path(current['path']).unlink();lib.sync(ws)
    assert queue.library(ws)==[]
    transcripts=txt.scan(ws);assert len(transcripts)==1
    assert links.pending(capture.CaptureStore(ws))['items'][0]['transcriptID']==transcripts[0]['id']


def test_external_rename_delete_and_cloud_folder_error(setup):
    ws,folder,_=setup;p=external(folder);first=txt.scan(ws,persist=True)[0]
    renamed=p.with_name('New name.txt');p.rename(renamed)
    assert txt.scan(ws)[0]['id']==first['id'] and txt.scan(ws)[0]['title']=='New name'
    renamed.unlink();lib.sync(ws);assert txt.scan(ws)==[] and not p.exists()
    folder.rename(folder.with_name('Temporarily unavailable'))
    with pytest.raises(ValueError,match='Cartella audio non accessibile'):lib.sync(ws)


def test_rename_moves_real_txt_preserves_edits_links_and_collisions(setup):
    ws,folder,engine=setup;audio(folder);item=queue.library(ws)[0];transcribe(ws,item['id'])
    first=txt.scan(ws,persist=True)[0];old=Path(first['path']);old.write_text('personal annotations')
    external(folder,'My title.txt','another transcript')
    result=txt.rename(ws,first['id'],'My title')['transcript'];new=Path(result['path'])
    assert new.name=='My title (2).txt' and new.read_text()=='personal annotations' and not old.exists()
    assert lib.records(ws)[item['id']]['export_text']==str(new)
    assert queue.status(ws)['jobs'][0]['export_text']==str(new)
    code=add_course(ws,'Statistics')['course']['code']
    links.assign(capture.CaptureStore(ws),{'assignments':[{'transcriptID':first['id'],'courseCode':code,'lessonNumber':3}]})
    moved=next(x for x in txt.scan(ws) if x['id']==first['id'])
    assert Path(moved['path']).read_text()=='personal annotations' and Path(moved['path']).name=='My title (2).txt'
    assert engine.calls==1


def test_dubbi_scan_over_thirty_external_without_audio_and_prepare(setup,monkeypatch):
    ws,folder,engine=setup;code=add_course(ws,'Operations Planning')['course']['code']
    for i in range(36):external(folder,f'Operations Planning {i}.txt','Operations planning lecture')
    pending=links.pending(capture.CaptureStore(ws))['items'];assert len(pending)==36
    assert all(any(c['courseCode']==code for c in x['candidateCourses']) for x in pending)
    chosen=pending[-1]
    links.assign(capture.CaptureStore(ws),{'assignments':[{'transcriptID':chosen['transcriptID'],'courseCode':code,'lessonNumber':7}]})
    monkeypatch.setattr(asr,'transcribe_audio',lambda *a,**k:pytest.fail('ASR must not run for a TXT'))
    pack=build_source_pack(ws,code+'-L07')
    assert pack['availability']['transcript'] and 'Operations planning lecture' in pack['evidence'][0]['text']
    assert engine.calls==0 and pack['evidence'][0]['start_seconds'] is None


def test_invalid_timing_saves_primary_text_and_archives(setup):
    ws,folder,engine=setup;source=audio(folder);digest=file_sha256(source);engine.end=999
    item=queue.library(ws)[0];result=transcribe(ws,item['id'])
    assert result['status']=='COMPLETED' and result['archive_status']=='COMPLETED'
    saved=json.loads(Path(result['transcript_json']).read_text());assert saved['completion_policy']=='text_saved'
    assert saved['segments'][0]['timestamp_unavailable'] and saved['segments'][0]['start_seconds'] is None
    assert '999' not in Path(result['export_text']).read_text()
    imported=_import_transcript(Path(result['transcript_json']),'DEMO-L01',bound_recording_id=item['id'])
    assert imported['segments'][0]['timestamp_unavailable']
    assert file_sha256(Path(result['current_audio_path']))==digest


def test_failed_secondary_saves_primary_and_empty_primary_still_fails(setup,monkeypatch):
    ws,folder,engine=setup;audio(folder);item=queue.library(ws)[0]
    monkeypatch.setattr('mynotes.asr_recovery.repair',lambda *a,**k:(_ for _ in ()).throw(asr.ASRError('secondary unavailable')))
    result=transcribe(ws,item['id']);assert result['status']=='COMPLETED'
    assert 'secondary unavailable' in json.loads(Path(result['transcript_json']).read_text())['quality_warnings'][0]['message']
    other=audio(folder,'No text.wav');other.write_bytes(other.read_bytes()+b'new source')
    engine.transcribe=lambda *a,**k:asr.EngineResult('argmax','fake','1',[])
    other_item=next(x for x in queue.library(ws) if x['path']==str(other))
    result=transcribe(ws,other_item['id']);assert result['status'] in {'FAILED','NEEDS_REVIEW'}
    assert other.exists() and not (folder/'Trascrizioni'/'Dubbi'/'No text.txt').exists()


def test_mtime_change_does_not_invalidate_and_new_content_does(setup):
    ws,folder,engine=setup;audio(folder);item=queue.library(ws)[0];transcribe(ws,item['id'])
    current=queue.library(ws)[0];p=Path(current['path']);st=p.stat();os.utime(p,(st.st_atime,st.st_mtime+100))
    assert queue.library(ws)[0]['transcription_status']=='COMPLETED'
    p.write_bytes(p.read_bytes()+b'changed');assert queue.library(ws)[0]['transcription_status']=='OUTDATED'
    assert Path(current['export_text']).exists() and engine.calls==1


def test_deleted_txt_not_regenerated_by_refresh(setup):
    ws,folder,engine=setup;audio(folder);item=queue.library(ws)[0];transcribe(ws,item['id'])
    path=Path(queue.library(ws)[0]['export_text']);path.unlink();lib.sync(ws);queue.library(ws)
    assert txt.scan(ws)==[] and not path.exists() and engine.calls==1


@pytest.mark.parametrize('name',['../escape','bad/name','..','.hidden','bad:name'])
def test_rename_rejects_paths(setup,name):
    ws,folder,_=setup;external(folder);item=txt.scan(ws)[0]
    with pytest.raises(ValueError):txt.rename(ws,item['id'],name)


def test_export_write_failure_offers_save_retry_without_asr(setup,monkeypatch,capsys):
    from argparse import Namespace
    ws,folder,engine=setup;source=audio(folder);item=queue.library(ws)[0]
    real=os.link
    def denied(src,dst,*args,**kwargs):
        if str(dst).endswith('.txt'):raise PermissionError('TXT destination denied')
        return real(src,dst,*args,**kwargs)
    monkeypatch.setattr(os,'link',denied)
    result=transcribe(ws,item['id'])
    assert result['status']=='FAILED' and source.exists()
    assert queue.library(ws)[0]['actions']==['finish']
    monkeypatch.setattr(os,'link',real)
    queue.run_cli(Namespace(workspace=str(ws.root),queue_command='finish',recording=item['id']))
    assert json.loads(capsys.readouterr().out)['archive_status']=='COMPLETED'
    assert engine.calls==1 and len(txt.scan(ws))==1


def test_agent_prompt_checks_all_txt_before_every_course_task(setup,monkeypatch):
    from mynotes.chat import launch_chat
    ws,folder,_=setup
    for number in range(36):external(folder,f'External lecture {number}.txt')
    config=ws.configuration_store().load()
    calls=[]
    monkeypatch.setattr('mynotes.chat.shutil.which',lambda *a,**k:'/usr/bin/true')
    monkeypatch.setattr('mynotes.chat.subprocess.run',lambda args,**kw:calls.append(args) or type('Done',(),{'returncode':0})())
    assert launch_chat(ws,config,'agy','Lavora sul corso')==0
    prompt=' '.join(calls[0])
    assert 'Before EVERY course task' in prompt and 'capture pending --json' in prompt
    assert 'TXT files without audio/metadata' in prompt and 'External lecture 35' in prompt


def test_two_existing_hardlinks_remain_two_visible_transcripts(setup):
    ws,folder,_=setup;p=external(folder);txt.scan(ws,persist=True)
    other=p.with_name('Another copy.txt');os.link(p,other)
    entries=txt.scan(ws,persist=True)
    assert len(entries)==2 and len({t['id'] for t in entries})==2


def test_legacy_export_names_migrate_once_without_modifying_content(setup):
    ws,folder,engine=setup;audio(folder);item=queue.library(ws)[0];transcribe(ws,item['id'])
    old=Path(queue.library(ws)[0]['export_text']);legacy=old.with_name(old.stem+'-'+item['id'][:8]+'.txt');old.rename(legacy)
    metadata=lib.records(ws)[item['id']];metadata['export_text']=str(legacy);lib.save(ws,metadata)
    legacy.write_text('Preserve annotations during migration')
    lib.sync(ws);lib.sync(ws)
    assert not legacy.exists() and old.read_text()=='Preserve annotations during migration'
    assert len(txt.scan(ws))==1 and engine.calls==1
