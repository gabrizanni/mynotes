"""Selective repairs must preserve source, text order and recoverable evidence."""
import json
from pathlib import Path
import struct
import subprocess
import wave

import pytest
from mynotes import asr
from mynotes.asr_recovery import assessment, repair, attest_recovery, load_recovery, POLICY_VERSION
from mynotes.utils import file_sha256, atomic_json_write


def wav(path, seconds=100, rate=100):
    with wave.open(str(path),'wb') as f:
        f.setparams((1,2,rate,0,'NONE','not compressed'))
        f.writeframes(b''.join(struct.pack('<h',i%32768) for i in range(seconds*rate)))
    return path


def rows():
    return [{'start':float(i), 'end':float(i+1), 'text':word+'.', 'confidence':0.,'confidence_source':'unavailable'}
            for i,word in zip(range(10,30), ['alpha','bravo','charlie','delta','echo','foxtrot','golf','hotel','india','juliet','kilo','lima','mike','november','oscar','papa','quebec','romeo','sierra','tango'])]


def test_fluid_wordtimings_groups_and_preserves_unknown_confidence():
    data={'text':'unused','wordTimings':[{'word':'Hello','startTime':.1,'endTime':.3},
          {'word':'world.','startTime':.3,'endTime':.7}, {'word':'Again!','startTime':1.,'endTime':2.}]}
    result=asr.parse_asr_payload(data)
    assert [(s['start'],s['end'],s['text']) for s in result]==[(.1,.7,'Hello world.'),(1.,2.,'Again!')]
    assert result[0]['confidence_source']=='unavailable'
    assert result[0]['words'][0]['confidence_source']=='unavailable'


def test_exact_pcm_clip_uses_actual_sample_offset(tmp_path):
    source=wav(tmp_path/'source.wav',seconds=2,rate=100)
    clip=tmp_path/'clip.wav'
    offset=asr.extract_audio_clip(source,clip,start=.125,duration=.202)
    assert offset==.12
    with wave.open(str(source)) as f:
        f.setpos(12);expected=f.readframes(21)
    with wave.open(str(clip)) as f:
        assert f.getnframes()==21 and f.readframes(21)==expected


def test_primary_prompt_is_disabled_by_default(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(asr,'_tool_version',lambda _:'fixture')
    monkeypatch.setattr(asr,'_run',lambda cmd:calls.append(cmd) or subprocess.CompletedProcess(cmd,0,'{"segments":[{"start":0,"end":1,"text":"Hello"}]}',''))
    engine=asr.ArgmaxEngine('/usr/bin/true',tmp_path/'models',tmp_path/'tokens')
    engine.transcribe(tmp_path/'input.wav',tmp_path/'out',model='fixture',language='en',vocabulary=['private term'],prompt='accent')
    assert '--prompt' not in calls[0] and '--word-timestamps' not in calls[0]


def test_proxy_and_missing_confidence_do_not_trigger_second_engine():
    data=[{**s,'confidence':.01,'confidence_source':'avg_logprob_proxy','avg_logprob':-.9} for s in rows()]
    assert assessment(data,100)==([],[])
    data[5].update(avg_logprob=-1.01)
    issues,windows=assessment(data,100)
    assert issues[0]['code']=='ENGINE_QUALITY_WARNING' and len(windows)==1


@pytest.mark.parametrize('change', ['zero','invert','backwards','repeat','word'])
def test_true_defects_create_bounded_windows(change):
    data=rows()
    if change=='zero':data[5]['end']=data[5]['start']
    if change=='invert':data[5]['end']=data[5]['start']-.02
    if change=='backwards':data[5]['start']=data[4]['start']-.02
    if change=='repeat':data[5]['text']='loop '*8
    if change=='word':data[5]['words']=[{'start':15,'end':30}]
    issues,windows=assessment(data,100)
    assert issues and windows and sum(x['clip_end']-x['clip_start'] for x in windows)<30


class Local:
    name='fluidaudio'
    def __init__(self, source_rows, offset, bad=False):self.rows=source_rows;self.offset=offset;self.calls=0;self.bad=bad
    def transcribe(self, audio, output_dir, **options):
        self.calls+=1
        with wave.open(str(audio)) as handle:
            length=handle.getnframes()/handle.getframerate()
            offset=struct.unpack('<h',handle.readframes(1))[0]/handle.getframerate()
        result=[{**s,'start':s['start']-offset,'end':s['end']-offset} for s in self.rows if s['start']>=offset and s['end']<=offset+length]
        if self.bad:result[5]['text']='Completely different 12345.'
        return asr.EngineResult('fluidaudio','v2','fixture',result,'word')


def test_one_local_repair_preserves_good_text_and_reuses_valid_clips(tmp_path):
    audio=wav(tmp_path/'input.wav'); original=rows();broken=[dict(s) for s in original];broken[5]['end']-=1.02
    primary=asr.EngineResult('argmax','fixture','fixture',broken,'segment')
    engine=Local(original,9)
    settings={'language':'en','vocabulary':[],'recovery_model':'v2'}
    directory=tmp_path/'asr/attempts'/'a';directory.mkdir(parents=True)
    fixed,report=repair(primary,audio=audio,duration=100,engine=engine,settings=settings,directory=directory,update=lambda *a,**k:None)
    assert engine.calls==1 and [s['text'] for s in fixed.segments]==[s['text'] for s in original]
    assert fixed.segments[0]==primary.segments[0] and report['comparisons'][0]['original_text']
    other=tmp_path/'asr/attempts'/'b';other.mkdir()
    repair(primary,audio=audio,duration=100,engine=engine,settings=settings,directory=other,update=lambda *a,**k:None)
    assert engine.calls==1
    fresh=tmp_path/'asr/attempts'/'c';fresh.mkdir()
    repair(primary,audio=audio,duration=100,engine=engine,settings={**settings,'_force_fresh':True},directory=fresh,update=lambda *a,**k:None)
    assert engine.calls==2


def test_extensive_repairs_never_start_second_engine(tmp_path):
    data=rows()
    for s in data:s['end']=s['start']
    directory=tmp_path/'a';directory.mkdir();engine=Local(rows(),0)
    with pytest.raises(asr.ASRReviewError,match='limit 30%'):
        repair(asr.EngineResult('argmax','x','x',data),audio=wav(tmp_path/'input.wav'),duration=35,engine=engine,
               settings={},directory=directory,update=lambda *a,**k:None)
    assert engine.calls==0 and not (directory/'transcript.txt').exists()


def test_disagreement_keeps_diagnosis_and_never_publishes(tmp_path):
    data=rows();data[5]['end']-=1.02;directory=tmp_path/'a';directory.mkdir()
    with pytest.raises(asr.ASRReviewError,match='disagree'):
        repair(asr.EngineResult('argmax','x','x',data),audio=wav(tmp_path/'input.wav'),duration=100,
               engine=Local(rows(),9,bad=True),settings={'language':'en','vocabulary':[]},directory=directory,update=lambda *a,**k:None)
    assert json.loads((directory/'repair-plan.json').read_text())['issues'][-1]['code']=='MATERIAL_DISAGREEMENT'
    assert not (directory/'transcript.txt').exists()


def test_recovery_checks_attempt_original_normalized_and_report(tmp_path):
    directory=tmp_path/'asr';identifier='a'*32;attempt=directory/'attempts'/identifier;attempt.mkdir(parents=True)
    audio=wav(attempt/'input.wav');report=attempt/'primary-result.json'
    atomic_json_write(report,{'engine':'argmax','model':'fixture','segments':rows()})
    atomic_json_write(directory/'status.json',{'attempt_id':identifier,'status':'NEEDS_REVIEW'})
    receipt=attest_recovery(directory,'source-hash')
    assert load_recovery(receipt,'source-hash',audio).segments==rows()
    with pytest.raises(asr.ASRError,match='IDENTITY'):load_recovery(receipt,'wrong',audio)
    report.write_text('{}')
    with pytest.raises(asr.ASRError,match='INTEGRITY'):load_recovery(receipt,'source-hash',audio)


def test_queue_recovery_is_explicit_and_single_admission(tmp_path,monkeypatch):
    import os, shutil
    from mynotes.workspace import Workspace
    from mynotes import transcription_queue as q, recording_library as lib
    from test_transcription_reliability import Fake
    monkeypatch.setenv('MYNOTES_CAPTURE_HOME',str(tmp_path/'native'))
    monkeypatch.setattr('mynotes.onboarding.managed_tools',lambda:{})
    ws=Workspace(tmp_path/'private');ws.initialize();cfg=ws.configuration_store().load();cfg['ingestion']['stable_seconds']=0;ws.configuration_store().save(cfg)
    folder=tmp_path/'Originals';lib.set_folder(ws,folder);source=wav(folder/'External.wav')
    primary=Fake();primary.name='argmax';secondary=Local(rows(),9)
    monkeypatch.setattr(asr,'default_engines',lambda *a:{'argmax':primary,'fluidaudio':secondary})
    item=q.library(ws)[0];q.enqueue(ws,recording_id=item['id']);job=q.status(ws)['jobs'][0]
    directory=q.asr_directory(ws,item);identifier='a'*32;attempt=directory/'attempts'/identifier;attempt.mkdir(parents=True)
    shutil.copy2(source,attempt/'input.wav');broken=rows();broken[5]['end']-=1.02
    atomic_json_write(attempt/'primary-result.json',{'engine':'argmax','model':'large-v3-v20240930_626MB','segments':broken})
    atomic_json_write(directory/'status.json',{'attempt_id':identifier,'status':'NEEDS_REVIEW'})
    with q.database(ws) as c:c.execute("UPDATE jobs SET status='NEEDS_REVIEW' WHERE id=?",(job['id'],))
    assert 'recover' in q.status(ws)['jobs'][0]['actions'] and 'recover' in q.library(ws)[0]['actions']
    q.change_job(ws,job['id'],'recover')
    with pytest.raises(ValueError,match='Attendi'):q.change_job(ws,job['id'],'recover')
    with q.database(ws) as c:c.execute("UPDATE jobs SET status='RUNNING',pid=? WHERE id=?",(os.getpid(),job['id']))
    result=q.run_job(ws,job['id'])
    assert result['status']=='COMPLETED' and primary.calls==0 and secondary.calls==1
    assert file_sha256(Path(result['current_audio_path']))==file_sha256(source) if source.exists() else True


def test_boundary_loss_is_never_silently_accepted(tmp_path):
    original=rows();broken=[dict(s) for s in original];broken[5]['end']-=1.02
    class MissingBoundary(Local):
        def transcribe(self,*a,**kw):
            result=super().transcribe(*a,**kw)
            result.segments[5]['text']='Lost boundary.'
            return result
    directory=tmp_path/'a';directory.mkdir()
    with pytest.raises(asr.ASRReviewError):
        repair(asr.EngineResult('argmax','x','x',broken),audio=wav(tmp_path/'input.wav'),duration=100,
               engine=MissingBoundary(original,9),settings={'language':'en','vocabulary':[]},directory=directory,update=lambda *a,**k:None)
    assert not (directory/'transcript.txt').exists()


def test_configured_quality_thresholds_are_reused():
    data=rows();data[5]['avg_logprob']=-.8
    assert assessment(data,100)==([],[])
    assert assessment(data,100,{'logprob_threshold':-.7})[0]


def test_crashed_worker_report_can_recover_but_exhausted_disagreement_cannot(tmp_path):
    from mynotes.asr_recovery import recovery_candidate
    directory=tmp_path/'asr';identifier='b'*32;attempt=directory/'attempts'/identifier;attempt.mkdir(parents=True)
    wav(attempt/'input.wav');atomic_json_write(attempt/'primary-result.json',{'engine':'argmax','model':'fixture','segments':rows()})
    atomic_json_write(directory/'status.json',{'attempt_id':identifier,'status':'RUNNING','pid':99999999})
    assert recovery_candidate(directory)['attempt_id']==identifier
    atomic_json_write(attempt/'repair-plan.json',{'policy_version':POLICY_VERSION,'issues':[{'code':'MATERIAL_DISAGREEMENT'}]})
    assert recovery_candidate(directory) is None


def test_engine_report_tampering_stops_recovery(tmp_path):
    directory=tmp_path/'asr';identifier='c'*32;attempt=directory/'attempts'/identifier;(attempt/'primary').mkdir(parents=True)
    audio=wav(attempt/'input.wav');atomic_json_write(attempt/'primary-result.json',{'engine':'argmax','model':'fixture','segments':rows()})
    raw=attempt/'primary/raw.json';atomic_json_write(raw,{'segments':rows()})
    atomic_json_write(directory/'status.json',{'attempt_id':identifier,'status':'NEEDS_REVIEW'})
    receipt=attest_recovery(directory,'source')
    raw.write_text('{"segments":[]}')
    with pytest.raises(asr.ASRError,match='REPORT_CHANGED'):load_recovery(receipt,'source',audio)



def test_repetition_across_segment_boundaries_is_verified_without_deleting_words():
    data=rows()
    for index in (5,6,7):data[index]['text']='Thank you.'
    before=json.loads(json.dumps(data))
    issues,windows=assessment(data,100)
    assert sum(i['code']=='REPETITIVE_SEGMENTS' for i in issues)==3 and windows
    assert data==before
