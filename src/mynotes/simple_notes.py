"""One joint writing turn; derived local layout never starts a reviewer."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
from .providers import (ProviderError, ProviderResponse, _decode_agy,
    _normalize_transport_notes, _prompt, _save_attempt, _stage_agy_context,
    _stage_agy_writer, provider_environment, _reported_settings, _jsonl_events)
from .execution import AttemptStart, prevent_idle_sleep
from .utils import file_sha256


def certified(record, request):
    if record.get('error') or record.get('returncode')!=0 or record.get('requested')!=asdict(request.requested):
        raise ProviderError('Joint writing transport failed or requested settings changed')
    results=[e['result'] for e in _jsonl_events(record['stdout']) if e.get('event')=='result']
    if len(results)!=1 or results[0].get('status')!='SUCCESS':
        raise ProviderError('Joint writing needs exactly one successful result')
    return _normalize_transport_notes(_decode_agy(results[0]),request.response_schema,provider='agy')


def project(request, notes, *, access=None):
    """Recover explicit metadata, then use the common figure/layout assembler."""
    from .source_organization import reconcile
    from .note_assembly import compile_notes
    if access is not None:
        observed={r['sha256'] for r in access}
        available={v['evidence_id']:v for v in request.source_pack.get('visuals',[])}
        for lesson in notes.get('lessons',[]):
            for choice in lesson['notes']['visual_decisions']:
                if choice.get('decision')!='include' and choice.get('inspected') is not True:continue
                visual=available.get(choice['evidence_id'])
                if not visual or file_sha256(Path(visual['image_path'])) not in observed:
                    raise ProviderError('NEEDS_REVIEW: source inspection claim lacks observed full access: '+choice['evidence_id'])
        for choice in notes.get('organization',[]):
            structural=choice.get('structural_review')
            if structural and structural.get('image_sha256') not in observed:
                raise ProviderError('NEEDS_REVIEW: structural exemption lacks observed full-source inspection')
    result=deepcopy(notes)
    rows={row['evidence_id']:row['canonical_slide_id'] for row in request.batch_context['ownership']}
    aliases={canonical:identifier for identifier,canonical in rows.items()}
    organization=[{**choice,'canonical_slide_id':aliases.get(choice['canonical_slide_id'],choice['canonical_slide_id'])}
                  for choice in result.get('organization',[])]
    parts=[{'lecture_id':row['lecture_id'],'blocks':row['notes']['blocks'],
            'organization_delta':[c for c in organization if c['owner']==row['lecture_id']]}
           for row in result['lessons']]
    parts=reconcile(parts,request)
    result['organization']=[c for part in parts for c in part['organization_delta']]
    decisions={v['evidence_id']:v for row in result['lessons'] for v in row['notes']['visual_decisions']}
    for row in result['lessons']:
        for visual in row['notes']['visual_decisions']:visual.pop('content_kind',None)
    result=compile_notes(request,result,decisions)
    if request.requested.quality=='standard':
        from .progressive_review import risks,digest
        from .note_assembly import attach_comments
        evidence={row['id']:row for row in request.source_pack['evidence']}
        for row in result['lessons']:
            for block in row['notes']['blocks']:
                selected=risks(block,evidence,request.style,'standard',policy=5)
                if 'source_uncertainty' in selected:
                    attach_comments(row['notes'],[{'block_sha256':digest(block),'issue_code':'source_uncertainty',
                        'reason':'Le fonti collegate contengono un’incertezza pertinente. Standard esegue controlli locali, senza verifica semantica del modello.'}])
    result['organization']=[{**c,'canonical_slide_id':rows.get(c['canonical_slide_id'],c['canonical_slide_id'])} for c in result['organization']]
    return result


def previews(request):
    """Bounded contact sheets for selection; never certify a full inspection."""
    from PIL import Image,ImageOps,ImageDraw
    visuals=request.source_pack.get('visuals',[]);sheets=[]
    for offset in range(0,len(visuals),12):
        members=visuals[offset:offset+12]
        sheet=Image.new('RGB',(1200,900),'white');draw=ImageDraw.Draw(sheet)
        for index,visual in enumerate(members):
            with Image.open(visual['image_path']) as source:
                thumb=ImageOps.contain(source.convert('RGB'),(390,190))
            x=(index%3)*400;y=(index//3)*225
            sheet.paste(thumb,(x,y+22));draw.text((x+6,y+4),visual['evidence_id'],fill='black')
        path=request.run_dir/f'preview-{offset//12:03d}.png';sheet.save(path)
        sheets.append({'path':str(path),'sha256':file_sha256(path),'members':[
            {'evidence_id':v['evidence_id'],'source_sha256':file_sha256(Path(v['image_path']))} for v in members]})
    return sheets


def generate(request):
    from .agy_parts import run_session
    from .agy_progressive import command, observed_access, stage_images
    request=stage_images(request)
    root=request.run_dir;root.mkdir(parents=True,exist_ok=True)
    _stage_agy_writer(root)
    context=_stage_agy_context(request,root)
    sheets=previews(request)
    context+='\nGrouped previews for selection only: '+json.dumps(sheets)+'\nInspect grouped previews once. Open full image_path for included figures, numerical visual data, structural exemptions and uncertain pages. For text/decorative pages assessed only in previews, return exclude with inspected=false. Preview access never means full-source inspection.'
    args=command(request,request.response_schema,root/'joint-provider.log')
    from .visual_memory import certificate_access
    access=certificate_access(request.batch_context.get('cached_visual_inspections',[]),asdict(request.requested))
    answer={};envelopes=[];completed=None;started=AttemptStart()
    def observe(event):
        row=observed_access(event,root)
        if row:access.append(row)
    def accept(envelope):
        if envelopes:raise ProviderError('Joint writing cannot start another turn')
        envelopes.append(envelope)
        if envelope.get('status')!='SUCCESS':raise ProviderError('Joint writer returned ERROR; retained without retry')
        answer.update(_normalize_transport_notes(_decode_agy(envelope),request.response_schema,provider='agy'))
        return None
    try:
        with prevent_idle_sleep():
            completed=run_session(args,_prompt(request,context=context),accept,cwd=str(root),
                env={**provider_environment(),'MYNOTES_GENERATION_WORKER':'1'},on_event=observe,
                abort_errors=True,log_file=root/'joint-provider.log')
        if completed.returncode or len(envelopes)!=1:raise ProviderError('Joint writing did not complete')
        attempt=_save_attempt(request,command=args,completed=completed,started=started,
            envelope={'transport':'agy_joint_v1','status':'SUCCESS','source_access':access,'preview_sheets':sheets})
        model,effort=_reported_settings(envelopes)
        return ProviderResponse(answer,'agy',model,effort,envelopes[0].get('usage',{}),str(attempt))
    except BaseException as error:
        if hasattr(error,'output'):
            out=error.output;err=getattr(error,'stderr','')
            completed=subprocess.CompletedProcess(args,None,out.decode(errors='replace') if isinstance(out,bytes) else out,err.decode(errors='replace') if isinstance(err,bytes) else err)
        _save_attempt(request,command=args,completed=completed,started=started,error=str(error),
            envelope={'transport':'agy_joint_v1','status':'ERROR','source_access':access,'preview_sheets':sheets})
        raise
