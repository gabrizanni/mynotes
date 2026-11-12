"""Observed previews are not full inspections; full data-image gates remain."""
from copy import deepcopy
from pathlib import Path
import pytest
from PIL import Image
from test_progressive_v2 import request
from mynotes.simple_notes import previews
from mynotes.source_inspection import validate
from mynotes.utils import file_sha256
from mynotes.providers import ProviderError


def case(tmp_path):
    req=request(tmp_path);image=tmp_path/'source.png';Image.new('RGB',(300,200),'white').save(image)
    req.source_pack['visuals']=[{'evidence_id':'S001','image_path':str(image)}]
    req.source_pack['evidence'][0]['content']='Course title'
    req.batch_context['ownership']=[{'canonical_slide_id':'deck:page:1','evidence_id':'S001'}]
    sheet=previews(req)[0]
    notes={'lessons':[{'lecture_id':'TEST-L01','notes':{'visual_decisions':[{'evidence_id':'S001','decision':'exclude','content_kind':'decoration','inspected':True,'reason':'Title'}]}}],
        'organization':[{'canonical_slide_id':'deck:page:1','owner':'TEST-L01','status':'material','evidence_ids':['S001'],
                         'structural_review':{'kind':'title','image_sha256':file_sha256(image),'reason':'Inspected title'}}]}
    return req,notes,sheet,[{'sha256':sheet['sha256'],'path':sheet['path']}]


def test_preview_normalizes_false_full_claim_without_inventing_access(tmp_path):
    req,notes,sheet,access=case(tmp_path);before=deepcopy(access)
    notices=validate(notes,req,access,[sheet])
    assert notes['lessons'][0]['notes']['visual_decisions'][0]['inspected'] is False
    assert notes['organization'][0]['evidence_ids']==[]
    assert notices and access==before
    assert 'not a full-image inspection' in notes['organization'][0]['structural_review']['reason']


@pytest.mark.parametrize('defect',['not_opened','changed_sheet','changed_source','numeric','diagram','include','required'])
def test_preview_cannot_bypass_real_inspection_requirements(tmp_path,defect):
    req,notes,sheet,access=case(tmp_path)
    choice=notes['lessons'][0]['notes']['visual_decisions'][0]
    if defect=='not_opened':access=[]
    if defect=='changed_sheet':Path(sheet['path']).write_bytes(b'changed')
    if defect=='changed_source':Image.new('RGB',(300,200),'black').save(req.source_pack['visuals'][0]['image_path'])
    if defect=='numeric':req.source_pack['evidence'][0]['content']='Rate 37%'
    if defect=='diagram':choice['content_kind']='diagram'
    if defect=='include':choice['decision']='include'
    if defect=='required':req.batch_context['required_visual_ids']=['S001']
    with pytest.raises(ProviderError):validate(notes,req,access,[sheet])


def test_full_image_observation_still_authorizes_included_diagram(tmp_path):
    req,notes,sheet,access=case(tmp_path)
    choice=notes['lessons'][0]['notes']['visual_decisions'][0];choice.update(decision='include',content_kind='diagram')
    access.append({'path':req.source_pack['visuals'][0]['image_path'],'sha256':file_sha256(Path(req.source_pack['visuals'][0]['image_path']))})
    assert validate(notes,req,access,[sheet])==[]


def test_preview_manifest_is_source_data_not_trusted_configuration(tmp_path):
    import json
    from dataclasses import replace
    from mynotes.providers import _context
    req,notes,sheet,access=case(tmp_path)
    req=replace(req,batch_context={**req.batch_context,'preview_sheets':[sheet]})
    parts=_context(req).splitlines()
    trusted=json.loads(parts[1]);untrusted=json.loads(parts[-1])
    assert 'preview_sheets' not in trusted['coordinated_batch']
    assert untrusted['preview_sheets']==[sheet]
    assert sheet['image_path']==sheet['path']
