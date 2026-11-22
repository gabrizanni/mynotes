"""Whisper.cpp adapter contract with a representative upstream JSON report."""
from pathlib import Path
import json
import wave

import pytest

from mynotes.asr import ASRError, WhisperCppEngine, transcribe_audio
from mynotes.configuration import course_template
from mynotes.workspace import Workspace


def test_whispercpp_keeps_segment_timing_confidence_and_language(tmp_path, monkeypatch):
    exe = tmp_path / "whisper-cli.exe"
    exe.write_bytes(b"fixture")
    (tmp_path / "ggml-large-v3-turbo.bin").touch()
    audio = tmp_path / "Lezione città.wav"
    commands = []

    def run(argv):
        commands.append(argv)
        base = Path(argv[argv.index("--output-file") + 1])
        base.with_suffix(".json").write_text(json.dumps({
            "result": {"language": "it"},
            "transcription": [{
                "text": " La città.",
                "offsets": {"from": 1250, "to": 3290},
                "tokens": [{"text": "[_BEG_]", "p": 0.1}, {"text": " La", "p": 0.8},
                           {"text": " città.", "p": 0.9}],
            }],
        }), encoding="utf-8")

    monkeypatch.setattr("mynotes.asr._run", run)
    result = WhisperCppEngine(str(exe), tmp_path).transcribe(
        audio, tmp_path / "result", model="large-v3-turbo", language="it-IT",
        vocabulary=[], prompt="",
    )
    argv = commands[0]
    assert "--no-gpu" in argv
    assert argv[argv.index("--file") + 1] == str(audio)
    assert argv[argv.index("--language") + 1] == "it"
    assert result.language == "it"
    assert result.timestamp_mode == "segment"
    assert result.version.startswith("sha256:")
    assert result.segments == [{
        "start": 1.25, "end": 3.29, "text": "La città.",
        "confidence": pytest.approx(0.85), "confidence_source": "engine",
    }]


def test_whispercpp_failed_report_cannot_reuse_a_previous_transcript(tmp_path, monkeypatch):
    exe = tmp_path / "whisper-cli.exe"
    exe.touch()
    (tmp_path / "ggml-large-v3-turbo.bin").touch()
    (tmp_path / "whisper-old.json").write_text('{"transcription":[],"result":{"language":"it"}}')
    monkeypatch.setattr("mynotes.asr._run", lambda argv: None)
    with pytest.raises(ASRError, match="valid transcription report"):
        WhisperCppEngine(str(exe), tmp_path).transcribe(
            tmp_path / "audio.wav", tmp_path, model="large-v3-turbo",
            language="it", vocabulary=[], prompt="",
        )


def test_whispercpp_transcription_uses_existing_validation_and_cache(tmp_path, monkeypatch):
    workspace = Workspace.from_value(tmp_path / "workspace")
    workspace.initialize()
    exe = tmp_path / "whisper-cli.exe"
    exe.write_bytes(b"fixture")
    models = tmp_path / "models"
    models.mkdir()
    (models / "ggml-large-v3-turbo.bin").touch()
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as sample:
        sample.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        sample.writeframes(b"\0\0" * 16000 * 2)

    def run(argv):
        report = Path(argv[argv.index("--output-file") + 1]).with_suffix(".json")
        report.write_text(json.dumps({
            "result": {"language": "it"},
            "transcription": [{
                "text": "Prova.", "offsets": {"from": 0, "to": 1500},
                "tokens": [{"text": "Prova.", "p": 0.9}],
            }],
        }), encoding="utf-8")

    monkeypatch.setattr("mynotes.asr._run", run)
    course = course_template()
    course.update(code="TEST", name="Test")
    course["transcription"].update(primary_engine="whispercpp", primary_model="large-v3-turbo", language="it")
    engines = {"whispercpp": WhisperCppEngine(str(exe), models)}
    transcript = transcribe_audio(workspace, "TEST-L01", course, audio, engines=engines)
    assert transcript["status"] == "COMPLETED"
    assert transcript["segments"][0]["text"] == "Prova."
    assert transcript["segments"][0]["end_seconds"] == 1.5
    assert (workspace.runs_dir / "TEST-L01/asr/transcript.txt").is_file()
    monkeypatch.setattr("mynotes.asr._run", lambda argv: pytest.fail("Expected the validated transcript cache"))
    assert transcribe_audio(workspace, "TEST-L01", course, audio, engines=engines)["cache_reused"]
