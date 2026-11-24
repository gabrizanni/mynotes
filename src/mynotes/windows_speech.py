"""Locate and verify the optional local speech model used on Windows."""
from __future__ import annotations

import base64
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from . import file_lock
from .utils import atomic_json_write, file_sha256

DEFAULT_MODEL = "large-v3-turbo"
MODEL_URL = (
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
    "98aa99a0a9db05ae2342309f5096248665f7cba3/ggml-large-v3-turbo.bin"
)
MODEL_SIZE = 1_624_555_275
MODEL_SHA256 = "1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69"


def whisper_executable(configured: str | None = None) -> str | None:
    bundled = Path(sys.executable).parent.parent / "speech/whisper/whisper-cli.exe"
    if configured:
        candidate = shutil.which(configured)
    elif bundled.is_file():
        candidate = str(bundled)
    else:
        candidate = shutil.which("whisper-cli.exe")
    return candidate if candidate and Path(candidate).suffix.casefold() == ".exe" else None


def model_directory() -> Path:
    from .onboarding import runtime_home
    return runtime_home() / "speech-models" / "whisper.cpp"


def _download_model_part(staging: Path) -> None:
    offset = staging.stat().st_size if staging.is_file() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with urlopen(Request(MODEL_URL, headers=headers), timeout=60) as source:
        if source.status == 206:
            expected_range = f"bytes {offset}-{MODEL_SIZE - 1}/{MODEL_SIZE}"
            if not offset or source.headers.get("Content-Range") != expected_range:
                staging.unlink(missing_ok=True)
                raise ValueError("The model server returned an unexpected byte range.")
        elif source.status == 200:
            offset = 0
        else:
            raise ValueError(f"The model server returned HTTP {source.status}.")
        with staging.open("ab" if offset else "wb") as target:
            size = offset
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > MODEL_SIZE:
                    raise ValueError("The speech model download exceeded its expected size.")
                target.write(chunk)


def install_default_model() -> Path:
    directory = model_directory()
    directory.mkdir(parents=True, exist_ok=True)
    model = directory / f"ggml-{DEFAULT_MODEL}.bin"
    with (directory / "download.lock").open("a+b") as lease:
        try:
            file_lock.flock(lease, file_lock.LOCK_EX | file_lock.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("The speech model is already downloading.") from error
        if model.is_file() and model.stat().st_size == MODEL_SIZE and file_sha256(model) == MODEL_SHA256:
            return model
        if model.exists():
            raise ValueError(f"Unexpected model at {model}. Move it aside before downloading the verified default.")
        staging = model.with_suffix(".download")
        if staging.is_symlink() or (staging.exists() and not staging.is_file()):
            raise ValueError("The speech model download path is occupied.")
        remaining = MODEL_SIZE - (staging.stat().st_size if staging.exists() else 0)
        if remaining < 0:
            staging.unlink()
            remaining = MODEL_SIZE
        if shutil.disk_usage(directory).free < remaining:
            raise ValueError("The speech model needs at least 1.63 GB of free disk space.")
        for attempt in range(3):
            if staging.is_file() and staging.stat().st_size == MODEL_SIZE:
                break
            try:
                _download_model_part(staging)
            except (URLError, TimeoutError, ConnectionError, http.client.IncompleteRead) as error:
                if attempt == 2:
                    raise ValueError("The speech model download was interrupted. Retry to resume it.") from error
        if not staging.is_file() or staging.stat().st_size != MODEL_SIZE:
            raise ValueError("The speech model download is incomplete. Retry to resume it.")
        if file_sha256(staging) != MODEL_SHA256:
            staging.unlink()
            raise ValueError("The speech model download did not match its expected checksum.")
        staging.replace(model)
    return model


def _identity(executable: str, weights: Path) -> dict:
    handle = weights.stat()
    return {
        "executable_sha256": file_sha256(Path(executable)),
        "model_path": str(weights.resolve()),
        "model_size": handle.st_size,
        "model_mtime_ns": handle.st_mtime_ns,
    }


def _proof_path() -> Path:
    from .onboarding import runtime_home
    return runtime_home() / "checks" / "whispercpp.json"


def current_proof(executable: str | None, weights: Path) -> dict | None:
    if executable and weights.is_file():
        try:
            proof = json.loads(_proof_path().read_text(encoding="utf-8"))
            if isinstance(proof, dict) and proof.get("transcription") is True and proof.get("identity") == _identity(executable, weights):
                return proof
        except (OSError, ValueError):
            pass
    return None


def _generate_spoken_test(path: Path) -> None:
    command = """
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$speech = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $speech.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::NotSet,
        [System.Speech.Synthesis.VoiceAge]::NotSet, 0, [cultureinfo]::GetCultureInfo('en-US'))
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(16000,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono)
    $speech.SetOutputToWaveFile($env:MYNOTES_SPEECH_TEST_WAV, $format)
    $speech.Speak('My Notes records audio and prepares clear study notes.')
} finally { $speech.Dispose() }
"""
    from .provider_process import run_windows_job
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    env = dict(os.environ, MYNOTES_SPEECH_TEST_WAV=str(path))
    result = run_windows_job([str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
                              "-EncodedCommand", encoded], timeout=30, env=env)
    if result.returncode or not path.is_file():
        raise ValueError("Windows could not generate the spoken test. Supply a short sample with --audio.")


def verify_transcription(executable: str, model_dir: Path, *, model: str = DEFAULT_MODEL,
                         audio: Path | None = None, language: str = "en") -> dict:
    from .asr import WhisperCppEngine, audio_duration, normalize_audio
    if audio is None and language != "en":
        raise ValueError("Supply a short audio sample to verify a language other than English.")
    weights = model_dir / f"ggml-{model}.bin"
    if not weights.is_file():
        raise ValueError(f"Install the local Whisper.cpp model {model} first.")
    signature = _identity(executable, weights)
    with tempfile.TemporaryDirectory(prefix="mynotes-speech-check-") as temporary:
        root = Path(temporary)
        normalized = root / "speech.wav"
        if audio is None:
            _generate_spoken_test(normalized)
        else:
            normalize_audio(audio, normalized)
        duration = audio_duration(normalized)
        started = time.monotonic()
        result = WhisperCppEngine(executable, model_dir).transcribe(
            normalized, root, model=model, language=language, vocabulary=[], prompt="",
        )
        elapsed = time.monotonic() - started
        words = " ".join(segment["text"] for segment in result.segments).casefold()
        if not words.strip() or (audio is None and not {"audio", "study"}.issubset(words.split())):
            raise ValueError("The local engine did not recognize the expected speech sample.")
        if _identity(executable, weights) != signature:
            raise ValueError("The local speech engine or model changed during validation.")
    proof = {"identity": signature, "transcription": True, "model": model,
             "language": result.language, "segments": len(result.segments),
             "engine_seconds": round(elapsed, 3), "audio_seconds": duration,
             "first_segment_start": result.segments[0]["start"], "last_segment_end": result.segments[-1]["end"]}
    atomic_json_write(_proof_path(), proof)
    return proof
