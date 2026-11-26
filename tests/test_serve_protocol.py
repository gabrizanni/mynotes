"""JSON-RPC framing, errors, lanes and channel isolation of `mynotes serve --stdio`."""
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from mynotes import cli_invoke
from mynotes.serve_methods import BY_NAME, METHODS, RpcError, build_argv, cli_result, validate
from serve_helpers import COURSE, Client, build_workspace, cli, environment


@pytest.fixture
def served(tmp_path, monkeypatch):
    workspace = build_workspace(tmp_path, monkeypatch)
    env = environment(tmp_path)
    client = Client(workspace.root, env)
    yield client, workspace, env
    assert client.close() == 0


def test_hello_lists_the_same_methods_as_list_methods(served):
    client, _, env = served
    hello = client.call("system.hello", {"protocol": 1})
    listed = json.loads(cli(env, "serve", "--list-methods", "--json").stdout)
    assert hello["protocol"] == listed["protocol"] == 1
    assert hello["methods"] == [row["name"] for row in listed["methods"]]
    assert {"capture.dashboard", "queue.status", "job.cancel", "watch.subscribe"} <= set(hello["methods"])
    assert client.request("system.hello", {"protocol": 2})["error"]["code"] == -32005


def test_framing_and_request_errors(served):
    client, _, _ = served
    client.write(b"not json\n")
    client.write(b"[" + json.dumps({"jsonrpc": "2.0", "id": 9, "method": "system.ping"}).encode() + b"]\n")
    client.write(json.dumps({"jsonrpc": "1.0", "id": 10, "method": "system.ping"}).encode() + b"\n")
    client.write(b"\xff\xfe\n")
    assert client.response(None)["error"]["code"] == -32700
    assert client.response(10)["error"]["code"] == -32600
    errors = {
        "missing method": client.request("no.such.method"),
        "array params": client.request("config.get", [1]),
        "unknown param": client.request("config.get", {"verbose": True}),
        "wrong type": client.request("queue.configure", {"parallel": "2"}),
        "missing": client.request("capture.source"),
        "choice": client.request("models.catalog", {"provider": "other"}),
        "one of": client.request("transcription.status", {}),
    }
    assert errors.pop("missing method")["error"]["code"] == -32601
    assert {name: reply["error"]["code"] for name, reply in errors.items()} == dict.fromkeys(errors, -32602)
    first = client.send("system.ping")
    assert client.response(first)["result"]["ok"] is True


def test_oversized_message_is_rejected_and_the_server_continues(served):
    client, _, _ = served
    client.write(b'{"jsonrpc":"2.0","id":1,"method":"system.ping","params":{"pad":"' + b"x" * (16 * 1024 * 1024) + b'"}}\n')
    assert client.response(None, timeout=120)["error"]["message"] == "Message larger than 16 MiB"
    assert client.call("system.ping")["ok"] is True


def test_cli_results_keep_exit_codes_and_plain_failures(served, tmp_path):
    client, _, _ = served
    status = client.call("transcription.status", {"lectureId": f"{COURSE}-L03"})
    assert status == {"exitCode": 0, "data": {"lecture_id": f"{COURSE}-L03", "status": "NOT_STARTED"}}
    unselected = client.request("notes.plan", {"course": COURSE, "lessons": "1"})["error"]
    assert unselected["code"] == -32001 and unselected["message"].startswith("No provider is selected")
    failure = client.request("course.edit", {"code": "MISSING", "course": {"code": "MISSING"}})["error"]
    assert failure["code"] == -32001 and failure["message"].startswith("Course not found")
    assert failure["data"]["exitCode"] == 2 and failure["data"]["stdout"].startswith("mynotes: ")
    assert not list((tmp_path / "workspace/state/desktop-requests").glob("*.json"))


def test_concurrent_requests_get_their_own_output(served):
    client, _, _ = served
    methods = ["config.get", "capture.dashboard", "course.pending", "memory.show", "style.show", "library.lessons"]
    params = {"memory.show": {"scope": "global", "area": "notes"}}
    expected = {name: client.call(name, params.get(name)) for name in methods}
    identifiers = {client.send(name, params.get(name)): name for name in methods * 6}
    for identifier, name in identifiers.items():
        assert client.response(identifier)["result"] == expected[name]


def test_cancel_request_stops_a_call_that_has_not_started(served, tmp_path):
    client, _, _ = served
    # Two slow probes occupy both slow-lane threads; the third call waits.
    probe = tmp_path / "bin" / "codex"
    probe.write_text("#!/bin/sh\nsleep 2\necho codex 0.0\n")
    probe.chmod(0o755)
    (tmp_path / "bin" / "claude").write_text(probe.read_text())
    (tmp_path / "bin" / "claude").chmod(0o755)
    busy = [client.send("doctor.get", {"provider": name, "refresh": True}) for name in ("codex", "claude")]
    waiting = client.send("queue.pause")
    client.write(json.dumps({"jsonrpc": "2.0", "method": "$/cancelRequest", "params": {"id": waiting}}).encode() + b"\n")
    assert client.response(waiting)["error"]["code"] == -32800
    for identifier in busy:
        assert client.response(identifier)["result"]["data"]["checks"]["agent"]["status"] in {"incompatible", "unverified"}
    assert client.call("queue.status")["data"]["paused"] is False


def test_doctor_is_cached_per_provider(served):
    client, _, _ = served
    first = client.call("doctor.get")
    assert first["cached"] is False and first["exitCode"] == 1
    assert client.call("doctor.get")["cached"] is True
    assert client.call("doctor.get", {"refresh": True})["cached"] is False


def test_channel_isolation_routes_strays_to_stderr(tmp_path):
    # Constant shell commands stand in for a child that inherits fds 0 and 1.
    script = (
        "import os, sys\n"
        "from mynotes.serve import protect_channel\n"
        "rpc_in, rpc_out = protect_channel()\n"
        "print('stray print')\n"
        "os.system('echo stray child')\n"
        "child_input = os.popen('cat').read()\n"
        "rpc_out.write(b'{\"jsonrpc\":\"2.0\",\"method\":\"ready\"}\\n')\n"
        "rpc_out.write(rpc_in.readline())\n"
        "print(repr(child_input), file=sys.stderr)\n"
    )
    completed = subprocess.run([sys.executable, "-c", script], input=b"channel line\n", capture_output=True, timeout=60)
    assert completed.stdout == b'{"jsonrpc":"2.0","method":"ready"}\nchannel line\n'
    assert b"stray print" in completed.stderr and b"stray child" in completed.stderr
    assert b"''" in completed.stderr  # A child reading stdin gets end of file, never the channel.


def test_invoke_is_byte_identical_and_isolates_threads(tmp_path, monkeypatch, capsys):
    workspace = build_workspace(tmp_path, monkeypatch)
    ws = ["--workspace", str(workspace.root)]
    commands = [["config", "show", "--json", *ws], ["capture", "dashboard", *ws], ["course", "pending", "--json", *ws],
                ["--version"], ["capture", "nope"], ["transcription-queue", "lessons", *ws]]
    direct = []
    from mynotes.cli import main
    for argv in commands:
        try:
            code = main(argv)
        except SystemExit as exit:
            code = exit.code
        captured = capsys.readouterr()
        direct.append((code, captured.out, captured.err))
    saved = sys.stdout, sys.stderr, sys.stdin
    try:
        with ThreadPoolExecutor(6) as pool:
            results = list(pool.map(lambda argv: cli_invoke.invoke(argv), commands * 5))
        prompt = cli_invoke.invoke(["configure", *ws])
    finally:
        sys.stdout, sys.stderr, sys.stdin = saved
    assert [(r.exit_code, r.stdout, r.stderr) for r in results] == direct * 5
    assert prompt.exit_code == 1 and prompt.stdout.endswith("Onboarding paused. Saved groups can be resumed with mynotes configure.\n")


def test_registry_builds_cli_arguments(tmp_path):
    workspace = str(tmp_path / "ws")
    method = BY_NAME["memory.update"]
    params = validate(method, {"scope": "course", "area": "notes", "course": COURSE, "expectedRevision": "r1",
                               "set": {"tone": "short"}, "remove": ["old"], "style": {"recap": False}})
    argv, files = build_argv(method, params, workspace)
    assert argv == ["memory", "update", "--scope=course", "--area=notes", f"--course={COURSE}",
                    "--expected-revision=r1", "--set", "tone", "short", "--remove=old",
                    '--style-json={"recap": false}', "--workspace", workspace, "--json"] and files == []
    argv, files = build_argv(BY_NAME["library.importAudio"], {"paths": ["a.wav"], "eventId": "e1"}, workspace)
    assert argv[-4:-2] == ["--file", str(files[0])] and json.loads(files[0].read_text()) == {"paths": ["a.wav"], "event_id": "e1"}
    assert oct(files[0].stat().st_mode & 0o777) == "0o600"
    assert build_argv(BY_NAME["queue.job"], {"action": "retry", "jobId": "j"}, workspace)[0][:3] == ["transcription-queue", "retry", "--job=j"]
    assert build_argv(BY_NAME["capture.start"], {"durationMinutes": 5}, workspace)[0][-2:] == ["--wait-seconds", "0"]
    for name in ("transcription.run", "notes.run", "lesson.process", "capture.sync", "capture.connect", "models.refresh", "word.verifyRenderer"):
        assert BY_NAME[name].lane == "job", name
    assert {method.lane for method in METHODS} == {"fast", "slow", "job"}


def _arguments_for(method, name: str, value) -> dict:
    """The method's required parameters, with `name` set to `value`."""
    filler = {"str": "X1", "int": 1, "float": 1.0, "bool": True, "list": ["a"], "pairs": {"k": "v"},
              "json": {}, "file": {}, "field": ["a.wav"], "subcommand": None}
    params = {}
    for param in method.params:
        if param.required and not param.server:
            params[param.name] = param.choices[0] if param.choices else filler[param.kind]
    if method.one_of and name not in method.one_of:
        first = next(p for p in method.params if p.name == method.one_of[0])
        params[first.name] = filler[first.kind]
    params[name] = value
    return params


def _parsed_values(namespace) -> list:
    values = []
    for value in vars(namespace).values():
        values += value if isinstance(value, list) else [value]
    return values


def test_flag_values_that_start_with_a_dash_stay_values(tmp_path):
    """Every flag value reaches the real parser as a value, never as an option (a file named `-x.m4a`)."""
    from mynotes.cli import build_parser
    workspace, checked = str(tmp_path / "ws"), []
    for method in METHODS:
        for param in method.params:
            if param.server or param.flag is None or param.choices or param.kind not in {"str", "int", "float", "list"}:
                continue
            value = {"str": "-x.m4a", "int": -3, "float": -2.5, "list": ["-x"]}[param.kind]
            argv, files = build_argv(method, _arguments_for(method, param.name, value), workspace)
            try:
                parsed = build_parser().parse_args(argv)
            except SystemExit:
                pytest.fail(f"{method.name}.{param.name}: {argv}")
            finally:
                for path in files:
                    path.unlink()
            expected = value[0] if isinstance(value, list) else value
            assert expected in _parsed_values(parsed) or str(expected) in map(str, _parsed_values(parsed)), (method.name, param.name)
            checked.append(f"{method.name}.{param.name}")
    assert {"transcription.run.audio", "lesson.process.transcript", "memory.update.remove", "queue.enqueue.readyRecordings",
            "capture.start.lesson"} <= set(checked)


@pytest.mark.parametrize(("name", "params", "field"), [
    ("transcription.status", {"lectureId": "-x"}, "lectureId"),
    ("course.link", {"code": "C", "path": "-folder"}, "path"),
    ("notes.restore", {"backupId": "--help"}, "backupId"),
    ("memory.update", {"scope": "global", "area": "notes", "expectedRevision": "r", "set": {"tone": "-short"}}, "set"),
    ("memory.update", {"scope": "global", "area": "notes", "expectedRevision": "r", "set": {"-k": "v"}}, "set"),
])
def test_values_the_parser_would_read_as_options_are_refused(name, params, field):
    """Positionals and `--set KEY VALUE` cannot use `=`: a leading '-' is refused before any command runs."""
    with pytest.raises(RpcError) as refused:
        validate(BY_NAME[name], params)
    assert refused.value.code == -32602 and refused.value.message == f"{field} cannot start with '-'"


def test_result_mapping():
    ok = cli_invoke.Invocation(1, '{"status": "NEEDS_SELECTION"}\n', "")
    assert cli_result(ok) == {"exitCode": 1, "data": {"status": "NEEDS_SELECTION"}}
    for code, status, rpc in ((3, "BUSY", -32002), (4, "NEEDS_REVIEW", -32003)):
        with pytest.raises(RpcError) as raised:
            cli_result(cli_invoke.Invocation(code, json.dumps({"status": status, "message": "m"}), ""))
        assert raised.value.code == rpc and raised.value.data["data"]["status"] == status
    with pytest.raises(RpcError) as raised:
        cli_result(cli_invoke.Invocation(2, "mynotes: Course not found: X\n", ""))
    assert raised.value.code == -32001 and raised.value.message == "Course not found: X"
    assert cli_result(cli_invoke.Invocation(0, "Saved.\n", "")) == {"exitCode": 0, "data": None, "text": "Saved.\n"}
