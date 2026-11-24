"""Verify Swift role payloads round-trip through the Python preferences API."""
import json
import shutil
import subprocess
import sys
from pathlib import Path
import pytest
from mynotes.preferences import model_preferences, save_model_preferences, save_preferences
from mynotes.workspace import Workspace

pytestmark=pytest.mark.skipif(sys.platform!='darwin' or not shutil.which('swiftc'),reason='macOS Swift required')


def test_native_role_payload_preserves_explicit_automatic_and_null_inheritance(tmp_path):
    driver=tmp_path/'main.swift'
    driver.write_text('''import Foundation
let shared = ModelRoleSelection(provider: "codex", model: "shared", effort: "high")
let automatic = ModelRoleSelection(provider: "agy", model: "", effort: "")
let value = ModelRolesPayload(shared: shared, chat: nil, notes: automatic, language: "it")
print(String(decoding: try JSONEncoder().encode(value), as: UTF8.self))
''')
    sources=Path(__file__).parents[1]/'setup/Sources/MyNotesSetup'
    binary=tmp_path/'payload'
    subprocess.run(['swiftc','-module-cache-path',str(tmp_path/'cache'),str(sources/'ModelChoices.swift'),str(sources/'ModelRoles.swift'),str(driver),'-o',str(binary)],check=True,capture_output=True,text=True)
    payload=json.loads(subprocess.check_output([str(binary)],text=True))
    assert payload['overrides']['chat'] is None
    assert payload['overrides']['notes']==dict(provider='agy',model='',effort='')
    ws=Workspace(tmp_path/'workspace');ws.initialize();save_preferences(ws,'codex','shared','it',effort='high')
    result=save_model_preferences(ws,payload,model_preferences(ws)['revision'])
    assert result['effective']['chat']['origin']=='default'
    assert result['effective']['notes']==dict(provider='agy',model='',effort='',origin='custom')


def test_native_setup_saves_both_roles_and_launches_the_chat_role(tmp_path,monkeypatch):
    """Exercise the production SetupModel/CLI bridge; replace only Terminal opening."""
    import os
    import shlex
    root=Path(__file__).parents[1]
    sources=root/'setup/Sources/MyNotesSetup'
    ws=Workspace(tmp_path/'workspace');ws.initialize();save_preferences(ws,'codex','shared','it',effort='high')
    prefix=tmp_path/'prefix';(prefix/'bin').mkdir(parents=True)
    launcher=prefix/'bin/mynotes'
    launcher.write_text('#!/bin/zsh\n# MyNotes managed launcher\nexport PYTHONPATH='+shlex.quote(str(root/'src'))+'\nexec '+shlex.quote(sys.executable)+' -m mynotes "$@"\n')
    launcher.chmod(0o700)
    app=tmp_path/'SetupCode.swift';app.write_text((sources/'App.swift').read_text().replace('@main',''))
    stub=tmp_path/'TerminalStub.swift';stub.write_text('''import Foundation
struct ChatTerminal {
 @MainActor static func open(script: URL, directory: URL) async throws {
  let path=ProcessInfo.processInfo.environment["ROLE_TEST_CHAT_SCRIPT"]!
  try Data(contentsOf:script).write(to:URL(fileURLWithPath:path))
 }
}
''')
    driver=tmp_path/'Driver.swift';driver.write_text('''import Foundation
@main struct Driver {
 @MainActor static func main() async throws {
  let setup=SetupModel()
  setup.separateModels=true
  setup.chatOverride=ModelRoleSelection(provider:"claude",model:"orchestrator",effort:"high")
  setup.notesOverride=ModelRoleSelection(provider:"agy",model:"writer",effort:"medium")
  try setup.savePreferences()
  try await setup.launchChat()
  let fm=FileManager.default
  let version=setup.runtime.appendingPathComponent("app/versions/0.4.1")
  try fm.createDirectory(at:version,withIntermediateDirectories:true)
  try Data("{}".utf8).write(to:version.appendingPathComponent("installation.json"))
  try fm.createSymbolicLink(at:setup.runtime.appendingPathComponent("app/current"),withDestinationURL:version)
  try JSONSerialization.data(withJSONObject:["version":"0.4.0"]).write(to:setup.runtime.appendingPathComponent("setup.json"))
  precondition(setup.receiptIsCurrent(expectedVersion:"0.4.1"))
  precondition(!setup.receiptIsCurrent(expectedVersion:"0.4.2"))
 }
}
''')
    binary=tmp_path/'settings-bridge'
    paths=[p for p in sources.glob('*.swift') if p.name not in {'App.swift','ChatTerminal.swift'}]
    compiled=subprocess.run(['swiftc','-parse-as-library','-module-cache-path',str(tmp_path/'cache'),*[str(p) for p in paths],str(app),str(stub),str(driver),'-o',str(binary)],capture_output=True,text=True)
    assert compiled.returncode==0,compiled.stderr[-6000:]
    (tmp_path/'runtime').mkdir()
    environment=dict({key:os.environ[key] for key in ('PATH','TMPDIR','USER') if key in os.environ}, HOME=str(tmp_path), MYNOTES_HOME=str(tmp_path/'runtime'),MYNOTES_WORKSPACE=str(ws.root),MYNOTES_PREFIX=str(prefix),MYNOTES_MODEL_PRESETS=str(root/'src/mynotes/resources/model-presets.json'),ROLE_TEST_CHAT_SCRIPT=str(tmp_path/'chat-script.txt'))
    executed=subprocess.run([str(binary)],env=environment,capture_output=True,text=True,timeout=30)
    assert executed.returncode==0,executed.stderr[-4000:]
    saved=model_preferences(ws)
    assert saved['effective']['chat']['provider']=='claude' and saved['effective']['chat']['model']=='orchestrator'
    assert saved['effective']['notes']['provider']=='agy' and saved['effective']['notes']['model']=='writer'
    assert "'chat'" in (tmp_path/'chat-script.txt').read_text() or ' chat' in (tmp_path/'chat-script.txt').read_text()
    assert "'claude'" not in (tmp_path/'chat-script.txt').read_text()
