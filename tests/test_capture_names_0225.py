"""Filename inference must not gate ASR or override a durable association."""
import json
import os
import uuid
from pathlib import Path

import pytest

from mynotes import capture, capture_associations, recording_library as lib, transcription_queue as q
from mynotes.configuration import course_template
from mynotes.ingestion import parse_recording_name, recording_name_association
from mynotes.sourcepack import SourcePackError
from mynotes.transcription import run_transcription
from mynotes.transcript_library import scan, assign, rename
from mynotes.utils import atomic_json_write
from mynotes.workflow import parse_lecture_id, WorkflowError
from test_recording_library import setup, audio, transcribe


def courses(ws):
    cfg = ws.configuration_store().load()
    cfg['courses'] = []
    for code, name in [('RAI', 'Responsible AI'), ('GEN9', 'Predictive and Generative AI'), ('OPS7', 'Operations Analytics')]:
        course = course_template()
        course.update(code=code, name=name, aliases=[])
        course['transcription']['language'] = 'en'
        cfg['courses'].append(course)
    ws.configuration_store().save(cfg)
    return cfg


@pytest.mark.parametrize('stem,expected', [
    ('RAI 12', ('RAI', 12)), ('RAI_L12', ('RAI', 12)), ('RAI-L12', ('RAI', 12)),
    ('rai12', ('RAI', 12)), ('RAI lecture 12', ('RAI', 12)),
    ('Responsible AI lesson 12', ('RAI', 12)),
    ('GEN9 12 2', ('GEN9', None)), ('GEN9 1 22', ('GEN9', None)),
    ('GEN9 12 (2)', ('GEN9', 12)), ('RAI_L12 (3)', ('RAI', 12)),
    ('RAI 12 copy 2', ('RAI', None)), ('RAI 12-13', ('RAI', None)),
    ('OPS7_L12', ('OPS7', 12)), ('OPS7 12', ('OPS7', 12)),
    ('OPS712', (None, None)), ('RAIN12', (None, None)),
    ('RAI 0', ('RAI', None)), ('RAI 00', ('RAI', None)),
    ('RAI 1', ('RAI', 1)), ('RAI 99', ('RAI', 99)),
    ('RAI 100', ('RAI', 100)), ('RAI 999', ('RAI', 999)), ('RAI 1000', ('RAI', None)),
])
def test_numeric_boundaries(setup, stem, expected):
    ws, _, _ = setup
    cfg = courses(ws)
    assert recording_name_association(Path(stem + '.m4a'), cfg) == expected
    assert parse_recording_name(Path(stem + '.m4a'), cfg) == (expected if expected[1] else None)


@pytest.mark.parametrize('number', [1, 99, 100, 999])
def test_consistent_canonical_range(number):
    assert parse_lecture_id(f'OPS7-L{number:02d}') == ('OPS7', number)


@pytest.mark.parametrize('value', ['RAI-L00', 'RAI-L000', 'RAI-L1000', 'RAI-L1', 'RAI-L-1'])
def test_invalid_canonical_numbers(value):
    with pytest.raises(WorkflowError): parse_lecture_id(value)


def test_conflicting_alias_is_not_assigned(setup):
    ws, _, _ = setup;cfg=courses(ws)
    cfg['courses'][0]['aliases']=['shared'];cfg['courses'][1]['aliases']=['shared']
    assert recording_name_association(Path('shared 12.m4a'), cfg) == (None, None)


def test_finder_rename_repairs_old_catalog_and_exports_current_name(setup):
    ws, folder, engine = setup;courses(ws)
    old = audio(folder, 'GEN9 12 2.wav');lib.sync(ws)
    item = next(iter(lib.records(ws).values()))
    item.update(course_code='GEN9', lecture_id='GEN9-L122');lib.save(ws, item)
    current = old.with_name('RAI 12.wav');old.rename(current)
    fresh = q.library(ws)[0]
    assert (fresh['id'], fresh['course_code'], fresh['lecture_id']) == (item['id'], 'RAI', 'RAI-L12')
    assert fresh['association_source'] == 'filename' and fresh['can_transcribe']
    result=transcribe(ws,item['id'])
    assert result['status'] == 'COMPLETED'
    assert Path(result['export_text']) == folder/'Trascrizioni/RAI/RAI 12.txt'
    assert engine.calls == 1


@pytest.mark.parametrize('source', ['explicit', 'calendar', 'capture'])
def test_authoritative_association_survives_rename(setup, source):
    ws, folder, _ = setup;courses(ws)
    path=audio(folder,'GEN9 12.wav');lib.sync(ws);item=q.library(ws)[0]
    if source == 'explicit':
        capture_associations.assign(capture.CaptureStore(ws), {'assignments':[{'recordingID':item['id'],'courseCode':'GEN9','lessonNumber':12}]})
    else:
        atomic_json_write(ws.state_dir/'capture/sessions/native.json', {'status':'COMPLETED','outputPath':str(path),'event':{'id':'native','recordingID':item['id'],'courseCode':'GEN9','lessonNumber':12,'sourceID':'ics-source' if source=='calendar' else 'manual'}})
    lib.sync(ws);path.rename(path.with_name('RAI 13.wav'))
    fresh=q.library(ws)[0]
    assert (fresh['course_code'],fresh['lecture_id'],fresh['association_source']) == ('GEN9','GEN9-L12',source)


def test_ambiguous_number_transcribes_with_known_course(setup):
    ws, folder, engine=setup;courses(ws);audio(folder,'GEN9 12 2.wav')
    item=q.library(ws)[0]
    assert item['course_code']=='GEN9' and item['lecture_id'] is None and item['can_transcribe']
    result=transcribe(ws,item['id'])
    assert result['status']=='COMPLETED' and Path(result['export_text']).parent.name=='GEN9'
    assert engine.calls==1


def test_missing_optional_material_does_not_prevent_queue_or_direct_asr(setup, monkeypatch):
    ws, folder, engine=setup;courses(ws);audio(folder,'RAI 12.wav');item=q.library(ws)[0]
    def missing(*args, **kwargs): raise SourcePackError('Saved personal notebook is missing')
    monkeypatch.setattr('mynotes.sourcepack.transcription_course_context',missing)
    result=q.enqueue(ws,recording_id=item['id'])
    assert result['added']==1 and result['notices'][0]['message'].startswith('Contesto della lezione')
    final=run_transcription(ws,recording_id=item['id'],engines={'argmax':engine})
    assert final['status']=='COMPLETED' and engine.calls==1


def test_invalid_lesson_uses_course_profile_but_real_configuration_errors_propagate(setup, monkeypatch):
    ws, _, _=setup;courses(ws)
    item={'id':'a'*32,'title':'RAI 12','course_code':'RAI','lecture_id':'RAI-L1000'}
    assert q.profile(ws,item)['code']=='RAI'
    assert item['context_warning']
    def internal(*args, **kwargs): raise TypeError('internal bug')
    monkeypatch.setattr('mynotes.sourcepack.transcription_course_context',internal)
    item['lecture_id']='RAI-L12'
    with pytest.raises(TypeError): q.profile(ws,item)


def test_pending_rename_reconciles_before_claim_preserving_id(setup):
    ws, folder, engine=setup;courses(ws);path=audio(folder,'GEN9 12.wav')
    identifier=q.library(ws)[0]['id'];q.enqueue(ws,recording_id=identifier);job=q.status(ws)['jobs'][0]
    path.rename(path.with_name('RAI 12.wav'))
    assert q.refresh_queued_association(ws,job['id'])
    with q.database(ws) as conn:
        row=conn.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone()
        payload=json.loads(row['payload']);assert payload['course_profile']['code']=='RAI'
        assert payload['recording']['lecture_id']=='RAI-L12'
        conn.execute("UPDATE jobs SET status='RUNNING',pid=? WHERE id=?",(os.getpid(),job['id']))
    assert q.run_job(ws,job['id'])['status']=='COMPLETED' and engine.calls==1


def test_active_worker_keeps_frozen_profile_and_exports_using_current_link(setup):
    ws, folder, engine=setup;courses(ws);path=audio(folder,'GEN9 12.wav');identifier=q.library(ws)[0]['id']
    q.enqueue(ws,recording_id=identifier);job=q.status(ws)['jobs'][0]
    with q.database(ws) as conn:
        conn.execute("UPDATE jobs SET status='RUNNING',pid=? WHERE id=?",(os.getpid(),job['id']))
        frozen=conn.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]
    path.rename(path.with_name('RAI 12.wav'));lib.sync(ws)
    assert not q.refresh_queued_association(ws,job['id'])
    with q.database(ws,readonly=True) as conn:
        assert conn.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]==frozen
    assert q.run_job(ws,job['id'])['status']=='COMPLETED' and engine.calls==1
    assert Path(q.library(ws)[0]['export_text']).parent.name=='RAI'


def test_cancelled_job_not_reactivated_by_rename(setup):
    ws,folder,_=setup;courses(ws);path=audio(folder,'GEN9 12.wav');identifier=q.library(ws)[0]['id']
    q.enqueue(ws,recording_id=identifier);job=q.status(ws)['jobs'][0];q.change_job(ws,job['id'],'remove')
    path.rename(path.with_name('RAI 12.wav'));lib.sync(ws)
    assert not q.refresh_queued_association(ws,job['id'])
    assert q.status(ws)['jobs'][0]['status']=='CANCELLED'


def test_changed_content_after_rename_requires_explicit_retry(setup):
    ws,folder,engine=setup;courses(ws);path=audio(folder,'GEN9 12.wav');identifier=q.library(ws)[0]['id']
    q.enqueue(ws,recording_id=identifier);job=q.status(ws)['jobs'][0]
    path.rename(path.with_name('RAI 12.wav'));path=folder/'RAI 12.wav'
    with path.open('ab') as out:out.write(b'changed')
    assert not q.refresh_queued_association(ws,job['id'])
    assert q.status(ws)['jobs'][0]['status']=='FAILED' and engine.calls==0


def test_txt_rename_and_assignment_keep_manual_text_without_asr(setup):
    ws,folder,engine=setup;courses(ws);audio(folder,'unassigned.wav');identifier=q.library(ws)[0]['id']
    transcribe(ws,identifier);txt=scan(ws,persist=True)[0];path=Path(txt['path']);path.write_text('Manual correction retained.\n')
    rename(ws,txt['id'],'My lesson')
    assign(ws,[{'transcriptID':txt['id'],'courseCode':'RAI','lessonNumber':12}])
    final=scan(ws)[0];assert Path(final['path']).name=='My lesson.txt'
    assert Path(final['path']).read_text()=='Manual correction retained.\n' and engine.calls==1


def test_completed_finder_rename_keeps_txt_independent_and_reuses_text(setup):
    ws,folder,engine=setup;courses(ws);audio(folder,'GEN9 12.wav');identifier=q.library(ws)[0]['id']
    transcribe(ws,identifier);item=q.library(ws)[0];txt_id=scan(ws,persist=True)[0]['id'];txt=Path(item['export_text']);txt.write_text('Personal text unchanged\n')
    old=Path(item['path']);old.rename(old.with_name('RAI 13.wav'));lib.sync(ws)
    current=q.library(ws)[0];assert current['lecture_id']=='RAI-L13' and current['transcription_status']=='COMPLETED'
    scanned=scan(ws)[0];assert scanned['lecture_id'] is None and txt.read_text()=='Personal text unchanged\n'
    result=run_transcription(ws,recording_id=identifier,engines={'argmax':engine})
    assert result['status']=='COMPLETED' and engine.calls==1
    assert Path(result['export_text']).read_text()=='Personal text unchanged\n'
    assert Path(result['export_text']).name==txt.name
    assert scan(ws)[0]['id']==txt_id


def test_pending_removal_when_source_disappears(setup):
    ws,folder,_=setup;courses(ws);path=audio(folder,'RAI 12.wav');identifier=q.library(ws)[0]['id']
    q.enqueue(ws,recording_id=identifier);job=q.status(ws)['jobs'][0];path.unlink()
    assert not q.refresh_queued_association(ws,job['id'])
    assert q.status(ws)['jobs'][0]['status']=='CANCELLED'


def test_duplicate_refresh_does_not_run_asr_twice(setup):
    ws,folder,engine=setup;courses(ws)
    # Different course vocabularies produce distinct frozen cache keys initially.
    cfg=ws.configuration_store().load();cfg['courses'][1]['transcription']['vocabulary']=['GEN9 term'];ws.configuration_store().save(cfg)
    first=audio(folder,'RAI 12.wav');second=audio(folder,'GEN9 12.wav')
    items={Path(i['path']).stem:i for i in q.library(ws)}
    for i in items.values():q.enqueue(ws,recording_id=i['id'])
    jobs={j['recording_id']:j for j in q.status(ws)['jobs']}
    second.rename(second.with_name('RAI 13.wav'))
    assert not q.refresh_queued_association(ws,jobs[items['GEN9 12']['id']]['id'])
    states=[j['status'] for j in q.status(ws)['jobs']]
    assert sorted(states)==['CANCELLED','QUEUED'] and engine.calls==0


def test_explicit_reassignment_updates_existing_txt_lesson_without_asr(setup):
    ws,folder,engine=setup;courses(ws);audio(folder,'RAI 14.wav');identifier=q.library(ws)[0]['id']
    transcribe(ws,identifier);lib.sync(ws)
    capture_associations.assign(capture.CaptureStore(ws), {'assignments':[{'recordingID':identifier,'courseCode':'RAI','lessonNumber':13}]})
    lib.sync(ws)
    assert q.library(ws)[0]['lecture_id']=='RAI-L13'
    assert scan(ws)[0]['lecture_id']=='RAI-L13' and engine.calls==1


def test_preparation_error_does_not_stop_other_queued_work(setup, monkeypatch):
    from mynotes.asr import ASRError
    ws,folder,_=setup;courses(ws);audio(folder,'RAI 12.wav');second=audio(folder,'RAI 13.wav')
    with second.open('ab') as out:out.write(b'distinct')
    q.enqueue(ws);jobs=q.status(ws)['jobs'];bad=jobs[0]['id'];good=jobs[1]['id'];session=str(uuid.uuid4());q.ui_lease(ws,session)
    original=q.refresh_queued_association
    def prepare(workspace,identifier):
        if identifier==bad:raise ASRError('Modello locale non disponibile')
        return original(workspace,identifier)
    monkeypatch.setattr(q,'refresh_queued_association',prepare)
    class Child:
        pid=os.getpid()
        def poll(self):
            q.ui_lease(ws,session,opened=False)
            with q.database(ws) as conn:conn.execute("UPDATE jobs SET status='COMPLETED',pid=NULL WHERE id=?",(good,))
            return 0
    monkeypatch.setattr(q.subprocess,'Popen',lambda command,**kwargs: Child())
    monkeypatch.setattr(q.subprocess,'run',lambda *a,**k:type('Pressure',(),{'stdout':'free percentage: 80%'})())
    q.run(ws,tick=0)
    states={j['id']:j for j in q.status(ws)['jobs']}
    assert states[bad]['status']=='FAILED' and 'Preparazione' in states[bad]['error']
    assert states[good]['status']=='COMPLETED'


def test_renamed_cloud_placeholder_does_not_block_later_jobs(setup, monkeypatch):
    ws,folder,_=setup;courses(ws);path=audio(folder,'RAI 12.wav');identifier=q.library(ws)[0]['id']
    q.enqueue(ws,recording_id=identifier);job=q.status(ws)['jobs'][0];path.rename(path.with_name('RAI 13.wav'))
    monkeypatch.setattr(lib,'local_audio',lambda p:False)
    assert not q.refresh_queued_association(ws,job['id'])
    row=q.status(ws)['jobs'][0]
    assert row['status']=='QUEUED' and row['error'].startswith('Audio non ancora disponibile')



def test_multiple_finder_renames_follow_recording_identity_during_active_work(setup):
    ws,folder,engine=setup;courses(ws);path=audio(folder,'GEN9 12.wav');lib.sync(ws)
    identifier=q.library(ws)[0]['id'];second=path.with_name('RAI 12.wav');path.rename(second);lib.sync(ws)
    q.enqueue(ws,recording_id=identifier);job=q.status(ws)['jobs'][0]
    with q.database(ws) as conn:conn.execute("UPDATE jobs SET status='RUNNING',pid=? WHERE id=?",(os.getpid(),job['id']))
    second.rename(second.with_name('RAI 13.wav'));lib.sync(ws)
    assert q.run_job(ws,job['id'])['status']=='COMPLETED' and engine.calls==1
    assert q.library(ws)[0]['lecture_id']=='RAI-L13'
