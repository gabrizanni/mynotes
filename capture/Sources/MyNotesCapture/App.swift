import AppKit
import AVFoundation
import Foundation
import UserNotifications
import Darwin

#if !CAPTURE_TESTS
@main
struct CaptureMain {
    @MainActor private static var delegate: CaptureApp?
    @MainActor static func main() {
        setbuf(stdout, nil)
        let arguments = CommandLine.arguments
        guard let position = arguments.firstIndex(of: "--workspace"), arguments.indices.contains(position + 1) else {
            fputs("MyNotes Capture requires --workspace PATH\n", stderr); exit(2)
        }
        do {
            let engine = try CaptureEngine(workspace: URL(fileURLWithPath: arguments[position + 1]))
            let app = NSApplication.shared
            let controller = CaptureApp(engine: engine)
            delegate = controller
            app.delegate = controller
            app.run()
        } catch {
            fputs("MyNotes Capture: \(error.localizedDescription)\n", stderr); exit(2)
        }
    }
}

#endif

@MainActor
final class CaptureEngine: NSObject {
    let workspace: URL
    let directory: URL
    private let configurationURL: URL
    private let scheduleURL: URL
    private let instanceLock: InstanceLock
    private var timer: DispatchSourceTimer?
    private var watchers: [DispatchSourceFileSystemObject] = []
    private var config: CaptureConfiguration
    private(set) var lectures: [Lecture] = []
    private(set) var sessions: [Session] = []
    private var recorder: SegmentedRecorder?
    private var current: Session?
    private var updateLease: InstanceLock?
    private let updateLockURL: URL
    private var powerActivity: NSObjectProtocol?
    private var sleeping = false
    private var exporting = Set<String>()
    private var attempted = Set<String>()
    private var warned = Set<String>()
    private var processing = Set<String>()
    private var syncProcess: Process?
    private var configurationProcess: Process?
    private var reloadError: String?
    private var nextSyncCheck = Date.distantPast
    private var title = "Starting"
    private var detail = ""
    private let authorization: () -> Bool
    var onChange: ((String, String, Bool) -> Void)?
    var onNotice: ((String, String) -> Void)?
    var isRecording: Bool { recorder?.isRecording == true }
    var outputDirectory: URL { URL(fileURLWithPath: config.outputDirectory) }
    var paused: Bool { config.paused }
    var nextLecture: Lecture? { lectures.first { $0.eligibility == "SCHEDULED" && $0.stopAt > Date() && !outputExists($0) } }

    init(workspace: URL, authorization: @escaping () -> Bool = { AVCaptureDevice.authorizationStatus(for: .audio) == .authorized }) throws {
        self.workspace = workspace.standardizedFileURL
        directory = workspace.appendingPathComponent("state/capture")
        configurationURL = workspace.appendingPathComponent("config/capture.json")
        scheduleURL = directory.appendingPathComponent("schedule.json")
        config = try CaptureJSON.load(CaptureConfiguration.self, from: configurationURL)
        guard config.schemaVersion == 1 else { throw CaptureFailure.message("Unsupported Capture configuration") }
        // A global process lock complements the workspace lock and prevents
        // recordings in two workspaces from competing for the same microphone.
        let home = ProcessInfo.processInfo.environment["MYNOTES_CAPTURE_HOME"].map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/MyNotes/Capture")
        instanceLock = try InstanceLock(url: home.appendingPathComponent("app.lock"))
        updateLockURL = home.appendingPathComponent("update.lock")
        self.authorization = authorization
        super.init()
        for name in ["requests", "responses", "sessions"] {
            try FileManager.default.createDirectory(at: directory.appendingPathComponent(name), withIntermediateDirectories: true)
        }
        try reload()
        loadSessions()
        for index in sessions.indices where ["RECORDING", "EXPORTING"].contains(sessions[index].status) {
            sessions[index].status = "INTERRUPTED"
            sessions[index].partial = true
            sessions[index].reason = "App restarted during recording; preserved segments"
            sessions[index].actualStop = Date()
            try saveSession(sessions[index])
        }
    }

    func start() {
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(willSleep), name: NSWorkspace.willSleepNotification, object: nil)
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(didWake), name: NSWorkspace.didWakeNotification, object: nil)
        for url in [configurationURL.deletingLastPathComponent(), directory.appendingPathComponent("requests")] {
            watch(url)
        }
        evaluate(now: Date())
        recoverAutomatic()
    }

    private func watch(_ url: URL) {
        let descriptor = open(url.path, O_EVTONLY)
        guard descriptor >= 0 else { return }
        let source = DispatchSource.makeFileSystemObjectSource(fileDescriptor: descriptor, eventMask: [.write, .rename], queue: .main)
        source.setEventHandler { [weak self] in
            Task { @MainActor [weak self] in self?.evaluate(now: Date()) }
        }
        source.setCancelHandler { close(descriptor) }
        source.resume()
        watchers.append(source)
    }

    private func reload() throws {
        let next = try CaptureJSON.load(CaptureConfiguration.self, from: configurationURL)
        let schedule = try CaptureJSON.load(Schedule.self, from: scheduleURL)
        guard next.schemaVersion == 1, schedule.schemaVersion == 1 else { throw CaptureFailure.message("Unsupported Capture schema") }
        for event in schedule.events {
            guard event.validTarget(next.courseCodes),
                  event.startsAt < event.endsAt, event.recordAt <= event.startsAt, event.stopAt >= event.endsAt,
                  event.stopAt.timeIntervalSince(event.recordAt) <= 14 * 3600 else {
                throw CaptureFailure.message("Invalid recording event")
            }
        }
        config = next
        // The current session retains its own immutable event snapshot.
        lectures = schedule.events.sorted { $0.recordAt < $1.recordAt }
    }

    func evaluate(now: Date) {
        guard !sleeping else { return }
        do { try reload(); reloadError = nil }
        catch { reloadError = error.localizedDescription; announce("Configuration error", error.localizedDescription, notice: true) }
        handleRequests(now: now)
        if let session = current {
            if !isRecording { failRecording(CaptureFailure.message("Microphone recorder stopped")) }
            else if now >= session.event.stopAt { finish(interrupted: false, reason: "Scheduled end") }
        }
        reconcilePast(now: now)
        if current == nil && config.enabled && !config.paused && reloadError == nil {
            if let event = lectures.first(where: { $0.isCurrent(now) && !outputExists($0) && !attempted.contains($0.id) && !isExporting($0) }) {
                do { try begin(event, now: now) }
                catch {
                    if !error.localizedDescription.contains("Capture is already running") {
                        attempted.insert(event.id)
                        if !sessions.contains(where: { $0.event.id == event.id && $0.status == "FAILED" }) {
                            let failure = Session(id: UUID().uuidString, event: event, directory: "", actualStart: now, actualStop: now,
                                                  status: "FAILED", partial: true, reason: error.localizedDescription, segments: [], isTest: false)
                            try? saveSession(failure)
                        }
                    }
                    if current == nil && exporting.isEmpty { updateLease = nil }
                    announce("Could not start \(event.lectureID)", error.localizedDescription, notice: true)
                }
            }
        }
        if current == nil {
            if let reloadError { announce("Configuration error", reloadError) }
            else if !exporting.isEmpty { announce("Saving audio", "Keep Capture open until export finishes") }
            else if !config.enabled { announce("Capture disabled", "Enable through your MyNotes agent") }
            else if config.paused { announce("Capture paused", "Current recordings are not affected by pause") }
            else if let latest = sessions.max(by: { $0.actualStart < $1.actualStart }), latest.status == "FAILED" {
                announce("Recording failed", "\(latest.event.lectureID): \(latest.reason)")
            }
            else if let next = nextLecture {
                if next.startsAt.timeIntervalSince(now) <= 300 && warned.insert(next.id).inserted {
                    onNotice?("Next: \(next.lectureID)", display(next.startsAt, timezone: next.timezone))
                }
                announce("Next: \(next.lectureID)", display(next.startsAt, timezone: next.timezone))
            } else { announce("No pending lecture", "Configure the timetable through your MyNotes agent") }
        }
        if now >= nextSyncCheck { syncCalendar() }
        persistRuntime()
        // Calendar deadlines use wall time and are reconciled on every wake.
        var delay: TimeInterval = 30
        if let active = current { delay = min(delay, max(0.1, active.event.stopAt.timeIntervalSince(now))) }
        if config.enabled && !config.paused, let next = lectures.first(where: { $0.recordAt > now && $0.eligibility == "SCHEDULED" }) {
            delay = min(delay, max(0.1, next.recordAt.timeIntervalSince(now)))
        }
        timer?.cancel()
        let nextTimer = DispatchSource.makeTimerSource(queue: .main)
        nextTimer.schedule(wallDeadline: .now() + delay, leeway: .milliseconds(100))
        nextTimer.setEventHandler { [weak self] in Task { @MainActor in self?.evaluate(now: Date()) } }
        nextTimer.resume(); timer = nextTimer
    }

    func startSelected() {
        guard let event = nextLecture else { return }
        do { try begin(event, now: Date(), allowEarly: true) }
        catch { announce("Recording could not start", error.localizedDescription, notice: true) }
    }

    private func begin(_ event: Lecture, now: Date, allowEarly: Bool = false, isTest: Bool = false, requestID: String? = nil) throws {
        guard recorder == nil else { throw CaptureFailure.message("A recording is already active") }
        guard isTest || (event.validTarget(config.courseCodes) && event.eligibility == "SCHEDULED") else {
            throw CaptureFailure.message("Event is not eligible for recording")
        }
        guard event.stopAt > now && (allowEarly || event.recordAt <= now) else {
            throw CaptureFailure.message("Recording window is unavailable")
        }
        guard authorization() else { throw CaptureFailure.message("Allow microphone access from the Capture menu") }
        guard isTest || !outputExists(event) else { throw CaptureFailure.message("Existing recording retained") }
        guard !isExporting(event) else { throw CaptureFailure.message("This lecture is being exported") }
        if updateLease == nil { updateLease = try InstanceLock(url: updateLockURL) }
        let interrupted = sessions.filter { $0.event.id == event.id && $0.status == "INTERRUPTED" && !$0.isTest }
        var session: Session
        if interrupted.count == 1 && event.isCurrent(now) && !isTest {
            session = interrupted[0]
            session.actualStop = nil
            session.partial = true
            session.reason = "Resumed after interruption; audio contains a gap"
            session.status = "RECORDING"
        } else {
            let id = UUID().uuidString
            let stage = URL(fileURLWithPath: config.stageDirectory).appendingPathComponent(isTest ? "tests/\(id)" : id)
            session = Session(id: id, event: event, directory: stage.path, actualStart: now,
                              status: "RECORDING", partial: now > event.startsAt.addingTimeInterval(5),
                              reason: "", segments: [], isTest: isTest)
        }
        session.requestID = requestID
        if session.destinationDirectory == nil { session.destinationDirectory = config.outputDirectory }
        let next = SegmentedRecorder(directory: URL(fileURLWithPath: session.directory), initialSegments: session.segments.map { URL(fileURLWithPath: $0) })
        current = session
        next.onSegment = { [weak self] urls in
            guard let self, var current = self.current else { return }
            current.segments = urls.map(\.path)
            self.current = current
            do { try self.saveSession(current) }
            catch { self.failRecording(error) }
        }
        next.onError = { [weak self] error in self?.failRecording(error) }
        recorder = next
        do {
            try saveSession(session)
            try next.start()
            guard isRecording else { throw CaptureFailure.message("Recorder unavailable") }
        } catch {
            _ = next.stop(); recorder = nil
            if var failed = current {
                failed.status = "FAILED"; failed.actualStop = now; failed.partial = true; failed.reason = error.localizedDescription
                try? saveSession(failed)
            }
            current = nil
            if exporting.isEmpty { updateLease = nil }
            throw error
        }
        attempted.insert(event.id)
        powerActivity = ProcessInfo.processInfo.beginActivity(options: [.userInitiated, .idleSystemSleepDisabled, .suddenTerminationDisabled], reason: "Recording \(event.lectureID)")
        announce(isTest ? "Microphone test: REC" : "Recording \(event.lectureID)", "Stops at \(display(event.stopAt, timezone: event.timezone))", notice: true)
        persistRuntime()
    }

    private func failRecording(_ error: Error) {
        finish(interrupted: true, reason: error.localizedDescription, failed: true)
        announce("Recording failed", error.localizedDescription, notice: true)
    }

    func finish(interrupted: Bool, reason: String, failed: Bool = false, requestID: String? = nil) {
        guard var session = current else {
            if let requestID { respond(requestID, status: "REJECTED", message: "No active recording") }
            return
        }
        session.segments = recorder?.stop().map(\.path) ?? session.segments
        recorder = nil; current = nil
        if let powerActivity { ProcessInfo.processInfo.endActivity(powerActivity) }
        powerActivity = nil
        session.actualStop = Date()
        session.partial = session.partial || interrupted || Date() < session.event.endsAt.addingTimeInterval(-5)
        session.reason = session.finalReason(reason)
        if let requestID { session.requestID = requestID }
        session.status = interrupted ? (failed ? "FAILED" : "INTERRUPTED") : "EXPORTING"
        do { try saveSession(session) }
        catch { announce("State write failed", error.localizedDescription, notice: true) }
        if interrupted {
            if exporting.isEmpty { updateLease = nil }
            if let requestID { respond(requestID, status: "FAILED", message: reason, session: session) }
            persistRuntime()
            return
        }
        export(session, requestID: requestID)
    }

    private func export(_ input: Session, requestID: String? = nil, completion: ((Bool) -> Void)? = nil) {
        guard !exporting.contains(input.id) else { completion?(false); return }
        do { if updateLease == nil { updateLease = try InstanceLock(url: updateLockURL) } }
        catch { completion?(false); return }
        exporting.insert(input.id)
        if current == nil { announce("Saving audio", "Keep Capture open until export finishes") }
        persistRuntime()
        Task { @MainActor [weak self] in
            guard let self else { return }
            var session = input
            let destination = session.isTest ? URL(fileURLWithPath: session.directory).appendingPathComponent("microphone-test.m4a")
                : URL(fileURLWithPath: session.destinationDirectory ?? self.config.outputDirectory).appendingPathComponent(session.event.filename)
            do {
                session.durationSeconds = try await SegmentAssembler.assemble(session.segments.map { URL(fileURLWithPath: $0) }, to: destination)
                session.outputPath = destination.path
                session.status = session.partial ? "PARTIAL" : "COMPLETED"
                if session.isTest { try CaptureJSON.save(session, to: destination.appendingPathExtension("capture.json")) }
                try self.saveSession(session)
                if let requestID { self.respond(requestID, status: session.status, message: "Audio saved", session: session) }
                self.announce("Saved \(session.event.lectureID)", session.partial ? "Partial recording: \(destination.lastPathComponent)" : destination.lastPathComponent, notice: true)
                completion?(true)
            } catch {
                session.status = "FAILED"; session.partial = true; session.reason = error.localizedDescription
                try? self.saveSession(session)
                if let requestID { self.respond(requestID, status: "FAILED", message: error.localizedDescription, session: session) }
                self.announce("Audio export failed", "Segments preserved: \(error.localizedDescription)", notice: true)
                completion?(false)
            }
            self.exporting.remove(input.id)
            if self.current == nil && self.exporting.isEmpty { self.updateLease = nil }
            self.persistRuntime()
            self.evaluate(now: Date())
        }
    }

    func recoverAutomatic() {
        let eligible = RecoveryPolicy.candidates(sessions, now: Date()).filter {
            $0.event.stopAt <= Date() && !outputExists($0.event) && !exporting.contains($0.id)
        }
        for var session in RecoveryPolicy.unambiguous(eligible) {
            session.partial = true
            session.reason = "Recovered after interruption; verify coverage"
            export(session)
        }
    }

    func recover(sessionID: String? = nil, requestID: String? = nil) {
        guard !isRecording else {
            if let requestID { respond(requestID, status: "REJECTED", message: "Recover after the current recording finishes") }
            return
        }
        var eligible = RecoveryPolicy.candidates(sessions, now: Date()).filter { !outputExists($0.event) && !exporting.contains($0.id) }
        if let sessionID { eligible = eligible.filter { $0.id == sessionID } }
        else if RecoveryPolicy.unambiguous(eligible).count != eligible.count {
            if let requestID { respond(requestID, status: "REJECTED", message: "Multiple attempts exist; specify --session") }
            announce("Recovery needs selection", "Ask your agent to select a session ID", notice: true)
            return
        }
        guard !eligible.isEmpty else {
            if let requestID { respond(requestID, status: "REJECTED", message: "No eligible staged session") }
            return
        }
        var remaining = eligible.count
        var successes = 0
        for var session in eligible {
            session.partial = true
            session.reason = "Recovered staged recording; verify coverage"
            export(session) { [weak self] success in
                remaining -= 1
                if success { successes += 1 }
                if remaining == 0, let requestID {
                    self?.respond(requestID, status: successes == eligible.count ? "PARTIAL" : "FAILED", message: "Recovered \(successes)/\(eligible.count) attempts")
                }
            }
        }
    }

    func testMicrophone(requestID: String? = nil) {
        let now = Date()
        let event = Lecture(id: UUID().uuidString, sourceID: "test", courseCode: "TEST", lessonNumber: 1,
                            startsAt: now, endsAt: now.addingTimeInterval(60), recordAt: now, stopAt: now.addingTimeInterval(60),
                            timezone: TimeZone.current.identifier, title: "Microphone test", eligibility: "SCHEDULED", reason: "")
        do {
            try begin(event, now: now, isTest: true)
            if let requestID { respond(requestID, status: "RECORDING", message: "60-second test started", session: current) }
            evaluate(now: now)
        } catch {
            if let requestID { respond(requestID, status: "REJECTED", message: error.localizedDescription) }
            announce("Microphone test failed", error.localizedDescription, notice: true)
        }
    }

    private func handleRequests(now: Date) {
        let paths = (try? FileManager.default.contentsOfDirectory(at: directory.appendingPathComponent("requests"), includingPropertiesForKeys: nil)) ?? []
        for path in paths.sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) where path.pathExtension == "json" {
            let id = path.deletingPathExtension().lastPathComponent
            let responseURL = directory.appendingPathComponent("responses/\(id).json")
            if FileManager.default.fileExists(atPath: responseURL.path) { try? FileManager.default.removeItem(at: path); continue }
            guard !processing.contains(id) else { continue }
            do {
                let request = try CaptureJSON.load(ControlRequest.self, from: path)
                guard request.id == id, request.schemaVersion == 1, request.expiresAt >= now,
                      request.createdAt <= now.addingTimeInterval(5), request.expiresAt.timeIntervalSince(request.createdAt) <= 60 else {
                    throw CaptureFailure.message("Expired or invalid request")
                }
                if let previous = sessions.first(where: { $0.requestID == id }) {
                    respond(id, status: previous.status, message: "Previously accepted action; not repeated", session: previous)
                    continue
                }
                processing.insert(id)
                switch request.action {
                case "start":
                    guard let event = request.payload.event, event.validTarget(config.courseCodes),
                          event.endsAt > event.startsAt, event.stopAt.timeIntervalSince(now) <= 12 * 3600 else {
                        throw CaptureFailure.message("Invalid manual recording")
                    }
                    try begin(event, now: now, requestID: id)
                    respond(id, status: "RECORDING", message: "Microphone recording started", session: current)
                case "stop": finish(interrupted: false, reason: "Stopped manually", requestID: id)
                case "test": testMicrophone(requestID: id)
                case "recover": recover(sessionID: request.payload.sessionID, requestID: id)
                default: throw CaptureFailure.message("Unknown capture action")
                }
            } catch { respond(id, status: "REJECTED", message: error.localizedDescription) }
        }
    }

    private func respond(_ id: String, status: String, message: String, session: Session? = nil) {
        guard UUID(uuidString: id) != nil || (id.count == 32 && id.allSatisfy(\.isHexDigit)) else { return }
        do {
            try CaptureJSON.save(ControlResponse(status: status, requestID: id, message: message, sessionID: session?.id, outputPath: session?.outputPath),
                                 to: directory.appendingPathComponent("responses/\(id).json"))
            try? FileManager.default.removeItem(at: directory.appendingPathComponent("requests/\(id).json"))
            processing.remove(id)
        } catch { announce("Control response failed", error.localizedDescription, notice: true) }
    }

    func syncCalendar(force: Bool = false) {
        nextSyncCheck = Date().addingTimeInterval(300)
        guard !config.sources.isEmpty else { return }
        guard syncProcess == nil else { return }
        nextSyncCheck = Date().addingTimeInterval(300)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: config.pythonExecutable)
        process.arguments = ["-m", "mynotes", "capture", "sync", "--workspace", workspace.path] + (force ? [] : ["--if-due"])
        let output = Pipe(); process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        process.terminationHandler = { [weak self] process in
            let data = output.fileHandleForReading.readDataToEndOfFile()
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.syncProcess = nil
                if process.terminationStatus != 0 {
                    let response = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
                    self.announce("Aggiornamento calendario non riuscito", response?["message"] as? String ?? "Controlla il collegamento del calendario in Capture.", notice: true)
                }
                else if force { self.announce("Calendar updated", String(data: data, encoding: .utf8) == nil ? "Check status through your agent" : "Latest timetable loaded", notice: true) }
                self.evaluate(now: Date())
            }
        }
        do { try process.run(); syncProcess = process }
        catch { announce("Calendar update unavailable", error.localizedDescription, notice: true) }
    }

    func togglePause() {
        guard configurationProcess == nil else { return }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: config.pythonExecutable)
        process.arguments = ["-m", "mynotes", "capture", config.paused ? "resume" : "pause", "--workspace", workspace.path]
        process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
        process.terminationHandler = { [weak self] process in Task { @MainActor in
            self?.configurationProcess = nil
            if process.terminationStatus != 0 { self?.announce("Pause change failed", "Use your agent to inspect configuration", notice: true) }
            self?.evaluate(now: Date())
        } }
        do { try process.run(); configurationProcess = process }
        catch { announce("Pause change failed", error.localizedDescription, notice: true) }
    }

    private func reconcilePast(now: Date) {
        for event in lectures where event.stopAt <= now && event.eligibility == "SCHEDULED" {
            guard !sessions.contains(where: { $0.event.id == event.id }) else { continue }
            let exists = outputExists(event)
            let session = Session(id: UUID().uuidString, event: event, directory: "", actualStart: now, actualStop: now,
                                  status: exists ? "EXISTING" : "MISSED", partial: !exists,
                                  reason: exists ? "Existing output retained" : "Recording window elapsed", segments: [], isTest: false)
            try? saveSession(session)
            if !exists { onNotice?("Missed: \(event.lectureID)", "No recording was captured within the scheduled window") }
        }
    }

    func outputExists(_ event: Lecture) -> Bool {
        // A naming upgrade must not re-record or re-export an existing original.
        let candidates = [event.filename, event.legacyFilename].map { outputDirectory.appendingPathComponent($0).path }
        if candidates.contains(where: { FileManager.default.fileExists(atPath: $0) }) { return true }
        return sessions.contains { session in
            guard session.event.id == event.id, let path = session.outputPath else { return false }
            return FileManager.default.fileExists(atPath: path)
        }
    }
    private func isExporting(_ event: Lecture) -> Bool { sessions.contains { $0.event.lectureID == event.lectureID && exporting.contains($0.id) } }
    private func loadSessions() {
        let urls = (try? FileManager.default.contentsOfDirectory(at: directory.appendingPathComponent("sessions"), includingPropertiesForKeys: nil)) ?? []
        sessions = urls.filter { $0.pathExtension == "json" }.compactMap { try? CaptureJSON.load(Session.self, from: $0) }
    }
    private func saveSession(_ session: Session) throws {
        try CaptureJSON.save(session, to: directory.appendingPathComponent("sessions/\(session.id).json"))
        if let position = sessions.firstIndex(where: { $0.id == session.id }) { sessions[position] = session }
        else { sessions.append(session) }
    }
    private func microphonePermission() -> String {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return "AUTHORIZED"
        case .denied: return "DENIED"
        case .restricted: return "RESTRICTED"
        default: return "NOT_DETERMINED"
        }
    }
    private func persistRuntime() {
        do {
            try CaptureJSON.save(RuntimeStatus(pid: getpid(), updatedAt: Date(), recording: isRecording, exporting: !exporting.isEmpty,
                                              microphonePermission: microphonePermission(), title: title, detail: detail,
                                              currentSession: current, sessions: sessions.sorted { $0.actualStart > $1.actualStart }),
                                 to: directory.appendingPathComponent("runtime.json"))
        } catch { fputs("Capture state write failed: \(error.localizedDescription)\n", stderr) }
    }
    private func announce(_ title: String, _ detail: String, notice: Bool = false) {
        if self.title != title || self.detail != detail {
            print("\(Date()) \(title): \(detail)")
            self.title = title; self.detail = detail
            onChange?(title, detail, isRecording)
            if notice { onNotice?(title, detail) }
        }
    }
    private func display(_ date: Date, timezone: String) -> String {
        let formatter = DateFormatter()
        formatter.timeZone = TimeZone(identifier: timezone) ?? .current
        formatter.dateFormat = "dd MMM HH:mm z"
        return formatter.string(from: date)
    }
    @objc private func willSleep() { interrupt() }
    @objc private func didWake() {
        sleeping = false
        for session in sessions where session.status == "INTERRUPTED" { attempted.remove(session.event.id) }
        nextSyncCheck = .distantPast
        evaluate(now: Date()); recoverAutomatic()
    }
    func interrupt() {
        sleeping = true
        timer?.cancel()
        finish(interrupted: true, reason: "macOS sleeping or app terminating")
        persistRuntime()
    }
    func shutdown() {
        interrupt()
        syncProcess?.terminate(); configurationProcess?.terminate()
        for watcher in watchers { watcher.cancel() }
        NSWorkspace.shared.notificationCenter.removeObserver(self)
    }
}

@MainActor
final class CaptureApp: NSObject, NSApplicationDelegate {
    private let engine: CaptureEngine
    private var statusItem: NSStatusItem?
    private var terminationSignal: DispatchSourceSignal?
    private let statusLine = NSMenuItem(title: "Starting…", action: nil, keyEquivalent: "")
    private var startItem: NSMenuItem!
    private var stopItem: NSMenuItem!
    private var pauseItem: NSMenuItem!
    init(engine: CaptureEngine) { self.engine = engine }
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        // launchctl and controlled shutdown send SIGTERM. Close the active
        // audio segment before exiting so the next launch can recover it.
        signal(SIGTERM, SIG_IGN)
        let termination = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        termination.setEventHandler { [weak self] in
            self?.engine.shutdown()
            NSApp.terminate(nil)
        }
        termination.resume()
        terminationSignal = termination
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        let menu = NSMenu()
        statusLine.isEnabled = false; menu.addItem(statusLine)
        menu.addItem(.separator())
        startItem = add("Start selected lecture", #selector(start), menu)
        stopItem = add("Stop and save", #selector(stop), menu)
        pauseItem = add("Pause automatic recording", #selector(pause), menu)
        _ = add("Record 60-second microphone test", #selector(test), menu)
        _ = add("Update calendar now", #selector(sync), menu)
        _ = add("Recover staged recordings", #selector(recover), menu)
        menu.addItem(.separator())
        _ = add("Microphone permission…", #selector(permission), menu)
        _ = add("Open recordings folder", #selector(folder), menu)
        _ = add("Quit MyNotes Capture", #selector(quit), menu)
        statusItem?.menu = menu
        engine.onChange = { [weak self] title, detail, recording in self?.update(title, detail, recording) }
        engine.onNotice = { title, detail in
            let content = UNMutableNotificationContent(); content.title = title; content.body = detail
            UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
        }
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
        if AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined { permission() }
        engine.start()

    }
    private func add(_ title: String, _ action: Selector, _ menu: NSMenu) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: ""); item.target = self; menu.addItem(item); return item
    }
    private func update(_ title: String, _ detail: String, _ recording: Bool) {
        statusItem?.button?.title = recording ? "● REC" : ""
        statusItem?.button?.image = recording ? nil : NSImage(systemSymbolName: "mic", accessibilityDescription: "MyNotes Capture")
        statusLine.title = "\(title)\n\(detail)"
        startItem?.isEnabled = !engine.isRecording && engine.nextLecture != nil
        stopItem?.isEnabled = engine.isRecording
        pauseItem?.title = engine.paused ? "Resume automatic recording" : "Pause automatic recording"
    }
    @objc private func start() { engine.startSelected(); engine.evaluate(now: Date()) }
    @objc private func stop() { engine.finish(interrupted: false, reason: "Stopped manually"); engine.evaluate(now: Date()) }
    @objc private func pause() { engine.togglePause() }
    @objc private func test() { engine.testMicrophone() }
    @objc private func sync() { engine.syncCalendar(force: true) }
    @objc private func recover() { engine.recover() }
    @objc private func folder() { NSWorkspace.shared.open(engine.outputDirectory) }
    @objc private func permission() {
        if AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined {
            AVCaptureDevice.requestAccess(for: .audio) { [weak self] _ in Task { @MainActor in self?.engine.evaluate(now: Date()) } }
        } else { NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")!) }
    }
    @objc private func quit() { engine.shutdown(); NSApp.terminate(nil) }
    func applicationWillTerminate(_ notification: Notification) { engine.shutdown() }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { permission(); return true }
}
