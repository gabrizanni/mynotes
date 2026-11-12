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


def install(monkeypatch, calls, *, fail=False, transform=None):
    def run(command,prompt,accept,**kwargs):
        calls.append(1)
        request=active[0]
        assert request.batch_context['progressive_protocol']==3
        result=IntegratedWriter().generate(request).notes
        if transform:transform(result,request)
        envelope={'status':'ERROR','error':'test failure'} if fail else {'status':'SUCCESS','conversation_id':'one','response':json.dumps(result)}
        assert accept(envelope) is None
        return subprocess.CompletedProcess(command,0,json.dumps({'event':'result','result':envelope}),'')
    active=[];original=simple.generate
    def generate(req):active[:]=[req];return original(req)
    monkeypatch.setattr(simple,'generate',generate)
    monkeypatch.setattr('mynotes.agy_progressive.command',lambda *a:['synthetic'])
    monkeypatch.setattr(simple,'_stage_agy_writer',lambda *a:None)
    monkeypatch.setattr(agy_parts,'run_session',run)
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
