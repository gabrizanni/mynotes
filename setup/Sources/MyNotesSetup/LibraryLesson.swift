import AppKit
import Foundation

@MainActor
func recycleLibraryFile(at path: String) async throws {
    let source = URL(fileURLWithPath: path)
    // Use macOS's Finder-style recycle operation, including for File Provider
    // folders such as OneDrive. Require confirmation for this specific file.
    let moved = try await NSWorkspace.shared.recycle([source])
    guard moved[source] != nil else {
        throw CocoaError(.fileWriteUnknown, userInfo: [NSFilePathErrorKey: path])
    }
}

extension CaptureModel {
    func libraryRequest(_ payload: [String: Any]) async throws -> [String: Any] {
        let executable = context.launcher, workspace = context.workspace
        let input = try JSONSerialization.data(withJSONObject: payload)
        let data = try await Task.detached {
            try Self.invoke(executable, workspace, "lesson-edit", [], input, group: "transcription-queue")
        }.value
        return try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
    }
    func courseProfile(_ course: String) async throws -> [(String, String)] {
        let executable = context.launcher, workspace = context.workspace
        let data = try await Task.detached {
            try Self.invoke(executable, workspace, "course-profile", ["--course", course], nil, group: "transcription-queue")
        }.value
        let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
        let profile = result["course"] as? [String: Any] ?? [:]
        let fields = [("name", "Nome"), ("code", "Codice"), ("kind", "Tipologia"), ("languages", "Lingue"), ("instructors", "Docenti"), ("modules", "Moduli"), ("assessment", "Esame"), ("context", "Contesto"), ("preparation", "Preparazione"), ("difficult_topics", "Argomenti difficili"), ("style", "Preferenze appunti"), ("folders", "Cartelle"), ("publication", "Pubblicazione")]
        func display(_ value: Any?) -> String {
            guard let value else { return "Non configurato" }
            if let text = value as? String { return text.isEmpty ? "Non configurato" : text }
            if let list = value as? [String] { return list.isEmpty ? "Non configurato" : list.joined(separator: ", ") }
            if let dictionary = value as? [String: Any] {
                return dictionary.isEmpty ? "Non configurato" : dictionary.keys.sorted().map { "\($0): \(display(dictionary[$0]))" }.joined(separator: "\n")
            }
            return String(describing: value)
        }
        return fields.map { ($0.1, display(profile[$0.0])) }
    }
    func deleteLesson(_ lesson: LibraryLesson, files: [String]) async {
        guard !queueBusy, !busy, !isPreview else { return }
        queueBusy = true; error = nil
        var completed: [String] = []
        do {
            let result = try await libraryRequest(["id": lesson.id, "action": "delete", "files": files])
            for path in result["files"] as? [String] ?? [] {
                try await recycleLibraryFile(at: path)
                completed.append(path)
                _ = try await libraryRequest(["id": lesson.id, "action": "trash-completed", "completed": completed])
            }
            notice = "Lezione rimossa. I file MyNotes sono nel Cestino; originali esterni e documenti condivisi restano conservati."
        } catch { self.error = "Eliminazione incompleta: \(error.localizedDescription). Puoi riprenderla da Gestisci corsi." }
        queueBusy = false
        await refresh(includeLibrary: true)
    }
    func trashLessonFile(_ lessonID: String, path: String) async {
        guard !queueBusy, !busy, !isPreview else { return }
        queueBusy = true; error = nil
        var prepared = false
        do {
            _ = try await libraryRequest(["id": lessonID, "action": "delete-file", "path": path])
            prepared = true
            try await recycleLibraryFile(at: path)
            prepared = false
            _ = try await libraryRequest(["id": lessonID, "action": "trash-completed", "completed": [path]])
            notice = "File spostato nel Cestino; gli altri contenuti sono conservati."
        } catch {
            var message = "Operazione incompleta: \(error.localizedDescription)"
            if prepared {
                do {
                    _ = try await libraryRequest(["id": lessonID, "action": "cancel-file-trash", "path": path])
                } catch {
                    message += " Verifica lo stato della lezione in Gestisci corsi."
                }
            }
            self.error = message
        }
        queueBusy = false
        await refresh(includeLibrary: true)
    }

}
