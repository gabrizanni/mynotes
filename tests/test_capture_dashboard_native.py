"""Run the native dashboard decoder against the actual Python JSON contract."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mynotes import capture, capture_dashboard
from test_capture import store, event

pytestmark=pytest.mark.skipif(sys.platform!='darwin' or not shutil.which('swiftc'),reason='macOS Swift compiler required')


@pytest.fixture(scope='module')
def native(tmp_path_factory):
    root=tmp_path_factory.mktemp('capture-native-ui');main=root/'main.swift'
    main.write_text('''import Foundation
do {
 let value = try CaptureSnapshot.decode(Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
 let period = CaptureDates.calendarPeriod(CaptureDates.read("2026-10-14T10:00:00Z")!, CaptureDates.read("2026-10-15T10:00:00Z")!, zone: "Asia/Kolkata")!
 let result: [String: Any] = ["state": value.stateLabel, "next": value.nextNumber("MATH"), "editable": value.events.map { value.editable($0) }, "statuses": value.events.map { $0.statusLabel }, "timezone": CaptureDates.label(value.events.first?.startsAt, zone: "UTC"), "periodStart": CaptureDates.write(period.0), "periodEnd": CaptureDates.write(period.1)]
 print(String(data: try JSONSerialization.data(withJSONObject: result), encoding: .utf8)!)
} catch { fputs("INVALID\\n", stderr); exit(2) }
''')
    binary=root/'decoder';helper=Path(__file__).parents[1]/'setup/Sources/MyNotesSetup/CaptureDashboard.swift'
    built=subprocess.run(['swiftc','-module-cache-path',str(root/'module-cache'),str(helper),str(main),'-o',str(binary)],text=True,capture_output=True)
    assert built.returncode==0,built.stderr
    def run(payload):
        fixture=root/'snapshot.json';fixture.write_text(json.dumps(payload))
        return subprocess.run([str(binary),str(fixture)],text=True,capture_output=True)
    return run


def test_actual_snapshot_decodes_dates_numbers_and_source_status(store,native):
    capture.import_schedule(store,[event(),event(2,'2026-10-12T12:00:00Z','2026-10-12T13:00:00Z')])
    value=capture_dashboard.dashboard(store);result=native(value)
    assert result.returncode==0,result.stderr
    payload=json.loads(result.stdout);assert payload['next']==3 and payload['statuses']==['Programmata','Programmata']
    assert payload['editable']==[True,True] and payload['state']=='Capture disattivato'
    assert payload['periodStart']=='2026-10-13T18:30:00Z' and payload['periodEnd']=='2026-10-15T18:30:00Z'


def test_native_unknown_process_and_active_lesson_are_not_reported_as_off(store,native):
    capture.import_schedule(store,[event()]);value=capture_dashboard.dashboard(store)
    value.update(running=None,enabled=True);value['runtime']={'recording':True,'currentSession':{'id':'one','event':{'id':value['events'][0]['id'],'courseCode':'MATH','lessonNumber':1},'status':'RECORDING'}}
    result=native(value);payload=json.loads(result.stdout)
    assert payload['state']=='Stato del processo non verificabile' and payload['editable']==[False]
    value['running']=True;payload=json.loads(native(value).stdout);assert payload['state']=='Registrazione in corso'


def test_native_invalid_dates_fail_instead_of_inventing_order(store,native):
    capture.import_schedule(store,[event()]);value=capture_dashboard.dashboard(store);value['events'][0]['startsAt']='invalid'
    assert native(value).returncode==2


def test_future_calendar_problem_is_not_a_failed_transcription(store,native):
    capture.import_schedule(store,[event()])
    value=capture_dashboard.dashboard(store)
    value['events'][0]['eligibility']='NEEDS_REVIEW'
    result=native(value)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['statuses']==['Programmazione da completare']
