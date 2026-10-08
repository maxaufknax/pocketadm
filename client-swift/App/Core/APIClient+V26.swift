import Foundation

// The endpoints added with server 0.26. Same actor as APIClient.swift.

extension APIClient {

    // MARK: - The assistant's notes

    func agentNotes() async throws -> AgentNotes {
        try await send(request("GET", "/api/agent/notes"), as: AgentNotes.self)
    }

    func addAgentNote(text: String, topic: String, subject: String = "") async throws -> AgentNotes {
        try await send(request("POST", "/api/agent/notes",
                               body: NoteBody(text: text, topic: topic, subject: subject, pinned: nil)),
                       as: AgentNotes.self)
    }

    /// Changes what is given; nil leaves a field as it is ("" clears the subject).
    func editAgentNote(_ id: String, text: String? = nil, topic: String? = nil,
                       subject: String? = nil, pinned: Bool? = nil) async throws -> AgentNotes {
        try await send(request("PATCH", "/api/agent/notes/\(id)",
                               body: NoteBody(text: text, topic: topic, subject: subject, pinned: pinned)),
                       as: AgentNotes.self)
    }

    func deleteAgentNote(_ id: String) async throws -> AgentNotes {
        try await send(request("DELETE", "/api/agent/notes/\(id)"), as: AgentNotes.self)
    }

    /// Merges duplicates and drops what is outdated, with one undo.
    func tidyAgentNotes() async throws -> AgentNotes {
        var req = try request("POST", "/api/agent/notes/tidy")
        req.timeoutInterval = 300
        return try await send(req, as: AgentNotes.self)
    }

    func undoAgentNotes() async throws -> AgentNotes {
        try await send(request("POST", "/api/agent/notes/undo"), as: AgentNotes.self)
    }

    func clearAgentNotes() async throws -> AgentNotes {
        try await send(request("POST", "/api/agent/notes/clear"), as: AgentNotes.self)
    }

    // MARK: - The server inventory

    func inventory(refresh: Bool = false) async throws -> ServerInventory {
        var req = try request("GET", "/api/inventory",
                              query: refresh ? [URLQueryItem(name: "refresh", value: "true")] : [])
        req.timeoutInterval = 90
        return try await send(req, as: ServerInventory.self)
    }

    /// One systemd unit with its journal.
    func systemUnit(_ unit: String, lines: Int = 150) async throws -> SystemUnit {
        try await send(request("GET", "/api/system/units/\(unit)",
                               query: [URLQueryItem(name: "lines", value: String(lines))]),
                       as: SystemUnit.self)
    }

    /// start, stop, restart, enable or disable; returns the unit afterwards.
    func systemUnitAction(_ unit: String, action: String) async throws -> SystemUnit {
        var req = try request("POST", "/api/system/units/\(unit)/\(action)")
        req.timeoutInterval = 120
        return try await send(req, as: SystemUnit.self)
    }

    // MARK: - The assistant

    /// How-tos the assistant saved for this server (skills).
    func agentSkills() async throws -> [AgentSkill] {
        try await send(request("GET", "/api/skills"), as: AgentSkillList.self).skills
    }

    func agentSkill(_ name: String) async throws -> String {
        try await send(request("GET", "/api/skills/\(name)"), as: SkillContent.self).content
    }

    func deleteAgentSkill(_ name: String) async throws {
        try await sendIgnoringBody(request("DELETE", "/api/skills/\(name)"))
    }

    /// What the assistant is told about the server at the start of each chat.
    func serverMap(refresh: Bool = false) async throws -> ServerMapText {
        try await send(request("GET", "/api/agent/servermap",
                               query: refresh ? [URLQueryItem(name: "refresh", value: "true")] : []),
                       as: ServerMapText.self)
    }

    func setServerMap(enabled: Bool) async throws {
        try await sendIgnoringBody(request("POST", "/api/agent/servermap", body: EnabledBody(enabled: enabled)))
    }

    /// A file or picture attached in the assistant chat, stored where every
    /// assistant can open it.
    func uploadChatFile(_ data: Data, name: String) async throws -> ChatUpload {
        var req = try request("POST", "/api/chat/upload", query: [URLQueryItem(name: "name", value: name)])
        req.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 300
        let body: Data
        let response: URLResponse
        do {
            (body, response) = try await session.upload(for: req, from: data)
        } catch {
            throw APIError.transport(Self.describe(transport: error))
        }
        guard let http = response as? HTTPURLResponse else { throw APIError.transport("Malformed response") }
        guard (200..<300).contains(http.statusCode) else { throw decodeError(body, status: http.statusCode) }
        do {
            return try JSONDecoder().decode(ChatUpload.self, from: body)
        } catch {
            throw APIError.notPocketADM
        }
    }

    func aiSuggestions() async throws -> [String] {
        try await send(request("GET", "/api/ai/suggestions"), as: AISuggestions.self).suggestions
    }
}

private struct EnabledBody: Encodable { let enabled: Bool }

private struct NoteBody: Encodable {
    let text: String?
    let topic: String?
    let subject: String?
    let pinned: Bool?
}
