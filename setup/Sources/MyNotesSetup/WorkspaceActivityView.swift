import SwiftUI

struct WorkspaceActivityView: View {
    @ObservedObject var model: CaptureModel
    @Binding var course: WorkspaceActivityCourse
    var openLesson: (String) -> Void
    @CaptureViewState<Bool> private var showActive = true
    @CaptureViewState<Bool> private var showReady = true
    @CaptureViewState<Bool> private var showReview = true
    @CaptureViewState<Bool> private var showHistory = false

    private var controlsDisabled: Bool { model.busy || model.queueBusy || model.isPreview }
    private var courses: [CaptureCourse] { model.snapshot?.courses ?? [] }

    var body: some View {
        let activity = WorkspaceActivity(lessons: model.lessons, queue: model.queue, course: course)
        VStack(alignment: .leading, spacing: 0) {
            header(activity)
            Divider()
            List {
                section("In lavorazione", entries: activity.active, expanded: $showActive)
                section("Da trascrivere", entries: activity.ready, expanded: $showReady)
                section("Da verificare", entries: activity.review, expanded: $showReview)
                if activity.count == 0 { emptyState }
                if !activity.history.isEmpty {
                    DisclosureGroup(isExpanded: $showHistory) {
                        ForEach(activity.history) { job in historyRow(job) }
                    } label: {
                        sectionLabel("Cronologia", count: activity.history.count)
                    }
                }
            }
            .listStyle(.inset)
        }
    }

    private func header(_ activity: WorkspaceActivity) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text("Attività").font(.largeTitle.weight(.semibold))
                    .accessibilityAddTraits(.isHeader)
                Text(activity.count == 1 ? "1 lezione da elaborare" : "\(activity.count) lezioni da elaborare")
                    .foregroundStyle(.secondary)
            }
            HStack(spacing: 16) {
                Picker("Corso", selection: $course) {
                    Text("Tutti i corsi").tag(WorkspaceActivityCourse.all)
                    ForEach(courses) { item in
                        Text(item.name).tag(WorkspaceActivityCourse.course(item.code))
                    }
                    if activity.hasUnassigned || course == .unassigned {
                        Text("Da associare").tag(WorkspaceActivityCourse.unassigned)
                    }
                    if case .course(let code) = course, !courses.contains(where: { $0.code == code }) {
                        Text(code).tag(course)
                    }
                }.pickerStyle(.menu)
                    .frame(maxWidth: 350, alignment: .leading)
                Spacer(minLength: 8)
                queueControls
            }
            if let queue = model.queue {
                if queue.paused {
                    Label("Coda in pausa per tutti i corsi", systemImage: "pause.circle")
                        .font(.callout).foregroundStyle(.secondary)
                } else if let reason = queue.waitReason {
                    Label(reason, systemImage: "pause.circle").font(.callout).foregroundStyle(.secondary)
                }
            }
        }
        .padding(24)
    }

    private var queueControls: some View {
        HStack(spacing: 8) {
            let paused = model.queue?.paused == true
            Button(paused ? "Riprendi coda" : "Pausa coda") {
                Task { await model.queueAction(paused ? "resume" : "pause") }
            }.help("Agisce sulla coda di tutti i corsi")
            Menu("Gestisci coda") {
                Button("Svuota lavori in attesa di tutti i corsi") {
                    Task { await model.queueAction("clear") }
                }.disabled(model.queue?.activeJobs.contains { $0.status == "QUEUED" } != true)
            }.fixedSize()
        }.disabled(controlsDisabled || model.queue == nil)
    }

    @ViewBuilder
    private func section(_ title: String, entries: [WorkspaceActivityEntry], expanded: Binding<Bool>) -> some View {
        if !entries.isEmpty {
            DisclosureGroup(isExpanded: expanded) {
                ForEach(entries) { entry in
                    lessonRow(entry)
                }
            } label: {
                sectionLabel(title, count: entries.count)
            }
        }
    }

    private func sectionLabel(_ title: String, count: Int) -> some View {
        HStack(spacing: 8) {
            Text(title).font(.headline)
            Text(count, format: .number)
                .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
        }
        .padding(.vertical, 6)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(title), \(count)")
        .accessibilityAddTraits(.isHeader)
    }

    private func lessonRow(_ entry: WorkspaceActivityEntry) -> some View {
        HStack(alignment: .top, spacing: 16) {
            Button { openLesson(entry.id) } label: {
                VStack(alignment: .leading, spacing: 4) {
                    Text(entry.lesson.title).fontWeight(.medium).foregroundStyle(.primary).lineLimit(2)
                    let name = courseName(entry.lesson.courseCode)
                    let date = entry.lesson.date
                    Text(date == .distantPast ? name : name + " · " + date.formatted(date: .abbreviated, time: .omitted))
                        .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
                .contentShape(Rectangle())
                .accessibilityElement(children: .combine)
            }.buttonStyle(.plain).help("Apri il dettaglio della lezione")
            Spacer(minLength: 8)
            switch entry.state {
            case .active(let status, let job):
                activeStatus(status, job: job)
                if let job {
                    FlowActions(actions: job.actions ?? []) { action in
                        Task { await model.queueAction(action, arguments: ["--job", job.id]) }
                    }.disabled(controlsDisabled)
                }
            case .ready(let recording):
                Button("Trascrivi") {
                    Task { await model.recordingAction("enqueue", item: recording) }
                }.buttonStyle(.bordered).disabled(controlsDisabled)
                    .accessibilityLabel("Trascrivi \(entry.lesson.title)")
            case .review(let reason):
                Text(reason).font(.callout).foregroundStyle(.secondary)
                    .multilineTextAlignment(.trailing).frame(maxWidth: 240, alignment: .trailing)
                Button("Apri lezione") { openLesson(entry.id) }
                    .accessibilityLabel("Apri lezione \(entry.lesson.title)")
            }
        }
        .padding(.vertical, 8)
    }

    private func activeStatus(_ status: String, job: CaptureQueueJob?) -> some View {
        VStack(alignment: .trailing, spacing: 4) {
            HStack(spacing: 6) {
                if status == "RUNNING" { ProgressView().controlSize(.small).accessibilityHidden(true) }
                Text(status == "RUNNING" && job?.stateFresh == true
                     ? capturePhaseLabel(job?.phase ?? "accepted") : captureStateLabel(status)).font(.callout)
            }
            if status == "RUNNING", let job, job.elapsedSeconds != nil {
                CaptureElapsedView(job: job).id(job.attemptID ?? job.id)
            }
            if status == "QUEUED", let position = job?.queuePosition {
                Text("Posizione \(position)").font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func historyRow(_ job: CaptureQueueJob) -> some View {
        HStack(spacing: 16) {
            Text(job.title).lineLimit(2)
            Spacer()
            Text(captureStateLabel(job.status)).font(.callout).foregroundStyle(.secondary)
            if job.status == "COMPLETED", let path = job.exportText ?? job.outputs?["transcript_text"] {
                Button("Apri trascrizione") { model.openFile(path) }
            }
        }.padding(.vertical, 4)
    }

    private var emptyState: some View {
        ContentUnavailableView {
            Label(course == .all ? "Nessuna lezione da elaborare" : "Nessuna lezione da elaborare per questo corso",
                  systemImage: "checkmark.circle")
        } actions: {
            if course != .all { Button("Mostra tutti i corsi") { course = .all } }
        }.frame(maxWidth: .infinity)
    }

    private func courseName(_ code: String?) -> String {
        guard let code else { return "Da associare" }
        return courses.first(where: { $0.code == code })?.name ?? code
    }
}
