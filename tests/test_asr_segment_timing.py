"""Argmax segment-only timing, precise diagnostics and conservative cache upgrade."""
from pathlib import Path
from dataclasses import replace
import json,math,subprocess
import pytest
from mynotes import asr
from mynotes.utils import atomic_json_write,file_sha256
from test_transcription_reliability import setup,Fake


@pytest.mark.parametrize('word_mode',[False,True])
def test_argmax_retains_glossary_and_segment_timestamps(tmp_path,monkeypatch,word_mode):
    calls=[]
    monkeypatch.setattr(asr,'_run',lambda cmd:calls.append(cmd) or subprocess.CompletedProcess(cmd,0,json.dumps({'segments':[{'start':1,'end':3,'text':'Transformer embeddings.','avgLogprob':-.4}]}),''))
    monkeypatch.setattr(asr,'_tool_version',lambda _: 'v1.1.0')
    engine=asr.ArgmaxEngine('/usr/bin/true',tmp_path/'models',tmp_path/'tokens',word_timestamps=word_mode)
    result=engine.transcribe(tmp_path/'input.wav',tmp_path/'out',model='test',language='en',vocabulary=['Transformer','Embeddings'],prompt='')
    cmd=calls[0]
    assert ('--word-timestamps' in cmd)==word_mode
    assert '--without-timestamps' not in cmd
    assert cmd[cmd.index('--prompt')+1]=='Transformer, Embeddings'
    assert cmd[cmd.index('--language')+1]=='en'
    assert result.timestamp_mode==('word' if word_mode else 'segment')
    assert result.segments[0]['start']==1 and result.segments[0]['end']==3


@pytest.mark.parametrize('fields,expected,source',[({},0.0,'unavailable'),({'avgLogprob':-.5},math.exp(-.5),'avg_logprob_proxy'),({'confidence':.85},.85,'engine'),({'avgLogprob':float('nan')},0.0,'unavailable')])
def test_missing_confidence_never_means_certainty(fields,expected,source):
    segment=asr.parse_asr_payload({'segments':[{'start':0,'end':2,'text':'Example.',**fields}]})[0]
    assert segment['confidence']==pytest.approx(expected) and segment['confidence_source']==source
    merged,_=asr.merge_passes([segment],[],threshold=.72)
    assert merged[0]['confidence_source']==source
    if source=='unavailable': assert merged[0]['needs_review']


@pytest.mark.parametrize('segments,code',[
 ([{'start_seconds':5,'end_seconds':6,'text':'First'},{'start_seconds':3,'end_seconds':4,'text':'Second'}],'SEGMENTS_OUT_OF_ORDER'),
 ([{'start_seconds':0,'end_seconds':12,'text':'Long'}],'TIMESTAMP_BEYOND_AUDIO'),
 ([{'start_seconds':3,'end_seconds':3,'text':'Collapsed'}],'INVALID_SEGMENT_INTERVAL'),
 ([{'start_seconds':0,'end_seconds':2,'text':'Word','words':[{'start':0,'end':4}]}],'WORD_OUTSIDE_SEGMENT')])
def test_invalid_timing_has_actionable_diagnostics(segments,code):
    issues=asr.validate_transcript({'segments':segments},10)
    assert issues[0]['code']==code and 'start_seconds' in issues[0]
    if code=='SEGMENTS_OUT_OF_ORDER':assert issues[0]['previous_start_seconds']==5


def test_fresh_segment_pass_does_not_reuse_word_pass(setup,tmp_path):
    ws,course,audio=setup
    class Engine(Fake):
        name='argmax'
        timestamp_mode='word'
        def transcribe(self,*args,**kwargs):return replace(super().transcribe(*args,**kwargs),timestamp_mode=self.timestamp_mode)
    engine=Engine();course['transcription']['primary_engine']='argmax'
    registry={'argmax':engine}
    word=asr.cache_identity(ws,course,audio,engines=registry)[2]
    cache=tmp_path/'cache.json'
    opts=dict(model='fixture',language='en',vocabulary=[],prompt='')
    asr._engine_pass(engine,cache,10,audio,tmp_path/'word',**opts)
    engine.timestamp_mode='segment'
    assert asr.cache_identity(ws,course,audio,engines=registry)[2]!=word
    result=asr._engine_pass(engine,cache,10,audio,tmp_path/'segment',**opts)
    assert engine.calls==2 and result.timestamp_mode=='segment'
    assert json.loads(cache.read_text())['timestamp_mode']=='segment'


def test_valid_legacy_transcript_is_preserved_without_asr(setup):
    ws,course,audio=setup
    class Engine(Fake):
        name='argmax'
        timestamp_mode='segment'
    engine=Engine();course['transcription']['primary_engine']='argmax';registry={'argmax':engine}
    asr.transcribe_audio(ws,'DEMO-L01',course,audio,engines=registry)
    path=ws.runs_dir/'DEMO-L01/asr/transcript.json';saved=json.loads(path.read_text());saved.pop('timestamp_mode')
    _,saved['settings_hash'],saved['cache_key'],_=asr.cache_identity(ws,course,audio,engines=registry,legacy_timing=True)
    atomic_json_write(path,saved);sha=file_sha256(path)
    result=asr.transcribe_audio(ws,'DEMO-L01',course,audio,engines=registry)
    assert result['cache_reused'] and engine.calls==1 and file_sha256(path)==sha


def test_nonfinite_word_confidence_is_not_certainty():
    got=asr.parse_asr_payload({'segments':[{'start':0,'end':1,'words':[{'word':'Example','start':0,'end':1,'probability':float('nan')}]}]})
    assert got[0]['confidence']==0 and got[0]['words'][0]['confidence']==0


def test_rounding_must_not_collapse_a_segment():
    issues=asr.validate_transcript({'segments':[{'start_seconds':10.1,'end_seconds':10.2,'text':'Outside'}]},10)
    assert issues[0]['code']=='EMPTY_INTERVAL_AFTER_ROUNDING'


@pytest.mark.parametrize('change_audio',[False,True])
def test_queued_legacy_timing_upgrade_keeps_other_input_checks(setup,change_audio):
    import os
    from mynotes import transcription_queue as q
    ws,course,audio=setup
    class Engine(Fake):
        name='argmax'
        timestamp_mode='segment'
    engine=Engine();registry={'argmax':engine}
    q.enqueue(ws);job=q.status(ws)['jobs'][0]
    with q.database(ws) as conn:
        row=conn.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone()
        payload=json.loads(row['payload']);payload['course_profile']['transcription']['primary_engine']='argmax'
        sha,signature,key,_=asr.cache_identity(ws,payload['course_profile'],audio,engines=registry,legacy_timing=True)
        payload.update(audio_sha256=sha,settings_hash=signature)
        conn.execute("UPDATE jobs SET status='RUNNING',pid=?,cache_key=?,payload=? WHERE id=?",(os.getpid(),key,json.dumps(payload),job['id']))
    if change_audio:audio.write_bytes(audio.read_bytes()+b'changed')
    result=q.run_job(ws,job['id'],engines=registry)
    if change_audio:assert result['status']=='FAILED' and engine.calls==0
    else:assert result['status']=='COMPLETED' and result['timing_policy_upgrade']['to']=='segment' and engine.calls==1
