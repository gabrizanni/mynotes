"""Linux payload pins stay consistent with the Windows engine and model pins."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pytest

from mynotes import windows_speech

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "setup/components.linux.lock.json").read_text())


@pytest.fixture
def builder(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("build_linux_payload")


def test_linux_model_pin_is_the_windows_default_model():
    model = next(c for c in LOCK["components"] if c["id"] == "whispercpp-model")["files"]
    assert model == [{"path": f"whisper.cpp/ggml-{windows_speech.DEFAULT_MODEL}.bin", "url": windows_speech.MODEL_URL,
                      "sha256": windows_speech.MODEL_SHA256, "size_bytes": windows_speech.MODEL_SIZE}]
    assert LOCK["asr"] == {"engine": "whispercpp", "model": windows_speech.DEFAULT_MODEL}


def test_linux_engine_uses_the_windows_whisper_tag_and_pinned_commit():
    workflow = (ROOT / ".github/workflows/windows-candidate.yml").read_text()
    pin = LOCK["build"]["whispercpp"]
    assert f"whisper.cpp/releases/download/{pin['tag']}/whisper-bin-x64.zip" in workflow
    assert f"whisper.cpp/{pin['commit']}/LICENSE" in workflow
    assert LOCK["whispercpp_version"] == pin["tag"]
    assert "-DGGML_NATIVE=OFF" in pin["cmake_options"] and "-DBUILD_SHARED_LIBS=OFF" in pin["cmake_options"]
    sample = LOCK["build"]["speech_sample"]
    assert f"whisper.cpp/{pin['commit']}/samples/jfk.wav" in sample["url"]
    assert sample["sha256"] in workflow.lower()


def test_linux_uv_pin_matches_the_macos_release_tool():
    macos = json.loads((ROOT / "setup/components.lock.json").read_text())["build"]["uv"]
    linux = LOCK["build"]["uv"]
    assert linux["version"] == macos["version"] and linux["licenses"] == macos["licenses"]
    assert linux["url"].endswith(f"/{linux['version']}/uv-x86_64-unknown-linux-gnu.tar.gz")


def test_setup_manifest_drops_build_inputs(builder):
    manifest = builder.setup_manifest(LOCK)
    assert "build" not in manifest and manifest["platform"] == "linux-x86_64"
    assert manifest["speech_sample"] == "samples/jfk.wav"


def test_release_manifest_hashes_every_payload_file(builder, tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin/whisper-cli").write_bytes(b"engine")
    (tmp_path / "README.md").write_text("readme")
    builder.write_manifest(tmp_path, "mynotes", "9.9.9")
    manifest = json.loads((tmp_path / "release-manifest.json").read_text())
    assert manifest["platform"] == "linux-x86_64" and manifest["version"] == "9.9.9"
    assert manifest["files"] == {"README.md": hashlib.sha256(b"readme").hexdigest(),
                                 "bin/whisper-cli": hashlib.sha256(b"engine").hexdigest()}
