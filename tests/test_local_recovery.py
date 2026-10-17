"""Public synthetic reproduction of a complete stream rejected for zero padding."""
from dataclasses import replace
from pathlib import Path
from copy import deepcopy
import json
import pytest
from mynotes import local_recovery as recovery,providers,agy_parts as a
from mynotes.utils import atomic_json_write,file_sha256
from mynotes.providers import ProviderError
from test_agy_parts import request,portion


def fixture(tmp_path):
    req=request(tmp_path)
    req=replace(req,source_pack={'evidence':[{'id':'S001','content':'A'*34000}]})
    providers._stage_agy_context(req,tmp_path)
    root=tmp_path/'writing-parts/original';root.mkdir(parents=True)
    parts=[portion(0,False),portion(1,True)]
    parts[1]['result']['blocks'][0]['evidence_ids']=['S0001']
    results=[];refs=[]
    for i,part in enumerate(parts):
        envelope={'status':'SUCCESS','conversation_id':'same','structured_output':part}
        results.append(envelope);raw={'part':part,'envelope':envelope}
        atomic_json_write(root/f'returned-{i:03d}.json',raw)
        if i==0:
            atomic_json_write(root/f'part-{i:03d}.json',raw)
            refs.append({'file':f'part-{i:03d}.json','sha256':file_sha256(root/f'part-{i:03d}.json')})
    atomic_json_write(root/'state.json',{'local_rejection':True,'conversation_id':'same','parts':refs,'used_seconds':678.68})
    (tmp_path/'provider-attempts').mkdir()
    record={'requested':req.requested.__dict__,'error':'Writing portion cites unknown evidence','returncode':None,
            'stdout':'\n'.join(json.dumps({'event':'result','result':r}) for r in results)}
    atomic_json_write(tmp_path/'provider-attempts/original.json',record)
    return req,root


def tree_hashes(root):return {str(p.relative_to(root)):file_sha256(p) for p in root.rglob('*') if p.is_file()}


def test_diagnosis_is_read_only_then_apply_keeps_failed_attempt(tmp_path):
    req,root=fixture(tmp_path);before=tree_hashes(tmp_path)
    response,receipt=recovery.recover_parts(req,tmp_path)
    assert tree_hashes(tmp_path)==before
    assert response.notes['blocks'][1]['evidence_ids']==['S001'] and receipt['active_seconds']==678.68
    response,receipt=recovery.recover_parts(req,tmp_path,apply=True)
    assert recovery.verify_local_receipt(Path(response.attempt_path),tmp_path)==response.notes
    assert json.loads((tmp_path/'provider-attempts/original.json').read_text())['returncode'] is None
    assert all(file_sha256(tmp_path/p)==h for p,h in before.items())


@pytest.mark.parametrize('defect',['incomplete','failed_turn','changed_checkpoint','wrong_conversation','changed_input','missing_portion','changed_source'])
def test_invalid_or_unproven_streams_cannot_be_recovered(tmp_path,defect):
    req,root=fixture(tmp_path)
    if defect=='changed_checkpoint':
        (root/'part-000.json').write_text('{}')
    elif defect=='changed_input':
        next((tmp_path/'request-inputs').glob('*/evidence-001.jsonl')).write_text('{}\n')
    elif defect=='missing_portion':(root/'returned-001.json').unlink()
    elif defect=='changed_source':req=replace(req,source_pack={'evidence':[{'id':'S001','content':'Changed'}]})
    else:
        path=tmp_path/'provider-attempts/original.json';record=json.loads(path.read_text())
        events=[json.loads(l) for l in record['stdout'].splitlines()]
        if defect=='failed_turn':events[-1]['result']['status']='ERROR'
        if defect=='wrong_conversation':events[-1]['result']['conversation_id']='other'
        if defect=='incomplete':
            part=events[-1]['result']['structured_output'];part.update(complete=False,next_topic='Still unfinished')
            events[-1]['result']['structured_output']=part
            atomic_json_write(root/'returned-001.json',{'part':part,'envelope':events[-1]['result']})
        record['stdout']='\n'.join(json.dumps(e) for e in events);atomic_json_write(path,record)
    with pytest.raises((ProviderError,OSError)):recovery.recover_parts(req,tmp_path)


def test_recovery_receipt_rejects_later_raw_tampering(tmp_path):
    req,root=fixture(tmp_path);response,_=recovery.recover_parts(req,tmp_path,apply=True)
    (root/'returned-001.json').write_text('{}')
    with pytest.raises(ProviderError,match='changed'):recovery.verify_local_receipt(Path(response.attempt_path),tmp_path)


def test_reference_alias_collision_does_not_hide_conflicting_decisions(tmp_path):
    req=request(tmp_path);one=portion();two=portion(1,True)
    two['result']['evidence_coverage']=[{'evidence_id':'S0001','disposition':'excluded','reason':'Conflicting'}]
    with pytest.raises(ProviderError,match='conflicting'):a.assemble(req,[one,two])


def test_unknown_speaker_is_not_an_automatic_block():
    from mynotes.generation_diagnostics import speaker_warning
    pack={'evidence':[{'id':'T001','speaker_role':'UNKNOWN'}]}
    assert speaker_warning(pack,'lecture') is None
    assert speaker_warning(pack,'professor')['code']=='PROFESSOR_NOT_ISOLATED'
    assert pack['evidence'][0]['speaker_role']=='UNKNOWN'


def test_source_read_audit_preserves_ranges_and_ignores_event_replay():
    from mynotes.generation_diagnostics import source_read_audit
    def event(i,start):return {'event':'step_update','step_update':{'state':'DONE','tool_name':'view_file','step_index':i,
        'conversation_id':'one','tool_info':{'parameters':{'AbsolutePath':'source','StartLine':start,'EndLine':start+100}}}}
    e=[event(1,0),event(1,0),event(2,101),event(3,0)]
    result=source_read_audit('\n'.join(json.dumps(r) for r in e))
    assert result['view_file_calls']==3 and result['unique_requests']==2 and result['exact_repeat_calls']==1


def test_apply_recovers_candidate_and_resume_never_invokes_provider(tmp_path,monkeypatch):
    from mynotes import batch as b
    from mynotes.batch_recovery import intent
    from mynotes.workspace import Workspace
    from test_batch_generation import workspace_and_deck,notes_for,Adapter
    workspace,deck,selection=workspace_and_deck(tmp_path,lessons=1,slides=2)
    settings=workspace.configuration_store().load();settings['providers']['default']='agy';settings['providers']['configured']['agy']={'model':'test-model','effort':'high'};workspace.configuration_store().save(settings)
    packpath=workspace.runs_dir/'OPS-L01/source_pack.json';pack=json.loads(packpath.read_text());pack['evidence'][0]['content']='A'*34000;atomic_json_write(packpath,pack)
    plan=b.plan_batch(workspace,'OPS',[1],selection=selection)
    packs,sources,inventory=b._packs(workspace,plan['lectures']);runtime={**plan,'packs':packs,'inventory':inventory,'requested':providers.RequestedModel(**plan['requested_model'])}
    root=workspace.runs_dir/'_batches'/plan['batch_id'];version=root/plan['fingerprint'];version.mkdir(parents=True)
    b._save(version/'manifest.json',plan);b._save(root/'manifest.json',plan)
    directory=root/'generation-checkpoints'/b._hash(intent(runtime,plan['lectures'],[],reviewed=True));directory.mkdir(parents=True)
    req,maps=b._request(runtime,plan['lectures'],directory,[])
    providers._stage_agy_context(req,directory)
    original=notes_for(req,'OPS-L01');original['blocks'][1]['evidence_ids']=['S0001']
    part={'part_index':0,'complete':True,'next_topic':'','result':{'lessons':[{'lecture_id':'OPS-L01','notes':original}]}}
    env={'status':'SUCCESS','conversation_id':'original','structured_output':part}
    state_root=directory/'writing-parts/original';state_root.mkdir(parents=True)
    atomic_json_write(state_root/'state.json',{'local_rejection':True,'parts':[],'used_seconds':600,'conversation_id':'original'})
    atomic_json_write(state_root/'returned-000.json',{'envelope':env,'part':part})
    (directory/'provider-attempts').mkdir()
    atomic_json_write(directory/'provider-attempts/writing-original.json',{'requested':req.requested.__dict__,'error':'Writing portion cites unknown evidence','returncode':None,'stdout':json.dumps({'event':'result','result':env})})
    # The old prompt matches this frozen manifest; recovery separately verifies staged input contents.
    before=tree_hashes(root)
    result=recovery.recover_batch(workspace,plan['batch_id'])
    assert result['lessons'][0]['status']=='LOCALLY_RECOVERABLE' and tree_hashes(root)==before
    applied=recovery.recover_batch(workspace,plan['batch_id'],apply=True)
    assert applied['lessons'][0]['status']=='CONTENT_RECOVERED'
    saved=workspace.runs_dir/'OPS-L01/lecture_notes.candidate.json'
    assert saved.is_file()
    class NoProvider:
        def generate(self,*_):pytest.fail('recovery/resume must not spend tokens')
    again=b.generate_batch(workspace,'OPS',[1],selection=selection,batch_id=plan['batch_id'],provider_adapter=NoProvider())
    assert again['status']=='CANDIDATE_READY'


def test_single_generation_keeps_raw_response_and_records_reference_conversion(tmp_path):
    from mynotes.workflow import generate_candidate
    from mynotes.providers import ProviderResponse
    from test_providers_quality import configured_workspace,source_pack,valid_notes
    workspace=configured_workspace(tmp_path);path=source_pack(workspace)
    raw=valid_notes();raw['blocks'][1]['evidence_ids']=['S0001','H0001']
    original=deepcopy(raw)
    class Provider:
        def generate(self,request):return ProviderResponse(raw,'codex','gpt-test','medium',{})
    result=generate_candidate(workspace,'OPS7-L01',provider_adapter=Provider(),speaker_focus='professor')
    assert raw==original
    candidate=json.loads(Path(result['candidate']).read_text())
    assert candidate['notes']['blocks'][1]['evidence_ids']==['S001','H001']
    assert candidate['request_options']['speaker_focus']=='professor'
    assert candidate['warnings'][0]['code']=='PROFESSOR_NOT_ISOLATED'
    reports=list((workspace.runs_dir/'OPS7-L01/generation-checkpoints').glob('*/*.normalization.json'))
    assert len(reports)==1 and len(json.loads(reports[0].read_text())['transformations'])==2


@pytest.mark.parametrize('role', ['STUDENT', None, 'unverified'])
def test_professor_focus_reports_when_voice_cannot_be_isolated(role):
    from mynotes.generation_diagnostics import speaker_warning
    assert speaker_warning({'evidence':[{'id':'T001','speaker_role':role}]},'professor')


def test_modified_recovery_receipt_cannot_change_its_own_proof(tmp_path):
    req,root=fixture(tmp_path);response,_=recovery.recover_parts(req,tmp_path,apply=True)
    path=Path(response.attempt_path);receipt=json.loads(path.read_text());receipt['active_seconds']=0
    atomic_json_write(path,receipt)
    with pytest.raises(ProviderError,match='receipt changed'):recovery.verify_local_receipt(path,tmp_path)
