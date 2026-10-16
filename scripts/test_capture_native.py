#!/usr/bin/env python3
"""Run native regression checks without requiring the XCTest framework."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="mynotes-capture-regression-") as value:
        temporary = Path(value)
        sdk = subprocess.check_output(["xcrun", "--show-sdk-path"], text=True).strip()
        sources = sorted((ROOT / "capture/Sources/MyNotesCapture").glob("*.swift"))
        executable = temporary / "capture-regression"
        subprocess.run(["swiftc", "-parse-as-library", "-swift-version", "5", "-D", "CAPTURE_TESTS",
                        "-target", f"{os.uname().machine}-apple-macosx14.0", "-sdk", sdk,
                        "-module-cache-path", str(temporary / "module-cache"),
                        *(str(path) for path in sources), str(ROOT / "capture/Tests/Regression.swift"),
                        "-o", str(executable)], check=True)
        subprocess.run([str(executable)], check=True, env=dict(os.environ, MYNOTES_CAPTURE_HOME=str(temporary / "runtime")))


if __name__ == "__main__":
    main()
