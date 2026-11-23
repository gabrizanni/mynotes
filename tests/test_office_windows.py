"""Optional integration check with the installed or bundled Windows renderer."""
import os
from pathlib import Path

import pytest
from docx import Document

from mynotes.onboarding import verify_word_renderer
from mynotes.publication import build_word_candidate, publication_history, publish_word_candidate, restore_backup
from mynotes.utils import file_sha256
from test_publication import prepared


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


def test_cumulative_publication_and_undo_with_real_renderer(tmp_path, monkeypatch):
    executable = os.environ.get("MYNOTES_TEST_SOFFICE")
    if not executable:
        pytest.skip("Set MYNOTES_TEST_SOFFICE to the actual LibreOffice executable")
    monkeypatch.setenv("MYNOTES_HOME", str(tmp_path / "Local application data"))
    monkeypatch.setenv("MYNOTES_SOFFICE", executable)
    workspace, target = prepared(tmp_path, formula=r"C = \frac{x}{y}")
    target.parent.mkdir()
    original = Document()
    original.add_paragraph("This synthetic manual paragraph must survive the lesson update.")
    original.save(target)
    original_hash = file_sha256(target)

    candidate = build_word_candidate(workspace, "OPS7-L01")
    published = publish_word_candidate(workspace, "OPS7-L01", Path(candidate["candidate"]))
    assert published["status"] == "PUBLISHED"
    published_document = Document(target)
    assert any("manual paragraph" in item.text for item in published_document.paragraphs)
    assert published_document.part.element.xpath("//m:oMath//m:f")
    backup, = publication_history(workspace, "OPS7")
    assert restore_backup(workspace, backup["backup_id"])["status"] == "RESTORED"
    assert file_sha256(target) == original_hash
