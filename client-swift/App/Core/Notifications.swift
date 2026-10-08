import BackgroundTasks
import Foundation
import UIKit
import UserNotifications

/// Notifications on this phone: the watch's messages and the assistant
/// waiting for an OK, like a chat app.
///
/// Two ways in, and the app uses whichever works:
///
///  * **Push.** The app registers its Apple push token with the PocketADM relay
///    (pocketadm.com/push), gets a random relay id back and hands that to the
///    server (POST /api/push/devices). The server sends through the relay; the
///    relay holds the Apple key. Instant, even with the app closed.
///  * **Background refresh.** Where push is not available (the relay has no
///    Apple key yet, an older server, a build without the entitlement), iOS
///    wakes the app every so often and it asks the server what is new — the
///    same messages as local notifications, some minutes late.
@MainActor
final class PushManager: NSObject, ObservableObject {
    static let shared = PushManager()

    static let relay = URL(string: "https://pocketadm.com/push")!
    static let refreshTask = "de.maxaufknax.pocketadm.refresh"

    @Published private(set) var authorization: UNAuthorizationStatus = .notDetermined
    @Published private(set) var relayID: String?
    /// The relay can deliver (it has an Apple key) — said at registration.
    @Published private(set) var relayReady = false
    /// This phone is registered with the signed-in server.
    @Published private(set) var serverDevice: String?
    @Published private(set) var lastError: String?

    /// What is on screen, so a banner is not shown for a message you are
    /// already reading.
    var channelVisible = false
    var openChat: String?

    /// Taps on a notification are routed through the app state.
    weak var app: AppState?

    private enum Key {
        static let relayID = "pocketadm.push.relayID"
        static let relayReady = "pocketadm.push.relayReady"
        static let device = "pocketadm.push.device"
        static let wanted = "pocketadm.push.wanted"
        static let minimum = "pocketadm.push.min"
        static let assistant = "pocketadm.push.assistant"
        static let preview = "pocketadm.push.preview"
        static let lastWatch = "pocketadm.push.lastWatch"
        static let notifiedChats = "pocketadm.push.notifiedChats"
    }

    private let defaults = UserDefaults.standard

    override init() {
        super.init()
        relayID = defaults.string(forKey: Key.relayID)
        relayReady = defaults.bool(forKey: Key.relayReady)
        serverDevice = defaults.string(forKey: Key.device)
    }

    // MARK: - Preferences (also sent to the server)

    /// The user turned notifications on in the app (and has not turned them off).
    var wanted: Bool {
        get { defaults.bool(forKey: Key.wanted) }
        set { defaults.set(newValue, forKey: Key.wanted); objectWillChange.send() }
    }

    /// info, important or critical: the least important watch message to get.
    var minimum: String {
        get { defaults.string(forKey: Key.minimum) ?? "info" }
        set { defaults.set(newValue, forKey: Key.minimum); objectWillChange.send() }
    }

    var assistant: Bool {
        get { defaults.object(forKey: Key.assistant) as? Bool ?? true }
        set { defaults.set(newValue, forKey: Key.assistant); objectWillChange.send() }
    }

    /// Off: the notification only says that there is news, not what it is.
    var preview: Bool {
        get { defaults.object(forKey: Key.preview) as? Bool ?? true }
        set { defaults.set(newValue, forKey: Key.preview); objectWillChange.send() }
    }

    var allowed: Bool {
        authorization == .authorized || authorization == .provisional || authorization == .ephemeral
    }

    /// Real push works end to end for this phone.
    var pushActive: Bool { allowed && wanted && relayID != nil && relayReady && serverDevice != nil }

    // MARK: - Launch

    /// From `application(_:didFinishLaunchingWithOptions:)`: the background
    /// task has to be registered before launch ends.
    func configure() {
        UNUserNotificationCenter.current().delegate = self
        _ = BGTaskScheduler.shared.register(forTaskWithIdentifier: Self.refreshTask, using: nil) { task in
            guard let refresh = task as? BGAppRefreshTask else {
                task.setTaskCompleted(success: false)
                return
            }
            Task { @MainActor in await PushManager.shared.handle(refresh) }
        }
        Task { await refreshAuthorization() }
    }

    func refreshAuthorization() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        authorization = settings.authorizationStatus
        guard allowed, wanted else { return }
        // the token can change; asking again is cheap and refreshes the relay
        UIApplication.shared.registerForRemoteNotifications()
        scheduleRefresh()
    }

    /// The switch in the app. Asks iOS once; afterwards the answer lives in
    /// the iOS settings, which the screen links to.
    @discardableResult
    func enable() async -> Bool {
        wanted = true
        if defaults.double(forKey: Key.lastWatch) == 0 {
            defaults.set(Date().timeIntervalSince1970, forKey: Key.lastWatch)   // no flood of old news
        }
        do {
            _ = try await UNUserNotificationCenter.current()
                .requestAuthorization(options: [.alert, .sound, .badge])
        } catch {
            lastError = error.localizedDescription
        }
        await refreshAuthorization()
        return allowed
    }

    func disable() async {
        wanted = false
        await unregisterFromServer()
        BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: Self.refreshTask)
    }

    // MARK: - Apple's token → relay → server

    func didRegister(deviceToken: Data) {
        let hex = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { await registerWithRelay(token: hex) }
    }

    /// No push in this build (simulator, or built without the entitlement):
    /// background refresh still brings the news.
    func didFailToRegister(_ error: Error) {
        lastError = error.localizedDescription
    }

    private func registerWithRelay(token: String) async {
        var request = URLRequest(url: Self.relay.appendingPathComponent("v1/register"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 20
        #if DEBUG
        let environment = "sandbox"
        #else
        let environment = "production"
        #endif
        let body: [String: Any] = ["token": token, "bundle": Bundle.main.bundleIdentifier ?? "",
                                   "env": environment]
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200,
                  let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let id = json["relay_id"] as? String else {
                lastError = "The push relay did not accept this phone."
                return
            }
            relayID = id
            relayReady = json["apns"] as? Bool ?? false
            defaults.set(id, forKey: Key.relayID)
            defaults.set(relayReady, forKey: Key.relayReady)
            lastError = nil
            await registerWithServer()
        } catch {
            lastError = error.localizedDescription
        }
    }

    /// Tells the signed-in server where to push (and what this phone wants).
    func registerWithServer() async {
        guard wanted, let relayID, let client = app?.client ?? Self.storedClient() else { return }
        do {
            let device = try await client.registerPushDevice(
                relayID: relayID, name: UIDevice.current.name, min: minimum,
                assistant: assistant, preview: preview)
            serverDevice = device.id
            defaults.set(device.id, forKey: Key.device)
        } catch {
            // an older server has no push: background refresh covers it
            serverDevice = nil
            defaults.removeObject(forKey: Key.device)
        }
    }

    func unregisterFromServer() async {
        if let id = serverDevice, let client = app?.client ?? Self.storedClient() {
            try? await client.removePushDevice(id)
        }
        serverDevice = nil
        defaults.removeObject(forKey: Key.device)
    }

    /// Signing out or forgetting the server: it must stop pushing to this
    /// phone. `client` is the old server's, taken before its token is dropped.
    func forget(using client: APIClient?) async {
        if let id = serverDevice, let client { try? await client.removePushDevice(id) }
        serverDevice = nil
        defaults.removeObject(forKey: Key.device)
        defaults.removeObject(forKey: Key.notifiedChats)
        defaults.set(Date().timeIntervalSince1970, forKey: Key.lastWatch)
        setBadge(0)
    }

    /// Signed in (again): tell this server where to push.
    func signedIn() async {
        defaults.set(Date().timeIntervalSince1970, forKey: Key.lastWatch)
        await registerWithServer()
    }

    // MARK: - Seen

    /// The channel was open: nothing up to `t` needs a notification any more.
    func markWatchSeen(upTo t: Double, unread: Int = 0) {
        if t > defaults.double(forKey: Key.lastWatch) { defaults.set(t, forKey: Key.lastWatch) }
        setBadge(unread)
    }

    func setBadge(_ count: Int) {
        UNUserNotificationCenter.current().setBadgeCount(max(0, count)) { _ in }
    }

    // MARK: - Background refresh

    func scheduleRefresh() {
        let request = BGAppRefreshTaskRequest(identifier: Self.refreshTask)
        request.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)
        try? BGTaskScheduler.shared.submit(request)
    }

    private func handle(_ task: BGAppRefreshTask) async {
        scheduleRefresh()
        let work = Task { await self.checkForNews() }
        task.expirationHandler = { work.cancel() }
        await work.value
        task.setTaskCompleted(success: true)
    }

    /// New watch messages and chats waiting for an OK, as local notifications —
    /// only when real push does not bring them already.
    func checkForNews() async {
        guard allowed, wanted, !pushActive, let client = app?.client ?? Self.storedClient() else { return }
        let last = defaults.double(forKey: Key.lastWatch)
        if let page = try? await client.watchChannel(after: last, limit: 20) {
            let fresh = page.messages.filter { !$0.isUser && $0.t > last && wants($0.importance) }
            for message in fresh.suffix(3) {
                await post(id: "watch-\(message.id)",
                           title: message.title.isEmpty ? "Watch" : message.title,
                           body: message.text, thread: "watch",
                           info: ["kind": "watch", "message": message.id],
                           timeSensitive: message.importance == "critical")
            }
            if let newest = page.messages.map(\.t).max() { defaults.set(newest, forKey: Key.lastWatch) }
            setBadge(page.unread)
        }
        guard assistant, let chats = try? await client.chats(search: "") else { return }
        var notified = Set(defaults.stringArray(forKey: Key.notifiedChats) ?? [])
        for chat in chats where chat.waiting && !notified.contains(chat.id) {
            await post(id: "chat-\(chat.id)", title: "The assistant is waiting for your OK",
                       body: chat.title.isEmpty ? "Open the chat to allow or decline." : chat.title,
                       thread: "chat-\(chat.id)", info: ["kind": "assistant", "chat": chat.id],
                       timeSensitive: false)
            notified.insert(chat.id)
        }
        let stillWaiting = Set(chats.filter(\.waiting).map(\.id))
        defaults.set(Array(notified.intersection(stillWaiting)), forKey: Key.notifiedChats)
    }

    private func wants(_ importance: String) -> Bool {
        let rank = ["info": 0, "important": 1, "critical": 2]
        return (rank[importance] ?? 0) >= (rank[minimum] ?? 0)
    }

    private func post(id: String, title: String, body: String, thread: String,
                      info: [String: String], timeSensitive: Bool) async {
        let content = UNMutableNotificationContent()
        let server = app?.serverName ?? ""
        content.title = preview ? title : (server.isEmpty ? "PocketADM" : server)
        content.body = preview ? body : "New message from your server."
        content.threadIdentifier = thread
        content.sound = .default
        content.userInfo = ["pocketadm": info]
        if timeSensitive { content.interruptionLevel = .timeSensitive }
        let request = UNNotificationRequest(identifier: id, content: content, trigger: nil)
        try? await UNUserNotificationCenter.current().add(request)
    }

    /// The signed-in server without the UI: background refresh can run before
    /// any screen exists. The token's keychain item is readable after the
    /// first unlock (KeychainStore).
    static func storedClient() -> APIClient? {
        guard let raw = UserDefaults.standard.string(forKey: "pocketadm.serverURL"),
              let url = URL(string: raw), let token = KeychainStore.get("authToken") else { return nil }
        return APIClient(baseURL: url, token: token)
    }
}

// MARK: - Presenting and tapping

extension PushManager: UNUserNotificationCenterDelegate {

    /// In front: no banner for what is already on screen.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            willPresent notification: UNNotification) async
        -> UNNotificationPresentationOptions {
        let info = Self.payload(notification.request.content.userInfo)
        return await MainActor.run {
            if info["kind"] == "watch" && channelVisible { return [] }
            if info["kind"] == "assistant", let chat = info["chat"], chat == openChat { return [] }
            return [.banner, .list, .sound]
        }
    }

    /// A tap opens what the notification is about.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            didReceive response: UNNotificationResponse) async {
        let info = Self.payload(response.notification.request.content.userInfo)
        await MainActor.run {
            guard let app else { return }
            if info["kind"] == "assistant", let chat = info["chat"], !chat.isEmpty {
                app.openChat(chat)
            } else {
                app.open(.alerts)
            }
        }
    }

    /// Remote pushes carry {"pocketadm": {...}} next to "aps"; local ones the same.
    nonisolated static func payload(_ userInfo: [AnyHashable: Any]) -> [String: String] {
        guard let raw = userInfo["pocketadm"] as? [String: Any] else { return [:] }
        var out: [String: String] = [:]
        for (key, value) in raw { out[key] = "\(value)" }
        return out
    }
}

/// UIKit's half of notifications: the push token arrives here.
final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        MainActor.assumeIsolated { PushManager.shared.configure() }
        return true
    }

    func application(_ application: UIApplication,
                     didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        MainActor.assumeIsolated { PushManager.shared.didRegister(deviceToken: deviceToken) }
    }

    func application(_ application: UIApplication,
                     didFailToRegisterForRemoteNotificationsWithError error: Error) {
        MainActor.assumeIsolated { PushManager.shared.didFailToRegister(error) }
    }
}
