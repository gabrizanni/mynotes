"""Exercise native polling with a delayed launcher; no recorder or ASR is started."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from mynotes import capture_dashboard
from test_capture import store

pytestmark = pytest.mark.skipif(sys.platform != 'darwin' or not shutil.which('swiftc'), reason='macOS Swift compiler required')


def test_native_silent_refresh_coalesces_and_rejects_stale_state(store, tmp_path):
    snapshot = capture_dashboard.dashboard(store)
    snapshot.update(enabled=True, running=True)
    snapshot['runtime'] = {'recording': False, 'exporting': False, 'sessions': []}
    (tmp_path / 'snapshot.json').write_text(json.dumps(snapshot))
    (tmp_path / 'library.json').write_text(json.dumps({'recordings': [
        {'id': 'historical', 'path': '/private/tmp/historical.m4a', 'title': 'Earlier lesson', 'status': 'NOT_STARTED',
         'course_code': 'MATH', 'lecture_id': 'MATH-L08', 'created_at': '2026-10-01T00:00:00Z'}
    ]}))
    prefix = tmp_path / 'prefix'
    (prefix / 'bin').mkdir(parents=True)
    launcher = prefix / 'bin/mynotes'
    launcher.write_text('#!' + sys.executable + '\n' + r'''
import json,os,pathlib,sys,time
root=pathlib.Path(os.environ['UI_TEST_ROOT']); group,action=sys.argv[1:3]
state=root/'snapshot.json';data=json.loads(state.read_text())
with (root/'calls.txt').open('a') as f:f.write(group+' '+action+'\n')
if group=='capture' and action=='dashboard':
 if (root/'fail-dashboard').exists():
  print('private diagnostic that must not replace user errors',file=sys.stderr);sys.exit(2)
 if (root/'hold-dashboard').exists():
  (root/'read-started').touch()
  deadline=time.monotonic()+8
  while (root/'hold-dashboard').exists() and time.monotonic()<deadline:time.sleep(.01)
 print(json.dumps(data))
elif group=='transcription-queue' and action=='status':
 queue={'parallel':1,'paused':False,'ui_open':False,'jobs':[]}
 if (root/'queue.json').exists():queue=json.loads((root/'queue.json').read_text())
 print(json.dumps(queue))
elif group=='transcription-queue' and action=='library':print((root/'library.json').read_text())
elif group=='transcription-queue' and action=='enqueue':print((root/'queue-response.json').read_text())
elif group=='transcription-queue' and action=='rename':
 print('mynotes: Finish the active job before changing it');sys.exit(2)
elif group=='capture' and action=='connect':
 print('failed private https://calendar.google.com/calendar/ical/private-token/basic.ics',file=sys.stderr);sys.exit(2)
elif group=='capture' and action=='stop':
 data['runtime']={'recording':False,'exporting':False,'sessions':[]};state.write_text(json.dumps(data))
 print(json.dumps({'status':'COMPLETED'}))
elif group=='capture' and action=='status':print(json.dumps({'running':True,'enabled':True,'paused':False,'runtime':data['runtime']}))
elif group=='capture' and action=='pause':
 data['paused']=True;state.write_text(json.dumps(data));print(json.dumps({'status':'PAUSED'}))
else:sys.exit(2)
''')
    launcher.chmod(0o700)
    driver = tmp_path / 'Driver.swift'
    driver.write_text(r'''import Foundation
import Combine
@main struct Driver {
 @MainActor static func main() async throws {
  let root = URL(fileURLWithPath: ProcessInfo.processInfo.environment["UI_TEST_ROOT"]!)
  func mark(_ name: String) { FileManager.default.createFile(atPath: root.appendingPathComponent(name).path, contents: Data()) }
  func remove(_ name: String) { try? FileManager.default.removeItem(at: root.appendingPathComponent(name)) }
  func exists(_ name: String) -> Bool { FileManager.default.fileExists(atPath: root.appendingPathComponent(name).path) }
  func calls(_ name: String) -> Int {
   ((try? String(contentsOf: root.appendingPathComponent("calls.txt"), encoding: .utf8)) ?? "").components(separatedBy: "\n").filter { $0 == name }.count
  }
  func waitForRead() async {
   for _ in 0..<400 { if exists("read-started") { return }; try? await Task.sleep(for: .milliseconds(10)) }
   preconditionFailure("Delayed dashboard did not start")
  }
  func updateRuntime(_ runtime: [String: Any]) throws {
   let path = root.appendingPathComponent("snapshot.json")
   var data = try JSONSerialization.jsonObject(with: Data(contentsOf: path)) as! [String: Any]
   data["runtime"] = runtime
   try JSONSerialization.data(withJSONObject: data).write(to: path, options: .atomic)
  }
  let model = CaptureModel()
  await model.refresh()
  precondition(model.snapshot != nil && model.refreshWarning == nil && !model.busy)
  precondition(calls("transcription-queue library") == 0, "Calendar reads must not scan the archive")
  precondition(model.pollingInterval == 15)
  var changes = 0
  let subscription = model.objectWillChange.sink { changes += 1 }
  let clock = Date(timeIntervalSince1970: 1800000000)
  precondition(model.claimPoll(at: clock, force: true))
  precondition(!model.claimPoll(at: clock.addingTimeInterval(14)))
  precondition(model.claimPoll(at: clock.addingTimeInterval(15)))
  precondition(changes == 0, "Advancing the polling clock must not invalidate the view")
  await model.refresh(silent: true)
  precondition(changes == 0, "Identical silent reads must publish nothing")

  remove("read-started"); mark("hold-dashboard")
  let before = calls("capture dashboard")
  let first = Task { await model.refresh(silent: true) }
  await waitForRead()
  precondition(!model.busy && !model.queueBusy, "Polling must leave controls usable")
  let second = Task { await model.refresh(silent: true) }
  try? await Task.sleep(for: .milliseconds(70))
  precondition(calls("capture dashboard") == before + 1, "Overlapping timer requests must share one read")
  remove("hold-dashboard")
  await first.value; await second.value
  precondition(changes == 0 && calls("transcription-queue library") == 0)

  model.error = "Keep the action error"; model.notice = "Keep the action result"
  mark("fail-dashboard")
  let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
  let lastSnapshot = try encoder.encode(model.snapshot)
  await model.refresh(silent: true)
  precondition(model.refreshWarning != nil && model.error == "Keep the action error" && model.notice == "Keep the action result")
  let retainedSnapshot = try encoder.encode(model.snapshot)
  precondition(retainedSnapshot == lastSnapshot, "Failed reads retain the last snapshot")
  changes = 0
  await model.refresh(silent: true)
  precondition(changes == 0, "Repeated failures do not repeatedly publish the same warning")
  remove("fail-dashboard")
  await model.refresh(silent: true)
  precondition(model.refreshWarning == nil && model.error == "Keep the action error")

  let prepared = await model.prepareLesson()
  precondition(prepared && model.snapshot?.nextNumber("MATH") == 9, "Forms must include archived lesson numbers")
  precondition(calls("transcription-queue library") == 1)
  let queue = "{\"parallel\":1,\"paused\":false,\"ui_open\":false,\"jobs\":[{\"id\":\"q\",\"recording_id\":\"historical\",\"title\":\"Test\",\"status\":\"RUNNING\"}]}"
  try Data(queue.utf8).write(to: root.appendingPathComponent("queue.json"))
  await model.refresh(silent: true)
  precondition(model.pollingInterval == 5)
  changes = 0
  precondition(model.claimPoll(at: clock, force: true))
  precondition(!model.claimPoll(at: clock.addingTimeInterval(4)))
  precondition(model.claimPoll(at: clock.addingTimeInterval(5)))
  precondition(changes == 0, "Active polling bookkeeping must not publish changes either")
  remove("queue.json")

  try updateRuntime(["recording":false,"exporting":true,"sessions":[]])
  await model.refresh(silent: true)
  precondition(model.pollingInterval == 5)
  let scanned = calls("transcription-queue library")
  try updateRuntime(["recording":false,"exporting":false,"sessions":[]])
  await model.refresh(silent: true)
  precondition(calls("transcription-queue library") == scanned + 1, "Completed exports refresh the archive once")
  await model.refresh(silent: true)
  precondition(calls("transcription-queue library") == scanned + 1 && model.pollingInterval == 15)

  try updateRuntime(["recording":true,"exporting":false,"sessions":[]])
  await model.refresh(silent: true)
  remove("read-started"); mark("hold-dashboard")
  let stale = Task { await model.refresh(silent: true) }
  await waitForRead()
  await model.stopNow()
  precondition(model.snapshot?.isRecording == false, "Stop must not wait for the dashboard lock")
  remove("hold-dashboard")
  await stale.value
  await model.refresh(silent: true)
  precondition(model.snapshot?.isRecording == false, "A stale response must not undo Stop")

  remove("read-started"); mark("hold-dashboard")
  let preAction = Task { await model.refresh(silent: true) }
  await waitForRead()
  let action = Task { await model.perform("pause") }
  try? await Task.sleep(for: .milliseconds(70))
  precondition(model.busy, "An action can start while a silent read is in progress")
  remove("hold-dashboard")
  await preAction.value
  let succeeded = await action.value
  precondition(succeeded && model.snapshot?.paused == true && !model.busy)
  func queueResponse(_ payload: [String: Any]) throws {
   try JSONSerialization.data(withJSONObject: payload).write(to: root.appendingPathComponent("queue-response.json"), options: .atomic)
  }
  try queueResponse(["status":"FAILED", "message":"Audio non disponibile. Ripristina il file."])
  await model.queueAction("enqueue")
  precondition(model.error == "Audio non disponibile. Ripristina il file." && model.notice == nil)
  await model.refresh(silent: true)
  precondition(model.error == "Audio non disponibile. Ripristina il file.", "A silent refresh retains queue action errors")
  try queueResponse(["status":"QUEUED", "added":0, "skipped":[["reason":"NEEDS_REVIEW", "blocking_reasons":[["code":"AUDIO_NOT_FINAL", "message":"Attendi la fine del salvataggio."]]]]])
  await model.queueAction("enqueue")
  precondition(model.error?.contains("Nessuna nuova trascrizione aggiunta.") == true && model.error?.contains("Attendi la fine del salvataggio.") == true && model.notice == nil)
  try queueResponse(["status":"QUEUED", "added":0, "skipped":[]])
  await model.queueAction("enqueue")
  precondition(model.error == nil && model.notice == "Nessuna nuova trascrizione aggiunta alla coda.")
  try queueResponse(["status":"QUEUED", "added":1, "skipped":[]])
  await model.queueAction("enqueue")
  precondition(model.error == nil && model.notice == "1 registrazione aggiunta alla coda.")
  await model.queueAction("rename")
  precondition(model.error == "mynotes: Finish the active job before changing it" && model.notice == nil, "Plain CLI stdout errors must remain visible")
  let connected = await model.perform("connect", payload:["url":"https://calendar.google.com/calendar/ical/private-token/basic.ics"])
  precondition(!connected && model.error?.contains("private-token") == false && model.error?.contains("https://") == false, "Calendar diagnostics must never expose private URLs")
  withExtendedLifetime(subscription) {}
  print("PASS")
 }
}
''')
    source = Path(__file__).parents[1] / 'setup/Sources/MyNotesSetup'
    binary = tmp_path / 'refresh-test'
    result = subprocess.run([
        'swiftc', '-parse-as-library', '-swift-version', '5', '-target', 'arm64-apple-macosx14.0',
        '-module-cache-path', str(tmp_path / 'module-cache'), str(source / 'CaptureDashboard.swift'),
        str(source / 'CaptureModel.swift'), str(driver), '-o', str(binary)
    ], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    environment = {**os.environ, 'MYNOTES_PREFIX': str(prefix), 'MYNOTES_WORKSPACE': str(store.workspace.root),
                   'MYNOTES_HOME': str(tmp_path / 'home'), 'UI_TEST_ROOT': str(tmp_path)}
    environment.pop('MYNOTES_CAPTURE_PREVIEW', None)
    result = subprocess.run([str(binary)], env=environment, cwd=tmp_path, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'PASS' in result.stdout
