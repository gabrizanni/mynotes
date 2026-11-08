import SwiftUI
import Foundation

struct WorkspaceAudioImportView: View {
    @ObservedObject var model: CaptureModel
    let urls: [URL]
    let initialEventID: String?
    var initialCourse: String? = nil
    var initialLectureID: String? = nil
    @Environment(\.dismiss) private var dismiss
    @CaptureViewState<String> private var eventID = ""
    @CaptureViewState<String> private var course = ""
    @CaptureViewState<Int> private var number = 1
    @CaptureViewState<Bool> private var hasNumber = false
    @CaptureViewState<Bool> private var inspecting = true
    @CaptureViewState<[CaptureAudioInspection]> private var files: [CaptureAudioInspection] = []
    @CaptureViewState<String?> private var failure: String?
    @CaptureViewState<[URL]> private var scoped: [URL] = []
    private var events: [CaptureEvent] { (model.snapshot?.events ?? []).sorted { $0.start > $1.start } }
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            if model.snapshot?.isRecording == true || model.snapshot?.isExporting == true { WorkspaceRecordingBar(model: model) }
            VStack(alignment: .leading, spacing: 8) {
                Text("Importa audio").font(.title2.weight(.semibold))
                Text("Una copia entra nella raccolta. I file originali restano dove sono.").font(.callout).foregroundStyle(.secondary)
            }
            if inspecting { ProgressView("Verifica dei file…").frame(maxWidth: .infinity, minHeight: 100) }
            else if let failure {
                Label(failure, systemImage: "exclamationmark.circle").foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                Button("Verifica di nuovo") { Task { await inspect() } }
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        ForEach(files) { file in
                            HStack(spacing: 12) {
                                Image(systemName: "waveform").foregroundStyle(.secondary)
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(file.name).font(.callout.weight(.medium))
                                    Text((file.duration.map { Duration.seconds($0).formatted(.time(pattern: .minuteSecond)) } ?? "Durata non disponibile") + " · " + ByteCountFormatter.string(fromByteCount: file.size, countStyle: .file)).font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Image(systemName: "checkmark.circle").foregroundStyle(.secondary).accessibilityLabel("Audio verificato")
                            }.padding(.vertical, 4)
                        }
                    }
                }.frame(maxHeight: 170)
            }
            Divider()
            Form {
                Picker("Lezione", selection: $eventID) {
                    Text("Senza lezione del calendario").tag("")
                    ForEach(events) { event in
                        Text(event.title + " · " + CaptureDates.label(event.startsAt, zone: event.timezone)).tag(event.id)
                    }
                }
                if eventID.isEmpty {
                    Picker("Corso", selection: $course) {
                        Text("Senza corso / da associare").tag("")
                        ForEach(model.snapshot?.courses ?? []) { Text($0.name).tag($0.code) }
                    }
                    if !course.isEmpty {
                        Toggle("Indica il numero della lezione", isOn: $hasNumber)
                        if hasNumber { Stepper("Lezione \(number)", value: $number, in: 1...999) }
                    }
                }
            }
            Text("Se la lezione ha già un audio, potrai confrontare le sorgenti e scegliere quale trascrivere. Nessun file viene sostituito.").font(.caption).foregroundStyle(.secondary)
            if let error = model.error { Text(error).font(.callout).foregroundStyle(.red).textSelection(.enabled) }
            HStack {
                Button("Annulla") { dismiss() }.keyboardShortcut(.cancelAction).disabled(model.queueBusy)
                Spacer()
                if model.queueBusy { ProgressView().controlSize(.small) }
                Button("Importa nella raccolta") { Task {
                    var payload: [String: Any] = ["paths": urls.map(\.path)]
                    if !eventID.isEmpty { payload["event_id"] = eventID }
                    else if !course.isEmpty {
                        payload["course"] = course
                        if hasNumber { payload["lesson"] = number }
                    }
                    await model.queueAction("import-audio", payload: payload)
                    if model.error == nil { dismiss() }
                } }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(inspecting || failure != nil || files.count != urls.count || model.queueBusy || model.isPreview)
            }
        }.padding(28).frame(width: 580)
        .task {
            eventID = initialEventID ?? ""; course = initialCourse ?? ""
            if let raw = initialLectureID?.components(separatedBy: "-L").last, let value = Int(raw) { number = value; hasNumber = true }
            scoped = urls.filter { $0.startAccessingSecurityScopedResource() }
            await inspect()
        }
        .onDisappear { scoped.forEach { $0.stopAccessingSecurityScopedResource() } }
    }
    private func inspect() async {
        inspecting = true; failure = nil
        defer { inspecting = false }
        do { files = try await model.inspectAudio(urls) }
        catch { files = []; failure = error.localizedDescription }
    }
}
