"""Native presentation and action routing; no microphone or ASR calls."""
import json,os,shutil,subprocess,sys
from pathlib import Path
import pytest
pytestmark=pytest.mark.skipif(sys.platform!='darwin' or not shutil.which('swiftc'),reason='macOS compiler required')

def test_native_filters_history_and_missing_job_never_enqueue(tmp_path):
    recordings=[]
    for index,state in enumerate(['NOT_STARTED','QUEUED','RUNNING','FAILED','NEEDS_REVIEW','WAITING','COMPLETED','CANCELLING']):
        recordings.append({'id':str(index),'path':'/synthetic.wav','title':'Example','status':state,'created_at':'2026-01-01T00:00:00Z','actions':['enqueue'] if index==0 else ['restart']})
    jobs=[{'id':ident,'recording_id':rec,'title':'Example','status':status} for ident,rec,status in [('old','A','FAILED'),('new','A','COMPLETED'),('wait','B','QUEUED'),('invalid','C','NEEDS_REVIEW'),('cancelled','D','CANCELLED')]]
    fixture=tmp_path/'fixture.json';fixture.write_text(json.dumps({'recordings':recordings,'queue':{'parallel':2,'paused':False,'ui_open':True,'jobs':jobs}}))
    driver=tmp_path/'main.swift';driver.write_text('''import Foundation
struct Fixture: Decodable { var recordings: [CaptureRecording]; var queue: CaptureQueue }
let f = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
func ids(_ filter: CaptureRecordingFilter) -> [String] { f.recordings.filter { filter.includes($0) }.map(\\.id) }
precondition(ids(.ready)==["0"])
precondition(ids(.active)==["1","2","7"])
precondition(!CaptureRecordingFilter.allCases.map(\\.rawValue).contains("review"))
precondition(ids(.completed)==["6"])
precondition(f.queue.currentJobs.count==4 && f.queue.activeJobs.map(\\.id)==["wait"])
precondition(f.queue.failedJobs.map(\\.id)==["invalid"])
precondition(Set(f.queue.historyJobs.map(\\.id))==Set(["old","new","cancelled"]))
precondition(f.recordings[3].arguments(for:"restart",parallel:2)==nil, "Missing job must never silently enqueue")
precondition(f.recordings[0].arguments(for:"enqueue",parallel:2)==["--recording","0","--parallel","2"])
precondition(f.recordings[0].arguments(for:"unknown",parallel:2)==nil)
print("PASS")
''')
    source=Path(__file__).parents[1]/'setup/Sources/MyNotesSetup/CaptureDashboard.swift';binary=tmp_path/'checks'
    built=subprocess.run(['swiftc','-module-cache-path',str(tmp_path/'cache'),str(source),str(driver),'-o',str(binary)],capture_output=True,text=True)
    assert built.returncode==0,built.stderr
    run=subprocess.run([str(binary),str(fixture)],capture_output=True,text=True)
    assert run.returncode==0,run.stderr


@pytest.mark.parametrize('fail_first', [False, True])
def test_native_bulk_ignores_errors_and_repeated_clicks(tmp_path, fail_first):
    rows=[{'id':str(i),'path':'/synthetic.wav','title':f'Audio {i}','status':state,'created_at':'2026-01-01T00:00:00Z','actions':actions} for i,state,actions in [(1,'NOT_STARTED',['enqueue']),(2,'NOT_STARTED',['enqueue']),(3,'FAILED',['retry'])]]
    (tmp_path/'rows.json').write_text(json.dumps(rows))
    prefix=tmp_path/'prefix';(prefix/'bin').mkdir(parents=True)
    cli=prefix/'bin/mynotes'
    cli.write_text('#!'+sys.executable+'\n'+'''import json,os,pathlib,sys,time
r=pathlib.Path(os.environ['UI_TEST_ROOT']);a=sys.argv[2]
if a=='enqueue':
 with (r/'calls').open('a') as f:f.write(sys.argv[sys.argv.index('--recording')+1]+'\\n')
 time.sleep(.15)
 if os.environ.get('UI_FAIL_FIRST')=='1' and sys.argv[sys.argv.index('--recording')+1]=='1':
  print('mynotes: Lecture id must use COURSE-LNN with an alphanumeric course code');raise SystemExit(1)
 out={'status':'QUEUED','added':1,'notices':[{'message':'Contesto della lezione non disponibile: trascrivo con il profilo del corso.'}]}
elif a=='dashboard':out={'schemaVersion':1,'configured':True,'enabled':True,'paused':False,'running':True,'runtime':{'recording':False,'exporting':False,'sessions':[]},'sources':[],'events':[],'courses':[],'refreshHours':6}
elif a=='status':out={'parallel':1,'paused':False,'ui_open':True,'jobs':[]}
elif a=='library':out={'recordings':json.loads((r/'rows.json').read_text())}
else:raise SystemExit(2)
print(json.dumps(out))
''');cli.chmod(0o700)
    driver=tmp_path/'Driver.swift';driver.write_text('''import Foundation
@main struct Driver {
 @MainActor static func main() async throws {
  let model = CaptureModel()
  model.openAudio("/nonexistent-mynotes-ui-test/audio.wav")
  precondition(model.error?.contains("Audio non disponibile") == true)
  let rows = try JSONDecoder().decode([CaptureRecording].self, from: Data(contentsOf: URL(fileURLWithPath: ProcessInfo.processInfo.environment["UI_TEST_ROOT"]!+"/rows.json")))
  await model.recordingAction("restart", item: rows[2])
  precondition(model.error != nil, "Missing attempt must produce an error without calling the CLI")
  let first = Task { await model.enqueueSelection(rows, selectedCount: 3) }
  try? await Task.sleep(for: .milliseconds(50))
  precondition(model.queueBusy)
  await model.enqueueSelection(rows, selectedCount: 3)
  await first.value
  precondition(!model.queueBusy)
  if ProcessInfo.processInfo.environment["UI_FAIL_FIRST"] == "1" {
   precondition(model.error?.contains("Associazione della lezione non valida") == true)
   precondition(model.notice?.contains("1 registrazione aggiunta") == true)
  } else {
   precondition(model.error == nil && model.notice?.contains("2 registrazioni aggiunte") == true)
  }
  precondition(model.notice?.contains("Contesto della lezione non disponibile") == true)
  precondition(model.notice?.contains("1 escluse") == true)
  print("PASS")
 }
}
''')
    source=Path(__file__).parents[1]/'setup/Sources/MyNotesSetup';binary=tmp_path/'bulk-test'
    built=subprocess.run(['swiftc','-parse-as-library','-swift-version','5','-target','arm64-apple-macosx14.0','-module-cache-path',str(tmp_path/'cache'),str(source/'CaptureDashboard.swift'),str(source/'CaptureModel.swift'),str(driver),'-o',str(binary)],capture_output=True,text=True)
    assert built.returncode==0,built.stderr
    env={**os.environ,'MYNOTES_PREFIX':str(prefix),'MYNOTES_WORKSPACE':str(tmp_path/'workspace'),'MYNOTES_HOME':str(tmp_path/'home'),'UI_TEST_ROOT':str(tmp_path), 'UI_FAIL_FIRST':'1' if fail_first else '0'}
    env.pop('MYNOTES_CAPTURE_PREVIEW',None)
    run=subprocess.run([str(binary)],env=env,cwd=tmp_path,capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stderr
    assert (tmp_path/'calls').read_text().splitlines()==['1','2']
