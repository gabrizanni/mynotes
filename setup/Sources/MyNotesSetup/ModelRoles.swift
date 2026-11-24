import Foundation

struct ModelRoleSelection: Codable, Equatable {
    var provider: String
    var model: String
    var effort: String
    var summary: String {
        let agent = ["codex": "Codex", "agy": "Agy", "claude": "Claude Code"][provider] ?? provider
        return agent + " · " + (model.isEmpty ? "Predefinito dell’agente" : model) + " · " + (effort.isEmpty ? "Automatico" : effort.capitalized)
    }
    var isValid: Bool { ["codex", "agy", "claude"].contains(provider) && ModelChoices.valid(model) && effort != ModelChoices.pendingEffort }
}

struct ModelRolesPayload: Encodable {
    var shared: ModelRoleSelection
    var chat: ModelRoleSelection?
    var notes: ModelRoleSelection?
    var language: String
    enum CodingKeys: String, CodingKey { case shared = "default", overrides, language }
    enum RoleKeys: String, CodingKey { case chat, notes }
    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(shared, forKey: .shared)
        try container.encode(language, forKey: .language)
        var roles = container.nestedContainer(keyedBy: RoleKeys.self, forKey: .overrides)
        try roles.encode(chat, forKey: .chat)
        try roles.encode(notes, forKey: .notes)
    }
}
