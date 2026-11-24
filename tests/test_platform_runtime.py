"""Regression coverage for portable private-workspace operations."""
import os
import subprocess
import sys

import pytest

from mynotes import file_lock
from mynotes.cli import main
from mynotes.process_status import probe_process
from mynotes.transcription_queue import database
from mynotes.workspace import Workspace, default_workspace_path


def test_locks_coordinate_with_another_process(tmp_path):
    lease = tmp_path / "recording.lease"
    script = """
import sys
from mynotes import file_lock

with open(sys.argv[1], "a+") as lock:
    file_lock.flock(lock, file_lock.LOCK_SH)
    print("ready", flush=True)
    sys.stdin.read()
"""
    with subprocess.Popen([sys.executable, "-c", script, str(lease)],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          text=True) as child:
        try:
            assert child.stdout.readline().strip() == "ready"
            with lease.open("a+") as lock:
                file_lock.flock(lock, file_lock.LOCK_SH | file_lock.LOCK_NB)
                file_lock.flock(lock, file_lock.LOCK_UN)
                with pytest.raises(BlockingIOError):
                    file_lock.flock(lock, file_lock.LOCK_EX | file_lock.LOCK_NB)
        finally:
            child.stdin.close()
            child.wait(timeout=10)

    with lease.open("a+") as lock, lease.open("a+") as other:
        file_lock.flock(lock, file_lock.LOCK_EX | file_lock.LOCK_NB)
        with pytest.raises(BlockingIOError):
            file_lock.flock(other, file_lock.LOCK_SH | file_lock.LOCK_NB)
        file_lock.flock(lock, file_lock.LOCK_UN)
        file_lock.flock(other, file_lock.LOCK_EX | file_lock.LOCK_NB)


def test_checking_liveness_does_not_stop_running_child():
    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                             stdin=subprocess.PIPE)
    try:
        probe_process(child.pid)
        assert child.poll() is None
    finally:
        child.communicate(timeout=10)
    with pytest.raises(ProcessLookupError):
        probe_process(child.pid)


def test_workspace_and_readonly_queue_accept_spaces_and_uri_characters(tmp_path):
    workspace = Workspace.from_value(tmp_path / "Lezioni di città #1")
    assert main(["init", "--workspace", str(workspace.root)]) == 0
    assert workspace.is_initialized()
    with database(workspace) as connection:
        assert not connection.execute("SELECT id FROM jobs").fetchall()
    with database(workspace, readonly=True) as connection:
        assert not connection.execute("SELECT id FROM jobs").fetchall()
    assert main(["status", "--workspace", str(workspace.root)]) == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows profile layout")
def test_windows_defaults_keep_private_data_inside_local_appdata(tmp_path, monkeypatch):
    from mynotes.capture import runtime_home as capture_home
    from mynotes.onboarding import runtime_home

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MYNOTES_HOME", raising=False)
    monkeypatch.delenv("MYNOTES_CAPTURE_HOME", raising=False)
    assert runtime_home() == tmp_path / "MyNotes"
    assert capture_home() == tmp_path / "MyNotes/Capture"
    assert default_workspace_path() == tmp_path / "MyNotes/workspace"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows provider launch")
def test_windows_provider_rejects_shell_shims(tmp_path, monkeypatch):
    from mynotes.providers import provider_executable

    provider_dir = tmp_path / "Official app"
    provider_dir.mkdir()
    (tmp_path / "claude.exe").touch()
    (provider_dir / "claude.cmd").touch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", f".{os.pathsep}{provider_dir}")
    # Use the explicit search path to exclude software installed on the runner.
    assert provider_executable("claude", path=os.environ["PATH"]) is None
    executable = provider_dir / "claude.exe"
    executable.touch()
    assert provider_executable("claude", path=os.environ["PATH"]) == str(executable)
