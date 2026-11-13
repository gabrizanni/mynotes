"""Recover missing page metadata from explicit, unambiguous narrative links.

This never adds citations or declares semantic completeness. Existing coverage
validation still checks the content; ambiguous and uncited pages remain missing.
"""
from copy import deepcopy


def reconcile(units, request):
    result=deepcopy(units)
    settled={d['canonical_slide_id'] for part in result for d in part['organization_delta']}
    pages={row['id'] for row in request.source_pack['evidence'] if row['id'].startswith('S')}
    origins=request.batch_context.get('lesson_evidence',{})
    for identifier in sorted(pages-settled):
        candidates=[]
        for part in result:
            for block in part['blocks']:
                if (identifier in block.get('evidence_ids',[]) and
                    block.get('kind') in {'paragraph','bullets','table','formula','visual'} and
                    block.get('coverage_role') in {'full','supplement'}):
                    candidates.append((part,block))
        owners={part['lecture_id'] for part,_ in candidates}
        if len(owners)!=1:
            continue
        owner=next(iter(owners))
        roles={block['coverage_role'] for _,block in candidates}
        if len(roles)!=1:
            continue
        role=next(iter(roles))
        transcripts=list(dict.fromkeys(i for _,block in candidates for i in block['evidence_ids'] if i.startswith('T')))
        # Do not turn a slide citation alone into a claim that it was spoken.
        if role=='full' and (not transcripts or set(transcripts)-set(origins.get(owner,[]))):
            continue
        if len(transcripts)>12:
            continue  # Preserve the full provenance rather than truncate to fit metadata.
        choice={'canonical_slide_id':identifier,'owner':owner,
                'status':'taught' if role=='full' else 'supplement',
                'reason':'Application-derived missing assignment from existing explicit, unique main-narrative source links; not a semantic coverage certificate.',
                'evidence_ids':transcripts if role=='full' else []}
        candidates[0][0]['organization_delta'].append(choice)
    return result
