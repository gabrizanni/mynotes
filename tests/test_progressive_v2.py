"""Synthetic transport, risk and publication-contract checks; no model calls."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest

from mynotes import progressive_wire as w, progressive_review as r, agy_progressive as a
from mynotes.providers import ProviderError, ProviderRequest, RequestedModel, _context
from mynotes.utils import atomic_json_write, file_sha256


def request(tmp_path, quality='standard'):
    tmp_path.mkdir(exist_ok=True,parents=True)
    return ProviderRequest('TEST-L01',{'languages':{'notes':'en'}},{},RequestedModel('agy','gemini-test','high',quality),
        source_pack={'lecture_id':'TEST-L01','evidence':[
            {'id':'S001','kind':'slide','content':'Cost and service interact.'},
            {'id':'S002','kind':'slide','content':'Maintenance preserves service.'},
            {'id':'T001','kind':'transcript','content':'Cost and service interact.','start_seconds':0.0,'end_seconds':2.5},
            {'id':'T002','kind':'transcript','content':'Adequate inventory supports service.','start_seconds':2.5,'end_seconds':5.0},
            {'id':'T003','kind':'transcript','content':'Maintenance preserves service.','start_seconds':0.0,'end_seconds':5.0}], 'visuals':[]},
        run_dir=tmp_path,style={'recap':True,'recap_language':'en','question_count':2,'image_policy':'essential'},
        batch_context={'progressive_protocol':2,'lessons':['TEST-L01','TEST-L02'],
            'lesson_evidence':{'TEST-L01':['S001','S002','T001','T002'],'TEST-L02':['S001','S002','T003']}})


def unit(index=0, second=False):
    lesson='TEST-L02' if second else 'TEST-L01';s='S002' if second else 'S001';t='T003' if second else 'T001..T002'
    return {'version':2,'part_index':index,'lecture_id':lesson,'header':{'title':'Maintenance' if second else 'Cost and service','summary':'Source-backed lesson.'},
        'blocks':[{'kind':'overview','items':['Maintenance' if second else 'Cost and service'],'evidence_ids':[s]},
            {'kind':'paragraph','text':'Maintenance preserves service.' if second else 'Adequate inventory supports service while inventory incurs cost.',
             'evidence_ids':[s,t],'coverage_role':'full'},
            {'kind':'qa','question':'What does this mechanism accomplish?','answer':'It supports service.','evidence_ids':[s]},
            {'kind':'recap','text':'The mechanism supports service.','evidence_ids':[s]}],
        'organization_delta':[{'canonical_slide_id':s,'owner':lesson,'status':'taught','reason':'Explicit spoken source.','evidence_ids':[t]}],
        'visual_delta':[],'lesson_complete':True,'complete':second,'next_topic':'' if second else 'Maintenance in lesson two'}


def test_compact_projection_is_lossless_including_uncertainty(tmp_path):
    req=request(tmp_path)
    req.source_pack['evidence'][2].update(speaker_role='UNKNOWN',uncertainty_codes=['uncertain'],asr_alternative='Exact alternative')
    data=json.loads(_context(req).splitlines()[-1])
    restored=w.restore_sources(data)
    assert restored[2]['start_seconds']==0.0 and restored[2]['end_seconds']==2.5
    assert restored[2]['content']=='Cost and service interact.' and restored[2]['asr_alternative']=='Exact alternative'
    assert restored[2]['uncertainty_codes']==['uncertain']
    assert 'untrusted_evidence' not in data['evidence'][0]


@pytest.mark.parametrize('reference',['T001..T003','T002..T001','T001..T999','T000..T002'])
def test_bad_ranges_never_cross_missing_or_lesson_boundaries(tmp_path,reference):
    req=request(tmp_path)
    with pytest.raises(ProviderError):w.expand({'evidence_ids':[reference]},{e['id'] for e in req.source_pack['evidence']},w.origins(req))


def test_units_reconstruct_once_without_parallel_narrative(tmp_path):
    req=request(tmp_path);one,proof=w.validate_unit(req,unit(),[]);two,_=w.validate_unit(req,unit(1,True),[one])
    w.validate_content(req,one,[]);w.validate_content(req,two,[one])
    notes=w.assemble(req,[one,two])
    assert proof and len(notes['organization'])==2 and len(notes['lessons'])==2
    assert notes['lessons'][0]['notes']['blocks'][1]['evidence_ids']==['S001','T001','T002']
    assert notes['lessons'][0]['notes']['sections']==[]
    repeated=deepcopy(unit(1,True));repeated['organization_delta'].append(unit()['organization_delta'][0])
    with pytest.raises(ProviderError,match='repeats settled'):w.validate_unit(req,repeated,[one])


def test_unit_limits_apply_before_another_model_request(tmp_path):
    req=request(tmp_path);value=unit();value['blocks'][1]['text']='x'*(w.MAX_UNIT_BYTES+1)
    with pytest.raises(ProviderError,match='32 KiB'):w.validate_unit(req,value,[])
    value=unit();value['blocks']*=2
    with pytest.raises(ProviderError,match='six blocks'):w.validate_unit(req,value,[])


def test_standard_selects_weak_points_without_unknown_or_term_only(tmp_path):
    req=request(tmp_path);evidence={e['id']:e for e in req.source_pack['evidence']}
    block={'kind':'paragraph','text':'[[term:inventory]] supports service.','evidence_ids':['T001']}
    evidence['T001']['speaker_role']='UNKNOWN'
    assert r.risks(block,evidence,req.style,'standard')==[]
    assert r.risks({**block,'text':'SHAP proves a 40% causal improvement.'},evidence,req.style,'standard')
    assert r.risks(block,evidence,req.style,'approfondito')==['extended_source_review']
    assert r.risks({**block,'kind':'table'},evidence,req.style,'base')==[]


def test_risk_packets_never_serialize_unrelated_anchors(tmp_path):
    req=request(tmp_path,'approfondito');value,_=w.validate_unit(req,unit(),[])
    tasks=r.packets(req,value,tmp_path/'review')
    assert all(len(packet)<=3 for packet in tasks)
    for targets in tasks:
        packet=r.packet_request(req,targets,value,tmp_path/'review')
        context=json.loads(_context(packet).splitlines()[1])
        assert set(context['coordinated_batch'])=={'progressive_protocol'}
        untrusted=json.loads(_context(packet).splitlines()[-1])
        assert len(untrusted['review_task']['targets'])<=3
        assert 'T003' not in {e['id'] for e in packet.source_pack['evidence']}


def test_risk_patch_is_anchored_and_cannot_touch_other_blocks(tmp_path):
    req=request(tmp_path);value,_=w.validate_unit(req,unit(),[]);block=value['blocks'][1]
    target={'block_id':'B1','base_sha256':r.digest(block),'block':block,'reasons':['test']}
    replacement={**block,'text':'A source-supported correction.'}
    reply={'decisions':[{'block_id':'B1','base_sha256':r.digest(block),'status':'patch','reason':'Observed defect','blocks':[replacement]}], 'visual_updates':[]}
    changed=r.apply_review(req,value,[target],reply)
    assert changed['blocks'][0]==value['blocks'][0] and changed['blocks'][1]['text']==replacement['text']
    assert value['blocks'][1]==block
    reply['decisions'][0]['base_sha256']='changed'
    with pytest.raises(ProviderError,match='stale'):r.apply_review(req,value,[target],reply)


def test_falsely_claimed_image_inspection_is_blocked(tmp_path):
    from PIL import Image
    req=request(tmp_path);image=tmp_path/'page.png';Image.new('RGB',(20,20),'orange').save(image)
    req.source_pack['visuals']=[{'evidence_id':'S001','image_path':str(image),'visual_decision':'candidate'}]
    value=unit();value['visual_delta']=[{'evidence_id':'S001','decision':'include','reason':'Relational diagram','inspected':True,'content_kind':'diagram'}]
    with pytest.raises(ProviderError,match='no observed'):a.image_check(req,value,[])
    event={'event':'step_update','step_update':{'state':'DONE','tool_name':'read_file','tool_info':{'parameters':{'Path':str(image)},'output':'image'}}}
    observed=a.observed_access(event,tmp_path)
    a.image_check(req,value,[observed])


def test_complete_error_cannot_publish_or_restart_a_risk_packet(tmp_path,monkeypatch):
    req=request(tmp_path);value,_=w.validate_unit(req,unit(),[])
    calls=[]
    def fail(packet):calls.append(1);raise ProviderError('ERROR envelope')
    with pytest.raises(ProviderError,match='ERROR'):r.review_unit(req,value,tmp_path,fail)
    with pytest.raises(ProviderError,match='already consumed'):r.review_unit(req,value,tmp_path,fail)
    assert calls==[1]


def test_stream_reconstruction_and_reopen_without_any_model(tmp_path,monkeypatch):
    from mynotes import agy_parts
    req=request(tmp_path)
    monkeypatch.setattr(a,'command',lambda *args:['synthetic'])
    monkeypatch.setattr(a,'_stage_agy_writer',lambda path:None)
    calls=[]
    def run(command,prompt,accept,**kwargs):
        calls.append(1);kwargs['on_event']({'event':'init','conversation_id':'same-conversation'})
        events=[]
        for index,raw in enumerate([unit(),unit(1,True)]):
            envelope={'status':'SUCCESS','conversation_id':'same-conversation','response':json.dumps(raw)}
            events.append({'event':'result','result':envelope})
            next_prompt=accept(envelope)
            assert (next_prompt is None)==bool(index)
        return subprocess.CompletedProcess(command,0,'\n'.join(json.dumps(e) for e in events),'')
    def single(packet):
        from mynotes.providers import _save_attempt
        from mynotes.execution import AttemptStart
        targets=packet.batch_context['review_task']['targets']
        reply={'decisions':[{'block_id':t['block_id'],'base_sha256':t['base_sha256'],'status':'approved','reason':'Supported','blocks':[]} for t in targets],'visual_updates':[]}
        envelope={'status':'SUCCESS','response':json.dumps(reply)}
        attempt=_save_attempt(packet,command=['synthetic'],completed=subprocess.CompletedProcess(['synthetic'],0,json.dumps({'event':'result','result':envelope}),''),started=AttemptStart(),envelope={'status':'SUCCESS','source_access':[]})
        return reply,attempt
    monkeypatch.setattr(agy_parts,'run_session',run);monkeypatch.setattr(a,'run_single',single)
    result=a.generate(req)
    assert len(result.notes['lessons'])==2
    record=json.loads(Path(result.attempt_path).read_text());assert a.verify_receipt(record,tmp_path)==result.notes
    again=a.generate(req);assert again.notes==result.notes and calls==[1]
    file=next((tmp_path/'progressive-v2').glob('unit-*.json'));file.write_text('{}')
    with pytest.raises(ProviderError,match='unit changed'):a.verify_receipt(record,tmp_path)


def test_stream_stops_on_observed_provider_retry_not_on_academic_text(tmp_path):
    import sys,os
    from mynotes.agy_parts import run_session
    code="""import json,sys,time
sys.stdin.readline()
print(json.dumps({'event':'step_update','step_update':{'step_type':'error','state':'DONE','message':'Your previous response was cut off because it exceeded the output token limit'}}),flush=True)
time.sleep(30)
"""
    with pytest.raises(ProviderError,match='truncated'):
        run_session([sys.executable,'-u','-c',code],'test',lambda _:None,cwd=str(tmp_path),env=dict(os.environ),abort_errors=True,total_seconds=3)
    good="""import json,sys
sys.stdin.readline()
print(json.dumps({'event':'step_update','step_update':{'step_type':'tool','state':'DONE','tool_info':{'output':'The lecture explains unexpected EOF in a program'}}}),flush=True)
print(json.dumps({'event':'result','result':{'status':'SUCCESS'}}),flush=True)
"""
    result=run_session([sys.executable,'-u','-c',good],'test',lambda _:None,cwd=str(tmp_path),env=dict(os.environ),abort_errors=True,total_seconds=3)
    assert result.returncode==0


def install_fake_transport(monkeypatch, calls, invalid=False):
    from mynotes import agy_parts
    from mynotes.providers import _save_attempt
    from mynotes.execution import AttemptStart
    from test_autonomous_organization import IntegratedWriter
    holder={}
    def command(req,schema,log):holder['request']=req;return ['synthetic']
    monkeypatch.setattr(a,'command',command)
    def run(command,prompt,accept,**kwargs):
        req=holder['request'];calls.append(req);kwargs['on_event']({'event':'init','conversation_id':'same-conversation'})
        legacy=IntegratedWriter().generate(req).notes
        aliases={row['canonical_slide_id']:row['evidence_id'] for row in req.batch_context['ownership']}
        events=[]
        for index,row in enumerate(legacy['lessons']):
            notes=row['notes'];own=[{**d,'canonical_slide_id':aliases[d['canonical_slide_id']]} for d in legacy['organization'] if d['owner']==row['lecture_id']]
            raw={'version':2,'part_index':index,'lecture_id':row['lecture_id'],
                'header':{'title':notes['title'],'summary':notes['summary']},'blocks':notes['blocks'],
                'organization_delta':own,'visual_delta':[],'lesson_complete':True,
                'complete':index==len(legacy['lessons'])-1,'next_topic':'' if index==len(legacy['lessons'])-1 else 'Next lesson'}
            if invalid:raw['blocks'][1]={'kind':'heading','level':2,'text':'Bad heading','evidence_ids':raw['blocks'][1]['evidence_ids']}
            envelope={'status':'SUCCESS','conversation_id':'same-conversation','response':json.dumps(raw)}
            events.append({'event':'result','result':envelope});accept(envelope)
        return subprocess.CompletedProcess(command,0,'\n'.join(json.dumps(e) for e in events),'')
    def single(packet,*args):
        targets=packet.batch_context['review_task']['targets']
        reply={'decisions':[{'block_id':t['block_id'],'base_sha256':t['base_sha256'],'status':'approved','reason':'Source-backed','blocks':[]} for t in targets],'visual_updates':[]}
        envelope={'status':'SUCCESS','response':json.dumps(reply)}
        attempt=_save_attempt(packet,command=['synthetic'],completed=subprocess.CompletedProcess(['synthetic'],0,json.dumps({'event':'result','result':envelope}),''),started=AttemptStart(),envelope={'status':'SUCCESS','source_access':[]})
        return reply,attempt
    monkeypatch.setattr(agy_parts,'run_session',run);monkeypatch.setattr(a,'run_single',single)


def test_notes_v2_delivers_both_and_regenerate_preserves_original_job(tmp_path,monkeypatch):
    from mynotes import notes_run,publication,batch_documents
    from test_autonomous_organization import prepared
    from test_publication import fake_render
    ws=prepared(tmp_path)
    config=ws.configuration_store().load();config['providers']['configured']['agy']={'model':'gemini-test','effort':'high'};ws.configuration_store().save(config)
    calls=[];install_fake_transport(monkeypatch,calls)
    monkeypatch.setattr(publication,'_render_and_validate',fake_render);monkeypatch.setattr(batch_documents,'_render_and_validate',fake_render)
    result=notes_run.run_notes(ws,'OPS',[1,2],provider='agy')
    assert result['status'] in {'COMPLETED','COMPLETED_WITH_WARNINGS'},result.get('problem')
    assert len(result['delivery']['files'])==3
    again=notes_run.run_notes(ws,'OPS',resume=result['batch_id'])
    assert again['reused'] and len(calls)==1
    old=(ws.runs_dir/'_batches'/result['batch_id']/'manifest.json').read_bytes()
    regenerated=notes_run.run_notes(ws,'OPS',[1,2],provider='agy',regenerate=True)
    assert regenerated['batch_id']!=result['batch_id'] and len(calls)==2
    assert (ws.runs_dir/'_batches'/result['batch_id']/'manifest.json').read_bytes()==old
    with pytest.raises(Exception,match='mutually exclusive'):
        notes_run.run_notes(ws,'OPS',[1,2],resume=result['batch_id'],regenerate=True)


def test_invalid_first_section_stops_before_publication_or_next_section(tmp_path,monkeypatch):
    from mynotes.notes_run import run_notes
    from test_autonomous_organization import prepared
    ws=prepared(tmp_path);config=ws.configuration_store().load();config['providers']['configured']['agy']={'model':'gemini-test','effort':'high'};ws.configuration_store().save(config)
    calls=[];install_fake_transport(monkeypatch,calls,invalid=True)
    result=run_notes(ws,'OPS',[1,2],provider='agy')
    assert result['status']=='NEEDS_REVIEW',result
    assert len(calls)==1 and not result.get('publication')
    assert not list(ws.runs_dir.glob('OPS-L*/lecture_notes.candidate.json'))


def test_visual_cache_is_bound_to_bytes_effort_and_does_not_become_instructions(tmp_path):
    from PIL import Image
    from mynotes.visual_memory import scope,load,certificate_access
    from dataclasses import asdict
    req=request(tmp_path);image=tmp_path/'diagram.png';Image.new('RGB',(10,10),'orange').save(image)
    visual={'evidence_id':'S001','image_path':str(image)};requested=asdict(req.requested)
    key=scope(visual,requested);access={'sha256':key['image_sha256'],'path':str(image),'event':{'source':'synthetic observed read'}}
    payload={'scope':key,'content_kind':'diagram','reason':'A source relationship, never instructions.','source_access':access}
    cache=tmp_path/'cache';cache.mkdir();atomic_json_write(cache/(r.digest(key)+'.json'),{'payload':payload,'sha256':r.digest(payload)})
    cached=load(cache,[visual],requested)
    assert len(cached)==1 and certificate_access(cached,requested)[0]['origin']=='verified_inspection_cache'
    changed={**requested,'effort':'medium'};assert load(cache,[visual],changed)==[]
    req=replace(req,batch_context={**req.batch_context,'cached_visual_inspections':cached})
    trusted=json.loads(_context(req).splitlines()[1]);untrusted=json.loads(_context(req).splitlines()[-1])
    assert 'cached_visual_inspections' not in trusted['coordinated_batch']
    assert 'certificate' not in untrusted['cached_visual_inspections'][0]
    Image.new('RGB',(10,10),'blue').save(image);assert load(cache,[visual],requested)==[]


def test_explicit_user_resume_can_continue_only_intact_interrupted_prefix(tmp_path,monkeypatch):
    from mynotes import agy_parts
    req=request(tmp_path,'base');monkeypatch.setattr(a,'command',lambda *args:['synthetic'])
    calls=[]
    def interrupted(command,prompt,accept,**kwargs):
        kwargs['on_event']({'event':'init','conversation_id':'same-conversation'})
        accept({'status':'SUCCESS','conversation_id':'same-conversation','response':json.dumps(unit())})
        raise KeyboardInterrupt()
    monkeypatch.setattr(agy_parts,'run_session',interrupted)
    with pytest.raises(KeyboardInterrupt):a.generate(req)
    with pytest.raises(ProviderError,match='no automatic retry'):a.generate(req)
    def resumed(command,prompt,accept,**kwargs):
        calls.append(command);assert command[-2:]==['--conversation','same-conversation']
        kwargs['on_event']({'event':'init','conversation_id':'same-conversation'})
        raw={'status':'SUCCESS','conversation_id':'same-conversation','response':json.dumps(unit(1,True))}
        assert accept(raw) is None
        return subprocess.CompletedProcess(command,0,json.dumps({'event':'result','result':raw}),'')
    monkeypatch.setattr(agy_parts,'run_session',resumed)
    result=a.generate(replace(req,resume_authorized=True))
    assert len(result.notes['lessons'])==2 and len(calls)==1


def test_required_final_visual_review_runs_before_any_course_replace(tmp_path,monkeypatch):
    from mynotes import course_update,publication
    from test_publication import prepared,fake_render
    ws,target=prepared(tmp_path);monkeypatch.setattr(publication,'_render_and_validate',fake_render)
    original=target.read_bytes() if target.exists() else None
    def reject(built):
        assert Path(built['candidate']).is_file()
        raise ProviderError('NEEDS_REVIEW: rendered page defect')
    with pytest.raises(ProviderError,match='rendered page'):
        course_update.update_course(ws,['OPS7-L01'],before_publish=reject)
    assert (target.read_bytes() if target.exists() else None)==original
