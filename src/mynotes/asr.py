"""Configurable local speech recognition with deterministic cache metadata."""

from __future__ import annotations

import hashlib
import contextvars
import fcntl
import os
import signal
import threading
import time
import uuid
import wave
from contextlib import contextmanager
import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol

from .utils import atomic_json_write, file_sha256
from .workspace import Workspace


class ASRBusyError(RuntimeError):
    """A conflicting operation is already running; no engine was started."""

    def __init__(self, message, state=None):
        super().__init__(message)
        self.state = state or {}


class ASRError(RuntimeError):
    """Raised when a requested local ASR capability is unavailable or fails."""


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def timestamp(seconds: float) -> str:
    whole = max(0, int(seconds))
    return f"{whole // 3600:02d}:{(whole % 3600) // 60:02d}:{whole % 60:02d}"


def speaker_role(speaker: Any) -> str:
    """Only an explicit role label establishes professor or student status."""
    label = str(speaker or "UNKNOWN").strip().casefold()
    if label in {"professor", "teacher", "instructor", "lecturer"}:
        return "Professor"
    if re.fullmatch(r"student(?:\s+\d+)?", label):
        return "Student"
    return "UNKNOWN"


def parse_asr_payload(payload: Any) -> list[dict[str, Any]]:
    """Normalize WhisperKit, FluidAudio and common Whisper JSON shapes."""
    if isinstance(payload, list):
        raw = payload
    elif isinstance(payload, dict):
        nested = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        raw = payload.get("segments") or payload.get("chunks") or nested.get("segments") or []
        if not raw and payload.get("text"):
            raw = [payload]
    else:
        raw = []
    result: list[dict[str, Any]] = []
    cursor = 0.0
    for item in raw:
        if not isinstance(item, dict):
            continue
        bounds = item.get("timestamp") or item.get("timestamps")
        start = item.get("start", item.get("startTime", cursor))
        end = item.get("end", item.get("endTime", start))
        if isinstance(bounds, (list, tuple)) and len(bounds) >= 2:
            start, end = bounds[:2]
        start_f = _number(start, float("nan"))
        end_f = _number(end, float("nan"))
        words: list[dict[str, Any]] = []
        for word in item.get("words", []) if isinstance(item.get("words"), list) else []:
            if not isinstance(word, dict):
                continue
            token = str(word.get("word", word.get("text", ""))).strip()
            if not token:
                continue
            word_start = _number(word.get("start", word.get("startTime", start_f)), start_f)
            word_end = _number(word.get("end", word.get("endTime", word_start)), float("nan"))
            words.append(
                {
                    "word": token,
                    "start": word_start,
                    "end": word_end,
                    "confidence": max(0.0, min(1.0, _number(word.get("probability", word.get("confidence", 1.0)), 1.0))),
                }
            )
        text = str(item.get("text") or item.get("transcript") or "").strip()
        text = re.sub(r"<\|[^|]+\|>", " ", text)
        if words and not text:
            text = " ".join(word["word"] for word in words)
        text = re.sub(r"\s+([,.;:!?])", r"\1", re.sub(r"\s+", " ", text)).strip()
        if not text:
            continue
        confidence = _number(item.get("confidence", item.get("score", 1.0)), 1.0)
        if words:
            confidence = sum(word["confidence"] for word in words) / len(words)
        normalized = {"start": start_f, "end": end_f, "text": text, "confidence": max(0.0, min(1.0, confidence))}
        if words:
            normalized["words"] = words
        if item.get("speaker") or item.get("speakerId"):
            normalized["speaker"] = str(item.get("speaker") or item.get("speakerId"))
        result.append(normalized)
        cursor = end_f
    return result


@dataclass(frozen=True)
class EngineResult:
    engine: str
    model: str
    version: str
    segments: list[dict[str, Any]]


class SpeechEngine(Protocol):
    name: str

    def transcribe(
        self,
        audio: Path,
        output_dir: Path,
        *,
        model: str,
        language: str,
        vocabulary: list[str],
        prompt: str,
        clips: list[tuple[float, float]] | None = None,
    ) -> EngineResult: ...


_execution = contextvars.ContextVar("asr_execution", default=None)


def _run(command: list[str], *, timeout: int = 7200) -> subprocess.CompletedProcess[str]:
    """Drain both streams to private logs with bounded memory and real heartbeats."""
    execution = _execution.get()
    if execution is None:
        return _run_legacy(command, timeout=timeout)
    log_dir, update = execution
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    prefix = uuid.uuid4().hex
    paths = {name: log_dir / f"{prefix}.{name}.log" for name in ("stdout", "stderr")}
    buffers = {name: bytearray() for name in paths}
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    except OSError as error:
        raise ASRError(f"ENGINE_START: {Path(command[0]).name}: {error}") from error
    def drain(name, stream):
        with paths[name].open("wb") as handle:
            os.chmod(paths[name], 0o600)
            while chunk := stream.read1(65536):
                handle.write(chunk)
                handle.flush()
                buffers[name].extend(chunk)
                if len(buffers[name]) > 1024 * 1024:
                    del buffers[name][:-1024 * 1024]
        stream.close()
    threads = [threading.Thread(target=drain, args=(name, getattr(process, name)), daemon=True) for name in paths]
    for thread in threads:
        thread.start()
    started = time.monotonic()
    try:
        while True:
            try:
                process.wait(timeout=min(5, max(0.01, timeout - (time.monotonic() - started))))
                break
            except subprocess.TimeoutExpired:
                update("processing", logs={name: str(path) for name, path in paths.items()})
                if time.monotonic() - started >= timeout:
                    raise ASRError(f"TIMEOUT: {Path(command[0]).name} exceeded {timeout} seconds")
    except BaseException:
        # Only terminate the dedicated group created by this invocation.
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        raise
    finally:
        for thread in threads:
            thread.join(timeout=5)
    stdout, stderr = (bytes(buffers[name]).decode("utf-8", errors="replace") for name in ("stdout", "stderr"))
    if process.returncode:
        raise ASRError(f"ENGINE_ERROR: {Path(command[0]).name}: {(stderr or stdout)[-2000:]}")
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _run_legacy(command: list[str], *, timeout: int = 7200) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ASRError(f"Local tool could not run: {command[0]}") from error
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        raise ASRError(f"Local tool failed ({Path(command[0]).name}): {detail}")
    return completed


def _tool_version(executable: str) -> str:
    try:
        output = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10)
        return (output.stdout or output.stderr).strip().splitlines()[0] or "unknown"
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return "unknown"


def _latest_segments(directory: Path) -> list[dict[str, Any]]:
    for path in sorted(directory.rglob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            parsed = parse_asr_payload(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
        if parsed:
            return parsed
    return []


class ArgmaxEngine:
    name = "argmax"

    def __init__(self, executable: str, model_dir: Path, tokenizer_dir: Path) -> None:
        self.executable = executable
        self.model_dir = model_dir
        self.tokenizer_dir = tokenizer_dir

    def transcribe(self, audio: Path, output_dir: Path, *, model: str, language: str, vocabulary: list[str], prompt: str, clips: list[tuple[float, float]] | None = None) -> EngineResult:
        if not Path(self.executable).is_file() and shutil.which(self.executable) is None:
            raise ASRError(f"Argmax executable not found: {self.executable}")
        output_dir.mkdir(parents=True, exist_ok=True)
        if clips:
            combined: list[dict[str, Any]] = []
            version = _tool_version(self.executable)
            for number, (start, end) in enumerate(clips, start=1):
                clip = output_dir / "clips" / f"clip-{number:03d}.wav"
                extract_audio_clip(audio, clip, start=start, duration=max(0.1, end - start))
                result = self.transcribe(
                    clip, output_dir / f"clip-{number:03d}", model=model, language=language,
                    vocabulary=vocabulary, prompt=prompt, clips=None,
                )
                version = result.version
                for segment in result.segments:
                    shifted = dict(segment)
                    shifted["start"] = _number(segment.get("start")) + start
                    shifted["end"] = _number(segment.get("end")) + start
                    if segment.get("words"):
                        shifted["words"] = [
                            {**word, "start": _number(word.get("start")) + start, "end": _number(word.get("end")) + start}
                            for word in segment["words"]
                        ]
                    combined.append(shifted)
            return EngineResult(self.name, model, version, combined)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.tokenizer_dir.mkdir(parents=True, exist_ok=True)
        local_models = [
            path for path in self.model_dir.rglob(f"*{model}")
            if path.is_dir() and ".cache" not in path.parts and (path / "AudioEncoder.mlmodelc").is_dir()
        ]
        model_arguments = ["--model-path", str(local_models[0])] if len(local_models) == 1 else ["--model", model]
        command = [
            self.executable, "transcribe", *model_arguments, "--audio-path", str(audio),
            "--word-timestamps", "--report",
            "--report-path", str(output_dir), "--download-model-path", str(self.model_dir),
            "--download-tokenizer-path", str(self.tokenizer_dir), "--concurrent-worker-count", "1",
        ]
        command.append("--incremental-loading")
        if language != "auto":
            command.extend(["--language", language])
        combined_prompt = ", ".join(vocabulary)
        if prompt:
            combined_prompt = f"{prompt}. {combined_prompt}" if combined_prompt else prompt
        if combined_prompt:
            command.extend(["--prompt", combined_prompt])
        # Existing compiled models are read concurrently; first-time model or
        # tokenizer preparation is serialized across all CLI/queue callers.
        with (self.model_dir.parent / ".asr-model-preparation.lock").open("a+") as model_lease:
            warm = len(local_models) == 1 and any(self.tokenizer_dir.iterdir())
            fcntl.flock(model_lease, fcntl.LOCK_SH if warm else fcntl.LOCK_EX)
            completed = _run(command)
        segments = _latest_segments(output_dir)
        if not segments and completed.stdout.strip().startswith(("{", "[")):
            try:
                segments = parse_asr_payload(json.loads(completed.stdout))
            except json.JSONDecodeError:
                pass
        if not segments:
            raise ASRError("Argmax finished without a readable timestamped transcript")
        return EngineResult(self.name, model, _tool_version(self.executable), segments)


class FluidAudioEngine:
    name = "fluidaudio"

    def __init__(self, executable: str) -> None:
        self.executable = executable

    def transcribe(self, audio: Path, output_dir: Path, *, model: str, language: str, vocabulary: list[str], prompt: str, clips: list[tuple[float, float]] | None = None) -> EngineResult:
        if clips:
            raise ASRError("FluidAudio selective clip transcription is not supported by this adapter")
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / "transcription.json"
        vocabulary_path = output_dir / "vocabulary.txt"
        vocabulary_path.write_text("\n".join(vocabulary) + ("\n" if vocabulary else ""), encoding="utf-8")
        command = [
            self.executable, "transcribe", str(audio), "--model-version", model,
            "--language", language, "--metadata", "--word-timestamps", "--output-json", str(output),
        ]
        if vocabulary:
            command.extend(["--custom-vocab", str(vocabulary_path)])
        _run(command)
        if not output.is_file():
            raise ASRError("FluidAudio finished without its requested JSON output")
        segments = parse_asr_payload(json.loads(output.read_text(encoding="utf-8")))
        if not segments:
            raise ASRError("FluidAudio produced no readable transcript segments")
        return EngineResult(self.name, model, _tool_version(self.executable), segments)


def normalize_audio(source: Path, destination: Path, *, afconvert: str = "/usr/bin/afconvert") -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.casefold() == ".wav":
        shutil.copy2(source, destination)
        return destination
    try:
        _run([afconvert, "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(source), str(destination)], timeout=1800)
    except ASRError as native_error:
        try:
            import av

            with av.open(str(source)) as input_container, av.open(str(destination), mode="w") as output_container:
                output_stream = output_container.add_stream("pcm_s16le", rate=16000)
                output_stream.layout = "mono"
                resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
                for frame in input_container.decode(audio=0):
                    for converted in resampler.resample(frame):
                        for packet in output_stream.encode(converted):
                            output_container.mux(packet)
                for converted in resampler.resample(None):
                    for packet in output_stream.encode(converted):
                        output_container.mux(packet)
                for packet in output_stream.encode(None):
                    output_container.mux(packet)
        except Exception as fallback_error:
            raise ASRError(
                f"Audio normalization failed with afconvert and PyAV: {fallback_error}"
            ) from native_error
    if not destination.is_file():
        raise ASRError("Audio normalization did not create the expected WAV file")
    return destination


def extract_audio_clip(source: Path, destination: Path, *, start: float, duration: float) -> Path:
    """Write an exact local WAV excerpt without relying on fragile container seeking."""
    try:
        import av
    except ImportError as error:
        raise ASRError("PyAV is required for selective local ASR passes") from error
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with av.open(str(source)) as input_container, av.open(str(destination), mode="w") as output_container:
            input_stream = input_container.streams.audio[0]
            input_container.seek(int(start / float(input_stream.time_base)), stream=input_stream)
            output_stream = output_container.add_stream("pcm_s16le", rate=16000)
            output_stream.layout = "mono"
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            written = 0
            maximum = int(duration * 16000)
            for frame in input_container.decode(input_stream):
                for converted in resampler.resample(frame):
                    if written >= maximum:
                        break
                    written += converted.samples
                    for packet in output_stream.encode(converted):
                        output_container.mux(packet)
                if written >= maximum:
                    break
            for packet in output_stream.encode(None):
                output_container.mux(packet)
    except Exception as error:
        raise ASRError(f"Could not extract local ASR clip: {error}") from error
    if not destination.is_file() or destination.stat().st_size == 0:
        raise ASRError("Selective ASR clip is empty")
    return destination


def _similarity(left: str, right: str) -> float:
    clean = lambda value: re.sub(r"[^a-z0-9 ]", "", value.casefold())
    return SequenceMatcher(None, clean(left), clean(right)).ratio()


def _overlap_text(segments: list[dict[str, Any]], start: float, end: float) -> str:
    return " ".join(
        str(item["text"])
        for item in segments
        if _number(item.get("end")) >= start - 0.2 and _number(item.get("start")) <= end + 0.2
    ).strip()


def merge_passes(primary: list[dict[str, Any]], secondary: list[dict[str, Any]], *, threshold: float) -> tuple[list[dict[str, Any]], list[tuple[float, float]]]:
    merged: list[dict[str, Any]] = []
    review_windows: list[tuple[float, float]] = []
    for index, segment in enumerate(primary, start=1):
        start, end = _number(segment.get("start")), _number(segment.get("end"))
        alternative = _overlap_text(secondary, start, end) if secondary else ""
        agreement = _similarity(str(segment["text"]), alternative) if alternative else float(segment.get("confidence", 0.0))
        needs_review = agreement < threshold
        if needs_review:
            review_windows.append((max(0.0, start - 2.0), end + 2.0))
        merged.append(
            {
                "id": f"T{index:03d}", "start_seconds": start, "end_seconds": end,
                "start": timestamp(start), "end": timestamp(end),
                "speaker": str(segment.get("speaker", "UNKNOWN")),
                "speaker_role": speaker_role(segment.get("speaker")),
                "speaker_role_source": "asr_label" if speaker_role(segment.get("speaker")) != "UNKNOWN" else "unverified",
                "text": str(segment["text"]),
                "alternative": alternative or None, "agreement": round(agreement, 4),
                "confidence": round(_number(segment.get("confidence"), 0.0), 4), "needs_review": needs_review,
                **({"words": segment["words"]} if segment.get("words") else {}),
            }
        )
    return merged, review_windows


def _centroid(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    size = min(len(vector) for vector in vectors)
    result = [sum(float(vector[index]) for vector in vectors) / len(vectors) for index in range(size)]
    norm = math.sqrt(sum(value * value for value in result)) or 1.0
    return [value / norm for value in result]


def _embedding_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload if isinstance(payload, list) else payload.get("embeddings", [])
    return [item for item in rows if isinstance(item, dict) and isinstance(item.get("embedding256"), list)]


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right)) if left and right else 0.0


def _parse_turns(payload: Any) -> list[dict[str, Any]]:
    rows = payload if isinstance(payload, list) else payload.get("segments", payload.get("turns", [])) if isinstance(payload, dict) else []
    return [
        {
            "start": _number(item.get("start", item.get("startTime", item.get("startTimeSeconds")))),
            "end": _number(item.get("end", item.get("endTime", item.get("endTimeSeconds")))),
            "speaker": str(item.get("role") or item.get("speaker") or item.get("speakerId") or "UNKNOWN"),
            "confidence": _number(item.get("confidence", item.get("score", 1.0)), 1.0),
        }
        for item in rows if isinstance(item, dict)
    ]


def _diarize_and_apply(workspace: Workspace, course_code: str, audio: Path, run_dir: Path, settings: dict[str, Any], transcript: list[dict[str, Any]]) -> dict[str, Any]:
    if settings.get("diarization_engine") != "fluidaudio":
        return {"status": "disabled"}
    executable = settings.get("tools", {}).get("fluidaudio") or shutil.which("fluidaudio") or shutil.which("fluidaudiocli")
    if not executable:
        raise ASRError("FluidAudio diarization was requested but no executable is configured")
    diarization = run_dir / "diarization.json"
    embeddings = run_dir / "speaker_embeddings.json"
    _run([executable, "process", str(audio), "--mode", "offline", "--output", str(diarization), "--export-embeddings", str(embeddings)])
    turns = _parse_turns(json.loads(diarization.read_text(encoding="utf-8"))) if diarization.is_file() else []
    professor_label: str | None = None
    reference_path = Path(settings["professor_reference"]) if settings.get("professor_reference") else None
    if reference_path and not reference_path.is_file():
        raise ASRError(f"Configured professor reference is missing: {reference_path}")
    profile_path = workspace.state_dir / "speaker_profiles" / f"{course_code}.json"
    reference_hash = file_sha256(reference_path) if reference_path and reference_path.is_file() else None
    cached_profile = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.is_file() else None
    if reference_hash and (cached_profile is None or cached_profile.get("source_sha256") != reference_hash):
        enrollment_dir = workspace.state_dir / "speaker_profiles" / f"{course_code}-enrollment"
        enrollment_dir.mkdir(parents=True, exist_ok=True)
        reference_embeddings = enrollment_dir / "embeddings.json"
        _run([executable, "process", str(reference_path), "--mode", "offline", "--num-speakers", "1", "--output", str(enrollment_dir / "turns.json"), "--export-embeddings", str(reference_embeddings)], timeout=3600)
        center = _centroid([row["embedding256"] for row in _embedding_rows(reference_embeddings)])
        if not center:
            raise ASRError("Professor enrollment clip produced no usable embedding")
        atomic_json_write(profile_path, {"course_code": course_code, "source_sha256": reference_hash, "centroid": center})
    if profile_path.is_file() and (not reference_hash or json.loads(profile_path.read_text(encoding="utf-8")).get("source_sha256") == reference_hash):
        reference = json.loads(profile_path.read_text(encoding="utf-8")).get("centroid", [])
        clusters: dict[int, list[list[float]]] = {}
        for row in _embedding_rows(embeddings):
            cluster = int(row.get("cluster", -1))
            if cluster >= 0:
                clusters.setdefault(cluster, []).append(row["embedding256"])
        scores = {cluster: _cosine(reference, _centroid(vectors)) for cluster, vectors in clusters.items()}
        if scores:
            cluster, score = max(scores.items(), key=lambda item: item[1])
            if score >= 0.75:
                professor_label = f"S{cluster + 1}"
    speakers: dict[str, str] = {}
    for segment in transcript:
        best = max(
            turns,
            key=lambda turn: max(0.0, min(segment["end_seconds"], turn["end"]) - max(segment["start_seconds"], turn["start"])),
            default=None,
        )
        overlap = (
            max(0.0, min(segment["end_seconds"], best["end"]) - max(segment["start_seconds"], best["start"]))
            if best else 0.0
        )
        if not best or overlap <= 0 or best["confidence"] < 0.5:
            segment["speaker"] = "UNKNOWN"
        elif professor_label and best["speaker"] == professor_label:
            segment["speaker"] = "Professor"
        elif best["speaker"] == "UNKNOWN":
            segment["speaker"] = "UNKNOWN"
        else:
            prefix = "Student" if professor_label else "Speaker"
            speakers.setdefault(best["speaker"], f"{prefix} {len(speakers) + 1}")
            segment["speaker"] = speakers[best["speaker"]]
        segment["speaker_role"] = speaker_role(segment["speaker"])
        segment["speaker_role_source"] = "diarization" if segment["speaker_role"] != "UNKNOWN" else "unverified"
        segment["speaker_role_confidence"] = round(best["confidence"], 4) if best and overlap > 0 else None
    return {"status": "completed", "professor_identified": professor_label is not None, "turns": len(turns)}


def default_engines(workspace: Workspace, settings: dict[str, Any]) -> dict[str, SpeechEngine]:
    tools = settings.get("tools", {})
    from .onboarding import managed_tools, managed_tool
    managed = managed_tools()
    data_root = Path(tools.get("model_cache") or managed.get("model_cache") or workspace.root / "local-models").expanduser()
    result: dict[str, SpeechEngine] = {}
    argmax = tools.get("argmax") or managed_tool("argmax") or shutil.which("argmax-cli")
    if argmax:
        result["argmax"] = ArgmaxEngine(argmax, data_root / "whisperkit", data_root / "tokenizers")
    fluid = tools.get("fluidaudio") or shutil.which("fluidaudio") or shutil.which("fluidaudiocli")
    if fluid:
        result["fluidaudio"] = FluidAudioEngine(fluid)
    return result


def recording_profile(course, audio):
    """A saved Capture language override is local recording metadata."""
    from copy import deepcopy
    metadata = Path(str(audio) + ".capture.json")
    if metadata.is_file():
        try:
            language = json.loads(metadata.read_text()).get("event", {}).get("transcriptionLanguage")
        except (OSError, ValueError, AttributeError) as error:
            raise ASRError("Recording metadata needs review") from error
        if language is not None:
            if not isinstance(language, str) or not re.fullmatch(r"auto|[a-z]{2,3}(?:-[A-Za-z]{2,4})?", language):
                raise ASRError("Recording language needs review")
            course = deepcopy(course)
            course["transcription"]["language"] = language
    return course


def cache_identity(workspace, course, audio, *, engines=None):
    course = recording_profile(course, audio)
    settings = course["transcription"]
    audio_hash = file_sha256(audio)
    relevant = {
        key: settings.get(key)
        for key in (
            "language", "vocabulary", "accent", "primary_engine", "primary_model",
            "secondary_engine", "secondary_model", "extra_pass_engine", "extra_pass_model",
            "agreement_threshold", "diarization_engine", "professor_reference",
        )
    }
    registry = engines if engines is not None else default_engines(workspace, settings)
    identities = {}
    for name, engine in registry.items():
        executable = getattr(engine, "executable", None)
        path = Path(executable) if executable else None
        identities[name] = {"implementation": f"{type(engine).__module__}.{type(engine).__qualname__}",
                            "executable": str(path) if path else None,
                            "executable_sha256": file_sha256(path) if path and path.is_file() else None,
                            "model_directory": str(getattr(engine, "model_dir", "")),
                            "tokenizer_directory": str(getattr(engine, "tokenizer_dir", ""))}
    for name, engine in registry.items():
        root = getattr(engine, "model_dir", None)
        if root and Path(root).is_dir():
            identities[name]["model_files"] = [
                (str(path.relative_to(root)), path.stat().st_size, path.stat().st_mtime_ns)
                for path in sorted(Path(root).rglob("*")) if path.is_file() and ".cache" not in path.parts
            ]
    relevant["cache_schema"] = 2
    relevant["engine_identity"] = identities
    relevant["configured_tools"] = settings.get("tools", {})
    reference = Path(settings["professor_reference"]) if settings.get("professor_reference") else None
    relevant["professor_reference_sha256"] = file_sha256(reference) if reference and reference.is_file() else None
    settings_hash = hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()
    cache_key = hashlib.sha256(f"{audio_hash}:{settings_hash}".encode()).hexdigest()
    return audio_hash, settings_hash, cache_key, registry


def _transcribe_audio_locked(
    workspace: Workspace,
    lecture_id: str,
    course: dict[str, Any],
    audio: Path,
    *,
    engines: dict[str, SpeechEngine] | None = None,
    update=None,
    attempt_dir: Path | None = None,
) -> dict[str, Any]:
    """Transcribe with configured passes and reuse an exact cache match."""
    update = update or (lambda *args, **kwargs: None)
    settings = course["transcription"]
    run_dir = workspace.runs_dir / lecture_id / "asr"
    run_dir.mkdir(parents=True, exist_ok=True)
    audio_hash, settings_hash, cache_key, registry = cache_identity(workspace, course, audio, engines=engines)
    for key in ("primary_engine", "secondary_engine", "extra_pass_engine"):
        if settings.get(key) and settings[key] not in registry:
            raise ASRError(f"CONFIGURATION: requested ASR engine is unavailable: {settings[key]}")
    manifest_path = run_dir / "transcript.json"
    if manifest_path.is_file():
        try:
            cached = json.loads(manifest_path.read_text(encoding="utf-8"))
            issues = validate_transcript(cached, cached.get("audio_duration_seconds"))
            normalized = Path(cached.get("normalized_audio_path", ""))
            if not normalized.is_file() or file_sha256(normalized) != cached.get("normalized_audio_sha256") or cached.get("segments_sha256") != segments_digest(cached.get("segments")):
                issues.append({"reason": "Cache integrity mismatch"})
            if cached.get("cache_key") == cache_key and not issues and cached.get("status") == "COMPLETED":
                cached["cache_reused"] = True
                update("completed", status="COMPLETED", cache_reused=True, transcript_json=str(manifest_path))
                return cached
        except (OSError, ValueError, TypeError, KeyError):
            cached = None
        # Retain cache and diagnosis; replacement happens only after a valid attempt.
        if cached is None or issues:
            quarantine = run_dir / "cache-review"
            quarantine.mkdir(exist_ok=True, mode=0o700)
            shutil.copy2(manifest_path, quarantine / f"{uuid.uuid4().hex}.json")
    run_dir = attempt_dir or run_dir / "attempts" / uuid.uuid4().hex
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    normalized_audio = run_dir / "input.wav"
    update("normalizing")
    normalize_audio(audio, normalized_audio, afconvert=settings.get("tools", {}).get("afconvert", "/usr/bin/afconvert"))
    duration = audio_duration(normalized_audio)
    if duration is None and engines is None:
        raise ASRError("INVALID_AUDIO: normalized audio has no verifiable duration")
    update("engine_started", audio_duration_seconds=duration)

    primary_name = settings["primary_engine"]
    if primary_name not in registry:
        raise ASRError(f"Requested primary ASR engine is unavailable: {primary_name}")
    prompt = f"Spoken with {settings['accent']} accent" if settings.get("accent") else ""
    passes: list[EngineResult] = []
    update("primary_pass")
    primary = _engine_pass(registry[primary_name], manifest_path.parent / "pass-cache" / cache_key / "primary.json", duration,
        normalized_audio, run_dir / "primary", model=settings["primary_model"], language=settings["language"],
        vocabulary=settings["vocabulary"], prompt=prompt,
    )
    validate_engine_result(primary, run_dir / "primary-result.json", duration)
    passes.append(primary)
    secondary_segments: list[dict[str, Any]] = []
    if settings.get("secondary_engine"):
        name = settings["secondary_engine"]
        if name not in registry:
            raise ASRError(f"Requested secondary ASR engine is unavailable: {name}")
        update("secondary_pass")
        secondary = _engine_pass(registry[name], manifest_path.parent / "pass-cache" / cache_key / "secondary.json", duration,
            normalized_audio, run_dir / "secondary", model=settings["secondary_model"], language=settings["language"],
            vocabulary=settings["vocabulary"], prompt=prompt,
        )
        validate_engine_result(secondary, run_dir / "secondary-result.json", duration)
        passes.append(secondary)
        secondary_segments = secondary.segments
    merged, review_windows = merge_passes(primary.segments, secondary_segments, threshold=settings["agreement_threshold"])
    if settings.get("extra_pass_engine") and review_windows:
        name = settings["extra_pass_engine"]
        if name not in registry:
            raise ASRError(f"Requested extra-pass ASR engine is unavailable: {name}")
        update("extra_pass")
        extra = _engine_pass(registry[name], manifest_path.parent / "pass-cache" / cache_key / "extra.json", duration,
            normalized_audio, run_dir / "extra", model=settings["extra_pass_model"], language=settings["language"],
            vocabulary=settings["vocabulary"], prompt=prompt, clips=review_windows,
        )
        validate_engine_result(extra, run_dir / "extra-result.json", duration, ordered=False)
        passes.append(extra)
        for segment in merged:
            if segment["needs_review"]:
                alternative = _overlap_text(extra.segments, segment["start_seconds"], segment["end_seconds"])
                if alternative:
                    segment["extra_alternative"] = alternative
    diarization = _diarize_and_apply(workspace, course["code"], normalized_audio, run_dir, settings, merged)
    manifest = {
        "schema_version": 2, "status": "COMPLETED", "audio_duration_seconds": duration, "attempt_id": run_dir.name, "lecture_id": lecture_id, "audio_path": str(audio), "audio_sha256": audio_hash,
        "normalized_audio_path": str(normalized_audio), "normalized_audio_sha256": file_sha256(normalized_audio),
        "settings_hash": settings_hash, "cache_key": cache_key, "cache_reused": False,
        "language": settings["language"], "accent_hint": settings.get("accent", ""),
        "passes": [{"engine": item.engine, "model": item.model, "version": item.version} for item in passes],
        "diarization": diarization, "segments": merged,
    }
    update("validating")
    issues = validate_transcript(manifest, duration)
    if file_sha256(audio) != audio_hash:
        raise ASRError("INPUT_CHANGED: audio changed while transcribing; previous transcript preserved")
    if issues:
        manifest["status"] = "NEEDS_REVIEW"
        manifest["issues"] = issues
        atomic_json_write(run_dir / "transcript-review.json", manifest)
        raise ASRReviewError("NEEDS_REVIEW: invalid transcript timestamps", issues, run_dir / "transcript-review.json")
    manifest["segments_sha256"] = segments_digest(manifest["segments"])
    atomic_json_write(run_dir / "transcript.json", manifest)
    atomic_json_write(manifest_path, manifest)
    text_path = manifest_path.with_suffix(".txt")
    write_transcript_text(text_path, manifest)
    update("completed", status="COMPLETED", transcript_json=str(manifest_path), transcript_text=str(text_path), cache_reused=False)
    return manifest


class ASRReviewError(ASRError):
    def __init__(self, message, issues, path):
        super().__init__(message)
        self.issues, self.path = issues, path


def audio_duration(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as handle:
            return handle.getnframes() / handle.getframerate()
    except (OSError, wave.Error, EOFError, ZeroDivisionError):
        return None


def validate_transcript(payload: Any, duration: float | None = None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list) or not payload["segments"]:
        return [{"reason": "Transcript must have nonempty segments"}]
    issues = []
    if duration is not None and (not isinstance(duration, (float, int)) or not math.isfinite(duration) or duration <= 0):
        return [{"reason": "Invalid audio duration"}]
    previous = -1.0
    for index, segment in enumerate(payload["segments"]):
        try:
            start, end = float(segment["start_seconds"]), float(segment["end_seconds"])
            valid = math.isfinite(start) and math.isfinite(end) and start >= -0.5 and end > start and start >= previous
            valid = valid and isinstance(segment["text"], str) and bool(segment["text"].strip())
            if duration is not None:
                valid = valid and end <= duration + 0.5
            if not valid:
                raise ValueError("Invalid segment bounds or text")
            previous = start
            words = segment.get("words", [])
            if not isinstance(words, list):
                raise ValueError("Words must be a list")
            for word in words:
                ws, we = float(word["start"]), float(word["end"])
                if not math.isfinite(ws) or not math.isfinite(we) or ws < start - 0.5 or we < ws or we > end + 0.5:
                    raise ValueError("Invalid word bounds")
            segment["start_seconds"] = max(0.0, min(start, duration)) if duration is not None else max(0.0, start)
            segment["end_seconds"] = min(end, duration) if duration is not None else end
            segment["start"], segment["end"] = timestamp(segment["start_seconds"]), timestamp(segment["end_seconds"])
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            issues.append({"segment": index + 1, "reason": str(error)})
    return issues


def validate_engine_result(result: EngineResult, path: Path, duration: float | None, *, ordered=True) -> None:
    raw = {"engine": result.engine, "model": result.model, "segments": result.segments}
    atomic_json_write(path, raw)
    normalized = [{**segment, "start_seconds": segment.get("start"), "end_seconds": segment.get("end")} for segment in result.segments]
    if not ordered:
        normalized.sort(key=lambda segment: segment["start_seconds"])
    issues = validate_transcript({"segments": normalized}, duration)
    if issues:
        raise ASRReviewError("NEEDS_REVIEW: engine output has invalid timestamps", issues, path)
    for raw_segment, checked in zip(result.segments, normalized):
        if ordered:
            raw_segment["start"], raw_segment["end"] = checked["start_seconds"], checked["end_seconds"]


def write_transcript_text(path: Path, manifest: dict) -> None:
    from tempfile import NamedTemporaryFile
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        for segment in manifest["segments"]:
            handle.write(f"[{segment['start']}–{segment['end']}] {segment.get('speaker', 'UNKNOWN')}: {segment['text']}\n")
        temporary = handle.name
    os.replace(temporary, path)


@contextmanager
def asr_workspace_lock(workspace: Workspace):
    from .asr_slots import admission
    with admission(workspace):
        yield


def transcribe_audio(workspace, lecture_id, course, audio, *, engines=None, progress=False, _lecture_locked=False, recording_id=None):
    from contextlib import nullcontext
    from .workflow import _generation_lock, WorkflowError, parse_lecture_id
    course = recording_profile(course, audio)
    if recording_id is None:
        lecture_id = lecture_id.upper()
        parse_lecture_id(lecture_id)
    else:
        if not re.fullmatch(r"[a-f0-9]{32}", recording_id):
            raise ASRError("Invalid standalone recording ID")
        lecture_id = "_recordings/" + recording_id
    try:
        with (nullcontext() if _lecture_locked else _generation_lock(workspace, lecture_id)), asr_workspace_lock(workspace):
            directory = workspace.runs_dir / lecture_id / "asr"
            attempt_id = uuid.uuid4().hex
            attempt_dir = directory / "attempts" / attempt_id
            attempt_dir.mkdir(parents=True, mode=0o700)
            state_path = directory / "status.json"
            started = time.time()
            state = {"schema_version": 1, "lecture_id": lecture_id, "attempt_id": attempt_id, "pid": os.getpid(), "started_at": started, "status": "RUNNING"}
            def update(phase, **changes):
                state.update(changes)
                state.update(phase=phase, updated_at=time.time(), elapsed_seconds=round(time.time() - started, 2))
                atomic_json_write(state_path, state)
                atomic_json_write(workspace.state_dir / "asr-active.json", state)
                atomic_json_write(attempt_dir / "status.json", state)
                if progress:
                    import sys
                    print(f"Transcription {lecture_id}: {phase} ({state['elapsed_seconds']:.0f}s)", file=sys.stderr, flush=True)
            update("accepted")
            token = _execution.set((attempt_dir / "logs", update))
            previous_handler = None
            if threading.current_thread() is threading.main_thread():
                previous_handler = signal.getsignal(signal.SIGTERM)
                def interrupted(signum, frame):
                    raise SystemExit("Transcription interrupted by SIGTERM")
                signal.signal(signal.SIGTERM, interrupted)
            try:
                result = _transcribe_audio_locked(workspace, lecture_id, course, Path(audio), engines=engines, update=update, attempt_dir=attempt_dir)
                if result.get("cache_reused"):
                    text_path = directory / "transcript.txt"
                    write_transcript_text(text_path, result)
                    if state.get("phase") != "completed":
                        update("completed", status="COMPLETED", transcript_text=str(text_path))
                return result
            except BaseException as error:
                status = "NEEDS_REVIEW" if isinstance(error, ASRReviewError) else "INTERRUPTED" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "FAILED"
                update("stopped", status=status, error=str(error), **({"issues": error.issues, "review_path": str(error.path)} if isinstance(error, ASRReviewError) else {}))
                raise
            finally:
                if previous_handler is not None:
                    signal.signal(signal.SIGTERM, previous_handler)
                _execution.reset(token)
    except WorkflowError as error:
        raise ASRBusyError(f"BUSY: {error}", transcription_status_read(workspace, lecture_id)) from error



def segments_digest(segments) -> str:
    return hashlib.sha256(json.dumps(segments, sort_keys=True, ensure_ascii=False).encode()).hexdigest()



def _engine_pass(engine, cache_path, duration, audio, output_dir, *, model, language, vocabulary, prompt, clips=None):
    """Reuse only an explicitly verified pass bound to the complete request key."""
    try:
        saved = json.loads(cache_path.read_text())
        if saved.get("segments_sha256") != segments_digest(saved.get("segments")) or saved.get("model") != model or saved.get("engine") != engine.name or saved.get("clips") != (list(map(list, clips)) if clips else None):
            raise ValueError("Pass cache mismatch")
        result = EngineResult(saved["engine"], saved["model"], saved["version"], saved["segments"])
        validate_engine_result(result, output_dir.parent / f"{output_dir.name}-reused.json", duration, ordered=not bool(clips))
        return result
    except (OSError, ValueError, TypeError, KeyError, ASRReviewError):
        pass
    result = engine.transcribe(audio, output_dir, model=model, language=language, vocabulary=vocabulary, prompt=prompt, clips=clips)
    validate_engine_result(result, output_dir.parent / f"{output_dir.name}-result.json", duration, ordered=not bool(clips))
    atomic_json_write(cache_path, {"engine": result.engine, "model": result.model, "version": result.version,
        "segments": result.segments, "segments_sha256": segments_digest(result.segments),
        "clips": list(map(list, clips)) if clips else None})
    return result



def transcription_status_read(workspace, lecture_id):
    if lecture_id.startswith("_recordings/"):
        path = workspace.runs_dir / lecture_id / "asr/status.json"
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {"status": "NOT_STARTED"}
    from .transcription import transcription_status
    return transcription_status(workspace, lecture_id)
