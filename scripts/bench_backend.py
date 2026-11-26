#!/usr/bin/env python3
"""Measure MyNotes backend cold start, polling cost and the stdio server.

Every run uses a synthetic workspace in a temporary directory, an isolated HOME
and a PATH without AI agents. No provider, network, transcription engine or
Word renderer is started. Point --repo/--python at another checkout to measure
it with the same harness, then compare two result files with --compare.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import queue
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEAVY_MODULES = ("docx", "lxml", "pypdfium2", "PIL", "av", "pptx", "openpyxl")
COURSE = "DEMO"
FIXED_MTIME = 1_700_000_000  # Old enough for every stability check.


def default_python(repo: Path) -> Path:
    windows = repo / ".venv" / "Scripts" / "python.exe"
    return windows if os.name == "nt" else repo / ".venv" / "bin" / "python"


def isolated_env(root: Path, workspace: Path) -> dict[str, str]:
    """Child environment that cannot reach the user's tools, agents or data."""
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("MYNOTES_", "PYTHON"))}
    home = root / "home"
    empty_bin = root / "bin"
    for directory in (home, empty_bin):
        directory.mkdir(parents=True, exist_ok=True)
    system_path = [] if os.name == "nt" else ["/usr/bin", "/bin"]
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        system_path = [str(Path(system_root) / "System32"), system_root]
    environment.update(
        HOME=str(home), USERPROFILE=str(home), PATH=os.pathsep.join([str(empty_bin), *system_path]),
        MYNOTES_WORKSPACE=str(workspace), MYNOTES_HOME=str(root / "runtime"),
        MYNOTES_CAPTURE_HOME=str(root / "capture-runtime"),
    )
    return environment


def mynotes_command(python: Path, *arguments: str) -> list[str]:
    # The Windows app launches the backend the same way.
    return [str(python), "-I", "-X", "utf8", "-m", "mynotes", *arguments]


def _write_wav(path: Path, seconds: float = 2.0, rate: int = 16000) -> None:
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames * 2))
    os.utime(path, (FIXED_MTIME, FIXED_MTIME))


def build_workspace(python: Path, root: Path, *, lessons: int = 12) -> Path:
    """Create a demo course, schedule and WAV files through the target's own CLI."""
    workspace = root / "workspace"
    recordings = root / "recordings"
    recordings.mkdir(parents=True, exist_ok=True)
    inputs = root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    environment = isolated_env(root, workspace)

    def run(*arguments: str) -> None:
        completed = subprocess.run(mynotes_command(python, *arguments), env=environment, cwd=root,
                                   capture_output=True, text=True, encoding="utf-8", timeout=120)
        if completed.returncode != 0:
            raise RuntimeError(f"setup step failed: {' '.join(arguments)}\n{completed.stdout}{completed.stderr}")

    course = {"code": COURSE, "name": "Synthetic Systems", "kind": "mixed",
              "languages": {"lesson": "en", "notes": "en"}}
    (inputs / "course.json").write_text(json.dumps(course), encoding="utf-8")
    (inputs / "paths.json").write_text(json.dumps({"recording_dirs": [str(recordings)]}), encoding="utf-8")
    (inputs / "providers.json").write_text(json.dumps({"default": "claude", "configured": {"claude": {}}}), encoding="utf-8")
    events = [{"courseCode": COURSE, "lessonNumber": number,
               "startsAt": f"2030-03-{number:02d}T09:00:00Z", "endsAt": f"2030-03-{number:02d}T10:30:00Z",
               "title": f"Synthetic lesson {number}"} for number in range(1, lessons + 1)]
    (inputs / "events.json").write_text(json.dumps(events), encoding="utf-8")
    for number in range(1, lessons + 1):
        _write_wav(recordings / f"{COURSE}-L{number:02d}.wav")

    run("init", "--workspace", str(workspace))
    run("course", "add", "--workspace", str(workspace), "--file", str(inputs / "course.json"))
    run("configure", "--workspace", str(workspace), "--group", "paths", "--answers", str(inputs / "paths.json"))
    run("configure", "--workspace", str(workspace), "--group", "providers", "--answers", str(inputs / "providers.json"))
    run("capture", "configure", "--workspace", str(workspace))
    run("capture", "import", "--workspace", str(workspace), "--file", str(inputs / "events.json"))
    run("transcription-queue", "configure", "--workspace", str(workspace), "--parallel", "1")
    return workspace


class Reaper:
    """Collect CPU of direct children and, on Linux, of detached grandchildren."""

    def __init__(self) -> None:
        self.subreaper = False
        if sys.platform.startswith("linux"):
            try:
                import ctypes
                libc = ctypes.CDLL(None, use_errno=True)
                self.subreaper = libc.prctl(36, 1, 0, 0, 0) == 0  # PR_SET_CHILD_SUBREAPER
            except (OSError, AttributeError):
                self.subreaper = False

    @staticmethod
    def run(command: list[str], environment: dict[str, str], cwd: Path) -> dict:
        started = time.perf_counter()
        process = subprocess.Popen(command, env=environment, cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if hasattr(os, "wait4"):
            _, status, usage = os.wait4(process.pid, 0)
            process.returncode = os.waitstatus_to_exitcode(status)
            cpu = usage.ru_utime + usage.ru_stime
        else:
            process.wait()
            cpu = None
        return {"wall": time.perf_counter() - started, "cpu": cpu, "exit": process.returncode}

    def reap_orphans(self, *, settle: float = 10.0) -> dict:
        """Wait for adopted detached processes (queue runners) and sum their CPU."""
        if not self.subreaper:
            return {"count": None, "cpu": None}
        count, cpu = 0, 0.0
        deadline = time.monotonic() + settle
        while True:
            try:
                pid, status, usage = os.wait4(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
                continue
            count += 1
            cpu += usage.ru_utime + usage.ru_stime
        return {"count": count, "cpu": cpu}


def summarize(values: list[float]) -> dict:
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, max(0, round(0.95 * len(ordered)) - 1))]
    return {"median": statistics.median(ordered), "p95": p95, "min": ordered[0], "max": ordered[-1], "n": len(ordered)}


def commands(workspace: Path, session: str) -> dict[str, list[str]]:
    ws = ["--workspace", str(workspace)]
    return {
        "--version": ["--version"],
        "capture dashboard": ["capture", "dashboard", *ws],
        "queue status": ["transcription-queue", "status", *ws],
        "queue library": ["transcription-queue", "library", *ws],
        "heartbeat": ["transcription-queue", "heartbeat", *ws, "--session", session],
        "doctor": ["doctor", "--json", *ws],
    }


def measure_cold_starts(python: Path, root: Path, workspace: Path, environment: dict, runs: int, reaper: Reaper) -> dict:
    results = {}
    for name, arguments in commands(workspace, str(uuid.uuid4())).items():
        command = mynotes_command(python, *arguments)
        reaper.run(command, environment, root)  # warm the file cache
        samples = [reaper.run(command, environment, root) for _ in range(runs)]
        orphans = reaper.reap_orphans()
        entry = {"wall_ms": summarize([s["wall"] * 1000 for s in samples]),
                 "exit_codes": sorted({s["exit"] for s in samples}),
                 "detached_spawns_per_call": None if orphans["count"] is None else orphans["count"] / (runs + 1)}
        if all(s["cpu"] is not None for s in samples):
            entry["child_cpu_ms"] = summarize([s["cpu"] * 1000 for s in samples])
        results[name] = entry
    return results


def measure_imports(python: Path, root: Path, environment: dict, runs: int) -> dict:
    probe = ("import sys, time, json; t = time.perf_counter(); import mynotes.cli; "
             "elapsed = time.perf_counter() - t; "
             f"print(json.dumps({{'seconds': elapsed, 'heavy': sorted(m for m in {HEAVY_MODULES!r} if m in sys.modules)}}))")
    samples, heavy = [], []
    for index in range(runs + 1):
        completed = subprocess.run([str(python), "-I", "-X", "utf8", "-c", probe], env=environment, cwd=root,
                                   capture_output=True, text=True, check=True)
        value = json.loads(completed.stdout)
        if index:
            samples.append(value["seconds"] * 1000)
        heavy = value["heavy"]
    traced = subprocess.run([str(python), "-I", "-X", "utf8", "-X", "importtime", "-c", "import mynotes.cli"],
                            env=environment, cwd=root, capture_output=True, text=True, check=True)
    rows = []
    for line in traced.stderr.splitlines():
        if not line.startswith("import time:") or "cumulative" in line:
            continue
        _, self_us, cumulative_us, name = (part.strip() for part in line.replace("import time:", "|", 1).split("|"))
        rows.append({"module": name, "self_us": int(self_us), "cumulative_us": int(cumulative_us)})
    rows.sort(key=lambda row: row["cumulative_us"], reverse=True)
    return {"import_mynotes_cli_ms": summarize(samples), "heavy_modules_loaded": heavy, "importtime_top15": rows[:15]}


def measure_poll_cycle(python: Path, root: Path, workspace: Path, environment: dict, seconds: float, interval: float,
                       reaper: Reaper) -> dict:
    """Reproduce the native apps' timer: four short CLI processes every interval."""
    session = str(uuid.uuid4())
    selected = commands(workspace, session)
    cycle = [selected[name] for name in ("capture dashboard", "queue status", "queue library", "heartbeat")]
    started = time.monotonic()
    cpu, spawns, ticks = 0.0, 0, 0
    cpu_known = True
    while time.monotonic() - started < seconds:
        tick = time.monotonic()
        for arguments in cycle:
            sample = reaper.run(mynotes_command(python, *arguments), environment, root)
            spawns += 1
            if sample["cpu"] is None:
                cpu_known = False
            else:
                cpu += sample["cpu"]
        ticks += 1
        time.sleep(max(0.0, interval - (time.monotonic() - tick)))
    elapsed = time.monotonic() - started
    orphans = reaper.reap_orphans()
    if orphans["count"] is not None:
        spawns += orphans["count"]
        cpu += orphans["cpu"]
    return {"seconds": elapsed, "ticks": ticks, "interval_seconds": interval,
            "cpu_seconds": cpu if cpu_known else None,
            "cpu_percent_one_core": cpu / elapsed * 100 if cpu_known else None,
            "process_spawns": spawns if orphans["count"] is not None else None,
            "process_spawns_per_minute": spawns / elapsed * 60 if orphans["count"] is not None else None,
            "detached_runner_spawns": orphans["count"]}


class Client:
    """Minimal newline-delimited JSON-RPC client for `mynotes serve --stdio`."""

    def __init__(self, command: list[str], environment: dict, cwd: Path, stderr) -> None:
        self.process = subprocess.Popen(command, env=environment, cwd=cwd, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=stderr)
        self.responses: dict[int, queue.Queue] = {}
        self.notifications: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.next_id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.process.stdout:
            received = time.perf_counter()
            message = json.loads(line)
            if "id" in message and message["id"] is not None and "method" not in message:
                self.responses.setdefault(message["id"], queue.Queue()).put(message)
            else:
                self.notifications.put((received, message))

    def call(self, method: str, params: dict | None = None, timeout: float = 60) -> dict:
        with self.lock:
            self.next_id += 1
            identifier = self.next_id
            self.responses.setdefault(identifier, queue.Queue())
        payload = {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params or {}}
        self.process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        self.process.stdin.flush()
        message = self.responses[identifier].get(timeout=timeout)
        self.responses.pop(identifier, None)
        if "error" in message:
            raise RuntimeError(f"{method}: {message['error']}")
        return message["result"]

    def wait_notification(self, method: str, timeout: float) -> float | None:
        deadline = time.perf_counter() + timeout
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return None
            try:
                received, message = self.notifications.get(timeout=remaining)
            except queue.Empty:
                return None
            if message.get("method") == method:
                return received

    def close(self) -> None:
        try:
            self.call("system.shutdown", timeout=10)
        except (RuntimeError, queue.Empty, OSError, ValueError):
            pass
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


def server_supported(python: Path, root: Path, environment: dict) -> bool:
    completed = subprocess.run(mynotes_command(python, "serve", "--list-methods", "--json"), env=environment,
                               cwd=root, capture_output=True, text=True)
    return completed.returncode == 0


def measure_server(python: Path, root: Path, workspace: Path, environment: dict, *, calls: int,
                   idle_seconds: float, changes: int) -> dict:
    if not server_supported(python, root, environment):
        return {"supported": False}
    log = (root / "serve.log").open("wb")
    started = time.perf_counter()
    client = Client(mynotes_command(python, "serve", "--stdio", "--workspace", str(workspace)), environment, root, log)
    try:
        hello = client.call("system.hello", {"protocol": 1})
        hello_ms = (time.perf_counter() - started) * 1000
        timings = {}
        for name, method, params in (("capture.dashboard", "capture.dashboard", {}),
                                     ("queue.status", "queue.status", {}),
                                     ("watch.snapshot capture (cached)", "watch.snapshot", {"topic": "capture"})):
            client.call(method, params)
            samples = []
            for _ in range(calls):
                begin = time.perf_counter()
                client.call(method, params)
                samples.append((time.perf_counter() - begin) * 1000)
            timings[name] = summarize(samples)
        client.call("session.open", {"visible": True})
        client.call("watch.subscribe", {"topics": ["capture", "queue", "library", "config"]})
        time.sleep(3)  # Let the first scan settle before sampling idle cost.
        while not client.notifications.empty():
            client.notifications.get_nowait()
        before = client.call("system.stats")
        time.sleep(idle_seconds)
        after = client.call("system.stats")
        idle_notifications = client.notifications.qsize()
        cpu = (after["cpuSeconds"] + after["childCpuSeconds"]) - (before["cpuSeconds"] + before["childCpuSeconds"])
        wall = after["uptimeSeconds"] - before["uptimeSeconds"]
        latencies = []
        ws = ["--workspace", str(workspace)]
        for index in range(changes):
            while not client.notifications.empty():
                client.notifications.get_nowait()
            action = "pause" if index % 2 == 0 else "resume"
            subprocess.run(mynotes_command(python, "capture", action, *ws), env=environment, cwd=root,
                           capture_output=True, check=True)
            changed = time.perf_counter()
            received = client.wait_notification("capture.changed", timeout=10)
            latencies.append(None if received is None else (received - changed) * 1000)
        client.call("session.close")
        return {"supported": True, "protocol": hello.get("protocol"), "hello_from_spawn_ms": hello_ms,
                "round_trip_ms": timings, "idle": {
                    "seconds": wall, "cpu_seconds": cpu, "cpu_percent_one_core": cpu / wall * 100,
                    "process_spawns": after["spawns"] - before["spawns"],
                    "process_spawns_per_minute": (after["spawns"] - before["spawns"]) / wall * 60,
                    "notifications": idle_notifications, "rss_bytes": after.get("rssBytes")},
                "change_to_notification_ms": summarize([v for v in latencies if v is not None]) if any(v is not None for v in latencies) else None,
                "missed_notifications": sum(v is None for v in latencies)}
    finally:
        client.close()
        log.close()


def machine() -> dict:
    model = platform.processor()
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    return {"system": platform.system(), "release": platform.release(), "machine": platform.machine(),
            "cpu": model, "cpu_count": os.cpu_count()}


def revision(repo: Path) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def benchmark(arguments: argparse.Namespace) -> dict:
    repo = Path(arguments.repo).resolve()
    python = Path(arguments.python) if arguments.python else default_python(repo)
    reaper = Reaper()
    with tempfile.TemporaryDirectory(prefix="mynotes-bench-") as temporary:
        root = Path(temporary)
        workspace = build_workspace(python, root)
        environment = isolated_env(root, workspace)
        version = subprocess.run(mynotes_command(python, "--version"), env=environment, cwd=root,
                                 capture_output=True, text=True, check=True).stdout.strip()
        result = {"schema": 1, "label": arguments.label or revision(repo), "revision": revision(repo),
                  "mynotes": version, "python": subprocess.run([str(python), "-c", "import sys; print(sys.version.split()[0])"],
                                                               capture_output=True, text=True).stdout.strip(),
                  "machine": machine(), "runs": arguments.runs, "linux_subreaper": reaper.subreaper,
                  "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        result["imports"] = measure_imports(python, root, environment, arguments.runs)
        result["cold_start"] = measure_cold_starts(python, root, workspace, environment, arguments.runs, reaper)
        result["poll_cycle"] = measure_poll_cycle(python, root, workspace, environment, arguments.poll_seconds,
                                                  arguments.poll_interval, reaper)
        result["server"] = measure_server(python, root, workspace, environment, calls=arguments.rpc_calls,
                                          idle_seconds=arguments.idle_seconds, changes=arguments.changes)
        reaper.reap_orphans(settle=2)
    return result


def _get(data: dict, path: tuple) -> float | None:
    for key in path:
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data if isinstance(data, (int, float)) and not isinstance(data, bool) else None


def compare(before: dict, after: dict) -> str:
    rows: list[tuple[str, tuple]] = [("`import mynotes.cli` median (ms)", ("imports", "import_mynotes_cli_ms", "median"))]
    for name in before.get("cold_start", {}) | after.get("cold_start", {}):
        rows.append((f"`{name}` cold start median (ms)", ("cold_start", name, "wall_ms", "median")))
        rows.append((f"`{name}` cold start p95 (ms)", ("cold_start", name, "wall_ms", "p95")))
        rows.append((f"`{name}` child CPU median (ms)", ("cold_start", name, "child_cpu_ms", "median")))
    rows += [
        ("Old poll cycle CPU (% of one core)", ("poll_cycle", "cpu_percent_one_core")),
        ("Old poll cycle spawns per minute", ("poll_cycle", "process_spawns_per_minute")),
        ("Server `system.hello` from spawn (ms)", ("server", "hello_from_spawn_ms")),
        ("RPC `capture.dashboard` median (ms)", ("server", "round_trip_ms", "capture.dashboard", "median")),
        ("RPC `capture.dashboard` p95 (ms)", ("server", "round_trip_ms", "capture.dashboard", "p95")),
        ("RPC `queue.status` p95 (ms)", ("server", "round_trip_ms", "queue.status", "p95")),
        ("RPC cached capture snapshot p95 (ms)", ("server", "round_trip_ms", "watch.snapshot capture (cached)", "p95")),
        ("Server idle CPU, UI open (% of one core)", ("server", "idle", "cpu_percent_one_core")),
        ("Server idle spawns per minute", ("server", "idle", "process_spawns_per_minute")),
        ("File change to notification median (ms)", ("server", "change_to_notification_ms", "median")),
        ("File change to notification max (ms)", ("server", "change_to_notification_ms", "max")),
    ]
    lines = [f"| Metric | Before ({before.get('label')}) | After ({after.get('label')}) | Change |",
             "| --- | ---: | ---: | ---: |"]
    for label, path in rows:
        old, new = _get(before, path), _get(after, path)
        if old is None and new is None:
            continue
        change = f"{(new - old) / old * 100:+.0f}%" if old not in (None, 0) and new is not None else "n/a"
        lines.append(f"| {label} | {_fmt(old)} | {_fmt(new)} | {change} |")
    heavy_before = before.get("imports", {}).get("heavy_modules_loaded")
    heavy_after = after.get("imports", {}).get("heavy_modules_loaded")
    lines.append(f"| Heavy modules loaded by `import mynotes.cli` | {', '.join(heavy_before or []) or 'none'} | "
                 f"{', '.join(heavy_after or []) or 'none'} | |")
    return "\n".join(lines) + "\n"


def _fmt(value: float | None) -> str:
    if value is None:
        return "not measured"
    return f"{value:.2f}" if abs(value) < 10 else f"{value:.1f}" if abs(value) < 100 else f"{value:.0f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=str(ROOT), help="checkout to measure (default: this one)")
    parser.add_argument("--python", help="interpreter with that checkout installed (default: <repo>/.venv)")
    parser.add_argument("--label", help="name shown by --compare (default: short commit)")
    parser.add_argument("--runs", type=int, default=15, help="measured cold starts per command")
    parser.add_argument("--poll-seconds", type=float, default=60)
    parser.add_argument("--poll-interval", type=float, default=5)
    parser.add_argument("--idle-seconds", type=float, default=60)
    parser.add_argument("--rpc-calls", type=int, default=200)
    parser.add_argument("--changes", type=int, default=6, help="file changes timed until their notification")
    parser.add_argument("--output", help="write the JSON result here")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="print a Markdown table")
    arguments = parser.parse_args()
    if arguments.compare:
        before, after = (json.loads(Path(path).read_text(encoding="utf-8")) for path in arguments.compare)
        sys.stdout.write(compare(before, after))
        return 0
    result = benchmark(arguments)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        Path(arguments.output).write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
