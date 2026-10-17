import AppKit
import SwiftUI
import Combine

// Preserve the State property wrapper when Command Line Tools lack SwiftUI macros.
typealias CaptureViewState<Value> = SwiftUI.State<Value>

private enum CaptureSheet: Equatable, Identifiable {
    case calendar, scheduled, immediate, edit, course
    case repair(String)
    var id: String {
        switch self {
        case .calendar: return "calendar"
        case .scheduled: return "scheduled"
        case .immediate: return "immediate"
        case .edit: return "edit"
        case .course: return "course"
        case .repair(let sourceID): return "repair:" + sourceID
        }
    }
}

struct CaptureView: View {
    @StateObject private var model = CaptureModel()
    @CaptureViewState<String?> private var selected: String?
    @CaptureViewState<String> private var query = ""
    @CaptureViewState<Bool> private var showCancelled = false
    @CaptureViewState<String> private var section = "calendar"
    @CaptureViewState<CaptureSheet?> private var sheet: CaptureSheet?
    @Environment(\.scenePhase) private var scenePhase
    let active: Bool
    private let poll = Timer.publish(every: 5, on: .main, in: .common).autoconnect()
    private var snapshot: CaptureSnapshot? { model.snapshot }
    private var selectedEvent: CaptureEvent? { snapshot?.events.first { $0.id == selected } }
    private var events: [CaptureEvent] {
        (snapshot?.events ?? []).filter {
            (CaptureDates.read($0.stopAt) ?? .distantPast) > Date()
                && (showCancelled || $0.eligibility != "CANCELLED")
                && (query.isEmpty || ($0.title + " " + $0.courseLabel).localizedCaseInsensitiveContains(query))
        }.sorted { $0.start == $1.start ? $0.id < $1.id : $0.start < $1.start }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header
            if let error = model.error { ScrollView { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }.frame(maxHeight: 80) }
            if let notice = model.notice {
                HStack {
                    Label(notice, systemImage: "checkmark.circle").font(.callout).foregroundStyle(.secondary)
                    Spacer()
                    Button { model.notice = nil } label: { Image(systemName: "xmark") }.buttonStyle(.plain).help("Chiudi messaggio")
                }
            }
            if let warning = model.refreshWarning { Label(warning, systemImage: "arrow.clockwise").font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
            if let snapshot {
                status(snapshot)
                Picker("Vista Capture", selection: $section) {
                    Text("Calendario").tag("calendar")
                    Text("Registrazioni").tag("recordings")
                    Text("Coda").tag("transcriptions")
                }.pickerStyle(.segmented).labelsHidden().frame(maxWidth: 460)
                if section == "calendar" { HSplitView {
                    VStack(alignment: .leading, spacing: 14) {
                        calendars(snapshot)
                        lessons
                    }.frame(minWidth: 430, idealWidth: 500)
                    detail(snapshot).frame(minWidth: 350, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                } } else { CaptureRecordingsView(model: model, queueVisible: section == "transcriptions") }
            } else {
                ContentUnavailableView("Capture non disponibile", systemImage: "calendar.badge.clock", description: Text("Completa il setup di MyNotes e premi Aggiorna. Puoi registrare anche senza corso."))
            }
        }.padding(24)
        .task(id: model.notice) {
            guard let message = model.notice else { return }
            try? await Task.sleep(for: .seconds(10))
            if !Task.isCancelled && model.notice == message { model.notice = nil }
        }
        .onAppear { if active { refreshVisible() } }
        .onReceive(poll) { date in
            if active && scenePhase == .active && sheet == nil && model.claimPoll(at: date) {
                Task { await model.refresh(silent: true, includeLibrary: section != "calendar") }
            }
        }
        .onChange(of: active) { _, visible in if visible { refreshVisible() } }
        .onChange(of: scenePhase) { _, phase in if phase == .active { refreshVisible() } }
        .onChange(of: section) { _, _ in refreshVisible() }
        .onChange(of: sheet) { _, value in if value == nil { refreshVisible() } }
        .onChange(of: model.lastEventID) { _, value in if let value { selected = value } }
        .sheet(item: $sheet) { kind in
            if let snapshot {
                if kind == .course { CaptureCourseForm(model: model) }
                else if case .repair(let sourceID) = kind { CaptureCalendarForm(model: model, snapshot: snapshot, repairing: sourceID) }
                else if kind == .calendar { CaptureCalendarForm(model: model, snapshot: snapshot) }
                else { CaptureLessonForm(model: model, snapshot: snapshot, event: kind == .edit ? selectedEvent : nil, immediate: kind == .immediate) }
            }
        }
    }
    private func refreshVisible() {
        guard active, scenePhase == .active, sheet == nil else { return }
        _ = model.claimPoll(force: true)
        Task {
            await model.refresh(silent: model.snapshot != nil, includeLibrary: section != "calendar")
            if model.isPreview && selected == nil { selected = model.snapshot?.events.first?.id }
        }
    }
    private func openLesson(_ kind: CaptureSheet) {
        Task { if await model.prepareLesson() { sheet = kind } }
    }
    private var header: some View {
        HStack(spacing: 12) {
            if let path = Bundle.main.url(forResource: "MyNotesCaptureLogo", withExtension: "png"), let logo = NSImage(contentsOf: path) {
                Image(nsImage: logo).resizable().scaledToFit().frame(width: 32, height: 32).accessibilityHidden(true)
            } else { Image(systemName: "mic.fill").font(.largeTitle).foregroundStyle(.blue) }
            VStack(alignment: .leading) {
                Text("Capture").font(.title2.bold())
                if let count = snapshot?.courses.filter({ $0.contextPending == true }).count, count > 0 {
                    Text(count == 1 ? "1 corso da completare nella prossima chat" : "\(count) corsi da completare nella prossima chat").font(.caption).foregroundStyle(.secondary)
                }
                if model.isPreview { Text("Anteprima · dati di esempio").font(.caption).foregroundStyle(.secondary) }
            }
            Spacer()
            if model.busy { ProgressView().controlSize(.small) }
            Button("Aggiorna") { Task { await model.refresh(includeLibrary: section != "calendar") } }.disabled(model.busy)
            if section == "calendar" {
                Button("Programma lezione", systemImage: "calendar.badge.plus") { openLesson(.scheduled) }.disabled(unavailable)
            }
            Menu("Opzioni Capture") {
                Button("Nuovo corso…") { sheet = .course }.disabled(model.busy || model.isPreview)
                if let snapshot {
                    Button(snapshot.paused ? "Riprendi calendario" : "Sospendi calendario") { Task { await model.perform(snapshot.paused ? "resume" : "pause") } }.disabled(!snapshot.enabled || model.busy || model.isPreview)
                    Button(snapshot.enabled ? "Disattiva Capture" : "Attiva Capture") { Task { await model.perform(snapshot.enabled ? "disable" : "enable") } }.disabled(snapshot.isRecording || snapshot.isExporting || model.busy || model.isPreview)
                }
            }.fixedSize()
            Button("Registra ora", systemImage: "record.circle") { openLesson(.immediate) }.buttonStyle(.borderedProminent).disabled(unavailable || snapshot?.running != true || snapshot?.runtime.recording == true || snapshot?.runtime.exporting == true)
        }
    }
    private var unavailable: Bool { model.busy || model.isPreview }
    private func status(_ snapshot: CaptureSnapshot) -> some View {
        HStack(spacing: 16) {
            Label(snapshot.stateLabel, systemImage: snapshot.isRecording ? "record.circle.fill" : "mic")
                .foregroundStyle(snapshot.isRecording ? Color.red : Color.primary)
            if let current = snapshot.runtime.currentSession, snapshot.isRecording {
                Text(current.event.courseLabel).font(.callout)
                if let start = CaptureDates.read(current.actualStart) {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        let seconds = max(0, Int(context.date.timeIntervalSince(start)))
                        Text(String(format: "%02d:%02d:%02d", seconds / 3600, seconds / 60 % 60, seconds % 60)).monospacedDigit().font(.callout)
                    }
                }
            }
            Spacer()
            Text(snapshot.runtime.microphonePermission == "AUTHORIZED" ? "Microfono autorizzato" : "Microfono da verificare").font(.caption).foregroundStyle(.secondary)
            Group { if snapshot.isRecording {
                Button("Stop e salva") { Task { await model.stopNow() } }.disabled(model.stopping || model.isPreview)
            } else {
                if snapshot.paused { Text("Calendario sospeso").font(.callout).foregroundStyle(.secondary) }
            } }.disabled(snapshot.isRecording ? model.stopping || model.isPreview : model.busy || model.isPreview)
        }.padding(12).background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
    }
    private func calendars(_ snapshot: CaptureSnapshot) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("Calendari").font(.headline); Spacer()
                    Button("Collega calendario", systemImage: "plus") { sheet = .calendar }.disabled(unavailable)
                    Button("Sincronizza tutti") { Task { await model.perform("sync") } }.disabled(model.busy || model.isPreview || snapshot.sources.isEmpty)
                }
                if snapshot.sources.isEmpty { Text("Collega un calendario HTTPS/iCal oppure aggiungi una lezione speciale.").font(.callout).foregroundStyle(.secondary) }
                if !snapshot.sources.isEmpty { ScrollView { VStack(alignment: .leading, spacing: 12) { ForEach(snapshot.sources) { source in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Label(source.label, systemImage: "calendar").font(.callout.bold()).fixedSize(horizontal: false, vertical: true)
                        }
                        HStack {
                            Button("Sincronizza") { Task { await model.perform("sync", arguments: ["--source", source.id]) } }
                            Button("Correggi collegamento") { sheet = .repair(source.id) }
                            Button("Scollega") { Task { await model.perform("unsubscribe", arguments: ["--source", source.id]) } }
                        }.disabled(model.busy || model.isPreview)
                        if source.importMode == nil || source.importMode == "mapped" {
                            Button("Importa tutti gli eventi con orario") { Task {
                                if await model.perform("set-import-mode", arguments: ["--source", source.id, "--mode", "all_timed"]) {
                                    _ = await model.perform("sync", arguments: ["--source", source.id])
                                }
                            } }.font(.caption).disabled(model.busy || model.isPreview)
                        }
                        Text("\(source.host) · \(source.timezone)").font(.caption).foregroundStyle(.secondary)
                        Text("Ultimo aggiornamento: " + CaptureDates.label(source.lastSuccess)).font(.caption).foregroundStyle(.secondary)
                        if let next = source.nextAttempt { Text("Prossimo tentativo: " + CaptureDates.label(next)).font(.caption).foregroundStyle(.secondary) }
                        if let summary = source.summary {
                            Text("\(summary.imported) eventi · \(summary.recognizedCourses) corsi riconosciuti · \(summary.pending) da associare · \(summary.excluded) esclusi").font(.caption).foregroundStyle(.secondary)
                            if let issues = summary.issues, !issues.isEmpty {
                                DisclosureGroup("Eventi esclusi") {
                                    ForEach(Array(issues.enumerated()), id: \.offset) { _, issue in
                                        Text("\(issue.title): \(issue.reason)").font(.caption)
                                    }
                                }
                            }
                        }
                        if source.error != nil {
                            Text(source.message ?? "Sincronizzazione non riuscita. Controlla il collegamento e riprova.")
                                .font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
                            Text(source.lastSuccess == nil ? "Nessuna lezione importata." : "Conservata la pianificazione dell’ultimo aggiornamento riuscito.")
                                .font(.caption).foregroundStyle(.secondary)
                            if source.retryable == false { Text("Aggiornamento automatico sospeso: correggi il collegamento o premi Sincronizza per riprovare.").font(.caption).fixedSize(horizontal: false, vertical: true) }
                        }
                    }
                } } }.frame(maxHeight: 210) }
                Text("Aggiornamento automatico ogni \(snapshot.refreshHours, specifier: "%g") ore quando Capture è attivo.").font(.caption).foregroundStyle(.secondary)
            }.frame(maxWidth: .infinity, alignment: .leading).padding(6)
        }
    }
    private var lessons: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack { Text("Prossime lezioni").font(.headline); Spacer(); Toggle("Mostra annullate", isOn: $showCancelled).toggleStyle(.checkbox).font(.caption) }
            TextField("Cerca corso o lezione", text: $query)
            List(selection: $selected) {
                ForEach(events) { event in
                    VStack(alignment: .leading, spacing: 5) {
                        HStack { Text(event.title).font(.headline).lineLimit(2); Spacer(); Text(event.statusLabel).font(.caption).foregroundStyle(event.eligibility == "SCHEDULED" ? Color.secondary : Color.orange) }
                        Text(event.courseLabel).font(.callout).foregroundStyle(.secondary)
                        Text(CaptureDates.label(event.startsAt, zone: event.timezone) + " → " + CaptureDates.label(event.endsAt, zone: event.timezone)).font(.caption).foregroundStyle(.secondary)
                        if event.localChanges || event.special { Text(event.special ? "Lezione speciale" : "Modifiche locali").font(.caption).foregroundStyle(.secondary) }
                    }.padding(.vertical, 5).tag(event.id)
                }
            }.overlay { if events.isEmpty { ContentUnavailableView("Nessuna lezione in programma", systemImage: "calendar", description: Text("Sincronizza un calendario o aggiungi una lezione.")) } }
        }
    }
    @ViewBuilder private func detail(_ snapshot: CaptureSnapshot) -> some View {
        if let event = selectedEvent {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Dettaglio lezione").font(.headline)
                    Text(event.title).font(.title2.bold())
                    Text(event.courseLabel).foregroundStyle(.secondary)
                    Label(event.statusLabel, systemImage: event.eligibility == "SCHEDULED" ? "checkmark.circle" : "exclamationmark.triangle")
                    Divider()
                    LabeledContent("Inizio", value: CaptureDates.label(event.startsAt, zone: event.timezone))
                    LabeledContent("Fine", value: CaptureDates.label(event.endsAt, zone: event.timezone))
                    LabeledContent("Fuso", value: event.timezone)
                    LabeledContent("Origine", value: event.special ? "Lezione speciale" : snapshot.sources.first(where: { $0.id == event.sourceID })?.label ?? "Calendario importato")
                    if !event.reason.isEmpty { Text(event.reason).font(.callout).foregroundStyle(.orange) }
                    if event.localChanges { Text("Le modifiche locali restano valide dopo la sincronizzazione.").font(.callout).foregroundStyle(.secondary) }
                    if ["CANCELLED", "NEEDS_REVIEW"].contains(event.sourceEligibility) {
                        Text("Il calendario ha annullato questa lezione o richiede una verifica. Una modifica locale non la riattiva.").font(.callout).foregroundStyle(.secondary)
                    }
                    HStack {
                        Button("Modifica") { sheet = .edit }
                        if event.localChanges { Button("Ripristina calendario") { Task { await model.perform("reset-lesson", arguments: ["--event", event.id]) } } }
                    }.disabled(model.busy || model.isPreview || !snapshot.editable(event))
                    if !snapshot.editable(event) { Text("Registrazione o salvataggio in corso: termina e salva prima di modificare questa lezione.").font(.caption).foregroundStyle(.secondary) }
                    Text("Le modifiche riguardano MyNotes; il calendario esterno rimane la fonte originale.").font(.caption).foregroundStyle(.secondary)
                }.padding(16).frame(maxWidth: .infinity, alignment: .leading)
            }
        } else { ContentUnavailableView("Seleziona una lezione", systemImage: "calendar", description: Text("Qui trovi orari, origine e impostazioni della registrazione.")) }
    }
}

private struct CaptureLessonForm: View {
    @ObservedObject var model: CaptureModel
    let snapshot: CaptureSnapshot
    let event: CaptureEvent?
    let immediate: Bool
    @Environment(\.dismiss) private var dismiss
    @CaptureViewState<String> private var course: String
    @CaptureViewState<Int> private var number: Int
    @CaptureViewState<String> private var title: String
    @CaptureViewState<Date> private var start: Date
    @CaptureViewState<Date> private var end: Date
    @CaptureViewState<String> private var timezone: String
    @CaptureViewState<Int> private var duration = 60
    @CaptureViewState<Bool> private var addingCourse = false
    @CaptureViewState<String> private var language = "default"
    @CaptureViewState<Bool> private var enabled: Bool
    init(model: CaptureModel, snapshot: CaptureSnapshot, event: CaptureEvent?, immediate: Bool) {
        self.model = model; self.snapshot = snapshot; self.event = event; self.immediate = immediate
        let code = event?.courseCode ?? snapshot.courses.first?.code ?? ""
        _course = State(initialValue: code); _number = State(initialValue: event?.lessonNumber ?? snapshot.nextNumber(code))
        _title = State(initialValue: event?.title ?? (immediate ? "" : "Lezione speciale"))
        _start = State(initialValue: event?.start ?? Date().addingTimeInterval(3600))
        _end = State(initialValue: event?.end ?? Date().addingTimeInterval(7200))
        _timezone = State(initialValue: event?.timezone ?? TimeZone.current.identifier)
        _enabled = State(initialValue: event?.recordingEnabled ?? true)
    }
    private var valid: Bool {
        (!course.isEmpty && (1...999).contains(number) || course.isEmpty && snapshot.standaloneSupported == true) && !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && (immediate ? (1...720).contains(duration) : TimeZone(identifier: timezone) != nil && end > start && end.timeIntervalSince(start) <= 43200 && (event != nil || start > Date()))
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(immediate ? "Registra ora" : event == nil ? "Aggiungi lezione speciale" : "Modifica lezione").font(.title2.bold())
            Form {
                HStack {
                    Picker("Corso", selection: $course) {
                        Text("Nessun corso").tag("").disabled(snapshot.standaloneSupported != true)
                        ForEach(model.snapshot?.courses ?? snapshot.courses) { Text($0.name).tag($0.code) }
                    }.disabled(event != nil)
                    Button("Aggiungi corso", systemImage: "plus") { addingCourse = true }.disabled(event != nil)
                }
                    .onChange(of: course) { _, value in if event == nil { number = snapshot.nextNumber(value) } }
                if !course.isEmpty { Stepper("Numero della lezione: \(number)", value: $number, in: 1...999).disabled(event != nil) }
                TextField("Titolo", text: $title)
                if event == nil {
                    DisclosureGroup("Opzioni trascrizione") {
                        Picker("Lingua", selection: $language) {
                            Text(course.isEmpty ? "Automatica" : "Usa la lingua del corso").tag("default")
                            Text("Rileva automaticamente").tag("auto")
                            Text("Italiano").tag("it"); Text("English").tag("en")
                        }
                    }
                }
                if immediate { Stepper("Durata: \(duration) minuti", value: $duration, in: 1...720) }
                else {
                    TextField("Fuso orario", text: $timezone).help("Per esempio Europe/Rome o Asia/Kolkata")
                    DatePicker("Inizio", selection: $start, displayedComponents: [.date, .hourAndMinute])
                    DatePicker("Fine", selection: $end, displayedComponents: [.date, .hourAndMinute])
                    if event != nil { Toggle("Registra questa lezione", isOn: $enabled).disabled(["CANCELLED", "NEEDS_REVIEW"].contains(event?.sourceEligibility ?? "")) }
                }
            }.environment(\.timeZone, TimeZone(identifier: timezone) ?? .current).disabled(model.busy)
            if immediate { Text("La richiesta viene inviata al registratore attivo. La registrazione è confermata soltanto quando Capture acquisisce il microfono.").font(.caption).foregroundStyle(.secondary) }
            if let error = model.error { Text(error).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true) }
            HStack {
                Button("Annulla") { dismiss() }.keyboardShortcut(.cancelAction).disabled(model.busy)
                Spacer()
                if model.busy { ProgressView().controlSize(.small) }
                Button(immediate ? "Avvia registrazione" : "Salva") { Task { await save() } }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(!valid || model.busy)
            }
        }.padding(24).frame(width: 520).disabled(model.isPreview)
        .sheet(isPresented: $addingCourse) { CaptureCourseForm(model: model) { course = model.createdCourse ?? course; number = model.snapshot?.nextNumber(course) ?? 1 } }
    }
    private func save() async {
        let success: Bool
        if immediate {
            var args = ["--title", title.trimmingCharacters(in: .whitespacesAndNewlines), "--duration-minutes", String(duration), "--wait-seconds", "10"]
            args += course.isEmpty ? ["--unassigned"] : ["--course", course, "--lesson", String(number)]
            if language != "default" { args += ["--language", language] }
            success = await model.perform("start", arguments: args)
        }
        else {
            var payload: [String: Any] = ["title": title.trimmingCharacters(in: .whitespacesAndNewlines), "startsAt": CaptureDates.write(start), "endsAt": CaptureDates.write(end), "timezone": timezone]
            if event == nil && language != "default" { payload["transcriptionLanguage"] = language }
            if let event { payload["recordingEnabled"] = enabled; success = await model.perform("edit-lesson", arguments: ["--event", event.id], payload: payload) }
            else {
                if course.isEmpty { payload["unassigned"] = true }
                else { payload["courseCode"] = course; payload["lessonNumber"] = number }
                success = await model.perform("add-lesson", payload: payload)
            }
        }
        if success { dismiss() }
    }
}

private struct CaptureMappingDraft: Identifiable {
    let id = UUID()
    var match = ""
    var courseCode: String
    var firstLesson = 1
}
private struct CaptureCalendarForm: View {
    @ObservedObject var model: CaptureModel
    let snapshot: CaptureSnapshot
    let repairing: String?
    @Environment(\.dismiss) private var dismiss
    @CaptureViewState<Bool> private var loaded = false
    @CaptureViewState<String> private var mode = "all_timed"
    @CaptureViewState<String> private var label = "Calendario lezioni"
    @CaptureViewState<String> private var url = ""
    @CaptureViewState<String> private var timezone = TimeZone.current.identifier
    @CaptureViewState<Date> private var start = Date()
    @CaptureViewState<Date> private var end = Date().addingTimeInterval(120 * 86400)
    @CaptureViewState<[CaptureMappingDraft]> private var mappings: [CaptureMappingDraft]
    @CaptureViewState<String> private var sourceID = UUID().uuidString
    @CaptureViewState<Bool> private var advanced = false
    init(model: CaptureModel, snapshot: CaptureSnapshot, repairing: String? = nil) {
        self.model = model; self.snapshot = snapshot; self.repairing = repairing
        _mappings = State(initialValue: [])
    }
    private var valid: Bool {
        loaded && !label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !url.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && periodIsValid
            && mappings.allSatisfy { !$0.match.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !$0.courseCode.isEmpty && (1...999).contains($0.firstLesson) }
    }
    private var periodIsValid: Bool {
        guard let (first, last) = CaptureDates.calendarPeriod(start, end, zone: timezone) else { return false }
        return end >= start && last > first && last.timeIntervalSince(first) <= 550 * 86400
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(repairing == nil ? "Collega calendario" : "Correggi collegamento").font(.title2.bold())
            ScrollView {
            VStack(alignment: .leading, spacing: 16) {
            Form {
                TextField("Nome", text: $label)
                SecureField("Link Google Calendar, HTTPS o webcal/iCal", text: $url)
                TextField("Fuso orario", text: $timezone)
                DatePicker("Da", selection: $start, displayedComponents: [.date])
                DatePicker("Fino a", selection: $end, displayedComponents: [.date])
            }.environment(\.timeZone, TimeZone(identifier: timezone) ?? .current).disabled(model.busy)
            Text("Importa tutti gli eventi con orario. I corsi riconosciuti vengono associati automaticamente; potrai organizzare gli altri nella chat.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            DisclosureGroup("Associazioni avanzate", isExpanded: $advanced) {
            ScrollView {
                VStack(spacing: 12) {
                    ForEach($mappings) { $mapping in
                        VStack(alignment: .leading, spacing: 8) {
                            TextField("Il titolo contiene…", text: $mapping.match)
                            HStack {
                                Picker("Corso", selection: $mapping.courseCode) { ForEach(snapshot.courses) { Text($0.name).tag($0.code) } }
                                if mode == "mapped" { Stepper("Prima lezione: \(mapping.firstLesson)", value: $mapping.firstLesson, in: 1...999) }
                                else { Text("Numero dal titolo, se presente").font(.caption).foregroundStyle(.secondary) }
                                Button("Rimuovi", systemImage: "minus.circle") { mappings.removeAll { $0.id == mapping.id } }
                            }
                        }
                    }
                }
            }.frame(maxHeight: 200).disabled(model.busy)
            Button("Aggiungi associazione", systemImage: "plus") { mappings.append(CaptureMappingDraft(courseCode: snapshot.courses.first?.code ?? "")) }.disabled(model.busy || snapshot.courses.isEmpty)
            }
            Text("Puoi incollare il link Google Calendar oppure un link iCal HTTPS/webcal. Il collegamento resta privato. Se il calendario Google non è pubblico, usa l’indirizzo iCal privato dalle sue impostazioni.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if model.busy, let notice = model.notice { Text(notice).font(.callout).fixedSize(horizontal: false, vertical: true) }
            if let error = model.error { Text(error).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true) }
            }
            }.frame(maxHeight: 580)
            HStack {
                Button("Annulla") { dismiss() }.keyboardShortcut(.cancelAction).disabled(model.busy); Spacer()
                if model.busy { ProgressView().controlSize(.small) }
                Button(repairing == nil ? "Collega e sincronizza" : "Verifica e salva") { Task { await save() } }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(!valid || model.busy)
            }
        }.padding(24).frame(width: 700).disabled(model.isPreview)
        .task {
            guard !loaded else { return }
            if let repairing {
                guard let source = await model.calendarSource(repairing) else { return }
                sourceID = source.sourceID; label = source.label; url = source.url; timezone = source.timezone
                start = CaptureDates.read(source.periodStart) ?? start
                end = (CaptureDates.read(source.periodEnd) ?? end).addingTimeInterval(-1)
                mode = source.importMode ?? "mapped"
                mappings = source.mappings.map { CaptureMappingDraft(match: $0.match, courseCode: $0.courseCode, firstLesson: $0.firstLesson ?? 1) }
            }
            loaded = true
        }
    }
    private func save() async {
        guard let (first, last) = CaptureDates.calendarPeriod(start, end, zone: timezone) else { return }
        let payload: [String: Any] = ["sourceID": sourceID, "label": label.trimmingCharacters(in: .whitespacesAndNewlines), "url": url.trimmingCharacters(in: .whitespacesAndNewlines), "timezone": timezone,
            "periodStart": CaptureDates.write(first), "periodEnd": CaptureDates.write(last), "importMode": mode,
            "mappings": mappings.map { mapping -> [String: Any] in
                var result: [String: Any] = ["match": mapping.match.trimmingCharacters(in: .whitespacesAndNewlines), "courseCode": mapping.courseCode]
                if mode == "mapped" { result["firstLesson"] = mapping.firstLesson }
                return result
            }]
        if await model.perform("connect", payload: payload) { dismiss() }
    }
}
