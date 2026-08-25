import Foundation
import SwiftUI

/// Owns "which server are we talking to, and are we signed in" — the one piece
/// of state every screen needs.
@MainActor
final class AppState: ObservableObject {

    enum Phase: Equatable {
        /// No server chosen yet, or the stored one was signed out of.
        case connect
        /// Server identified, but this device has no token for it.
        case authenticate(ServerInfo)
        /// Signed in — the tab bar is up.
        case ready
    }

    @Published private(set) var phase: Phase = .connect
    @Published private(set) var serverURL: URL?
    @Published private(set) var serverName: String = ""
    @Published private(set) var serverInfo: ServerInfo?

    private var token: String?

    private static let urlKey = "pocketadm.serverURL"
    private static let tokenAccount = "authToken"

    /// A client bound to the current server + token, or nil if we have neither.
    var client: APIClient? {
        guard let serverURL else { return nil }
        return APIClient(baseURL: serverURL, token: token)
    }

    var currentToken: String? { token }

    init() { restore() }

    // MARK: - Persistence

    private func restore() {
        guard let stored = UserDefaults.standard.string(forKey: Self.urlKey),
              let url = URL(string: stored) else { return }
        serverURL = url
        token = KeychainStore.get(Self.tokenAccount)
        // A stored token is *assumed* good so the app opens straight onto the
        // dashboard; the first API call revalidates it and `signOut(reason:)`
        // takes over if it has expired.
        phase = token == nil ? .connect : .ready
    }

    // MARK: - Transitions

    /// Server URL confirmed by a successful /api/info probe.
    func adopt(url: URL, info: ServerInfo) {
        serverURL = url
        serverInfo = info
        serverName = info.serverName
        UserDefaults.standard.set(url.absoluteString, forKey: Self.urlKey)
        phase = .authenticate(info)
    }

    func signIn(token: String, serverName: String?) {
        self.token = token
        KeychainStore.set(token, for: Self.tokenAccount)
        if let serverName, !serverName.isEmpty { self.serverName = serverName }
        phase = .ready
    }

    /// Drops the token but keeps the server URL, so signing out lands on the
    /// login screen for the same box instead of making you retype the address.
    func signOut(reason: String? = nil) {
        token = nil
        KeychainStore.set(nil, for: Self.tokenAccount)
        signOutReason = reason
        if let info = serverInfo {
            phase = .authenticate(info)
        } else {
            phase = .connect
        }
    }

    /// Forgets the server entirely — back to the Connect screen.
    func forgetServer() {
        token = nil
        KeychainStore.set(nil, for: Self.tokenAccount)
        serverURL = nil
        serverInfo = nil
        serverName = ""
        UserDefaults.standard.removeObject(forKey: Self.urlKey)
        phase = .connect
    }

    @Published var signOutReason: String?

    /// Every screen funnels its errors through here. A dead token has to end
    /// the session, or the app sits on a screen retrying a call that can only
    /// ever 401 — the exact reload loop the web client had.
    func handle(_ error: Error) {
        // Cast first: an enum-case pattern cannot match the existential `Error`
        // directly (unlike a `catch` clause, where the compiler inserts this).
        guard let apiError = error as? APIClient.APIError else { return }
        if case .unauthorized(let message) = apiError {
            signOut(reason: message)
        }
    }

    /// Re-probes /api/info for a server restored from disk, so the login screen
    /// knows whether to ask for a 2FA code.
    func refreshServerInfo() async {
        guard let serverURL, serverInfo == nil else { return }
        let probe = APIClient(baseURL: serverURL)
        if let info = try? await probe.info() {
            serverInfo = info
            serverName = info.serverName
        }
    }
}

// MARK: - URL normalisation

enum ServerURL {
    /// Turns whatever someone typed into candidate URLs to probe, in order.
    ///
    /// People type `192.168.1.10:8090`, `myserver.duckdns.org`, or a full URL.
    /// With no scheme, https is tried first and http second — a LAN box on a
    /// plain port is the common self-hosted case and must still work, but it
    /// should never win over a working TLS endpoint.
    static func candidates(from raw: String) -> [URL] {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return [] }

        let stripped = trimmed.hasSuffix("/") ? String(trimmed.dropLast()) : trimmed
        if stripped.lowercased().hasPrefix("http://") || stripped.lowercased().hasPrefix("https://") {
            return URL(string: stripped).map { [$0] } ?? []
        }
        return ["https://\(stripped)", "http://\(stripped)"].compactMap(URL.init(string:))
    }
}
