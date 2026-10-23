"""Checkpointed portions of one writing phase in one Agy conversation.

A portion is accepted only from its own SUCCESS result. Final notes still pass
all normal academic/Word validation. Failed turns are never salvaged as notes.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import threading
import time
from typing import Callable

from .providers import (ProviderError, ProviderCancelledError, ProviderQuotaError,
                        ProviderTransportError, ProviderAuthenticationError, ProviderSuspendedError, ProviderTimeoutError,
                        ProviderRequest, ProviderResponse, RequestedModel, _agy_schema, _context,
                        _decode_agy, _normalize_transport_notes, _prompt, _quota_error,
                        _save_attempt, _stage_agy_context, _stage_agy_writer,
                        notes_schema, provider_environment, provider_executable,
                        provider_override_flags)
from .utils import atomic_json_write, file_sha256
from .provider_limits import validate_command_size
from .execution import AttemptStart, SleepMonitor, prevent_idle_sleep
from .generation_budget import MODEL_PHASE_SECONDS, CLI_PHASE_TIMEOUT, invocation_timeout

PROTOCOL = 1
BLOCKS_PER_PART = 12
MAX_PARTS = 16
TOTAL_SECONDS = MODEL_PHASE_SECONDS  # local checks have a separate allowance
FIRST_TURN_SECONDS = 300
NEXT_TURN_SECONDS = 300


def transport_error(message: str) -> ProviderError:
    if _quota_error(message):
        return ProviderQuotaError(message[:700])
    if re.search(r'authentication|unauthorized|not logged|sign.in|login required|\b401\b', message, re.I):
        return ProviderAuthenticationError(message[:700])
    if re.search(r'broken pipe|connection reset|can.t assign requested address|network issue|unexpected EOF|stream.*EOF|\b50[234]\b|connection.*(?:closed|refused|timed out)|request failed.*(?:tcp|socket)', message, re.I | re.S):
        return ProviderTransportError(message[:700])
    return ProviderError(message[:700])


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def part_schema(request: ProviderRequest) -> dict:
    result = deepcopy(request.response_schema or notes_schema(compact=True))
    note = result['properties']['lessons']['items']['properties']['notes'] if request.batch_context is not None else result
    note['properties']['blocks']['maxItems'] = BLOCKS_PER_PART
    return {'type': 'object', 'additionalProperties': False,
            'properties': {'part_index': {'type': 'integer'}, 'complete': {'type': 'boolean'},
                           'next_topic': {'type': 'string'}, 'result': result},
            'required': ['part_index', 'complete', 'next_topic', 'result']}


def _entries(request: ProviderRequest, value: dict) -> list[tuple[str, dict]]:
    if request.batch_context is None:
        return [(request.lecture_id, value)]
    rows = value.get('lessons')
    if not isinstance(rows, list) or not rows:
        raise ProviderError('Writing portion requires at least one lesson')
    expected = request.batch_context['lessons']
    ids = [r.get('lecture_id') for r in rows if isinstance(r, dict)]
    if len(ids) != len(rows) or len(set(ids)) != len(ids) or any(i not in expected for i in ids) or ids != sorted(ids, key=expected.index):
        raise ProviderError('Writing portions changed the selected lesson order')
    return [(r['lecture_id'], r.get('notes')) for r in rows]


def assemble(request: ProviderRequest, parts: list[dict]) -> dict:
    """Lossless assembly; no inferred coverage, role, inspection or missing text."""
    expected = request.batch_context['lessons'] if request.batch_context else [request.lecture_id]
    merged, seen, last_lesson = {}, {}, 0
    valid_ids = {r['id'] for r in request.source_pack.get('evidence', [])}
    for number, part in enumerate(parts):
        from .evidence_refs import normalize_references, EvidenceReferenceError
        try:
            part, _ = normalize_references(part, valid_ids)
        except EvidenceReferenceError as error:
            raise ProviderError('Writing portion cites unknown evidence: ' + str(error)) from error
        if (not isinstance(part, dict) or type(part.get('part_index')) is not int or part['part_index'] != number
                or type(part.get('complete')) is not bool or not isinstance(part.get('next_topic'), str)
                or not isinstance(part.get('result'), dict)):
            raise ProviderError('Invalid or nonconsecutive writing portion')
        if number < len(parts)-1 and part['complete']:
            raise ProviderError('A completed writing portion cannot have successors')
        if not part['complete'] and not part['next_topic'].strip():
            raise ProviderError('Incomplete writing portion has no continuation')
        if part['complete'] and part['next_topic'].strip():
            raise ProviderError('Completed writing portion still declares unfinished content')
        count = 0
        for lecture, note in _entries(request, part['result']):
            position = expected.index(lecture)
            if position < last_lesson:
                raise ProviderError('Writing returned to an earlier lesson')
            last_lesson = position
            if not isinstance(note, dict) or not isinstance(note.get('blocks'), list) or not note['blocks']:
                raise ProviderError('Writing portion has no structured content')
            count += len(note['blocks'])
            if lecture not in merged:
                merged[lecture] = deepcopy(note)
                for key in ('blocks', 'evidence_coverage', 'visual_decisions', 'model_knowledge', 'review_questions'):
                    merged[lecture][key] = []
            target = merged[lecture]
            for block in note['blocks']:
                if not isinstance(block, dict) or not isinstance(block.get('evidence_ids'), list) or set(block['evidence_ids']) - valid_ids:
                    raise ProviderError('Writing portion cites unknown evidence')
                kind = block.get('kind')
                if kind == 'overview' and target['blocks']:
                    raise ProviderError('Writing repeated the lesson overview')
                if kind in {'paragraph', 'bullets', 'table', 'formula', 'visual'}:
                    body = {k:block[k] for k in ('text','items','rows','expression','visual_id') if k in block}
                    normalized = re.sub(r'\s+', ' ', json.dumps(body, sort_keys=True, ensure_ascii=False)).casefold()
                    key = (lecture, normalized)
                    if key in seen:
                        raise ProviderError('Writing repeats an already saved narrative block')
                    seen[key] = True
                target['blocks'].append(deepcopy(block))
            for key, idkey, states in (('evidence_coverage','evidence_id',('disposition',)), ('visual_decisions','evidence_id',('decision','inspected'))):
                rows = note.get(key)
                if not isinstance(rows, list):
                    raise ProviderError('Writing portion lacks evidence/visual decisions')
                existing = {r[idkey]:r for r in target[key]}
                for row in rows:
                    if not isinstance(row, dict) or row.get(idkey) not in valid_ids:
                        raise ProviderError('Writing decision cites unknown evidence')
                    old = existing.get(row[idkey])
                    if old and any(old.get(k) != row.get(k) for k in states):
                        raise ProviderError('Writing portions contain conflicting source decisions')
                    if not old:
                        target[key].append(deepcopy(row)); existing[row[idkey]] = row
            for key in ('model_knowledge','review_questions'):
                for item in note.get(key, []):
                    if item not in target[key]: target[key].append(deepcopy(item))
            if any(note.get(k) for k in ('sections','bullets','table','formulas')) or note.get('formula'):
                raise ProviderError('Writing portions must keep legacy parallel narrative empty')
        if not 1 <= count <= BLOCKS_PER_PART:
            raise ProviderError('Writing portion exceeds its block budget; retained, not retried')
    if parts and parts[-1]['complete'] and list(merged) != expected:
        raise ProviderError('Writing declared completion before all selected lessons')
    return {'lessons':[{'lecture_id':k,'notes':v} for k,v in merged.items()]} if request.batch_context else merged.get(request.lecture_id,{})


def _wire(prompt: str) -> bytes:
    return (json.dumps({'event':'user','message':{'content':prompt}}, ensure_ascii=True)+'\n').encode()


def run_session(command: list[str], prompt: str, accept: Callable[[dict], str | None], *, cwd: str, env: dict,
                total_seconds: float = TOTAL_SECONDS, first_seconds: float = FIRST_TURN_SECONDS,
                next_seconds: float = NEXT_TURN_SECONDS, on_event: Callable[[dict], None] | None = None) -> subprocess.CompletedProcess:
    """One process/conversation; separate stderr, bounded turns, group cleanup."""
    outputs = [bytearray(), bytearray()]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               cwd=cwd, env=env, start_new_session=True)
    previous = None
    if threading.current_thread() is threading.main_thread():
        def interrupted(_sig, _frame): raise ProviderCancelledError('Note generation interrupted by user')
        previous = signal.signal(signal.SIGTERM, interrupted)
    started = turn_started = time.monotonic(); turn_limit = first_seconds
    pending = b''; finished = False; useful = set(); read_requests = set(); sleep = SleepMonitor()
    try:
        process.stdin.write(_wire(prompt)); process.stdin.flush()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, 0)
            selector.register(process.stderr, selectors.EVENT_READ, 1)
            while selector.get_map():
                now = time.monotonic()
                slept = sleep.poll()
                if slept:
                    error = ProviderSuspendedError(f'System suspended for {slept:.0f}s; old transport closed')
                    error.suspended_seconds = slept
                    raise error
                if now-started > total_seconds or now-turn_started > turn_limit:
                    total_expired = now-started > total_seconds
                    error = subprocess.TimeoutExpired(command, total_seconds if total_expired else turn_limit)
                    error.budget_kind = "phase" if total_expired else "useful-progress"
                    error.elapsed_seconds = now-started if total_expired else now-turn_started
                    raise error
                ready = selector.select(timeout=min(.25, max(.01,total_seconds-(now-started))))
                if finished and process.poll() is not None and not ready:
                    # Agy descendants may retain pipe handles after its parent
                    # has exited. Require the actual parent status, not EOF.
                    break
                for key, _ in ready:
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj); continue
                    outputs[key.data].extend(data)
                    if key.data: continue
                    pending += data
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n',1)
                        if not line.strip(): continue
                        try: event = json.loads(line)
                        except (ValueError, UnicodeError) as error: raise ProviderError('Invalid Agy streaming event') from error
                        if on_event is not None: on_event(event)
                        step = event.get('step_update', {})
                        progress = (step.get('step_index'), step.get('step_type'))
                        initialization = ('init', event.get('conversation_id'))
                        useful_step = (event.get('event') == 'step_update' and step.get('state') == 'DONE'
                                       and step.get('step_type') in {'tool','agent_response'} and progress not in useful)
                        if useful_step and step.get('tool_name') == 'view_file':
                            read_key=json.dumps(step.get('tool_info',{}).get('parameters',{}),sort_keys=True)
                            useful_step=read_key not in read_requests
                            read_requests.add(read_key)
                        if ((event.get('event') == 'init' and initialization not in useful) or useful_step):
                            useful.add(initialization if event.get('event') == 'init' else progress)
                            turn_started = time.monotonic()
                        if event.get('event') != 'result': continue
                        if finished: raise ProviderError('Agy emitted another result after final completion')
                        result = event.get('result')
                        if not isinstance(result, dict): raise ProviderError('Agy result is not an object')
                        next_prompt = accept(result)
                        if next_prompt is None:
                            process.stdin.close(); process.stdin = None; finished = True
                            turn_limit = 5; turn_started = time.monotonic()
                        else:
                            process.stdin.write(_wire(next_prompt)); process.stdin.flush()
                            turn_started = time.monotonic(); turn_limit = next_seconds
            code = process.wait(timeout=3)
            try: os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            if not finished or pending.strip(): raise ProviderError('Agy stream ended without a complete terminal result')
            return subprocess.CompletedProcess(command,code,outputs[0].decode(errors='replace'),outputs[1].decode(errors='replace'))
    except BaseException as error:
        try: os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError: pass
        if process.stdin is not None:
            try: process.stdin.close()
            except (OSError,ValueError): pass
            process.stdin = None
        try: out,err = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            try: os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            out,err = process.communicate()
        error.output = bytes(outputs[0])+(out or b''); error.stderr = bytes(outputs[1])+(err or b'')
        raise
    finally:
        if previous is not None: signal.signal(signal.SIGTERM,previous)
        for pipe in (process.stdout,process.stderr): pipe.close()


def _generate_parts(request: ProviderRequest) -> ProviderResponse:
    directory = request.run_dir.resolve()
    schema = part_schema(request)
    identity = fingerprint({
        'protocol':PROTOCOL,'prompt':_prompt(request),'source_pack':request.source_pack,'requested':asdict(request.requested),'schema':schema})
    root = directory/'writing-parts'/identity
    if not root.resolve().is_relative_to(directory):raise ProviderError('Writing checkpoint directory is outside the private run')
    root.mkdir(parents=True,exist_ok=True)
    state_path = root/'state.json'; parts=[]; refs=[]; state={}
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state.get('fingerprint') != identity: raise ProviderError('Writing checkpoint identity changed')
        if state.get('local_rejection'): raise ProviderError('Saved writing portion needs local review; no automatic paid retry')
        for ref in state.get('parts',[]):
            path = root/ref['file']
            if path.parent != root or path.is_symlink() or file_sha256(path) != ref['sha256']:
                raise ProviderError('Writing checkpoint was modified; no paid regeneration')
            payload=json.loads(path.read_text())
            if payload['envelope'].get('status') != 'SUCCESS': raise ProviderError('Checkpoint came from a failed turn')
            if payload['envelope'].get('conversation_id') != state.get('conversation_id'):raise ProviderError('Writing checkpoint conversation changed')
            if _normalize_transport_notes(_decode_agy(payload['envelope']),schema) != payload['part']:raise ProviderError('Writing checkpoint differs from its returned portion')
            parts.append(payload['part']); refs.append(ref)
        assemble(request,parts)
        if state.get('finalized'):
            attempt=Path(state['attempt_path']); saved=json.loads(attempt.read_text())
            verify_receipt(saved,directory)
            return ProviderResponse(assemble(request,parts),'agy','','',saved['envelope']['usage'],str(attempt))
        if parts and parts[-1]['complete']: raise ProviderError('Complete portions lack a clean final process exit; review retained transport')
    used_seconds=state.get('used_seconds',0)
    if type(used_seconds) not in (int,float) or not math.isfinite(used_seconds) or used_seconds<0:raise ProviderError('Writing checkpoint has an invalid time budget')
    remaining=min(TOTAL_SECONDS-used_seconds, invocation_timeout(request.timeout_seconds))
    if remaining <= 1: raise ProviderError('The lesson writing budget is exhausted; retained portions require review')
    _stage_agy_writer(directory)
    context = _stage_agy_context(request,directory)
    protocol = (
        f'INCREMENTAL WRITING PROTOCOL: this is one writing phase delivered in portions, NOT an additional review. '
        f'Return only the NEXT contiguous portion, at most {BLOCKS_PER_PART} blocks TOTAL, via finish. '
        'The response schema wraps result inside part_index, complete and next_topic. Ordinary lesson requirements apply across the ASSEMBLED result, not independently to each portion. '
        'Begin the first portion of a lesson with its one overview. Continue the teaching sequence without restarting. Do not compress explanations to make the whole lesson fit one response. '
        'Return only NEW blocks, NEW final source decisions and new questions. Empty legacy fields; formula=null. '
        'Coverage decisions need inventory for S/H only, not every transcript utterance. Cite relevant T IDs in narrative blocks. '
        'Finish promptly after a coherent portion instead of drafting the entire lesson in thoughts first. '
        'complete=false means more explanations remain; next_topic identifies the exact next topic. '
        'Only the final portion of each lesson contains its questions and recap. complete=true only when ALL selected lessons, owned slides and pertinent transcript concepts are covered; then next_topic is empty. '
        'Never repeat an earlier explanation/table/image. Other lessons\' slides are not an excuse to anticipate them. '
        'Do not inspect future-owned slides merely to exclude them; ownership supplies that reason. '
        'Across portions, the selected lesson order and full coverage rules stay mandatory.'
    )
    active=replace(request,response_schema=schema)
    initial=_prompt(active,context=context)+'\n'+protocol+'\nRequired part_index: '+str(len(parts))
    if parts:
        initial=protocol+f'\nResume part_index {len(parts)} in this same conversation. Previously saved portions are complete and must not be repeated. Continue at: '+parts[-1]['next_topic']
    command=[provider_executable('agy'),'--agent','mynotes-note-writer','--disable-slash-commands','--mode','plan',
             '--input-format','stream-json','--output-format','stream-json','--print-timeout',CLI_PHASE_TIMEOUT,
             *provider_override_flags(request.requested),'--json-schema',json.dumps(_agy_schema(schema),separators=(',',':')),'--add-dir',str(directory)]
    if state.get('conversation_id'): command += ['--conversation',state['conversation_id']]
    env={**provider_environment(),'MYNOTES_GENERATION_WORKER':'1'};validate_command_size(command,env)
    started=AttemptStart(); last={}
    def save_state(**changes):
        state.update({'fingerprint':identity,'parts':refs,'used_seconds':used_seconds+time.monotonic()-started,**changes})
        atomic_json_write(state_path,state)
    def observe(event):
        if used_seconds+time.monotonic()-started >= 600 and not state.get('ten_minute_warning'):
            print('MyNotes: writing active for more than ten minutes; saved portions retained, no completion estimate.',file=sys.stderr,flush=True)
            save_state(ten_minute_warning=True)
        if event.get('event') == 'init':
            cid = event.get('conversation_id')
            if not isinstance(cid, str) or not cid or (state.get('conversation_id') and state['conversation_id'] != cid):
                raise ProviderError('Agy changed its writing conversation at initialization')
            save_state(conversation_id=cid, status='WRITING')
        elif event.get('event') == 'step_update':
            step = event.get('step_update', {})
            if step.get('state') == 'DONE':
                save_state(last_step=step.get('step_index'), last_activity=step.get('step_type'))
    def accept(envelope):
        nonlocal last
        last=envelope
        status=str(envelope.get('status','')).upper()
        if status != 'SUCCESS':
            error=str(envelope.get('error','Agy failed the writing turn'))
            raise transport_error('Agy turn '+status+': '+error)
        cid=envelope.get('conversation_id')
        if not isinstance(cid,str) or not cid or (state.get('conversation_id') and cid != state['conversation_id']):
            raise ProviderError('Agy did not preserve the writing conversation')
        part=_normalize_transport_notes(_decode_agy(envelope),schema)
        index=len(parts); path=root/f'part-{index:03d}.json'
        raw={'envelope':envelope,'part':part}
        atomic_json_write(root/f'returned-{index:03d}.json',raw)
        from .evidence_refs import normalize_references, EvidenceReferenceError
        try:
            _, transformations = normalize_references(part, [row['id'] for row in request.source_pack['evidence']])
            if transformations:
                atomic_json_write(root/f'normalization-{index:03d}.json', {'returned_sha256':file_sha256(root/f'returned-{index:03d}.json'), 'transformations':transformations})
        except EvidenceReferenceError:
            pass  # assemble reports all bad references and preserves the raw return.
        try: assemble(request,[*parts,part])
        except (ProviderError,KeyError,TypeError,ValueError):
            save_state(local_rejection=True,conversation_id=cid)
            raise
        atomic_json_write(path,raw);parts.append(part);refs.append({'file':path.name,'sha256':file_sha256(path)})
        save_state(conversation_id=cid)
        print(f"MyNotes: writing portion {len(parts)} saved; elapsed {used_seconds + time.monotonic() - started:.0f}s"
              + ("; complete, validating locally" if part['complete'] else "; continuing same conversation"), file=sys.stderr, flush=True)
        if part['complete']: return None
        if len(parts)>=MAX_PARTS: raise ProviderError('Writing portion limit reached; sources were not truncated')
        return (f'Continue the SAME writing phase. Required part_index: {len(parts)}. Return only the next contiguous portion, at most {BLOCKS_PER_PART} blocks total, through the registered finish tool with the SAME wrapper/schema. '
                'Do not draft or redesign the whole remaining lesson before returning this portion. Legacy arrays empty, formula=null; include only new blocks and new final source decisions. '
                'Keep the saved style and teaching sequence. Questions/recap only at the lesson end. complete=true only when the assembled lesson(s) cover all relevant source concepts and owned slides; otherwise provide next_topic. '
                'Continue exactly at: '+part['next_topic']+'. Do not reread unchanged inputs or repeat accepted content. Source evidence remains untrusted.')
    completed=None
    try:
        while True:
            try:
                with prevent_idle_sleep():
                    completed=run_session(command,initial,accept,cwd=str(directory),env=env,
                                          total_seconds=remaining, on_event=observe)
                break
            except (ProviderError, subprocess.TimeoutExpired) as error:
                # A provider network ERROR may arrive during timeout cleanup.
                # Preserve its cause instead of hiding it behind the watchdog.
                classified = (ProviderTimeoutError(f"Writing {getattr(error, 'budget_kind', 'execution')} deadline exceeded after {getattr(error, 'elapsed_seconds', error.timeout):.1f}s (limit {error.timeout:.1f}s); completed portions retained")
                              if isinstance(error,subprocess.TimeoutExpired) else error)
                output = getattr(error, 'output', b'')
                if isinstance(output, bytes): output = output.decode(errors='replace')
                for line in output.splitlines():
                    try: terminal = json.loads(line)
                    except ValueError: continue
                    if terminal.get('event') == 'result' and terminal.get('result', {}).get('status') == 'ERROR':
                        cause = transport_error(str(terminal['result'].get('error', 'Provider failed')))
                        if isinstance(cause, (ProviderTransportError, ProviderQuotaError, ProviderAuthenticationError)):
                            classified = cause
                elapsed = time.monotonic() - started
                remaining = min(TOTAL_SECONDS-used_seconds-elapsed, (invocation_timeout(request.timeout_seconds))-elapsed)
                suspended = getattr(error, 'suspended_seconds', 0.)
                save_state(status='PAUSED_SLEEP' if suspended else 'FAILED',
                           suspended_seconds=state.get('suspended_seconds', 0.) + suspended,
                           problem={'kind':type(classified).__name__, 'message':str(classified)[:700]})
                failed = subprocess.CompletedProcess(command, None, output,
                            getattr(error, 'stderr', b''))
                if isinstance(failed.stderr, bytes): failed.stderr=failed.stderr.decode(errors='replace')
                _save_attempt(request,command=command,completed=failed,started=started,error=str(classified)[:700],
                              envelope={'transport':'agy_parts_v1','status':'ERROR','error_kind':type(classified).__name__})
                if not isinstance(classified, ProviderTransportError) or state.get('network_retries',0) >= 1 or remaining <= 11:
                    for attr in ('output','stderr'):
                        if hasattr(error,attr):setattr(classified,attr,getattr(error,attr))
                    raise classified
                save_state(network_retries=state.get('network_retries',0)+1,status='RETRY_WAIT')
                print('MyNotes: temporary connection failure; one resume in 10s with the same model',file=sys.stderr,flush=True)
                time.sleep(10)
                remaining -= 10
                if state.get('conversation_id'):
                    if '--conversation' not in command: command += ['--conversation',state['conversation_id']]
                    initial = protocol + f'\nResume part_index {len(parts)} in this same conversation after transport interruption. '
                    initial += 'Previously accepted portions must not be repeated. Reuse source files already read; inspect only missing input. '
                    initial += 'Continue at: ' + (parts[-1]['next_topic'] if parts else 'the first not-yet-saved teaching portion')
                save_state(status='WRITING')
        if completed.returncode != 0: raise ProviderError('Agy writing process failed after its last turn')
        notes=assemble(request,parts)
        if not parts or not parts[-1]['complete']: raise ProviderError('Writing stream did not finish the selected lessons')
        receipt=root/'complete.json';atomic_json_write(receipt,{'protocol':PROTOCOL,'fingerprint':identity,'parts':refs,'assembled_sha256':fingerprint(notes),'schema':schema,
            'lecture_id':request.lecture_id,'lessons':request.batch_context['lessons'] if request.batch_context else None,
            'source_ids':[row['id'] for row in request.source_pack['evidence']]})
        usage={'session_cumulative':last.get('usage',{}),'scope':'Raw cumulative session counters, not additive across resumed processes'}
        attempt=_save_attempt(request,command=command,completed=completed,started=started,envelope={
            'transport':'agy_parts_v1','status':'SUCCESS','usage':usage,'receipt':str(receipt),'receipt_sha256':file_sha256(receipt)})
        save_state(finalized=True,attempt_path=str(attempt),status='CONTENT_RETURNED',problem=None)
        return ProviderResponse(notes,'agy','','',usage,str(attempt))
    except BaseException as error:
        save_state()
        if completed is None and hasattr(error,'output'):
            def text(v):return v.decode(errors='replace') if isinstance(v,bytes) else v or ''
            completed=subprocess.CompletedProcess(command,None,text(error.output),text(error.stderr))
        attempt=_save_attempt(request,command=command,completed=completed,started=started,error=str(error)[:800],invocation_started=completed is not None,
            envelope={'transport':'agy_parts_v1','status':'ERROR','usage':{'session_cumulative':last.get('usage',{}),'scope':'Raw cumulative session counters, not additive across resumed processes'}})
        if isinstance(error,(KeyboardInterrupt,SystemExit)):raise
        message = ('Writing portion exceeded its time budget; completed portions retained' if isinstance(error,subprocess.TimeoutExpired) else str(error)[:700])
        raise type(error)(f'{message} (attempt: {attempt})') if isinstance(error,ProviderError) else ProviderError(f'Incremental writing failed: {message} (attempt: {attempt})') from error


def verify_receipt(record: dict, workspace_root: Path) -> dict:
    """Word cannot use incomplete/ERROR turns or altered portion checkpoints."""
    if record.get('error') or record.get('returncode') != 0:
        raise ProviderError('Incremental candidate came from a failed process')
    envelope=record.get('envelope',{})
    if envelope.get('transport') != 'agy_parts_v1' or envelope.get('status') != 'SUCCESS':raise ProviderError('Writing receipt is not successful')
    receipt=Path(envelope['receipt'])
    if not receipt.is_absolute() or receipt.is_symlink() or not receipt.resolve().is_relative_to(workspace_root.resolve()) or file_sha256(receipt) != envelope['receipt_sha256']:
        raise ProviderError('Incremental receipt was modified or moved outside the workspace')
    saved=json.loads(receipt.read_text());refs=saved['parts'];parts=[]
    if saved.get('protocol') != PROTOCOL or not refs or len(refs)>MAX_PARTS:
        raise ProviderError('Incomplete writing receipt')
    for index,ref in enumerate(refs):
        if ref['file'] != f'part-{index:03d}.json':raise ProviderError('Writing receipt changed the portion order')
        path=receipt.parent/ref['file']
        if path.is_symlink() or file_sha256(path) != ref['sha256']:raise ProviderError('Writing portion was modified')
        value=json.loads(path.read_text());raw=value['envelope']
        if parts and raw.get('conversation_id') != json.loads((receipt.parent/refs[0]['file']).read_text())['envelope'].get('conversation_id'):raise ProviderError('Writing portions changed conversation')
        if raw.get('status') != 'SUCCESS' or _normalize_transport_notes(_decode_agy(raw),saved['schema']) != value['part']:
            raise ProviderError('Writing portion lacks an intact SUCCESS response')
        parts.append(value['part'])
    selected=saved.get('lessons')
    req=ProviderRequest(saved['lecture_id'],{}, {}, RequestedModel('agy','','','standard'),
                        source_pack={'evidence':[{'id':i} for i in saved['source_ids']]},
                        batch_context={'lessons':selected} if selected else None)
    if not parts[-1]['complete'] or fingerprint(assemble(req,parts)) != saved['assembled_sha256']:
        raise ProviderError('Writing portions do not form the complete recorded result')
    results=[event.get('result') for event in (json.loads(line) for line in record['stdout'].splitlines() if line.strip()) if event.get('event')=='result']
    if not results or any(not isinstance(r,dict) or r.get('status') != 'SUCCESS' for r in results):
        raise ProviderError('Writing stream includes a failed terminal response')
    if _normalize_transport_notes(_decode_agy(results[-1]),saved['schema']) != parts[-1]:
        raise ProviderError('Final writing portion differs from its terminal result')
    return assemble(req,parts)


def generate_parts(request: ProviderRequest) -> ProviderResponse:
    try:
        return _generate_parts(request)
    except (OSError,ValueError,KeyError,TypeError) as error:
        raise ProviderError('Invalid writing checkpoint or local input; no automatic paid retry: '+str(error)[:300]) from error
