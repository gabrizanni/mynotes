from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import time
import uuid
import wave

import pytest

from mynotes import asr, capture, transcription_queue as q
from mynotes.capture_courses import add_course, pending_courses, defer_context
from mynotes.utils import atomic_json_write
from mynotes.workspace import Workspace
from test_transcription_reliability import Fake


@pytest.fixture
def ws(tmp_path, monkeypatch):
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()
    monkeypatch.setattr('mynotes.onboarding.documents_root', lambda: tmp_path / "documents")
    monkeypatch.setattr('mynotes.onboarding.managed_tools', lambda: {})
    settings = workspace.configuration_store().load()
    settings['ingestion']['stable_seconds'] = 0
    workspace.configuration_store().save(settings)
    return workspace


def audio(ws, title="A conversation", *, partial=False):
    directory = ws.root / "recordings/capture"; directory.mkdir(parents=True, exist_ok=True)
    identifier = uuid.uuid4().hex
    path = directory / (identifier + '.wav')
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000); out.writeframes(b'\0\0' * 64000)
    atomic_json_write(Path(str(path)+'.capture.json'), {'id':uuid.uuid4().hex,'event':{'id':identifier,'recordingID':identifier,'title':title,'startsAt':'2026-01-01T00:00:00Z'},
        'outputPath':str(path),'status':'COMPLETED','partial':partial,'durationSeconds':4,'isTest':False})
    return path, identifier


def fake_registry(monkeypatch):
    engine = Fake(); engine.name = 'argmax'
    monkeypatch.setattr(asr, 'default_engines', lambda *args: {'argmax':engine})
    return engine


def claim(ws, job):
    with q.database(ws) as conn:
        conn.execute("UPDATE jobs SET status='RUNNING',pid=? WHERE id=?", (os.getpid(), job))


def test_name_only_course_preserves_onboarding_and_existing_settings(ws):
    before = ws.configuration_store().load()
    result = add_course(ws, '  Econometria  ')
    course = result['course']
    assert result['status']=='ADDED' and course['name']=='Econometria'
    assert course['context_setup']=='pending' and course['transcription']['language']=='auto'
    after = ws.configuration_store().load()
    assert after['onboarding']==before['onboarding'] and after['providers']==before['providers']
    assert add_course(ws,'ECONOMETRIA')['status']=='EXISTS'
    assert len(pending_courses(ws))==1
    defer_context(ws,course['code']);assert pending_courses(ws)==[]
    after=ws.configuration_store().load();after['courses'][0]['context']='Theory and applied exercises'
    ws.configuration_store().save(after)
    assert ws.configuration_store().load()['courses'][0]['context_setup']=='complete'


def test_queue_and_library_read_only_with_no_audio_hashing(ws, monkeypatch):
    audio(ws)
    before={str(p) for p in ws.root.rglob('*')}
    monkeypatch.setattr(q,'file_sha256',lambda p:pytest.fail('Library must not hash audio'))
    assert q.status(ws)['jobs']==[]
    assert q.library(ws)[0]['status']=='NOT_STARTED'
    assert {str(p) for p in ws.root.rglob('*')}==before


def test_unassigned_event_has_no_fake_course_or_lesson():
    event=capture.normalize_event({'recordingID':uuid.uuid4().hex,'title':'Interview / notes',
        'startsAt':'2026-01-01T00:00:00Z','endsAt':'2026-01-01T01:00:00Z'},set())
    assert event['courseCode'] is None and event['lessonNumber'] is None
    with pytest.raises(capture.CaptureError):
        capture.normalize_event({**event,'courseCode':'FAKE'},set())
    config={'recordBeforeSeconds':0,'recordAfterSeconds':0}
    other=capture.normalize_event({**event,'id':'another','recordingID':uuid.uuid4().hex,'startsAt':'2026-01-02T00:00:00Z','endsAt':'2026-01-02T01:00:00Z'},set())
    assert all(e['eligibility']=='SCHEDULED' for e in capture.validate_windows([event,other],config))


def test_standalone_requires_native_capability(ws):
    store=capture.CaptureStore(ws)
    with pytest.raises(capture.CaptureError,match='Aggiorna'):
        capture.require_standalone(store)
    from datetime import datetime, UTC
    atomic_json_write(store.runtime_path,{'capabilities':['standalone-recordings-v1'], 'pid':os.getpid(), 'updatedAt':datetime.now(UTC).isoformat()})
    capture.require_standalone(store)


def test_enqueue_is_idempotent_and_transcribes_standalone_with_valid_cache(ws,monkeypatch):
    path,identifier=audio(ws); engine=fake_registry(monkeypatch)
    assert q.enqueue(ws,2)['added']==1
    assert q.enqueue(ws,2)['added']==0
    job=q.status(ws)['jobs'][0];claim(ws,job['id'])
    assert q.run_job(ws,job['id'])['status']=='COMPLETED'
    assert engine.calls==1
    assert q.status(ws)['jobs'][0]['status']=='COMPLETED'
    item=q.library(ws)[0];assert item['status']=='COMPLETED' and Path(item['transcript_text']).is_file()
    Path(item['transcript_text']).unlink()
    assert q.enqueue(ws,2)['added']==0 and Path(item['transcript_text']).is_file()
    assert engine.calls==1


def test_changed_input_does_not_start_engine(ws,monkeypatch):
    path,_=audio(ws);engine=fake_registry(monkeypatch);q.enqueue(ws)
    job=q.status(ws)['jobs'][0]
    path.write_bytes(path.read_bytes()+b'changed')
    claim(ws,job['id']);assert q.run_job(ws,job['id'])['status']=='FAILED'
    assert engine.calls==0


def test_partial_audio_and_duplicate_contents_are_not_repeated(ws,monkeypatch):
    fake_registry(monkeypatch)
    audio(ws,partial=True);audio(ws,'One');audio(ws,'Duplicate bytes')
    result=q.enqueue(ws)
    assert result['added']==1 and len(result['skipped'])==1


def test_shared_asr_slots_bound_direct_and_queued_work(ws):
    from mynotes.asr_slots import admission
    q.configure(ws,parallel=2)
    with admission(ws),admission(ws):
        with pytest.raises(asr.ASRBusyError):
            with admission(ws):pass
        q.configure(ws,parallel=1)
        with pytest.raises(asr.ASRBusyError):
            with admission(ws):pass
    with admission(ws):
        with pytest.raises(asr.ASRBusyError):
            with admission(ws):pass


def test_old_exclusive_lock_and_new_shared_slots_exclude_each_other(ws):
    from mynotes.asr_slots import admission
    with (ws.state_dir/'.asr.lock').open('a+') as legacy:
        fcntl.flock(legacy,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(asr.ASRBusyError):
            with admission(ws):pass
    with admission(ws):
        with (ws.state_dir/'.asr.lock').open('a+') as legacy:
            with pytest.raises(BlockingIOError):fcntl.flock(legacy,fcntl.LOCK_EX|fcntl.LOCK_NB)


def test_window_lease_and_manual_pause(ws):
    session=str(uuid.uuid4())
    q.ui_lease(ws,session);assert q.status(ws)['ui_open']
    q.configure(ws,paused=True);assert q.status(ws)['paused']
    q.ui_lease(ws,str(uuid.uuid4()),opened=False);assert q.status(ws)['ui_open']
    q.ui_lease(ws,session,opened=False);assert not q.status(ws)['ui_open']
    q.ui_lease(ws,session);assert not q.status(ws)['ui_open']
    assert q.status(ws)['paused']


def test_closed_window_starts_no_queued_workers(ws,monkeypatch):
    fake_registry(monkeypatch);audio(ws);q.enqueue(ws)
    monkeypatch.setattr(q.subprocess,'Popen',lambda *a,**kw:pytest.fail('No job may start with a closed window'))
    q.run(ws)
    assert q.status(ws)['jobs'][0]['status']=='QUEUED'


def test_forged_output_path_is_not_discovered(ws,tmp_path):
    path,_=audio(ws);sidecar=Path(str(path)+'.capture.json');session=json.loads(sidecar.read_text())
    other=tmp_path/'private.wav';other.write_bytes(path.read_bytes());session['outputPath']=str(other)
    atomic_json_write(sidecar,session)
    assert q.library(ws)==[]


def test_duplicate_alias_gets_shared_result_without_second_asr(ws, monkeypatch):
    engine = fake_registry(monkeypatch)
    audio(ws, 'First'); audio(ws, 'Alias')
    assert q.enqueue(ws)['added'] == 1
    assert sorted(item['status'] for item in q.library(ws)) == ['NOT_STARTED', 'QUEUED']
    job = q.status(ws)['jobs'][0]; claim(ws, job['id'])
    q.run_job(ws, job['id'])
    items = q.library(ws)
    assert all(item['status'] == 'COMPLETED' and Path(item['transcript_text']).is_file() for item in items)
    assert engine.calls == 1
    assert 'result' not in q.status(ws)['jobs'][0]


def test_active_job_finishes_after_window_close_without_next_dispatch(ws, monkeypatch):
    fake_registry(monkeypatch)
    audio(ws, 'First'); second, _ = audio(ws, 'Second')
    second.write_bytes(second.read_bytes() + b'different')
    q.enqueue(ws); session = str(uuid.uuid4()); q.ui_lease(ws, session)
    launched = []
    class Child:
        pid = os.getpid()
        polls = 0
        def poll(self):
            self.polls += 1
            if self.polls == 1:
                return None
            with q.database(ws) as conn:
                conn.execute("UPDATE jobs SET status='COMPLETED',pid=NULL WHERE id=?", (launched[0],))
            return 0
    def launch(command, **kwargs):
        assert not launched, 'Closing the window must leave the second job queued'
        launched.append(command[-1]); q.ui_lease(ws, session, opened=False)
        return Child()
    monkeypatch.setattr(q.subprocess, 'Popen', launch)
    monkeypatch.setattr(q.subprocess, 'run', lambda *a, **kw: type('Pressure', (), {'stdout': 'free percentage: 80%'})())
    q.run(ws, tick=0)
    assert sorted(job['status'] for job in q.status(ws)['jobs']) == ['COMPLETED', 'QUEUED']


def test_dead_worker_can_be_retried_explicitly(ws, monkeypatch, capsys):
    from argparse import Namespace
    fake_registry(monkeypatch); audio(ws); q.enqueue(ws)
    job = q.status(ws)['jobs'][0]
    with q.database(ws) as conn:
        conn.execute("UPDATE jobs SET status='RUNNING',pid=99999999 WHERE id=?", (job['id'],))
    assert q.status(ws)['jobs'][0]['status'] == 'INTERRUPTED'
    monkeypatch.setattr(q, 'launch_runner', lambda ws: None)
    q.run_cli(Namespace(workspace=str(ws.root), queue_command='retry', job=job['id']))
    assert q.status(ws)['jobs'][0]['status'] == 'QUEUED'


def test_recording_language_override_does_not_modify_course(ws):
    path, identifier = audio(ws)
    sidecar = Path(str(path) + '.capture.json')
    saved = json.loads(sidecar.read_text()); saved['event']['transcriptionLanguage'] = 'it'
    atomic_json_write(sidecar, saved)
    item = q.library(ws)[0]
    assert q.profile(ws, item)['transcription']['language'] == 'it'
    assert asr.recording_profile({'transcription': {'language': 'en'}}, path)['transcription']['language'] == 'it'
