"""The queue runner starts only when it has work and no other runner is alive."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from mynotes import file_lock, transcription_queue as q
from mynotes.cli import main
from mynotes.utils import atomic_json_write
from test_capture_queue import audio, fake_registry, ws  # noqa: F401 - pytest fixture

SESSION = "6f0c1d2e-3b4a-4c5d-8e7f-a0b1c2d3e4f5"


@pytest.fixture
def spawns(monkeypatch):
    """Record runner launches instead of starting processes."""
    class Spy:
        calls = []

        def __getattr__(self, name):
            return getattr(subprocess, name)

        def Popen(self, command, **options):
            self.calls.append(command)
            return SimpleNamespace(pid=0, poll=lambda: 0)
    spy = Spy()
    monkeypatch.setattr(q, "subprocess", spy)
    return spy.calls


def heartbeat(workspace, capsys):
    assert main(["transcription-queue", "heartbeat", "--session", SESSION, "--workspace", str(workspace.root)]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "OK"}


def test_idle_heartbeat_starts_no_runner(ws, spawns, capsys):
    q.configure(ws, parallel=1)  # The queue database exists, with no job.
    for _ in range(3):
        heartbeat(ws, capsys)
    assert spawns == []
    assert not q.runner_needed(ws)


def test_enqueue_starts_one_runner_and_heartbeats_keep_it_running(ws, spawns, monkeypatch, capsys):
    fake_registry(monkeypatch)
    audio(ws)
    assert main(["transcription-queue", "enqueue", "--all-untranscribed", "--workspace", str(ws.root)]) == 0
    capsys.readouterr()
    assert len(spawns) == 1
    assert spawns[0][-4:] == ["transcription-queue", "run", "--workspace", str(ws.root)]
    assert q.status(ws)["jobs"][0]["status"] == "QUEUED"
    heartbeat(ws, capsys)  # Work exists and no runner holds the lock: as before, one runner.
    assert len(spawns) == 2


def test_alive_runner_prevents_another_even_with_work(ws, spawns, monkeypatch, capsys):
    fake_registry(monkeypatch)
    audio(ws)
    q.enqueue(ws)
    with (q.root(ws) / "runner.lock").open("a+") as held:
        file_lock.flock(held, file_lock.LOCK_EX | file_lock.LOCK_NB)
        heartbeat(ws, capsys)
        assert q.launch_runner(ws) is None
    assert spawns == []


def test_recorded_memory_hold_is_still_refreshed_by_a_runner(ws, spawns, capsys):
    q.configure(ws, parallel=1)
    atomic_json_write(q.root(ws) / "wait.json", {"reason": "In attesa di memoria disponibile"})
    heartbeat(ws, capsys)
    assert len(spawns) == 1
    atomic_json_write(q.root(ws) / "wait.json", {"reason": None})
    heartbeat(ws, capsys)
    assert len(spawns) == 1
