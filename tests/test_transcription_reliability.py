from __future__ import annotations

import json
import os
import sys
import threading
import time
import wave
from pathlib import Path

import pytest

from mynotes import asr, ingestion
from mynotes.asr import ASRBusyError, ASRError, ASRReviewError, EngineResult, transcribe_audio
from mynotes.configuration import course_template
from mynotes.sourcepack import build_source_pack
from mynotes.transcription import run_transcription, transcription_status
from mynotes.utils import atomic_json_write, file_sha256
from mynotes.workflow import _generation_lock
from mynotes.workspace import Workspace


@pytest.fixture
def setup(tmp_path):
    workspace = Workspace.from_value(tmp_path / 'private')
    workspace.initialize()
    configuration = workspace.configuration_store().load()
    course = course_template()
    course.update(code='DEMO', name='Demonstration', aliases=['sample lecture'])
    course['transcription'].update(primary_engine='fake', primary_model='fixture', secondary_engine='', extra_pass_engine='')
    configuration['courses'] = [course]
    configuration['ingestion']['stable_seconds'] = 0
    folder = tmp_path / 'audio'
    folder.mkdir()
    configuration['paths']['recording_dirs'] = [str(folder)]
    workspace.configuration_store().save(configuration)
    audio = folder / 'DEMO_L01.wav'
    with wave.open(str(audio), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(100)
        handle.writeframes(b'\x00\x00' * 1000)
    return workspace, course, audio


class Fake:
    name = 'fake'
    def __init__(self, *, end=4.0, hook=None):
        self.calls = 0
        self.end = end
        self.hook = hook
        self.directories = []
    def transcribe(self, audio, output_dir, **options):
        self.calls += 1
        self.directories.append(output_dir)
        if self.hook:
            self.hook()
        return EngineResult(self.name, options['model'], 'fixture', [
            {'start': 0.0, 'end': self.end, 'text': 'A complete example sentence.', 'confidence': 1.0}])


def test_direct_output_cache_status_and_text(setup):
    workspace, course, audio = setup
    fake = Fake()
    first = run_transcription(workspace, 'DEMO-L01', engines={'fake': fake})
    second = run_transcription(workspace, 'DEMO-L01', engines={'fake': fake})
    assert first['status'] == 'COMPLETED' and second['cache_reused'] is True
    assert fake.calls == 1
    assert Path(first['transcript_text']).read_text().startswith('[00:00:00–00:00:04]')
    assert transcription_status(workspace, 'DEMO-L01')['status'] == 'COMPLETED'


def test_discovery_reads_only_selected_audio(setup, monkeypatch):
    workspace, _, audio = setup
    other = audio.with_name('DEMO_L02.wav')
    other.write_bytes(b'must not read this cloud file')
    opened = []
    original = ingestion.hydrate
    monkeypatch.setattr(ingestion, 'hydrate', lambda path: (opened.append(path), original(path)))
    run_transcription(workspace, 'DEMO-L01', engines={'fake': Fake()})
    assert opened == [audio]
    assert not (workspace.state_dir / 'recordings.json').exists()


def test_duplicate_and_missing_audio_do_not_start_engine(setup):
    workspace, _, audio = setup
    fake = Fake()
    audio.with_name('sample lecture 1.wav').write_bytes(audio.read_bytes())
    with pytest.raises(ASRError, match='unambiguous'):
        run_transcription(workspace, 'DEMO-L01', engines={'fake': fake})
    with pytest.raises(ASRError, match='unambiguous'):
        run_transcription(workspace, 'DEMO-L02', engines={'fake': fake})
    assert fake.calls == 0
    assert transcription_status(workspace, 'DEMO-L02')['status'] == 'FAILED'


@pytest.mark.parametrize('corruption', ['empty', 'json', 'text', 'normalized'])
def test_corrupted_cache_is_not_accepted(setup, corruption):
    workspace, course, audio = setup
    fake = Fake()
    first = transcribe_audio(workspace, 'DEMO-L01', course, audio, engines={'fake': fake})
    path = workspace.runs_dir / 'DEMO-L01' / 'asr' / 'transcript.json'
    if corruption == 'json':
        path.write_text('{')
    elif corruption == 'normalized':
        Path(first['normalized_audio_path']).write_bytes(b'corrupted')
    else:
        payload = json.loads(path.read_text())
        if corruption == 'empty':
            payload['segments'] = []
        else:
            payload['segments'][0]['text'] = 'modified unverified text'
        path.write_text(json.dumps(payload))
    second = transcribe_audio(workspace, 'DEMO-L01', course, audio, engines={'fake': fake})
    assert second['cache_reused'] is False
    assert second['segments'][0]['text'] == 'A complete example sentence.'
    assert list((path.parent / 'cache-review').glob('*.json'))


@pytest.mark.parametrize('end', [10.6, float('nan'), float('inf'), -1.0])
def test_timestamp_failures_preserve_prior_transcript(setup, end):
    workspace, course, audio = setup
    first = transcribe_audio(workspace, 'DEMO-L01', course, audio, engines={'fake': Fake()})
    final = workspace.runs_dir / 'DEMO-L01' / 'asr' / 'transcript.json'
    original = final.read_bytes()
    course['transcription']['primary_model'] = 'changed-settings'
    with pytest.raises(ASRReviewError) as raised:
        transcribe_audio(workspace, 'DEMO-L01', course, audio, engines={'fake': Fake(end=end)})
    assert final.read_bytes() == original
    assert raised.value.path.is_file()
    assert transcription_status(workspace, 'DEMO-L01')['status'] == 'NEEDS_REVIEW'


def test_small_rounding_clamped(setup):
    workspace, course, audio = setup
    manifest = transcribe_audio(workspace, 'DEMO-L01', course, audio, engines={'fake': Fake(end=10.3)})
    assert manifest['segments'][0]['end_seconds'] == 10.0


def test_same_lesson_and_workspace_exclusion(setup):
    workspace, course, audio = setup
    entered, finish = threading.Event(), threading.Event()
    failures = []
    def wait():
        entered.set()
        assert finish.wait(5)
    fake = Fake(hook=wait)
    def first():
        try:
            transcribe_audio(workspace, 'DEMO-L01', course, audio, engines={'fake': fake})
        except BaseException as error:
            failures.append(error)
    thread = threading.Thread(target=first)
    thread.start()
    assert entered.wait(5)
    try:
        with pytest.raises(ASRBusyError):
            transcribe_audio(workspace, 'DEMO-L01', course, audio, engines={'fake': Fake()})
        with pytest.raises(ASRBusyError) as raised:
            transcribe_audio(workspace, 'DEMO-L02', course, audio, engines={'fake': Fake()})
        assert raised.value.state['lecture_id'] == 'DEMO-L01'
        with pytest.raises(Exception, match='already running'):
            build_source_pack(workspace, 'DEMO-L01', audio=audio, engines={'fake': Fake()})
    finally:
        finish.set()
        thread.join(5)
    assert not failures and fake.calls == 1


def test_prepare_uses_same_glossary_and_does_not_repeat_asr(setup):
    workspace, _, audio = setup
    fake = Fake()
    run_transcription(workspace, 'DEMO-L01', engines={'fake': fake})
    pack = build_source_pack(workspace, 'DEMO-L01', audio=audio, engines={'fake': fake})
    assert pack['transcription']['cache_reused'] is True
    assert fake.calls == 1


def test_changed_audio_fails_and_releases_locks(setup):
    workspace, course, audio = setup
    fake = Fake(hook=lambda: audio.write_bytes(audio.read_bytes() + b'change'))
    with pytest.raises(ASRError, match='INPUT_CHANGED'):
        transcribe_audio(workspace, 'DEMO-L01', course, audio, engines={'fake': fake})
    with _generation_lock(workspace, 'DEMO-L01'):
        pass
    assert transcription_status(workspace, 'DEMO-L01')['status'] == 'FAILED'


def test_fresh_attempts_cannot_read_previous_outputs(setup):
    workspace, course, audio = setup
    fake = Fake()
    transcribe_audio(workspace, 'DEMO-L01', course, audio, engines={'fake': fake})
    course['transcription']['primary_model'] = 'new'
    class NoOutput(Fake):
        def transcribe(self, audio, output_dir, **options):
            assert not output_dir.exists()
            return EngineResult('fake', options['model'], 'fixture', [])
    with pytest.raises(ASRReviewError):
        transcribe_audio(workspace, 'DEMO-L01', course, audio, engines={'fake': NoOutput()})


def test_completed_primary_pass_reused_after_secondary_failure(setup):
    workspace, course, audio = setup
    primary = Fake()
    class Secondary(Fake):
        name = 'second'
        def transcribe(self, audio, output_dir, **options):
            self.calls += 1
            if self.calls == 1:
                raise ASRError('synthetic failure')
            return EngineResult(self.name, options['model'], 'fixture', [
                {'start': 0.0, 'end': 4.0, 'text': 'A complete example sentence.', 'confidence': 1.0}])
    secondary = Secondary()
    course['transcription'].update(secondary_engine='second', secondary_model='fixture')
    with pytest.raises(ASRError):
        transcribe_audio(workspace, 'DEMO-L01', course, audio, engines={'fake': primary, 'second': secondary})
    manifest = transcribe_audio(workspace, 'DEMO-L01', course, audio, engines={'fake': primary, 'second': secondary})
    assert manifest['status'] == 'COMPLETED' and primary.calls == 1 and secondary.calls == 2


def test_status_is_read_only_and_detects_gone_process(setup):
    workspace, _, _ = setup
    assert transcription_status(workspace, 'DEMO-L01')['status'] == 'NOT_STARTED'
    assert not (workspace.runs_dir / 'DEMO-L01').exists()
    path = workspace.runs_dir / 'DEMO-L01' / 'asr' / 'status.json'
    atomic_json_write(path, {'lecture_id': 'DEMO-L01', 'status': 'RUNNING', 'pid': 99999999})
    before = path.read_bytes()
    assert transcription_status(workspace, 'DEMO-L01')['status'] == 'INTERRUPTED'
    assert path.read_bytes() == before


def test_progress_drains_large_streams_to_private_logs(tmp_path):
    updates = []
    token = asr._execution.set((tmp_path / 'logs', lambda *args, **kwargs: updates.append((args, kwargs))))
    try:
        result = asr._run([sys.executable, '-c', 'import sys; sys.stdout.write("x"*2000000); sys.stderr.write("y"*2000000)'])
    finally:
        asr._execution.reset(token)
    assert len(result.stdout) == len(result.stderr) == 1024 * 1024
    logs = list((tmp_path / 'logs').glob('*.log'))
    assert len(logs) == 2 and all(path.stat().st_size == 2000000 for path in logs)
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in logs)


def test_timeout_kills_only_owned_process(tmp_path):
    token = asr._execution.set((tmp_path / 'logs', lambda *args, **kwargs: None))
    try:
        with pytest.raises(ASRError, match='TIMEOUT'):
            asr._run([sys.executable, '-c', 'import time; time.sleep(5)'], timeout=0.05)
    finally:
        asr._execution.reset(token)


def test_parser_preserves_invalid_bounds_for_validation():
    result = asr.parse_asr_payload({'segments': [{'start': 5, 'end': 1, 'text': 'Invalid'}]})
    assert result[0]['end'] == 1
