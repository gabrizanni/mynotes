"""A real `mynotes serve --stdio` process and a synthetic workspace for the serve tests."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

from mynotes import capture
from mynotes.configuration import course_template
from mynotes.workspace import Workspace

COURSE = "DEMO"


def environment(tmp_path: Path) -> dict[str, str]:
    """A child environment with no agents on PATH and private runtime folders."""
    home, tools = tmp_path / "home", tmp_path / "bin"
    home.mkdir(exist_ok=True)
    tools.mkdir(exist_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("MYNOTES_")}
    env.update(HOME=str(home), USERPROFILE=str(home), PATH=os.pathsep.join([str(tools), "/usr/bin", "/bin"]),
               MYNOTES_HOME=str(tmp_path / "runtime"), MYNOTES_CAPTURE_HOME=str(tmp_path / "capture-runtime"))
    return env


def build_workspace(tmp_path: Path, monkeypatch) -> Workspace:
    """Demo course, static schedule, two stable WAV recordings and a queue database."""
    for key, value in environment(tmp_path).items():
        if key.startswith("MYNOTES_") or key == "HOME":
            monkeypatch.setenv(key, value)
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    workspace = Workspace.from_value(tmp_path / "workspace")
    workspace.initialize()
    config = workspace.configuration_store().load()
    course = course_template()
    course.update(code=COURSE, name="Synthetic Systems")
    config["courses"] = [course]
    config["paths"]["recording_dirs"] = [str(recordings)]
    workspace.configuration_store().save(config)
    store = capture.CaptureStore(workspace)
    capture.configure(store, {})
    capture.import_schedule(store, [{"courseCode": COURSE, "lessonNumber": n, "title": f"Lesson {n}",
                                     "startsAt": f"2030-03-0{n}T09:00:00Z", "endsAt": f"2030-03-0{n}T10:30:00Z"}
                                    for n in (1, 2, 3)])
    for number in (1, 2):
        path = recordings / f"{COURSE}-L{number:02d}.wav"
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(bytes(32000))
        os.utime(path, (1_700_000_000, 1_700_000_000))
    from mynotes.transcription_queue import configure
    configure(workspace, parallel=1)
    return workspace


def cli(env: dict, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "mynotes", *arguments], env=env, capture_output=True,
                          text=True, encoding="utf-8", timeout=120)


class Client:
    def __init__(self, workspace: Path, env: dict) -> None:
        self.process = subprocess.Popen([sys.executable, "-m", "mynotes", "serve", "--stdio", "--workspace", str(workspace)],
                                        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.responses: dict = {}
        self.notifications: list[dict] = []
        self.lines: list[bytes] = []
        self.condition = threading.Condition()
        self.next_id = 0
        self.stderr: list[bytes] = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=lambda: self.stderr.extend(self.process.stderr), daemon=True).start()

    def _read(self) -> None:
        for line in self.process.stdout:
            self.lines.append(line)
            message = json.loads(line)
            with self.condition:
                if "method" in message:
                    self.notifications.append(message)
                else:
                    self.responses[message.get("id")] = message
                self.condition.notify_all()

    def write(self, data: bytes) -> None:
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def send(self, method: str, params=None, *, identifier=None) -> object:
        if identifier is None:
            self.next_id += 1
            identifier = self.next_id
        message = {"jsonrpc": "2.0", "id": identifier, "method": method}
        if params is not None:
            message["params"] = params
        self.write((json.dumps(message) + "\n").encode())
        return identifier

    def response(self, identifier, timeout: float = 60) -> dict:
        deadline = time.monotonic() + timeout
        with self.condition:
            while identifier not in self.responses:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"no response to {identifier}")
                self.condition.wait(remaining)
            return self.responses.pop(identifier)

    def request(self, method: str, params=None, timeout: float = 60) -> dict:
        return self.response(self.send(method, params), timeout)

    def call(self, method: str, params=None, timeout: float = 60):
        message = self.request(method, params, timeout)
        assert "error" not in message, message
        return message["result"]

    def notification(self, method: str, timeout: float = 30, where=lambda params: True) -> dict:
        """The first matching notification, received before or after this call."""
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                for index, message in enumerate(self.notifications):
                    if message["method"] == method and where(message["params"]):
                        return self.notifications.pop(index)["params"]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"no {method} notification")
                self.condition.wait(remaining)

    def drain(self) -> list[dict]:
        with self.condition:
            messages, self.notifications = self.notifications, []
            return messages

    def close(self) -> int:
        if self.process.poll() is None:
            try:
                self.call("system.shutdown", timeout=10)
            except (AssertionError, TimeoutError, OSError, ValueError):
                pass
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            return self.process.wait(timeout=30)
        finally:
            if self.process.poll() is None:
                self.process.kill()
