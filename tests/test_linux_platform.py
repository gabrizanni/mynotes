"""Linux platform defaults, with macOS and Windows values pinned unchanged."""
from __future__ import annotations

import json
import os
import sys
import wave
from pathlib import Path

import pytest

from mynotes import asr, capture, models, onboarding, platform_paths, windows_speech
from mynotes.audio_probe import valid_audio
from mynotes.cli import main
from mynotes.configuration import course_template
from mynotes.workspace import Workspace


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for name in ("MYNOTES_HOME", "MYNOTES_CAPTURE_HOME"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def write_wav(path: Path, *, seconds: float = 0.5, rate: int = 16_000) -> Path:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\x00\x08" * int(rate * seconds))
    return path


def test_linux_runtime_home_shares_the_default_workspace_root(home, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    expected = home / ".local/share/mynotes"
    assert platform_paths.default_runtime_home() == expected
    assert onboarding.runtime_home() == expected
    assert capture.runtime_home() == expected / "Capture"
    assert models._cache_path("codex") == expected / "model-catalogs/codex.json"


def test_linux_runtime_home_honours_overrides(home, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("MYNOTES_HOME", str(tmp_path / "private runtime"))
    assert onboarding.runtime_home() == tmp_path / "private runtime"
    assert capture.runtime_home() == tmp_path / "private runtime/Capture"
    monkeypatch.setenv("MYNOTES_CAPTURE_HOME", str(tmp_path / "recorder"))
    assert capture.runtime_home() == tmp_path / "recorder"


def test_macos_runtime_home_is_unchanged(home, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    library = home / "Library/Application Support/MyNotes"
    assert platform_paths.default_runtime_home() == library
    assert onboarding.runtime_home() == library
    assert capture.runtime_home() == library / "Capture"
    assert models._cache_path("claude") == library / "model-catalogs/claude.json"


def test_windows_runtime_home_is_unchanged(home, monkeypatch):
    # Patch os.name only around the call: pathlib picks its class from it.
    local = home / "AppData/Local/MyNotes"
    monkeypatch.setattr(platform_paths, "windows_data_home", lambda: local)
    with monkeypatch.context() as windows:
        windows.setattr(platform_paths.os, "name", "nt")
        result = platform_paths.default_runtime_home()
    assert result == local


def test_linux_config_home_follows_absolute_xdg_only(home, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/config")
    assert platform_paths.linux_config_home() == home / ".config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert platform_paths.linux_config_home() == tmp_path / "xdg"


@pytest.mark.linux_defaults
def test_linux_courses_default_to_cpu_whispercpp_without_diarization(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    transcription = course_template()["transcription"]
    assert transcription["primary_engine"] == "whispercpp"
    assert transcription["primary_model"] == "large-v3-turbo"
    assert transcription["diarization_engine"] is None


@pytest.mark.linux_defaults
def test_macos_course_defaults_are_unchanged(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    transcription = course_template()["transcription"]
    assert transcription["primary_engine"] == "argmax"
    assert transcription["primary_model"] == "large-v3-v20240930_626MB"
    assert transcription["diarization_engine"] is None


def fake_whisper(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable lookup")
def test_posix_whisper_lookup_prefers_configured_tool_then_path(tmp_path, monkeypatch):
    managed = fake_whisper(tmp_path / "managed/whisper-cli")
    on_path = fake_whisper(tmp_path / "bin/whisper-cli")
    monkeypatch.setenv("PATH", str(on_path.parent))
    assert windows_speech.whisper_executable(str(managed)) == str(managed)
    assert windows_speech.whisper_executable() == str(on_path)
    on_path.chmod(0o644)
    assert windows_speech.whisper_executable() is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable lookup")
def test_linux_engines_use_the_installed_speech_model_directory(home, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("MYNOTES_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("PATH", str(fake_whisper(tmp_path / "bin/whisper-cli").parent))
    engine = asr.default_engines(Workspace(tmp_path / "workspace"), {"primary_engine": "whispercpp"})["whispercpp"]
    assert engine.executable == str(tmp_path / "bin/whisper-cli")
    assert engine.model_dir == tmp_path / "runtime/speech-models/whisper.cpp"


def test_linux_verification_requires_a_spoken_sample(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(ValueError, match="--audio"):
        windows_speech.verify_transcription(str(tmp_path / "whisper-cli"), tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable lookup")
def test_linux_doctor_reports_whispercpp_steps_in_order(home, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("MYNOTES_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(onboarding, "find_soffice", lambda *_: None)
    workspace = Workspace(tmp_path / "workspace")
    report = onboarding.doctor(workspace)
    assert report["checks"]["transcription"]["action"] == "Install whisper-cli as described in docs/LINUX.md."
    assert "libreoffice-writer" in report["checks"]["word"]["action"]

    whisper = fake_whisper(tmp_path / "bin/whisper-cli")
    monkeypatch.setenv("PATH", str(whisper.parent))
    assert "install-speech-model" in onboarding.doctor(workspace)["checks"]["transcription"]["action"]

    weights = tmp_path / "runtime/speech-models/whisper.cpp/ggml-large-v3-turbo.bin"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"fixture weights")
    transcription = onboarding.doctor(workspace)["checks"]["transcription"]
    assert transcription["model_present"] is True and transcription["execution_verified"] is False
    assert "verify-transcription --audio" in transcription["action"]

    proof = tmp_path / "runtime/checks/whispercpp.json"
    proof.parent.mkdir(parents=True)
    proof.write_text(json.dumps({"transcription": True, "identity": windows_speech._identity(str(whisper), weights)}))
    transcription = onboarding.doctor(workspace)["checks"]["transcription"]
    assert transcription["status"] == "ready" and transcription["execution_verified"] is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable lookup")
def test_linux_cli_runs_speech_setup_commands(home, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert main(["verify-transcription", "--workspace", str(tmp_path / "workspace"), "--json"]) == 2
    assert "docs/LINUX.md" in capsys.readouterr().out
    monkeypatch.setattr(windows_speech, "install_default_model", lambda: tmp_path / "ggml-large-v3-turbo.bin")
    assert main(["install-speech-model", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "INSTALLED"


def test_macos_cli_still_rejects_speech_setup_commands(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert main(["install-speech-model"]) == 2
    assert capsys.readouterr().out.strip() == "mynotes: This local speech setup command is available on Windows."


def test_audio_probe_accepts_decodable_audio_only(tmp_path):
    assert valid_audio(write_wav(tmp_path / "speech.wav"))
    (tmp_path / "empty.wav").write_bytes(b"")
    (tmp_path / "noise.m4a").write_bytes(b"not an audio container")
    assert not valid_audio(tmp_path / "empty.wav")
    assert not valid_audio(tmp_path / "noise.m4a")
    assert not valid_audio(tmp_path / "missing.wav")


def test_linux_normalization_decodes_with_pyav_without_afconvert(tmp_path, monkeypatch):
    import av
    monkeypatch.setattr(sys, "platform", "linux")
    source = tmp_path / "lesson.m4a"
    with av.open(str(source), mode="w", format="ipod") as container:
        stream = container.add_stream("aac", rate=48_000)
        stream.layout = "mono"
        frame = av.AudioFrame(format="fltp", layout="mono", samples=48_000)
        frame.planes[0].update(bytes(4 * 48_000))
        frame.sample_rate, frame.pts = 48_000, 0
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    monkeypatch.setattr(asr, "_run", lambda *a, **k: pytest.fail("afconvert must not run on Linux"))
    result = asr.normalize_audio(source, tmp_path / "out/lesson.wav", afconvert="/missing/afconvert")
    with wave.open(str(result), "rb") as audio:
        assert (audio.getnchannels(), audio.getframerate(), audio.getsampwidth()) == (1, 16_000, 2)
        assert audio.getnframes() > 8_000


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable lookup")
def test_linux_setup_verification_uses_the_bundled_sample(home, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("MYNOTES_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("PATH", str(fake_whisper(tmp_path / "bin/whisper-cli").parent))
    monkeypatch.setattr(onboarding, "find_soffice", lambda *_: "/usr/bin/soffice")
    monkeypatch.setattr(onboarding, "verify_word_renderer", lambda: {"word": True, "word_pages": 1})
    sample = write_wav(tmp_path / "jfk.wav")
    checked = []

    def verify(executable, model_dir, *, audio=None, **_):
        checked.append((executable, model_dir, audio))
        return {"identity": {"executable_sha256": "ab" * 32}, "transcription": True}
    monkeypatch.setattr(windows_speech, "verify_transcription", verify)
    workspace = Workspace(tmp_path / "workspace")
    with pytest.raises(ValueError, match="speech sample"):
        onboarding.verify_local_components(workspace)
    proof = onboarding.verify_local_components(workspace, sample)
    assert checked[-1] == (str(tmp_path / "bin/whisper-cli"), tmp_path / "runtime/speech-models/whisper.cpp", sample)
    assert proof["transcription"] is True and proof["word"] is True and proof["word_pages"] == 1
    assert proof["asr_engine_version"] == "sha256:" + "ab" * 32
