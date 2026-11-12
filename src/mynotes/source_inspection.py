"""Truthful full/preview evidence for the simple joint transport."""
from pathlib import Path
import re
from .utils import file_sha256
from .providers import ProviderError


def validate(notes, request, access, sheets):
    full={row['sha256'] for row in access}
    visuals={v['evidence_id']:v for v in request.source_pack.get('visuals',[])}
    sources={row['id']:row for row in request.source_pack['evidence']}
    canonical={row['canonical_slide_id']:row['evidence_id'] for row in request.batch_context['ownership']}
    preview=set()
    for sheet in sheets:
        path=Path(sheet['path'])
        if (sheet['sha256'] not in full or not path.is_file() or file_sha256(path)!=sheet['sha256']):continue
        for member in sheet['members']:
            visual=visuals.get(member['evidence_id'])
            if visual and file_sha256(Path(visual['image_path']))==member['source_sha256']:
                preview.add(member['evidence_id'])
    decisions={v['evidence_id']:v for lesson in notes['lessons'] for v in lesson['notes']['visual_decisions']}
    def opened(identifier):
        visual=visuals.get(identifier)
        return bool(visual and file_sha256(Path(visual['image_path'])) in full)
    notices=[]
    for identifier,choice in decisions.items():
        if opened(identifier):continue
        if (choice.get('decision')=='include' or choice.get('content_kind') in {'chart','diagram','table','formula','uncertain'}):
            raise ProviderError('NEEDS_REVIEW: source needs observed full access: '+identifier)
        if choice.get('inspected') is True:
            if identifier not in preview:
                raise ProviderError('NEEDS_REVIEW: source inspection claim lacks observed full access: '+identifier)
            choice['inspected']=False
            notices.append({'evidence_id':identifier,'issue_code':'optional_visual_disagreement',
                'reason':'Classificazione da anteprima osservata; nessuna ispezione completa dichiarata dal programma.'})
    for choice in notes.get('organization',[]):
        structural=choice.get('structural_review')
        if not structural:continue
        identifier=canonical.get(choice['canonical_slide_id'],choice['canonical_slide_id'])
        visual=visuals.get(identifier)
        if not visual or file_sha256(Path(visual['image_path']))!=structural['image_sha256']:
            raise ProviderError('NEEDS_REVIEW: structural source image changed')
        if not opened(identifier):
            text=str(sources.get(identifier,{}).get('content',sources.get(identifier,{}).get('text','')))
            classification=decisions.get(identifier,{})
            if (identifier not in preview or classification.get('content_kind') not in {'text','decoration'}
                or classification.get('decision')!='exclude' or identifier in request.batch_context.get('required_visual_ids',[])
                or len(text.split())>40 or re.search(r'\d\s*[%=]|https?://|\b(?:SELECT|import|def)\b',text)):
                raise ProviderError('NEEDS_REVIEW: structural exemption lacks observed source inspection')
            structural['reason']='Preview-observed structural classification, not a full-image inspection. '+structural['reason'].removeprefix('Inspected ')
        # A material page makes no spoken attribution; its own S alias is not a T turn.
        if choice.get('evidence_ids')==[identifier]:choice['evidence_ids']=[]
    return notices
