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
    @Published var lastEventID: String?
    @Published var recordings: [CaptureRecording] = []
    @Published var queue: CaptureQueue?
    @Published var queueBusy = false
    @Published var createdCourse: String?
    let context = CaptureContext()
    let preview = ProcessInfo.processInfo.environment["MYNOTES_CAPTURE_PREVIEW"].map { URL(fileURLWithPath: $0) }
    var isPreview: Bool { preview != nil }
    private var pendingRequest: String?

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
            throw NSError(domain: "Capture", code: Int(process.terminationStatus), userInfo: [NSLocalizedDescriptionKey: String(data: errorBox.data, encoding: .utf8) ?? "Comando Capture non riuscito."])
        }
        return output
    }

    private func fetch() async throws -> CaptureSnapshot {
        if let preview { return try CaptureSnapshot.decode(Data(contentsOf: preview)) }
        let executable = context.launcher, workspace = context.workspace
        let data = try await Task.detached { try Self.invoke(executable, workspace, "dashboard", [], nil) }.value
        return try CaptureSnapshot.decode(data)
    }
    func refresh(silent: Bool = false) async {
        guard !busy else { return }
        busy = true; defer { busy = false }
        do {
            snapshot = try await fetch(); error = nil; resolvePending()
            if isPreview { recordings = snapshot?.recordings ?? []; queue = snapshot?.queue }
            else { try await fetchLibrary() }
        }
        catch { self.error = "Capture non disponibile: \(error.localizedDescription)" }
    }
    @discardableResult func perform(_ action: String, arguments: [String] = [], payload: [String: Any]? = nil) async -> Bool {
        guard !busy, !isPreview else { return false }
        busy = true; error = nil; notice = nil; defer { busy = false }
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
            lastEventID = response["eventID"] as? String
            if ["FAILED", "REJECTED", "SYNC_FAILED", "INTERRUPTED", "DEFERRED"].contains(status) {
                error = response["message"] as? String ?? "Operazione non completata. Controlla gli errori del calendario e lo stato di Capture."
                snapshot = try await fetch(); return false
            }
            notice = status == "PENDING" ? "Richiesta inviata, non ancora confermata dal registratore." : status == "RECORDING" ? "Il registratore ha confermato l’acquisizione del microfono." : "Operazione completata."
            pendingRequest = status == "PENDING" ? response["requestID"] as? String : nil
            snapshot = try await fetch(); resolvePending(); return true
        } catch { self.error = error.localizedDescription; return false }
    }
    func stopNow() async {
        guard !stopping, !isPreview else { return }
        stopping = true; defer { stopping = false }
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
            snapshot?.running = state.running; snapshot?.runtime = state.runtime
            snapshot?.enabled = state.enabled; snapshot?.paused = state.paused
        } catch { self.error = error.localizedDescription }
    }
    private func resolvePending() {
        guard let id = pendingRequest, let response = snapshot?.responses?.first(where: { $0.requestID == id }) else { return }
        pendingRequest = nil
        if ["FAILED", "REJECTED", "INTERRUPTED"].contains(response.status) {
            error = response.message ?? "Richiesta non completata."
            notice = nil
        } else { notice = "Il registratore ha confermato la richiesta." }
    }

    private func fetchLibrary() async throws {
        let executable = context.launcher, workspace = context.workspace
        let library = try await Task.detached { try Self.invoke(executable, workspace, "library", [], nil, group: "transcription-queue") }.value
        recordings = try JSONDecoder().decode(CaptureLibrary.self, from: library).recordings
        snapshot?.recordings = recordings
        let state = try await Task.detached { try Self.invoke(executable, workspace, "status", [], nil, group: "transcription-queue") }.value
        queue = try JSONDecoder().decode(CaptureQueue.self, from: state)
    }

    func queueAction(_ action: String, arguments: [String] = []) async {
        guard !queueBusy, !isPreview else { return }
        queueBusy = true; defer { queueBusy = false }
        do {
            let executable = context.launcher, workspace = context.workspace
            _ = try await Task.detached { try Self.invoke(executable, workspace, action, arguments, nil, group: "transcription-queue") }.value
            try await fetchLibrary()
        } catch { self.error = error.localizedDescription }
    }

    func addCourse(_ name: String) async -> Bool {
        guard !busy, !isPreview else { return false }
        busy = true; defer { busy = false }
        do {
            let executable = context.launcher, workspace = context.workspace
            let data = try await Task.detached { try Self.invoke(executable, workspace, "add", ["--name", name, "--defer-context", "--json"], nil, group: "course") }.value
            let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            createdCourse = (object?["course"] as? [String: Any])?["code"] as? String
            snapshot = try await fetch()
            notice = object?["status"] as? String == "EXISTS" ? "Il corso esiste già ed è stato selezionato." : "Corso aggiunto. Puoi già registrare; completerai il contesto nella prossima chat."
            return createdCourse != nil
        } catch { self.error = error.localizedDescription; return false }
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
