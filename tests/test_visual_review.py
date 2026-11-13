"""Explicit figure decisions preserve source proof, old patches and prose."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path
import subprocess

import pytest

from mynotes import agy_progressive as transport, progressive_wire as wire, visual_review as v
from mynotes.providers import ProviderError
from mynotes.utils import atomic_json_write, file_sha256
from mynotes.workspace import Workspace
from test_progressive_v2 import request, unit


def prepared(tmp_path):
    workspace=Workspace.from_value(tmp_path/'workspace');root=workspace.runs_dir/'_batches/TEST-123/generation-checkpoints/hash/progressive-v2'
    root.mkdir(parents=True);image=root/'diagram.png';image.write_bytes(b'unchanged-source-image')
    req=request(root.parent,'base');req.source_pack['visuals']=[{'evidence_id':'S002','image_path':str(image)}]
    one,_=wire.validate_unit(req,unit(),[]);two,_=wire.validate_unit(req,unit(1,True),[one])
    two['visual_delta']=[{'evidence_id':'S002','decision':'exclude','content_kind':'text','inspected':True,'reason':'Mistaken label-only exclusion.'}]
    refs=[]
    for i,u in enumerate([one,two]):
        path=root/f'unit-{i:03d}.json';atomic_json_write(path,{'effective':u});refs.append({'file':path.name,'sha256':file_sha256(path)})
    atomic_json_write(root/'request.json',{'lecture_id':req.lecture_id,'course':req.course,'style':req.style,
        'requested':asdict(req.requested),'source_pack':req.source_pack,'batch_context':req.batch_context})
    atomic_json_write(root/'state.json',{'status':'NEEDS_REVIEW','units':refs})
    access=[{'sha256':file_sha256(image)}];(root/'access.jsonl').write_text(json.dumps(access[0])+'\n')
    review={'version':1,'source_proof_sha256':file_sha256(root/'request.json'),'accepted_units':refs,
        'reason':'Restore the requested source diagram; keep previous automatic corrections.',
        'changes':[{'evidence_id':'S002','image_sha256':file_sha256(image),'before_sha256':v.digest(two['visual_delta'][0]),
            'owner':'TEST-L02','content_kind':'diagram','placement':'before_footer','label':'Source conceptual diagram',
            'reason':'Spatial arrangement is required and was visually inspected.'}]}
    return workspace,root,req,refs,access,review,wire.assemble(req,[one,two])


def test_explicit_restore_preserves_prose_and_prior_receipts_and_reuses(tmp_path):
    ws,root,req,refs,access,review,notes=prepared(tmp_path);before=deepcopy(notes)
    prefix={p.name:p.read_bytes() for p in root.glob('unit-*.json')}
    first=v.record_review(ws,'TEST-123',review);assert first['provider_invoked'] is False
    assert v.record_review(ws,'TEST-123',review)==first
    actual,ref=v.project(notes,req,root,refs,access)
    for previous,row in zip(before['lessons'],actual['lessons']):
        assert [b for b in row['notes']['blocks'] if b['kind']!='visual']==previous['notes']['blocks']
    owner=actual['lessons'][1]['notes'];kinds=[b['kind'] for b in owner['blocks']]
    assert kinds==['overview','paragraph','visual','qa','recap']
    assert owner['visual_decisions'][0]['decision']=='include'
    assert v.project(actual,req,root,refs,access,ref)[0]==actual
    assert notes==before and all((root/name).read_bytes()==data for name,data in prefix.items())
    (root/'visual-review.json').write_text('{}')
    with pytest.raises(ProviderError,match='receipt changed'):v.project(notes,req,root,refs,access,ref)


@pytest.mark.parametrize('problem',['source','prefix','image','stale','foreign','duplicate','unobserved','limit'])
def test_restoration_rejects_changed_sources_and_cannot_bypass_frozen_limits(tmp_path,problem):
    ws,root,req,refs,access,review,notes=prepared(tmp_path)
    if problem=='source':review['source_proof_sha256']='bad'
    elif problem=='prefix':(root/'unit-000.json').write_text('{}')
    elif problem=='image':(root/'diagram.png').write_bytes(b'changed')
    elif problem=='stale':review['changes'][0]['before_sha256']='bad'
    elif problem=='foreign':review['changes'][0]['owner']='TEST-L01'
    elif problem=='duplicate':review['changes']*=2
    elif problem=='unobserved':access=[]
    elif problem=='limit':req=replace(req,style={**req.style,'image_limit':0})
    with pytest.raises(ProviderError):v.apply(notes,req,root,refs,access,review)


def test_saved_visual_choice_cannot_be_silently_replaced_or_added_to_completed_job(tmp_path):
    ws,root,req,refs,access,review,notes=prepared(tmp_path);v.record_review(ws,'TEST-123',review)
    changed=deepcopy(review);changed['changes'][0]['label']='Different caption'
    with pytest.raises(ProviderError,match='already recorded'):v.record_review(ws,'TEST-123',changed)
    state=json.loads((root/'state.json').read_text());state['finalized']=True;atomic_json_write(root/'state.json',state)
    with pytest.raises(ProviderError,match='stopped unfinished'):v.record_review(ws,'TEST-123',review)


def test_explicit_reclassification_reuses_observed_pixels_without_claiming_a_new_model_inspection(tmp_path):
    ws,root,req,refs,access,review,notes=prepared(tmp_path)
    access[0].update(origin='verified_inspection_cache',content_kind='text')
    restored=v.apply(notes,req,root,refs,access,review)
    assert restored['lessons'][1]['notes']['visual_decisions'][0]['decision']=='include'
    assert json.loads((root/'unit-001.json').read_text())['effective']['visual_delta'][0]['content_kind']=='text'


def test_cli_records_the_explicit_source_choice_without_invoking_a_provider(tmp_path,capsys,monkeypatch):
    from mynotes.cli import main
    ws,root,req,refs,access,review,notes=prepared(tmp_path)
    monkeypatch.setattr(transport,'run_single',lambda _:pytest.fail('A visual decision must be local'))
    path=tmp_path/'review.json';atomic_json_write(path,review)
    assert main(['visual-review','TEST-123','--workspace',str(ws.root),'--file',str(path)])==0
    assert json.loads(capsys.readouterr().out)['provider_invoked'] is False


def test_generation_receipt_verifies_the_derivative_without_relabeling_original_units(tmp_path,monkeypatch):
    from mynotes import agy_parts
    root=tmp_path/'progressive-v2';req=request(tmp_path,'base')
    image=tmp_path/'diagram.png';image.write_bytes(b'observed-source')
    req.source_pack['visuals']=[{'evidence_id':'S002','image_path':str(image)}]
    one=unit();one['visual_delta']=[{'evidence_id':'S002','decision':'exclude','content_kind':'text',
        'inspected':True,'reason':'Mistaken classification.'}]
    two=unit(1,True);two.update(complete=False,lesson_complete=False,next_topic='Questions and recap')
    two['blocks']=two['blocks'][:2]
    three=unit(2,True);three.update(header=None,organization_delta=[],blocks=three['blocks'][2:])
    monkeypatch.setattr(transport,'command',lambda *args:['synthetic'])
    def run(command,prompt,accept,**kwargs):
        events=[];kwargs['on_event']({'event':'init','conversation_id':'same'})
        kwargs['on_event']({'step_update':{'state':'DONE','tool_name':'view_file','tool_info':{'parameters':{'Path':str(image)}}}})
        for raw in [one,two,three]:
            if raw is three:
                refs=json.loads((root/'state.json').read_text())['units']
                atomic_json_write(root/'visual-review.json',{'version':1,'source_proof_sha256':file_sha256(root/'request.json'),
                    'accepted_units':refs,'reason':'Explicitly restore inspected source figure.',
                    'changes':[{'evidence_id':'S002','image_sha256':file_sha256(image),
                        'before_sha256':v.digest(one['visual_delta'][0]),'owner':'TEST-L02','content_kind':'diagram',
                        'placement':'before_footer','label':'Source diagram','reason':'Retain spatial information.'}]})
            envelope={'status':'SUCCESS','conversation_id':'same','response':json.dumps(raw)}
            events.append({'event':'result','result':envelope});accept(envelope)
        return subprocess.CompletedProcess(command,0,'\n'.join(json.dumps(e) for e in events),'')
    monkeypatch.setattr(agy_parts,'run_session',run)
    result=transport.generate(req);record=json.loads(Path(result.attempt_path).read_text())
    assert transport.verify_receipt(record,tmp_path)==result.notes
    owner=result.notes['lessons'][1]['notes']
    assert [b['kind'] for b in owner['blocks']]==['overview','paragraph','visual','qa','recap']
    assert json.loads((root/'unit-000.json').read_text())['effective']['visual_delta'][0]['decision']=='exclude'
    from mynotes import visual_memory
    visual_memory.save(tmp_path/'cache',req,result)
    cached=visual_memory.load(tmp_path/'cache',req.source_pack['visuals'],asdict(req.requested))
    assert len(cached)==1 and cached[0]['content_kind']=='diagram'
    (root/'visual-review.json').write_text('{}')
    with pytest.raises(ProviderError,match='receipt changed'):transport.verify_receipt(record,tmp_path)
