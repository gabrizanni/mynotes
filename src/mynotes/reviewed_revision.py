"""Source-bound review of observed defects, preserving v2 per-block allowances."""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import re

from . import progressive_review as risk
from . import agy_progressive as transport
from .providers import ProviderRequest, ProviderResponse, RequestedModel
from .utils import atomic_json_write, file_sha256
from .workflow import WorkflowError, _generation_lock, _load_generation_phase


def _read(path):
    return json.loads(path.read_text())


def _signature(block):
    return risk.digest({k:v for k,v in block.items() if k!='evidence_ids'})


def _slots(request, receipt):
    """Map effective blocks to their original allowance, including split patches."""
    result = {}
    for ref in receipt['units']:
        saved = _read(Path(receipt['_directory'])/ref['file']); unit = saved['raw_unit']
        patches = {}
        for row in saved['reviews']:
            for decision in _read(Path(row['path']))['response']['decisions']:
                if decision['status']=='patch':
                    patches[int(decision['block_id'][1:])] = risk.replacement_blocks(request,unit,decision)
        for index, block in enumerate(unit['blocks']):
            for effective in patches.get(index,[block]):
                key = (unit['lecture_id'],_signature(effective))
                result.setdefault(key,[]).append({'unit':unit['part_index'],'block':index,'corrected':index in patches})
    return result


def _base(directory, root, *, intent=None):
    intent = _read(directory/'intent.json') if intent is None else intent
    for name in ('base','source_proof','original_manifest'):
        row=intent[name]; path=Path(row['path'])
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or file_sha256(path)!=row['sha256']:
            raise WorkflowError('Reviewed revision source or snapshot changed')
    response = _load_generation_phase(Path(intent['base']['path']),previous=None)
    proof = _read(Path(intent['source_proof']['path']))
    if intent['requested']!=proof['requested']:
        raise WorkflowError('Observed review cannot change the saved model or effort')
    request = ProviderRequest(proof['lecture_id'],proof['course'],{},RequestedModel(**proof['requested']),
        source_pack=proof['source_pack'],style=proof['style'],batch_context=proof['batch_context'],run_dir=directory)
    attempt=Path(response.attempt_path); record=_read(attempt)
    if (attempt.is_symlink() or not attempt.resolve().is_relative_to(root.resolve())
            or file_sha256(attempt)!=intent['attempt_sha256']):
        raise WorkflowError('Original reviewed transport changed')
    if transport.verify_receipt(record,root)!=response.notes:
        raise WorkflowError('Reviewed revision requires intact successful original writing')
    receipt=_read(Path(record['envelope']['receipt']));receipt['_directory']=str(Path(record['envelope']['receipt']).parent)
    if intent.get('reference_review'):
        from .reference_review import verify,apply
        verify(intent['reference_review'],root)
        ref=_read(Path(intent['reference_review']['path']))
        response=replace(response,notes=apply(response.notes,ref['review'],ref['lesson_evidence']))
    return intent,request,response,receipt


def _targets(request,response,receipt,review,maps):
    from .batch_recovery import validate_review_defects
    defects=validate_review_defects(review,response,request.batch_context['lessons'],maps)
    slots=_slots(request,receipt);seen=set();targets={}
    for defect in defects:
        lecture=defect['lecture_id']; index=int(re.fullmatch(r'blocks\[(\d+)\]',defect['path'])[1])
        if (lecture,index) in seen:raise WorkflowError('A reviewed block may occur only once')
        seen.add((lecture,index))
        note=next(row['notes'] for row in response.notes['lessons'] if row['lecture_id']==lecture)
        block=note['blocks'][index]; origins=slots.get((lecture,_signature(block)),[])
        if len(origins)!=1:raise WorkflowError('Reviewed block cannot be bound uniquely to its original allowance')
        if origins[0]['corrected']:raise WorkflowError('NEEDS_REVIEW: this block already received its automatic correction')
        targets.setdefault(lecture,[]).append({'block_id':f'B{index}','base_sha256':risk.digest(block),
            'block':deepcopy(block),'reasons':['observed_defect: '+defect['reason']], 'original_slot':origins[0]})
    return targets


def _apply_packets(request,response,records,directory,*,invoke=None):
    corrected=deepcopy(response.notes); refs=[]
    known={}
    for ref in records['receipt']['units']:
        for row in _read(Path(records['receipt']['_directory'])/ref['file'])['effective']['visual_delta']:
            known[row['evidence_id']]=row['content_kind']
    request=replace(request,batch_context={**request.batch_context,'review_visual_classifications':known})
    number=0
    for lecture,targets in records['targets'].items():
        note=next(row['notes'] for row in corrected['lessons'] if row['lecture_id']==lecture)
        unit={'lecture_id':lecture,'blocks':deepcopy(note['blocks']),'visual_delta':[]}
        edits={};visual_updates={}
        groups=[];current=[]
        for target in targets:
            try:
                if len(current)==3:raise WorkflowError('packet full')
                risk.packet_request(request,[*current,target],unit,directory/'size-check')
            except (WorkflowError,RuntimeError):
                if not current:raise
                groups.append(current);current=[target]
                risk.packet_request(request,current,unit,directory/'size-check')
            else:current.append(target)
        if current:groups.append(current)
        for group in groups:
            path=directory/f'packet-{number:03d}';number+=1;path.mkdir(parents=True,exist_ok=True)
            task={'targets':group,'unit_sha256':risk.digest(unit)};result=path/'review.json'
            if result.exists():
                saved=_read(result)
                if saved['task']!=task:raise WorkflowError('Saved observed-defect review changed')
            else:
                if invoke is None:raise WorkflowError('Reviewed revision lacks a saved successful packet')
                if (path/'started.json').exists():raise WorkflowError('NEEDS_REVIEW: observed-defect packet already consumed its attempt')
                atomic_json_write(path/'started.json',task)
                reply,attempt=invoke(risk.packet_request(request,group,unit,path))
                saved={'task':task,'response':reply,'attempt':str(attempt),'attempt_sha256':file_sha256(attempt)}
                atomic_json_write(result,saved)
            attempt=Path(saved['attempt'])
            if (attempt.is_symlink() or not attempt.resolve().is_relative_to(directory.resolve())
                    or file_sha256(attempt)!=saved['attempt_sha256']
                    or _read(attempt)['requested']!=records['requested']):
                raise WorkflowError('Observed-defect review transport changed')
            reply=transport.verify_single(attempt,path,risk.review_schema())
            if reply!=saved['response']:raise WorkflowError('Reviewed patch differs from its successful response')
            risk.apply_review(request,unit,group,reply)
            transport.image_check(request,{'visual_delta':reply['visual_updates']},_read(attempt)['envelope']['source_access'])
            for decision in reply['decisions']:
                if decision['status']=='patch':edits[int(decision['block_id'][1:])]=risk.replacement_blocks(request,unit,decision)
            risk.merge_visual_updates(visual_updates,reply['visual_updates'])
            refs.append({'path':str(result),'sha256':file_sha256(result)})
        note['blocks']=[b for i,block in enumerate(unit['blocks']) for b in edits.get(i,[deepcopy(block)])]
        decisions={v['evidence_id']:v for v in note['visual_decisions']}
        for key,update in visual_updates.items():
            decisions[key]={k:v for k,v in update.items() if k!='content_kind'}
        note['visual_decisions']=list(decisions.values())
    return replace(response,notes=corrected),refs


def verify(candidate,workspace_root):
    """Validate the derivative separately; never relabel the original transport."""
    ref=candidate['reviewed_revision'];path=Path(ref['path'])
    if path.is_symlink() or not path.resolve().is_relative_to(workspace_root.resolve()) or file_sha256(path)!=ref['sha256']:
        raise WorkflowError('Reviewed revision receipt changed')
    saved=_read(path);intent,request,base,receipt=_base(path.parent,workspace_root)
    if saved['intent_sha256']!=file_sha256(path.parent/'intent.json') or saved['review_sha256']!=risk.digest(intent['review']):
        raise WorkflowError('Reviewed revision intent changed')
    targets=_targets(request,base,receipt,intent['review'],intent['maps'])
    corrected,refs=_apply_packets(request,base,{'receipt':receipt,'targets':targets,'requested':intent['requested']},path.parent)
    if (refs!=saved['packets'] or risk.digest(corrected.notes)!=saved['corrected_notes_sha256']
            or risk.digest(candidate['notes'])!=saved['validated_notes_sha256'][candidate['notes']['lecture_id']]):
        raise WorkflowError('Candidate differs from its source-bound reviewed revision')


def review_saved(workspace,batch_id,review,*,invoke=None):
    from . import batch
    from .publication import _load_candidate
    from .generation_budget import PhaseBudget
    from .utils import atomic_json_write
    root=workspace.runs_dir/'_batches'/batch_id;directory=root/'observed-review';directory.mkdir(exist_ok=True)
    current=batch.load_batch_manifest(workspace,batch_id)
    if current.get('progressive_protocol')!=2 or len(current['groups'])!=1:
        raise WorkflowError('Observed v2 review requires a completed joint progressive job')
    with ExitStack() as locks:
        for lecture in current['lectures']:locks.enter_context(_generation_lock(workspace,lecture))
        intent_path=directory/'intent.json'
        if (directory/'committed.json').exists():
            if _read(intent_path)['review']!=review:raise WorkflowError('Observed review already recorded; no additional automatic round')
            if _read(directory/'committed.json')['result_sha256']!=file_sha256(directory/'result.json'):
                raise WorkflowError('Reviewed revision commit changed')
            for lecture in current['lectures']:_load_candidate(workspace,lecture)
            return {'status':'CANDIDATE_READY','fingerprint':current['fingerprint'],'reused':True}
        if intent_path.exists():
            if _read(intent_path)['review']!=review:raise WorkflowError('Observed review already recorded; no additional automatic round')
        else:
            old={l:_load_candidate(workspace,l)[0] for l in current['lectures']}
            attempts={p for row in old.values() for p in row.get('shared_attempt_paths',[])}
            writers=[Path(p) for p in attempts if _read(Path(p)).get('envelope',{}).get('transport')=='agy_progressive_v2']
            if len(writers)!=1:raise WorkflowError('Observed review needs one certified original writer')
            receipt_path=Path(_read(writers[0])['envelope']['receipt']);group=receipt_path.parent.parent
            bases=list(group.glob('returned-writing-*.json'))
            if len(bases)!=1:raise WorkflowError('Original writing response is ambiguous')
            proof=receipt_path.parent/'request.json'
            intent={'review':review,'base':{'path':str(bases[0]),'sha256':file_sha256(bases[0])},
                'source_proof':{'path':str(proof),'sha256':file_sha256(proof)},'attempt_sha256':file_sha256(writers[0]),
                'original_manifest':{'path':str(root/'manifest.json'),'sha256':file_sha256(root/'manifest.json')},
                'original_candidates':{},'maps':batch._read(group/'request-intent.json')['maps'],
                'requested':current['requested_model'],'reference_review':next(iter(old.values())).get('reference_review')}
            _,preflight_request,preflight_base,preflight_receipt=_base(directory,workspace.root,intent=intent)
            _targets(preflight_request,preflight_base,preflight_receipt,review,intent['maps'])
            original_manifest=directory/'original-manifest.json'
            if original_manifest.exists():raise WorkflowError('Unbound original review snapshot needs inspection')
            original_manifest.write_bytes((root/'manifest.json').read_bytes())
            intent['original_manifest']={'path':str(original_manifest),'sha256':file_sha256(original_manifest)}
            for lecture in current['lectures']:
                path=directory/(lecture+'.original.json');path.write_bytes((workspace.runs_dir/lecture/'lecture_notes.candidate.json').read_bytes())
                intent['original_candidates'][lecture]={'path':str(path),'sha256':file_sha256(path)}
            atomic_json_write(intent_path,intent)
        intent,request,base,receipt=_base(directory,workspace.root)
        targets=_targets(request,base,receipt,review,intent['maps'])
        manifest=batch._read(Path(intent['original_manifest']['path']))
        if manifest['style']!=request.style or manifest['course']!=request.course:
            raise WorkflowError('Observed review cannot change saved writing preferences')
        packs,sources,inventory,_=batch._prepared_context(workspace,manifest['lectures'],manifest.get('context_lessons'),manifest.get('selection'),True)
        if sources!=manifest['sources']:raise WorkflowError('Sources changed since the observed review base')
        runtime={**manifest,'packs':packs,'inventory':inventory,'requested':RequestedModel(**manifest['requested_model'])}
        batch._unchanged(runtime)
        baseline=batch._validate_group(base,runtime,manifest['lectures'],intent['maps'],final_phase=True,continuity=[])
        for lecture,row in intent['original_candidates'].items():
            path=Path(row['path'])
            if (path.is_symlink() or not path.resolve().is_relative_to(workspace.root.resolve())
                    or file_sha256(path)!=row['sha256'] or _read(path)['notes']!=baseline[lecture]):
                raise WorkflowError('Original candidate does not match the certified review base')
        budget=None
        def call(packet):
            nonlocal budget
            if budget is None:budget=PhaseBudget(directory/'phase-budget.json',manifest['lectures'],receipt_directories=[directory])
            with budget.track('progressive_review'):
                return (invoke or transport.run_single)(packet)
        corrected,packets=_apply_packets(request,base,{'receipt':receipt,'targets':targets,'requested':intent['requested']},directory,invoke=call)
        validated=batch._validate_group(corrected,runtime,manifest['lectures'],intent['maps'],final_phase=True,continuity=[])
        batch._unchanged(runtime)
        revision=risk.digest({'original':manifest['fingerprint'],'observed_review':review})
        result={'kind':'reviewed_progressive_revision','review_sha256':risk.digest(review),'revision':revision,
            'intent_sha256':file_sha256(intent_path),
            'packets':packets,'corrected_notes_sha256':risk.digest(corrected.notes),
            'validated_notes_sha256':{l:risk.digest(n) for l,n in validated.items()}}
        result_path=directory/'result.json'
        if result_path.exists() and _read(result_path)!=result:raise WorkflowError('Saved reviewed result changed')
        if not result_path.exists():atomic_json_write(result_path,result)
        lineage={'path':str(result_path),'sha256':file_sha256(result_path)}
        payloads={}
        for lecture in manifest['lectures']:
            payload=deepcopy(_read(Path(intent['original_candidates'][lecture]['path'])))
            payload.update(notes=validated[lecture],batch_fingerprint=revision,reviewed_revision=lineage,published_to_word=False)
            payload.pop('published_path',None);payload.pop('published_hash',None);payloads[lecture]=payload
            actual=_read(workspace.runs_dir/lecture/'lecture_notes.candidate.json')
            normalized=lambda v:{k:x for k,x in v.items() if k not in {'published_to_word','published_path','published_hash'}}
            if normalized(actual) not in [normalized(payload),normalized(_read(Path(intent['original_candidates'][lecture]['path'])))]:
                raise WorkflowError('Current candidate changed during observed review; no overwrite')
        completion={'candidates':[{'lecture_id':l,'sha256':__import__('hashlib').sha256((json.dumps(p,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode()).hexdigest()} for l,p in payloads.items()],
                    'phases':[],'repair_lineage':None}
        batch._save(root/revision/'group-000/complete.json',completion)
        for lecture,payload in payloads.items():atomic_json_write(workspace.runs_dir/lecture/'lecture_notes.candidate.json',payload)
        updated={**manifest,'fingerprint':revision,'reviewed_revision':lineage,'status':'CANDIDATE_READY'}
        updated.pop('word_candidates',None)
        updated['shared_attempts']=[*manifest.get('shared_attempts',[]),
            *[{'phase':'progressive_review','group':0,'lessons':manifest['lectures'],
               'attempt_path':_read(Path(row['path']))['attempt'],'source':'observed_defect'} for row in packets]]
        batch._save(root/revision/'manifest.json',updated);batch._save(root/'manifest.json',updated)
        atomic_json_write(directory/'committed.json',{'result_sha256':file_sha256(result_path)})
        return {'status':'CANDIDATE_READY','fingerprint':revision,'reviewed_revision':lineage}
