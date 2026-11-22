"""Optional integration check with the installed or bundled Windows renderer."""
import os

import pytest

from mynotes.onboarding import verify_word_renderer


@pytest.mark.skipif(os.name != "nt", reason="Windows LibreOffice integration")
def test_windows_renders_generated_docx_and_keeps_verified_proof(tmp_path, monkeypatch):
    executable = os.environ.get("MYNOTES_TEST_SOFFICE")
    if not executable:
        pytest.skip("Set MYNOTES_TEST_SOFFICE to the actual soffice.com path")
    monkeypatch.setenv("MYNOTES_HOME", str(tmp_path / "Studio locale"))
    monkeypatch.setenv("MYNOTES_SOFFICE", executable)
    proof = verify_word_renderer()
    assert proof["word"] is True
    assert proof["word_pages"] == 1
    receipt = tmp_path / "Studio locale" / "checks" / "word-renderer.json"
    assert receipt.is_file()
    before = receipt.stat().st_mtime_ns
    assert verify_word_renderer() == proof
    assert receipt.stat().st_mtime_ns == before
