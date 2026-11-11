"""A synchronous notes request owns its work through Word delivery.

The conversational agent selects intent and sources. This controller, rather
than the chat's next turn, performs technical recovery, Word builds and update.
It never starts ASR, changes a model, or retries a successful model response.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

from . import batch
from .providers import ProviderError
from .publication import PublicationError, _load_candidate
from .utils import file_sha256
from .workflow import WorkflowError


@contextmanager
def _request_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.notes.lock').open('a+') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise WorkflowError('This notes request is already running; attach with batch-status. No second job was started.') from error
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _candidate_hash(path):
    payload = json.loads(path.read_text())
    payload['published_to_word'] = False
    payload.pop('published_path', None)
    payload.pop('published_hash', None)
    return batch._hash(payload)


def _valid_files(files):
    return bool(files) and all(Path(row['path']).is_file() and file_sha256(Path(row['path'])) == row['sha256'] for row in files)


def _transcript_boundary(workspace, lectures):
    """Declared partial coverage is a boundary, never infer completeness from length."""
    usable, warnings = [], []
    for lecture in lectures:
        path = workspace.runs_dir / lecture / 'source_pack.json'
        try:
            pack = json.loads(path.read_text())
        except (OSError, ValueError):
            return usable, warnings + [{'lecture_id': lecture, 'code': 'MISSING_SOURCE_PACK'}]
        turns = [row for row in pack.get('evidence', []) if str(row.get('id', '')).startswith('T')]
        if not turns:
            return usable, warnings + [{'lecture_id': lecture, 'code': 'MISSING_TRANSCRIPT'}]
        # Only explicit source metadata can prove a known coverage loss. Plain
        # TXT remains usable, but its length is not an audio completeness test.
        descriptors = [pack, pack.get('transcript', {}), pack.get('transcription', {}),
                       *pack.get('sources', [])]
        from .sourcepack import _recording_coverage
        descriptors.extend(_recording_coverage(workspace, Path(row['path'])) or {}
                           for row in pack.get('sources', []) if row.get('role') == 'imported_transcript')
        partial = any(isinstance(row, dict) and (
            row.get('partial') is True or row.get('coverage_complete') is False
            or row.get('coverage_warning') or row.get('coverage_warnings')) for row in descriptors)
        if partial:
            return usable, warnings + [{'lecture_id': lecture, 'code': 'PARTIAL_TRANSCRIPT',
                                       'message': 'Source metadata declares incomplete coverage; stop before this lesson.'}]
        usable.append(lecture)
    return usable, warnings


def _local_word(operation, progress):
    """One local retry for a timeout/temporary IO error, never content validation."""
    for attempt in range(2):
        try:
            return operation()
        except (OSError, subprocess.TimeoutExpired, PublicationError) as error:
            transient = isinstance(error, (OSError, subprocess.TimeoutExpired)) or any(
                marker in str(error).lower() for marker in ('timed out', 'timeout', 'temporarily unavailable'))
            if attempt or not transient:
                raise
            progress('LOCAL_WORD_RETRY', {'reason': str(error), 'provider_invoked': False})


def _generate_with_local_recovery(workspace, code, numbers, resume, kwargs, adapter, autonomy, emit, review_defects=None):
    """A technical local recovery may continue; provider errors never enter a retry loop."""
    for local_attempt in range(2):
        try:
            return batch.generate_batch(workspace, code, numbers, batch_id=resume,
                provider_adapter=adapter, repair_missing=True, build_word=True,
                word_mode='standalone', autonomous_organization=autonomy, review_defects=review_defects, **kwargs)
        except PublicationError as error:
            transient = any(marker in str(error).lower() for marker in ('timeout', 'timed out', 'temporarily unavailable'))
            if local_attempt or not transient:
                raise
            emit('LOCAL_WORD_RETRY', {'reason': str(error), 'provider_invoked': False})
        except WorkflowError:
            if local_attempt:
                raise
            # This diagnosis cannot call a provider or alter a retained attempt.
            # Apply only a certified, complete locally recoverable result.
            from .local_recovery import recover_batch
            identifier = resume or code + '-' + batch._hash([f'{code}-L{n:02d}' for n in numbers])[:12]
            diagnosis = recover_batch(workspace, identifier)
            if not any(row.get('status') == 'LOCALLY_RECOVERABLE' for row in diagnosis.get('lessons', [])):
                raise
            emit('RECOVERING_LOCALLY', {'provider_invoked': False, 'diagnosis': diagnosis})
            recover_batch(workspace, identifier, apply=True)
    raise WorkflowError('Local recovery did not reach a resumable checkpoint')


def run_notes(workspace, course_code, lesson_numbers=None, *, resume=None, provider=None,
              quality=None, instructions=None, image_limit=None, selection=None,
              budgets=None, speaker_focus=None, context_lessons=None, plan_only=False,
              contiguous_transcripts=False, provider_adapter=None, progress=None, review_defects=None):
    """Run one explicit request; existing candidate-only commands are unchanged."""
    if review_defects is not None and (not resume or plan_only):
        raise WorkflowError('Reviewed defects require --resume of an existing batch')
    code = course_code.upper()
    if isinstance(budgets, batch.BatchBudget):
        budgets = asdict(budgets)
    saved = batch.load_batch_manifest(workspace, resume) if resume else {}
    if saved and saved['course_code'] != code:
        raise WorkflowError('Resume batch belongs to a different course')
    numbers = sorted(set(lesson_numbers or [int(l.rsplit('-L', 1)[1]) for l in saved.get('lectures', [])]))
    if not numbers:
        raise WorkflowError('Supply --lessons or --resume BATCH_ID')
    if saved and [f'{code}-L{n:02d}' for n in numbers] != saved['lectures']:
        raise WorkflowError('Resume preserves the original selected lessons; finish it before requesting another range')
    original_lectures = [f'{code}-L{n:02d}' for n in numbers]
    boundary_warnings = []
    if contiguous_transcripts:
        if numbers != list(range(numbers[0], numbers[-1] + 1)):
            raise WorkflowError('A contiguous transcript request cannot skip lesson numbers')
        usable, boundary_warnings = _transcript_boundary(workspace, original_lectures)
        if saved and usable != original_lectures:
            return {'status': 'SOURCE_BOUNDARY', 'batch_id': resume, 'lectures': usable,
                    'warnings': boundary_warnings, 'provider_invoked': False, 'published_to_word': False}
        numbers = [int(l.rsplit('-L', 1)[1]) for l in usable]
        if not numbers:
            return {'status': 'SOURCE_BOUNDARY', 'lectures': [], 'warnings': boundary_warnings,
                    'provider_invoked': False, 'published_to_word': False}
    options = saved.get('request_options', {})
    kwargs = dict(provider=provider or saved.get('requested_model', {}).get('provider'),
                  quality=quality or saved.get('requested_model', {}).get('quality', 'standard'),
                  instructions=instructions if instructions is not None else options.get('instructions', ''),
                  image_limit=image_limit if image_limit is not None else options.get('image_limit'),
                  selection=selection if selection is not None else saved.get('selection'),
                  budgets=budgets if budgets is not None else saved.get('budgets'),
                  speaker_focus=speaker_focus or options.get('speaker_focus'),
                  context_lessons=context_lessons if context_lessons is not None else saved.get('context_lessons'),
                  writing_snapshot=saved if saved.get('course') and saved.get('style') else None)
    # A settled legacy job keeps its exact historical writing request. New
    # requests integrate organization into the normal writing response.
    # Explicit complete requests migrate unfinished historical placement too;
    # generation retains and revalidates paid writing under a recorded revision.
    autonomy = bool(saved.get('autonomous_organization') or saved.get('organization_pending')) if saved else True
    if saved and autonomy:
        # Preserve completed paid portions of pre-joint jobs. This compatibility
        # path is never selected for a fresh request.
        kwargs['writing_snapshot']['organization_mode'] = saved.get('organization_mode', 'progressive_legacy')
    plan = batch.plan_batch(workspace, code, numbers, autonomous_organization=autonomy, **kwargs)
    if plan_only:
        return {**plan, 'requested_lectures': original_lectures, 'warnings': boundary_warnings,
                'provider_invoked': False, 'published_to_word': False, 'delivery': 'standalone_and_course_update'}
    if plan['status'] != 'PLANNED':
        return {**plan, 'warnings': boundary_warnings, 'published_to_word': False}
    # Keep only the fields that define writing, not progress or source revisions.
    kwargs['writing_snapshot'] = {key: plan[key] for key in ('course_code', 'lectures', 'course', 'style', 'requested_model')}
    if plan.get('organization_mode'):
        kwargs['writing_snapshot']['organization_mode'] = plan['organization_mode']
    root = workspace.runs_dir / '_batches' / (resume or plan['batch_id'])
    state_path = root / 'notes-run.json'
    observer = progress or (lambda *_: None)
    with _request_lock(root):
        previous = batch._read(state_path) if state_path.exists() else {}
        state = {**previous, 'batch_id': resume or plan['batch_id'], 'lectures': plan['lectures'],
                 'requested_lectures': original_lectures, 'pid': os.getpid(),
                 'warnings': list({batch._hash(w): w for w in previous.get('warnings', []) + boundary_warnings}.values()),
                 'model': plan['requested_model'], 'scope_fingerprint': batch._hash({
                     'sources': plan['sources'], 'requested': plan['requested_model'], 'style': plan['style'],
                     'course': plan['course'], 'context_sources': plan.get('context_sources', {}),
                     'selection': kwargs['selection'], 'autonomy': autonomy})}
        if previous and previous.get('scope_fingerprint') != state['scope_fingerprint']:
            # A newly reviewed selection is legitimate, but the old completion
            # cannot certify it; batch resume must revalidate retained writing.
            state.pop('generation_complete', None)
            state.pop('delivery', None)
            state.pop('candidate_hashes', None)

        stage_started = time.monotonic()
        stage_wall = time.time()
        current_stage = None
        def emit(stage, detail=None):
            nonlocal stage_started, stage_wall, current_stage
            now = time.monotonic()
            if current_stage is not None:
                state.setdefault('timeline', []).append({'phase': current_stage,
                    'started_at_epoch': stage_wall, 'ended_at_epoch': time.time(),
                    'elapsed_seconds': now - stage_started,
                    'scope': 'controller wall time; nested provider/Word receipts are not additive'})
            stage_started, stage_wall, current_stage = now, time.time(), stage
            state.update(status=stage, updated_at=time.time(), activity=detail or {})
            batch._save(state_path, state)
            observer(stage, detail or {})

        try:
            if (review_defects is None and previous.get('status') in {'COMPLETED', 'COMPLETED_WITH_WARNINGS'} and state.get('generation_complete')
                    and _valid_files(previous.get('delivery', {}).get('files', []))
                    and previous.get('publication', {}).get('output')
                    and Path(previous['publication']['output']).is_file()
                    and file_sha256(Path(previous['publication']['output'])) == previous['publication'].get('output_hash')):
                if previous.get('problem'):
                    previous.setdefault('resolved_problems', []).append({
                        **previous.pop('problem'), 'resolved_at_epoch': time.time()})
                    batch._save(state_path, previous)
                return {**previous, 'reused': True, 'provider_invoked': False}
            reusable = review_defects is None and bool(state.get('generation_complete'))
            if review_defects is not None:
                state.pop('delivery', None)
            for lecture in plan['lectures']:
                if not reusable:
                    break
                path = workspace.runs_dir / lecture / 'lecture_notes.candidate.json'
                reusable = path.is_file() and _candidate_hash(path) == state.get('candidate_hashes', {}).get(lecture)
                if reusable:
                    _load_candidate(workspace, lecture)
            if not reusable:
                emit('GENERATING', {'next_action': 'recover_saved_work_before_provider'})
                generated = _generate_with_local_recovery(workspace, code, numbers, resume,
                    kwargs, provider_adapter, autonomy, emit, review_defects)
                state['generation'] = {'status': generated['status'], 'fingerprint': generated.get('fingerprint')}
                if generated['status'] != 'CANDIDATE_READY':
                    emit(generated['status'], generated.get('problem'))
                    return deepcopy(state)
                state['generation_complete'] = True
                state['candidate_hashes'] = {l: _candidate_hash(workspace.runs_dir / l / 'lecture_notes.candidate.json') for l in plan['lectures']}
                state['warnings'] += generated.get('warnings', [])
            emit('BUILDING_WORD')
            if not _valid_files(state.get('delivery', {}).get('files', [])):
                from .batch_documents import build_batch_documents
                state['delivery'] = _local_word(lambda: build_batch_documents(workspace, state['batch_id'], mode='both'), emit)
            emit('UPDATING_WORD')
            from .course_update import update_course
            result = _local_word(lambda: update_course(workspace, plan['lectures'], timeout_seconds=900), emit)
            state['publication'] = result
            if result['status'] in {'PUBLISHED', 'SKIPPED'}:
                if state.get('problem'):
                    state.setdefault('resolved_problems', []).append({
                        **state.pop('problem'), 'resolved_at_epoch': time.time()})
                target = Path(result.get('output', result.get('target', '')))
                if target.is_file():
                    state['publication'].update(output=str(target), output_hash=file_sha256(target))
                emit('COMPLETED_WITH_WARNINGS' if state['warnings'] else 'COMPLETED')
            else:
                # Review preference and personal conflicts are genuine external
                # choices, never silently override them to display completion.
                emit(result['status'], {'published_to_word': result.get('published_to_word', False)})
            return deepcopy(state)
        except (ProviderError, WorkflowError, PublicationError, OSError, ValueError, KeyError, TypeError) as error:
            state['problem'] = {'kind': type(error).__name__, 'message': str(error),
                                'saved_content_preserved': True, 'failed_phase': state.get('status'),
                                'next_action': f'mynotes notes {code} --resume {state["batch_id"]}'}
            # Batch owns all provider retry accounting; the controller never
            # wraps an exhausted/failed model call in another retry loop.
            try:
                current = batch.load_batch_manifest(workspace, state['batch_id'])
                status = current.get('status', 'STOPPED')
                state['problem']['batch_problem'] = current.get('problem')
            except (WorkflowError, OSError):
                status = 'STOPPED'
            emit(status if status not in {'RUNNING', 'CANDIDATE_READY', 'PLANNED'} else 'STOPPED')
            return deepcopy(state)
        except KeyboardInterrupt:
            emit('INTERRUPTED', {'saved_content_preserved': True})
            raise
