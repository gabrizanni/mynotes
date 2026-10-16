import Foundation

struct ModelChoice: Identifiable, Equatable, Decodable {
    let id: String
    let provider: String
    let label: String
    let model: String
    let defaultEffort: String
    let supportedEfforts: [String]
    var availability: String
    var selectable: Bool
    var source: String
    let effortScope: String
    let isDefault: Bool
    let description: String
    enum CodingKeys: String, CodingKey {
        case id, provider, label, model, availability, selectable, source, description
        case defaultEffort = "default_effort", supportedEfforts = "supported_efforts"
        case effortScope = "effort_scope", isDefault = "is_default"
    }
    init(id: String, provider: String, label: String, model: String, defaultEffort: String = "", supportedEfforts: [String] = [], availability: String = "assumed", selectable: Bool = true, source: String = "saved_preference", effortScope: String = "model", isDefault: Bool = false, description: String = "") {
        self.id = id; self.provider = provider; self.label = label; self.model = model
        self.defaultEffort = defaultEffort; self.supportedEfforts = supportedEfforts
        self.availability = availability; self.selectable = selectable; self.source = source
        self.effortScope = effortScope; self.isDefault = isDefault; self.description = description
    }
    init(from decoder: Decoder) throws {
        let v = try decoder.container(keyedBy: CodingKeys.self)
        id = try v.decode(String.self, forKey: .id); provider = try v.decode(String.self, forKey: .provider)
        label = try v.decode(String.self, forKey: .label); model = try v.decode(String.self, forKey: .model)
        defaultEffort = try v.decodeIfPresent(String.self, forKey: .defaultEffort) ?? ""
        supportedEfforts = try v.decodeIfPresent([String].self, forKey: .supportedEfforts) ?? []
        availability = try v.decodeIfPresent(String.self, forKey: .availability) ?? "saved_unavailable"
        selectable = try v.decodeIfPresent(Bool.self, forKey: .selectable) ?? false
        source = try v.decodeIfPresent(String.self, forKey: .source) ?? "saved_preference"
        effortScope = try v.decodeIfPresent(String.self, forKey: .effortScope) ?? "model"
        isDefault = try v.decodeIfPresent(Bool.self, forKey: .isDefault) ?? false
        description = try v.decodeIfPresent(String.self, forKey: .description) ?? ""
    }
    var statusLabel: String {
        switch availability {
        case "listed": return "Esposto dalla CLI"
        case "cached": return "Catalogo precedente"
        case "provider_default": return "Scelto dalla CLI"
        default: return "Non disponibile nel catalogo"
        }
    }
    var defaultEffortLabel: String { defaultEffort.isEmpty ? "Non comunicato dalla CLI" : defaultEffort }
}

struct ModelAccount: Decodable {
    var status: String; var kind: String; var label: String; var plan: String
    init(status: String = "unknown", kind: String = "", label: String = "", plan: String = "") {
        self.status = status; self.kind = kind; self.label = label; self.plan = plan
    }
    init(from decoder: Decoder) throws {
        let v = try decoder.container(keyedBy: CodingKeys.self)
        status = try v.decodeIfPresent(String.self, forKey: .status) ?? "unknown"
        kind = try v.decodeIfPresent(String.self, forKey: .kind) ?? ""
        label = try v.decodeIfPresent(String.self, forKey: .label) ?? ""
        plan = try v.decodeIfPresent(String.self, forKey: .plan) ?? ""
    }
    enum CodingKeys: String, CodingKey { case status, kind, label, plan }
    var displayLabel: String { [label, plan.isEmpty ? nil : plan.capitalized].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ") }
}

struct ModelQuota: Decodable { let group: String; let window: String; let remaining: String; let resetsAt: String
    enum CodingKeys: String, CodingKey { case group, window, remaining; case resetsAt = "resets_at" }
}
struct ModelCatalog: Decodable {
    let provider: String; let choices: [ModelChoice]; let presets: [ModelChoice]; let defaultPreset: String; let message: String
    let status: String; let account: ModelAccount; let source: String; let fetchedAt: String; let providerUpdatedAt: String
    let cliVersion: String; let quota: [ModelQuota]; let cacheWarning: String
    enum CodingKeys: String, CodingKey {
        case provider, choices, presets, message, status, account, source, quota
        case defaultPreset = "default_preset", fetchedAt = "fetched_at", providerUpdatedAt = "provider_updated_at"
        case cliVersion = "cli_version", schemaVersion = "schema_version", cacheWarning = "cache_warning"
    }
    init(provider: String, choices: [ModelChoice], presets: [ModelChoice] = [], defaultPreset: String = "", message: String = "", status: String = "no_catalog", account: ModelAccount = ModelAccount(), source: String = "unavailable", fetchedAt: String = "", providerUpdatedAt: String = "", cliVersion: String = "", quota: [ModelQuota] = [], cacheWarning: String = "") {
        self.provider = provider; self.choices = choices; self.presets = presets; self.defaultPreset = defaultPreset; self.message = message
        self.status = status; self.account = account; self.source = source; self.fetchedAt = fetchedAt; self.providerUpdatedAt = providerUpdatedAt
        self.cliVersion = cliVersion; self.quota = quota; self.cacheWarning = cacheWarning
    }
    init(from decoder: Decoder) throws {
        let v = try decoder.container(keyedBy: CodingKeys.self)
        guard try v.decodeIfPresent(Int.self, forKey: .schemaVersion) == 2 else {
            throw NSError(domain: "MyNotes", code: 2, userInfo: [NSLocalizedDescriptionKey: "Aggiorna i componenti MyNotes: il catalogo usa un formato precedente."])
        }
        provider = try v.decode(String.self, forKey: .provider); choices = try v.decode([ModelChoice].self, forKey: .choices)
        presets = try v.decodeIfPresent([ModelChoice].self, forKey: .presets) ?? []
        defaultPreset = try v.decodeIfPresent(String.self, forKey: .defaultPreset) ?? ""
        message = try v.decodeIfPresent(String.self, forKey: .message) ?? ""
        status = try v.decodeIfPresent(String.self, forKey: .status) ?? "no_catalog"
        account = try v.decodeIfPresent(ModelAccount.self, forKey: .account) ?? ModelAccount()
        source = try v.decodeIfPresent(String.self, forKey: .source) ?? "unavailable"
        fetchedAt = try v.decodeIfPresent(String.self, forKey: .fetchedAt) ?? ""
        providerUpdatedAt = try v.decodeIfPresent(String.self, forKey: .providerUpdatedAt) ?? ""
        cliVersion = try v.decodeIfPresent(String.self, forKey: .cliVersion) ?? ""
        quota = try v.decodeIfPresent([ModelQuota].self, forKey: .quota) ?? []
        cacheWarning = try v.decodeIfPresent(String.self, forKey: .cacheWarning) ?? ""
        guard choices.allSatisfy({ $0.provider == provider && ModelChoices.valid($0.model) }), Set(choices.map(\.id)).count == choices.count else {
            throw NSError(domain: "MyNotes", code: 2, userInfo: [NSLocalizedDescriptionKey: "Il catalogo contiene modelli incoerenti con l’agente."])
        }
    }
}

private struct PresetCatalog: Decodable { let presets: [ModelChoice]; let defaults: [String: String] }
enum ModelChoices {
    static let custom = "__custom__"
    static let pendingEffort = "__choose_effort__" // Legacy saved drafts only.
    static func initialEffort(_ choice: ModelChoice) -> String { choice.defaultEffort }
    static func selectionValid(_ choice: ModelChoice?, effort: String) -> Bool {
        guard let choice, choice.selectable, valid(choice.model), effort != pendingEffort else { return false }
        return effort.isEmpty || choice.supportedEfforts.contains(effort)
    }
    static func backend(choiceID: String, provider: String, choices: [ModelChoice], fallback: String) -> String {
        choices.first { $0.id == choiceID && $0.provider == provider }?.model ?? fallback
    }
    static func valid(_ value: String) -> Bool {
        value.isEmpty || value.range(of: "^[A-Za-z0-9][A-Za-z0-9._:/\\[\\]-]{0,199}$", options: .regularExpression) == value.startIndex..<value.endIndex
    }
    static func decode(_ data: Data) throws -> ModelCatalog { try JSONDecoder().decode(ModelCatalog.self, from: data) }
    static func bundled(provider: String, path: URL, saved: String? = nil, effort: String = "") -> ModelCatalog {
        let data = (try? Data(contentsOf: path)).flatMap { try? JSONDecoder().decode(PresetCatalog.self, from: $0) }
        let presets = (data?.presets ?? []).filter { $0.provider == provider }.map { choice in
            var row = choice; row.selectable = true; row.availability = "assumed"; return row
        }
        var choices = presets.map { choice in
            ModelChoice(id: choice.model, provider: provider, label: choice.label, model: choice.model, defaultEffort: choice.defaultEffort,
                        supportedEfforts: choice.supportedEfforts, availability: "assumed", source: "bundled_presets")
        }
        choices.append(ModelChoice(id: "", provider: provider, label: "Predefinito", model: "", availability: "provider_default", source: "provider_default"))
        if let saved, !saved.isEmpty, valid(saved), !choices.contains(where: { $0.model == saved }) {
            choices.append(ModelChoice(id: saved, provider: provider, label: saved, model: saved, supportedEfforts: effort.isEmpty ? [] : [effort]))
        }
        return ModelCatalog(provider: provider, choices: choices, presets: presets, defaultPreset: data?.defaults[provider] ?? "", message: "Elenco salvato. Aggiornamento soltanto su richiesta.")
    }
}
