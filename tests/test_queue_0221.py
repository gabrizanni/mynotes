"""Regression tests for real admission, trustworthy time, and source-safe recovery."""
import json, os, signal, subprocess, sys, time, uuid, shutil, wave
from pathlib import Path
import pytest
from mynotes import asr, transcription_queue as q, recording_library as lib, capture, capture_associations as links
from mynotes.asr_progress import AttemptProgress, continuous_time
from mynotes.asr_recovery import attest_recovery, reuse_attested_audio
from mynotes.utils import atomic_json_write, file_sha256
from test_recording_library import setup, audio, transcribe
from test_capture_queue import ws, fake_registry, claim
from test_asr_selective_recovery import wav, rows


def test_prepared_bundled_tokenizer_allows_two_reader_processes(tmp_path, ready_argmax):
    engine=asr.ArgmaxEngine('/usr/bin/true',tmp_path/'models',tmp_path/'tokens')
    engine.model_dir.mkdir();engine.tokenizer_dir.mkdir();ready_argmax(engine,'fixture')
    code='''import sys,time;from pathlib import Path;from mynotes.asr import _model_read_lease
with _model_read_lease(Path(sys.argv[1]),Path(sys.argv[2]),'fixture'):
 print('acquired',flush=True);time.sleep(2)
'''
    workers=[]
    try:
        for _ in range(2):
            worker=subprocess.Popen([sys.executable,'-c',code,str(engine.model_dir),str(engine.tokenizer_dir)],stdout=subprocess.PIPE,text=True)
            workers.append(worker)
        start=time.monotonic()
        assert all(p.stdout.readline().strip()=='acquired' for p in workers)
        assert time.monotonic()-start<1.5
    finally:
        for p in workers:
            p.terminate();p.wait(timeout=5)


def test_missing_assets_never_launch_asr(tmp_path,monkeypatch):
    e=asr.ArgmaxEngine('/usr/bin/true',tmp_path/'models',tmp_path/'tokens')
    monkeypatch.setattr(asr,'_run',lambda *a:pytest.fail('No inference with incomplete components'))
    with pytest.raises(asr.ASRError,match='MODEL_NOT_READY'):
        e.transcribe(tmp_path/'a.wav',tmp_path/'out',model='fixture',language='en',vocabulary=[],prompt='')


def test_waiting_reader_rechecks_model_and_is_cancellable(tmp_path,ready_argmax):
    import fcntl
    e=asr.ArgmaxEngine('/usr/bin/true',tmp_path/'models',tmp_path/'tokens');e.model_dir.mkdir();e.tokenizer_dir.mkdir()
    code="from pathlib import Path;import sys;from mynotes.asr import _model_read_lease\nwith _model_read_lease(Path(sys.argv[1]),Path(sys.argv[2]),'fixture'): print('ready',flush=True)"
    with (tmp_path/'.asr-model-preparation.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        p=subprocess.Popen([sys.executable,'-c',code,str(e.model_dir),str(e.tokenizer_dir)],stdout=subprocess.PIPE,text=True)
        time.sleep(.2);assert p.poll() is None
        ready_argmax(e,'fixture');fcntl.flock(lock,fcntl.LOCK_UN)
        stdout,_=p.communicate(timeout=5);assert p.returncode==0 and stdout.strip()=='ready'
        fcntl.flock(lock,fcntl.LOCK_EX)
        p=subprocess.Popen([sys.executable,'-c',code,str(e.model_dir),str(e.tokenizer_dir)])
        time.sleep(.2);p.terminate();p.wait(timeout=5);assert p.returncode != 0


def test_stopwatch_uses_continuous_clock_and_freezes_after_finish(tmp_path,monkeypatch):
    from mynotes import asr_progress as progress
    from mynotes.workspace import Workspace
    ws=Workspace(tmp_path);ws.initialize();d=ws.runs_dir/'QA-L01/asr';a=d/'attempts'/'a';a.mkdir(parents=True)
    clock=[100.];wall=[1000.]
    monkeypatch.setattr(progress,'continuous_time',lambda:clock[0]);monkeypatch.setattr(progress.time,'time',lambda:wall[0])
    state={'started_clock':100.,'lecture_id':'QA-L01','attempt_id':'a','status':'RUNNING'}
    p=AttemptProgress(ws,d,a,state);p('normalizing');clock[0]+=7;wall[0]-=900;p()
    saved=json.loads((a/'status.json').read_text());assert saved['elapsed_seconds']==7 and saved['phase']=='normalizing'
    p('processing');assert state['phase']=='normalizing'
    p('completed',status='COMPLETED');assert state['status']=='RUNNING' and state['phase']=='finalizing'
    p.finish('COMPLETED');clock[0]+=10;p('primary_pass');assert state['elapsed_seconds']==7 and state['phase']=='completed'


def test_clear_preserves_active_audio_pause_aliases_and_allows_reenqueue(ws,monkeypatch):
    fake_registry(monkeypatch)
    a,_=__import__('test_capture_queue').audio(ws,'one');b,_=__import__('test_capture_queue').audio(ws,'two');b.write_bytes(b.read_bytes()+b'x')
    q.enqueue(ws,parallel=2);jobs=q.status(ws)['jobs'];claim(ws,jobs[0]['id']);q.configure(ws,paused=True)
    result=q.clear(ws);assert result['removed']==1 and result['running']==1 and result['paused']
    assert a.exists() and b.exists() and q.clear(ws)['removed']==0
    with q.database(ws,readonly=True) as c:assert c.execute('SELECT count(*) FROM queue_events').fetchone()[0]==1
    q.enqueue(ws,recording_id=jobs[1]['recording_id']);s=q.status(ws)
    assert s['paused'] and s['parallel']==2 and sum(j['status']=='QUEUED' for j in s['jobs'])==1


def test_pause_between_snapshot_and_claim_prevents_dispatch(ws,monkeypatch):
    fake_registry(monkeypatch);__import__('test_capture_queue').audio(ws);q.enqueue(ws);session=str(uuid.uuid4());q.ui_lease(ws,session)
    real=q.status;called=[False]
    def stale_snapshot(workspace):
        snapshot=real(workspace)
        if not called[0]:
            called[0]=True;q.configure(ws,paused=True)
        return snapshot
    monkeypatch.setattr(q,'status',stale_snapshot)
    monkeypatch.setattr(q.subprocess,'Popen',lambda *a,**kw:pytest.fail('No worker after acknowledged pause'))
    monkeypatch.setattr(q.subprocess,'run',lambda *a,**kw:type('Pressure',(),{'stdout':'free percentage: 80%'})())
    q.run(ws,tick=0);assert real(ws)['jobs'][0]['status']=='QUEUED'


def test_wrong_attempt_state_is_never_shown_for_current_claim(ws,monkeypatch):
    fake_registry(monkeypatch);__import__('test_capture_queue').audio(ws);q.enqueue(ws);job=q.status(ws)['jobs'][0];claim(ws,job['id'])
    item=q.library(ws)[0]
    d=q.asr_directory(ws,item);d.mkdir(parents=True,exist_ok=True)
    atomic_json_write(d/'status.json',{'job_id':'old-job','claim_id':None,'pid':os.getpid(),'phase':'primary_pass','elapsed_seconds':500,'updated_clock':continuous_time()})
    result=q.status(ws)['jobs'][0];assert result['state_fresh'] is False and result['elapsed_seconds'] is None


def test_bound_recovery_reuses_identical_old_pcm_without_new_normalization(tmp_path):
    directory=tmp_path/'asr';aid='a'*32;a=directory/'attempts'/aid;a.mkdir(parents=True)
    wav(a/'input.wav');atomic_json_write(a/'primary-result.json',{'engine':'argmax','model':'fixture','segments':rows()})
    atomic_json_write(a/'primary-binding.json',{'audio_sha256':'original','normalized_audio_sha256':file_sha256(a/'input.wav'),'primary_sha256':file_sha256(a/'primary-result.json'),'engine_reports':{}})
    atomic_json_write(directory/'status.json',{'status':'FAILED','attempt_id':aid})
    receipt=attest_recovery(directory,'original');target=tmp_path/'reuse.wav'
    assert reuse_attested_audio(receipt,'original',target) and file_sha256(target)==file_sha256(a/'input.wav')
    (a/'primary-result.json').write_text('{}')
    with pytest.raises(asr.ASRError):reuse_attested_audio(receipt,'original',tmp_path/'bad.wav')


def test_legacy_reconversion_mismatch_falls_back_once_without_reusing_report(setup,monkeypatch):
    ws,folder,engine=setup;source=audio(folder);item=q.library(ws)[0];q.enqueue(ws,recording_id=item['id']);job=q.status(ws)['jobs'][0]
    d=q.asr_directory(ws,item);a=d/'attempts'/('b'*32);a.mkdir(parents=True)
    shutil.copy2(source,a/'input.wav')
    # Old decoder differs by a single bit: never relax byte equality to assert identity.
    with (a/'input.wav').open('r+b') as out:out.seek(100);out.write(b'\x01')
    atomic_json_write(a/'primary-result.json',{'engine':'argmax','model':q.profile(ws,item)['transcription']['primary_model'],'segments':[{'start':0,'end':4,'text':'Old text'}]})
    atomic_json_write(d/'status.json',{'status':'FAILED','attempt_id':'b'*32})
    with q.database(ws) as c:c.execute("UPDATE jobs SET status='FAILED' WHERE id=?",(job['id'],))
    q.change_job(ws,job['id'],'recover');claim(ws,job['id']);result=q.run_job(ws,job['id'])
    assert result['status']=='COMPLETED' and engine.calls==1
    assert list((d/'attempts').glob('*/recovery-fallback.json')) and (a/'primary-result.json').exists()


def test_external_completed_audio_is_pending_in_dubbi_and_estimate_can_be_corrected(setup):
    from mynotes.capture_courses import add_course
    ws,folder,engine=setup;audio(folder,'Statistics L01.wav');identifier=q.library(ws)[0]['id'];transcribe(ws,identifier)
    first=add_course(ws,'Statistics')['course']['code'];second=add_course(ws,'Finance')['course']['code'];store=capture.CaptureStore(ws)
    pending=links.pending(store)['items'];entry=next(x for x in pending if x['recordingID']==identifier)
    assert entry['candidateCourses'][0]['courseCode']==first
    assert Path(q.library(ws)[0]['export_text']).parent.name=='Dubbi'
    links.assign(store,{'assignments':[{'recordingID':identifier,'courseCode':first,'lessonNumber':1,'associationState':'estimated','reason':'Nome coerente','alternatives':[second]}]})
    item=q.library(ws)[0];assert item['association_state']=='estimated' and Path(item['export_text']).parent.name==first
    links.assign(store,{'assignments':[{'recordingID':identifier,'courseCode':second,'lessonNumber':2}]})
    assert q.library(ws)[0]['association_state']=='resolved' and q.library(ws)[0]['lecture_id']==f'{second}-L02'
    links.assign(store,{'assignments':[{'recordingID':identifier,'courseCode':first,'associationState':'estimated','reason':'Guess'}]})
    assert q.library(ws)[0]['course_code']==second and engine.calls==1
    assert len(list((folder/'Trascrizioni').rglob('*.txt')))==1


def test_estimate_never_overwrites_an_occupied_lesson(setup):
    from mynotes.capture_courses import add_course
    ws,folder,_=setup;a=audio(folder,'First.wav');lib.sync(ws);b=audio(folder,'Second.wav');lib.sync(ws)
    items=q.library(ws);code=add_course(ws,'Stats')['course']['code'];store=capture.CaptureStore(ws)
    links.assign(store,{'assignments':[{'recordingID':items[0]['id'],'courseCode':code,'lessonNumber':1}]})
    links.assign(store,{'assignments':[{'recordingID':items[1]['id'],'courseCode':code,'lessonNumber':1,'associationState':'estimated','reason':'Similar name'}]})
    found={x['id']:x for x in q.library(ws)}
    assert found[items[0]['id']]['lecture_id']==f'{code}-L01'
    assert found[items[1]['id']]['lecture_id'] is None and found[items[1]['id']]['association_conflict']


def test_reassignment_preserves_user_edited_dubbi_copy(setup):
    from mynotes.capture_courses import add_course
    ws,folder,engine=setup;audio(folder);identifier=q.library(ws)[0]['id'];transcribe(ws,identifier)
    item=q.library(ws)[0];path=Path(item['export_text']);path.write_text('User annotations')
    code=add_course(ws,'Stats')['course']['code'];links.assign(capture.CaptureStore(ws),{'assignments':[{'recordingID':identifier,'courseCode':code}]})
    assert path.read_text()=='User annotations' and q.library(ws)[0]['archive_status']=='PENDING' and engine.calls==1


def test_fluid_auto_does_not_pass_an_unsupported_language_hint(tmp_path,monkeypatch):
    calls=[]
    def run(command):
        calls.append(command)
        target=Path(command[command.index('--output-json')+1])
        atomic_json_write(target,{'wordTimings':[{'word':'Hello.','startTime':0.,'endTime':1.}]})
    monkeypatch.setattr(asr,'_run',run);monkeypatch.setattr(asr,'_tool_version',lambda _:'fixture')
    e=asr.FluidAudioEngine('/usr/bin/true')
    e.transcribe(tmp_path/'a.wav',tmp_path/'out',model='v2',language='auto',vocabulary=[],prompt='')
    assert '--language' not in calls[0]


def test_old_report_retains_detected_language_for_selective_verification(tmp_path):
    directory=tmp_path/'asr';a=directory/'attempts'/('d'*32);(a/'primary').mkdir(parents=True)
    wav(a/'input.wav');atomic_json_write(a/'primary-result.json',{'engine':'argmax','model':'fixture','segments':rows()})
    atomic_json_write(a/'primary/input.json',{'language':'en','segments':rows()})
    atomic_json_write(directory/'status.json',{'status':'FAILED','attempt_id':'d'*32})
    receipt=attest_recovery(directory,'source')
    from mynotes.asr_recovery import load_recovery
    assert load_recovery(receipt,'source',a/'input.wav').language=='en'


def test_removed_event_remains_accessible_after_reenqueue(ws,monkeypatch):
    fake_registry(monkeypatch);__import__('test_capture_queue').audio(ws);q.enqueue(ws);job=q.status(ws)['jobs'][0]
    q.clear(ws);assert not q.status(ws)['history_events']
    q.enqueue(ws);s=q.status(ws)
    assert s['jobs'][0]['status']=='QUEUED' and s['history_events'][0]['status']=='CANCELLED'
    assert s['history_events'][0]['recording_id']==job['recording_id']
