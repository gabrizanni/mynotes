"""Locate and verify the optional local speech model used on Windows."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

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
        if shutil.disk_usage(directory).free < MODEL_SIZE:
            raise ValueError("The speech model needs at least 1.63 GB of free disk space.")
        with tempfile.TemporaryDirectory(prefix="download-", dir=directory) as temporary:
            staging = Path(temporary) / model.name
            digest = hashlib.sha256()
            size = 0
            try:
                with urlopen(MODEL_URL, timeout=60) as source, staging.open("xb") as target:
                    while chunk := source.read(1024 * 1024):
                        size += len(chunk)
                        if size > MODEL_SIZE:
                            raise ValueError("The speech model download exceeded its expected size.")
                        digest.update(chunk)
                        target.write(chunk)
            except URLError as error:
                raise ValueError(f"Speech model download failed: {error.reason}") from error
            if size != MODEL_SIZE or digest.hexdigest() != MODEL_SHA256:
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
    from .asr import WhisperCppEngine, normalize_audio
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
        result = WhisperCppEngine(executable, model_dir).transcribe(
            normalized, root, model=model, language=language, vocabulary=[], prompt="",
        )
        words = " ".join(segment["text"] for segment in result.segments).casefold()
        if not words.strip() or (audio is None and not {"audio", "study"}.issubset(words.split())):
            raise ValueError("The local engine did not recognize the expected speech sample.")
        if _identity(executable, weights) != signature:
            raise ValueError("The local speech engine or model changed during validation.")
    proof = {"identity": signature, "transcription": True, "model": model,
             "language": result.language, "segments": len(result.segments)}
    atomic_json_write(_proof_path(), proof)
    return proof
