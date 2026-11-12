"""Standard reviews claims, not source IDs or harmless source typography."""
import pytest

from mynotes.progressive_review import risks
from mynotes.evidence_refs import without_source_citations


def test_source_id_alone_does_not_create_a_numeric_review_and_old_policy_stays_frozen():
    evidence={'S007':{'content':'Responsibility concerns voluntary action.'}}
    block={'kind':'paragraph','text':'Responsibility concerns voluntary action (S007).','evidence_ids':['S007']}
    assert risks(block,evidence,{},'standard',policy=5)==[]
    assert 'numbers_or_dates' in risks(block,evidence,{},'standard',policy=4)
    assert block['text'].endswith('(S007).')


@pytest.mark.parametrize('claim',['The error rate is 37% (S007).','Payment is $5 (S007).',
    'The date is 2024 (S007).',r'The equation is $x_2=3$ (S007).','Count one hundred items (S007).'])
def test_actual_quantities_stay_selected(claim):
    block={'kind':'paragraph','text':claim,'evidence_ids':['S007']}
    result=risks(block,{'S007':{'content':'Source passage.'}},{},'standard',policy=5)
    assert 'numbers_or_dates' in result


def test_unknown_ids_code_and_math_are_not_erased_by_the_analysis_projection():
    text=r'Literal `(S007)` and math $(S007)$; unknown (S999); citation (S007, T001).'
    got=without_source_citations(text,{'S007','T001'})
    assert r'`(S007)`' in got and '$(S007)$' in got and '(S999)' in got
    assert '(S007, T001)' not in got


def test_known_name_variants_match_but_acronyms_inside_unrelated_words_do_not():
    block={'kind':'paragraph','text':'Deep Blue is a system.','evidence_ids':['S001']}
    assert risks(block,{'S001':{'content':'DEEP\nBLUE is a system.'}},{},'standard',policy=5)==[]
    acronym={**block,'text':'AI makes a decision.'}
    assert 'unmatched_name_or_acronym' in risks(acronym,{'S001':{'content':'training methods'}},{},'standard',policy=5)


def test_all_risk_reasons_for_one_block_remain_one_target_not_independent_checks():
    block={'kind':'paragraph','text':'UNKNOWN always causes a 37% improvement.','evidence_ids':['S001']}
    result=risks(block,{'S001':{'content':'An uncertain observation.'}},{},'standard',policy=5)
    assert {'numbers_or_dates','unmatched_name_or_acronym','strong_causal_or_normative_claim'}<=set(result)
