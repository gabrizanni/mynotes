#!/usr/bin/env python3
"""Record deterministic CLI output on a synthetic workspace, for diffing two checkouts.

Run it once per checkout, then compare the files with `diff -u`. Each command's
exit code, stdout and stderr are recorded. Only values that differ between two
runs of the same checkout are normalized: the temporary root, UUIDs, random hex
identifiers, inode numbers, configuration revision hashes (they cover the temporary
paths), the interpreter version and wall-clock timestamps. The hash seed is fixed
so that set-ordered output is stable.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_backend import COURSE, ROOT, build_workspace, default_python, isolated_env  # noqa: E402

SESSION = "6f0c1d2e-3b4a-4c5d-8e7f-a0b1c2d3e4f5"


def cases(workspace: Path, root: Path) -> list[list[str]]:
    ws = ["--workspace", str(workspace)]
    lesson = f"{COURSE}-L01"
    return [
        ["--version"],
        ["--help"],
        ["capture", "--help"],
        ["transcription-queue", "--help"],
        ["notes", "--help"],
        ["config", "show", *ws],
        ["config", "show", "--json", *ws],
        ["doctor", *ws],
        ["doctor", "--json", *ws],
        ["preferences", "--show", *ws],
        ["memory", "show", "--scope", "global", "--area", "notes", "--json", *ws],
        ["memory", "show", "--scope", "course", "--area", "notes", "--course", COURSE, *ws],
        ["style", "show", *ws],
        ["style", "show", "--course", COURSE, *ws],
        ["course", "pending", "--json", *ws],
        ["course", "link", COURSE, str(root / "recordings"), "--dry-run", "--json", *ws],
        ["capture", "dashboard", *ws],
        ["capture", "status", *ws],
        ["capture", "schedule", *ws],
        ["capture", "pending", *ws],
        ["transcription-queue", "status", *ws],
        ["transcription-queue", "library", *ws],
        ["transcription-queue", "lessons", *ws],
        ["transcription-queue", "transcripts", *ws],
        ["transcription-queue", "course-profile", "--course", COURSE, *ws],
        ["transcription-status", lesson, "--json", *ws],
        ["notes", COURSE, "--lessons", "1", "--json", *ws],
        ["notes", COURSE, "--lessons", "1", "--plan-only", "--json", *ws],
        ["history", *ws],
        ["status", *ws],
        ["models", "--provider", "codex", "--json"],
        ["context", "--activity", "notes", "--json", *ws],
        ["course", "edit", "MISSING", "--file", str(root / "absent.json"), *ws],
        ["transcribe", "--json", *ws],
        ["notes", COURSE, "--lessons", "0", "--json", *ws],
        ["transcription-queue", "heartbeat", "--session", SESSION, *ws],
        ["transcription-queue", "status", *ws],
    ]


VOLATILE = (
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<UUID>"),
    (re.compile(r"\b[0-9a-f]{32}\b"), "<HEX32>"),
    (re.compile(r"(\"revision\": \")[0-9a-f]{64}\""), r'\1<SHA256>"'),
    (re.compile(r"(\"file_identity\": )\[\d+, \d+\]"), r"\1<FILE_ID>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"), "<TIME>"),
    (re.compile(r"\b\d{8}T\d{6}\d*Z\b"), "<STAMP>"),
    (re.compile(r"(\"(?:expires_at|created|updated|sampled_clock|updated_at|started_at)\": )\d+(?:\.\d+)?"), r"\1<EPOCH>"),
)


def normalize(text: str, root: Path, python_version: str) -> str:
    for spelling in {str(root), str(root.resolve())}:
        text = text.replace(spelling, "<ROOT>")
    text = text.replace(python_version, "<PYTHON>")
    for pattern, replacement in VOLATILE:
        text = pattern.sub(replacement, text)
    return text


def snapshot(python: Path) -> str:
    sections = []
    with tempfile.TemporaryDirectory(prefix="mynotes-snapshot-") as temporary:
        root = Path(temporary)
        workspace = build_workspace(python, root)
        environment = {**isolated_env(root, workspace), "PYTHONHASHSEED": "0"}
        version = subprocess.run([str(python), "-c", "import sys; print(sys.version.split()[0])"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        for arguments in cases(workspace, root):
            # Without -I, so that PYTHONHASHSEED applies; cwd is the temporary root.
            completed = subprocess.run([str(python), "-X", "utf8", "-m", "mynotes", *arguments], env=environment, cwd=root,
                                       capture_output=True, text=True, encoding="utf-8", timeout=120)
            shown = normalize(" ".join(arguments), root, version)
            sections.append(f"### mynotes {shown}\nexit: {completed.returncode}\n--- stdout\n"
                            f"{normalize(completed.stdout, root, version)}--- stderr\n"
                            f"{normalize(completed.stderr, root, version)}")
    return "\n".join(sections)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=str(ROOT), help="checkout to record (default: this one)")
    parser.add_argument("--python", help="interpreter with that checkout installed (default: <repo>/.venv)")
    parser.add_argument("--output", help="write the snapshot here instead of stdout")
    arguments = parser.parse_args()
    repo = Path(arguments.repo).resolve()
    text = snapshot(Path(arguments.python) if arguments.python else default_python(repo))
    if arguments.output:
        Path(arguments.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
