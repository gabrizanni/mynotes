"""Windows per-user Capture lifecycle and private WAV-to-M4A conversion."""

from __future__ import annotations

import argparse
import base64
import csv
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from . import file_lock
from .capture import CaptureError, CaptureStore, capture_status, read_json, runtime_home
from .utils import atomic_json_write
from .workspace import Workspace

TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"
SESSION_ID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"


def executable() -> Path:
    candidate = Path(os.environ.get("MYNOTES_CAPTURE_EXE") or
                     Path(sys.executable).parent.parent / "capture" / "MyNotes.Capture.exe")
    if not candidate.is_absolute() or not candidate.is_file() or candidate.suffix.lower() != ".exe":
        raise CaptureError("MyNotes Capture.exe is unavailable. Install the Windows MyNotes package.")
    return candidate


def installed() -> bool:
    try:
        executable()
    except CaptureError:
        return False
    return True


def _system_tool(name: str, *arguments: str, environment: dict | None = None) -> subprocess.CompletedProcess:
    command = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / name
    try:
        return subprocess.run([str(command), *arguments], capture_output=True, text=True,
                              errors="replace", timeout=30, env=environment,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CaptureError(f"Could not run {name}: {error}") from error


def _user_sid() -> str:
    result = _system_tool("whoami.exe", "/user", "/fo", "csv", "/nh")
    if result.returncode == 0:
        for row in csv.reader(result.stdout.splitlines()):
            if len(row) == 2 and re.fullmatch(r"S-1-[0-9]+(?:-[0-9]+)+", row[1]):
                return row[1]
    raise CaptureError("Cannot identify the current Windows user for Capture")


def task_name(sid: str) -> str:
    return f"MyNotes Capture {sid}"


def task_xml(sid: str, binary: Path, workspace: Path, home: Path) -> bytes:
    task = ET.Element("Task", version="1.4", xmlns=TASK_NAMESPACE)
    triggers = ET.SubElement(task, "Triggers")
    ET.SubElement(ET.SubElement(triggers, "LogonTrigger"), "UserId").text = sid
    principal = ET.SubElement(ET.SubElement(task, "Principals"), "Principal", id="CaptureUser")
    for key, value in (("UserId", sid), ("LogonType", "InteractiveToken"), ("RunLevel", "LeastPrivilege")):
        ET.SubElement(principal, key).text = value

    settings = ET.SubElement(task, "Settings")
    for key, value in (("MultipleInstancesPolicy", "IgnoreNew"), ("DisallowStartIfOnBatteries", "false"),
                       ("StopIfGoingOnBatteries", "false"), ("StartWhenAvailable", "true"),
                       ("Enabled", "true"), ("ExecutionTimeLimit", "PT0S")):
        ET.SubElement(settings, key).text = value
    restart = ET.SubElement(settings, "RestartOnFailure")
    ET.SubElement(restart, "Interval").text = "PT1M"
    ET.SubElement(restart, "Count").text = "3"

    action = ET.SubElement(ET.SubElement(task, "Actions", Context="CaptureUser"), "Exec")
    ET.SubElement(action, "Command").text = str(binary)
    ET.SubElement(action, "Arguments").text = subprocess.list2cmdline(
        ["--workspace", str(workspace), "--capture-home", str(home)]
    )
    ET.SubElement(action, "WorkingDirectory").text = str(binary.parent)
    return ET.tostring(task, encoding="utf-16", xml_declaration=True)


def _task(action: str, name: str, *arguments: str) -> subprocess.CompletedProcess:
    return _system_tool("schtasks.exe", action, "/TN", name, *arguments)


def _require_task(result: subprocess.CompletedProcess, action: str) -> None:
    if result.returncode:
        reason = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise CaptureError(f"Task Scheduler could not {action} MyNotes Capture: {reason}")


def set_enabled(store: CaptureStore, enabled: bool) -> dict:
    home = runtime_home().resolve()
    home.mkdir(parents=True, exist_ok=True)
    active_path = home / "active.json"
    workspace = store.workspace.root

    with (home / "update.lock").open("a+b") as lease:
        try:
            file_lock.flock(lease.fileno(), file_lock.LOCK_EX | file_lock.LOCK_NB)
        except BlockingIOError as error:
            raise CaptureError("Finish the active recording, audio export or application update first") from error
        active = read_json(active_path)
        if active and str(Path(active["workspace"]).resolve()).casefold() != str(workspace).casefold():
            raise CaptureError("Disable Capture in its active workspace before switching workspaces")
        if enabled and (not store.config_path.exists() or not store.schedule_path.exists()):
            raise CaptureError("Configure Capture and import or synchronize a calendar first")

        sid = _user_sid()
        name = task_name(sid)
        state = capture_status(store)
        if enabled and active and store.config()["enabled"] and state["running"] is True:
            return {"status": "ENABLED", "alreadyRunning": True}
        if not enabled:
            with store.lock():
                config = store.config()
                if not active and not config["enabled"]:
                    return {"status": "DISABLED"}
                _require_task(_task("/Delete", name, "/F"), "remove")
                config["enabled"] = False
                store.save_config(config)
            active_path.unlink(missing_ok=True)
            return {"status": "DISABLED"}

        binary = executable()
        with tempfile.TemporaryDirectory(prefix="task-", dir=home) as directory:
            description = Path(directory) / "capture.xml"
            description.write_bytes(task_xml(sid, binary, workspace, home))
            _require_task(_task("/Create", name, "/XML", str(description), "/F"), "register")
        previously_enabled = store.config()["enabled"]
        try:
            with store.lock():
                config = store.config()
                config["enabled"] = True
                store.save_config(config)
            atomic_json_write(active_path, {"workspace": str(workspace)})
            _require_task(_task("/Run", name), "start")
        except (OSError, CaptureError):
            if not active:
                _task("/Delete", name, "/F")
                active_path.unlink(missing_ok=True)
            with store.lock():
                config = store.config()
                config["enabled"] = bool(active) and previously_enabled
                store.save_config(config)
            raise

    return {"status": "ENABLED", "audioReadinessVerified": False}


# The installer holds the recording/export lease while it calls this command.
# Read and mutate the same task through Task Scheduler's COM interface. Native
# return values avoid depending on the system language or parsing task output.
_INSTALLER_TASK_CONTROL = r"""
$ErrorActionPreference = 'Stop'
$operation = $env:MYNOTES_INSTALLER_CAPTURE_OPERATION
$name = $env:MYNOTES_INSTALLER_CAPTURE_TASK
$expectedPath = $env:MYNOTES_INSTALLER_CAPTURE_EXE
$service = New-Object -ComObject Schedule.Service
$service.Connect()
$folder = $service.GetFolder('\')
$task = @($folder.GetTasks(1) | Where-Object { $_.Name -eq $name }) | Select-Object -First 1
if ($null -eq $task) {
    if ($operation -eq 'resume') { throw 'The Capture task to resume is missing' }
    [Console]::WriteLine('ABSENT')
    exit 0
}

$actions = $task.Definition.Actions
if ($actions.Count -ne 1) { throw 'The Capture task has unexpected actions' }
$action = $actions.Item(1)
if ($action.Type -ne 0 -or -not [IO.Path]::IsPathRooted($action.Path) -or
    -not [string]::Equals([IO.Path]::GetFullPath($action.Path),
        [IO.Path]::GetFullPath($expectedPath), [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The Capture task targets a different application'
}

$enabled = $task.Enabled
if ($operation -eq 'resume') {
    try {
        $task.Enabled = $true
        $null = $task.Run($null)
    } catch {
        $task.Enabled = $enabled
        throw
    }
    [Console]::WriteLine('RESUMED')
    exit 0
}

$wasRunning = $task.GetInstances(0).Count -gt 0
try {
    $task.Enabled = $false
    if ($task.GetInstances(0).Count -gt 0) { $task.Stop(0) }
    for ($attempt = 0; $attempt -lt 40 -and $task.GetInstances(0).Count -gt 0; $attempt++) {
        Start-Sleep -Milliseconds 100
    }
    if ($task.GetInstances(0).Count -gt 0) { throw 'The Capture worker did not stop' }
    if ($operation -eq 'remove') {
        $folder.DeleteTask($name, 0)
        [Console]::WriteLine('REMOVED')
    } elseif ($enabled) {
        [Console]::WriteLine('ENABLED')
    } else {
        [Console]::WriteLine('DISABLED')
    }
} catch {
    $failure = $_
    try {
        $task.Enabled = $enabled
        if ($enabled -and $wasRunning -and $task.GetInstances(0).Count -eq 0) {
            $null = $task.Run($null)
        }
    } catch {
        throw "Capture maintenance failed and its task could not be restored: $failure / $_"
    }
    throw $failure
}
"""


def _control_task(operation: str, binary: Path) -> str:
    results = {
        "quiesce": {"ENABLED", "DISABLED", "ABSENT"},
        "remove": {"REMOVED", "ABSENT"},
        "resume": {"RESUMED"},
    }
    env = dict(os.environ, MYNOTES_INSTALLER_CAPTURE_OPERATION=operation,
               MYNOTES_INSTALLER_CAPTURE_TASK=task_name(_user_sid()),
               MYNOTES_INSTALLER_CAPTURE_EXE=str(binary))
    encoded = base64.b64encode(_INSTALLER_TASK_CONTROL.encode("utf-16-le")).decode("ascii")
    result = _system_tool(r"WindowsPowerShell\v1.0\powershell.exe", "-NoLogo", "-NoProfile",
                          "-NonInteractive", "-EncodedCommand", encoded, environment=env)
    _require_task(result, operation)
    status = result.stdout.strip()
    if status not in results[operation]:
        raise CaptureError("Unexpected Capture maintenance result")
    return status


def _verify_worker_stopped(home: Path) -> None:
    # An instance started outside Task Scheduler owns the same app lease, and
    # cannot be replaced while its executable is in use.
    try:
        lease = (home / "app.lock").open("r+b")
    except FileNotFoundError:
        return
    with lease:
        try:
            file_lock.flock(lease.fileno(), file_lock.LOCK_EX | file_lock.LOCK_NB)
        except BlockingIOError as error:
            raise CaptureError("An instance of MyNotes Capture is still running") from error


def maintenance(operation: str, binary: Path, home: Path) -> str:
    """Stop or remove the owned task under the installer lease, or resume it after release."""
    if not binary.is_absolute() or binary.name.casefold() != "mynotes.capture.exe" or not home.is_absolute():
        raise CaptureError("Expected absolute Capture executable and runtime paths")
    if operation == "resume":
        return _control_task(operation, binary)
    if operation not in {"quiesce", "remove"}:
        raise CaptureError("Unknown Capture maintenance operation")
    original = _control_task("quiesce", binary)
    try:
        _verify_worker_stopped(home)
        if operation == "remove" and original != "ABSENT":
            return _control_task(operation, binary)
    except (OSError, CaptureError):
        if original == "ENABLED":
            _control_task("resume", binary)
        raise
    return original


def valid_audio(path: Path) -> bool:
    import av

    try:
        with av.open(str(path)) as audio:
            stream = audio.streams.audio[0]
            first = next(audio.decode(stream), None)
            return bool(first and first.samples > 0)
    except (OSError, IndexError, av.FFmpegError):
        return False


def export(store: CaptureStore, session_id: str) -> float:
    """Encode a worker-owned attempt without touching its final destination."""
    import av

    if not re.fullmatch(SESSION_ID, session_id):
        raise CaptureError("Invalid Capture session ID")
    session = read_json(store.directory / "sessions" / f"{session_id}.json", {})
    if session.get("id") != session_id or session.get("status") != "EXPORTING":
        raise CaptureError("Capture session is not ready for export")
    stage = Path(session["directory"])
    destination = Path(session["outputPath"])
    temporary = Path(session["exportTemporaryPath"])
    if (not stage.is_absolute() or stage.name.casefold() != session_id.casefold()
            or not destination.is_absolute() or destination.suffix.lower() != ".m4a"
            or temporary.parent != destination.parent
            or not re.fullmatch(rf"\.capture-{re.escape(session_id)}-[0-9a-f]{{32}}\.tmp", temporary.name)):
        raise CaptureError("Invalid Capture export receipt")
    segments = sorted(stage.glob("*.wav"))
    if (not segments or not stage.is_dir() or stage.is_symlink()
            or any(not re.fullmatch(r"[0-9]{19}-[0-9a-f]{32}\.wav", item.name)
                   or not item.is_file() or item.is_symlink() for item in segments)):
        raise CaptureError("No valid staged audio segments")

    sample_rate = 48_000
    total_samples = 0
    # The worker owns the final rename and records a digest before publishing.
    # Exclusive creation prevents a stale temporary file from being overwritten.
    with temporary.open("xb") as handle:
        try:
            with av.open(handle, mode="w", format="ipod") as container:
                output = container.add_stream("aac", rate=sample_rate)
                output.layout = "mono"
                output.bit_rate = 96_000
                for segment in segments:
                    resampler = av.AudioResampler(format="fltp", layout="mono", rate=sample_rate)
                    with av.open(str(segment)) as source:
                        audio = source.streams.audio[0]
                        for frame in source.decode(audio):
                            frame.pts = None
                            for converted in resampler.resample(frame):
                                converted.pts = total_samples
                                total_samples += converted.samples
                                container.mux(output.encode(converted))
                    for converted in resampler.resample(None):
                        converted.pts = total_samples
                        total_samples += converted.samples
                        container.mux(output.encode(converted))
                if not total_samples:
                    raise CaptureError("Staged audio has no samples")
                container.mux(output.encode(None))
        except (OSError, ValueError, IndexError, av.FFmpegError):
            handle.close()
            temporary.unlink(missing_ok=True)
            raise
    if not valid_audio(temporary):
        temporary.unlink(missing_ok=True)
        raise CaptureError("Converted Capture recording could not be decoded")
    return total_samples / sample_rate


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    encode = commands.add_parser("export")
    encode.add_argument("--workspace", required=True)
    encode.add_argument("--session", required=True)
    maintain = commands.add_parser("maintenance")
    maintain.add_argument("operation", choices=["quiesce", "resume", "remove"])
    maintain.add_argument("--capture-exe", required=True, type=Path)
    maintain.add_argument("--capture-home", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.command == "maintenance":
            print(maintenance(arguments.operation, arguments.capture_exe, arguments.capture_home))
        else:
            print(export(CaptureStore(Workspace.from_value(arguments.workspace)), arguments.session))
    except (OSError, ValueError, KeyError, IndexError) as error:
        parser.exit(1, f"Capture {arguments.command} failed: {error}\n")


if __name__ == "__main__":
    main()
