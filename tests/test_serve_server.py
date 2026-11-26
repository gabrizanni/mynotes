"""The server object itself, in-process: what its reading thread answers and the order it closes in.

Each test drives `Server.handle()` with JSON lines and reads what the server
writes back, with `dispatch` replaced for the method under test so that a
request can be held running for as long as the test needs.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from mynotes.serve import Server

HOLD_SECONDS = 30
ANSWER_SECONDS = 2


class Output:
    """A write end that keeps every JSON-RPC message the server sends."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.condition = threading.Condition()

    def write(self, data: bytes) -> int:
        with self.condition:
            self.messages.extend(json.loads(line) for line in data.splitlines() if line.strip())
            self.condition.notify_all()
        return len(data)

    def flush(self) -> None:
        pass

    def answer(self, identifier, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                found = next((m for m in self.messages if m.get("id") == identifier and "method" not in m), None)
                remaining = deadline - time.monotonic()
                if found is not None or remaining <= 0:
                    return found
                self.condition.wait(remaining)


def request(server: Server, identifier: int, method: str, params: dict | None = None) -> None:
    server.handle(json.dumps({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params or {}}).encode())


@pytest.fixture
def held(tmp_path, monkeypatch):
    """A server whose `config.get` (fast lane) blocks until `release` is set."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    output = Output()
    server = Server(str(tmp_path / "workspace"), None, output)
    release, entered = threading.Event(), threading.Semaphore(0)
    original = server.dispatch

    def dispatch(method, params):
        if method == "config.get":
            entered.release()
            release.wait(HOLD_SECONDS)
            return {"exitCode": 0, "data": "held"}
        return original(method, params)

    server.dispatch = dispatch
    yield server, output, release, entered
    release.set()
    closer = threading.Thread(target=server.close, daemon=True)  # A deadlocked close() must fail, not hang.
    closer.start()
    closer.join(HOLD_SECONDS)
    assert not closer.is_alive(), "Server.close() did not return"


def test_ping_is_answered_while_the_fast_lane_is_saturated(held):
    server, output, release, entered = held
    for identifier in range(1, 5):  # The fast lane has 4 threads: all of them are now held.
        request(server, identifier, "config.get")
    for _ in range(4):
        assert entered.acquire(timeout=ANSWER_SECONDS)
    request(server, 5, "config.get")  # Queued behind them.
    begun = time.monotonic()
    request(server, 99, "system.ping")
    answer = output.answer(99, ANSWER_SECONDS)
    assert answer is not None and answer["result"]["ok"] is True, "the supervisor's ping waited for the fast lane"
    assert time.monotonic() - begun < ANSWER_SECONDS
    assert output.answer(1, 0) is None  # Still held: the ping did not wait for them.
    release.set()
    assert all(output.answer(identifier, ANSWER_SECONDS)["result"]["data"] == "held" for identifier in range(1, 6))


def test_close_stops_the_assistant_before_waiting_for_running_requests(held):
    server, output, release, entered = held
    closed = threading.Event()
    original = server.assistant.close

    def close_assistant():
        closed.set()
        original()

    server.assistant.close = close_assistant
    request(server, 1, "config.get")
    assert entered.acquire(timeout=ANSWER_SECONDS)
    closing = threading.Thread(target=server.close, daemon=True)
    closing.start()
    # The supervisor kills the server 5 s after asking it to stop: replies
    # must already be stopped while a long request still runs.
    assert closed.wait(ANSWER_SECONDS), "the assistant was closed only after every lane finished"
    assert closing.is_alive() and output.answer(1, 0) is None
    release.set()
    closing.join(ANSWER_SECONDS)
    assert not closing.is_alive()
    assert output.answer(1, ANSWER_SECONDS)["result"]["data"] == "held"  # A running request is still answered.


def test_close_answers_queued_requests_instead_of_deadlocking(held):
    server, output, release, entered = held
    for identifier in range(1, 5):
        request(server, identifier, "config.get")
    for _ in range(4):
        assert entered.acquire(timeout=ANSWER_SECONDS)
    request(server, 5, "config.get")  # Queued: cancelling it runs its done callback.
    closing = threading.Thread(target=server.close, daemon=True)
    closing.start()
    queued = output.answer(5, ANSWER_SECONDS)
    assert queued is not None and queued["error"]["code"] == -32800, "close() deadlocked on a queued request"
    release.set()
    closing.join(ANSWER_SECONDS)
    assert not closing.is_alive()
    assert all(output.answer(identifier, 0)["result"]["data"] == "held" for identifier in range(1, 5))


def test_close_answers_a_waiting_job_wait(tmp_path, monkeypatch):
    from mynotes.serve_jobs import Job
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    output = Output()
    server = Server(str(tmp_path / "workspace"), None, output)
    job = Job(tmp_path, {"id": "job-held", "method": "notes.run", "params": {}, "state": "RUNNING"})
    server.jobs.jobs[job.id] = job  # Never ends on its own.
    request(server, 1, "job.wait", {"jobId": job.id, "timeoutSeconds": 60})
    time.sleep(0.2)
    assert output.answer(1, 0) is None
    begun = time.monotonic()
    server.close()
    assert time.monotonic() - begun < ANSWER_SECONDS
    assert output.answer(1, 0)["result"]["state"] == "RUNNING"
