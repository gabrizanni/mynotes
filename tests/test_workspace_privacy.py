"""Private technical state must be inaccessible to other local accounts."""
import os
import stat

from mynotes.workspace import Workspace


def test_new_workspace_is_private_even_with_permissive_umask(tmp_path):
    workspace = Workspace.from_value(tmp_path / "new workspace")
    previous = os.umask(0)
    try:
        workspace.initialize()
    finally:
        os.umask(previous)
    for directory in (workspace.root, *workspace.required_directories()):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_existing_workspace_permissions_and_files_are_preserved(tmp_path):
    root = tmp_path / "existing"
    root.mkdir(mode=0o750)
    original = root / "user document.txt"
    original.write_text("keep this")
    original.chmod(0o640)
    workspace = Workspace.from_value(root)
    workspace.initialize()
    workspace.initialize()
    assert stat.S_IMODE(root.stat().st_mode) == 0o750
    assert stat.S_IMODE(original.stat().st_mode) == 0o640
    assert original.read_text() == "keep this"
    for directory in workspace.required_directories():
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
