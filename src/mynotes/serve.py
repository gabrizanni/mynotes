"""`mynotes serve --stdio`: JSON-RPC 2.0 for desktop apps, one long-lived process.

The protocol is documented in docs/SERVE.md. One UTF-8 JSON object per line,
16 MiB at most, no batches. The JSON-RPC streams are moved to private file
descriptors before anything else runs: stdin then reads /dev/null and stdout
writes to stderr, so a stray print or an inherited child process (an agent's
`--version` probe, a queue runner) can neither corrupt nor read the channel.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, BinaryIO

from . import __version__
from . import cli_invoke
from .serve_methods import (BY_NAME, INTERNAL_ERROR, INVALID_PARAMS, INVALID_REQUEST, METHOD_NOT_FOUND, METHODS,
                            PARSE_ERROR, PROTOCOL, PROTOCOL_MISMATCH, REQUEST_CANCELLED, SHUTTING_DOWN, RpcError,
                            build_argv, cli_result, describe, lane, remove, validate)
from .serve_jobs import JobManager
from .serve_watch import Lease, Watcher

MAX_MESSAGE_BYTES = 16 * 1024 * 1024
DOCTOR_TTL_SECONDS = 60.0
LOGIN_POLL_SECONDS = 3.0
CAPTURE_REQUESTS = {"capture.start", "capture.stop", "capture.test", "capture.recover"}

SERVER_METHODS: dict[str, dict] = {
    "system.hello": {"params": {"protocol": "integer"}, "summary": "Handshake: protocol, versions, methods."},
    "system.ping": {"params": {}, "summary": "Liveness check."},
    "system.stats": {"params": {}, "summary": "CPU, memory, counters of this server."},
    "system.shutdown": {"params": {}, "summary": "Answer, then exit; running jobs continue."},
    "session.open": {"params": {"visible": "boolean"}, "summary": "Start renewing the queue lease (window open)."},
    "session.close": {"params": {"release": "boolean"}, "summary": "Stop the lease; release marks the window closed."},
    "session.setVisible": {"params": {"visible": "boolean"}, "summary": "Watch every 1 s when visible, 5 s when hidden."},
    "watch.subscribe": {"params": {"topics": "string[]"}, "summary": "Current snapshots, then <topic>.changed notifications."},
    "watch.unsubscribe": {"params": {"topics": "string[]"}, "summary": "Stop notifications for topics."},
    "watch.snapshot": {"params": {"topic": "string", "fresh": "boolean"}, "summary": "Cached snapshot of one topic."},
    "job.list": {"params": {"limit": "integer"}, "summary": "Jobs of this workspace, newest first."},
    "job.get": {"params": {"jobId": "string"}, "summary": "One job's state and result."},
    "job.wait": {"params": {"jobId": "string", "timeoutSeconds": "number"}, "summary": "Return when the job ends or the timeout passes."},
    "job.cancel": {"params": {"jobId": "string", "force": "boolean"}, "summary": "Interrupt, terminate, then kill the job."},
}


def method_table() -> list[dict]:
    rows = [{"name": name, "lane": "server", "params": [{"name": key, "type": kind, "required": False}
             for key, kind in spec["params"].items()], "command": None, "summary": spec["summary"]}
            for name, spec in SERVER_METHODS.items()]
    return sorted(rows + [describe(method) for method in METHODS], key=lambda row: row["name"])


class Server:
    def __init__(self, workspace: str, rpc_in: BinaryIO, rpc_out: BinaryIO) -> None:
        self.workspace = workspace
        self.rpc_in, self.rpc_out = rpc_in, rpc_out
        self.send_lock = threading.Lock()
        self.pending: dict[Any, Future] = {}
        self.pending_lock = threading.Lock()
        self.lanes = {"fast": ThreadPoolExecutor(4, thread_name_prefix="serve-fast"),
                      "slow": ThreadPoolExecutor(2, thread_name_prefix="serve-slow"),
                      "job": ThreadPoolExecutor(2, thread_name_prefix="serve-job"),
                      # job.wait blocks; it must never hold a fast-lane thread.
                      "wait": ThreadPoolExecutor(8, thread_name_prefix="serve-wait")}
        self.jobs = JobManager(workspace, self.notify)
        self.watcher = Watcher(workspace, self.notify)
        self.lease = Lease(workspace, self._count_spawn)
        self.started = time.monotonic()
        self.counters = {"requests": 0, "notifications": 0, "runnerSpawns": 0}
        self.counter_lock = threading.Lock()
        self.doctor_cache: dict[str, tuple[float, dict]] = {}
        self.doctor_locks: dict[str, threading.Lock] = {}
        self.shutting_down = threading.Event()

    # Output --------------------------------------------------------------
    def send(self, message: dict) -> None:
        data = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self.send_lock:
            try:
                self.rpc_out.write(data)
                self.rpc_out.flush()
            except (BrokenPipeError, OSError, ValueError):
                self.shutting_down.set()

    def notify(self, method: str, params: dict) -> None:
        with self.counter_lock:
            self.counters["notifications"] += 1
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def _count_spawn(self) -> None:
        with self.counter_lock:
            self.counters["runnerSpawns"] += 1

    def _error(self, identifier: Any, error: RpcError) -> None:
        self.send({"jsonrpc": "2.0", "id": identifier, "error": error.payload()})

    # Input ---------------------------------------------------------------
    def _read_line(self) -> bytes | None:
        line = self.rpc_in.readline(MAX_MESSAGE_BYTES + 1)
        if not line:
            return None
        if len(line) > MAX_MESSAGE_BYTES and not line.endswith(b"\n"):
            while line and not line.endswith(b"\n"):
                line = self.rpc_in.readline(MAX_MESSAGE_BYTES)
            self._error(None, RpcError(INVALID_REQUEST, "Message larger than 16 MiB"))
            return b""
        return line

    def serve_forever(self) -> int:
        try:
            while not self.shutting_down.is_set():
                line = self._read_line()
                if line is None:
                    break
                if line.strip():
                    self.handle(line)
        finally:
            self.close()
        return 0

    def handle(self, line: bytes) -> None:
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._error(None, RpcError(PARSE_ERROR, "Invalid JSON"))
            return
        if isinstance(message, list):
            self._error(None, RpcError(INVALID_REQUEST, "Batch requests are not supported"))
            return
        identifier = message.get("id") if isinstance(message, dict) else None
        if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str)
                or not isinstance(identifier, (str, int, type(None))) or isinstance(identifier, bool)):
            self._error(identifier if isinstance(identifier, (str, int)) else None, RpcError(INVALID_REQUEST, "Invalid request"))
            return
        method, params = message["method"], message.get("params", {})
        if "id" not in message:
            if method == "$/cancelRequest" and isinstance(params, dict):
                self._cancel(params.get("id"))
            return  # Other client notifications are ignored.
        if identifier is None:
            self._error(None, RpcError(INVALID_REQUEST, "Request id must be a string or an integer"))
            return
        with self.counter_lock:
            self.counters["requests"] += 1
        if not method.startswith("system."):
            self.watcher.activity()  # A user action usually changes files soon.
        if method == "system.shutdown":
            self.send({"jsonrpc": "2.0", "id": identifier, "result": {"ok": True, "jobsRunning": self.jobs.running()}})
            self.shutting_down.set()
            return
        if self.shutting_down.is_set():
            self._error(identifier, RpcError(SHUTTING_DOWN, "The server is shutting down"))
            return
        try:
            lane_name = self._lane(method, params)
        except RpcError as error:
            self._error(identifier, error)
            return
        with self.pending_lock:
            if identifier in self.pending:
                self._error(identifier, RpcError(INVALID_REQUEST, "Duplicate request id"))
                return
            future = self.lanes[lane_name].submit(self._run, identifier, method, params)
            self.pending[identifier] = future
        future.add_done_callback(lambda done, key=identifier: self._done(key, done))

    def _lane(self, method: str, params: Any) -> str:
        if method in SERVER_METHODS:
            return "wait" if method == "job.wait" else "fast"
        spec = BY_NAME.get(method)
        if spec is None:
            raise RpcError(METHOD_NOT_FOUND, f"Unknown method: {method}")
        if not isinstance(params, dict) and params is not None:
            raise RpcError(INVALID_PARAMS, "Parameters must be a JSON object")
        return lane(spec, params or {})

    def _cancel(self, identifier: Any) -> None:
        with self.pending_lock:
            future = self.pending.get(identifier)
        if future is not None:
            future.cancel()  # Only calls that have not started yet can be cancelled.

    def _done(self, identifier: Any, future: Future) -> None:
        with self.pending_lock:
            self.pending.pop(identifier, None)
        if future.cancelled():
            self._error(identifier, RpcError(REQUEST_CANCELLED, "Request cancelled before it started"))

    def _run(self, identifier: Any, method: str, params: Any) -> None:
        try:
            result = self.dispatch(method, params)
        except RpcError as error:
            self._error(identifier, error)
            return
        except Exception as error:
            self._error(identifier, RpcError(INTERNAL_ERROR, f"{type(error).__name__}: {error}"))
            return
        self.send({"jsonrpc": "2.0", "id": identifier, "result": result})

    # Dispatch ------------------------------------------------------------
    def dispatch(self, method: str, params: Any) -> Any:
        if method in SERVER_METHODS:
            params = params or {}
            if not isinstance(params, dict):
                raise RpcError(INVALID_PARAMS, "Parameters must be a JSON object")
            return getattr(self, "m_" + method.replace(".", "_"))(params)
        spec = BY_NAME[method]
        params = validate(spec, params)
        if lane(spec, params) == "job":
            return self.start_job(spec, params)
        if method == "doctor.get":
            return self.doctor(spec, params)
        if spec.direct is not None and not (spec.slow_if and params.get(spec.slow_if)):
            try:
                return {"exitCode": 0, "data": json.loads(json.dumps(spec.direct(self.workspace, params), ensure_ascii=False))}
            except Exception:
                pass  # The command reports this failure exactly as the CLI does.
        result = self.run_command(spec, params)
        if method in CAPTURE_REQUESTS and isinstance(result.get("data"), dict):
            data = result["data"]
            if data.get("status") == "PENDING" and isinstance(data.get("requestID"), str):
                self.watcher.track_request(data["requestID"])
        return result

    def run_command(self, spec, params: dict) -> dict:
        from .workspace import Workspace
        argv, files = build_argv(spec, params, self.workspace)
        try:
            invocation = cli_invoke.invoke(argv, serialize=not Workspace.from_value(self.workspace).is_initialized())
        finally:
            remove(files)
        return cli_result(invocation)

    def doctor(self, spec, params: dict) -> dict:
        key = params.get("provider") or ""
        lock = self.doctor_locks.setdefault(key, threading.Lock())
        with lock:  # One probe per provider at a time; the others reuse it.
            cached = self.doctor_cache.get(key)
            if cached and not params.get("refresh") and time.monotonic() - cached[0] < DOCTOR_TTL_SECONDS:
                return {**cached[1], "cached": True}
            result = self.run_command(spec, params)
            self.doctor_cache[key] = (time.monotonic(), result)
            return {**result, "cached": False}

    def start_job(self, spec, params: dict) -> dict:
        argv, files = build_argv(spec, params, self.workspace)
        job = self.jobs.start(spec.name, argv, params, request_files=files, progress_path=self._asr_status(spec, params))
        if spec.name == "provider.login":
            threading.Thread(target=self._watch_login, args=(job["id"], params["provider"]), daemon=True).start()
        return {"jobId": job["id"], "job": job}

    def _asr_status(self, spec, params: dict) -> Path | None:
        if spec.progress != "asr":
            return None
        root = Path(self.workspace).expanduser()
        if params.get("lectureId"):
            return root / "runs" / params["lectureId"].upper() / "asr" / "status.json"
        try:
            from .transcription_queue import asr_directory, library
            from .workspace import Workspace
            item = next(item for item in library(Workspace.from_value(self.workspace)) if item["id"] == params["recordingId"])
            return asr_directory(Workspace.from_value(self.workspace), item) / "status.json"
        except Exception:
            return None  # Progress then comes only from the job's own output.

    def _watch_login(self, job_id: str, provider: str) -> None:
        from .onboarding import provider_check
        previous = None
        job = self.jobs.get(job_id)
        while not job.done.is_set() and not self.shutting_down.is_set():
            try:
                state = provider_check(provider)
            except Exception as error:
                state = {"status": "unverified", "error": str(error)}
            if state.get("status") != previous:
                previous = state.get("status")
                self.notify("provider.authChanged", {"provider": provider, "jobId": job_id, "status": state})
            job.done.wait(LOGIN_POLL_SECONDS)

    # Server methods --------------------------------------------------------
    def m_system_hello(self, params: dict) -> dict:
        requested = params.get("protocol")
        if requested is not None and requested != PROTOCOL:
            raise RpcError(PROTOCOL_MISMATCH, f"Protocol {requested} is not supported", {"supported": [PROTOCOL]})
        return {"protocol": PROTOCOL, "server": "mynotes", "version": __version__,
                "python": sys.version.split()[0], "platform": sys.platform, "pid": os.getpid(),
                "workspace": str(Path(self.workspace).expanduser()), "methods": [row["name"] for row in method_table()],
                "topics": sorted(self.watcher.topics), "limits": {"maxMessageBytes": MAX_MESSAGE_BYTES}}

    def m_system_ping(self, params: dict) -> dict:
        return {"ok": True, "uptimeSeconds": time.monotonic() - self.started}

    def m_system_stats(self, params: dict) -> dict:
        times = os.times()
        with self.counter_lock:
            counters = dict(self.counters)
        return {"pid": os.getpid(), "uptimeSeconds": time.monotonic() - self.started,
                "cpuSeconds": times.user + times.system, "childCpuSeconds": times.children_user + times.children_system,
                "rssBytes": _rss_bytes(), "threads": threading.active_count(), **counters,
                "jobsStarted": self.jobs.started, "jobsRunning": self.jobs.running(),
                "spawns": self.jobs.started + counters["runnerSpawns"],
                "topicComputations": {name: topic.computations for name, topic in self.watcher.topics.items()}}

    def m_session_open(self, params: dict) -> dict:
        session = self.lease.open()
        self.watcher.set_visible(params.get("visible", True) is not False)
        return {"sessionId": session}

    def m_session_close(self, params: dict) -> dict:
        self.lease.close(release=params.get("release", True) is not False)
        return {"ok": True}

    def m_session_setVisible(self, params: dict) -> dict:
        if not isinstance(params.get("visible"), bool):
            raise RpcError(INVALID_PARAMS, "visible must be a boolean")
        self.watcher.set_visible(params["visible"])
        return {"visible": params["visible"]}

    def _topics(self, params: dict) -> list[str]:
        topics = params.get("topics")
        if not isinstance(topics, list) or not all(isinstance(topic, str) for topic in topics):
            raise RpcError(INVALID_PARAMS, "topics must be a list of strings", {"topics": sorted(self.watcher.topics)})
        return topics

    def m_watch_subscribe(self, params: dict) -> dict:
        return self.watcher.subscribe(self._topics(params))

    def m_watch_unsubscribe(self, params: dict) -> dict:
        return self.watcher.unsubscribe(self._topics(params))

    def m_watch_snapshot(self, params: dict) -> dict:
        topic = params.get("topic")
        if topic not in self.watcher.topics:
            raise RpcError(INVALID_PARAMS, "Unknown topic", {"topics": sorted(self.watcher.topics)})
        return self.watcher.snapshot(topic, force=params.get("fresh") is True)

    def m_job_list(self, params: dict) -> dict:
        limit = params.get("limit", 50)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise RpcError(INVALID_PARAMS, "limit must be an integer from 1 to 500")
        return {"jobs": self.jobs.list(limit=limit)}

    def _job_id(self, params: dict) -> str:
        if not isinstance(params.get("jobId"), str):
            raise RpcError(INVALID_PARAMS, "jobId must be a string")
        return params["jobId"]

    def m_job_get(self, params: dict) -> dict:
        return self.jobs.get(self._job_id(params)).public()

    def m_job_wait(self, params: dict) -> dict:
        timeout = params.get("timeoutSeconds", 30)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 <= timeout <= 300:
            raise RpcError(INVALID_PARAMS, "timeoutSeconds must be a number from 0 to 300")
        return self.jobs.wait(self._job_id(params), timeout)

    def m_job_cancel(self, params: dict) -> dict:
        return self.jobs.cancel(self._job_id(params), force=params.get("force") is True)

    # Shutdown --------------------------------------------------------------
    def close(self) -> None:
        self.shutting_down.set()
        with self.pending_lock:
            for future in list(self.pending.values()):
                future.cancel()
        for executor in self.lanes.values():
            executor.shutdown(wait=True, cancel_futures=True)
        self.lease.close(release=False)  # The lease lapses within 15 s, as after a crash.
        self.watcher.close()
        self.jobs.close()


def _rss_bytes() -> int | None:
    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            return int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        pass
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak if sys.platform == "darwin" else peak * 1024
    except (ImportError, OSError):
        return None


def protect_channel() -> tuple[BinaryIO, BinaryIO]:
    """Move JSON-RPC to private descriptors; fd 0 reads /dev/null, fd 1 writes to stderr."""
    sys.stdout.flush()
    rpc_in = os.fdopen(os.dup(0), "rb")
    rpc_out = os.fdopen(os.dup(1), "wb", buffering=0)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    cli_invoke.install()
    return rpc_in, rpc_out


def run_cli(arguments) -> int:
    if arguments.list_methods:
        table = method_table()
        if arguments.json:
            print(json.dumps({"protocol": PROTOCOL, "methods": table}, ensure_ascii=False, indent=2))
        else:
            for row in table:
                print(f"{row['name']:<28} {row['lane']:<6} {row['summary']}")
        return 0
    if not arguments.stdio:
        raise ValueError("Use mynotes serve --stdio, or --list-methods")
    from .workspace import Workspace
    workspace = str(Workspace.from_value(arguments.workspace).root)
    rpc_in, rpc_out = protect_channel()
    server = Server(workspace, rpc_in, rpc_out)
    server.jobs.reattach()
    return server.serve_forever()
