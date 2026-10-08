import Foundation
import SwiftUI
import UIKit

/// Live connection to a server-side agent session.
///
/// The session is **not** owned by this object: it lives on the server, keeps
/// running while the app is backgrounded, and is shared by every device looking
/// at the same chat. Connecting therefore means "attach and catch up", which is
/// why a snapshot replaces the transcript wholesale rather than merging into it.
@MainActor
final class ChatSocket: ObservableObject {

    enum Status: Equatable {
        case idle
        case connecting
        case connected
        /// The connection dropped and is being re-established on its own.
        case reconnecting
        /// Gave up after several attempts — the banner offers a manual retry.
        case failed(String)
    }

    @Published private(set) var status: Status = .idle
    @Published private(set) var items: [ChatItem] = []
    @Published private(set) var running = false
    @Published private(set) var paused = false
    @Published private(set) var pauseInfo: PauseInfo?
    @Published private(set) var plan: [PlanStep] = []
    @Published private(set) var chatID = ""
    @Published private(set) var title = ""
    /// The tool call waiting on a yes/no. At most one is outstanding, because
    /// the server blocks the run until it is answered.
    @Published private(set) var awaitingApproval: ToolCall?
    @Published var config = ChatConfig()

    private var task: URLSessionWebSocketTask?
    private let session = NetworkSession.make(.default)
    /// Set while the user is deliberately closing, so the receive loop's
    /// failure is not reported as a connection problem.
    private var closing = false

    /// A fresh socket URL per connect: the credential in it is a single-use
    /// ticket, so a reconnect cannot reuse the old one.
    private var urlProvider: (() async throws -> URL)?
    private var connectTask: Task<Void, Never>?
    private var attempts = 0
    /// The app is in the background (or about to be suspended).
    private var away = false
    private var backgroundTask: UIBackgroundTaskIdentifier = .invalid
    /// Messages typed while the connection was being re-established.
    private var outbox: [String] = []
    /// Remembers the open chat per server, so a cold start reopens it.
    private var memoryKey = ""

    // MARK: - Connection

    /// Shown when the socket could not even be opened (no ticket, no network)
    /// — the same banner with a Reconnect button as a dropped connection.
    func fail(_ message: String) {
        task = nil
        running = false
        status = .failed(message)
    }

    /// Attach to a chat ("" = a new one). `server` keys the memory of the
    /// open chat; `provider` fetches a fresh socket URL for every (re)connect.
    func connect(chatID: String, server: String, using provider: @escaping () async throws -> URL) {
        memoryKey = "pocketadm.chat.last." + server
        if chatID.isEmpty, self.chatID.isEmpty,
           let remembered = UserDefaults.standard.string(forKey: memoryKey) {
            self.chatID = remembered
        } else if !chatID.isEmpty {
            self.chatID = chatID
        }
        urlProvider = provider
        attempts = 0
        open()
    }

    /// Whether a connection is up or on its way — the view's `.task` runs on
    /// every appearance and must not tear down a working socket.
    var isActive: Bool {
        switch status {
        case .connected, .connecting, .reconnecting: return true
        default: return false
        }
    }

    private func open() {
        connectTask?.cancel()
        guard let provider = urlProvider else { return }
        closing = true
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        closing = false
        status = attempts == 0 ? .connecting : .reconnecting
        connectTask = Task { [weak self] in
            do {
                let url = try await provider()
                guard let self, !Task.isCancelled else { return }
                self.start(url)
            } catch {
                guard let self, !Task.isCancelled else { return }
                self.lost(error.localizedDescription)
            }
        }
    }

    private func start(_ url: URL) {
        let task = session.webSocketTask(with: url)
        self.task = task
        task.resume()
        receive(on: task)
        // The socket carries no chat identity of its own: the first frame
        // decides which session this device attaches to.
        send(raw: chatID.isEmpty ? ChatProtocol.reset() : ChatProtocol.open(chatID: chatID))
        if away { send(raw: ChatProtocol.away()) }
    }

    /// The connection is gone. In front, try again with growing pauses; in the
    /// background, wait — coming back reconnects at once.
    private func lost(_ message: String) {
        task = nil
        guard !closing else { return }
        if away {
            status = .idle
            return
        }
        attempts += 1
        if attempts > 6 {
            status = .failed(message)
            return
        }
        status = .reconnecting
        let delay = min(30.0, pow(2.0, Double(attempts - 1)))
        connectTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(delay))
            guard let self, !Task.isCancelled else { return }
            self.open()
        }
    }

    /// The Reconnect button.
    func retry() {
        attempts = 0
        open()
    }

    func disconnect() {
        closing = true
        connectTask?.cancel()
        urlProvider = nil
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        endBackgroundTask()
        if status != .idle { status = .idle }
    }

    // MARK: - App lifecycle

    /// Leaving the app. iOS grants some seconds before it suspends us: the
    /// socket stays open for them, so a quick look at another app does not
    /// even reconnect. The server is told nobody is watching, and pushes a
    /// notification when the assistant needs you meanwhile.
    func enterBackground() {
        away = true
        send(raw: ChatProtocol.away())
        endBackgroundTask()
        backgroundTask = UIApplication.shared.beginBackgroundTask(withName: "pocketadm.chat") { [weak self] in
            Task { @MainActor in self?.suspendQuietly() }
        }
    }

    private func suspendQuietly() {
        closing = true
        connectTask?.cancel()
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        if status != .idle { status = .idle }
        endBackgroundTask()
    }

    private func endBackgroundTask() {
        guard backgroundTask != .invalid else { return }
        UIApplication.shared.endBackgroundTask(backgroundTask)
        backgroundTask = .invalid
    }

    /// Back in front: keep a socket that survived, re-attach otherwise. The
    /// snapshot brings everything that happened meanwhile.
    func enterForeground() {
        away = false
        endBackgroundTask()
        guard urlProvider != nil else { return }
        guard let task, status == .connected else {
            attempts = 0
            open()
            return
        }
        // A suspended app's socket can be dead without knowing it; a ping
        // that is not answered within three seconds means reconnect.
        var answered = false
        task.sendPing { [weak self] error in
            Task { @MainActor in
                answered = true
                guard let self, self.task === task else { return }
                if error != nil {
                    self.attempts = 0
                    self.open()
                } else {
                    self.send(raw: ChatProtocol.back())
                }
            }
        }
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(3))
            guard let self, !answered, self.task === task else { return }
            self.attempts = 0
            self.open()
        }
    }

    private func receive(on task: URLSessionWebSocketTask) {
        task.receive { [weak self] result in
            Task { @MainActor in
                guard let self, self.task === task else { return }
                switch result {
                case .success(let message):
                    switch message {
                    case .string(let text):
                        self.apply(ChatProtocol.parse(text))
                    case .data(let data):
                        if let text = String(data: data, encoding: .utf8) {
                            self.apply(ChatProtocol.parse(text))
                        }
                    @unknown default:
                        break
                    }
                    // One `receive` yields exactly one frame; without re-arming
                    // the stream stops after the first message.
                    self.receive(on: task)
                case .failure(let error):
                    self.running = false
                    self.lost(error.localizedDescription)
                }
            }
        }
    }

    // MARK: - Sending

    func submit(_ text: String, context: String = "") {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        let frame = ChatProtocol.user(text: trimmed, context: context)
        guard task != nil, status == .connected else {
            if urlProvider != nil {
                // sent as soon as the connection is back
                outbox.append(frame)
                items.append(ChatItem(kind: .notice, text: "Reconnecting — your message goes out in a moment."))
                if status != .connecting && status != .reconnecting { retry() }
            } else {
                // Without this the composer clears and nothing else happens,
                // which reads exactly like the model ignoring the question.
                items.append(ChatItem(kind: .error, text: "Not connected — the message was not sent."))
            }
            return
        }
        // Echoed back by the server as `user_echo`; appending here as well
        // would show the message twice.
        send(raw: frame)
    }

    func answer(_ call: ToolCall, approved: Bool) {
        send(raw: ChatProtocol.approve(callID: call.callID, approved: approved))
        if awaitingApproval?.callID == call.callID { awaitingApproval = nil }
        update(callID: call.callID) { $0.state = approved ? .running : .denied }
    }

    func stop() {
        send(raw: ChatProtocol.stop())
    }

    func resume() {
        send(raw: ChatProtocol.resume())
        paused = false
        pauseInfo = nil
    }

    func apply(config: ChatConfig) {
        self.config = config
        send(raw: ChatProtocol.config(config))
    }

    /// Retract (or edit and resend) an already-sent message.
    func rewind(to ordinal: Int, text: String = "") {
        send(raw: ChatProtocol.rewind(ordinal: ordinal, text: text))
    }

    func startNewChat() {
        items = []
        plan = []
        chatID = ""
        title = ""
        awaitingApproval = nil
        if !memoryKey.isEmpty { UserDefaults.standard.removeObject(forKey: memoryKey) }
        send(raw: ChatProtocol.reset())
    }

    func openChat(_ id: String) {
        send(raw: ChatProtocol.open(chatID: id))
    }

    private func send(raw: String) {
        guard let task else { return }
        task.send(.string(raw)) { _ in }
    }

    // MARK: - Applying events

    private func apply(_ event: ChatServerEvent) {
        switch event {
        case .snapshot(let snapshot):
            items = snapshot.items
            config = snapshot.config
            running = snapshot.running
            paused = snapshot.paused
            plan = snapshot.plan
            pauseInfo = snapshot.pause
            chatID = snapshot.chatID
            title = snapshot.title
            // a call still waiting for its OK is part of the replay — show the
            // card again instead of a run that seems stuck
            awaitingApproval = snapshot.items.last(where: { $0.tool?.state == .requested })?.tool
            status = .connected
            attempts = 0
            if !memoryKey.isEmpty, !chatID.isEmpty {
                UserDefaults.standard.set(chatID, forKey: memoryKey)
            }
            let queued = outbox
            outbox = []
            for frame in queued { send(raw: frame) }

        case .userEcho(let text, let queued):
            let ordinal = items.filter { $0.kind == .user }.count
            items.append(ChatItem(kind: .user,
                                  text: text,
                                  ordinal: ordinal))
            if queued {
                items.append(ChatItem(kind: .notice,
                                      text: "Queued — it will steer the next step."))
            }

        case .assistantDelta(let delta):
            appendDelta(delta, to: .assistant)

        case .thinkingDelta(let delta):
            appendDelta(delta, to: .thinking)

        case .toolRequest(let call):
            awaitingApproval = call
            items.append(ChatItem(id: call.callID, kind: .tool, text: call.name, tool: call))

        case .toolStart(let call):
            if awaitingApproval?.callID == call.callID { awaitingApproval = nil }
            // A pre-approved call has no request card yet; an approved one does.
            if let index = items.firstIndex(where: { $0.tool?.callID == call.callID }) {
                items[index].tool = call
            } else {
                items.append(ChatItem(id: call.callID, kind: .tool, text: call.name, tool: call))
            }

        case .toolResult(let id, let output, let diff):
            update(callID: id) { call in
                call.output = output
                call.diff = diff
                call.state = output == "[denied by user]" ? .denied : .finished
            }
            if awaitingApproval?.callID == id { awaitingApproval = nil }

        case .plan(let steps):
            plan = steps

        case .runState(let isRunning, let isPaused):
            running = isRunning
            paused = isPaused
            if !isRunning { awaitingApproval = nil }

        case .paused(let info):
            paused = true
            pauseInfo = info

        case .config(let newConfig):
            config = newConfig

        case .chatMeta(let id, let newTitle):
            chatID = id
            title = newTitle
            if !memoryKey.isEmpty, !id.isEmpty {
                UserDefaults.standard.set(id, forKey: memoryKey)
            }

        case .permission(let title, let detail):
            items.append(ChatItem(kind: .notice,
                                  text: detail.isEmpty ? title : "\(title)\n\(detail)"))

        case .failure(let message):
            items.append(ChatItem(kind: .error, text: message))
            running = false

        case .rewound:
            // The server truncates its own history and the UI has already
            // dropped the rows; nothing further to show.
            break

        case .stopped:
            running = false
            awaitingApproval = nil
            items.append(ChatItem(kind: .notice, text: "Stopped."))

        case .done:
            running = false
            awaitingApproval = nil

        case .unknown:
            break
        }
    }

    /// Streaming text arrives one fragment at a time. Each fragment extends the
    /// bubble it belongs to; a new one is started only when the previous row is
    /// something else (a tool card, the user's turn).
    private func appendDelta(_ delta: String, to kind: ChatItem.Kind) {
        guard !delta.isEmpty else { return }
        if let last = items.indices.last, items[last].kind == kind {
            items[last].text += delta
        } else {
            items.append(ChatItem(kind: kind, text: delta))
        }
    }

    private func update(callID: String, _ change: (inout ToolCall) -> Void) {
        guard let index = items.firstIndex(where: { $0.tool?.callID == callID }),
              var call = items[index].tool else { return }
        change(&call)
        items[index].tool = call
    }
}
