"""Historical scoped Word import with journal proof and personal-content merge."""
from pathlib import Path
import sqlite3

from docx import Document
import pytest

from mynotes import publication as pub
from mynotes.generated_baseline import import_generated_baseline
from mynotes.utils import file_sha256
from test_publication import prepared, fake_render


def legacy_fixture(tmp_path):
    ws, target = prepared(tmp_path)
    target.parent.mkdir(parents=True)
    original = tmp_path / 'generated.docx'
    d = Document()
    d.add_paragraph('Personal cover outside the generated lesson.')
    for kind in ('START', 'BODY', 'END'):
        if kind == 'BODY':
            d.add_paragraph('Cost and service interact.')
        else:
            p = d.add_paragraph()
            p.add_run(f'[OLD-NOTES-{kind} course=OPS7 lecture=OPS7-L01 revision=OPS7-L01-R001 created=2026-01-01T00:00:00Z]').font.hidden = True
    d.save(original)
    target.write_bytes(original.read_bytes())
    journal = tmp_path / 'publication.sqlite3'
    with sqlite3.connect(journal) as db:
        db.execute('CREATE TABLE publication_transactions(review_id TEXT,lecture_id TEXT,target_sha256 TEXT,status TEXT,completed_at TEXT)')
        db.execute('CREATE TABLE reviews(review_id TEXT,status TEXT,candidate_docx TEXT)')
        db.execute('INSERT INTO publication_transactions VALUES (?,?,?,?,?)', ('R001','OPS7-L01',file_sha256(original),'COMPLETED','2026-01-01'))
        db.execute('INSERT INTO reviews VALUES (?,?,?)', ('R001','PUBLISHED',str(original)))
    return ws, target, original, journal


def test_imported_baseline_replaces_old_section_once_and_preserves_personal_addition(tmp_path, monkeypatch):
    ws, target, source, journal = legacy_fixture(tmp_path)
    d = Document(target)
    d.paragraphs[2].add_run(' My own warehouse example.')
    d.save(target)
    before = target.read_bytes()
    result = import_generated_baseline(ws, 'OPS7', journal=journal, document=source)
    assert result['status'] == 'BASELINE_IMPORTED' and target.read_bytes() == before
    monkeypatch.setattr(pub, '_render_and_validate', fake_render)
    updated = pub.update_course_notes(ws, 'OPS7-L01')
    assert updated['status'] == 'PUBLISHED'
    text = '\n'.join(p.text for p in Document(target).paragraphs)
    assert 'My own warehouse example.' in text and 'Personal cover' in text
    assert text.count('MYNOTES START OPS7-L01') == 1
    assert 'OLD-NOTES-START' not in text


def test_unpublished_or_changed_generated_document_is_not_adopted(tmp_path):
    ws, target, source, journal = legacy_fixture(tmp_path)
    source.write_bytes(source.read_bytes() + b'changed')
    before = target.read_bytes()
    with pytest.raises(pub.PublicationError, match='latest completed'):
        import_generated_baseline(ws, 'OPS7', journal=journal, document=source)
    assert target.read_bytes() == before


def test_visible_historical_markers_are_not_writable_boundaries(tmp_path):
    ws, target, source, journal = legacy_fixture(tmp_path)
    d = Document(source)
    for p in d.paragraphs:
        for run in p.runs: run.font.hidden = False
    with pytest.raises(pub.PublicationError, match='not hidden'):
        pub._boundaries(d, 'OPS7-L01')


def test_ambiguous_mixed_markers_are_rejected(tmp_path):
    ws, target, source, journal = legacy_fixture(tmp_path)
    d = Document(source)
    d.add_paragraph('MYNOTES START OPS7-L01')
    d.add_paragraph('MYNOTES END OPS7-L01')
    with pytest.raises(pub.PublicationError, match='ambiguous'):
        pub._boundaries(d, 'OPS7-L01')


def test_import_receipt_change_blocks_publication_without_modifying_word(tmp_path, monkeypatch):
    ws, target, source, journal = legacy_fixture(tmp_path)
    result = import_generated_baseline(ws, 'OPS7', journal=journal, document=source)
    Path(result['receipt']).write_text('{}')
    before = target.read_bytes()
    monkeypatch.setattr(pub, '_render_and_validate', fake_render)
    with pytest.raises(pub.PublicationError, match='receipt changed'):
        pub.update_course_notes(ws, 'OPS7-L01')
    assert target.read_bytes() == before
