import SwiftUI
import Foundation

private struct AudioDestination {
    var target = ""
    var course = ""
    var number = ""
}

struct WorkspaceAudioImportView: View {
    @ObservedObject var model: CaptureModel
    let urls: [URL]
    let initialEventID: String?
    var initialCourse: String? = nil
    var initialLectureID: String? = nil
    @Environment(\.dismiss) private var dismiss
    @CaptureViewState private var files: [CaptureAudioInspection] = []
    @CaptureViewState private var destinations: [String: AudioDestination] = [:]
    @CaptureViewState private var inspecting = true
    @CaptureViewState private var failure: String?
    @CaptureViewState private var scoped: [URL] = []
    private func binding(_ path: String) -> Binding<AudioDestination> {
        Binding(get: { destinations[path] ?? AudioDestination() }, set: { destinations[path] = $0 })
    }
    private var valid: Bool {
        files.allSatisfy { file in
            let choice = destinations[file.path] ?? AudioDestination()
            return choice.number.isEmpty || (!choice.course.isEmpty && Int(choice.number).map { (1...999).contains($0) } == true)
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Importa audio").font(.title2.weight(.semibold))
            Text("Scegli la destinazione di ogni file. Gli originali esterni restano conservati.").font(.callout).foregroundStyle(.secondary)
            if inspecting { ProgressView("Verifica dei file…") }
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    ForEach(files) { file in
                        VStack(alignment: .leading, spacing: 10) {
                            Label(file.name, systemImage: "waveform").font(.headline)
                            Text((file.duration.map { Duration.seconds($0).formatted(.time(pattern: .minuteSecond)) } ?? "Durata non disponibile") + " · " + ByteCountFormatter.string(fromByteCount: file.size, countStyle: .file)).font(.caption).foregroundStyle(.secondary)
                            if file.duplicate == true { Label("Già presente: non verrà creata una copia o modificata l’associazione.", systemImage: "doc.on.doc").font(.caption) }
                            if file.nameCollision == true { Text("Nome già presente: i contenuti diversi saranno conservati separatamente.").font(.caption).foregroundStyle(.secondary) }
                            Picker("Destinazione", selection: binding(file.path).target) {
                                Text("Assegna corso e numero…").tag("")
                                ForEach(model.libraryLessons.filter { !$0.hidden && ($0.course == (destinations[file.path]?.course ?? "") || (destinations[file.path]?.course ?? "").isEmpty) }) { lesson in
                                    Text("\(lesson.course ?? "Da associare") · \(lesson.number.map { "Lezione \($0)" } ?? lesson.title)").tag(lesson.id)
                                }
                            }
                            if (destinations[file.path]?.target ?? "").isEmpty {
                                HStack {
                                    Picker("Corso", selection: binding(file.path).course) {
                                        Text("Da associare").tag("")
                                        ForEach(model.snapshot?.courses ?? []) { Text($0.name).tag($0.code) }
                                    }
                                    TextField("Numero (facoltativo)", text: binding(file.path).number).textFieldStyle(.roundedBorder).frame(width: 180)
                                }
                                Text("Senza un numero confermato l’audio resta da assegnare; non viene aggiunto un numero progressivo.").font(.caption).foregroundStyle(.secondary)
                            } else {
                                Text("Se ci sono più audio, sceglierai quale trascrivere nel dettaglio della lezione.").font(.caption).foregroundStyle(.secondary)
                            }
                        }.disabled(file.duplicate == true)
                        Divider()
                    }
                }
            }.frame(maxHeight: 430)
            if let failure { Text(failure).foregroundStyle(.red).font(.callout) }
            if let error = model.error { Text(error).foregroundStyle(.red).font(.callout).textSelection(.enabled) }
            HStack {
                Button("Annulla") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("Importa nella raccolta") { Task {
                    let items: [[String: Any]] = files.map { file in
                        let destination = destinations[file.path] ?? AudioDestination()
                        var item: [String: Any] = ["path": file.path]
                        if !destination.target.isEmpty { item["target"] = destination.target }
                        else if !destination.course.isEmpty {
                            item["course"] = destination.course
                            if let number = Int(destination.number) { item["lesson"] = number }
                        }
                        return item
                    }
                    await model.queueAction("import-audio", payload: ["items": items])
                    if model.error == nil { dismiss() }
                } }.buttonStyle(.borderedProminent).disabled(inspecting || failure != nil || files.count != urls.count || !valid || model.queueBusy || model.isPreview)
            }
        }.padding(28).frame(width: 680)
            .task {
                scoped = urls.filter { $0.startAccessingSecurityScopedResource() }
                do {
                    files = try await model.inspectAudio(urls)
                    let target = model.libraryLessons.first { lesson in
                        initialEventID.map { lesson.eventIDs.contains($0) } == true
                            || initialLectureID.map { raw in lesson.course == initialCourse && lesson.number == Int(raw.components(separatedBy: "-L").last ?? "") } == true
                    }
                    for file in files { destinations[file.path] = AudioDestination(target: target?.id ?? "", course: initialCourse ?? target?.course ?? "", number: "") }
                } catch { failure = error.localizedDescription }
                inspecting = false
            }
            .onDisappear { scoped.forEach { $0.stopAccessingSecurityScopedResource() } }
    }
}
