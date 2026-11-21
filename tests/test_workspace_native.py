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


def test_activity_projects_lessons_before_queue_and_preserves_source_identity(tmp_path):
    source = Path(__file__).parents[1] / "setup/Sources/MyNotesSetup"
    driver = tmp_path / "main.swift"
    driver.write_text(r'''
import Foundation

func recording(_ id: String, course: String? = "MATH", status: String = "NOT_STARTED",
               selected: Bool = true, excluded: Bool = false, day: Int = 2) -> CaptureRecording {
    CaptureRecording(id: id, path: "/sample.wav", title: id, status: status, courseCode: course,
                     createdAt: "2026-11-0\(day)T10:00:00Z", sourceSelected: selected, sourceExcluded: excluded)
}
func job(_ id: String, recording: String, status: String, course: String = "MATH") -> CaptureQueueJob {
    CaptureQueueJob(id: id, recordingID: recording, title: recording, courseCode: course, status: status)
}
func transcript(_ recording: String?) -> CaptureTranscript {
    CaptureTranscript(id: "txt", path: "/sample.txt", title: "Saved", status: "COMPLETED",
                      recordingID: recording, createdAt: "")
}

let new = recording("new")
let old = recording("old", status: "COMPLETED", selected: false)
let lesson = WorkspaceLesson(id: "with-alternative", audio: [new, old], transcripts: [transcript("old")])
let earlier = WorkspaceLesson(id: "earlier", audio: [recording("first", day: 1)])
let unassigned = WorkspaceLesson(id: "unassigned", audio: [recording("other", course: nil)])
let excluded = WorkspaceLesson(id: "excluded", audio: [recording("excluded", selected: false, excluded: true)])
let choose = WorkspaceLesson(id: "choose", audio: [recording("a", selected: false), recording("b", selected: false)])
let independent = WorkspaceLesson(id: "independent", audio: choose.audio, transcripts: [transcript(nil)])
let completed = WorkspaceLesson(id: "complete", audio: [recording("complete", status: "COMPLETED")])
let empty = WorkspaceLesson(id: "calendar-only")
let local = WorkspaceActivity(lessons: [lesson, earlier, unassigned, excluded, choose, independent, completed, empty],
                              queue: nil, course: .course("MATH"))
precondition(local.ready.map(\.id) == ["earlier", "with-alternative"])
precondition(Set(local.review.map(\.id)) == ["excluded", "choose"])
precondition(local.active.isEmpty && local.count == 4 && local.hasUnassigned)
precondition(WorkspaceActivity(lessons: [lesson, unassigned], queue: nil, course: .unassigned).ready.map(\.id) == ["unassigned"])

var queue = CaptureQueue(parallel: 2, paused: true, uiOpen: true, jobs: [
    job("queued", recording: "first", status: "QUEUED"),
    job("running", recording: "new", status: "RUNNING"),
    job("different-course", recording: "other", status: "QUEUED", course: "OLD"),
    job("old-attempt", recording: "old", status: "COMPLETED")
])
let active = WorkspaceActivity(lessons: [earlier, lesson, unassigned], queue: queue, course: .course("MATH"))
precondition(active.active.map(\.id) == ["with-alternative", "earlier"])
precondition(active.ready.isEmpty && active.history.map(\.id) == ["old-attempt"])
precondition(WorkspaceActivity(lessons: [unassigned], queue: queue, course: .unassigned).active.map(\.id) == ["unassigned"])
precondition(queue.activeJobs.count == 3 && queue.paused)

queue.jobs[1].status = "COMPLETED"
let saved = WorkspaceLesson(id: lesson.id, audio: [recording("new", status: "COMPLETED"), old])
let done = WorkspaceActivity(lessons: [saved], queue: queue, course: .course("MATH"))
precondition(done.count == 0 && done.history.contains { $0.id == "running" })
var unsaved = saved
unsaved.audio[0].archiveStatus = "PENDING"
let pending = WorkspaceActivity(lessons: [unsaved], queue: nil, course: .all)
precondition(pending.review.map(\.id) == [saved.id])
''')
    binary = tmp_path / "activity"
    build = subprocess.run(
        ["swiftc", "-module-cache-path", str(tmp_path / "cache"), str(source / "CaptureDashboard.swift"),
         str(source / "WorkspaceModels.swift"), str(driver), "-o", str(binary)],
        capture_output=True, text=True,
    )
    assert build.returncode == 0, build.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


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
let stale = LibraryLesson(id: "stable-date", course: "MATH", number: 1, title: "Lecture", date: "2026-10-01T08:00:00Z", recordingIDs: [], transcriptIDs: [], eventIDs: [f.snapshot.events[0].id], notRecorded: false, hidden: false, revisionRequired: false)
let effective = WorkspaceLesson.build(snapshot:f.snapshot,audio:[],transcripts:[],registry:[stale])
precondition(effective[0].date == f.snapshot.events[0].start)
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


def test_durable_registry_controls_visibility_number_and_not_recorded(tmp_path):
    source = Path(__file__).parents[1] / 'setup/Sources/MyNotesSetup'
    fixture = tmp_path / 'lessons.json'
    fixture.write_text(json.dumps([
        {'id':'stable-one','course':'EXAMPLE','number':4,'title':'An existing lesson',
         'recording_ids':[],'transcript_ids':[],'event_ids':[],
         'not_recorded':True,'hidden':False,'revision_required':False},
        {'id':'stable-two','course':'EXAMPLE','number':5,'title':'Removed',
         'recording_ids':[],'transcript_ids':[],'event_ids':[],
         'not_recorded':False,'hidden':True,'revision_required':False}]))
    driver=tmp_path/'main.swift'
    driver.write_text('''
import Foundation
let rows = try JSONDecoder().decode([LibraryLesson].self, from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
let lessons = WorkspaceLesson.build(snapshot: nil, audio: [], transcripts: [], registry: rows)
precondition(lessons.count == 1)
precondition(lessons[0].id == "stable-one")
precondition(lessons[0].registry?.number == 4)
precondition(lessons[0].transcriptionLabel == "Non registrata")
precondition(lessons[0].hasContent)
print("PASS")
''')
    binary=tmp_path/'registry-projection'
    result=subprocess.run(['swiftc','-module-cache-path',str(tmp_path/'cache'),str(source/'CaptureDashboard.swift'),str(source/'WorkspaceModels.swift'),str(driver),'-o',str(binary)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    result=subprocess.run([str(binary),str(fixture)],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    assert 'PASS' in result.stdout


def test_calendar_overlap_layout_and_effective_date_precedence(tmp_path):
    source=Path(__file__).parents[1]/'setup/Sources/MyNotesSetup'
    fixture=tmp_path/'events.json'
    values=[]
    for identifier,start,end in [('a','08:00','09:00'),('b','10:00','11:00'),('c','10:30','11:30'),('d','18:00','19:30')]:
        values.append({'id':identifier,'sourceID':'special','courseCode':'EXAMPLE','lessonNumber':len(values)+1,'title':identifier,'startsAt':f'2026-11-06T{start}:00Z','endsAt':f'2026-11-06T{end}:00Z','timezone':'UTC','eligibility':'SCHEDULED','stopAt':f'2026-11-06T{end}:00Z','reason':'','localChanges':False,'recordingEnabled':True,'sourceEligibility':'SCHEDULED','special':True})
    fixture.write_text(json.dumps(values))
    driver=tmp_path/'main.swift'
    driver.write_text('''import Foundation
let events = try JSONDecoder().decode([CaptureEvent].self, from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
let lessons = events.map { WorkspaceLesson(id: $0.id, event: $0) }
let clock = WorkspaceCalendar(timezone: "UTC")
let positions = clock.placements(lessons, on: events[0].start)
precondition(positions.first { $0.id == "a" }!.columnCount == 1)
precondition(positions.first { $0.id == "d" }!.columnCount == 1)
precondition(positions.first { $0.id == "b" }!.columnCount == 2)
precondition(positions.first { $0.id == "c" }!.columnCount == 2)
precondition(positions.first { $0.id == "b" }!.column != positions.first { $0.id == "c" }!.column)
var touchingEvent = events[1]
touchingEvent.startsAt = events[0].endsAt
let touching = clock.placements([lessons[0], WorkspaceLesson(id: "touching", event: touchingEvent)], on: events[0].start)
precondition(touching.allSatisfy { $0.columnCount == 1 })
let sparse = clock.placements([lessons[0], lessons[3]], on: events[0].start)
precondition(sparse.allSatisfy { $0.span == $0.columnCount })
print("PASS")
''')
    binary=tmp_path/'layout'
    result=subprocess.run(['swiftc','-module-cache-path',str(tmp_path/'cache'),str(source/'CaptureDashboard.swift'),str(source/'WorkspaceModels.swift'),str(driver),'-o',str(binary)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    result=subprocess.run([str(binary),str(fixture)],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    assert 'PASS' in result.stdout
