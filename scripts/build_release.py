#!/usr/bin/env python3
"""Build and inspect a self-contained, public MyNotes release archive."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

from check_public_tree import ROOT, public_paths, validate


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="require the existing uv cache")
    arguments = parser.parse_args(argv)
    offline = ["--offline"] if arguments.offline else []
    run(["uv", "lock", "--check", *offline])
    run(["uv", "build", *offline])
    issues = validate()
    if issues:
        raise SystemExit("\n".join(issues))
    with (ROOT / "pyproject.toml").open("rb") as handle:
        version = tomllib.load(handle)["project"]["version"]
    dist = ROOT / "dist"
    wheels = sorted(dist.glob(f"mynotes-{version}-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected one wheel for {version}, found {len(wheels)}")
    archive = dist / f"mynotes-{version}-macos-release.zip"
    run([sys.executable, "scripts/build_capture.py"])
    capture_app = ROOT / "capture/output/MyNotes Capture.app"
    app_members = [path for path in sorted(capture_app.rglob("*")) if path.is_file()]
    members = public_paths() + [wheels[0]] + app_members
    manifest = {
        "name": "mynotes",
        "version": version,
        "capture": {"bundle_id": "com.gabrizanni.mynotes-capture", "architectures": ["arm64", "x86_64"]},
        "files": {str(path.relative_to(ROOT)): digest(path) for path in members},
    }
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for path in members:
            output.write(path, path.relative_to(ROOT))
        output.writestr("release-manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    with zipfile.ZipFile(archive) as output:
        archive_names = set(output.namelist())
        expected = set(manifest["files"]) | {"release-manifest.json"}
        if archive_names != expected:
            raise SystemExit("release archive members differ from the explicit manifest")
        for name in sorted(manifest["files"]):
            if hashlib.sha256(output.read(name)).hexdigest() != manifest["files"][name]:
                raise SystemExit(f"release archive hash mismatch: {name}")
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{digest(archive)}  {archive.name}\n", encoding="utf-8")
    print(archive)
    print(checksum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
