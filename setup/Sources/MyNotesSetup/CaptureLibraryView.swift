import SwiftUI
import AppKit

private func captureStateLabel(_ status: String) -> String {
    switch status {
    case "COMPLETED": return "Trascritta"
    case "QUEUED": return "In coda"
    case "RUNNING": return "In trascrizione"
    case "FAILED": return "Trascrizione non riuscita"
    case "INTERRUPTED": return "Trascrizione interrotta"
    case "NEEDS_REVIEW": return "Da verificare"
    case "OUTDATED": return "Audio modificato: trascrizione da aggiornare"
    case "WAITING": return "Audio non ancora disponibile"
    case "CANCELLED": return "Rimossa dalla coda"
    case "CANCELLING": return "Interruzione in corso"
    default: return "Da trascrivere"
    }
}

private func capturePhaseLabel(_ phase: String) -> String {
    switch phase {
    case "preparing_context", "accepted": return "Preparazione"
    case "normalizing": return "Preparazione audio"
    case "primary", "primary_pass", "processing", "engine_started": return "Trascrizione audio"
    case "secondary", "secondary_pass": return "Confronto delle trascrizioni"
    case "extra", "extra_pass": return "Verifica dei passaggi incerti"
    case "validating": return "Verifica dei timestamp"
    case "completed": return "Completata"
    default: return "Elaborazione in corso"
    }
}

struct CaptureRecordingsView: View {
    @ObservedObject var model: CaptureModel
    @CaptureViewState<String> private var query = ""
    @CaptureViewState<String> private var course = "all"
    @CaptureViewState<Set<String>> private var selected = []
    @CaptureViewState<CaptureRecordingFilter> private var statusFilter = .all
    @CaptureViewState<String> private var sortOrder = "recent"
    @CaptureViewState<Bool> private var showReview = false
    @CaptureViewState<Bool> private var showHistory = false
    @CaptureViewState<CaptureRecording?> private var renaming: CaptureRecording?
    @CaptureViewState<String> private var renameText = ""
    @CaptureViewState<CaptureRecording?> private var associating: CaptureRecording?
    @CaptureViewState<String> private var associationCourse = ""
    @CaptureViewState<String> private var associationLesson = ""
    let queueVisible: Bool
    private var recordings: [CaptureRecording] {
        model.recordings.sorted { left, right in
            switch sortOrder {
            case "title": return left.title.localizedStandardCompare(right.title) == .orderedAscending
            case "oldest": return left.createdAt < right.createdAt
            default: return left.createdAt > right.createdAt
            }
        }.filter { item in
            (query.isEmpty || (item.title + " " + (item.courseCode ?? "") + " " + courseName(item.courseCode) + " " + URL(fileURLWithPath: item.path).lastPathComponent).localizedCaseInsensitiveContains(query))
                && (course == "all" || (course == "none" ? item.courseCode == nil : item.courseCode == course))
                && statusFilter.includes(item)
        }
    }
    private var pendingRecordings: [CaptureRecording] { model.recordings.filter { CaptureRecordingFilter.ready.includes($0) && ($0.collection ?? "inbox") == "inbox" } }
    private var pendingCount: Int { pendingRecordings.count }
    private var selectedReady: [CaptureRecording] { recordings.filter { selected.contains($0.id) && CaptureRecordingFilter.ready.includes($0) } }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            controls
            if !queueVisible { folderControls }
            if queueVisible { queueList }
            else { libraryList }
        }
        .onChange(of: query) { _, _ in reconcileSelection() }
        .onChange(of: course) { _, _ in reconcileSelection() }
        .onChange(of: statusFilter) { _, _ in reconcileSelection() }
        .sheet(item: $renaming) { item in
            VStack(alignment: .leading, spacing: 18) {
                Text("Rinomina registrazione").font(.title2.bold())
                TextField("Titolo", text: $renameText).textFieldStyle(.roundedBorder)
                if let error = model.error { Text(error).font(.callout).foregroundStyle(.red) }
                HStack {
                    Button("Annulla") { renaming = nil }.keyboardShortcut(.cancelAction)
                    Spacer()
                    Button("Salva") { Task { await model.queueAction("rename", arguments: ["--recording", item.id, "--title", renameText]); if model.error == nil { renaming = nil } } }
                        .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(model.queueBusy || renameText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }.padding(24).frame(width: 440)
        }
        .sheet(item: $associating) { item in
            VStack(alignment: .leading, spacing: 18) {
                Text("Associa registrazione").font(.title2.bold())
                Text(item.title).foregroundStyle(.secondary)
                Picker("Corso", selection: $associationCourse) {
                    Text("Scegli un corso").tag("")
                    ForEach(model.snapshot?.courses ?? []) { Text($0.name).tag($0.code) }
                }
                TextField("Numero lezione (facoltativo)", text: $associationLesson).textFieldStyle(.roundedBorder)
                if let error = model.error { Text(error).font(.callout).foregroundStyle(.red) }
                HStack {
                    Button("Annulla") { associating = nil }.keyboardShortcut(.cancelAction)
                    Spacer()
                    Button("Salva associazione") {
                        Task {
                            var args = ["--recording", item.id, "--course", associationCourse]
                            if !associationLesson.isEmpty { args += ["--lesson", associationLesson] }
                            await model.queueAction("associate", arguments: args)
                            if model.error == nil { associating = nil }
                        }
                    }.disabled(associationCourse.isEmpty || model.queueBusy || (!associationLesson.isEmpty && (Int(associationLesson).map { !(1...999).contains($0) } ?? true)))
                }
            }.padding(24).frame(width: 440)
        }
    }

    private var controls: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(queueVisible ? "Coda trascrizioni" : "Registrazioni").font(.title3.bold())
                Text(queueVisible ? "\(model.queue?.activeJobs.filter { $0.status == "RUNNING" }.count ?? 0) in corso · \(model.queue?.activeJobs.filter { $0.status == "QUEUED" }.count ?? 0) in attesa · \(model.queue?.currentJobs.filter { $0.status == "COMPLETED" }.count ?? 0) completate" : "\(model.recordings.count) audio · \(pendingCount) pronti da trascrivere")
                    .font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
            if queueVisible { Picker("In parallelo", selection: Binding(get: { model.queue?.parallel ?? 1 }, set: { value in Task { await model.queueAction("configure", arguments: ["--parallel", String(value)]) } })) {
                Text("1 · consigliato").tag(1)
                ForEach(2...4, id: \.self) { Text(String($0)).tag($0) }
            }.frame(width: 195).help("Numero massimo di trascrizioni contemporanee, condiviso con la chat.") }
            if queueVisible {
                Button(model.queue?.paused == true ? "Riprendi coda" : "Pausa coda") { Task { await model.queueAction(model.queue?.paused == true ? "resume" : "pause") } }
                    .help("La pausa lascia terminare le trascrizioni già avviate.")
            }
            if !queueVisible {
                Button(selected.count > 1 ? "Trascrivi selezionate (\(selectedReady.count))" : "Trascrivi tutte le pronte (\(pendingCount))", systemImage: "text.badge.plus") {
                    Task {
                        if selected.count > 1 { await model.enqueueSelection(selectedReady, selectedCount: selected.count) }
                        else { await model.enqueueSelection(pendingRecordings, selectedCount: pendingCount) }
                    }
                }.buttonStyle(.borderedProminent).disabled(model.queueBusy || model.isPreview || (selected.count > 1 ? selectedReady.isEmpty : pendingCount == 0))
                    .help("Aggiunge solo gli audio pronti. Usa Maiuscole o Comando per selezionare più registrazioni.")
            }
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
                HStack { Picker("Stato", selection: $statusFilter) {
                    ForEach(CaptureRecordingFilter.allCases) { Text($0.title).tag($0) }
                }.pickerStyle(.segmented).labelsHidden().controlSize(.small)
                    Menu {
                        Picker("Ordina", selection: $sortOrder) { Text("Più recenti").tag("recent"); Text("Meno recenti").tag("oldest"); Text("Titolo").tag("title") }
                    } label: { Image(systemName: "arrow.up.arrow.down") }.menuStyle(.borderlessButton).fixedSize().help("Ordina registrazioni")
                }
                List(selection: $selected) {
                    ForEach(recordings) { item in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(item.title).font(.headline).lineLimit(2)
                            Text(courseName(item.courseCode)).font(.callout).foregroundStyle(.secondary)
                            HStack {
                                Text(CaptureDates.label(item.createdAt) + (item.duration.map { " · \(Int($0 / 60)) min" } ?? "")).lineLimit(1)
                                Spacer(minLength: 8)
                                Text(captureStateLabel(displayState(item))).lineLimit(1)
                            }.font(.caption).foregroundStyle(.secondary)
                        }.padding(.vertical, 5).tag(item.id)
                            .contextMenu { Button("Rinomina") { renameText = item.title; model.error = nil; renaming = item }.disabled(model.isPreview || model.queueBusy) }
                    }
                }.overlay { if recordings.isEmpty { ContentUnavailableView(model.recordings.isEmpty ? "Nessuna registrazione" : "Nessun risultato", systemImage: "line.3.horizontal.decrease.circle", description: Text(model.recordings.isEmpty ? "Registra una lezione oppure aggiungi un audio nella cartella della raccolta." : "Cambia ricerca, corso o stato per vedere gli altri audio.")) } }
            }.frame(minWidth: 430)
            Group {
                if selected.count == 1, let item = recordings.first(where: { selected.contains($0.id) }) {
                    ScrollView { VStack(alignment: .leading, spacing: 16) {
                        Text(item.title).font(.title2.bold())
                        Text(courseName(item.courseCode)).foregroundStyle(.secondary)
                        if !item.createdAt.isEmpty { LabeledContent("Registrata", value: CaptureDates.label(item.createdAt)) }
                        if let duration = item.duration { LabeledContent("Durata", value: "\(Int(duration / 60)) min \(Int(duration) % 60) s") }
                        Label(captureStateLabel(displayState(item)), systemImage: item.isTranscribed ? "checkmark.circle" : "waveform")
                        if item.hasProblem {
                            Label(CaptureProblem.summary(status: item.transcriptionState), systemImage: "exclamationmark.triangle")
                                .font(.callout).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)
                        }
                        if !item.detailMessages.isEmpty || item.diagnosticPath != nil {
                            DisclosureGroup(item.hasProblem ? "Problema e diagnosi" : "Informazioni sull’audio") {
                                ForEach(item.detailMessages, id: \.self) { message in
                                    Text(message).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                                }
                                if let path = item.diagnosticPath { Button("Mostra diagnosi") { model.revealFile(path) } }
                            }
                        }
                        if let position = item.queuePosition { Text("Posizione in coda: \(position)").font(.callout) }
                        if let reason = item.waitReason { Label(reason, systemImage: "pause.circle").font(.callout).foregroundStyle(.secondary) }
                        if item.archiveStatus == "PENDING" {
                            Label("Trascritta — archiviazione da completare", systemImage: "folder.badge.questionmark").foregroundStyle(.orange)
                            if let error = item.archiveError { Text(error).font(.caption).textSelection(.enabled) }
                        }
                        recordingActions(item)
                        HStack {
                            Button("Ascolta audio", systemImage: "play") { model.openAudio(item.path) }.disabled(item.audioAvailable == false)
                            Button("Mostra nel Finder") { model.revealFile(item.path) }
                        }
                        if item.isTranscribed, let path = item.exportText ?? item.transcriptText {
                            if item.archiveStatus == "PENDING" { Button("Apri trascrizione") { model.openFile(path) } }
                            else { Button("Apri trascrizione") { model.openFile(path) }.buttonStyle(.borderedProminent) }
                        }
                        Spacer()
                    }.padding(18).frame(maxWidth: .infinity, alignment: .leading) }
                } else { ContentUnavailableView(selected.count > 1 ? "\(selected.count) registrazioni selezionate" : "Seleziona una registrazione", systemImage: selected.count > 1 ? "checkmark.circle" : "waveform", description: Text(selected.count > 1 ? "\(selectedReady.count) pronte da trascrivere. Gli altri stati non vengono riavviati." : "Qui trovi audio e trascrizione.")) }
            }.frame(minWidth: 350, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
    }
    private func displayState(_ item: CaptureRecording) -> String { item.transcriptionState }
    private var folderControls: some View {
        HStack(spacing: 12) {
            Label(model.recordingFolder.map { URL(fileURLWithPath: $0).lastPathComponent } ?? "Cartella registrazioni", systemImage: "folder").font(.callout).lineLimit(1).help(model.recordingFolder ?? "Concorda la cartella con l’agente al primo utilizzo.")
            Spacer()
            Button("Apri cartella") { if let path = model.recordingFolder { model.openFile(path) } }.disabled(model.recordingFolder == nil)
            Menu("Opzioni cartella") { Button("Cambia cartella…") {
                let panel = NSOpenPanel(); panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
                panel.message = "Scegli la cartella degli audio originali. Le trascrizioni TXT saranno divise per corso."
                if panel.runModal() == .OK, let url = panel.url { Task { await model.queueAction("folder", arguments: ["--path", url.path]) } }
            }.disabled(model.queueBusy || model.busy || model.isPreview || model.snapshot?.isRecording == true || model.snapshot?.isExporting == true || model.queue?.activeJobs.isEmpty == false)
                Button("Copia percorso") {
                    if let path = model.recordingFolder { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(path, forType: .string) }
                }.disabled(model.recordingFolder == nil)
            }.fixedSize().help("Il cambio cartella è disponibile quando registrazioni, esportazioni e trascrizioni sono ferme.")
        }
    }
    private func reconcileSelection() {
        selected.formIntersection(Set(recordings.map(\.id)))
    }
    @ViewBuilder private func recordingActions(_ item: CaptureRecording) -> some View {
        let actions = item.actions ?? (item.transcriptionAllowed ? ["enqueue"] : [])
        FlowActions(actions: actions, archivePending: item.archiveStatus == "PENDING") { action in
            if action == "associate" {
                associationCourse = item.courseCode ?? ""; associationLesson = item.lectureID?.components(separatedBy: "-L").last ?? ""; model.error = nil; associating = item
            } else if action == "relink" {
                let panel = NSOpenPanel(); panel.canChooseDirectories = false; panel.canChooseFiles = true; panel.allowsMultipleSelection = false
                if panel.runModal() == .OK, let url = panel.url { Task { await model.queueAction("relink", arguments: ["--recording", item.id, "--path", url.path]) } }
            } else {
                Task {
                    await model.recordingAction(action, item: item)
                }
            }
        }.disabled(model.queueBusy || model.busy || model.isPreview)
    }
    private var queueList: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let reason = model.queue?.waitReason { Label(reason, systemImage: "pause.circle").font(.callout).foregroundStyle(.secondary) }
                if model.queue?.activeJobs.isEmpty != false {
                    Label("Nessun lavoro in attesa", systemImage: "checkmark.circle").font(.headline)
                    Text("Avvia una trascrizione dalla scheda Registrazioni.").foregroundStyle(.secondary)
                } else {
                    ForEach(model.queue?.activeJobs ?? []) { queueRow($0) }
                }
                Divider()
                DisclosureGroup("Da verificare (\(model.queue?.reviewJobs.count ?? 0))", isExpanded: $showReview) {
                    ForEach(model.queue?.reviewJobs ?? []) { queueRow($0) }
                }
                DisclosureGroup("Cronologia (\(model.queue?.historyJobs.count ?? 0))", isExpanded: $showHistory) {
                    ForEach(model.queue?.historyJobs ?? []) { queueRow($0, historical: true) }
                }
                Text("La pausa e la chiusura della finestra fermano le nuove partenze. I lavori attivi terminano e salvano il risultato.")
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(4).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
    private func queueRow(_ job: CaptureQueueJob, historical: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(job.title).font(.headline).lineLimit(1)
                    Text([courseName(job.courseCode), job.createdAt.map { CaptureDates.label($0) }].compactMap { $0 }.joined(separator: " · "))
                        .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer(minLength: 8)
                if job.status == "RUNNING" { ProgressView().controlSize(.small) }
                Text(captureStateLabel(job.status)).font(.callout)
                if !historical {
                    FlowActions(actions: job.actions ?? [], archivePending: job.archiveStatus == "PENDING") { action in
                        Task { await model.queueAction(action, arguments: action == "finish" ? ["--recording", job.recordingID] : ["--job", job.id]) }
                    }.disabled(model.queueBusy || model.busy || model.isPreview)
                }
            }
            if let phase = job.phase, job.status == "RUNNING" {
                Text("\(capturePhaseLabel(phase)) · \(Int(job.elapsedSeconds ?? 0)) s").font(.caption).foregroundStyle(.secondary)
            }
            if let position = job.queuePosition, job.status == "QUEUED" { Text("Posizione \(position)").font(.caption).foregroundStyle(.secondary) }
            if let path = job.exportText ?? job.outputs?["transcript_text"], job.status == "COMPLETED" {
                Button("Apri trascrizione") { model.openFile(path) }
            }
            if job.archiveStatus == "PENDING" { Label("Trascritta — archiviazione da completare", systemImage: "folder.badge.questionmark").foregroundStyle(.orange) }
            if let error = job.error, !error.isEmpty {
                DisclosureGroup("Problema e diagnosi") {
                    Text(CaptureProblem.summary(status: job.status)).font(.callout)
                    Text(error).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                    if let path = job.diagnosticPath { Button("Mostra diagnosi") { model.revealFile(path) } }
                }.font(.caption)
            }
        }.padding(.vertical, 10).padding(.horizontal, 10)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
    }
    private func courseName(_ code: String?) -> String {
        guard let code else { return "Senza corso" }
        return model.snapshot?.courses.first(where: { $0.code == code })?.name ?? code
    }

}

private struct FlowActions: View {
    var actions: [String]
    var archivePending = false
    var perform: (String) -> Void
    private func label(_ action: String) -> String {
        switch action {
        case "enqueue": return "Trascrivi"
        case "retry": return "Riprova"
        case "restart": return "Trascrivi da zero"
        case "remove": return "Rimuovi dalla coda"
        case "cancel": return "Interrompi trascrizione"
        case "finish": return archivePending ? "Completa archiviazione" : "Rigenera copia TXT"
        case "relink": return "Ricollega audio"
        case "associate": return "Associa corso"
        case "download": return "Scarica audio sul Mac"
        default: return action
        }
    }
    private var primary: String? {
        ["cancel", "remove", "enqueue", "retry", "restart", "relink", "download"].first { actions.contains($0) }
            ?? (archivePending && actions.contains("finish") ? "finish" : nil)
    }
    var body: some View {
        HStack(spacing: 8) {
            if let primary { Button(label(primary)) { perform(primary) } }
            let secondary = actions.filter { $0 != primary }
            if !secondary.isEmpty {
                Menu("Altre azioni") {
                    ForEach(secondary, id: \.self) { action in Button(label(action)) { perform(action) } }
                }.fixedSize()
            }
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
