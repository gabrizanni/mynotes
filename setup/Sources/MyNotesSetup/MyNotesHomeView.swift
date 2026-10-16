import AppKit
import SwiftUI
import Combine

struct MyNotesHomeView: View {
    @StateObject private var model = SetupModel()
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.scenePhase) private var scenePhase
    @CaptureViewState<Bool> private var setupExpanded = false
    let active: Bool
    private let preview = ProcessInfo.processInfo.environment["MYNOTES_UI_PREVIEW"] == "1"
    private var brand: Color { colorScheme == .dark ? Color(red: 0.55, green: 0.78, blue: 1) : Color(red: 0.086, green: 0.23, blue: 0.40) }
    private var modelLabel: String { model.selectedChoice?.label ?? (model.selectedModel.isEmpty ? "Predefinito" : model.selectedModel) }
    private func effortLabel(_ value: String) -> String { value.isEmpty ? "Automatico" : value.capitalized }
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            header
            VStack(alignment: .leading, spacing: 18) {
                Text("Chat e appunti").font(.title2.bold())
                Picker("Agente", selection: $model.provider) {
                    Text("Codex").tag("codex")
                    Text("Agy").tag("agy")
                    Text("Claude Code").tag("claude")
                }.pickerStyle(.segmented).disabled(model.busy || model.savingSelection)
                HStack(spacing: 12) {
                    modelMenu
                    Button { model.refreshModelCatalog() } label: {
                        Label("Aggiorna modelli", systemImage: "arrow.clockwise")
                    }.buttonStyle(.borderless).controlSize(.small).disabled(model.catalogLoading || model.busy)
                    if model.catalogLoading { ProgressView().controlSize(.small) }
                    Spacer(minLength: 0)
                }
                if let choice = model.selectedChoice, !choice.selectable {
                    Text("Il modello scelto non compare nell’ultimo aggiornamento. Scegline un altro dal menu.").font(.caption).foregroundStyle(.orange)
                }
                if let catalog = model.providerCatalog, !["ready", "stale", "no_catalog"].contains(catalog.status) {
                    Text(model.catalogMessage).font(.caption).foregroundStyle(.secondary)
                }
                Divider()
                Picker("Lingua della chat", selection: $model.interfaceLanguage) {
                    Text("Italiano").tag("it"); Text("English").tag("en")
                }.frame(maxWidth: 280).disabled(model.busy)
                HStack(spacing: 10) {
                    Label(model.agentUsable ? "Agente pronto" : model.providerFound ? "Agente installato" : "Agente da installare", systemImage: model.agentUsable ? "checkmark.circle" : "terminal").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button(model.providerFound ? "Aggiorna CLI…" : "Installa…") { NSWorkspace.shared.open(model.installURL) }
                    Button("Accedi…") { model.openLogin() }.disabled(!model.providerFound)
                    Button("Ricontrolla agente") { model.refreshProvider() }
                }.controlSize(.small).disabled(model.busy)
            }.padding(20).background(Color(nsColor: .controlBackgroundColor).opacity(0.5), in: RoundedRectangle(cornerRadius: 14))
            Text("La scelta vale per le nuove chat e gli appunti. L’elenco cambia solo quando premi Aggiorna modelli.").font(.caption).foregroundStyle(.secondary)
            Spacer(minLength: 0)
            localTools
            footer
        }.padding(22).tint(brand).background(Color(nsColor: .windowBackgroundColor))
        .onChange(of: model.provider) { previous, _ in model.selectProvider(from: previous) }
        .onChange(of: active) { _, value in if value { model.refreshAfterActivation() } }
        .onChange(of: scenePhase) { _, value in if value == .active && active { model.refreshAfterActivation() } }
    }
    private var modelMenu: some View {
        Menu {
            ForEach(model.modelChoices.filter { !$0.model.isEmpty && $0.selectable }) { choice in
                Menu {
                    Button {
                        model.selectModel(choice.id); model.effortID = ""
                    } label: {
                        if model.modelID == choice.id && model.effortID.isEmpty { Label("Automatico", systemImage: "checkmark") } else { Text("Automatico") }
                    }
                    ForEach(choice.supportedEfforts, id: \.self) { effort in
                        Button {
                            if model.modelID != choice.id { model.selectModel(choice.id) }
                            model.effortID = effort
                        } label: {
                            if model.modelID == choice.id && model.effortID == effort { Label(effortLabel(effort), systemImage: "checkmark") } else { Text(effortLabel(effort)) }
                        }
                    }
                } label: {
                    if model.modelID == choice.id { Label(choice.label, systemImage: "checkmark") } else { Text(choice.label) }
                }
            }
            Divider()
            Button("Predefinito dell’agente") { model.selectModel("") }
        } label: {
            HStack(spacing: 7) {
                Text(modelLabel).fontWeight(.medium).lineLimit(1)
                Text(effortLabel(model.effortID)).foregroundStyle(.secondary)
            }
        }.menuStyle(.borderlessButton).fixedSize(horizontal: true, vertical: false)
            .disabled(model.busy || model.savingSelection).accessibilityLabel("Modello ed effort")
    }
    private var header: some View {
        HStack(spacing: 14) {
            if let path = Bundle.main.url(forResource: "MyNotesLogo", withExtension: "png"), let logo = NSImage(contentsOf: path) {
                Image(nsImage: logo).resizable().scaledToFit().frame(width: 48, height: 48).accessibilityHidden(true)
            }
            VStack(alignment: .leading, spacing: 3) {
                Text("MyNotes").font(.largeTitle.bold()).foregroundStyle(brand)
                Text("Scegli il tuo agente. Dai forma alle tue note.").foregroundStyle(.secondary)
            }
            Spacer()
            if preview { Label("Anteprima locale", systemImage: "eye").font(.caption).foregroundStyle(.secondary) }
            Button("Guida", systemImage: "questionmark.circle") { NSWorkspace.shared.open(model.payload.appendingPathComponent("docs/Guida.html")) }.buttonStyle(.borderless)
        }
    }
    private var localTools: some View {
        VStack(alignment: .leading, spacing: 8) {
            if model.busy {
                Text(model.message).font(.callout)
                if model.determinateProgress { ProgressView(value: model.progress) } else { ProgressView().controlSize(.small) }
                if !model.progressDetail.isEmpty { Text(model.progressDetail).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
                if let numbers = model.downloadNumbers { Text(numbers).font(.caption.monospacedDigit()) }
                if model.waitingForNetwork { Text("Download in attesa. Il progresso parziale è conservato.").font(.caption).foregroundStyle(.orange) }
            } else {
                DisclosureGroup(model.prepared ? "Strumenti locali · " + (model.componentsReady ? "pronti" : "da verificare") : "Strumenti locali · da preparare", isExpanded: $setupExpanded) {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(model.downloadSummary).font(.caption).foregroundStyle(.secondary)
                            Text(model.message).font(.caption)
                            if !model.diagnostics.isEmpty { Text(model.diagnostics).font(.caption).textSelection(.enabled) }
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    }.frame(maxHeight: 110)
                }.font(.callout)
            }
            if let error = model.error { Text(error).font(.caption).foregroundStyle(.red).lineLimit(3).textSelection(.enabled) }
            if let notice = model.selectionNotice { Label(notice, systemImage: "checkmark.circle").font(.caption).foregroundStyle(.secondary) }
        }
    }
    private var footer: some View {
        VStack(spacing: 12) {
            Divider()
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("PROSSIMA CHAT E NUOVI APPUNTI").font(.caption2.bold()).foregroundStyle(.secondary)
                    Text((model.selectedChoice?.label ?? model.selectedModel) + " · " + model.effortLabel).font(.callout).lineLimit(1)
                }
                Spacer(minLength: 8)
                if model.savingSelection { ProgressView().controlSize(.small) }
                Button("Salva scelta") { model.saveSelection() }.disabled(!model.modelIsValid || model.savingSelection || model.busy)
                Button(model.error != nil ? "Riprova setup" : model.prepared ? "Controlla strumenti" : "Prepara MyNotes") { model.prepare() }.disabled(model.busy || preview)
                Button("Apri la chat", systemImage: "arrow.up.right") { model.openChat() }.buttonStyle(.borderedProminent)
                    .disabled(model.busy || model.savingSelection || !model.modelIsValid || !model.prepared || !model.providerFound || !model.agentUsable || !model.componentsReady || preview)
            }
        }
    }
}
