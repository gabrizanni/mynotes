"""Native asynchronous UI transport: private JSON inputs and stop during sync."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mynotes import capture, capture_dashboard
from test_capture import store, event

pytestmark=pytest.mark.skipif(sys.platform!='darwin' or not shutil.which('swiftc'),reason='macOS Swift compiler required')


def test_native_model_uses_private_input_and_stop_is_not_blocked_by_sync(store,tmp_path):
    capture.import_schedule(store,[event()]);snapshot=capture_dashboard.dashboard(store)
    snapshot.update(enabled=True,running=True)
    snapshot['runtime']={'recording':True,'exporting':False,'currentSession':{'id':'attempt','event':{'id':snapshot['events'][0]['id'],'courseCode':'MATH','lessonNumber':1},'status':'RECORDING'}}
    state=tmp_path/'snapshot.json';state.write_text(json.dumps(snapshot))
    prefix=tmp_path/'prefix';(prefix/'bin').mkdir(parents=True)
    launcher=prefix/'bin/mynotes'
    launcher.write_text('#!'+sys.executable+'\n'+'''import json,os,pathlib,sys,time
root=pathlib.Path(os.environ['UI_TEST_ROOT']);state=root/'snapshot.json';data=json.loads(state.read_text());action=sys.argv[2]
with (root/'calls.txt').open('a') as f:f.write(action+'\\n')
if action=='dashboard':print(json.dumps(data))
elif action=='add-lesson':
 file=pathlib.Path(sys.argv[sys.argv.index('--file')+1]);payload=json.loads(file.read_text())
 (root/'input-proof.json').write_text(json.dumps({'payload':payload,'mode':file.stat().st_mode & 0o777,'path':str(file)}))
 print(json.dumps({'status':'ADDED','eventID':'special:test'}))
elif action=='sync':
 time.sleep(2);print(json.dumps({'status':'SYNCED','sources':[]}))
elif action=='stop':
 data['runtime']={'recording':False,'exporting':False,'sessions':[]};state.write_text(json.dumps(data))
 print(json.dumps({'status':'PARTIAL','message':'Audio saved'}))
elif action=='status':print(json.dumps({'running':True,'enabled':True,'paused':False,'runtime':data['runtime']}))
else:raise SystemExit(2)
''')
    launcher.chmod(0o700)
    driver=tmp_path/'Driver.swift';driver.write_text('''import Foundation
@main struct Driver {
 @MainActor static func main() async {
  let model = CaptureModel()
  await model.refresh()
  precondition(model.snapshot?.isRecording == true)
  let hostile = "text'; touch SHOULD_NOT_EXIST; $(echo hidden)"
  let saved = await model.perform("add-lesson", payload: ["title": hostile])
  precondition(saved && model.lastEventID == "special:test")
  let sync = Task { await model.perform("sync") }
  try? await Task.sleep(for: .milliseconds(150))
  precondition(model.busy)
  await model.stopNow()
  precondition(model.busy && model.snapshot?.isRecording == false, "Stop must complete while sync is still running")
  _ = await sync.value
  precondition(model.snapshot?.isRecording == false && model.error == nil)
  print("PASS")
 }
}
''')
    source=Path(__file__).parents[1]/'setup/Sources/MyNotesSetup';binary=tmp_path/'model-test'
    result=subprocess.run(['swiftc','-parse-as-library','-swift-version','5','-target','arm64-apple-macosx14.0','-module-cache-path',str(tmp_path/'module-cache'),str(source/'CaptureDashboard.swift'),str(source/'CaptureModel.swift'),str(driver),'-o',str(binary)],text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    environment={**os.environ,'MYNOTES_PREFIX':str(prefix),'MYNOTES_WORKSPACE':str(store.workspace.root),'MYNOTES_HOME':str(tmp_path/'home'),'UI_TEST_ROOT':str(tmp_path)}
    environment.pop('MYNOTES_CAPTURE_PREVIEW',None)
    result=subprocess.run([str(binary)],env=environment,cwd=tmp_path,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
    proof=json.loads((tmp_path/'input-proof.json').read_text())
    assert proof['mode']==0o600 and '$(echo hidden)' in proof['payload']['title']
    assert not Path(proof['path']).exists() and not (tmp_path/'SHOULD_NOT_EXIST').exists()
    assert 'PASS' in result.stdout
