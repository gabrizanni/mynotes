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


def test_renderer_change_rebuilds_only_word_and_preserves_notes(tmp_path, monkeypatch):
    ws,batch_id=finished_batch(tmp_path,monkeypatch)
    first=nr.run_notes(ws,'OPS',resume=batch_id,provider_adapter=NoProvider())
    originals={p:p.read_bytes() for p in ws.runs_dir.glob('OPS-L*/lecture_notes.candidate.json')}
    current=pub.word_renderer_hash()+'-synthetic-update'
    monkeypatch.setattr(pub,'word_renderer_hash',lambda:current)
    monkeypatch.setattr(bd,'word_renderer_hash',lambda:current)
    monkeypatch.setattr('mynotes.batch.generate_batch',lambda *a,**k:pytest.fail('Renderer recovery must not regenerate notes'))
    result=nr.run_notes(ws,'OPS',resume=batch_id,provider_adapter=NoProvider())
    assert result['status']=='COMPLETED' and result['delivery']['renderer_hash']==current
    assert first['delivery']['files']!=result['delivery']['files']
    assert all(p.read_bytes()==data for p,data in originals.items())
    assert nr.run_notes(ws,'OPS',resume=batch_id,provider_adapter=NoProvider())['reused']


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

    assert 'problem' not in result
    assert result['resolved_problems'][0]['message'] == 'synthetic commit defect'


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


@pytest.mark.parametrize('changed', [False, True])
def test_cached_transcript_requires_current_audio_identity(tmp_path, monkeypatch, changed):
    from mynotes.cli import _notes_preparation
    from mynotes.utils import file_sha256
    ws, _, _ = workspace_and_deck(tmp_path, lessons=1, slides=2)
    pack_path = ws.runs_dir / 'OPS-L01/source_pack.json'
    pack = json.loads(pack_path.read_text())
    material = tmp_path / 'material.txt'
    material.write_text('Inventory buffers replenishment delays.')
    pack['sources'] = [{'role': 'lecture_material', 'path': str(material), 'sha256': file_sha256(material)}]
    audio = tmp_path / 'OPS_L01.wav'
    audio.write_bytes(b'original synthetic recording')
    digest = file_sha256(audio)
    pack['sources'].append({'role': 'recording', 'path': str(audio), 'sha256': digest})
    atomic_json_write(pack_path, pack)
    cached = ws.runs_dir / 'OPS-L01/asr/transcript.json'
    atomic_json_write(cached, {'status': 'COMPLETED', 'lecture_id': 'OPS-L01', 'audio_path': str(audio),
        'audio_sha256': digest, 'segments': [{'id': 'T001', 'text': 'Inventory buffers replenishment delays.',
                                            'start_seconds': 0, 'end_seconds': 3, 'speaker': 'UNKNOWN'}]})
    # Force preparation while retaining the same selected sources.
    pack['sources'][0]['sha256'] = 'stale material hash'
    atomic_json_write(pack_path, pack)
    before = pack_path.read_bytes()
    if changed: audio.write_bytes(b'a different recording')
    monkeypatch.setattr('mynotes.sourcepack.transcribe_audio', lambda *a, **k: pytest.fail('Notes cannot start ASR'))
    pending = _notes_preparation(ws, ['OPS-L01'])
    if changed:
        assert pending[0]['reason'] == 'UNBOUND_CACHED_TRANSCRIPT' and not pending[0]['asr_started']
        assert pack_path.read_bytes() == before
    else:
        assert pending == []
        rebuilt = json.loads(pack_path.read_text())
        assert any(s['role'] == 'recording' and s['sha256'] == digest for s in rebuilt['sources'])


def test_import_preserves_declared_partial_coverage_boundary(tmp_path):
    from mynotes.sourcepack import build_source_pack
    ws, _, _ = workspace_and_deck(tmp_path, lessons=1, slides=2)
    transcript = tmp_path / 'partial.json'
    atomic_json_write(transcript, {'lecture_id': 'OPS-L01', 'partial': True, 'coverage_complete': False,
        'coverage_warning': 'Opening thirty minutes missing', 'segments': [{'id': 'T001',
        'text': 'Inventory buffers replenishment delays.', 'start_seconds': 0, 'end_seconds': 3}]})
    build_source_pack(ws, 'OPS-L01', transcript_path=transcript)
    usable, warnings = nr._transcript_boundary(ws, ['OPS-L01'])
    assert usable == [] and warnings[0]['code'] == 'PARTIAL_TRANSCRIPT'


def test_edited_capture_txt_retains_known_partial_recording_coverage(tmp_path, monkeypatch):
    from mynotes.sourcepack import build_source_pack
    ws, _, _ = workspace_and_deck(tmp_path, lessons=1, slides=2)
    transcript = tmp_path / 'renamed.txt'
    transcript.write_text('Edited user transcript of a recovered partial recording.')
    monkeypatch.setattr('mynotes.recording_library.options', lambda _: {'folder': str(tmp_path)})
    monkeypatch.setattr('mynotes.transcript_library.scan', lambda *a, **k: [
        {'path': str(transcript), 'recording_id': 'partial-recording'}])
    monkeypatch.setattr('mynotes.recording_library.records', lambda _: {
        'partial-recording': {'id': 'partial-recording', 'partial': True, 'capture_reason': 'Recovered after interruption'}})
    build_source_pack(ws, 'OPS-L01', transcript_path=transcript)
    assert nr._transcript_boundary(ws, ['OPS-L01'])[0] == []


def test_autonomous_recovery_accepts_bound_effective_context_without_writes(tmp_path):
    from test_autonomous_organization import prepared, IntegratedWriter
    from test_batch_generation import VERIFIED
    from mynotes.local_recovery import recover_batch
    ws = prepared(tmp_path)
    first = generate_batch(ws, 'OPS', [1, 2], autonomous_organization=True,
                           provider_adapter=IntegratedWriter(), pause_after='OPS-L01', budgets=VERIFIED)
    before = {str(p): p.read_bytes() for p in ws.root.rglob('*') if p.is_file()}
    result = recover_batch(ws, first['batch_id'])
    # The joint request has settled both lessons; no unfinished second group.
    assert result['lessons'] == [] and not result['provider_invoked']
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


def test_completed_batch_accepts_one_source_bound_review_then_reuses(tmp_path, monkeypatch):
    import subprocess
    from mynotes.batch import _hash
    from mynotes.workflow import _load_generation_phase
    from mynotes.providers import ProviderResponse, _save_attempt, AttemptStart
    from test_autonomous_organization import prepared, IntegratedWriter
    ws = prepared(tmp_path)
    monkeypatch.setattr(pub, '_render_and_validate', fake_render)
    monkeypatch.setattr(bd, '_render_and_validate', fake_render)
    class Recorded(IntegratedWriter):
        def generate(self, request):
            result = super().generate(request)
            attempt = _save_attempt(request, command=['synthetic'], started=AttemptStart(),
                completed=subprocess.CompletedProcess(['synthetic'],0,json.dumps(result.notes),''))
            return ProviderResponse(result.notes,'codex','test-model','high',attempt_path=str(attempt))
    built = generate_batch(ws,'OPS',[1,2],autonomous_organization=True,provider_adapter=Recorded())
    batch_id = built['batch_id']
    nr.run_notes(ws, 'OPS', resume=batch_id, provider_adapter=NoProvider())
    root = ws.runs_dir/'_batches'/batch_id
    raw_path = next(root.glob('generation-checkpoints/*/returned-writing-*.json'))
    original = raw_path.read_bytes()
    response = _load_generation_phase(raw_path, previous=None)
    blocks = response.notes['lessons'][0]['notes']['blocks']
    index = next(i for i,b in enumerate(blocks) if b['kind']=='paragraph')
    review = {'base_notes_sha256': _hash(response.notes), 'defects': [
        {'lecture_id': 'OPS-L01', 'path': f'blocks[{index}]', 'evidence_id': 'S001', 'reason': 'Observed unsupported specificity'}]}
    class Correction:
        calls = 0
        def generate(self, request):
            self.calls += 1
            assert request.phase == 'coverage_repair'
            task = request.batch_context['repair_task']; anchor = task['editable_blocks'][0]
            block = {**blocks[index], 'text': 'A corrected, source-supported explanation.'}
            notes = {'insertions': [], 'attachments': [], 'visual_updates': [], 'replacements': [{**anchor, 'block': block}]}
            attempt = _save_attempt(request, command=['synthetic'], started=AttemptStart(),
                completed=subprocess.CompletedProcess(['synthetic'],0,json.dumps(notes),''))
            return ProviderResponse(notes,'codex','test-model','high',attempt_path=str(attempt))
    adapter = Correction()
    result = nr.run_notes(ws,'OPS',resume=batch_id,review_defects=review,provider_adapter=adapter)
    assert result['status'] in {'COMPLETED','COMPLETED_WITH_WARNINGS'}, result.get('problem')
    assert adapter.calls == 1 and raw_path.read_bytes() == original
    again = nr.run_notes(ws,'OPS',resume=batch_id,provider_adapter=NoProvider())
    assert again['reused'] and not again['provider_invoked']
    same = nr.run_notes(ws,'OPS',resume=batch_id,review_defects=review,provider_adapter=NoProvider())
    assert same['status'] in {'COMPLETED','COMPLETED_WITH_WARNINGS'}, same.get('problem')
    changed = nr.run_notes(ws,'OPS',resume=batch_id,review_defects={**review,'defects': [{**review['defects'][0], 'reason': 'Another review'}]},provider_adapter=NoProvider())
    assert changed['status'] == 'FAILED' and 'already consumed' in changed['problem']['message']
