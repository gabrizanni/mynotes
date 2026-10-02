from __future__ import annotations

import json
import sqlite3

import pytest

from mynotes.cli import main
from mynotes.resources import default_configuration
from mynotes.utils import file_sha256
from mynotes.workspace import Workspace


def test_default_resources_are_package_owned_and_user_neutral() -> None:
    defaults = default_configuration()
    assert defaults["courses"] == []
    assert defaults["workspace_schema_version"] == 2


def test_two_initialized_workspaces_do_not_share_configuration_or_state(tmp_path) -> None:
    first = Workspace.from_value(tmp_path / "first")
    second = Workspace.from_value(tmp_path / "second")
    first.initialize()
    second.initialize()

    first_settings = json.loads(first.configuration_path.read_text(encoding="utf-8"))
    first_settings["courses"].append({"code": "TEST1"})
    first.configuration_path.write_text(json.dumps(first_settings), encoding="utf-8")
    with sqlite3.connect(first.database_path) as connection:
        connection.execute("INSERT INTO workspace_metadata(key, value) VALUES (?, ?)", ("marker", "first"))

    second_settings = json.loads(second.configuration_path.read_text(encoding="utf-8"))
    with sqlite3.connect(second.database_path) as connection:
        second_markers = connection.execute("SELECT key FROM workspace_metadata WHERE key = 'marker'").fetchall()

    assert second_settings["courses"] == []
    assert second_markers == []


def test_help_and_import_do_not_require_or_create_a_workspace(tmp_path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "not-created"
    monkeypatch.setenv("MYNOTES_WORKSPACE", str(workspace))

    with pytest.raises(SystemExit) as result:
        main(["--help"])
    assert result.value.code == 0
    captured = capsys.readouterr()
    assert "mynotes" in captured.out
    assert not workspace.exists()


def test_cli_initializes_and_diagnoses_an_explicit_workspace(tmp_path, capsys) -> None:
    workspace = tmp_path / "private workspace"
    assert main(["init", "--workspace", str(workspace)]) == 0
    assert main(["doctor", "--workspace", str(workspace)]) == 0
    captured = capsys.readouterr()
    assert "configuration: present" in captured.out


def test_configuration_is_created_with_a_stable_content_hash(tmp_path) -> None:
    workspace = Workspace.from_value(tmp_path / "workspace")
    workspace.initialize()
    initial_hash = file_sha256(workspace.configuration_path)
    workspace.initialize()
    assert file_sha256(workspace.configuration_path) == initial_hash
