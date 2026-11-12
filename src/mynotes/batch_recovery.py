"""Read-only batch reporting, compatible transport reuse, and bounded repair."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import asdict, replace
import json
import shutil
from pathlib import Path
import time

from .providers import ProviderError, ProviderResponse, invoke_provider
from .utils import atomic_json_write, file_sha256
from .workflow import WorkflowError, _load_generation_phase


def intent(plan, lectures, continuity, *, reviewed=False):
    ownership={k:deepcopy(v) for k,v in plan['ownership'].items()
               if any(lesson in plan['inventory'][k]['occurrences'] for lesson in lectures)}
    if not reviewed:
        for choice in ownership.values(): choice.pop('structural_review',None)
    return {'lectures':lectures,'sources':{l:plan['sources'][l] for l in lectures},'ownership':ownership,
            'requested':plan['requested_model'],'style':plan['style'],'course':plan['course'],
            'budgets':plan['budgets'],'contract':plan['contract'],'generation_contract':plan['generation_contract'],
            'prompt_contract':plan['prompt_contract'],'continuity':continuity,
            **({'autonomous_organization':True} if plan.get('autonomous_organization') else {}),
            **({'risk_policy_version':plan['risk_policy_version']} if plan.get('risk_policy_version') else {}),
            **({'context_sources':plan['context_sources']} if plan.get('context_sources') else {})}


def equivalent_intent(old,plan,lectures,continuity):
    left=intent(old,lectures,continuity);right=intent(plan,lectures,continuity)
    left.pop('prompt_contract');right.pop('prompt_contract')
    return left==right


def prompt_matches(directory,old,plan,lectures,continuity):
    """A module-only update is compatible only if its ACTUAL writing prompt matches.

    Legacy manifests hash the entire provider module, including unrelated logs.
    Its incremental identity also binds the exact prompt/schema/source context.
    Use that second proof, never blindly ignore the historical contract hash.
    """
    if old['prompt_contract']==plan['prompt_contract']:return True
    from .batch import _request
    from .providers import RequestedModel,_prompt,_context
    from .agy_parts import fingerprint,part_schema,_writing_identity
    runtime={**old,'packs':plan['packs'],'inventory':plan['inventory'],'requested':RequestedModel(**old['requested_model'])}
    request,_=_request(runtime,lectures,directory,continuity)
    semantic_identity = _writing_identity(request, part_schema(request))
    semantic_state = directory/'writing-parts'/semantic_identity/'state.json'
    if semantic_state.is_file() and json.loads(semantic_state.read_text()).get('fingerprint') == semantic_identity:
        return True
    context=_context(request);lines=context.splitlines();current=json.loads(lines[1])
    def canonical_configuration(value):
        value=deepcopy(value)
        batch=value.get('coordinated_batch',{})
        if 'ownership' in batch:batch['ownership']=sorted(batch['ownership'],key=lambda r:r['canonical_slide_id'])
        return value
    # The old manifest sorts dictionary keys; the original staged configuration
    # preserves request ordering. Prove semantic equality, then reproduce its
    # exact serialization rather than weakening the saved prompt fingerprint.
    configurations=[None,*[json.loads(path.read_text()) for path in directory.glob('request-inputs/*/configuration.json')]]
    for attempt in (directory/'provider-attempts').glob('*.json'):
        record=json.loads(attempt.read_text());command=record.get('command',[])
        if '--print' not in command:continue
        index=command.index('--print')
        if index+1>=len(command):continue
        prompt_lines=command[index+1].splitlines()
        for position,line in enumerate(prompt_lines[:-1]):
            if line==lines[0]:
                try:configurations.append(json.loads(prompt_lines[position+1]))
                except ValueError:pass
    for saved in configurations:
        candidate=context
        if saved is not None:
            if canonical_configuration(saved)!=canonical_configuration(current):continue
            candidate='\n'.join([lines[0],json.dumps(saved,ensure_ascii=False,separators=(',',':')),*lines[2:]])
        identity=fingerprint({'protocol':1,'prompt':_prompt(request,context=candidate),'source_pack':request.source_pack,
                              'requested':asdict(request.requested),'schema':part_schema(request)})
        state=directory/'writing-parts'/identity/'state.json'
        if state.is_file() and json.loads(state.read_text()).get('fingerprint')==identity:return True
        # Single-turn Agy attempts have no portions fingerprint. Prove the exact
        # original command prompt and complete staged input content instead.
        from .providers import _stage_agy_context
        staged = _stage_agy_context(request, directory, write=False, context=candidate)
        if staged != candidate:
            files = json.loads(staged.splitlines()[-1])
            try:
                configuration = json.loads(Path(files[0]['path']).read_text())
                metadata = json.loads(Path(files[1]['path']).read_text())
                rows = [json.loads(line) for item in files[2:] for line in Path(item['path']).read_text().splitlines() if line.strip()]
                expected = json.loads(candidate.splitlines()[-1]); evidence = expected.pop('evidence')
                if configuration != json.loads(candidate.splitlines()[1]) or metadata != expected or rows != evidence:
                    continue
                if any(Path(item['path']).is_symlink() or not Path(item['path']).resolve().is_relative_to(directory.resolve())
                       or len(Path(item['path']).read_bytes()) != item['bytes'] or len(Path(item['path']).read_text().splitlines()) != item['lines'] for item in files):
                    continue
            except (OSError,ValueError,KeyError,IndexError):
                continue
        expected_prompt = _prompt(request, context=staged)
        for attempt in (directory/'provider-attempts').glob('*.json'):
            record=json.loads(attempt.read_text()); command=record.get('command',[])
            if (record.get('requested')!=asdict(request.requested) or record.get('error') or record.get('returncode')!=0
                    or record.get('phase')!='writing' or '--print' not in command):continue
            index=command.index('--print')
            if index+1<len(command) and command[index+1]==expected_prompt:
                return True
    return False


def compatible_response(root, plan, lectures, continuity):
    from .batch import _hash, _read
    expected=intent(plan,lectures,continuity)
    filename='returned-writing-'+_hash(continuity)[:12]+'.json'
    for manifest in sorted(root.glob('*/manifest.json')):
        old=_read(manifest)
        # Inventories are reconstructed from unchanged current packs, not guesses.
        old={**old,'inventory':plan['inventory']}
        try: same = equivalent_intent(old,plan,lectures,continuity)
        except KeyError: continue
        if not same: continue
        directory=root/'generation-checkpoints'/_hash(intent(old,lectures,continuity,reviewed=True))
        path=directory/filename
        if not prompt_matches(directory,old,plan,lectures,continuity):continue
        if not path.is_file():
            from .batch import _request
            from .providers import RequestedModel
            from .local_recovery import recover_parts
            request,_=_request({**old,'packs':plan['packs'],'inventory':plan['inventory'],'requested':RequestedModel(**old['requested_model'])},lectures,directory,continuity)
            response,receipt=recover_parts(request,directory,apply=True)
            if response is not None:
                return response, {'manifest':str(manifest),'manifest_sha256':file_sha256(manifest),
                                  'returned':response.attempt_path,'returned_sha256':file_sha256(Path(response.attempt_path)),
                                  'reason':'Complete original SUCCESS stream with locally canonicalized references'}
            continue
        response=_load_generation_phase(path,previous=None)
        if response is None or not response.attempt_path:continue
        if not Path(response.attempt_path).is_absolute() or not Path(response.attempt_path).is_file():continue
        record=json.loads(Path(response.attempt_path).read_text())
        if record.get('transport') != 'agy_local_recovery_v1' and (record.get('error') or record.get('returncode')!=0):continue
        from .batch import _request
        from .providers import RequestedModel
        runtime={**old,'packs':plan['packs'],'inventory':plan['inventory'],'requested':RequestedModel(**old['requested_model'])}
        request,_=_request(runtime,lectures,directory,continuity)
        persisted_notes=response.notes
        response=certify_response(response,request,root)
        if record.get('requested')!=plan['requested_model']:continue
        return response, {'manifest':str(manifest),'manifest_sha256':file_sha256(manifest),
                          'returned':str(path),'returned_sha256':file_sha256(path),'restored_from_success_receipt':persisted_notes!=response.notes,
                          'reason':'same generation intent; only reviewed structural classifications may differ'}
    return None,None


def _historical_continuity(root, manifest, lectures):
    """Reconstruct the original chain from hash-bound completed candidates."""
    from .batch import _read, _hash, _continuity
    prior = deepcopy(manifest.get('prior_continuity', []))
    for index, group in enumerate(manifest['groups']):
        if group == lectures:
            return prior
        complete = root/manifest['fingerprint']/f'group-{index:03d}'/'complete.json'
        if not complete.is_file(): return None
        for saved in _read(complete)['candidates']:
            lesson = saved['lecture_id']
            paths = [root.parent.parent/lesson/'lecture_notes.candidate.json', *root.glob(f'*/previous-candidates/{lesson}-*.json')]
            payload = None
            for path in paths:
                if not path.is_file(): continue
                row = json.loads(path.read_text())
                row['published_to_word'] = False; row.pop('published_path', None); row.pop('published_hash', None)
                digest = __import__('hashlib').sha256((json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True)+'\n').encode()).hexdigest()
                if digest == saved['sha256']:
                    payload = row; break
            if payload is None: return None
            notes = payload['notes']; summary = _continuity(lesson, notes)
            if manifest.get('contract', 1) == 1:
                summary = {'lecture_id': lesson,
                    'topics': [b['text'][:100] for b in notes['blocks'] if b['kind']=='heading'][:30],
                    'recap': next((b['text'][:1600] for b in reversed(notes['blocks']) if b['kind']=='recap'), ''),
                    'narrative_fingerprints': summary['narrative_fingerprints']}
            prior.append(summary)
    return None


def revised_response(root, plan, lectures, continuity, *, preserve_manifest=True):
    """Revalidate a verified original under a new policy, never claim intent equality.

    Source snapshots, output lessons, model and style remain exact. Organization,
    continuity and validation policy may change. Raw transport and its original
    manifest stay immutable and are explicitly bound into the new candidate.
    """
    if not plan.get('organization_revision'):
        return None, None
    from .batch import _read, _request
    from .providers import RequestedModel
    current, current_maps = _request(plan, lectures, root, continuity)
    for manifest in sorted(root.glob('*/manifest.json')):
        old = _read(manifest)
        if lectures not in old.get('groups', []):
            continue
        if any(old.get(key) != plan.get(key) for key in ('requested_model', 'style', 'course')):
            continue
        if old.get('context_sources', {}) != plan.get('context_sources', {}) or any(old.get('sources', {}).get(lesson) != plan['sources'].get(lesson) for lesson in lectures):
            continue
        runtime = {**old, 'packs': plan['packs'], 'inventory': plan['inventory'],
                   'requested': RequestedModel(**old['requested_model'])}
        _, old_maps = _request(runtime, lectures, root, [])
        if old_maps != current_maps:
            continue
        # Checkpoints are enumerated, then matched by their immutable lesson
        # array and source-bound manifest. No prompt/hash is rewritten.
        for path in sorted((root/'generation-checkpoints').glob('*/returned-writing-*.json')):
            response = _load_generation_phase(path, previous=None)
            if response is None or [row.get('lecture_id') for row in response.notes.get('lessons', [])] != lectures:
                continue
            attempt = Path(response.attempt_path or '')
            if not attempt.is_absolute() or not attempt.resolve().is_relative_to(root.resolve()) or not attempt.is_file():
                continue
            # Bind the retained checkpoint to this manifest's generation intent.
            from .batch import _hash
            historical = _historical_continuity(root, old, lectures)
            saved_continuities = [historical] if historical is not None else []
            if not any(path.parent.name == _hash(intent(runtime, lectures, prior, reviewed=True)) for prior in saved_continuities):
                continue
            record = json.loads(attempt.read_text())
            if record.get('requested') != plan['requested_model']:
                continue
            response = certify_response(response, current, root)
            retained = manifest
            if preserve_manifest:
                retained = root/'revisions'/plan['fingerprint']/'original-manifests'/manifest.parent.name/'manifest.json'
                retained.parent.mkdir(parents=True, exist_ok=True)
                if not retained.exists(): retained.write_bytes(manifest.read_bytes())
                if file_sha256(retained) != file_sha256(manifest):
                    raise WorkflowError('Original manifest snapshot changed; no automatic writing retry')
            return response, {'kind': 'organization_revalidation', 'manifest': str(retained),
                'manifest_sha256': file_sha256(retained), 'returned': str(path), 'returned_sha256': file_sha256(path),
                'original_attempt': str(attempt), 'original_attempt_sha256': file_sha256(attempt),
                'reason': 'Original successful writing revalidated under new organization, not an equivalent writing request.'}
    return None, None


def certify_response(response,request,root):
    if not response.attempt_path or not Path(response.attempt_path).is_absolute():return response
    record=json.loads(Path(response.attempt_path).read_text())
    if record.get('transport') == 'agy_local_recovery_v1':
        from .local_recovery import verify_local_receipt
        raw=verify_local_receipt(Path(response.attempt_path),root)
        if response.notes != raw: raise WorkflowError('Recovered result changed')
        return response
    if record.get('error') or record.get('returncode')!=0:raise WorkflowError('Saved response came from a failed provider transport')
    if record.get('envelope',{}).get('transport')=='agy_progressive_v2':
        from .agy_progressive import verify_receipt
        raw=verify_receipt(record,root)
    elif record.get('envelope',{}).get('transport')=='agy_parts_v1':
        from .agy_parts import verify_receipt
        raw=verify_receipt(record,root)
    elif record.get('requested',{}).get('provider')=='agy':
        from .providers import _decode_agy, _normalize_transport_notes
        if record.get('requested') != asdict(request.requested):
            raise WorkflowError('Saved writing requested settings changed')
        envelope=json.loads(record['stdout'])
        if envelope.get('status')!='SUCCESS':raise WorkflowError('Saved writing transport is not SUCCESS')
        raw=_normalize_transport_notes(_decode_agy(envelope),request.response_schema,provider='agy')
    else:
        return response
    if raw==response.notes:return response
    owners={r['evidence_id']:r['owner'] for r in request.batch_context['ownership']}
    safe=True
    for before,after in zip(raw['lessons'],response.notes.get('lessons',[])):
        a=deepcopy(before['notes']);b=deepcopy(after.get('notes',{}))
        cited={id for block in a['blocks'] for id in block['evidence_ids']}
        for field in ('evidence_coverage','visual_decisions'):
            original={v['evidence_id']:v for v in a.pop(field,[])};edited={v['evidence_id']:v for v in b.pop(field,[])}
            safe &= all(edited.get(k)==v for k,v in original.items())
            for k,v in edited.items():
                if k in original:continue
                safe &= owners.get(k) is not None and owners[k]!=before['lecture_id'] and k not in cited
                safe &= (v.get('disposition')=='excluded' if field=='evidence_coverage' else v.get('decision')=='exclude' and v.get('inspected') is False)
        safe &= a==b and before['lecture_id']==after.get('lecture_id')
    safe &= len(raw['lessons'])==len(response.notes.get('lessons',[]))
    return response if safe else replace(response,notes=raw)


def historical_active_seconds(root,plan,lectures,continuity):
    """Legacy private state has cumulative active time, not additive event usage."""
    from .batch import _hash,_read
    directories=set()
    for manifest in root.glob('*/manifest.json'):
        old={**_read(manifest),'inventory':plan['inventory']}
        try: same = equivalent_intent(old,plan,lectures,continuity)
        except KeyError: continue
        if same:
            directory=root/'generation-checkpoints'/_hash(intent(old,lectures,continuity,reviewed=True))
            if prompt_matches(directory,old,plan,lectures,continuity):directories.add(directory)
    total=0.
    for directory in directories:
        values=[]
        for path in directory.glob('writing-parts/*/state.json'):
            state=json.loads(path.read_text());value=state.get('used_seconds',0)
            if type(value) not in (int,float) or value<0:raise WorkflowError('Invalid historical active budget')
            values.append(value)
        total+=max(values,default=0.)
    return total


def bound_budget_checkpoints(root, plan, lectures):
    """Bind historical clocks to immutable manifests, including older organization."""
    from .batch import _hash, _read
    directories = set()
    for path in root.glob('*/manifest.json'):
        old = _read(path)
        for group in old.get('groups', []):
            if not set(group) & set(lectures):
                continue
            prior = _historical_continuity(root, old, group)
            if prior is None:
                continue  # A legacy aggregate without receipts will fail closed.
            try:
                runtime = {**old, 'inventory': plan['inventory']}
                directory = root/'generation-checkpoints'/_hash(intent(runtime, group, prior, reviewed=True))
            except KeyError:
                continue
            if directory.is_dir():
                directories.add(directory)
    return sorted(directories)


def validate_review_defects(review, response, lectures, maps):
    """Accept an actual review only against the exact retained response.

    These are coordinator observations, never an automatic semantic verdict.
    Paths and citations must identify existing blocks of the frozen result.
    """
    from .batch import _hash
    import re
    if (not isinstance(review, dict) or set(review) != {'base_notes_sha256', 'defects'}
            or review['base_notes_sha256'] != _hash(response.notes)
            or not isinstance(review['defects'], list) or not review['defects']):
        raise WorkflowError('Reviewed defects need the exact retained response hash and a nonempty defects array')
    rows = {r['lecture_id']: r['notes'] for r in response.notes['lessons']}
    seen = set()
    for defect in review['defects']:
        if (not isinstance(defect, dict) or set(defect) != {'lecture_id', 'path', 'evidence_id', 'reason'}
                or defect['lecture_id'] not in lectures or not isinstance(defect['path'], str)
                or not isinstance(defect['reason'], str) or not defect['reason'].strip()):
            raise WorkflowError('A reviewed defect needs a selected lesson, block path, evidence ID and reason')
        match = re.fullmatch(r'blocks\[(\d+)\]', defect['path'])
        blocks = rows[defect['lecture_id']]['blocks']
        if not match or int(match[1]) >= len(blocks):
            raise WorkflowError('Reviewed block path is unavailable')
        identifier = maps[defect['lecture_id']].get(defect['evidence_id'])
        if identifier is None or identifier not in blocks[int(match[1])].get('evidence_ids', []):
            raise WorkflowError('Reviewed defect is not linked to its actual source block')
        key = (defect['lecture_id'], defect['path'], identifier)
        if key in seen:
            raise WorkflowError('Duplicate reviewed defect')
        seen.add(key)
    return deepcopy(review['defects'])


def _correction_contract(request, response, defects, maps):
    from .batch import _hash
    base_hash=_hash(response.notes)
    allowed={maps[d['lecture_id']][d['evidence_id']] for d in defects}
    task={'defects':[{**d,'evidence_id':maps[d['lecture_id']][d['evidence_id']]} for d in defects],
          'anchors':[{'lecture_id':row['lecture_id'],'block_sha256':_hash(b),'block':b}
                     for row in response.notes['lessons'] for b in row['notes']['blocks']], 'base_notes_sha256':base_hash}
    editable = set()
    for defect in task['defects']:
        row = next(row for row in response.notes['lessons'] if row['lecture_id'] == defect['lecture_id'])
        import re
        match = re.fullmatch(r'blocks\[(\d+)\]', defect.get('path', ''))
        if match:
            editable.add((defect['lecture_id'], _hash(row['notes']['blocks'][int(match[1])])))
    task['editable_blocks'] = [{'lecture_id': l, 'block_sha256': digest} for l, digest in sorted(editable)]
    task['lesson_evidence'] = request.batch_context['lesson_evidence']
    block={'type':'object','additionalProperties':False,'properties':{
        'kind':{'type':'string','enum':['paragraph','visual']},'text':{'type':'string'},
        'visual_id':{'type':'string','enum':sorted(allowed)},
        'evidence_ids':{'type':'array','items':{'type':'string','enum':sorted(allowed)}},
        'coverage_role':{'type':'string','enum':['full','supplement']}},
        'required':['kind','text','evidence_ids','coverage_role']}
    schema={'type':'object','additionalProperties':False,'properties':{'insertions':{'type':'array','maxItems':len(defects),
        'items':{'type':'object','additionalProperties':False,'properties':{'lecture_id':{'type':'string'},'after_block_sha256':{'type':'string'},'block':block},
                 'required':['lecture_id','after_block_sha256','block']}}},'required':['insertions']}
    schema['properties']['attachments']={'type':'array','maxItems':len(defects),'items':{'type':'object','additionalProperties':False,'properties':{'lecture_id':{'type':'string'},'block_sha256':{'type':'string'},'evidence_ids':{'type':'array','items':{'type':'string','enum':sorted(allowed)}}},'required':['lecture_id','block_sha256','evidence_ids']}}
    schema['properties']['visual_updates']={'type':'array','maxItems':len(defects),'items':{'type':'object','additionalProperties':False,'properties':{'evidence_id':{'type':'string','enum':sorted(allowed)},'decision':{'type':'string','enum':['include','exclude']},'reason':{'type':'string'},'inspected':{'type':'boolean'}},'required':['evidence_id','decision','reason','inspected']}}
    schema['required']+=['attachments','visual_updates']
    from .providers import notes_schema
    replacement_block = deepcopy(notes_schema(compact=True)['properties']['blocks']['items'])
    replacement_block['properties']['coverage_role'] = {'type': 'string', 'enum': ['full', 'supplement', 'reference', 'new_insight']}
    replacement_block['properties']['prior_lecture_id'] = {'type': 'string'}
    schema['properties']['replacements'] = {'type': 'array', 'maxItems': len(editable), 'items': {'type': 'object', 'additionalProperties': False,
        'properties': {'lecture_id': {'type': 'string'}, 'block_sha256': {'type': 'string'}, 'block': replacement_block}, 'required': ['lecture_id', 'block_sha256', 'block']}}
    schema['properties']['moves'] = {'type': 'array', 'maxItems': len(editable), 'items': {'type': 'object', 'additionalProperties': False,
        'properties': {'lecture_id': {'type': 'string'}, 'block_sha256': {'type': 'string'}, 'to_lecture_id': {'type': 'string'}, 'after_block_sha256': {'type': 'string'}},
        'required': ['lecture_id', 'block_sha256', 'to_lecture_id', 'after_block_sha256']}}
    schema['properties']['deletions'] = {'type': 'array', 'maxItems': len(editable), 'items': {'type': 'object', 'additionalProperties': False,
        'properties': {'lecture_id': {'type': 'string'}, 'block_sha256': {'type': 'string'}}, 'required': ['lecture_id', 'block_sha256']}}
    if editable:
        # Replacements need the existing passage's cited evidence too; coverage
        # repair still receives all original anchors and only staged sources.
        for row in response.notes['lessons']:
            for b in row['notes']['blocks']:
                if (row['lecture_id'], _hash(b)) in editable: allowed.update(b['evidence_ids'])
    return task, schema, allowed


def _saved_correction(saved, request, task, schema, directory):
    """Certify retained patch from the immutable SUCCESS transport, no AI call."""
    from .providers import _decode_agy, _transport_normalization
    if saved.get('base_notes_sha256') != task['base_notes_sha256'] or saved.get('task') != task:
        raise WorkflowError('Saved targeted correction differs from its source task; no paid retry')
    patch = ProviderResponse(**saved['response'])
    if patch.provider != request.requested.provider:
        raise WorkflowError('Targeted correction provider changed')
    if patch.provider != 'agy':
        return patch, None
    attempt = Path(patch.attempt_path or '')
    if not attempt.is_absolute() or attempt.is_symlink() or not attempt.resolve().is_relative_to(directory.resolve()):
        raise WorkflowError('Targeted correction attempt is outside its private checkpoint')
    record = json.loads(attempt.read_text())
    if record.get('error') or record.get('returncode') != 0 or record.get('requested') != asdict(request.requested):
        raise WorkflowError('Targeted correction lacks a successful matching provider attempt')
    envelope = json.loads(record['stdout'])
    if envelope.get('status') != 'SUCCESS':
        raise WorkflowError('Targeted correction transport is not SUCCESS')
    raw = _decode_agy(envelope)
    normalized, receipt = _transport_normalization(raw, schema, provider='agy')
    saved_normalized, _ = _transport_normalization(patch.notes, schema, provider='agy')
    if saved_normalized != normalized:
        raise WorkflowError('Targeted correction differs from its SUCCESS transport')
    receipt = {**receipt, 'attempt':str(attempt), 'attempt_sha256':file_sha256(attempt), 'schema':schema}
    return replace(patch, notes=normalized), receipt


def retained_correction(request, response, defects, maps):
    """Read-only diagnosis, bound to exactly the task normal resume would use."""
    directory = request.run_dir/'coverage-repair'
    path = directory/'repair.json'
    if not path.is_file():
        return None
    task, schema, _ = _correction_contract(request, response, defects, maps)
    patch, receipt = _saved_correction(json.loads(path.read_text()), request, task, schema, directory)
    return replace(response, notes=apply_correction(response.notes, patch.notes, task)), receipt


def repair_coverage(request,response,defects,maps,adapter,timeout,*,retained_from=None):
    """One hash-anchored correction of explicit defects, including replacements."""
    from .batch import _hash, _stage_visuals
    # A compatible response can bypass the new-writing branch that normally
    # stages images. Repair still needs verified pixels at the new paths.
    _stage_visuals(request)
    directory=request.run_dir/'coverage-repair';directory.mkdir(parents=True,exist_ok=True)
    path=directory/'repair.json'
    proof_directory = directory
    if not path.exists() and retained_from is not None:
        retained_from = Path(retained_from)
        candidate = retained_from/'coverage-repair/repair.json'
        if not candidate.exists() and (retained_from/'coverage-repair/started.json').exists():
            raise WorkflowError('Targeted correction already attempted in the retained checkpoint; no paid retry after an update')
        if candidate.is_file():
            if (retained_from.is_symlink() or not retained_from.resolve().is_relative_to(request.run_dir.parent.resolve())):
                raise WorkflowError('Retained correction is outside its source-bound batch checkpoints')
            path = candidate
            proof_directory = candidate.parent
    task,schema,allowed=_correction_contract(request,response,defects,maps)
    base_hash=task["base_notes_sha256"]
    visual_ids = {maps[d['lecture_id']][d['evidence_id']] for d in defects}
    selected={'lecture_id':request.lecture_id,'course':request.source_pack.get('course',{}),
              'evidence':[e for e in request.source_pack['evidence'] if e['id'] in allowed],
              'visuals':[deepcopy(v) for v in request.source_pack.get('visuals',[]) if v.get('evidence_id') in visual_ids]}
    for visual in selected['visuals']:
        image=Path(visual['image_path']);target=directory/'visuals'/(file_sha256(image)+image.suffix)
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(image,target);visual['image_path']=str(target)
    repair_request=replace(request,phase='coverage_repair',source_pack=selected,batch_context={'repair_task':task},
                           response_schema=schema,run_dir=directory,timeout_seconds=timeout)
    if path.exists():
        saved=json.loads(path.read_text())
        patch,normalization=_saved_correction(saved,repair_request,task,schema,proof_directory)
    else:
        if (directory/'started.json').exists():raise WorkflowError('Targeted correction already attempted; inspect its retained attempt, no paid retry')
        revision = request.batch_context.get('revision_id')
        guards = []
        if revision:
            guard_root = request.run_dir.parent.parent/'correction-allowances'/revision
            for lesson in {d['lecture_id'] for d in defects}:
                guard = guard_root/(lesson+'.json')
                if guard.exists(): raise WorkflowError('This lesson already used its automatic correction for this revision; saved work retained')
                guards.append(guard)
            for guard in guards:
                atomic_json_write(guard, {'base_notes_sha256': base_hash, 'checkpoint': str(directory)})
        atomic_json_write(directory/'started.json',{'base_notes_sha256':base_hash,'task':task})
        patch=invoke_provider(repair_request,adapter)
        atomic_json_write(path,{'base_notes_sha256':base_hash,'task':task,'response':asdict(patch)})
        patch,normalization=_saved_correction(json.loads(path.read_text()),repair_request,task,schema,directory)
    notes=apply_correction(response.notes,patch.notes,task)
    lineage={'base_attempt':response.attempt_path,'base_notes_sha256':base_hash,'repair':str(path),'repair_sha256':file_sha256(path),
             'repair_attempt':patch.attempt_path,'repair_usage':patch.usage,'effective_notes_sha256':_hash(notes),'operations':patch.notes}
    if normalization is not None:lineage['transport_normalization']=normalization
    atomic_json_write(directory/'lineage.json',lineage)
    return replace(response,notes=notes),lineage


def apply_correction(original,patch,task):
    from .batch import _hash,NARRATIVE_KINDS
    notes=deepcopy(original);rows={r['lecture_id']:r['notes'] for r in notes['lessons']};seen=set()
    allowed={d['evidence_id'] for d in task['defects']}
    if set(patch)-{'insertions','attachments','visual_updates','replacements','moves','deletions'}:raise WorkflowError('Unexpected targeted correction operation')
    by_lesson={l:{d['evidence_id'] for d in task['defects'] if d['lecture_id']==l} for l in rows}
    anchors={(l,_hash(b)):b for l,row in rows.items() for b in row['blocks']}
    insertions=patch.get('insertions',[]);attachments=patch.get('attachments',[]);updates=patch.get('visual_updates',[])
    replacements=patch.get('replacements',[]);moves=patch.get('moves',[]);deletions=patch.get('deletions',[])
    editable={(r['lecture_id'],r['block_sha256']) for r in task.get('editable_blocks',[])}
    if (not all(isinstance(v,list) for v in (insertions,attachments,updates,replacements,moves,deletions))
            or not 1<=len(insertions)+len(attachments)+len(replacements)+len(moves)+len(deletions)<=len(allowed)+len(editable) or len(updates)>len(allowed)):
        raise WorkflowError('Invalid targeted correction')
    modified=set()
    for op in replacements+moves+deletions:
        key=(op.get('lecture_id'),op.get('block_sha256'));anchor=anchors.get(key)
        if key not in editable or anchor is None or key in modified:
            raise WorkflowError('Correction may only change hash-anchored defective blocks')
        modified.add(key);lecture=key[0];blocks=rows[lecture]['blocks']
        index=next(i for i,b in enumerate(blocks) if b is anchor)
        if op in replacements:
            if set(op)!={'lecture_id','block_sha256','block'}:raise WorkflowError('Invalid targeted replacement')
            block=op['block'];ids=block.get('evidence_ids',[])
            if not ids or set(ids)-set(task.get('lesson_evidence',{}).get(lecture,[])):
                raise WorkflowError('Replacement cites evidence outside its lesson context')
            blocks[index]=deepcopy(block)
            anchors[key]=blocks[index]
        elif op in moves:
            if set(op)!={'lecture_id','block_sha256','to_lecture_id','after_block_sha256'}:raise WorkflowError('Invalid targeted move')
            dest=op['to_lecture_id'];target=anchors.get((dest,op['after_block_sha256']))
            if dest not in rows or dest==lecture or target is None or set(anchor['evidence_ids'])-set(task.get('lesson_evidence',{}).get(dest,[])):
                raise WorkflowError('Move needs a verified destination and evidence visible in both lessons')
            del blocks[index];destination=rows[dest]['blocks'];position=next(i for i,b in enumerate(destination) if b is target)
            destination.insert(position+1,deepcopy(anchor))
        else:
            if set(op)!={'lecture_id','block_sha256'}:raise WorkflowError('Invalid targeted deletion')
            del blocks[index]
        seen.update(set(anchor['evidence_ids'])&allowed)
    for op in insertions:
        if not isinstance(op,dict) or set(op)!={'lecture_id','after_block_sha256','block'} or op['lecture_id'] not in rows:raise WorkflowError('Correction may only insert into the affected lesson')
        l=op['lecture_id'];b=op['block'];ids=b.get('evidence_ids',[]);anchor=anchors.get((l,op['after_block_sha256']))
        visual = b.get('kind') == 'visual'
        if (b.get('kind') not in {'paragraph','visual'} or not str(b.get('text','')).strip()
                or not ids or set(ids)-by_lesson[l] or (set(ids)&seen and not visual)
                or b.get('coverage_role') not in {'full','supplement'}
                or (visual and (len(ids) != 1 or b.get('visual_id') != ids[0]
                    or any(v.get('kind') == 'visual' and v.get('visual_id') == ids[0] for v in rows[l]['blocks'])))):
            raise WorkflowError('Correction changed its allowed coverage scope')
        if anchor is None or anchor['kind'] not in NARRATIVE_KINDS | {'heading'}:
            raise WorkflowError('Targeted correction anchor changed')
        blocks=rows[l]['blocks'];index=next((i for i,v in enumerate(blocks) if v is anchor),None)
        if index is None:raise WorkflowError('Targeted correction anchor was removed')
        blocks.insert(index+1,deepcopy(b));seen.update(ids)
    for op in attachments:
        if not isinstance(op,dict) or set(op)!={'lecture_id','block_sha256','evidence_ids'} or op['lecture_id'] not in rows:raise WorkflowError('Invalid source attachment')
        l=op['lecture_id'];ids=op['evidence_ids'];anchor=anchors.get((l,op['block_sha256']))
        if not ids or set(ids)-by_lesson[l] or set(ids)&seen or anchor is None or not any(b is anchor for b in rows[l]['blocks']) or anchor['kind'] not in NARRATIVE_KINDS or anchor.get('coverage_role') not in {'full','supplement'}:
            raise WorkflowError('Source attachment is outside the specific coverage defect')
        anchor['evidence_ids']+=list(ids);seen.update(ids)
    visual_seen=set()
    for update in updates:
        id=update.get('evidence_id')
        if (set(update)!={'evidence_id','decision','reason','inspected'} or id not in allowed or id in visual_seen
                or update['decision'] not in {'include','exclude'} or update['inspected'] is not True or not str(update['reason']).strip()):raise WorkflowError('Invalid inspected visual correction')
        visual_seen.add(id)
        row=next(rows[l] for l in rows if id in by_lesson[l]);old=next((v for v in row['visual_decisions'] if v['evidence_id']==id),None)
        if old is None:raise WorkflowError('Correction cannot create an unrelated visual decision')
        old.update(update)
        if update['decision'] == 'include' and not any(b.get('kind') == 'visual' and b.get('visual_id') == id for b in row['blocks']):
            raise WorkflowError('An included correction image needs an anchored visual block')
    if seen!=allowed:raise WorkflowError('Targeted correction did not cover every reported defect')
    for row in rows.values():
        for coverage in row['evidence_coverage']:
            if coverage['evidence_id'] in seen:coverage.update(disposition='used',reason='Explained or source-linked by the retained targeted correction')
    return notes


def _candidate_completion_digest(path):
    """Publication markers are mutable; the certified academic payload is not."""
    value=json.loads(Path(path).read_text())
    value['published_to_word']=False
    value.pop('published_path',None);value.pop('published_hash',None)
    encoded=(json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode()
    return __import__('hashlib').sha256(encoded).hexdigest()


def _notes_request_status(root):
    from .batch import _read
    import os
    path=root/'notes-run.json'
    if not path.is_file():return None
    value=_read(path)
    if value.get('status') in {'GENERATING','BUILDING_WORD','UPDATING_WORD','LOCAL_WORD_RETRY','RECOVERING_LOCALLY'}:
        try:os.kill(value['pid'],0)
        except (ProcessLookupError,KeyError):value={**value,'reported_status':value.get('status'),'status':'INTERRUPTED'}
        except PermissionError:value={**value,'reported_status':value.get('status'),'status':'STATUS_UNAVAILABLE','liveness':'permission_denied'}
    def intact(row):
        path=Path(row.get('path',''))
        return path.is_file() and file_sha256(path)==row.get('sha256')
    files=value.get('delivery',{}).get('files',[])
    publication=value.get('publication',{});target=Path(publication.get('output',''))
    value['delivery_files_valid']=bool(files) and all(intact(row) for row in files)
    value['publication_file_valid']=target.is_file() and file_sha256(target)==publication.get('output_hash')
    if value.get('status') in {'COMPLETED','COMPLETED_WITH_WARNINGS'} and not (value['delivery_files_valid'] and value['publication_file_valid']):
        value={**value,'reported_status':value['status'],'status':'DELIVERY_CHANGED','problem':{
            'kind':'DELIVERY_CHANGED','message':'A delivered document is missing or changed since completion; no generation was started.'}}
    return value


def batch_status(workspace,batch_id):
    from .batch import load_batch_manifest,_read
    manifest=load_batch_manifest(workspace,batch_id);root=workspace.runs_dir/'_batches'/batch_id
    lessons=[]
    for lecture in manifest['lectures']:
        entry=next((c for c in manifest.get('candidates',[]) if c['lecture_id']==lecture),None)
        valid=False;word=None
        if entry:
            candidate=Path(entry['candidate'])
            group=next(i for i,g in enumerate(manifest['groups']) if lecture in g)
            complete=root/manifest['fingerprint']/f'group-{group:03d}'/'complete.json'
            if candidate.is_file() and complete.is_file():
                saved=_read(complete);valid=any(c['lecture_id']==lecture and c['sha256'] in {file_sha256(candidate),_candidate_completion_digest(candidate)} for c in saved['candidates'])
            word_entry=manifest.get('word_candidates',{}).get(lecture)
            if valid and word_entry and Path(word_entry['path']).is_file() and file_sha256(Path(word_entry['path']))==word_entry['sha256']:word=word_entry['path']
        lessons.append({'lecture_id':lecture,'content_validated':valid,'word_candidate':word,
                        'status':'WORD_READY' if word else 'CONTENT_READY' if valid else 'PENDING'})
    activity=manifest.get('activity',{})
    retries=manifest.get('network_retries',0)
    if activity:
        import os
        try:os.kill(activity['pid'],0)
        except (ProcessLookupError,KeyError):activity={**activity,'status':'STOPPED'}
        except PermissionError:activity={**activity,'status':'UNKNOWN','reported_status':activity.get('status'),'liveness':'permission_denied'}
        directory=Path(activity.get('checkpoint',''))
        if directory.is_absolute() and directory.resolve().is_relative_to(root.resolve()):
            progressive=directory/'progressive-v2/state.json'
            if progressive.is_file():
                value=json.loads(progressive.read_text())
                activity={**activity,'progressive_status':value.get('status'),'accepted_sections':len(value.get('units',[])),
                    **{key:value[key] for key in ('written_sections','verified_sections','waiting_sections','deliverable') if key in value},
                    'current_unit':value.get('current_unit'),'last_step':value.get('last_step'),
                    'last_activity':value.get('last_activity'),'progressive_problem':value.get('problem')}
            states=list(directory.glob('writing-parts/*/state.json'))
            if states:
                state=json.loads(max(states,key=lambda p:p.stat().st_mtime).read_text())
                if not any(e.get('lessons')==activity.get('lessons') for e in manifest.get('shared_attempts',[])):
                    retries+=state.get('network_retries',0)
                if activity['status'] not in {'STOPPED','UNKNOWN','BUILDING_WORD'} and state.get('status') in {'RETRY_WAIT','PAUSED_SLEEP','WRITING'}:
                    activity={**activity,'status':state['status'],'active_seconds':state.get('used_seconds'),'suspended_seconds':state.get('suspended_seconds',0)}
    status='INTERRUPTED' if manifest['status']=='RUNNING' and activity.get('status')=='STOPPED' else manifest['status']
    if status=='RUNNING' and activity.get('status')=='UNKNOWN':status='STATUS_UNAVAILABLE'
    recovery=None
    if status in {'FAILED','INTERRUPTED'} and manifest.get('activity',{}).get('checkpoint'):
        try:
            from .local_recovery import recover_batch
            recovery=recover_batch(workspace,batch_id,apply=False)
        except (OSError,ValueError,RuntimeError,KeyError,TypeError) as error:
            recovery={'status':'NEEDS_REVIEW','message':str(error),'provider_invoked':False}
    for group in (recovery or {}).get('lessons', []):
        for row in lessons:
            if row['lecture_id'] in group['lessons'] and not row['content_validated']:
                row.update({k:group[k] for k in ('status','phase','defects','next_action','raw_result_preserved') if k in group})
                row['active_seconds']=group.get('active_seconds_preserved')
    for row in lessons:
        budgets=[]; phases={}; blocked=[]; has_phases=False
        for file in (root/'active-budgets').glob('*.json'):
            value=json.loads(file.read_text())
            if row['lecture_id'] in value.get('lessons',[]):
                budgets.append(sum(value['phases'].values()) if 'phases' in value else value.get('used_seconds',0))
                if 'phases' in value:
                    has_phases=True
                    for phase, seconds in value['phases'].items():
                        phases[phase]=max(phases.get(phase,0),seconds)
                    blocked.extend(value.get('model_block_reasons',[]))
        if has_phases:
            from .generation_budget import MODEL_PHASE_SECONDS, LOCAL_CHECK_SECONDS
            row['phase_budget']={'used_seconds':phases, 'phase_limit_seconds':None, 'model_timeout_policy':'soft_progress',
                'local_check_limit_seconds':LOCAL_CHECK_SECONDS, 'model_block_reasons':sorted(set(blocked)),
                'remaining_from_saved_accounting':{phase:0 if blocked and phase!='local_checks' else (None if phase!='local_checks' else max(0,limit-phases.get(phase,0)))
                    for phase,limit in [('writing',MODEL_PHASE_SECONDS),('coverage_repair',MODEL_PHASE_SECONDS),('local_checks',LOCAL_CHECK_SECONDS)]}}
        row['active_seconds']=max([v for v in [*budgets,row.get('active_seconds')] if v is not None],default=None)
    notes_request=_notes_request_status(root)
    return {'batch_id':batch_id,'status':notes_request['status'] if notes_request else status,'lessons':lessons,'activity':activity,
            'notes_request':notes_request,
            'problem':manifest.get('problem',manifest.get('error')),'network_retries':retries,
            'local_recovery':recovery,'warnings':manifest.get('warnings',[]),
            'organization_pending':manifest.get('organization_pending',[]),
            'phase':next((r['phase'] for r in lessons if r.get('phase')),None) or (manifest.get('problem') if isinstance(manifest.get('problem'),dict) else {}).get('phase') or activity.get('status'), 'published_to_word':bool(notes_request and notes_request.get('publication_file_valid') and notes_request.get('status') in {'COMPLETED','COMPLETED_WITH_WARNINGS'})}



def verify_repair_lineage(candidate,workspace_root):
    from .batch import _hash
    from .providers import _decode_agy
    ref=candidate.get('repair_lineage')
    if not ref:return
    path=Path(ref['path'])
    if not path.is_absolute() or not path.resolve().is_relative_to(workspace_root.resolve()) or file_sha256(path)!=ref['sha256']:
        raise WorkflowError('Targeted correction lineage was modified')
    line=json.loads(path.read_text())
    for key in ('base_response','repair','repair_attempt'):
        p=Path(line[key])
        if not p.is_absolute() or not p.resolve().is_relative_to(workspace_root.resolve()):raise WorkflowError('Correction provenance is outside the workspace')
    base=_load_generation_phase(Path(line['base_response']),previous=None)
    if base is None or _hash(base.notes)!=line['base_notes_sha256'] or base.attempt_path!=line['base_attempt']:
        raise WorkflowError('Targeted correction base changed')
    patch_path=Path(line['repair'])
    if file_sha256(patch_path)!=line['repair_sha256']:raise WorkflowError('Targeted correction response changed')
    patch=json.loads(patch_path.read_text())
    if patch['base_notes_sha256']!=line['base_notes_sha256']:
        raise WorkflowError('Correction base differs from its retained response')
    operations = patch['response']['notes']
    if patch['response'].get('attempt_path') != line['repair_attempt']:
        raise WorkflowError('Targeted correction attempt binding changed')
    record=json.loads(Path(line['repair_attempt']).read_text())
    if record.get('error') or record.get('returncode')!=0 or record['requested']!=candidate['requested_model']:
        raise WorkflowError('Targeted correction lacks a successful matching provider attempt')
    if record['requested']['provider']=='agy':
        envelope=json.loads(record['stdout'])
        if envelope.get('status')!='SUCCESS':
            raise WorkflowError('Targeted correction differs from its SUCCESS transport')
        normalization = line.get('transport_normalization')
        if normalization is not None:
            from .providers import ProviderRequest, RequestedModel
            request = ProviderRequest('', {}, {}, RequestedModel(**record['requested']))
            verified, proof = _saved_correction(patch, request, patch['task'], normalization['schema'], patch_path.parent)
            if proof != normalization:
                raise WorkflowError('Correction transport normalization changed')
            operations = verified.notes
        elif _decode_agy(envelope)!=operations:
            # Historical lineages retain their exact original transport contract.
            raise WorkflowError('Targeted correction differs from its SUCCESS transport')
    if operations != line['operations']:
        raise WorkflowError('Correction operations differ from their retained response')
    effective=apply_correction(base.notes,operations,patch['task'])
    if _hash(effective)!=line['effective_notes_sha256']:raise WorkflowError('Targeted correction assembly changed')
    if line['validated_notes_sha256'].get(candidate['notes'].get('lecture_id',candidate.get('lecture_id',''))) is None:
        # Current candidates identify lessons via their source pack.
        pack=json.loads(Path(candidate['source_pack']).read_text());lecture=pack['lecture_id']
    else:lecture=candidate.get('lecture_id',candidate['notes'].get('lecture_id'))
    if _hash(candidate['notes'])!=line['validated_notes_sha256'].get(lecture):raise WorkflowError('Corrected candidate changed after validation')
