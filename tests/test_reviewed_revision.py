"""Observed v2 defects use only still-unused block correction allowances."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from mynotes import agy_progressive as transport, notes_run, publication, batch_documents
from mynotes.progressive_review import digest
from mynotes.providers import _save_attempt, ProviderError
from mynotes.execution import AttemptStart
from mynotes.utils import file_sha256


def reviewer(calls, *, patch=False):
    def invoke(packet):
        calls.append(packet)
        targets=packet.batch_context['review_task']['targets'];decisions=[]
        for target in targets:
            block=deepcopy(target['block']);status='approved';blocks=[]
            if patch and block['kind'] in {'paragraph','qa'}:
                status='patch'
                for key in ('text','answer'):
                    if key in block:block[key]=block[key].replace(' (such as phantom networks or invented methods)','')
                blocks=[block]
            decisions.append({'block_id':target['block_id'],'base_sha256':target['base_sha256'],
                              'status':status,'reason':'Checked the specific observed source defect.','blocks':blocks})
        reply={'decisions':decisions,'visual_updates':[]};envelope={'status':'SUCCESS','response':json.dumps(reply)}
        attempt=_save_attempt(packet,command=['synthetic'],started=AttemptStart(),
            completed=subprocess.CompletedProcess(['synthetic'],0,json.dumps({'event':'result','result':envelope}),''),
            envelope={'status':'SUCCESS','source_access':[]})
        return reply,attempt
    return invoke


def finished(tmp_path,monkeypatch,*,already_patched=False):
    from test_progressive_v2 import install_fake_transport
    from test_autonomous_organization import prepared
    from test_publication import fake_render
    ws=prepared(tmp_path);cfg=ws.configuration_store().load()
    cfg['providers']['configured']['agy']={'model':'gemini-test','effort':'high'};ws.configuration_store().save(cfg)
    writing=[]
    def add_detail(notes,request):
        paragraph=next(b for b in notes['lessons'][0]['notes']['blocks'] if b['kind']=='paragraph')
        paragraph['text']+=' (such as phantom networks or invented methods)'
    install_fake_transport(monkeypatch,writing,transform=add_detail)
    monkeypatch.setattr(transport,'run_single',reviewer([],patch=already_patched))
    monkeypatch.setattr(publication,'_render_and_validate',fake_render);monkeypatch.setattr(batch_documents,'_render_and_validate',fake_render)
    from test_progressive_v2 import save_historical_job
    save_historical_job(ws)
    result=notes_run.run_notes(ws,'OPS',[1,2],provider='agy')
    assert result['status'] in {'COMPLETED','COMPLETED_WITH_WARNINGS'},result.get('problem')
    root=ws.runs_dir/'_batches'/result['batch_id'];base=next(root.rglob('returned-writing-*.json'))
    notes=json.loads(base.read_text())['payload']['response']['notes']
    row=notes['lessons'][0];index=next(i for i,b in enumerate(row['notes']['blocks']) if b['kind']=='paragraph')
    mapping=json.loads((base.parent/'request-intent.json').read_text())['payload']['maps'][row['lecture_id']]
    global_id=row['notes']['blocks'][index]['evidence_ids'][0];local=next(k for k,v in mapping.items() if v==global_id)
    review={'base_notes_sha256':digest(notes),'defects':[{'lecture_id':row['lecture_id'],'path':f'blocks[{index}]',
        'evidence_id':local,'reason':'Remove only the parenthetical methods not supplied in the source; keep the explanation.'}]}
    return ws,result,review,writing,base


def test_observed_defect_is_patched_once_without_whole_writing_and_reused(tmp_path,monkeypatch):
    ws,first,review,writing,base=finished(tmp_path,monkeypatch);original=file_sha256(base)
    calls=[];monkeypatch.setattr(transport,'run_single',reviewer(calls,patch=True))
    result=notes_run.run_notes(ws,'OPS',resume=first['batch_id'],review_defects=review)
    assert result['status'] in {'COMPLETED','COMPLETED_WITH_WARNINGS'},result.get('problem')
    assert len(calls)==len(writing)==1 and len(calls[0].batch_context['review_task']['targets'])==1
    candidate=json.loads((ws.runs_dir/'OPS-L01/lecture_notes.candidate.json').read_text())
    assert 'phantom networks' not in json.dumps(candidate['notes']) and candidate['reviewed_revision']
    assert file_sha256(base)==original and Path(first['publication']['output']).is_file()
    again=notes_run.run_notes(ws,'OPS',resume=first['batch_id'],review_defects=review)
    assert again['reused'] and len(calls)==len(writing)==1
    reopened=notes_run.run_notes(ws,'OPS',resume=first['batch_id'])
    assert reopened['reused'] and reopened['provider_invoked'] is False
    receipt=Path(candidate['reviewed_revision']['path']);receipt.write_text('{}')
    stopped=notes_run.run_notes(ws,'OPS',resume=first['batch_id'])
    assert stopped['status'] not in {'COMPLETED','COMPLETED_WITH_WARNINGS'} and len(calls)==1


def test_already_corrected_block_cannot_gain_another_attempt(tmp_path,monkeypatch):
    ws,first,review,writing,base=finished(tmp_path,monkeypatch,already_patched=True)
    monkeypatch.setattr(transport,'run_single',lambda *_:pytest.fail('This block already consumed its correction'))
    result=notes_run.run_notes(ws,'OPS',resume=first['batch_id'],review_defects=review)
    assert result['status']=='NEEDS_REVIEW' and 'already received' in result['problem']['message']
    assert len(writing)==1
    ordinary=notes_run.run_notes(ws,'OPS',resume=first['batch_id'])
    assert ordinary['status']=='NEEDS_REVIEW'
    assert notes_run.run_notes(ws,'OPS',resume=first['batch_id'])['status']=='NEEDS_REVIEW'


def test_failed_observed_review_never_restarts_or_silently_delivers_old_word(tmp_path,monkeypatch):
    ws,first,review,writing,_=finished(tmp_path,monkeypatch);calls=[]
    def failed(packet):
        calls.append(1);raise ProviderError('NEEDS_REVIEW: synthetic provider interruption')
    monkeypatch.setattr(transport,'run_single',failed)
    result=notes_run.run_notes(ws,'OPS',resume=first['batch_id'],review_defects=review)
    assert result['status']=='NEEDS_REVIEW' and len(calls)==1
    again=notes_run.run_notes(ws,'OPS',resume=first['batch_id'],review_defects=review)
    assert again['status']=='NEEDS_REVIEW' and len(calls)==1
    assert notes_run.run_notes(ws,'OPS',resume=first['batch_id'])['status']=='NEEDS_REVIEW'
    assert Path(first['publication']['output']).is_file() and len(writing)==1


def test_stale_review_does_not_consume_a_block_or_mutate_the_original(tmp_path,monkeypatch):
    ws,first,review,writing,base=finished(tmp_path,monkeypatch)
    review['base_notes_sha256']='stale';original=file_sha256(base)
    monkeypatch.setattr(transport,'run_single',lambda *_:pytest.fail('Invalid review cannot start a model'))
    stopped=notes_run.run_notes(ws,'OPS',resume=first['batch_id'],review_defects=review)
    assert stopped['status'] not in {'COMPLETED','COMPLETED_WITH_WARNINGS'}
    assert file_sha256(base)==original and not (base.parents[2]/'observed-review/intent.json').exists()
    assert notes_run.run_notes(ws,'OPS',resume=first['batch_id'])['status'] in {'COMPLETED','COMPLETED_WITH_WARNINGS'}


def test_observed_packet_contains_only_its_three_targets_not_the_whole_lesson(tmp_path,monkeypatch):
    from mynotes.providers import _context
    ws,first,review,writing,base=finished(tmp_path,monkeypatch)
    raw=json.loads(base.read_text())['payload']['response']['notes'];row=raw['lessons'][0]
    maps=json.loads((base.parent/'request-intent.json').read_text())['payload']['maps'][row['lecture_id']]
    indices=[i for i,b in enumerate(row['notes']['blocks']) if b['kind'] in {'paragraph','qa','recap'}][:3]
    assert len(indices)==3
    review['defects']=[{'lecture_id':row['lecture_id'],'path':f'blocks[{i}]',
        'evidence_id':next(k for k,v in maps.items() if v==row['notes']['blocks'][i]['evidence_ids'][0]),'reason':'A concrete source review.'} for i in indices]
    calls=[];invoke=reviewer(calls,patch=True)
    def checked(packet):
        targets=packet.batch_context['review_task']['targets']
        assert len(targets)==3 and len(_context(packet).encode())<64*1024
        assert 'blocks' not in packet.batch_context['review_task']
        assert len(targets)<len(row['notes']['blocks'])
        return invoke(packet)
    monkeypatch.setattr(transport,'run_single',checked)
    result=notes_run.run_notes(ws,'OPS',resume=first['batch_id'],review_defects=review)
    assert result['status'] in {'COMPLETED','COMPLETED_WITH_WARNINGS'},result.get('problem')
    assert len(calls)==len(writing)==1
