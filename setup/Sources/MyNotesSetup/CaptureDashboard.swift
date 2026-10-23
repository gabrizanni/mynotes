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
    var reasonCode: String? = nil
    var courseLabel: String { if let code = courseCode { if let number = lessonNumber { return "\(code) · Lezione \(number)" }; return code }; return "Corso da associare" }
    var start: Date { CaptureDates.read(startsAt) ?? .distantFuture }
    var end: Date { CaptureDates.read(endsAt) ?? .distantFuture }
    // Older runtimes have human-readable reasons only. Keep them readable without
    // making the view depend on English diagnostics or changing scheduling rules.
    var diagnosticCode: String {
        if let reasonCode, !reasonCode.isEmpty { return reasonCode }
        switch reason {
        case "Calendar event has invalid times": return "INVALID_EVENT"
        case "Title matches multiple mappings": return "UNKNOWN_COURSE"
        case "Calendar cancellation": return "CALENDAR_CANCELLED"
        case "Event disappeared from calendar; verify before recording": return "EVENT_DISAPPEARED"
        case "Recording excluded locally": return "LOCAL_EXCLUSION"
        case "Calendar disconnected": return "CALENDAR_DISCONNECTED"
        case "Removed from static timetable": return "EVENT_DISAPPEARED"
        case "Overlapping lectures or duplicate course/lesson": return "SCHEDULE_CONFLICT"
        default: return ""
        }
    }
    var statusLabel: String {
        switch eligibility {
        case "SCHEDULED": return "Programmata"
        case "CONFLICT": return "Conflitto da risolvere"
        case "NEEDS_REVIEW":
            switch diagnosticCode {
            case "INVALID_START", "MISSING_END", "INVALID_END", "INVALID_DURATION": return "Orario da correggere"
            case "UNKNOWN_COURSE": return "Corso da associare"
            case "EVENT_DISAPPEARED": return "Evento da verificare"
            default: return "Programmazione da completare"
            }
        case "CANCELLED":
            if diagnosticCode == "CALENDAR_DISCONNECTED" { return "Calendario scollegato" }
            if diagnosticCode == "CALENDAR_CANCELLED" { return "Annullata dal calendario" }
            return diagnosticCode == "LOCAL_EXCLUSION" || !recordingEnabled ? "Esclusa localmente" : "Annullata dal calendario"
        default: return "Stato non riconosciuto"
        }
    }
    var statusExplanation: String {
        if eligibility == "SCHEDULED" { return "La lezione è nel programma automatico. La registrazione è confermata soltanto quando Capture acquisisce il microfono." }
        switch diagnosticCode {
        case "INVALID_START": return "Il calendario non fornisce un inizio valido. Correggi l’evento nel calendario e sincronizza."
        case "MISSING_END": return "Manca l’orario di fine. Aggiungilo nel calendario e sincronizza."
        case "INVALID_END": return "L’orario di fine non è valido. Correggilo nel calendario e sincronizza."
        case "INVALID_DURATION": return "La durata non è valida o supera il limite di 12 ore. Correggi gli orari nel calendario e sincronizza."
        case "ALL_DAY_EVENT": return "L’evento dura tutta la giornata e non ha un orario registrabile. Imposta inizio e fine nel calendario e sincronizza."
        case "INVALID_RECURRENCE": return "La ricorrenza dell’evento non è valida. Correggila nel calendario e sincronizza."
        case "INVALID_EVENT": return "I dati dell’evento non sono validi. Verifica orari e corso nel calendario, poi sincronizza."
        case "UNKNOWN_COURSE": return "Il corso non è riconosciuto in modo univoco. Correggi l’associazione nelle impostazioni del calendario e sincronizza."
        case "CALENDAR_CANCELLED": return "Il calendario ha annullato questa lezione. Per riattivarla, correggi l’evento nel calendario e sincronizza."
        case "EVENT_DISAPPEARED": return "L’evento non è più presente nel calendario importato. Verifica il calendario e sincronizza prima di registrare."
        case "LOCAL_EXCLUSION": return "Hai escluso questa registrazione in MyNotes. Premi Modifica e attiva Registra questa lezione per reinserirla."
        case "CALENDAR_DISCONNECTED": return "Il calendario è stato scollegato. Collegalo di nuovo per importare le lezioni."
        case "SCHEDULE_CONFLICT": return "Questa lezione si sovrappone a un’altra o ripete lo stesso corso e numero. Correggi gli orari o escludi una delle lezioni."
        default:
            if eligibility == "CONFLICT" { return "Gli orari o il numero della lezione sono in conflitto. Verifica le lezioni interessate prima di registrare." }
            if eligibility == "CANCELLED" { return recordingEnabled ? "Questa lezione è annullata e non verrà registrata automaticamente." : "Questa registrazione è esclusa localmente. Puoi riattivarla con Modifica." }
            return "Questa lezione non verrà registrata automaticamente. Verifica il calendario e sincronizza; se il problema resta, controlla i dati dell’evento."
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
    var scheduleIssues: [String]?
    var blockedEvents: [String: String]?
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
    func session(for event: CaptureEvent) -> CaptureSession? {
        (runtime.sessions ?? []).filter { $0.event.id == event.id }
            .max { (CaptureDates.read($0.actualStart) ?? .distantPast) < (CaptureDates.read($1.actualStart) ?? .distantPast) }
    }
    func eventStatusLabel(_ event: CaptureEvent) -> String {
        if isRecording && runtime.currentSession?.event.id == event.id { return "Registrazione in corso" }
        if runtime.blockedEvents?[event.id] != nil { return "Registrazione bloccata" }
        guard event.eligibility == "SCHEDULED", let session = session(for: event) else { return event.statusLabel }
        switch session.status {
        case "EXPORTING": return running == true ? "Salvataggio audio" : "Salvataggio da verificare"
        case "COMPLETED": return "Registrata"
        case "PARTIAL": return "Registrazione parziale"
        case "EXISTING": return "Audio già presente"
        case "FAILED": return "Registrazione non riuscita"
        case "MISSED": return "Registrazione mancata"
        case "INTERRUPTED", "RECORDING": return "Registrazione interrotta o da verificare"
        default: return event.statusLabel
        }
    }
    func eventNeedsAttention(_ event: CaptureEvent) -> Bool {
        if isRecording && runtime.currentSession?.event.id == event.id { return false }
        return runtime.blockedEvents?[event.id] != nil || event.eligibility != "SCHEDULED" || ["FAILED", "MISSED", "INTERRUPTED", "RECORDING"].contains(session(for: event)?.status ?? "")
    }
    func eventStatusExplanation(_ event: CaptureEvent) -> String {
        if isRecording && runtime.currentSession?.event.id == event.id { return "Il registratore sta acquisendo il microfono per questa lezione." }
        if let reason = runtime.blockedEvents?[event.id] { return "Il registratore ha escluso questa lezione: " + reason + " Correggi i dati del calendario e sincronizza prima di registrare." }
        guard event.eligibility == "SCHEDULED", let session = session(for: event) else { return event.statusExplanation }
        switch session.status {
        case "EXPORTING": return "Il registratore sta salvando l’audio. Attendi il completamento prima di chiudere Capture; se il processo è fermo, verifica la registrazione."
        case "COMPLETED", "PARTIAL", "EXISTING": return "Il registratore segnala audio salvato per questa lezione. Apri Registrazioni per verificarlo e ascoltarlo."
        case "FAILED": return "La registrazione ha segnalato un errore. Controlla il microfono e gli eventuali segmenti salvati prima di riprovare."
        case "MISSED": return "La finestra della lezione è terminata senza una registrazione. Il Mac deve essere acceso e sveglio e Capture attivo durante la lezione."
        case "INTERRUPTED", "RECORDING": return "L’ultimo tentativo non risulta concluso. Verifica lo stato del registratore e gli eventuali segmenti salvati prima di riprovare."
        default: return event.statusExplanation
        }
    }
    func nextPendingEvent(at now: Date) -> CaptureEvent? {
        let savedStatuses = ["COMPLETED", "PARTIAL", "EXISTING"] + (running == true ? ["RECORDING", "EXPORTING"] : [])
        let saved = Set((runtime.sessions ?? []).filter { savedStatuses.contains($0.status) }.map { $0.event.id })
        return events.filter { $0.eligibility != "CANCELLED" && $0.end > now && !saved.contains($0.id) }
            .sorted { $0.start == $1.start ? $0.id < $1.id : $0.start < $1.start }.first
    }
    var runtimeFailed: Bool {
        runtime.title == "Configuration error" || runtime.title == "Recording failed" || runtime.title?.hasPrefix("Could not start ") == true
    }
    func stateLabel(at now: Date) -> String {
        if running == nil { return "Stato del processo non verificabile" }
        if isRecording { return "Registrazione in corso" }
        if isExporting { return "Salvataggio audio" }
        if !enabled { return "Capture disattivato" }
        if paused { return "Programmazione in pausa" }
        if running != true { return "Capture non in esecuzione" }
        if runtime.title == "Configuration error" { return "Configurazione da correggere" }
        if runtimeFailed { return "Registrazione non riuscita" }
        if let next = nextPendingEvent(at: now), runtime.blockedEvents?[next.id] != nil { return "Prossima lezione bloccata" }
        if !(runtime.scheduleIssues ?? []).isEmpty || !(runtime.blockedEvents ?? [:]).isEmpty { return "Lezioni escluse dal registratore" }
        if ["DENIED", "RESTRICTED"].contains(runtime.microphonePermission ?? "") { return "Microfono non autorizzato" }
        if let next = nextPendingEvent(at: now), next.eligibility != "SCHEDULED" { return "Prossima lezione bloccata" }
        if sources.contains(where: { $0.error != nil }) { return "Calendario non aggiornato" }
        if runtime.microphonePermission != "AUTHORIZED" { return "Microfono da verificare" }
        return nextPendingEvent(at: now) == nil ? "Nessuna lezione da registrare" : "Calendario attivo · in attesa"
    }
    var stateLabel: String { stateLabel(at: Date()) }
    func stateDetail(at now: Date) -> String? {
        if runtimeFailed && !isRecording && !isExporting && running == true {
            return runtime.title == "Configuration error" ? "Il registratore non può leggere la configurazione. Correggi la configurazione prima della prossima lezione." : "Il registratore ha segnalato un errore. Controlla il microfono e la registrazione interessata prima di riprovare."
        }
        if let next = nextPendingEvent(at: now), runtime.blockedEvents?[next.id] != nil {
            return "Prossima: \(next.title) · \(CaptureDates.label(next.startsAt, zone: next.timezone)). " + eventStatusExplanation(next)
        }
        if !(runtime.scheduleIssues ?? []).isEmpty || !(runtime.blockedEvents ?? [:]).isEmpty {
            let count = max(runtime.scheduleIssues?.count ?? 0, runtime.blockedEvents?.count ?? 0)
            return (count == 1 ? "Il registratore ha escluso una lezione con dati non validi." : "Il registratore ha escluso \(count) lezioni con dati non validi.") + " Correggi il calendario e sincronizza; le altre lezioni restano in programma."
        }
        if ["DENIED", "RESTRICTED"].contains(runtime.microphonePermission ?? "") && !isRecording {
            return "Consenti l’accesso al microfono in Impostazioni di Sistema → Privacy e sicurezza → Microfono."
        }
        if let next = nextPendingEvent(at: now) {
            let prefix = "Prossima: \(next.title) · \(CaptureDates.label(next.startsAt, zone: next.timezone)). "
            if next.eligibility != "SCHEDULED" { return prefix + next.statusExplanation }
            if !enabled || paused || running != true { return prefix + "La registrazione automatica richiede Capture attivo, il calendario ripreso e il Mac acceso e sveglio." }
            return prefix + "In programma; la registrazione non è ancora iniziata. Il Mac deve restare acceso e sveglio."
        }
        if sources.contains(where: { $0.error != nil }) { return "Sincronizzazione non riuscita: verifica il collegamento. Resta disponibile l’ultimo programma importato, se presente." }
        return nil
    }
    var stateDetail: String? { stateDetail(at: Date()) }

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
    var reviewSummary: String?
    var qualitySummary: String?
    var diagnosticPath: String?
    var collection: String?
    var archiveStatus: String?
    var archiveError: String?
    var actions: [String]?
    var jobID: String?
    var queuePosition: Int?
    var waitReason: String?
    var associationState: String?
    var associationReason: String?
    var associationConflict: String?
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
        case associationState = "association_state", associationReason = "association_reason", associationConflict = "association_conflict"
        case transcriptionStatus = "transcription_status", canTranscribe = "can_transcribe", audioAvailable = "audio_available"
        case warnings, blockingReasons = "blocking_reasons"
        case reviewSummary = "review_summary"
        case qualitySummary = "quality_summary"
        case diagnosticPath = "diagnostic_path"
        case actions, collection, exportText = "export_text", archiveStatus = "archive_status", archiveError = "archive_error"
        case jobID = "job_id", queuePosition = "queue_position", waitReason = "wait_reason"
    }
}
struct CaptureTranscript: Codable, Identifiable {
    var id: String; var path: String; var title: String; var status: String
    var courseCode: String?; var recordingID: String?; var lectureID: String?; var createdAt: String
    enum CodingKeys: String, CodingKey {
        case id, path, title, status
        case courseCode = "course_code", recordingID = "recording_id", lectureID = "lecture_id", createdAt = "created_at"
    }
}
struct CaptureLibrary: Decodable { var recordings: [CaptureRecording]; var transcripts: [CaptureTranscript]?; var folder: String?; var folderConfigured: Bool?
    enum CodingKeys: String, CodingKey { case recordings, transcripts, folder; case folderConfigured = "folder_configured" }
}
struct CaptureQueueJob: Codable, Identifiable {
    var id: String; var createdAt: String?; var recordingID: String; var title: String; var courseCode: String?
    var sourcePresent: Bool?
    var status: String; var error: String?; var phase: String?; var elapsedSeconds: Double?
    var attemptID: String?; var updatedClock: Double?; var sampledClock: Double?
    var stateFresh: Bool?; var activity: String?; var recoveryNotice: String?
    var actions: [String]?
    var queuePosition: Int?
    var reviewSummary: String?
    var qualitySummary: String?
    var diagnosticPath: String?
    var outputs: [String: String]?
    var exportText: String?
    var archiveStatus: String?
    var archiveError: String?
    enum CodingKeys: String, CodingKey {
        case sourcePresent = "source_present"
        case id, title, status, error, phase, actions, outputs, activity
        case attemptID = "attempt_id", updatedClock = "updated_clock", sampledClock = "sampled_clock", stateFresh = "state_fresh", recoveryNotice = "recovery_notice"
        case exportText = "export_text", archiveStatus = "archive_status", archiveError = "archive_error"
        case reviewSummary = "review_summary"
        case qualitySummary = "quality_summary"
        case queuePosition = "queue_position", diagnosticPath = "diagnostic_path"
        case recordingID = "recording_id", courseCode = "course_code", elapsedSeconds = "elapsed_seconds", createdAt = "created_at"
    }
}
struct CaptureQueue: Codable {
    var parallel: Int; var paused: Bool; var uiOpen: Bool; var jobs: [CaptureQueueJob]
    var waitReason: String?
    var activityCounts: [String: Int]?
    var historyEvents: [CaptureQueueJob]?
    enum CodingKeys: String, CodingKey { case parallel, paused, jobs; case uiOpen = "ui_open", waitReason = "wait_reason", activityCounts = "activity_counts", historyEvents = "history_events" }
}

enum CaptureRecordingFilter: String, CaseIterable, Identifiable {
    case all, ready, active, completed
    var id: String { rawValue }
    var title: String {
        switch self {
        case .all: return "Tutte"
        case .ready: return "Pronte"
        case .active: return "In coda"
        case .completed: return "Trascritte"
        }
    }
    func includes(_ item: CaptureRecording) -> Bool {
        switch self {
        case .all: return true
        case .ready: return item.transcriptionState == "NOT_STARTED" && (item.actions?.contains("enqueue") ?? item.transcriptionAllowed)
        case .active: return ["QUEUED", "RUNNING", "CANCELLING"].contains(item.transcriptionState)
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
        return jobs.enumerated().filter { latest[$0.element.recordingID] == $0.offset && $0.element.sourcePresent != false }.map(\.element)
    }
    var activeJobs: [CaptureQueueJob] { currentJobs.filter { ["RUNNING", "QUEUED", "CANCELLING"].contains($0.status) } }
    var failedJobs: [CaptureQueueJob] { currentJobs.filter { ["FAILED", "INTERRUPTED", "NEEDS_REVIEW"].contains($0.status) || $0.archiveStatus == "PENDING" } }
    var historyJobs: [CaptureQueueJob] {
        let visible = Set((activeJobs + failedJobs).map(\.id))
        return jobs.reversed().filter { !visible.contains($0.id) } + (historyEvents ?? [])
    }
}

enum CaptureProblem {
    static func summary(status: String) -> String {
        switch status {
        case "NEEDS_REVIEW": return "Il tentativo non ha salvato una trascrizione. Usa Riprova; l’audio è conservato."
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

