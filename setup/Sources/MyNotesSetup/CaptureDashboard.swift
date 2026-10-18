import Foundation

enum CaptureDates {
    static func read(_ value: String?) -> Date? {
        guard let value else { return nil }
        let formatter = ISO8601DateFormatter()
        if let date = formatter.date(from: value) { return date }
        formatter.formatOptions.insert(.withFractionalSeconds)
        return formatter.date(from: value)
    }
    static func write(_ value: Date) -> String { ISO8601DateFormatter().string(from: value) }
    static func calendarPeriod(_ from: Date, _ through: Date, zone: String) -> (Date, Date)? {
        guard let timeZone = TimeZone(identifier: zone) else { return nil }
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = timeZone
        let first = calendar.startOfDay(for: from)
        guard let last = calendar.date(byAdding: .day, value: 1, to: calendar.startOfDay(for: through)) else { return nil }
        return (first, last)
    }
    static func label(_ value: String?, zone: String? = nil) -> String {
        guard let date = read(value) else { return "Non disponibile" }
        let formatter = DateFormatter(); formatter.locale = Locale(identifier: "it_IT")
        formatter.timeZone = zone.flatMap(TimeZone.init(identifier:)) ?? .current
        formatter.dateFormat = "dd MMM, HH:mm z"
        return formatter.string(from: date)
    }
}

struct CaptureCourse: Codable, Identifiable { var code: String; var name: String; var contextPending: Bool?; var id: String { code } }
struct CaptureMapping: Codable { var match: String; var courseCode: String; var firstLesson: Int? }
struct CaptureImportIssue: Codable { var title: String; var reason: String }
struct CaptureImportSummary: Codable { var imported: Int; var recognizedCourses: Int; var pending: Int; var excluded: Int; var issues: [CaptureImportIssue]? }
struct CaptureSource: Codable, Identifiable {
    var sourceID: String; var label: String; var host: String; var timezone: String
    var lastSuccess: String?; var nextAttempt: String?; var error: String?; var mappings: [CaptureMapping]
    var importMode: String?; var summary: CaptureImportSummary?
    var code: String?; var message: String?; var retryable: Bool?; var suggestedAction: String?
    var id: String { sourceID }
}
struct CaptureSubscription: Decodable {
    var sourceID: String; var label: String; var url: String; var timezone: String
    var periodStart: String; var periodEnd: String; var importMode: String?; var mappings: [CaptureMapping]
}
struct CaptureSourceResponse: Decodable { var source: CaptureSubscription }
struct CaptureEvent: Codable, Identifiable {
    var id: String; var sourceID: String; var courseCode: String?; var lessonNumber: Int?
    var title: String; var startsAt: String; var endsAt: String; var stopAt: String; var timezone: String
    var eligibility: String; var reason: String; var localChanges: Bool; var recordingEnabled: Bool
    var sourceEligibility: String; var special: Bool
    var courseLabel: String { if let code = courseCode { if let number = lessonNumber { return "\(code) · Lezione \(number)" }; return code }; return "Corso da associare" }
    var start: Date { CaptureDates.read(startsAt) ?? .distantFuture }
    var end: Date { CaptureDates.read(endsAt) ?? .distantFuture }
    var statusLabel: String {
        switch eligibility {
        case "SCHEDULED": return "Programmata"
        case "CONFLICT": return "Conflitto"
        case "NEEDS_REVIEW": return "Da verificare"
        case "CANCELLED": return recordingEnabled ? "Annullata" : "Esclusa localmente"
        default: return "Stato non riconosciuto"
        }
    }
}
struct CaptureSession: Codable {
    var id: String; var event: CaptureSessionEvent; var status: String; var outputPath: String?
    var actualStart: String?
}
struct CaptureSessionEvent: Codable {
    var id: String; var courseCode: String?; var lessonNumber: Int?; var title: String?
    var courseLabel: String { if let code = courseCode, let number = lessonNumber { return "\(code) · L\(number)" }; return title ?? "Senza corso" }
}
struct CaptureRuntime: Codable {
    var recording: Bool?; var exporting: Bool?; var microphonePermission: String?
    var title: String?; var detail: String?; var currentSession: CaptureSession?; var sessions: [CaptureSession]?
}
struct CaptureResponse: Codable { var requestID: String; var status: String; var message: String? }
struct CaptureSnapshot: Codable {
    var schemaVersion: Int; var configured: Bool; var enabled: Bool; var paused: Bool
    var running: Bool?; var processCheck: String; var runtime: CaptureRuntime
    var sources: [CaptureSource]; var events: [CaptureEvent]; var courses: [CaptureCourse]; var refreshHours: Double
    var responses: [CaptureResponse]?
    var standaloneSupported: Bool?
    var calendarRecordingsSupported: Bool?
    var recordings: [CaptureRecording]?
    var queue: CaptureQueue?
    var isRecording: Bool { running == true && runtime.recording == true }
    var isExporting: Bool { running == true && runtime.exporting == true }
    static func decode(_ data: Data) throws -> Self {
        let value = try JSONDecoder().decode(Self.self, from: data)
        guard value.schemaVersion == 1, value.events.allSatisfy({ CaptureDates.read($0.startsAt) != nil && CaptureDates.read($0.endsAt) != nil && CaptureDates.read($0.stopAt) != nil }) else {
            throw NSError(domain: "Capture", code: 1, userInfo: [NSLocalizedDescriptionKey: "Calendario Capture non valido o versione non supportata."])
        }
        return value
    }
    func nextNumber(_ course: String) -> Int {
        let numbers = events.filter { $0.courseCode == course }.compactMap(\.lessonNumber)
            + (runtime.sessions ?? []).filter { $0.event.courseCode == course }.compactMap { $0.event.lessonNumber }
        let recorded = (recordings ?? []).filter { $0.courseCode == course }.compactMap { Int($0.lectureID?.components(separatedBy: "-L").last ?? "") }
        return min(999, ((numbers + recorded).max() ?? 0) + 1)
    }
    func editable(_ event: CaptureEvent) -> Bool {
        guard running != false else { return true }
        return !(runtime.recording == true && runtime.currentSession?.event.id == event.id)
            && !(runtime.sessions ?? []).contains { $0.event.id == event.id && $0.status == "EXPORTING" }
    }
    var stateLabel: String {
        if running == nil { return "Stato del processo non verificabile" }
        if running == true && runtime.recording == true { return "Registrazione in corso" }
        if running == true && runtime.exporting == true { return "Salvataggio audio" }
        if !enabled { return "Capture disattivato" }
        if paused { return "Programmazione in pausa" }
        return running == true ? "Capture pronto" : "Capture non in esecuzione"
    }
}

struct CaptureContext {
    let workspace: String; let launcher: URL
    init(environment: [String: String] = ProcessInfo.processInfo.environment) {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let runtime = URL(fileURLWithPath: environment["MYNOTES_HOME"] ?? home.appendingPathComponent("Library/Application Support/MyNotes").path)
        let settings = (try? Data(contentsOf: runtime.appendingPathComponent("setup.json"))).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        workspace = environment["MYNOTES_WORKSPACE"] ?? settings?["workspace"] as? String ?? runtime.appendingPathComponent("workspace").path
        launcher = URL(fileURLWithPath: environment["MYNOTES_PREFIX"] ?? home.appendingPathComponent(".local").path).appendingPathComponent("bin/mynotes")
    }
}

struct CaptureRecordingMessage: Codable {
    var code: String; var message: String
}
struct CaptureRecording: Codable, Identifiable {
    var id: String; var path: String; var title: String; var status: String
    var courseCode: String?; var lectureID: String?; var duration: Double?; var createdAt: String
    var transcriptText: String?
    var exportText: String?
    var diagnosticPath: String?
    var collection: String?
    var archiveStatus: String?
    var archiveError: String?
    var actions: [String]?
    var jobID: String?
    var queuePosition: Int?
    var waitReason: String?
    var transcriptionStatus: String?
    var canTranscribe: Bool?
    var audioAvailable: Bool?
    var warnings: [CaptureRecordingMessage]?
    var blockingReasons: [CaptureRecordingMessage]?
    var transcriptionState: String { transcriptionStatus ?? status }
    var isTranscribed: Bool { transcriptionState == "COMPLETED" }
    var transcriptionAllowed: Bool { canTranscribe ?? (transcriptionState == "NOT_STARTED") }
    enum CodingKeys: String, CodingKey {
        case id, path, title, status, duration
        case courseCode = "course_code", lectureID = "lecture_id", createdAt = "created_at", transcriptText = "transcript_text"
        case transcriptionStatus = "transcription_status", canTranscribe = "can_transcribe", audioAvailable = "audio_available"
        case warnings, blockingReasons = "blocking_reasons"
        case diagnosticPath = "diagnostic_path"
        case actions, collection, exportText = "export_text", archiveStatus = "archive_status", archiveError = "archive_error"
        case jobID = "job_id", queuePosition = "queue_position", waitReason = "wait_reason"
    }
}
struct CaptureLibrary: Decodable { var recordings: [CaptureRecording]; var folder: String?; var folderConfigured: Bool?
    enum CodingKeys: String, CodingKey { case recordings, folder; case folderConfigured = "folder_configured" }
}
struct CaptureQueueJob: Codable, Identifiable {
    var id: String; var createdAt: String?; var recordingID: String; var title: String; var courseCode: String?
    var status: String; var error: String?; var phase: String?; var elapsedSeconds: Double?
    var actions: [String]?
    var queuePosition: Int?
    var diagnosticPath: String?
    var outputs: [String: String]?
    var exportText: String?
    var archiveStatus: String?
    var archiveError: String?
    enum CodingKeys: String, CodingKey {
        case id, title, status, error, phase, actions, outputs
        case exportText = "export_text", archiveStatus = "archive_status", archiveError = "archive_error"
        case queuePosition = "queue_position", diagnosticPath = "diagnostic_path"
        case recordingID = "recording_id", courseCode = "course_code", elapsedSeconds = "elapsed_seconds", createdAt = "created_at"
    }
}
struct CaptureQueue: Codable {
    var parallel: Int; var paused: Bool; var uiOpen: Bool; var jobs: [CaptureQueueJob]
    var waitReason: String?
    enum CodingKeys: String, CodingKey { case parallel, paused, jobs; case uiOpen = "ui_open", waitReason = "wait_reason" }
}

enum CaptureRecordingFilter: String, CaseIterable, Identifiable {
    case all, ready, active, review, completed
    var id: String { rawValue }
    var title: String {
        switch self {
        case .all: return "Tutte"
        case .ready: return "Pronte"
        case .active: return "In coda"
        case .review: return "Da verificare"
        case .completed: return "Trascritte"
        }
    }
    func includes(_ item: CaptureRecording) -> Bool {
        switch self {
        case .all: return true
        case .ready: return item.transcriptionState == "NOT_STARTED" && (item.actions?.contains("enqueue") ?? item.transcriptionAllowed)
        case .active: return ["QUEUED", "RUNNING", "CANCELLING"].contains(item.transcriptionState)
        case .review: return ["FAILED", "INTERRUPTED", "NEEDS_REVIEW", "OUTDATED", "WAITING"].contains(item.transcriptionState) || item.archiveStatus == "PENDING"
        case .completed: return item.isTranscribed
        }
    }
}

extension CaptureQueue {
    // Old attempts remain in history, rather than competing with a newer
    // request for the same original in the main queue or its counters.
    var currentJobs: [CaptureQueueJob] {
        var latest: [String: Int] = [:]
        for (index, job) in jobs.enumerated() { latest[job.recordingID] = index }
        return jobs.enumerated().filter { latest[$0.element.recordingID] == $0.offset }.map(\.element)
    }
    var activeJobs: [CaptureQueueJob] { currentJobs.filter { ["RUNNING", "QUEUED", "CANCELLING"].contains($0.status) } }
    var reviewJobs: [CaptureQueueJob] { currentJobs.filter { ["FAILED", "INTERRUPTED", "NEEDS_REVIEW"].contains($0.status) || $0.archiveStatus == "PENDING" } }
    var historyJobs: [CaptureQueueJob] {
        let visible = Set((activeJobs + reviewJobs).map(\.id))
        return jobs.reversed().filter { !visible.contains($0.id) }
    }
}

enum CaptureProblem {
    static func summary(status: String) -> String {
        switch status {
        case "NEEDS_REVIEW": return "Il risultato richiede una verifica. Audio e diagnosi sono conservati; usa l’azione disponibile per procedere."
        case "INTERRUPTED", "CANCELLED": return "La trascrizione è stata interrotta. Audio e tentativi precedenti sono conservati."
        case "OUTDATED": return "L’audio è cambiato rispetto alla trascrizione salvata. Verifica la sorgente prima di riprovare."
        case "WAITING": return "L’audio non è ancora pronto: potrebbe essere in salvataggio, assente o disponibile soltanto nel cloud."
        default: return "La trascrizione non è riuscita. L’audio è conservato; controlla i dettagli e riprova."
        }
    }
}
extension CaptureRecording {
    var hasProblem: Bool { ["FAILED", "INTERRUPTED", "NEEDS_REVIEW", "OUTDATED", "WAITING"].contains(transcriptionState) }
    var detailMessages: [String] {
        var seen = Set<String>()
        return ((warnings ?? []) + (blockingReasons ?? [])).compactMap {
            let text = $0.message.trimmingCharacters(in: .whitespacesAndNewlines)
            return !text.isEmpty && seen.insert(text).inserted ? text : nil
        }
    }
    func arguments(for action: String, parallel: Int) -> [String]? {
        switch action {
        case "remove", "cancel", "retry", "restart", "recover":
            guard let jobID, !jobID.isEmpty else { return nil }
            return ["--job", jobID]
        case "download", "finish": return ["--recording", id]
        case "enqueue": return ["--recording", id, "--parallel", String(parallel)]
        default: return nil
        }
    }
}

