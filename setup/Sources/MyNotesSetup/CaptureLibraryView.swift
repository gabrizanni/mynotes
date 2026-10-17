import SwiftUI
import AppKit

private func captureStateLabel(_ status: String) -> String {
    switch status {
    case "COMPLETED": return "Trascritta"
    case "QUEUED": return "In coda"
    case "RUNNING": return "In trascrizione"
    case "FAILED": return "Trascrizione non riuscita"
    case "INTERRUPTED": return "Trascrizione interrotta"
    case "NEEDS_REVIEW": return "Trascrizione non completata"
    case "OUTDATED": return "Audio modificato: trascrizione da aggiornare"
    case "WAITING": return "Audio non ancora disponibile"
    case "CANCELLED": return "Rimossa dalla coda"
    default: return "Da trascrivere"
    }
}

private func capturePhaseLabel(_ phase: String) -> String {
    switch phase {
    case "preparing_context", "accepted": return "Preparazione"
    case "normalizing": return "Preparazione audio"
    case "primary", "engine_started": return "Trascrizione audio"
    case "secondary": return "Confronto delle trascrizioni"
    case "extra": return "Verifica dei passaggi incerti"
    case "validating": return "Verifica dei timestamp"
    case "completed": return "Completata"
    default: return "Elaborazione in corso"
    }
}

struct CaptureRecordingsView: View {
    @ObservedObject var model: CaptureModel
    @CaptureViewState<String> private var query = ""
    @CaptureViewState<String> private var course = "all"
    @CaptureViewState<String?> private var selected: String?
    @CaptureViewState<String> private var statusFilter = "all"
    @CaptureViewState<CaptureRecording?> private var renaming: CaptureRecording?
    @CaptureViewState<String> private var renameText = ""
    let queueVisible: Bool
    private var recordings: [CaptureRecording] {
        model.recordings.filter { item in
            (query.isEmpty || (item.title + " " + (item.courseCode ?? "")).localizedCaseInsensitiveContains(query))
                && (course == "all" || (course == "none" ? item.courseCode == nil : item.courseCode == course))
                && (statusFilter == "all" || (statusFilter == "COMPLETED" ? item.isTranscribed : !item.isTranscribed))
        }
    }
    private var pendingCount: Int { model.recordings.filter { item in item.transcriptionAllowed && !item.isTranscribed && !(model.queue?.jobs.contains { $0.recordingID == item.id && $0.status != "CANCELLED" } ?? false) }.count }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            controls
            if queueVisible { queueList }
            else { libraryList }
        }
        .sheet(item: $renaming) { item in
            VStack(alignment: .leading, spacing: 18) {
                Text("Rinomina registrazione").font(.title2.bold())
                TextField("Titolo", text: $renameText).textFieldStyle(.roundedBorder)
                HStack {
                    Button("Annulla") { renaming = nil }.keyboardShortcut(.cancelAction)
                    Spacer()
                    Button("Salva") { Task { await model.queueAction("rename", arguments: ["--recording", item.id, "--title", renameText]); renaming = nil } }
                        .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(model.queueBusy || renameText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }.padding(24).frame(width: 440)
        }
    }
    private var controls: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(queueVisible ? "Trascrizioni sul Mac" : "Le tue registrazioni").font(.title3.bold())
                Text(queueVisible ? "\(model.queue?.jobs.filter { $0.status == "COMPLETED" }.count ?? 0) completate · \(model.queue?.jobs.filter { $0.status == "RUNNING" }.count ?? 0) in corso" : "Audio di tutti i corsi e registrazioni libere.")
                    .font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
            Picker("Alla volta", selection: Binding(get: { model.queue?.parallel ?? 1 }, set: { value in Task { await model.queueAction("configure", arguments: ["--parallel", String(value)]) } })) {
                Text("1 · consigliato").tag(1)
                ForEach(2...4, id: \.self) { Text(String($0)).tag($0) }
            }.frame(width: 195).help("Numero massimo di registrazioni elaborate contemporaneamente. Più lavori richiedono più memoria.")
            if queueVisible {
                Button(model.queue?.paused == true ? "Riprendi" : "Pausa") { Task { await model.queueAction(model.queue?.paused == true ? "resume" : "pause") } }
                    .help("La pausa lascia terminare le trascrizioni già avviate.")
            }
            Button("Trascrivi tutte (\(pendingCount))", systemImage: "text.badge.plus") {
                Task { await model.queueAction("enqueue", arguments: ["--all-untranscribed", "--parallel", String(model.queue?.parallel ?? 1)]) }
            }.buttonStyle(.borderedProminent).disabled(model.queueBusy || model.isPreview || pendingCount == 0)
            if model.queueBusy { ProgressView().controlSize(.small) }
        }.disabled(model.queueBusy || model.isPreview)
    }
    private var libraryList: some View {
        HSplitView {
            VStack(spacing: 10) {
                HStack {
                    TextField("Cerca una registrazione", text: $query)
                    Picker("Corso", selection: $course) {
                        Text("Tutti i corsi").tag("all"); Text("Senza corso").tag("none")
                        ForEach(model.snapshot?.courses ?? []) { Text($0.name).tag($0.code) }
                    }.labelsHidden().frame(width: 165)
                }
                Picker("Stato", selection: $statusFilter) {
                    Text("Tutte").tag("all"); Text("Da trascrivere").tag("NOT_STARTED"); Text("Trascritte").tag("COMPLETED")
                }.pickerStyle(.segmented).labelsHidden()
                List(selection: $selected) {
                    ForEach(recordings) { item in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(item.title).font(.headline).lineLimit(2)
                            Text(item.lectureID ?? item.courseCode ?? "Corso da associare").font(.callout).foregroundStyle(.secondary)
                            Text(CaptureDates.label(item.createdAt)).font(.caption).foregroundStyle(.secondary)
                            Text(captureStateLabel(displayState(item))).font(.caption).foregroundStyle(.secondary)
                        }.padding(.vertical, 5).tag(item.id)
                            .contextMenu { Button("Rinomina") { renameText = item.title; renaming = item }.disabled(model.isPreview) }
                    }
                }.overlay { if recordings.isEmpty { ContentUnavailableView("Nessuna registrazione", systemImage: "waveform", description: Text("Le registrazioni salvate compariranno qui.")) } }
            }.frame(minWidth: 430)
            Group {
                if let item = model.recordings.first(where: { $0.id == selected }) {
                    VStack(alignment: .leading, spacing: 16) {
                        Text(item.title).font(.title2.bold())
                        Text(item.lectureID ?? item.courseCode ?? "Corso da associare").foregroundStyle(.secondary)
                        if !item.createdAt.isEmpty { LabeledContent("Registrata", value: CaptureDates.label(item.createdAt)) }
                        if let duration = item.duration { LabeledContent("Durata", value: "\(Int(duration / 60)) min \(Int(duration) % 60) s") }
                        Label(captureStateLabel(displayState(item)), systemImage: item.isTranscribed ? "checkmark.circle" : "waveform")
                        ForEach(Array((item.warnings ?? []).enumerated()), id: \.offset) { _, warning in
                            Label(warning.message, systemImage: "info.circle").font(.callout).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        ForEach(Array((item.blockingReasons ?? []).enumerated()), id: \.offset) { _, reason in
                            Label(reason.message, systemImage: "exclamationmark.triangle").font(.callout).foregroundStyle(.orange)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        if !item.isTranscribed {
                            Button(retryJob(item) == nil ? "Trascrivi" : "Riprova trascrizione", systemImage: "text.badge.plus") {
                                Task {
                                    if let job = retryJob(item) { await model.queueAction("retry", arguments: ["--job", job.id]) }
                                    else { await model.queueAction("enqueue", arguments: ["--recording", item.id, "--parallel", String(model.queue?.parallel ?? 1)]) }
                                }
                            }.buttonStyle(.borderedProminent)
                                .disabled(model.queueBusy || model.isPreview || !item.transcriptionAllowed)
                        }
                        HStack {
                            Button("Ascolta audio", systemImage: "play") { NSWorkspace.shared.open(URL(fileURLWithPath: item.path)) }.disabled(item.audioAvailable == false)
                            Button("Mostra nel Finder") { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: item.path)]) }
                        }
                        if let path = item.transcriptText { Button("Apri trascrizione") { NSWorkspace.shared.open(URL(fileURLWithPath: path)) } }
                        Spacer()
                    }.padding(18)
                } else { ContentUnavailableView("Seleziona una registrazione", systemImage: "waveform", description: Text("Qui trovi audio e trascrizione.")) }
            }.frame(minWidth: 350, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
    }
    private func retryJob(_ item: CaptureRecording) -> CaptureQueueJob? {
        guard let job = model.queue?.jobs.last(where: { $0.recordingID == item.id }),
              ["FAILED", "INTERRUPTED", "NEEDS_REVIEW"].contains(job.status) else { return nil }
        return job
    }
    private func displayState(_ item: CaptureRecording) -> String {
        if item.isTranscribed { return "COMPLETED" }
        return model.queue?.jobs.last(where: { $0.recordingID == item.id && ["QUEUED", "RUNNING", "FAILED", "INTERRUPTED", "NEEDS_REVIEW"].contains($0.status) })?.status ?? item.transcriptionState
    }
    private var queueList: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Chiudendo la finestra non partiranno altri lavori. Quelli già avviati termineranno e salveranno il risultato.").font(.callout).foregroundStyle(.secondary)
            if let reason = model.queue?.waitReason { Label(reason, systemImage: "pause.circle").font(.callout).foregroundStyle(.secondary) }
            ScrollView {
                LazyVStack(spacing: 10) {
                    ForEach(model.queue?.jobs ?? []) { job in
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                VStack(alignment: .leading, spacing: 3) { Text(job.title).font(.headline); Text(job.courseCode ?? "Corso da associare").font(.caption).foregroundStyle(.secondary); if let date = job.createdAt { Text(CaptureDates.label(date)).font(.caption).foregroundStyle(.secondary) } }
                                Spacer()
                                if job.status == "RUNNING" { ProgressView().controlSize(.small) }
                                Text(captureStateLabel(job.status)).font(.callout)
                                if ["FAILED", "INTERRUPTED", "NEEDS_REVIEW"].contains(job.status) {
                                    Button("Riprova") { Task { await model.queueAction("retry", arguments: ["--job", job.id]) } }.disabled(model.queueBusy || model.isPreview)
                                }
                                if job.status == "QUEUED" { Button("Rimuovi") { Task { await model.queueAction("remove", arguments: ["--job", job.id]) } }.disabled(model.queueBusy || model.isPreview) }
                            }
                            if let phase = job.phase, job.status == "RUNNING" { Text("\(capturePhaseLabel(phase)) · \(Int(job.elapsedSeconds ?? 0)) s").font(.caption).foregroundStyle(.secondary) }
                            if let error = job.error, !error.isEmpty { DisclosureGroup("Dettagli") { Text(error).font(.caption).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) } }
                        }.padding(14).background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
                    }
                }
            }.overlay { if model.queue?.jobs.isEmpty != false { ContentUnavailableView("La coda è vuota", systemImage: "text.badge.checkmark", description: Text("Aggiungi le registrazioni da trascrivere.")) } }
        }
    }
}

struct CaptureCourseForm: View {
    @ObservedObject var model: CaptureModel
    var onSaved: (() -> Void)? = nil
    @Environment(\.dismiss) private var dismiss
    @CaptureViewState<String> private var name = ""
    @FocusState private var focused: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Nuovo corso").font(.title2.bold())
            TextField("Nome del corso", text: $name).textFieldStyle(.roundedBorder).focused($focused)
            Text("Completerai il contesto nella prossima chat. Puoi già registrare.").font(.callout).foregroundStyle(.secondary)
            if let error = model.error { Text(error).font(.callout).foregroundStyle(.red) }
            HStack {
                Button("Annulla") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("Aggiungi corso") { Task { if await model.addCourse(name) { onSaved?(); dismiss() } } }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(model.busy || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }.padding(24).frame(width: 440).onAppear { focused = true }.disabled(model.isPreview)
    }
}
