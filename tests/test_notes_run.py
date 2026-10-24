"""Complete local delivery, fault recovery and honest external boundaries.

Writing and rendering fixtures are synthetic. The shared Word builder and
publication/merge code are real; these tests certify no actual provider or GUI.
"""
import json
from pathlib import Path

from docx import Document
import pytest

from mynotes import notes_run as nr, publication as pub, batch_documents as bd
from mynotes.batch import generate_batch
from mynotes.providers import ProviderQuotaError
from mynotes.utils import atomic_json_write
from test_batch_generation import workspace_and_deck, Adapter
from test_publication import fake_render


def finished_batch(tmp_path, monkeypatch):
    import subprocess
    from mynotes.providers import ProviderResponse, _save_attempt, AttemptStart
    ws, _, selection = workspace_and_deck(tmp_path, lessons=2, slides=22)
    monkeypatch.setattr(pub, '_render_and_validate', fake_render)
    monkeypatch.setattr(bd, '_render_and_validate', fake_render)
    class Recorded(Adapter):
        def generate(self, request):
            result = super().generate(request)
            attempt = _save_attempt(request, command=['synthetic'], started=AttemptStart(),
                completed=subprocess.CompletedProcess(['synthetic'], 0, json.dumps(result.notes), ''))
            return ProviderResponse(result.notes, result.provider, result.model, result.effort, attempt_path=str(attempt))
    built = generate_batch(ws, 'OPS', [1, 2], selection=selection, provider_adapter=Recorded())
    return ws, built['batch_id']


class NoProvider:
    def generate(self, request):
        pytest.fail('Saved successful work must not invoke a provider')


def test_resume_delivers_both_words_and_publishes_once_then_reuses(tmp_path, monkeypatch):
    ws, batch_id = finished_batch(tmp_path, monkeypatch)
    events = []
    result = nr.run_notes(ws, 'OPS', resume=batch_id, provider_adapter=NoProvider(), progress=lambda stage, _: events.append(stage))
    assert result['status'] == 'COMPLETED'
    assert len(result['delivery']['files']) == 3
    assert result['publication']['status'] == 'PUBLISHED'
    document = Document(result['publication']['output'])
    text = '\n'.join(p.text for p in document.paragraphs)
    assert text.count('MYNOTES START OPS-L01') == text.count('MYNOTES START OPS-L02') == 1
    assert events[-2:] == ['UPDATING_WORD', 'COMPLETED']
    again = nr.run_notes(ws, 'OPS', resume=batch_id, provider_adapter=NoProvider())
    assert again['status'] == 'COMPLETED' and again['reused'] and not again['provider_invoked']


def test_word_transient_failure_is_local_retry_without_model(tmp_path, monkeypatch):
    ws, batch_id = finished_batch(tmp_path, monkeypatch)
    real = bd.build_batch_documents
    calls = []
    def once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError('temporary local filesystem failure')
        return real(*args, **kwargs)
    monkeypatch.setattr(bd, 'build_batch_documents', once)
    result = nr.run_notes(ws, 'OPS', resume=batch_id, provider_adapter=NoProvider())
    assert result['status'] == 'COMPLETED' and len(calls) == 2


def test_failure_after_words_resumes_publication_without_generation(tmp_path, monkeypatch):
    from mynotes import course_update
    ws, batch_id = finished_batch(tmp_path, monkeypatch)
    real = course_update.update_course
    monkeypatch.setattr(course_update, 'update_course', lambda *a, **k: (_ for _ in ()).throw(pub.PublicationError('synthetic commit defect')))
    stopped = nr.run_notes(ws, 'OPS', resume=batch_id, provider_adapter=NoProvider())
    assert stopped['status'] == 'STOPPED' and stopped['generation_complete']
    assert len(stopped['delivery']['files']) == 3
    monkeypatch.setattr(course_update, 'update_course', real)
    monkeypatch.setattr('mynotes.batch.generate_batch', lambda *a, **k: pytest.fail('Publication recovery must reuse verified content'))
    result = nr.run_notes(ws, 'OPS', resume=batch_id, provider_adapter=NoProvider())
    assert result['status'] == 'COMPLETED'


def test_review_mode_preserves_official_word_and_returns_candidate(tmp_path, monkeypatch):
    ws, batch_id = finished_batch(tmp_path, monkeypatch)
    config = ws.configuration_store().load()
    config['courses'][0]['publication']['mode'] = 'review'
    ws.configuration_store().save(config)
    result = nr.run_notes(ws, 'OPS', resume=batch_id, provider_adapter=NoProvider())
    assert result['status'] == 'AWAITING_APPROVAL'
    assert Path(result['publication']['candidate']).is_file()
    assert not Path(result['publication']['target']).exists()


def test_partial_transcript_stops_before_gap_without_claiming_audio_completeness(tmp_path):
    ws, _, _ = workspace_and_deck(tmp_path, lessons=2, slides=2)
    for number in (1, 2):
        path = ws.runs_dir / f'OPS-L{number:02d}' / 'source_pack.json'
        pack = json.loads(path.read_text())
        pack['evidence'].append({'id': 'T001', 'kind': 'transcript', 'text': 'Synthetic speech source.'})
        if number == 2:
            pack['coverage_complete'] = False
        atomic_json_write(path, pack)
    usable, warnings = nr._transcript_boundary(ws, ['OPS-L01', 'OPS-L02'])
    assert usable == ['OPS-L01'] and warnings[0]['code'] == 'PARTIAL_TRANSCRIPT'


def test_plan_only_does_not_write_or_invoke_provider(tmp_path):
    ws, _, _ = workspace_and_deck(tmp_path, lessons=1, slides=2)
    before = {str(p): p.read_bytes() for p in ws.root.rglob('*') if p.is_file()}
    result = nr.run_notes(ws, 'OPS', [1], plan_only=True, provider_adapter=NoProvider())
    assert not result['provider_invoked'] and not result['published_to_word']
    assert {str(p): p.read_bytes() for p in ws.root.rglob('*') if p.is_file()} == before


def test_request_lock_prevents_duplicate_work(tmp_path):
    with nr._request_lock(tmp_path):
        with pytest.raises(nr.WorkflowError, match='already running'):
            with nr._request_lock(tmp_path):
                pytest.fail('Concurrent writer admitted')


def test_unknown_validation_failure_is_not_retried(tmp_path):
    calls = []
    def invalid():
        calls.append(1)
        raise pub.PublicationError('Unknown source evidence')
    with pytest.raises(pub.PublicationError):
        nr._local_word(invalid, lambda *_: None)
    assert len(calls) == 1


def test_fresh_existing_txt_is_prepared_before_contiguous_boundary(tmp_path, monkeypatch):
    from mynotes.cli import _notes_preparation
    ws, _, _ = workspace_and_deck(tmp_path, lessons=1, slides=2)
    (ws.runs_dir / 'OPS-L01/source_pack.json').unlink()
    transcript = tmp_path / 'OPS_L01.txt'
    transcript.write_text('Inventory is a buffer between demand and replenishment. Lead time affects the amount of stock required.')
    monkeypatch.setattr('mynotes.recording_library.options', lambda _: {'folder': str(tmp_path)})
    monkeypatch.setattr('mynotes.transcript_library.scan', lambda *a, **k: [
        {'path': str(transcript), 'lecture_id': None, 'course_code': 'OPS', 'status': 'COMPLETED'}])
    monkeypatch.setattr('mynotes.sourcepack.transcribe_audio', lambda *a, **k: pytest.fail('Existing TXT cannot start ASR'))
    assert _notes_preparation(ws, ['OPS-L01'], contiguous=True) == []
    assert nr._transcript_boundary(ws, ['OPS-L01'])[0] == ['OPS-L01']


def test_missing_transcript_does_not_start_asr(tmp_path, monkeypatch):
    from mynotes.cli import _notes_preparation
    ws, _, _ = workspace_and_deck(tmp_path, lessons=1, slides=2)
    (ws.runs_dir / 'OPS-L01/source_pack.json').unlink()
    monkeypatch.setattr('mynotes.sourcepack.transcribe_audio', lambda *a, **k: pytest.fail('Notes cannot start ASR'))
    pending = _notes_preparation(ws, ['OPS-L01'], contiguous=True)
    assert pending[0]['reason'] == 'MISSING_TRANSCRIPT' and not pending[0]['asr_started']


def test_autonomous_recovery_accepts_bound_effective_context_without_writes(tmp_path):
    from test_autonomous_organization import prepared, IntegratedWriter
    from mynotes.local_recovery import recover_batch
    ws = prepared(tmp_path)
    first = generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True,
                           provider_adapter=IntegratedWriter(), pause_after='OPS-L01')
    before = {str(p): p.read_bytes() for p in ws.root.rglob('*') if p.is_file()}
    result = recover_batch(ws, first['batch_id'])
    assert result['lessons'][0]['status'] == 'NOT_STARTED' and not result['provider_invoked']
    assert before == {str(p): p.read_bytes() for p in ws.root.rglob('*') if p.is_file()}


def test_legacy_successful_repair_migrates_and_continues_without_rewriting(tmp_path, monkeypatch):
    import subprocess
    from mynotes.providers import ProviderResponse, _save_attempt, AttemptStart
    from mynotes.batch import _hash
    from test_progressive_slides import initial_deck, ProgressiveWriter
    from test_autonomous_organization import IntegratedWriter
    ws, selection = initial_deck(tmp_path)
    monkeypatch.setattr(pub, '_render_and_validate', fake_render)
    monkeypatch.setattr(bd, '_render_and_validate', fake_render)
    calls = []
    class Legacy:
        def generate(self, request):
            calls.append((request.lecture_id, request.phase))
            if request.phase == 'writing':
                response = ProgressiveWriter().generate(request)
                notes = response.notes['lessons'][0]['notes']
                notes['blocks'] = [b for b in notes['blocks'] if b.get('evidence_ids') != ['S002']]
            else:
                anchor = next(row for row in request.batch_context['repair_task']['anchors'] if row['block']['kind'] == 'paragraph')
                response = ProviderResponse({'insertions': [{'lecture_id': 'OPS-L01', 'after_block_sha256': anchor['block_sha256'],
                    'block': {'kind': 'paragraph', 'text': 'The second slide develops a separate source-supported mechanism.',
                              'evidence_ids': ['S002'], 'coverage_role': 'full'}}], 'attachments': [], 'visual_updates': []}, 'codex', 'test-model', 'high')
            attempt = _save_attempt(request, command=['synthetic'], started=AttemptStart(),
                completed=subprocess.CompletedProcess(['synthetic'], 0, json.dumps(response.notes), ''))
            return ProviderResponse(response.notes, 'codex', 'test-model', 'high', attempt_path=str(attempt))
    initial = generate_batch(ws, 'OPS', [1, 2], selection=selection, provider_adapter=Legacy(),
                             repair_missing=True, pause_after='OPS-L01')
    original_patch = next((ws.runs_dir / '_batches' / initial['batch_id']).glob('generation-checkpoints/*/coverage-repair/repair.json'))
    raw = original_patch.read_bytes()
    class Next:
        def generate(self, request):
            calls.append((request.lecture_id, request.phase))
            assert request.lecture_id == 'OPS-L02' and request.phase == 'writing'
            return IntegratedWriter().generate(request)
    result = nr.run_notes(ws, 'OPS', resume=initial['batch_id'], provider_adapter=Next())
    assert result['status'] == 'COMPLETED', result.get('problem')
    assert calls == [('OPS-L01', 'writing'), ('OPS-L01', 'coverage_repair'), ('OPS-L02', 'writing')]
    assert original_patch.read_bytes() == raw
