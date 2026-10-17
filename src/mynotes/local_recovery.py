"""Offline recovery of complete SUCCESS turns rejected by a local validator.

The failed process record is immutable. A separate receipt certifies the raw
turns and the only permitted transformation, reference zero-padding.
"""
from pathlib import Path
import json

from .providers import ProviderError, ProviderResponse, ProviderRequest, RequestedModel, _decode_agy, _normalize_transport_notes
from .utils import atomic_json_write, file_sha256
from .evidence_refs import normalize_references


def _contained(path, root):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ProviderError('Recovery file is outside its private run')
    return path


def recover_parts(request, directory, *, apply=False):
    from .agy_parts import assemble, part_schema, fingerprint
    directory = Path(directory).resolve()
    attempts = sorted((directory/'provider-attempts').glob('*.json'), key=lambda p:p.stat().st_mtime, reverse=True)
    states = list(directory.glob('writing-parts/*/state.json'))
    if not states:
        return None, None
    state_path = _contained(max(states,key=lambda p:p.stat().st_mtime),directory)
    state = json.loads(state_path.read_text()); parts_root = state_path.parent
    if not state.get('local_rejection'):
        return None, None
    records = []
    for path in attempts:
        path=_contained(path,directory)
        record=json.loads(path.read_text())
        if record.get('requested') != request.requested.__dict__:
            continue
        results=[]
        for line in record.get('stdout','').splitlines():
            if line.strip():
                event=json.loads(line)
                if event.get('event')=='result': results.append(event.get('result'))
        if results:
            records.append((path,record,results))
    if not records:
        raise ProviderError('No recorded provider stream for local recovery')
    attempt,record,results=records[0]
    if (not record.get('error') or not str(record['error']).startswith('Writing portion cites unknown evidence')
            or any(not isinstance(r,dict) or r.get('status')!='SUCCESS' for r in results)):
        raise ProviderError('Only a complete SUCCESS stream rejected locally can be recovered')
    # Reconstruct and compare all evidence and metadata against this request.
    # A mutable manifest alone cannot certify that the model read the right data.
    from .providers import _context
    context=_context(request).splitlines()
    config=json.loads(context[1]);data=json.loads(context[-1]);expected=data.pop('evidence')
    def comparable(value):
        from copy import deepcopy
        value=deepcopy(value)
        batch=value.get('coordinated_batch',{})
        if 'ownership' in batch:batch['ownership']=sorted(batch['ownership'],key=lambda r:r['canonical_slide_id'])
        return value
    candidates=[]
    for folder in (directory/'request-inputs').glob('*'):
        if not folder.is_dir():continue
        try:
            loaded=[]
            for file in sorted(folder.glob('evidence-*.jsonl')):
                loaded.extend(json.loads(line) for line in file.read_text().splitlines() if line.strip())
            if (loaded==expected and comparable(json.loads((folder/'configuration.json').read_text()))==comparable(config)
                    and json.loads((folder/'source-metadata.json').read_text())==data):candidates.append(folder)
        except (OSError,ValueError):continue
    if len(candidates)!=1:
        raise ProviderError('Original staged inputs do not match the source-bound request')
    parts=[]; proof={str(state_path):file_sha256(state_path),str(attempt):file_sha256(attempt)}
    transformations=[]; schema=part_schema(request)
    if len(results)!=len(state.get('parts',[]))+1:
        raise ProviderError('Missing or repeated provider portions')
    for i,envelope in enumerate(results):
        if envelope.get('conversation_id')!=state.get('conversation_id'):
            raise ProviderError('Recovery conversation changed')
        path=parts_root/f'returned-{i:03d}.json'
        path=_contained(path,directory)
        raw=json.loads(path.read_text())
        if raw.get('envelope')!=envelope or _normalize_transport_notes(_decode_agy(envelope),schema)!=raw.get('part'):
            raise ProviderError('Retained portion differs from original provider stream')
        proof[str(path)]=file_sha256(path)
        if i<len(state['parts']):
            ref=state['parts'][i];accepted=_contained(parts_root/ref['file'],directory)
            if accepted.name!=f'part-{i:03d}.json' or file_sha256(accepted)!=ref['sha256'] or json.loads(accepted.read_text())!=raw:
                raise ProviderError('Accepted checkpoint changed')
            proof[str(accepted)]=file_sha256(accepted)
        part,changes=normalize_references(raw['part'],[r['id'] for r in request.source_pack['evidence']])
        transformations.extend({'part_index':i,**r} for r in changes);parts.append(part)
    if not parts[-1].get('complete'):
        raise ProviderError('Incomplete saved response cannot become a finished candidate')
    notes=assemble(request,parts)
    # Bind all staged inputs and image attachments; the caller also proves the
    # original prompt identity against the frozen plan and current source packs.
    for path in directory.glob('request-inputs/**/*'):
        if path.is_file(): proof[str(path)]=file_sha256(path)
    receipt={'schema_version':1,'transport':'agy_local_recovery_v1','status':'RECOVERED_LOCALLY',
             'original_process_succeeded':False,'reason':'Complete SUCCESS turns rejected by the local reference validator',
             'files':proof,'conversation_id':state['conversation_id'],'requested':request.requested.__dict__,
             'source_ids':[r['id'] for r in request.source_pack['evidence']],
             'lecture_id':request.lecture_id,'lessons':request.batch_context['lessons'] if request.batch_context else None,
             'schema':schema,'parts':[str(parts_root/f'returned-{i:03d}.json') for i in range(len(parts))],
             'transformations':transformations,'assembled_sha256':fingerprint(notes),
             'active_seconds':state.get('used_seconds',record.get('active_seconds',0)),
             'usage':{'session_cumulative':results[-1].get('usage',{}),'scope':'Original conversation; local recovery consumes no tokens'},
             'source_read_audit':__import__('mynotes.generation_diagnostics',fromlist=['source_read_audit']).source_read_audit(record.get('stdout',''))}
    path=directory/'local-recovery'/f'{fingerprint(receipt)}.json'
    if apply and not path.exists():
        atomic_json_write(path,receipt)
    return ProviderResponse(notes,'agy','','',receipt['usage'],str(path) if apply else None),receipt


def verify_local_receipt(path, root):
    from .agy_parts import assemble, fingerprint
    path=_contained(path,root);receipt=json.loads(path.read_text())
    if path.stem != fingerprint(receipt):
        raise ProviderError('Recovery receipt changed')
    if receipt.get('transport')!='agy_local_recovery_v1' or receipt.get('status')!='RECOVERED_LOCALLY':
        raise ProviderError('Invalid local recovery receipt')
    for name,digest in receipt['files'].items():
        if file_sha256(_contained(name,root))!=digest:raise ProviderError('Recovery input changed')
    request=ProviderRequest(receipt['lecture_id'],{}, {},RequestedModel(**receipt['requested']),
        source_pack={'evidence':[{'id':i} for i in receipt['source_ids']]},
        batch_context={'lessons':receipt['lessons']} if receipt['lessons'] else None)
    parts=[];changes=[]
    for i,name in enumerate(receipt['parts']):
        raw=json.loads(_contained(name,root).read_text());envelope=raw['envelope']
        if envelope.get('status')!='SUCCESS' or envelope.get('conversation_id')!=receipt['conversation_id']:
            raise ProviderError('Recovery response is not SUCCESS in the original conversation')
        if _normalize_transport_notes(_decode_agy(envelope),receipt['schema'])!=raw['part']:
            raise ProviderError('Recovery response content changed')
        part,report=normalize_references(raw['part'],receipt['source_ids'])
        changes.extend({'part_index':i,**r} for r in report);parts.append(part)
    notes=assemble(request,parts)
    if not parts[-1]['complete'] or changes!=receipt['transformations'] or fingerprint(notes)!=receipt['assembled_sha256']:
        raise ProviderError('Recovery assembly or transformation report changed')
    return notes


def recover_batch(workspace,batch_id,*,apply=False):
    from .batch import load_batch_manifest,_packs,_request,_continuity,_validate_group,_unchanged,generate_batch
    from .batch_recovery import prompt_matches
    manifest=load_batch_manifest(workspace,batch_id)
    packs,sources,inventory=_packs(workspace,manifest['lectures'])
    if sources!=manifest['sources']:raise ProviderError('Batch sources changed; recovery stopped')
    plan={**manifest,'packs':packs,'inventory':inventory,'requested':RequestedModel(**manifest['requested_model'])}
    _unchanged(plan)
    continuity=list(plan.get('prior_continuity',[]));diagnoses=[]
    for lessons in manifest['groups']:
        if all((workspace.runs_dir/l/'lecture_notes.candidate.json').exists() for l in lessons):
            for lesson in lessons:
                candidate=json.loads((workspace.runs_dir/lesson/'lecture_notes.candidate.json').read_text())
                if candidate.get('source_pack_hash')!=sources[lesson]['sha256']:raise ProviderError('Existing candidate sources changed')
                continuity.append(_continuity(lesson,candidate['notes']))
            continue
        from .batch_recovery import intent
        from .batch import _hash
        directory=workspace.runs_dir/'_batches'/batch_id/'generation-checkpoints'/_hash(intent(plan,lessons,continuity,reviewed=True))
        if not directory.exists():
            diagnoses.append({'lessons':lessons,'status':'NOT_STARTED','next_action':'generate'});break
        request,maps=_request(plan,lessons,directory,continuity)
        if not prompt_matches(directory,plan,plan,lessons,continuity):raise ProviderError('Original request identity cannot be verified')
        response,receipt=recover_parts(request,directory,apply=False)
        if response is None:
            diagnoses.append({'lessons':lessons,'status':'NO_LOCAL_RECOVERY','next_action':'inspect_saved_result'});break
        started=__import__('time').monotonic()
        from .generation_diagnostics import group_defects
        from .workflow import WorkflowError
        defects=group_defects(response,plan,lessons,maps)
        try:
            validated=_validate_group(response,plan,lessons,maps,final_phase=True,continuity=continuity)
        except WorkflowError as error:
            if not defects: defects.append({'phase':'CONTENT_VALIDATION','code':type(error).__name__,'message':str(error),
                                           'action':'Review saved content; no provider invoked'})
        if defects:
            diagnoses.append({'lessons':lessons,'status':'NEEDS_CONTENT_REVIEW','phase':'CONTENT_VALIDATION',
                              'transformations':len(receipt['transformations']),'defects':defects,
                              'active_seconds_preserved':receipt['active_seconds'],'provider_invoked':False,
                              'raw_result_preserved':True,'next_action':'review_targeted_correction'})
            break
        row={'lessons':lessons,'status':'LOCALLY_RECOVERABLE','phase':'LOCAL_VALIDATION','transformations':len(receipt['transformations']),
             'active_seconds_preserved':receipt['active_seconds'],'provider_invoked':False,'defects':[],
             'local_validation_seconds':__import__('time').monotonic()-started}
        diagnoses.append(row)
        if apply:
            response,receipt=recover_parts(request,directory,apply=True)
            row['receipt']=response.attempt_path;row['status']='RECOVERY_CERTIFIED'
        break
    if apply and any(r['status']=='RECOVERY_CERTIFIED' for r in diagnoses):
        class NoProvider:
            def generate(self,*_):raise ProviderError('Offline recovery cannot invoke a provider')
        options=manifest.get('request_options',{})
        recovered=generate_batch(workspace,manifest['course_code'],[int(l.rsplit('-L',1)[1]) for l in manifest['lectures']],
            provider=manifest['requested_model']['provider'],quality=manifest['requested_model']['quality'],
            selection=manifest.get('selection'),budgets=manifest.get('budgets'),instructions=options.get('instructions',''),
            image_limit=options.get('image_limit'),speaker_focus=options.get('speaker_focus'),
            batch_id=batch_id,provider_adapter=NoProvider(),pause_after=diagnoses[-1]['lessons'][-1])
        diagnoses[-1]['status']='CONTENT_RECOVERED'
    return {'batch_id':batch_id,'status':'CONTENT_RECOVERED' if apply and any(r['status']=='CONTENT_RECOVERED' for r in diagnoses) else 'DIAGNOSED',
            'lessons':diagnoses,'provider_invoked':False,'applied':any(r['status']=='CONTENT_RECOVERED' for r in diagnoses)}
