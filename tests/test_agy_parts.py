from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from mynotes import agy_parts as a
from mynotes.providers import ProviderError, ProviderRequest, RequestedModel
from test_providers_quality import valid_notes


def request(tmp_path):
    return ProviderRequest('TEST-L01', {}, {}, RequestedModel('agy','test-model','high','standard'),
                           source_pack={'evidence':[{'id':'S001','content':'Source.'}]}, run_dir=tmp_path)


def portion(index=0, complete=False, blocks=None):
    notes=valid_notes();notes.update(bullets=[],table=[],formulas=[],sections=[],formula=None)
    notes['evidence_coverage']=[{'evidence_id':'S001','disposition':'used','reason':'Source concept.'}]
    notes['visual_decisions']=[]
    notes['blocks']=blocks or [{'kind':'paragraph','text':f'Explanation {index}.','evidence_ids':['S001']}]
    return {'part_index':index,'complete':complete,'next_topic':'' if complete else 'Next source concept','result':notes}


def test_portions_assemble_without_rewriting_or_duplicate_metadata(tmp_path):
    req=request(tmp_path);one=portion();two=portion(1,True)
    got=a.assemble(req,[one,two])
    assert got['blocks']==one['result']['blocks']+two['result']['blocks']
    assert got['evidence_coverage']==one['result']['evidence_coverage']
    assert one['result']['blocks']==portion()['result']['blocks']


@pytest.mark.parametrize('policy,incremental', [(None, False), ('0', False), ('1', True), ('auto', True)])
def test_adapter_defaults_to_single_call_and_retains_explicit_parts(tmp_path, monkeypatch, policy, incremental):
    from mynotes import providers
    req = request(tmp_path)
    if policy is None:
        monkeypatch.delenv('MYNOTES_AGY_PARTS', raising=False)
    else:
        monkeypatch.setenv('MYNOTES_AGY_PARTS', policy)
    monkeypatch.setattr(providers, 'provider_executable', lambda _: sys.executable)
    monkeypatch.setattr(a, 'generate_parts', lambda value: 'incremental')
    monkeypatch.setattr(providers, '_context', lambda value: 'x' * (providers.AGY_INLINE_CONTEXT_BYTES + 1))
    def single(*args, **kwargs): raise RuntimeError('single-turn')
    monkeypatch.setattr(providers, '_run_agy_worker', single)
    monkeypatch.setattr(providers, '_stage_agy_context', lambda *args, **kwargs: 'staged test context')
    if incremental:
        assert providers.AgyAdapter().generate(req) == 'incremental'
    else:
        with pytest.raises(RuntimeError, match='single-turn'): providers.AgyAdapter().generate(req)
    assert os.environ.get('MYNOTES_AGY_PARTS') == policy


@pytest.mark.parametrize('change',[
    lambda p:p.update(part_index=3),
    lambda p:p.update(next_topic="Still unfinished"),
    lambda p:p['result']['blocks'][0].update(evidence_ids=['S999']),
    lambda p:p['result']['blocks'][0].update(text='Explanation 0.'),
    lambda p:p['result']['evidence_coverage'][0].update(disposition='excluded'),
    lambda p:p['result'].update(blocks=p['result']['blocks']*13),
])
def test_bad_portions_never_become_a_complete_result(tmp_path,change):
    second=portion(1,True);change(second)
    with pytest.raises(ProviderError):a.assemble(request(tmp_path),[portion(),second])


def test_session_uses_one_process_and_preserves_separate_error_stream(tmp_path):
    code='''import json,sys,os
for n,line in enumerate(sys.stdin):
 print('diagnostic '+str(n),file=sys.stderr,flush=True)
 text=json.loads(line)['message']['content']
 print(json.dumps({'event':'result','result':{'status':'SUCCESS','value':text,'pid':os.getpid()}}),flush=True)
'''
    got=[]
    def accept(value):
        got.append(value)
        return 'second' if len(got)==1 else None
    result=a.run_session([sys.executable,'-u','-c',code],'first',accept,cwd=str(tmp_path),env=dict(os.environ),total_seconds=5)
    assert result.returncode==0 and len(got)==2
    assert got[0]['pid']==got[1]['pid']
    assert [x['value'] for x in got]==['first','second']
    assert 'diagnostic 1' in result.stderr and 'diagnostic' not in result.stdout


def test_session_timeout_stops_child_group_and_retains_accepted_prefix(tmp_path):
    pid=tmp_path/'child.pid'
    code='''import subprocess,sys,json,pathlib,time
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
pathlib.Path(sys.argv[1]).write_text(str(p.pid))
for line in sys.stdin:
 print(json.dumps({'event':'result','result':{'status':'SUCCESS'}}),flush=True)
 time.sleep(60)
'''
    got=[]
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        a.run_session([sys.executable,'-u','-c',code,str(pid)],'first',lambda e:got.append(e) or 'next',cwd=str(tmp_path),env=dict(os.environ),total_seconds=3,next_seconds=.3)
    assert len(got)==1 and b'SUCCESS' in caught.value.output
    status=subprocess.run(['ps','-p',pid.read_text(),'-o','stat='],capture_output=True,text=True).stdout.strip()
    assert not status or status.startswith('Z')


def _fake_session(results, commands):
    def run(command,prompt,accept,**kw):
        commands.append((command,prompt));events=[]
        for part in results:
            env={'status':'SUCCESS','conversation_id':'same-session','structured_output':part,'usage':{'input_tokens':100,'total_tokens':150}}
            events.append(json.dumps({'event':'result','result':env}))
            next_prompt=accept(env)
        return subprocess.CompletedProcess(command,0,'\n'.join(events)+'\n','')
    return run


def test_checkpoint_reuse_makes_no_second_provider_call(tmp_path,monkeypatch):
    req=request(tmp_path);commands=[]
    monkeypatch.setattr(a,'provider_executable',lambda _:sys.executable)
    monkeypatch.setattr(a,'run_session',_fake_session([portion(),portion(1,True)],commands))
    first=a.generate_parts(req);second=a.generate_parts(req)
    assert first.notes==second.notes and len(commands)==1
    assert first.usage['session_cumulative']['input_tokens']==100
    record=json.loads(Path(first.attempt_path).read_text())
    assert record['envelope']['transport']=='agy_parts_v1'
    assert len(json.loads(Path(record['envelope']['receipt']).read_text())['parts'])==2


def test_corrupt_checkpoint_stops_without_paid_call(tmp_path,monkeypatch):
    commands=[];monkeypatch.setattr(a,'provider_executable',lambda _:sys.executable)
    monkeypatch.setattr(a,'run_session',_fake_session([portion(0,True)],commands))
    a.generate_parts(request(tmp_path))
    p=next((tmp_path/'writing-parts').glob('*/part-000.json'));p.write_text('{}')
    with pytest.raises(ProviderError,match='modified'):a.generate_parts(request(tmp_path))
    assert len(commands)==1


def test_failed_turn_is_not_saved_but_successful_prefix_resumes_same_conversation(tmp_path,monkeypatch):
    req=request(tmp_path);commands=[]
    monkeypatch.setattr(a,'provider_executable',lambda _:sys.executable)
    def fail(command,prompt,accept,**kw):
        commands.append(command)
        accept({'status':'SUCCESS','conversation_id':'same-session','structured_output':portion(),'usage':{'input_tokens':100}})
        accept({'status':'ERROR','conversation_id':'same-session','structured_output':portion(1,True),'error':'EOF'})
    monkeypatch.setattr(a,'run_session',fail)
    with pytest.raises(ProviderError,match='ERROR'):a.generate_parts(req)
    assert len(list((tmp_path/'writing-parts').glob('*/part-*.json')))==1
    monkeypatch.setattr(a,'run_session',_fake_session([portion(1,True)],commands))
    response=a.generate_parts(req)
    command,prompt=commands[-1]
    assert command[command.index('--conversation')+1]=='same-session'
    assert 'Resume part_index 1' in prompt and len(response.notes['blocks'])==2


def test_local_rejection_is_retained_and_not_automatically_paid_again(tmp_path,monkeypatch):
    req=request(tmp_path);commands=[]
    monkeypatch.setattr(a,'provider_executable',lambda _:sys.executable)
    bad=portion();bad['result']['blocks'][0]['evidence_ids']=['S999']
    monkeypatch.setattr(a,'run_session',_fake_session([bad],commands))
    for _ in range(2):
        with pytest.raises(ProviderError):a.generate_parts(req)
    assert len(commands)==1


def test_schema_caps_each_portion_but_does_not_cap_explanation_length(tmp_path):
    schema=a.part_schema(request(tmp_path));blocks=schema['properties']['result']['properties']['blocks']
    assert blocks['maxItems']==a.BLOCKS_PER_PART
    assert 'maxLength' not in blocks['items']['properties']['text']


def test_receipt_rejects_modified_error_turn_or_wrong_final_response(tmp_path,monkeypatch):
    commands=[];monkeypatch.setattr(a,'provider_executable',lambda _:sys.executable)
    monkeypatch.setattr(a,'run_session',_fake_session([portion(0,True)],commands))
    response=a.generate_parts(request(tmp_path));record=json.loads(Path(response.attempt_path).read_text())
    a.verify_receipt(record,tmp_path)
    bad=deepcopy(record);bad['envelope']['status']='ERROR'
    with pytest.raises(ProviderError):a.verify_receipt(bad,tmp_path)
    bad=deepcopy(record);event=json.loads(bad['stdout']);event['result']['status']='ERROR';bad['stdout']=json.dumps(event)
    with pytest.raises(ProviderError):a.verify_receipt(bad,tmp_path)
    p=next((tmp_path/'writing-parts').glob('*/part-000.json'));p.write_text('{}')
    with pytest.raises(ProviderError):a.verify_receipt(record,tmp_path)


def test_changed_sources_start_a_distinct_context_without_overwriting_saved_parts(tmp_path,monkeypatch):
    commands=[];monkeypatch.setattr(a,'provider_executable',lambda _:sys.executable)
    monkeypatch.setattr(a,'run_session',_fake_session([portion(0,True)],commands))
    req=request(tmp_path);a.generate_parts(req)
    old={p:p.read_bytes() for p in (tmp_path/'writing-parts').rglob('*') if p.is_file()}
    newer=replace(req,source_pack={'evidence':[{'id':'S001','content':'Changed source.'}]})
    a.generate_parts(newer)
    assert len(commands)==2 and all(p.read_bytes()==b for p,b in old.items())


def test_batch_assembly_preserves_lecture_order_and_never_returns_to_earlier_lesson(tmp_path):
    req=replace(request(tmp_path),batch_context={'lessons':['TEST-L01','TEST-L02']})
    def batch(part,lecture):
        part['result']={'lessons':[{'lecture_id':lecture,'notes':part['result']}]};return part
    first=batch(portion(),'TEST-L01');second=batch(portion(1,True),'TEST-L02')
    got=a.assemble(req,[first,second]);assert [r['lecture_id'] for r in got['lessons']]==['TEST-L01','TEST-L02']
    third=batch(portion(2,True),'TEST-L01');second['complete']=False;second['next_topic']='Next lecture'
    with pytest.raises(ProviderError,match='earlier'):a.assemble(req,[first,second,third])


def test_word_loader_accepts_only_complete_unchanged_successful_parts(tmp_path,monkeypatch):
    from mynotes.workspace import Workspace
    from mynotes.utils import atomic_json_write,file_sha256
    from mynotes.publication import _load_candidate,PublicationError
    ws=Workspace.from_value(tmp_path/'private');ws.initialize();run=ws.runs_dir/'TEST-L01';run.mkdir(parents=True)
    req=replace(request(run),run_dir=run)
    commands=[];monkeypatch.setattr(a,'provider_executable',lambda _:sys.executable)
    monkeypatch.setattr(a,'run_session',_fake_session([portion(0,True)],commands))
    response=a.generate_parts(req)
    pack=run/'source_pack.json';atomic_json_write(pack,req.source_pack)
    atomic_json_write(run/'lecture_notes.candidate.json',{'notes':response.notes,'source_pack':str(pack),'source_pack_hash':file_sha256(pack),'shared_attempt_paths':[response.attempt_path]})
    assert _load_candidate(ws,'TEST-L01')[0]['notes']==response.notes
    part=next((run/'writing-parts').glob('*/part-000.json'));part.write_text('{}')
    with pytest.raises(PublicationError,match='cannot be verified'):_load_candidate(ws,'TEST-L01')


def test_final_batch_coverage_stays_strict_without_another_paid_write(tmp_path,monkeypatch):
    from mynotes import providers
    from mynotes.batch import generate_batch
    from mynotes.workflow import WorkflowError
    from test_batch_generation import workspace_and_deck,notes_for
    ws,_,selection=workspace_and_deck(tmp_path,lessons=1,slides=3)
    config=ws.configuration_store().load();config['providers']['configured']['agy']={'model':'test-model','effort':'high'};ws.configuration_store().save(config)
    commands=[];monkeypatch.setenv('MYNOTES_AGY_PARTS','1')
    monkeypatch.setattr(providers,'provider_executable',lambda _:sys.executable)
    monkeypatch.setattr(a,'provider_executable',lambda _:sys.executable)
    class Adapter:
        def generate(self,req):
            notes=notes_for(req,'OPS-L01')
            notes['blocks']=[b for b in notes['blocks'] if b.get('kind')!='paragraph' or 'S001' in b['evidence_ids']]
            part={'part_index':0,'complete':True,'next_topic':'','result':{'lessons':[{'lecture_id':'OPS-L01','notes':notes}]}}
            monkeypatch.setattr(a,'run_session',_fake_session([part],commands))
            return providers.AgyAdapter().generate(req)
    for _ in range(2):
        with pytest.raises(WorkflowError,match='omits the full explanation'):
            generate_batch(ws,'OPS',[1],selection=selection,provider='agy',provider_adapter=Adapter())
    assert len(commands)==1 and not list(ws.runs_dir.rglob('lecture_notes.candidate.json'))


def test_clean_parent_exit_is_not_mistaken_for_failed_response_when_child_holds_pipe(tmp_path):
    pid=tmp_path/'child.pid'
    code='''import subprocess,sys,json,pathlib
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
pathlib.Path(sys.argv[1]).write_text(str(p.pid))
line=sys.stdin.readline()
print(json.dumps({'event':'result','result':{'status':'SUCCESS'}}),flush=True)
'''
    start=time.monotonic()
    result=a.run_session([sys.executable,'-u','-c',code,str(pid)],'first',lambda e:None,cwd=str(tmp_path),env=dict(os.environ),total_seconds=3)
    assert result.returncode==0 and time.monotonic()-start<2
    status=subprocess.run(['ps','-p',pid.read_text(),'-o','stat='],capture_output=True,text=True).stdout.strip()
    assert not status or status.startswith('Z')
