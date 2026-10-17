"""Independent local checks, reported together without editing source content."""
from .evidence_refs import normalize_references, EvidenceReferenceError


def group_defects(response,plan,lectures,maps):
    from .batch import _remap_notes, NARRATIVE_KINDS
    defects=[]
    valid={value for mapping in maps.values() for value in mapping.values()}
    try:
        normalized,_=normalize_references(response.notes,valid)
    except EvidenceReferenceError as error:
        return [{'phase':'REFERENCES',**row} for row in error.defects]
    entries=normalized.get('lessons',[])
    for row in entries:
        lecture=row.get('lecture_id')
        if lecture not in lectures:continue
        notes=_remap_notes(row.get('notes',{}),maps[lecture]);pack=plan['packs'][lecture]
        evidence={r['id']:r for r in pack['evidence']}
        for i,block in enumerate(notes.get('blocks',[])):
            foreign=[]
            for identifier in block.get('evidence_ids',[]):
                canonical=evidence.get(identifier,{}).get('canonical_slide_id')
                choice=plan['ownership'].get(canonical,{})
                if choice.get('owner') and choice['owner']!=lecture:
                    foreign.append({'evidence_id':identifier,'owner':choice['owner']})
            if block.get('kind') not in NARRATIVE_KINDS and foreign:
                content=block.get('answer',block.get('text',' '.join(block.get('items',[]))))
                if len(str(content).split())>50:
                    defects.append({'phase':'CONTENT_VALIDATION','code':'CROSS_LESSON_SUMMARY_TOO_LONG',
                        'lecture_id':lecture,'path':f'blocks[{i}]','kind':block.get('kind'),
                        'word_count':len(str(content).split()),'sources':foreign,
                        'action':'Review source support and revise only this answer through a separately authorized correction; no automatic rewrite'})
    return defects


def speaker_warning(pack,focus):
    if focus!='professor':return None
    turns=[e for e in pack.get('evidence',[]) if e.get('id','').startswith('T')]
    roles=[str(e.get('speaker_role','UNKNOWN')).upper() for e in turns]
    if 'PROFESSOR' not in roles or any(role not in {'PROFESSOR','STUDENT'} for role in roles):
        return {'code':'PROFESSOR_NOT_ISOLATED','message':'La trascrizione non distingue tutte le voci. Il testo non è certificato come solo docente.'}
    return None


def source_read_audit(stdout):
    """Read recorded tool events, deduplicating event replay but not real reads."""
    import json
    events=[];seen_events=set();reads={};repeated=[]
    for line in stdout.splitlines():
        try:event=json.loads(line)
        except ValueError:continue
        step=event.get('step_update',{})
        if step.get('state')!='DONE' or step.get('tool_name')!='view_file':continue
        identity=(step.get('conversation_id'),step.get('step_index'))
        if identity in seen_events:continue
        seen_events.add(identity)
        parameters=step.get('tool_info',{}).get('parameters',{})
        key=json.dumps(parameters,sort_keys=True)
        row={'step_index':step.get('step_index'),'parameters':parameters,'reported_output':step.get('tool_info',{}).get('output'),
             'duration_seconds':step.get('duration_seconds')}
        events.append(row)
        if key in reads:repeated.append({'first_step':reads[key],'repeat_step':step.get('step_index'),'parameters':parameters})
        else:reads[key]=step.get('step_index')
    return {'view_file_calls':len(events),'unique_requests':len(reads),'exact_repeat_calls':len(repeated),'reads':events,'repeats':repeated,
            'interpretation':'Exact repeated parameters are measurable; overlapping ranges may be needed after truncated responses.'}
