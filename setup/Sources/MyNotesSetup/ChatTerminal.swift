import AppKit
import Foundation

enum ChatTerminal {
    static let bundleIdentifier = "com.apple.Terminal"

    @MainActor static func open(script: URL, directory: URL) async throws {
        guard script.isFileURL, FileManager.default.isExecutableFile(atPath: script.path),
              let terminal = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleIdentifier) else {
            throw NSError(domain: "MyNotes", code: 2, userInfo: [NSLocalizedDescriptionKey: "Impossibile aprire la chat: verifica Terminale e riprova."])
        }
        // Explicitly choose Terminal rather than the default .command handler.
        // Pass the URL as a document, so quotes/metacharacters are never shell input.
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            NSWorkspace.shared.open([script], withApplicationAt: terminal, configuration: NSWorkspace.OpenConfiguration()) { _, error in
                if let error { continuation.resume(throwing: error) }
                else { continuation.resume() }
            }
        }
    }
}
