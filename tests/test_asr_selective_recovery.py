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


def test_primary_prompt_is_disabled_by_default(tmp_path,monkeypatch,ready_argmax):
    calls=[]
    monkeypatch.setattr(asr,'_tool_version',lambda _:'fixture')
    monkeypatch.setattr(asr,'_run',lambda cmd:calls.append(cmd) or subprocess.CompletedProcess(cmd,0,'{"segments":[{"start":0,"end":1,"text":"Hello"}]}',''))
    engine=asr.ArgmaxEngine('/usr/bin/true',tmp_path/'models',tmp_path/'tokens')
    ready_argmax(engine, 'fixture')
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


def test_disagreement_completes_with_alternatives_and_real_replacement_timing(tmp_path):
    data=rows();data[5]['end']-=1.02;directory=tmp_path/'a';directory.mkdir()
    fixed, report = repair(asr.EngineResult('argmax','x','x',data),audio=wav(tmp_path/'input.wav'),duration=100,
        engine=Local(rows(),9,bad=True),settings={'language':'en','vocabulary':[]},directory=directory,update=lambda *a,**k:None)
    assert 'MATERIAL_DISAGREEMENT' in {w['code'] for w in report['warnings']}
    assert fixed.segments[5]['text']=='Completely different 12345.'
    assert fixed.segments[5]['asr_alternative']=='foxtrot.'
    assert fixed.segments[5]['source_engine']=='fluidaudio'
    assert report['comparisons'][0]['original_text']=='foxtrot.'
    assert report['attempted_clips'] and report['completion_policy']=='checked_with_warnings'


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


def test_uncertain_boundary_retains_valid_primary_after_second_check(tmp_path):
    original=rows();original[5]['avg_logprob']=-1.2
    class MissingBoundary(Local):
        def transcribe(self,*a,**kw):
            result=super().transcribe(*a,**kw)
            result.segments[5]['text']='Lost boundary.'
            return result
    directory=tmp_path/'a';directory.mkdir();engine=MissingBoundary(rows(),9)
    fixed,report=repair(asr.EngineResult('argmax','x','x',original),audio=wav(tmp_path/'input.wav'),duration=100,
        engine=engine,settings={'language':'en','vocabulary':[]},directory=directory,update=lambda *a,**k:None)
    assert engine.calls==1 and fixed.segments[5]['text']=='foxtrot.'
    assert fixed.segments[5]['asr_alternative']=='Lost boundary.'
    assert fixed.segments[5]['checked_by']=='fluidaudio'
    assert fixed.segments[5]['source_engine']=='argmax'
    assert 'REPAIR_BOUNDARY_MISMATCH' in fixed.segments[5]['uncertainty_codes']
    assert any(w['code']=='RESIDUAL_ENGINE_QUALITY_WARNING' for w in report['warnings'])


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


def test_review_summary_distinguishes_completed_checks_from_pending_verification():
    from mynotes.asr_recovery import review_summary
    assert 'timestamp incoerenti' in review_summary([{'code':'INVALID_SEGMENT_INTERVAL'}])
    assert 'timestamp incoerenti' in review_summary([{'code':'SEGMENTS_OUT_OF_ORDER'}])
    assert 'Confronto del testo' in review_summary([{'code':'REPAIR_BOUNDARY_MISMATCH'}])
    assert review_summary([]) is None


def test_free_recordings_find_existing_managed_fluid_without_course_setup(tmp_path,monkeypatch):
    from mynotes.workspace import Workspace
    monkeypatch.setattr('mynotes.onboarding.managed_tools',lambda:{})
    monkeypatch.setattr('mynotes.onboarding.managed_tool',lambda name:'/usr/bin/true' if name=='fluidaudio' else None)
    monkeypatch.setattr(asr.shutil,'which',lambda _:None)
    registry=asr.default_engines(Workspace(tmp_path),{'tools':{}})
    assert registry['fluidaudio'].executable=='/usr/bin/true'


def test_independent_pass_continues_after_disagreement_and_final_timing_still_blocks(tmp_path):
    original=rows(); original += [{**s,'start':s['start']+40,'end':s['end']+40} for s in rows()]
    original[5]['avg_logprob']=-1.2;original[25]['avg_logprob']=-1.2
    engine=Local(rows()+[{**s,'start':s['start']+40,'end':s['end']+40} for s in rows()],0,bad=True)
    d=tmp_path/'attempt';d.mkdir()
    fixed,plan=repair(asr.EngineResult('argmax','x','x',original),audio=wav(tmp_path/'a.wav',seconds=150),duration=150,
        engine=engine,settings={'language':'en','vocabulary':[],'_force_fresh':True},directory=d,update=lambda *a,**k:None)
    assert engine.calls==2 and len(plan['attempted_clips'])==2
    assert fixed.segments[5]['text']==original[5]['text'] and fixed.segments[25]['text']==original[25]['text']
    class Invalid(Local):
        def transcribe(self,*a,**kw):
            result=super().transcribe(*a,**kw);result.segments[0]['end']=result.segments[0]['start'];return result
    other=tmp_path/'invalid';other.mkdir()
    with pytest.raises(asr.ASRReviewError):
        repair(asr.EngineResult('argmax','x','x',original),audio=tmp_path/'a.wav',duration=150,
            engine=Invalid(rows(),0),settings={'language':'en','vocabulary':[],'_force_fresh':True},directory=other,update=lambda *a,**k:None)


def test_completed_uncertain_transcript_exports_archives_and_prepares_without_asr(tmp_path,monkeypatch):
    from mynotes.workspace import Workspace
    from mynotes import transcription_queue as q, recording_library as lib
    from mynotes.sourcepack import _import_transcript
    monkeypatch.setenv('MYNOTES_CAPTURE_HOME',str(tmp_path/'native'))
    monkeypatch.setattr('mynotes.onboarding.managed_tools',lambda:{})
    ws=Workspace(tmp_path/'private');ws.initialize();cfg=ws.configuration_store().load();cfg['ingestion']['stable_seconds']=0;ws.configuration_store().save(cfg)
    folder=tmp_path/'Originals';lib.set_folder(ws,folder);audio=wav(folder/'External.wav');digest=file_sha256(audio)
    class Primary:
        name='argmax'
        def transcribe(self,*a,**kw):
            data=rows();data[5]['avg_logprob']=-1.2
            return asr.EngineResult('argmax',kw['model'],'fixture',data,'segment')
    engine=Local(rows(),0,bad=True);registry={'argmax':Primary(),'fluidaudio':engine}
    monkeypatch.setattr(asr,'default_engines',lambda *a:registry)
    item=q.library(ws)[0];q.enqueue(ws,recording_id=item['id']);job=q.status(ws)['jobs'][0]
    import os
    with q.database(ws) as c:c.execute("UPDATE jobs SET status='RUNNING',pid=? WHERE id=?",(os.getpid(),job['id']))
    result=q.run_job(ws,job['id'],engines=registry)
    assert result['status']=='COMPLETED' and result['quality_summary'] and result['archive_status']=='COMPLETED'
    assert file_sha256(Path(result['current_audio_path']))==digest and engine.calls==1
    text=Path(result['export_text']).read_text()
    assert '[passaggio incerto]' in text and 'Timestamp indicativi' in text
    saved=json.loads(Path(result['transcript_json']).read_text())
    assert saved['status']=='COMPLETED' and saved['human_verified'] is False and saved['quality_warnings']
    assert saved['segments'][5]['text']=='foxtrot.' and saved['segments'][5]['needs_review']
    imported=_import_transcript(Path(result['transcript_json']),'QA-L01',bound_recording_id=item['id'])
    assert imported['quality_warnings']==saved['quality_warnings'] and imported['segments'][5]['asr_alternative']
    item=q.library(ws)[0]
    assert item['transcription_status']=='COMPLETED' and item['quality_summary'] and 'restart' not in item['actions']
    assert q.status(ws)['jobs'][0]['quality_summary']


def test_previous_valid_policy_two_cache_does_not_repeat_asr(tmp_path,monkeypatch):
    from mynotes.workspace import Workspace
    from mynotes.configuration import course_template
    from test_transcription_reliability import Fake
    monkeypatch.setattr('mynotes.onboarding.managed_tools',lambda:{})
    ws=Workspace(tmp_path/'private');ws.initialize();course=course_template();course.update(code='QA',name='QA')
    engine=Fake();engine.name='argmax';registry={'argmax':engine};audio=wav(tmp_path/'original.wav')
    asr.transcribe_audio(ws,'QA-L01',course,audio,engines=registry)
    path=ws.runs_dir/'QA-L01/asr/transcript.json';payload=json.loads(path.read_text());payload['repair_policy_version']=2
    _,payload['settings_hash'],payload['cache_key'],_=asr.cache_identity(ws,course,audio,engines=registry,repair_policy_version=2)
    atomic_json_write(path,payload)
    result=asr.transcribe_audio(ws,'QA-L01',course,audio,engines=registry)
    assert result['cache_reused'] and engine.calls==1


def test_verified_empty_secondary_keeps_primary_with_warning_but_not_empty_primary(tmp_path):
    data=rows();data[5]['avg_logprob']=-1.2
    class Silent:
        name='fluidaudio'
        def transcribe(self,*a,**kw):return asr.EngineResult('fluidaudio','v2','fixture',[],'word',True)
    d=tmp_path/'asr/attempts/a';d.mkdir(parents=True)
    result,plan=repair(asr.EngineResult('argmax','x','x',data),audio=wav(tmp_path/'a.wav'),duration=100,
        engine=Silent(),settings={'language':'en','vocabulary':[]},directory=d,update=lambda *a,**k:None)
    assert result.segments[5]['text']=='foxtrot.'
    assert 'EMPTY_REPAIR' in result.segments[5]['uncertainty_codes'] and plan['attempted_clips'][0]['verified_empty']
    with pytest.raises(asr.ASRReviewError):asr.validate_engine_result(asr.EngineResult('argmax','x','x',[]),d/'empty-primary.json',100)


@pytest.mark.parametrize('valid',[False,True])
def test_fluid_distinguishes_no_speech_from_malformed_output(tmp_path,monkeypatch,valid):
    source=wav(tmp_path/'source.wav')
    def run(cmd):
        output=Path(cmd[cmd.index('--output-json')+1])
        payload={'text':'','wordTimings':[],'durationSeconds':100,'audioFile':str(source)} if valid else {}
        atomic_json_write(output,payload)
        return subprocess.CompletedProcess(cmd,0,'','')
    monkeypatch.setattr(asr,'_run',run);monkeypatch.setattr(asr,'_tool_version',lambda _:'fixture')
    engine=asr.FluidAudioEngine('/usr/bin/true')
    if valid:
        result=engine.transcribe(source,tmp_path/'out',model='v2',language='en',vocabulary=[],prompt='')
        assert result.verified_empty and result.segments==[]
    else:
        with pytest.raises(asr.ASRError,match='no readable'):engine.transcribe(source,tmp_path/'out',model='v2',language='en',vocabulary=[],prompt='')
