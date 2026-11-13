"""Explicit source-bound figure restoration, separate from automatic patches."""
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path

from .progressive_review import digest
from .providers import ProviderError, ProviderRequest, RequestedModel
from .utils import atomic_json_write, file_sha256


def _read(path):
    return json.loads(path.read_text())


def apply(notes, request, root, refs, access, review):
    """Restore only explicitly identified images; never rewrite source prose."""
    from .agy_progressive import image_check
    fields={'version','source_proof_sha256','accepted_units','reason','changes'}
    if (not isinstance(review,dict) or set(review)!=fields or type(review['version']) is not int or review['version']!=1
            or review['source_proof_sha256']!=file_sha256(root/'request.json')
            or not isinstance(review['reason'],str) or not review['reason'].strip()
            or not isinstance(review['accepted_units'],list) or not review['accepted_units']
            or review['accepted_units']!=refs[:len(review['accepted_units'])]
            or not isinstance(review['changes'],list) or not 1<=len(review['changes'])<=12):
        raise ProviderError('Visual review needs an unchanged source proof, saved prefix and explicit changes')
    known={}
    for index,ref in enumerate(review['accepted_units']):
        path=root/ref['file']
        if ref['file']!=f'unit-{index:03d}.json' or path.is_symlink() or file_sha256(path)!=ref['sha256']:
            raise ProviderError('Visual review saved prefix changed')
        known.update({v['evidence_id']:v for v in _read(path)['effective']['visual_delta']})
    visuals={v['evidence_id']:v for v in request.source_pack.get('visuals',[])}
    owners={v['canonical_slide_id']:v['owner'] for v in notes['organization']}
    result=deepcopy(notes);seen=set()
    for change in review['changes']:
        if (not isinstance(change,dict) or set(change)!={'evidence_id','image_sha256','before_sha256','owner','content_kind','placement','label','reason'}
                or change['content_kind'] not in {'chart','diagram'} or change['placement']!='before_footer'
                or any(not isinstance(change[k],str) or not change[k].strip()
                       for k in ('evidence_id','image_sha256','before_sha256','owner','label','reason'))
                or len(change['label'])>480):
            raise ProviderError('Visual review may restore only an explicit source chart or diagram')
        identifier=change['evidence_id'];visual=visuals.get(identifier);before=known.get(identifier)
        if (identifier in seen or not before or not visual or owners.get(identifier)!=change['owner']
                or digest(before)!=change['before_sha256'] or not visual.get('image_path')
                or file_sha256(Path(visual['image_path']))!=change['image_sha256']):
            raise ProviderError('Visual review has a stale, duplicate, foreign or changed source image')
        seen.add(identifier)
        decision={'evidence_id':identifier,'content_kind':change['content_kind'],'decision':'include',
                  'inspected':True,'reason':'Explicit visual review: '+change['reason']}
        # Verify the original observed pixels, including a certified cached
        # inspection. The new classification is explicitly the operator's
        # decision, never presented as a new observation by the old provider.
        image_check(request,{'visual_delta':[before]},access)
        placed=[row['lecture_id'] for row in result['lessons'] for block in row['notes']['blocks']
                if block.get('kind')=='visual' and block.get('visual_id')==identifier]
        if placed and placed!=[change['owner']]:
            raise ProviderError('Visual review cannot duplicate or relocate a placed source image')
        owner=next((row['notes'] for row in result['lessons'] if row['lecture_id']==change['owner']),None)
        if owner is None:raise ProviderError('Visual review owner has not been written')
        if not placed:
            index=next((i for i,b in enumerate(owner['blocks']) if b['kind'] in {'qa','recap'}),len(owner['blocks']))
            owner['blocks'].insert(index,{'kind':'visual','visual_id':identifier,'evidence_ids':[identifier],
                                          'coverage_role':'full','label':change['label']})
        for row in result['lessons']:
            choices={v['evidence_id']:v for v in row['notes']['visual_decisions']}
            choices[identifier]={k:v for k,v in decision.items() if k!='content_kind'}
            if row['lecture_id']!=change['owner']:
                choices[identifier].update(decision='exclude',reason='Source figure restored in its owning lesson.')
            row['notes']['visual_decisions']=list(choices.values())
        coverage={v['evidence_id']:v for v in owner['evidence_coverage']}
        coverage[identifier]={'evidence_id':identifier,'disposition':'used','reason':decision['reason']}
        owner['evidence_coverage']=list(coverage.values())
        # The restoration and ordinary source resources share one placement
        # pass; a newly inserted figure cannot strand its dependent links.
        if request.batch_context.get('risk_policy_version',1)>=4:
            from .note_assembly import place_owned_blocks
            for block in owner['blocks']:
                if block.get('kind')=='visual' and block.get('visual_id')==identifier:
                    block.setdefault('source_derivation',{'kind':'source_figure','source_evidence_id':identifier})
            place_owned_blocks(owner,request.source_pack['evidence'])
    if request.style.get('image_policy')=='none' or request.style.get('image_limit')==0:
        raise ProviderError('Visual review conflicts with the frozen no-image preference')
    limit=request.style.get('image_limit')
    if limit is not None and any(sum(b['kind']=='visual' for b in row['notes']['blocks'])>limit for row in result['lessons']):
        raise ProviderError('Visual review exceeds the frozen image limit')
    return result


def project(notes, request, root, refs, access, reference=None):
    """Bind the derivative to its own receipt; historical receipts stay valid."""
    path=root/'visual-review.json'
    if reference is None:
        if not path.exists():return notes,None
        reference={'path':str(path),'sha256':file_sha256(path)}
    if (Path(reference['path'])!=path or path.is_symlink() or file_sha256(path)!=reference['sha256']):
        raise ProviderError('Explicit visual review receipt changed')
    return apply(notes,request,root,refs,access,_read(path)),reference


def record_review(workspace, batch_id, review):
    """Record a bounded user-directed restoration on a stopped, unfinished job."""
    from .notes_run import _request_lock
    from .workflow import _generation_lock
    from .progressive_wire import assemble
    batch=workspace.runs_dir/'_batches'/batch_id
    if not batch.resolve().is_relative_to((workspace.runs_dir/'_batches').resolve()):
        raise ProviderError('Visual review batch is outside the workspace')
    with _request_lock(batch):
        roots=list(batch.glob('generation-checkpoints/*/progressive-v2/state.json'))
        if len(roots)!=1:raise ProviderError('Visual review needs one unambiguous progressive job')
        root=roots[0].parent
        if (not root.resolve().is_relative_to(batch.resolve()) or roots[0].is_symlink()
                or (root/'request.json').is_symlink()):
            raise ProviderError('Visual review source proof is outside the job')
        state=_read(roots[0]);proof=_read(root/'request.json')
        if state.get('finalized') or state.get('status') not in {'NEEDS_REVIEW','INTERRUPTED'}:
            raise ProviderError('Visual review requires a stopped unfinished job; completed receipts stay immutable')
        request=ProviderRequest(proof['lecture_id'],proof['course'],{},RequestedModel(**proof['requested']),
            source_pack=proof['source_pack'],style=proof['style'],batch_context=proof['batch_context'],run_dir=root.parent)
        with ExitStack() as locks:
            for lecture in sorted(proof['batch_context']['lessons']):locks.enter_context(_generation_lock(workspace,lecture))
            units=[_read(root/ref['file'])['effective'] for ref in state['units']]
            access=[json.loads(line) for line in (root/'access.jsonl').read_text().splitlines()]
            notes=assemble(request,units)
            apply(notes,request,root,state['units'],access,review)
            path=root/'visual-review.json'
            if path.exists() and _read(path)!=review:
                raise ProviderError('Visual review already recorded; no silent replacement')
            if not path.exists():atomic_json_write(path,review)
            return {'status':'VISUAL_REVIEW_RECORDED','path':str(path),'sha256':file_sha256(path),
                    'provider_invoked':False,'narrative_changed':False,'prior_receipts_changed':False}
