"""Capture requests share recording identity across chat, queue and preparation."""
import json
import os
from pathlib import Path
import uuid

import pytest

from mynotes import capture, capture_associations, transcription_queue as queue
from mynotes.asr import ASRError, ASRBusyError
from mynotes.capture_courses import add_course
from mynotes.cli import main
from mynotes.sourcepack import build_source_pack
from mynotes.transcription import run_transcription, transcription_status
from mynotes.utils import atomic_json_write
from test_capture_queue import ws, audio, fake_registry, claim


def edit_sidecar(path, **changes):
    sidecar = Path(str(path) + '.capture.json')
    data = json.loads(sidecar.read_text())
    data.update(changes)
    atomic_json_write(sidecar, data)
    return data


def test_final_partial_is_available_without_conflating_coverage_and_transcription(ws, monkeypatch):
    path, identifier = audio(ws, partial=True)
    edit_sidecar(path, status='PARTIAL', reason='Stopped manually')
    item = queue.library(ws)[0]
    assert item['status'] == 'NEEDS_REVIEW'  # Older clients retain aggregate compatibility.
    assert item['transcription_status'] == 'NOT_STARTED' and item['can_transcribe']
    assert item['audio_available'] and not item['blocking_reasons']
    assert {w['code'] for w in item['warnings']} == {'MANUAL_STOP', 'COURSE_UNASSIGNED'}
    engine = fake_registry(monkeypatch)
    result = run_transcription(ws, recording_id=identifier, engines={'argmax': engine})
    assert result['status'] == 'COMPLETED' and engine.calls == 1
    item = queue.library(ws)[0]
    assert item['transcription_status'] == 'COMPLETED' and item['status'] == 'COMPLETED'
    assert Path(item['transcript_json']).is_file() and not item['can_transcribe']


@pytest.mark.parametrize('status', ['RECORDING', 'STARTED_LATE', 'EXPORTING', 'INTERRUPTED', 'FAILED'])
def test_nonfinal_audio_cannot_transcribe(ws, monkeypatch, status):
    path, identifier = audio(ws)
    edit_sidecar(path, status=status)
    engine = fake_registry(monkeypatch)
    item = queue.library(ws)[0]
    assert item['transcription_status'] == 'WAITING' and not item['can_transcribe']
    assert item['blocking_reasons'][0]['code'] == 'AUDIO_NOT_FINAL'
    with pytest.raises(ASRError, match='AUDIO_UNAVAILABLE'):
        run_transcription(ws, recording_id=identifier, engines={'argmax': engine})
    assert engine.calls == 0


def test_runtime_export_blocks_old_final_sidecar(ws, monkeypatch):
    path, identifier = audio(ws, partial=True)
    edit_sidecar(path, status='PARTIAL')
    store = capture.CaptureStore(ws)
    atomic_json_write(store.runtime_path, {'pid': os.getpid(), 'exporting': True,
        'sessions': [{'status': 'EXPORTING', 'event': {'recordingID': identifier}}]})
    assert not queue.library(ws)[0]['can_transcribe']


def test_missing_audio_is_visible_with_specific_reason(ws):
    path, identifier = audio(ws)
    path.unlink()
    item = queue.library(ws)[0]
    assert item['id'] == identifier and not item['audio_available'] and not item['can_transcribe']
    assert item['blocking_reasons'][0]['code'] == 'AUDIO_UNAVAILABLE'


def test_unknown_course_is_nonblocking_and_uses_neutral_profile(ws, monkeypatch):
    path, identifier = audio(ws)
    data = edit_sidecar(path)
    data['event'].update(courseCode='OLDCOURSE', lessonNumber=3)
    edit_sidecar(path, event=data['event'])
    item = queue.library(ws)[0]
    assert item['can_transcribe'] and item['transcription_status'] == 'NOT_STARTED'
    assert 'UNKNOWN_COURSE' in [w['code'] for w in item['warnings']]
    engine = fake_registry(monkeypatch)
    assert run_transcription(ws, recording_id=identifier, engines={'argmax': engine})['status'] == 'COMPLETED'
    assert engine.calls == 1


def test_closed_window_chat_assignment_and_prepare_reuse_one_original_asr(ws, monkeypatch):
    path, identifier = audio(ws, 'Operations 17', partial=True)
    edit_sidecar(path, status='PARTIAL')
    engine = fake_registry(monkeypatch)
    assert not queue.status(ws)['ui_open']
    first = run_transcription(ws, recording_id=identifier, engines={'argmax': engine})
    transcript = Path(first['transcript_json']); before = transcript.read_bytes()
    code = add_course(ws, 'Operations Planning')['course']['code']
    capture_associations.assign(capture.CaptureStore(ws), {'assignments': [
        {'recordingID': identifier, 'courseCode': code, 'lessonNumber': 17}]})
    assert run_transcription(ws, f'{code}-L17', engines={'argmax': engine})['cache_reused']
    assert run_transcription(ws, f'{code}-L17', audio=path, engines={'argmax': engine})['cache_reused']
    assert transcription_status(ws, recording_id=identifier)['status'] == 'COMPLETED'
    assert transcription_status(ws, f'{code}-L17')['status'] == 'COMPLETED'
    pack = build_source_pack(ws, f'{code}-L17', audio=path)
    assert pack['transcription']['capture_recording_id'] == identifier
    assert engine.calls == 1 and transcript.read_bytes() == before
    assert not (ws.runs_dir / f'{code}-L17/asr/transcript.json').exists()


def test_direct_claims_only_requested_queued_job_preserving_profile_without_ui(ws, monkeypatch):
    path, identifier = audio(ws)
    engine = fake_registry(monkeypatch)
    queue.enqueue(ws, recording_id=identifier)
    job = queue.status(ws)['jobs'][0]
    with queue.database(ws, readonly=True) as conn:
        profile = json.loads(conn.execute('SELECT payload FROM jobs WHERE id=?', (job['id'],)).fetchone()['payload'])['course_profile']
    code = add_course(ws, 'Different subject')['course']['code']
    capture_associations.assign(capture.CaptureStore(ws), {'assignments': [
        {'recordingID': identifier, 'courseCode': code}]})
    result = run_transcription(ws, recording_id=identifier, engines={'argmax': engine})
    assert result['status'] == 'COMPLETED' and engine.calls == 1
    saved = json.loads(Path(result['transcript_json']).read_text())
    assert saved['language'] == profile['transcription']['language'] == 'auto'
    assert not queue.status(ws)['ui_open']
    assert queue.status(ws)['jobs'][0]['status'] == 'COMPLETED'


def test_selected_enqueue_does_not_enqueue_other_recordings_and_missing_id_is_readonly(ws, monkeypatch):
    fake_registry(monkeypatch)
    first, identifier = audio(ws)
    second, other = audio(ws)
    second.write_bytes(second.read_bytes() + b'distinct')
    before = {str(p) for p in ws.root.rglob('*')}
    with pytest.raises(ValueError, match='non trovata'):
        queue.enqueue(ws, recording_id=uuid.uuid4().hex)
    assert {str(p) for p in ws.root.rglob('*')} == before
    assert queue.enqueue(ws, recording_id=identifier)['added'] == 1
    assert [j['recording_id'] for j in queue.status(ws)['jobs']] == [identifier]
    assert queue.enqueue(ws, recording_id=identifier)['added'] == 0


def test_duplicate_course_lesson_uses_selected_capture_id_without_overwriting(ws, monkeypatch):
    code = add_course(ws, 'Example')['course']['code']
    engine = fake_registry(monkeypatch)
    ids = []
    for index in range(2):
        path, identifier = audio(ws)
        if index:
            path.write_bytes(path.read_bytes() + b'distinct')
        data = edit_sidecar(path)
        data['event'].update(courseCode=code, lessonNumber=1)
        edit_sidecar(path, event=data['event'])
        ids.append(identifier)
    assert all(r['can_transcribe'] for r in queue.library(ws))
    with pytest.raises(ASRError, match='AMBIGUOUS_RECORDING'):
        run_transcription(ws, f'{code}-L01', engines={'argmax': engine})
    first = run_transcription(ws, recording_id=ids[0], engines={'argmax': engine})
    second = run_transcription(ws, recording_id=ids[1], engines={'argmax': engine})
    assert first['transcript_json'] != second['transcript_json'] and engine.calls == 2


@pytest.mark.parametrize('corruption', ['segments', 'audio', 'normalized'])
def test_existing_result_corruption_never_silently_runs_asr_again(ws, monkeypatch, corruption):
    path, identifier = audio(ws)
    engine = fake_registry(monkeypatch)
    result = run_transcription(ws, recording_id=identifier, engines={'argmax': engine})
    transcript = Path(result['transcript_json']); saved = json.loads(transcript.read_text())
    if corruption == 'segments':
        saved['segments'][0]['text'] = 'Modified'
        atomic_json_write(transcript, saved)
    elif corruption == 'audio':
        path.write_bytes(path.read_bytes() + b'changed')
    else:
        Path(saved['normalized_audio_path']).write_bytes(b'changed')
    with pytest.raises((ValueError, ASRError), match='integrity|integrità|match|validation'):
        run_transcription(ws, recording_id=identifier, engines={'argmax': engine})
    assert engine.calls == 1


def test_cli_recording_target_and_status_are_readable_and_reject_mixed_targets(ws, monkeypatch, capsys):
    _, identifier = audio(ws)
    seen = []
    monkeypatch.setattr('mynotes.transcription.run_transcription', lambda *a, **kw: seen.append(kw) or {'status': 'COMPLETED'})
    assert main(['transcribe', '--recording', identifier, '--workspace', str(ws.root), '--json']) == 0
    assert seen == [{'recording_id': identifier, 'progress': True}]
    assert json.loads(capsys.readouterr().out)['status'] == 'COMPLETED'
    assert main(['transcription-status', '--recording', identifier, '--workspace', str(ws.root), '--json']) == 0
    assert json.loads(capsys.readouterr().out)['recording_id'] == identifier
    assert main(['transcribe', 'OPS-L01', '--recording', identifier, '--workspace', str(ws.root), '--json']) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'FAILED'
    assert len(seen) == 1


@pytest.mark.parametrize('reason,code,fragment', [
    ('Recovered after interruption; verify coverage', 'RECOVERED_AUDIO', 'interruzione'),
    ('Started late', 'STARTED_LATE', 'ritardo')])
def test_partial_warning_explains_reason(ws, reason, code, fragment):
    path, _ = audio(ws, partial=True)
    edit_sidecar(path, status='PARTIAL', reason=reason)
    warning = queue.library(ws)[0]['warnings'][0]
    assert warning['code'] == code and fragment in warning['message']


def test_missing_txt_is_not_advertised_and_explicit_reuse_restores_it(ws, monkeypatch):
    _, identifier = audio(ws)
    engine = fake_registry(monkeypatch)
    result = run_transcription(ws, recording_id=identifier, engines={'argmax': engine})
    text = Path(result['transcript_text']); text.unlink()
    before = {str(p) for p in ws.root.rglob('*')}
    item = queue.library(ws)[0]
    assert item['transcription_status'] == 'COMPLETED' and 'transcript_text' not in item
    assert {str(p) for p in ws.root.rglob('*')} == before
    assert run_transcription(ws, recording_id=identifier, engines={'argmax': engine})['cache_reused']
    assert text.is_file() and engine.calls == 1


def test_direct_busy_job_is_not_duplicated(ws, monkeypatch):
    _, identifier = audio(ws)
    engine = fake_registry(monkeypatch)
    queue.enqueue(ws, recording_id=identifier)
    job = queue.status(ws)['jobs'][0]; claim(ws, job['id'])
    with pytest.raises(ASRBusyError):
        run_transcription(ws, recording_id=identifier, engines={'argmax': engine})
    assert engine.calls == 0


def test_failed_queue_retry_is_explicit_and_keeps_original_profile(ws, monkeypatch):
    _, identifier = audio(ws)
    fake_registry(monkeypatch)
    queue.enqueue(ws, recording_id=identifier)
    job = queue.status(ws)['jobs'][0]
    with queue.database(ws) as conn:
        original = conn.execute('SELECT payload FROM jobs WHERE id=?', (job['id'],)).fetchone()['payload']
        conn.execute("UPDATE jobs SET status='FAILED',error='Model missing' WHERE id=?", (job['id'],))
    item = queue.library(ws)[0]
    assert item['can_transcribe'] and item['transcription_status'] == 'FAILED'
    assert 'Model missing' in item['warnings'][-1]['message']
    code = add_course(ws, 'Another class')['course']['code']
    capture_associations.assign(capture.CaptureStore(ws), {'assignments': [
        {'recordingID': identifier, 'courseCode': code}]})
    assert queue.enqueue(ws, recording_id=identifier)['added'] == 1
    with queue.database(ws, readonly=True) as conn:
        retried = conn.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone()
    assert retried['payload'] == original and retried['status'] == 'QUEUED'



def test_direct_failure_and_interrupted_state_are_visible_without_queue(ws, monkeypatch):
    _, identifier = audio(ws)
    engine = fake_registry(monkeypatch)
    def fail():
        raise ASRError('Selected model is missing')
    engine.hook = fail
    with pytest.raises(ASRError, match='model is missing'):
        run_transcription(ws, recording_id=identifier, engines={'argmax': engine})
    item = queue.library(ws)[0]
    assert item['transcription_status'] == 'FAILED' and item['can_transcribe']
    assert 'model is missing' in item['warnings'][-1]['message']
    state = queue.asr_directory(ws, item) / 'status.json'
    payload = json.loads(state.read_text()); payload.update(status='RUNNING', pid=99999999)
    atomic_json_write(state, payload)
    assert queue.library(ws)[0]['transcription_status'] == 'INTERRUPTED'


def test_invalid_asr_result_has_actionable_block_without_third_archive_category(ws):
    _, identifier = audio(ws)
    item = queue.library(ws)[0]
    directory = queue.asr_directory(ws, item)
    atomic_json_write(directory / 'status.json', {'status': 'NEEDS_REVIEW',
        'lecture_id': '_recordings/' + identifier, 'error': 'Invalid transcript timestamps'})
    item = queue.library(ws)[0]
    assert item['transcription_status'] == 'NEEDS_REVIEW' and not item['can_transcribe']
    assert item['blocking_reasons'][0] == {'code': 'ASR_RESULT_INVALID', 'message': 'Invalid transcript timestamps'}
    assert 'transcript_json' not in item



def associated_capture(ws):
    path, identifier = audio(ws)
    code = add_course(ws, 'Associated subject')['course']['code']
    capture_associations.assign(capture.CaptureStore(ws), {'assignments': [
        {'recordingID': identifier, 'courseCode': code, 'lessonNumber': 1}]})
    return path, identifier, f'{code}-L01'


def test_prepare_first_keeps_capture_identity_and_later_direct_reuses_one_asr(ws, monkeypatch):
    path, identifier, lecture = associated_capture(ws)
    engine = fake_registry(monkeypatch)
    pack = build_source_pack(ws, lecture, audio=path, engines={'argmax': engine})
    assert pack['transcription']['capture_recording_id'] == identifier
    assert pack['transcription']['original_asr']['lecture_id'] == '_recordings/' + identifier
    transcript = ws.runs_dir / '_recordings' / identifier / 'asr' / 'transcript.json'
    original = transcript.read_bytes()
    assert run_transcription(ws, recording_id=identifier, engines={'argmax': engine})['cache_reused']
    assert run_transcription(ws, lecture, audio=path, engines={'argmax': engine})['cache_reused']
    assert not (ws.runs_dir / lecture / 'asr' / 'transcript.json').exists()
    assert transcript.read_bytes() == original and engine.calls == 1


@pytest.mark.parametrize('first_action', ['prepare', 'direct'])
def test_prepare_and_direct_share_recording_lock_even_with_two_asr_slots(ws, monkeypatch, first_action):
    import threading
    path, identifier, lecture = associated_capture(ws)
    queue.configure(ws, parallel=2)
    engine = fake_registry(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    results, errors = [], []
    def hold_engine():
        entered.set()
        if not release.wait(15):
            raise AssertionError('Test did not release the ASR engine')
    engine.hook = hold_engine
    def prepare():
        return build_source_pack(ws, lecture, audio=path, engines={'argmax': engine})
    def direct():
        return run_transcription(ws, recording_id=identifier, engines={'argmax': engine})
    actions = {'prepare': prepare, 'direct': direct}
    def first():
        try:
            results.append(actions[first_action]())
        except BaseException as error:
            errors.append(error)
    worker = threading.Thread(target=first)
    worker.start()
    try:
        assert entered.wait(10)
        second = direct if first_action == 'prepare' else prepare
        with pytest.raises(ASRBusyError):
            second()
        assert engine.calls == 1
    finally:
        release.set()
        worker.join(15)
    assert not worker.is_alive() and not errors and len(results) == 1
    assert run_transcription(ws, recording_id=identifier, engines={'argmax': engine})['cache_reused']
    assert engine.calls == 1
