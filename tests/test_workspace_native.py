"""Native lesson/calendar projections consume the real additive JSON contract."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
from mynotes import capture, capture_dashboard
from test_capture import store, event

pytestmark=pytest.mark.skipif(sys.platform!='darwin' or not shutil.which('swiftc'),reason='macOS compiler required')


def test_lessons_keep_preferred_audio_and_previous_transcript_provenance(store,tmp_path):
    capture.import_schedule(store,[event()])
    snapshot=capture_dashboard.dashboard(store)
    eid=snapshot['events'][0]['id'];key='event:'+eid
    base={'path':'/synthetic.wav','title':'Example lecture','status':'NOT_STARTED','created_at':'2026-10-12T10:00:00Z','course_code':'MATH','lecture_id':'MATH-L01','lesson_key':key}
    records=[{**base,'id':'one','source_selected':True},{**base,'id':'two','source_selected':False}]
    records.append({**base,'id':'unassigned','course_code':None,'lecture_id':None,'lesson_key':'audio:unassigned'})
    transcripts=[{'id':'old','path':'/synthetic-old.txt','title':'Earlier transcript','status':'COMPLETED','recording_id':'two','course_code':'MATH','lecture_id':'MATH-L01','created_at':'2026-10-12T10:00:00Z'},
                 {'id':'text-only','path':'/synthetic-only.txt','title':'Transcript without audio','status':'COMPLETED','recording_id':'missing','course_code':'MATH','lecture_id':'MATH-L04','created_at':'2026-10-10T10:00:00Z'}]
    fixture=tmp_path/'fixture.json';fixture.write_text(json.dumps({'snapshot':snapshot,'audio':records,'transcripts':transcripts}))
    driver=tmp_path/'main.swift';driver.write_text(r'''import Foundation
struct Fixture: Decodable {var snapshot:CaptureSnapshot;var audio:[CaptureRecording];var transcripts:[CaptureTranscript]}
let f=try JSONDecoder().decode(Fixture.self,from:Data(contentsOf:URL(fileURLWithPath:CommandLine.arguments[1])))
let lessons=WorkspaceLesson.build(snapshot:f.snapshot,audio:f.audio,transcripts:f.transcripts)
precondition(lessons.count==3)
precondition(Set(lessons.map(\.id)).count==3)
let lesson=lessons.first{$0.audio.count==2}!
precondition(lesson.selectedAudio?.id=="one" && lesson.transcriptAudioID=="two")
precondition(lesson.transcriptPath=="/synthetic-old.txt")
precondition(lessons.first{$0.transcripts.contains{$0.id=="text-only"}}?.audio.isEmpty==true)
precondition(lessons.first{$0.id=="audio:unassigned"}?.isUnassigned==true)
precondition(lessons.map(\.id)==WorkspaceLesson.build(snapshot:f.snapshot,audio:f.audio,transcripts:f.transcripts).map(\.id))
let clock=WorkspaceCalendar(timezone:"Europe/Rome")
let date=CaptureDates.read("2026-10-25T12:00:00Z")!
let days=clock.week(date)
precondition(days.count==7 && clock.calendar.component(.weekday,from:days[0])==2)
precondition(clock.month(date).count==42)
precondition(clock.minuteOfDay(CaptureDates.read("2026-10-25T02:30:00Z")!)==210)
precondition(clock.calendar.dateComponents([.day],from:days[0],to:days[6]).day==6)
let local=WorkspaceCalendar(timezone:"Asia/Kolkata")
precondition(local.clock(CaptureDates.read("2026-11-07T08:00:00Z")!)=="13:30")
print("PASS")
''')
    root=Path(__file__).parents[1]/'setup/Sources/MyNotesSetup';binary=tmp_path/'checks'
    result=subprocess.run(['swiftc','-module-cache-path',str(tmp_path/'cache'),str(root/'CaptureDashboard.swift'),str(root/'WorkspaceModels.swift'),str(driver),'-o',str(binary)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    result=subprocess.run([str(binary),str(fixture)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert 'PASS' in result.stdout
