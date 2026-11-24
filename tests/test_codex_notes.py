"""Codex parity regressions; all model transports are synthetic."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest
from mynotes import codex_notes
from mynotes.providers import CodexAdapter, ProviderError, RequestedModel
from mynotes.batch import _request, _prepared_context, plan_batch
from test_autonomous_organization import prepared, IntegratedWriter


def request(tmp_path):
    ws = prepared(tmp_path)
    plan = plan_batch(ws, 'OPS', [1], provider='codex', autonomous_organization=True, progressive_protocol=3)
    packs, _, inventory, _ = _prepared_context(ws, plan['lectures'], autonomous_organization=True)
    root = tmp_path / 'worker'; root.mkdir()
    req, _ = _request({**plan, 'packs': packs, 'inventory': inventory,
                      'requested': RequestedModel(**plan['requested_model'])}, plan['lectures'], root, [])
    from PIL import Image
    image = root / 'source.png'; Image.new('RGB',(100,100),'white').save(image)
    return replace(req, source_pack={**req.source_pack, 'visuals':[dict(evidence_id='S001',image_path=str(image))]})


def test_non_page_s_sources_remain_cited_without_invented_ownership(tmp_path):
    req = request(tmp_path)
    extra = [dict(id='S900', kind='slide', cell=1, content='Notebook cell'),
             dict(id='S901', kind='slide', paragraph=1, content='https://example.org')]
    req = replace(req, source_pack={**req.source_pack, 'evidence': [*req.source_pack['evidence'], *extra]})
    raw = IntegratedWriter().generate(req).notes
    raw['lessons'][0]['notes']['blocks'].insert(1, dict(kind='paragraph', text='Source explanation.',
        coverage_role='full', evidence_ids=['S900', 'S901', 'T001']))
    original = deepcopy(raw)
    out = codex_notes.project(req, raw)
    assert len(out['organization']) == len(raw['organization'])
    assert any(b.get('text') == 'Source explanation.' and b['evidence_ids'] == ['S900','S901','T001']
               for b in out['lessons'][0]['notes']['blocks'])
    assert raw == original


def test_supplement_allows_shared_block_and_narrative_order():
    note = {'blocks': [dict(kind='recap', evidence_ids=['S001']),
                       dict(kind='paragraph', coverage_role='supplement', evidence_ids=['S003','S001']),
                       dict(kind='heading', evidence_ids=['S001'])]}
    assert codex_notes.validate_supplement(note, 'S001', {}) == [1]
    note['blocks'][1]['coverage_role'] = 'reference'
    with pytest.raises(ProviderError, match='NEEDS_CONTENT_REVIEW.*source.pdf.*reference-only'):
        codex_notes.validate_supplement(note, 'S001', {'S001':dict(source_file='source.pdf',page=3)})


def test_joint_transport_uses_stdin_compact_schema_previews_and_requested_settings(tmp_path, monkeypatch):
    req = request(tmp_path)
    monkeypatch.setattr('mynotes.providers.provider_executable', lambda _: '/synthetic/codex')
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert command[-1] == '-' and kwargs['input'].endswith(codex_notes.instructions())
        assert '--image' in command
        assert req.source_pack['evidence'][0].get('content', req.source_pack['evidence'][0].get('text','')) in kwargs['input']
        schema = json.loads(Path(command[command.index('--output-schema')+1]).read_text())
        assert schema['properties']['lessons']['items']['properties']['notes']['properties']['evidence_coverage']['maxItems']==0
        result = IntegratedWriter().generate(req).notes
        Path(command[command.index('--output-last-message')+1]).write_text(json.dumps(result))
        stdout = '\n'.join(json.dumps(e) for e in [dict(type='item.completed',item=dict(type='agent_message',text=json.dumps(result))),dict(type='turn.completed')])
        return subprocess.CompletedProcess(command, 0, stdout, '')
    monkeypatch.setattr('mynotes.providers.subprocess.run', run)
    response = CodexAdapter().generate(req)
    record = json.loads(Path(response.attempt_path).read_text())
    assert codex_notes.certified(record,req) == response.notes
    assert len(calls)==1 and '--sandbox' in calls[0]
    record['stdout']='{"type":"turn.failed"}'
    with pytest.raises(ProviderError, match='completed turn'):codex_notes.certified(record, req)


def test_image_access_never_confuses_previews_or_failed_events_with_full_images(tmp_path):
    req = request(tmp_path);image = req.source_pack['visuals'][0]['image_path']
    events = [dict(type='item.completed',item=dict(type='image_view',path=image)),
              dict(type='item.completed',item=dict(type='image_view',path=image,status='failed')),
              dict(type='item.completed',item=dict(type='image_view',path=str(tmp_path/'preview.png')))]
    assert len(codex_notes.source_access(events,req))==1


def test_launch_denial_is_saved_and_not_retried(tmp_path, monkeypatch):
    req = request(tmp_path);calls=[]
    monkeypatch.setattr('mynotes.providers.provider_executable', lambda _: '/synthetic/codex')
    def run(cmd, **kwargs):
        calls.append(cmd);return subprocess.CompletedProcess(cmd,1,'','failed to initialize in-process app-server client: Operation not permitted')
    monkeypatch.setattr('mynotes.providers.subprocess.run',run)
    with pytest.raises(ProviderError,match='CODEX_LAUNCH_DENIED'):CodexAdapter().generate(req)
    assert len(calls)==1
    assert any('CODEX_LAUNCH_DENIED' in json.loads(f.read_text()).get('error','') for f in req.run_dir.glob('provider-attempts/writing-*.json') if '.schema.' not in f.name)


def test_saved_success_resumes_through_both_words_and_is_idempotent(tmp_path, monkeypatch):
    from mynotes import notes_run, publication, batch_documents, providers
    from test_publication import fake_render
    ws = prepared(tmp_path)
    monkeypatch.setattr(publication, '_render_and_validate', fake_render)
    monkeypatch.setattr(batch_documents, '_render_and_validate', fake_render)
    calls = []
    class Recorded(IntegratedWriter):
        def generate(self, request):
            calls.append(request)
            result = super().generate(request)
            stdout = '\n'.join(json.dumps(e) for e in [
                dict(type='item.completed', item=dict(type='agent_message',text=json.dumps(result.notes))),
                dict(type='turn.completed')])
            attempt = providers._save_attempt(request,command=['codex','exec','--json'],
                started=providers.AttemptStart(),completed=subprocess.CompletedProcess(['codex'],0,stdout,''))
            return replace(result,attempt_path=str(attempt))
    original = codex_notes.project
    failures = []
    def fail_once(*args, **kwargs):
        if not failures:
            failures.append(True);raise ProviderError('Synthetic local projection failure')
        return original(*args, **kwargs)
    monkeypatch.setattr(codex_notes,'project',fail_once)
    first = notes_run.run_notes(ws,'OPS',[1,2],provider='codex',provider_adapter=Recorded())
    assert first['status']=='FAILED'
    second = notes_run.run_notes(ws,'OPS',resume=first['batch_id'],provider_adapter=Recorded())
    assert second['status'] in {'COMPLETED','COMPLETED_WITH_WARNINGS'},second.get('problem')
    assert len(second['delivery']['files'])==3
    third = notes_run.run_notes(ws,'OPS',resume=first['batch_id'],provider_adapter=Recorded())
    assert third['reused'] and len(calls)==1


def test_preview_attachment_cannot_certify_an_included_full_image(tmp_path):
    req, sheets = codex_notes.prepare(request(tmp_path))
    raw = IntegratedWriter().generate(req).notes
    raw['lessons'][0]['notes']['visual_decisions'] = [dict(evidence_id='S001',decision='include',
        inspected=True,reason='Source figure selected.')]
    with pytest.raises(ProviderError, match='observed full access'):
        codex_notes.project(req, raw, access=[], preview_sheets=sheets)


def test_relative_source_paths_are_verified_from_workspace(tmp_path, monkeypatch):
    from mynotes.batch import _packs, _unchanged
    ws=prepared(tmp_path)
    pack_path=ws.runs_dir/'OPS-L01/source_pack.json'
    pack=json.loads(pack_path.read_text())
    source=Path(pack['sources'][0]['path'])
    local=ws.root/'imported.pdf';local.write_bytes(source.read_bytes())
    old=pack['sources'][0]['path'];pack['sources'][0]['path']='imported.pdf'
    for evidence in pack['evidence']:
        if evidence.get('source_file')==old:evidence['source_file']='imported.pdf'
    pack_path.write_text(json.dumps(pack))
    monkeypatch.chdir(tmp_path)
    _,snapshots,_=_packs(ws,['OPS-L01'])
    _unchanged(dict(sources=snapshots,workspace_state=str(ws.state_dir)))
