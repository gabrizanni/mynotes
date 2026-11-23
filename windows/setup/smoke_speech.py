"""Record timings from an installed Whisper.cpp inference on a public fixture."""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from mynotes.asr import extract_audio_clip, normalize_audio
from mynotes.windows_speech import model_directory, verify_transcription, whisper_executable


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--language", required=True, choices=("it", "en"))
    parser.add_argument("--excerpt-seconds", type=int)
    arguments = parser.parse_args()
    executable = whisper_executable()
    if not executable:
        raise RuntimeError("The candidate Whisper.cpp executable is missing")
    with tempfile.TemporaryDirectory(prefix="mynotes-speech-fixture-") as temporary:
        root = Path(temporary)
        audio = arguments.audio
        if arguments.excerpt_seconds:
            source = normalize_audio(audio, root / "full.wav")
            audio = root / "excerpt.wav"
            extract_audio_clip(source, audio, start=0, duration=arguments.excerpt_seconds)
        proof = verify_transcription(executable, model_directory(), model="tiny",
                                     audio=audio, language=arguments.language)
    if proof["language"] != arguments.language:
        raise RuntimeError(f"The candidate returned an unexpected language: {proof['language']}")
    del proof["identity"]
    print(json.dumps(proof))


if __name__ == "__main__":
    main()
