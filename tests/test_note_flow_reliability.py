"""Delivery policy, independent of private courses or reference documents."""
from copy import deepcopy
from dataclasses import replace
import json
import zipfile

import pytest
from PIL import Image

from mynotes import note_assembly as assembly, progressive_review as r, progressive_wire as w
from mynotes.providers import ProviderError
from test_progressive_v2 import request,unit


def modern(tmp_path):
    req=request(tmp_path)
    return replace(req,batch_context={**req.batch_context,'risk_policy_version':4})


def test_selected_figure_and_dependent_resource_share_the_same_layout_pass(tmp_path):
    req=modern(tmp_path)
    for row in req.source_pack['evidence']:
        if row['id'].startswith('S'):row.update(source_file='deck.pdf',page=int(row['id'][1:]))
    req.source_pack['evidence'].append({'id':'S003','source_file':'deck.pdf','page':3,
        'content':'Additional explanation https://www.youtube.com/watch?v=example'})
    image=tmp_path/'source.png';Image.new('RGB',(100,100),'white').save(image)
    req.source_pack['visuals']=[{'evidence_id':'S002','image_path':str(image)}]
    first,_=w.validate_unit(req,unit(),[]);last=unit(1,True)
    last['organization_delta'].append({'canonical_slide_id':'S003','owner':'TEST-L02','status':'supplement',
        'evidence_ids':[],'reason':'Exact supplied resource.'})
    last['visual_delta']=[{'evidence_id':'S002','decision':'include','inspected':True,'content_kind':'diagram','reason':'Spatial conceptual relationships.'},
        {'evidence_id':'S003','decision':'exclude','inspected':True,'content_kind':'text','reason':'Native title and link.'}]
    last,_=w.validate_unit(req,last,[first]);notes=w.assemble(req,[first,last])
    owner=notes['lessons'][1]['notes'];kinds=[b['kind'] for b in owner['blocks']]
    assert kinds==['overview','paragraph','visual','paragraph','qa','recap']
    assembly.validate_supplement(owner,'S003',{'after':'S002'})
    before=deepcopy(owner);assembly.place_owned_blocks(owner,req.source_pack['evidence']);assert owner==before
    assert sum(b.get('visual_id')=='S002' for b in owner['blocks'])==1


def test_minor_uncertainty_is_a_comment_but_missing_content_cannot_be_downgraded(tmp_path):
    req=modern(tmp_path);raw=unit();block=raw['blocks'][1]
    target={'block_id':'B1','base_sha256':r.digest(block),'block':block,'reasons':['source_uncertainty']}
    decision={'block_id':'B1','base_sha256':target['base_sha256'],'status':'unresolved','severity':'warning',
        'issue_code':'source_uncertainty','reason':'The source leaves this detail uncertain.','blocks':[]}
    reply={'decisions':[decision],'visual_updates':[]}
    checked=r.apply_review(req,raw,[target],reply)
    assert checked['blocks']==raw['blocks'] and len(checked['review_notices'])==1
    note={'blocks':deepcopy(checked['blocks'])};assembly.attach_comments(note,checked['review_notices'])
    assert note['blocks'][1]['review_comments']==['The source leaves this detail uncertain.']
    decision['issue_code']='missing_content'
    with pytest.raises(ProviderError,match='NEEDS_REVIEW'):r.apply_review(req,raw,[target],reply)
    decision.update(issue_code='source_uncertainty',severity='blocking')
    with pytest.raises(ProviderError):r.apply_review(req,raw,[target],reply)


def test_reviewer_cannot_silently_remove_a_selected_inspected_diagram(tmp_path):
    req=modern(tmp_path);raw=unit();choice={'evidence_id':'S001','decision':'include','inspected':True,
        'content_kind':'diagram','reason':'Meaningful spatial arrangement.'};raw['visual_delta']=[choice]
    block=raw['blocks'][1];target={'block_id':'B1','base_sha256':r.digest(block),'block':block,'reasons':['source_graphic_choice']}
    reply={'decisions':[{'block_id':'B1','base_sha256':target['base_sha256'],'status':'approved','reason':'Checked','blocks':[]}],
        'visual_updates':[{**choice,'decision':'exclude','content_kind':'text','reason':'Only labels.'}]}
    checked=r.apply_review(req,raw,[target],reply)
    assert checked['visual_delta']==[choice] and checked['review_notices'][0]['evidence_id']=='S001'
    historical=replace(req,batch_context={**req.batch_context,'risk_policy_version':3})
    assert r.apply_review(historical,raw,[target],reply)['visual_delta'][0]['decision']=='exclude'


def test_explicit_required_figure_is_retained_even_if_its_classifier_says_text(tmp_path):
    req=modern(tmp_path);req=replace(req,batch_context={**req.batch_context,'required_visual_ids':['S002']})
    image=tmp_path/'diagram.png';Image.new('RGB',(32,32),'white').save(image)
    req.source_pack['visuals']=[{'evidence_id':'S002','image_path':str(image)}]
    first,_=w.validate_unit(req,unit(),[]);last=unit(1,True)
    last['visual_delta']=[{'evidence_id':'S002','decision':'exclude','content_kind':'text','inspected':True,'reason':'Classifier disputed the layout.'}]
    note=w.assemble(req,[first,last])['lessons'][1]['notes']
    assert any(b.get('visual_id')=='S002' for b in note['blocks'])
    assert next(v for v in note['visual_decisions'] if v['evidence_id']=='S002')['decision']=='include'
    last['visual_delta'][0]['inspected']=False
    with pytest.raises(ProviderError,match='inspection'):w.assemble(req,[first,last])


def test_required_figure_selection_fails_before_model_when_source_image_is_missing(tmp_path):
    from test_autonomous_organization import prepared
    from mynotes.batch import plan_batch
    from mynotes.workflow import WorkflowError
    ws=prepared(tmp_path);cfg=ws.configuration_store().load()
    cfg['providers']['configured']['agy']={'model':'gemini-test','effort':'high'};ws.configuration_store().save(cfg)
    plan=plan_batch(ws,'OPS',[1,2],provider='agy',autonomous_organization=True,progressive_protocol=2)
    key=next(iter(plan['inventory'])) if 'inventory' in plan else next(iter(plan['ownership']))
    selection={key:{**plan['ownership'][key],'required_visual':True}}
    with pytest.raises(WorkflowError,match='required source figure'):
        plan_batch(ws,'OPS',[1,2],provider='agy',autonomous_organization=True,progressive_protocol=2,selection=selection)


def test_warnings_are_native_word_comments_without_changing_the_body(tmp_path):
    from mynotes.publication import _new_document,append_note_blocks,_validate_comment_links,PublicationError
    document=_new_document({'name':'Test course'},{})
    notes={'blocks':[{'kind':'paragraph','text':'A source-backed explanation.',
        'evidence_ids':['S001'],'review_comments':['A bounded source uncertainty.']}]}
    append_note_blocks(document,notes,{'visuals':[]});path=tmp_path/'warnings.docx';document.save(path)
    with zipfile.ZipFile(path) as z:
        assert b'A bounded source uncertainty.' in z.read('word/comments.xml')
        assert b'commentRangeStart' in z.read('word/document.xml')
        assert _validate_comment_links(z,z.read('word/document.xml'))==1
        malformed=z.read('word/document.xml').replace(b'w:id="0"',b'w:id="999"')
        with pytest.raises(PublicationError,match='dangling'):_validate_comment_links(z,malformed)
    assert [p.text for p in document.paragraphs if p.text]==['A source-backed explanation.']


def test_actual_missing_content_uses_one_small_source_bound_addition(tmp_path):
    req=modern(tmp_path);first,_=w.validate_unit(req,unit(),[]);last=unit(1,True)
    last['blocks'][1].update(text='A different topic already explained.',evidence_ids=['T003'])
    targets=r.coverage_targets(req,last,[first],set());assert len(targets)==1 and targets[0]['block_id']=='C:S002'
    target=targets[0];reply={'decisions':[{'block_id':target['block_id'],'base_sha256':target['base_sha256'],
        'status':'patch','reason':'Missing source concept.','blocks':[{'kind':'paragraph','text':'Maintenance preserves service.',
            'evidence_ids':['S002'],'coverage_role':'supplement'}]}],'visual_updates':[]}
    checked=r.apply_review(req,last,targets,reply);before=deepcopy(last['blocks'])
    note=w.assemble(req,[first,checked])['lessons'][1]['notes']
    assert checked['blocks']==before
    assert sum(b.get('text')=='Maintenance preserves service.' for b in note['blocks'])==1
    assert note['blocks'][-1]['kind']=='recap'


def test_source_attachment_requires_the_exact_explanation_provided_to_the_reviewer(tmp_path):
    req=modern(tmp_path);first,_=w.validate_unit(req,unit(),[]);last=unit(1,True)
    last['blocks'][1]['evidence_ids']=['T003'];target=r.coverage_targets(req,last,[first],set())[0]
    candidate=target['source_link_candidates'][0]
    link={**{k:candidate[k] for k in ('part_index','block_index','base_sha256')},'evidence_id':'S002','reason':'Verified match.'}
    reply={'decisions':[{'block_id':target['block_id'],'base_sha256':target['base_sha256'],'status':'approved','reason':'Already explained.','blocks':[]}],
        'visual_updates':[],'source_links':[link]}
    assert r.apply_review(req,last,[target],reply)['source_links']==[link]
    link['base_sha256']='stale'
    with pytest.raises(ProviderError,match='stale'):r.apply_review(req,last,[target],reply)


def test_ordinary_flow_resolves_a_source_link_and_delivers_warnings_without_manual_json(tmp_path,monkeypatch):
    import subprocess
    from mynotes import agy_progressive as a,notes_run,publication,batch_documents
    from mynotes.providers import _save_attempt
    from mynotes.execution import AttemptStart
    from test_autonomous_organization import prepared
    from test_progressive_v2 import install_fake_transport
    from test_publication import fake_render
    ws=prepared(tmp_path);cfg=ws.configuration_store().load()
    cfg['providers']['configured']['agy']={'model':'gemini-test','effort':'high'};ws.configuration_store().save(cfg)
    writers=[];reviews=[]
    def missing_metadata(notes,request):
        note=notes['lessons'][0]['notes'];note['blocks'][0]['evidence_ids'].append('S002')
        next(b for b in note['blocks'] if b.get('kind')=='paragraph' and b['evidence_ids']==['S002'])['evidence_ids']=['S001']
    install_fake_transport(monkeypatch,writers,transform=missing_metadata)
    def review(packet):
        targets=packet.batch_context['review_task']['targets'];reviews.append(targets)
        reply={'decisions':[],'visual_updates':[],'source_links':[]}
        for target in targets:
            decision={'block_id':target['block_id'],'base_sha256':target['base_sha256'],'status':'approved','reason':'Source checked.','blocks':[]}
            if target['block_id'].startswith('C:'):
                candidate=next(c for c in target['source_link_candidates'] if 'slide S002 ' in c['block'].get('text',''))
                reply['source_links'].append({**{k:candidate[k] for k in ('part_index','block_index','base_sha256')},
                    'evidence_id':target['block_id'][2:],'reason':'The existing paragraph already explains this page.'})
            elif target['block']['kind']=='recap':
                decision.update(status='unresolved',severity='warning',issue_code='minor_editorial',reason='A small editorial uncertainty remains.')
            reply['decisions'].append(decision)
        env={'status':'SUCCESS','response':json.dumps(reply)}
        attempt=_save_attempt(packet,command=['synthetic'],completed=subprocess.CompletedProcess(['synthetic'],0,json.dumps({'event':'result','result':env}),''),
            started=AttemptStart(),envelope={'status':'SUCCESS','source_access':[]})
        return reply,attempt
    monkeypatch.setattr(a,'run_single',review)
    monkeypatch.setattr(publication,'_render_and_validate',fake_render);monkeypatch.setattr(batch_documents,'_render_and_validate',fake_render)
    from test_progressive_v2 import save_historical_job
    save_historical_job(ws)
    finished=notes_run.run_notes(ws,'OPS',[1,2],provider='agy')
    assert finished['status']=='COMPLETED_WITH_WARNINGS',finished.get('problem')
    assert len(writers)==1 and any(t['block_id'].startswith('C:') for group in reviews for t in group)
    candidate=json.loads((ws.runs_dir/'OPS-L01/lecture_notes.candidate.json').read_text())
    block=next(b for b in candidate['notes']['blocks'] if 'slide S002 ' in b.get('text',''))
    assert set(block['evidence_ids'])=={'S001','S002'}
    for file in finished['delivery']['files']:
        with zipfile.ZipFile(file['path']) as z:assert 'word/comments.xml' in z.namelist()
    count=len(reviews);again=notes_run.run_notes(ws,'OPS',resume=finished['batch_id'])
    assert again['provider_invoked'] is False and again['reused'] and len(writers)==1 and len(reviews)==count
