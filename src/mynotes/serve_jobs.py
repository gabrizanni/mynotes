"""Long `mynotes serve` methods as separate processes that survive a server restart.

Each job is `python -I -X utf8 -m mynotes.rpc_job --record R -- <cli args>`
in its own session (Unix) or Windows job object. Its stdout and stderr go to
files beside the record in `state/desktop-jobs/`, never to pipes, so a job
keeps running when the server stops. The server tails the stderr file:
JSON lines with a "stage" become `job.progress`, other lines `job.output`.
After the job exits, its stdout is the result, mapped like any CLI call.

A restarted server reads the records: a job whose lock is still held is
watched again; one that ended while no server watched it is finalized from
its exit file; one that died without an exit file is INTERRUPTED, with a
hint for resuming it.
"""
from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import file_lock
from .cli_invoke import Invocation
from .serve_methods import JOB_NOT_FOUND, RpcError, cli_result, remove
from .utils import atomic_json_write

FINAL = {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}
CANCEL_STEP_SECONDS = 10.0
POLL_SECONDS = 0.25
ASR_POLL_SECONDS = 2.0
STARTUP_GRACE_SECONDS = 30.0


def lock_held(path: Path) -> bool:
    """True while another process holds the job's exclusive lock."""
    if not path.is_file():
        return False
    try:
        with path.open("a+") as handle:
            try:
                file_lock.flock(handle, file_lock.LOCK_EX | file_lock.LOCK_NB)
            except BlockingIOError:
                return True
            file_lock.flock(handle, file_lock.LOCK_UN)
            return False
    except OSError:
        return False


class Job:
    def __init__(self, directory: Path, record: dict) -> None:
        self.record = record
        self.base = directory / record["id"]
        self.path = self.base.with_suffix(".json")
        self.process: subprocess.Popen | None = None
        self.windows_job = None
        self.offset = 0
        self.done = threading.Event()
        self.lock = threading.Lock()

    def sibling(self, suffix: str) -> Path:
        return self.path.with_name(self.path.name + suffix)

    @property
    def id(self) -> str:
        return self.record["id"]

    @property
    def state(self) -> str:
        return self.record["state"]

    def save(self) -> None:
        atomic_json_write(self.path, self.record)

    def pid(self) -> int | None:
        try:
            return int(self.sibling(".pid").read_text())
        except (OSError, ValueError):
            return self.record.get("pid")

    def alive(self) -> bool:
        if self.process is not None:
            return self.process.poll() is None
        if lock_held(self.sibling(".lock")):
            return True
        # Not locked yet: the job takes its lock first, then writes its PID.
        starting = not self.sibling(".pid").exists() and not self.sibling(".exit").exists()
        if not starting or time.time() - self.record.get("startedAt", 0) >= STARTUP_GRACE_SECONDS:
            return False
        from .process_status import probe_process
        try:
            probe_process(int(self.record.get("pid") or 0))
            return True
        except PermissionError:
            return True
        except (ProcessLookupError, OSError, ValueError):
            return False

    def exit_code(self) -> int | None:
        try:
            return int(self.sibling(".exit").read_text())
        except (OSError, ValueError):
            if self.process is not None and self.process.poll() is not None:
                return self.process.returncode
            return None

    def public(self) -> dict:
        keys = ("id", "method", "params", "state", "pid", "startedAt", "finishedAt", "exitCode", "result", "error",
                "lastProgress", "cancelRequested", "resumeHint")
        return {key: self.record.get(key) for key in keys if key in self.record}


class JobManager:
    def __init__(self, workspace: str, notify: Callable[[str, dict], None], *, python: str | None = None) -> None:
        self.workspace = workspace
        self.directory = Path(workspace).expanduser() / "state" / "desktop-jobs"
        self.notify = notify
        self.python = python or sys.executable
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.started = 0
        self.closing = threading.Event()

    # Lifecycle -----------------------------------------------------------
    def reattach(self) -> None:
        if not self.directory.is_dir():
            return
        for path in sorted(self.directory.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(record, dict) or record.get("id") != path.stem:
                continue
            job = Job(self.directory, record)
            with self.lock:
                self.jobs[job.id] = job
            if job.state in FINAL:
                job.done.set()
                continue
            stderr = job.sibling(".err")
            job.offset = stderr.stat().st_size if stderr.is_file() else 0
            self._scan_history(job)
            if job.alive():
                threading.Thread(target=self._monitor, args=(job,), name=f"job-{job.id}", daemon=True).start()
            else:
                self._finish(job)  # Ended while no server watched it.

    def close(self) -> None:
        """Stop watching; running jobs continue and are reattached later."""
        self.closing.set()

    def start(self, method: str, argv: list[str], params: dict, *, request_files: list[Path] = (),
              progress_path: Path | None = None) -> dict:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        identifier = f"job-{secrets.token_hex(8)}"
        record = {"schemaVersion": 1, "id": identifier, "method": method, "params": params, "argv": argv,
                  "state": "RUNNING", "startedAt": time.time(), "requestFiles": [str(p) for p in request_files],
                  "progressPath": str(progress_path) if progress_path else None, "cancelRequested": False}
        job = Job(self.directory, record)
        command = [self.python, "-I", "-X", "utf8", "-m", "mynotes.rpc_job", "--record", str(job.path), "--", *argv]
        try:
            with open(job.sibling(".out"), "wb") as stdout, open(job.sibling(".err"), "wb") as stderr:
                options = dict(stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, cwd=str(Path(self.workspace).expanduser()))
                if os.name == "nt":
                    from .provider_process import WindowsJob
                    job.windows_job = WindowsJob()
                    job.process = job.windows_job.start(command, **options)
                else:
                    job.process = subprocess.Popen(command, start_new_session=True, **options)
        except BaseException:
            remove(list(request_files))
            if job.windows_job is not None:
                job.windows_job.close()
            raise
        record["pid"] = job.process.pid
        job.save()
        with self.lock:
            self.jobs[identifier] = job
            self.started += 1
        self.notify("job.started", {"jobId": identifier, "method": method, "params": params, "pid": job.process.pid})
        threading.Thread(target=self._monitor, args=(job,), name=f"job-{identifier}", daemon=True).start()
        return job.public()

    # Queries -------------------------------------------------------------
    def get(self, identifier: str) -> Job:
        with self.lock:
            job = self.jobs.get(identifier)
        if job is None:
            raise RpcError(JOB_NOT_FOUND, f"Job not found: {identifier}")
        return job

    def list(self, *, limit: int = 50) -> list[dict]:
        with self.lock:
            jobs = list(self.jobs.values())
        jobs.sort(key=lambda job: job.record.get("startedAt", 0), reverse=True)
        return [job.public() for job in jobs[:limit]]

    def running(self) -> int:
        with self.lock:
            return sum(job.state == "RUNNING" for job in self.jobs.values())

    def wait(self, identifier: str, timeout: float | None = None) -> dict:
        job = self.get(identifier)
        job.done.wait(timeout)
        return job.public()

    # Cancellation --------------------------------------------------------
    def cancel(self, identifier: str, *, force: bool = False) -> dict:
        job = self.get(identifier)
        with job.lock:
            if job.state in FINAL:
                return job.public()
            first = not job.record.get("cancelRequested")
            job.record["cancelRequested"] = True
            job.save()
        if first or force:
            threading.Thread(target=self._cancel_ladder, args=(job, force), daemon=True).start()
        return job.public()

    def _cancel_ladder(self, job: Job, force: bool) -> None:
        """Interrupt, wait 10 s, terminate, wait 10 s, then kill the process tree."""
        steps = () if force else (self._interrupt, self._terminate)
        for step in steps:
            if not job.alive():
                return
            step(job)
            if job.done.wait(CANCEL_STEP_SECONDS):
                return
        if job.alive():
            self._kill_tree(job)

    def _signal(self, job: Job, number: int) -> None:
        pid = job.pid()
        if pid and job.alive():
            try:
                os.kill(pid, number)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    def _interrupt(self, job: Job) -> None:
        if os.name == "nt":
            job.sibling(".cancel").touch()
        else:
            self._signal(job, signal.SIGINT)

    def _terminate(self, job: Job) -> None:
        if os.name != "nt":
            self._signal(job, signal.SIGTERM)

    def _kill_tree(self, job: Job) -> None:
        if os.name == "nt":
            if job.windows_job is not None:
                job.windows_job.close()
            else:
                self._signal(job, signal.SIGTERM)  # TerminateProcess
            return
        pid = job.pid()
        if not pid or not job.alive():
            return
        try:
            if os.getpgid(pid) == pid:  # The job leads its own session and group.
                os.killpg(pid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    # Monitoring ----------------------------------------------------------
    def _scan_history(self, job: Job) -> None:
        """After a restart, recover the last stage and batch ID seen so far."""
        try:
            text = job.sibling(".err").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for line in text.splitlines():
            self._progress_value(job, line)

    def _progress_value(self, job: Job, line: str) -> dict | None:
        try:
            value = json.loads(line)
        except ValueError:
            return None
        if not isinstance(value, dict) or not isinstance(value.get("stage"), str):
            return None
        job.record["lastProgress"] = value
        if isinstance(value.get("batch_id"), str):
            job.record["batchId"] = value["batch_id"]
        return value

    def _read_lines(self, job: Job) -> list[str]:
        path = job.sibling(".err")
        try:
            with path.open("rb") as handle:
                handle.seek(job.offset)
                data = handle.read()
        except OSError:
            return []
        end = data.rfind(b"\n")
        if end < 0:
            return []
        job.offset += end + 1
        return data[:end].decode("utf-8", errors="replace").splitlines()

    def _emit_lines(self, job: Job, lines: list[str]) -> None:
        for line in lines:
            value = self._progress_value(job, line)
            if value is not None:
                self.notify("job.progress", {"jobId": job.id, **value})
            elif line.strip():
                self.notify("job.output", {"jobId": job.id, "stream": "stderr", "text": line})

    def _asr_progress(self, job: Job, seen: dict) -> None:
        path = job.record.get("progressPath")
        if not path:
            return
        try:
            state = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(state, dict) or state.get("pid") not in (None, job.pid()):
            return  # Another process's attempt; not this job's progress.
        current = {"stage": "TRANSCRIBING", "phase": state.get("phase"), "status": state.get("status"),
                   "elapsedSeconds": state.get("elapsed_seconds")}
        if current != seen.get("asr"):
            seen["asr"] = current
            job.record["lastProgress"] = current
            self.notify("job.progress", {"jobId": job.id, **current})

    def _monitor(self, job: Job) -> None:
        seen: dict = {}
        last_asr = 0.0
        while not self.closing.is_set():
            self._emit_lines(job, self._read_lines(job))
            if time.monotonic() - last_asr >= ASR_POLL_SECONDS:
                last_asr = time.monotonic()
                self._asr_progress(job, seen)
            if not job.alive():
                break
            time.sleep(POLL_SECONDS)
        if self.closing.is_set():
            return
        if job.process is not None:
            job.process.wait()
        self._emit_lines(job, self._read_lines(job))
        self._finish(job)

    def _finish(self, job: Job) -> None:
        code = job.exit_code()
        stdout = self._text(job.sibling(".out"))
        stderr = self._text(job.sibling(".err"))
        with job.lock:
            record = job.record
            record.update(finishedAt=time.time(), exitCode=code)
            if code is None:
                record["state"] = "INTERRUPTED"
                record["error"] = {"code": -32001, "message": "The job stopped without reporting a result"}
            else:
                try:
                    record["result"] = cli_result(Invocation(code, stdout, stderr))
                except RpcError as error:
                    record["error"] = error.payload()
                cancelled = record.get("cancelRequested") and code != 0
                record["state"] = "COMPLETED" if code == 0 else "CANCELLED" if cancelled else "FAILED"
            if record["state"] in {"INTERRUPTED", "CANCELLED"}:
                record["resumeHint"] = self._resume_hint(job)
            job.save()
        remove([Path(path) for path in record.get("requestFiles", [])])
        if job.windows_job is not None:
            job.windows_job.close()
        job.done.set()
        self.notify("job.finished", {"jobId": job.id, "state": record["state"], "exitCode": code,
                                     **{key: record[key] for key in ("result", "error", "resumeHint") if key in record}})

    @staticmethod
    def _text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _resume_hint(self, job: Job) -> dict:
        """How to continue: notes jobs resume their saved batch; others repeat the call."""
        record = job.record
        params = dict(record.get("params") or {})
        batch = record.get("batchId") or self._batch_of(job.pid())
        if record.get("method") in {"notes.run", "lesson.process"} and batch:
            course = params.get("course") or str(params.get("lectureId", "")).upper().rsplit("-L", 1)[0]
            return {"method": "notes.run", "params": {"course": course, "resume": batch}}
        return {"method": record.get("method"), "params": params}

    def _batch_of(self, pid: int | None) -> str | None:
        if not pid:
            return None
        for path in (Path(self.workspace).expanduser() / "runs" / "_batches").glob("*/notes-run.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(state, dict) and state.get("pid") == pid and isinstance(state.get("batch_id"), str):
                return state["batch_id"]
        return None
