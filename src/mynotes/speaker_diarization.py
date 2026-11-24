"""Run local Sherpa diarization in a cancellable, separate Python process."""
from __future__ import annotations

import argparse
from array import array
import math
import sys
import tempfile
import wave
from collections import defaultdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .utils import atomic_json_write, file_sha256

MODEL_NAMES = ("segmentation.onnx", "embedding.onnx")
SAMPLE_RATE = 16_000


def model_paths(settings: dict) -> tuple[Path, Path]:
    configured = settings.get("tools", {}).get("diarization_models")
    directory = Path(configured) if configured else Path(sys.executable).parent.parent / "speech/diarization"
    return directory / MODEL_NAMES[0], directory / MODEL_NAMES[1]


def identity(settings: dict) -> dict:
    try:
        installed = version("sherpa-onnx")
    except PackageNotFoundError:
        installed = None
    return {
        "sherpa_onnx": installed,
        "models": {str(path): file_sha256(path) if path.is_file() else None for path in model_paths(settings)},
    }


def _samples(path: Path, *, maximum: int | None = None) -> array:
    with wave.open(str(path), "rb") as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise ValueError("Diarization requires 16 kHz, mono, signed 16-bit PCM.")
        pcm = array("h")
        pcm.frombytes(audio.readframes(min(maximum, audio.getnframes()) if maximum else audio.getnframes()))
    if sys.byteorder != "little":
        pcm.byteswap()
    if not pcm:
        raise ValueError("Diarization received an empty recording.")
    return array("f", (sample / 32768 for sample in pcm))


def _unit_embedding(extractor, samples: array) -> list[float]:
    stream = extractor.create_stream()
    stream.accept_waveform(SAMPLE_RATE, samples)
    stream.input_finished()
    if not extractor.is_ready(stream):
        return []
    vector = list(extractor.compute(stream))
    norm = math.sqrt(sum(value ** 2 for value in vector))
    return [value / norm for value in vector] if norm > 0 and math.isfinite(norm) else []


def _match_reference(extractor, samples: array, turns: list[dict], reference: Path) -> str | None:
    from .asr import normalize_audio

    with tempfile.TemporaryDirectory(prefix="mynotes-speaker-reference-") as temporary:
        normalized = normalize_audio(reference, Path(temporary) / "reference.wav")
        reference_vector = _unit_embedding(extractor, _samples(normalized, maximum=30 * SAMPLE_RATE))
    if not reference_vector:
        raise ValueError("The professor reference contains insufficient speech.")
    clusters = defaultdict(lambda: array("f"))
    for item in sorted(turns, key=lambda turn: -(turn["end"] - turn["start"])):
        if item.get("confidence", 1) < 0.5 or item["end"] - item["start"] < 1:
            continue
        start, end = (int(item[key] * SAMPLE_RATE) for key in ("start", "end"))
        speaker_samples = clusters[item["speaker"]]
        cap = 15 * SAMPLE_RATE - len(speaker_samples)
        if cap > 0:
            speaker_samples.extend(samples[start:min(end, start + cap)])
    scores = []
    for speaker, audio in clusters.items():
        vector = _unit_embedding(extractor, audio)
        if len(vector) == len(reference_vector):
            scores.append((sum(a * b for a, b in zip(reference_vector, vector)), speaker))
    scores.sort(reverse=True)
    if scores and scores[0][0] >= 0.75 and (len(scores) == 1 or scores[0][0] - scores[1][0] >= 0.10):
        return scores[0][1]
    return None


def analyze(audio: Path, segmentation: Path, embedding: Path, reference: Path | None = None) -> dict:
    import sherpa_onnx

    if not segmentation.is_file() or not embedding.is_file():
        raise ValueError("The local Sherpa models are missing. Repair the MyNotes installation.")
    segmentation_config = sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
        pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(segmentation)),
        num_threads=2, provider="cpu",
    )
    embedding_config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(embedding), num_threads=2, provider="cpu",
    )
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=segmentation_config, embedding=embedding_config,
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.70, compute_confidence=True),
        min_duration_on=0.3, min_duration_off=0.5,
    )
    if not config.validate():
        raise ValueError("The local Sherpa speaker model configuration is invalid.")
    diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
    if diarizer.sample_rate != SAMPLE_RATE:
        raise ValueError("The local Sherpa model requires an unsupported sample rate.")
    samples = _samples(audio)
    duration = len(samples) / SAMPLE_RATE
    turns = []
    for item in diarizer.process(samples).sort_by_start_time():
        if item.speaker < 0 or not 0 <= item.start < item.end <= duration + 0.25:
            raise ValueError("Sherpa produced an invalid speaker interval.")
        turn = {"start": item.start, "end": min(item.end, duration), "speaker": f"S{item.speaker + 1}"}
        if turn["end"] <= turn["start"]:
            continue
        if math.isfinite(item.confidence) and -1 <= item.confidence <= 1:
            turn["confidence"] = (item.confidence + 1) / 2
        turns.append(turn)
    professor = None
    if reference is not None and turns:
        professor = _match_reference(sherpa_onnx.SpeakerEmbeddingExtractor(embedding_config), samples, turns, reference)
    return {"segments": turns, "professor": professor}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--segmentation", type=Path, required=True)
    parser.add_argument("--embedding", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        report = analyze(arguments.audio, arguments.segmentation, arguments.embedding, arguments.reference)
        atomic_json_write(arguments.output, report)
    except (ImportError, OSError, ValueError, RuntimeError, wave.Error) as error:
        parser.exit(1, f"Sherpa diarization failed: {error}\n")


if __name__ == "__main__":
    main()
