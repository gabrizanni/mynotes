from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _public_checker():
    spec = importlib.util.spec_from_file_location("check_public_tree", ROOT / "scripts" / "check_public_tree.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_release_allowlist_is_present_and_free_of_private_references() -> None:
    checker = _public_checker()
    assert checker.validate(ROOT) == []
    assert all(path.is_file() for path in checker.public_paths(ROOT))


def test_installer_is_versioned_and_never_downloads_models() -> None:
    installer = (ROOT / "scripts" / "install-mynotes.sh").read_text(encoding="utf-8")
    assert "uv export" in installer and "--locked" in installer
    assert "MyNotes managed launcher" in installer
    assert "AI/speech model downloads: none" in installer
