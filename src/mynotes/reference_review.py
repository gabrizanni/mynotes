"""Explicit source-link rectification without rewriting certified content."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

from .progressive_review import digest
from .utils import atomic_json_write, file_sha256
from .workflow import WorkflowError, _load_generation_phase


def apply(notes, review, lesson_evidence):
    if (set(review) != {'version', 'base_notes_sha256', 'reason', 'changes'}
            or type(review['version']) is not int or review['version'] not in {1,2} or review['base_notes_sha256'] != digest(notes)
            or not isinstance(review['reason'], str) or not review['reason'].strip()
            or not isinstance(review['changes'], list) or not 1 <= len(review['changes']) <= 12):
        raise WorkflowError('Reference review needs an unchanged base, reason and bounded explicit changes')
    result = deepcopy(notes); seen = set()
    for change in review['changes']:
        if (not isinstance(change, dict)
                or set(change) != {'target', 'index', 'lecture_id', 'before_sha256', 'evidence_ids', 'reason'}
                or type(change['index']) is not int or change['index'] < 0
                or not isinstance(change['reason'], str) or not change['reason'].strip()):
            raise WorkflowError('Reference review may change only explicit evidence IDs')
        owner = change['lecture_id']; key = (change['target'], owner, change['index'])
        if owner not in lesson_evidence or key in seen:
            raise WorkflowError('Reference review has an unknown or duplicate target')
        seen.add(key)
        try:
            if change['target'] == 'organization':
                target = result['organization'][change['index']]
                if target['owner'] != owner or target['status'] != 'taught':
                    raise WorkflowError('Reference review cannot change page ownership or teaching status')
            elif change['target'] == 'block':
                lesson = next(row for row in result['lessons'] if row['lecture_id'] == owner)
                target = lesson['notes']['blocks'][change['index']]
            else:
                raise WorkflowError('Unknown reference review target')
        except (IndexError, KeyError, StopIteration) as error:
            raise WorkflowError('Reference review target no longer exists') from error
        ids = change['evidence_ids']; before = target.get('evidence_ids', [])
        if (digest(target) != change['before_sha256'] or not isinstance(ids, list)
                or not 1 <= len(ids) <= 256 or any(not isinstance(i, str) for i in ids)
                or len(set(ids)) != len(ids) or set(ids) - set(lesson_evidence[owner])
                or (review['version']==1 and (
                    {i for i in ids if not i.startswith('T')} != {i for i in before if not i.startswith('T')}
                    or not any(i.startswith('T') for i in ids)))):
            raise WorkflowError('Reference review has stale, foreign or changed source-page references')
        if review['version']==2:
            # An explicit operator may attach a missing source page only after
            # checking that its explanation is already present. This is not an
            # automatic coverage fallback or permission to invent an explanation.
            added=set(ids)-set(before)
            owners={row['canonical_slide_id']:row['owner'] for row in notes['organization']
                    if row['status'] in {'taught','supplement'}}
            if (change['target']!='block' or target.get('kind') not in {'paragraph','bullets'}
                    or not set(before)<=set(ids) or not added
                    or any(not i.startswith('S') or owners.get(i)!=owner for i in added)):
                raise WorkflowError('Source-page attachment may only add owned slide IDs to an existing explanation')
        # Everything except this field stays byte-for-byte equivalent as JSON.
        target['evidence_ids'] = list(ids)
    return result


def rectify(response, request, base_path, receipt_path, review=None):
    """One durable, explicit local rectification; never a new provider allowance."""
    saved = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
    if saved and review is not None and saved['review'] != review:
        raise WorkflowError('Reference review already recorded; reread the saved rectification')
    review = saved['review'] if saved else review
    if review is None:
        return response, None
    base = _load_generation_phase(base_path, previous=None)
    if base is None or base.notes != response.notes:
        raise WorkflowError('Reference review requires the original saved successful response')
    evidence = request.batch_context['lesson_evidence']
    corrected = apply(response.notes, review, evidence)
    record = {'kind': 'explicit_reference_review', 'review': review,
              'base_path': str(base_path), 'base_sha256': file_sha256(base_path),
              'lesson_evidence': evidence, 'corrected_notes_sha256': digest(corrected),
              'provider_invoked': False, 'narrative_changed': False}
    if saved and saved != record:
        raise WorkflowError('Saved reference review provenance changed')
    if not saved:
        atomic_json_write(receipt_path, record)
    return replace(response, notes=corrected), {'path': str(receipt_path), 'sha256': file_sha256(receipt_path)}


def verify(reference, workspace_root):
    path = Path(reference['path']); root = workspace_root.resolve()
    if (path.is_symlink() or not path.resolve().is_relative_to(root)
            or file_sha256(path) != reference['sha256']):
        raise WorkflowError('Reference review receipt changed or is outside the workspace')
    record = json.loads(path.read_text()); base_path = Path(record['base_path'])
    if (base_path.is_symlink() or not base_path.resolve().is_relative_to(root)
            or file_sha256(base_path) != record['base_sha256']):
        raise WorkflowError('Reference review original response changed')
    base = _load_generation_phase(base_path, previous=None)
    corrected = apply(base.notes, record['review'], record['lesson_evidence'])
    if (record['kind'] != 'explicit_reference_review' or record['provider_invoked'] is not False
            or record['narrative_changed'] is not False or digest(corrected) != record['corrected_notes_sha256']):
        raise WorkflowError('Reference review changed content outside its source links')
