import Foundation

/// Everything the app knows how to ask a PocketADM server.
///
/// The server is *self-hosted*, so there is no baked-in host: the base URL
/// arrives from the Connect screen and lives in the keychain alongside the
/// token.
actor APIClient {
    private let baseURL: URL
    private let token: String?
    private let session: URLSession

    init(baseURL: URL, token: String? = nil) {
        self.baseURL = baseURL
        self.token = token
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 20
        cfg.waitsForConnectivity = false
        // NetworkSession applies the pins from pairing QRs (TrustStore.swift)
        self.session = NetworkSession.make(cfg)
    }

    /// A transport error, in words that say what to do. A server with its own
    /// certificate that this device has not paired with ends in a cancelled
    /// TLS handshake, which URLSession reports as "cancelled".
    static func describe(transport error: Error) -> String {
        if let urlError = error as? URLError {
            switch urlError.code {
            case .cancelled, .serverCertificateUntrusted, .serverCertificateHasUnknownRoot,
                 .serverCertificateHasBadDate, .serverCertificateNotYetValid,
                 .secureConnectionFailed:
                return "This server's certificate is not one this phone knows. A PocketADM server "
                    + "with its own certificate is added by scanning its pairing QR — the installer "
                    + "prints one, and `docker exec helmsman python -m server.cli pair` shows a new one."
            default:
                break
            }
        }
        return error.localizedDescription
    }

    // MARK: - Errors

    enum APIError: LocalizedError, Equatable {
        case badURL
        case notPocketADM
        /// Password was right but the server wants the second factor. The
        /// server signals this with **401 + {"totp": true}**, not a 2xx — a
        /// client that only looks at the status code tells the user their
        /// password is wrong and the login can never succeed.
        case totpRequired
        case wrongTOTP(String)
        case unauthorized(String)
        case http(Int, String)
        case transport(String)

        var errorDescription: String? {
            switch self {
            case .badURL:            return "That does not look like a server address."
            case .notPocketADM:      return "Reached something, but it is not a PocketADM server."
            case .totpRequired:      return "This server requires a 2FA code."
            case .wrongTOTP(let m):  return m
            case .unauthorized(let m): return m
            case .http(let c, let m): return m.isEmpty ? "Server error (\(c))" : m
            case .transport(let m):  return m
            }
        }
    }

    // MARK: - Request plumbing
    //
    // Internal rather than private: the endpoint list is split across
    // APIClient+Ops.swift to keep either file readable, and `private` in Swift
    // is file-scoped — it would hide these from the other half of the same type.

    func request(
        _ method: String,
        _ path: String,
        query: [URLQueryItem] = [],
        body: (any Encodable)? = nil
    ) throws -> URLRequest {
        guard var comps = URLComponents(url: baseURL.appendingPathComponent(path),
                                        resolvingAgainstBaseURL: false) else {
            throw APIError.badURL
        }
        if !query.isEmpty { comps.queryItems = query }
        guard let url = comps.url else { throw APIError.badURL }

        var req = URLRequest(url: url)
        req.httpMethod = method
        if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONEncoder().encode(AnyEncodable(body))
        }
        return req
    }

    /// Pulls `detail` out of FastAPI's error envelope so the UI can show what
    /// the server actually said instead of a bare status code.
    func decodeError(_ data: Data, status: Int) -> APIError {
        let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        let detail = (obj?["detail"] as? String) ?? ""
        if status == 401 {
            // The 2FA handshake rides on 401. `totp: true` with no code sent
            // means "now ask for one"; with a code sent it means "wrong code".
            if obj?["totp"] as? Bool == true {
                return detail.lowercased().contains("required")
                    ? .totpRequired
                    : .wrongTOTP(detail.isEmpty ? "Wrong 2FA code" : detail)
            }
            return .unauthorized(detail.isEmpty ? "Not authorised" : detail)
        }
        return .http(status, detail)
    }

    func send<T: Decodable>(_ req: URLRequest, as: T.Type) async throws -> T {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            throw APIError.transport(Self.describe(transport: error))
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError.transport("Malformed response")
        }
        guard (200..<300).contains(http.statusCode) else {
            throw decodeError(data, status: http.statusCode)
        }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError.notPocketADM
        }
    }

    @discardableResult
    func sendIgnoringBody(_ req: URLRequest) async throws -> Bool {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            throw APIError.transport(Self.describe(transport: error))
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError.transport("Malformed response")
        }
        guard (200..<300).contains(http.statusCode) else {
            throw decodeError(data, status: http.statusCode)
        }
        return true
    }

    // MARK: - Unauthenticated

    /// Confirms a URL really hosts PocketADM. `helmsman: true` is the marker —
    /// "Helmsman" is the project's internal name and no other service sets it.
    func info() async throws -> ServerInfo {
        let info = try await send(request("GET", "/api/info"), as: ServerInfo.self)
        guard info.helmsman else { throw APIError.notPocketADM }
        return info
    }

    func login(password: String, totp: String = "") async throws -> TokenResponse {
        try await send(
            request("POST", "/api/login", body: LoginBody(password: password, totp: totp)),
            as: TokenResponse.self
        )
    }

    func pairClaim(code: String) async throws -> TokenResponse {
        try await send(
            request("POST", "/api/pair/claim", body: PairClaimBody(code: code)),
            as: TokenResponse.self
        )
    }

    /// Trades the one-time code from a single sign-on round trip for a token.
    func ssoClaim(code: String) async throws -> TokenResponse {
        try await send(
            request("POST", "/api/auth/oidc/claim", body: PairClaimBody(code: code)),
            as: TokenResponse.self
        )
    }

    /// Where the identity provider round trip starts (see SSOSignIn.swift).
    nonisolated func ssoStartURL() -> URL? {
        guard var comps = URLComponents(url: baseURL.appendingPathComponent("/api/auth/oidc/start"),
                                        resolvingAgainstBaseURL: false) else { return nil }
        comps.queryItems = [URLQueryItem(name: "client", value: "app")]
        return comps.url
    }

    // MARK: - Authenticated

    func me() async throws -> MeResponse {
        try await send(request("GET", "/api/me"), as: MeResponse.self)
    }

    func system() async throws -> SystemSnapshot {
        try await send(request("GET", "/api/system"), as: SystemSnapshot.self)
    }

    func metricsHistory(minutes: Int = 60) async throws -> MetricsHistory {
        try await send(
            request("GET", "/api/metrics/history",
                    query: [URLQueryItem(name: "minutes", value: String(minutes))]),
            as: MetricsHistory.self
        )
    }

    func containers() async throws -> [Container] {
        try await send(request("GET", "/api/containers"), as: [Container].self)
    }

    func containerDetail(_ cid: String) async throws -> ContainerDetail {
        try await send(request("GET", "/api/containers/\(cid)/detail"), as: ContainerDetail.self)
    }

    func containerLogs(_ cid: String, tail: Int = 200) async throws -> String {
        try await send(
            request("GET", "/api/containers/\(cid)/logs",
                    query: [URLQueryItem(name: "tail", value: String(tail))]),
            as: ContainerLogs.self
        ).logs
    }

    /// action is one of start / stop / restart — POST /api/containers/{cid}/{action}.
    func containerAction(_ cid: String, _ action: ContainerAction) async throws {
        try await sendIgnoringBody(request("POST", "/api/containers/\(cid)/\(action.rawValue)"))
    }

    func pairNew() async throws -> PairCode {
        try await send(request("POST", "/api/pair/new"), as: PairCode.self)
    }

    func terminalTargets() async throws -> TerminalTargets {
        try await send(request("GET", "/api/terminal/targets"), as: TerminalTargets.self)
    }

    func terminalSessions() async throws -> TerminalSessionList {
        try await send(request("GET", "/api/terminal/sessions"), as: TerminalSessionList.self)
    }

    func createTerminalSession(context: String, title: String = "") async throws -> TerminalSession {
        try await send(
            request("POST", "/api/terminal/sessions",
                    body: TermSessionBody(context: context, title: title)),
            as: TerminalSessionCreated.self
        ).session
    }

    func closeTerminalSession(_ sid: String) async throws {
        try await sendIgnoringBody(request("DELETE", "/api/terminal/sessions/\(sid)"))
    }

    // MARK: - WebSocket URLs

    /// The URL for one WebSocket connect. The credential is a single-use
    /// ticket that expires in seconds (POST /api/ws/ticket), not the 30-day
    /// token: WebSocket URLs end up in reverse-proxy access logs, and a logged
    /// ticket is already spent. A server older than 0.23 has no tickets and
    /// still gets the token.
    func liveWebSocketURL(path: String, extra: [URLQueryItem] = []) async throws -> URL {
        let credential: URLQueryItem
        do {
            let ticket = try await send(request("POST", "/api/ws/ticket"), as: WSTicket.self)
            credential = URLQueryItem(name: "ticket", value: ticket.ticket)
        } catch APIError.http(let status, _) where status == 404 || status == 405 {
            credential = URLQueryItem(name: "token", value: token ?? "")
        }
        guard let url = webSocketURL(path: path, credential: credential, extra: extra) else {
            throw APIError.badURL
        }
        return url
    }

    /// https→wss, http→ws, port kept, credential first.
    nonisolated func webSocketURL(path: String, credential: URLQueryItem,
                                  extra: [URLQueryItem] = []) -> URL? {
        guard var comps = URLComponents(url: baseURL.appendingPathComponent(path),
                                        resolvingAgainstBaseURL: false) else { return nil }
        comps.scheme = (comps.scheme == "https") ? "wss" : "ws"
        comps.queryItems = [credential] + extra
        return comps.url
    }

    // MARK: - Bodies

    private struct LoginBody: Encodable { let password: String; let totp: String }
    private struct PairClaimBody: Encodable { let code: String }
    private struct TermSessionBody: Encodable { let context: String; let title: String }
    private struct WSTicket: Decodable { let ticket: String }
}

enum ContainerAction: String, CaseIterable {
    case start, stop, restart

    var label: String { rawValue.capitalized }
    var symbol: String {
        switch self {
        case .start:   return "play.fill"
        case .stop:    return "stop.fill"
        case .restart: return "arrow.clockwise"
        }
    }
}

/// `JSONEncoder` cannot encode an existential `any Encodable` directly; this
/// wrapper forwards to the concrete type's own encode.
private struct AnyEncodable: Encodable {
    let wrapped: any Encodable
    init(_ wrapped: any Encodable) { self.wrapped = wrapped }
    func encode(to encoder: Encoder) throws { try wrapped.encode(to: encoder) }
}
