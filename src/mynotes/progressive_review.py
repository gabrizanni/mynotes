"""Bounded risk review: one response approves, patches or retains a problem."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import re
from pathlib import Path

from .providers import ProviderError, _context, notes_schema
from .progressive_wire import MAX_REVIEW_BLOCKS, MAX_REVIEW_INPUT, MAX_REVIEW_OUTPUT, expand, origins, size, validate_shape
from .utils import atomic_json_write, file_sha256


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def merge_visual_updates(known, updates):
    for update in updates:
        identifier=update['evidence_id'];old=known.get(identifier)
        if old and any(old.get(k)!=update.get(k) for k in ('decision','inspected','content_kind')):
            raise ProviderError('NEEDS_REVIEW: risk packets disagree on a source graphic')
        if old and old['reason']!=update['reason']:
            update={**update,'reason':old['reason']+'; '+update['reason']}
        known[identifier]=update


def risks(block, evidence, style, quality):
    if quality == 'base':
        return []
    if quality in {'approfondito', 'revisione'}:
        return ['extended_source_review']
    kind = block.get('kind')
    text = json.dumps({k: block[k] for k in ('text','question','answer','items','rows','expression') if k in block}, ensure_ascii=False)
    source = ' '.join(str(evidence.get(i, {}).get('content', evidence.get(i, {}).get('text',''))) for i in block.get('evidence_ids', []))
    result = []
    if kind in {'formula','table','visual'}: result.append(kind)
    if re.search(r'\d|[%€$£]', text): result.append('numbers_or_dates')
    if kind == 'recap': result.append('recap_language_and_summary')
    if re.search(r'\b(always|never|inevitab\w*|proves?|theorem\w*|legally|liab\w*|causes|caused|sempre|mai|dimostr\w*|teorem\w*|legalmente|provoca\w*)\b|\b(leads? to|results? in|strict liability)\b|\b(show\w*|establish\w*).{0,50}\bcausal', text, re.I):
        result.append('strong_causal_or_normative_claim')
    names = re.findall(r'\b(?:[A-Z][A-Za-zÀ-ÿ’-]+(?:\s+[A-Z][A-Za-zÀ-ÿ’-]+)+|[A-Z]{2,})\b', text)
    if kind not in {'heading','overview'} and any(name.casefold() not in source.casefold() for name in names): result.append('unmatched_name_or_acronym')
    if any(evidence.get(i, {}).get('uncertainty_codes') for i in block.get('evidence_ids', [])):
        result.append('source_uncertainty')
    # UNKNOWN voice and term markup alone are deliberately not risk criteria.
    return result


def review_schema():
    block = deepcopy(notes_schema(compact=True)['properties']['blocks']['items'])
    block['properties']['coverage_role'] = {'type':'string','enum':['full','supplement','reference','new_insight']}
    block['properties']['prior_lecture_id'] = {'type':'string'}
    block['properties']['evidence_ids']['maxItems']=16
    for key in ('text','question','answer'):block['properties'][key]['maxLength']=6000
    visual = deepcopy(notes_schema()['properties']['visual_decisions']['items'])
    visual['properties']['content_kind'] = {'type':'string','enum':['text','table','formula','chart','diagram','decoration','uncertain']}
    visual['required'] += ['inspected','content_kind']
    return {'type':'object','additionalProperties':False,'properties':{
        'decisions':{'type':'array','minItems':1,'maxItems':MAX_REVIEW_BLOCKS,'items':{
            'type':'object','additionalProperties':False,'properties':{
                'block_id':{'type':'string'},'base_sha256':{'type':'string'},
                'status':{'type':'string','enum':['approved','patch','unresolved']},'reason':{'type':'string'},
                'blocks':{'type':'array','maxItems':2,'items':block}},
            'required':['block_id','base_sha256','status','reason','blocks']}},
        'visual_updates':{'type':'array','items':visual}},'required':['decisions','visual_updates']}


def packet_request(request, targets, unit, directory):
    ids = {i for t in targets for i in t['block']['evidence_ids']}
    selected = [deepcopy(r) for r in request.source_pack['evidence'] if r['id'] in ids]
    visuals = [deepcopy(v) for v in request.source_pack.get('visuals', []) if v['evidence_id'] in ids and v.get('image_path')]
    # No whole-lesson anchors, continuity arrays or completed narrative.
    context = {'progressive_protocol': 2, 'review_task': {'lecture_id': unit['lecture_id'], 'targets': targets,
        'visual_decisions': [v for v in unit['visual_delta'] if v['evidence_id'] in ids]}}
    candidate = replace(request, phase='progressive_review', source_pack={
        'lecture_id': unit['lecture_id'], 'evidence': selected, 'visuals': visuals},
        batch_context=context, response_schema=review_schema(), previous_notes=None, run_dir=directory,
        context_mode='thorough')
    if len(_context(candidate).encode()) + size(candidate.response_schema) > MAX_REVIEW_INPUT:
        raise ProviderError('NEEDS_REVIEW: one risk block has overly broad references; no sources truncated')
    return candidate


def packets(request, unit, directory):
    evidence = {r['id']:r for r in request.source_pack['evidence']}
    source_visuals = {v['evidence_id']:v for v in request.source_pack.get('visuals',[])}
    def graphical(v):
        signals=source_visuals.get(v['evidence_id'],{}).get('signals',{})
        return v.get('content_kind') in {'chart','diagram','uncertain'} or signals.get('drawings',0)>12 or signals.get('images',0)>0
    targets = []
    for index, block in enumerate(unit['blocks']):
        reasons = risks(block, evidence, request.style, request.requested.quality)
        # Every source graphic decision must be evaluated, even if excluded.
        linked = set(block['evidence_ids'])
        for v in unit['visual_delta']:
            if v['evidence_id'] in linked and graphical(v):
                reasons.append('source_graphic_choice')
        if reasons:
            targets.append({'block_id': f'B{index}', 'base_sha256': digest(block), 'block': deepcopy(block), 'reasons': sorted(set(reasons))})
    # A graphic classified before its narrative still needs a bounded check.
    for v in unit['visual_delta']:
        if request.requested.quality == 'base' or not graphical(v):
            continue
        if any(v['evidence_id'] in t['block']['evidence_ids'] for t in targets): continue
        synthetic = {'kind':'paragraph','text':v['reason'],'evidence_ids':[v['evidence_id']], 'coverage_role':'reference'}
        targets.append({'block_id':'V:'+v['evidence_id'], 'base_sha256':digest(synthetic), 'block':synthetic,
                        'reasons':['source_graphic_choice']})
    result = []; current = []
    for target in targets:
        attempt = current + [target]
        try:
            if len(attempt) > MAX_REVIEW_BLOCKS: raise ProviderError('packet full')
            packet_request(request, attempt, unit, directory)
        except ProviderError:
            if not current: raise
            result.append(current); current = [target]
            packet_request(request, current, unit, directory)
        else: current = attempt
    if current: result.append(current)
    return result


def apply_review(request, original, targets, reply):
    if size(reply) > MAX_REVIEW_OUTPUT:
        raise ProviderError('NEEDS_REVIEW: risk response exceeds 24 KiB')
    validate_shape(reply,review_schema())
    if set(reply) != {'decisions','visual_updates'}:
        raise ProviderError('Risk review changed its response contract')
    known = {t['block_id']: t for t in targets}; seen = set(); unit = deepcopy(original)
    replacements = {}
    for decision in reply['decisions']:
        key = decision.get('block_id'); target = known.get(key)
        if target is None or key in seen or decision.get('base_sha256') != target['base_sha256']:
            raise ProviderError('Risk review has a stale, duplicate or unrelated block')
        seen.add(key)
        if decision.get('status') == 'unresolved':
            raise ProviderError('NEEDS_REVIEW: '+str(decision.get('reason','Unresolved source issue')))
        if decision.get('status') == 'approved':
            if decision.get('blocks'): raise ProviderError('Approval cannot change content')
            continue
        if decision.get('status') != 'patch' or not 1 <= len(decision.get('blocks', [])) <= 2:
            raise ProviderError('Risk correction needs one or two explicit replacement blocks')
        if key.startswith('V:'):
            raise ProviderError('NEEDS_REVIEW: graphic without a narrative anchor cannot be invented by a review')
        expanded, _ = expand(decision['blocks'], {r['id'] for r in request.source_pack['evidence']}, origins(request))
        allowed = set(target['block']['evidence_ids'])
        for block in expanded:
            if not block.get('evidence_ids') or set(block['evidence_ids']) - allowed:
                raise ProviderError('Risk correction cites sources outside its target')
        replacements[int(key[1:])] = expanded
    if seen != set(known): raise ProviderError('Risk review did not account for every target')
    # Indices are the raw-unit coordinates; patches never shift later targets.
    unit['blocks'] = [b for index, block in enumerate(original['blocks']) for b in replacements.get(index, [deepcopy(block)])]
    allowed_visuals = {i for t in targets for i in t['block']['evidence_ids'] if i.startswith('S')}
    decisions = {v['evidence_id']: v for v in unit['visual_delta']}
    for update in reply['visual_updates']:
        identifier = update.get('evidence_id')
        if identifier not in allowed_visuals or identifier not in decisions or update.get('inspected') is not True:
            raise ProviderError('Risk visual update lacks an inspected target')
        decisions[identifier] = deepcopy(update)
    unit['visual_delta'] = list(decisions.values())
    return unit


def review_unit(request, unit, root, invoke):
    """Durable packet allowance is spent before invoking the provider."""
    directory = root / f'review-{unit["part_index"]:03d}'
    tasks = packets(request, unit, directory)
    effective = deepcopy(unit); refs = []
    # Apply each packet in original coordinates, then combine by raw index.
    edits = {}; visual_updates = {}
    for number, targets in enumerate(tasks):
        packet_dir = directory / f'{number:03d}'; packet_dir.mkdir(parents=True, exist_ok=True)
        path = packet_dir / 'review.json'; task = {'unit_sha256':digest(unit), 'targets':targets}
        if path.exists():
            saved = json.loads(path.read_text())
            if saved['task'] != task: raise ProviderError('Saved risk review inputs changed')
            from .agy_progressive import verify_single
            response = verify_single(saved['attempt'], packet_dir, review_schema())
            if response != saved['response']: raise ProviderError('Saved risk review differs from SUCCESS')
        else:
            if (packet_dir/'started.json').exists():
                raise ProviderError('NEEDS_REVIEW: this risk packet already consumed its automatic attempt')
            atomic_json_write(packet_dir/'started.json', task)
            response, attempt = invoke(packet_request(request, targets, unit, packet_dir))
            saved = {'task':task,'response':response,'attempt':str(attempt),'attempt_sha256':file_sha256(attempt)}
            atomic_json_write(path, saved)
        changed = apply_review(request, unit, targets, saved['response'])
        for decision in saved['response']['decisions']:
            if decision['status'] == 'patch':
                index = int(decision['block_id'][1:])
                edits[index], _ = expand(decision['blocks'], {r['id'] for r in request.source_pack['evidence']}, origins(request))
        merge_visual_updates(visual_updates,saved['response']['visual_updates'])
        refs.append({'path':str(path),'sha256':file_sha256(path)})
    effective['blocks'] = [b for i,block in enumerate(unit['blocks']) for b in edits.get(i,[deepcopy(block)])]
    effective['visual_delta'] = [visual_updates.get(v['evidence_id'],v) for v in unit['visual_delta']]
    return effective, refs
