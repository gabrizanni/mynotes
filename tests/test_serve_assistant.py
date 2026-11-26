"""`assistant.*`: the chat agent streamed through `mynotes serve`, against fake provider CLIs.

Each fake is a Python script named `claude`, `codex` or `agy` on a private PATH
(HOME is private too, so no real agent can be found). It records its argv,
stdin, PID and process group, then plays a recorded stream from a plan file.
`tests/fixtures/assistant/claude-ready.jsonl` is one real Claude Code 2.1 run
(`--print --output-format stream-json --verbose --include-partial-messages`),
sanitized: ids are synthetic and paths, hooks and account data are removed.
"""
from __future__ import annotations

import json
import os
import signal
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from mynotes import serve_assistant
from mynotes.serve_assistant import (CLAUDE_ALLOWED, CLAUDE_TOOLS, CONTEXT_NOTE, PANEL_NOTE, REPLAY_NOTE, SESSION_NOT_FOUND,
                                     TURN_RUNNING, Assistant, TurnOutput, parse_agy, parse_codex, provider_failure)
from mynotes.providers import RequestedModel, provider_override_flags
from mynotes.serve_methods import INVALID_PARAMS, RpcError
from mynotes.workspace import Workspace

pytestmark = pytest.mark.skipif(os.name == "nt", reason="fake provider scripts use POSIX shebangs and process groups")

FIXTURES = Path(__file__).parent / "fixtures" / "assistant"

FAKE = """#!{python}
import json, os, signal, subprocess, sys, time
from pathlib import Path
name = Path(sys.argv[0]).name
argv = sys.argv[1:]
log = Path(os.environ["FAKE_PROVIDER_LOG"])
plan = json.loads(Path(os.environ["FAKE_PROVIDER_PLAN"]).read_text())
data = sys.stdin.buffer.readline() if name == "agy" else sys.stdin.buffer.read()
resumed = any(flag in argv for flag in ("--resume", "resume", "--conversation"))
turns = plan.get(name, plan)
steps = turns["resume"] if resumed and "resume" in turns else turns["first"]
def note(**row):
    with log.open("a") as handle:
        handle.write(json.dumps(row) + "\\n")
note(kind="start", name=name, argv=argv, stdin=data.decode("utf-8"), pid=os.getpid(), pgid=os.getpgid(0), cwd=os.getcwd(),
     env={{key: os.environ.get(key) for key in ("MYNOTES_WORKSPACE", "MYNOTES_CHAT_ACTIVE")}})
code = 0
for step in steps:
    if "out" in step:
        value = step["out"]
        sys.stdout.write((value if isinstance(value, str) else json.dumps(value)) + "\\n")
        sys.stdout.flush()
    elif "big" in step:
        sys.stdout.write("x" * step["big"] + "\\n")
        sys.stdout.flush()
    elif "stderr" in step:
        sys.stderr.write(step["stderr"] + "\\n")
        sys.stderr.flush()
    elif "sleep" in step:
        time.sleep(step["sleep"])
    elif "spawn" in step:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        note(kind="child", pid=child.pid)
    elif "ignore_signals" in step:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    elif "stdin_eof" in step:
        sys.stdin.buffer.read()
        note(kind="stdin_closed")
    elif "exit" in step:
        code = step["exit"]
sys.exit(code)
"""


# Recorded streams ---------------------------------------------------------------
def claude_stream(session: str, text: list[str], *, tool: bool = False) -> list[dict]:
    events = [{"out": {"type": "system", "subtype": "init", "session_id": session, "tools": ["Bash", "Read"]}},
              {"out": {"type": "stream_event", "event": {"type": "message_start", "message": {"content": []}}}},
              {"out": {"type": "stream_event", "event": {"type": "content_block_start", "index": 0,
                                                          "content_block": {"type": "text", "text": ""}}}}]
    events += [{"out": {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                                           "delta": {"type": "text_delta", "text": part}}}} for part in text]
    events.append({"out": {"type": "assistant", "message": {"content": [{"type": "text", "text": "".join(text)}]}}})
    if tool:
        command = "mynotes notes STAT --lessons 3 --json"
        events += [
            {"out": {"type": "stream_event", "event": {"type": "content_block_start", "index": 1,
                                                        "content_block": {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}}}},
            {"out": {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash",
                     "input": {"command": command, "description": "Prepare notes for STAT lesson 3"}}]}}},
            {"out": {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                     "content": "{\"status\": \"COMPLETED\"}", "is_error": False}]}}},
            {"out": {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_2", "name": "Bash",
                     "input": {"command": "rm -rf ~/Documents"}}]}}},
            {"out": {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_2", "is_error": True,
                     "content": "Claude requested permissions to use Bash, but you haven't granted it yet."}]}}},
            {"out": {"type": "stream_event", "event": {"type": "content_block_start", "index": 0,
                                                        "content_block": {"type": "text", "text": ""}}}},
            {"out": {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                                        "delta": {"type": "text_delta", "text": "Notes are ready."}}}},
        ]
    events.append({"out": {"type": "result", "subtype": "success", "is_error": False, "result": "".join(text),
                           "session_id": session, "usage": {"input_tokens": 3, "output_tokens": 5}, "total_cost_usd": 0.01}})
    return events


CODEX_FIRST = [
    {"out": {"type": "thread.started", "thread_id": "thread-1"}},
    {"out": {"type": "turn.started"}},
    {"out": {"type": "item.started", "item": {"id": "item_0", "type": "command_execution",
                                              "command": "bash -lc 'mynotes status'", "status": "in_progress"}}},
    {"out": {"type": "item.completed", "item": {"id": "item_0", "type": "command_execution", "command": "bash -lc 'mynotes status'",
                                                "exit_code": 0, "status": "completed", "aggregated_output": "ok"}}},
    {"out": {"type": "item.completed", "item": {"id": "item_1", "type": "reasoning", "text": "private reasoning"}}},
    {"out": {"type": "item.completed", "item": {"id": "item_2", "type": "agent_message", "text": "Everything is ready."}}},
    {"out": {"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 4}}},
]
CODEX_RESUME = [
    {"out": {"type": "thread.started", "thread_id": "thread-1"}},
    {"out": {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "Second answer."}}},
    {"out": {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}},
]
AGY_FIRST = [
    {"out": {"event": "init", "conversation_id": "conv-1"}},
    {"out": {"event": "step_update", "step_update": {"step_index": 1, "step_type": "tool", "state": "RUNNING", "tool_name": "run_command",
                                                     "tool_info": {"parameters": {"CommandLine": "mynotes context --activity notes"}}}}},
    {"out": {"event": "step_update", "step_update": {"step_index": 1, "step_type": "tool", "state": "DONE", "tool_name": "run_command",
                                                     "tool_info": {"parameters": {"CommandLine": "mynotes context --activity notes"}, "output": "ok"}}}},
    {"out": {"event": "step_update", "step_update": {"step_index": 2, "step_type": "agent_response", "state": "RUNNING", "text": "Hel"}}},
    {"out": {"event": "step_update", "step_update": {"step_index": 2, "step_type": "agent_response", "state": "DONE", "text": "Hello there"}}},
    {"out": {"event": "result", "result": {"status": "SUCCESS", "response": "Hello there", "conversation_id": "conv-1"}}},
    {"stdin_eof": True},
]


# Harness ---------------------------------------------------------------------------
OPENED: list[Assistant] = []


def open_assistant(workspace: Workspace, recorder) -> Assistant:
    assistant = Assistant(str(workspace.root), recorder)
    OPENED.append(assistant)
    return assistant


@pytest.fixture(autouse=True)
def close_assistants():
    """A failing test must not leave a fake provider running."""
    yield
    while OPENED:
        OPENED.pop().close()


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.condition = threading.Condition()

    def __call__(self, method: str, params: dict) -> None:
        with self.condition:
            self.events.append((method, json.loads(json.dumps(params))))
            self.condition.notify_all()

    def wait(self, method: str, timeout: float = 20, **match) -> dict:
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                for name, params in self.events:
                    if name == method and all(params.get(key) == value for key, value in match.items()):
                        return params
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"no {method} {match}: {self.events}")
                self.condition.wait(remaining)

    def end(self, turn_id: str, timeout: float = 20) -> tuple[str, dict]:
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                for name, params in self.events:
                    if name in {"assistant.done", "assistant.error"} and params.get("turnId") == turn_id:
                        return name, params
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"turn {turn_id} did not end: {self.events}")
                self.condition.wait(remaining)

    def of(self, method: str, turn_id: str) -> list[dict]:
        with self.condition:
            return [params for name, params in self.events if name == method and params.get("turnId") == turn_id]


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    """Private HOME and PATH with fake claude, codex and agy; returns (workspace, plan path, log path)."""
    home, tools = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    tools.mkdir()
    for name in ("claude", "codex", "agy"):
        path = tools / name
        path.write_text(FAKE.format(python=sys.executable), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    plan, log = tmp_path / "plan.json", tmp_path / "provider.log"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", os.pathsep.join([str(tools), "/usr/bin", "/bin"]))
    monkeypatch.setenv("FAKE_PROVIDER_PLAN", str(plan))
    monkeypatch.setenv("FAKE_PROVIDER_LOG", str(log))
    monkeypatch.delenv("MYNOTES_CHAT_ACTIVE", raising=False)
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()
    configuration = workspace.configuration_store().load()
    configuration["onboarding"]["status"] = "complete"
    configuration["courses"] = [{"code": "STAT", "name": "Statistics"}]
    configuration["providers"]["configured"] = {"claude": {"model": "haiku", "effort": "low"}}
    workspace.configuration_store().save(configuration)
    return workspace, plan, log


def write_plan(plan: Path, **turns) -> None:
    plan.write_text(json.dumps(turns), encoding="utf-8")


def runs(log: Path, kind: str = "start") -> list[dict]:
    if not log.exists():
        return []
    return [row for row in map(json.loads, log.read_text().splitlines()) if row["kind"] == kind]


def start(assistant: Assistant, **params) -> dict:
    answer = assistant.dispatch("assistant.start", params)
    answer.then()
    return answer.result


def send(assistant: Assistant, **params) -> dict:
    answer = assistant.dispatch("assistant.send", params)
    answer.then()
    return answer.result


def gone(pid: int, timeout: float = 10) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        try:  # A zombie child of this test process still answers kill(0).
            finished, _ = os.waitpid(pid, os.WNOHANG)
            if finished:
                return True
        except ChildProcessError:
            pass
        time.sleep(0.05)
    return False


# Claude Code --------------------------------------------------------------------------
def test_real_claude_fixture_streams_a_delta_and_finishes(fakes):
    workspace, plan, log = fakes
    stream = [{"out": line} for line in (FIXTURES / "claude-ready.jsonl").read_text().splitlines()]
    write_plan(plan, first=stream)
    recorder = Recorder()
    assistant = open_assistant(workspace, recorder)
    started = start(assistant, provider="claude", message="Reply with the single word: ready")
    name, done = recorder.end(started["turnId"])
    assert name == "assistant.done", done
    assert [row["text"] for row in recorder.of("assistant.delta", started["turnId"])] == ["ready"]
    assert done["text"] == "ready"
    assert done["providerSession"] == "00000000-0000-4000-8000-000000000001"
    assert done["usage"]["output_tokens"] == 4 and done["usage"]["total_cost_usd"] > 0
    record = assistant.dispatch("assistant.get", {"sessionId": started["sessionId"]})
    assert record["turns"][0]["state"] == "COMPLETED" and record["turns"][0]["invalidLines"] == 0


def test_claude_argv_prompt_tools_and_resume(fakes):
    workspace, plan, log = fakes
    write_plan(plan, first=claude_stream("sess-1", ["Let me ", "check."], tool=True),
               resume=claude_stream("sess-1", ["Second."]))
    recorder = Recorder()
    assistant = open_assistant(workspace, recorder)
    started = start(assistant, provider="claude", message="Prepare notes for lesson 3",
                    context={"course": "STAT", "lesson": 3, "page": "Lesson 3 · Statistics"})
    name, done = recorder.end(started["turnId"])
    assert name == "assistant.done", done
    assert done["text"] == "Let me check.\n\nNotes are ready."
    first = runs(log)[0]
    executable = str(workspace.root.parent / "bin" / "claude")
    assert first["argv"] == ["--print", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                             "--permission-mode", "dontAsk", "--tools", CLAUDE_TOOLS, "--allowedTools", CLAUDE_ALLOWED,
                             "--append-system-prompt-file", str(workspace.root / "AGENTS.md"),
                             "--model", "haiku", "--effort", "low"]
    assert not any("dangerously" in part or "bypass" in part for part in first["argv"])
    assert first["cwd"] == str(workspace.root)
    assert first["env"] == {"MYNOTES_WORKSPACE": str(workspace.root), "MYNOTES_CHAT_ACTIVE": "1"}
    prompt = first["stdin"]
    assert prompt.startswith("MyNotes startup context: workspace=")
    assert "User request: Prepare notes for lesson 3" in prompt
    assert CONTEXT_NOTE + '{"course": "STAT", "lesson": 3, "page": "Lesson 3 · Statistics"}' in prompt
    assert prompt.endswith(PANEL_NOTE)
    assert Path(executable).is_file()
    tools = {row["toolId"]: row for row in recorder.of("assistant.tool", started["turnId"])}
    assert tools["toolu_1"]["status"] == "completed"
    assert tools["toolu_1"]["summary"] == "Prepare notes for STAT lesson 3"
    assert tools["toolu_1"]["command"] == "mynotes notes STAT --lessons 3 --json"
    assert tools["toolu_2"]["status"] == "denied"

    second = send(assistant, sessionId=started["sessionId"], message="And lesson 4?")
    assert second["turnId"] == "turn-2"
    name, done = recorder.end("turn-2")
    assert name == "assistant.done" and done["text"] == "Second."
    resumed = runs(log)[1]
    assert resumed["argv"][-2:] == ["--resume", "sess-1"]
    assert resumed["stdin"] == "And lesson 4?"
    record = assistant.dispatch("assistant.get", {"sessionId": started["sessionId"]})
    assert record["providerSession"] == "sess-1" and record["resume"] == "native"
    assert [turn["state"] for turn in record["turns"]] == ["COMPLETED", "COMPLETED"]
    assert [tool["status"] for tool in record["turns"][0]["tools"]] == ["completed", "denied"]


def test_transcript_is_private_and_a_crash_reads_back_interrupted(fakes):
    workspace, plan, log = fakes
    write_plan(plan, first=claude_stream("sess-2", ["Hi."]))
    recorder = Recorder()
    assistant = open_assistant(workspace, recorder)
    started = start(assistant, provider="claude", message="Hello")
    recorder.end(started["turnId"])
    path = workspace.root / "state" / "assistant" / f"{started['sessionId']}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    record = json.loads(path.read_text())
    record["turns"].append({**record["turns"][0], "id": "turn-2", "state": "RUNNING"})
    path.write_text(json.dumps(record))
    reloaded = open_assistant(workspace, Recorder())
    listed = reloaded.dispatch("assistant.list", {})["sessions"]
    assert [row["id"] for row in listed] == [started["sessionId"]] and listed[0]["state"] == "INTERRUPTED"
    assert reloaded.dispatch("assistant.get", {"sessionId": started["sessionId"]})["turns"][1]["error"]["code"] == "INTERRUPTED"


# Codex and Antigravity ------------------------------------------------------------------
def test_codex_exec_json_and_resume(fakes):
    workspace, plan, log = fakes
    write_plan(plan, first=CODEX_FIRST, resume=CODEX_RESUME)
    recorder = Recorder()
    assistant = open_assistant(workspace, recorder)
    started = start(assistant, provider="codex", message="Check my setup")
    name, done = recorder.end(started["turnId"])
    assert name == "assistant.done" and done["text"] == "Everything is ready."
    assert done["providerSession"] == "thread-1" and done["usage"]["input_tokens"] == 10
    first = runs(log)[0]
    record = assistant.dispatch("assistant.get", {"sessionId": started["sessionId"]})
    overrides = provider_override_flags(RequestedModel("codex", record["model"], record["effort"], "standard"))
    assert overrides, "the configured Codex model is passed as in `mynotes chat`"
    assert first["argv"] == ["exec", "--json", "--skip-git-repo-check", "-C", str(workspace.root), "--sandbox", "workspace-write",
                             *overrides, "-"]
    assert "CODEX_LAUNCH_DENIED" in first["stdin"] and "User request: Check my setup" in first["stdin"]
    tool = recorder.of("assistant.tool", started["turnId"])[-1]
    assert tool == {**tool, "toolId": "item_0", "name": "shell", "status": "completed", "command": "bash -lc 'mynotes status'"}
    send(assistant, sessionId=started["sessionId"], message="Next")
    name, done = recorder.end("turn-2")
    assert name == "assistant.done" and done["text"] == "Second answer."
    assert runs(log)[1]["argv"] == first["argv"][:-1] + ["resume", "thread-1", "-"]


def test_antigravity_streams_like_agy_progressive_and_resumes_the_conversation(fakes):
    workspace, plan, log = fakes
    write_plan(plan, first=AGY_FIRST, resume=AGY_FIRST)
    recorder = Recorder()
    assistant = open_assistant(workspace, recorder)
    started = start(assistant, provider="agy", message="Hello")
    name, done = recorder.end(started["turnId"])
    assert name == "assistant.done" and done["text"] == "Hello there"
    assert [row["text"] for row in recorder.of("assistant.delta", started["turnId"])] == ["Hel", "lo there"]
    first = runs(log)[0]
    record = assistant.dispatch("assistant.get", {"sessionId": started["sessionId"]})
    overrides = provider_override_flags(RequestedModel("agy", record["model"], record["effort"], "standard"))
    assert first["argv"] == ["--agent", "mynotes", "--mode", "accept-edits", "--add-dir", str(workspace.root), *overrides,
                             "--input-format", "stream-json", "--output-format", "stream-json", "--print-timeout", "0"]
    message = json.loads(first["stdin"])
    assert message["event"] == "user" and "User request: Hello" in message["message"]["content"]
    assert runs(log, "stdin_closed"), "stdin is closed after the result event"
    send(assistant, sessionId=started["sessionId"], message="Again")
    recorder.end("turn-2")
    assert runs(log)[1]["argv"][-2:] == ["--conversation", "conv-1"]


def test_without_a_provider_session_the_next_turn_replays_a_bounded_transcript(fakes):
    workspace, plan, log = fakes
    no_session = [step for step in AGY_FIRST if step.get("out", {}).get("event") != "init"]
    for step in no_session:
        if step.get("out", {}).get("event") == "result":
            step["out"]["result"].pop("conversation_id")
    write_plan(plan, first=no_session)
    recorder = Recorder()
    assistant = open_assistant(workspace, recorder)
    started = start(assistant, provider="agy", message="First question")
    recorder.end(started["turnId"])
    send(assistant, sessionId=started["sessionId"], message="Second question")
    recorder.end("turn-2")
    second = runs(log)[1]
    assert "--conversation" not in second["argv"]
    content = json.loads(second["stdin"])["message"]["content"]
    assert REPLAY_NOTE in content and "User: First question\nAssistant: Hello there" in content
    assert "New message: Second question" in content
    assert assistant.dispatch("assistant.get", {"sessionId": started["sessionId"]})["resume"] == "replay"


# Failures -----------------------------------------------------------------------------
@pytest.mark.parametrize(("provider", "steps", "code"), [
    ("claude", [{"out": {"type": "system", "subtype": "init", "session_id": "s"}},
                {"out": {"type": "result", "subtype": "success", "is_error": True, "session_id": "s",
                         "result": "Claude AI usage limit reached · resets 5pm"}}], "QUOTA"),
    ("claude", [{"out": {"type": "assistant", "error": "authentication_failed",
                         "message": {"content": [{"type": "text", "text": "Invalid API key · Please run /login"}]}}},
                {"out": {"type": "result", "subtype": "success", "is_error": True, "result": "Invalid API key · Please run /login"}},
                {"exit": 1}], "AUTH"),
    ("codex", [{"out": {"type": "thread.started", "thread_id": "t"}},
               {"out": {"type": "error", "message": "Reconnecting... 1/5"}},
               {"out": {"type": "turn.failed", "error": {"message": "stream disconnected before completion: error sending request"}}},
               {"exit": 1}], "NETWORK"),
    ("codex", [{"stderr": "Error: Not logged in. Run codex login"}, {"exit": 1}], "AUTH"),
    ("agy", [{"out": {"event": "result", "result": {"status": "ERROR", "error": "You have exhausted your quota"}}}], "QUOTA"),
    ("claude", [{"out": "not json at all"}, {"exit": 2}], "PROVIDER"),
])
def test_provider_failures_map_to_the_provider_error_classes(fakes, provider, steps, code):
    workspace, plan, log = fakes
    write_plan(plan, first=steps)
    recorder = Recorder()
    assistant = open_assistant(workspace, recorder)
    started = start(assistant, provider=provider, message="Hello")
    name, error = recorder.end(started["turnId"])
    assert name == "assistant.error" and error["code"] == code, error
    assert error["message"]
    record = assistant.dispatch("assistant.get", {"sessionId": started["sessionId"]})
    assert record["turns"][0]["state"] == "FAILED" and record["turns"][0]["error"]["code"] == code


def test_missing_cli_is_unavailable(fakes, monkeypatch):
    workspace, plan, log = fakes
    monkeypatch.setattr(serve_assistant, "provider_executable", lambda provider: None)
    recorder = Recorder()
    assistant = open_assistant(workspace, recorder)
    started = start(assistant, provider="claude", message="Hello")
    name, error = recorder.end(started["turnId"])
    assert (name, error["code"]) == ("assistant.error", "UNAVAILABLE")
    assert "code.claude.com" in error["message"] and not runs(log)


def test_malformed_unknown_and_oversized_lines_never_stop_a_turn(fakes, monkeypatch):
    workspace, plan, log = fakes
    monkeypatch.setattr(serve_assistant, "MAX_LINE_BYTES", 4096)
    stream = claude_stream("sess-3", ["Fine."])
    stream[1:1] = [{"out": "{broken json"}, {"out": "[1, 2, 3]"}, {"out": {"type": "mystery", "payload": {"x": 1}}},
                   {"big": 20000}, {"out": "\x00\x01"},
                   {"out": {"type": "stream_event", "event": "not an object"}},
                   {"out": {"type": "assistant", "message": {"content": "not a list"}}}]
    write_plan(plan, first=stream)
    recorder = Recorder()
    assistant = open_assistant(workspace, recorder)
    started = start(assistant, provider="claude", message="Hello")
    name, done = recorder.end(started["turnId"])
    assert name == "assistant.done" and done["text"] == "Fine."
    turn = assistant.dispatch("assistant.get", {"sessionId": started["sessionId"]})["turns"][0]
    assert turn["invalidLines"] == 4  # broken JSON, an array, control bytes, one oversized line


def test_reply_and_tool_rows_are_bounded():
    output = TurnOutput(lambda text: None, lambda row: None)
    output.delta("x" * (serve_assistant.MAX_REPLY_CHARS + 10))
    assert len(output.text) == serve_assistant.MAX_REPLY_CHARS and output.truncated
    for index in range(serve_assistant.MAX_TOOLS_PER_TURN + 5):
        output.tool(f"t{index}", name="Bash", summary="s" * 500, command="c" * 10_000)
    assert len(output.tools) == serve_assistant.MAX_TOOLS_PER_TURN
    row = next(iter(output.tools.values()))
    assert len(row["summary"]) == serve_assistant.MAX_SUMMARY_CHARS and len(row["command"]) == serve_assistant.MAX_COMMAND_CHARS


def test_unknown_codex_and_agy_events_are_ignored():
    output = TurnOutput(lambda text: None, lambda row: None)
    for event in ({"type": "item.completed", "item": "x"}, {"type": "todo"}, {"type": "item.completed", "item": {"type": "todo_list"}}):
        parse_codex(output, event)
    for event in ({"event": "step_update", "step_update": "x"}, {"event": "telemetry"}, {"event": "result", "result": None}):
        parse_agy(output, event)
    assert output.text == "" and not output.tools and not output.success


def test_failure_classification_uses_the_existing_classes():
    from mynotes.providers import (ProviderAuthenticationError, ProviderError, ProviderQuotaError,
                                   ProviderTransportError)
    assert isinstance(provider_failure("rate_limit_error: slow down"), ProviderQuotaError)
    assert isinstance(provider_failure("anything", "authentication_failed"), ProviderAuthenticationError)
    assert isinstance(provider_failure("getaddrinfo ENOTFOUND api.example.com"), ProviderTransportError)
    assert type(provider_failure("Something else went wrong")) is ProviderError


# Cancellation ---------------------------------------------------------------------------
@pytest.mark.parametrize("stubborn", [False, True], ids=["interrupt", "kill"])
def test_cancel_mid_stream_leaves_no_process(fakes, monkeypatch, stubborn):
    workspace, plan, log = fakes
    monkeypatch.setattr(serve_assistant, "CANCEL_STEP_SECONDS", 0.5)
    steps = [{"ignore_signals": True}] if stubborn else []
    steps += [{"out": {"type": "system", "subtype": "init", "session_id": "sess-4"}}, {"spawn": True},
              {"out": {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Working"}}}},
              {"sleep": 60}]
    write_plan(plan, first=steps)
    recorder = Recorder()
    assistant = open_assistant(workspace, recorder)
    started = start(assistant, provider="claude", message="Long task")
    recorder.wait("assistant.delta", turnId=started["turnId"])
    with pytest.raises(RpcError) as busy:
        assistant.dispatch("assistant.send", {"sessionId": started["sessionId"], "message": "More"})
    assert busy.value.code == TURN_RUNNING
    live = assistant.dispatch("assistant.get", {"sessionId": started["sessionId"]})["turns"][0]
    assert (live["state"], live["text"]) == ("RUNNING", "Working")
    begun = time.monotonic()
    assert assistant.dispatch("assistant.cancel", {"sessionId": started["sessionId"]})["cancelRequested"] is True
    name, error = recorder.end(started["turnId"])
    assert (name, error["code"]) == ("assistant.error", "CANCELLED")
    assert time.monotonic() - begun < 8
    provider = runs(log)[0]
    child = runs(log, "child")[0]
    assert gone(provider["pid"]) and gone(child["pid"])
    with pytest.raises(ProcessLookupError):
        os.killpg(provider["pgid"], 0)
    turn = assistant.dispatch("assistant.get", {"sessionId": started["sessionId"]})["turns"][0]
    assert turn["state"] == "CANCELLED" and turn["text"] == "Working"
    assert assistant.dispatch("assistant.cancel", {"sessionId": started["sessionId"]})["cancelRequested"] is False


def test_close_kills_running_replies(fakes):
    workspace, plan, log = fakes
    write_plan(plan, first=[{"out": {"type": "system", "subtype": "init", "session_id": "s"}}, {"spawn": True}, {"sleep": 60}])
    recorder = Recorder()
    assistant = open_assistant(workspace, recorder)
    started = start(assistant, provider="claude", message="Long task")
    deadline = time.monotonic() + 10
    while not runs(log, "child") and time.monotonic() < deadline:
        time.sleep(0.05)
    assistant.close()
    name, error = recorder.end(started["turnId"])
    assert error["code"] == "CANCELLED"
    assert gone(runs(log)[0]["pid"]) and gone(runs(log, "child")[0]["pid"])


def test_concurrent_starts_never_exceed_the_reply_limit(fakes, monkeypatch):
    workspace, _, log = fakes
    assistant = open_assistant(workspace, Recorder())
    original = Assistant._new_turn

    def slow_new_turn(self, session, params):
        time.sleep(0.05)  # Widen the gap between the capacity check and the RUNNING record.
        return original(self, session, params)

    monkeypatch.setattr(Assistant, "_new_turn", slow_new_turn)
    attempts = 2 * serve_assistant.MAX_RUNNING_TURNS
    barrier, accepted, refused = threading.Barrier(attempts), [], []

    def attempt():
        barrier.wait()
        try:  # Answers only: no reply is launched, so nothing ends meanwhile.
            accepted.append(assistant.dispatch("assistant.start", {"provider": "claude", "message": "Hi"}).result)
        except RpcError as error:
            refused.append(error.code)

    threads = [threading.Thread(target=attempt) for _ in range(attempts)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert len(accepted) == serve_assistant.MAX_RUNNING_TURNS and assistant.running() == serve_assistant.MAX_RUNNING_TURNS
    assert refused == [TURN_RUNNING] * serve_assistant.MAX_RUNNING_TURNS
    first = accepted[0]["sessionId"]
    assistant.dispatch("assistant.cancel", {"sessionId": first})  # Not launched yet: recorded on the turn.
    record = assistant.dispatch("assistant.get", {"sessionId": first})
    assert record["turns"][-1]["cancelRequested"] is True and not runs(log)


# Parameters -----------------------------------------------------------------------------
@pytest.mark.parametrize(("method", "params", "code"), [
    ("assistant.start", {}, INVALID_PARAMS),
    ("assistant.start", {"message": "   "}, INVALID_PARAMS),
    ("assistant.start", {"message": "x" * 20_001}, INVALID_PARAMS),
    ("assistant.start", {"message": "hi", "provider": "other"}, INVALID_PARAMS),
    ("assistant.start", {"message": "hi", "model": "bad model; rm"}, INVALID_PARAMS),
    ("assistant.start", {"message": "hi", "context": {"course": "../etc"}}, INVALID_PARAMS),
    ("assistant.start", {"message": "hi", "context": {"script": "x"}}, INVALID_PARAMS),
    ("assistant.start", {"message": "hi", "verbose": True}, INVALID_PARAMS),
    ("assistant.send", {"sessionId": "as-0000000000000000", "message": "hi"}, SESSION_NOT_FOUND),
    ("assistant.get", {"sessionId": "../../config"}, SESSION_NOT_FOUND),
    ("assistant.list", {"limit": 0}, INVALID_PARAMS),
    ("assistant.cancel", {}, INVALID_PARAMS),
])
def test_parameters_are_checked(fakes, method, params, code):
    workspace, _, _ = fakes
    with pytest.raises(RpcError) as error:
        open_assistant(workspace, Recorder()).dispatch(method, params)
    assert error.value.code == code


# Through `mynotes serve --stdio` --------------------------------------------------------------
def test_serve_streams_the_assistant_and_shutdown_leaves_no_provider(tmp_path, monkeypatch):
    from serve_helpers import Client, build_workspace, environment
    workspace = build_workspace(tmp_path, monkeypatch)
    env = environment(tmp_path)
    tools = tmp_path / "bin"
    path = tools / "claude"
    path.write_text(FAKE.format(python=sys.executable), encoding="utf-8")
    path.chmod(0o755)
    plan, log = tmp_path / "plan.json", tmp_path / "provider.log"
    write_plan(plan, first=claude_stream("sess-serve", ["Hello ", "from the panel."], tool=True),
               resume=[{"out": {"type": "system", "subtype": "init", "session_id": "sess-serve"}}, {"spawn": True}, {"sleep": 60}])
    env.update(FAKE_PROVIDER_PLAN=str(plan), FAKE_PROVIDER_LOG=str(log))
    client = Client(workspace.root, env)
    try:
        hello = client.call("system.hello", {"protocol": 1})
        assert {"assistant.start", "assistant.send", "assistant.cancel", "assistant.list", "assistant.get"} <= set(hello["methods"])
        started = client.call("assistant.start", {"provider": "claude", "message": "Hi", "context": {"course": "DEMO"}})
        done = client.notification("assistant.done", where=lambda p: p["turnId"] == started["turnId"])
        assert done["text"] == "Hello from the panel.\n\nNotes are ready." and done["providerSession"] == "sess-serve"
        methods = [message["method"] for message in client.drain()]
        assert methods.index("assistant.delta") < methods.index("assistant.tool")
        listed = client.call("assistant.list")
        assert listed["sessions"][0]["id"] == started["sessionId"]
        second = client.call("assistant.send", {"sessionId": started["sessionId"], "message": "Again"})
        deadline = time.monotonic() + 10
        while not runs(log, "child") and time.monotonic() < deadline:
            time.sleep(0.05)
        assert client.request("assistant.send", {"sessionId": started["sessionId"], "message": "x"})["error"]["code"] == TURN_RUNNING
        stats = client.call("system.stats")
        assert stats["assistantRunning"] == 1
    finally:
        assert client.close() == 0
    assert second["turnId"] == "turn-2"
    assert gone(runs(log)[1]["pid"]) and gone(runs(log, "child")[0]["pid"])
    record = json.loads((workspace.root / "state" / "assistant" / f"{started['sessionId']}.json").read_text())
    assert [turn["state"] for turn in record["turns"]] == ["COMPLETED", "CANCELLED"]
