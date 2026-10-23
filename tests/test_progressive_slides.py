"""Progressive placement is source review, never duplicated paid slide writing."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from mynotes.batch import generate_batch, plan_batch
from mynotes.providers import ProviderResponse
from mynotes.utils import atomic_json_write
from mynotes.workflow import WorkflowError
from test_batch_generation import workspace_and_deck, notes_for


def initial_deck(tmp_path):
    ws, deck, selection = workspace_and_deck(tmp_path, lessons=2, slides=95)
    for number in (1, 2):
        path = ws.runs_dir/f'OPS-L{number:02d}'/'source_pack.json'
        pack = json.loads(path.read_text())
        if number == 2:
            pack['evidence'] = []
            pack['sources'] = []
        pack['evidence'].append({'id':'T001','kind':'transcript','text':f'Complete synthetic lesson {number} recording evidence.',
            'start_seconds':0,'end_seconds':100,'speaker_role':'UNKNOWN'})
        pack['availability']['transcript'] = True
        atomic_json_write(path, pack)
    selection = {key: {'owner':'OPS-L01','status':'taught'} if index < 50 else
                 {'owner':'OPS-L01','status':'deferred','review_before':'OPS-L02',
                  'reason':'Placement awaits reading the next full transcript.'}
                 for index, key in enumerate(selection)}
    return ws, selection


class ProgressiveWriter:
    def __init__(self): self.calls = []

    def generate(self, request):
        self.calls.append(request)
        assert request.phase == 'writing', 'Deferred slides must not invoke coverage repair'
        filtered = deepcopy(request)
        filtered.batch_context['ownership'] = [r for r in filtered.batch_context['ownership'] if r['status'] != 'deferred']
        rows = [{'lecture_id':l, 'notes':notes_for(filtered,l)} for l in request.batch_context['lessons']]
        return ProviderResponse({'lessons':rows}, 'codex', 'test-model', 'high')


def test_single_attached_deck_requires_local_review_not_ninety_five_explanations(tmp_path):
    ws, _ = initial_deck(tmp_path)
    writer = ProgressiveWriter()
    result = generate_batch(ws,'OPS',[1,2],provider_adapter=writer)
    assert result['status'] == 'NEEDS_ORGANIZATION'
    assert writer.calls == []
    assert len(result['organization_pending']) == 95


def test_deferred_pages_are_preserved_and_stop_before_next_lesson_without_repair(tmp_path):
    ws, selection = initial_deck(tmp_path)
    originals = {p:p.read_bytes() for p in ws.runs_dir.glob('OPS-L*/source_pack.json')}
    writer = ProgressiveWriter()
    result = generate_batch(ws,'OPS',[1,2],selection=selection,provider_adapter=writer,repair_missing=True)
    assert result['status'] == 'NEEDS_ORGANIZATION'
    assert [r.lecture_id for r in writer.calls] == ['OPS-L01']
    candidate = json.loads(Path(result['candidates'][0]['candidate']).read_text())
    assert len(candidate['slide_ownership']) == 95
    assert sum(b.get('coverage_role') == 'full' for b in candidate['notes']['blocks']) == 50
    assert {p:p.read_bytes() for p in originals} == originals
    # Public build/update share this loader; direct commands must obey the
    # same pending-placement gate as --build-word.
    from mynotes.publication import _load_candidate, PublicationError
    with pytest.raises(PublicationError, match='placement is deferred'):
        _load_candidate(ws, 'OPS-L01')
    again = generate_batch(ws,'OPS',[1,2],selection=selection,provider_adapter=writer,batch_id=result['batch_id'])
    assert again['status'] == 'NEEDS_ORGANIZATION' and len(writer.calls) == 1


def test_next_lesson_can_own_original_deck_pages_without_rewriting_source_packs(tmp_path):
    ws, selection = initial_deck(tmp_path)
    for key, choice in selection.items():
        if choice['status'] == 'deferred':
            selection[key] = {'owner':'OPS-L02','status':'taught', 'reason':'Next full transcript establishes the topic boundary.'}
    before = {p:p.read_bytes() for p in ws.runs_dir.glob('OPS-L*/source_pack.json')}
    writer = ProgressiveWriter()
    result = generate_batch(ws,'OPS',[1,2],selection=selection,provider_adapter=writer)
    assert result['status'] == 'CANDIDATE_READY'
    counts = []
    for entry in result['candidates']:
        payload = json.loads(Path(entry['candidate']).read_text())
        counts.append(sum(b.get('coverage_role') == 'full' for b in payload['notes']['blocks']))
        if entry['lecture_id'] == 'OPS-L02':
            effective = json.loads(Path(payload['source_pack']).read_text())
            carried = [r for r in effective['evidence'] if r.get('origin_lecture_id')]
            assert len(carried) == 45 and all(r['kind'] == 'slide' for r in carried)
    assert counts == [50,45]
    assert {p:p.read_bytes() for p in before} == before


@pytest.mark.parametrize('change', [dict(reason=''), dict(review_before='OPS-L01'),dict(review_before='OPS-L03'),
                                    dict(uses={'OPS-L01':{'role':'development','reason':'x','evidence_ids':['T001']}})])
def test_deferral_cannot_hide_pages_without_a_prepared_next_boundary(tmp_path, change):
    ws, selection = initial_deck(tmp_path)
    key = next(k for k,v in selection.items() if v['status']=='deferred')
    selection[key].update(change)
    with pytest.raises(WorkflowError, match='deferred slide'):
        plan_batch(ws,'OPS',[1,2],selection=selection)


def test_resume_resolves_deferred_boundary_and_reuses_lesson_one(tmp_path):
    # A provider receipt is necessary for verified old-content recovery. This
    # adapter writes realistic SUCCESS records, but never calls a real model.
    from mynotes.providers import _save_attempt, AttemptStart
    import subprocess
    ws, selection = initial_deck(tmp_path)
    class Receipted(ProgressiveWriter):
        def generate(self, req):
            response = super().generate(req)
            output = subprocess.CompletedProcess(['synthetic'], 0, json.dumps(response.notes), '')
            path = _save_attempt(req, command=['synthetic'], completed=output, started=AttemptStart())
            return ProviderResponse(response.notes,response.provider,response.model,response.effort,attempt_path=str(path))
    writer = Receipted()
    first = generate_batch(ws,'OPS',[1,2],selection=selection,provider_adapter=writer)
    assert first['status'] == 'NEEDS_ORGANIZATION'
    for key, value in list(selection.items()):
        if value['status'] == 'deferred':
            selection[key] = {'owner':'OPS-L02','status':'taught'}
    result = generate_batch(ws,'OPS',[1,2],selection=selection,provider_adapter=writer,batch_id=first['batch_id'])
    assert result['status'] == 'CANDIDATE_READY'
    assert [r.lecture_id for r in writer.calls] == ['OPS-L01','OPS-L02']
    assert len(list((ws.runs_dir/'_batches'/result['batch_id']/'revisions').glob('*.json'))) == 1
    for entry in result['candidates']:
        candidate = json.loads(Path(entry['candidate']).read_text())
        assert candidate['related_lessons'] == ['OPS-L01','OPS-L02']
    from mynotes.course_update import build_course_revision
    from mynotes.publication import PublicationError
    with pytest.raises(PublicationError, match='same coordinated revision'):
        build_course_revision(ws, ['OPS-L02'])


def test_batch_correction_can_run_more_than_ten_minutes_after_eight_minute_writing(tmp_path, monkeypatch):
    from mynotes import generation_budget
    clock = [1.]
    monkeypatch.setattr(generation_budget.time, 'monotonic', lambda: clock[0])
    ws, _, selection = workspace_and_deck(tmp_path, lessons=1, slides=2)
    class Writer:
        calls = []
        def generate(self, req):
            self.calls.append(req.phase)
            if req.phase == 'writing':
                assert req.timeout_seconds == 1200
                clock[0] += 480
                notes = notes_for(req,'OPS-L01')
                notes['blocks'] = [b for b in notes['blocks'] if b.get('evidence_ids') != ['S002']]
                return ProviderResponse({'lessons':[{'lecture_id':'OPS-L01','notes':notes}]},'codex','test-model','high')
            assert req.timeout_seconds == 1200
            clock[0] += 700
            task = req.batch_context['repair_task']
            anchor = next(r for r in task['anchors'] if r['block']['kind'] == 'paragraph')
            return ProviderResponse({'insertions':[{'lecture_id':'OPS-L01','after_block_sha256':anchor['block_sha256'],
                'block':{'kind':'paragraph','text':'The second slide explains another mechanism supported by source evidence.',
                         'evidence_ids':['S002'],'coverage_role':'full'}}], 'attachments':[], 'visual_updates':[]},
                'codex','test-model','high')
    writer = Writer()
    result = generate_batch(ws,'OPS',[1],selection=selection,provider_adapter=writer,repair_missing=True)
    assert result['status'] == 'CANDIDATE_READY' and writer.calls == ['writing','coverage_repair']
    ledger = json.loads(next((ws.runs_dir/'_batches'/result['batch_id']/'active-budgets').glob('*.json')).read_text())
    assert ledger['phases']['writing'] == 480 and ledger['phases']['coverage_repair'] == 700


def test_batch_reports_a_real_adapter_deadline_as_timed_out(tmp_path, monkeypatch):
    import subprocess
    from mynotes import providers
    from mynotes.batch_recovery import batch_status
    ws, _, selection = workspace_and_deck(tmp_path, lessons=1, slides=2)
    configuration = ws.configuration_store().load()
    configuration['providers']['configured']['agy'] = {'model':'test-model','effort':'high'}
    ws.configuration_store().save(configuration)
    monkeypatch.setattr(providers,'provider_executable',lambda _:'/fake/agy')
    monkeypatch.setenv('MYNOTES_AGY_PARTS','0')
    def expired(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])
    monkeypatch.setattr(providers,'_run_agy_worker',expired)
    with pytest.raises(providers.ProviderTimeoutError):
        generate_batch(ws,'OPS',[1],selection=selection,provider='agy')
    root = next((ws.runs_dir/'_batches').iterdir())
    status = batch_status(ws,root.name)
    assert status['status'] == 'TIMED_OUT' and status['problem']['phase'] == 'WRITING'
