import Foundation

// The endpoints added with server 0.24. Same actor as APIClient.swift.

extension APIClient {

    // MARK: - Apps and containers

    func services() async throws -> [AppGroup] {
        try await send(request("GET", "/api/services"), as: ServicesResponse.self).groups
    }

    /// start / stop / restart every container of an app, in a working order.
    func groupAction(_ groupID: String, _ action: ContainerAction) async throws -> GroupActionResult {
        try await send(request("POST", "/api/services/\(groupID)/action",
                               body: ActionBody(action: action.rawValue)),
                       as: GroupActionResult.self)
    }

    /// pause / unpause / kill — the actions beyond start, stop and restart.
    func containerCommand(_ cid: String, _ command: String) async throws {
        try await sendIgnoringBody(request("POST", "/api/containers/\(cid)/\(command)"))
    }

    /// `live` is the cheap form for a screen that polls every few seconds.
    func containerStats(_ cid: String, live: Bool) async throws -> ContainerStats {
        try await send(request("GET", "/api/containers/\(cid)/stats",
                               query: live ? [URLQueryItem(name: "live", value: "1")] : []),
                       as: ContainerStats.self)
    }

    func containerTop(_ cid: String) async throws -> ContainerTop {
        try await send(request("GET", "/api/containers/\(cid)/top"), as: ContainerTop.self)
    }

    func containerEvents(_ cid: String, hours: Int = 72) async throws -> [ContainerEvent] {
        try await send(request("GET", "/api/containers/\(cid)/events",
                               query: [URLQueryItem(name: "hours", value: String(hours))]),
                       as: ContainerEventList.self).events
    }

    func containerLogs(_ cid: String, tail: Int, since: Int, timestamps: Bool) async throws -> String {
        var query = [URLQueryItem(name: "tail", value: String(tail))]
        if since > 0 { query.append(URLQueryItem(name: "since", value: String(since))) }
        if timestamps { query.append(URLQueryItem(name: "timestamps", value: "true")) }
        return try await send(request("GET", "/api/containers/\(cid)/logs", query: query),
                              as: ContainerLogs.self).logs
    }

    /// The live tail, as plain text lines — driven by the caller with
    /// `URLSession.bytes(for:)` like the job log.
    func containerLogStreamRequest(_ cid: String, tail: Int = 0) throws -> URLRequest {
        try request("GET", "/api/containers/\(cid)/logs/stream",
                    query: [URLQueryItem(name: "tail", value: String(tail))])
    }

    func setRestartPolicy(_ cid: String, policy: String) async throws {
        try await sendIgnoringBody(request("POST", "/api/containers/\(cid)/restart-policy",
                                           body: PolicyBody(policy: policy)))
    }

    // MARK: - Files and drives

    func storage() async throws -> [Filesystem] {
        try await send(request("GET", "/api/storage"), as: StorageResponse.self).filesystems
    }

    func listDirectory(_ path: String, hidden: Bool) async throws -> FSListing {
        var query: [URLQueryItem] = [URLQueryItem(name: "files", value: "1")]
        if !path.isEmpty { query.insert(URLQueryItem(name: "path", value: path), at: 0) }
        if hidden { query.append(URLQueryItem(name: "hidden", value: "1")) }
        return try await send(request("GET", "/api/fs", query: query), as: FSListing.self)
    }

    func folderUsage(_ path: String) async throws -> FolderUsage {
        try await send(request("GET", "/api/fs/usage",
                               query: [URLQueryItem(name: "path", value: path)]),
                       as: FolderUsage.self)
    }

    func searchFiles(in path: String, query text: String) async throws -> FileSearchResult {
        try await send(request("GET", "/api/fs/search",
                               query: [URLQueryItem(name: "path", value: path),
                                       URLQueryItem(name: "q", value: text)]),
                       as: FileSearchResult.self)
    }

    /// The file itself, for QuickLook or the share sheet.
    func fileRequest(_ path: String, download: Bool = false) throws -> URLRequest {
        var query = [URLQueryItem(name: "path", value: path)]
        if download { query.append(URLQueryItem(name: "download", value: "1")) }
        var req = try request("GET", "/api/fs/raw", query: query)
        req.timeoutInterval = 600
        return req
    }

    func pruneImages() async throws -> String {
        try await send(request("POST", "/api/maintenance/prune-images"), as: JobRef.self).jobID
    }

    // MARK: - Activity

    func activity(limit: Int = 100, before: Double = 0, categories: [String] = []) async throws -> ActivityFeed {
        var query = [URLQueryItem(name: "limit", value: String(limit))]
        if before > 0 { query.append(URLQueryItem(name: "before", value: String(before))) }
        if !categories.isEmpty {
            query.append(URLQueryItem(name: "category", value: categories.joined(separator: ",")))
        }
        return try await send(request("GET", "/api/activity", query: query), as: ActivityFeed.self)
    }

    func activityStreamRequest(categories: [String] = []) throws -> URLRequest {
        var req = try request("GET", "/api/activity/stream",
                              query: categories.isEmpty ? [] :
                                [URLQueryItem(name: "category", value: categories.joined(separator: ","))])
        req.timeoutInterval = 3600
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        return req
    }

    // MARK: - Health

    func muteCheck(_ id: String, muted: Bool, note: String = "") async throws {
        try await sendIgnoringBody(request("POST", "/api/reports/checks/\(id)/mute",
                                           body: MuteBody(muted: muted, note: note)))
    }

    func dismissPermissions(_ ids: [String]) async throws {
        try await sendIgnoringBody(request("POST", "/api/permissions/bulk",
                                           body: PermissionsBody(ids: ids, action: "dismiss")))
    }

    // MARK: - The watch

    func watchStatus() async throws -> WatchStatus {
        try await send(request("GET", "/api/watch"), as: WatchStatus.self)
    }

    func saveWatch(_ changes: [String: WatchValue]) async throws -> WatchStatus {
        try await send(request("POST", "/api/watch", body: WatchChangesBody(changes: changes)),
                       as: WatchStatus.self)
    }

    func runWatch(kind: String = "test") async throws {
        try await sendIgnoringBody(request("POST", "/api/watch/run", body: KindBody(kind: kind)))
    }

    func pauseWatch(minutes: Int) async throws -> WatchStatus {
        try await send(request("POST", "/api/watch/pause", body: MinutesBody(minutes: minutes)),
                       as: WatchStatus.self)
    }

    func muteWatchTopic(_ topic: String, hours: Double, note: String = "") async throws -> WatchStatus {
        try await send(request("POST", "/api/watch/mute",
                               body: TopicMuteBody(topic: topic, hours: hours, note: note)),
                       as: WatchStatus.self)
    }

    func testWatchDelivery() async throws {
        try await sendIgnoringBody(request("POST", "/api/watch/test-delivery"))
    }

    func clearWatchMemory() async throws -> WatchStatus {
        try await send(request("POST", "/api/watch/memory/clear"), as: WatchStatus.self)
    }

    func alertFeedback(_ id: String, helpful: Bool) async throws {
        try await sendIgnoringBody(request("POST", "/api/notifications/\(id)/feedback",
                                           body: HelpfulBody(helpful: helpful)))
    }

    func deleteAlert(_ id: String) async throws {
        try await sendIgnoringBody(request("DELETE", "/api/notifications/\(id)"))
    }

    // MARK: - AI accounts

    func aiAccounts() async throws -> AIAccounts {
        try await send(request("GET", "/api/ai/accounts"), as: AIAccounts.self)
    }

    func startSignIn(engine: String) async throws -> SignInFlow {
        try await send(request("POST", "/api/ai/accounts/\(engine)/signin"),
                       as: SignInFlowEnvelope.self).flow
    }

    func signInFlow(_ id: String) async throws -> SignInFlow {
        try await send(request("GET", "/api/ai/signin/\(id)"), as: SignInFlowEnvelope.self).flow
    }

    func submitSignInCode(_ id: String, code: String) async throws -> SignInFlow {
        try await send(request("POST", "/api/ai/signin/\(id)/code", body: CodeBody(code: code)),
                       as: SignInFlowEnvelope.self).flow
    }

    func cancelSignIn(_ id: String) async throws {
        try await sendIgnoringBody(request("DELETE", "/api/ai/signin/\(id)"))
    }

    func signOut(engine: String) async throws -> AIAccounts {
        try await send(request("POST", "/api/ai/accounts/\(engine)/signout"), as: AIAccounts.self)
    }

    /// feature: assistant / watch / insights. provider "" = same as the assistant.
    func setRoute(feature: String, provider: String, model: String) async throws -> [String: AIAccounts.Route] {
        try await send(request("POST", "/api/ai/routes",
                               body: RouteBody(feature: feature, provider: provider, model: model)),
                       as: RoutesResponse.self).routes
    }

    // MARK: - Chats

    func chats(search: String) async throws -> [ChatSummary] {
        try await send(request("GET", "/api/chats",
                               query: search.isEmpty ? [] : [URLQueryItem(name: "q", value: search)]),
                       as: ChatIndex.self).chats
    }

    func pinChat(_ id: String, pinned: Bool) async throws {
        try await sendIgnoringBody(request("POST", "/api/chats/\(id)/pin",
                                           body: PinnedBody(pinned: pinned)))
    }

    func deleteChats(_ ids: [String]) async throws {
        try await sendIgnoringBody(request("POST", "/api/chats/delete", body: IDsBody(ids: ids)))
    }

    func exportChat(_ id: String) async throws -> String {
        try await send(request("GET", "/api/chats/\(id)/export"), as: ChatExport.self).markdown
    }

    func explainUpdate(image: String, lang: String) async throws -> String {
        try await send(request("POST", "/api/updates/explain",
                               body: ExplainBody(subject: image, kind: "docker", lang: lang)),
                       as: ExplanationResponse.self).explanation
    }
}

// Field names are the server's, verbatim (see APIClient+Ops.swift).
private struct ActionBody: Encodable { let action: String }
private struct PolicyBody: Encodable { let policy: String }
private struct MuteBody: Encodable { let muted: Bool; let note: String }
private struct PermissionsBody: Encodable { let ids: [String]; let action: String }
private struct WatchChangesBody: Encodable { let changes: [String: WatchValue] }
private struct KindBody: Encodable { let kind: String }
private struct MinutesBody: Encodable { let minutes: Int }
private struct TopicMuteBody: Encodable { let topic: String; let hours: Double; let note: String }
private struct HelpfulBody: Encodable { let helpful: Bool }
private struct CodeBody: Encodable { let code: String }
private struct RouteBody: Encodable { let feature: String; let provider: String; let model: String }
private struct PinnedBody: Encodable { let pinned: Bool }
private struct IDsBody: Encodable { let ids: [String] }
private struct ExplainBody: Encodable { let subject: String; let kind: String; let lang: String }

private struct ExplanationResponse: Decodable {
    let explanation: String

    enum CodingKeys: String, CodingKey { case explanation }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        explanation = c.get(.explanation, "")
    }
}
