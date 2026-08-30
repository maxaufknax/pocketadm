import Foundation

// The rest of the server's surface: updates, the app catalog, health reports,
// notifications, the audit log, local AI, coding CLIs, files, host users and
// every settings mutation. Split from APIClient.swift purely for readability —
// it is the same actor.

extension APIClient {

    // MARK: - Notifications

    func notifications() async throws -> NotificationFeed {
        try await send(request("GET", "/api/notifications"), as: NotificationFeed.self)
    }

    func markNotificationsSeen() async throws {
        try await sendIgnoringBody(request("POST", "/api/notifications/seen"))
    }

    // MARK: - Updates

    /// `force` skips the server's one-hour cache. Registries rate-limit, so it
    /// belongs behind a deliberate pull-to-refresh, not every appearance.
    func updates(force: Bool = false) async throws -> UpdatesResponse {
        try await send(
            request("GET", "/api/updates",
                    query: force ? [URLQueryItem(name: "force", value: "true")] : []),
            as: UpdatesResponse.self)
    }

    func updateDetail(image: String) async throws -> UpdateDetail {
        try await send(
            request("GET", "/api/updates/detail",
                    query: [URLQueryItem(name: "image", value: image)]),
            as: UpdateDetail.self)
    }

    func applyUpdate(image: String, recreate: Bool = true) async throws -> String {
        try await send(request("POST", "/api/updates/apply",
                               body: ApplyUpdateBody(image: image, recreate: recreate)),
                       as: JobRef.self).jobID
    }

    func applyAllUpdates(images: [String]) async throws -> String {
        try await send(request("POST", "/api/updates/apply-all",
                               body: ApplyAllBody(images: images)),
                       as: JobRef.self).jobID
    }

    func setUpdateIgnored(image: String, ignored: Bool) async throws {
        try await sendIgnoringBody(request("POST", "/api/updates/ignore",
                                           body: IgnoreBody(image: image, ignored: ignored)))
    }

    // MARK: - Snapshots

    func snapshots() async throws -> [Snapshot] {
        try await send(request("GET", "/api/snapshots"), as: SnapshotList.self).snapshots
    }

    func rollbackSnapshot(_ id: String) async throws -> String {
        try await send(request("POST", "/api/snapshots/\(id)/rollback"), as: JobRef.self).jobID
    }

    func deleteSnapshot(_ id: String) async throws {
        try await sendIgnoringBody(request("DELETE", "/api/snapshots/\(id)"))
    }

    // MARK: - Jobs

    func job(_ id: String) async throws -> JobStatus {
        try await send(request("GET", "/api/jobs/\(id)"), as: JobStatus.self)
    }

    func jobs(kind: String? = nil) async throws -> [JobStatus] {
        let query = kind.map { [URLQueryItem(name: "kind", value: $0)] } ?? []
        return try await send(request("GET", "/api/jobs", query: query), as: [JobStatus].self)
    }

    /// The live log of a running job, line by line.
    ///
    /// `/api/jobs/{id}/stream` is a plain `text/plain` stream, not SSE — the
    /// job's own `follow()` emits an empty chunk every 15s as a keep-alive, so
    /// a client that waits for whole lines must tolerate empty reads.
    ///
    /// Hands back the request rather than the bytes: `URLSession.bytes(for:)`
    /// has to be driven from the caller so the log can render as it arrives.
    /// Deliberately *not* `nonisolated` — it reaches into the actor's own
    /// request builder, which a non-isolated context may not touch.
    func jobStreamRequest(_ id: String) throws -> URLRequest {
        try request("GET", "/api/jobs/\(id)/stream")
    }

    // MARK: - App catalog

    func apps() async throws -> AppsResponse {
        try await send(request("GET", "/api/apps"), as: AppsResponse.self)
    }

    func refreshCatalog() async throws -> CatalogInfo {
        try await send(request("POST", "/api/apps/catalog/refresh"), as: CatalogInfo.self)
    }

    /// Install runs synchronously on the server (docker compose up), so this
    /// can take a while — the caller shows a spinner rather than a job log.
    func installApp(_ id: String, values: [String: String]) async throws -> String {
        try await send(request("POST", "/api/apps/\(id)/install",
                               body: InstallBody(values: values)),
                       as: CommandOutput.self).output
    }

    func uninstallApp(_ id: String, removeData: Bool) async throws -> String {
        try await send(
            request("POST", "/api/apps/\(id)/uninstall",
                    query: [URLQueryItem(name: "remove_data", value: removeData ? "true" : "false")]),
            as: CommandOutput.self).output
    }

    // MARK: - Checks & reports

    func reports() async throws -> ReportsIndex {
        try await send(request("GET", "/api/reports"), as: ReportsIndex.self)
    }

    /// 404 when the server has never run a check — that is an empty state, not
    /// an error, so callers map `.http(404, _)` accordingly.
    func latestReport() async throws -> Report {
        try await send(request("GET", "/api/reports/latest"), as: Report.self)
    }

    func report(named name: String) async throws -> Report {
        try await send(request("GET", "/api/reports/\(name)"), as: Report.self)
    }

    func runReport() async throws -> Report {
        try await send(request("POST", "/api/reports/run"), as: Report.self)
    }

    func analyzeReport(named name: String, lang: String = "") async throws -> String {
        try await send(request("POST", "/api/reports/analyze",
                               body: AnalyzeBody(name: name, lang: lang)),
                       as: AnalysisResponse.self).analysis
    }

    func setReportConfig(intervalMin: Int, auto: Bool) async throws {
        try await sendIgnoringBody(
            request("POST", "/api/reports/config",
                    body: ReportConfigBody(interval_min: intervalMin, auto: auto)))
    }

    // MARK: - Audit log

    func audit(limit: Int = 80, action: String = "", source: String = "",
               before: Double = 0) async throws -> AuditFeed {
        var query = [URLQueryItem(name: "limit", value: String(limit))]
        if !action.isEmpty { query.append(URLQueryItem(name: "action", value: action)) }
        if !source.isEmpty { query.append(URLQueryItem(name: "source", value: source)) }
        if before > 0 { query.append(URLQueryItem(name: "before", value: String(before))) }
        return try await send(request("GET", "/api/audit", query: query), as: AuditFeed.self)
    }

    // MARK: - Local AI

    func localAI() async throws -> LocalAIStatus {
        try await send(request("GET", "/api/localai/status"), as: LocalAIStatus.self)
    }

    func installLocalAI() async throws -> String {
        try await send(request("POST", "/api/localai/install"), as: JobRef.self).jobID
    }

    func connectLocalAI() async throws -> LocalAIStatus {
        try await send(request("POST", "/api/localai/connect"), as: LocalAIStatus.self)
    }

    func pullModel(_ model: String) async throws -> String {
        try await send(request("POST", "/api/localai/pull", body: ModelBody(model: model)),
                       as: JobRef.self).jobID
    }

    func deleteModel(_ model: String) async throws {
        try await sendIgnoringBody(
            request("POST", "/api/localai/delete", body: ModelBody(model: model)))
    }

    func setOllamaBase(_ base: String) async throws -> LocalAIStatus {
        try await send(request("POST", "/api/localai/base", body: BaseBody(base: base)),
                       as: LocalAIStatus.self)
    }

    // MARK: - Coding CLIs

    func codingCLIs() async throws -> [CLITool] {
        try await send(request("GET", "/api/clis"), as: CLIList.self).clis
    }

    func installCLI(_ tool: String) async throws -> String {
        try await send(request("POST", "/api/clis/\(tool)/install"), as: JobRef.self).jobID
    }

    // MARK: - Files

    /// `files: true` also returns file entries; the plain form lists only
    /// directories, which is all the workspace picker needs.
    func listDirectory(_ path: String, files: Bool = true) async throws -> FSListing {
        var query: [URLQueryItem] = []
        if !path.isEmpty { query.append(URLQueryItem(name: "path", value: path)) }
        query.append(URLQueryItem(name: "files", value: files ? "1" : "0"))
        return try await send(request("GET", "/api/fs", query: query), as: FSListing.self)
    }

    func readFile(_ path: String) async throws -> FileContent {
        try await send(request("GET", "/api/fs/read",
                               query: [URLQueryItem(name: "path", value: path)]),
                       as: FileContent.self)
    }

    // MARK: - Host users

    func serverUsers() async throws -> ServerUsers {
        try await send(request("GET", "/api/server/users"), as: ServerUsers.self)
    }

    func serverIdentity() async throws -> ServerIdentity {
        try await send(request("GET", "/api/server/identity"), as: ServerIdentity.self)
    }

    func setUserPassword(_ name: String, password: String) async throws -> String {
        try await send(request("POST", "/api/server/users/\(name)/password",
                               body: PasswordOnlyBody(password: password)),
                       as: OKResponse.self).message
    }

    func setUserLocked(_ name: String, locked: Bool) async throws -> String {
        try await send(request("POST", "/api/server/users/\(name)/lock",
                               body: FlagBody(value: locked)),
                       as: OKResponse.self).message
    }

    func setUserAdmin(_ name: String, admin: Bool) async throws -> String {
        try await send(request("POST", "/api/server/users/\(name)/admin",
                               body: FlagBody(value: admin)),
                       as: OKResponse.self).message
    }

    func createUser(_ name: String, password: String, admin: Bool) async throws -> String {
        try await send(request("POST", "/api/server/users",
                               body: CreateUserBody(name: name, password: password, admin: admin)),
                       as: OKResponse.self).message
    }

    // MARK: - AI configuration

    func aiModels() async throws -> AIModels {
        try await send(request("GET", "/api/ai/models"), as: AIModels.self)
    }

    func aiUsage() async throws -> AIUsage {
        try await send(request("GET", "/api/ai/usage"), as: AIUsage.self)
    }

    /// `keys` maps provider -> key; "" leaves a stored key alone and "-" clears
    /// it, so the UI never has to echo a secret back to save something else.
    func saveAISettings(keys: [String: String], defaultProvider: String = "",
                        defaultModel: String = "") async throws {
        try await sendIgnoringBody(
            request("POST", "/api/settings/ai",
                    body: AIConfigBody(keys: keys, default_provider: defaultProvider,
                                       default_model: defaultModel)))
    }

    // MARK: - Security

    /// Changing the password revokes every other session, so the server hands
    /// back a fresh token for *this* device — storing it is not optional.
    func changePassword(current: String, new: String) async throws -> String? {
        try await send(request("POST", "/api/settings/password",
                               body: ChangePasswordBody(current: current, new: new)),
                       as: OKResponse.self).token
    }

    func totpSetup() async throws -> TOTPSetup {
        try await send(request("GET", "/api/settings/2fa/setup"), as: TOTPSetup.self)
    }

    func enableTOTP(secret: String, code: String) async throws {
        try await sendIgnoringBody(request("POST", "/api/settings/2fa/enable",
                                           body: TOTPEnableBody(secret: secret, code: code)))
    }

    func disableTOTP(password: String, code: String) async throws {
        try await sendIgnoringBody(request("POST", "/api/settings/2fa/disable",
                                           body: TOTPDisableBody(password: password, code: code)))
    }

    func revokeOtherSessions() async throws -> String? {
        try await send(request("POST", "/api/settings/sessions/revoke"), as: OKResponse.self).token
    }

    func acknowledgeExposure() async throws {
        try await sendIgnoringBody(request("POST", "/api/settings/exposure/ack"))
    }

    // MARK: - Server settings

    func setServerName(_ name: String) async throws -> String {
        try await send(request("POST", "/api/settings/server", body: ServerNameBody(name: name)),
                       as: ServerNameResponse.self).serverName
    }

    func setWorkspaces(_ paths: [String]) async throws {
        try await sendIgnoringBody(request("POST", "/api/settings/workspaces",
                                           body: WorkspacesBody(paths: paths)))
    }

    func setDefaultWorkspace(_ path: String) async throws {
        try await sendIgnoringBody(request("POST", "/api/settings/default-workspace",
                                           body: PathBody(path: path)))
    }

    // MARK: - Agent configuration

    func agentMemory() async throws -> String {
        try await send(request("GET", "/api/agent/memory"), as: TextPayload.self).memory
    }

    func saveAgentMemory(_ text: String) async throws {
        try await sendIgnoringBody(request("POST", "/api/agent/memory",
                                           body: MemoryBody(memory: text)))
    }

    func agentInstructions() async throws -> String {
        try await send(request("GET", "/api/agent/instructions"), as: TextPayload.self).instructions
    }

    func saveAgentInstructions(_ text: String) async throws {
        try await sendIgnoringBody(request("POST", "/api/agent/instructions",
                                           body: InstructionsBody(instructions: text)))
    }

    func agentTools() async throws -> [AgentTool] {
        try await send(request("GET", "/api/agent/tools"), as: AgentToolList.self).tools
    }

    func setAgentTool(_ name: String, enabled: Bool) async throws {
        try await sendIgnoringBody(request("POST", "/api/agent/tools/\(name)",
                                           body: EnabledBody(enabled: enabled)))
    }

    // MARK: - Chats

    func chats() async throws -> [ChatSummary] {
        try await send(request("GET", "/api/chats"), as: ChatIndex.self).chats
    }

    func renameChat(_ id: String, title: String) async throws {
        try await sendIgnoringBody(request("POST", "/api/chats/\(id)/rename",
                                           body: TitleBody(title: title)))
    }

    func archiveChat(_ id: String, archived: Bool) async throws {
        try await sendIgnoringBody(request("POST", "/api/chats/\(id)/archive",
                                           body: ArchivedBody(archived: archived)))
    }

    func deleteChat(_ id: String) async throws {
        try await sendIgnoringBody(request("DELETE", "/api/chats/\(id)"))
    }

    // MARK: - Containers (the parts beyond start/stop)

    func containerStats(_ cid: String) async throws -> ContainerStats {
        try await send(request("GET", "/api/containers/\(cid)/stats"), as: ContainerStats.self)
    }

    /// Named volumes survive, so app data is not lost — the UI says so before
    /// asking. The server refuses to remove PocketADM's own container.
    func removeContainer(_ cid: String, force: Bool) async throws {
        try await sendIgnoringBody(
            request("DELETE", "/api/containers/\(cid)",
                    query: [URLQueryItem(name: "force", value: force ? "true" : "false")]))
    }

    func describeContainer(_ cid: String, lang: String = "") async throws -> String {
        try await send(request("POST", "/api/containers/\(cid)/describe",
                               body: LangBody(lang: lang)),
                       as: DescriptionResponse.self).description
    }
}

// MARK: - Request bodies
//
// Field names are the server's, verbatim — these encode straight to JSON, so a
// Swift-style rename here would silently post keys FastAPI ignores.

private struct ApplyUpdateBody: Encodable { let image: String; let recreate: Bool }
private struct ApplyAllBody: Encodable { let images: [String] }
private struct IgnoreBody: Encodable { let image: String; let ignored: Bool }
private struct InstallBody: Encodable { let values: [String: String] }
private struct AnalyzeBody: Encodable { let name: String; let lang: String }
private struct ReportConfigBody: Encodable { let interval_min: Int; let auto: Bool }
private struct ModelBody: Encodable { let model: String }
private struct BaseBody: Encodable { let base: String }
private struct PasswordOnlyBody: Encodable { let password: String }
private struct FlagBody: Encodable { let value: Bool }
private struct CreateUserBody: Encodable { let name: String; let password: String; let admin: Bool }
private struct AIConfigBody: Encodable {
    let keys: [String: String]
    let default_provider: String
    let default_model: String
}
private struct ChangePasswordBody: Encodable { let current: String; let new: String }
private struct TOTPEnableBody: Encodable { let secret: String; let code: String }
private struct TOTPDisableBody: Encodable { let password: String; let code: String }
private struct ServerNameBody: Encodable { let name: String }
private struct WorkspacesBody: Encodable { let paths: [String] }
private struct PathBody: Encodable { let path: String }
private struct MemoryBody: Encodable { let memory: String }
private struct InstructionsBody: Encodable { let instructions: String }
private struct EnabledBody: Encodable { let enabled: Bool }
private struct TitleBody: Encodable { let title: String }
private struct ArchivedBody: Encodable { let archived: Bool }
private struct LangBody: Encodable { let lang: String }

// MARK: - Small responses

private struct ServerNameResponse: Decodable {
    let serverName: String
    enum CodingKeys: String, CodingKey { case serverName = "server_name" }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        serverName = c.get(.serverName, "")
    }
}

private struct DescriptionResponse: Decodable {
    let description: String
    enum CodingKeys: String, CodingKey { case description }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        description = c.get(.description, "")
    }
}
