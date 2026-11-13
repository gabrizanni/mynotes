"""Single joint writing followed by local delivery; no paid providers."""
from copy import deepcopy
from dataclasses import replace
import json
import subprocess
from pathlib import Path
import pytest
from mynotes import simple_notes as simple, providers, agy_parts
from mynotes.providers import ProviderError,ProviderResponse
from test_autonomous_organization import prepared,IntegratedWriter
from test_publication import fake_render


def install(monkeypatch, calls, *, fail=False, transform=None, split=False, interrupt=False):
    lookup=providers.provider_executable
    monkeypatch.setattr(providers,'provider_executable',lambda name:'/synthetic/agy' if name=='agy' else lookup(name))
    def run(command,prompt,accept,**kwargs):
        calls.append(1)
        request=active[0]
        assert request.batch_context['progressive_protocol']==3
        result=IntegratedWriter().generate(request).notes
        if transform:transform(result,request)
        events=[{'event':'init','conversation_id':'one'}]
        kwargs['on_event'](events[0])
        if split:
            block=result['lessons'][0]['notes']['blocks'][1]
            for index in range(13):
                result['lessons'][0]['notes']['blocks'].insert(1,{'kind':'paragraph','coverage_role':'full',
                    'text':f'Distinct source-supported development {index}.','evidence_ids':block['evidence_ids']})
            portions=[{'lessons':[row],'organization':[c for c in result['organization'] if c['owner']==row['lecture_id']]}
                      for row in result['lessons']]
            if split=='same_lesson':
                first=deepcopy(portions[0]);second=deepcopy(portions[0])
                first['lessons'][0]['notes']['blocks']=first['lessons'][0]['notes']['blocks'][:14]
                first['lessons'][0]['notes']['review_questions']=[]
                note=second['lessons'][0]['notes'];note['blocks']=note['blocks'][14:]
                note.update(title='',summary='',evidence_coverage=[],visual_decisions=[])
                second['organization']=[]
                portions=[first,second,portions[1]]
        else:portions=[result]
        first=1 if '--conversation' in command else 0
        for index in range(first,len(portions)):
            part={'part_index':index,'complete':index==len(portions)-1,'next_topic':'' if index==len(portions)-1 else 'Next lesson', 'result':portions[index]}
            envelope={'status':'ERROR','error':'test failure'} if fail else {'status':'SUCCESS','conversation_id':'one','response':json.dumps(part)}
            events.append({'event':'result','result':envelope})
            following=accept(envelope)
            assert (following is None)==part['complete']
            if following is not None:assert 'APPLICATION-STAGED REQUEST INPUTS' not in following
            if interrupt and index==0:
                from mynotes.providers import ProviderCancelledError
                error=ProviderCancelledError('Explicit test interruption');error.output='\n'.join(json.dumps(e) for e in events);error.stderr=''
                raise error
        return subprocess.CompletedProcess(command,0,'\n'.join(json.dumps(e) for e in events),'')
    active=[];original=simple.generate
    def generate(req):active[:]=[req];return original(req)
    monkeypatch.setattr(simple,'generate',generate)
    monkeypatch.setattr('mynotes.agy_progressive.command',lambda *a:['synthetic'])
    monkeypatch.setattr(simple,'_stage_agy_writer',lambda *a:None)
    monkeypatch.setattr(agy_parts,'run_session',run)
    monkeypatch.setattr(agy_parts,'provider_executable',lambda _:'/synthetic/agy')
    monkeypatch.setattr('mynotes.agy_progressive.run_single',lambda *a:pytest.fail('No Standard reviewer'))
    monkeypatch.setattr('mynotes.batch_recovery.repair_coverage',lambda *a,**kw:pytest.fail('No Standard model repair'))


def test_one_joint_write_delivers_and_reopens_without_model(tmp_path,monkeypatch):
    from mynotes import notes_run,publication,batch_documents
    ws=prepared(tmp_path);cfg=ws.configuration_store().load()
    cfg['providers']['configured']['agy']={'model':'gemini-test','effort':'medium'};ws.configuration_store().save(cfg)
    calls=[];install(monkeypatch,calls)
    renders=[]
    def render(path,*args,**kwargs):
        renders.append(str(path))
        return fake_render(path,*args,**kwargs)
    monkeypatch.setattr(publication,'_render_and_validate',render)
    monkeypatch.setattr(batch_documents,'_render_and_validate',render)
    result=notes_run.run_notes(ws,'OPS',[1,2],provider='agy')
    assert result['status'] in {'COMPLETED','COMPLETED_WITH_WARNINGS'},result.get('problem')
    assert len(calls)==1 and len(result['delivery']['files'])==3
    manifest=json.loads((ws.runs_dir/'_batches'/result['batch_id']/'manifest.json').read_text())
    assert manifest['payload']['progressive_protocol']==3
    assert notes_run.run_notes(ws,'OPS',resume=result['batch_id'])['reused']
    assert calls==[1]
    assert len(renders)==len(set(renders)), 'Same document rendered twice'
    # Publication must verify the actual stream, not assume a decoded outer JSON is SUCCESS.
    from mynotes.publication import _load_candidate,PublicationError
    attempt=next(ws.runs_dir.glob('_batches/*/generation-checkpoints/*/provider-attempts/*.json'))
    record=json.loads(attempt.read_text());events=[json.loads(line) for line in record['stdout'].splitlines()]
    events[-1]['result']['status']='ERROR'
    record['stdout']='\n'.join(json.dumps(event) for event in events)
    attempt.write_text(json.dumps(record))
    with pytest.raises(PublicationError,match='transport'):_load_candidate(ws,'OPS-L01')


def test_error_retains_attempt_and_cannot_restart(tmp_path,monkeypatch):
    from mynotes import notes_run
    ws=prepared(tmp_path);cfg=ws.configuration_store().load()
    cfg['providers']['configured']['agy']={'model':'gemini-test','effort':'medium'};ws.configuration_store().save(cfg)
    calls=[];install(monkeypatch,calls,fail=True)
    result=notes_run.run_notes(ws,'OPS',[1,2],provider='agy')
    assert result['status'] in {'FAILED','NEEDS_REVIEW'}
    assert not list(ws.runs_dir.glob('OPS-L*/lecture_notes.candidate.json'))
    notes_run.run_notes(ws,'OPS',resume=result['batch_id'])
    assert calls==[1]


@pytest.mark.parametrize('interrupt',[False,True])
@pytest.mark.parametrize('split',[True,'same_lesson'])
def test_broad_topics_deliver_without_block_cap_and_resume_saved_prefix(tmp_path,monkeypatch,interrupt,split):
    from mynotes import notes_run,publication,batch_documents
    ws=prepared(tmp_path);cfg=ws.configuration_store().load()
    cfg['providers']['configured']['agy']={'model':'gemini-test','effort':'medium'};ws.configuration_store().save(cfg)
    calls=[];install(monkeypatch,calls,split=split,interrupt=interrupt)
    monkeypatch.setattr(publication,'_render_and_validate',fake_render)
    monkeypatch.setattr(batch_documents,'_render_and_validate',fake_render)
    result=notes_run.run_notes(ws,'OPS',[1,2],provider='agy')
    if interrupt:
        assert result['status'] not in {'COMPLETED','COMPLETED_WITH_WARNINGS'}
        saved=next(ws.runs_dir.glob('_batches/*/generation-checkpoints/*/writing-parts/*/part-000.json'))
        original=saved.read_bytes()
        result=notes_run.run_notes(ws,'OPS',resume=result['batch_id'])
        assert saved.read_bytes()==original
    assert result['status'] in {'COMPLETED','COMPLETED_WITH_WARNINGS'},result.get('problem')
    assert len(result['delivery']['files'])==3 and len(calls)==1+int(interrupt)
    assert notes_run.run_notes(ws,'OPS',resume=result['batch_id'])['reused']
    assert len(calls)==1+int(interrupt)
    receipts=list(ws.runs_dir.glob('_batches/*/generation-checkpoints/*/writing-parts/*/complete.json'))
    assert len(receipts)==1
    receipt=json.loads(receipts[0].read_text())
    assert receipt['block_limit'] is None and len(receipt['parts'])==(3 if split=='same_lesson' else 2)


def test_preview_sheets_are_grouped_hash_bound_but_do_not_claim_full_inspection(tmp_path):
    from PIL import Image
    from test_progressive_v2 import request
    req=request(tmp_path);visuals=[]
    for index in range(13):
        path=tmp_path/f'source-{index}.png';Image.new('RGB',(640,480),(index,10,20)).save(path)
        visuals.append({'evidence_id':f'S{index+1:03}','image_path':str(path)})
    req.source_pack['visuals']=visuals
    sheets=simple.previews(req)
    assert [len(row['members']) for row in sheets]==[12,1]
    assert all(Path(row['path']).is_file() for row in sheets)
    assert all('source_sha256' in member and 'inspected' not in member for row in sheets for member in row['members'])
    assert len({member['source_sha256'] for row in sheets for member in row['members']})==13


def test_new_joint_contract_never_activates_legacy_parts_even_when_forced(tmp_path,monkeypatch):
    from test_progressive_v2 import request
    req=request(tmp_path);req=replace(req,batch_context={**req.batch_context,'progressive_protocol':3})
    monkeypatch.setenv('MYNOTES_AGY_PARTS','1')
    assert providers._agy_uses_parts(req) is False


def test_compact_bookkeeping_preserves_content_and_canonical_contract(tmp_path):
    from test_progressive_v2 import request,unit
    from mynotes import progressive_wire as wire
    from mynotes.batch import batch_notes_schema
    req=replace(request(tmp_path),response_schema=batch_notes_schema(autonomous_organization=True))
    req.batch_context['ownership']=[{'evidence_id':i,'canonical_slide_id':'deck:'+i} for i in ('S001','S002')]
    original=deepcopy(req.response_schema)
    schema=simple.writing_schema(req)
    assert schema['properties']['organization']['items']['properties']['canonical_slide_id']['enum']==['S001','S002']
    assert schema['properties']['lessons']['items']['properties']['notes']['properties']['evidence_coverage']['maxItems']==0
    assert req.response_schema==original
    without_slides=replace(req,batch_context={**req.batch_context,'ownership':[]})
    empty=simple.writing_schema(without_slides)['properties']['organization']
    assert empty['maxItems']==0 and 'enum' not in empty['items']['properties']['canonical_slide_id']
    one,_=wire.validate_unit(req,unit(),[]);two,_=wire.validate_unit(req,unit(1,True),[one])
    notes=wire.assemble(req,[one,two]);blocks=deepcopy([r['notes']['blocks'] for r in notes['lessons']])
    for row in notes['lessons']:row['notes']['evidence_coverage']=[]
    simple.derive_coverage(req,notes)
    assert [r['notes']['blocks'] for r in notes['lessons']]==blocks
    for row in notes['lessons']:
        cited={i for b in row['notes']['blocks'] for i in b['evidence_ids']}
        assert all((c['disposition']=='used')==(c['evidence_id'] in cited) for c in row['notes']['evidence_coverage'])
    prior=deepcopy(notes);simple.derive_coverage(req,notes);assert notes==prior


def test_compact_response_delivers_using_only_existing_citations(tmp_path,monkeypatch):
    from mynotes import notes_run,publication,batch_documents
    ws=prepared(tmp_path);cfg=ws.configuration_store().load()
    cfg['providers']['configured']['agy']={'model':'gemini-test','effort':'medium'};ws.configuration_store().save(cfg)
    def compact(result,request):
        aliases={r['canonical_slide_id']:r['evidence_id'] for r in request.batch_context['ownership']}
        for choice in result['organization']:
            choice['canonical_slide_id']=aliases[choice['canonical_slide_id']]
            note=next(r['notes'] for r in result['lessons'] if r['lecture_id']==choice['owner'])
            block=next(b for b in note['blocks'] if b['kind']=='paragraph' and choice['canonical_slide_id'] in b['evidence_ids'])
            block['evidence_ids'].extend(choice['evidence_ids'])
            choice['evidence_ids']=[]
        for row in result['lessons']:row['notes']['evidence_coverage']=[]
    calls=[];install(monkeypatch,calls,transform=compact)
    monkeypatch.setattr(publication,'_render_and_validate',fake_render)
    monkeypatch.setattr(batch_documents,'_render_and_validate',fake_render)
    result=notes_run.run_notes(ws,'OPS',[1,2],provider='agy')
    assert result['status'] in {'COMPLETED','COMPLETED_WITH_WARNINGS'},result.get('problem')
    assert notes_run.run_notes(ws,'OPS',resume=result['batch_id'])['reused']
    assert calls==[1]


def test_joint_figure_assembly_uses_source_alias_then_preserves_canonical_ownership(tmp_path):
    from test_progressive_v2 import request,unit
    from mynotes import progressive_wire as wire
    req=request(tmp_path)
    one,_=wire.validate_unit(req,unit(),[]);two,_=wire.validate_unit(req,unit(1,True),[one])
    notes=wire.assemble(req,[one,two])
    req.batch_context['ownership']=[{'evidence_id':'S001','canonical_slide_id':'deck:page:1'}, {'evidence_id':'S002','canonical_slide_id':'deck:page:2'}]
    for choice in notes['organization']:choice['canonical_slide_id']={'S001':'deck:page:1','S002':'deck:page:2'}[choice['canonical_slide_id']]
    req.source_pack['visuals']=[{'evidence_id':'S001','image_path':str(tmp_path/'source.png')}]
    notes['lessons'][0]['notes']['visual_decisions']=[{'evidence_id':'S001','decision':'include','inspected':True,'content_kind':'diagram','reason':'Spatial relationship'}]
    original=deepcopy(notes)
    projected=simple.project(req,notes)
    assert any(b.get('visual_id')=='S001' for b in projected['lessons'][0]['notes']['blocks'])
    assert projected['organization'][0]['canonical_slide_id']=='deck:page:1'
    assert notes==original


def test_new_base_keeps_low_confidence_sources_and_import_uncertainty_losslessly(tmp_path):
    from test_progressive_v2 import request
    from mynotes.providers import _context
    from mynotes.progressive_wire import restore_sources
    req=request(tmp_path,'base')
    req=replace(req,context_mode='reduced',batch_context={**req.batch_context,'progressive_protocol':3,'risk_policy_version':6})
    row=req.source_pack['evidence'][2]
    row.update(confidence=.3,needs_review=True,alternative='Exact alternative',agreement=.42,source_engine='imported')
    restored=restore_sources(json.loads(_context(req).splitlines()[-1]))
    assert len(restored)==len(req.source_pack['evidence'])
    target=next(r for r in restored if r['id']==row['id'])
    assert all(target[k]==row[k] for k in ('confidence','needs_review','alternative','agreement','source_engine'))


def test_minor_recap_language_issue_delivers_with_comment_not_model_repair(tmp_path,monkeypatch):
    from mynotes import notes_run,publication,batch_documents
    from zipfile import ZipFile
    ws=prepared(tmp_path);cfg=ws.configuration_store().load()
    cfg['providers']['configured']['agy']={'model':'gemini-test','effort':'medium'};ws.configuration_store().save(cfg)
    def english(result,request):
        assert request.style.get('recap_language','it')=='it'
        for lesson in result['lessons']:
            recap=next(b for b in lesson['notes']['blocks'] if b['kind']=='recap')
            recap['text']='The source explains this mechanism which connects these concepts with their context while preserving the distinction between them. '*5
    calls=[];install(monkeypatch,calls,transform=english)
    monkeypatch.setattr(publication,'_render_and_validate',fake_render)
    monkeypatch.setattr(batch_documents,'_render_and_validate',fake_render)
    result=notes_run.run_notes(ws,'OPS',[1,2],provider='agy')
    assert result['status']=='COMPLETED_WITH_WARNINGS',result.get('problem')
    assert calls==[1]
    for row in result['delivery']['files']:
        with ZipFile(row['path']) as doc:
            assert 'riepilogo' in doc.read('word/comments.xml').decode()


def test_local_inspection_failure_preserves_successful_writing_for_resume(tmp_path,monkeypatch):
    from mynotes import notes_run
    ws=prepared(tmp_path);cfg=ws.configuration_store().load()
    cfg['providers']['configured']['agy']={'model':'gemini-test','effort':'medium'};ws.configuration_store().save(cfg)
    def unobserved(result,request):
        result['lessons'][0]['notes']['visual_decisions']=[{'evidence_id':'S001','decision':'include','inspected':True,'reason':'Unobserved test figure'}]
    calls=[];install(monkeypatch,calls,transform=unobserved)
    result=notes_run.run_notes(ws,'OPS',[1,2],provider='agy')
    assert result['status']=='NEEDS_REVIEW'
    attempts=list(ws.runs_dir.glob('_batches/*/generation-checkpoints/*/provider-attempts/*.json'))
    assert len(attempts)==1
    record=json.loads(attempts[0].read_text())
    assert record['error'] is None and record['envelope']['status']=='SUCCESS'
    assert list(ws.runs_dir.glob('_batches/*/generation-checkpoints/*/returned-writing-*.json'))
    again=notes_run.run_notes(ws,'OPS',resume=result['batch_id'])
    assert again['status']=='NEEDS_REVIEW' and calls==[1]
    assert not list(ws.runs_dir.glob('OPS-L*/lecture_notes.candidate.json'))


def test_provided_video_links_are_preserved_locally_not_invented_as_a_lecture(tmp_path):
    from test_progressive_v2 import request,unit
    from mynotes import progressive_wire as wire
    req=request(tmp_path)
    one,_=wire.validate_unit(req,unit(),[]);two,_=wire.validate_unit(req,unit(1,True),[one])
    notes=wire.assemble(req,[one,two])
    req.batch_context.update(progressive_protocol=3,ownership=[{'evidence_id':'S001','canonical_slide_id':'deck:page:1'},{'evidence_id':'S002','canonical_slide_id':'deck:page:2'}])
    req.source_pack['evidence'][1].update(content='First video https://www.youtube.com/watch?v=one Second video https://youtu.be/two 2',page=2,source_file='deck.pdf')
    owner=notes['lessons'][1]['notes'];owner['blocks']=[b for b in owner['blocks'] if b.get('coverage_role') not in {'full','supplement'}]
    before=deepcopy(notes)
    after=simple.project(req,notes)
    blocks=after['lessons'][1]['notes']['blocks']
    derived=[b for b in blocks if b.get('source_derivation',{}).get('kind')=='exact_source_resource']
    assert len(derived)==1 and len(derived[0]['items'])==2
    assert 'First video' in derived[0]['items'][0] and 'https://youtu.be/two' in derived[0]['items'][1]
    assert blocks.index(derived[0])<next(i for i,b in enumerate(blocks) if b['kind']=='qa')
    assert simple.project(req,after)==after
    assert notes==before


def test_resource_only_page_does_not_certify_unsupported_teaching_claim(tmp_path):
    from test_progressive_v2 import request,unit
    from mynotes import progressive_wire as wire
    req=request(tmp_path)
    one,_=wire.validate_unit(req,unit(),[]);two,_=wire.validate_unit(req,unit(1,True),[one])
    notes=wire.assemble(req,[one,two])
    req.batch_context.update(progressive_protocol=3,ownership=[{'evidence_id':'S001','canonical_slide_id':'deck:page:1'},{'evidence_id':'S002','canonical_slide_id':'deck:page:2'}])
    req.source_pack['evidence'][1].update(content='Source video https://youtu.be/exact 2',page=2)
    owner=notes['lessons'][1]['notes'];owner['blocks']=[b for b in owner['blocks'] if b.get('coverage_role') not in {'full','supplement'}]
    decision=next(c for c in notes['organization'] if c['canonical_slide_id']=='S002')
    decision.update(status='taught',evidence_ids=['S002'])
    original=deepcopy(notes);after=simple.project(req,notes)
    decision=next(c for c in after['organization'] if c['canonical_slide_id']=='deck:page:2')
    assert decision['status']=='supplement' and decision['evidence_ids']==[]
    block=next(b for b in after['lessons'][1]['notes']['blocks'] if b.get('source_derivation',{}).get('kind')=='exact_source_resource')
    assert block['evidence_ids']==['S002'] and 'https://youtu.be/exact' in block['text']
    assert 'unverified_resource_teaching' in json.dumps(after)
    assert simple.project(req,after)==after and notes==original


def test_native_heading_alias_is_normalized_without_rewriting_content():
    from mynotes.batch import batch_notes_schema
    from mynotes.progressive_wire import native_schema
    from mynotes.providers import _transport_normalization
    contract=batch_notes_schema(autonomous_organization=True)
    native=native_schema(contract)
    props=native['properties']['lessons']['items']['properties']['notes']['properties']['blocks']['items']['properties']
    assert 'heading' in props
    raw={'lessons':[{'lecture_id':'TEST-L01','notes':{'blocks':[{'kind':'heading','heading':'Source concept','level':3,'evidence_ids':['S001']}]}}]}
    normalized,receipt=_transport_normalization(raw,contract,provider='agy')
    block=normalized['lessons'][0]['notes']['blocks'][0]
    assert block=={'kind':'heading','text':'Source concept','level':3,'evidence_ids':['S001']}
    assert receipt['transformations'][0]['kind']=='heading_text_alias'
    assert raw['lessons'][0]['notes']['blocks'][0]['heading']=='Source concept'
    raw['lessons'][0]['notes']['blocks'][0]['text']='Conflicting concept'
    with pytest.raises(ProviderError,match='Conflicting'):_transport_normalization(raw,contract,provider='agy')


def test_joint_clarification_can_cite_both_transcripts_without_copying_input(tmp_path,monkeypatch):
    from mynotes import notes_run,publication,batch_documents
    ws=prepared(tmp_path);cfg=ws.configuration_store().load()
    cfg['providers']['configured']['agy']={'model':'gemini-test','effort':'medium'};ws.configuration_store().save(cfg)
    originals={p:p.read_bytes() for p in ws.runs_dir.glob('OPS-L*/source_pack.json')}
    def clarify(result,request):
        speech=[r for r in request.source_pack['evidence'] if r['id'].startswith('T')]
        assert len(speech)==2
        assert {r['text'] for r in speech}=={f'Complete transcript for distinct lecture {n} topics.' for n in (1,2)}
        assert len([r for r in request.source_pack['evidence'] if r['id'].startswith('S')])==4
        own=request.batch_context['lesson_evidence']
        turns={lesson:[i for i in ids if i.startswith('T')] for lesson,ids in own.items()}
        assert all(len(ids)==1 for ids in turns.values())
        assert turns['OPS-L01']!=turns['OPS-L02']
        paragraph=next(b for b in result['lessons'][0]['notes']['blocks'] if b['kind']=='paragraph')
        paragraph['evidence_ids']+=turns['OPS-L01']+turns['OPS-L02']
        paragraph['text']='One main explanation integrates the later transcript clarification.'
    calls=[];install(monkeypatch,calls,transform=clarify)
    monkeypatch.setattr(publication,'_render_and_validate',fake_render)
    monkeypatch.setattr(batch_documents,'_render_and_validate',fake_render)
    result=notes_run.run_notes(ws,'OPS',[1,2],provider='agy')
    assert result['status'] in {'COMPLETED','COMPLETED_WITH_WARNINGS'},result.get('problem')
    assert calls==[1]
    candidate=json.loads((ws.runs_dir/'OPS-L01/lecture_notes.candidate.json').read_text())
    pack=json.loads(Path(candidate['source_pack']).read_text())
    foreign=next(r for r in pack['evidence'] if r.get('origin_lecture_id')=='OPS-L02' and r['id'].startswith('T'))
    block=next(b for b in candidate['notes']['blocks'] if b.get('text','').startswith('One main explanation'))
    assert foreign['id'] in block['evidence_ids'] and foreign['origin_evidence_id']=='T001'
    assert {p:p.read_bytes() for p in originals}==originals
    assert notes_run.run_notes(ws,'OPS',resume=result['batch_id'])['reused'] and calls==[1]


def test_foreign_clarification_does_not_prove_the_owner_taught_a_slide(tmp_path,monkeypatch):
    from mynotes import notes_run
    ws=prepared(tmp_path);cfg=ws.configuration_store().load()
    cfg['providers']['configured']['agy']={'model':'gemini-test','effort':'medium'};ws.configuration_store().save(cfg)
    def foreign_proof(result,request):
        result['organization'][0]['evidence_ids']=[i for i in request.batch_context['lesson_evidence']['OPS-L02'] if i.startswith('T')]
    calls=[];install(monkeypatch,calls,transform=foreign_proof)
    result=notes_run.run_notes(ws,'OPS',[1,2],provider='agy')
    assert result['status'] in {'FAILED','NEEDS_REVIEW'} and calls==[1]
    assert 'transcript' in json.dumps(result.get('problem')).lower()
    assert not list(ws.runs_dir.glob('OPS-L*/lecture_notes.candidate.json'))


def test_standard_keeps_foreign_page_recall_without_another_image_decision(tmp_path,monkeypatch):
    from mynotes import notes_run,publication,batch_documents
    ws=prepared(tmp_path);cfg=ws.configuration_store().load()
    cfg['providers']['configured']['agy']={'model':'gemini-test','effort':'medium'};ws.configuration_store().save(cfg)
    from PIL import Image
    image=tmp_path/'text-page.png';Image.new('RGB',(100,100),'white').save(image)
    source=ws.runs_dir/'OPS-L01/source_pack.json';pack=json.loads(source.read_text())
    pack['visuals']=[{'evidence_id':'S001','image_path':str(image)}];source.write_text(json.dumps(pack))
    def recall(result,request):
        second=result['lessons'][1]['notes']
        second['blocks'].insert(1,{'kind':'paragraph','text':'Brief recall linking the earlier main explanation to the current discussion.',
            'evidence_ids':['S001'],'coverage_role':'full'})
        second['visual_decisions']=[v for v in second['visual_decisions'] if v['evidence_id']!='S001']
    calls=[];install(monkeypatch,calls,transform=recall)
    monkeypatch.setattr(publication,'_render_and_validate',fake_render)
    monkeypatch.setattr(batch_documents,'_render_and_validate',fake_render)
    result=notes_run.run_notes(ws,'OPS',[1,2],provider='agy')
    assert result['status']=='COMPLETED_WITH_WARNINGS',result.get('problem')
    note=json.loads((ws.runs_dir/'OPS-L02/lecture_notes.candidate.json').read_text())['notes']
    block=next(b for b in note['blocks'] if b.get('text','').startswith('Brief recall'))
    assert block['coverage_role']=='reference' and block['prior_lecture_id']=='OPS-L01'
    assert block['evidence_ids']==['S001'] and block['review_comments']
    assert next(v for v in note['visual_decisions'] if v['evidence_id']=='S001')['decision']=='exclude'
    assert calls==[1]
