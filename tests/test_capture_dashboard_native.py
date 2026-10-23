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
 let result: [String: Any] = ["state": value.stateLabel, "next": value.nextNumber("MATH"), "editable": value.events.map { value.editable($0) }, "statuses": value.events.map { $0.statusLabel }, "recordingStatuses": value.events.map { value.eventStatusLabel($0) }, "needsAttention": value.events.map { value.eventNeedsAttention($0) }, "recordingExplanations": value.events.map { value.eventStatusExplanation($0) }, "explanations": value.events.map { $0.statusExplanation }, "postEndState": value.stateLabel(at: CaptureDates.read("2026-10-12T11:01:00Z")!), "referenceState": value.stateLabel(at: CaptureDates.read("2026-10-12T09:00:00Z")!), "referenceDetail": value.stateDetail(at: CaptureDates.read("2026-10-12T09:00:00Z")!) ?? "", "timezone": CaptureDates.label(value.events.first?.startsAt, zone: "UTC"), "periodStart": CaptureDates.write(period.0), "periodEnd": CaptureDates.write(period.1)]
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


def test_native_calendar_reasons_are_localized_and_actionable(store,native):
    capture.import_schedule(store,[event()])
    value=capture_dashboard.dashboard(store)
    cases = [
        ('NEEDS_REVIEW','MISSING_END','Orario da correggere','Manca l’orario di fine'),
        ('NEEDS_REVIEW','INVALID_START','Orario da correggere','inizio valido'),
        ('NEEDS_REVIEW','INVALID_END','Orario da correggere','fine non è valido'),
        ('NEEDS_REVIEW','INVALID_DURATION','Orario da correggere','12 ore'),
        ('NEEDS_REVIEW','UNKNOWN_COURSE','Corso da associare','associazione'),
        ('NEEDS_REVIEW','ALL_DAY_EVENT','Programmazione da completare','giornata'),
        ('NEEDS_REVIEW','INVALID_RECURRENCE','Programmazione da completare','ricorrenza'),
        ('NEEDS_REVIEW','EVENT_DISAPPEARED','Evento da verificare','non è più presente'),
        ('CONFLICT','SCHEDULE_CONFLICT','Conflitto da risolvere','sovrappone'),
        ('CANCELLED','CALENDAR_CANCELLED','Annullata dal calendario','ha annullato'),
        ('CANCELLED','LOCAL_EXCLUSION','Esclusa localmente','Premi Modifica'),
        ('CANCELLED','CALENDAR_DISCONNECTED','Calendario scollegato','Collegalo di nuovo'),
    ]
    for eligibility,code,label,detail in cases:
        value['events'][0].update(eligibility=eligibility,reasonCode=code)
        payload=json.loads(native(value).stdout)
        assert payload['statuses']==[label]
        assert detail in payload['explanations'][0]
        assert 'Calendar event' not in payload['explanations'][0]


def test_native_legacy_invalid_event_does_not_claim_calendar_cancellation(store,native):
    capture.import_schedule(store,[event()])
    value=capture_dashboard.dashboard(store)
    value['events'][0].update(eligibility='NEEDS_REVIEW',sourceEligibility='NEEDS_REVIEW',reason='Calendar event has invalid times')
    value['events'][0].pop('reasonCode',None)
    payload=json.loads(native(value).stdout)
    assert 'dati' in payload['explanations'][0]
    assert 'annullat' not in payload['explanations'][0]


def test_native_idle_banner_never_hides_blocked_next_lesson_or_recorder_failures(store,native):
    capture.import_schedule(store,[event()])
    value=capture_dashboard.dashboard(store)
    value.update(enabled=True,running=True)
    value['runtime']={'microphonePermission':'AUTHORIZED','recording':False,'exporting':False}
    value['events'][0].update(eligibility='NEEDS_REVIEW',reasonCode='MISSING_END')
    payload=json.loads(native(value).stdout)
    assert payload['referenceState']=='Prossima lezione bloccata'
    assert 'Manca l’orario di fine' in payload['referenceDetail']
    value['events'][0].update(eligibility='SCHEDULED',reasonCode=None,reason='')
    payload=json.loads(native(value).stdout)
    assert payload['referenceState']=='Calendario attivo · in attesa'
    assert 'registrazione non è ancora iniziata' in payload['referenceDetail']
    value['runtime'].update(title='Recording failed')
    assert json.loads(native(value).stdout)['referenceState']=='Registrazione non riuscita'
    value['runtime'].update(title='Configuration error')
    assert json.loads(native(value).stdout)['referenceState']=='Configurazione da correggere'
    value['runtime'].update(title='Next: MATH-L01',scheduleIssues=['Obsolete course'])
    payload=json.loads(native(value).stdout)
    assert payload['referenceState']=='Lezioni escluse dal registratore'
    assert 'una lezione' in payload['referenceDetail']
    value['runtime'].update(scheduleIssues=[],microphonePermission='DENIED')
    assert json.loads(native(value).stdout)['referenceState']=='Microfono non autorizzato'
    value['runtime'].update(recording=True)
    assert json.loads(native(value).stdout)['referenceState']=='Registrazione in corso'


def test_native_cancelled_saved_and_failed_sync_states_are_distinct(store,native):
    capture.import_schedule(store,[event()])
    value=capture_dashboard.dashboard(store)
    value.update(enabled=True,running=True)
    value['runtime']={'microphonePermission':'AUTHORIZED','recording':False}
    value['events'][0].update(eligibility='CANCELLED',reasonCode='CALENDAR_CANCELLED')
    assert json.loads(native(value).stdout)['referenceState']=='Nessuna lezione da registrare'
    value['events'][0].update(eligibility='SCHEDULED',reasonCode=None)
    value['runtime']['sessions']=[{'id':'saved','event':{'id':value['events'][0]['id']},'status':'COMPLETED'}]
    assert json.loads(native(value).stdout)['referenceState']=='Nessuna lezione da registrare'
    value['runtime']['sessions']=[]
    value['sources']=[{'sourceID':'test','label':'Calendar','host':'example.test','timezone':'UTC','error':'offline','mappings':[]}]
    assert json.loads(native(value).stdout)['referenceState']=='Calendario non aggiornato'
    value.update(paused=True)
    assert json.loads(native(value).stdout)['referenceState']=='Programmazione in pausa'


def test_native_lesson_row_reports_actual_recording_and_attempt_failure(store,native):
    capture.import_schedule(store,[event()])
    value=capture_dashboard.dashboard(store)
    value.update(enabled=True,running=True)
    attempt={'id':'attempt','event':{'id':value['events'][0]['id']},'status':'RECORDING'}
    value['runtime']={'recording':True,'currentSession':attempt,'sessions':[attempt]}
    payload=json.loads(native(value).stdout)
    assert payload['recordingStatuses']==['Registrazione in corso']
    assert 'sta acquisendo il microfono' in payload['recordingExplanations'][0]
    value['runtime'].update(recording=False,currentSession=None)
    for state,label in [('FAILED','Registrazione non riuscita'),('MISSED','Registrazione mancata'),('COMPLETED','Registrata'),('PARTIAL','Registrazione parziale'),('EXISTING','Audio già presente'),('EXPORTING','Salvataggio audio')]:
        attempt['status']=state
        payload=json.loads(native(value).stdout)
        assert payload['recordingStatuses']==[label]


def test_native_postroll_does_not_advertise_a_fresh_recording_start(store,native):
    capture.import_schedule(store,[event()])
    value=capture_dashboard.dashboard(store)
    value.update(enabled=True,running=True)
    value['runtime']={'microphonePermission':'AUTHORIZED','recording':False}
    assert value['events'][0]['stopAt'] > '2026-10-12T11:01:00Z'
    assert json.loads(native(value).stdout)['postEndState']=='Nessuna lezione da registrare'
    attempt={'id':'active','event':{'id':value['events'][0]['id']},'status':'RECORDING'}
    value['runtime'].update(recording=True,currentSession=attempt,sessions=[attempt])
    payload=json.loads(native(value).stdout)
    assert payload['postEndState']=='Registrazione in corso'
    assert payload['recordingStatuses']==['Registrazione in corso']


def test_native_quarantine_overrides_python_scheduled_row_detail_and_next_banner(store,native):
    capture.import_schedule(store,[event(),event(2,'2026-10-12T12:00:00Z','2026-10-12T13:00:00Z')])
    value=capture_dashboard.dashboard(store)
    value.update(enabled=True,running=True)
    blocked_id=value['events'][0]['id']
    value['runtime']={'recording':False,'microphonePermission':'AUTHORIZED',
                      'scheduleIssues':['Corso non più configurato'],
                      'blockedEvents':{blocked_id:'Corso non più configurato'}}
    payload=json.loads(native(value).stdout)
    assert payload['statuses']==['Programmata','Programmata'], 'Python eligibility remains separate evidence'
    assert payload['recordingStatuses']==['Registrazione bloccata','Programmata']
    assert payload['needsAttention']==[True,False]
    assert 'Corso non più configurato' in payload['recordingExplanations'][0]
    assert payload['referenceState']=='Prossima lezione bloccata'
    assert 'Corso non più configurato' in payload['referenceDetail']
    assert 'In programma' not in payload['referenceDetail']
    # Current recorder evidence remains authoritative if a later schedule reload
    # quarantines the already active lesson: a configuration update cannot stop it.
    active={'id':'active','event':{'id':blocked_id},'status':'RECORDING'}
    value['runtime'].update(recording=True,currentSession=active,sessions=[active])
    payload=json.loads(native(value).stdout)
    assert payload['recordingStatuses'][0]=='Registrazione in corso'
    assert payload['referenceState']=='Registrazione in corso'
