from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from docx import Document
from PIL import Image

from mynotes.configuration import course_template
from mynotes.publication import (
    PublicationError,
    append_equation,
    build_word_candidate,
    publication_history,
    publish_word_candidate,
    restore_backup,
)
from mynotes.utils import atomic_json_write, file_sha256
from mynotes.workspace import Workspace
from mynotes.workflow import status


def prepared(tmp_path: Path, *, visual: bool = False, formula: str | None = None) -> tuple[Workspace, Path]:
    workspace = Workspace.from_value(tmp_path / "private"); workspace.initialize()
    configuration = workspace.configuration_store().load(); course = course_template()
    course.update({"code": "OPS7", "name": "Operations"}); course["folders"]["handout"] = str(tmp_path / "handouts")
    configuration["courses"] = [course]; workspace.configuration_store().save(configuration)
    run = workspace.runs_dir / "OPS7-L01"; run.mkdir(parents=True)
    image = run / "chart.png"
    if visual:
        Image.new("RGB", (400, 200), "white").save(image)
    pack = {
        "schema_version": 1, "lecture_id": "OPS7-L01", "course": {"code": "OPS7", "name": "Operations", "lesson_number": 1},
        "availability": {}, "evidence": [{"id": "S001", "kind": "slide", "content": "Trade-off", "source_file": "deck.pdf", "page": 2}],
        "visuals": ([{"evidence_id": "S001", "image_path": str(image), "source_file": "deck.pdf", "page": 2}] if visual else []),
        "directives": [], "sources": [], "glossary": [],
    }
    source = run / "source_pack.json"; atomic_json_write(source, pack)
    notes = {
        "lecture_id": "OPS7-L01", "title": "Trade-off", "summary": "Summary", "bullets": [], "table": [], "formula": formula,
        "sections": [{"heading": "Framework", "level": 3, "body": "Cost and service interact.", "bullets": ["Cost", "Service"], "evidence_ids": ["S001"]}],
        "evidence_coverage": [{"evidence_id": "S001", "disposition": "used", "reason": "core"}],
        "visual_decisions": ([{"evidence_id": "S001", "decision": "include", "reason": "Essential chart"}] if visual else []), "model_knowledge": [],
    }
    atomic_json_write(run / "lecture_notes.candidate.json", {"notes": notes, "source_pack": str(source), "source_pack_hash": file_sha256(source), "published_to_word": False})
    return workspace, tmp_path / "handouts" / "OPS7_COURSE_NOTES.docx"


def fake_render(path: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True); pdf = output / f"{path.stem}.pdf"; pdf.write_bytes(b"test")
    return {"pdf": str(pdf), "pages": [{"page": 1, "characters": 1, "images": 0}], "renderer": "test"}


def test_equation_is_structured_omml_fraction_root_and_scripts(tmp_path) -> None:
    path = tmp_path / "equation.docx"; document = Document()
    append_equation(document, r"y = \frac{\sqrt{x_1}}{\sum_{i=1}^{n} z_i}"); document.save(path)
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    assert b"<m:f>" in xml and b"<m:rad>" in xml and b"<m:nary>" in xml
    assert b"<m:sSub>" in xml or b"<m:sSubSup>" in xml


def test_unsupported_formula_blocks_candidate(tmp_path, monkeypatch) -> None:
    workspace, _ = prepared(tmp_path, formula=r"\begin{matrix}a\end{matrix}")
    monkeypatch.setattr("mynotes.publication._render_and_validate", fake_render)
    with pytest.raises(PublicationError, match="Unsupported formula"):
        build_word_candidate(workspace, "OPS7-L01")


def test_build_embeds_visual_and_publish_backup_restore(tmp_path, monkeypatch) -> None:
    workspace, target = prepared(tmp_path, visual=True, formula=r"c = \frac{x}{y}")
    target.parent.mkdir(parents=True); base = Document(); base.add_paragraph("Manual content outside MyNotes blocks."); base.save(target)
    original = file_sha256(target)
    monkeypatch.setattr("mynotes.publication._render_and_validate", fake_render)
    built = build_word_candidate(workspace, "OPS7-L01")
    candidate = Path(built["candidate"])
    assert Document(candidate).inline_shapes
    result = publish_word_candidate(workspace, "OPS7-L01", candidate)
    assert result["status"] == "PUBLISHED" and file_sha256(target) != original
    assert status(workspace)[0]["status"] == "PUBLISHED"
    assert "Manual content outside" in "\n".join(p.text for p in Document(target).paragraphs)
    history = publication_history(workspace, "OPS7")
    assert len(history) == 1 and history[0]["sha256"] == original
    restored = restore_backup(workspace, history[0]["backup_id"])
    assert restored["status"] == "RESTORED" and file_sha256(target) == original


def test_external_edit_after_build_blocks_publish(tmp_path, monkeypatch) -> None:
    workspace, target = prepared(tmp_path)
    monkeypatch.setattr("mynotes.publication._render_and_validate", fake_render)
    built = build_word_candidate(workspace, "OPS7-L01")
    target.parent.mkdir(parents=True); Document().save(target)
    current = file_sha256(target)
    with pytest.raises(PublicationError, match="changed after candidate"):
        publish_word_candidate(workspace, "OPS7-L01", Path(built["candidate"]))
    assert file_sha256(target) == current
