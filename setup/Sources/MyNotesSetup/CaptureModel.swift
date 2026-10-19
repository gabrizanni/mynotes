import Foundation
import AppKit
import SwiftUI
import Combine

@MainActor final class CaptureModel: ObservableObject {
    @Published var snapshot: CaptureSnapshot?
    @Published var busy = false
    @Published var stopping = false
    @Published var error: String?
    @Published var notice: String?
    @Published var refreshWarning: String?
    @Published var lastEventID: String?
    @Published var recordings: [CaptureRecording] = []
    @Published var transcripts: [CaptureTranscript] = []
    @Published var queue: CaptureQueue?
    @Published var queueBusy = false
    @Published var recordingFolder: String?
    @Published var folderConfigured = false
    @Published var createdCourse: String?
    let context = CaptureContext()
    let preview = ProcessInfo.processInfo.environment["MYNOTES_CAPTURE_PREVIEW"].map { URL(fileURLWithPath: $0) }
    var isPreview: Bool { preview != nil }
    private var pendingRequest: String?
    private var readTask: Task<Void, Never>?
    private var readIncludesLibrary = false
    private var revision: UInt64 = 0
    private var libraryDirty = false
    private var lastPoll = Date.distantPast

    var pollingInterval: TimeInterval {
        if pendingRequest != nil || snapshot?.isRecording == true || snapshot?.isExporting == true
            || queue?.jobs.contains(where: { ["QUEUED", "RUNNING", "CANCELLING"].contains($0.status) }) == true { return 5 }
        return 15
    }
    // The polling clock is bookkeeping, not view state: advancing it must not
    // invalidate the parent View or make SwiftUI recalculate List geometry.
    func claimPoll(at date: Date = Date(), force: Bool = false) -> Bool {
        guard force || date.timeIntervalSince(lastPoll) >= pollingInterval else { return false }
        lastPoll = date
        return true
    }
    private func invalidateReads() { revision &+= 1 }
    private static func equal<Value: Encodable>(_ left: Value, _ right: Value) -> Bool {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        guard let a = try? encoder.encode(left), let b = try? encoder.encode(right) else { return false }
        return a == b
    }
    private func publish(_ next: CaptureSnapshot, recordings nextRecordings: [CaptureRecording]?, queue nextQueue: CaptureQueue?) {
        if let nextRecordings, !Self.equal(recordings, nextRecordings) { recordings = nextRecordings }
        if !Self.equal(queue, nextQueue) { queue = nextQueue }
        if !Self.equal(snapshot, Optional(next)) { snapshot = next }
    }

    fileprivate nonisolated static func invoke(_ executable: URL, _ workspace: String, _ action: String, _ arguments: [String], _ payload: Data?, group: String = "capture") throws -> Data {
        var args = [group, action, "--workspace", workspace] + arguments
        var directory: URL?
        defer { if let directory { try? FileManager.default.removeItem(at: directory) } }
        if let payload {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent("mynotes-capture-ui-\(UUID().uuidString)")
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            directory = root
            let file = root.appendingPathComponent("input.json")
            try payload.write(to: file, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
            args += ["--file", file.path]
        }
        let process = Process(), stdout = Pipe(), stderr = Pipe()
        process.executableURL = executable; process.arguments = args
        process.standardOutput = stdout; process.standardError = stderr
        // Drain both pipes concurrently; a long diagnostic must not deadlock the UI.
        let errors = DispatchGroup(); errors.enter()
        let errorBox = CaptureErrorBytes()
        DispatchQueue.global().async { errorBox.data = stderr.fileHandleForReading.readDataToEndOfFile(); errors.leave() }
        do { try process.run() }
        catch { stdout.fileHandleForWriting.closeFile(); stderr.fileHandleForWriting.closeFile(); errors.wait(); throw error }
        stdout.fileHandleForWriting.closeFile(); stderr.fileHandleForWriting.closeFile()
        let output = stdout.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit(); errors.wait()
        if process.terminationStatus != 0 {
            // JSON failure responses (e.g. quota-free sync failures) still have useful structure.
            if (try? JSONSerialization.jsonObject(with: output)) != nil { return output }
            let diagnostic = String(data: errorBox.data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
            let fallback = diagnostic?.isEmpty == false ? diagnostic : String(data: output, encoding: .utf8)
            // Queue CLI errors may be printed on stdout. Calendar diagnostics can
            // contain private URLs, so only structured responses are shown there.
            let message = group == "transcription-queue" ? Self.queueMessage(fallback) : nil
            throw NSError(domain: "Capture", code: Int(process.terminationStatus), userInfo: [NSLocalizedDescriptionKey: message ?? "Comando non riuscito. Controlla lo stato e riprova."])
        }
        return output
    }

    nonisolated private static func queueMessage(_ value: String?) -> String? {
        guard let text = value?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else { return nil }
        let sensitive = #"(?i)https?://|webcal://|group\.calendar\.google\.com|token\s*[=:]|authorization\s*[=:]|password\s*[=:]|secret\s*[=:]|Traceback"#
        guard text.range(of: sensitive, options: .regularExpression) == nil else { return nil }
        return String(text.prefix(600))
    }
    private func fetch() async throws -> CaptureSnapshot {
        if let preview { return try CaptureSnapshot.decode(Data(contentsOf: preview)) }
        let executable = context.launcher, workspace = context.workspace
        let data = try await Task.detached { try Self.invoke(executable, workspace, "dashboard", [], nil) }.value
        return try CaptureSnapshot.decode(data)
    }
    // A timer read never becomes an operation: controls and the current view stay usable.
    // A foreground request can share an existing read; a library request waits for a
    // lighter calendar read, then loads the archive before proposing a lesson number.
    func refresh(silent: Bool = false, includeLibrary: Bool = false) async {
        if let current = readTask {
            let includesLibrary = readIncludesLibrary
            await current.value
            if includeLibrary && !includesLibrary { await refresh(silent: silent, includeLibrary: true) }
            return
        }
        guard !busy, !queueBusy, !stopping else { return }
        if !silent { busy = true }
        let version = revision
        readIncludesLibrary = includeLibrary
        let task = Task { @MainActor in
            defer { self.readTask = nil; self.readIncludesLibrary = false; if !silent { self.busy = false } }
            do {
                try await self.readAndPublish(version: version, includeLibrary: includeLibrary)
                guard version == self.revision else { return }
                if self.refreshWarning != nil { self.refreshWarning = nil }
            } catch {
                guard version == self.revision else { return }
                let message = self.snapshot == nil
                    ? "Impossibile leggere lo stato di Capture. Premi Aggiorna per riprovare."
                    : "Aggiornamento dello stato non riuscito. I dati visualizzati restano disponibili; premi Aggiorna per riprovare."
                if error.localizedDescription.contains("Cartella") {
                    self.recordings = []; self.transcripts = []
                    self.refreshWarning = error.localizedDescription
                    return
                }
                if self.refreshWarning != message { self.refreshWarning = message }
            }
        }
        readTask = task
        await task.value
    }
    func prepareLesson() async -> Bool {
        if isPreview { return true }
        await refresh(includeLibrary: true)
        return snapshot != nil && refreshWarning == nil && !busy && !queueBusy && !stopping
    }
    private func readAndPublish(version: UInt64, includeLibrary: Bool) async throws {
        var next = try await fetch()
        guard version == revision else { return }
        if isPreview {
            publish(next, recordings: next.recordings ?? [], queue: next.queue)
            resolvePending(); return
        }
        let executable = context.launcher, workspace = context.workspace
        let stateData = try await Task.detached { try Self.invoke(executable, workspace, "status", [], nil, group: "transcription-queue") }.value
        let nextQueue = try JSONDecoder().decode(CaptureQueue.self, from: stateData)
        guard version == revision else { return }
        let priorFinished = Set((snapshot?.runtime.sessions ?? []).filter { ["COMPLETED", "PARTIAL"].contains($0.status) }.map(\.id))
        let newFinished = Set((next.runtime.sessions ?? []).filter { ["COMPLETED", "PARTIAL"].contains($0.status) }.map(\.id))
        let justSaved = snapshot?.isExporting == true && !next.isExporting
            || snapshot?.isRecording == true && !next.isRecording && !next.isExporting
            || !newFinished.subtracting(priorFinished).isEmpty
        let nextRecordings: [CaptureRecording]?
        if includeLibrary || justSaved || libraryDirty { nextRecordings = try await fetchLibrary(version: version) }
        else { nextRecordings = nil }
        guard version == revision else { return }
        next.recordings = nextRecordings ?? recordings
        next.queue = nextQueue
        publish(next, recordings: nextRecordings, queue: nextQueue)
        if nextRecordings != nil { libraryDirty = false }
        resolvePending()
    }
    private func refreshAfterCommand() {
        Task {
            if let current = self.readTask { await current.value }
            await self.refresh(silent: true)
        }
    }
    private func reconcile(version: UInt64, includeLibrary: Bool = false) async throws {
        if let current = readTask { await current.value }
        guard version == revision else { return }
        try await readAndPublish(version: version, includeLibrary: includeLibrary)
    }
    func calendarSource(_ id: String) async -> CaptureSubscription? {
        guard !busy, !isPreview else { return nil }
        invalidateReads()
        busy = true; error = nil; defer { busy = false }
        do {
            let executable = context.launcher, workspace = context.workspace
            let data = try await Task.detached { try Self.invoke(executable, workspace, "source", ["--source", id], nil) }.value
            return try JSONDecoder().decode(CaptureSourceResponse.self, from: data).source
        } catch {
            self.error = "Impossibile caricare il collegamento del calendario. Riprova."
            return nil
        }
    }
    @discardableResult func perform(_ action: String, arguments: [String] = [], payload: [String: Any]? = nil) async -> Bool {
        guard !busy, !queueBusy, !isPreview else { return false }
        invalidateReads(); let version = revision
        busy = true; error = nil; notice = nil
        defer { busy = false; if version != revision { refreshAfterCommand() } }
        if action == "connect" {
            let parts = (payload?["url"] as? String).flatMap { URLComponents(string: $0.trimmingCharacters(in: .whitespacesAndNewlines)) }
            notice = parts?.host == "calendar.google.com" && parts?.path == "/calendar/embed"
                ? "Riconosciuto un link Google Calendar. Verifico il calendario iCal corrispondente…"
                : "Verifico il calendario e le lezioni nel periodo selezionato…"
        }
        do {
            let executable = context.launcher, workspace = context.workspace
            let input = try payload.map { try JSONSerialization.data(withJSONObject: $0) }
            if action == "enable" && snapshot?.configured == false {
                _ = try await Task.detached { try Self.invoke(executable, workspace, "configure", [], nil) }.value
            }
            let data = try await Task.detached { try Self.invoke(executable, workspace, action, arguments, input) }.value
            let response = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
            let status = response["status"] as? String ?? ""
            guard !status.isEmpty else { throw NSError(domain: "Capture", code: 1, userInfo: [NSLocalizedDescriptionKey: "Risposta Capture non valida. Aggiorna MyNotes e riprova."]) }
            guard version == revision else { return !["FAILED", "REJECTED", "SYNC_FAILED", "INTERRUPTED", "DEFERRED"].contains(status) }
            lastEventID = response["eventID"] as? String
            if ["FAILED", "REJECTED", "SYNC_FAILED", "INTERRUPTED", "DEFERRED"].contains(status) {
                notice = nil
                error = response["message"] as? String ?? "Operazione non completata. Controlla gli errori del calendario e lo stato di Capture."
                try await reconcile(version: version); return false
            }
            notice = response["message"] as? String ?? (status == "PENDING" ? "Richiesta inviata, non ancora confermata dal registratore." : status == "RECORDING" ? "Il registratore ha confermato l’acquisizione del microfono." : "Operazione completata.")
            pendingRequest = status == "PENDING" ? response["requestID"] as? String : nil
            try await reconcile(version: version); return true
        } catch {
            if version == revision { self.notice = nil; self.error = error.localizedDescription }
            return false
        }
    }
    func stopNow() async {
        guard !stopping, !isPreview else { return }
        invalidateReads(); let version = revision
        stopping = true; libraryDirty = true
        defer { stopping = false; refreshAfterCommand() }
        do {
            let executable = context.launcher, workspace = context.workspace
            let data = try await Task.detached { try Self.invoke(executable, workspace, "stop", ["--wait-seconds", "10"], nil) }.value
            let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
            let status = result["status"] as? String ?? ""
            if ["FAILED", "REJECTED"].contains(status) { error = result["message"] as? String ?? "Salvataggio non riuscito." }
            else if status == "PENDING" {
                pendingRequest = result["requestID"] as? String
                notice = "Stop richiesto, in attesa della conferma del registratore."
            } else if ["COMPLETED", "PARTIAL"].contains(status) { notice = status == "PARTIAL" ? "Audio salvato come registrazione parziale." : "Audio salvato." }
            else { throw NSError(domain: "Capture", code: 1, userInfo: [NSLocalizedDescriptionKey: "Conferma di salvataggio non valida."]) }
            // Status does not acquire the calendar sync lock; stop remains usable
            // while a slow calendar download is in progress.
            let stateData = try await Task.detached { try Self.invoke(executable, workspace, "status", [], nil) }.value
            let state = try JSONDecoder().decode(CaptureStatus.self, from: stateData)
            guard version == revision else { return }
            if var next = snapshot {
                next.running = state.running; next.runtime = state.runtime
                next.enabled = state.enabled; next.paused = state.paused
                publish(next, recordings: nil, queue: queue)
            }
        } catch { if version == revision { self.error = error.localizedDescription } }
    }
    private func resolvePending() {
        guard let id = pendingRequest, let response = snapshot?.responses?.first(where: { $0.requestID == id }) else { return }
        pendingRequest = nil
        if ["FAILED", "REJECTED", "INTERRUPTED"].contains(response.status) {
            error = response.message ?? "Richiesta non completata."
            notice = nil
        } else { notice = "Il registratore ha confermato la richiesta." }
    }

    private func fetchLibrary(version: UInt64) async throws -> [CaptureRecording] {
        let executable = context.launcher, workspace = context.workspace
        let library = try await Task.detached { try Self.invoke(executable, workspace, "library", ["--sync"], nil, group: "transcription-queue") }.value
        let value = try JSONDecoder().decode(CaptureLibrary.self, from: library)
        if version == revision {
            let nextTranscripts = value.transcripts ?? []
            if !Self.equal(transcripts, nextTranscripts) { transcripts = nextTranscripts }
            if recordingFolder != value.folder { recordingFolder = value.folder }
            if folderConfigured != (value.folderConfigured ?? false) { folderConfigured = value.folderConfigured ?? false }
        }
        return value.recordings
    }

    func queueAction(_ action: String, arguments: [String] = []) async {
        guard !queueBusy, !busy, !isPreview else { return }
        invalidateReads(); let version = revision
        queueBusy = true; error = nil; notice = nil
        defer { queueBusy = false; if version != revision { refreshAfterCommand() } }
        do {
            let executable = context.launcher, workspace = context.workspace
            let data = try await Task.detached { try Self.invoke(executable, workspace, action, arguments, nil, group: "transcription-queue") }.value
            let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
            let status = result["status"] as? String ?? ""
            guard !status.isEmpty else { throw NSError(domain: "Capture", code: 1, userInfo: [NSLocalizedDescriptionKey: "Risposta della coda non valida. Aggiorna MyNotes e riprova."]) }
            guard version == revision else { return }
            if ["FAILED", "REJECTED", "NEEDS_REVIEW", "BUSY", "INTERRUPTED", "DEFERRED"].contains(status) {
                error = Self.queueMessage(result["message"] as? String) ?? "Operazione non completata. Controlla la registrazione e riprova."
            } else if status == "QUEUED", let added = result["added"] as? Int {
                let skipped = result["skipped"] as? [[String: Any]] ?? []
                var reasons: [String] = []
                for item in skipped {
                    let blockers = item["blocking_reasons"] as? [[String: Any]] ?? []
                    for blocker in blockers {
                        if let message = Self.queueMessage(blocker["message"] as? String), !reasons.contains(message) { reasons.append(message) }
                    }
                    if blockers.isEmpty && item["reason"] as? String == "EXISTING_TRANSCRIPT_NEEDS_REVIEW" {
                        let message = "La trascrizione esistente richiede una verifica prima di riprovare."
                        if !reasons.contains(message) { reasons.append(message) }
                    }
                }
                let details = reasons.prefix(3).joined(separator: " ")
                if added == 0 {
                    if !details.isEmpty { error = "Nessuna nuova trascrizione aggiunta. " + details }
                    else if (result["completed_reused"] as? Int ?? 0) > 0 { notice = "Trascrizione esistente riutilizzata. Copia TXT e archiviazione aggiornate." }
                    else { notice = "Nessuna nuova trascrizione aggiunta alla coda." }
                } else {
                    notice = (added == 1 ? "1 registrazione aggiunta alla coda." : "\(added) registrazioni aggiunte alla coda.") + (details.isEmpty ? "" : " Registrazioni escluse: " + details)
                }
            } else if ["OK", "ASSIGNED", "PAUSED", "RESUMED", "CANCELLED", "STOPPED", "RUNNING", "COMPLETED"].contains(status) {
                notice = Self.queueMessage(result["message"] as? String) ?? "Operazione completata."
            } else {
                error = "Risposta della coda non riconosciuta. Aggiorna MyNotes e riprova."
            }
            try await reconcile(version: version, includeLibrary: true)
        } catch { if version == revision { self.notice = nil; self.error = error.localizedDescription } }
    }

    func recordingAction(_ action: String, item: CaptureRecording) async {
        guard let arguments = item.arguments(for: action, parallel: queue?.parallel ?? 1) else {
            notice = nil
            error = "Il comando non è disponibile per questa registrazione. Premi Aggiorna per ricaricare lo stato."
            return
        }
        await queueAction(action, arguments: arguments)
        if error == nil, let message = notice { notice = "\(item.title): \(message)" }
    }

    func enqueueSelection(_ items: [CaptureRecording], selectedCount: Int) async {
        guard !queueBusy, !busy, !isPreview else { return }
        let ready = items.filter { CaptureRecordingFilter.ready.includes($0) }
        guard !ready.isEmpty else { error = "Nessun audio selezionato è pronto da trascrivere."; return }
        invalidateReads(); let version = revision
        queueBusy = true; notice = nil; error = nil
        defer { queueBusy = false }
        var added = 0
        var problems: [String] = []
        let parallel = queue?.parallel ?? 1
        for item in ready {
            do {
                let executable = context.launcher, workspace = context.workspace
                let data = try await Task.detached { try Self.invoke(executable, workspace, "enqueue", ["--recording", item.id, "--parallel", String(parallel)], nil, group: "transcription-queue") }.value
                let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
                guard result["status"] as? String == "QUEUED" else {
                    throw NSError(domain: "Capture", code: 1, userInfo: [NSLocalizedDescriptionKey: Self.queueMessage(result["message"] as? String) ?? "Inserimento non riuscito."])
                }
                added += result["added"] as? Int ?? 0
                if let skipped = result["skipped"] as? [[String: Any]], !skipped.isEmpty { problems.append("\(item.title): audio non più pronto.") }
            } catch { problems.append("\(item.title): \(error.localizedDescription)") }
        }
        let excluded = max(0, selectedCount-ready.count)
        notice = (added == 1 ? "1 registrazione aggiunta alla coda." : "\(added) registrazioni aggiunte alla coda.") + (excluded > 0 ? " \(excluded) escluse perché non pronte." : "")
        if !problems.isEmpty { error = problems.prefix(3).joined(separator: " ") }
        do { try await reconcile(version: version, includeLibrary: true) }
        catch { refreshWarning = "Impossibile aggiornare la vista. Premi Aggiorna per verificare i lavori aggiunti." }
    }

    func openAudio(_ path: String) {
        notice = nil; error = nil
        guard FileManager.default.fileExists(atPath: path) else {
            error = "Audio non disponibile in questa posizione. Premi Aggiorna oppure ricollega il file."
            return
        }
        guard let player = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.apple.QuickTimePlayerX") else {
            error = "QuickTime Player non è disponibile. Usa Mostra nel Finder per aprire l’audio con un altro lettore."
            return
        }
        NSWorkspace.shared.open([URL(fileURLWithPath: path)], withApplicationAt: player, configuration: NSWorkspace.OpenConfiguration()) { [weak self] _, failure in
            if let failure {
                let message = failure.localizedDescription
                Task { @MainActor [weak self] in self?.error = "Impossibile aprire l’audio in QuickTime: \(message)" }
            }
        }
    }
    func openFile(_ path: String) {
        notice = nil; error = nil
        guard FileManager.default.fileExists(atPath: path), NSWorkspace.shared.open(URL(fileURLWithPath: path)) else {
            error = "Impossibile aprire il file. Potrebbe essere stato spostato o non essere disponibile sul Mac. Premi Aggiorna per controllare il collegamento."
            return
        }
    }
    func revealFile(_ path: String) {
        notice = nil; error = nil
        guard FileManager.default.fileExists(atPath: path) else {
            error = "Il file non è più in questa posizione. Premi Aggiorna oppure ricollega l’audio."
            return
        }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
    }

    func addCourse(_ name: String) async -> Bool {
        guard !busy, !queueBusy, !isPreview else { return false }
        invalidateReads(); let version = revision
        busy = true
        defer { busy = false; if version != revision { refreshAfterCommand() } }
        do {
            let executable = context.launcher, workspace = context.workspace
            let data = try await Task.detached { try Self.invoke(executable, workspace, "add", ["--name", name, "--defer-context", "--json"], nil, group: "course") }.value
            let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            guard version == revision else { return false }
            createdCourse = (object?["course"] as? [String: Any])?["code"] as? String
            try await reconcile(version: version)
            notice = object?["status"] as? String == "EXISTS" ? "Il corso esiste già ed è stato selezionato." : "Corso aggiunto. Puoi già registrare; completerai il contesto nella prossima chat."
            return createdCourse != nil
        } catch { if version == revision { self.error = error.localizedDescription }; return false }
    }
}

struct CaptureWindowLease: NSViewRepresentable {
    func makeNSView(context: Context) -> LeaseView { LeaseView() }
    func updateNSView(_ nsView: LeaseView, context: Context) {}
    final class LeaseView: NSView {
        private var timer: Timer?
        private var observers: [NSObjectProtocol] = []
        private var session = UUID().uuidString.lowercased()
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            guard ProcessInfo.processInfo.environment["MYNOTES_CAPTURE_PREVIEW"] == nil else { return }
            if let window, timer == nil {
                session = UUID().uuidString.lowercased()
                send("heartbeat")
                timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.send("heartbeat") }
                for name in [NSWindow.willCloseNotification, NSApplication.willTerminateNotification] {
                    observers.append(NotificationCenter.default.addObserver(forName: name, object: name == NSWindow.willCloseNotification ? window : nil, queue: .main) { [weak self] _ in self?.closeLease() })
                }
            } else if window == nil {
                closeLease()
                for observer in observers { NotificationCenter.default.removeObserver(observer) }; observers = []
            }
        }
        private func closeLease() {
            timer?.invalidate(); timer = nil
            // Close synchronously: the application may terminate before a CLI
            // task starts. The tombstone rejects late in-flight heartbeats.
            let path = URL(fileURLWithPath: CaptureContext().workspace).appendingPathComponent("state/transcription-queue/ui.json")
            let closed = path.deletingLastPathComponent().appendingPathComponent("closed")
            try? FileManager.default.createDirectory(at: closed, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            try? Data().write(to: closed.appendingPathComponent(session), options: .atomic)
            if let data = try? Data(contentsOf: path), let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any], object["session"] as? String == session,
               let output = try? JSONSerialization.data(withJSONObject: ["session": session, "expires_at": 0, "closed": true]) { try? output.write(to: path, options: .atomic) }
            send("close")
        }
        private func send(_ action: String) {
            let ctx = CaptureContext(), id = session
            Task.detached { _ = try? CaptureModel.invoke(ctx.launcher, ctx.workspace, action, ["--session", id], nil, group: "transcription-queue") }
        }
        deinit { timer?.invalidate(); for observer in observers { NotificationCenter.default.removeObserver(observer) } }
    }
}
private final class CaptureErrorBytes: @unchecked Sendable { var data = Data() }
private struct CaptureStatus: Decodable { var running: Bool?; var runtime: CaptureRuntime; var enabled: Bool; var paused: Bool }
