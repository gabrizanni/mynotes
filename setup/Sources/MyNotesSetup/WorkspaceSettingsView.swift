import AppKit
import SwiftUI

private enum SettingsSheet: Identifiable {
    case calendar, repair(String), course
    var id: String { switch self { case .calendar: return "calendar"; case .repair(let id): return id; case .course: return "course" } }
}

struct WorkspaceSettingsView: View {
    @ObservedObject var model: CaptureModel
    @ObservedObject var setup: SetupModel
    @CaptureViewState<String> private var section = "general"
    @CaptureViewState<SettingsSheet?> private var sheet: SettingsSheet?
    @CaptureViewState<CaptureSource?> private var disconnect: CaptureSource?
    var body: some View {
        VStack(spacing: 0) {
            if model.snapshot?.isRecording == true || model.snapshot?.isExporting == true { WorkspaceRecordingBar(model: model); Divider() }
            Picker("Impostazioni", selection: $section) {
                Text("Generali").tag("general")
                Text("Calendari").tag("calendars")
                Text("Registrazione").tag("recording")
                Text("Trascrizione").tag("transcription")
                Text("Agenti AI").tag("agents")
            }.pickerStyle(.segmented).labelsHidden().padding(24)
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    switch section {
                    case "calendars": calendars
                    case "recording": recording
                    case "transcription": transcription
                    case "agents": agents
                    default: general
                    }
                    if let error = model.error { Label(error, systemImage: "exclamationmark.circle").font(.callout).foregroundStyle(.red).textSelection(.enabled) }
                    if let error = setup.error { Text(error).font(.callout).foregroundStyle(.red).textSelection(.enabled) }
                }.padding(28).frame(maxWidth: .infinity, alignment: .leading)
            }
        }.frame(width: 760, height: 620).background(WorkspaceStyle.background)
        .task { await model.refresh(silent: model.snapshot != nil, includeLibrary: true); if setup.prepared { await setup.refreshDiagnostics() } }
        .sheet(item: $sheet) { kind in
            if let snapshot = model.snapshot {
                switch kind {
                case .calendar: CaptureCalendarForm(model: model, snapshot: snapshot)
                case .repair(let id): CaptureCalendarForm(model: model, snapshot: snapshot, repairing: id)
                case .course: CaptureCourseForm(model: model)
                }
            }
        }
        .confirmationDialog("Scollegare questo calendario?", isPresented: Binding(get: { disconnect != nil }, set: { if !$0 { disconnect = nil } }), presenting: disconnect) { source in
            Button("Scollega calendario", role: .destructive) { Task { await model.perform("unsubscribe", arguments: ["--source", source.id]) }; disconnect = nil }
            Button("Annulla", role: .cancel) { disconnect = nil }
        } message: { _ in Text("Le registrazioni e le trascrizioni esistenti restano conservate.") }
    }
    private var general: some View {
        VStack(alignment: .leading, spacing: 24) {
            heading("Il tuo spazio", "File, corsi e strumenti restano sul tuo Mac.")
            GroupBox {
                VStack(alignment: .leading, spacing: 12) {
                    LabeledContent("Versione", value: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "Sviluppo")
                    LabeledContent("Cartella audio", value: model.recordingFolder.map { URL(fileURLWithPath: $0).lastPathComponent } ?? "Da configurare")
                    HStack {
                        Button("Apri cartella") { if let path = model.recordingFolder { model.openFile(path) } }.disabled(model.recordingFolder == nil)
                        Button("Scegli cartella…") { chooseFolder() }.disabled(model.busy || model.queueBusy || model.snapshot?.isRecording == true || model.snapshot?.isExporting == true || model.queue?.activeJobs.isEmpty == false || model.isPreview)
                    }
                    if let path = model.recordingFolder { Text(path).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
                    Text("I file aggiunti alla cartella compaiono nella Raccolta. Gli audio importati dall’app vengono copiati qui.").font(.caption).foregroundStyle(.secondary)
                }.padding(12)
            }
            VStack(alignment: .leading, spacing: 12) {
                HStack { Text("Corsi").font(.headline); Spacer(); Button("Nuovo corso…", systemImage: "plus") { sheet = .course }.disabled(model.isPreview) }
                ForEach(model.snapshot?.courses ?? []) { course in
                    HStack { Text(course.name); Spacer(); if course.contextPending == true { Text("Contesto da completare nella chat").font(.caption).foregroundStyle(.secondary) } }.padding(.vertical, 6)
                }
                if model.snapshot?.courses.isEmpty != false { Text("Puoi registrare e trascrivere anche senza corso.").font(.callout).foregroundStyle(.secondary) }
            }
            Divider()
            Button("Apri guida", systemImage: "questionmark.circle") { setup.openGuide() }
        }
    }
    private var calendars: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack { heading("Calendari collegati", "Le sorgenti aggiornano il calendario senza modificare i tuoi calendari esterni."); Spacer(); Button("Collega…", systemImage: "plus") { sheet = .calendar } }
            ForEach(model.snapshot?.sources ?? []) { source in
                GroupBox {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Label(source.label, systemImage: "calendar").font(.headline)
                            Spacer()
                            Menu("Altro") {
                                Button("Sincronizza") { Task { await model.perform("sync", arguments: ["--source", source.id]) } }
                                Button("Correggi collegamento…") { sheet = .repair(source.id) }
                                if source.importMode == nil || source.importMode == "mapped" {
                                    Button("Importa tutti gli eventi con orario") { Task {
                                        if await model.perform("set-import-mode", arguments: ["--source", source.id, "--mode", "all_timed"]) { await model.perform("sync", arguments: ["--source", source.id]) }
                                    } }
                                }
                                Divider()
                                Button("Scollega…", role: .destructive) { disconnect = source }
                            }.fixedSize()
                        }
                        Text(source.host + " · " + source.timezone).font(.callout).foregroundStyle(.secondary)
                        Text("Aggiornato: " + CaptureDates.label(source.lastSuccess)).font(.caption).foregroundStyle(.secondary)
                        if let error = source.message, source.error != nil { Label(error, systemImage: "exclamationmark.circle").font(.callout).foregroundStyle(.orange) }
                        DisclosureGroup("Dettagli sincronizzazione") {
                            VStack(alignment: .leading, spacing: 8) {
                                if let next = source.nextAttempt { Text("Prossimo tentativo: " + CaptureDates.label(next)).font(.caption) }
                                if let summary = source.summary {
                                    Text("\(summary.imported) eventi · \(summary.pending) da associare · \(summary.excluded) esclusi").font(.caption)
                                    ForEach(Array(Set((summary.issues ?? []).map { $0.title + ": " + $0.reason })).sorted(), id: \.self) { issue in Text(issue).font(.caption) }
                                }
                            }.foregroundStyle(.secondary).padding(.top, 8)
                        }.font(.callout)
                    }.padding(12)
                }
            }
            if model.snapshot?.sources.isEmpty != false { ContentUnavailableView("Nessun calendario collegato", systemImage: "calendar", description: Text("Puoi collegarne uno oppure programmare una lezione dal menu Aggiungi.")) }
            Button("Sincronizza tutti") { Task { await model.perform("sync") } }.disabled(model.snapshot?.sources.isEmpty != false)
        }.disabled(model.busy || model.isPreview)
    }
    private var recording: some View {
        VStack(alignment: .leading, spacing: 24) {
            heading("Registrazione", "La tua preferenza di attivazione rimane valida dopo la chiusura dell’app.")
            if let snapshot = model.snapshot {
                Form {
                    Toggle("Capture abilitato", isOn: Binding(get: { snapshot.enabled }, set: { value in Task { await model.perform(value ? "enable" : "disable") } }))
                        .disabled(snapshot.isRecording || snapshot.isExporting)
                    Toggle("Programmazione automatica", isOn: Binding(get: { !snapshot.paused }, set: { value in Task { await model.perform(value ? "resume" : "pause") } })).disabled(!snapshot.enabled)
                    LabeledContent("Stato", value: snapshot.stateLabel)
                    LabeledContent("Microfono", value: snapshot.runtime.microphonePermission == "AUTHORIZED" ? "Autorizzato" : "Da verificare")
                }
                HStack {
                    if snapshot.enabled && snapshot.running == false { Button("Riavvia Capture") { Task { await model.restoreRecorderIfNeeded(retry: true) } } }
                    Button("Verifica microfono nelle Impostazioni di Sistema") { NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")!) }
                }
                DisclosureGroup("Test e recupero") {
                    HStack {
                        Button("Test microfono di 60 secondi") { Task {
                            let ready = snapshot.running == true ? true : await model.restoreRecorderIfNeeded(retry: true)
                            if ready { await model.perform("test", arguments: ["--wait-seconds", "10"]) }
                        } }
                        Button("Recupera audio conservati") { Task { await model.perform("recover", arguments: ["--wait-seconds", "10"]) } }
                    }.padding(.top, 8).disabled(snapshot.isRecording || snapshot.isExporting)
                }
            }
            Text("Se Digiexam chiude Capture, il recupero resta sospeso mentre Digiexam è aperto. Chiudilo e torna a MyNotes per riprendere.").font(.callout).foregroundStyle(.secondary)
        }.disabled(model.busy || model.queueBusy || model.isPreview)
    }
    private var transcription: some View {
        VStack(alignment: .leading, spacing: 24) {
            heading("Trascrizione", "Lingua e profilo seguono l’audio o il corso associato.")
            Text("La scelta dell’audio si fa nel dettaglio della lezione. Con più sorgenti, MyNotes richiede una scelta prima di trascrivere.").font(.callout).foregroundStyle(.secondary)
            DisclosureGroup("Opzioni avanzate") {
                Form {
                    Stepper("Lavori in parallelo: \(model.queue?.parallel ?? 1)", value: Binding(get: { model.queue?.parallel ?? 1 }, set: { value in Task { await model.queueAction("configure", arguments: ["--parallel", String(value)]) } }), in: 1...4)
                    Text("La concorrenza non cambia la sorgente scelta né i tentativi già avviati.").font(.caption).foregroundStyle(.secondary)
                }.padding(.top, 12)
            }
            Button("Verifica strumenti locali") { Task { await setup.refreshDiagnostics() } }
            Text(setup.diagnostics).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
        }.disabled(model.queueBusy || model.isPreview)
    }
    private var agents: some View {
        VStack(alignment: .leading, spacing: 24) {
            heading("Agenti AI", "Queste scelte valgono per le nuove chat e gli appunti. La chat continua ad aprirsi nell’agente esterno.")
            Form {
                Picker("Agente", selection: $setup.provider) { Text("Codex").tag("codex"); Text("Agy").tag("agy"); Text("Claude Code").tag("claude") }
                Picker("Modello", selection: Binding(get: { setup.modelID }, set: { setup.selectModel($0) })) {
                    ForEach(setup.modelChoices.filter { $0.selectable }) { Text($0.label).tag($0.id) }
                }
                Picker("Effort", selection: $setup.effortID) {
                    Text("Automatico").tag("")
                    ForEach(setup.effortChoices.filter { !$0.isEmpty }, id: \.self) { Text($0).tag($0) }
                }
                Picker("Lingua della chat", selection: $setup.interfaceLanguage) { Text("Italiano").tag("it"); Text("English").tag("en") }
            }.onChange(of: setup.provider) { previous, _ in setup.selectProvider(from: previous) }
            HStack {
                Button("Salva preferenze") { setup.saveSelection() }.buttonStyle(.borderedProminent).disabled(!setup.modelIsValid)
                Menu("Gestisci agente") {
                    Button("Aggiorna elenco modelli") { setup.refreshModelCatalog() }
                    Button("Accedi…") { setup.openLogin() }
                    Button(setup.providerFound ? "Aggiorna CLI…" : "Installa CLI…") { NSWorkspace.shared.open(setup.installURL) }
                    Button("Ricontrolla agente") { setup.refreshProvider() }
                }
                .fixedSize()
                if setup.catalogLoading || setup.savingSelection { ProgressView().controlSize(.small) }
            }
            if let notice = setup.selectionNotice { Label(notice, systemImage: "checkmark.circle").font(.callout).foregroundStyle(.secondary) }
            Divider()
            HStack {
                Label(setup.prepared ? "MyNotes installato" : "MyNotes da preparare", systemImage: setup.prepared ? "checkmark.circle" : "wrench.and.screwdriver")
                Spacer()
                Button(setup.prepared ? "Ricontrolla strumenti" : "Prepara MyNotes") { if setup.prepared { Task { await setup.refreshDiagnostics() } } else { setup.prepare() } }
            }
            if setup.busy { ProgressView(setup.message); Text(setup.progressDetail).font(.caption).foregroundStyle(.secondary) }
            DisclosureGroup("Stato e dettagli tecnici") { Text(setup.diagnostics.isEmpty ? setup.catalogMessage : setup.diagnostics).font(.caption).textSelection(.enabled).padding(.top, 8) }
        }.disabled(setup.busy || setup.savingSelection || model.isPreview)
    }
    private func heading(_ title: String, _ subtitle: String) -> some View { VStack(alignment: .leading, spacing: 8) { Text(title).font(.title2.weight(.semibold)); Text(subtitle).font(.callout).foregroundStyle(.secondary) } }
    private func chooseFolder() {
        let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.canCreateDirectories = true; panel.prompt = "Usa questa cartella"
        if panel.runModal() == .OK, let url = panel.url { Task { await model.queueAction("folder", arguments: ["--path", url.path]) } }
    }
}

extension SetupModel {
    func openGuide() { NSWorkspace.shared.open(payload.appendingPathComponent("docs/Guida.html")) }
}
