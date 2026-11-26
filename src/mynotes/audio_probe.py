"""Decode-based audio validation shared by platforms without a native probe."""
from __future__ import annotations

from pathlib import Path


def valid_audio(path: Path) -> bool:
    """True only when PyAV decodes at least one non-empty audio frame."""
    import av

    try:
        with av.open(str(path)) as audio:
            stream = audio.streams.audio[0]
            first = next(audio.decode(stream), None)
            return bool(first and first.samples > 0)
    except (OSError, IndexError, av.FFmpegError):
        return False
