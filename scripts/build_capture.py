#!/usr/bin/env python3
"""Build a universal native Capture app without embedding user configuration."""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(*, native_only: bool = False) -> Path:
    capture = ROOT / "capture"
    destination = capture / "output/MyNotes Capture.app"
    destination.parent.mkdir(parents=True, exist_ok=True)
    build_home = Path(tempfile.gettempdir()) / "mynotes-capture-release"
    build_home.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, CLANG_MODULE_CACHE_PATH=str(build_home / "clang-cache"),
                       SWIFTPM_MODULECACHE_OVERRIDE=str(build_home / "swift-cache"))
    architectures = [os.uname().machine] if native_only else ["arm64", "x86_64"]
    binaries = []
    for architecture in architectures:
        arguments = ["swift", "build", "--package-path", str(capture), "-c", "release",
                     "--product", "MyNotesCapture", "--triple", f"{architecture}-apple-macosx14.0",
                     "--scratch-path", str(build_home / architecture), "--cache-path", str(build_home / "cache"),
                     "--config-path", str(build_home / "config"), "--security-path", str(build_home / "security"), "--disable-sandbox"]
        subprocess.run(arguments, env=environment, check=True)
        bin_path = subprocess.check_output(arguments + ["--show-bin-path"], env=environment, text=True).strip()
        binaries.append(Path(bin_path) / "MyNotesCapture")
    temporary = Path(tempfile.mkdtemp(prefix="capture-app-", dir=destination.parent)) / "MyNotes Capture.app"
    try:
        executable = temporary / "Contents/MacOS/MyNotesCapture"
        executable.parent.mkdir(parents=True)
        if len(binaries) == 1:
            shutil.copy2(binaries[0], executable)
        else:
            subprocess.run(["lipo", "-create", *(str(p) for p in binaries), "-output", str(executable)], check=True)
        info = plistlib.loads((capture / "Resources/Info.plist").read_bytes())
        version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        info["CFBundleShortVersionString"] = version
        info["MyNotesCaptureCapabilities"] = ["standalone-recordings-v1", "calendar-recordings-v1"]
        (temporary / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        resources = temporary / "Contents/Resources"
        resources.mkdir()
        for name in ("MyNotesCapture.icns", "MyNotesCaptureLogo.png"):
            shutil.copy2(capture / "Resources" / name, resources / name)
        identity = os.environ.get("MYNOTES_CAPTURE_SIGNING_IDENTITY", "-")
        signing = ["codesign", "--force", "--sign", identity, "--identifier", info["CFBundleIdentifier"],
                   "--entitlements", str(capture / "Resources/Entitlements.plist")]
        if identity != "-":
            signing += ["--options", "runtime", "--timestamp"]
        subprocess.run(signing + [str(temporary)], check=True)
        subprocess.run(["codesign", "--verify", "--strict", str(temporary)], check=True)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(temporary), destination)
    finally:
        shutil.rmtree(temporary.parent, ignore_errors=True)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-only", action="store_true", help="local development build, not universal release")
    arguments = parser.parse_args()
    print(build(native_only=arguments.native_only))
