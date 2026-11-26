"""Private staged-WAV to M4A conversion shared by the Windows and Linux workers.

The worker owns the session receipt and the final rename; this module only
writes the exclusive temporary file named in that receipt.
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from .audio_probe import valid_audio
from .capture import CaptureError, CaptureStore, read_json
from .workspace import Workspace

SESSION_ID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"


def _create_temporary(path: Path):
    """Exclusive creation; on POSIX the recording is owner-only (0600) from the start.

    The worker publishes this inode by link or rename, so the final audio keeps
    the same mode. Windows keeps its existing ACL-based behavior.
    """
    if os.name == "nt":
        return path.open("xb")
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")


def export(store: CaptureStore, session_id: str) -> float:
    """Encode a worker-owned attempt without touching its final destination."""
    import av

    if not re.fullmatch(SESSION_ID, session_id):
        raise CaptureError("Invalid Capture session ID")
    session = read_json(store.directory / "sessions" / f"{session_id}.json", {})
    if session.get("id") != session_id or session.get("status") != "EXPORTING":
        raise CaptureError("Capture session is not ready for export")
    stage = Path(session["directory"])
    destination = Path(session["outputPath"])
    temporary = Path(session["exportTemporaryPath"])
    if (not stage.is_absolute() or stage.name.casefold() != session_id.casefold()
            or not destination.is_absolute() or destination.suffix.lower() != ".m4a"
            or temporary.parent != destination.parent
            or not re.fullmatch(rf"\.capture-{re.escape(session_id)}-[0-9a-f]{{32}}\.tmp", temporary.name)):
        raise CaptureError("Invalid Capture export receipt")
    segments = sorted(stage.glob("*.wav"))
    if (not segments or not stage.is_dir() or stage.is_symlink()
            or any(not re.fullmatch(r"[0-9]{19}-[0-9a-f]{32}\.wav", item.name)
                   or not item.is_file() or item.is_symlink() for item in segments)):
        raise CaptureError("No valid staged audio segments")

    sample_rate = 48_000
    total_samples = 0
    # The worker owns the final rename and records a digest before publishing.
    # Exclusive creation prevents a stale temporary file from being overwritten.
    with _create_temporary(temporary) as handle:
        try:
            with av.open(handle, mode="w", format="ipod") as container:
                output = container.add_stream("aac", rate=sample_rate)
                output.layout = "mono"
                output.bit_rate = 96_000
                for segment in segments:
                    resampler = av.AudioResampler(format="fltp", layout="mono", rate=sample_rate)
                    with av.open(str(segment)) as source:
                        audio = source.streams.audio[0]
                        for frame in source.decode(audio):
                            frame.pts = None
                            for converted in resampler.resample(frame):
                                converted.pts = total_samples
                                total_samples += converted.samples
                                container.mux(output.encode(converted))
                    for converted in resampler.resample(None):
                        converted.pts = total_samples
                        total_samples += converted.samples
                        container.mux(output.encode(converted))
                if not total_samples:
                    raise CaptureError("Staged audio has no samples")
                container.mux(output.encode(None))
        except (OSError, ValueError, IndexError, av.FFmpegError):
            handle.close()
            temporary.unlink(missing_ok=True)
            raise
    if not valid_audio(temporary):
        temporary.unlink(missing_ok=True)
        raise CaptureError("Converted Capture recording could not be decoded")
    return total_samples / sample_rate


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    encode = commands.add_parser("export")
    encode.add_argument("--workspace", required=True)
    encode.add_argument("--session", required=True)
    arguments = parser.parse_args(argv)
    try:
        print(export(CaptureStore(Workspace.from_value(arguments.workspace)), arguments.session))
    except (OSError, ValueError, KeyError, IndexError) as error:
        parser.exit(1, f"Capture {arguments.command} failed: {error}\n")


if __name__ == "__main__":
    main()
