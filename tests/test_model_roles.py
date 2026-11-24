"""Shared and role-specific model settings; no provider generation."""
from copy import deepcopy
import json
import subprocess
import pytest
from mynotes.workspace import Workspace
from mynotes.preferences import model_preferences, save_model_preferences, save_preferences
from mynotes.configuration import ConfigurationError, validate_configuration
from mynotes.models import resolve_role_selection
from mynotes.workflow import resolve_model
from mynotes.chat import launch_chat
from mynotes.cli import main


def choice(provider='codex', model='chat-model', effort='high'):
    return dict(provider=provider,model=model,effort=effort)


@pytest.fixture
def ws(tmp_path):
    ws=Workspace(tmp_path/'private');ws.initialize()
    save_preferences(ws,'codex','shared','it',effort='high')
    return ws


def save(ws, chat=None, notes=None, shared=None):
    state=model_preferences(ws)
    return save_model_preferences(ws,dict(default=shared or state['default'],overrides=dict(chat=chat,notes=notes),language='it'),state['revision'])


def test_legacy_and_shared_changes_keep_custom_roles(ws):
    state=model_preferences(ws)
    assert state['effective']['chat']==state['effective']['notes']
    assert state['overrides']==dict(chat=None,notes=None)
    save(ws,notes=choice('agy','writing-model','medium'))
    save_preferences(ws,'codex','new-shared',effort='low')
    state=model_preferences(ws)
    assert state['effective']['chat']['model']=='new-shared'
    assert state['effective']['notes']['provider']=='agy'
    assert state['effective']['notes']['effort']=='medium'
    reset=save(ws)
    assert reset['effective']['chat']==reset['effective']['notes']


def test_independent_agents_models_efforts_and_explicit_provider_precedence(ws,monkeypatch):
    save(ws,chat=choice('codex','orchestrator','high'),notes=choice('agy','writer','medium'))
    cfg=ws.configuration_store().load()
    assert resolve_model(cfg,None,'standard').provider=='agy'
    assert resolve_model(cfg,'agy','standard').model=='writer'
    assert resolve_model(cfg,'codex','standard').model=='shared'
    calls=[]
    monkeypatch.setattr('mynotes.chat.shutil.which',lambda *a,**kw:'/bin/codex')
    monkeypatch.setattr('mynotes.chat.subprocess.run',lambda cmd,**kw:calls.append(cmd) or subprocess.CompletedProcess(cmd,0))
    launch_chat(ws,cfg,None)
    assert calls[0][calls[0].index('--model')+1]=='orchestrator'
    assert 'Never copy the chat agent' in calls[0][-1]
    assert '"provider": "agy"' in calls[0][-1]


def test_explicit_automatic_is_not_inheritance(ws):
    state=save(ws,chat=choice('claude','',''))
    assert state['effective']['chat']['model']==''
    save_preferences(ws,'codex','changed',effort='medium')
    assert model_preferences(ws)['effective']['chat']['provider']=='claude'


def test_atomic_conflict_and_invalid_payload_preserve_all_settings(ws):
    old=model_preferences(ws)
    save_preferences(ws,'codex','external-change')
    before=ws.configuration_path.read_bytes()
    payload=dict(default=old['default'],overrides=dict(chat=None,notes=choice('agy','writer','medium')),language='it')
    with pytest.raises(ConfigurationError,match='CONFIGURATION_CONFLICT'):
        save_model_preferences(ws,payload,old['revision'])
    assert ws.configuration_path.read_bytes()==before
    payload['overrides']['notes']['effort']='ultra'
    with pytest.raises(ConfigurationError):save_model_preferences(ws,payload,model_preferences(ws)['revision'])
    assert ws.configuration_path.read_bytes()==before


def test_read_only_show_and_structured_cli(ws,capsys):
    before={p:p.read_bytes() for p in ws.root.rglob('*') if p.is_file()}
    assert main(['preferences','--workspace',str(ws.root),'--show'])==0
    shown=json.loads(capsys.readouterr().out)
    assert before=={p:p.read_bytes() for p in ws.root.rglob('*') if p.is_file()}
    payload=dict(default=shown['default'],overrides=dict(chat=None,notes=choice('agy','writer','medium')),language='en')
    assert main(['preferences','--workspace',str(ws.root),'--models-json',json.dumps(payload),'--expected-revision',shown['revision']])==0
    assert json.loads(capsys.readouterr().out)['effective']['notes']['model']=='writer'
    assert main(['preferences','--workspace',str(ws.root),'--show','--provider','codex'])!=0


@pytest.mark.parametrize('roles',[[],{'other':None},{'chat':{}},{'notes':dict(provider='codex',model='bad name',effort='high')},{'notes':dict(provider=[],model='',effort='')}])
def test_invalid_saved_role_configuration_is_rejected(ws,roles):
    cfg=ws.configuration_store().load();cfg['providers']['roles']=roles
    with pytest.raises(ConfigurationError):validate_configuration(cfg)


def test_context_reports_both_roles_without_overriding_resume(ws):
    from mynotes.context import task_context,render_context
    save(ws,notes=choice('agy','writer','medium'))
    ctx=task_context(ws.configuration_store().load(),'notes')
    assert ctx['model_roles']['notes']['provider']=='agy'
    assert '"model": "writer"' in render_context(ctx)


def test_saved_batch_retains_writer_after_global_role_changes(tmp_path, monkeypatch):
    from test_notes_run import finished_batch
    from mynotes.notes_run import run_notes
    from mynotes.batch import load_batch_manifest
    ws,batch_id=finished_batch(tmp_path,monkeypatch)
    original=deepcopy(load_batch_manifest(ws,batch_id)['requested_model'])
    save(ws,chat=choice('claude','chat-other','high'),notes=choice('agy','new-writer','medium'))
    class NoGeneration:
        def generate(self,request):pytest.fail('Resume must not generate with the new model')
    result=run_notes(ws,'OPS',resume=batch_id,provider_adapter=NoGeneration())
    assert result['status'] in {'COMPLETED','COMPLETED_WITH_WARNINGS'},result.get('problem')
    assert load_batch_manifest(ws,batch_id)['requested_model']==original


def test_save_response_revision_cannot_adopt_a_later_concurrent_edit(ws,monkeypatch):
    from mynotes.configuration import ConfigurationStore
    original=ConfigurationStore.update
    def update_with_later_edit(store,transform,**kwargs):
        written=original(store,transform,**kwargs)
        def external(settings):
            settings['personal']['interface_language']='en'
            return settings
        original(store,external)
        return written
    monkeypatch.setattr(ConfigurationStore,'update',update_with_later_edit)
    saved=save(ws,notes=choice('agy','writer','medium'))
    assert saved['language']=='it'
    assert saved['revision']!=model_preferences(ws)['revision']
    payload=dict(default=saved['default'],overrides=saved['overrides'],language='it')
    with pytest.raises(ConfigurationError,match='CONFIGURATION_CONFLICT'):
        save_model_preferences(ws,payload,saved['revision'])
