"""Service recovery uses synthetic launchctl and a fake native CLI; no real audio."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from mynotes import capture, capture_dashboard
from test_capture import store


@pytest.mark.parametrize('enabled', [False, True])
def test_failed_bootstrap_preserves_previous_preferences(store, tmp_path, monkeypatch, enabled):
    config = store.config()
    config.update(enabled=enabled, paused=True)
    store.save_config(config)
    before = store.config()
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(capture, 'app_path', lambda: tmp_path / 'Capture.app')
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 5 if args[1] == 'bootstrap' else 0, '', 'Synthetic launch failure')

    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(capture.CaptureError, match='bootstrap failed'):
        capture.set_enabled(store, True)
    after = store.config()
    assert {k: v for k, v in after.items() if k != 'revision'} == {k: v for k, v in before.items() if k != 'revision'}
    assert [a[1] for a in calls] == ['bootout', 'bootstrap']


@pytest.mark.skipif(sys.platform != 'darwin' or not shutil.which('swiftc'), reason='macOS Swift compiler required')
def test_native_recovery_waits_for_exam_exit_and_allows_explicit_retry(store, tmp_path):
    state = capture_dashboard.dashboard(store)
    state.update(enabled=True, paused=True, running=False)
    # A crashed recorder can leave stale flags; they must not lock the button.
    state['runtime'] = {'recording': True, 'exporting': True, 'sessions': []}
    (tmp_path / 'snapshot.json').write_text(json.dumps(state))
    prefix = tmp_path / 'prefix'
    (prefix / 'bin').mkdir(parents=True)
    cli = prefix / 'bin/mynotes'
    cli.write_text('#!' + sys.executable + '\n' + r'''
import json, os, pathlib, sys, time
r=pathlib.Path(os.environ['RECOVERY_TEST_ROOT']); group, action=sys.argv[1:3]
f=r/'snapshot.json'; state=json.loads(f.read_text())
with (r/'calls').open('a') as log: log.write(group+' '+action+'\n')
if group=='capture' and action=='dashboard': out=state
elif group=='capture' and action=='enable':
 time.sleep(.15)
 if (r/'fail').exists(): print('Synthetic launch failure',file=sys.stderr);sys.exit(2)
 state['running']=True;state['runtime']={'recording':False,'exporting':False,'sessions':[]};f.write_text(json.dumps(state));out={'status':'ENABLED'}
elif group=='capture' and action=='start':out={'status':'RECORDING'}
elif group=='transcription-queue' and action=='status':out={'parallel':1,'paused':False,'ui_open':False,'jobs':[]}
else:sys.exit(2)
print(json.dumps(out))
''')
    cli.chmod(0o700)
    driver = tmp_path / 'Driver.swift'
    driver.write_text(r'''import Foundation
@main struct Driver {
 @MainActor static func main() async throws {
  let root=URL(fileURLWithPath: ProcessInfo.processInfo.environment["RECOVERY_TEST_ROOT"]!)
  func count(_ name: String) -> Int {
   ((try? String(contentsOf: root.appendingPathComponent("calls"), encoding: .utf8)) ?? "").components(separatedBy: "\n").filter { $0 == name }.count
  }
  func setState(_ enabled: Bool, _ running: Bool?) throws {
   let file=root.appendingPathComponent("snapshot.json")
   var data=try JSONSerialization.jsonObject(with: Data(contentsOf: file)) as! [String:Any]
   data["enabled"]=enabled;data["running"]=running.map { $0 as Any } ?? NSNull()
   try JSONSerialization.data(withJSONObject:data).write(to:file)
  }
  var exam=true
  let model=CaptureModel(examIsRunning: { exam })
  await model.refresh()
  precondition(model.snapshot?.canStartManualRecording == true, "Stale flags cannot lock a stopped recorder")
  _ = await model.restoreRecorderIfNeeded()
  precondition(model.recorderRecoveryBlocked && count("capture enable")==0)
  let examStart=await model.perform("start")
  let examEnable=await model.perform("enable")
  precondition(!examStart && !examEnable && count("capture start")==0 && count("capture enable")==0)
  exam=false
  let first=Task { await model.restoreRecorderIfNeeded() }
  try? await Task.sleep(for: .milliseconds(30))
  _ = await model.restoreRecorderIfNeeded()
  let restored=await first.value
  precondition(restored && model.snapshot?.running == true && model.snapshot?.paused == true)
  precondition(count("capture enable")==1 && !model.recorderRecoveryBlocked)
  _ = await model.restoreRecorderIfNeeded()
  precondition(count("capture enable")==1, "A live recorder must not be restarted")
  try setState(true, false)
  await model.refresh()
  FileManager.default.createFile(atPath:root.appendingPathComponent("fail").path,contents:Data())
  _ = await model.restoreRecorderIfNeeded()
  precondition(model.error != nil && count("capture enable")==2)
  _ = await model.restoreRecorderIfNeeded()
  precondition(count("capture enable")==2, "Failed recovery cannot become a retry loop")
  try FileManager.default.removeItem(at:root.appendingPathComponent("fail"))
  _ = await model.restoreRecorderIfNeeded(retry:true)
  precondition(model.snapshot?.running == true && count("capture enable")==3)
  try setState(false,false)
  await model.refresh()
  _ = await model.restoreRecorderIfNeeded(retry:true)
  precondition(count("capture enable")==3 && model.snapshot?.canStartManualRecording == false)
  try setState(true,nil)
  await model.refresh()
  _ = await model.restoreRecorderIfNeeded(retry:true)
  precondition(count("capture enable")==3, "Unknown process state must not restart an active recorder")
  try setState(true,false)
  await model.refresh()
  let manual=await model.perform("start")
  precondition(manual && count("capture enable")==4 && count("capture start")==1)
  print("PASS")
 }
}
''')
    source = Path(__file__).parents[1] / 'setup/Sources/MyNotesSetup'
    binary = tmp_path / 'recovery-test'
    build = subprocess.run(['swiftc', '-parse-as-library', '-swift-version', '5', '-module-cache-path', str(tmp_path / 'cache'),
                            str(source / 'CaptureDashboard.swift'), str(source / 'CaptureModel.swift'), str(driver), '-o', str(binary)], capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    env = {**os.environ, 'MYNOTES_PREFIX': str(prefix), 'MYNOTES_WORKSPACE': str(store.workspace.root),
           'MYNOTES_HOME': str(tmp_path / 'home'), 'RECOVERY_TEST_ROOT': str(tmp_path)}
    env.pop('MYNOTES_CAPTURE_PREVIEW', None)
    run = subprocess.run([str(binary)], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert 'PASS' in run.stdout
