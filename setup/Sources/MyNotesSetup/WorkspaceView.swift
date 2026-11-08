import AppKit
import SwiftUI
import Combine
import UniformTypeIdentifiers

@MainActor final class WorkspaceState: ObservableObject {
    let capture = CaptureModel()
    let setup = SetupModel()
    @Published var route: WorkspaceRoute = .today
}

enum WorkspaceStyle {
    static let background = Color(nsColor: .textBackgroundColor)
    static let sidebar = Color(nsColor: .windowBackgroundColor)
    static let subtle = Color.primary.opacity(0.045)
    static let space: CGFloat = 8
    static let radius: CGFloat = 10
}

private enum WorkspaceSheet: Identifiable {
    case record, schedule, edit(CaptureEvent), detail(String), importAudio([URL], String?)
    var id: String {
        switch self {
        case .record: return "record"; case .schedule: return "schedule"
        case .edit(let event): return "edit:" + event.id
        case .detail(let id): return "detail:" + id
        case .importAudio: return "import"
        }
    }
}

struct MyNotesWorkspaceView: View {
    @ObservedObject var state: WorkspaceState
    @ObservedObject var model: CaptureModel
    @ObservedObject var setup: SetupModel
    @CaptureViewState<WorkspaceSheet?> private var sheet: WorkspaceSheet?
    @CaptureViewState<Bool> private var showImporter = false
    @CaptureViewState<String?> private var importEventID: String?
    @CaptureViewState<String?> private var selectedLessonID: String?
    @CaptureViewState<String> private var query = ""
    @CaptureViewState<String> private var course = "all"
    @CaptureViewState<Bool> private var attentionOnly = false
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.openSettings) private var openSettings
    private let poll = Timer.publish(every: 5, on: .main, in: .common).autoconnect()
    private var now: Date { ProcessInfo.processInfo.environment["MYNOTES_PREVIEW_DATE"].flatMap(CaptureDates.read) ?? Date() }
    private var lessons: [WorkspaceLesson] { WorkspaceLesson.build(snapshot: model.snapshot, audio: model.recordings, transcripts: model.transcripts) }
    private var clock: WorkspaceCalendar { WorkspaceCalendar(timezone: model.snapshot?.events.first?.timezone ?? TimeZone.current.identifier) }
    private var collection: [WorkspaceLesson] {
        lessons.filter {
            ($0.hasContent || ($0.event?.end ?? .distantFuture) < now)
            && (course == "all" || (course == "none" ? $0.isUnassigned : $0.courseCode == course))
            && (!attentionOnly || $0.needsAttention)
            && (query.isEmpty || ($0.title + " " + ($0.courseCode ?? "") + " " + courseName($0.courseCode)).localizedStandardContains(query))
        }
    }
    var body: some View {
        NavigationSplitView {
            VStack(spacing: 0) {
                HStack(spacing: 10) {
                    Image(systemName: "book.closed.fill").foregroundStyle(.tint)
                    Text("MyNotes").font(.headline)
                    Spacer()
                }.padding(.horizontal, 20).padding(.vertical, 24)
                List(selection: $state.route) {
                    ForEach(WorkspaceRoute.allCases) { route in
                        HStack {
                            Label(route.title, systemImage: route.symbol)
                            Spacer()
                            if route == .activity, let count = model.queue?.activeJobs.count, count > 0 {
                                Text(count, format: .number).font(.caption).foregroundStyle(.secondary)
                            }
                        }.tag(route).padding(.vertical, 5)
                    }
                }.listStyle(.sidebar)
                VStack(spacing: 8) {
                    Button { setup.openChat() } label: { Label("Apri chat esterna", systemImage: "arrow.up.right.square").frame(maxWidth: .infinity, alignment: .leading) }
                        .buttonStyle(.plain).disabled(model.isPreview || !setup.prepared || !setup.providerFound)
                        .help("Apre l’agente selezionato. Gestisci agente e strumenti nelle Impostazioni.")
                    Button { openSettings() } label: { Label("Impostazioni", systemImage: "gearshape").frame(maxWidth: .infinity, alignment: .leading) }
                        .buttonStyle(.plain).keyboardShortcut(",", modifiers: .command)
                }.font(.callout).padding(20)
            }.background(WorkspaceStyle.sidebar).navigationSplitViewColumnWidth(min: 176, ideal: 192, max: 232)
        } detail: {
            VStack(spacing: 0) {
                if model.snapshot?.isRecording == true || model.snapshot?.isExporting == true {
                    WorkspaceRecordingBar(model: model)
                    Divider()
                }
                if model.error != nil || model.refreshWarning != nil || model.recorderRecoveryBlocked {
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: "exclamationmark.circle").foregroundStyle(.orange)
                        Text(model.recorderRecoveryBlocked ? "Capture riprenderà quando chiudi Digiexam e torni a MyNotes." : model.error ?? model.refreshWarning ?? "")
                            .font(.callout).textSelection(.enabled)
                        Spacer()
                        Button("Riprova") { Task { await refresh(recover: true) } }.controlSize(.small)
                        if model.error != nil { Button { model.error = nil } label: { Image(systemName: "xmark") }.buttonStyle(.plain).accessibilityLabel("Chiudi messaggio") }
                    }.padding(16).background(WorkspaceStyle.subtle)
                    Divider()
                }
                if model.snapshot == nil {
                    ContentUnavailableView("Prepariamo il tuo spazio", systemImage: "book.closed", description: Text("Leggiamo calendario e raccolta. Se serve, completa la configurazione dalle Impostazioni."))
                } else {
                    page.frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                }
            }.background(WorkspaceStyle.background)
            .navigationTitle(state.route.title)
            .toolbar { workspaceToolbar }
        }
        .overlay(alignment: .bottomTrailing) {
            if let notice = model.notice {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "checkmark.circle").foregroundStyle(.secondary)
                    Text(notice).font(.callout).fixedSize(horizontal: false, vertical: true)
                    Button { model.notice = nil } label: { Image(systemName: "xmark") }.buttonStyle(.plain).accessibilityLabel("Chiudi conferma")
                }.padding(16).frame(maxWidth: 440, alignment: .leading)
                    .background(WorkspaceStyle.sidebar, in: RoundedRectangle(cornerRadius: 10)).padding(20)
            }
        }
        .task(id: model.notice) {
            guard let notice = model.notice else { return }
            try? await Task.sleep(for: .seconds(10))
            if !Task.isCancelled && model.notice == notice { model.notice = nil }
        }
        .onChange(of: model.importedRecordingID) { _, id in
            if let id, let lesson = lessons.first(where: { $0.audio.contains(where: { $0.id == id }) }) {
                selectedLessonID = lesson.id; query = ""; course = "all"; attentionOnly = false; state.route = .library
            }
        }
        .frame(minWidth: 900, minHeight: 620)
        .background(CaptureWindowLease().frame(width: 0, height: 0))
        .task {
            if ProcessInfo.processInfo.environment["MYNOTES_INITIAL_SECTION"] == "capture" { state.route = .calendar }
            if let route = ProcessInfo.processInfo.environment["MYNOTES_INITIAL_ROUTE"].flatMap(WorkspaceRoute.init(rawValue:)) { state.route = route }
            await refresh(recover: true)
        }
        .onChange(of: scenePhase) { _, phase in if phase == .active && sheet == nil { Task { await refresh(recover: true) } } }
        .onReceive(poll) { date in
            guard scenePhase == .active, sheet == nil, model.claimPoll(at: date) else { return }
            Task { await model.refresh(silent: true, includeLibrary: true) }
        }
        .fileImporter(isPresented: $showImporter, allowedContentTypes: [.audio], allowsMultipleSelection: true) { result in
            switch result {
            case .success(let urls): sheet = .importAudio(urls, importEventID)
            case .failure(let error): model.error = error.localizedDescription
            }
        }
        .dropDestination(for: URL.self) { urls, _ in
            guard !urls.isEmpty, !model.busy, !model.queueBusy else { return false }
            sheet = .importAudio(urls, nil); return true
        }
        .sheet(item: $sheet, onDismiss: { Task { await model.refresh(silent: true, includeLibrary: true) } }) { kind in
            if let snapshot = model.snapshot {
                switch kind {
                case .record: CaptureLessonForm(model: model, snapshot: snapshot, event: nil, immediate: true)
                case .schedule: CaptureLessonForm(model: model, snapshot: snapshot, event: nil, immediate: false)
                case .edit(let event): CaptureLessonForm(model: model, snapshot: snapshot, event: event, immediate: false)
                case .importAudio(let urls, let eventID): WorkspaceAudioImportView(model: model, urls: urls, initialEventID: eventID)
                case .detail(let id):
                    NavigationStack {
                        if let lesson = lessons.first(where: { $0.id == id }) {
                            WorkspaceLessonDetail(model: model, lesson: lesson, courseName: courseName(lesson.courseCode))
                        } else { ContentUnavailableView("Lezione non disponibile", systemImage: "doc.questionmark") }
                    }.toolbar { ToolbarItem(placement: .confirmationAction) { Button("Fine") { sheet = nil } } }
                        .frame(minWidth: 560, idealWidth: 660, minHeight: 580, idealHeight: 680)
                }
            }
        }
    }
    @ToolbarContentBuilder private var workspaceToolbar: some ToolbarContent {
                ToolbarItemGroup(placement: .primaryAction) {
                    Menu {
                        Button("Importa audio…", systemImage: "square.and.arrow.down") { importAudio() }
                        Button("Programma lezione…", systemImage: "calendar.badge.plus") { sheet = .schedule }
                    } label: { Label("Aggiungi", systemImage: "plus").labelStyle(.titleAndIcon) }
                    .disabled(model.snapshot == nil || model.busy || model.queueBusy)
                    if model.snapshot?.isRecording != true && model.snapshot?.isExporting != true {
                        Button { sheet = .record } label: { Label("Registra", systemImage: "record.circle").labelStyle(.titleAndIcon) }
                            .disabled(model.recorderRecoveryBlocked || model.snapshot?.canStartManualRecording != true || model.busy || model.queueBusy)
                    }
                }
    }
    @ViewBuilder private var page: some View {
        switch state.route {
        case .today: today
        case .calendar:
            WorkspaceCalendarView(model: model, lessons: lessons, now: now) { sheet = .detail($0) }
        case .library: library
        case .activity:
            CaptureRecordingsView(model: model, queueVisible: true, activityOnly: true).padding(24)
        }
    }
    private var today: some View {
        let dayLessons = lessons.filter { clock.sameDay($0.date, now) }.sorted { $0.date < $1.date }
        let next = lessons.filter { ($0.event?.end ?? .distantPast) > now && $0.event?.eligibility == "SCHEDULED" }.min { $0.date < $1.date }
        let problems = lessons.filter(\.needsAttention).filter { $0.hasContent || $0.event?.eligibility != "SCHEDULED" }
        return ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(clock.label(now, format: "EEEE d MMMM").capitalized).font(.largeTitle.weight(.semibold))
                    Text("Il tuo spazio per lezioni, audio e trascrizioni.").foregroundStyle(.secondary)
                }
                VStack(alignment: .leading, spacing: 12) {
                    Text("Le lezioni di oggi").font(.headline)
                    if dayLessons.isEmpty {
                        VStack(alignment: .leading, spacing: 16) {
                            Text("Oggi non ci sono lezioni in calendario.").foregroundStyle(.secondary)
                            if let next {
                                Text("La prossima è " + next.title + " · " + CaptureDates.label(next.event?.startsAt, zone: next.timezone)).font(.callout)
                                Button("Vedi lezione") { sheet = .detail(next.id) }
                            } else { Button("Importa un audio") { importAudio() } }
                        }.frame(maxWidth: .infinity, alignment: .leading).padding(20).background(WorkspaceStyle.subtle, in: RoundedRectangle(cornerRadius: 10))
                    } else {
                        ForEach(dayLessons) { lesson in
                            Button { sheet = .detail(lesson.id) } label: { WorkspaceLessonRow(lesson: lesson, subtitle: courseName(lesson.courseCode), snapshot: model.snapshot) }.buttonStyle(.plain)
                            Divider()
                        }
                    }
                }
                if !problems.isEmpty {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack { Text("Da sistemare").font(.headline); Spacer(); Button("Vedi tutti") { attentionOnly = true; state.route = .library }.buttonStyle(.plain).foregroundStyle(.tint) }
                        ForEach(Array(problems.prefix(4))) { lesson in
                            Button { sheet = .detail(lesson.id) } label: {
                                HStack(spacing: 12) {
                                    Image(systemName: "exclamationmark.circle").foregroundStyle(.orange)
                                    Text(lesson.title).lineLimit(1)
                                    Spacer()
                                    Text(lesson.needsChoice ? "Scegli audio" : lesson.isUnassigned ? "Da associare" : "Da verificare").font(.callout).foregroundStyle(.secondary)
                                    Image(systemName: "chevron.right").foregroundStyle(.tertiary)
                                }.padding(.vertical, 10).contentShape(Rectangle())
                            }.buttonStyle(.plain)
                        }
                    }
                }
                if let snapshot = model.snapshot, !snapshot.enabled || snapshot.paused || snapshot.running == false {
                    HStack(spacing: 12) {
                        Image(systemName: "mic.slash").foregroundStyle(.secondary)
                        VStack(alignment: .leading, spacing: 4) { Text(snapshot.stateLabel).font(.callout.weight(.medium)); Text(snapshot.paused ? "Il calendario resta in pausa finché non lo riprendi." : "Gestisci registrazione e calendario nelle Impostazioni.").font(.caption).foregroundStyle(.secondary) }
                        Spacer(); Button("Impostazioni") { openSettings() }
                    }.padding(16).background(WorkspaceStyle.subtle, in: RoundedRectangle(cornerRadius: 10))
                }
            }.padding(32).frame(maxWidth: 940, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
    private var library: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("Cerca una lezione o un corso", text: $query).textFieldStyle(.plain)
                Picker("Corso", selection: $course) {
                    Text("Tutti i corsi").tag("all"); Text("Da associare").tag("none")
                    ForEach(model.snapshot?.courses ?? []) { Text($0.name).tag($0.code) }
                }.labelsHidden().frame(maxWidth: 210)
                Menu {
                    Toggle("Solo da sistemare", isOn: $attentionOnly)
                    Button("Trascrivi audio selezionati per le lezioni") { Task { await model.queueAction("enqueue", arguments: ["--all-untranscribed"]) } }
                        .disabled(model.queueBusy || model.busy || model.isPreview)
                    Button("Aggiorna raccolta") { Task { await model.refresh(includeLibrary: true) } }
                    Button("Verifica file della raccolta") { Task { await model.queueAction("verify-library") } }
                        .disabled(model.queueBusy || model.busy || model.isPreview)
                } label: { Image(systemName: "line.3.horizontal.decrease") }.menuStyle(.borderlessButton).fixedSize().accessibilityLabel("Filtra e gestisci raccolta")
            }.padding(20)
            Divider()
            GeometryReader { container in
                if container.size.width >= 740 {
                HSplitView {
                    lessonList.frame(minWidth: 280, idealWidth: 310, maxWidth: 370)
                    if let lesson = lessons.first(where: { $0.id == selectedLessonID }) {
                        WorkspaceLessonDetail(model: model, lesson: lesson, courseName: courseName(lesson.courseCode)).frame(minWidth: 410)
                    } else { ContentUnavailableView("Tutto parte da una lezione", systemImage: "waveform", description: Text("Scegli una lezione per ascoltare l’audio e leggere la trascrizione.")).frame(minWidth: 410) }
                }.frame(width: container.size.width, height: container.size.height)
                } else {
                VStack(spacing: 0) {
                    if let lesson = lessons.first(where: { $0.id == selectedLessonID }) {
                        HStack {
                            Button { selectedLessonID = nil } label: { Label("Raccolta", systemImage: "chevron.left") }
                                .buttonStyle(.plain).foregroundStyle(.tint).keyboardShortcut("[", modifiers: .command)
                            Spacer()
                        }.padding(.horizontal, 20).padding(.vertical, 12)
                        Divider()
                        WorkspaceLessonDetail(model: model, lesson: lesson, courseName: courseName(lesson.courseCode))
                    } else {
                        List(collection) { lesson in
                            Button { selectedLessonID = lesson.id } label: {
                                WorkspaceLessonRow(lesson: lesson, subtitle: courseName(lesson.courseCode), snapshot: model.snapshot)
                            }.buttonStyle(.plain)
                        }.listStyle(.plain).overlay { if collection.isEmpty { emptyCollection } }
                    }
                }

                }
            }
        }
    }
    private var lessonList: some View {
        List(selection: $selectedLessonID) {
            ForEach(collection) { lesson in
                WorkspaceLessonRow(lesson: lesson, subtitle: courseName(lesson.courseCode), snapshot: model.snapshot).tag(lesson.id)
            }
        }.listStyle(.plain).overlay { if collection.isEmpty { emptyCollection } }
    }
    private var emptyCollection: some View { ContentUnavailableView("Nessun risultato", systemImage: "magnifyingglass", description: Text("Cambia i filtri o importa un audio dal menu Aggiungi.")) }
    private func courseName(_ code: String?) -> String { model.snapshot?.courses.first { $0.code == code }?.name ?? "Senza corso" }
    private func importAudio() { importEventID = nil; showImporter = true }
    private func refresh(recover: Bool) async {
        await model.refresh(silent: model.snapshot != nil, includeLibrary: true)
        if recover { await model.restoreRecorderIfNeeded(retry: true) }
    }
}

struct WorkspaceLessonRow: View {
    let lesson: WorkspaceLesson
    let subtitle: String
    let snapshot: CaptureSnapshot?
    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: lesson.hasContent ? "waveform" : "calendar").foregroundStyle(.secondary).frame(width: 20).padding(.top, 2)
            VStack(alignment: .leading, spacing: 6) {
                Text(lesson.title).font(.body.weight(.medium)).lineLimit(2)
                Text(subtitle).font(.callout).foregroundStyle(.secondary).lineLimit(1)
                HStack(spacing: 8) {
                    Text(CaptureDates.label(CaptureDates.write(lesson.date), zone: lesson.timezone)).lineLimit(1)
                    if lesson.hasContent { Text("· " + lesson.transcriptionLabel).lineLimit(1) }
                }.font(.caption).foregroundStyle(.secondary)
                if let event = lesson.event, !lesson.hasContent { Text(snapshot?.eventStatusLabel(event) ?? event.statusLabel).font(.caption).foregroundStyle(lesson.needsAttention ? Color.orange : Color.secondary) }
            }
            Spacer(minLength: 0)
        }.padding(.vertical, 12).contentShape(Rectangle()).accessibilityElement(children: .combine)
    }
}

struct WorkspaceRecordingBar: View {
    @ObservedObject var model: CaptureModel
    var body: some View {
        HStack(spacing: 12) {
            Circle().fill(model.snapshot?.isRecording == true ? Color.red : Color.orange).frame(width: 8, height: 8)
            Text(model.snapshot?.isRecording == true ? "Registrazione in corso" : "Salvataggio audio").font(.callout.weight(.medium))
            if let session = model.snapshot?.runtime.currentSession {
                Text(session.event.courseLabel).font(.callout).foregroundStyle(.secondary)
                if let start = CaptureDates.read(session.actualStart) {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        Text(Duration.seconds(max(0, context.date.timeIntervalSince(start))).formatted(.time(pattern: .hourMinuteSecond))).monospacedDigit().font(.callout)
                    }
                }
            }
            Spacer()
            if model.snapshot?.isRecording == true { Button("Stop e salva") { Task { await model.stopNow() } }.disabled(model.stopping || model.isPreview) }
        }.padding(.horizontal, 24).padding(.vertical, 12).background(WorkspaceStyle.subtle)
    }
}
