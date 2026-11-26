"""Every fast method returns exactly what its CLI command prints, on the same workspace."""
import json

import pytest

from mynotes import capture
from mynotes.serve_methods import BY_NAME, METHODS, build_argv
from serve_helpers import COURSE, Client, build_workspace, cli, environment

# Parameters for each fast method; every fast method must appear here.
CALLS = {
    "config.get": [{}],
    "preferences.get": [{}],
    "memory.show": [{"scope": "global", "area": "notes"}, {"scope": "course", "area": "dialogue", "course": COURSE}],
    "style.show": [{}, {"course": COURSE}],
    "course.pending": [{}],
    "models.catalog": [{"provider": "codex"}, {"provider": "claude", "saved": "custom-model"}],
    "capture.dashboard": [{}],
    "capture.status": [{}],
    "capture.schedule": [{}],
    "capture.pending": [{}],
    "capture.source": [{"source": "teaching"}, {"source": "absent"}],
    "queue.status": [{}],
    "library.list": [{}, {"course": COURSE}, {"query": "L01"}],
    "library.transcripts": [{}],
    "library.lessons": [{}],
    "library.courseProfile": [{"course": COURSE}],
    "transcription.status": [{"lectureId": f"{COURSE}-L01"}, {"recordingId": "missing"}],
    "notes.batchStatus": [{"batchId": f"{COURSE}-0123456789ab"}],
    "notes.history": [{}, {"course": COURSE}],
    "usage.get": [{"lectureId": f"{COURSE}-L01"}],
}


def test_every_fast_method_is_covered():
    # Methods without a command (library.transcript) have no CLI to compare with; their own tests cover them.
    assert set(CALLS) == {method.name for method in METHODS if method.lane == "fast" and method.command}


@pytest.fixture(scope="module")
def parity(tmp_path_factory):
    monkeypatch = pytest.MonkeyPatch()
    tmp_path = tmp_path_factory.mktemp("parity")
    workspace = build_workspace(tmp_path, monkeypatch)
    capture.subscribe(capture.CaptureStore(workspace), {
        "sourceID": "teaching", "url": "https://calendar.example.invalid/feed.ics", "timezone": "Europe/Rome",
        "periodStart": "2030-01-01T00:00:00Z", "periodEnd": "2030-06-01T00:00:00Z",
        "mappings": [{"match": "Systems", "courseCode": COURSE, "firstLesson": 1}]})
    env = environment(tmp_path)
    client = Client(workspace.root, env)
    yield client, workspace, env
    client.close()
    monkeypatch.undo()


@pytest.mark.parametrize("name,params", [(name, params) for name, calls in CALLS.items() for params in calls])
def test_rpc_data_equals_cli_stdout(parity, name, params):
    client, workspace, env = parity
    argv, files = build_argv(BY_NAME[name], params, str(workspace.root))
    assert not files
    completed = cli(env, *argv)
    reply = client.request(name, params)
    try:
        expected = json.loads(completed.stdout)
    except ValueError:
        # Plain failures: the same exit code and the same `mynotes: ...` text.
        assert reply["error"]["data"]["exitCode"] == completed.returncode
        assert reply["error"]["data"]["stdout"] == completed.stdout
        return
    assert reply["result"] == {"exitCode": completed.returncode, "data": expected}
    if name == "library.list" and not params:
        assert len(expected["recordings"]) == 2  # The comparison covers real rows.
    if name == "capture.dashboard":
        assert len(expected["events"]) == 3 and len(expected["sources"]) == 1
