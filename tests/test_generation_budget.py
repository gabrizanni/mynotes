from dataclasses import replace
import json
import subprocess

import pytest

from mynotes import generation_budget as b
from mynotes import providers
from mynotes.providers import ProviderError, ProviderRequest, RequestedModel
from mynotes.utils import atomic_json_write


def record(directory, name, phase_name, start, active, **changes):
    path = directory/'provider-attempts'/f'{name}.json'
    atomic_json_write(path, {'attempt_id':name, 'invocation_started':True, 'phase':phase_name,
        'started_at':f'20261023T{start}Z', 'active_seconds':active, **changes})
    return path


def test_writing_and_correction_have_independent_twenty_minutes(tmp_path, monkeypatch):
    clock = [10.]
    monkeypatch.setattr(b.time, 'monotonic', lambda:clock[0])
    budget = b.PhaseBudget(tmp_path/'budget.json', ['RAI-L01'])
    with budget.track('writing') as allowance:
        assert allowance == 1200
        clock[0] += 480
    with budget.track('coverage_repair') as allowance:
        assert allowance == 1200
        clock[0] += 700
    assert budget.remaining('writing') == 720
    assert budget.remaining('coverage_repair') == 500
    assert budget.remaining('local_checks') == 120
    resumed = b.PhaseBudget(budget.path, ['RAI-L01'])
    assert resumed.snapshot()['phases'] == {'writing':480, 'coverage_repair':700}


def test_failed_invoke_is_charged_and_reorganization_cannot_reset(tmp_path, monkeypatch):
    clock = [10.]; monkeypatch.setattr(b.time, 'monotonic', lambda:clock[0])
    old = b.PhaseBudget(tmp_path/'old.json', ['RAI-L01'])
    with pytest.raises(RuntimeError):
        with old.track('coverage_repair'):
            clock[0] += 1205
            raise RuntimeError('provider timeout')
    current = b.PhaseBudget(tmp_path/'new.json', ['RAI-L01','RAI-L02'], previous_paths=[old.path])
    assert current.remaining('coverage_repair') == 0
    assert current.remaining('writing') == 1200
    with pytest.raises(b.GenerationBudgetError, match='exhausted'):
        with current.track('coverage_repair'):
            pytest.fail('A new correction must not start')


def test_local_validation_cannot_report_success_after_its_own_deadline(tmp_path, monkeypatch):
    clock = [10.]; monkeypatch.setattr(b.time, 'monotonic', lambda:clock[0])
    budget = b.PhaseBudget(tmp_path/'budget.json', ['RAI-L01'])
    with pytest.raises(b.GenerationBudgetError, match='Local generation checks exceeded'):
        with budget.track('local_checks'):
            clock[0] += 121
    assert budget.remaining('writing') == 1200
    assert json.loads(budget.path.read_text())['phases']['local_checks'] == 121


def test_active_marker_is_persisted_before_work_and_crash_fails_closed(tmp_path):
    budget = b.PhaseBudget(tmp_path/'budget.json', ['RAI-L01'])
    with budget.track('writing'):
        assert json.loads(budget.path.read_text())['active_phase']['phase'] == 'writing'
        with pytest.raises(b.GenerationBudgetError, match='Interrupted'):
            b.PhaseBudget(budget.path, ['RAI-L01'])
    assert json.loads(budget.path.read_text())['active_phase'] is None


def test_old_shared_allowance_reconstructed_from_immutable_attempts(tmp_path):
    checkpoint = tmp_path/'checkpoint'
    first = record(checkpoint, 'base', 'writing', '163824244624', 459.375)
    second = record(checkpoint/'coverage-repair', 'repair', 'coverage_repair', '164603784752', 620.885)
    before = {p:p.read_bytes() for p in (first, second)}
    budget = b.PhaseBudget(tmp_path/'budget.json', ['RAI-L01'], receipt_directories=[checkpoint], legacy_used_seconds=1080.596)
    assert budget.remaining('writing') == pytest.approx(740.625)
    assert budget.remaining('coverage_repair') == pytest.approx(579.115)
    assert {p:p.read_bytes() for p in before} == before
    assert not budget.path.exists()  # diagnosis has no writes


def test_cumulative_streaming_attempt_is_not_counted_twice(tmp_path):
    record(tmp_path, 'failed-turn', 'writing', '160000000000', 50)
    record(tmp_path, 'cumulative-outer', 'writing', '160000000000', 110)
    totals, _ = b.receipt_consumption([tmp_path])
    assert totals == {'writing':110}


@pytest.mark.parametrize('changes', [dict(invocation_started=None),dict(active_seconds=float('nan')),
                                    dict(active_seconds=-1),dict(started_at='missing'),dict(phase=None)])
def test_uncertain_historical_consumption_does_not_grant_fresh_allowance(tmp_path, changes):
    record(tmp_path, 'old', 'writing', '160000000000', 50, **changes)
    with pytest.raises(b.GenerationBudgetError):
        b.PhaseBudget(tmp_path/'budget.json', ['RAI-L01'], receipt_directories=[tmp_path])


def test_legacy_budget_without_bound_receipts_fails_closed(tmp_path):
    atomic_json_write(tmp_path/'old.json', {'lessons':['RAI-L01'], 'used_seconds':1080.5})
    with pytest.raises(b.GenerationBudgetError, match='reliably separated'):
        b.PhaseBudget(tmp_path/'budget.json', ['RAI-L01'], previous_paths=[tmp_path/'old.json'])


@pytest.mark.parametrize('remaining', [1200, 579.1])
def test_agy_supervisor_uses_phase_residual_and_cli_does_not_cut_it_short(tmp_path, monkeypatch, remaining):
    request = ProviderRequest('RAI-L01', {}, {}, RequestedModel('agy','test-model','high','standard'),
                              phase='coverage_repair', run_dir=tmp_path, timeout_seconds=remaining)
    monkeypatch.setattr(providers, 'provider_executable', lambda _: '/fake/agy')
    monkeypatch.setenv('MYNOTES_AGY_PARTS','0')
    monkeypatch.setattr(providers, '_stage_agy_context', lambda *args: 'test context')
    calls = []
    def timeout(command, **kwargs):
        calls.append((command,kwargs))
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])
    monkeypatch.setattr(providers, '_run_agy_worker', timeout)
    with pytest.raises(providers.ProviderTimeoutError, match=f'coverage_repair phase exceeded its remaining {remaining:.1f}-second'):
        providers.AgyAdapter().generate(request)
    command, kwargs = calls[0]
    assert kwargs['timeout'] == remaining
    assert command[command.index('--print-timeout')+1] == '21m'
    assert '--model' in command and 'high' in command


def test_zero_remaining_allowance_does_not_start_a_paid_worker(tmp_path, monkeypatch):
    request = ProviderRequest('RAI-L01', {}, {}, RequestedModel('agy','test','high','standard'),
                              run_dir=tmp_path, timeout_seconds=0)
    monkeypatch.setattr(providers, 'provider_executable', lambda _: '/fake/agy')
    monkeypatch.setenv('MYNOTES_AGY_PARTS','0')
    monkeypatch.setattr(providers, '_run_agy_worker', lambda *a,**k:pytest.fail('worker started'))
    with pytest.raises(ProviderError, match='no remaining execution allowance'):
        providers.AgyAdapter().generate(request)


def test_parts_keep_progress_watchdogs_but_have_full_phase_allowance():
    from mynotes import agy_parts
    assert agy_parts.TOTAL_SECONDS == 1200
    assert agy_parts.FIRST_TURN_SECONDS == agy_parts.NEXT_TURN_SECONDS == 300


@pytest.mark.parametrize('adapter_class,provider', [(providers.CodexAdapter,'codex'),(providers.ClaudeAdapter,'claude')])
@pytest.mark.parametrize('remaining',[None, 321.5])
def test_other_adapters_share_phase_limit_and_report_actual_residual(tmp_path, monkeypatch, adapter_class, provider, remaining):
    request = ProviderRequest('RAI-L01', {}, {}, RequestedModel(provider,'test-model','high','standard'),
                              run_dir=tmp_path, timeout_seconds=remaining)
    monkeypatch.setattr(providers, 'provider_executable', lambda _: '/fake/provider')
    calls = []
    def timeout(command, **kwargs):
        calls.append(kwargs['timeout'])
        raise subprocess.TimeoutExpired(command,kwargs['timeout'])
    monkeypatch.setattr(providers.subprocess,'run',timeout)
    expected = 1200 if remaining is None else remaining
    with pytest.raises(providers.ProviderTimeoutError,match=f'{provider} writing phase exceeded its remaining {expected:.1f}-second'):
        adapter_class().generate(request)
    assert calls == [expected]


def test_estimated_historical_start_does_not_certify_phase_reconstruction(tmp_path):
    record(tmp_path,'old','writing','160000000000',50,start_time_source='estimated_legacy_caller')
    with pytest.raises(b.GenerationBudgetError,match='estimated'):
        b.receipt_consumption([tmp_path])


def test_direct_candidate_keeps_phase_residual_after_failed_invoke(tmp_path, monkeypatch):
    from test_providers_quality import configured_workspace, source_pack, valid_notes
    from mynotes.workflow import generate_candidate, WorkflowError
    from mynotes.providers import ProviderResponse
    workspace = configured_workspace(tmp_path); source_pack(workspace)
    clock = [100.]; monkeypatch.setattr(b.time,'monotonic',lambda:clock[0])
    class Adapter:
        def __init__(self): self.remaining = []
        def generate(self,request):
            self.remaining.append(request.timeout_seconds)
            if len(self.remaining) == 1:
                clock[0] += 900
                raise ProviderError('simulated process failure')
            return ProviderResponse(valid_notes(), 'codex', 'test', 'high')
    adapter = Adapter()
    with pytest.raises(WorkflowError,match='simulated process failure'):
        generate_candidate(workspace,'OPS7-L01',provider_adapter=adapter)
    assert generate_candidate(workspace,'OPS7-L01',provider_adapter=adapter)['status'] == 'CANDIDATE_READY'
    assert adapter.remaining == [1200, 300]
    ledger, = list((workspace.runs_dir/'OPS7-L01/generation-checkpoints').glob('*/phase-budget.json'))
    assert json.loads(ledger.read_text())['phases']['writing'] == 900


def test_direct_candidate_unknown_legacy_failure_never_gets_new_budget(tmp_path):
    from test_providers_quality import configured_workspace, source_pack
    from mynotes.workflow import generate_candidate, WorkflowError
    workspace = configured_workspace(tmp_path); source_pack(workspace)
    run = workspace.runs_dir/'OPS7-L01'
    record(run,'legacy-failure','writing','160000000000',110)
    class Adapter:
        def generate(self,request): pytest.fail('Unknown old generation triggered a new provider call')
    with pytest.raises(WorkflowError,match='Unbound historical provider attempts'):
        generate_candidate(workspace,'OPS7-L01',provider_adapter=Adapter())


def test_attempt_receipt_records_direct_generation_identity(tmp_path):
    from mynotes.execution import AttemptStart
    request = ProviderRequest('RAI-L01',{}, {},RequestedModel('agy','test','high','standard'),
                              run_dir=tmp_path,generation_id='source-bound-intent')
    path = providers._save_attempt(request,command=['fake'],completed=None,started=AttemptStart(),invocation_started=False)
    assert json.loads(path.read_text())['generation_id'] == 'source-bound-intent'


def test_uncertain_receipts_allow_only_local_recovery_with_sticky_model_block(tmp_path):
    old = record(tmp_path/'checkpoint','legacy','writing','160000000000',50)
    payload = json.loads(old.read_text()); payload.pop('attempt_id'); atomic_json_write(old,payload)
    path = tmp_path/'phase-budget.json'
    budget = b.PhaseBudget(path,['RAI-L01'],receipt_paths=[old],permit_local_recovery=True)
    assert budget.remaining('writing') == budget.remaining('coverage_repair') == 0
    assert budget.remaining('local_checks') == 120
    assert json.loads(path.read_text())['model_block_reasons']
    with budget.track('local_checks'):
        pass
    old.unlink()
    revised = b.PhaseBudget(tmp_path/'revision.json',['RAI-L01'],previous_paths=[path],permit_local_recovery=True)
    assert revised.remaining('writing') == 0
    with pytest.raises(b.GenerationBudgetError,match='uncertain historical consumption'):
        with revised.track('writing'):
            pytest.fail('A paid call was authorized from unknown history')


def test_local_recovery_does_not_bypass_corrupt_ledger_or_interrupted_marker(tmp_path):
    path = tmp_path/'phase-budget.json'
    atomic_json_write(path,{'schema_version':2,'lessons':['RAI-L01'],'phases':{},'active_phase':{'phase':'writing'}})
    with pytest.raises(b.GenerationBudgetError,match='Interrupted'):
        b.PhaseBudget(path,['RAI-L01'],permit_local_recovery=True)
    atomic_json_write(path,{'schema_version':2,'lessons':['RAI-L01'],'phases':{'writing':-1},'active_phase':None})
    with pytest.raises(b.GenerationBudgetError,match='Invalid saved'):
        b.PhaseBudget(path,['RAI-L01'],permit_local_recovery=True)


def test_direct_cached_legacy_success_recovers_locally_without_granting_paid_retry(tmp_path):
    from test_providers_quality import configured_workspace, source_pack, valid_notes
    from mynotes.workflow import generate_candidate, WorkflowError
    from mynotes.providers import ProviderResponse
    workspace = configured_workspace(tmp_path); source_pack(workspace)
    run = workspace.runs_dir/'OPS7-L01'
    class Adapter:
        calls = 0
        def generate(self,request):
            self.calls += 1
            attempt = record(run,'old-success','writing','160000000000',90)
            return ProviderResponse(valid_notes(),'codex','test','high',attempt_path=str(attempt))
    adapter = Adapter()
    assert generate_candidate(workspace,'OPS7-L01',provider_adapter=adapter)['status'] == 'CANDIDATE_READY'
    candidate = run/'lecture_notes.candidate.json'; candidate.unlink()
    ledger, = list((run/'generation-checkpoints').glob('*/phase-budget.json'))
    ledger.unlink()  # Model an old version which did not yet write phase ledgers.
    attempt = run/'provider-attempts/old-success.json'
    payload = json.loads(attempt.read_text()); payload.pop('attempt_id'); atomic_json_write(attempt,payload)
    assert generate_candidate(workspace,'OPS7-L01',provider_adapter=adapter)['status'] == 'CANDIDATE_READY'
    assert adapter.calls == 1
    assert json.loads(ledger.read_text())['model_block_reasons']
    candidate.unlink()
    for name in ('writing.json','returned-writing.json'):
        (ledger.parent/name).unlink()
    with pytest.raises(WorkflowError,match='Model phase blocked|Unbound historical provider attempts'):
        generate_candidate(workspace,'OPS7-L01',provider_adapter=adapter)
    assert adapter.calls == 1
