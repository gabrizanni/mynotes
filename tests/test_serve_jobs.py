"""Jobs: progress, cancellation, records and reattachment after a server restart.

A fake `claude` on PATH stands in for the agent's sign-in, so `provider.login`
is a real job process that runs until it is cancelled. No agent is contacted.
"""
import json
import os
import signal
import sys
import time

import pytest

from mynotes import file_lock, transcription_queue as queue
from serve_helpers import Client, build_workspace, cli, environment

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX job signals; Windows uses a cancel file")


def fake_agent(tmp_path, seconds=60):
    script = tmp_path / "bin" / "claude"
    script.write_text("#!/bin/sh\n"
                      'if [ "$1" = auth ] && [ "$2" = login ]; then\n'
                      '  echo \'{"stage": "WAITING_FOR_BROWSER", "provider": "claude"}\' >&2\n'
                      '  echo "Open the sign-in page" >&2\n'
                      f"  sleep {seconds}\n"
                      "  exit 0\n"
                      "fi\n"
                      "echo claude 0.0\n")
    script.chmod(0o755)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def wait_until(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise TimeoutError
        time.sleep(0.1)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    workspace = build_workspace(tmp_path, monkeypatch)
    return workspace, environment(tmp_path)


def start_login(client):
    started = client.call("provider.login", {"provider": "claude"})
    job_id = started["jobId"]
    client.notification("job.started", where=lambda p: p["jobId"] == job_id)
    return job_id, started["job"]["pid"]


def test_progress_output_and_cancel(setup, tmp_path):
    workspace, env = setup
    fake_agent(tmp_path)
    client = Client(workspace.root, env)
    try:
        job_id, pid = start_login(client)
        progress = client.notification("job.progress", where=lambda p: p["jobId"] == job_id)
        assert progress["stage"] == "WAITING_FOR_BROWSER"
        output = client.notification("job.output", where=lambda p: p["jobId"] == job_id)
        assert output["text"] == "Open the sign-in page"
        auth = client.notification("provider.authChanged", where=lambda p: p["jobId"] == job_id)
        assert auth["provider"] == "claude" and auth["status"]["status"] in {"incompatible", "unverified"}
        assert client.call("job.get", {"jobId": job_id})["state"] == "RUNNING"
        began = time.monotonic()
        assert client.call("job.cancel", {"jobId": job_id})["cancelRequested"] is True
        finished = client.notification("job.finished", where=lambda p: p["jobId"] == job_id)
        assert finished["state"] == "CANCELLED" and finished["exitCode"] == 130
        assert time.monotonic() - began < 10  # The first step, an interrupt, was enough.
        assert finished["resumeHint"] == {"method": "provider.login", "params": {"provider": "claude"}}
        assert not alive(pid)
        record = json.loads((workspace.state_dir / "desktop-jobs" / f"{job_id}.json").read_text())
        assert record["state"] == "CANCELLED" and record["lastProgress"]["stage"] == "INTERRUPTED"
        assert client.call("job.list")["jobs"][0]["id"] == job_id
        assert client.request("job.get", {"jobId": "job-unknown"})["error"]["code"] == -32004
    finally:
        client.close()


def test_restart_reattaches_a_running_job(setup, tmp_path):
    workspace, env = setup
    fake_agent(tmp_path)
    first = Client(workspace.root, env)
    job_id, pid = start_login(first)
    assert first.close() == 0
    assert alive(pid)  # The job outlives the server.
    second = Client(workspace.root, env)
    try:
        listed = {job["id"]: job for job in second.call("job.list")["jobs"]}
        assert listed[job_id]["state"] == "RUNNING"
        second.call("job.cancel", {"jobId": job_id})
        finished = second.notification("job.finished", where=lambda p: p["jobId"] == job_id)
        assert finished["state"] == "CANCELLED"
        wait_until(lambda: not alive(pid))
    finally:
        second.close()


def test_job_that_ended_while_no_server_watched_is_finalized(setup, tmp_path):
    workspace, env = setup
    fake_agent(tmp_path, seconds=1)
    first = Client(workspace.root, env)
    job_id, pid = start_login(first)
    first.close()
    exit_file = workspace.state_dir / "desktop-jobs" / f"{job_id}.json.exit"
    wait_until(exit_file.exists)
    second = Client(workspace.root, env)
    try:
        job = second.call("job.get", {"jobId": job_id})
        assert job["state"] == "COMPLETED" and job["exitCode"] == 0
        assert job["result"] == {"exitCode": 0, "data": None, "text": ""}
    finally:
        second.close()


def test_killed_job_is_interrupted_with_a_resume_hint(setup, tmp_path):
    workspace, env = setup
    fake_agent(tmp_path)
    first = Client(workspace.root, env)
    job_id, pid = start_login(first)
    first.notification("job.progress", where=lambda p: p["jobId"] == job_id)
    first.close()
    os.killpg(pid, signal.SIGKILL)  # The job and its fake agent, both started by this test.
    wait_until(lambda: not alive(pid))
    second = Client(workspace.root, env)
    try:
        job = second.call("job.wait", {"jobId": job_id, "timeoutSeconds": 10})
        assert job["state"] == "INTERRUPTED" and job["exitCode"] is None
        assert job["resumeHint"] == {"method": "provider.login", "params": {"provider": "claude"}}
    finally:
        second.close()


def test_forced_cancel_kills_the_process_group(setup, tmp_path):
    workspace, env = setup
    fake_agent(tmp_path)
    client = Client(workspace.root, env)
    try:
        job_id, pid = start_login(client)
        client.notification("job.progress", where=lambda p: p["jobId"] == job_id)
        client.call("job.cancel", {"jobId": job_id, "force": True})
        finished = client.notification("job.finished", where=lambda p: p["jobId"] == job_id)
        assert finished["state"] == "CANCELLED" and finished["exitCode"] == -signal.SIGKILL
        wait_until(lambda: not alive(pid))
    finally:
        client.close()


def test_runner_probe_prevents_redundant_runners(setup):
    workspace, env = setup
    lock = queue.root(workspace) / "runner.lock"
    assert not queue.runner_active(workspace)
    with lock.open("a+") as held:
        file_lock.flock(held, file_lock.LOCK_EX | file_lock.LOCK_NB)
        assert queue.runner_active(workspace)
        assert queue.launch_runner(workspace) is None
        session = "6f0c1d2e-3b4a-4c5d-8e7f-a0b1c2d3e4f5"
        completed = cli(env, "transcription-queue", "heartbeat", "--session", session, "--workspace", str(workspace.root))
        assert completed.returncode == 0 and json.loads(completed.stdout) == {"status": "OK"}
        assert not (queue.root(workspace) / "runner.log").exists()  # No runner was started.
    assert not queue.runner_active(workspace)
    assert not queue.queue_has_work(workspace)


def test_open_session_renews_the_lease_without_spawning_when_idle(setup):
    workspace, env = setup
    client = Client(workspace.root, env)
    try:
        session = client.call("session.open", {"visible": True})["sessionId"]
        wait_until(lambda: queue.status(workspace)["ui_open"], timeout=10)
        lease = json.loads((queue.root(workspace) / "ui.json").read_text())
        assert lease["session"] == session
        time.sleep(6)  # More than one renewal interval.
        assert client.call("system.stats")["spawns"] == 0
        client.call("session.close")
        assert not queue.status(workspace)["ui_open"]
        assert client.call("session.open")["sessionId"] != session
    finally:
        client.close()


def test_watch_notifies_on_change_only(setup):
    workspace, env = setup
    client = Client(workspace.root, env)
    try:
        subscribed = client.call("watch.subscribe", {"topics": ["capture", "config"]})
        before = subscribed["snapshots"]["capture"]
        assert before["data"]["paused"] is False and before["hash"]
        cached = client.call("watch.snapshot", {"topic": "capture"})
        assert cached["hash"] == before["hash"]
        computed = client.call("system.stats")["topicComputations"]["capture"]
        began = time.monotonic()
        assert cli(env, "capture", "pause", "--workspace", str(workspace.root)).returncode == 0
        changed = client.notification("capture.changed", timeout=10)
        assert changed["data"]["paused"] is True and changed["hash"] != before["hash"]
        assert time.monotonic() - began < 5
        client.drain()
        time.sleep(2.5)
        assert client.call("system.stats")["topicComputations"]["capture"] <= computed + 2
        assert [m for m in client.drain() if m["method"] == "capture.changed"] == []  # Nothing changed, nothing sent.
        assert client.request("watch.subscribe", {"topics": ["nope"]})["error"]["code"] == -32602
    finally:
        client.close()


def test_capture_request_completion_is_notified(setup):
    workspace, env = setup
    client = Client(workspace.root, env)
    try:
        client.call("watch.subscribe", {"topics": ["capture"]})
        pending = client.call("capture.test")["data"]
        assert pending["status"] == "PENDING"
        response = workspace.state_dir / "capture" / "responses" / f"{pending['requestID']}.json"
        response.parent.mkdir(parents=True, exist_ok=True)
        response.write_text(json.dumps({"requestID": pending["requestID"], "status": "COMPLETED", "message": "ok"}))
        completed = client.notification("capture.requestCompleted", timeout=10)
        assert completed["requestId"] == pending["requestID"] and completed["response"]["status"] == "COMPLETED"
    finally:
        client.close()


def test_queue_topic_follows_commits_and_settings_not_lease_renewals(setup):
    import sqlite3
    workspace, env = setup
    client = Client(workspace.root, env)
    try:
        client.call("session.open")
        client.call("watch.subscribe", {"topics": ["queue"]})
        time.sleep(1.5)
        start = client.call("system.stats")["topicComputations"]["queue"]
        with sqlite3.connect(queue.root(workspace) / "queue.sqlite3") as conn:  # Another writer commits.
            conn.execute("INSERT INTO recordings(id, metadata) VALUES ('synthetic', '{}')")
        wait_until(lambda: client.call("system.stats")["topicComputations"]["queue"] > start, timeout=5)
        assert cli(env, "transcription-queue", "pause", "--workspace", str(workspace.root)).returncode == 0
        assert client.notification("queue.changed", timeout=5, where=lambda p: p["data"]["paused"])["data"]["paused"]
    finally:
        client.close()
