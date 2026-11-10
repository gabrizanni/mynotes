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
let boundary=CaptureDates.read("2026-11-07T23:30:00Z")!
precondition(local.clock(boundary)=="05:00")
precondition(local.calendar.component(.day,from:boundary)==8)
precondition(local.originalTimeLabel(boundary,timezone:"Asia/Kolkata")==nil)
precondition(local.originalTimeLabel(boundary,timezone:"Europe/Rome")!.contains("00:30"))
precondition(local.originalTimeLabel(boundary,timezone:"Unknown/Zone")==nil)
let beforeMidnight=CaptureDates.read("2026-11-07T18:00:00Z")!
let afterMidnight=CaptureDates.read("2026-11-07T20:00:00Z")!
let first=local.clipped(start:beforeMidnight,end:afterMidnight,on:beforeMidnight)!
let second=local.clipped(start:beforeMidnight,end:afterMidnight,on:afterMidnight)!
precondition(first.duration==1800 && second.duration==5400)
precondition(first.end==second.start)
precondition(local.clipped(start:beforeMidnight,end:first.end,on:afterMidnight)==nil)
precondition(local.clipped(start:afterMidnight,end:beforeMidnight,on:beforeMidnight)==nil)
let autumn=CaptureDates.read("2026-10-25T12:00:00Z")!
let spring=CaptureDates.read("2026-03-29T12:00:00Z")!
precondition(clock.hours(autumn).count==25 && clock.dayInterval(autumn).duration==90000)
precondition(clock.hours(spring).count==23 && clock.dayInterval(spring).duration==82800)
let firstRepeated=CaptureDates.read("2026-10-25T00:30:00Z")!
let secondRepeated=CaptureDates.read("2026-10-25T01:30:00Z")!
precondition(clock.clock(firstRepeated)==clock.clock(secondRepeated))
precondition(clock.timelineMinute(secondRepeated,on:autumn)-clock.timelineMinute(firstRepeated,on:autumn)==60)
precondition(clock.label(firstRepeated,format:"zzz") != clock.label(secondRepeated,format:"zzz"))
let ny=WorkspaceCalendar(timezone:"America/New_York")
precondition(ny.calendar.component(.day,from:boundary)==7)
precondition(ny.clock(boundary)=="18:30")
precondition(clock.clock(CaptureDates.read("2026-10-23T08:00:00Z")!)=="10:00")
precondition(clock.clock(CaptureDates.read("2026-10-30T09:00:00Z")!)=="10:00")
print("PASS")
''')
    root=Path(__file__).parents[1]/'setup/Sources/MyNotesSetup';binary=tmp_path/'checks'
    result=subprocess.run(['swiftc','-module-cache-path',str(tmp_path/'cache'),str(root/'CaptureDashboard.swift'),str(root/'WorkspaceModels.swift'),str(driver),'-o',str(binary)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    result=subprocess.run([str(binary),str(fixture)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert 'PASS' in result.stdout


def test_projection_and_concurrent_date_formatting_stay_within_interaction_budget(store, tmp_path):
    """Synthetic archive catches formatter construction and quadratic joins."""
    from datetime import datetime, timedelta, timezone
    capture.import_schedule(store, [event()])
    snapshot = capture_dashboard.dashboard(store)
    template = snapshot['events'][0]
    base = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
    snapshot['events'] = []
    for number in range(1, 301):
        start = base + timedelta(days=number)
        end = start + timedelta(hours=1)
        iso = lambda value: value.isoformat().replace('+00:00', 'Z')
        snapshot['events'].append({**template, 'id': f'event-{number}', 'lessonNumber': number,
                                   'startsAt': iso(start), 'recordAt': iso(start),
                                   'endsAt': iso(end), 'stopAt': iso(end)})
    fixture = tmp_path / 'fixture.json'
    fixture.write_text(json.dumps(snapshot))
    driver = tmp_path / 'main.swift'
    driver.write_text(r'''import Foundation
let snapshot = try CaptureSnapshot.decode(Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
let start = Date()
for _ in 0..<50 {
 let lessons = WorkspaceLesson.build(snapshot: snapshot, audio: [], transcripts: [])
 precondition(lessons.count == 300 && lessons.first!.date >= lessons.last!.date)
}
let elapsed = Date().timeIntervalSince(start)
precondition(elapsed < 4, "Projection regressed to work measured in seconds per interaction")
DispatchQueue.concurrentPerform(iterations: 1000) { index in
 let value = index % 2 == 0 ? "2026-11-07T08:00:00.123Z" : "2026-11-07T08:00:00+00:00"
 let date = CaptureDates.read(value)!
 precondition(CaptureDates.write(date) == "2026-11-07T08:00:00Z")
 precondition(CaptureDates.format(date, zone: TimeZone(identifier: "Asia/Kolkata")!, pattern: "HH:mm") == "13:30")
}
precondition(CaptureDates.read("invalid") == nil)
print("PASS projection_50_ms", elapsed * 1000)
''')
    source = Path(__file__).parents[1] / 'setup/Sources/MyNotesSetup'
    binary = tmp_path / 'performance'
    built = subprocess.run(['swiftc', '-O', '-module-cache-path', str(tmp_path / 'cache'),
                            str(source / 'CaptureDashboard.swift'), str(source / 'WorkspaceModels.swift'),
                            str(driver), '-o', str(binary)], capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    result = subprocess.run([str(binary), str(fixture)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'PASS' in result.stdout
