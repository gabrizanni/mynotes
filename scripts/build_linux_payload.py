#!/usr/bin/env python3
"""Build the Linux x86-64 release payload from pinned public components.

Builds whisper-cli on the CPU from its locked upstream tag with CMake, adds
the pinned uv and the public whisper.cpp speech sample, and writes a
self-verifying archive for scripts/setup_runtime.py. Speech model weights,
private configuration and provider authentication are never packaged.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

from build_dmg import download, run, sha256
from check_public_tree import ROOT, public_paths, validate

LOCK = ROOT / "setup/components.linux.lock.json"
DEFAULT_JOBS = 4


def whisper_binary(pin: dict, cache: Path, *, offline: bool, jobs: int) -> tuple[Path, Path]:
    """Return whisper-cli and its license, built from the pinned commit."""
    source = cache / f"whisper.cpp-{pin['tag']}"
    if not (source / ".git").is_dir():
        if offline:
            raise RuntimeError("Pinned whisper.cpp source missing from build cache")
        run(["git", "clone", "--quiet", "--depth", "1", "--branch", pin["tag"], pin["repository"], str(source)])
    actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if actual != pin["commit"]:
        raise RuntimeError("whisper.cpp source does not match the pinned commit")
    license_path = source / "LICENSE"
    if sha256(license_path) != pin["license_sha256"]:
        raise RuntimeError("whisper.cpp license does not match its pinned checksum")
    build = cache / f"whisper.cpp-{pin['tag']}-build"
    run(["cmake", "-S", str(source), "-B", str(build), *pin["cmake_options"]])
    run(["cmake", "--build", str(build), "--config", "Release", "--target", "whisper-cli", "--parallel", str(jobs)])
    binary = build / "bin" / "whisper-cli"
    if not binary.is_file():
        raise RuntimeError("whisper.cpp build did not produce whisper-cli")
    return binary, license_path


def uv_binary(pin: dict, cache: Path, *, offline: bool) -> Path:
    archive = download(pin["url"], cache / f"uv-{pin['version']}-linux.tar.gz", pin["sha256"], offline=offline)
    extract = cache / f"uv-{pin['version']}-linux"
    extract.mkdir(exist_ok=True)
    with tarfile.open(archive) as source:
        source.extractall(extract, filter="data")
    return next(path for path in extract.rglob("uv") if path.is_file())


def wheel_for(version: str, offline: list[str]) -> Path:
    run(["uv", "lock", "--check", *offline], cwd=ROOT)
    run(["uv", "build", "--wheel", *offline], cwd=ROOT)
    wheels = sorted((ROOT / "dist").glob(f"mynotes-{version}-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one wheel for {version}, found {len(wheels)}")
    return wheels[0]


def setup_manifest(lock: dict) -> dict:
    """What setup_runtime reads: components and engine pins, without build inputs."""
    manifest = {key: value for key, value in lock.items() if key != "build"}
    manifest["speech_sample"] = lock["build"]["speech_sample"]["path"]
    return manifest


def stage(payload: Path, relative: str, source: Path, *, mode: int | None = None) -> None:
    target = payload / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if mode is not None:
        target.chmod(mode)


def write_manifest(payload: Path, name: str, version: str) -> None:
    files = {str(path.relative_to(payload)): sha256(path) for path in sorted(payload.rglob("*"))
             if path.is_file() and path.name != "release-manifest.json"}
    manifest = {"name": name, "version": version, "platform": "linux-x86_64", "files": files}
    (payload / "release-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def build_payload(*, offline: bool = False, cache: Path | None = None, jobs: int = DEFAULT_JOBS) -> Path:
    if not sys.platform.startswith("linux") or platform.machine() not in {"x86_64", "AMD64"}:
        raise RuntimeError("The Linux payload is built on Linux x86-64")
    cache = cache or Path(tempfile.gettempdir()) / "mynotes-linux-build"
    cache.mkdir(parents=True, exist_ok=True)
    lock = json.loads(LOCK.read_text())
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    flags = ["--offline"] if offline else []
    issues = validate()
    if issues:
        raise RuntimeError("\n".join(issues))
    wheel = wheel_for(version, flags)
    build = lock["build"]
    uv = uv_binary(build["uv"], cache, offline=offline)
    sample = build["speech_sample"]
    sample_file = download(sample["url"], cache / "samples" / Path(sample["path"]).name, sample["sha256"], offline=offline)
    whisper, whisper_license = whisper_binary(build["whispercpp"], cache, offline=offline, jobs=jobs)
    name = f"mynotes-{version}-linux-x86_64"
    with tempfile.TemporaryDirectory(prefix="mynotes-linux-", dir=cache) as temporary:
        payload = Path(temporary) / name
        for path in public_paths():
            stage(payload, str(path.relative_to(ROOT)), path)
        stage(payload, f"dist/{wheel.name}", wheel)
        stage(payload, "bin/uv", uv, mode=0o755)
        stage(payload, "bin/whisper-cli", whisper, mode=0o755)
        stage(payload, sample["path"], sample_file)
        stage(payload, "third-party/whisper.cpp/LICENSE", whisper_license)
        for license_name, digest in build["uv"]["licenses"].items():
            url = f"https://raw.githubusercontent.com/astral-sh/uv/{build['uv']['version']}/{license_name}"
            stage(payload, f"third-party/uv/{license_name}", download(url, cache / "uv-licenses" / license_name, digest, offline=offline))
        (payload / "setup-components.json").write_text(json.dumps(setup_manifest(lock), indent=2) + "\n")
        write_manifest(payload, "mynotes", version)
        result = ROOT / "dist" / f"MyNotes-{version}-linux-x86_64.tar.gz"
        result.unlink(missing_ok=True)
        with tarfile.open(result, "w:gz") as archive:
            archive.add(payload, arcname=name)
    result.with_name(result.name + ".sha256").write_text(f"{sha256(result)}  {result.name}\n")
    print(result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="use only the verified build cache")
    parser.add_argument("--cache", type=Path, help="persistent cache for sources, builds and downloads")
    parser.add_argument("--jobs", type=int, default=int(os.environ.get("MYNOTES_BUILD_JOBS", DEFAULT_JOBS)))
    arguments = parser.parse_args()
    build_payload(offline=arguments.offline, cache=arguments.cache, jobs=arguments.jobs)
