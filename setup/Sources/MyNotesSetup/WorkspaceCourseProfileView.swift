import SwiftUI

struct WorkspaceCourseProfile: Decodable {
    struct Course: Decodable {
        let code: String
        let name: String
        let kind: String
        let languages: [String: String]
        let instructors: [String]
        let modules: [String]
        let assessment: String?
        let preparation: String?
        let context: String
        let difficultTopics: [String]
        let folders: [String: String]
        let files: [String: String]
        let publication: Publication

        struct Publication: Decodable { let mode: String }

        enum CodingKeys: String, CodingKey {
            case code, name, kind, languages, instructors, modules, assessment, preparation, context, folders, files, publication
            case difficultTopics = "difficult_topics"
        }
    }

    struct Notes: Decodable {
        let own: [String: String]
        let effective: [String: String]
        let style: Style

        struct Style: Decodable {
            let own: Values
            let effective: Values

            struct Values: Decodable {
                let writingStyle: String?
                let listDensity: String?
                let imagePolicy: String?
                let imageLimit: Int?
                let recap: Bool?
                let questionCount: Int?
                let hasImageLimitOverride: Bool

                enum CodingKeys: String, CodingKey {
                    case writingStyle = "writing_style", listDensity = "list_density"
                    case imagePolicy = "image_policy", imageLimit = "image_limit"
                    case recap, questionCount = "question_count"
                }

                init(from decoder: Decoder) throws {
                    let values = try decoder.container(keyedBy: CodingKeys.self)
                    writingStyle = try values.decodeIfPresent(String.self, forKey: .writingStyle)
                    listDensity = try values.decodeIfPresent(String.self, forKey: .listDensity)
                    imagePolicy = try values.decodeIfPresent(String.self, forKey: .imagePolicy)
                    imageLimit = try values.decodeIfPresent(Int.self, forKey: .imageLimit)
                    recap = try values.decodeIfPresent(Bool.self, forKey: .recap)
                    questionCount = try values.decodeIfPresent(Int.self, forKey: .questionCount)
                    hasImageLimitOverride = values.contains(.imageLimit)
                }
            }
        }
    }

    let course: Course
    let notes: Notes?
}

struct WorkspaceCourseProfileView: View {
    @ObservedObject var model: CaptureModel
    @ObservedObject var setup: SetupModel
    let code: String
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.openSettings) private var openSettings
    @CaptureViewState<WorkspaceCourseProfile?> private var profile: WorkspaceCourseProfile?
    @CaptureViewState<Int> private var reload = 0
    @CaptureViewState<Bool> private var loading = true
    @CaptureViewState<Bool> private var launching = false
    @CaptureViewState<String?> private var loadError: String?
    @CaptureViewState<String?> private var launchError: String?
    @CaptureViewState<Bool> private var showCourse = true
    @CaptureViewState<Bool> private var showNotes = true
    @CaptureViewState<Bool> private var showDelivery = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack(spacing: 12) {
                    if setup.prepared && setup.providerFound || model.isPreview {
                        Button("Modifica profilo in chat", systemImage: "arrow.up.right.square") { openChat() }
                            .disabled(launching || profile == nil || model.isPreview)
                        if launching { ProgressView().controlSize(.small).accessibilityLabel("Apertura chat") }
                    } else {
                        Button("Configura agente…", systemImage: "gearshape") { openSettings() }
                    }
                    Spacer()
                    if loading { ProgressView().controlSize(.small).accessibilityLabel("Lettura del profilo") }
                    Button("Aggiorna profilo", systemImage: "arrow.clockwise") { reload += 1 }
                        .labelStyle(.iconOnly).help("Aggiorna profilo").disabled(loading)
                }
                if let launchError {
                    Label(launchError, systemImage: "exclamationmark.circle").font(.callout).foregroundStyle(.red)
                }
                if let loadError {
                    Label(loadError, systemImage: "exclamationmark.circle").font(.callout).foregroundStyle(.secondary)
                }
                if let profile {
                    section("Il corso", expanded: $showCourse) { courseFields(profile.course) }
                    section("Come vuoi gli appunti", expanded: $showNotes) { noteFields(profile) }
                    section("Materiali e consegna", expanded: $showDelivery) { deliveryFields(profile.course) }
                }
            }
            .padding(24).frame(maxWidth: 820, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task(id: reload) {
            loading = true
            loadError = nil
            do {
                let value = try await model.courseProfile(code)
                guard !Task.isCancelled else { return }
                profile = value
            } catch {
                guard !Task.isCancelled else { return }
                loadError = "Impossibile leggere il profilo. Premi Aggiorna profilo per riprovare."
            }
            loading = false
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { reload += 1 }
        }
    }

    private func openChat() {
        launching = true
        launchError = nil
        let message = [
            "Vorrei modificare il profilo del corso \(code).",
            "Leggi il profilo attuale con mynotes transcription-queue course-profile --course \(code) e chiedimi cosa desidero cambiare.",
            "Applica solo le modifiche che richiedo per questo codice. Usa memory show/update con la revisione corrente e ambito corso per le preferenze.",
            "Per gli altri campi usa course edit \(code) --file con i soli campi richiesti. Conserva la descrizione originale e le altre impostazioni.",
            "Dopo il salvataggio riassumi cosa è cambiato. Non avviare trascrizioni, generazione o pubblicazione di appunti."
        ].joined(separator: " ")
        Task {
            defer { launching = false }
            do { try await setup.launchChat(message: message) }
            catch { launchError = "Impossibile aprire la chat. \(error.localizedDescription)" }
        }
    }

    private func section<Content: View>(_ title: String, expanded: Binding<Bool>, @ViewBuilder content: @escaping () -> Content) -> some View {
        DisclosureGroup(isExpanded: expanded) {
            VStack(alignment: .leading, spacing: 12, content: content)
                .frame(maxWidth: .infinity, alignment: .leading).padding(.top, 12).padding(.leading, 16)
                .textSelection(.enabled)
        } label: {
            Text(title).font(.headline).foregroundStyle(.primary).accessibilityAddTraits(.isHeader)
        }
        .tint(.secondary).padding(16)
        .background(WorkspaceStyle.subtle, in: RoundedRectangle(cornerRadius: WorkspaceStyle.radius))
    }

    @ViewBuilder private func courseFields(_ course: WorkspaceCourseProfile.Course) -> some View {
        let kinds = ["quantitative": "Quantitativo", "theoretical": "Teorico", "cases": "Casi di studio",
                     "programming": "Programmazione", "mixed": "Misto"]
        field("Tipologia", kinds[course.kind])
        field("Lingua delle lezioni", language(course.languages["lesson"]))
        field("Docenti", course.instructors.joined(separator: ", "))
        field("Moduli", course.modules.joined(separator: "\n"))
        field("Esame", course.assessment)
        field("Preparazione", course.preparation)
        field("Argomenti difficili", course.difficultTopics.joined(separator: "\n"))
        field("Descrizione originale", course.context)
    }

    @ViewBuilder private func noteFields(_ profile: WorkspaceCourseProfile) -> some View {
        field("Lingua degli appunti", language(profile.course.languages["notes"]))
        if let notes = profile.notes {
            let current = notes.style.effective
            let own = notes.style.own
            let writing = ["clear_study": "Chiari e orientati allo studio", "concise": "Sintetici", "detailed": "Approfonditi"]
            let density = ["balanced": "Equilibrata", "prose_first": "Prevalentemente discorsiva", "list_first": "Prevalentemente schematica"]
            let images = ["essential": "Figure essenziali", "none": "Nessuna immagine", "explanatory": "Figure spiegate"]
            field("Livello di dettaglio", current.writingStyle.flatMap { writing[$0] }, origin: origin(own.writingStyle != nil))
            field("Struttura", current.listDensity.flatMap { density[$0] }, origin: origin(own.listDensity != nil))
            field("Immagini", current.imagePolicy.flatMap { images[$0] }, origin: origin(own.imagePolicy != nil))
            if current.imagePolicy != "none" {
                field("Limite immagini", current.imageLimit.map(String.init) ?? "Nessun limite numerico", origin: origin(own.hasImageLimitOverride))
            }
            field("Riepilogo", current.recap.map { $0 ? "Incluso" : "Disattivato" }, origin: origin(own.recap != nil))
            field("Domande di ripasso", current.questionCount.map(String.init), origin: origin(own.questionCount != nil))
            let courseRules = notes.effective.keys.sorted().filter { notes.own[$0] != nil }.compactMap { notes.effective[$0] }
            let inheritedRules = notes.effective.keys.sorted().filter { notes.own[$0] == nil }.compactMap { notes.effective[$0] }
            field("Istruzioni del corso", courseRules.joined(separator: "\n\n"))
            field("Istruzioni ereditate", inheritedRules.joined(separator: "\n\n"))
            if courseRules.isEmpty && inheritedRules.isEmpty {
                Text("Nessuna istruzione personalizzata.").font(.callout).foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder private func deliveryFields(_ course: WorkspaceCourseProfile.Course) -> some View {
        let folders = [("materials", "Materiali"), ("recordings", "Registrazioni"), ("transcripts", "Trascrizioni"),
                       ("syllabus", "Syllabus"), ("personal_notes", "Cartella appunti"), ("handout", "Cartella dispensa")]
        if !folders.contains(where: { course.folders[$0.0]?.isEmpty == false }) && course.files.values.allSatisfy(\.isEmpty) {
            Text("Nessuna cartella o documento collegato.").font(.callout).foregroundStyle(.secondary)
        }
        ForEach(folders, id: \.0) { key, label in field(label, course.folders[key]) }
        field("Documento appunti", course.files["personal_notes"])
        field("Documento dispensa", course.files["handout"])
        field("Pubblicazione", course.publication.mode == "review" ? "Dopo revisione" : "Automatica")
    }

    @ViewBuilder private func field(_ title: String, _ value: String?, origin: String? = nil) -> some View {
        if let value, !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                Text(value).fixedSize(horizontal: false, vertical: true)
                if let origin { Text(origin).font(.caption).foregroundStyle(.secondary) }
            }
        }
    }

    private func language(_ code: String?) -> String? {
        code.map { Locale(identifier: "it").localizedString(forIdentifier: $0)?.capitalized ?? $0 }
    }

    private func origin(_ own: Bool) -> String { own ? "Del corso" : "Ereditato" }
}
