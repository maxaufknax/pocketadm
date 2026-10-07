import Foundation
import SwiftUI

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

    // MARK: - Connection

    /// Shown when the socket could not even be opened (no ticket, no network)
    /// — the same banner with a Reconnect button as a dropped connection.
    func fail(_ message: String) {
        task = nil
        running = false
        status = .failed(message)
    }

    func connect(to url: URL, chatID: String) {
        disconnect()
        closing = false
        status = .connecting
        let task = session.webSocketTask(with: url)
        self.task = task
        task.resume()
        status = .connected
        receive(on: task)
        // The socket carries no chat identity of its own: the first frame
        // decides which session this device attaches to.
        send(raw: chatID.isEmpty ? ChatProtocol.reset() : ChatProtocol.open(chatID: chatID))
    }

    func disconnect() {
        closing = true
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        if status != .idle { status = .idle }
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
                    guard !self.closing else { return }
                    self.status = .failed(error.localizedDescription)
                    self.running = false
                    self.task = nil
                }
            }
        }
    }

    // MARK: - Sending

    func submit(_ text: String, context: String = "") {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        guard task != nil else {
            // Without this the composer clears and nothing else happens, which
            // reads exactly like the model ignoring the question.
            items.append(ChatItem(kind: .error,
                                  text: "Not connected — the message was not sent."))
            return
        }
        // Echoed back by the server as `user_echo`; appending here as well
        // would show the message twice.
        send(raw: ChatProtocol.user(text: trimmed, context: context))
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
            awaitingApproval = nil

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
