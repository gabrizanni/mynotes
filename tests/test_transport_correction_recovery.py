"""Bounded local recovery of Agy finish metadata; no real provider calls."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import pytest

from mynotes import batch as b, batch_recovery as recovery, local_recovery
from mynotes.providers import ProviderResponse, _normalize_transport_notes, _transport_normalization, _prompt, _stage_agy_context
from mynotes.utils import atomic_json_write, file_sha256
from mynotes.workflow import WorkflowError
from test_batch_generation import workspace_and_deck, notes_for


def test_agy_metadata_is_top_level_text_only_and_unknown_operations_survive():
    schema={'properties':{'insertions':{'type':'array'},'replacements':{'type':'array'}},'required':['insertions']}
    raw={'insertions':[], 'replacements':None, 'toolAction':'finish', 'toolSummary':'Done',
         'unknown':None, 'nested':{'toolAction':'not metadata'}}
    original=deepcopy(raw)
    normalized,receipt=_transport_normalization(raw,schema,provider='agy')
    assert normalized=={'insertions':[], 'unknown':None, 'nested':{'toolAction':'not metadata'}}
    assert raw==original and receipt['policy']=='transport_normalization_v1'
    assert len(receipt['transformations'])==3
    assert _normalize_transport_notes(raw,schema)['toolAction']=='finish'
    assert _normalize_transport_notes({'toolAction':{}},schema,provider='agy')=={'toolAction':{}}
    with pytest.raises(WorkflowError,match='Unexpected targeted correction'):
        recovery.apply_correction({'lessons':[]},normalized,{'defects':[]})


class RetainedAgy:
    def __init__(self):self.calls=[]
    def generate(self,request):
        self.calls.append(request.phase)
        if request.phase=='writing':
            notes=notes_for(request,'OPS-L01')
            notes['blocks']=[row for row in notes['blocks'] if row.get('evidence_ids')!=['S002']]
            value={'lessons':[{'lecture_id':'OPS-L01','notes':notes}]}
        else:
            task=request.batch_context['repair_task'];anchor=task['anchors'][1]['block_sha256']
            value={'insertions':[{'lecture_id':'OPS-L01','after_block_sha256':anchor,
                     'block':{'kind':'paragraph','text':'Slide two explains its distinct source supported mechanism in depth.',
                              'evidence_ids':['S002'],'coverage_role':'full'}}],
                   'attachments':[],'visual_updates':[], 'replacements':None,
                   'toolAction':'finish','toolSummary':'Explained the missing concept.'}
        envelope={'status':'SUCCESS','structured_output':value}
        path=request.run_dir/'provider-attempts'/f'{request.phase}-original.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        context=_stage_agy_context(request,request.run_dir)
        atomic_json_write(path,{'requested':asdict(request.requested),'phase':request.phase,'returncode':0,'error':None,
                               'stdout':json.dumps(envelope),'command':['agy','--print',_prompt(request,context=context)]})
        return ProviderResponse(value,'agy','','',{},str(path))


def failed_legacy_batch(tmp_path,monkeypatch):
    workspace,_,selection=workspace_and_deck(tmp_path,lessons=1,slides=2)
    # Freeze the historical full-deck validation contract under test.
    legacy=workspace.configuration_store().load();legacy['style']['profile']['slide_scope']='all_supplied';workspace.configuration_store().save(legacy)
    config=workspace.configuration_store().load();config['providers']['default']='agy'
    config['providers']['configured']['agy']={'model':'test-model','effort':'high'}
    workspace.configuration_store().save(config)
    adapter=RetainedAgy();real=recovery._saved_correction
    def fail_after_saved(*args):raise WorkflowError('Unexpected targeted correction operation')
    monkeypatch.setattr(recovery,'_saved_correction',fail_after_saved)
    with pytest.raises(WorkflowError,match='Unexpected targeted correction'):
        b.generate_batch(workspace,'OPS',[1],selection=selection,provider_adapter=adapter,repair_missing=True)
    monkeypatch.setattr(recovery,'_saved_correction',real)
    assert adapter.calls==['writing','coverage_repair']
    root=next((workspace.runs_dir/'_batches').iterdir())
    return workspace,selection,root


def hashes(root):return {str(p.relative_to(root)):file_sha256(p) for p in root.rglob('*') if p.is_file()}


def test_saved_correction_diagnoses_read_only_recovers_and_verifies_lineage(tmp_path,monkeypatch):
    workspace,selection,root=failed_legacy_batch(tmp_path,monkeypatch)
    before=hashes(root)
    diagnosed=local_recovery.recover_batch(workspace,root.name)
    assert diagnosed['lessons'][0]['status']=='LOCALLY_RECOVERABLE'
    assert diagnosed['lessons'][0]['transformations']==3 and hashes(root)==before
    result=local_recovery.recover_batch(workspace,root.name,apply=True)
    assert result['applied'] and result['provider_invoked'] is False
    candidate=json.loads((workspace.runs_dir/'OPS-L01/lecture_notes.candidate.json').read_text())
    recovery.verify_repair_lineage(candidate,workspace.root)
    line=json.loads(Path(candidate['repair_lineage']['path']).read_text())
    assert line['transport_normalization']['policy']=='transport_normalization_v1'
    assert 'toolAction' not in line['operations']
    raw=json.loads(Path(line['repair']).read_text())
    assert raw['response']['notes']['toolAction']=='finish'
    assert raw['response']['notes']['replacements'] is None
    assert all(file_sha256(root/p)==digest for p,digest in before.items() if 'provider-attempts/' in p or p.endswith('/repair.json') or p.endswith('/certified-base.json') or '/returned-writing-' in p)
    class NoProvider:
        def generate(self,*_):pytest.fail('repeat resume must not call provider')
    resumed=b.generate_batch(workspace,'OPS',[1],selection=selection,batch_id=root.name,provider_adapter=NoProvider())
    assert resumed['status']=='CANDIDATE_READY'


def test_single_turn_prompt_proof_rejects_staged_source_changes(tmp_path,monkeypatch):
    workspace,selection,root=failed_legacy_batch(tmp_path,monkeypatch)
    plan=b.load_batch_manifest(workspace,root.name)
    packs,_,inventory=b._packs(workspace,plan['lectures']);runtime={**plan,'packs':packs,'inventory':inventory}
    plan['prompt_contract']='old-module-hash'
    directory=next((root/'generation-checkpoints').iterdir())
    # Without portions fingerprints, only the exact saved --print prompt proves reuse.
    assert recovery.prompt_matches(directory,plan,runtime,plan['lectures'],[])
    attempt=next((directory/'provider-attempts').glob('*.json'))
    record=json.loads(attempt.read_text());record['command'][-1]+=' changed'
    atomic_json_write(attempt,record)
    assert not recovery.prompt_matches(directory,plan,runtime,plan['lectures'],[])


def test_normalization_receipt_cannot_forge_a_different_policy(tmp_path,monkeypatch):
    workspace,_,root=failed_legacy_batch(tmp_path,monkeypatch)
    local_recovery.recover_batch(workspace,root.name,apply=True)
    candidate=json.loads((workspace.runs_dir/'OPS-L01/lecture_notes.candidate.json').read_text())
    path=Path(candidate['repair_lineage']['path']);line=json.loads(path.read_text())
    line['transport_normalization']['policy']='ignore_anything'
    atomic_json_write(path,line);candidate['repair_lineage']['sha256']=file_sha256(path)
    with pytest.raises(WorkflowError,match='normalization changed'):
        recovery.verify_repair_lineage(candidate,workspace.root)


@pytest.mark.parametrize('field,value',[('toolAction',{}),('unknownOperation',[]),('unknownOperation',None)])
def test_nonmetadata_operations_are_rejected_without_provider_retry(tmp_path,monkeypatch,field,value):
    workspace,_,root=failed_legacy_batch(tmp_path,monkeypatch)
    directory=next((root/'generation-checkpoints').iterdir());repair=directory/'coverage-repair/repair.json'
    saved=json.loads(repair.read_text());saved['response']['notes'][field]=value
    attempt=Path(saved['response']['attempt_path']);record=json.loads(attempt.read_text())
    envelope=json.loads(record['stdout']);envelope['structured_output'][field]=value
    record['stdout']=json.dumps(envelope);atomic_json_write(attempt,record);atomic_json_write(repair,saved)
    with pytest.raises(WorkflowError,match='Unexpected targeted correction'):
        local_recovery.recover_batch(workspace,root.name)


def test_status_keeps_published_content_valid_and_verifies_delivery_files(tmp_path):
    from test_batch_generation import Adapter
    import os
    workspace,_,selection=workspace_and_deck(tmp_path,lessons=1,slides=2)
    generated=b.generate_batch(workspace,'OPS',[1],selection=selection,provider_adapter=Adapter())
    candidate=workspace.runs_dir/'OPS-L01/lecture_notes.candidate.json'
    payload=json.loads(candidate.read_text());payload.update(published_to_word=True,published_path=str(tmp_path/'course.docx'),published_hash='published')
    atomic_json_write(candidate,payload)
    root=workspace.runs_dir/'_batches'/generated['batch_id'];document=tmp_path/'lesson.docx';document.write_bytes(b'verified synthetic delivery')
    target=tmp_path/'course.docx';target.write_bytes(b'verified synthetic course')
    sidecar={'status':'COMPLETED','pid':os.getpid(),'delivery':{'files':[{'path':str(document),'sha256':file_sha256(document)}]},
             'publication':{'output':str(target),'output_hash':file_sha256(target)}}
    b._save(root/'notes-run.json',sidecar);before=hashes(root)
    result=recovery.batch_status(workspace,generated['batch_id'])
    assert result['status']=='COMPLETED' and result['published_to_word']
    assert result['lessons'][0]['content_validated'] is True
    assert hashes(root)==before
    document.write_bytes(b'externally changed')
    changed=recovery.batch_status(workspace,generated['batch_id'])
    assert changed['status']=='DELIVERY_CHANGED' and changed['published_to_word'] is False
    assert changed['notes_request']['reported_status']=='COMPLETED'


def test_status_marks_dead_notes_controller_interrupted(tmp_path,monkeypatch):
    from test_batch_generation import Adapter
    workspace,_,selection=workspace_and_deck(tmp_path,lessons=1,slides=2)
    generated=b.generate_batch(workspace,'OPS',[1],selection=selection,provider_adapter=Adapter())
    root=workspace.runs_dir/'_batches'/generated['batch_id']
    b._save(root/'notes-run.json',{'status':'BUILDING_WORD','pid':123456789})
    result=recovery.batch_status(workspace,generated['batch_id'])
    assert result['status']=='INTERRUPTED' and result['notes_request']['reported_status']=='BUILDING_WORD'


def test_agy_adapter_saves_raw_attempt_and_separate_normalization_receipt(tmp_path,monkeypatch):
    import subprocess,sys
    from dataclasses import replace
    from mynotes import providers
    from test_agy_parts import request
    schema={'type':'object','properties':{'insertions':{'type':'array'},'moves':{'type':'array'}},'required':['insertions']}
    req=replace(request(tmp_path),phase='coverage_repair',response_schema=schema)
    raw={'insertions':[], 'moves':None,'toolAction':'finish','toolSummary':'Description'}
    envelope={'status':'SUCCESS','structured_output':raw}
    monkeypatch.setattr(providers,'provider_executable',lambda _:sys.executable)
    monkeypatch.setattr(providers,'_run_agy_worker',lambda command,**kwargs:subprocess.CompletedProcess(command,0,json.dumps(envelope),''))
    response=providers.AgyAdapter().generate(req)
    assert response.notes=={'insertions':[]}
    attempt=Path(response.attempt_path);record=json.loads(attempt.read_text())
    assert json.loads(record['stdout'])==envelope
    proof=json.loads((tmp_path/'transport-normalization'/(attempt.stem+'.json')).read_text())
    assert proof['attempt_sha256']==file_sha256(attempt) and len(proof['transformations'])==3
