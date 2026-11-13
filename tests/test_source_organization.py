"""Metadata recovery is not new content or invented evidence."""
from copy import deepcopy
from dataclasses import replace
from test_progressive_v2 import request,unit
from mynotes.source_organization import reconcile
from mynotes.progressive_wire import validate_unit,assemble


def test_missing_assignment_restored_from_explicit_same_lesson_content(tmp_path):
    req=request(tmp_path)
    raw=unit();raw['organization_delta']=[]
    value,_=validate_unit(req,raw,[])
    before=deepcopy(value)
    result=reconcile([value],req)
    choice=result[0]['organization_delta'][0]
    assert choice['canonical_slide_id']=='S001'
    assert choice['owner']=='TEST-L01' and choice['status']=='taught'
    assert choice['evidence_ids']==['T001','T002']
    assert value==before and result[0]['blocks']==before['blocks']
    assert reconcile(result,req)==result


def test_unknown_shared_reference_or_unsupported_spoken_claim_is_not_guessed(tmp_path):
    req=request(tmp_path)
    first=unit();first['organization_delta']=[]
    second=unit(1,True);second['blocks'][1]['evidence_ids']=['S001','T003']
    second['organization_delta']=[]
    assert not reconcile([first,second],req)[0]['organization_delta']
    first['blocks'][1]['evidence_ids']=['S001']
    assert not reconcile([first],req)[0]['organization_delta']
    first['blocks'][1]['evidence_ids']=['S001','T003']
    assert not reconcile([first],req)[0]['organization_delta']


def test_supplement_stays_supplement_and_reference_does_not_count(tmp_path):
    req=request(tmp_path);raw=unit();raw['organization_delta']=[]
    raw['blocks'][1]['coverage_role']='reference'
    assert not reconcile([raw],req)[0]['organization_delta']
    raw['blocks'][1]['coverage_role']='supplement'
    choice=reconcile([raw],req)[0]['organization_delta'][0]
    assert choice['status']=='supplement' and choice['evidence_ids']==[]


def test_new_projection_recovers_metadata_without_reinterpreting_old_jobs(tmp_path):
    req=request(tmp_path);one,_=validate_unit(req,unit(),[])
    two,_=validate_unit(req,unit(1,True),[one]);one['organization_delta']=[]
    assert len(assemble(req,[one,two])['organization'])==1
    req=replace(req,batch_context={**req.batch_context,'risk_policy_version':6})
    result=assemble(req,[one,two])
    assert len(result['organization'])==2
    assert result['lessons'][0]['notes']['blocks'][1]['evidence_ids']==one['blocks'][1]['evidence_ids']


def test_natural_sections_allow_twelve_blocks_without_expanding_byte_budget(tmp_path):
    import pytest
    from mynotes.providers import ProviderError
    from mynotes.agy_progressive import protocol
    from mynotes.progressive_wire import schema,MAX_UNIT_BYTES
    old=request(tmp_path);new=replace(old,batch_context={**old.batch_context,'risk_policy_version':6})
    raw=unit();paragraph=raw['blocks'][1]
    raw['blocks']=[raw['blocks'][0],*[{**paragraph,'text':f'Explicit source concept {i}.'} for i in range(8)],*raw['blocks'][2:]]
    with pytest.raises(ProviderError,match='six|6'):validate_unit(old,raw,[])
    value,_=validate_unit(new,raw,[])
    assert len(value['blocks'])==11 and schema(new)['properties']['blocks']['maxItems']==12
    assert MAX_UNIT_BYTES==32768
    assert 'TWELVE' in protocol(new) and 'SIX' in protocol(old)
