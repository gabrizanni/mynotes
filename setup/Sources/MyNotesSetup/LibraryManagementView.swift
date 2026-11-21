import SwiftUI

struct LibraryManagementView: View {
    @ObservedObject var model: CaptureModel
    let openProfile: (String) -> Void
    private let fixedCourse: String?
    @CaptureViewState private var course: String
    @CaptureViewState private var edited: LibraryLesson?
    @CaptureViewState private var showShift = false
    @CaptureViewState private var addCourse = false
    @CaptureViewState private var includeRemoved = false
    @Environment(\.dismiss) private var dismiss
    init(model: CaptureModel, course: String?, openProfile: @escaping (String) -> Void) {
        self.model = model
        self.openProfile = openProfile
        fixedCourse = course
        _course = CaptureViewState<String>(initialValue: course ?? "")
    }
    private var rows: [LibraryLesson] {
        model.libraryLessons.filter { $0.course == course && (includeRemoved || !$0.hidden) }.sorted {
            if $0.number == $1.number { return $0.id < $1.id }
            return ($0.number ?? 1000) < ($1.number ?? 1000)
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text(fixedCourse == nil ? "Elenco corsi" : "Organizza lezioni").font(.title2.weight(.semibold))
                Spacer()
                if fixedCourse == nil { Button("Aggiungi corso…") { addCourse = true } }
                Button("Fine") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            if let fixedCourse {
                Text(model.snapshot?.courses.first { $0.code == fixedCourse }?.name ?? fixedCourse)
                    .foregroundStyle(.secondary)
            } else {
                HStack {
                    Picker("Corso", selection: $course) {
                        Text("Scegli un corso").tag("")
                        ForEach(model.snapshot?.courses ?? []) { Text($0.name).tag($0.code) }
                    }
                    Button("Apri profilo del corso") { openProfile(course) }.disabled(course.isEmpty)
                }
            }
            if !course.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Toggle("Mostra lezioni rimosse", isOn: $includeRemoved)
                            Spacer()
                            Button("Sposta numerazione…") { showShift = true }.disabled(rows.isEmpty || model.queueBusy)
                        }
                        List(rows) { row in
                            HStack {
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(row.number.map { "Lezione \($0) · \(row.title)" } ?? row.title)
                                    Text(row.hidden ? "Rimossa dall’app" : row.notRecorded ? "Non registrata" : row.revisionRequired ? "Numerazione da verificare" : "\(row.recordingIDs.count) audio").font(.caption).foregroundStyle(.secondary)
                                    if !(row.pendingTrash ?? []).isEmpty { Text("Eliminazione da completare").font(.caption).foregroundStyle(.orange) }
                                }
                                Spacer()
                                if row.hidden {
                                    Button("Ripristina") { Task { await model.queueAction("lesson-edit", payload: ["id": row.id, "action": "restore"]) } }
                                    if !(row.pendingTrash ?? []).isEmpty {
                                        Button("Riprendi eliminazione…") { edited = row }
                                    }
                                } else { Button("Gestisci…") { edited = row } }
                            }.padding(.vertical, 4)
                        }.overlay { if rows.isEmpty { ContentUnavailableView("Nessuna lezione", systemImage: "books.vertical") } }
                }
            } else { ContentUnavailableView("Scegli un corso", systemImage: "books.vertical", description: Text("Consulta le lezioni e il profilo del corso.")) }
            if let error = model.error { Text(error).font(.callout).foregroundStyle(.red).textSelection(.enabled) }
        }.padding(24).frame(minWidth: 680, minHeight: 520)
            .sheet(isPresented: $addCourse) {
                CaptureCourseForm(model: model) {
                    if let created = model.createdCourse { course = created }
                }
            }
            .sheet(item: $edited) { row in LibraryLessonEditor(model: model, lessonID: row.id) }
            .sheet(isPresented: $showShift) { LibraryNumberShift(model: model, course: course) }
    }
}

struct LibraryLessonEditor: View {
    @ObservedObject var model: CaptureModel
    let lessonID: String
    @Environment(\.dismiss) private var dismiss
    @CaptureViewState private var number = 1
    @CaptureViewState private var files: [String]?
    @CaptureViewState private var renumberPreview = false
    @CaptureViewState private var moveRecording = ""
    @CaptureViewState private var target = ""
    private var row: LibraryLesson? { model.libraryLessons.first { $0.id == lessonID } }
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Gestisci lezione").font(.title2.weight(.semibold))
            if let row {
                Text(row.title).font(.headline)
                if !row.hidden {
                    HStack {
                        TextField("Numero", value: $number, format: .number).textFieldStyle(.roundedBorder).frame(width: 90)
                        Stepper("Numero lezione", value: $number, in: 1...999).labelsHidden()
                        Button("Verifica modifica") { Task {
                            do {
                                _ = try await model.libraryRequest(["action": "renumber", "preview": true, "changes": [["id": row.id, "number": number, "before": row.number as Any? ?? NSNull()]]])
                                renumberPreview = true
                            } catch { model.error = error.localizedDescription }
                        } }.disabled(row.course == nil || model.queueBusy)
                    }
                    if renumberPreview {
                        Text("Lezione \(row.number.map(String.init) ?? "senza numero") → \(number). I documenti già pubblicati non saranno riscritti.").font(.callout)
                        Button("Conferma numero") { Task {
                            await model.queueAction("lesson-edit", payload: ["action": "renumber", "changes": [["id": row.id, "number": number, "before": row.number as Any? ?? NSNull()]]])
                            renumberPreview = false
                        } }.buttonStyle(.borderedProminent)
                    }
                    if row.revisionRequired { Text("Questa lezione contiene risultati associati al numero precedente. Per riprendere la generazione, ripristina il numero originale; conserva i documenti già pubblicati.").font(.callout).foregroundStyle(.orange) }
                    Divider()
                    Button(row.notRecorded ? "Rimuovi stato Non registrata" : "Segna come Non registrata") { Task {
                        await model.queueAction("lesson-edit", payload: ["id": row.id, "action": row.notRecorded ? "recorded" : "not-recorded"])
                    } }
                    if !row.recordingIDs.isEmpty {
                        DisclosureGroup("Sposta un audio in un’altra lezione") {
                            Picker("Audio", selection: $moveRecording) {
                                Text("Scegli audio").tag("")
                                ForEach(model.recordings.filter { row.recordingIDs.contains($0.id) }) { Text($0.originalName ?? $0.title).tag($0.id) }
                            }
                            Picker("Destinazione", selection: $target) {
                                Text("Scegli lezione").tag("")
                                ForEach(model.libraryLessons.filter { !$0.hidden && $0.id != row.id && $0.course == row.course }) { other in
                                    Text(other.number.map { "Lezione \($0) · \(other.title)" } ?? other.title).tag(other.id)
                                }
                            }
                            Button("Sposta audio") { Task {
                                await model.queueAction("lesson-edit", payload: ["action": "move-audio", "recording": moveRecording, "target": target])
                            } }.disabled(moveRecording.isEmpty || target.isEmpty)
                        }
                    }
                    Divider()
                    Button("Rimuovi soltanto dall’app…") { files = nil; showHide = true }
                }
                if !row.hidden || !(row.pendingTrash ?? []).isEmpty { Button(row.hidden ? "Riprendi eliminazione…" : "Elimina lezione e contenuti MyNotes…", role: .destructive) { Task {
                    do { files = try await model.libraryRequest(["id": row.id, "action": "inventory"])["files"] as? [String] ?? [] }
                    catch { model.error = error.localizedDescription }
                } } }
                if let files {
                    Text("Saranno spostati nel Cestino \(files.count) file. Originali esterni e documenti condivisi sono conservati.").font(.callout)
                    ScrollView { VStack(alignment: .leading) { ForEach(files, id: \.self) { Text($0).font(.caption).textSelection(.enabled) } } }.frame(maxHeight: 130)
                    Button("Conferma eliminazione", role: .destructive) { Task {
                        await model.deleteLesson(row, files: files)
                        if model.error == nil { dismiss() }
                    } }
                }
            }
            if let error = model.error { Text(error).foregroundStyle(.red).font(.callout).textSelection(.enabled) }
            HStack { Spacer(); Button("Fine") { dismiss() }.keyboardShortcut(.cancelAction) }
        }.padding(28).frame(width: 580)
            .disabled(model.queueBusy)
            .onAppear { number = row?.number ?? 1 }
            .onChange(of: number) { _, _ in renumberPreview = false }
            .confirmationDialog("Rimuovere la lezione soltanto dall’app?", isPresented: $showHide) {
                Button("Rimuovi dall’app", role: .destructive) { Task {
                    await model.queueAction("lesson-edit", payload: ["id": lessonID, "action": "hide"])
                    if model.error == nil { dismiss() }
                } }
            } message: { Text("I file restano conservati. Puoi ripristinare la lezione da Organizza lezioni → Mostra lezioni rimosse.") }
    }
    @CaptureViewState private var showHide = false
}

struct LibraryNumberShift: View {
    @ObservedObject var model: CaptureModel
    let course: String
    @Environment(\.dismiss) private var dismiss
    @CaptureViewState private var first = 1
    @CaptureViewState private var last = 1
    @CaptureViewState private var offset = 1
    @CaptureViewState private var preview: [[String: Any]] = []
    @CaptureViewState private var changes: [[String: Any]] = []
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Sposta numerazione").font(.title2.weight(.semibold))
            Form {
                TextField("Dalla lezione", value: $first, format: .number)
                TextField("Alla lezione", value: $last, format: .number)
                TextField("Spostamento (+ o −)", value: $offset, format: .number)
            }
            Button("Verifica e mostra anteprima") { Task {
                let proposed: [[String: Any]] = model.libraryLessons.filter { !$0.hidden && $0.course == course && ($0.number ?? 0) >= first && ($0.number ?? 1000) <= last }.map { ["id": $0.id, "number": ($0.number ?? 0) + offset, "before": $0.number ?? 0] }
                do {
                    let result = try await model.libraryRequest(["action": "renumber", "preview": true, "changes": proposed])
                    preview = result["changes"] as? [[String: Any]] ?? []; changes = proposed
                } catch { model.error = error.localizedDescription; preview = []; changes = [] }
            } }
            ScrollView {
                VStack(alignment: .leading) {
                    ForEach(preview.map { "Lezione \($0["before"] as? Int ?? 0) → \($0["after"] as? Int ?? 0)" }, id: \.self) { Text($0) }
                }
            }.frame(maxHeight: 160)
            if let error = model.error { Text(error).foregroundStyle(.red).font(.callout) }
            HStack {
                Button("Annulla") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("Conferma spostamento") { Task {
                    await model.queueAction("lesson-edit", payload: ["action": "renumber", "changes": changes])
                    if model.error == nil { dismiss() }
                } }.disabled(changes.isEmpty || model.queueBusy).buttonStyle(.borderedProminent)
            }
        }.padding(28).frame(width: 500)
            .onChange(of: first) { _, _ in changes = []; preview = [] }
            .onChange(of: last) { _, _ in changes = []; preview = [] }
            .onChange(of: offset) { _, _ in changes = []; preview = [] }
    }
}
