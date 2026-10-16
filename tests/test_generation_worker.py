import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mynotes import providers
from mynotes.cli import main
from mynotes.providers import ProviderError, ProviderRequest, RequestedModel, _context, _run_agy_worker
from mynotes.batch import generate_batch
from mynotes.workflow import WorkflowError
from test_batch_generation import workspace_and_deck, Adapter, VERIFIED


def test_compact_context_preserves_every_utterance_and_provenance():
    rows = [{"id": f"T{n:03d}", "kind": "transcript", "text": f"Exact source sentence {n}.",
             "start_seconds": n, "end_seconds": n+1, "speaker": "UNKNOWN",
             "speaker_role": "Professor" if n == 400 else "UNKNOWN",
             "speaker_role_source": "unverified", "speaker_role_confidence": None}
            for n in range(1, 1001)]
    req = ProviderRequest("TEST-L01", {}, {}, RequestedModel("agy", "test", "high", "standard"), source_pack={"evidence": rows})
    payload = json.loads(_context(req).splitlines()[-1])
    assert len(payload["evidence"]) == len(rows)
    defaults = payload["evidence_defaults"]["T"]
    for old, compact in zip(rows, payload["evidence"]):
        restored = {**defaults, **compact}
        for key in ("id", "kind", "start_seconds", "end_seconds", "speaker", "speaker_role", "speaker_role_source", "speaker_role_confidence"):
            assert restored.get(key) == old[key]
        assert restored["content"] == old["text"]
    assert sum("speaker_role_source" in r for r in payload["evidence"]) == 0


def test_writing_worker_cannot_start_another_mynotes_flow(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MYNOTES_GENERATION_WORKER", "1")
    root = tmp_path / "uncreated"
    assert main(["init", "--workspace", str(root)]) == 3
    assert not root.exists()
    assert "cannot invoke MyNotes" in capsys.readouterr().out


def test_error_envelope_is_rejected_even_with_complete_notes(tmp_path, monkeypatch):
    from test_providers_quality import valid_notes
    req = ProviderRequest("TEST-L01", {}, {}, RequestedModel("agy", "test", "high", "standard"), run_dir=tmp_path)
    monkeypatch.setattr(providers, "provider_executable", lambda _: "/fake/agy")
    monkeypatch.setattr(providers, "_run_agy_worker", lambda command, **kw: subprocess.CompletedProcess(command, 0, json.dumps({"status":"ERROR", "response":json.dumps(valid_notes()), "error":"EOF"}), ""))
    with pytest.raises(ProviderError, match="AGY_ENVELOPE_ERROR"):
        providers.AgyAdapter().generate(req)
    attempt = json.loads(next((tmp_path / "provider-attempts").glob("*.json")).read_text())
    assert attempt["error"] is not None


def test_writer_has_no_shell_and_has_bounded_runtime(tmp_path, monkeypatch):
    from test_providers_quality import valid_notes
    req = ProviderRequest("TEST-L01", {}, {}, RequestedModel("agy", "test", "high", "standard"), run_dir=tmp_path)
    monkeypatch.setattr(providers, "provider_executable", lambda _: "/fake/agy")
    def run(command, **kw):
        assert command[command.index("--agent")+1] == "mynotes-note-writer"
        assert "--disable-slash-commands" in command
        assert command[command.index("--print-timeout")+1] == "19m"
        assert kw["timeout"] == 1200 and kw["env"]["MYNOTES_GENERATION_WORKER"] == "1"
        agent = (tmp_path / ".agents/agents/mynotes-note-writer.md").read_text()
        assert "  - view_file" in agent and "  - run_command" not in agent
        assert "  - finish" in agent
        assert "registered finish tool" in command[-1]
        return subprocess.CompletedProcess(command, 0, json.dumps({"status":"SUCCESS", "structured_output":valid_notes()}), "")
    monkeypatch.setattr(providers, "_run_agy_worker", run)
    providers.AgyAdapter().generate(req)


def test_rejected_batch_retains_transport_without_a_second_paid_call(tmp_path):
    ws, _, selection = workspace_and_deck(tmp_path)
    def invalid(response, request):
        response["lessons"][0]["notes"]["blocks"][1]["coverage_role"] = "reference"
    adapter = Adapter(invalid)
    for _ in range(2):
        with pytest.raises(WorkflowError, match="coverage_role=full"):
            generate_batch(ws, "OPS", [1, 2], selection=selection, budgets=VERIFIED, provider_adapter=adapter)
    assert len(adapter.calls) == 1
    assert list(ws.runs_dir.rglob("returned-writing-*.json"))
    assert not list(ws.runs_dir.rglob("lecture_notes.candidate.json"))


@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
def test_writer_timeout_reaps_its_process_group(tmp_path):
    pidfile = tmp_path / "child.pid"
    code = "import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);pathlib.Path(sys.argv[1]).write_text(str(p.pid));print('partial output',flush=True);time.sleep(60)"
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _run_agy_worker([sys.executable, "-c", code, str(pidfile)], timeout=0.7, cwd=str(tmp_path), env=dict(os.environ))
    assert "partial output" in caught.value.output
    child = int(pidfile.read_text())
    # A dead child can briefly remain a zombie until the OS parent reaps it.
    row = subprocess.run(["ps", "-p", str(child), "-o", "stat="], capture_output=True, text=True).stdout.strip()
    assert not row or row.startswith("Z")


def test_historical_error_candidate_cannot_reach_word(tmp_path):
    from mynotes.publication import _load_candidate, PublicationError
    from mynotes.workspace import Workspace
    from mynotes.utils import atomic_json_write, file_sha256
    ws = Workspace.from_value(tmp_path / "workspace")
    pack = ws.runs_dir / "TEST-L01/source_pack.json"
    atomic_json_write(pack, {"evidence": []})
    attempt = pack.parent / "provider-attempts/writing-000000000001.json"
    atomic_json_write(attempt, {"requested":{"provider":"agy"}, "returncode":0, "error":None, "stdout":json.dumps({"status":"ERROR", "response":"{}"})})
    candidate = {"source_pack":str(pack), "source_pack_hash":file_sha256(pack), "shared_attempt_paths":[str(attempt)], "notes":{}}
    atomic_json_write(pack.parent / "lecture_notes.candidate.json", candidate)
    with pytest.raises(PublicationError, match="failed provider transport"):
        _load_candidate(ws, "TEST-L01")


def test_provider_error_does_not_resend_entire_response_to_coordinator(tmp_path, monkeypatch):
    req = ProviderRequest("TEST-L01", {}, {}, RequestedModel("agy", "test", "high", "standard"), run_dir=tmp_path)
    monkeypatch.setattr(providers, "provider_executable", lambda _: "/fake/agy")
    response = "very large invalid draft " * 10000
    monkeypatch.setattr(providers, "_run_agy_worker", lambda command, **kw: subprocess.CompletedProcess(command, 0, json.dumps({"status":"ERROR", "response":response, "error":"EOF"}), ""))
    with pytest.raises(ProviderError) as caught:
        providers.AgyAdapter().generate(req)
    assert len(str(caught.value)) < 1200
    assert "very large invalid draft" not in str(caught.value)
    saved = json.loads(next((tmp_path / 'provider-attempts').glob('*.json')).read_text())
    assert response in json.loads(saved['stdout'])['response']


@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
def test_sigterm_stops_writer_instead_of_being_swallowed_by_selector(tmp_path):
    """InterruptedError is swallowed by selectors; cancellation must escape it."""
    import signal
    import time
    pidfile = tmp_path / "writer.pid"
    driver = tmp_path / "driver.py"
    driver.write_text('''import os,sys\nfrom pathlib import Path\nfrom mynotes.providers import _run_agy_worker,ProviderCancelledError\ntry:\n _run_agy_worker([sys.executable,"-c","import os,time,pathlib,sys;pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(60)",sys.argv[1]],timeout=50,cwd=str(Path(sys.argv[1]).parent),env=dict(os.environ))\nexcept ProviderCancelledError:\n print("CANCELLED",flush=True)\n''')
    process = subprocess.Popen([sys.executable, str(driver), str(pidfile)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ))
    try:
        deadline = time.monotonic()+5
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pidfile.exists()
        os.kill(process.pid, signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=6)
        assert process.returncode == 0 and "CANCELLED" in stdout, stderr
        child = int(pidfile.read_text())
        state = subprocess.run(["ps", "-p", str(child), "-o", "stat="],capture_output=True,text=True).stdout.strip()
        assert not state or state.startswith("Z")
    finally:
        if process.poll() is None:
            process.kill(); process.communicate()


def test_writing_schema_prevents_duplicate_legacy_narrative_without_capping_notes():
    schema = providers.notes_schema(compact=True)
    assert "maxItems" not in schema["properties"]["blocks"]
    for field in ("bullets", "table", "formulas", "sections"):
        assert schema["properties"][field]["maxItems"] == 0
    assert "maxItems" not in schema["properties"]["review_questions"]
    assert schema["properties"]["formula"]["type"] == ["string", "null"]
    assert "maxItems" not in providers.notes_schema()["properties"]["sections"]


def test_large_agy_inputs_are_lossless_and_planning_remains_readonly(tmp_path):
    rows = [{"id": f"T{n:03d}", "kind": "transcript", "text": f"Exact sentence {n}: quoted \\\"value\\\" and \\n text.",
             "start_seconds": n, "end_seconds": n + .5, "speaker_role": "UNKNOWN"}
            for n in range(1, 1001)]
    req = ProviderRequest("TEST-L01", {"preferences": {"persistent_instructions": ["Saved style."]}}, {},
                          RequestedModel("agy", "test", "high", "standard"),
                          source_pack={"evidence": rows}, run_dir=tmp_path / "run")
    planned = providers._stage_agy_context(req, req.run_dir, write=False)
    assert not req.run_dir.exists()
    req.run_dir.mkdir()
    staged = providers._stage_agy_context(req, req.run_dir)
    assert staged == planned and len(staged.encode()) < 10000
    manifest_path = next((req.run_dir / "request-inputs").glob("*/manifest.json"))
    manifest = json.loads(manifest_path.read_text())
    assert manifest["evidence_count"] == len(rows)
    expected = json.loads(_context(req).splitlines()[-1])
    got = []
    for item in manifest["files"]:
        path = Path(item["path"])
        if path.name.startswith("evidence-"):
            assert path.stat().st_size <= providers.AGY_INPUT_PART_BYTES
            got.extend(json.loads(line) for line in path.read_text().splitlines())
    assert got == expected["evidence"]
    meta = json.loads((manifest_path.parent / "source-metadata.json").read_text())
    assert meta == {key: value for key, value in expected.items() if key != "evidence"}
    config = json.loads((manifest_path.parent / "configuration.json").read_text())
    assert config["course"]["preferences"]["persistent_instructions"] == ["Saved style."]
    assert "UNTRUSTED SOURCE DATA" in staged


def test_staged_request_inputs_preserve_earlier_attempt_sources(tmp_path):
    from dataclasses import replace
    pack = {"evidence": [{"id": "T001", "text": "Original evidence. " * 3000}]}
    req = ProviderRequest("TEST-L01", {}, {}, RequestedModel("agy", "test", "high", "standard"), source_pack=pack, run_dir=tmp_path)
    first = providers._stage_agy_context(req, tmp_path)
    before = {p: p.read_bytes() for p in (tmp_path / "request-inputs").rglob("*") if p.is_file()}
    newer = replace(req, source_pack={"evidence": [{"id": "T001", "text": "Changed evidence. " * 3000}]})
    second = providers._stage_agy_context(newer, tmp_path)
    assert first != second
    assert all(path.read_bytes() == data for path, data in before.items())
