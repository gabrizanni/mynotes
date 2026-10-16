import Foundation

/// Presents current-component progress without treating verification as a download.
struct SetupProgressPresentation {
    let title: String
    let detail: String
    let fraction: Double?
    let downloadNumbers: String?
    let activity: String
    let waitingForNetwork: Bool

    static func read(_ state: [String: Any], modifiedAt: Date, startedAt: Date, now: Date) -> Self? {
        guard modifiedAt >= startedAt else { return nil }
        let stage = state["stage"] as? String ?? ""
        let detail = state["message"] as? String ?? ""
        let phase = state["phase"] as? String ?? ""
        // Older payloads report byte counts through completed/total only while downloading.
        let downloading = phase == "download" || (phase.isEmpty && detail.hasPrefix("Downloading "))
        let suppliedBytes = state["downloaded_bytes"] as? Double
        let suppliedTotal = state["total_bytes"] as? Double
        let completed = suppliedBytes ?? (downloading ? state["completed"] as? Double : nil)
        let total = suppliedTotal ?? (downloading ? state["total"] as? Double : nil)
        var fraction: Double?
        var numbers: String?
        if downloading, let completed, let total, completed.isFinite, total.isFinite, completed >= 0, total > 0 {
            fraction = min(1, completed / total)
            numbers = String(format: "Download corrente: %.1f / %.1f MiB · %.1f%%", completed / 1_048_576, total / 1_048_576, (fraction ?? 0) * 100)
        }
        let epoch = state["last_data_at"] as? Double
        let lastData = epoch.flatMap { $0.isFinite && $0 > 0 ? Date(timeIntervalSince1970: $0) : nil }
        let awaitingFirstData = downloading && state.keys.contains("last_data_at") && lastData == nil
        let startedEpoch = state["started_at"] as? Double
        let requestStarted = startedEpoch.flatMap { $0.isFinite && $0 > 0 ? Date(timeIntervalSince1970: $0) : nil } ?? modifiedAt
        let observedAt = downloading ? (awaitingFirstData ? requestStarted : lastData ?? modifiedAt) : modifiedAt
        let elapsed = now.timeIntervalSince(observedAt)
        let seconds = Int(min(Double(Int.max / 4), max(0, elapsed.isFinite ? elapsed : 0)))
        let age = seconds < 60 ? "\(seconds) s fa" : "\(seconds / 60) min \(seconds % 60) s fa"
        let waiting = downloading && seconds >= 60
        let title: String
        if downloading {
            title = detail.lowercased().contains("libreoffice") ? "Scarico LibreOffice per verificare il Word…" : stage == "python" || stage == "bootstrap" ? "Scarico Python privato…" : "Scarico i componenti per la trascrizione…"
        } else if stage == "components" {
            title = phase == "prepare" || detail.hasPrefix("Preparing ") ? "Preparo i componenti locali…" : "Verifico i componenti già presenti…"
        } else {
            title = ["installation": detail.hasPrefix("Checking ") ? "Verifico l’installazione di MyNotes e Capture…" : "Installo MyNotes e Capture…",
                     "workspace": "Preparo le cartelle per la prima conversazione…",
                     "verification": "Provo trascrizione locale e creazione del Word…",
                     "python": "Preparo Python privato…", "bootstrap": "Preparo Python privato…",
                     "ready": "Componenti pronti. Puoi aprire la chat.",
                     "failed": "Il setup si è fermato. Puoi riprendere con Riprova." ][stage] ?? "Preparazione in corso…"
        }
        return Self(title: title, detail: detail, fraction: fraction, downloadNumbers: numbers,
                    activity: awaitingFirstData ? "Attendo i primi dati del download da \(seconds) s." : downloading ? "Ultimi dati ricevuti: \(age)." : "Ultimo aggiornamento: \(age).", waitingForNetwork: waiting)
    }
}
