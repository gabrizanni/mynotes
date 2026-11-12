"""Reference-only repairs retain successful transport, text and correction limits."""
from copy import deepcopy
import json

import pytest

from mynotes.reference_review import apply, verify
from mynotes.progressive_review import digest
from mynotes.workflow import WorkflowError


def fixture():
    notes={'organization':[{'owner':'A-L02','status':'taught','canonical_slide_id':'S001','evidence_ids':['S001','T001']}],
           'lessons':[{'lecture_id':'A-L02','notes':{'blocks':[{'kind':'paragraph','text':'Unchanged source-backed explanation.', 'evidence_ids':['S001','T001']}]}}]}
    changes=[{'target':kind,'index':0,'lecture_id':'A-L02','before_sha256':digest(target),
              'evidence_ids':['S001','T002'],'reason':'Current lecture source inspected.'}
             for kind,target in [('organization',notes['organization'][0]),('block',notes['lessons'][0]['notes']['blocks'][0])]]
    return notes,{'version':1,'base_notes_sha256':digest(notes),'reason':'Explicit reference review.','changes':changes}


def test_reference_rectification_changes_only_ids_and_preserves_original():
    notes,review=fixture();before=deepcopy(notes)
    changed=apply(notes,review,{'A-L02':['S001','T002']})
    assert notes==before and changed['lessons'][0]['notes']['blocks'][0]['text']==notes['lessons'][0]['notes']['blocks'][0]['text']
    assert changed['organization'][0]['evidence_ids']==['S001','T002']


def test_explicit_missing_page_attachment_preserves_prose_and_every_existing_reference():
    notes,review=fixture();notes['organization'].append({'canonical_slide_id':'S002','owner':'A-L02','status':'taught'})
    target=notes['lessons'][0]['notes']['blocks'][0]
    review.update(version=2,base_notes_sha256=digest(notes),changes=[{
        'target':'block','index':0,'lecture_id':'A-L02','before_sha256':digest(target),
        'evidence_ids':['S001','T001','S002'],'reason':'The existing explanation was checked against both source pages.'}])
    changed=apply(notes,review,{'A-L02':['S001','S002','T001']})
    assert changed['lessons'][0]['notes']['blocks'][0]=={**target,'evidence_ids':['S001','T001','S002']}
    assert changed['organization']==notes['organization']


@pytest.mark.parametrize('problem',['transcript','remove','owner','footer'])
def test_page_attachment_cannot_change_other_references_or_manufacture_coverage_in_footer(problem):
    notes,review=fixture();notes['organization'].append({'canonical_slide_id':'S002','owner':'A-L02','status':'taught'})
    target=notes['lessons'][0]['notes']['blocks'][0];ids=['S001','T001','S002']
    if problem=='transcript':ids.append('T002')
    if problem=='remove':ids.remove('S001')
    if problem=='owner':notes['organization'][1]['owner']='A-L01'
    if problem=='footer':target['kind']='recap'
    review.update(version=2,base_notes_sha256=digest(notes),changes=[{'target':'block','index':0,'lecture_id':'A-L02',
        'before_sha256':digest(target),'evidence_ids':ids,'reason':'Explicit source review.'}])
    with pytest.raises(WorkflowError):apply(notes,review,{'A-L02':['S001','S002','T001','T002']})


@pytest.mark.parametrize('defect',['stale','foreign','page','text','duplicate'])
def test_reference_rectification_rejects_stale_foreign_and_narrative_changes(defect):
    notes,review=fixture();change=review['changes'][0]
    if defect=='stale':change['before_sha256']='stale'
    if defect=='foreign':change['evidence_ids']=['S001','T001']
    if defect=='page':change['evidence_ids']=['S002','T002']
    if defect=='text':change['text']='A new narrative'
    if defect=='duplicate':review['changes'].append(deepcopy(change))
    with pytest.raises(WorkflowError):apply(notes,review,{'A-L02':['S001','S002','T002']})


def test_stopped_real_controller_can_rectify_refs_deliver_and_reuse_without_model(tmp_path,monkeypatch):
    from mynotes import notes_run,publication,batch_documents
    from test_autonomous_organization import prepared
    from test_progressive_v2 import install_fake_transport
    from test_publication import fake_render
    from mynotes.utils import file_sha256
    ws=prepared(tmp_path);config=ws.configuration_store().load()
    config['providers']['configured']['agy']={'model':'gemini-test','effort':'high'};ws.configuration_store().save(config)
    calls=[]
    def bad_refs(notes,request):
        row=next(r for r in notes['organization'] if r['owner']=='OPS-L02');row['evidence_ids']=['T001']
    install_fake_transport(monkeypatch,calls,transform=bad_refs)
    monkeypatch.setattr(publication,'_render_and_validate',fake_render);monkeypatch.setattr(batch_documents,'_render_and_validate',fake_render)
    stopped=notes_run.run_notes(ws,'OPS',[1,2],provider='agy')
    assert stopped['status']=='FAILED' and 'transcript evidence' in stopped['problem']['message']
    root=ws.runs_dir/'_batches'/stopped['batch_id'];base=next(root.rglob('returned-writing-*.json'))
    response=json.loads(base.read_text())['payload']['response']['notes'];original=file_sha256(base)
    index=next(i for i,r in enumerate(response['organization']) if r['owner']=='OPS-L02');target=response['organization'][index]
    review={'version':1,'base_notes_sha256':digest(response),'reason':'Source checked after stopped delivery.','changes':[
        {'target':'organization','index':index,'lecture_id':'OPS-L02','before_sha256':digest(target),'evidence_ids':['T002'],'reason':'Actual second transcript.'}]}
    finished=notes_run.run_notes(ws,'OPS',resume=stopped['batch_id'],reference_review=review)
    assert finished['status'] in {'COMPLETED','COMPLETED_WITH_WARNINGS'},finished.get('problem')
    assert len(calls)==1 and len(finished['delivery']['files'])==3 and file_sha256(base)==original
    candidate=json.loads((ws.runs_dir/'OPS-L02/lecture_notes.candidate.json').read_text())
    verify(candidate['reference_review'],ws.root)
    again=notes_run.run_notes(ws,'OPS',resume=stopped['batch_id'])
    assert again['reused'] and again['provider_invoked'] is False and len(calls)==1
    receipt=root/'reference-review.json';receipt.write_text('{}')
    blocked=notes_run.run_notes(ws,'OPS',resume=stopped['batch_id'])
    assert blocked['status'] not in {'COMPLETED','COMPLETED_WITH_WARNINGS'} and len(calls)==1
