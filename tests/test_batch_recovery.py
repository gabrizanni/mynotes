from copy import deepcopy
from dataclasses import asdict,replace
import json
from pathlib import Path
import os,sys,subprocess,time
import pytest
from mynotes import agy_parts as a
from mynotes.batch import CoverageError,generate_batch,plan_batch,_packs,_request,_hash
from mynotes.batch_recovery import batch_status,repair_coverage,compatible_response
from mynotes.providers import ProviderError,ProviderQuotaError,ProviderTransportError,ProviderResponse
from mynotes.utils import atomic_json_write,file_sha256
from mynotes.workflow import WorkflowError
from test_batch_generation import workspace_and_deck,Adapter,notes_for
from test_agy_parts import request,portion,_fake_session


def test_one_network_recovery_saves_init_before_first_portion(tmp_path,monkeypatch):
    req=request(tmp_path);calls=[];waits=[]
    monkeypatch.setattr(a,'provider_executable',lambda _:sys.executable)
    monkeypatch.setattr(a.time,'sleep',lambda n:waits.append(n))
    def run(command,prompt,accept,**kw):
        calls.append((list(command),prompt))
        kw['on_event']({'event':'init','conversation_id':'same-cid'})
        if len(calls)==1:raise ProviderTransportError('connection reset by peer')
        value=portion(0,True);raw={'conversation_id':'same-cid','status':'SUCCESS','response':json.dumps(value),'usage':{}}
        assert accept(raw) is None
        return subprocess.CompletedProcess(command,0,json.dumps({'event':'result','result':raw})+'\n','')
    monkeypatch.setattr(a,'run_session',run)
    result=a.generate_parts(req)
    assert len(calls)==2 and waits.count(10)==1 and '--conversation' in calls[1][0]
    assert calls[1][0][-1]=='same-cid' and 'Resume part_index 0' in calls[1][1]
    state=json.loads(next(tmp_path.glob('writing-parts/*/state.json')).read_text())
    assert state['network_retries']==1 and state['finalized'] is True
    a.verify_receipt(json.loads(Path(result.attempt_path).read_text()),tmp_path)


@pytest.mark.parametrize('failure',[ProviderQuotaError('quota exhausted'),ProviderError('invalid source')])
def test_non_network_error_does_not_retry(tmp_path,monkeypatch,failure):
    calls=[];monkeypatch.setattr(a,'provider_executable',lambda _:sys.executable)
    def run(*args,**kwargs):calls.append(1);raise failure
    monkeypatch.setattr(a,'run_session',run)
    with pytest.raises(type(failure)):a.generate_parts(request(tmp_path))
    assert len(calls)==1


def test_second_network_failure_stops_across_resumes(tmp_path,monkeypatch):
    calls=[];monkeypatch.setattr(a,'provider_executable',lambda _:sys.executable);monkeypatch.setattr(a.time,'sleep',lambda _:None)
    def run(*args,**kwargs):calls.append(1);raise ProviderTransportError('broken pipe')
    monkeypatch.setattr(a,'run_session',run)
    with pytest.raises(ProviderTransportError):a.generate_parts(request(tmp_path))
    assert len(calls)==2
    with pytest.raises(ProviderTransportError):a.generate_parts(request(tmp_path))
    assert len(calls)==3  # explicit new call, no second automatic recovery


def test_structural_review_rejects_notebook_code_before_provider(tmp_path):
    ws,deck,sel=workspace_and_deck(tmp_path,lessons=1,slides=1)
    from PIL import Image
    path=ws.runs_dir/'OPS-L01/source_pack.json';pack=json.loads(path.read_text());image=path.parent/'page.png';Image.new('RGB',(10,10),'white').save(image)
    pack['evidence'][0]['content']='Notebook title\nimport pandas as pd\nfrom sklearn.metrics import accuracy_score'
    pack['visuals']=[{'evidence_id':'S001','source_file':str(deck),'page':1,'image_path':str(image)}];atomic_json_write(path,pack)
    sel[next(iter(sel))]={'owner':'OPS-L01','status':'material','structural_review':{'kind':'title','reason':'Header','image_sha256':file_sha256(image)}}
    with pytest.raises(WorkflowError,match='code or data'):plan_batch(ws,'OPS',[1],selection=sel)


@pytest.mark.parametrize('anchor_kind',['heading','paragraph'])
def test_targeted_insert_preserves_all_existing_blocks_and_stops_paid_retry(tmp_path,anchor_kind):
    ws,deck,selection=workspace_and_deck(tmp_path,lessons=1,slides=2)
    plan=plan_batch(ws,'OPS',[1],selection=selection);packs,sources,inventory=_packs(ws,['OPS-L01'])
    from mynotes.providers import RequestedModel
    runtime={**plan,'packs':packs,'sources':sources,'inventory':inventory,'requested':RequestedModel(**plan['requested_model'])}
    req,maps=_request(runtime,['OPS-L01'],tmp_path/'run',[]);notes=notes_for(req,'OPS-L01')
    original_image=tmp_path/'source-image.png';original_image.write_bytes(b'verified source image')
    staged_image=req.run_dir/'visuals/source-image.png'
    req=replace(req,source_pack={**req.source_pack,'visuals':[{'evidence_id':'S002','image_path':str(staged_image),
        'original_image_path':str(original_image),'image_sha256':file_sha256(original_image)}]})
    notes['blocks']=[b for b in notes['blocks'] if not(b.get('kind')=='paragraph' and b['evidence_ids']==['S002'])]
    heading={'kind':anchor_kind,'text':'Notebook setup','evidence_ids':['S001'],'coverage_role':'full'};notes['blocks'].insert(1,heading)
    response=ProviderResponse({'lessons':[{'lecture_id':'OPS-L01','notes':notes}]},'codex','','')
    defects=[{'lecture_id':'OPS-L01','evidence_id':'S002','source_file':str(deck),'page':2}]
    class Repair:
        calls=0
        def generate(self,r):
            self.calls+=1
            assert staged_image.read_bytes()==original_image.read_bytes()
            assert Path(r.source_pack['visuals'][0]['image_path']).read_bytes()==original_image.read_bytes()
            return ProviderResponse({'insertions':[{'lecture_id':'OPS-L01','after_block_sha256':_hash(heading),'block':{'kind':'paragraph','text':'The setup loads data handling and evaluation libraries.','evidence_ids':['S002'],'coverage_role':'full'}}]},'codex','','')
    adapter=Repair();fixed,lineage=repair_coverage(req,response,defects,maps,adapter,40)
    old=deepcopy(notes['blocks']);blocks=fixed.notes['lessons'][0]['notes']['blocks'];assert len(blocks)==len(old)+1
    assert [b for b in blocks if b not in old]==[blocks[2]] and response.notes['lessons'][0]['notes']['blocks']==old
    again,_=repair_coverage(req,response,defects,maps,adapter,40);assert adapter.calls==1 and again.notes==fixed.notes
    (req.run_dir/'coverage-repair/repair.json').unlink()
    with pytest.raises(WorkflowError,match='already attempted'):repair_coverage(req,response,defects,maps,adapter,40)
    assert adapter.calls==1


def test_batch_status_is_read_only_and_does_not_claim_json_is_word(tmp_path):
    ws,_,sel=workspace_and_deck(tmp_path,lessons=1,slides=2)
    result=generate_batch(ws,'OPS',[1],selection=sel,provider_adapter=Adapter())
    before={str(p):p.read_bytes() for p in ws.root.rglob('*') if p.is_file()}
    status=batch_status(ws,result['batch_id'])
    assert status['lessons'][0]['status']=='CONTENT_READY' and status['lessons'][0]['word_candidate'] is None
    assert before=={str(p):p.read_bytes() for p in ws.root.rglob('*') if p.is_file()}


def test_batch_derives_only_uncited_other_owner_exclusions_without_paid_repair(tmp_path):
    ws,_,sel=workspace_and_deck(tmp_path)
    def omit_other_owner(response,request):
        for row in response['lessons']:
            own={x['evidence_id'] for x in request.batch_context['ownership'] if x['owner']==row['lecture_id']}
            row['notes']['evidence_coverage']=[x for x in row['notes']['evidence_coverage'] if x['evidence_id'] in own]
    writer=Adapter(omit_other_owner)
    result=generate_batch(ws,'OPS',[1,2],selection=sel,provider_adapter=writer)
    assert len(writer.calls)==2 and result['status']=='CANDIDATE_READY'
    saved=json.loads(Path(result['candidates'][0]['candidate']).read_text())['notes']
    assert len(saved['evidence_coverage'])==30
    assert next(x for x in saved['evidence_coverage'] if x['evidence_id']=='S021')['disposition']=='excluded'


@pytest.mark.parametrize('owned,cited',[(True,False),(False,True)])
def test_missing_owned_or_cited_coverage_cannot_be_invented_from_ledger(tmp_path,owned,cited):
    ws,_,sel=workspace_and_deck(tmp_path)
    def omit(response,request):
        notes=response['lessons'][0]['notes'];identifier='S001' if owned else 'S021'
        notes['evidence_coverage']=[x for x in notes['evidence_coverage'] if x['evidence_id']!=identifier]
        if cited:notes['blocks'][1]['evidence_ids'].append(identifier)
    with pytest.raises(WorkflowError,match='omits required evidence coverage'):
        generate_batch(ws,'OPS',[1,2],selection=sel,provider_adapter=Adapter(omit))


def test_reuse_survives_module_only_change_but_not_changed_sources(tmp_path,monkeypatch):
    from mynotes import providers,batch
    ws,deck,sel=workspace_and_deck(tmp_path,lessons=1,slides=2)
    config=ws.configuration_store().load();config['providers']['configured']['agy']={'model':'test-model','effort':'high'};ws.configuration_store().save(config)
    commands=[];monkeypatch.setenv('MYNOTES_AGY_PARTS','1');monkeypatch.setattr(providers,'provider_executable',lambda _:sys.executable);monkeypatch.setattr(a,'provider_executable',lambda _:sys.executable)
    monkeypatch.setattr(providers,'AGY_INLINE_CONTEXT_BYTES',0)
    class Writer:
        def generate(self,req):
            p={'part_index':0,'complete':True,'next_topic':'','result':{'lessons':[{'lecture_id':'OPS-L01','notes':notes_for(req,'OPS-L01')}]}}
            monkeypatch.setattr(a,'run_session',_fake_session([p],commands))
            return providers.AgyAdapter().generate(req)
    first=generate_batch(ws,'OPS',[1],selection=sel,provider='agy',provider_adapter=Writer())
    path=Path(first['candidates'][0]['candidate']);before=path.read_bytes();original_hash=batch.file_sha256
    monkeypatch.setattr(batch,'file_sha256',lambda p:'changed-provider-module-only' if Path(p).name=='providers.py' else original_hash(p))
    second=generate_batch(ws,'OPS',[1],selection=sel,provider='agy',provider_adapter=Writer())
    assert len(commands)==1 and path.read_bytes()==before and second['candidates'][0]['reused'] is True
    # A changed source is never covered by this compatibility rule.
    deck.write_bytes(b'changed academic source')
    changed=generate_batch(ws,'OPS',[1],selection=sel,provider='agy',provider_adapter=Writer())
    assert changed['status']=='NEEDS_PREPARATION'
    assert len(commands)==1


def test_correction_attachments_need_real_narrative_and_preserve_text():
    from mynotes.batch_recovery import apply_correction
    original={'lessons':[{'lecture_id':'OPS-L01','notes':{'blocks':[{'kind':'paragraph','text':'Original explanation','coverage_role':'full','evidence_ids':['S001']}],
         'evidence_coverage':[{'evidence_id':'S002','disposition':'excluded','reason':'Old reason'}],
         'visual_decisions':[{'evidence_id':'S002','decision':'exclude','inspected':False,'reason':'Old reason'}]}}]}
    task={'defects':[{'lecture_id':'OPS-L01','evidence_id':'S002'}]};anchor=_hash(original['lessons'][0]['notes']['blocks'][0])
    patch={'insertions':[],'attachments':[{'lecture_id':'OPS-L01','block_sha256':anchor,'evidence_ids':['S002']}],
           'visual_updates':[{'evidence_id':'S002','decision':'exclude','inspected':True,'reason':'Inspected chart, represented by the existing explanation'}]}
    fixed=apply_correction(original,patch,task);block=fixed['lessons'][0]['notes']['blocks'][0]
    assert block['text']=='Original explanation' and block['evidence_ids']==['S001','S002']
    assert original['lessons'][0]['notes']['blocks'][0]['evidence_ids']==['S001']
    patch['attachments'][0]['evidence_ids']=['S999']
    with pytest.raises(WorkflowError,match='specific coverage defect'):apply_correction(original,patch,task)


@pytest.mark.parametrize('event', [
    {'event':'step_update','step_update':{'step_index':1,'step_type':'tool','state':'DONE'}},
    {'event':'init','conversation_id':'same-conversation'},
])
def test_duplicate_activity_does_not_reset_idle_watchdog(tmp_path,event):
    code='''import sys,json,time
sys.stdin.readline()
for i in range(25):
 print(json.dumps(EVENT),flush=True)
 time.sleep(.02)
'''.replace('EVENT',repr(event))
    start=time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):a.run_session([sys.executable,'-u','-c',code],'start',lambda _:None,cwd=str(tmp_path),env=dict(os.environ),total_seconds=2,first_seconds=.12)
    assert time.monotonic()-start<1


def test_sleep_detection_closes_only_its_transport(tmp_path,monkeypatch):
    from mynotes.providers import ProviderSuspendedError
    class Monitor:
        def poll(self):return 60
    monkeypatch.setattr(a,'SleepMonitor',Monitor)
    code='import sys,time;sys.stdin.readline();time.sleep(10)'
    with pytest.raises(ProviderSuspendedError) as error:
        a.run_session([sys.executable,'-u','-c',code],'start',lambda _:None,cwd=str(tmp_path),env=dict(os.environ),total_seconds=2)
    assert error.value.suspended_seconds==60


def test_actual_legacy_cli_status_does_not_write_workspace(tmp_path,capsys):
    from mynotes.cli import main
    ws,_,sel=workspace_and_deck(tmp_path,lessons=1,slides=2)
    result=generate_batch(ws,'OPS',[1],selection=sel,provider_adapter=Adapter())
    before={str(p):p.read_bytes() for p in ws.root.rglob('*') if p.is_file()}
    assert main(['batch-status',result['batch_id'],'--workspace',str(ws.root),'--json'])==0
    data=json.loads(capsys.readouterr().out);assert data['lessons'][0]['status']=='CONTENT_READY'
    assert before=={str(p):p.read_bytes() for p in ws.root.rglob('*') if p.is_file()}


def test_batch_builds_each_word_before_advancing_and_pauses(tmp_path,monkeypatch):
    from mynotes import publication
    ws,_,sel=workspace_and_deck(tmp_path,lessons=2,slides=30);order=[]
    class Writer(Adapter):
        def generate(self,req):order.append('write-'+req.lecture_id);return super().generate(req)
    def build(workspace,lecture,**kwargs):
        order.append('word-'+lecture);path=workspace.runs_dir/lecture/'word.docx';path.write_bytes(b'isolated synthetic docx marker');return {'candidate':str(path)}
    monkeypatch.setattr(publication,'build_word_candidate',build)
    result=generate_batch(ws,'OPS',[1,2],selection=sel,provider_adapter=Writer(),build_word=True,pause_after='OPS-L01')
    assert order==['write-OPS-L01','word-OPS-L01'] and result['status']=='PAUSED_REVIEW'
    assert batch_status(ws,result['batch_id'])['lessons'][0]['status']=='WORD_READY'


def test_saved_correction_can_use_reserved_local_build_time_without_another_provider_call(tmp_path,monkeypatch):
    from mynotes import batch_recovery,publication
    ws,_,sel=workspace_and_deck(tmp_path,lessons=1,slides=2)
    class Writer:
        calls=[]
        def generate(self,req):
            self.calls.append(req.phase)
            if req.phase=='coverage_repair':
                task=req.batch_context['repair_task'];anchor=next(a for a in task['anchors'] if a['block']['kind']=='paragraph')
                patch={'insertions':[{'lecture_id':'OPS-L01','after_block_sha256':anchor['block_sha256'],
                       'block':{'kind':'paragraph','text':'The second source explains a separate supported mechanism.',
                                'evidence_ids':['S002'],'coverage_role':'full'}}],'attachments':[],'visual_updates':[]}
                return ProviderResponse(patch,'codex','test-model','high')
            notes=notes_for(req,'OPS-L01');notes['blocks']=[b for b in notes['blocks'] if b.get('evidence_ids')!=['S002']]
            return ProviderResponse({'lessons':[{'lecture_id':'OPS-L01','notes':notes}]},'codex','test-model','high')
    original=batch_recovery.apply_correction
    def fail(*args):raise WorkflowError('synthetic local failure after retained correction')
    monkeypatch.setattr(batch_recovery,'apply_correction',fail)
    writer=Writer()
    with pytest.raises(WorkflowError,match='synthetic local failure'):
        generate_batch(ws,'OPS',[1],selection=sel,provider_adapter=writer,repair_missing=True)
    assert writer.calls==['writing','coverage_repair']
    monkeypatch.setattr(batch_recovery,'apply_correction',original)
    monkeypatch.setattr(batch_recovery,'historical_active_seconds',lambda *args:1175)
    def build(workspace,lecture,**kwargs):
        assert 0<kwargs['timeout_seconds']<=25
        path=workspace.runs_dir/lecture/'word.docx';path.write_bytes(b'isolated fake Word');return {'candidate':str(path)}
    monkeypatch.setattr(publication,'build_word_candidate',build)
    result=generate_batch(ws,'OPS',[1],selection=sel,provider_adapter=writer,repair_missing=True,build_word=True)
    assert result['status']=='CANDIDATE_READY' and writer.calls==['writing','coverage_repair']


def test_renderer_change_rebuilds_word_without_rewriting_content(tmp_path,monkeypatch):
    from mynotes import publication
    ws,_,sel=workspace_and_deck(tmp_path,lessons=1,slides=2);writer=Adapter();builds=[]
    def build(workspace,lecture,**kwargs):
        path=workspace.runs_dir/lecture/f'word-{len(builds)}.docx';path.write_bytes(b'fake Word');builds.append(path);return {'candidate':str(path)}
    monkeypatch.setattr(publication,'build_word_candidate',build)
    monkeypatch.setattr(publication,'word_renderer_hash',lambda:'renderer-v1')
    first=generate_batch(ws,'OPS',[1],selection=sel,provider_adapter=writer,build_word=True)
    before=Path(first['candidates'][0]['candidate']).read_bytes()
    monkeypatch.setattr(publication,'word_renderer_hash',lambda:'renderer-v2')
    second=generate_batch(ws,'OPS',[1],selection=sel,provider_adapter=writer,build_word=True)
    generate_batch(ws,'OPS',[1],selection=sel,provider_adapter=writer,build_word=True)
    assert len(writer.calls)==1 and len(builds)==2
    assert Path(second['candidates'][0]['candidate']).read_bytes()==before
    assert second['word_candidates']['OPS-L01']['renderer_hash']=='renderer-v2'


def test_onboarding_progress_does_not_invalidate_completed_batch(tmp_path):
    ws,_,sel=workspace_and_deck(tmp_path,lessons=1,slides=2);writer=Adapter()
    first=generate_batch(ws,'OPS',[1],selection=sel,provider_adapter=writer)
    path=Path(first['candidates'][0]['candidate']);before=path.read_bytes()
    config=ws.configuration_store().load();config['courses'][0]['context_setup']='complete';ws.configuration_store().save(config)
    second=generate_batch(ws,'OPS',[1],selection=sel,provider_adapter=writer,batch_id=first['batch_id'])
    assert len(writer.calls)==1 and second['fingerprint']==first['fingerprint'] and path.read_bytes()==before


def test_resume_never_silently_rewrites_saved_candidate_after_real_context_change(tmp_path):
    ws,_,sel=workspace_and_deck(tmp_path,lessons=1,slides=2);writer=Adapter()
    first=generate_batch(ws,'OPS',[1],selection=sel,provider_adapter=writer)
    path=Path(first['candidates'][0]['candidate']);before=path.read_bytes()
    config=ws.configuration_store().load();config['courses'][0]['context']='A different requested teaching objective.';ws.configuration_store().save(config)
    with pytest.raises(WorkflowError,match='Resume would rewrite an existing batch candidate'):
        generate_batch(ws,'OPS',[1],selection=sel,provider_adapter=writer,batch_id=first['batch_id'])
    assert len(writer.calls)==1 and path.read_bytes()==before


def test_matching_manifest_keeps_word_cache_after_another_plan_failed(tmp_path,monkeypatch):
    from mynotes import publication
    from mynotes.batch import load_batch_manifest,_save
    ws,_,sel=workspace_and_deck(tmp_path,lessons=1,slides=2);writer=Adapter();builds=[]
    def build(workspace,lecture,**kwargs):
        path=workspace.runs_dir/lecture/'word.docx';path.write_bytes(b'fake Word');builds.append(path);return {'candidate':str(path)}
    monkeypatch.setattr(publication,'build_word_candidate',build)
    first=generate_batch(ws,'OPS',[1],selection=sel,provider_adapter=writer,build_word=True)
    saved=load_batch_manifest(ws,first['batch_id'])
    _save(ws.runs_dir/'_batches'/first['batch_id']/'manifest.json',{**saved,'fingerprint':'other-plan','status':'FAILED','word_candidates':{}})
    second=generate_batch(ws,'OPS',[1],selection=sel,provider_adapter=writer,batch_id=first['batch_id'],build_word=True)
    assert len(writer.calls)==1 and len(builds)==1 and second['status']=='CANDIDATE_READY'


def test_transport_receipt_overrides_unrecorded_citation_edits(tmp_path,monkeypatch):
    from mynotes import providers
    from mynotes.batch_recovery import certify_response
    ws,_,sel=workspace_and_deck(tmp_path,lessons=1,slides=2)
    config=ws.configuration_store().load();config['providers']['configured']['agy']={'model':'test-model','effort':'high'};ws.configuration_store().save(config)
    monkeypatch.setenv('MYNOTES_AGY_PARTS','1');monkeypatch.setattr(providers,'provider_executable',lambda _:sys.executable);monkeypatch.setattr(a,'provider_executable',lambda _:sys.executable)
    plan=plan_batch(ws,'OPS',[1],selection=sel,provider='agy');packs,sources,inventory=_packs(ws,['OPS-L01'])
    from mynotes.providers import RequestedModel
    runtime={**plan,'packs':packs,'sources':sources,'inventory':inventory,'requested':RequestedModel(**plan['requested_model'])}
    req,_=_request(runtime,['OPS-L01'],ws.runs_dir/'test',[]);notes=notes_for(req,'OPS-L01');part={'part_index':0,'complete':True,'next_topic':'','result':{'lessons':[{'lecture_id':'OPS-L01','notes':notes}]}}
    monkeypatch.setattr(a,'run_session',_fake_session([part],[]));response=providers.AgyAdapter().generate(req)
    changed=deepcopy(response.notes);changed['lessons'][0]['notes']['blocks'][1]['evidence_ids'].append('S002')
    restored=certify_response(replace(response,notes=changed),req,ws.root)
    assert restored.notes==response.notes and changed!=restored.notes


def test_attempt_logs_capture_real_start_and_end(tmp_path):
    from mynotes.providers import _save_attempt
    from mynotes.execution import AttemptStart
    start=AttemptStart();time.sleep(.02)
    path=_save_attempt(request(tmp_path),command=['synthetic'],completed=subprocess.CompletedProcess([],0,'',''),started=start)
    record=json.loads(path.read_text());assert record['start_time_source']=='captured'
    assert record['started_at']<record['ended_at'] and record['elapsed_seconds']>=.02


def test_unexpected_local_renderer_error_is_saved_as_stopped(tmp_path,monkeypatch):
    from mynotes import publication
    ws,_,sel=workspace_and_deck(tmp_path,lessons=1,slides=2)
    def fail(*a,**kw):raise KeyError('missing legacy style')
    monkeypatch.setattr(publication,'build_word_candidate',fail)
    with pytest.raises(WorkflowError,match='Batch stopped during BUILDING_WORD'):
        generate_batch(ws,'OPS',[1],selection=sel,provider_adapter=Adapter(),build_word=True)
    from mynotes.batch import load_batch_manifest
    root=ws.runs_dir/'_batches';batch_id=next(root.iterdir()).name;status=batch_status(ws,batch_id)
    assert status['status']=='FAILED' and status['activity']['status']=='STOPPED'
    assert status['problem']['kind']=='KeyError' and status['lessons'][0]['status']=='CONTENT_READY'
    class NoProvider:
        def generate(self, *_): pytest.fail('A Word error must never trigger another generation')
    def restored(workspace, lecture, **kwargs):
        path=workspace.runs_dir/lecture/'rebuilt.docx';path.write_bytes(b'synthetic renderer result')
        return {'candidate':str(path)}
    monkeypatch.setattr(publication,'build_word_candidate',restored)
    resumed=generate_batch(ws,'OPS',[1],selection=sel,provider_adapter=NoProvider(),build_word=True)
    assert resumed['status']=='CANDIDATE_READY' and resumed['word_candidates']['OPS-L01']


def test_missing_process_permission_is_not_reported_as_interruption(tmp_path,monkeypatch):
    from mynotes.batch import load_batch_manifest,_save
    ws,_,sel=workspace_and_deck(tmp_path,lessons=1,slides=2)
    result=generate_batch(ws,'OPS',[1],selection=sel,provider_adapter=Adapter());plan=load_batch_manifest(ws,result['batch_id'])
    plan['status']='RUNNING';plan['activity']={'pid':123456,'status':'WRITING','lessons':['OPS-L01']};_save(ws.runs_dir/'_batches'/result['batch_id']/'manifest.json',plan)
    def denied(*args):raise PermissionError('restricted process inspection')
    monkeypatch.setattr(os,'kill',denied)
    assert batch_status(ws,result['batch_id'])['status']=='STATUS_UNAVAILABLE'
