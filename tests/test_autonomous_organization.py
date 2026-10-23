"""One ordinary writing call places shared slides and proceeds to Word."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from mynotes.batch import generate_batch, plan_batch
from mynotes.providers import ProviderResponse
from mynotes.utils import atomic_json_write, file_sha256
from mynotes.workflow import WorkflowError
from test_batch_generation import workspace_and_deck, notes_for, VERIFIED


def prepared(tmp_path):
    ws, deck, _ = workspace_and_deck(tmp_path, slides=4)
    for number in (1, 2):
        path = ws.runs_dir / f'OPS-L{number:02d}' / 'source_pack.json'
        pack = json.loads(path.read_text())
        if number == 2:
            pack['evidence'] = []; pack['sources'] = []
        pack['evidence'].append({'id': 'T001', 'kind': 'transcript', 'text': f'Complete transcript for distinct lecture {number} topics.', 'start_seconds': 0, 'end_seconds': 100})
        pack['availability']['transcript'] = True
        atomic_json_write(path, pack)
    return ws


class IntegratedWriter:
    def __init__(self, transform=None): self.calls = []; self.transform = transform

    def generate(self, request):
        self.calls.append(request)
        assert request.phase == 'writing'
        lecture = request.lecture_id
        decisions = []
        filtered = deepcopy(request)
        filtered.batch_context['ownership'] = []
        for choice in request.batch_context['ownership']:
            unsettled = choice.get('provisional') or choice['status'] in {'pending', 'deferred'}
            if not unsettled:
                filtered.batch_context['ownership'].append(choice)
                continue
            current = lecture.endswith('02') or choice['evidence_id'] in {'S001', 'S002'}
            status = 'taught' if current else 'pending'
            decisions.append({'canonical_slide_id': choice['canonical_slide_id'], 'status': status,
                              'reason': 'This full transcript establishes these concepts.' if current else 'Review next complete lesson before placement.',
                              'evidence_ids': ['T001'] if current else []})
            filtered.batch_context['ownership'].append({**choice, 'owner': lecture if current else 'OPS-L99', 'status': status})
        result = {'organization': decisions, 'lessons': [{'lecture_id': lecture, 'notes': notes_for(filtered, lecture)}]}
        if self.transform: self.transform(result, request)
        return ProviderResponse(result, 'codex', 'test-model', 'high')


def test_autonomous_plan_is_read_only_and_keeps_ordered_single_lesson_groups(tmp_path):
    ws = prepared(tmp_path)
    before = {p: p.read_bytes() for p in ws.root.rglob('*') if p.is_file()}
    plan = plan_batch(ws, 'OPS', [1, 2], autonomous_organization=True, budgets=VERIFIED)
    assert plan['status'] == 'PLANNED'
    assert plan['groups'] == [['OPS-L01'], ['OPS-L02']]
    assert {p: p.read_bytes() for p in ws.root.rglob('*') if p.is_file()} == before


def test_integrated_placement_reaches_both_lessons_and_resumes_without_model_calls(tmp_path, monkeypatch):
    ws = prepared(tmp_path); writer = IntegratedWriter()
    originals = {p: p.read_bytes() for p in ws.runs_dir.glob('OPS-L*/source_pack.json')}
    built = []
    def build(workspace, lecture, **kwargs):
        assert kwargs['mode'] == 'standalone'
        from mynotes.publication import _load_candidate
        _load_candidate(workspace, lecture)
        # L01 is built before writing L02; pending pool never blocks settled L01.
        assert len(writer.calls) == len(built) + 1
        path = workspace.runs_dir / lecture / 'synthetic.docx'; path.write_bytes(b'synthetic Word')
        built.append(lecture)
        return {'candidate': str(path)}
    monkeypatch.setattr('mynotes.publication.build_word_candidate', build)
    result = generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True, provider_adapter=writer, build_word=True)
    assert result['status'] == 'CANDIDATE_READY'
    assert result['pending_course_slides'] == []
    assert built == ['OPS-L01', 'OPS-L02']
    assert len(writer.calls) == 2
    assert len(result['organization_receipts']) == 2
    paragraphs = []
    for row in result['candidates']:
        candidate = json.loads(Path(row['candidate']).read_text())
        assert len(candidate['slide_ownership']) == 2
        paragraphs.append([b['evidence_ids'] for b in candidate['notes']['blocks'] if b.get('coverage_role') == 'full'])
    assert paragraphs == [[['S001'], ['S002']], [['S003'], ['S004']]]
    assert {p: p.read_bytes() for p in originals} == originals
    again = generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True, provider_adapter=writer, build_word=True, batch_id=result['batch_id'])
    assert again['status'] == 'CANDIDATE_READY' and len(writer.calls) == 2 and len(built) == 2


def test_final_selected_lesson_cannot_silently_leave_unexplained_exam_slides(tmp_path):
    ws = prepared(tmp_path)
    def leave(result, request):
        if request.lecture_id.endswith('02'):
            result['organization'][-1].update(status='pending', evidence_ids=[])
    writer = IntegratedWriter(leave)
    with pytest.raises(WorkflowError, match='last selected lesson'):
        generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True, provider_adapter=writer)
    assert (ws.runs_dir / 'OPS-L01/lecture_notes.candidate.json').is_file()
    assert not (ws.runs_dir / 'OPS-L02/lecture_notes.candidate.json').exists()


def test_taught_decision_requires_actual_current_transcript_evidence(tmp_path):
    ws = prepared(tmp_path)
    def wrong(result, request): result['organization'][0]['evidence_ids'] = ['T999']
    with pytest.raises(WorkflowError, match='T999.*UNKNOWN_REFERENCE'):
        generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True, provider_adapter=IntegratedWriter(wrong))


def test_organization_receipt_tampering_blocks_word(tmp_path):
    ws = prepared(tmp_path)
    result = generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True, provider_adapter=IntegratedWriter())
    candidate = json.loads(Path(result['candidates'][0]['candidate']).read_text())
    receipt = Path(candidate['organization_receipt']['path'])
    receipt.write_text('{}')
    from mynotes.publication import _load_candidate, PublicationError
    with pytest.raises(PublicationError, match='organization provenance'):
        _load_candidate(ws, 'OPS-L01')


def test_incremental_assembly_retains_integrated_organization_and_rejects_conflicts():
    from mynotes.agy_parts import assemble, part_schema
    from mynotes.batch import batch_notes_schema
    from mynotes.providers import ProviderRequest, RequestedModel, ProviderError
    request = ProviderRequest('OPS-L01', {}, {}, RequestedModel('agy', 'model', 'high', 'standard'),
        source_pack={'evidence': [{'id': 'S001'}]}, batch_context={'lessons': ['OPS-L01']},
        response_schema=batch_notes_schema(autonomous_organization=True))
    choice = {'canonical_slide_id': 'sha256:abc:page:1', 'status': 'supplement', 'reason': 'Exam material', 'evidence_ids': []}
    def part(index, complete, text, decision):
        return {'part_index': index, 'complete': complete, 'next_topic': '' if complete else 'next concept',
                'result': {'organization': [decision], 'lessons': [{'lecture_id': 'OPS-L01', 'notes': {
                    'blocks': [{'kind': 'paragraph', 'text': text, 'evidence_ids': ['S001']}],
                    'evidence_coverage': [], 'visual_decisions': []}}]}}
    parts = [part(0, False, 'First explanation', choice), part(1, True, 'Second explanation', choice)]
    assert 'organization' in part_schema(request)['properties']['result']['required']
    assert assemble(request, parts)['organization'] == [choice]
    parts[-1]['result']['organization'][0] = {**choice, 'status': 'pending'}
    with pytest.raises(ProviderError, match='conflicting slide organization'):
        assemble(request, parts)


def test_publication_markers_do_not_force_paid_rewrite_on_resume(tmp_path):
    ws = prepared(tmp_path); writer = IntegratedWriter()
    first = generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True, provider_adapter=writer)
    for row in first['candidates']:
        path = Path(row['candidate']); candidate = json.loads(path.read_text())
        candidate.update(published_to_word=True, published_path='saved-handout.docx', published_hash='publishedhash')
        atomic_json_write(path, candidate)
    second = generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True, provider_adapter=writer, batch_id=first['batch_id'])
    assert second['status'] == 'CANDIDATE_READY' and len(writer.calls) == 2
    assert all(json.loads(Path(row['candidate']).read_text())['published_to_word'] for row in second['candidates'])


def test_module_change_reuses_source_bound_autonomous_writing(tmp_path, monkeypatch):
    import subprocess
    from mynotes.providers import _save_attempt, AttemptStart
    from mynotes import batch
    ws = prepared(tmp_path)
    class Receipted(IntegratedWriter):
        def generate(self, request):
            response = super().generate(request)
            output = subprocess.CompletedProcess(['synthetic'], 0, json.dumps(response.notes), '')
            path = _save_attempt(request, command=['synthetic'], completed=output, started=AttemptStart())
            return ProviderResponse(response.notes, response.provider, response.model, response.effort, attempt_path=str(path))
    writer = Receipted()
    first = generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True, provider_adapter=writer)
    original = batch.file_sha256
    monkeypatch.setattr(batch, 'file_sha256', lambda p: 'newmodulecontract' if Path(p).name == 'providers.py' else original(p))
    second = generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True, provider_adapter=writer, batch_id=first['batch_id'])
    assert second['status'] == 'CANDIDATE_READY' and len(writer.calls) == 2
    for row in second['candidates']:
        candidate = json.loads(Path(row['candidate']).read_text())
        assert candidate['source_revalidation']['kind'] == 'organization_revalidation'
        from mynotes.publication import _load_candidate
        _load_candidate(ws, row['lecture_id'])


def test_saved_successful_correction_survives_resume_and_module_update(tmp_path, monkeypatch):
    import subprocess
    from mynotes.providers import _save_attempt, AttemptStart
    from mynotes import batch
    ws = prepared(tmp_path)
    class Corrected(IntegratedWriter):
        def generate(self, request):
            if request.phase == 'coverage_repair':
                self.calls.append(request)
                anchor = next(row for row in request.batch_context['repair_task']['anchors'] if row['block']['kind'] == 'paragraph')
                notes = {'insertions': [{'lecture_id': request.lecture_id, 'after_block_sha256': anchor['block_sha256'],
                    'block': {'kind': 'paragraph', 'text': 'A second source mechanism explains its distinct causal relation in the current lesson.',
                              'evidence_ids': ['S002'], 'coverage_role': 'full'}}], 'attachments': [], 'visual_updates': []}
                response = ProviderResponse(notes, 'codex', 'test-model', 'high')
            else:
                response = super().generate(request)
                if request.lecture_id.endswith('01'):
                    blocks = response.notes['lessons'][0]['notes']['blocks']
                    response.notes['lessons'][0]['notes']['blocks'] = [row for row in blocks if not (row.get('coverage_role') == 'full' and row['evidence_ids'] == ['S002'])]
            output = subprocess.CompletedProcess(['synthetic'], 0, json.dumps(response.notes), '')
            path = _save_attempt(request, command=['synthetic'], completed=output, started=AttemptStart())
            return ProviderResponse(response.notes, response.provider, response.model, response.effort, attempt_path=str(path))
    writer = Corrected()
    first = generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True, provider_adapter=writer, repair_missing=True)
    assert first['status'] == 'CANDIDATE_READY' and len(writer.calls) == 3
    repeat = generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True, provider_adapter=writer, repair_missing=True, batch_id=first['batch_id'])
    assert repeat['status'] == 'CANDIDATE_READY' and len(writer.calls) == 3
    original = batch.file_sha256
    monkeypatch.setattr(batch, 'file_sha256', lambda p: 'newmodulecontract' if Path(p).name == 'providers.py' else original(p))
    after = generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True, provider_adapter=writer, repair_missing=True, batch_id=first['batch_id'])
    assert after['status'] == 'CANDIDATE_READY' and len(writer.calls) == 3
    from mynotes.publication import _load_candidate
    _load_candidate(ws, 'OPS-L01')


def test_settled_legacy_batch_can_build_standalone_without_changing_generation_contract(tmp_path, monkeypatch):
    from test_batch_generation import Adapter
    ws, _, selection = workspace_and_deck(tmp_path, lessons=1, slides=2)
    writer = Adapter(); modes = []
    def build(workspace, lecture, **kwargs):
        modes.append(kwargs.get('mode', 'update'))
        assert kwargs['timeout_seconds'] == 300
        path = workspace.runs_dir / lecture / f'{len(modes)}.docx'; path.write_bytes(b'synthetic Word')
        return {'candidate': str(path)}
    monkeypatch.setattr('mynotes.publication.build_word_candidate', build)
    first = generate_batch(ws, 'OPS', [1], selection=selection, provider_adapter=writer, build_word=True)
    second = generate_batch(ws, 'OPS', [1], selection=selection, provider_adapter=writer, build_word=True, word_mode='standalone', batch_id=first['batch_id'])
    assert first['status'] == second['status'] == 'CANDIDATE_READY'
    assert len(writer.calls) == 1 and modes == ['update', 'standalone']
