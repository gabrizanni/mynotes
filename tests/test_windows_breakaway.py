"""Windows job objects for serve jobs and the queue runner: flag construction, on every platform.

What runs here is the decision logic: which creation flags and job limits are
asked for, the fallback when a parent job refuses breakaway, and which
Windows call each step of `job.cancel` makes. That a job really outlives the
app and that TerminateJobObject stops its tree can only be observed on
Windows (`tests/test_provider_windows.py` holds the native job tests).
"""
from __future__ import annotations

import errno
import signal
import subprocess

import pytest

from mynotes import provider_process, serve_jobs, transcription_queue
from mynotes.provider_process import (CREATE_BREAKAWAY_FROM_JOB, CREATE_NO_WINDOW, CREATE_SUSPENDED,
                                      JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, creation_flags, job_limit_flags,
                                      popen_breakaway)


class FakePopen:
    """Records each call; refuses the first `refusals` with the given error."""

    def __init__(self, error: OSError | None = None, refusals: int = 0) -> None:
        self.calls: list[dict] = []
        self.error, self.refusals = error, refusals
        self.pid = 4242

    def __call__(self, command, **options):
        self.calls.append({"command": command, **options})
        if self.error is not None and len(self.calls) <= self.refusals:
            raise self.error
        return self


def test_windows_constants_match_the_sdk():
    # winbase.h and winnt.h values; a wrong bit would silently change ownership.
    assert (CREATE_SUSPENDED, CREATE_NO_WINDOW, CREATE_BREAKAWAY_FROM_JOB) == (0x4, 0x08000000, 0x01000000)
    assert (provider_process.JOB_OBJECT_LIMIT_BREAKAWAY_OK, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE) == (0x800, 0x2000)


def test_job_limits_and_creation_flags():
    assert job_limit_flags(kill_on_close=True) == JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    assert job_limit_flags(kill_on_close=False) == 0
    assert creation_flags(console=False) == CREATE_SUSPENDED | CREATE_NO_WINDOW
    assert creation_flags(console=True) == CREATE_SUSPENDED
    assert creation_flags(console=False, suspended=False) == CREATE_NO_WINDOW


def test_breakaway_is_asked_first():
    popen = FakePopen()
    process, broke_away = popen_breakaway(["mynotes"], CREATE_NO_WINDOW, popen=popen, stdin=subprocess.DEVNULL)
    assert process is popen and broke_away is True
    assert [call["creationflags"] for call in popen.calls] == [CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB]
    assert popen.calls[0]["stdin"] == subprocess.DEVNULL


def test_a_job_that_refuses_breakaway_still_starts_the_process_inside_it():
    denied = PermissionError(errno.EACCES, "Access is denied")  # What CreateProcess raises without BREAKAWAY_OK.
    popen = FakePopen(denied, refusals=1)
    process, broke_away = popen_breakaway(["mynotes"], CREATE_NO_WINDOW, popen=popen)
    assert process is popen and broke_away is False
    assert [call["creationflags"] for call in popen.calls] == [CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB, CREATE_NO_WINDOW]


def test_other_start_errors_are_not_retried():
    popen = FakePopen(FileNotFoundError(errno.ENOENT, "missing"), refusals=1)
    with pytest.raises(FileNotFoundError):
        popen_breakaway(["missing"], CREATE_NO_WINDOW, popen=popen)
    assert len(popen.calls) == 1


class FakeWindowsJob:
    created: list["FakeWindowsJob"] = []

    def __init__(self, *, kill_on_close: bool = True) -> None:
        self.kill_on_close = kill_on_close
        self.broke_away = False
        self.started: list[dict] = []
        self.terminated = self.closed = False
        FakeWindowsJob.created.append(self)

    def start(self, command, *, breakaway: bool = False, **options):
        self.started.append({"command": command, "breakaway": breakaway, **options})
        self.broke_away = breakaway
        return FakePopen()

    def terminate(self, exit_code: int = 1) -> None:
        self.terminated = True

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def windows(monkeypatch):
    FakeWindowsJob.created.clear()
    monkeypatch.setattr(provider_process, "WINDOWS", True)
    monkeypatch.setattr(provider_process, "WindowsJob", FakeWindowsJob)
    return FakeWindowsJob.created


def test_a_serve_job_breaks_away_into_a_job_without_kill_on_close(windows, tmp_path):
    manager = serve_jobs.JobManager(str(tmp_path), lambda *_: None)
    job = serve_jobs.Job(tmp_path, {"id": "job-1", "state": "RUNNING"})
    manager._spawn(job, ["python", "-m", "mynotes.rpc_job"], {"stdin": subprocess.DEVNULL})
    [created] = windows
    assert created.kill_on_close is False  # The server's exit must not end the job.
    assert created.started[0]["breakaway"] is True  # Nor the supervisor's kill-on-close job.
    assert job.windows_job is created and job.process.pid == 4242


def test_cancel_terminates_the_job_object_and_falls_back_to_the_process(windows, tmp_path, monkeypatch):
    manager = serve_jobs.JobManager(str(tmp_path), lambda *_: None)
    job = serve_jobs.Job(tmp_path, {"id": "job-1", "state": "RUNNING"})
    manager._spawn(job, ["python"], {})
    signals: list[int] = []
    monkeypatch.setattr(manager, "_signal", lambda _job, number: signals.append(number))
    manager._interrupt(job)
    assert job.sibling(".cancel").exists() and signals == []  # Windows: the cancel file, never a signal.
    manager._terminate(job)
    assert signals == []
    manager._kill_tree(job)
    assert windows[0].terminated is True and windows[0].closed is False
    reattached = serve_jobs.Job(tmp_path, {"id": "job-2", "state": "RUNNING"})  # After a restart: no handle.
    manager._kill_tree(reattached)
    assert signals == [signal.SIGTERM]  # os.kill on Windows is TerminateProcess.


def test_the_queue_runner_breaks_away_too(windows, tmp_path, monkeypatch):
    popen = FakePopen()
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(transcription_queue, "runner_active", lambda _workspace: False)
    monkeypatch.setattr(transcription_queue, "runner_needed", lambda _workspace: True)
    monkeypatch.setattr(transcription_queue, "root", lambda _workspace: tmp_path / "queue")
    monkeypatch.setattr(transcription_queue, "_worker_command", lambda _workspace, *args: ["mynotes-runner", *args])
    assert transcription_queue.launch_runner(object()) is popen
    [call] = popen.calls
    assert call["creationflags"] == CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB
    assert "start_new_session" not in call and call["command"] == ["mynotes-runner", "run"]


def test_posix_jobs_and_runner_keep_their_own_session(monkeypatch, tmp_path):
    monkeypatch.setattr(provider_process, "WINDOWS", False)
    popen = FakePopen()
    monkeypatch.setattr(subprocess, "Popen", popen)
    job = serve_jobs.Job(tmp_path, {"id": "job-1", "state": "RUNNING"})
    serve_jobs.JobManager._spawn(job, ["python"], {})
    provider_process.popen_detached(["runner"])
    assert [call.get("start_new_session") for call in popen.calls] == [True, True]
    assert all("creationflags" not in call for call in popen.calls)
