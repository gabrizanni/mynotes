"""One deterministic layout for program-owned figures and source resources."""
from copy import deepcopy

from .providers import ProviderError
from .progressive_review import digest

NARRATIVE = {'paragraph','bullets','table','formula','visual'}


def attach_comments(note, notices):
    for notice in notices:
        target=next((b for b in note['blocks'] if digest({k:v for k,v in b.items() if k!='review_comments'})==notice.get('block_sha256')),None)
        if target is None and notice.get('content_sha256'):
            target=next((b for b in note['blocks'] if digest({k:v for k,v in b.items() if k not in {'evidence_ids','review_comments'}})==notice['content_sha256']),None)
        if target is None and notice.get('evidence_id'):
            identifier=notice['evidence_id']
            target=next((b for b in note['blocks'] if b.get('kind')=='visual' and b.get('visual_id')==identifier),None)
            if target is None:
                target=next((b for b in note['blocks'] if b['kind'] in NARRATIVE and identifier in b.get('evidence_ids',[])),None)
        if target is not None:
            comment=str(notice['reason'])
            if comment not in target.setdefault('review_comments',[]):target['review_comments'].append(comment)
        if notice not in note.setdefault('review_issues',[]):note['review_issues'].append(deepcopy(notice))


def place_owned_blocks(note, evidence):
    """Keep authored prose ordered; place derived blocks by their source topic.

    A later restoration of a source figure must move its dependent resources
    with it. Reference-only mentions and footer citations do not move a topic.
    """
    rows={row['id']:row for row in evidence}
    blocks=note['blocks']
    derived=[b for b in blocks if b.get('source_derivation',{}).get('kind') in {'exact_source_resource','source_figure','coverage_repair'}]
    fixed=[b for b in blocks if b not in derived]
    def location(block):
        identifier=block['source_derivation']['source_evidence_id'];source=rows[identifier]
        page=source.get('page')
        return str(source.get('source_file','')),page if type(page) is int else 0,identifier
    for block in sorted(derived,key=location):
        file,page,identifier=location(block)
        preceding=[]
        for index,other in enumerate(fixed):
            if other['kind'] not in NARRATIVE or other.get('coverage_role')=='reference':continue
            if identifier in other.get('evidence_ids',[]) or any(str(rows.get(i,{}).get('source_file',''))==file
                   and type(rows.get(i,{}).get('page')) is int
                   and 0<rows[i]['page']<=page for i in other.get('evidence_ids',[])):
                preceding.append(index)
        footer=next((i for i,b in enumerate(fixed) if b['kind'] in {'qa','recap'}),len(fixed))
        position=min(max(preceding)+1,footer) if preceding else footer
        fixed.insert(position,block)
    note['blocks']=fixed
    return note


def compile_notes(request, notes, decisions):
    """Materialize selected source figures without asking a model to place them."""
    result=deepcopy(notes)
    sources={r['id']:r for r in request.source_pack['evidence']}
    visuals={v['evidence_id']:v for v in request.source_pack.get('visuals',[])}
    required=set(request.batch_context.get('required_visual_ids',[]))
    for row in result['lessons']:
        lecture=row['lecture_id'];note=row['notes']
        own={r['canonical_slide_id'] for r in result['organization'] if r['owner']==lecture}
        placed={b.get('visual_id') for b in note['blocks'] if b['kind']=='visual'}
        for identifier,decision in decisions.items():
            if identifier not in own or (decision.get('decision')!='include' and identifier not in required) or identifier in placed:continue
            if decision.get('inspected') is not True or identifier not in visuals:
                raise ProviderError('NEEDS_REVIEW: selected figure lacks source inspection')
            if request.style.get('image_policy')=='none' or request.style.get('image_limit')==0:
                raise ProviderError('NEEDS_REVIEW: selected figure conflicts with frozen preferences')
            source=sources[identifier]
            note['blocks'].append({'kind':'visual','visual_id':identifier,'evidence_ids':[identifier],
                'coverage_role':'full','label':str(source.get('title') or ''),
                'source_derivation':{'kind':'source_figure','source_evidence_id':identifier}})
            placed.add(identifier)
        if any(i in own and i not in placed for i in required):
            raise ProviderError('NEEDS_REVIEW: explicitly required source figure is missing')
        place_owned_blocks(note,request.source_pack['evidence'])
        choices={v['evidence_id']:v for v in note['visual_decisions']}
        coverage={v['evidence_id']:v for v in note['evidence_coverage']}
        for identifier in placed:
            choices[identifier]={'evidence_id':identifier,'decision':'include','inspected':True,
                'reason':decisions[identifier]['reason']}
            coverage[identifier]={'evidence_id':identifier,'disposition':'used',
                'reason':'Inspected source figure retained in its owning lesson.'}
        note['visual_decisions']=list(choices.values());note['evidence_coverage']=list(coverage.values())
    return result


def apply_notices(notes, units):
    result=notes
    owners={r['canonical_slide_id']:r['owner'] for r in result['organization']}
    for unit in units:
        for notice in unit.get('review_notices',[]):
            owner=owners.get(notice.get('evidence_id'),unit['lecture_id'])
            note=next((r['notes'] for r in result['lessons'] if r['lecture_id']==owner),None)
            if note is not None:attach_comments(note,[notice])
    return result


def project_units(units):
    originals={u['part_index']:u for u in units};projected=deepcopy(originals)
    for unit in units:
        for link in unit.get('source_links',[]):
            try:
                before=originals[link['part_index']]['blocks'][link['block_index']]
                target=projected[link['part_index']]['blocks'][link['block_index']]
            except (KeyError,IndexError) as error:raise ProviderError('Source-link target disappeared') from error
            if originals[link['part_index']]['lecture_id']!=unit['lecture_id'] or digest(before)!=link['base_sha256']:
                raise ProviderError('Source-link target changed after review')
            if link['evidence_id'] not in target['evidence_ids']:target['evidence_ids'].append(link['evidence_id'])
        additions=unit.get('coverage_repairs',[])
        if additions:
            blocks=projected[unit['part_index']]['blocks']
            footer=next((i for i,b in enumerate(blocks) if b['kind'] in {'qa','recap'}),len(blocks))
            blocks[footer:footer]=deepcopy(additions)
    return [projected[u['part_index']] for u in units]


def primary_positions(blocks, identifier):
    return [i for i,b in enumerate(blocks) if b['kind'] in NARRATIVE
            and b.get('coverage_role') in {'full','supplement'} and identifier in b.get('evidence_ids',[])]


def validate_supplement(note, identifier, anchors):
    """The same primary-topic interpretation used by the placement pass."""
    positions=primary_positions(note['blocks'],identifier)
    if not positions:raise ProviderError('NEEDS_REVIEW: supplementary source has no content')
    for direction,anchor in anchors.items():
        points=primary_positions(note['blocks'],anchor)
        if not points or (direction=='before' and max(positions)>=min(points)) or (direction=='after' and min(positions)<=max(points)):
            raise ProviderError('NEEDS_REVIEW: supplementary source is outside its topic anchors')
