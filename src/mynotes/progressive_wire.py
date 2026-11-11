"""Versioned, lossless wire projection; saved source packs remain unchanged."""
from copy import deepcopy
import json
import re

from .providers import ProviderError, notes_schema

VERSION = 2
MAX_BLOCKS = 6
MAX_UNITS = 32  # Same 192-block ceiling as the historical 16 x 12 protocol.
MAX_UNIT_BYTES = 32 * 1024
MAX_REVIEW_BLOCKS = 3
MAX_REVIEW_INPUT = 64 * 1024
MAX_REVIEW_OUTPUT = 24 * 1024
FIELDS = {'id': 'i', 'content': 't', 'start_seconds': 's', 'end_seconds': 'e', 'page': 'p'}


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode())


def validate_shape(value, contract, path='$'):
    """Validate the portable subset locally, independently of finish-tool claims."""
    kinds = contract.get('type', [])
    kinds = [kinds] if isinstance(kinds,str) else kinds
    matches = {'object':isinstance(value,dict), 'array':isinstance(value,list), 'string':isinstance(value,str),
        'integer':type(value) is int, 'boolean':type(value) is bool, 'null':value is None}
    if kinds and not any(matches.get(kind,False) for kind in kinds):
        raise ProviderError(f'Progressive schema type mismatch at {path}')
    if 'enum' in contract and value not in contract['enum']:
        raise ProviderError(f'Progressive schema enum mismatch at {path}')
    if value is None:return
    if isinstance(value,str) and len(value)>contract.get('maxLength',float('inf')):
        raise ProviderError(f'Progressive schema text limit at {path}')
    if isinstance(value,dict):
        props=contract.get('properties',{})
        if set(contract.get('required',[]))-set(value) or (contract.get('additionalProperties') is False and set(value)-set(props)):
            raise ProviderError(f'Progressive schema fields mismatch at {path}')
        for key,item in value.items():validate_shape(item,props.get(key,{}),path+'.'+key)
    if isinstance(value,list):
        if len(value)<contract.get('minItems',0) or len(value)>contract.get('maxItems',float('inf')):
            raise ProviderError(f'Progressive schema array limit at {path}')
        for index,item in enumerate(value):validate_shape(item,contract.get('items',{}),f'{path}[{index}]')


def compact_sources(data):
    result = deepcopy(data)
    result['evidence_field_names'] = {v: k for k, v in FIELDS.items()}
    result['untrusted_evidence'] = True
    result['evidence'] = [{FIELDS.get(k, k): v for k, v in row.items() if k != 'untrusted_evidence'}
                          for row in result['evidence']]
    return result


def restore_sources(data):
    names = data['evidence_field_names']
    return [{**{names.get(k, k): v for k, v in row.items()}, 'untrusted_evidence': True}
            for row in data['evidence']]


def origins(request):
    found = {}
    for lesson, ids in request.batch_context['lesson_evidence'].items():
        for identifier in ids:
            if identifier.startswith('T'):
                found.setdefault(identifier, set()).add(lesson)
    return {key: sorted(value) for key, value in found.items()}


def expand(value, evidence, source_origins):
    """Expand only dense, same-origin T ranges, retaining a normalization proof."""
    valid = set(evidence); report = []
    numeric = {}
    for identifier in valid:
        match = re.fullmatch(r'T(\d+)', identifier)
        if match:
            numeric.setdefault(int(match[1]), []).append(identifier)
    def visit(item, field=None):
        if isinstance(item, dict):
            return {k: visit(v, k) for k, v in item.items()}
        if not isinstance(item, list):
            return deepcopy(item)
        if field != 'evidence_ids':
            return [visit(v) for v in item]
        result = []
        for identifier in item:
            if not isinstance(identifier, str): raise ProviderError('Source references must be strings')
            match = re.fullmatch(r'T(\d+)\.\.T(\d+)', identifier) if isinstance(identifier, str) else None
            if not match:
                if identifier not in valid:
                    raise ProviderError('Unknown progressive source reference')
                result.append(identifier); continue
            first, last = map(int, match.groups())
            if first > last or last - first >= len(valid):
                raise ProviderError('Invalid progressive reference interval')
            expanded = []
            for index in range(first, last + 1):
                candidates = numeric.get(index, [])
                if len(candidates) != 1:
                    raise ProviderError('Missing or ambiguous progressive reference interval')
                expanded.append(candidates[0])
            owners = {tuple(source_origins.get(i, [])) for i in expanded}
            if len(owners) != 1 or len(next(iter(owners))) != 1:
                raise ProviderError('Progressive reference interval crosses lesson origins')
            report.append({'range': identifier, 'expanded': expanded})
            result.extend(expanded)
        return list(dict.fromkeys(result))
    return visit(value), report


def schema(request):
    from .lesson_organization import integrated_schema
    block = deepcopy(notes_schema(compact=True)['properties']['blocks']['items'])
    block['properties']['coverage_role'] = {'type': 'string', 'enum': ['full', 'supplement', 'reference', 'new_insight']}
    block['properties']['prior_lecture_id'] = {'type': 'string'}
    block['properties']['evidence_ids']['maxItems'] = 16
    for key in ('text','question','answer'):
        block['properties'][key]['maxLength'] = 6000
    decisions = deepcopy(notes_schema()['properties']['visual_decisions']['items'])
    decisions['properties']['content_kind'] = {'type': 'string', 'enum': ['text', 'table', 'formula', 'chart', 'diagram', 'decoration', 'uncertain']}
    decisions['required'] += ['inspected', 'content_kind']
    decisions['properties']['reason']['maxLength'] = 480
    organization = integrated_schema()
    organization['items']['required'].append('owner')
    organization['items']['properties']['reason']['maxLength'] = 480
    organization['items']['properties']['evidence_ids']['maxItems'] = 12
    organization['items']['properties']['canonical_slide_id']['enum'] = [r['id'] for r in request.source_pack['evidence'] if r['id'].startswith('S')]
    return {'type': 'object', 'additionalProperties': False, 'properties': {
        'version': {'type': 'integer', 'enum': [VERSION]}, 'part_index': {'type': 'integer'},
        'lecture_id': {'type': 'string', 'enum': request.batch_context['lessons']},
        'header': {'type': ['object', 'null'], 'additionalProperties': False,
            'properties': {'title': {'type': 'string','maxLength':240}, 'summary': {'type': 'string','maxLength':1600}}, 'required': ['title', 'summary']},
        'blocks': {'type': 'array', 'minItems': 1, 'maxItems': MAX_BLOCKS, 'items': block},
        'organization_delta': organization,
        'visual_delta': {'type': 'array', 'items': decisions},
        'lesson_complete': {'type': 'boolean'}, 'complete': {'type': 'boolean'}, 'next_topic': {'type': 'string'}},
        'required': ['version', 'part_index', 'lecture_id', 'header', 'blocks', 'organization_delta', 'visual_delta',
                     'lesson_complete', 'complete', 'next_topic']}


def validate_unit(request, raw, previous):
    if size(raw) > MAX_UNIT_BYTES:
        raise ProviderError('Progressive unit exceeds 32 KiB; retained without retry')
    if raw.get('version') != VERSION or raw.get('part_index') != len(previous):
        raise ProviderError('Progressive unit version/order changed')
    blocks = raw.get('blocks')
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= MAX_BLOCKS:
        raise ProviderError('Progressive unit needs one to six blocks')
    validate_shape(raw,schema(request))
    valid = {r['id'] for r in request.source_pack['evidence']}
    unit, proof = expand(raw, valid, origins(request))
    settled_visuals={v['evidence_id']:v for p in previous for v in p['visual_delta']}
    new_visuals=[]
    for decision in unit['visual_delta']:
        old=settled_visuals.get(decision['evidence_id'])
        if old is not None:
            if any(old.get(k)!=decision.get(k) for k in ('decision','content_kind','inspected')):
                raise ProviderError('Progressive visual decision conflicts with settled inspection')
            proof.append({'kind':'unchanged_visual_decision','evidence_id':decision['evidence_id'],
                'prior':deepcopy(old),'returned':deepcopy(decision)})
        else:new_visuals.append(decision)
    unit['visual_delta']=new_visuals
    lessons = request.batch_context['lessons']; lecture = unit['lecture_id']
    if lecture not in lessons:
        raise ProviderError('Progressive unit has an unknown lesson')
    before = [p for p in previous if p['lecture_id'] == lecture]
    if before and before[-1]['lesson_complete']:
        raise ProviderError('Progressive unit reopened a completed lesson')
    if previous and lecture != previous[-1]['lecture_id']:
        if not previous[-1]['lesson_complete'] or lessons.index(lecture) != lessons.index(previous[-1]['lecture_id']) + 1:
            raise ProviderError('Progressive unit changed teaching order')
    elif not previous and lecture != lessons[0]:
        raise ProviderError('Progressive writing must start at the first selected lesson')
    if bool(unit['header']) == bool(before):
        raise ProviderError('Lesson header must occur exactly once')
    if not before and (not unit['header'].get('title') or not unit['header'].get('summary')):
        raise ProviderError('Progressive lesson header is empty')
    allowed_kinds = set(notes_schema()['properties']['blocks']['items']['properties']['kind']['enum'])
    for block in unit['blocks']:
        if block.get('kind') not in allowed_kinds or not block.get('evidence_ids'):
            raise ProviderError('Progressive block lacks content type or sources')
        if block['kind'] in {'paragraph', 'bullets', 'table', 'formula', 'visual'} and block.get('coverage_role') not in {'full','supplement','reference','new_insight'}:
            raise ProviderError('Progressive narrative lacks its source coverage role')
        prior_blocks=[b for p in previous for b in p['blocks']]
        if block['kind']=='overview' and before:
            raise ProviderError('Progressive writing repeated the lesson overview')
        def content(b):
            return {k:b[k] for k in ('kind','text','items','rows','expression','visual_id') if k in b}
        if block['kind'] in {'paragraph','bullets','table','formula','visual'} and block.get('coverage_role')!='reference':
            if any(content(block)==content(b) for b in prior_blocks):
                raise ProviderError('Progressive writing repeats accepted substantive content')
    prior_pages = {d['canonical_slide_id'] for p in previous for d in p['organization_delta']}
    prior_visuals = {d['evidence_id'] for p in previous for d in p['visual_delta']}
    for rows, key, seen in [(unit['organization_delta'], 'canonical_slide_id', prior_pages),
                            (unit['visual_delta'], 'evidence_id', prior_visuals)]:
        for row in rows:
            if row[key] in seen:
                raise ProviderError('Progressive unit repeats settled source metadata')
            seen.add(row[key])
    if unit['complete'] and (not unit['lesson_complete'] or lecture != lessons[-1] or unit['next_topic']):
        raise ProviderError('Progressive completion is inconsistent')
    if not unit['complete'] and not unit['next_topic'].strip():
        raise ProviderError('Incomplete progressive unit lacks its continuation')
    return unit, proof


def assemble(request, units):
    """Project accepted content and explicit decisions to the existing validator."""
    lessons = {}; organization = []; visuals = {}
    for unit in units:
        lecture = unit['lecture_id']
        if lecture not in lessons:
            lessons[lecture] = {**unit['header'], 'bullets': [], 'table': [], 'formula': None, 'formulas': [],
                'sections': [], 'evidence_coverage': [], 'visual_decisions': [], 'model_knowledge': [],
                'review_questions': [], 'blocks': []}
        lessons[lecture]['blocks'].extend(deepcopy(unit['blocks']))
        organization.extend(deepcopy(unit['organization_delta']))
        visuals.update({row['evidence_id']: row for row in unit['visual_delta']})
    for lecture, note in lessons.items():
        cited = {i for b in note['blocks'] for i in b['evidence_ids']}
        source_rows={r['id']:r for r in request.source_pack['evidence']}
        if any(u['lecture_id']==lecture and u['lesson_complete'] for u in units):
            for choice in organization:
                identifier=choice['canonical_slide_id'];source=source_rows.get(identifier,{})
                if choice['owner']!=lecture or identifier in cited or visuals.get(identifier,{}).get('content_kind')!='text':continue
                resource=source_resource(source)
                if resource is None:continue
                title,url=resource
                prefix='Integrazione dalle slide: ' if request.course.get('languages',{}).get('notes')=='it' else 'Supplementary slide material: '
                block={'kind':'paragraph','text':prefix+title+' — '+url,'evidence_ids':[identifier],
                    'coverage_role':'supplement','supplement_labelled':True,
                    'source_derivation':{'kind':'exact_source_resource','source_evidence_id':identifier}}
                # Keep the exact provided resource next to its source topic,
                # before subsequent material and the program-owned footer.
                before=[index for index,b in enumerate(note['blocks']) if b['kind'] in {'paragraph','bullets','table','formula','visual'}
                    and any(source_rows.get(i,{}).get('source_file')==source.get('source_file')
                        and 0<source_rows.get(i,{}).get('page',0)<source.get('page',0) for i in b['evidence_ids'])]
                position=max(before)+1 if before else next((i for i,b in enumerate(note['blocks']) if b['kind'] in {'qa','recap'}),len(note['blocks']))
                note['blocks'].insert(position,block);cited.add(identifier)
        placed = {b.get('visual_id') for b in note['blocks'] if b['kind'] == 'visual'}
        # This is a citation ledger, not a semantic correctness claim.
        note['evidence_coverage'] = [{'evidence_id': i, 'disposition': 'used',
            'reason': 'Linked by accepted ordered blocks; academic fidelity is checked separately.'} for i in sorted(cited) if not i.startswith('T')]
        for choice in organization:
            identifier = choice['canonical_slide_id']
            if choice['owner'] == lecture and choice['status'] == 'material' and identifier in {e['id'] for e in request.source_pack['evidence']} and identifier not in cited:
                note['evidence_coverage'].append({'evidence_id':identifier,'disposition':'excluded','reason':choice['reason']})
        for identifier, decision in visuals.items():
            note['visual_decisions'].append({'evidence_id': identifier,
                'decision': 'include' if identifier in placed else 'exclude',
                'inspected': decision['inspected'],
                'reason': decision['reason'] if decision['decision'] == 'exclude' or identifier in placed else 'No image placement in this lesson; shared source decision retained.'})
    return {'organization': organization, 'lessons': [{'lecture_id': l, 'notes': n} for l, n in lessons.items()]}


def source_resource(source):
    """Only a short title and one supplied video URL, never data or a lecture."""
    from urllib.parse import urlparse
    text=str(source.get('content',source.get('text',''))).strip()
    urls=re.findall(r'https?://[^\s<>]+',text)
    if len(urls)!=1 or urlparse(urls[0]).hostname not in {'youtube.com','www.youtube.com','youtu.be','vimeo.com','www.vimeo.com'}:
        return None
    title=text.replace(urls[0],'').strip()
    if source.get('page') is not None:title=re.sub(r'\s+'+re.escape(str(source['page']))+r'\s*$','',title).strip()
    if not title or len(title.split())>16 or re.search(r'\d|[▪•=\\$%]',title):return None
    return title,urls[0]


def validate_content(request, unit, previous):
    from .workflow import (_validate_inline_term_markers, _validate_explicit_ratio_percentages,
                           _validate_integer_table_totals, _validate_numeric_table_support,
                           _validate_candidate_notes)
    evidence = {r['id']:r for r in request.source_pack['evidence']}
    all_units = [*previous,unit]
    decisions = [v for u in all_units for v in u['visual_delta']]
    for block in unit['blocks']:
        kind = block['kind']
        for field in ('text','question','answer','label'):
            if isinstance(block.get(field),str):
                _validate_inline_term_markers(block[field]);_validate_explicit_ratio_percentages(block[field])
        if kind in {'heading','paragraph','recap'} and not block.get('text','').strip():
            raise ProviderError('NEEDS_REVIEW: empty content block')
        if kind=='heading' and block.get('level') not in {3,4}:raise ProviderError('NEEDS_REVIEW: invalid heading hierarchy')
        if kind in {'overview','bullets'} and not block.get('items'):raise ProviderError('NEEDS_REVIEW: empty list')
        if kind=='formula' and not block.get('expression','').strip():raise ProviderError('NEEDS_REVIEW: empty formula')
        if kind=='qa' and (not block.get('question','').strip() or not block.get('answer','').strip()):raise ProviderError('NEEDS_REVIEW: empty study answer')
        if kind=='table':
            rows=block.get('rows',[])
            if not rows or any(not isinstance(row,list) or len(row)!=len(rows[0]) for row in rows):raise ProviderError('NEEDS_REVIEW: malformed table')
            _validate_integer_table_totals(rows)
            _validate_numeric_table_support(block,evidence,source_pack=request.source_pack,decisions=decisions,source_root=request.run_dir)
    if unit['lesson_complete']:
        assembled=assemble(request,all_units)
        note=next(row['notes'] for row in assembled['lessons'] if row['lecture_id']==unit['lecture_id'])
        own={d['canonical_slide_id'] for d in assembled['organization'] if d['owner']==unit['lecture_id']}
        cited={i for b in note['blocks'] for i in b['evidence_ids']}
        selected=own|cited
        pack={**request.source_pack,'evidence':[r for r in request.source_pack['evidence'] if r['id'].startswith('T') or r['id'] in selected],
            'visuals':[v for v in request.source_pack.get('visuals',[]) if v['evidence_id'] in selected]}
        note['visual_decisions']=[v for v in note['visual_decisions'] if v['evidence_id'] in selected]
        _validate_candidate_notes(note,unit['lecture_id'],pack,source_root=request.run_dir,style=request.style)
        from .generation_diagnostics import recap_language_defects
        from .providers import ProviderResponse
        scoped={'lessons':[{'lecture_id':unit['lecture_id'],'notes':note}]}
        identity={i:i for i in evidence}
        if recap_language_defects(ProviderResponse(scoped,'','',''),{'style':request.style},[unit['lecture_id']],{unit['lecture_id']:identity}):
            raise ProviderError('NEEDS_REVIEW: recap language is inconsistent with the saved preference')
    if unit['complete']:
        assembled=assemble(request,all_units)
        available={r['id'] for r in request.source_pack['evidence'] if r['id'].startswith('S')}
        assigned={d['canonical_slide_id'] for d in assembled['organization']}
        # v2 tells the writer to use exact S aliases, expanded later by batch.
        if assigned!=available:raise ProviderError('NEEDS_REVIEW: final page organization is incomplete')
        candidates={v['evidence_id'] for v in request.source_pack.get('visuals',[]) if v.get('visual_decision')!='exclude'}
        if candidates!={v['evidence_id'] for v in decisions}:raise ProviderError('NEEDS_REVIEW: final visual decisions incomplete')
        placed={b.get('visual_id') for row in assembled['lessons'] for b in row['notes']['blocks'] if b['kind']=='visual'}
        if request.style.get('image_policy','essential')!='none' and request.style.get('image_limit')!=0:
            required={v['evidence_id'] for v in decisions if v.get('content_kind') in {'chart','diagram'} and (v.get('decision')=='include' or request.style.get('image_limit') is None)}
            if required-placed:raise ProviderError('NEEDS_REVIEW: inspected source graph/diagram has no image placement')
