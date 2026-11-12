"""Native finish constraints and image scope must match progressive instructions."""
from copy import deepcopy
from dataclasses import replace
import json
import re

import pytest

from mynotes import agy_progressive as a, progressive_review as r, progressive_wire as w
from test_progressive_v2 import request, unit


@pytest.mark.parametrize('review',[False,True])
def test_actual_finish_tool_accepts_compact_refs_without_changing_portable_contract(tmp_path,monkeypatch,review):
    req=request(tmp_path)
    portable=r.review_schema() if review else w.schema(req)
    original=deepcopy(portable)
    monkeypatch.setattr(a,'provider_executable',lambda _: '/fake/agy')
    monkeypatch.setattr(a,'provider_override_flags',lambda _: [])
    command=a.command(req,portable,tmp_path/'provider.log')
    native=json.loads(command[command.index('--json-schema')+1])
    block=(native['properties']['decisions']['items']['properties']['blocks']['items']
           if review else native['properties']['blocks']['items'])
    references=block['properties']['evidence_ids']
    for identifier in ['S001','T1234','T006..T010','T1..T2']:
        assert re.fullmatch(references['items']['pattern'],identifier)
    for identifier in ['T006..S010','Tbad','T006,T007']:
        assert not re.fullmatch(references['items']['pattern'],identifier)
    assert references['maxItems']==16
    assert portable==original  # Includes historical byte/capacity behavior.
    old_block=(portable['properties']['decisions']['items']['properties']['blocks']['items']
               if review else portable['properties']['blocks']['items'])
    assert not re.fullmatch(old_block['properties']['evidence_ids']['items']['pattern'],'T006..T010')


@pytest.mark.parametrize('classification,drawings,images,quality,expected',[
    ('text',0,0,'standard',1),
    (None,0,0,'standard',2),
    ('text',20,0,'standard',2),
    ('text',0,1,'standard',2),
    ('text',0,0,'approfondito',2),
])
def test_graphic_risk_does_not_reopen_unrelated_inspected_text_images(
        tmp_path,classification,drawings,images,quality,expected):
    req=request(tmp_path,quality)
    req.source_pack['visuals']=[
        {'evidence_id':'S001','image_path':str(tmp_path/'diagram.png'),'signals':{'drawings':20}},
        {'evidence_id':'S002','image_path':str(tmp_path/'text.png'),'signals':{'drawings':drawings,'images':images}},
    ]
    known={'S001':'diagram',**({'S002':classification} if classification else {})}
    req=replace(req,batch_context={**req.batch_context,'review_visual_classifications':known})
    raw=unit();raw['visual_delta']=[
        {'evidence_id':'S001','content_kind':'diagram','decision':'include','inspected':True,'reason':'Observed diagram.'},
        {'evidence_id':'S002','content_kind':classification or 'uncertain','decision':'exclude','inspected':True,'reason':'Observed source.'},
    ]
    block={'kind':'paragraph','text':'A diagram and its textual explanation.',
           'evidence_ids':['S001','S002','T001']}
    targets=[{'block_id':'B1','base_sha256':r.digest(block),'block':block,'reasons':['source_graphic_choice']}]
    packet=r.packet_request(req,targets,raw,tmp_path/'packet')
    assert len(packet.source_pack['visuals'])==expected
    assert packet.batch_context['review_task']['targets']==targets
    assert {row['id'] for row in packet.source_pack['evidence']}=={'S001','S002','T001'}
    unclassified=replace(req,batch_context={k:v for k,v in req.batch_context.items()
                                         if k!='review_visual_classifications'})
    assert r.packets(req,raw,tmp_path/'grouped')==r.packets(unclassified,raw,tmp_path/'grouped')


def test_unknown_graphic_scope_still_retains_the_requested_image(tmp_path):
    req=request(tmp_path)
    req.source_pack['visuals']=[{'evidence_id':'S001','image_path':str(tmp_path/'source.png')}]
    req=replace(req,batch_context={**req.batch_context,'review_visual_classifications':{'S001':'text'}})
    raw=unit();block=raw['blocks'][1]
    target={'block_id':'B1','base_sha256':r.digest(block),'block':block,'reasons':['source_graphic_choice']}
    packet=r.packet_request(req,[target],raw,tmp_path/'packet')
    assert len(packet.source_pack['visuals'])==1
