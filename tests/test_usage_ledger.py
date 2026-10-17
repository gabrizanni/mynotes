import json
import subprocess
import time
from pathlib import Path

import pytest

from mynotes.cli import main
from mynotes.providers import (AgyAdapter,ClaudeAdapter,CodexAdapter,ProviderError,ProviderQuotaError,
    ProviderRequest,ProviderResponse,RequestedModel,_save_attempt)
from mynotes.usage import lecture_usage
from mynotes.workspace import Workspace
from mynotes.workflow import WorkflowError,generate_candidate
from test_providers_quality import configured_workspace,source_pack,valid_notes


def request(root,provider='codex',phase='writing'):
    return ProviderRequest('OPS7-L01',{'name':'Public fixture'},{'transcript':'SYNTHETIC_PUBLIC_INPUT'},RequestedModel(provider,'','','standard'),run_dir=root,phase=phase)


def save_attempt(root,usage=None,provider='codex',error=None):
    stdout=json.dumps({'usage':usage}) if usage is not None else 'No structured usage was reported.'
    completed=subprocess.CompletedProcess(['/test/provider'],1 if error else 0,stdout,'')
    return _save_attempt(request(root,provider),command=['/test/provider'],completed=completed,started=time.monotonic(),error=error)


def snapshot(root):
    return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_all_attempts_include_failed_usage_and_do_not_double_count_cache(tmp_path):
    workspace=Workspace.from_value(tmp_path/'private');run=workspace.runs_dir/'OPS7-L01'
    save_attempt(run,{'input_tokens':10,'cached_input_tokens':4,'output_tokens':2,'total_tokens':12})
    save_attempt(run,{'input_tokens':5,'cached_input_tokens':1,'output_tokens':1,'total_tokens':6},error='transport failed')
    save_attempt(run,{'input_tokens':3,'output_tokens':1},provider='agy')
    before=snapshot(workspace.root)
    report=lecture_usage(workspace,'OPS7-L01')
    assert snapshot(workspace.root)==before and not workspace.database_path.exists()
    codex=report['providers']['codex']
    assert codex['attempts']==2 and codex['failed']==1 and codex['returned']==1
    assert codex['reported_totals']=={'input_tokens':15,'cached_input_tokens':5,'output_tokens':3,'total_tokens':18}
    assert report['providers']['agy']['reported_totals']=={'input_tokens':3,'output_tokens':1}
    assert not report['partial']
    assert 'billing claim' in report['scope']


def test_unknown_usage_is_not_zero_and_unfamiliar_raw_counters_survive(tmp_path):
    workspace=Workspace.from_value(tmp_path/'private');run=workspace.runs_dir/'OPS7-L01'
    save_attempt(run,None,error='unstructured provider failure')
    unknown={'future_provider_counter':17,'modelUsage':{'a-model':{'inputTokens':3}},'total_cost_usd':0.01}
    save_attempt(run,unknown)
    report=lecture_usage(workspace,'OPS7-L01')
    assert report['attempts'][0]['reported_usage'] is None or report['attempts'][1]['reported_usage'] is None
    assert unknown in [item['reported_usage'] for item in report['attempts']]
    assert report['providers']['codex']['reported_totals']=={}
    assert report['providers']['codex']['unknown_usage_attempts']==1 and report['partial']


def test_partial_metric_subtotals_have_coverage_warning(tmp_path):
    workspace=Workspace.from_value(tmp_path/'private');run=workspace.runs_dir/'OPS7-L01'
    save_attempt(run,{'input_tokens':9,'output_tokens':2});save_attempt(run,{'output_tokens':3})
    report=lecture_usage(workspace,'OPS7-L01')
    assert report['providers']['codex']['reported_totals']=={'input_tokens':9,'output_tokens':5}
    assert report['providers']['codex']['counter_reporting_attempts']=={'input_tokens':1,'output_tokens':2}
    assert report['partial'] and any('part of the attempts' in x['reason'] for x in report['warnings'])


def test_corrupt_records_auxiliary_files_and_duplicates_do_not_inflate_usage(tmp_path):
    workspace=Workspace.from_value(tmp_path/'private');run=workspace.runs_dir/'OPS7-L01'
    path=save_attempt(run,{'input_tokens':9})
    directory=path.parent
    (directory/'writing-aaaaaaaaaaaa.json').write_bytes(path.read_bytes())
    (directory/'writing-bbbbbbbbbbbb.json').write_text('{broken')
    (directory/'writing-cccccccc.schema.json').write_text('{}')
    (directory/'writing-dddddddd.final.json').write_text('{"usage":{"input_tokens":900}}')
    report=lecture_usage(workspace,'OPS7-L01')
    assert len(report['attempts'])==1 and report['providers']['codex']['reported_totals']=={'input_tokens':9}
    assert len(report['warnings'])==2 and report['partial']


def test_conflicting_same_attempt_identity_is_not_totaled(tmp_path):
    workspace=Workspace.from_value(tmp_path/'private');run=workspace.runs_dir/'OPS7-L01'
    path=save_attempt(run,{'input_tokens':9});data=json.loads(path.read_text());data['reported_usage']['input_tokens']=90
    (path.parent/'writing-aaaaaaaaaaaa.json').write_text(json.dumps(data))
    report=lecture_usage(workspace,'OPS7-L01')
    assert report['attempts']==[] and report['providers']=={} and report['partial']


@pytest.mark.parametrize('provider,adapter',[('codex',CodexAdapter),('agy',AgyAdapter),('claude',ClaudeAdapter)])
def test_failed_transports_preserve_reported_usage(provider,adapter,tmp_path,monkeypatch):
    monkeypatch.setattr('mynotes.providers.shutil.which',lambda *_args,**_kwargs:'/test/'+provider)
    usage={'input_tokens':7,'output_tokens':3}
    output=json.dumps({'type':'turn.failed','status':'ERROR','subtype':'error_during_execution','is_error':True,'usage':usage})
    monkeypatch.setattr('mynotes.providers._run_agy_worker' if provider == 'agy' else 'mynotes.providers.subprocess.run',lambda command,**kwargs:subprocess.CompletedProcess(command,1,output,'failure'))
    root=tmp_path/'private/runs/OPS7-L01'
    with pytest.raises(ProviderError):adapter().generate(request(root,provider))
    saved=json.loads(next((root/'provider-attempts').glob('writing-????????????.json')).read_text())
    assert saved['reported_usage']==usage and saved['invocation_started'] is True
    report=lecture_usage(Workspace.from_value(tmp_path/'private'),'OPS7-L01')
    assert report['providers'][provider]['failed']==1
    assert report['providers'][provider]['reported_totals']==usage


@pytest.mark.parametrize('provider,adapter',[('codex',CodexAdapter),('agy',AgyAdapter),('claude',ClaudeAdapter)])
def test_large_request_is_rejected_before_any_provider_process(provider,adapter,tmp_path,monkeypatch):
    monkeypatch.setattr('mynotes.providers.shutil.which',lambda *_args,**_kwargs:'/test/'+provider)
    monkeypatch.setattr('mynotes.providers.provider_environment',lambda:{'PATH':'/test','AUDIT_ENV':'PUBLIC_ONLY'})
    monkeypatch.setattr('mynotes.provider_limits._positive_sysconf',lambda name:1024 if name=='SC_ARG_MAX' else 4096)
    monkeypatch.setattr('mynotes.providers._run_agy_worker' if provider == 'agy' else 'mynotes.providers.subprocess.run',lambda *_args,**_kwargs:pytest.fail('local byte guard must reject before subprocess'))
    root=tmp_path/'private/runs/OPS7-L01'
    with pytest.raises(ProviderError,match='Local CLI input limit') as error:adapter().generate(request(root,provider))
    assert 'SYNTHETIC_PUBLIC_INPUT' not in str(error.value) and 'PUBLIC_ONLY' not in str(error.value)
    report=lecture_usage(Workspace.from_value(tmp_path/'private'),'OPS7-L01')
    attempt=report['attempts'][0]
    assert attempt['invocation_started'] is False and attempt['outcome']=='local_rejected'
    assert attempt['reported_usage'] is None and report['providers'][provider]['reported_totals']=={}


@pytest.mark.parametrize('provider,adapter',[('codex',CodexAdapter),('agy',AgyAdapter),('claude',ClaudeAdapter)])
def test_timeout_keeps_partial_reported_usage_without_guessing_return_code(provider,adapter,tmp_path,monkeypatch):
    monkeypatch.setattr('mynotes.providers.shutil.which',lambda *_args,**_kwargs:'/test/'+provider)
    def run(command,**kwargs):
        raise subprocess.TimeoutExpired(command,1,output=b'{"usage":{"input_tokens":4}}\n')
    monkeypatch.setattr('mynotes.providers._run_agy_worker' if provider == 'agy' else 'mynotes.providers.subprocess.run',run)
    root=tmp_path/'private/runs/OPS7-L01'
    with pytest.raises(ProviderError):adapter().generate(request(root,provider))
    report=lecture_usage(Workspace.from_value(tmp_path/'private'),'OPS7-L01')
    assert report['attempts'][0]['invocation_started'] is True
    assert report['providers'][provider]['reported_totals']=={'input_tokens':4}
    assert json.loads(Path(report['attempts'][0]['path']).read_text())['returncode'] is None


def test_reused_phases_and_whole_candidate_are_local_events_not_extra_usage(tmp_path):
    workspace=configured_workspace(tmp_path);source_pack(workspace)
    class Provider:
        calls=[];failed=False
        def generate(self,req):
            self.calls.append(req.phase)
            fail=req.phase=='scoped_review' and not self.failed
            usage={'input_tokens':5 if fail else 10}
            attempt=_save_attempt(req,command=['fixture'],completed=subprocess.CompletedProcess(['fixture'],1 if fail else 0,json.dumps({'usage':usage}),''),started=time.monotonic(),error='quota reached' if fail else None)
            if fail:
                self.failed=True;raise ProviderQuotaError('quota reached')
            return ProviderResponse(valid_notes(),'codex','','',usage,str(attempt))
    provider=Provider()
    with pytest.raises(WorkflowError):generate_candidate(workspace,'OPS7-L01',quality='approfondito',provider_adapter=provider)
    assert generate_candidate(workspace,'OPS7-L01',quality='approfondito',provider_adapter=provider)['status']=='CANDIDATE_READY'
    assert generate_candidate(workspace,'OPS7-L01',quality='approfondito',provider_adapter=provider)['status']=='SKIPPED'
    before=snapshot(workspace.root);report=lecture_usage(workspace,'OPS7-L01')
    assert snapshot(workspace.root)==before
    assert provider.calls==['writing','scoped_review','scoped_review']
    assert report['providers']['codex']['reported_totals']=={'input_tokens':25}
    assert len(report['successful_saved_phases'])==2
    assert report['reuse']['phase_events']==1 and report['reuse']['candidate_events']==1
    assert all(x['provider_invoked'] is False for x in report['reuse']['events'])
    phase=next((workspace.runs_dir/'OPS7-L01/generation-checkpoints').glob('*/writing.json'))
    data=json.loads(phase.read_text());data['payload']['response']['usage']={'input_tokens':999};phase.write_text(json.dumps(data))
    assert len(lecture_usage(workspace,'OPS7-L01')['successful_saved_phases'])==1


def test_usage_cli_does_not_initialize_missing_workspace_or_sqlite(tmp_path,capsys,monkeypatch):
    monkeypatch.setattr(Workspace,'initialize',lambda *_:pytest.fail('usage must not initialize workspace'))
    root=tmp_path/'absent'
    assert main(['usage','OPS7-L01','--json','--workspace',str(root)])==0
    report=json.loads(capsys.readouterr().out)
    assert report['attempts']==[] and report['providers']=={}
    assert not root.exists()


@pytest.mark.parametrize('provider,adapter',[('codex',CodexAdapter),('agy',AgyAdapter),('claude',ClaudeAdapter)])
def test_missing_launcher_is_recorded_as_local_failure_without_provider_call(provider,adapter,tmp_path,monkeypatch):
    monkeypatch.setattr('mynotes.providers.shutil.which',lambda *_args,**_kwargs:None)
    monkeypatch.setattr('mynotes.providers._run_agy_worker' if provider == 'agy' else 'mynotes.providers.subprocess.run',lambda *_args,**_kwargs:pytest.fail('missing launcher must not invoke subprocess'))
    root=tmp_path/'private/runs/OPS7-L01'
    with pytest.raises(ProviderError,match='not installed'):adapter().generate(request(root,provider))
    report=lecture_usage(Workspace.from_value(tmp_path/'private'),'OPS7-L01')
    assert report['attempts'][0]['invocation_started'] is False
    assert report['attempts'][0]['reported_usage'] is None
    assert report['providers'][provider]['local_rejected']==1
    assert report['providers'][provider]['reported_totals']=={}


def test_legacy_attempt_without_new_fields_still_reports_verified_stdout(tmp_path):
    workspace=Workspace.from_value(tmp_path/'private');run=workspace.runs_dir/'OPS7-L01'
    path=save_attempt(run,{'input_tokens':9});data=json.loads(path.read_text())
    for field in ('attempt_id','reported_usage','invocation_started'):data.pop(field)
    path.write_text(json.dumps(data))
    (path.parent/'writing-aaaaaaaaaaaa.json').write_text(json.dumps(data))
    report=lecture_usage(workspace,'OPS7-L01')
    assert len(report['attempts'])==1
    assert report['providers']['codex']['reported_totals']=={'input_tokens':9}
    assert report['attempts'][0]['attempt_id'].startswith('legacy:')


@pytest.mark.parametrize('provider,adapter',[('codex',CodexAdapter),('agy',AgyAdapter),('claude',ClaudeAdapter)])
def test_preflight_checks_exact_environment_used_for_the_process(provider,adapter,tmp_path,monkeypatch):
    monkeypatch.setattr('mynotes.providers.shutil.which',lambda *_args,**_kwargs:'/test/'+provider)
    calls=[];checked=[]
    environment={'PATH':'/test','PUBLIC_FIELD':'one environment snapshot'}
    monkeypatch.setattr('mynotes.providers.provider_environment',lambda:calls.append('environment') or environment)
    monkeypatch.setattr('mynotes.providers.validate_command_size',lambda command,env:checked.append(env) or {})
    def run(command,**kwargs):
        assert kwargs['env'] is checked[0]
        assert all(kwargs['env'][key] == value for key, value in environment.items())
        if provider == 'agy': assert kwargs['env']['MYNOTES_GENERATION_WORKER'] == '1'
        return subprocess.CompletedProcess(command,1,'','fixture failure')
    monkeypatch.setattr('mynotes.providers._run_agy_worker' if provider == 'agy' else 'mynotes.providers.subprocess.run',run)
    with pytest.raises(ProviderError):adapter().generate(request(tmp_path,provider))
    assert calls==['environment']


def test_cumulative_lecture_usage_and_legacy_result_streams_share_one_conversation(tmp_path):
    workspace=Workspace.from_value(tmp_path/'private');run=workspace.runs_dir/'OPS7-L01'
    for tokens in [10,18]:
        path=save_attempt(run,None,provider='agy',error='local validation')
        data=json.loads(path.read_text());data['reported_usage']=None
        data['envelope']={'transport':'agy_parts_v1','status':'ERROR'}
        data['stdout']=json.dumps({'event':'result','result':{'status':'SUCCESS','conversation_id':'one',
            'usage':{'input_tokens':tokens,'thinking_tokens':3,'cache_read_tokens':50}}})
        path.write_text(json.dumps(data))
    result=lecture_usage(workspace,'OPS7-L01')['providers']['agy']
    assert result['attempts']==2 and result['failed']==2
    assert result['reported_totals']=={'input_tokens':18,'thinking_tokens':3,'cache_read_tokens':50}
    assert result['expected_reporting_units']==1
