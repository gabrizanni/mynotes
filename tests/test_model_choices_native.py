"""Native decoder accepts future models/efforts and separates preset shortcuts."""
import json
import shutil
import subprocess
import sys
from pathlib import Path
import pytest
from mynotes import models, model_discovery as discovery

pytestmark=pytest.mark.skipif(sys.platform!='darwin' or not shutil.which('swiftc'),reason='macOS Swift required')

@pytest.fixture(scope='module')
def native(tmp_path_factory):
    root=tmp_path_factory.mktemp('model-choice-native');driver=root/'main.swift'
    driver.write_text('''import Foundation
let args = CommandLine.arguments
let catalog = args[1] == "json" ? try ModelChoices.decode(Data(contentsOf: URL(fileURLWithPath: args[2]))) : ModelChoices.bundled(provider: args[1], path: URL(fileURLWithPath: args[2]), saved: args[3], effort: args[4])
let value: [String:Any] = ["ids": catalog.choices.map { $0.id }, "labels": catalog.choices.map { $0.label }, "presets": catalog.presets.map { $0.label }, "supported": catalog.choices.map { $0.supportedEfforts }, "defaults": catalog.choices.map { $0.defaultEffort }, "selectable": catalog.choices.map { $0.selectable }, "valid": catalog.choices.map { ModelChoices.selectionValid($0, effort: args[4]) }, "syntax": ModelChoices.valid(args[3])]
print(String(data: try JSONSerialization.data(withJSONObject: value), encoding: .utf8)!)
''')
    source=Path(__file__).parents[1]/'setup/Sources/MyNotesSetup/ModelChoices.swift';binary=root/'decoder'
    result=subprocess.run(['swiftc','-module-cache-path',str(root/'cache'),str(source),str(driver),'-o',str(binary)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    def run(provider,source,saved='',effort=''):
        result=subprocess.run([str(binary),provider,str(source),saved,effort],capture_output=True,text=True)
        assert result.returncode==0,result.stderr
        return json.loads(result.stdout)
    return run


def test_python_catalog_passes_through_future_model_and_effort(tmp_path,monkeypatch,native):
    monkeypatch.setenv('MYNOTES_HOME',str(tmp_path/'runtime'))
    monkeypatch.setattr(discovery,'discover',lambda *_:{'entries':[{'model':'never-hardcoded-model','label':'Future model','supported_efforts':['novel_effort'],'default_effort':'novel_effort'}],'source':'fixture_cli','account':{}})
    value=models.catalog('codex',refresh=True,saved='removed-model',saved_effort='high')
    source=tmp_path/'catalog.json';source.write_text(json.dumps(value))
    native_value=native('json',source,effort='novel_effort')
    assert native_value['ids']==[c['id'] for c in value['choices']]
    assert native_value['supported'][0]==['novel_effort'] and native_value['valid'][0]
    assert native_value['presets']==['GPT Terra High']
    assert native_value['selectable'][-1] is False


@pytest.mark.parametrize('provider,presets',[('codex',['GPT Terra High']),('agy',['Gemini Flash 3.8 High','Gemini Flash 3.8 Medium']),('claude',[])])
def test_bundled_choices_are_selectable_without_discovery(native,provider,presets):
    source=Path(__file__).parents[1]/'src/mynotes/resources/model-presets.json'
    value=native(provider,source,'saved-model','high')
    assert value['presets']==presets and value['ids'][-2:]==['','saved-model']
    assert len(value['ids'])==len(presets)+2
    assert all(value['selectable'])


@pytest.mark.parametrize('candidate,valid',[('',True),('sonnet[1m]',True),('model\n',False),('--model',False),('invalid model',False)])
def test_safe_identifiers_are_not_a_model_name_allowlist(tmp_path,native,candidate,valid):
    assert native('agy',tmp_path/'missing.json',candidate)['syntax']==valid
