"""Change notifications and the window lease for `mynotes serve`, instead of polling commands.

Watcher: every second while the window is visible (five when hidden) it
compares the modification time, size and inode of each topic's files and
directories. MyNotes writes JSON atomically (temporary file, then rename), so
a directory's own signature also changes on every write inside it. The queue
database is compared by SQLite's `data_version`, which changes only when
another connection commits data, and the window lease by its session: the
lease renewal every 5 s rewrites both without changing any answer. A topic is
recomputed only when its signature changes, when its time-dependent fields may
have aged (`refresh` seconds), or every 2 s while queue jobs run. A
notification is sent only when the snapshot's hash changes. After 30 s
without a change, a running job, a pending capture request or a client
request, a visible window is checked every 2 s instead of every second.

Cache: the last snapshot per topic is reused by watch.snapshot while its
signature is unchanged and it is younger than the topic's refresh interval.
One thread computes a topic at a time; the others wait for its result.

Lease: while a session is open, `ui_lease` runs every 5 s, the same 15 s
lease the native apps renew with `transcription-queue heartbeat`, followed by
`launch_runner`, which starts a runner only when it has work and none runs.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VISIBLE_SECONDS, HIDDEN_SECONDS = 1.0, 5.0
QUIET_SECONDS, QUIET_AFTER_SECONDS = 2.0, 30.0  # Visible, but nothing changed for 30 s.
LEASE_SECONDS = 5.0
REQUEST_EXPIRY_SECONDS = 70.0  # Capture requests expire after 60 s.


def _workspace(path: str):
    from .workspace import Workspace
    return Workspace.from_value(path)


def compute_capture(path: str) -> Any:
    from .serve_methods import _direct_dashboard
    return _direct_dashboard(path, {})


def compute_queue(path: str) -> Any:
    from .serve_methods import _direct_queue_status
    return _direct_queue_status(path, {})


def compute_library(path: str) -> Any:
    from .serve_methods import _direct_library
    return _direct_library(path, {})


def compute_config(path: str) -> Any:
    return _workspace(path).configuration_store().load()


def _queue_files(root: Path) -> list[Path]:
    # Not the folder itself, nor ui.json or the database files: the lease
    # rewrites those every 5 s. Watcher._signature adds what they mean.
    queue = root / "state" / "transcription-queue"
    return [queue / "settings.json", queue / "wait.json", queue / "labels.json", queue / "closed"]


def capture_paths(root: Path, _extra: list[Path]) -> list[Path]:
    capture = root / "state" / "capture"
    return [root / "config" / "capture.json", root / "config" / "settings.json", capture,
            *(capture / name for name in ("schedule.json", "source-events.json", "runtime.json", "sync.json", "overrides.json")),
            capture / "responses", capture / "requests"]


def queue_paths(root: Path, _extra: list[Path]) -> list[Path]:
    return [*_queue_files(root), root / "state" / "capture", root / "state" / "recording-library",
            root / "config" / "settings.json"]


def library_paths(root: Path, extra: list[Path]) -> list[Path]:
    return [root / "config" / "settings.json", root / "config" / "capture.json", root / "state" / "capture",
            root / "state" / "recording-library", *_queue_files(root), *extra]


def config_paths(root: Path, _extra: list[Path]) -> list[Path]:
    return [root / "config" / "settings.json"]


@dataclass
class Topic:
    name: str
    compute: Callable[[str], Any]
    paths: Callable[[Path, list[Path]], list[Path]]
    refresh: float | None  # seconds before time-dependent fields are recomputed
    lock: threading.Lock = field(default_factory=threading.Lock)
    signature: tuple | None = None
    computed_at: float = 0.0
    data: Any = None
    error: dict | None = None
    digest: str | None = None
    computations: int = 0
    path_key: tuple | None = None
    path_strings: list[str] = field(default_factory=list)  # Built once: pathlib costs more than stat.


def topics() -> dict[str, Topic]:
    return {topic.name: topic for topic in (
        Topic("capture", compute_capture, capture_paths, 10.0),
        Topic("queue", compute_queue, queue_paths, 10.0),
        Topic("library", compute_library, library_paths, 30.0),
        Topic("config", compute_config, config_paths, None),
    )}


def signature(paths: list) -> tuple:
    values = []
    for path in paths:
        try:
            stat = os.stat(path)
            values.append((stat.st_mtime_ns, stat.st_size, stat.st_ino))
        except OSError:
            values.append(None)
    return tuple(values)


def digest(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class Watcher:
    def __init__(self, workspace: str, notify: Callable[[str, dict], None]) -> None:
        self.workspace = workspace
        self.root = Path(workspace).expanduser()
        self.notify = notify
        self.topics = topics()
        self.subscribed: set[str] = set()
        self.requests: dict[str, float] = {}
        self.visible = True
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None
        self._library_dirs: tuple[tuple, list[Path]] = ((), [])
        self._library_config = [str(self.root / "config" / "settings.json"), str(self.root / "config" / "capture.json"),
                                str(self.root / "state" / "recording-library")]
        self.quiet_since = time.monotonic()
        self._database: sqlite3.Connection | None = None
        self._database_inode: int | None = None
        self._database_lock = threading.Lock()

    # Snapshots -------------------------------------------------------------
    def _extra_paths(self, topic: Topic) -> list[Path]:
        if topic.name != "library":
            return []
        config = signature(self._library_config)
        if config != self._library_dirs[0]:
            self._library_dirs = (config, self._recording_dirs())
        return self._library_dirs[1]

    def _recording_dirs(self) -> list[Path]:
        """Folders whose entries change when recordings or transcripts appear."""
        try:
            from .capture import CaptureStore
            from .recording_library import folder
            workspace = _workspace(self.workspace)
            settings = workspace.configuration_store().load()
            dirs = {Path(p).expanduser() for p in settings["paths"]["recording_dirs"]}
            dirs.add(Path(CaptureStore(workspace).config()["outputDirectory"]))
            managed = folder(workspace)
            dirs.update({managed, managed / "Trascrizioni"})
            return sorted(dirs)
        except Exception:
            return []

    def _queue_version(self) -> tuple | None:
        """Changes only when another connection commits data to the queue database."""
        path = self.root / "state" / "transcription-queue" / "queue.sqlite3"
        with self._database_lock:
            try:
                inode = os.stat(path).st_ino
                if self._database is None or self._database_inode != inode:
                    self._close_database()
                    self._database = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                                                     timeout=1, check_same_thread=False)
                    self._database_inode = inode
                return inode, self._database.execute("PRAGMA data_version").fetchone()[0]
            except (OSError, sqlite3.Error):
                self._close_database()
                return ("unavailable", time.monotonic())  # Unknown: recompute.

    def _close_database(self) -> None:
        if self._database is not None:
            self._database.close()
        self._database = self._database_inode = None

    def _lease_identity(self) -> tuple | None:
        try:
            lease = json.loads((self.root / "state" / "transcription-queue" / "ui.json").read_text(encoding="utf-8"))
            return lease.get("session"), lease.get("closed")
        except (OSError, ValueError, AttributeError):
            return None

    def _signature(self, topic: Topic) -> tuple:
        extra = self._extra_paths(topic)
        if topic.path_key != tuple(extra) or not topic.path_strings:
            topic.path_strings = [os.fspath(path) for path in topic.paths(self.root, extra)]
            topic.path_key = tuple(extra)
        parts: tuple = (signature(topic.path_strings),)
        if topic.name in {"queue", "library"}:
            parts += (self._queue_version(),)
        if topic.name == "queue":
            parts += (self._lease_identity(),)  # Expiry is time-dependent: covered by `refresh`.
        return parts

    def _busy(self, topic: Topic) -> bool:
        if topic.name != "queue" or not isinstance(topic.data, dict):
            return False
        return any(job.get("status") in {"RUNNING", "CANCELLING"} for job in topic.data.get("jobs", []))

    def snapshot(self, name: str, *, force: bool = False) -> dict:
        topic = self.topics[name]
        with topic.lock:
            current = self._signature(topic)
            age = time.monotonic() - topic.computed_at
            limit = 2.0 if self._busy(topic) else topic.refresh
            fresh = topic.signature == current and (limit is None or age < limit)
            if force or not fresh or (topic.data is None and topic.error is None):
                self._compute(topic, current)
            return self._payload(topic)

    def _compute(self, topic: Topic, current: tuple) -> None:
        topic.computations += 1
        try:
            data = json.loads(json.dumps(topic.compute(self.workspace), ensure_ascii=False))
            topic.data, topic.error = data, None
            topic.digest = digest(data)
        except Exception as error:  # Reported to the client; the CLI would print `mynotes: ...`.
            topic.data, topic.error = None, {"code": -32001, "message": str(error), "type": type(error).__name__}
            topic.digest = digest(topic.error)
        topic.signature, topic.computed_at = current, time.monotonic()

    @staticmethod
    def _payload(topic: Topic) -> dict:
        return {"topic": topic.name, "hash": topic.digest, "data": topic.data,
                **({"error": topic.error} if topic.error else {})}

    # Subscriptions -----------------------------------------------------------
    def subscribe(self, names: list[str]) -> dict:
        unknown = sorted(set(names) - set(self.topics))
        if unknown:
            from .serve_methods import INVALID_PARAMS, RpcError
            raise RpcError(INVALID_PARAMS, f"Unknown topic: {', '.join(unknown)}", {"topics": sorted(self.topics)})
        snapshots = {name: self.snapshot(name) for name in names}
        with self.lock:
            self.subscribed.update(names)
        self._ensure_thread()
        return {"topics": sorted(self.subscribed), "snapshots": snapshots}

    def unsubscribe(self, names: list[str]) -> dict:
        with self.lock:
            self.subscribed.difference_update(names)
            return {"topics": sorted(self.subscribed)}

    def set_visible(self, visible: bool) -> None:
        self.visible = visible
        self.activity()
        self.wake.set()

    def activity(self) -> None:
        """A client request: return to checking every second."""
        self.quiet_since = time.monotonic()

    def track_request(self, request_id: str) -> None:
        with self.lock:
            self.requests[request_id] = time.monotonic() + REQUEST_EXPIRY_SECONDS
        self._ensure_thread()
        self.wake.set()

    # Loop --------------------------------------------------------------------
    def _ensure_thread(self) -> None:
        with self.lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="serve-watch", daemon=True)
                self.thread.start()

    def close(self) -> None:
        self.stopping.set()
        self.wake.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
        with self._database_lock:
            self._close_database()

    def _run(self) -> None:
        while not self.stopping.is_set():
            with self.lock:
                names = sorted(self.subscribed)
            for name in names:
                topic = self.topics[name]
                before = topic.digest
                try:
                    payload = self.snapshot(name)
                except Exception as error:  # Never let one topic stop the others.
                    print(f"mynotes serve: watch {name}: {error}", file=sys.stderr)
                    continue
                if payload["hash"] != before:
                    self.notify(f"{name}.changed", payload)
                    self.activity()
            self._check_requests()
            self.wake.wait(self._interval())
            self.wake.clear()

    def _interval(self) -> float:
        if not self.visible:
            return HIDDEN_SECONDS
        with self.lock:
            pending = bool(self.requests)
        if pending or self._busy(self.topics["queue"]):
            self.activity()
        return QUIET_SECONDS if time.monotonic() - self.quiet_since >= QUIET_AFTER_SECONDS else VISIBLE_SECONDS

    def _check_requests(self) -> None:
        with self.lock:
            pending = dict(self.requests)
        responses = self.root / "state" / "capture" / "responses"
        for request_id, deadline in pending.items():
            path = responses / f"{request_id}.json"
            response = None
            try:
                response = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            if response is None and time.monotonic() < deadline:
                continue
            with self.lock:
                self.requests.pop(request_id, None)
            self.notify("capture.requestCompleted", {"requestId": request_id, "response": response,
                                                     "expired": response is None})


class Lease:
    """The open window's queue lease, renewed every 5 s while a session is open."""

    def __init__(self, workspace: str, on_spawn: Callable[[], None]) -> None:
        self.workspace = workspace
        self.on_spawn = on_spawn
        self.session: str | None = None
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.runners: list = []
        self.lock = threading.Lock()

    def open(self) -> str:
        with self.lock:
            if self.session is None:
                self.session = str(uuid.uuid4())  # A new session after every restart or close.
                self.stop = threading.Event()
                self.thread = threading.Thread(target=self._run, args=(self.session, self.stop),
                                               name="serve-lease", daemon=True)
                self.thread.start()
            return self.session

    def close(self, *, release: bool = True) -> None:
        """Stop renewing; `release` also tells the queue the window closed, like `close`."""
        with self.lock:
            session, self.session = self.session, None
            self.stop.set()
        if session and release:
            try:
                from .transcription_queue import ui_lease
                workspace = _workspace(self.workspace)
                if workspace.is_initialized():
                    ui_lease(workspace, session, opened=False)
            except Exception as error:
                print(f"mynotes serve: lease close: {error}", file=sys.stderr)

    def _run(self, session: str, stop: threading.Event) -> None:
        from . import transcription_queue as queue
        while not stop.is_set():
            try:
                workspace = _workspace(self.workspace)
                if workspace.is_initialized():
                    if queue.ui_lease(workspace, session, opened=True).get("status") == "CLOSED":
                        return
                    runner = queue.launch_runner(workspace)  # None when idle or already running.
                    if runner is not None:
                        self.runners.append(runner)
                        self.on_spawn()
                self.runners = [runner for runner in self.runners if runner.poll() is None]  # Reap.
            except Exception as error:
                print(f"mynotes serve: lease: {error}", file=sys.stderr)
            stop.wait(LEASE_SECONDS)
