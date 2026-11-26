"""Method table for `mynotes serve`: RPC names, parameters, CLI commands, lanes.

Each CLI-backed method turns its camelCase parameters into the flags of an
existing command. Its result is `{exitCode, data}`, where data is the JSON the
command prints. Lanes:

- fast: read-only commands, in-process, 4 threads;
- slow: commands that write local files, in-process, 2 threads;
- job: anything that can call an AI agent, transcription, LibreOffice, the
  network, or install signal handlers; it runs as `python -m mynotes.rpc_job`.

Methods handled by the server itself (system, session, watch, job) are listed
in serve.SERVER_METHODS.
"""
from __future__ import annotations

import json
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cli_invoke import Invocation

PROTOCOL = 1

# JSON-RPC 2.0 reserved codes and the server's own.
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603
COMMAND_FAILED, BUSY, NEEDS_REVIEW, JOB_NOT_FOUND, PROTOCOL_MISMATCH, SHUTTING_DOWN = -32001, -32002, -32003, -32004, -32005, -32006
NOT_FOUND = -32009  # library.transcript: unknown recording, or no transcript yet
REQUEST_CANCELLED = -32800


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code, self.message, self.data = code, message, data

    def payload(self) -> dict:
        return {"code": self.code, "message": self.message, **({"data": self.data} if self.data is not None else {})}


@dataclass(frozen=True)
class Param:
    """One RPC parameter.

    kind: str, int, float, bool (a store_true flag), list (flag repeated per
    item), pairs (object; `flag KEY VALUE` per entry), json (inline JSON text),
    file (JSON value written to a private request file, passed by path), field
    (a key of the method's request file).
    """
    name: str
    flag: str | None = None  # None: positional, in table order
    kind: str = "str"
    required: bool = False
    key: str | None = None  # request-file key for kind "field"
    choices: tuple[str, ...] = ()
    server: bool = False  # read by the server, never passed to the command


@dataclass(frozen=True)
class Method:
    name: str
    lane: str
    command: tuple[str, ...] = ()
    params: tuple[Param, ...] = ()
    summary: str = ""
    workspace: bool = True
    json_flag: bool = False
    fixed: tuple[str, ...] = ()  # flags always appended
    request_file: str | None = None  # flag that receives the "field" params
    slow_if: str | None = None  # boolean parameter that moves a fast call to the slow lane
    progress: str = "stderr"  # job progress source: stderr JSON lines, or "asr" status files
    direct: Callable[..., Any] | None = field(default=None, compare=False)
    exclusive: tuple[str, ...] = ()  # parameters of which at most one may be given
    one_of: tuple[str, ...] = ()  # parameters of which exactly one is required


def P(name: str, flag: str | None = None, kind: str = "str", **options: Any) -> Param:
    return Param(name, flag, kind, **options)


LESSON, RECORDING = P("lectureId"), P("recordingId", "--recording")
PROVIDER = P("provider", "--provider", choices=("codex", "agy", "claude"))
MEMORY = (P("scope", "--scope", required=True, choices=("global", "course")),
          P("area", "--area", required=True, choices=("notes", "dialogue")), P("course", "--course"))
NOTES = (P("course", required=True), P("lessons", "--lessons"), P("resume", "--resume"),
         P("regenerate", "--regenerate", "bool"), PROVIDER, P("quality", "--quality"),
         P("instructions", "--instructions"), P("imageLimit", "--image-limit", "int"),
         P("selection", "--selection", "file"), P("budgets", "--budgets", "file"),
         P("speakerFocus", "--speaker-focus", choices=("lecture", "professor")),
         P("contextLessons", "--context-lessons"), P("contiguousTranscripts", "--contiguous-transcripts", "bool"))
QUEUE = ("transcription-queue",)


def _direct_dashboard(workspace: str, _params: dict) -> Any:
    from . import capture_dashboard
    from .capture import CaptureStore
    from .workspace import Workspace
    return capture_dashboard.dashboard(CaptureStore(Workspace.from_value(workspace)))


def _provider_status(provider: str) -> Any:
    from .onboarding import provider_check
    return provider_check(provider)


def _queue_workspace(workspace: str):
    from .workspace import Workspace
    ws = Workspace.from_value(workspace)
    if not ws.is_initialized():
        raise ValueError("Initialize MyNotes first")
    return ws


def _direct_queue_status(workspace: str, _params: dict) -> Any:
    from .transcription_queue import status
    return status(_queue_workspace(workspace))


def _direct_library(workspace: str, params: dict) -> Any:
    from .transcription_queue import library_view
    return library_view(_queue_workspace(workspace), query=params.get("query"), date=params.get("date"),
                        course=params.get("course"))


def _direct_transcript(workspace: str, params: dict) -> Any:
    from .serve_transcript import transcript
    return transcript(workspace, params)


METHODS: tuple[Method, ...] = (
    # Configuration and courses
    Method("config.get", "fast", ("config", "show"), json_flag=True, summary="Private configuration (`config show --json`)."),
    Method("preferences.get", "fast", ("preferences",), fixed=("--show",), summary="Shared, role and effective model choices."),
    Method("preferences.saveModels", "slow", ("preferences",), (P("models", "--models-json", "json", required=True),
           P("expectedRevision", "--expected-revision", required=True)), summary="Atomic model configuration."),
    Method("profile.save", "slow", ("profile",), (P("background", "--background"), P("skip", "--skip", "bool")),
           one_of=("background", "skip"), summary="The learner's free-form background answer."),
    Method("memory.show", "fast", ("memory", "show"), MEMORY, json_flag=True, summary="Persistent preferences."),
    Method("memory.update", "slow", ("memory", "update"), (*MEMORY, P("expectedRevision", "--expected-revision", required=True),
           P("set", "--set", "pairs"), P("remove", "--remove", "list"), P("style", "--style-json", "json"),
           P("inherit", "--inherit", "list")), json_flag=True, summary="Atomic preference edit."),
    Method("memory.reset", "slow", ("memory", "reset"), (*MEMORY, P("expectedRevision", "--expected-revision", required=True)),
           json_flag=True, summary="Reset one preference scope."),
    Method("style.show", "fast", ("style", "show"), (P("course", "--course"),), summary="Effective note style."),
    Method("style.edit", "slow", ("style", "edit"), (P("course", "--course"), P("style", "--file", "file"),
           P("reset", "--reset", "bool")), one_of=("style", "reset"), summary="Edit global or course style."),
    Method("style.preview", "slow", ("style", "preview"), (P("course", "--course"), P("output", "--output")),
           summary="Synthetic Word preview of a style (no LibreOffice)."),
    Method("course.pending", "fast", ("course", "pending"), json_flag=True, summary="Courses whose context is pending."),
    Method("course.add", "slow", ("course", "add"), (P("name", "--name", required=True), P("notesLanguage", "--notes-language"),
           P("materials", "--materials"), P("recordings", "--recordings"), P("contextAnswer", "--context-answer"),
           P("settings", "--settings-json", "json"), P("deferContext", "--defer-context", "bool")), json_flag=True,
           summary="Add a course by name."),
    Method("course.edit", "slow", ("course", "edit"), (P("code", required=True), P("course", "--file", "file", required=True)),
           summary="Replace a course from a JSON object."),
    Method("course.link", "slow", ("course", "link"), (P("code", required=True), P("path", required=True), P("notes", "--notes"),
           P("notesFile", "--notes-file"), P("handoutFile", "--handout-file"),
           P("publicationMode", "--publication-mode", choices=("automatic", "review")), P("recordings", "--recordings"),
           P("dryRun", "--dry-run", "bool")), json_flag=True, summary="Connect a course folder; exit 1 means NEEDS_SELECTION."),
    Method("course.deferContext", "slow", ("course", "defer-context"), (P("code", required=True),), json_flag=True,
           summary="Mark a course's context questions as deferred."),
    Method("models.catalog", "fast", ("models",), (P("provider", "--provider", required=True, choices=("codex", "agy", "claude")),
           P("saved", "--saved"), P("savedEffort", "--saved-effort")), workspace=False, json_flag=True,
           summary="Saved model catalog, without refreshing it."),
    Method("models.refresh", "job", ("models",), (P("provider", "--provider", required=True, choices=("codex", "agy", "claude")),
           P("saved", "--saved"), P("savedEffort", "--saved-effort")), workspace=False, json_flag=True, fixed=("--refresh",),
           summary="Refresh the catalog from the agent CLI (no generation)."),
    Method("doctor.get", "slow", ("doctor",), (PROVIDER, P("refresh", kind="bool", server=True)), json_flag=True,
           summary="Readiness report, cached 60 s per provider; exit 1 means not ready."),
    Method("provider.status", "slow", summary="Agent CLI presence, compatibility and sign-in state.",
           params=(P("provider", required=True, server=True, choices=("codex", "agy", "claude")),),
           direct=lambda _workspace, params: _provider_status(params["provider"])),
    Method("provider.login", "job", ("provider-login",), (P("provider", "--provider", required=True, choices=("codex", "agy", "claude")),),
           workspace=False, summary="Agent sign-in; the server polls provider.status every 3 s meanwhile."),
    # Capture
    Method("capture.dashboard", "fast", ("capture", "dashboard"), direct=_direct_dashboard, summary="Capture dashboard."),
    Method("capture.status", "fast", ("capture", "status"), summary="Recorder process state."),
    Method("capture.schedule", "fast", ("capture", "schedule"), summary="Effective recording schedule."),
    Method("capture.pending", "fast", ("capture", "pending"), summary="Recordings waiting for a course."),
    Method("capture.source", "fast", ("capture", "source"), (P("source", "--source", required=True),), summary="One calendar source."),
    Method("capture.configure", "slow", ("capture", "configure"), (P("config", "--file", "file"),), summary="Capture settings patch."),
    Method("capture.import", "slow", ("capture", "import"), (P("events", "--file", "file", required=True),), summary="Static schedule."),
    Method("capture.subscribe", "slow", ("capture", "subscribe"), (P("source", "--file", "file", required=True),),
           summary="Save a calendar subscription (no download)."),
    Method("capture.connect", "job", ("capture", "connect"), (P("source", "--file", "file", required=True),),
           summary="Subscribe and download a calendar."),
    Method("capture.sync", "job", ("capture", "sync"), (P("ifDue", "--if-due", "bool"), P("source", "--source")),
           summary="Download subscribed calendars."),
    Method("capture.setImportMode", "slow", ("capture", "set-import-mode"), (P("source", "--source", required=True),
           P("mode", "--mode", required=True, choices=("mapped", "all_timed"))), summary="Calendar import mode."),
    Method("capture.addLesson", "slow", ("capture", "add-lesson"), (P("lesson", "--file", "file", required=True),), summary="Special lesson."),
    Method("capture.editLesson", "slow", ("capture", "edit-lesson"), (P("event", "--event", required=True),
           P("patch", "--file", "file", required=True)), summary="Local change to one event."),
    Method("capture.resetLesson", "slow", ("capture", "reset-lesson"), (P("event", "--event", required=True),), summary="Drop local changes."),
    Method("capture.unsubscribe", "slow", ("capture", "unsubscribe"), (P("source", "--source", required=True),), summary="Remove a calendar."),
    Method("capture.assign", "slow", ("capture", "assign"), (P("assignments", "--file", "file", required=True),), summary="Assign recordings."),
    Method("capture.enable", "job", ("capture", "enable"), summary="Install and start the recorder service."),
    Method("capture.disable", "job", ("capture", "disable"), summary="Stop and remove the recorder service."),
    Method("capture.pause", "slow", ("capture", "pause"), summary="Pause scheduled recording."),
    Method("capture.resume", "slow", ("capture", "resume"), summary="Resume scheduled recording."),
    Method("capture.start", "slow", ("capture", "start"), (P("course", "--course"), P("lesson", "--lesson", "int"),
           P("unassigned", "--unassigned", "bool"), P("title", "--title"), P("language", "--language"),
           P("durationMinutes", "--duration-minutes", "int", required=True)), fixed=("--wait-seconds", "0"),
           summary="Ask the recorder to start; completion arrives as capture.requestCompleted."),
    Method("capture.stop", "slow", ("capture", "stop"), fixed=("--wait-seconds", "0"), summary="Ask the recorder to stop."),
    Method("capture.test", "slow", ("capture", "test"), fixed=("--wait-seconds", "0"), summary="Ask for a microphone test."),
    Method("capture.recover", "slow", ("capture", "recover"), (P("session", "--session"),), fixed=("--wait-seconds", "0"),
           summary="Ask the recorder to recover an attempt."),
    Method("capture.confirmTest", "slow", ("capture", "confirm-test"), (P("audio", "--audio", required=True),
           P("audibleConfirmed", "--audible-confirmed", "bool", required=True)), summary="Record a listened microphone test."),
    # Transcription queue and recording library
    Method("queue.status", "fast", (*QUEUE, "status"), direct=_direct_queue_status, summary="Queue jobs and wait reason."),
    Method("queue.enqueue", "slow", (*QUEUE, "enqueue"), (P("allUntranscribed", "--all-untranscribed", "bool"),
           P("recordingId", "--recording"), P("readyRecordings", "--ready-recording", "list"), P("parallel", "--parallel", "int")),
           one_of=("allUntranscribed", "recordingId", "readyRecordings"), summary="Add recordings to the queue."),
    Method("queue.pause", "slow", (*QUEUE, "pause"), summary="Pause the queue."),
    Method("queue.resume", "slow", (*QUEUE, "resume"), summary="Resume the queue."),
    Method("queue.clear", "slow", (*QUEUE, "clear"), summary="Remove waiting jobs (audio kept)."),
    Method("queue.configure", "slow", (*QUEUE, "configure"), (P("parallel", "--parallel", "int", required=True),),
           summary="Simultaneous transcriptions, 1-4."),
    Method("queue.job", "slow", QUEUE, (P("action", None, "subcommand", required=True,
           choices=("retry", "restart", "recover", "remove", "cancel")), P("jobId", "--job", required=True)),
           summary="Change one queue job."),
    Method("library.list", "fast", (*QUEUE, "library"), (P("query", "--query"), P("date", "--date"), P("course", "--course"),
           P("sync", "--sync", "bool")), slow_if="sync", direct=_direct_library, summary="Recordings, lessons and transcripts."),
    Method("library.transcripts", "fast", (*QUEUE, "transcripts"), (P("query", "--query"), P("date", "--date"),
           P("course", "--course"), P("sync", "--sync", "bool")), slow_if="sync", summary="Transcript library."),
    Method("library.transcript", "fast", params=(P("recordingId", server=True), P("lectureId", server=True),
           P("maxBytes", kind="int", server=True)), one_of=("recordingId", "lectureId"), direct=_direct_transcript,
           summary="Text of one lesson's or recording's transcript; the path comes from the library."),
    Method("library.lessons", "fast", (*QUEUE, "lessons"), summary="Lesson registry."),
    Method("library.courseProfile", "fast", (*QUEUE, "course-profile"), (P("course", "--course", required=True),),
           summary="One course's lesson profile."),
    Method("library.editLesson", "slow", (*QUEUE, "lesson-edit"), (P("edit", "--file", "file", required=True),),
           summary="Lesson registry action (update, create, renumber, move-audio, ...)."),
    Method("library.renameTranscript", "slow", (*QUEUE, "rename-transcript"), (P("transcript", "--transcript", required=True),
           P("name", "--name", required=True)), summary="Rename a transcript."),
    Method("library.folder", "slow", (*QUEUE, "folder"), (P("path", "--path", required=True),), summary="Managed folder."),
    Method("library.relink", "slow", (*QUEUE, "relink"), (P("recordingId", "--recording", required=True),
           P("path", "--path", required=True)), summary="Point a recording to a moved file."),
    Method("library.download", "job", (*QUEUE, "download"), (P("recordingId", "--recording", required=True),),
           summary="Make a cloud-backed recording local."),
    Method("library.associate", "slow", (*QUEUE, "associate"), (P("recordingId", "--recording", required=True),
           P("course", "--course", required=True), P("lesson", "--lesson", "int")), summary="Assign a recording."),
    Method("library.rename", "slow", (*QUEUE, "rename"), (P("recordingId", "--recording", required=True),
           P("title", "--title", required=True)), summary="Recording title."),
    Method("library.inspectAudio", "slow", (*QUEUE, "inspect-audio"), (P("paths", kind="field", required=True),),
           request_file="--file", summary="Inspect audio files before import."),
    Method("library.importAudio", "slow", (*QUEUE, "import-audio"), (P("paths", kind="field"), P("items", kind="field"),
           P("eventId", kind="field", key="event_id"), P("course", kind="field"), P("lesson", kind="field")),
           request_file="--file", one_of=("paths", "items"), summary="Import audio files (copies them)."),
    Method("library.associateAudio", "slow", (*QUEUE, "associate-audio"), (P("recordingId", "--recording", required=True),
           P("eventId", kind="field", key="event_id"), P("course", kind="field"), P("lesson", kind="field")),
           request_file="--file", summary="Attach imported audio to a lesson."),
    *(Method(f"library.{name}", "slow", (*QUEUE, command), (P("recordingId", "--recording", required=True),),
             summary=summary) for name, command, summary in (
        ("preferAudio", "prefer-audio", "Prefer this audio source."), ("excludeAudio", "exclude-audio", "Exclude this audio source."),
        ("restoreAudio", "restore-audio", "Restore an excluded source."), ("prepareTrash", "prepare-trash", "Prepare a source for the trash."),
        ("finish", "finish", "Archive a completed transcription."))),
    Method("library.verify", "slow", (*QUEUE, "verify-library"), summary="Verify library files."),
    # Transcription, notes and the one-step lesson
    Method("transcription.status", "fast", ("transcription-status",), (LESSON, RECORDING), json_flag=True,
           one_of=("lectureId", "recordingId"), summary="One transcription's persisted progress."),
    Method("transcription.run", "job", ("transcribe",), (LESSON, RECORDING, P("audio", "--audio")), json_flag=True,
           one_of=("lectureId", "recordingId"), progress="asr", summary="Transcribe one lesson or recording."),
    Method("notes.plan", "slow", ("notes",), NOTES, json_flag=True, fixed=("--plan-only",),
           summary="Inspect sources for a notes request; no generation or writes."),
    Method("notes.run", "job", ("notes",), NOTES, json_flag=True, summary="Write notes, build Word, update the course."),
    Method("notes.batchStatus", "fast", ("batch-status",), (P("batchId", required=True),), json_flag=True,
           summary="Saved notes job progress."),
    Method("notes.history", "fast", ("history",), (P("course"),), summary="Restorable cumulative-Word backups."),
    Method("notes.undo", "slow", ("undo",), (P("course", required=True),), summary="Undo the last course update."),
    Method("notes.restore", "slow", ("restore",), (P("backupId", required=True),), summary="Restore one backup."),
    Method("notes.publish", "job", ("publish",), (P("lecture", required=True), P("candidate", "--candidate")),
           summary="Publish a validated Word candidate into the course handout (renders with LibreOffice)."),
    Method("lesson.process", "job", ("lesson", "process"), (P("lectureId", required=True), P("audio", "--audio"),
           P("transcript", "--transcript"), PROVIDER, P("quality", "--quality"), P("instructions", "--instructions")),
           json_flag=True, exclusive=("audio", "transcript"),
           summary="Check, transcribe only if needed (or import a transcript), prepare and write notes for one lesson."),
    Method("usage.get", "fast", ("usage",), (LESSON, P("batchId", "--batch")), json_flag=True,
           one_of=("lectureId", "batchId"), summary="Local provider attempt counters."),
    # Local components
    Method("word.verifyRenderer", "job", ("verify-word-renderer",), workspace=False, json_flag=True,
           summary="Render a generated Word file with LibreOffice."),
    Method("speech.installModel", "job", ("install-speech-model",), workspace=False, json_flag=True,
           summary="Download the verified speech model (Windows)."),
    Method("speech.verify", "job", ("verify-transcription",), (P("audio", "--audio"),
           P("language", "--language", choices=("en", "it"))), json_flag=True, summary="Run the local speech engine on a sample."),
)

BY_NAME = {method.name: method for method in METHODS}


def lane(method: Method, params: dict) -> str:
    return "slow" if method.slow_if and params.get(method.slow_if) else method.lane


def validate(method: Method, params: Any) -> dict:
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise RpcError(INVALID_PARAMS, "Parameters must be a JSON object")
    known = {param.name: param for param in method.params}
    unknown = sorted(set(params) - set(known))
    if unknown:
        raise RpcError(INVALID_PARAMS, f"Unknown parameter: {', '.join(unknown)}")
    for param in method.params:
        value = params.get(param.name)
        if value is None:
            if param.required:
                raise RpcError(INVALID_PARAMS, f"Missing parameter: {param.name}")
            continue
        _check_type(param, value)
    given = [name for name in method.one_of if params.get(name) not in (None, False, [], "")]
    if method.one_of and len(given) != 1:
        raise RpcError(INVALID_PARAMS, f"Give exactly one of: {', '.join(method.one_of)}")
    if len([name for name in method.exclusive if params.get(name) not in (None, False, [], "")]) > 1:
        raise RpcError(INVALID_PARAMS, f"Give at most one of: {', '.join(method.exclusive)}")
    return {key: value for key, value in params.items() if value is not None}


def _check_type(param: Param, value: Any) -> None:
    expected = {
        "str": lambda v: isinstance(v, str), "subcommand": lambda v: isinstance(v, str),
        "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "bool": lambda v: isinstance(v, bool),
        "list": lambda v: isinstance(v, list) and all(isinstance(i, str) for i in v),
        "pairs": lambda v: isinstance(v, dict) and all(isinstance(i, str) for i in v.values()),
    }.get(param.kind)
    if expected is not None and not expected(value):
        raise RpcError(INVALID_PARAMS, f"Wrong type for parameter: {param.name}")
    if param.choices and value not in param.choices:
        raise RpcError(INVALID_PARAMS, f"{param.name} must be one of: {', '.join(param.choices)}")
    if isinstance(value, str) and "\0" in value:
        raise RpcError(INVALID_PARAMS, f"Invalid text in parameter: {param.name}")


def request_directory(workspace: str) -> Path:
    # Same private location as the Windows app's request files.
    return Path(workspace).expanduser() / "state" / "desktop-requests"


def _request_file(workspace: str, value: Any) -> Path:
    directory = request_directory(workspace)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"{secrets.token_hex(16)}.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False)
    return path


def build_argv(method: Method, params: dict, workspace: str) -> tuple[list[str], list[Path]]:
    """CLI arguments for a validated call, plus request files to delete afterwards."""
    argv, flags, files = list(method.command), [], []
    fields: dict[str, Any] = {}
    try:
        for param in method.params:
            value = params.get(param.name)
            if value is None or param.server:
                continue
            if param.kind == "subcommand":
                argv.append(value)
            elif param.flag is None and param.kind != "field":
                argv.append(value)
            elif param.kind == "bool":
                if value:
                    flags.append(param.flag)
            elif param.kind == "list":
                for item in value:
                    flags += [param.flag, item]
            elif param.kind == "pairs":
                for key, text in value.items():
                    flags += [param.flag, key, text]
            elif param.kind == "json":
                flags += [param.flag, json.dumps(value, ensure_ascii=False)]
            elif param.kind == "file":
                files.append(_request_file(workspace, value))
                flags += [param.flag, str(files[-1])]
            elif param.kind == "field":
                fields[param.key or param.name] = value
            else:
                flags += [param.flag, str(value)]
        if method.request_file:
            files.append(_request_file(workspace, fields))
            flags += [method.request_file, str(files[-1])]
    except BaseException:
        remove(files)
        raise
    if method.workspace:
        flags += ["--workspace", workspace]
    if method.json_flag:
        flags.append("--json")
    return argv + flags + list(method.fixed), files


def remove(files: list[Path]) -> None:
    for path in files:
        try:
            path.unlink()
        except OSError:
            pass


def cli_result(invocation: Invocation) -> dict:
    """Map a command's exit code and output to an RPC result or error."""
    try:
        data = json.loads(invocation.stdout)
    except ValueError:
        data = _NOT_JSON
    status = data.get("status") if isinstance(data, dict) else None
    if data is not _NOT_JSON:
        detail = {"exitCode": invocation.exit_code, "data": data}
        if invocation.exit_code == 3 and status == "BUSY":
            raise RpcError(BUSY, str(data.get("message") or "Busy"), detail)
        if invocation.exit_code == 4 and status == "NEEDS_REVIEW":
            raise RpcError(NEEDS_REVIEW, str(data.get("message") or "Needs review"), detail)
        return detail
    if invocation.exit_code == 0:
        return {"exitCode": 0, "data": None, "text": invocation.stdout}
    lines = [line for line in invocation.stdout.splitlines() if line.strip()]
    failure = next((line for line in reversed(lines) if line.startswith("mynotes: ")), None)
    if failure is not None:
        message = failure[len("mynotes: "):]
    else:
        errors = [line for line in invocation.stderr.splitlines() if line.strip()]
        message = (lines or errors or ["Command failed"])[-1]
    raise RpcError(COMMAND_FAILED, message, {"exitCode": invocation.exit_code, "stdout": invocation.stdout,
                                              "stderr": invocation.stderr})


_NOT_JSON = object()


TYPES = {"str": "string", "subcommand": "string", "int": "integer", "float": "number", "bool": "boolean",
         "list": "string[]", "pairs": "object<string>", "json": "json", "file": "json", "field": "json"}


def describe(method: Method) -> dict:
    return {"name": method.name, "lane": method.lane, "params": [
        {"name": p.name, "type": TYPES[p.kind], "required": p.required,
         **({"choices": list(p.choices)} if p.choices else {})} for p in method.params],
        "command": ["mynotes", *method.command, *method.fixed] if method.command else None, "summary": method.summary}
