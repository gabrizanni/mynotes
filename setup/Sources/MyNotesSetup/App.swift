import AppKit
import SwiftUI
import Foundation
import Combine
import CryptoKit

private func quoted(_ value: String) -> String { "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'" }
private func verifyPayload(_ root: URL) throws {
    let data = try Data(contentsOf: root.appendingPathComponent("release-manifest.json"))
    let manifest = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
    guard let files = manifest["files"] as? [String: String], files["bin/uv"] != nil, files["scripts/setup_runtime.py"] != nil else {
        throw NSError(domain: "MyNotes", code: 2, userInfo: [NSLocalizedDescriptionKey: "Il pacchetto di installazione è incompleto."])
    }
    for (path, expected) in files {
        let url = root.appendingPathComponent(path).standardizedFileURL
        guard url.path.hasPrefix(root.standardizedFileURL.path + "/") else { throw NSError(domain: "MyNotes", code: 2) }
        let bytes = try Data(contentsOf: url)
        let digest = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
        guard digest == expected else { throw NSError(domain: "MyNotes", code: 2, userInfo: [NSLocalizedDescriptionKey: "Il controllo del pacchetto è fallito: " + path + ". Scarica di nuovo MyNotes."]) }
    }
}
private struct CommandResult { let code: Int32; let output: String }
private func command(_ executable: URL, _ arguments: [String], environment: [String: String]? = nil, allowFailure: Bool = false) throws -> CommandResult {
    let process = Process(), pipe = Pipe()
    process.executableURL = executable; process.arguments = arguments
    process.environment = environment ?? ProcessInfo.processInfo.environment; process.standardOutput = pipe; process.standardError = pipe
    try process.run()
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()
    let result = CommandResult(code: process.terminationStatus, output: String(data: data, encoding: .utf8) ?? "")
    if result.code != 0 && !allowFailure { throw NSError(domain: "MyNotes", code: Int(result.code), userInfo: [NSLocalizedDescriptionKey: String(result.output.suffix(2500))]) }
    return result
}

@MainActor final class SetupModel: ObservableObject {
    @Published var provider: String
    @Published var modelID = ""
    @Published var customModel = ""
    @Published var effortID = ""
    @Published var catalogChoices: [ModelChoice] = []
    @Published var catalogMessage = ""
    @Published var catalogLoading = false
    private var catalogRequestID = UUID()
    private var catalogTask: Task<Void, Never>?
    @Published var providerCatalog: ModelCatalog?
    @Published var selectionNotice: String?
    @Published var savingSelection = false
    private var loginPending = false
    @Published var interfaceLanguage = Locale.preferredLanguages.first?.hasPrefix("it") == true ? "it" : "en"
    private var rememberedModels: [String: String] = [:]
    private var rememberedEfforts: [String: String] = [:]
    @Published var busy = false
    @Published var message = "Scegli il tuo agente: prepariamo gli strumenti per registrazioni e appunti Word."
    @Published var error: String?
    @Published var progress = 0.0
    @Published var progressDetail = ""
    @Published var downloadNumbers: String?
    @Published var activity = ""
    @Published var waitingForNetwork = false
    @Published var determinateProgress = false
    private var startedAt: Date?
    @Published var diagnostics = ""
    @Published var providerFound = false
    @Published var prepared = false
    @Published var agentUsable = false
    @Published var componentsReady = false
    private var timer: Timer?
    private let home = FileManager.default.homeDirectoryForCurrentUser
    var runtime: URL { URL(fileURLWithPath: ProcessInfo.processInfo.environment["MYNOTES_HOME"] ?? home.appendingPathComponent("Library/Application Support/MyNotes").path) }
    var workspace: String {
        if let requested = ProcessInfo.processInfo.environment["MYNOTES_WORKSPACE"] { return requested }
        if let data = try? Data(contentsOf: runtime.appendingPathComponent("setup.json")),
           let saved = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let path = saved["workspace"] as? String { return path }
        return runtime.appendingPathComponent("workspace").path
    }
    var payload: URL { Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/release") }
    var downloadSummary: String {
        guard let data = try? Data(contentsOf: payload.appendingPathComponent("setup-components.json")),
              let manifest = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let components = manifest["components"] as? [[String: Any]] else { return "La prima configurazione scarica i componenti locali; le configurazioni successive li riusano." }
        let total = components.reduce(0) { value, component in
            value + (component["size_bytes"] as? Int ?? 0) + ((component["files"] as? [[String: Any]]) ?? []).reduce(0) { $0 + ($1["size_bytes"] as? Int ?? 0) }
        }
        return "Componenti locali: " + ByteCountFormatter.string(fromByteCount: Int64(total), countStyle: .file) + ", più Python e pacchetti. Serve Internet al primo setup; i download completati vengono riusati."
    }
    var launcher: URL { URL(fileURLWithPath: ProcessInfo.processInfo.environment["MYNOTES_PREFIX"] ?? home.appendingPathComponent(".local").path).appendingPathComponent("bin/mynotes") }
    var progressFile: URL { runtime.appendingPathComponent("setup-progress.json") }
    var selectedChoice: ModelChoice? { catalogChoices.first { $0.id == modelID && $0.provider == provider } }
    var selectedModel: String { ModelChoices.backend(choiceID: modelID, provider: provider, choices: catalogChoices, fallback: customModel).trimmingCharacters(in: .whitespacesAndNewlines) }
    var modelIsValid: Bool { ModelChoices.selectionValid(selectedChoice, effort: effortID) }
    var effortLabel: String {
        effortID == ModelChoices.pendingEffort ? "Da scegliere" : effortID.isEmpty ? "Automatico" : effortID
    }
    var modelChoices: [ModelChoice] { catalogChoices }
    var effortChoices: [String] {
        var choices = selectedChoice?.supportedEfforts ?? []
        if !effortID.isEmpty && effortID != ModelChoices.pendingEffort && !choices.contains(effortID) { choices.append(effortID) }
        return choices
    }
    func selectModel(_ id: String) {
        guard let choice = catalogChoices.first(where: { $0.id == id && $0.provider == provider && $0.selectable }) else { return }
        modelID = id; customModel = choice.model; effortID = ModelChoices.initialEffort(choice)
    }
    func loadModelPreferences(loadLanguage: Bool = false) {
        let settingsURL = URL(fileURLWithPath: workspace).appendingPathComponent("config/settings.json")
        let settings = (try? Data(contentsOf: settingsURL)).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        let providers = settings?["providers"] as? [String: Any]
        let configured = providers?["configured"] as? [String: [String: Any]]
        let saved = rememberedModels[provider] ?? configured?[provider]?["model"] as? String
        let savedEffort = rememberedEfforts[provider] ?? configured?[provider]?["effort"] as? String
        let presetsPath = URL(fileURLWithPath: ProcessInfo.processInfo.environment["MYNOTES_MODEL_PRESETS"] ?? payload.appendingPathComponent("src/mynotes/resources/model-presets.json").path)
        let catalog = ModelChoices.bundled(provider: provider, path: presetsPath, saved: saved, effort: savedEffort ?? "")
        catalogChoices = catalog.choices; catalogMessage = catalog.message; providerCatalog = catalog
        if let saved {
            let matching = catalogChoices.first { $0.provider == provider && $0.model == saved && (savedEffort == nil || $0.defaultEffort == savedEffort) }
                ?? catalogChoices.first { $0.provider == provider && $0.model == saved }
            modelID = matching?.id ?? saved; customModel = saved; effortID = savedEffort ?? ""
        } else if let initial = catalog.presets.first(where: { $0.id == catalog.defaultPreset && $0.provider == provider }) {
            modelID = initial.model; customModel = initial.model; effortID = savedEffort ?? initial.defaultEffort
            if !catalogChoices.contains(where: { $0.model == initial.model }) {
                catalogChoices.append(ModelChoice(id: initial.model, provider: provider, label: initial.label, model: initial.model))
            }
        } else {
            modelID = ""; customModel = ""; effortID = savedEffort ?? ""
        }
        if loadLanguage, let personal = settings?["personal"] as? [String: Any], let language = personal["interface_language"] as? String, ["it", "en"].contains(language) {
            interfaceLanguage = language
        }
    }
    func selectProvider(from previous: String) {
        rememberedModels[previous] = ModelChoices.backend(choiceID: modelID, provider: previous, choices: catalogChoices, fallback: customModel)
        rememberedEfforts[previous] = effortID
        loadModelPreferences(); refreshProvider(); loadSavedModelCatalog()
    }
    func selectPreset(_ preset: ModelChoice) {
        guard preset.selectable, let choice = catalogChoices.first(where: { $0.model == preset.model && $0.selectable }), choice.supportedEfforts.contains(preset.defaultEffort) else { return }
        modelID = choice.id; customModel = choice.model; effortID = preset.defaultEffort; selectionNotice = nil
    }
    func refreshAfterActivation() {
        guard !busy else { return }
        if loginPending {
            loginPending = false; refreshProvider()
        }
    }
    func cancelModelRefresh() {
        catalogTask?.cancel(); catalogRequestID = UUID(); catalogLoading = false
        catalogMessage = "Aggiornamento annullato. Il catalogo precedente è conservato."
    }
    func refreshModelCatalog() {
        readModelCatalog(refresh: true)
    }
    func loadSavedModelCatalog() {
        readModelCatalog(refresh: false)
    }
    private func readModelCatalog(refresh: Bool) {
        catalogTask?.cancel()
        let request = UUID(); catalogRequestID = request
        guard FileManager.default.isExecutableFile(atPath: launcher.path) else {
            catalogLoading = false; catalogMessage = "Prepara MyNotes per leggere il catalogo delle CLI."; return
        }
        let selected = provider, saved = selectedModel, savedEffort = effortID == ModelChoices.pendingEffort ? "" : effortID, executable = launcher
        catalogLoading = true; selectionNotice = nil
        catalogTask = Task {
            do {
                var arguments = ["models", "--provider", selected, "--saved", saved, "--saved-effort", savedEffort, "--json"]
                if refresh { arguments.append("--refresh") }
                let output = try await ModelCatalogCommand.read(executable, arguments: arguments)
                let catalog = try ModelChoices.decode(output)
                guard !Task.isCancelled, catalogRequestID == request, provider == selected, catalog.provider == selected else { return }
                let currentModel = selectedModel
                var choices = catalog.choices
                if !choices.contains(where: { $0.model == currentModel }) {
                    let removed = refresh && catalog.status == "ready"
                    choices.append(ModelChoice(id: currentModel, provider: selected, label: currentModel, model: currentModel,
                                               availability: removed ? "saved_unavailable" : "assumed", selectable: !removed))
                }
                catalogChoices = choices; providerCatalog = catalog; catalogMessage = catalog.message
                modelID = choices.first { $0.model == currentModel }?.id ?? currentModel
                catalogLoading = false
                if catalog.account.status == "connected" { agentUsable = true }
            } catch {
                guard catalogRequestID == request, !Task.isCancelled else { return }
                catalogMessage = error is CancellationError ? "Tempo di aggiornamento scaduto. Il catalogo precedente è conservato." : error.localizedDescription
                catalogLoading = false
            }
        }
    }
    func saveSelection() {
        guard modelIsValid, !savingSelection else { return }
        let executable = launcher, arguments = ["preferences", "--workspace", workspace, "--provider", provider, "--model", selectedModel, "--effort", effortID, "--language", interfaceLanguage]
        let selected = provider, model = selectedModel, effort = effortID
        savingSelection = true; selectionNotice = nil; error = nil
        Task {
            do {
                _ = try await ModelCatalogCommand.read(executable, arguments: arguments)
                rememberedModels[selected] = model; rememberedEfforts[selected] = effort
                selectionNotice = "Scelta salvata per le prossime chat e generazioni."
            } catch { self.error = error.localizedDescription }
            savingSelection = false
        }
    }
    func savePreferences() throws {
        guard modelIsValid else { throw NSError(domain: "MyNotes", code: 1, userInfo: [NSLocalizedDescriptionKey: "Scegli un modello e un effort compatibili con il tuo agente."]) }
        _ = try command(launcher, ["preferences", "--workspace", workspace, "--provider", provider, "--model", selectedModel, "--effort", effortID, "--language", interfaceLanguage])
        rememberedModels[provider] = selectedModel; rememberedEfforts[provider] = effortID
    }
    var installURL: URL {
        URL(string: provider == "codex" ? "https://developers.openai.com/codex/cli" : provider == "claude" ? "https://code.claude.com/docs/en/setup" : "https://antigravity.google/docs/cli/")!
    }
    init() {
        let environment = ProcessInfo.processInfo.environment
        let root = URL(fileURLWithPath: environment["MYNOTES_HOME"] ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/MyNotes").path)
        let saved = (try? Data(contentsOf: root.appendingPathComponent("setup.json"))).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        let savedWorkspace = environment["MYNOTES_WORKSPACE"] ?? saved?["workspace"] as? String ?? root.appendingPathComponent("workspace").path
        let settings = (try? Data(contentsOf: URL(fileURLWithPath: savedWorkspace).appendingPathComponent("config/settings.json"))).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        let savedProviders = settings?["providers"] as? [String: Any]
        let requested = environment["MYNOTES_PROVIDER"] ?? savedProviders?["default"] as? String ?? UserDefaults.standard.string(forKey: "MyNotesProvider") ?? saved?["provider"] as? String ?? "codex"
        provider = ["codex", "agy", "claude"].contains(requested) ? requested : "codex"
        loadModelPreferences(loadLanguage: true)
        prepared = receiptIsCurrent()
        readProgress()
        refreshProvider()
        loadSavedModelCatalog()
    }
    func receiptIsCurrent() -> Bool {
        guard FileManager.default.isExecutableFile(atPath: launcher.path),
              let launcherText = try? String(contentsOf: launcher, encoding: .utf8), launcherText.contains("MyNotes managed launcher"),
              let data = try? Data(contentsOf: runtime.appendingPathComponent("setup.json")),
              let receipt = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
        return receipt["version"] as? String == Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String
    }
    func readProgress() {
        guard busy, let startedAt,
              let data = try? Data(contentsOf: progressFile),
              let state = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let attributes = try? FileManager.default.attributesOfItem(atPath: progressFile.path),
              let modifiedAt = attributes[.modificationDate] as? Date,
              let presentation = SetupProgressPresentation.read(state, modifiedAt: modifiedAt, startedAt: startedAt, now: Date()) else { return }
        message = presentation.title
        progressDetail = presentation.detail
        downloadNumbers = presentation.downloadNumbers
        activity = presentation.activity
        waitingForNetwork = presentation.waitingForNetwork
        determinateProgress = presentation.fraction != nil
        progress = presentation.fraction ?? 0
    }
    func refreshProvider() {
        UserDefaults.standard.set(provider, forKey: "MyNotesProvider")
        let selected = provider
        providerFound = false; agentUsable = false; diagnostics = ""
        Task {
            let found = await Task.detached { () -> Bool in
                let result = try? command(URL(fileURLWithPath: "/bin/zsh"), ["-lic", "command -v " + selected])
                return result?.output.split(separator: "\n").last.map { FileManager.default.isExecutableFile(atPath: String($0)) } ?? false
            }.value
            guard selected == provider else { return }
            providerFound = found
            if prepared { await refreshDiagnostics() }
        }
    }
    func refreshDiagnostics() async {
        let executable = launcher, selected = provider
        let result = await Task.detached { () -> CommandResult? in
            var environment = ProcessInfo.processInfo.environment
            let login = try? command(URL(fileURLWithPath: "/bin/zsh"), ["-lic", "print -r -- $PATH"])
            if let path = login?.output.split(separator: "\n").last { environment["PATH"] = String(path) }
            return try? command(executable, ["doctor", "--json", "--provider", selected], environment: environment, allowFailure: true)
        }.value
        guard selected == provider else { return }
        guard let text = result?.output, let data = text.data(using: .utf8),
              let report = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        let components = report["checks"] as? [String: [String: Any]] ?? [:]
        let status = components["agent"]?["status"] as? String ?? "unverified"
        agentUsable = ["ready", "unverified"].contains(status)
        componentsReady = ["installation", "transcription", "word"].allSatisfy { components[$0]?["status"] as? String == "ready" }
        let names = ["installation": "Installazione", "agent": "Agente", "transcription": "Trascrizione", "word": "Word", "capture": "Capture"]
        diagnostics = ["installation", "agent", "transcription", "word", "capture"].compactMap { key in
            guard let check = components[key] else { return nil }
            let status = check["status"] as? String ?? "unverified"
            let labels = ["ready": "Pronto", "unverified": "Da provare nella chat", "missing": "Da preparare", "optional": "Puoi configurarlo nella chat", "login_required": "Devi accedere", "incompatible": "Devi aggiornare l’agente"]
            var action = ""
            if key == "agent" && status == "missing" { action = "Premi Installa…, poi ricontrolla." }
            if status == "login_required" { action = "Premi Accedi…, poi ricontrolla." }
            if status == "incompatible" { action = "Premi Installa… per le istruzioni di aggiornamento." }
            if key != "agent" && status == "missing" { action = "Premi Prepara MyNotes o Riprova." }
            return "\(names[key] ?? key): \(labels[status] ?? "Da verificare")" + (action.isEmpty ? "" : "\n\(action)")
        }.joined(separator: "\n\n")
    }
    func prepare() {
        guard !busy else { return }
        busy = true; error = nil; progress = 0; startedAt = Date()
        progressDetail = ""; downloadNumbers = nil; activity = ""; waitingForNetwork = false; determinateProgress = false
        message = "Controllo il pacchetto e preparo Python privato. I download parziali vengono conservati per la ripresa."
        let release = payload, selected = provider, appHome = runtime, progressPath = progressFile, selectedWorkspace = workspace
        timer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in Task { @MainActor in self?.readProgress() } }
        Task {
            do {
                try await Task.detached {
                    guard !Bundle.main.bundleURL.path.hasPrefix("/Volumes/") else { throw NSError(domain: "MyNotes", code: 1, userInfo: [NSLocalizedDescriptionKey: "Trascina prima MyNotes in Applicazioni, poi aprila da lì."]) }
                    guard ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 14 else { throw NSError(domain: "MyNotes", code: 1, userInfo: [NSLocalizedDescriptionKey: "Serve macOS 14 o successivo."]) }
                    let arch = try command(URL(fileURLWithPath: "/usr/bin/uname"), ["-m"])
                    guard arch.output.trimmingCharacters(in: .whitespacesAndNewlines) == "arm64" else { throw NSError(domain: "MyNotes", code: 1, userInfo: [NSLocalizedDescriptionKey: "Questa versione supporta Mac Apple Silicon."]) }
                    var existingParent = appHome
                    while !FileManager.default.fileExists(atPath: existingParent.path) { existingParent.deleteLastPathComponent() }
                    let attributes = try FileManager.default.attributesOfFileSystem(forPath: existingParent.path)
                    guard (attributes[.systemFreeSize] as? NSNumber)?.int64Value ?? 0 > 5_000_000_000 else { throw NSError(domain: "MyNotes", code: 1, userInfo: [NSLocalizedDescriptionKey: "Libera almeno 5 GB per preparare componenti e modelli."]) }
                    try verifyPayload(release)
                    try FileManager.default.createDirectory(at: appHome, withIntermediateDirectories: true)
                    let manifestData = try Data(contentsOf: release.appendingPathComponent("setup-components.json"))
                    let manifest = try JSONSerialization.jsonObject(with: manifestData) as? [String: Any] ?? [:]
                    let version = manifest["python_version"] as? String ?? "3.12.13"
                    var env = ProcessInfo.processInfo.environment
                    env["MYNOTES_HOME"] = appHome.path
                    env["UV_PYTHON_INSTALL_DIR"] = appHome.appendingPathComponent("app/python").path
                    env["UV_CACHE_DIR"] = appHome.appendingPathComponent("cache/uv").path
                    env["PATH"] = release.appendingPathComponent("bin").path + ":" + (env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin")
                    let login = try? command(URL(fileURLWithPath: "/bin/zsh"), ["-lic", "print -r -- $PATH"])
                    if let path = login?.output.split(separator: "\n").last { env["PATH"] = release.appendingPathComponent("bin").path + ":" + String(path) }
                    let uv = release.appendingPathComponent("bin/uv")
                    _ = try command(uv, ["python", "install", "--no-bin", version], environment: env)
                    let found = try command(uv, ["python", "find", "--managed-python", version], environment: env)
                    guard let python = found.output.split(separator: "\n").last else { throw NSError(domain: "MyNotes", code: 2) }
                    _ = try command(URL(fileURLWithPath: String(python)), [release.appendingPathComponent("scripts/setup_runtime.py").path, "--release-root", release.path, "--provider", selected, "--workspace", selectedWorkspace, "--progress-file", progressPath.path], environment: env)
                }.value
                prepared = receiptIsCurrent()
                if modelIsValid { try savePreferences() }
                loadSavedModelCatalog()
                message = "Componenti preparati. Apri la chat: MyNotes si presenterà e ti chiederà brevemente il tuo background."
                progress = 1
                await refreshDiagnostics()
            } catch { self.error = error.localizedDescription; message = "Il setup conserva il progresso. Risolvi il problema e premi Riprova." }
            timer?.invalidate(); busy = false
        }
    }
    func openChat() {
        do {
            try savePreferences()
            let path = runtime.appendingPathComponent("Open MyNotes.command")
            let script = "#!/bin/zsh\nexport MYNOTES_HOME=" + quoted(runtime.path) + "\nexport MYNOTES_WORKSPACE=" + quoted(workspace) + "\nexport PATH=" + quoted(launcher.deletingLastPathComponent().path) + ":\"$PATH\"\ncd " + quoted(runtime.path) + "\nexec " + quoted(launcher.path) + " " + quoted(provider) + "\n"
            try script.write(to: path, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: path.path)
            NSWorkspace.shared.open(path)
        } catch { self.error = error.localizedDescription }
    }
    func openLogin() {
        loginPending = true
        do {
            let path = runtime.appendingPathComponent("Login MyNotes.command")
            try FileManager.default.createDirectory(at: runtime, withIntermediateDirectories: true)
            let login = provider == "codex" ? "codex login" : provider == "claude" ? "claude auth login" : "agy"
            try ("#!/bin/zsh\n" + login + "\n").write(to: path, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: path.path)
            NSWorkspace.shared.open(path)
        } catch { self.error = error.localizedDescription }
    }
}

private struct MyNotesRootView: View {
    @CaptureViewState<String> private var section = ProcessInfo.processInfo.environment["MYNOTES_INITIAL_SECTION"] == "capture" ? "capture" : "notes"
    var body: some View {
        TabView(selection: $section) {
            MyNotesHomeView(active: section == "notes").tabItem { Label("MyNotes", systemImage: "book.closed") }.tag("notes")
            CaptureView(active: section == "capture").tabItem { Label("Capture", systemImage: "mic") }.tag("capture")
        }.frame(minWidth: 1000, minHeight: 700).background(CaptureWindowLease().frame(width: 0, height: 0))
    }
}
@main struct MyNotesSetup: App {
    var body: some Scene {
        Window("MyNotes", id: "mynotes-main") { MyNotesRootView() }.defaultSize(width: 1040, height: 780).windowResizability(.contentMinSize)
    }
}
