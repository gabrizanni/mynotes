from copy import deepcopy
import pytest
from mynotes.evidence_refs import normalize_references,EvidenceReferenceError


def test_only_reference_fields_change_and_raw_result_is_immutable():
    raw={'text':'T0038 and 0038 must stay literal','expression':r'T_{0038}',
         'evidence_ids':['T0038','S0002'],'visual_id':'S0002','coverage':[{'evidence_id':'T0038'}]}
    before=deepcopy(raw);got,report=normalize_references(raw,{'T038','S002'})
    assert raw==before and got['text']==raw['text'] and got['expression']==raw['expression']
    assert got['evidence_ids']==['T038','S002'] and got['visual_id']=='S002'
    assert len(report)==4


@pytest.mark.parametrize('value,valid,code',[
    ('T0038',{'T038','T00038'},'AMBIGUOUS_REFERENCE'),
    ('S0038',{'T038'},'UNKNOWN_REFERENCE'),
    ('X038',{'T038'},'UNKNOWN_REFERENCE'),
    ('T039',{'T038'},'UNKNOWN_REFERENCE'),
    ('T-038',{'T038'},'UNKNOWN_REFERENCE')])
def test_aliases_never_cross_namespaces_or_guess(value,valid,code):
    with pytest.raises(EvidenceReferenceError) as e:normalize_references({'evidence_ids':[value]},valid)
    assert e.value.defects[0]['code']==code


def test_exact_identity_wins_even_with_numeric_collision():
    got,report=normalize_references({'evidence_ids':['T038']},{'T038','T0038'})
    assert got['evidence_ids']==['T038'] and not report


def test_all_independent_bad_references_are_reported():
    with pytest.raises(EvidenceReferenceError) as e:normalize_references({'blocks':[{'evidence_ids':['T999','S999']} ]},{'T001'})
    assert len(e.value.defects)==2 and e.value.defects[1]['path'].endswith('[1]')
