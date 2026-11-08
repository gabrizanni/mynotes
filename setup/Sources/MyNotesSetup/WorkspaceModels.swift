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
    var title: String { event?.title ?? audio.first?.title ?? transcripts.first?.title ?? "Registrazione" }
    var courseCode: String? { event?.courseCode ?? audio.first?.courseCode ?? transcripts.first?.courseCode }
    var date: Date { event?.start ?? CaptureDates.read(audio.first?.createdAt ?? transcripts.first?.createdAt) ?? .distantPast }
    var timezone: String { event?.timezone ?? TimeZone.current.identifier }
    var selectedAudio: CaptureRecording? {
        audio.first { $0.sourceSelected == true && $0.sourceExcluded != true }
            ?? (audio.count == 1 && audio[0].sourceSelected == nil && audio[0].sourceExcluded != true ? audio[0] : nil)
    }
    var needsChoice: Bool { audio.contains { $0.sourceSelectionRequired == true } || audio.count > 1 && selectedAudio == nil }
    var isUnassigned: Bool { courseCode == nil }
    var hasContent: Bool { !audio.isEmpty || !transcripts.isEmpty }
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
    static func build(snapshot: CaptureSnapshot?, audio: [CaptureRecording], transcripts: [CaptureTranscript]) -> [Self] {
        let events = snapshot?.events ?? []
        var values: [String: Self] = Dictionary(uniqueKeysWithValues: events.map { ("event:" + $0.id, Self(id: "event:" + $0.id, event: $0)) })
        func key(lectureID: String?, recordingID: String, eventID: String? = nil) -> String {
            if let eventID, values["event:" + eventID] != nil { return "event:" + eventID }
            if let lectureID {
                let matches = events.filter { event in
                    guard let code = event.courseCode, let number = event.lessonNumber else { return false }
                    return String(format: "%@-L%02d", code, number) == lectureID
                }
                if matches.count == 1 { return "event:" + matches[0].id }
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
        return values.values.sorted { $0.date == $1.date ? $0.id < $1.id : $0.date > $1.date }
    }
}

struct WorkspaceCalendar {
    var calendar: Calendar
    init(timezone: String = TimeZone.current.identifier) {
        calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: timezone) ?? .current
        calendar.firstWeekday = 2
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
    func minuteOfDay(_ date: Date) -> Int {
        let components = calendar.dateComponents([.hour, .minute], from: date)
        return (components.hour ?? 0) * 60 + (components.minute ?? 0)
    }
    func clock(_ date: Date) -> String {
        let format = DateFormatter(); format.locale = Locale(identifier: "it_IT"); format.timeZone = calendar.timeZone; format.dateFormat = "HH:mm"
        return format.string(from: date)
    }
    func label(_ date: Date, format pattern: String) -> String {
        let format = DateFormatter(); format.locale = Locale(identifier: "it_IT"); format.timeZone = calendar.timeZone; format.dateFormat = pattern
        return format.string(from: date)
    }
}
