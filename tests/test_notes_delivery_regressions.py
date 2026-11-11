"""Public synthetic fixtures for the private comparison's failure classes."""
import json
from dataclasses import replace

import pytest

from mynotes import publication
from mynotes.batch import generate_batch
from mynotes.providers import ProviderError, _prompt
from mynotes.sourcepack import build_source_pack
from mynotes.workflow import WorkflowError
from test_autonomous_organization import prepared, IntegratedWriter
from test_batch_generation import VERIFIED
from test_inline_currency import rendered


def test_currency_followed_by_several_math_percentages_preserves_all_prose():
    text = (r'Participants received $1 to read a profile. Their accuracy was $67\%$, '
            r'compared with $65\%$. The error rates were $37\%$ and $27\%$, '
            r'while the other comparison gave $40\%$ and $29\%$ for the two groups.')
    paragraph, actual = rendered(text)
    expected = text
    for percent in (67, 65, 37, 27, 40, 29):
        expected = expected.replace(f'${percent}\\%$', f'{percent}%')
    assert actual == expected
    assert len(paragraph._p.xpath('.//m:oMath')) == 6
    assert all(len(''.join(node.itertext())) < 100 for node in paragraph._p.xpath('.//m:oMath'))


def test_joint_context_has_one_deck_two_distinct_complete_transcripts(tmp_path):
    ws = prepared(tmp_path); writer = IntegratedWriter()
    result = generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True,
                            budgets=VERIFIED, provider_adapter=writer)
    assert result['status'] == 'CANDIDATE_READY' and len(writer.calls) == 1
    request = writer.calls[0]
    assert len([r for r in request.source_pack['evidence'] if r['id'].startswith('S')]) == 4
    assert len([r for r in request.source_pack['evidence'] if r['id'].startswith('T')]) == 2
    assert len(result['candidates']) == 2
    assert not result['pending_course_slides']
    prompt = _prompt(request)
    assert 'false-positive rate uses actual negatives' in prompt
    assert 'descriptive distribution chart does not establish causal' in prompt
    assert 'assign leftovers to the last lesson by default' in request.batch_context['organization_instructions']


def test_unknown_joint_limits_are_explicit_but_do_not_force_repeated_decks(tmp_path):
    ws = prepared(tmp_path); writer = IntegratedWriter()
    result = generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True, provider_adapter=writer)
    assert len(writer.calls) == 1
    assert result['budget_basis'] == 'cli_only_model_limits_unknown'
    assert result['warnings'][0]['code'] == 'UNVERIFIED_MODEL_LIMITS'


def test_too_small_configured_joint_budget_never_invokes_writer(tmp_path):
    ws = prepared(tmp_path); writer = IntegratedWriter()
    with pytest.raises(WorkflowError, match='No provider was invoked'):
        generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True,
                       budgets=replace(VERIFIED, context_tokens=100), provider_adapter=writer)
    assert not writer.calls


def test_failed_envelope_cannot_trigger_whole_job_restart_on_resume(tmp_path):
    ws = prepared(tmp_path)
    class Failed:
        calls = 0
        def generate(self, request):
            self.calls += 1
            raise ProviderError('ERROR envelope: interrupted stream')
    writer = Failed()
    with pytest.raises(ProviderError, match='ERROR envelope'):
        generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True,
                       budgets=VERIFIED, provider_adapter=writer)
    identifier = next((ws.runs_dir / '_batches').iterdir()).name
    with pytest.raises(WorkflowError, match='no automatic whole-lesson restart'):
        generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True,
                       budgets=VERIFIED, provider_adapter=writer, batch_id=identifier)
    assert writer.calls == 1
    assert not list(ws.runs_dir.glob('OPS-L*/lecture_notes.candidate.json'))


@pytest.mark.parametrize('legacy_marker_only', [False, True])
def test_updating_program_cannot_reset_failed_whole_writing_guard(tmp_path, monkeypatch, legacy_marker_only):
    from mynotes import batch
    ws = prepared(tmp_path)
    class Failed:
        calls = 0
        def generate(self, request):
            self.calls += 1
            raise ProviderError('Interrupted stream')
    writer = Failed()
    with pytest.raises(ProviderError):
        generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True, provider_adapter=writer)
    root = next((ws.runs_dir / '_batches').iterdir())
    if legacy_marker_only:
        for marker in (root / 'invocation-guards').glob('*.json'):
            marker.unlink()  # Construct the previous guard format.
    original = batch.file_sha256
    monkeypatch.setattr(batch, 'file_sha256', lambda p: 'updated-provider-module' if str(p).endswith('/providers.py') else original(p))
    with pytest.raises(WorkflowError, match='no automatic whole-lesson restart'):
        generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True,
                       provider_adapter=writer, batch_id=root.name)
    assert writer.calls == 1


def test_joint_portions_reach_two_words_and_resume_without_another_session(tmp_path, monkeypatch):
    import sys
    from copy import deepcopy
    from mynotes import agy_parts, providers, publication, batch_documents
    from mynotes.notes_run import run_notes
    from test_agy_parts import _fake_session
    from test_publication import fake_render
    ws = prepared(tmp_path)
    config = ws.configuration_store().load()
    config['providers']['default'] = 'agy'
    config['providers']['configured']['agy'] = {'model': 'test-model', 'effort': 'high'}
    ws.configuration_store().save(config)
    # This fixture exercises the historical v1 transport, not the new v2 wire.
    from mynotes.batch import plan_batch, _save
    historical = plan_batch(ws, 'OPS', [1, 2], provider='agy', budgets=VERIFIED, autonomous_organization=True)
    _save(ws.runs_dir/'_batches'/historical['batch_id']/'manifest.json', historical)
    commands = []
    monkeypatch.setenv('MYNOTES_AGY_PARTS', '1')
    monkeypatch.setattr(providers, 'provider_executable', lambda _: sys.executable)
    monkeypatch.setattr(agy_parts, 'provider_executable', lambda _: sys.executable)
    monkeypatch.setattr(publication, '_render_and_validate', fake_render)
    monkeypatch.setattr(batch_documents, '_render_and_validate', fake_render)
    def session(command, prompt, accept, **kwargs):
        # The actual staged request supplies maps and ownership to the writer.
        # Recover them from the original request captured below, not literals.
        response = IntegratedWriter().generate(captured[0]).notes
        parts = [{'part_index': i, 'complete': i == 1,
                  'next_topic': '' if i == 1 else 'Start the second selected lesson.',
                  'result': {'organization': deepcopy(response['organization']) if i == 0 else [],
                             'lessons': [deepcopy(response['lessons'][i])]}} for i in range(2)]
        return _fake_session(parts, commands)(command, prompt, accept, **kwargs)
    captured = []
    original = agy_parts.generate_parts
    def record(request):
        captured.append(request)
        return original(request)
    monkeypatch.setattr(agy_parts, 'generate_parts', record)
    monkeypatch.setattr(agy_parts, 'run_session', session)
    first = run_notes(ws, 'OPS', [1, 2], budgets=VERIFIED)
    assert first['status'] == 'COMPLETED', first.get('problem')
    assert len(commands) == 1 and len(captured) == 1
    assert len(first['delivery']['files']) == 3
    assert len(list(ws.runs_dir.glob('_batches/*/generation-checkpoints/*/writing-parts/*/part-*.json'))) == 2
    second = run_notes(ws, 'OPS', resume=first['batch_id'])
    assert second['reused'] and not second['provider_invoked']
    assert len(commands) == 1


def test_failed_targeted_correction_is_not_repeated_after_program_update(tmp_path, monkeypatch):
    import subprocess
    from mynotes import batch
    from mynotes.providers import _save_attempt, AttemptStart, ProviderResponse
    ws = prepared(tmp_path)
    class FailedRepair(IntegratedWriter):
        def generate(self, request):
            if request.phase == 'coverage_repair':
                self.calls.append(request)
                raise ProviderError('Synthetic correction transport failure')
            response = super().generate(request)
            first = response.notes['lessons'][0]['notes']
            first['blocks'] = [r for r in first['blocks'] if not (r.get('coverage_role') == 'full' and r['evidence_ids'] == ['S002'])]
            path = _save_attempt(request, command=['synthetic'], started=AttemptStart(),
                completed=subprocess.CompletedProcess(['synthetic'], 0, json.dumps(response.notes), ''))
            return ProviderResponse(response.notes, response.provider, response.model, response.effort, attempt_path=str(path))
    writer = FailedRepair()
    with pytest.raises(ProviderError, match='correction transport'):
        generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True,
                       budgets=VERIFIED, provider_adapter=writer, repair_missing=True)
    root = next((ws.runs_dir / '_batches').iterdir())
    marker = next(root.glob('generation-checkpoints/*/coverage-repair/started.json'));before=marker.read_bytes()
    original = batch.file_sha256
    monkeypatch.setattr(batch, 'file_sha256', lambda p: 'updated-provider-module' if str(p).endswith('/providers.py') else original(p))
    with pytest.raises(WorkflowError, match='correction already attempted'):
        generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True,
                       budgets=VERIFIED, provider_adapter=writer, repair_missing=True, batch_id=root.name)
    assert len(writer.calls) == 2 and marker.read_bytes() == before


@pytest.mark.parametrize('suffix', ['txt', 'json'])
def test_validated_import_updates_hint_without_changing_writing_style(tmp_path, suffix):
    from mynotes.memory import writing_configuration
    ws = prepared(tmp_path)
    transcript = tmp_path / 'imports' / f'lesson.{suffix}'; transcript.parent.mkdir()
    transcript.write_text('An observed mechanism.' if suffix == 'txt' else json.dumps({
        'segments': [{'id': 'T001', 'text': 'An observed mechanism.', 'start_seconds': 0, 'end_seconds': 5}]}))
    before = writing_configuration(ws.configuration_store().load(), 'OPS')
    build_source_pack(ws, 'OPS-L01', transcript_path=transcript)
    settings = ws.configuration_store().load()
    assert settings['courses'][0]['folders']['transcripts'] == str(transcript.parent)
    assert writing_configuration(settings, 'OPS') == before


def pdf_fixture(path, *, x=50, y=750, text='Ordinary text'):
    """Minimal real PDF with positioned Helvetica text; no Office dependency."""
    stream = f'BT /F1 12 Tf {x} {y} Td ({text}) Tj ET'.encode()
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    data = b'%PDF-1.4\n'; offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data)); data += f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(data)
    data += b'xref\n0 6\n0000000000 65535 f \n'
    data += b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    data += f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
    path.write_bytes(data)


@pytest.mark.parametrize('x,text,rejected', [(50, 'Ordinary text', False), (540, 'Outside printable area', True), (50, '', True)])
def test_actual_pdf_geometry_blocks_overflow_and_empty_pages(tmp_path, monkeypatch, x, text, rejected):
    pdf = tmp_path / 'render.pdf'; pdf_fixture(pdf, x=x, text=text)
    monkeypatch.setattr(publication, '_soffice', lambda: 'fixture-renderer')
    monkeypatch.setattr(publication, 'convert_to_pdf', lambda *a, **k: pdf)
    if rejected:
        with pytest.raises(publication.PublicationError, match='printable area|empty page'):
            publication._render_and_validate(tmp_path / 'input.docx', tmp_path)
    else:
        assert publication._render_and_validate(tmp_path / 'input.docx', tmp_path)['pages'][0]['characters'] > 0


def test_page_number_alone_does_not_hide_an_empty_final_page(tmp_path, monkeypatch):
    pdf = tmp_path / 'footer-only.pdf'; pdf_fixture(pdf, x=290, y=20, text='1')
    monkeypatch.setattr(publication, '_soffice', lambda: 'fixture-renderer')
    monkeypatch.setattr(publication, 'convert_to_pdf', lambda *a, **k: pdf)
    with pytest.raises(publication.PublicationError, match='empty page'):
        publication._render_and_validate(tmp_path / 'input.docx', tmp_path)


def test_footer_layout_is_normalized_without_rewriting_questions_or_narrative(tmp_path):
    from copy import deepcopy
    from test_batch_generation import workspace_and_deck, Adapter
    ws,_,selection=workspace_and_deck(tmp_path,lessons=1,slides=2);raw=[]
    def misplaced_footer(result, request):
        note=result['lessons'][0]['notes'];blocks=note['blocks']
        recap=blocks.pop();blocks.insert(-1,recap)
        blocks.insert(-2,{'kind':'heading','text':'Synthesis and review','level':3,'evidence_ids':['S001']})
        raw.append(deepcopy(note))
    result=generate_batch(ws,'OPS',[1],selection=selection,provider_adapter=Adapter(misplaced_footer))
    from pathlib import Path
    note=json.loads(Path(result['candidates'][0]['candidate']).read_text())['notes']
    assert [b['kind'] for b in note['blocks'][-2:]]==['qa','recap']
    assert note['blocks'][-1]==next(b for b in raw[0]['blocks'] if b['kind']=='recap')
    assert note['blocks'][-2]==next(b for b in raw[0]['blocks'] if b['kind']=='qa')
    assert note['render_normalizations'][0]['narrative_and_answers_unchanged']


def test_reviewed_defects_bind_exact_response_and_real_block_sources(tmp_path):
    from mynotes.batch import plan_batch, _prepared_context, _request, _hash
    from mynotes.providers import RequestedModel
    from mynotes.batch_recovery import validate_review_defects
    ws = prepared(tmp_path)
    plan = plan_batch(ws, 'OPS', [1, 2], autonomous_organization=True)
    packs, sources, inventory, _ = _prepared_context(ws, plan['lectures'], autonomous_organization=True)
    runtime = {**plan, 'packs': packs, 'sources': sources, 'inventory': inventory, 'requested': RequestedModel(**plan['requested_model'])}
    request, maps = _request(runtime, plan['lectures'], tmp_path/'request', [])
    response = IntegratedWriter().generate(request)
    block = response.notes['lessons'][0]['notes']['blocks'][1]
    inverse = {v: k for k, v in maps['OPS-L01'].items()}
    defect = {'lecture_id': 'OPS-L01', 'path': 'blocks[1]', 'evidence_id': inverse[block['evidence_ids'][0]], 'reason': 'Observed source mismatch'}
    review = {'base_notes_sha256': _hash(response.notes), 'defects': [defect]}
    assert validate_review_defects(review, response, plan['lectures'], maps) == [defect]
    with pytest.raises(WorkflowError, match='exact retained'):
        validate_review_defects({**review, 'base_notes_sha256': 'changed'}, response, plan['lectures'], maps)
    with pytest.raises(WorkflowError, match='actual source'):
        validate_review_defects({**review, 'defects': [{**defect, 'evidence_id': 'S999'}]}, response, plan['lectures'], maps)


def test_targeted_visual_insertion_preserves_original_prose_and_requires_inspection():
    from copy import deepcopy
    from mynotes.batch import _hash
    from mynotes.batch_recovery import apply_correction
    body = {'kind': 'paragraph', 'text': 'Original explanation.', 'evidence_ids': ['S001'], 'coverage_role': 'full'}
    original = {'lessons': [{'lecture_id': 'OPS-L01', 'notes': {'blocks': [body],
        'visual_decisions': [{'evidence_id': 'S001', 'decision': 'exclude', 'reason': 'Wrong exclusion'}],
        'evidence_coverage': [{'evidence_id': 'S001', 'disposition': 'used', 'reason': 'Explained'}]}}]}
    task = {'defects': [{'lecture_id': 'OPS-L01', 'evidence_id': 'S001'}]}
    patch = {'insertions': [{'lecture_id': 'OPS-L01', 'after_block_sha256': _hash(body),
        'block': {'kind': 'visual', 'visual_id': 'S001', 'text': 'Source relationships.', 'coverage_role': 'full', 'evidence_ids': ['S001']}}],
        'visual_updates': [{'evidence_id': 'S001', 'decision': 'include', 'reason': 'Inspected relational diagram', 'inspected': True}]}
    updated = apply_correction(original, patch, task)['lessons'][0]['notes']
    assert updated['blocks'][0] == body and updated['blocks'][1]['kind'] == 'visual'
    assert original['lessons'][0]['notes']['visual_decisions'][0]['decision'] == 'exclude'
    invalid = deepcopy(patch); invalid['visual_updates'][0]['inspected'] = False
    with pytest.raises(WorkflowError, match='inspected visual'):
        apply_correction(original, invalid, task)


def test_long_english_recaps_are_collected_for_one_bounded_correction():
    from mynotes.providers import ProviderResponse
    from mynotes.generation_diagnostics import recap_language_defects
    english = ('This is the lesson in which the methods and their results are discussed. '
               'These are the assumptions that the sources explain, while those results remain within their scope. ')*5
    response = ProviderResponse({'lessons': [{'lecture_id': l, 'notes': {'blocks': [{'kind': 'recap', 'text': english, 'evidence_ids': ['S001']}]}}
                                            for l in ['OPS-L01', 'OPS-L02']]}, 'codex', 'test', 'high')
    maps = {l: {'S001': 'S001'} for l in ['OPS-L01', 'OPS-L02']}
    assert len(recap_language_defects(response, {'style': {'recap_language': 'it'}}, list(maps), maps)) == 2
    assert not recap_language_defects(response, {'style': {'recap_language': 'en'}}, list(maps), maps)


def test_visual_can_follow_a_replaced_anchor_without_rewriting_other_blocks():
    from mynotes.batch import _hash
    from mynotes.batch_recovery import apply_correction
    body = {'kind': 'paragraph', 'text': 'Original', 'evidence_ids': ['S001'], 'coverage_role': 'full'}
    original = {'lessons': [{'lecture_id': 'OPS-L01', 'notes': {'blocks': [body], 'evidence_coverage': [],
        'visual_decisions': [{'evidence_id': 'S001', 'decision': 'exclude', 'reason': 'Incorrect exclusion'}]}}]}
    task = {'defects': [{'lecture_id': 'OPS-L01', 'evidence_id': 'S001'}],
        'editable_blocks': [{'lecture_id': 'OPS-L01', 'block_sha256': _hash(body)}], 'lesson_evidence': {'OPS-L01': ['S001']}}
    patch = {'replacements': [{'lecture_id': 'OPS-L01', 'block_sha256': _hash(body), 'block': {**body, 'text': 'Corrected'}}],
        'insertions': [{'lecture_id': 'OPS-L01', 'after_block_sha256': _hash(body), 'block': {
            'kind': 'visual', 'text': 'Inspected diagram', 'visual_id': 'S001', 'evidence_ids': ['S001'], 'coverage_role': 'full'}}],
        'visual_updates': [{'evidence_id': 'S001', 'decision': 'include', 'reason': 'Actual diagram', 'inspected': True}]}
    blocks = apply_correction(original, patch, task)['lessons'][0]['notes']['blocks']
    assert blocks[0]['text'] == 'Corrected' and blocks[1]['kind'] == 'visual'
    assert original['lessons'][0]['notes']['blocks'][0]['text'] == 'Original'
