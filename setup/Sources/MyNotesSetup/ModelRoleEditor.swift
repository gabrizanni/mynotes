import AppKit
import SwiftUI

/// Each activity owns its catalog task and availability, independently of the other role.
struct ModelRoleEditor: View {
    let title: String
    let detail: String
    let shared: ModelRoleSelection
    let launcher: URL
    let presets: URL
    @Binding var selection: ModelRoleSelection?
    @Binding var valid: Bool
    @CaptureViewState<[ModelChoice]> private var choices: [ModelChoice] = []
    @CaptureViewState<Bool> private var loading = false
    @CaptureViewState<UUID> private var requestID = UUID()
    @CaptureViewState<String> private var message = ""
    @CaptureViewState<String> private var readiness = "Da verificare"
    private var effective: ModelRoleSelection { selection ?? shared }
    private var custom: Binding<Bool> {
        Binding(get: { selection != nil }, set: { selection = $0 ? shared : nil; valid = true })
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.headline)
            Text(detail).font(.caption).foregroundStyle(.secondary)
            Toggle("Usa il predefinito", isOn: Binding(get: { !custom.wrappedValue }, set: { custom.wrappedValue = !$0 }))
            if selection != nil {
                Picker("Agente per " + title.lowercased(), selection: Binding(get: { effective.provider }, set: {
                    let catalog = ModelChoices.bundled(provider: $0, path: presets)
                    let first = catalog.presets.first { $0.id == catalog.defaultPreset }
                    selection = ModelRoleSelection(provider: $0, model: first?.model ?? "", effort: first?.defaultEffort ?? "")
                })) {
                    Text("Codex").tag("codex"); Text("Agy").tag("agy"); Text("Claude Code").tag("claude")
                }.pickerStyle(.segmented)
                HStack {
                    Menu {
                        ForEach(choices.filter { !$0.model.isEmpty && $0.selectable }) { choice in
                            Menu(choice.label) {
                                Button("Automatico") { choose(choice, effort: "") }
                                ForEach(choice.supportedEfforts, id: \.self) { effort in
                                    Button(effort.capitalized) { choose(choice, effort: effort) }
                                }
                            }
                        }
                        Divider()
                        Button("Predefinito dell’agente") {
                            selection = ModelRoleSelection(provider: effective.provider, model: "", effort: ""); validate()
                        }
                    } label: {
                        Text(effective.model.isEmpty ? "Predefinito dell’agente" : effective.model)
                        Text(effective.effort.isEmpty ? "Automatico" : effective.effort.capitalized).foregroundStyle(.secondary)
                    }.menuStyle(.borderlessButton).accessibilityLabel("Modello ed effort per " + title.lowercased())
                    Button("Aggiorna modelli") { Task { await load(refresh: true) } }.buttonStyle(.borderless).disabled(loading)
                    if loading { ProgressView().controlSize(.small) }
                }
                if !valid {
                    Text("Il modello o l’effort non è disponibile nell’elenco corrente. Scegli una combinazione valida.").font(.caption).foregroundStyle(.orange)
                }
                HStack {
                    Text(readiness).font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("Installa / guida…") { if let url = URL(string: installURL) { NSWorkspace.shared.open(url) } }
                    Button("Accedi…") { login() }
                    Button("Ricontrolla") { Task { await checkAgent() } }
                }.controlSize(.small)
                if !message.isEmpty { Text(message).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
            } else {
                Text(shared.summary).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            }
        }
        .task(id: selection?.provider) { if selection != nil { await load(refresh: false); await checkAgent() } }
        .onChange(of: selection) { _, _ in validate() }
        .onDisappear { requestID = UUID() }
    }
    private var installURL: String {
        ["codex": "https://developers.openai.com/codex/cli/", "agy": "https://antigravity.google/download", "claude": "https://code.claude.com/docs/en/setup"][effective.provider] ?? ""
    }
    private func choose(_ choice: ModelChoice, effort: String) {
        selection = ModelRoleSelection(provider: effective.provider, model: choice.model, effort: effort); validate()
    }
    private func validate() {
        guard let selection else { valid = true; return }
        let choice = choices.first { $0.model == selection.model }
        valid = selection.isValid && (choice.map { ModelChoices.selectionValid($0, effort: selection.effort) } ?? true)
    }
    @MainActor private func load(refresh: Bool) async {
        let token = UUID(); requestID = token
        let selected = effective
        choices = ModelChoices.bundled(provider: selected.provider, path: presets, saved: selected.model, effort: selected.effort).choices
        loading = true; message = ""
        defer { if requestID == token { loading = false } }
        do {
            var args = ["models", "--provider", selected.provider, "--saved", selected.model, "--saved-effort", selected.effort, "--json"]
            if refresh { args.append("--refresh") }
            let data = try await ModelCatalogCommand.read(launcher, arguments: args)
            let catalog = try ModelChoices.decode(data)
            guard !Task.isCancelled, requestID == token, selection?.provider == selected.provider else { return }
            choices = catalog.choices
            // A model selected while the read was running must remain visible.
            if let current = selection, !choices.contains(where: { $0.model == current.model }) {
                choices += ModelChoices.bundled(provider: current.provider, path: presets, saved: current.model, effort: current.effort).choices.filter { $0.model == current.model }
            }
            message = catalog.message; validate()
        } catch {
            guard !Task.isCancelled, requestID == token else { return }
            message = error.localizedDescription; validate()
        }
    }
    @MainActor private func checkAgent() async {
        let selected = effective.provider
        do {
            let data = try await ModelCatalogCommand.read(launcher, arguments: ["doctor", "--provider", selected, "--json"])
            let report = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            let checks = report?["checks"] as? [String: [String: Any]]
            guard selection?.provider == selected, !Task.isCancelled else { return }
            let status = checks?["agent"]?["status"] as? String ?? "unverified"
            readiness = status == "ready" ? "Agente pronto" : (checks?["agent"]?["action"] as? String ?? "Agente da verificare")
        } catch { if selection?.provider == selected { readiness = "Verifica non disponibile: " + error.localizedDescription } }
    }
    private func login() {
        // Use the ordinary MyNotes auth command; no passwords or prompts pass through SwiftUI.
        let text = "#!/bin/zsh\nexec " + quote(launcher.path) + " provider-login --provider " + quote(effective.provider) + "\n"
        let script = FileManager.default.temporaryDirectory.appendingPathComponent("MyNotes Login-" + UUID().uuidString + ".command")
        do {
            try ("#!/bin/zsh\n/bin/rm -f -- \"$0\"\n" + text.components(separatedBy: "\n").dropFirst().joined(separator: "\n")).write(to: script, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: script.path)
            Task { do { try await ChatTerminal.open(script: script, directory: script.deletingLastPathComponent()) } catch { message = error.localizedDescription } }
        } catch { message = error.localizedDescription }
    }
    private func quote(_ value: String) -> String { "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'" }
}
