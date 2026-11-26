"""Linux branch of the verified setup, with synthetic uv, LibreOffice and checks."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("setup_runtime", Path(__file__).parents[1] / "scripts/setup_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
REAL_RUN = subprocess.run
MODEL = b"synthetic speech model"


def executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def release(tmp_path, monkeypatch):
    root = tmp_path / "release"
    (root / "dist").mkdir(parents=True)
    (root / "dist/mynotes-test.whl").write_bytes(b"new wheel")
    lock = json.loads((Path(__file__).parents[1] / "setup/components.linux.lock.json").read_text())
    model = lock["components"][1]["files"][0]
    model.update(sha256=hashlib.sha256(MODEL).hexdigest(), size_bytes=len(MODEL))
    runtime.atomic_json(root / "setup-components.json", {**{k: v for k, v in lock.items() if k != "build"},
                                                         "speech_sample": "samples/jfk.wav"})
    runtime.atomic_json(root / "release-manifest.json", {"files": {}})
    (root / "pyproject.toml").write_text('[project]\nversion="0.2.1"\n')
    executable(root / "bin/whisper-cli", "exit 0")
    executable(root / "bin/uv", "exit 99")
    (root / "samples").mkdir()
    (root / "samples/jfk.wav").write_bytes(b"RIFF fixture")
    home = tmp_path / "runtime"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("MYNOTES_HOME", str(home))
    monkeypatch.setenv("MYNOTES_APP_HOME", str(home / "app"))
    monkeypatch.setenv("MYNOTES_PREFIX", str(tmp_path / "prefix"))
    monkeypatch.setenv("MYNOTES_INSTALL_CAPTURE_GUARD", "1")
    monkeypatch.setenv("MYNOTES_USER_HOME", str(tmp_path / "user"))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))

    def download(spec, destination, *args):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(MODEL)
        return destination
    monkeypatch.setattr(runtime, "component_download", download)
    return root, home


class FakeSystem:
    """uv creates a venv; the installed runtime answers find_soffice and checks."""

    def __init__(self, root, home, soffice="/usr/bin/soffice"):
        self.root, self.home, self.soffice = root, home, soffice
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        text = " ".join(map(str, command))
        if command[0] == str(self.root / "bin/uv") and command[1] == "venv":
            venv = Path(command[-1])
            (venv / "bin").mkdir(parents=True)
            (venv / "bin/python").symlink_to(sys.executable)
            executable(venv / "bin/mynotes", "printf 'fixture launcher works %s\\n' \"$MYNOTES_HOME\"")
        if "find_soffice" in text:
            return subprocess.CompletedProcess(command, 0, stdout=self.soffice + "\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def run_setup(root):
    return runtime.main(["--release-root", str(root), "--provider", "claude"])


def test_linux_setup_installs_locked_runtime_engine_model_and_launcher(release, tmp_path, monkeypatch):
    root, home = release
    system = FakeSystem(root, home)
    monkeypatch.setattr(runtime.subprocess, "run", system)
    assert run_setup(root) == 0
    version = home / "app/versions/0.2.1"
    assert (home / "app/current").resolve() == version.resolve()
    assert json.loads((version / "installation.json").read_text())["wheel_sha256"] == runtime.digest(root / "dist/mynotes-test.whl")
    tools = json.loads((home / "tools.json").read_text())
    assert tools["soffice"] == "/usr/bin/soffice"
    assert Path(tools["whispercpp"]).parent.parent == home / "tools/whispercpp"
    assert Path(tools["whispercpp"]).parent.name.startswith("b5454-") and os.access(tools["whispercpp"], os.X_OK)
    assert (home / "speech-models/whisper.cpp/ggml-large-v3-turbo.bin").read_bytes() == MODEL
    uv_calls = [command for command in system.calls if command[0] == str(root / "bin/uv")]
    assert [command[1] for command in uv_calls] == ["export", "venv", "pip", "pip"]
    assert "--require-hashes" in uv_calls[2] and "--locked" in uv_calls[0]
    assert not any(command[0] == "/bin/zsh" for command in system.calls)
    verify = next(command for command in system.calls if "verify_local_components" in " ".join(map(str, command)))
    assert verify[-1] == str(root / "samples/jfk.wav")
    assert not (tmp_path / "user/.zshrc").exists()
    launcher = tmp_path / "prefix/bin/mynotes"
    assert launcher.read_text().startswith("#!/bin/sh\n# MyNotes managed launcher\n")
    environment = {key: value for key, value in os.environ.items() if key != "MYNOTES_HOME"}
    output = REAL_RUN([str(launcher)], env=environment, capture_output=True, text=True)
    assert output.returncode == 0, output.stderr
    assert output.stdout.strip() == f"fixture launcher works {home}"
    assert json.loads((home / "setup.json").read_text())["provider"] == "claude"
    assert json.loads((home / "setup-progress.json").read_text())["stage"] == "ready"


def test_missing_libreoffice_names_package_managers_and_rolls_back(release, tmp_path, monkeypatch):
    root, home = release
    system = FakeSystem(root, home, soffice="")
    monkeypatch.setattr(runtime.subprocess, "run", system)
    assert run_setup(root) == 2
    error = json.loads((home / "setup-progress.json").read_text())["error"]
    assert "pacman -S libreoffice-fresh" in error and "apt install libreoffice-writer" in error
    assert not (home / "app/current").exists() and not (home / "app/current").is_symlink()
    assert not (tmp_path / "prefix/bin/mynotes").exists()
    assert not (home / "tools.json").exists()
    assert (home / "app/versions/0.2.1/installation.json").is_file()

    system.soffice = "/usr/bin/soffice"
    system.calls.clear()
    assert run_setup(root) == 0
    assert not any(command[0] == str(root / "bin/uv") for command in system.calls)
    assert (home / "app/current").resolve() == (home / "app/versions/0.2.1").resolve()


def test_failed_dependency_install_leaves_no_partial_version(release, monkeypatch):
    root, home = release
    system = FakeSystem(root, home)

    def failing(command, **kwargs):
        if command[0] == str(root / "bin/uv") and command[1] == "pip":
            raise subprocess.CalledProcessError(1, command)
        return system(command, **kwargs)
    monkeypatch.setattr(runtime.subprocess, "run", failing)
    assert run_setup(root) == 2
    assert not (home / "app/versions/0.2.1").exists()


def test_unmanaged_launcher_is_preserved(release, tmp_path, monkeypatch):
    root, home = release
    launcher = tmp_path / "prefix/bin/mynotes"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n# personal unrelated script\n")
    monkeypatch.setattr(runtime.subprocess, "run", FakeSystem(root, home))
    assert run_setup(root) == 2
    assert "Refusing to overwrite" in json.loads((home / "setup-progress.json").read_text())["error"]
    assert "personal unrelated script" in launcher.read_text()
    assert not (home / "app/current").exists()


def test_linux_payload_is_refused_on_another_platform(release, monkeypatch):
    root, home = release
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(runtime.subprocess, "run", FakeSystem(root, home))
    assert run_setup(root) == 2
    assert "for Linux" in json.loads((home / "setup-progress.json").read_text())["error"]
