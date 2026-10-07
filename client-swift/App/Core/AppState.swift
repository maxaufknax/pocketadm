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

    /// GET /api/me, cached. It decides which features are even offered — the
    /// assistant tab without an AI key, pairing on a server that forbids it —
    /// so screens read this instead of each re-fetching capabilities.
    @Published private(set) var me: MeResponse?
    /// Unread count behind the dashboard's bell. Refreshed with the dashboard
    /// poll rather than on its own timer.
    @Published private(set) var unseenAlerts = 0

    private var token: String?

    private static let urlKey = "pocketadm.serverURL"
    private static let tokenAccount = "authToken"

    /// A client bound to the current server + token, or nil if we have neither.
    var client: APIClient? {
        guard let serverURL else { return nil }
        return APIClient(baseURL: serverURL, token: token)
    }

    var currentToken: String? { token }

    /// A real PocketADM server with sample data, read-only, password "demo".
    /// App Review uses it, and so does anyone without a server of their own —
    /// tests/test_connect_screen.py checks it matches the App Review notes.
    static let demoServer = URL(string: "https://demo.pocketadm.com")!

    /// Screenshot runs (the release workflow drives the simulator):
    /// `-PocketADMScreenshotTab assistant` opens the demo on that tab, with no
    /// stored server involved.
    static let screenshotTab: String? = {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "-PocketADMScreenshotTab"), i + 1 < args.count else { return nil }
        return args[i + 1]
    }()

    init() {
        if Self.screenshotTab == nil { restore() }
    }

    /// Signs in to the public demo server.
    func openDemo() async throws {
        let client = APIClient(baseURL: Self.demoServer)
        let info = try await client.info()
        let result = try await client.login(password: "demo")
        adopt(url: Self.demoServer, info: info)
        signIn(token: result.token, serverName: result.serverName ?? info.serverName)
    }

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

    /// Changing the password and revoking other sessions both invalidate every
    /// existing token — including this device's. The server hands back a
    /// replacement, and dropping it on the floor signs the admin out of their
    /// own phone one request later.
    func replaceToken(_ token: String) {
        self.token = token
        KeychainStore.set(token, for: Self.tokenAccount)
    }

    /// Refreshes the capability snapshot. Silent on failure: a screen that
    /// merely wanted to know whether to show a button must not tear down the
    /// session because the network blinked.
    func refreshMe() async {
        guard let client else { return }
        if let fresh = try? await client.me() {
            me = fresh
            if !fresh.serverName.isEmpty { serverName = fresh.serverName }
        }
    }

    func refreshAlerts() async {
        guard let client else { return }
        if let feed = try? await client.notifications() {
            unseenAlerts = feed.unseen
        }
    }

    func clearAlertBadge() { unseenAlerts = 0 }

    /// Drops the token but keeps the server URL, so signing out lands on the
    /// login screen for the same box instead of making you retype the address.
    func signOut(reason: String? = nil) {
        token = nil
        KeychainStore.set(nil, for: Self.tokenAccount)
        me = nil
        unseenAlerts = 0
        signOutReason = reason
        if let info = serverInfo {
            phase = .authenticate(info)
        } else {
            phase = .connect
        }
    }

    /// Forgets the server entirely — back to the Connect screen. Its TLS pin
    /// goes too: re-adding it takes a fresh pairing QR, which re-pins it.
    func forgetServer() {
        if let serverURL { TrustStore.setPin(nil, for: serverURL) }
        token = nil
        KeychainStore.set(nil, for: Self.tokenAccount)
        me = nil
        unseenAlerts = 0
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
