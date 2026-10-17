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
            'prompt_contract':plan['prompt_contract'],'continuity':continuity}


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
    from .agy_parts import fingerprint,part_schema
    runtime={**old,'packs':plan['packs'],'inventory':plan['inventory'],'requested':RequestedModel(**old['requested_model'])}
    request,_=_request(runtime,lectures,directory,continuity)
    context=_context(request);lines=context.splitlines();current=json.loads(lines[1])
    def canonical_configuration(value):
        value=deepcopy(value)
        batch=value.get('coordinated_batch',{})
        if 'ownership' in batch:batch['ownership']=sorted(batch['ownership'],key=lambda r:r['canonical_slide_id'])
        return value
    # The old manifest sorts dictionary keys; the original staged configuration
    # preserves request ordering. Prove semantic equality, then reproduce its
    # exact serialization rather than weakening the saved prompt fingerprint.
    configurations=[None,*directory.glob('request-inputs/*/configuration.json')]
    for path in configurations:
        candidate=context
        if path is not None:
            saved=json.loads(path.read_text())
            if canonical_configuration(saved)!=canonical_configuration(current):continue
            candidate='\n'.join([lines[0],json.dumps(saved,ensure_ascii=False,separators=(',',':')),*lines[2:]])
        identity=fingerprint({'protocol':1,'prompt':_prompt(request,context=candidate),'source_pack':request.source_pack,
                              'requested':asdict(request.requested),'schema':part_schema(request)})
        state=directory/'writing-parts'/identity/'state.json'
        if state.is_file() and json.loads(state.read_text()).get('fingerprint')==identity:return True
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


def certify_response(response,request,root):
    if not response.attempt_path or not Path(response.attempt_path).is_absolute():return response
    record=json.loads(Path(response.attempt_path).read_text())
    if record.get('transport') == 'agy_local_recovery_v1':
        from .local_recovery import verify_local_receipt
        raw=verify_local_receipt(Path(response.attempt_path),root)
        if response.notes != raw: raise WorkflowError('Recovered result changed')
        return response
    if record.get('error') or record.get('returncode')!=0:raise WorkflowError('Saved response came from a failed provider transport')
    if record.get('envelope',{}).get('transport')!='agy_parts_v1':return response
    from .agy_parts import verify_receipt
    raw=verify_receipt(record,root)
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


def repair_coverage(request,response,defects,maps,adapter,timeout):
    """One hash-anchored correction; existing prose is never rewritten."""
    from .batch import _hash, _stage_visuals
    # A compatible response can bypass the new-writing branch that normally
    # stages images. Repair still needs verified pixels at the new paths.
    _stage_visuals(request)
    directory=request.run_dir/'coverage-repair';directory.mkdir(parents=True,exist_ok=True)
    path=directory/'repair.json'
    base_hash=_hash(response.notes)
    allowed={maps[d['lecture_id']][d['evidence_id']] for d in defects}
    task={'defects':[{**d,'evidence_id':maps[d['lecture_id']][d['evidence_id']]} for d in defects],
          'anchors':[{'lecture_id':row['lecture_id'],'block_sha256':_hash(b),'block':b}
                     for row in response.notes['lessons'] for b in row['notes']['blocks']], 'base_notes_sha256':base_hash}
    block={'type':'object','additionalProperties':False,'properties':{
        'kind':{'type':'string','enum':['paragraph']},'text':{'type':'string'},
        'evidence_ids':{'type':'array','items':{'type':'string','enum':sorted(allowed)}},
        'coverage_role':{'type':'string','enum':['full','supplement']}},
        'required':['kind','text','evidence_ids','coverage_role']}
    schema={'type':'object','additionalProperties':False,'properties':{'insertions':{'type':'array','maxItems':len(defects),
        'items':{'type':'object','additionalProperties':False,'properties':{'lecture_id':{'type':'string'},'after_block_sha256':{'type':'string'},'block':block},
                 'required':['lecture_id','after_block_sha256','block']}}},'required':['insertions']}
    schema['properties']['attachments']={'type':'array','maxItems':len(defects),'items':{'type':'object','additionalProperties':False,'properties':{'lecture_id':{'type':'string'},'block_sha256':{'type':'string'},'evidence_ids':{'type':'array','items':{'type':'string','enum':sorted(allowed)}}},'required':['lecture_id','block_sha256','evidence_ids']}}
    schema['properties']['visual_updates']={'type':'array','maxItems':len(defects),'items':{'type':'object','additionalProperties':False,'properties':{'evidence_id':{'type':'string','enum':sorted(allowed)},'decision':{'type':'string','enum':['exclude']},'reason':{'type':'string'},'inspected':{'type':'boolean'}},'required':['evidence_id','decision','reason','inspected']}}
    schema['required']+=['attachments','visual_updates']
    selected={'lecture_id':request.lecture_id,'course':request.source_pack.get('course',{}),
              'evidence':[e for e in request.source_pack['evidence'] if e['id'] in allowed],
              'visuals':[deepcopy(v) for v in request.source_pack.get('visuals',[]) if v.get('evidence_id') in allowed]}
    for visual in selected['visuals']:
        image=Path(visual['image_path']);target=directory/'visuals'/(file_sha256(image)+image.suffix)
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(image,target);visual['image_path']=str(target)
    repair_request=replace(request,phase='coverage_repair',source_pack=selected,batch_context={'repair_task':task},
                           response_schema=schema,run_dir=directory,timeout_seconds=timeout)
    if path.exists():
        saved=json.loads(path.read_text())
        if saved['base_notes_sha256']!=base_hash or saved['task']!=task:raise WorkflowError('Saved targeted correction differs from its source task; no paid retry')
        patch=ProviderResponse(**saved['response'])
    else:
        if (directory/'started.json').exists():raise WorkflowError('Targeted correction already attempted; inspect its retained attempt, no paid retry')
        atomic_json_write(directory/'started.json',{'base_notes_sha256':base_hash,'task':task})
        patch=invoke_provider(repair_request,adapter)
        atomic_json_write(path,{'base_notes_sha256':base_hash,'task':task,'response':asdict(patch)})
    notes=apply_correction(response.notes,patch.notes,task)
    lineage={'base_attempt':response.attempt_path,'base_notes_sha256':base_hash,'repair':str(path),'repair_sha256':file_sha256(path),
             'repair_attempt':patch.attempt_path,'repair_usage':patch.usage,'effective_notes_sha256':_hash(notes),'operations':patch.notes}
    atomic_json_write(directory/'lineage.json',lineage)
    return replace(response,notes=notes),lineage


def apply_correction(original,patch,task):
    from .batch import _hash,NARRATIVE_KINDS
    notes=deepcopy(original);rows={r['lecture_id']:r['notes'] for r in notes['lessons']};seen=set()
    allowed={d['evidence_id'] for d in task['defects']}
    if set(patch)-{'insertions','attachments','visual_updates'}:raise WorkflowError('Unexpected targeted correction operation')
    by_lesson={l:{d['evidence_id'] for d in task['defects'] if d['lecture_id']==l} for l in rows}
    anchors={(l,_hash(b)):b for l,row in rows.items() for b in row['blocks']}
    insertions=patch.get('insertions',[]);attachments=patch.get('attachments',[]);updates=patch.get('visual_updates',[])
    if (not all(isinstance(v,list) for v in (insertions,attachments,updates))
            or not 1<=len(insertions)+len(attachments)<=len(allowed) or len(updates)>len(allowed)):
        raise WorkflowError('Invalid targeted correction')
    for op in insertions:
        if not isinstance(op,dict) or set(op)!={'lecture_id','after_block_sha256','block'} or op['lecture_id'] not in rows:raise WorkflowError('Correction may only insert into the affected lesson')
        l=op['lecture_id'];b=op['block'];ids=b.get('evidence_ids',[]);anchor=anchors.get((l,op['after_block_sha256']))
        if b.get('kind')!='paragraph' or not str(b.get('text','')).strip() or not ids or set(ids)-by_lesson[l] or set(ids)&seen or b.get('coverage_role') not in {'full','supplement'}:
            raise WorkflowError('Correction changed its allowed coverage scope')
        if anchor is None or anchor['kind'] not in NARRATIVE_KINDS | {'heading'}:
            raise WorkflowError('Targeted correction anchor changed')
        blocks=rows[l]['blocks'];index=next(i for i,v in enumerate(blocks) if v is anchor)
        blocks.insert(index+1,deepcopy(b));seen.update(ids)
    for op in attachments:
        if not isinstance(op,dict) or set(op)!={'lecture_id','block_sha256','evidence_ids'} or op['lecture_id'] not in rows:raise WorkflowError('Invalid source attachment')
        l=op['lecture_id'];ids=op['evidence_ids'];anchor=anchors.get((l,op['block_sha256']))
        if not ids or set(ids)-by_lesson[l] or set(ids)&seen or anchor is None or anchor['kind'] not in NARRATIVE_KINDS or anchor.get('coverage_role') not in {'full','supplement'}:
            raise WorkflowError('Source attachment is outside the specific coverage defect')
        anchor['evidence_ids']+=list(ids);seen.update(ids)
    visual_seen=set()
    for update in updates:
        id=update.get('evidence_id')
        if (set(update)!={'evidence_id','decision','reason','inspected'} or id not in allowed or id in visual_seen
                or update['decision']!='exclude' or update['inspected'] is not True or not str(update['reason']).strip()):raise WorkflowError('Invalid inspected visual correction')
        visual_seen.add(id)
        row=next(rows[l] for l in rows if id in by_lesson[l]);old=next((v for v in row['visual_decisions'] if v['evidence_id']==id),None)
        if old is None:raise WorkflowError('Correction cannot create an unrelated visual decision')
        old.update(update)
    if seen!=allowed:raise WorkflowError('Targeted correction did not cover every reported defect')
    for row in rows.values():
        for coverage in row['evidence_coverage']:
            if coverage['evidence_id'] in seen:coverage.update(disposition='used',reason='Explained or source-linked by the retained targeted correction')
    return notes


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
                saved=_read(complete);valid=any(c['lecture_id']==lecture and c['sha256']==file_sha256(candidate) for c in saved['candidates'])
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
        budgets=[]
        for file in (root/'active-budgets').glob('*.json'):
            value=json.loads(file.read_text())
            if row['lecture_id'] in value.get('lessons',[]):budgets.append(value.get('used_seconds',0))
        row['active_seconds']=max([v for v in [*budgets,row.get('active_seconds')] if v is not None],default=None)
    return {'batch_id':batch_id,'status':status,'lessons':lessons,'activity':activity,
            'problem':manifest.get('problem',manifest.get('error')),'network_retries':retries,
            'local_recovery':recovery,'warnings':manifest.get('warnings',[]),
            'phase':next((r['phase'] for r in lessons if r.get('phase')),None) or (manifest.get('problem') if isinstance(manifest.get('problem'),dict) else {}).get('phase') or activity.get('status'), 'published_to_word':False}



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
    if patch['base_notes_sha256']!=line['base_notes_sha256'] or patch['response']['notes']!=line['operations']:
        raise WorkflowError('Correction operations differ from their retained response')
    effective=apply_correction(base.notes,patch['response']['notes'],patch['task'])
    if _hash(effective)!=line['effective_notes_sha256']:raise WorkflowError('Targeted correction assembly changed')
    record=json.loads(Path(line['repair_attempt']).read_text())
    if record.get('error') or record.get('returncode')!=0 or record['requested']!=candidate['requested_model']:
        raise WorkflowError('Targeted correction lacks a successful matching provider attempt')
    if record['requested']['provider']=='agy':
        envelope=json.loads(record['stdout'])
        if envelope.get('status')!='SUCCESS' or _decode_agy(envelope)!=patch['response']['notes']:
            raise WorkflowError('Targeted correction differs from its SUCCESS transport')
    if line['validated_notes_sha256'].get(candidate['notes'].get('lecture_id',candidate.get('lecture_id',''))) is None:
        # Current candidates identify lessons via their source pack.
        pack=json.loads(Path(candidate['source_pack']).read_text());lecture=pack['lecture_id']
    else:lecture=candidate.get('lecture_id',candidate['notes'].get('lecture_id'))
    if _hash(candidate['notes'])!=line['validated_notes_sha256'].get(lecture):raise WorkflowError('Corrected candidate changed after validation')
