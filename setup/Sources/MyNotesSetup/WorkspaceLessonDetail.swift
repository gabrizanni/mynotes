import AppKit
import SwiftUI
import AVFoundation
import Combine
import UniformTypeIdentifiers

private enum LessonSheet: Identifiable {
    case rename(CaptureRecording), renameTranscript(CaptureTranscript), associate(CaptureRecording), eventAssociation(CaptureEvent), edit(CaptureEvent), files([URL])
    var id: String { switch self { case .rename(let x): return "rename:" + x.id; case .renameTranscript(let x): return "transcript:" + x.id; case .associate(let x): return "associate:" + x.id; case .eventAssociation(let x): return "event-association:" + x.id; case .edit(let x): return "edit:" + x.id; case .files: return "files" } }
}

struct WorkspaceLessonDetail: View {
    @ObservedObject var model: CaptureModel
    let fallback: WorkspaceLesson
    let courseName: String
    @CaptureViewState<LessonSheet?> private var sheet: LessonSheet?
    @CaptureViewState<Bool> private var showImporter = false
    @CaptureViewState<CaptureRecording?> private var trashCandidate: CaptureRecording?
    @CaptureViewState<Bool> private var showAlternatives = false
    @CaptureViewState<String> private var transcript = ""
    init(model: CaptureModel, lesson: WorkspaceLesson, courseName: String) { self.model = model; self.fallback = lesson; self.courseName = courseName }
    private var lesson: WorkspaceLesson {
        WorkspaceLesson.build(snapshot: model.snapshot, audio: model.recordings, transcripts: model.transcripts).first { $0.id == fallback.id } ?? fallback
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(lesson.title).font(.title2.weight(.semibold)).textSelection(.enabled)
                        Text(courseName).foregroundStyle(.secondary)
                        Text(CaptureDates.label(CaptureDates.write(lesson.date), zone: lesson.timezone)).font(.callout).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Menu {
                        Button("Importa audio…", systemImage: "square.and.arrow.down") { showImporter = true }
                        if let event = lesson.event, let snapshot = model.snapshot {
                            if event.recordingID != nil { Button("Associa a corso e numero…") { sheet = .eventAssociation(event) }.disabled(!snapshot.editable(event)) }
                            Button("Modifica lezione…") { sheet = .edit(event) }.disabled(!snapshot.editable(event))
                            if event.localChanges { Button("Ripristina dal calendario") { Task { await model.perform("reset-lesson", arguments: ["--event", event.id]) } } }
                        }
                    } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).fixedSize().accessibilityLabel("Altre azioni sulla lezione")
                }
                if lesson.numberConflict {
                    Label("Il titolo e il numero associato non coincidono. Verifica l’associazione prima di proseguire.", systemImage: "exclamationmark.circle")
                        .font(.callout).foregroundStyle(.orange)
                }
                if let event = lesson.event, !lesson.hasContent, let snapshot = model.snapshot {
                    VStack(alignment: .leading, spacing: 8) {
                        Label(snapshot.eventStatusLabel(event), systemImage: "calendar").font(.callout.weight(.medium))
                        Text(snapshot.eventStatusExplanation(event)).font(.callout).foregroundStyle(.secondary)
                    }
                }
                audioSection
                if let audio = lesson.selectedAudio {
                    let action = audio.actions?.first { ["enqueue", "retry", "restart", "recover"].contains($0) || $0 == "finish" && audio.archiveStatus == "PENDING" }
                    if let action {
                        HStack {
                            Text(lesson.transcriptionLabel).font(.callout).foregroundStyle(.secondary)
                            Spacer()
                            Button(action == "enqueue" ? "Trascrivi" : action == "finish" ? "Completa salvataggio" : "Riprova trascrizione") { Task { await model.recordingAction(action, item: audio) } }
                                .buttonStyle(.borderedProminent).disabled(model.queueBusy || model.busy || model.isPreview)
                        }
                    }
                    if !audio.detailMessages.isEmpty {
                        DisclosureGroup("Dettagli dell’audio") {
                            ForEach(audio.detailMessages, id: \.self) { Text($0).font(.callout).foregroundStyle(.secondary).textSelection(.enabled) }
                        }.font(.callout)
                    }
                    if ["QUEUED", "RUNNING", "CANCELLING"].contains(audio.transcriptionState) {
                        Label(lesson.transcriptionLabel + " · segui l’avanzamento in Attività", systemImage: "waveform.path").font(.callout).foregroundStyle(.secondary)
                    }
                }
                if let path = lesson.transcriptPath {
                    Divider()
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text("Trascrizione").font(.headline)
                            Spacer()
                            Button("Apri completa", systemImage: "arrow.up.right") { model.openFile(path) }.controlSize(.small)
                            Menu {
                                Button("Mostra nel Finder") { model.revealFile(path) }
                                if let item = lesson.transcripts.first(where: { $0.path == path }) {
                                    Button("Rinomina trascrizione…") { sheet = .renameTranscript(item) }
                                }
                            } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).fixedSize().accessibilityLabel("Azioni trascrizione")
                        }
                        if let identifier = lesson.transcriptAudioID, let original = lesson.audio.first(where: { $0.id == identifier }) {
                            Text("Da: " + (original.originalName ?? URL(fileURLWithPath: original.path).lastPathComponent)).font(.caption).foregroundStyle(.secondary)
                            if let selected = lesson.selectedAudio, selected.id != identifier {
                                Label("Questa trascrizione appartiene all’audio precedente. La nuova sorgente non è ancora trascritta.", systemImage: "info.circle").font(.callout).foregroundStyle(.orange)
                            }
                        }
                        Text(transcript.isEmpty ? "Caricamento della trascrizione…" : transcript).font(.body).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .task(id: path) {
                        transcript = ""
                        let value = await Task.detached { () -> String in
                            guard let handle = FileHandle(forReadingAtPath: path) else { return "Il file non è disponibile. Usa Apri completa per verificarlo." }
                            defer { try? handle.close() }
                            let data = (try? handle.read(upToCount: 80_000)) ?? Data()
                            return String(decoding: data, as: UTF8.self)
                        }.value
                        if !Task.isCancelled { transcript = value.isEmpty ? "La trascrizione è vuota." : value }
                    }
                }
                let older = lesson.transcripts.filter { $0.path != lesson.transcriptPath }
                if !older.isEmpty {
                    DisclosureGroup("Altre trascrizioni (\(older.count))") {
                        ForEach(older) { item in
                            Button(item.title) { model.openFile(item.path) }.buttonStyle(.plain).foregroundStyle(.tint)
                                .contextMenu {
                                    Button("Rinomina…") { sheet = .renameTranscript(item) }
                                    Button("Mostra nel Finder") { model.revealFile(item.path) }
                                }.padding(.vertical, 6)
                        }
                    }
                }
                if let error = model.error { Text(error).font(.callout).foregroundStyle(.red).textSelection(.enabled) }
            }.padding(28).frame(maxWidth: .infinity, alignment: .leading)
        }.background(WorkspaceStyle.background)
        .fileImporter(isPresented: $showImporter, allowedContentTypes: [.audio], allowsMultipleSelection: true) { result in
            switch result { case .success(let urls): sheet = .files(urls); case .failure(let error): model.error = error.localizedDescription }
        }
        .dropDestination(for: URL.self) { urls, _ in guard !urls.isEmpty else { return false }; sheet = .files(urls); return true }
        .sheet(item: $sheet) { kind in
            switch kind {
            case .renameTranscript(let transcript): WorkspaceTranscriptRenameView(model: model, transcript: transcript)
            case .rename(let audio): WorkspaceAudioEditView(model: model, audio: audio, association: false)
            case .associate(let audio): WorkspaceAudioEditView(model: model, audio: audio, association: true)
            case .eventAssociation(let event): WorkspaceEventAssociationView(model: model, event: event)
            case .edit(let event): if let snapshot = model.snapshot { CaptureLessonForm(model: model, snapshot: snapshot, event: event, immediate: false) }
            case .files(let urls): WorkspaceAudioImportView(model: model, urls: urls, initialEventID: lesson.event?.id, initialCourse: lesson.courseCode, initialLectureID: lesson.selectedAudio?.lectureID)
            }
        }
        .confirmationDialog("Spostare l’audio nel Cestino?", isPresented: Binding(get: { trashCandidate != nil }, set: { if !$0 { trashCandidate = nil } }), presenting: trashCandidate) { audio in
            Button("Sposta nel Cestino", role: .destructive) { Task { await model.trashAudio(audio) }; trashCandidate = nil }
            Button("Annulla", role: .cancel) { trashCandidate = nil }
        } message: { audio in Text("Verrà spostato solo \(audio.originalName ?? audio.title). La trascrizione e gli altri audio restano conservati.") }
    }
    @ViewBuilder private var audioSection: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack { Text("Audio").font(.headline); Spacer(); if !lesson.audio.isEmpty { Text("\(lesson.audio.count) \(lesson.audio.count == 1 ? "sorgente" : "sorgenti")").font(.caption).foregroundStyle(.secondary) } }
            if lesson.audio.isEmpty {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Hai registrato la lezione con un altro dispositivo?").font(.callout).foregroundStyle(.secondary)
                    Button("Importa audio…", systemImage: "square.and.arrow.down") { showImporter = true }.buttonStyle(.borderedProminent)
                    Text("Puoi anche trascinare qui un file. L’originale viene conservato.").font(.caption).foregroundStyle(.secondary)
                }.padding(20).frame(maxWidth: .infinity, alignment: .leading).background(WorkspaceStyle.subtle, in: RoundedRectangle(cornerRadius: 10))
            } else {
                if let audio = lesson.selectedAudio {
                    audioRow(audio, selected: true)
                    if audio.audioAvailable != false { WorkspaceAudioPlayer(url: URL(fileURLWithPath: audio.path)).id(audio.id) }
                } else {
                    Label(lesson.needsChoice ? "Scegli quale registrazione usare per questa lezione." : "Gli audio di questa lezione sono esclusi. Puoi ripristinarli qui sotto.", systemImage: "info.circle").font(.callout).foregroundStyle(.secondary)
                }
                let alternatives = lesson.audio.filter { $0.id != lesson.selectedAudio?.id }
                if !alternatives.isEmpty {
                    if lesson.selectedAudio == nil {
                        ForEach(alternatives) { audio in audioRow(audio, selected: false) }
                    } else {
                        DisclosureGroup("Audio alternativi (\(alternatives.count))", isExpanded: $showAlternatives) {
                            VStack(spacing: 16) { ForEach(alternatives) { audio in audioRow(audio, selected: false) } }.padding(.top, 12)
                        }.font(.callout)
                    }
                }
            }
        }
    }
    private func audioRow(_ audio: CaptureRecording, selected: Bool) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                Image(systemName: selected ? "checkmark.circle.fill" : "waveform").foregroundStyle(selected ? Color.accentColor : Color.secondary)
                VStack(alignment: .leading, spacing: 5) {
                    Text(audio.originalName ?? URL(fileURLWithPath: audio.path).lastPathComponent).font(.callout.weight(.medium)).textSelection(.enabled)
                    Text((audio.origin ?? "Audio") + " · " + (audio.duration.map { Duration.seconds($0).formatted(.time(pattern: .minuteSecond)) } ?? "Durata non disponibile") + (selected ? " · In uso" : audio.sourceExcluded == true ? " · Escluso" : " · Alternativo")).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Menu {
                    Button("Ascolta con QuickTime") { model.openAudio(audio.path) }.disabled(audio.audioAvailable == false)
                    Button("Mostra nel Finder") { model.revealFile(audio.path) }
                    if audio.actions?.contains("finish") == true {
                        Button(audio.archiveStatus == "PENDING" ? "Completa salvataggio" : "Ricrea copia TXT") { Task { await model.recordingAction("finish", item: audio) } }
                    }
                    Button("Rinomina…") { sheet = .rename(audio) }
                    Button("Associa a corso e lezione…") { sheet = .associate(audio) }
                    if audio.actions?.contains("download") == true { Button("Scarica audio sul Mac") { Task { await model.recordingAction("download", item: audio) } } }
                    Divider()
                    Button(audio.sourceExcluded == true ? "Ripristina audio" : "Escludi dalla trascrizione") { Task { await model.queueAction(audio.sourceExcluded == true ? "restore-audio" : "exclude-audio", arguments: ["--recording", audio.id]) } }
                    Button("Sposta nel Cestino…", role: .destructive) { trashCandidate = audio }
                } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).fixedSize().accessibilityLabel("Azioni audio " + audio.title)
            }
            if !selected {
                HStack {
                    Button("Ascolta") { model.openAudio(audio.path) }.disabled(audio.audioAvailable == false)
                    Button("Usa per trascrivere") { Task { await model.queueAction("prefer-audio", arguments: ["--recording", audio.id]) } }.disabled(audio.audioAvailable == false)
                }.controlSize(.small)
            }
        }.padding(16).frame(maxWidth: .infinity, alignment: .leading).background(WorkspaceStyle.subtle, in: RoundedRectangle(cornerRadius: 10))
            .disabled(model.busy || model.queueBusy || model.isPreview)
    }
}

@MainActor private final class AudioPlayback: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published var playing = false
    @Published var position = 0.0
    @Published var failure: String?
    private var player: AVAudioPlayer?
    var duration: Double { player?.duration ?? 0 }
    init(url: URL) {
        super.init()
        do { player = try AVAudioPlayer(contentsOf: url); player?.delegate = self; player?.prepareToPlay() }
        catch { failure = "Anteprima non disponibile. Apri l’audio con QuickTime dal menu." }
    }
    func toggle() {
        guard let player else { return }
        if player.isPlaying { player.pause(); playing = false }
        else { if position >= duration { player.currentTime = 0 }; playing = player.play() }
    }
    func seek(_ value: Double) { player?.currentTime = value; position = value }
    func tick() { position = player?.currentTime ?? 0; playing = player?.isPlaying == true }
    func stop() { player?.pause(); playing = false }
    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in self.playing = false; self.position = self.duration }
    }
}

private struct WorkspaceAudioPlayer: View {
    @StateObject private var playback: AudioPlayback
    private let timer = Timer.publish(every: 0.3, on: .main, in: .common).autoconnect()
    init(url: URL) { _playback = StateObject(wrappedValue: AudioPlayback(url: url)) }
    var body: some View {
        if let failure = playback.failure { Text(failure).font(.caption).foregroundStyle(.secondary) }
        else {
            HStack(spacing: 12) {
                Button { playback.toggle() } label: { Image(systemName: playback.playing ? "pause.fill" : "play.fill").frame(width: 28, height: 28) }.buttonStyle(.plain).accessibilityLabel(playback.playing ? "Pausa audio" : "Ascolta audio")
                Text(Duration.seconds(playback.position).formatted(.time(pattern: .minuteSecond))).font(.caption.monospacedDigit())
                Slider(value: Binding(get: { playback.position }, set: { playback.seek($0) }), in: 0...max(1, playback.duration)).accessibilityLabel("Posizione audio")
                Text(Duration.seconds(playback.duration).formatted(.time(pattern: .minuteSecond))).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }.onReceive(timer) { _ in if playback.playing { playback.tick() } }.onDisappear { playback.stop() }
        }
    }
}

private struct WorkspaceAudioEditView: View {
    @ObservedObject var model: CaptureModel
    let audio: CaptureRecording
    let association: Bool
    @Environment(\.dismiss) private var dismiss
    @CaptureViewState<String> private var title = ""
    @CaptureViewState<String> private var course = ""
    @CaptureViewState<Bool> private var hasNumber = false
    @CaptureViewState<Int> private var number = 1
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(association ? "Associa audio" : "Rinomina audio").font(.title2.weight(.semibold))
            Form {
                if association {
                    Picker("Corso", selection: $course) { Text("Scegli un corso").tag(""); ForEach(model.snapshot?.courses ?? []) { Text($0.name).tag($0.code) } }
                    Toggle("Associa a una lezione", isOn: $hasNumber)
                    if hasNumber { Stepper("Lezione \(number)", value: $number, in: 1...999) }
                } else { TextField("Titolo", text: $title) }
            }
            if let error = model.error { Text(error).font(.callout).foregroundStyle(.red) }
            HStack {
                Button("Annulla") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("Salva") { Task {
                    if association {
                        var payload: [String: Any] = ["course": course]
                        if hasNumber { payload["lesson"] = number }
                        await model.queueAction("associate-audio", arguments: ["--recording", audio.id], payload: payload)
                    } else { await model.queueAction("rename", arguments: ["--recording", audio.id, "--title", title]) }
                    if model.error == nil { dismiss() }
                } }.buttonStyle(.borderedProminent).disabled(model.queueBusy || (association ? course.isEmpty : title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty))
            }
        }.padding(28).frame(width: 460).onAppear {
            title = audio.title; course = audio.courseCode ?? ""; hasNumber = audio.lectureID != nil
            number = audio.lectureID?.components(separatedBy: "-L").last.flatMap(Int.init) ?? 1
        }
    }
}

private struct WorkspaceTranscriptRenameView: View {
    @ObservedObject var model: CaptureModel
    let transcript: CaptureTranscript
    @Environment(\.dismiss) private var dismiss
    @CaptureViewState<String> private var name = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Rinomina trascrizione").font(.title2.weight(.semibold))
            TextField("Nome del file TXT", text: $name).textFieldStyle(.roundedBorder)
            if let error = model.error { Text(error).font(.callout).foregroundStyle(.red) }
            HStack {
                Button("Annulla") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("Salva") { Task {
                    await model.queueAction("rename-transcript", arguments: ["--transcript", transcript.id, "--name", name])
                    if model.error == nil { dismiss() }
                } }.buttonStyle(.borderedProminent).disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.queueBusy || model.isPreview)
            }
        }.padding(28).frame(width: 460).onAppear { name = transcript.title }
    }
}

private struct WorkspaceEventAssociationView: View {
    @ObservedObject var model: CaptureModel
    let event: CaptureEvent
    @Environment(\.dismiss) private var dismiss
    @CaptureViewState<String> private var course = ""
    @CaptureViewState<Int> private var number = 1
    @CaptureViewState<Bool> private var hasNumber = true
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Associa lezione").font(.title2.weight(.semibold))
            Text(event.title).foregroundStyle(.secondary)
            Form {
                Picker("Corso", selection: $course) { Text("Scegli un corso").tag(""); ForEach(model.snapshot?.courses ?? []) { Text($0.name).tag($0.code) } }
                Toggle("Indica il numero della lezione", isOn: $hasNumber)
                if hasNumber { Stepper("Lezione \(number)", value: $number, in: 1...999) }
            }
            if let error = model.error { Text(error).font(.callout).foregroundStyle(.red) }
            HStack {
                Button("Annulla") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("Salva associazione") { Task {
                    let assignment: [String: Any] = ["eventID": event.id, "courseCode": course, "lessonNumber": hasNumber ? number as Any : NSNull()]
                    if await model.perform("assign", payload: ["assignments": [assignment]]) { dismiss() }
                } }.buttonStyle(.borderedProminent).disabled(course.isEmpty || model.busy || model.isPreview)
            }
        }.padding(28).frame(width: 460).onAppear { course = event.courseCode ?? ""; number = event.lessonNumber ?? 1; hasNumber = event.lessonNumber != nil }
    }
}
