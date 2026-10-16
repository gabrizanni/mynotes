"""Recorder recovery stays paired with installer lease release."""
import fcntl
import importlib.util
import json
import signal
import subprocess
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("capture_install_guard", Path(__file__).parents[1] / "scripts/capture_install_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


@pytest.fixture
def active_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    home = tmp_path / "Capture"
    home.mkdir()
    workspace = tmp_path / "workspace"
    (home / "active.json").write_text(json.dumps({"workspace": str(workspace)}))
    plist = tmp_path / "Library/LaunchAgents/com.gabrizanni.mynotes-capture.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("fake")
    monkeypatch.setenv("MYNOTES_CAPTURE_HOME", str(home))
    monkeypatch.setattr(sys, "argv", ["guard", "fake-installer"])
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if command[1] == "bootstrap":
            # Recorder can acquire its shared update lease only after unlock.
            with (home / "update.lock").open("a+") as check:
                fcntl.flock(check.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(check.fileno(), fcntl.LOCK_UN)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(guard.subprocess, "run", run)
    monkeypatch.setattr(guard.os, "killpg", lambda *args: None)
    return home, calls


def fake_child(error=None, code=0, signal_received=None):
    class Child:
        pid = 4321
        def __init__(self, *args, **kwargs): self.waited = False
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def poll(self): return 0 if self.waited else None
        def send_signal(self, signum):
            if signal_received is not None:
                signal_received.append(signum)
        def wait(self, timeout=None):
            if error is not None and not self.waited:
                self.waited = True
                raise error
            return code
    return Child


@pytest.mark.parametrize("error", [KeyboardInterrupt(), OSError("installer failed")])
def test_interrupt_or_exception_restarts_after_releasing_lock(active_guard, monkeypatch, error):
    home, calls = active_guard
    previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    monkeypatch.setattr(guard.subprocess, "Popen", fake_child(error))
    with pytest.raises(type(error)):
        guard.main()
    assert [command[1] for command in calls] == ["bootout", "bootstrap"]
    assert {s: signal.getsignal(s) for s in previous} == previous
    with (home / "update.lock").open("a+") as lease:
        fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_install_failure_returns_code_and_restarts(active_guard, monkeypatch):
    _, calls = active_guard
    monkeypatch.setattr(guard.subprocess, "Popen", fake_child(code=2))
    assert guard.main() == 2
    assert [command[1] for command in calls] == ["bootout", "bootstrap"]


def test_targeted_sigterm_forwarded_and_recovered(active_guard, monkeypatch):
    _, calls = active_guard
    received = []
    class InterruptedChild(fake_child(signal_received=received)):
        def poll(self): return None
        def wait(self, timeout=None):
            if not self.waited:
                self.waited = True
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            return 143
    monkeypatch.setattr(guard.subprocess, "Popen", InterruptedChild)
    monkeypatch.setattr(guard.os, "killpg", lambda pid,signum: received.append(signum))
    with pytest.raises(SystemExit) as ended:
        guard.main()
    assert ended.value.code == 143
    assert received and all(signum == signal.SIGTERM for signum in received)
    assert [command[1] for command in calls] == ["bootout", "bootstrap"]


def test_busy_lease_never_stops_capture(active_guard, monkeypatch):
    home, calls = active_guard
    with (home / "update.lock").open("a+") as lease:
        fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert guard.main() == 2
    assert calls == []


def test_real_targeted_termination_stops_child_and_releases_lease(tmp_path):
    """Real isolated processes; no active Capture or account configuration."""
    import os
    import time
    home = tmp_path / "guard"
    pid_file = tmp_path / "child.pid"
    command = [sys.executable, str(Path(__file__).parents[1] / "scripts/capture_install_guard.py"),
               sys.executable, "-c", "import os,time,sys;from pathlib import Path;Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(60)", str(pid_file)]
    environment = dict(os.environ, MYNOTES_CAPTURE_HOME=str(home))
    with subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pid_file.exists()
        child_pid = int(pid_file.read_text())
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=10)
        assert process.returncode == 143
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
    with (home / "update.lock").open("a+") as lease:
        fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)



def test_uncheckable_recording_process_defers_update(active_guard, monkeypatch):
    home, calls = active_guard
    workspace = Path(json.loads((home / "active.json").read_text())["workspace"])
    runtime = workspace / "state/capture/runtime.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text(json.dumps({"pid": 9999, "recording": True}))
    monkeypatch.setattr(guard.os, "kill", lambda *args: (_ for _ in ()).throw(PermissionError("probe denied")))
    monkeypatch.setattr(guard.subprocess, "Popen", lambda *args,**kwargs: pytest.fail("Must not launch installer"))
    assert guard.main() == 2
    assert calls == []
