"""Configurable local speech recognition with deterministic cache metadata."""

from __future__ import annotations

import hashlib
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
        start_f = _number(start, cursor)
        end_f = max(start_f, _number(end, start_f))
        words: list[dict[str, Any]] = []
        for word in item.get("words", []) if isinstance(item.get("words"), list) else []:
            if not isinstance(word, dict):
                continue
            token = str(word.get("word", word.get("text", ""))).strip()
            if not token:
                continue
            word_start = _number(word.get("start", word.get("startTime", start_f)), start_f)
            word_end = max(word_start, _number(word.get("end", word.get("endTime", word_start)), word_start))
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


def _run(command: list[str], *, timeout: int = 7200) -> subprocess.CompletedProcess[str]:
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
            "--language", language, "--word-timestamps", "--report",
            "--report-path", str(output_dir), "--download-model-path", str(self.model_dir),
            "--download-tokenizer-path", str(self.tokenizer_dir), "--concurrent-worker-count", "1",
        ]
        command.append("--incremental-loading")
        combined_prompt = ", ".join(vocabulary)
        if prompt:
            combined_prompt = f"{prompt}. {combined_prompt}" if combined_prompt else prompt
        if combined_prompt:
            command.extend(["--prompt", combined_prompt])
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
    data_root = Path(tools.get("model_cache", workspace.root / "local-models")).expanduser()
    result: dict[str, SpeechEngine] = {}
    argmax = tools.get("argmax") or shutil.which("argmax-cli")
    if argmax:
        result["argmax"] = ArgmaxEngine(argmax, data_root / "whisperkit", data_root / "tokenizers")
    fluid = tools.get("fluidaudio") or shutil.which("fluidaudio") or shutil.which("fluidaudiocli")
    if fluid:
        result["fluidaudio"] = FluidAudioEngine(fluid)
    return result


def transcribe_audio(
    workspace: Workspace,
    lecture_id: str,
    course: dict[str, Any],
    audio: Path,
    *,
    engines: dict[str, SpeechEngine] | None = None,
) -> dict[str, Any]:
    """Transcribe with configured passes and reuse an exact cache match."""
    settings = course["transcription"]
    run_dir = workspace.runs_dir / lecture_id / "asr"
    run_dir.mkdir(parents=True, exist_ok=True)
    audio_hash = file_sha256(audio)
    relevant = {
        key: settings.get(key)
        for key in (
            "language", "vocabulary", "accent", "primary_engine", "primary_model",
            "secondary_engine", "secondary_model", "extra_pass_engine", "extra_pass_model",
            "agreement_threshold", "diarization_engine", "professor_reference",
        )
    }
    reference = Path(settings["professor_reference"]) if settings.get("professor_reference") else None
    relevant["professor_reference_sha256"] = file_sha256(reference) if reference and reference.is_file() else None
    settings_hash = hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()
    cache_key = hashlib.sha256(f"{audio_hash}:{settings_hash}".encode()).hexdigest()
    manifest_path = run_dir / "transcript.json"
    if manifest_path.is_file():
        cached = json.loads(manifest_path.read_text(encoding="utf-8"))
        if cached.get("cache_key") == cache_key:
            cached["cache_reused"] = True
            return cached

    normalized_audio = run_dir / "input.wav"
    normalize_audio(
        audio,
        normalized_audio,
        afconvert=settings.get("tools", {}).get("afconvert", "/usr/bin/afconvert"),
    )

    registry = engines if engines is not None else default_engines(workspace, settings)
    primary_name = settings["primary_engine"]
    if primary_name not in registry:
        raise ASRError(f"Requested primary ASR engine is unavailable: {primary_name}")
    prompt = f"Spoken with {settings['accent']} accent" if settings.get("accent") else ""
    passes: list[EngineResult] = []
    primary = registry[primary_name].transcribe(
        normalized_audio, run_dir / "primary", model=settings["primary_model"], language=settings["language"],
        vocabulary=settings["vocabulary"], prompt=prompt,
    )
    passes.append(primary)
    secondary_segments: list[dict[str, Any]] = []
    if settings.get("secondary_engine"):
        name = settings["secondary_engine"]
        if name not in registry:
            raise ASRError(f"Requested secondary ASR engine is unavailable: {name}")
        secondary = registry[name].transcribe(
            normalized_audio, run_dir / "secondary", model=settings["secondary_model"], language=settings["language"],
            vocabulary=settings["vocabulary"], prompt=prompt,
        )
        passes.append(secondary)
        secondary_segments = secondary.segments
    merged, review_windows = merge_passes(primary.segments, secondary_segments, threshold=settings["agreement_threshold"])
    if settings.get("extra_pass_engine") and review_windows:
        name = settings["extra_pass_engine"]
        if name not in registry:
            raise ASRError(f"Requested extra-pass ASR engine is unavailable: {name}")
        extra = registry[name].transcribe(
            normalized_audio, run_dir / "extra", model=settings["extra_pass_model"], language=settings["language"],
            vocabulary=settings["vocabulary"], prompt=prompt, clips=review_windows,
        )
        passes.append(extra)
        for segment in merged:
            if segment["needs_review"]:
                alternative = _overlap_text(extra.segments, segment["start_seconds"], segment["end_seconds"])
                if alternative:
                    segment["extra_alternative"] = alternative
    diarization = _diarize_and_apply(workspace, course["code"], normalized_audio, run_dir, settings, merged)
    manifest = {
        "schema_version": 1, "lecture_id": lecture_id, "audio_path": str(audio), "audio_sha256": audio_hash,
        "normalized_audio_path": str(normalized_audio), "normalized_audio_sha256": file_sha256(normalized_audio),
        "settings_hash": settings_hash, "cache_key": cache_key, "cache_reused": False,
        "language": settings["language"], "accent_hint": settings.get("accent", ""),
        "passes": [{"engine": item.engine, "model": item.model, "version": item.version} for item in passes],
        "diarization": diarization, "segments": merged,
    }
    atomic_json_write(manifest_path, manifest)
    return manifest
