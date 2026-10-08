import Foundation

// The endpoints added with server 0.25. Same actor as APIClient.swift.

extension APIClient {

    // MARK: - The watch's channel

    /// Oldest first. `after` = what is new since then, `before` = older ones.
    func watchChannel(after: Double = 0, before: Double = 0, limit: Int = 60) async throws -> WatchChannelPage {
        var query = [URLQueryItem(name: "limit", value: String(limit))]
        if after > 0 { query.append(URLQueryItem(name: "after", value: String(after))) }
        if before > 0 { query.append(URLQueryItem(name: "before", value: String(before))) }
        return try await send(request("GET", "/api/watch/channel", query: query), as: WatchChannelPage.self)
    }

    /// Write to the watch; its answer arrives in the channel.
    func sendToWatch(_ text: String) async throws -> ChannelChatResponse {
        try await send(request("POST", "/api/watch/chat", body: ChannelTextBody(text: text)),
                       as: ChannelChatResponse.self)
    }

    func markChannelRead(upTo t: Double = 0) async throws {
        try await sendIgnoringBody(request("POST", "/api/watch/channel/read", body: ReadBody(t: t)))
    }

    func channelFeedback(_ id: String, helpful: Bool) async throws {
        try await sendIgnoringBody(request("POST", "/api/watch/channel/\(id)/feedback",
                                           body: ChannelHelpfulBody(helpful: helpful)))
    }

    func deleteChannelMessage(_ id: String) async throws {
        try await sendIgnoringBody(request("DELETE", "/api/watch/channel/\(id)"))
    }

    // MARK: - Push to this phone

    func pushStatus() async throws -> PushStatus {
        try await send(request("GET", "/api/push"), as: PushStatus.self)
    }

    func registerPushDevice(relayID: String, name: String, min: String, assistant: Bool,
                            preview: Bool) async throws -> PushDevice {
        try await send(request("POST", "/api/push/devices",
                               body: PushDeviceBody(relay_id: relayID, name: name, platform: "ios",
                                                    min: min, assistant: assistant, preview: preview)),
                       as: PushDevice.self)
    }

    func removePushDevice(_ id: String) async throws {
        try await sendIgnoringBody(request("DELETE", "/api/push/devices/\(id)"))
    }

    func testPush() async throws {
        try await sendIgnoringBody(request("POST", "/api/push/test"))
    }

    // MARK: - Files: where to start, and changes

    func fsStart() async throws -> FSStart {
        try await send(request("GET", "/api/fs/start"), as: FSStart.self)
    }

    /// Saves a text file. `expectedModified` refuses to overwrite a file that
    /// changed on the server meanwhile (HTTP 409).
    func writeFile(_ path: String, content: String, expectedModified: Double?,
                   create: Bool = false) async throws -> FSChange {
        try await send(request("POST", "/api/fs/write",
                               body: WriteBody(path: path, content: content,
                                               expected_modified: expectedModified, create: create)),
                       as: FSChange.self)
    }

    /// Puts a file back as it was before a save.
    func restoreFile(version: String) async throws -> FSChange {
        try await send(request("POST", "/api/fs/restore", body: VersionBody(version: version)),
                       as: FSChange.self)
    }

    func makeFolder(in path: String, name: String) async throws -> FSChange {
        try await send(request("POST", "/api/fs/mkdir", body: PathNameBody(path: path, name: name)),
                       as: FSChange.self)
    }

    /// Streams a local file into a folder on the server.
    func uploadFile(_ local: URL, to folder: String, name: String, overwrite: Bool = false) async throws -> FSChange {
        var req = try request("POST", "/api/fs/upload",
                              query: [URLQueryItem(name: "path", value: folder),
                                      URLQueryItem(name: "name", value: name),
                                      URLQueryItem(name: "overwrite", value: overwrite ? "true" : "false")])
        req.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 1800
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.upload(for: req, fromFile: local)
        } catch {
            throw APIError.transport(Self.describe(transport: error))
        }
        guard let http = response as? HTTPURLResponse else { throw APIError.transport("Malformed response") }
        guard (200..<300).contains(http.statusCode) else { throw decodeError(data, status: http.statusCode) }
        do {
            return try JSONDecoder().decode(FSChange.self, from: data)
        } catch {
            throw APIError.notPocketADM
        }
    }

    func renameFile(_ path: String, to name: String) async throws -> FSChange {
        try await send(request("POST", "/api/fs/rename", body: PathNameBody(path: path, name: name)),
                       as: FSChange.self)
    }

    /// "move" or "copy" files and folders into another folder.
    func moveFiles(_ paths: [String], to folder: String, copy: Bool) async throws {
        try await sendIgnoringBody(request("POST", "/api/fs/move",
                                           body: MoveBody(paths: paths, dest: folder,
                                                          action: copy ? "copy" : "move",
                                                          overwrite: false)))
    }

    func deleteFiles(_ paths: [String]) async throws {
        try await sendIgnoringBody(request("POST", "/api/fs/delete", body: PathsBody(paths: paths)))
    }

    func changeMode(_ path: String, mode: String, recursive: Bool) async throws -> FSChange {
        try await send(request("POST", "/api/fs/chmod",
                               body: ModeBody(path: path, mode: mode, recursive: recursive)),
                       as: FSChange.self)
    }

    func extractArchive(_ path: String) async throws -> FSChange {
        try await send(request("POST", "/api/fs/extract", body: OnePathBody(path: path)),
                       as: FSChange.self)
    }

    /// A folder as one zip download.
    func folderArchiveRequest(_ path: String) throws -> URLRequest {
        var req = try request("GET", "/api/fs/archive", query: [URLQueryItem(name: "path", value: path)])
        req.timeoutInterval = 1800
        return req
    }

    /// The update sheet: details plus the summary, when one was written before.
    func updateDetail(image: String, lang: String) async throws -> UpdateDetail {
        try await send(request("GET", "/api/updates/detail",
                               query: [URLQueryItem(name: "image", value: image),
                                       URLQueryItem(name: "lang", value: lang)]),
                       as: UpdateDetail.self)
    }
}

// Field names are the server's, verbatim.
private struct ChannelTextBody: Encodable { let text: String }
private struct ReadBody: Encodable { let t: Double }
private struct ChannelHelpfulBody: Encodable { let helpful: Bool }
private struct PushDeviceBody: Encodable {
    let relay_id: String
    let name: String
    let platform: String
    let min: String
    let assistant: Bool
    let preview: Bool
}
private struct WriteBody: Encodable {
    let path: String
    let content: String
    let expected_modified: Double?
    let create: Bool
}
private struct VersionBody: Encodable { let version: String }
private struct PathNameBody: Encodable { let path: String; let name: String }
private struct MoveBody: Encodable { let paths: [String]; let dest: String; let action: String; let overwrite: Bool }
private struct PathsBody: Encodable { let paths: [String] }
private struct ModeBody: Encodable { let path: String; let mode: String; let recursive: Bool }
private struct OnePathBody: Encodable { let path: String }
