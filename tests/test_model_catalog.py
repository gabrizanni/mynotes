"""Dynamic, provider-scoped catalogs and preference compatibility."""
import json
from pathlib import Path

import pytest

from mynotes import models, model_discovery as discovery
from mynotes.configuration import ConfigurationError
from mynotes.preferences import save_preferences
from mynotes.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('MYNOTES_HOME', str(tmp_path/'runtime'))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path/'codex'))
    monkeypatch.setattr(discovery, 'context_key', lambda *a: 'account-one')


def row(model='brand-new-release', efforts=None, default='deep_next'):
    return {'model':model,'label':'New release','default_effort':default,'supported_efforts':efforts or ['low','deep_next'], 'effort_scope':'model','is_default':False}


def report(entries=None):
    return {'entries':entries or [row()], 'source':'fixture_cli','freshness':'cli_catalog','account':{'status':'connected','label':'ChatGPT','plan':'plus','kind':'chatgpt'}}


def test_presets_are_shortcuts_not_phantom_models(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery,'discover',lambda *_:report())
    for provider, labels in [('codex',['GPT Terra High']),('agy',['Gemini Flash 3.8 High','Gemini Flash 3.8 Medium']),('claude',[])]:
        result=models.catalog(provider,refresh=True)
        assert [p['label'] for p in result['presets']]==labels
        assert all(c['provider']==provider for c in result['choices'])
        assert not any(p['selectable'] for p in result['presets'])
        assert [c['model'] for c in result['choices'] if c['model']]==['brand-new-release']
        assert result['schema_version']==2 and result['access_verified'] is False


def test_new_release_and_new_effort_appear_without_app_changes(tmp_path, monkeypatch):
    responses=iter([report([row('old-release')]), report([row('old-release'),row('never-coded-in-mynotes')])])
    calls=[]
    def live(*args):calls.append(args);return next(responses)
    monkeypatch.setattr(discovery,'discover',live)
    first=models.catalog('codex',refresh=True)
    second=models.catalog('codex',refresh=True,saved='old-release',saved_effort='low')
    assert 'never-coded-in-mynotes' not in [c['model'] for c in first['choices']]
    new=next(c for c in second['choices'] if c['model']=='never-coded-in-mynotes')
    assert new['supported_efforts']==['low','deep_next'] and new['default_effort']=='deep_next'
    assert len(calls)==2
    workspace=Workspace(tmp_path/'private')
    saved=save_preferences(workspace,'codex','never-coded-in-mynotes',effort='deep_next')
    assert saved['effort']=='deep_next'
    assert models.catalog('codex')['status']=='stale'


def test_opening_or_saving_never_discovers_models(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery,'discover',lambda *_:pytest.fail('Only an explicit refresh may discover models'))
    monkeypatch.setattr(discovery,'context_key',lambda *_:pytest.fail('Offline list must not inspect account changes'))
    result=models.catalog('agy',saved='saved-custom-model',saved_effort='high')
    assert all(c['selectable'] for c in result['choices'])
    assert {'gemini-3.8-flash-high','gemini-3.8-flash-medium','saved-custom-model'}.issubset({c['model'] for c in result['choices']})
    save_preferences(Workspace(tmp_path/'private'),'agy','saved-custom-model',effort='high')


def test_membership_changes_only_after_successful_refresh(monkeypatch):
    monkeypatch.setattr(discovery,'discover',lambda *_:report([row('old')]))
    models.catalog('codex',refresh=True)
    monkeypatch.setattr(discovery,'discover',lambda *_:report([row('new')]))
    assert models.catalog('codex')['choices'][0]['model']=='old'
    fresh=models.catalog('codex',refresh=True,saved='old')
    assert fresh['choices'][0]['model']=='new'
    assert not next(c for c in fresh['choices'] if c['model']=='old')['selectable']
    assert not next(c for c in models.catalog('codex',saved='old')['choices'] if c['model']=='old')['selectable']


def test_failed_refresh_does_not_disable_cached_models(monkeypatch):
    monkeypatch.setattr(discovery,'discover',lambda *_:report([row('stable')]))
    models.catalog('agy',refresh=True)
    def fail(*_): raise discovery.DiscoveryError('not_installed','Temporarily missing CLI')
    monkeypatch.setattr(discovery,'discover',fail)
    result=models.catalog('agy',refresh=True)
    assert result['choices'][0]['model']=='stable' and result['choices'][0]['selectable']


def test_codex_cache_is_explicitly_previous_and_preserves_real_capabilities(tmp_path):
    cache=tmp_path/'cache';cache.mkdir()
    (cache/'models_cache.json').write_text(json.dumps({'models':[
        {'slug':'gpt-5.6-terra','display_name':'GPT Terra','visibility':'list','default_reasoning_level':'medium','supported_reasoning_levels':[{'effort':'medium'},{'effort':'high'}]},
        {'slug':'new','visibility':'list','default_reasoning_level':'future_effort','supported_reasoning_levels':[{'effort':'future_effort'}]},
        {'slug':'hidden','visibility':'hide'},None]}))
    result=models.catalog('codex',codex_home=cache,saved='old')
    assert result['status']=='stale' and result['source']=='codex_local_cache'
    assert next(c for c in result['choices'] if c['model']=='gpt-5.6-terra')['default_effort']=='medium'
    assert result['presets'][0]['default_effort']=='high' and result['presets'][0]['selectable']
    assert not any(c['model']=='hidden' for c in result['choices'])
    assert next(c for c in result['choices'] if c['model']=='old')['selectable']


@pytest.mark.parametrize('cache',['not json','[]','{"models":{}}'])
def test_explicit_corrupt_legacy_cache_preserves_saved_choice(tmp_path,cache):
    (tmp_path/'models_cache.json').write_text(cache)
    result=models.catalog('codex',codex_home=tmp_path,saved='old-model')
    assert result['status']=='no_catalog'
    assert all(c['selectable'] for c in result['choices'])
    assert result['presets'][0]['label']=='GPT Terra High'


def test_failed_refresh_retains_last_good_snapshot_and_choice(monkeypatch):
    monkeypatch.setattr(discovery,'discover',lambda *_:report())
    first=models.catalog('codex',refresh=True)
    def failed(*_):raise discovery.DiscoveryError('error','Retry')
    monkeypatch.setattr(discovery,'discover',failed)
    second=models.catalog('codex',refresh=True,saved='brand-new-release',saved_effort='low')
    assert second['status']=='stale' and second['fetched_at']==first['fetched_at']
    assert second['choices'][0]['model']=='brand-new-release' and second['message']=='Retry'


def test_removed_model_stays_visible_but_cannot_start_new_request(monkeypatch):
    monkeypatch.setattr(discovery,'discover',lambda *_:report())
    models.catalog('agy',refresh=True)
    monkeypatch.setattr(discovery,'discover',lambda *_:report([row('replacement')]))
    result=models.catalog('agy',refresh=True,saved='brand-new-release',saved_effort='high')
    old=next(c for c in result['choices'] if c['model']=='brand-new-release')
    assert old['availability']=='saved_unavailable' and not old['selectable']


def test_account_change_does_not_change_list_without_refresh(monkeypatch):
    monkeypatch.setattr(discovery,'discover',lambda *_:report())
    models.catalog('agy',refresh=True)
    monkeypatch.setattr(discovery,'context_key',lambda *a:'other-account')
    result=models.catalog('agy',saved='brand-new-release')
    assert result['status']=='stale' and all(c['selectable'] for c in result['choices'])
    assert result['choices'][0]['model']=='brand-new-release'


def test_login_error_is_not_hidden_by_old_catalog(monkeypatch):
    monkeypatch.setattr(discovery,'discover',lambda *_:report());models.catalog('agy',refresh=True)
    def login(*_):raise discovery.DiscoveryError('login_required','Sign in')
    monkeypatch.setattr(discovery,'discover',login)
    result=models.catalog('agy',refresh=True)
    assert result['status']=='login_required' and all(c['selectable'] for c in result['choices'])


def test_non_installed_claude_does_not_invent_models(monkeypatch):
    def missing(*_):raise discovery.DiscoveryError('not_installed','Install')
    monkeypatch.setattr(discovery,'discover',missing)
    result=models.catalog('claude',refresh=True,saved='sonnet')
    assert result['status']=='not_installed' and not result['presets']
    assert all(c['selectable'] for c in result['choices'])


def test_defaults_and_explicit_preferences_remain_compatible():
    settings={'providers':{'configured':{'codex':{},'agy':{},'claude':{}}}}
    before=json.dumps(settings)
    assert models.resolve_provider_options(settings,'codex')==('gpt-5.6-terra','high')
    assert models.resolve_provider_options(settings,'agy')==('gemini-3.8-flash-high','high')
    assert models.resolve_provider_options(settings,'claude')==('','')
    assert json.dumps(settings)==before
    settings['providers']['configured']['codex']={'model':'older-model'}
    assert models.resolve_provider_options(settings,'codex')==('older-model','')


def test_atomic_presets_and_provider_default(tmp_path):
    workspace=Workspace(tmp_path/'private')
    assert save_preferences(workspace,'agy','',preset='gemini-flash-3.8-medium')['effort']=='medium'
    save_preferences(workspace,'agy','gemini-3.8-flash-medium',effort='high')
    save_preferences(workspace,'codex','',preset='gpt-terra-high')
    save_preferences(workspace,'codex','',effort='')
    assert models.resolve_provider_options(workspace.configuration_store().load(),'codex')==('','')


@pytest.mark.parametrize('provider,preset,effort',[('agy',None,'ultra'),('codex',None,'invalid'),('codex','gemini-flash-3.8-high',None)])
def test_invalid_preferences_do_not_initialize_workspace(tmp_path,provider,preset,effort):
    workspace=Workspace(tmp_path/'private')
    with pytest.raises(ConfigurationError):save_preferences(workspace,provider,'',effort=effort,preset=preset)
    assert not workspace.root.exists()


def test_unsupported_effort_rejected_before_write(tmp_path,monkeypatch):
    monkeypatch.setattr(discovery,'discover',lambda *_:report([row('limited-model',['low'],'low')]))
    models.catalog('codex',refresh=True)
    workspace=Workspace(tmp_path/'private')
    with pytest.raises(ConfigurationError):save_preferences(workspace,'codex','limited-model',effort='high')
    assert not workspace.root.exists()
