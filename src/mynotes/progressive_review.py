"""Bounded risk review: one response approves, patches or retains a problem."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import re
import unicodedata
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


def _relevant_uncertainty(block_text, rows):
    """Select uncertainty linked to a claim, not a flagged unrelated ASR aside.

    This is a local risk heuristic, not semantic certification. Explicit source
    uncertainty codes remain unconditional; digits/examples/causal claims retain
    their independent risk checks.
    """
    stop=set('this that these those with from which what where when while there their they have been does doing about some many other because also into such than then them more most very could would should must will each only whether under over through without between questo questa questi queste della delle dello degli nella nelle sono come anche quando quindi perché'.split())
    def words(text):
        text=re.sub(r'\[CHECK[^\]]*\]','',text,flags=re.I)
        return {w for w in re.findall(r'\w+',text.casefold()) if len(w)>3 and w not in stop}
    def content(row):return str(row.get('content',row.get('text','')))
    flagged=[row for row in rows if row.get('needs_review') is True]
    reliable=words(' '.join(content(row) for row in rows if not row.get('needs_review')))
    claims=words(block_text)
    for row in flagged:
        uncertain=words(content(row))
        alternative=str(row.get('alternative') or row.get('asr_alternative') or '')
        negations=lambda text:bool(re.search(r"\b(?:no|not|never|without|cannot|non|mai|senza)\b|\b\w+n['’]t\b",text.casefold()))
        if alternative and len(uncertain & claims)>=2 and negations(alternative)!=negations(content(row)):
            return True
        if len(uncertain)>=2 and (not reliable or len((uncertain & claims)-reliable)>=2):
            return True
    return False


def risks(block, evidence, style, quality, *, policy=1):
    if quality == 'base':
        return []
    if quality in {'approfondito', 'revisione'}:
        return ['extended_source_review']
    kind = block.get('kind')
    content={k:block[k] for k in ('text','question','answer','items','rows','expression') if k in block}
    if policy>=5:
        from .evidence_refs import without_source_citations
        def clean(value):
            if isinstance(value,str):return without_source_citations(value,evidence)
            if isinstance(value,list):return [clean(v) for v in value]
            return value
        content={k:clean(v) for k,v in content.items()}
    text = json.dumps(content, ensure_ascii=False)
    source = ' '.join(str(evidence.get(i, {}).get('content', evidence.get(i, {}).get('text',''))) for i in block.get('evidence_ids', []))
    result = []
    if kind in {'formula','table','visual'}: result.append(kind)
    if re.search(r'\d|[%€$£]', text) or (policy>=5 and re.search(r'\b(?:hundreds?|thousands?|millions?|billions?|cento|mille|milioni)\b',text,re.I)): result.append('numbers_or_dates')
    if kind == 'recap': result.append('recap_language_and_summary')
    if re.search(r'\b(always|never|inevitab\w*|proves?|theorem\w*|legally|liab\w*|causes|caused|sempre|mai|dimostr\w*|teorem\w*|legalmente|provoca\w*)\b|\b(leads? to|results? in|strict liability)\b|\b(show\w*|establish\w*).{0,50}\bcausal', text, re.I):
        result.append('strong_causal_or_normative_claim')
    names = re.findall(r'\b(?:[A-Z][A-Za-zÀ-ÿ’-]+(?:\s+[A-Z][A-Za-zÀ-ÿ’-]+)+|[A-Z]{2,})\b', text)
    if policy>=5:
        def canonical(value):
            value=re.sub(r'\[\[term:([^\]]+)\]\]',r'\1',value)
            return re.sub(r'\s+',' ',re.sub(r'[-–—]', ' ',unicodedata.normalize('NFKC',value).casefold().replace('’',"'")))
        named_source=canonical(source)
        unmatched=any(not re.search(r'(?<!\w)'+re.escape(canonical(name))+r'(?!\w)',named_source) for name in names)
    else:unmatched=any(name.casefold() not in source.casefold() for name in names)
    if kind not in {'heading','overview'} and unmatched:result.append('unmatched_name_or_acronym')
    if any(evidence.get(i, {}).get('uncertainty_codes') for i in block.get('evidence_ids', [])):
        result.append('source_uncertainty')
    if policy >= 2 and kind not in {'heading','overview'}:
        if re.search(r'\bdirect(?:ly)? (?:consequences|caus\w*)\b|\b(?:in order to|so as to|to prevent)\b',text,re.I):
            result.append('strong_causal_or_normative_claim')
        rows=[evidence.get(i,{}) for i in block.get('evidence_ids', [])]
        if (_relevant_uncertainty(text,rows) if policy>=3 else any(row.get('needs_review') is True for row in rows)):
            result.append('source_uncertainty')
        if re.search(r'\b(hundreds?|thousands?|millions?|billions?)\b|\b(one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty)\s+(seconds?|minutes?|hours?|days?|years?|percent)\b', text, re.I):
            result.append('numbers_or_dates')
        # Concrete examples can introduce unmentioned methods without capitals.
        # Select only examples with at least two substantive words absent from
        # their actual cited sources; ordinary terminology alone is insufficient.
        stop = set('such as including for example and or the a an of in to with come ad esempio e o il la un una dei delle'.split())
        source_words = set(re.findall(r'\w+', source.casefold()))
        examples = re.findall(r'\((?:such as|e\.g\.|including|for example|come|ad esempio)\s+([^)]{1,400})\)', text, re.I)
        if any(len({w for w in re.findall(r'\w+', example.casefold()) if len(w)>3 and w not in stop and w not in source_words})>=2 for example in examples):
            result.append('unmatched_specific_example')
    # UNKNOWN voice and term markup alone are deliberately not risk criteria.
    return result


def review_schema(request=None):
    block = deepcopy(notes_schema(compact=True)['properties']['blocks']['items'])
    block['properties']['coverage_role'] = {'type':'string','enum':['full','supplement','reference','new_insight']}
    block['properties']['prior_lecture_id'] = {'type':'string'}
    block['properties']['evidence_ids']['maxItems']=16
    for key in ('text','question','answer'):block['properties'][key]['maxLength']=6000
    visual = deepcopy(notes_schema()['properties']['visual_decisions']['items'])
    visual['properties']['content_kind'] = {'type':'string','enum':['text','table','formula','chart','diagram','decoration','uncertain']}
    visual['required'] += ['inspected','content_kind']
    result={'type':'object','additionalProperties':False,'properties':{
        'decisions':{'type':'array','minItems':1,'maxItems':MAX_REVIEW_BLOCKS,'items':{
            'type':'object','additionalProperties':False,'properties':{
                'block_id':{'type':'string'},'base_sha256':{'type':'string'},
                'status':{'type':'string','enum':['approved','patch','unresolved']},'reason':{'type':'string'},
                'blocks':{'type':'array','maxItems':2,'items':block}},
            'required':['block_id','base_sha256','status','reason','blocks']}},
        'visual_updates':{'type':'array','items':visual}},'required':['decisions','visual_updates']}
    if request is not None and request.batch_context.get('risk_policy_version',1)>=4:
        props=result['properties']['decisions']['items']['properties']
        props['severity']={'type':'string','enum':['warning','blocking']}
        props['issue_code']={'type':'string','enum':['source_uncertainty','minor_editorial','optional_visual_disagreement','missing_content','invalid_reference','core_content_error']}
        result['properties']['source_links']={'type':'array','maxItems':3,'items':{
            'type':'object','additionalProperties':False,'properties':{
                'evidence_id':{'type':'string'},'part_index':{'type':'integer'},'block_index':{'type':'integer'},
                'base_sha256':{'type':'string'},'reason':{'type':'string'}},
            'required':['evidence_id','part_index','block_index','base_sha256','reason']}}
    return result


def coverage_targets(request,unit,previous_units,occupied):
    """Ask about missing links/content, never invent source associations locally."""
    if (request.batch_context.get('risk_policy_version',1)<4 or not unit['lesson_complete']
            or request.requested.quality=='base'):return []
    units=[*previous_units,unit];lecture=unit['lecture_id'];body=[];covered=set()
    for part in units:
        if part['lecture_id']!=lecture:continue
        for index,block in enumerate(part['blocks']):
            if block['kind'] in {'paragraph','bullets','table','formula','visual'} and block.get('coverage_role') in {'full','supplement','new_insight'}:
                covered.update(block['evidence_ids'])
                if part is not unit or index not in occupied:
                    body.append({'part_index':part['part_index'],'block_index':index,'base_sha256':digest(block),'block':block})
    decisions={v['evidence_id']:v for part in units for v in part['visual_delta']}
    covered.update(i for i,v in decisions.items() if v['decision']=='include')
    covered.update(request.batch_context.get('required_visual_ids',[]))
    from .progressive_wire import source_resources
    sources={r['id']:r for r in request.source_pack['evidence']}
    def words(text):return set(re.findall(r'\w{4,}',str(text).casefold()))
    targets=[]
    for choice in [d for part in units for d in part['organization_delta']]:
        identifier=choice['canonical_slide_id']
        if choice['owner']!=lecture or identifier in covered or choice.get('structural_review'):continue
        source=sources[identifier]
        if decisions.get(identifier,{}).get('content_kind')=='text' and source_resources(source):continue
        ids=[identifier,*choice['evidence_ids']]
        block={'kind':'paragraph','text':source.get('content',source.get('text','')),
               'evidence_ids':list(dict.fromkeys(ids)),'coverage_role':'supplement'}
        candidates=sorted(body,key=lambda b:len(words(json.dumps(b['block'],ensure_ascii=False)) & words(block['text'])),reverse=True)[:3]
        targets.append({'block_id':'C:'+identifier,'base_sha256':digest(block),'block':block,
                        'reasons':['missing_source_link_or_content'],'source_link_candidates':deepcopy(candidates)})
    return targets


def context_anchors(request, targets, unit, previous_units=()):
    """Relevant narrative neighbors for extended checks, never a whole ledger."""
    if (request.requested.quality not in {'approfondito','revisione'}
            or request.batch_context.get('risk_policy_version',1)<3):
        return []
    entries=[]
    for prior in [*previous_units,unit]:
        for index,block in enumerate(prior['blocks']):
            entries.append({'lecture_id':prior['lecture_id'],'part_index':prior.get('part_index',0),
                            'block_index':index,'block':block})
    offset=len(entries)-len(unit['blocks'])
    target_positions={offset+int(t['block_id'][1:]) for t in targets if t['block_id'].startswith('B')}
    substantive={'paragraph','table','formula','visual'}
    selected={}
    def add(position,target):
        if position in target_positions:return
        if position not in selected and len(selected)==3:return
        anchor=selected.setdefault(position,{**deepcopy(entries[position]),'for_block_ids':[]})
        if target['block_id'] not in anchor['for_block_ids']:anchor['for_block_ids'].append(target['block_id'])
    for target in targets:
        if not target['block_id'].startswith('B'):continue
        position=offset+int(target['block_id'][1:])
        prior_lecture=target['block'].get('prior_lecture_id')
        if prior_lecture:
            linked=[i for i,e in enumerate(entries[:offset]) if e['lecture_id']==prior_lecture
                    and e['block']['kind'] in substantive]
            if linked:
                ids=set(target['block']['evidence_ids'])
                best=max(linked,key=lambda i:(len(ids & set(entries[i]['block']['evidence_ids'])),i))
                add(best,target)
        for positions in (range(position-1,-1,-1),range(position+1,len(entries))):
            neighbor=next((i for i in positions if entries[i]['block']['kind'] in substantive),None)
            if neighbor is not None:add(neighbor,target)
    return list(selected.values())


def review_task(request, targets, unit, previous_units=()):
    task={'unit_sha256':digest(unit),'targets':targets}
    anchors=context_anchors(request,targets,unit,previous_units)
    if anchors:task['context_anchors_sha256']=digest(anchors)
    return task


def graphic_ids(request, unit):
    sources={v['evidence_id']:v for v in request.source_pack.get('visuals',[])}
    return {v['evidence_id'] for v in unit['visual_delta']
        if v.get('content_kind') in {'chart','diagram','uncertain'}
        or sources.get(v['evidence_id'],{}).get('signals',{}).get('drawings',0)>12
        or sources.get(v['evidence_id'],{}).get('signals',{}).get('images',0)>0}


def packet_request(request, targets, unit, directory, *, previous_units=()):
    ids = {i for t in targets for i in t['block']['evidence_ids']}
    anchors=context_anchors(request,targets,unit,previous_units)
    ids.update(i for a in anchors for i in a['block']['evidence_ids'])
    selected = [deepcopy(r) for r in request.source_pack['evidence'] if r['id'] in ids]
    visuals = [deepcopy(v) for v in request.source_pack.get('visuals', []) if v['evidence_id'] in ids and v.get('image_path')]
    # Standard has no narrative anchors. Extended checks get only relevant
    # neighbors/explicit prior-lesson links, with their actual source evidence.
    context = {'progressive_protocol': 2, 'review_task': {'lecture_id': unit['lecture_id'], 'targets': targets,
        'visual_decisions': [v for v in unit['visual_delta'] if v['evidence_id'] in ids],
        'existing_visual_ids': [b['visual_id'] for b in unit['blocks'] if b.get('kind')=='visual']}}
    if request.batch_context.get('risk_policy_version', 1) >= 3:
        context['risk_policy_version'] = request.batch_context['risk_policy_version']
    if anchors:context['review_task']['context_anchors_not_independent_evidence']=anchors
    candidate = replace(request, phase='progressive_review', source_pack={
        'lecture_id': unit['lecture_id'], 'evidence': selected, 'visuals': visuals},
        batch_context=context, response_schema=review_schema(request), previous_notes=None, run_dir=directory,
        context_mode='thorough')
    if len(_context(candidate).encode()) + size(candidate.response_schema) > MAX_REVIEW_INPUT:
        raise ProviderError('NEEDS_REVIEW: one risk block has overly broad references; no sources truncated')
    # Preserve the original capacity/grouping check above, including for saved
    # reviews. Standard needs source text, not another screenshot of known text.
    known = request.batch_context.get('review_visual_classifications', {})
    graphic_targets = {i for target in targets if 'source_graphic_choice' in target['reasons']
                       for i in target['block']['evidence_ids']}
    if unit['visual_delta']:
        # A risk attached to one graphic is not a request to reopen every
        # already inspected text slide cited by that paragraph or overview.
        graphic_targets &= graphic_ids(request,unit)
    if request.requested.quality == 'standard' and known:
        visuals = [v for v in visuals if known.get(v['evidence_id']) != 'text'
                   or v['evidence_id'] in graphic_targets]
        candidate = replace(candidate, source_pack={**candidate.source_pack, 'visuals': visuals})
    return candidate


def packets(request, unit, directory, *, previous_units=()):
    evidence = {r['id']:r for r in request.source_pack['evidence']}
    graphics=graphic_ids(request,unit)
    def graphical(v):return v['evidence_id'] in graphics
    targets = []
    for index, block in enumerate(unit['blocks']):
        reasons = risks(block, evidence, request.style, request.requested.quality,
                        policy=request.batch_context.get('risk_policy_version',1))
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
    return group_targets(request,unit,targets,directory,previous_units=previous_units)


def group_targets(request,unit,targets,directory,*,previous_units=()):
    result = []; current = []
    for target in targets:
        attempt = current + [target]
        try:
            if len(attempt) > MAX_REVIEW_BLOCKS: raise ProviderError('packet full')
            packet_request(request, attempt, unit, directory, previous_units=previous_units)
        except ProviderError:
            if not current: raise
            result.append(current); current = [target]
            packet_request(request, current, unit, directory, previous_units=previous_units)
        else: current = attempt
    if current: result.append(current)
    return result


def replacement_blocks(request, original, decision):
    index=int(decision['block_id'][1:])
    placed={b.get('visual_id') for i,b in enumerate(original['blocks']) if i!=index and b.get('kind')=='visual'}
    blocks,_=expand(decision['blocks'],{r['id'] for r in request.source_pack['evidence']},origins(request))
    blocks=[b for b in blocks if not (b.get('kind')=='visual' and b.get('visual_id') in placed)]
    if not blocks:raise ProviderError('Risk correction cannot replace content with an already placed image')
    return blocks


def apply_review(request, original, targets, reply):
    if size(reply) > MAX_REVIEW_OUTPUT:
        raise ProviderError('NEEDS_REVIEW: risk response exceeds 24 KiB')
    validate_shape(reply,review_schema(request))
    if set(reply)-{'decisions','visual_updates','source_links'} or not {'decisions','visual_updates'}<=set(reply):
        raise ProviderError('Risk review changed its response contract')
    known = {t['block_id']: t for t in targets}; seen = set(); unit = deepcopy(original)
    notices=[];modern=request.batch_context.get('risk_policy_version',1)>=4
    for field in ('review_notices','source_links','coverage_repairs'):unit.pop(field,None)
    supplied = {identifier for target in targets for identifier in target['block']['evidence_ids']}
    replacements = {}
    for decision in reply['decisions']:
        key = decision.get('block_id'); target = known.get(key)
        if target is None or key in seen or decision.get('base_sha256') != target['base_sha256']:
            raise ProviderError('Risk review has a stale, duplicate or unrelated block')
        seen.add(key)
        if decision.get('status') == 'unresolved':
            if (modern and decision.get('severity')=='warning'
                    and decision.get('issue_code') in {'source_uncertainty','minor_editorial','optional_visual_disagreement'}):
                notices.append({'block_sha256':digest(target['block']),
                    'content_sha256':digest({k:v for k,v in target['block'].items() if k not in {'evidence_ids','review_comments'}}),
                    **({'evidence_id':key[2:]} if key.startswith('V:') else {}),
                    'reason':str(decision['reason']),'issue_code':decision['issue_code']})
                continue
            raise ProviderError('NEEDS_REVIEW: '+str(decision.get('reason','Unresolved source issue')))
        if decision.get('status') == 'approved':
            if decision.get('blocks'): raise ProviderError('Approval cannot change content')
            if key.startswith('C:') and not any(v['evidence_id']==key[2:] for v in reply.get('source_links',[])):
                raise ProviderError('NEEDS_REVIEW: coverage approval needs an explicit existing-explanation link')
            continue
        if decision.get('status') != 'patch' or not 1 <= len(decision.get('blocks', [])) <= 2:
            raise ProviderError('Risk correction needs one or two explicit replacement blocks')
        if key.startswith('V:'):
            raise ProviderError('NEEDS_REVIEW: graphic without a narrative anchor cannot be invented by a review')
        expanded, _ = expand(decision['blocks'], {r['id'] for r in request.source_pack['evidence']}, origins(request))
        allowed = supplied
        for block in expanded:
            if not block.get('evidence_ids') or set(block['evidence_ids']) - allowed:
                raise ProviderError('Risk correction cites sources outside its target')
        if key.startswith('C:'):
            for block in expanded:
                if block['kind'] not in {'paragraph','bullets'} or key[2:] not in block['evidence_ids']:
                    raise ProviderError('NEEDS_REVIEW: missing-content repair must explain its source page')
                block['source_derivation']={'kind':'coverage_repair','source_evidence_id':key[2:]}
            unit.setdefault('coverage_repairs',[]).extend(expanded)
        else:replacements[int(key[1:])] = replacement_blocks(request,original,decision)
    if seen != set(known): raise ProviderError('Risk review did not account for every target')
    # Indices are the raw-unit coordinates; patches never shift later targets.
    unit['blocks'] = [b for index, block in enumerate(original['blocks']) for b in replacements.get(index, [deepcopy(block)])]
    allowed_visuals = {i for t in targets for i in t['block']['evidence_ids'] if i.startswith('S')}
    decisions = {v['evidence_id']: v for v in unit['visual_delta']}
    for update in reply['visual_updates']:
        identifier = update.get('evidence_id')
        if identifier not in allowed_visuals or update.get('inspected') is not True:
            raise ProviderError('Risk visual update lacks an inspected target')
        old=decisions.get(identifier)
        if (modern and old and old.get('decision')=='include' and old.get('content_kind') in {'chart','diagram'}
                and update.get('decision')!='include'):
            notices.append({'evidence_id':identifier,'reason':str(update['reason']),
                            'issue_code':'optional_visual_disagreement'})
            continue  # Preserve the selected, inspected source; no silent omission.
        decisions[identifier] = deepcopy(update)
    unit['visual_delta'] = list(decisions.values())
    if notices:unit['review_notices']=notices
    links=[]
    for link in reply.get('source_links',[]):
        target=known.get('C:'+link['evidence_id'])
        approved=next((d for d in reply['decisions'] if d['block_id']=='C:'+link['evidence_id'] and d['status']=='approved'),None)
        candidate=next((c for c in target.get('source_link_candidates',[]) if
            all(c[k]==link[k] for k in ('part_index','block_index','base_sha256'))),None) if target else None
        if candidate is None or approved is None or not str(link['reason']).strip() or link['evidence_id'] in {v['evidence_id'] for v in links}:
            raise ProviderError('NEEDS_REVIEW: source link has a stale or unprovided explanation')
        links.append(deepcopy(link))
    if links:unit['source_links']=links
    return unit


def _review_pass(request,unit,root,invoke,tasks,*,previous_units=(),offset=0):
    directory = root / f'review-{unit["part_index"]:03d}'
    effective = deepcopy(unit); refs = []
    # Apply each packet in original coordinates, then combine by raw index.
    edits = {}; visual_updates = {}
    def resolve(item):
        number, targets = item
        packet_dir = directory / f'{number:03d}'
        if not getattr(request,'local_only',False):packet_dir.mkdir(parents=True, exist_ok=True)
        path = packet_dir / 'review.json'; task = review_task(request,targets,unit,previous_units)
        if path.exists():
            saved = json.loads(path.read_text())
            if saved['task'] != task: raise ProviderError('Required risk review context changed')
            if (file_sha256(Path(saved['attempt']))!=saved['attempt_sha256']
                    or json.loads(Path(saved['attempt']).read_text()).get('requested')!=asdict(request.requested)):
                raise ProviderError('Saved risk review transport changed')
            from .agy_progressive import verify_single
            response = verify_single(saved['attempt'], packet_dir, review_schema(request))
            if response != saved['response']: raise ProviderError('Saved risk review differs from SUCCESS')
        else:
            if getattr(request,'local_only',False):
                raise ProviderError('NEEDS_REVIEW: this packet lacks a saved result; no new model call permitted')
            if (packet_dir/'started.json').exists():
                raise ProviderError('NEEDS_REVIEW: this risk packet already consumed its automatic attempt')
            from .review_dispatch import cancel_event
            cancellation=cancel_event.get()
            if cancellation is not None and cancellation.is_set():
                raise ProviderError('Review cancelled before consuming an attempt')
            atomic_json_write(packet_dir/'started.json', task)
            response, attempt = invoke(packet_request(request, targets, unit, packet_dir,previous_units=previous_units))
            saved = {'task':task,'response':response,'attempt':str(attempt),'attempt_sha256':file_sha256(attempt)}
            atomic_json_write(path, saved)
        # Validate before releasing the slot; blocking replies stop dispatch.
        changed = apply_review(request, unit, targets, saved['response'])
        return path, saved, changed

    indexed = list(enumerate(tasks,start=offset))
    if request.batch_context.get('risk_policy_version',1)>=5 and not getattr(request,'local_only',False):
        from .review_dispatch import dispatch
        def resources(item):
            return {key for target in item[1] for key in [
                target['block_id'], *['visual:'+i for i in target['block'].get('evidence_ids',[]) if i.startswith('S')]]}
        resolved = dispatch(indexed, resolve, resources)
    else:
        resolved = map(resolve,indexed)
    for path, saved, changed in resolved:
        for decision in saved['response']['decisions']:
            if decision['status'] == 'patch' and decision['block_id'].startswith('B'):
                index = int(decision['block_id'][1:])
                edits[index] = replacement_blocks(request,unit,decision)
        updated={v['evidence_id'] for v in saved['response']['visual_updates']}
        merge_visual_updates(visual_updates,[v for v in changed['visual_delta'] if v['evidence_id'] in updated])
        if changed.get('review_notices'):effective.setdefault('review_notices',[]).extend(changed['review_notices'])
        for field in ('source_links','coverage_repairs'):
            if changed.get(field):effective.setdefault(field,[]).extend(changed[field])
        refs.append({'path':str(path),'sha256':file_sha256(path)})
    effective['blocks'] = [b for i,block in enumerate(unit['blocks']) for b in edits.get(i,[deepcopy(block)])]
    effective['visual_delta'] = [visual_updates.pop(v['evidence_id'],v) for v in unit['visual_delta']]
    effective['visual_delta'].extend(visual_updates.values())
    return effective, refs


def review_unit(request, unit, root, invoke, *, inspected_visuals=None, previous_units=()):
    """One content pass, then missing-source closure on its actual saved prose."""
    if inspected_visuals:
        request=replace(request,batch_context={**request.batch_context,
            'review_visual_classifications':{v['evidence_id']:v['content_kind'] for v in inspected_visuals}})
    directory=root/f'review-{unit["part_index"]:03d}'
    tasks=packets(request,unit,directory,previous_units=previous_units)
    effective,refs=_review_pass(request,unit,root,invoke,tasks,previous_units=previous_units)
    missing=coverage_targets(request,effective,previous_units,set())
    if missing:
        closure=group_targets(request,effective,missing,directory,previous_units=previous_units)
        effective,more=_review_pass(request,effective,root,invoke,closure,previous_units=previous_units,offset=len(refs))
        refs.extend(more)
    return effective,refs
