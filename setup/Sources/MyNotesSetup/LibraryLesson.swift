import Foundation

extension CaptureModel {
    func libraryRequest(_ payload: [String: Any]) async throws -> [String: Any] {
        let executable = context.launcher, workspace = context.workspace
        let input = try JSONSerialization.data(withJSONObject: payload)
        let data = try await Task.detached {
            try Self.invoke(executable, workspace, "lesson-edit", [], input, group: "transcription-queue")
        }.value
        return try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
    }
    func courseProfile(_ course: String) async throws -> WorkspaceCourseProfile {
        let executable = context.launcher, workspace = context.workspace
        let data = try await Task.detached {
            try Self.invoke(executable, workspace, "course-profile", ["--course", course], nil, group: "transcription-queue")
        }.value
        let result = try JSONDecoder().decode(WorkspaceCourseProfile.self, from: data)
        guard result.course.code == course else { throw CocoaError(.fileReadCorruptFile) }
        return result
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
