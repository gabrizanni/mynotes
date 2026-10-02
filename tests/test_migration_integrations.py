from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from mynotes.cli import main
from mynotes.integrations import install_integration
from mynotes.migration import MigrationError, apply_manifest, build_manifest, load_manifest, save_manifest
from mynotes.integrations import IntegrationError
from mynotes.utils import file_sha256
from mynotes.workspace import Workspace


def legacy_source(tmp_path):
    source = tmp_path / "legacy"
    (source / "config").mkdir(parents=True)
    (source / "runs" / "BIO1-L01").mkdir(parents=True)
    (source / "archives").mkdir()
    (source / "capture").mkdir()
    (source / "config" / "courses.json").write_text(json.dumps({"BIO1": {"name": "Biology"}}), encoding="utf-8")
    (source / "runs" / "BIO1-L01" / "transcript.json").write_text('{"segments": []}', encoding="utf-8")
    (source / "archives" / "BIO1_COURSE_NOTES.docx").write_bytes(b"synthetic-word")
    (source / "capture" / "active.json").write_text('{"active": true}', encoding="utf-8")
    (source / "state").mkdir()
    with sqlite3.connect(source / "state" / "workflow.sqlite3") as connection:
        connection.execute("CREATE TABLE lessons(id TEXT)")
        connection.execute("INSERT INTO lessons VALUES ('BIO1-L01')")
    return source


def test_dry_run_manifest_and_repeatable_apply_preserve_legacy(tmp_path) -> None:
    source = legacy_source(tmp_path)
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()
    transcript_hash = file_sha256(source / "runs" / "BIO1-L01" / "transcript.json")
    manifest = build_manifest(source, workspace)
    saved = save_manifest(workspace, manifest)
    assert saved.is_file()
    assert manifest["capture_state"] == "excluded"
    assert all("capture" not in entry["relative_path"] for entry in manifest["entries"])
    first = apply_manifest(manifest)
    second = apply_manifest(manifest)
    copied = workspace.migrations_dir / "legacy" / "runs" / "BIO1-L01" / "transcript.json"
    assert first["copied"] == len(manifest["entries"])
    assert second == {"copied": 0, "skipped": len(manifest["entries"])}
    assert file_sha256(copied) == transcript_hash
    assert file_sha256(source / "runs" / "BIO1-L01" / "transcript.json") == transcript_hash
    loaded = load_manifest(saved, workspace)
    assert loaded["manifest_id"] == manifest["manifest_id"]
    assert (workspace.migrations_dir / "converted" / "path-map.json").is_file()


def test_edited_manifest_and_edited_managed_integration_are_rejected(tmp_path) -> None:
    source = legacy_source(tmp_path); workspace = Workspace.from_value(tmp_path / "private"); workspace.initialize()
    saved = save_manifest(workspace, build_manifest(source, workspace))
    payload = json.loads(saved.read_text(encoding="utf-8")); payload["entries"][0]["action"] = "ignore"
    saved.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MigrationError, match="changed after"):
        load_manifest(saved, workspace)
    installed = install_integration("codex", tmp_path / "project")
    skill = Path(installed["path"]) / "SKILL.md"; skill.write_text("local edit", encoding="utf-8")
    with pytest.raises(IntegrationError, match="edited locally"):
        install_integration("codex", tmp_path / "project", action="update")


def test_codex_and_agy_exports_are_generic_managed_and_contain_no_workspace_data(tmp_path) -> None:
    codex = install_integration("codex", tmp_path / "codex")
    agy = install_integration("agy", tmp_path / "agy")
    codex_root, agy_root = Path(codex["path"]), Path(agy["path"])
    assert (codex_root / "SKILL.md").is_file()
    assert (agy_root / "plugin.json").is_file()
    assert (agy_root / "skills" / "mynotes" / "SKILL.md").is_file()
    agent = agy_root / "agents" / "mynotes.md"
    assert agent.is_file()
    assert "mainAgent: true" in agent.read_text(encoding="utf-8")
    assert str(tmp_path) not in (codex_root / "SKILL.md").read_text(encoding="utf-8")
    assert str(tmp_path) not in agent.read_text(encoding="utf-8")
    assert install_integration("codex", tmp_path / "codex", action="update")["status"] == "UPDATED"


def test_status_and_chat_launch_one_real_provider_session(tmp_path, capsys, monkeypatch) -> None:
    workspace = Workspace.from_value(tmp_path / "private")
    workspace.initialize()
    configuration = workspace.configuration_store().load()
    configuration["providers"]["configured"] = {"agy": {"model": "gemini-test", "effort": "high"}}
    workspace.configuration_store().save(configuration)
    monkeypatch.setattr("mynotes.chat.shutil.which", lambda _: "/usr/local/bin/agy")
    calls = []
    monkeypatch.setattr("mynotes.chat.subprocess.run", lambda command, **kwargs: calls.append((command, kwargs)) or type("Done", (), {"returncode": 0})())
    assert main(["status", "--workspace", str(workspace.root)]) == 0
    assert main(["chat", "--workspace", str(workspace.root), "--provider", "agy", "What is pending?"]) == 0
    assert calls and "--prompt-interactive" in calls[0][0]
    assert calls[0][1]["env"]["MYNOTES_CHAT_ACTIVE"] == "1"
