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
    ws, _, selection = workspace_and_deck(tmp_path, lessons=2, slides=22)
    monkeypatch.setattr(pub, '_render_and_validate', fake_render)
    monkeypatch.setattr(bd, '_render_and_validate', fake_render)
    built = generate_batch(ws, 'OPS', [1, 2], selection=selection, provider_adapter=Adapter())
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
    # The changed administrative publication preference has no authority to
    # rewrite writing. Reuse the prevalidated content in the same request.
    monkeypatch.setattr('mynotes.batch.plan_batch', lambda *a, **k: {
        **__import__('mynotes.batch', fromlist=['load_batch_manifest']).load_batch_manifest(ws, batch_id), 'status': 'PLANNED'})
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
