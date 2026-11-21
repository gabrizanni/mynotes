"""Import an identified generated Word baseline, never infer personal content.

An explicitly supplied, stable publication journal certifies the last generated
document. The current handout may contain later personal edits: diff3 preserves
those against this archived generated baseline during the ordinary course update.
"""
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import sqlite3

from docx import Document

from .course_update import baseline_index, _index_path
from .publication import PublicationError, _course, _target, _boundaries
from .utils import atomic_json_write, file_sha256
from .workflow import parse_lecture_id, _generation_lock, _publication_lock


def import_generated_baseline(workspace, code, *, journal, document):
    code = code.upper()
    parse_lecture_id(code + '-L01')
    journal, document = Path(journal).resolve(), Path(document).resolve()
    if not journal.is_file() or not document.is_file():
        raise PublicationError('The original publication journal and generated Word must be locally available')
    wal = Path(str(journal) + '-wal')
    if wal.is_file() and wal.stat().st_size:
        raise PublicationError('Use a consistent, stopped journal snapshot before importing a generated baseline')
    journal_hash, document_hash = file_sha256(journal), file_sha256(document)
    with sqlite3.connect(journal.as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT review_id,lecture_id,target_sha256 FROM publication_transactions WHERE status='COMPLETED' AND lecture_id LIKE ? ORDER BY completed_at DESC LIMIT 1", (code + '-L%',)).fetchone()
        if row is None or row['target_sha256'] != document_hash:
            raise PublicationError('Document is not the latest completed generated publication in this journal')
        review = db.execute('SELECT status,candidate_docx FROM reviews WHERE review_id=?', (row['review_id'],)).fetchone()
        if review is None or review['status'] != 'PUBLISHED':
            raise PublicationError('The matching original revision was not published')
        # A relocated immutable copy is permitted, but its bytes must still be
        # those certified by the completed publication transaction above.
    source = Document(document)
    lessons = []
    for number in range(1, 100):
        lecture = f'{code}-L{number:02d}'
        if _boundaries(source, lecture) is not None: lessons.append(lecture)
    if not lessons:
        raise PublicationError('Generated baseline has no unambiguous lesson boundaries')
    course = _course(workspace.configuration_store().load(), code)
    target = _target(course, workspace)
    if not target.is_file():
        raise PublicationError('A remembered handout is missing; baseline import does not recreate it')
    with ExitStack() as locks:
        for lecture in lessons: locks.enter_context(_generation_lock(workspace, lecture))
        locks.enter_context(_publication_lock(target))
        target_hash = file_sha256(target)
        current = Document(target)
        for lecture in lessons:
            if _boundaries(current, lecture) is None:
                raise PublicationError('Current handout does not contain the same verified lesson identities')
        index = baseline_index(workspace, code)
        for lecture in lessons:
            if lecture in index and index[lecture]['sha256'] != document_hash:
                raise PublicationError('A different generated baseline already exists; preserve it for ordinary three-way merge')
        directory = workspace.archives_dir / 'generated-baselines' / code / document_hash
        directory.mkdir(parents=True, exist_ok=True)
        saved = directory / 'generated.docx'
        saved_journal = directory / 'publication.sqlite3'
        if not saved.exists(): shutil.copy2(document, saved)
        if not saved_journal.exists(): shutil.copy2(journal, saved_journal)
        if file_sha256(saved) != document_hash or file_sha256(saved_journal) != journal_hash or file_sha256(journal) != journal_hash or file_sha256(document) != document_hash:
            raise PublicationError('Baseline import inputs changed; current Word was not modified')
        receipt = {'kind':'verified_generated_baseline_import','course_code':code,'lectures':lessons,
                   'journal':str(saved_journal),'journal_sha256':journal_hash,'review_id':row['review_id'],
                   'document_sha256':document_hash,'current_handout_sha256':target_hash,'current_handout_unchanged':True}
        receipt_path = directory / f'import-{journal_hash[:12]}-{target_hash[:12]}.json'
        atomic_json_write(receipt_path, receipt)
        for lecture in lessons:
            index.setdefault(lecture, {'path':str(saved),'sha256':document_hash,
                                      'import_receipt':str(receipt_path),'import_receipt_sha256':file_sha256(receipt_path)})
        if file_sha256(target) != target_hash:
            raise PublicationError('Current handout changed during baseline import; no Word replacement occurred')
        atomic_json_write(_index_path(workspace, code), index)
        return {'status':'BASELINE_IMPORTED','lectures':lessons,'receipt':str(receipt_path),
                'current_handout_unchanged':True,'provider_invoked':False}
