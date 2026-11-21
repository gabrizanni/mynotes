import Foundation

// Routes describe product areas, independently of Capture and future chat/editor modules.
enum WorkspaceRoute: String, CaseIterable, Identifiable {
    case today, calendar, library, activity
    var id: String { rawValue }
    var title: String {
        switch self { case .today: return "Oggi"; case .calendar: return "Calendario"; case .library: return "Raccolta"; case .activity: return "Attività" }
    }
    var symbol: String {
        switch self { case .today: return "sun.max"; case .calendar: return "calendar"; case .library: return "square.stack"; case .activity: return "waveform.path" }
    }
}

struct WorkspaceLesson: Identifiable {
    let id: String
    var event: CaptureEvent?
    var audio: [CaptureRecording] = []
    var transcripts: [CaptureTranscript] = []
    var registry: LibraryLesson?
    var title: String { registry?.title ?? event?.title ?? audio.first?.title ?? transcripts.first?.title ?? "Registrazione" }
    var courseCode: String? { registry?.course ?? event?.courseCode ?? audio.first?.courseCode ?? transcripts.first?.courseCode }
    private var resolvedDate: Date?
    var date: Date { resolvedDate ?? event?.start ?? CaptureDates.read(audio.first?.createdAt ?? transcripts.first?.createdAt) ?? .distantPast }
    var timezone: String { event?.timezone ?? TimeZone.current.identifier }
    var selectedAudio: CaptureRecording? {
        audio.first { $0.sourceSelected == true && $0.sourceExcluded != true }
            ?? (audio.count == 1 && audio[0].sourceSelected == nil && audio[0].sourceExcluded != true ? audio[0] : nil)
    }
    var needsChoice: Bool { audio.contains { $0.sourceSelectionRequired == true } || audio.count > 1 && selectedAudio == nil }
    var isUnassigned: Bool { courseCode == nil }
    var hasContent: Bool { registry?.notRecorded == true || !audio.isEmpty || !transcripts.isEmpty }
    var needsAttention: Bool {
        needsChoice || isUnassigned || audio.contains { $0.hasProblem } || event.map { $0.eligibility == "NEEDS_REVIEW" || $0.eligibility == "CONFLICT" } == true
    }
    var transcriptPath: String? {
        selectedAudio?.exportText ?? selectedAudio?.transcriptText ?? transcripts.first?.path
            ?? audio.first(where: { $0.isTranscribed })?.exportText ?? audio.first(where: { $0.isTranscribed })?.transcriptText
    }
    var transcriptAudioID: String? {
        if selectedAudio?.exportText != nil || selectedAudio?.transcriptText != nil { return selectedAudio?.id }
        return transcripts.first?.recordingID ?? audio.first(where: { $0.isTranscribed })?.id
    }
    var transcriptionLabel: String {
        if registry?.notRecorded == true { return "Non registrata" }
        if registry?.revisionRequired == true { return "Numerazione da verificare" }
        if needsChoice { return "Scegli audio" }
        if let selectedAudio {
            switch selectedAudio.transcriptionState {
            case "QUEUED": return "In coda"
            case "RUNNING", "CANCELLING": return "In trascrizione"
            case "COMPLETED": return "Trascritta"
            case "FAILED", "INTERRUPTED", "NEEDS_REVIEW", "OUTDATED": return "Da verificare"
            case "WAITING": return "Audio in attesa"
            default: return selectedAudio.sourceExcluded == true ? "Audio escluso" : "Da trascrivere"
            }
        }
        if transcriptPath != nil { return "Trascritta" }
        if !audio.isEmpty { return "Scegli audio" }
        return "Nessun audio"
    }
    func recordingLabel(_ snapshot: CaptureSnapshot?) -> String {
        if registry?.notRecorded == true { return "Non registrata" }
        if let event, snapshot?.isRecording == true, snapshot?.runtime.currentSession?.event.id == event.id { return "In registrazione" }
        if let selectedAudio, selectedAudio.partial == true { return "Registrazione parziale" }
        if audio.contains(where: { $0.audioAvailable != false }) { return "Audio disponibile" }
        if !audio.isEmpty { return "Audio da verificare" }
        return event.map { snapshot?.eventStatusLabel($0) ?? $0.statusLabel } ?? "Nessun audio"
    }
    var numberConflict: Bool {
        guard let event, let number = event.lessonNumber else { return false }
        let parts = event.title.split(whereSeparator: { $0 == " " || $0 == "_" || $0 == "-" })
        guard let last = parts.last, let titleNumber = Int(last.replacingOccurrences(of: "L", with: "")) else { return false }
        return titleNumber != number
    }
    static func build(snapshot: CaptureSnapshot?, audio: [CaptureRecording], transcripts: [CaptureTranscript], registry: [LibraryLesson] = []) -> [Self] {
        if !registry.isEmpty {
            return registry.filter { !$0.hidden }.map { row in
                var lesson = Self(id: row.id, event: snapshot?.events.first { row.eventIDs.contains($0.id) }, audio: audio.filter { row.recordingIDs.contains($0.id) }, transcripts: transcripts.filter { row.transcriptIDs.contains($0.id) })
                lesson.registry = row
                lesson.resolvedDate = lesson.event?.start ?? row.date.flatMap(CaptureDates.read) ?? lesson.date
                return lesson
            }.sorted { $0.date == $1.date ? $0.id < $1.id : $0.date > $1.date }
        }
        let events = snapshot?.events ?? []
        var values: [String: Self] = Dictionary(uniqueKeysWithValues: events.map { ("event:" + $0.id, Self(id: "event:" + $0.id, event: $0)) })
        var eventKeysByLecture: [String: [String]] = [:]
        for event in events {
            if let code = event.courseCode, let number = event.lessonNumber {
                eventKeysByLecture[String(format: "%@-L%02d", code, number), default: []].append("event:" + event.id)
            }
        }
        func key(lectureID: String?, recordingID: String, eventID: String? = nil) -> String {
            if let eventID, values["event:" + eventID] != nil { return "event:" + eventID }
            if let lectureID {
                let matches = eventKeysByLecture[lectureID] ?? []
                if matches.count == 1 { return matches[0] }
                if matches.isEmpty { return "lesson:" + lectureID }
            }
            return "audio:" + recordingID
        }
        var audioKeys: [String: String] = [:]
        for item in audio {
            let identifier = item.lessonKey ?? key(lectureID: item.lectureID, recordingID: item.id, eventID: item.eventID)
            if values[identifier] == nil { values[identifier] = Self(id: identifier) }
            values[identifier]?.audio.append(item); audioKeys[item.id] = identifier
        }
        for transcript in transcripts {
            let identifier = transcript.recordingID.flatMap { audioKeys[$0] }
                ?? key(lectureID: transcript.lectureID, recordingID: transcript.recordingID ?? "transcript:" + transcript.id)
            if values[identifier] == nil { values[identifier] = Self(id: identifier) }
            values[identifier]?.transcripts.append(transcript)
        }
        var result = Array(values.values)
        for index in result.indices { result[index].resolvedDate = result[index].date }
        return result.sorted { $0.date == $1.date ? $0.id < $1.id : $0.date > $1.date }
    }
}

enum WorkspaceActivityCourse: Hashable {
    case all, unassigned, course(String)

    func includes(_ code: String?) -> Bool {
        switch self {
        case .all: return true
        case .unassigned: return code == nil
        case .course(let selected): return code == selected
        }
    }
}

struct WorkspaceActivityEntry: Identifiable {
    enum State {
        case active(String, CaptureQueueJob?)
        case ready(CaptureRecording)
        case review(String)
    }

    let lesson: WorkspaceLesson
    let state: State
    var id: String { lesson.id }

    init?(lesson: WorkspaceLesson, activeJob: CaptureQueueJob?) {
        guard lesson.registry?.hidden != true, lesson.registry?.notRecorded != true, !lesson.audio.isEmpty else { return nil }
        self.lesson = lesson
        if let activeJob {
            state = .active(activeJob.status, activeJob)
        } else if let active = lesson.audio.first(where: { ["RUNNING", "CANCELLING"].contains($0.transcriptionState) })
            ?? lesson.audio.first(where: { $0.transcriptionState == "QUEUED" }) {
            state = .active(active.transcriptionState, nil)
        } else if lesson.registry?.revisionRequired == true {
            state = .review("Numerazione da verificare")
        } else if let selected = lesson.selectedAudio {
            if selected.archiveStatus == "PENDING" {
                state = .review("Archiviazione da completare")
            } else if selected.isTranscribed {
                return nil
            } else if selected.hasProblem {
                state = .review(selected.actions?.contains("download") == true
                    ? "Scarica l’audio sul Mac"
                    : selected.blockingReasons?.first?.message ?? CaptureProblem.summary(status: selected.transcriptionState))
            } else if lesson.transcripts.contains(where: { $0.recordingID == selected.id && $0.status == "COMPLETED" }) {
                return nil
            } else if selected.audioAvailable == false {
                state = .review(selected.actions?.contains("download") == true ? "Scarica l’audio sul Mac" : "Audio non disponibile")
            } else if CaptureRecordingFilter.ready.includes(selected) {
                state = .ready(selected)
            } else {
                state = .review(selected.blockingReasons?.first?.message ?? "Controlla la sorgente audio")
            }
        } else if lesson.transcripts.contains(where: { $0.recordingID == nil && $0.status == "COMPLETED" }) {
            return nil
        } else if lesson.audio.allSatisfy({ $0.sourceExcluded == true }) {
            state = .review("Audio escluso dalla trascrizione")
        } else {
            state = .review("Scegli la sorgente audio")
        }
    }
}

struct WorkspaceActivity {
    var active: [WorkspaceActivityEntry] = []
    var ready: [WorkspaceActivityEntry] = []
    var review: [WorkspaceActivityEntry] = []
    var history: [CaptureQueueJob] = []
    let hasUnassigned: Bool
    var count: Int { active.count + ready.count + review.count }

    init(lessons: [WorkspaceLesson], queue: CaptureQueue?, course: WorkspaceActivityCourse) {
        let activeJobs = queue?.activeJobs ?? []
        let jobsByRecording = Dictionary(uniqueKeysWithValues: activeJobs.enumerated().map { ($0.element.recordingID, $0.offset) })
        var lessonByRecording: [String: WorkspaceLesson] = [:]
        var activeByRank: [(entry: WorkspaceActivityEntry, rank: Int)] = []
        var unassigned = false
        for lesson in lessons {
            for recording in lesson.audio {
                lessonByRecording[recording.id] = lesson
            }
            let rank = lesson.audio.compactMap { jobsByRecording[$0.id] }.min { lhs, rhs in
                let a = activeJobs[lhs], b = activeJobs[rhs]
                if (a.status == "QUEUED") != (b.status == "QUEUED") { return a.status != "QUEUED" }
                return lhs < rhs
            }
            guard let entry = WorkspaceActivityEntry(lesson: lesson, activeJob: rank.map({ activeJobs[$0] })) else { continue }
            unassigned = unassigned || lesson.isUnassigned
            guard course.includes(lesson.courseCode) else { continue }
            switch entry.state {
            case .active: activeByRank.append((entry, rank ?? Int.max))
            case .ready: ready.append(entry)
            case .review: review.append(entry)
            }
        }
        active = activeByRank.sorted { lhs, rhs in
            guard case .active(let a, _) = lhs.entry.state, case .active(let b, _) = rhs.entry.state else { return false }
            if (a == "QUEUED") != (b == "QUEUED") { return a != "QUEUED" }
            if lhs.rank != rhs.rank { return lhs.rank < rhs.rank }
            return lhs.entry.id < rhs.entry.id
        }.map(\.entry)
        ready.sort(by: Self.oldestFirst)
        review.sort(by: Self.oldestFirst)
        for job in queue?.historyJobs ?? [] where !["QUEUED", "RUNNING", "CANCELLING"].contains(job.status) {
            let code = lessonByRecording[job.recordingID].map(\.courseCode) ?? job.courseCode
            unassigned = unassigned || code == nil
            if course.includes(code) { history.append(job) }
        }
        hasUnassigned = unassigned
    }

    private static func oldestFirst(_ lhs: WorkspaceActivityEntry, _ rhs: WorkspaceActivityEntry) -> Bool {
        let a = lhs.lesson.date, b = rhs.lesson.date
        if a == b { return lhs.id < rhs.id }
        if a == .distantPast { return false }
        if b == .distantPast { return true }
        return a < b
    }
}

struct WorkspaceCalendar {
    var calendar: Calendar
    init(timezone: String = TimeZone.autoupdatingCurrent.identifier) {
        calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: timezone) ?? .current
        calendar.firstWeekday = 2
    }
    func originalTimeLabel(_ date: Date, timezone: String) -> String? {
        guard let source = TimeZone(identifier: timezone), source.identifier != calendar.timeZone.identifier else { return nil }
        return CaptureDates.format(date, zone: source, pattern: "dd MMM, HH:mm z") + " · " + source.identifier
    }
    func week(_ date: Date) -> [Date] {
        let day = calendar.startOfDay(for: date)
        let offset = (calendar.component(.weekday, from: day) + 5) % 7
        let first = calendar.date(byAdding: .day, value: -offset, to: day)!
        return (0..<7).compactMap { calendar.date(byAdding: .day, value: $0, to: first) }
    }
    func month(_ date: Date) -> [Date] {
        let first = calendar.date(from: calendar.dateComponents([.year, .month], from: date))!
        let beginning = week(first)[0]
        return (0..<42).compactMap { calendar.date(byAdding: .day, value: $0, to: beginning) }
    }
    func sameDay(_ a: Date, _ b: Date) -> Bool { calendar.isDate(a, inSameDayAs: b) }
    func dayInterval(_ day: Date) -> DateInterval {
        calendar.dateInterval(of: .day, for: day)!
    }
    func clipped(start: Date, end: Date, on day: Date) -> DateInterval? {
        let bounds = dayInterval(day)
        let from = max(start, bounds.start), through = min(end, bounds.end)
        return from < through ? DateInterval(start: from, end: through) : nil
    }
    func hours(_ day: Date) -> [Date] {
        let bounds = dayInterval(day)
        return stride(from: 0.0, to: bounds.duration, by: 3600).map { bounds.start.addingTimeInterval($0) }
    }
    func timelineMinute(_ date: Date, on day: Date) -> Double {
        date.timeIntervalSince(dayInterval(day).start) / 60
    }
    func minuteOfDay(_ date: Date) -> Int {
        let components = calendar.dateComponents([.hour, .minute], from: date)
        return (components.hour ?? 0) * 60 + (components.minute ?? 0)
    }
    func clock(_ date: Date) -> String {
        CaptureDates.format(date, zone: calendar.timeZone, pattern: "HH:mm")
    }
    func label(_ date: Date, format pattern: String) -> String {
        CaptureDates.format(date, zone: calendar.timeZone, pattern: pattern)
    }
}

// Horizontal space is shared only within a connected group of overlapping
// visible event cards, never across unrelated hours of the same day.
struct WorkspaceCalendarPlacement: Identifiable {
    var lesson: WorkspaceLesson
    var start: Date
    var end: Date
    var column: Int
    var columnCount: Int
    var span: Int
    var id: String { lesson.id }
}

extension WorkspaceCalendar {
    func placements(_ lessons: [WorkspaceLesson], on day: Date, minimumMinutes: Double = 0) -> [WorkspaceCalendarPlacement] {
        let entries = lessons.compactMap { lesson -> WorkspaceCalendarPlacement? in
            guard let event = lesson.event, let segment = clipped(start: event.start, end: event.end, on: day) else { return nil }
            return WorkspaceCalendarPlacement(lesson: lesson, start: segment.start,
                end: max(segment.end, segment.start.addingTimeInterval(minimumMinutes * 60)), column: 0, columnCount: 1, span: 1)
        }.sorted { $0.start == $1.start ? ($0.end == $1.end ? $0.id < $1.id : $0.end > $1.end) : $0.start < $1.start }
        var groups: [[WorkspaceCalendarPlacement]] = []
        var groupEnd = Date.distantPast
        for entry in entries {
            if groups.isEmpty || entry.start >= groupEnd { groups.append([entry]); groupEnd = entry.end }
            else { groups[groups.count - 1].append(entry); groupEnd = max(groupEnd, entry.end) }
        }
        return groups.flatMap { group in
            var ends: [Date] = []
            var placed: [WorkspaceCalendarPlacement] = []
            for var entry in group {
                let column = ends.firstIndex { $0 <= entry.start } ?? ends.count
                if column == ends.count { ends.append(entry.end) } else { ends[column] = entry.end }
                entry.column = column; placed.append(entry)
            }
            for index in placed.indices {
                placed[index].columnCount = ends.count
                var span = 1
                for column in (placed[index].column + 1)..<ends.count {
                    if placed.contains(where: { $0.column == column && $0.start < placed[index].end && $0.end > placed[index].start }) { break }
                    span += 1
                }
                placed[index].span = span
            }
            return placed
        }
    }
}
