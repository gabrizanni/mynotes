"""Synthetic provider transports. No auth, inference or real CLIs."""
import os
import subprocess
import sys
import time

import pytest

from mynotes.agy_parts import run_session
from mynotes.model_discovery import Channel
from mynotes.process_status import probe_process
from mynotes.provider_process import ThreadedPipes, run_windows_job
from mynotes.providers import provider_environment

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows subprocess and Job Objects")


def _python(code, *arguments):
    return [sys.executable, "-u", "-c", code, *arguments]


def test_threaded_pipes_preserve_each_stream_and_eof():
    with subprocess.Popen(
        _python("import sys; print('data'); print('warnings', file=sys.stderr)"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        pipes = ThreadedPipes({"out": process.stdout, "err": process.stderr})
        received = {"out": b"", "err": b""}
        deadline = time.monotonic() + 5
        try:
            while pipes.active and time.monotonic() < deadline:
                for stream, chunk in pipes.read(0.1):
                    received[stream] += chunk
            assert not pipes.active
        finally:
            pipes.close()
    assert received["out"].strip() == b"data"
    assert received["err"].strip() == b"warnings"


@windows_only
def test_provider_can_locate_the_installed_runtime_for_child_tools():
    assert os.path.dirname(sys.executable) in provider_environment()["PATH"].split(os.pathsep)


@windows_only
def test_job_reads_large_utf8_stdin_without_shell_expansion():
    prompt = 'Italiano, spazi, "virgolette" & emoji 📖\n' * 3000
    completed = run_windows_job(
        _python("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"),
        input_text=prompt,
        timeout=10,
    )
    assert completed.returncode == 0
    assert completed.stdout == prompt


def _assert_exited(pid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            probe_process(pid)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail(f"Descendant {pid} survived after the provider closed")


_OWNED_CHILD = """
import subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
print(child.pid, flush=True)
if len(sys.argv) > 2:
    Path(sys.argv[2]).touch()
if sys.argv[1] == "hang":
    time.sleep(20)
"""


@windows_only
def test_job_reaps_descendant_when_parent_exits_with_open_pipes():
    completed = run_windows_job(_python(_OWNED_CHILD, "exit"), timeout=10)
    assert completed.returncode == 0
    _assert_exited(int(completed.stdout.strip()))


@windows_only
def test_job_timeout_reaps_descendant_and_keeps_diagnostics():
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        run_windows_job(_python(_OWNED_CHILD, "hang"), timeout=3)
    _assert_exited(int(caught.value.output.strip()))


@windows_only
def test_job_cancellation_stops_descendants_and_keeps_diagnostics(tmp_path):
    marker = tmp_path / "started"

    class Cancelled(Exception):
        pass

    def cancel_when_started():
        if marker.is_file():
            raise Cancelled("test cancellation")

    with pytest.raises(Cancelled) as caught:
        run_windows_job(_python(_OWNED_CHILD, "hang", str(marker)), timeout=10,
                        check_cancelled=cancel_when_started)
    _assert_exited(int(caught.value.output.strip()))


@windows_only
def test_agy_stream_transmits_multiple_turns_and_keeps_stderr(tmp_path):
    provider = """
import json, sys
print("provider diagnostic", file=sys.stderr)
for line in sys.stdin.buffer:
    content = json.loads(line)["message"]["content"]
    response = {"event": "result", "result": {"size": len(content)}}
    print(json.dumps(response), flush=True)
"""
    turns = []
    first = "Lezione 🇮🇹" * 5000
    second = "Controllo"

    def accept(result):
        turns.append(result["size"])
        return second if len(turns) == 1 else None

    completed = run_session(_python(provider), first, accept, cwd=str(tmp_path), env=os.environ.copy(),
                            total_seconds=15)
    assert completed.returncode == 0
    assert turns == [len(first), len(second)]
    assert "provider diagnostic" in completed.stderr


@windows_only
def test_metadata_channel_round_trip_uses_native_windows_pipes():
    provider = """
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    response = {"id": request["id"], "result": request["params"]}
    print(json.dumps(response), flush=True)
"""
    channel = Channel(_python(provider), os.environ.copy(), time.monotonic() + 10)
    try:
        request = {"id": 4, "params": {"message": "catalogo"}}
        channel.send(request)
        assert channel.response(4) == request["params"]
    finally:
        channel.close()
