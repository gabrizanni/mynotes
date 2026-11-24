"""Measure real local inference on the same public fixture on Mac and Windows."""
from __future__ import annotations

import argparse
import json
import platform
import tempfile
import time
from pathlib import Path

from mynotes.asr import ArgmaxEngine, WhisperCppEngine, audio_duration, normalize_audio, validate_engine_result
from mynotes.utils import file_sha256
from mynotes.windows_speech import DEFAULT_MODEL, model_directory, whisper_executable


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--language", choices=("it", "en"), required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected", required=True)
    parser.add_argument("--engine", choices=("whispercpp", "argmax"), default="whispercpp")
    parser.add_argument("--executable")
    parser.add_argument("--model-directory", type=Path)
    parser.add_argument("--tokenizer-directory", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    executable = args.executable or whisper_executable()
    if not executable:
        parser.error("The installed speech executable is missing")
    models = args.model_directory or model_directory()
    if args.engine == "argmax" and not args.tokenizer_directory:
        parser.error("Argmax requires --tokenizer-directory")
    engine = (ArgmaxEngine(executable, models, args.tokenizer_directory) if args.engine == "argmax"
              else WhisperCppEngine(executable, models))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mynotes-long-speech-") as temporary:
        root = Path(temporary)
        audio = normalize_audio(args.audio, root / "input.wav")
        duration = audio_duration(audio)
        started = time.monotonic()
        result = engine.transcribe(audio, root / "inference", model=args.model,
                                   language=args.language, vocabulary=[], prompt="")
        elapsed = time.monotonic() - started
        validate_engine_result(result, args.report.with_suffix(".segments.json"), duration)
        text = " ".join(segment["text"] for segment in result.segments)
        if len(text.split()) < 100 or args.expected.casefold() not in text.casefold():
            raise RuntimeError("The long fixture did not produce the expected spoken content")
        report = {
            "platform": platform.platform(), "architecture": platform.machine(),
            "engine": result.engine, "model": result.model, "version": result.version,
            "source_sha256": file_sha256(args.audio), "normalized_sha256": file_sha256(audio),
            "language": args.language, "audio_seconds": duration,
            "engine_seconds": round(elapsed, 3), "segments": len(result.segments),
            "first_segment_start": result.segments[0]["start"],
            "last_segment_end": result.segments[-1]["end"],
            "timestamp_validation": "passed", "text": text,
        }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "text"}))


if __name__ == "__main__":
    main()
