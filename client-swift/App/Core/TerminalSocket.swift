import Foundation

/// Bridges `/ws/terminal` to a terminal emulator.
///
/// Wire protocol (from server/terminal.py):
///   * client → server: JSON **text** frames
///       `{"type":"input","data":"..."}`
///       `{"type":"resize","cols":N,"rows":N}`
///   * server → client: **raw** text frames carrying PTY output — not JSON.
///     Anything that tries to parse them as JSON gets nothing but errors.
@MainActor
final class TerminalSocket: ObservableObject {

    enum Status: Equatable {
        case idle
        case connecting
        case connected
        case closed(String?)
    }

    @Published private(set) var status: Status = .idle

    /// PTY output, handed over as it arrives.
    var onOutput: ((String) -> Void)?
    /// Called before a re-attach replays the scrollback, so the emulator can
    /// start clean instead of showing everything twice.
    var onReattach: (() -> Void)?

    private var task: URLSessionWebSocketTask?
    private let session = NetworkSession.make(.default)
    /// A fresh URL (single-use ticket) for every connect.
    private var urlProvider: (() async throws -> URL)?
    private var connectTask: Task<Void, Never>?
    private var closing = false
    private var attachedOnce = false
    private var attempts = 0
    private var away = false
    /// The liveness check after coming back: pings sent and answered.
    private var pingsSent = 0
    private var pingsAnswered = 0

    /// The socket could not be opened at all (no ticket, no network).
    func fail(_ message: String) {
        task = nil
        status = .closed(message)
    }

    func connect(using provider: @escaping () async throws -> URL) {
        urlProvider = provider
        attempts = 0
        open()
    }

    private func open() {
        connectTask?.cancel()
        guard let provider = urlProvider else { return }
        closing = true
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        closing = false
        status = .connecting
        connectTask = Task { [weak self] in
            do {
                let url = try await provider()
                guard let self, !Task.isCancelled else { return }
                self.start(url)
            } catch {
                guard let self, !Task.isCancelled else { return }
                self.status = .closed(error.localizedDescription)
            }
        }
    }

    private func start(_ url: URL) {
        // the server replays the session's scrollback on every attach
        if attachedOnce { onReattach?() }
        attachedOnce = true
        let task = session.webSocketTask(with: url)
        self.task = task
        task.resume()
        status = .connected
        receiveLoop(task)
    }

    /// One `receive` only ever delivers a single message, so every handler has
    /// to re-arm. Forgetting that yields a terminal that prints exactly one
    /// frame and then goes silent forever.
    private func receiveLoop(_ task: URLSessionWebSocketTask) {
        task.receive { [weak self] result in
            Task { @MainActor in
                guard let self, self.task === task else { return }
                switch result {
                case .success(let message):
                    switch message {
                    case .string(let text):
                        self.onOutput?(text)
                    case .data(let data):
                        // The server sends text frames, but a binary frame is
                        // still valid PTY bytes — decode leniently rather than
                        // dropping output on the floor.
                        if let text = String(data: data, encoding: .utf8) {
                            self.onOutput?(text)
                        }
                    @unknown default:
                        break
                    }
                    self.receiveLoop(task)
                case .failure(let error):
                    self.task = nil
                    guard !self.closing else { return }
                    self.status = .closed(error.localizedDescription)
                    // the session lives on the server: in front, re-attach
                    if !self.away, self.attempts < 3 {
                        self.attempts += 1
                        let delay = Double(self.attempts)
                        self.connectTask = Task { [weak self] in
                            try? await Task.sleep(for: .seconds(delay))
                            guard let self, !Task.isCancelled else { return }
                            self.open()
                        }
                    }
                }
            }
        }
    }

    // MARK: - App lifecycle

    func enterBackground() { away = true }

    /// Back in front: a suspended app's socket can be dead without knowing it,
    /// so ping, and re-attach when there is no answer within three seconds.
    func enterForeground() {
        away = false
        guard urlProvider != nil else { return }
        guard let task, status == .connected else {
            attempts = 0
            open()
            return
        }
        pingsSent += 1
        let ping = pingsSent
        task.sendPing { [weak self] error in
            Task { @MainActor in
                guard let self, self.task === task else { return }
                self.pingsAnswered = max(self.pingsAnswered, ping)
                guard error != nil else { return }
                self.attempts = 0
                self.open()
            }
        }
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(3))
            guard let self, self.pingsAnswered < ping, self.task === task else { return }
            self.attempts = 0
            self.open()
        }
    }

    func send(input: String) {
        send(json: ["type": "input", "data": input])
    }

    func send(cols: Int, rows: Int) {
        guard cols > 0, rows > 0 else { return }
        send(json: ["type": "resize", "cols": cols, "rows": rows])
    }

    private func send(json object: [String: Any]) {
        guard let task,
              let data = try? JSONSerialization.data(withJSONObject: object),
              let text = String(data: data, encoding: .utf8) else { return }
        task.send(.string(text)) { _ in }
    }

    func disconnect() {
        closing = true
        connectTask?.cancel()
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        if status != .idle { status = .closed(nil) }
    }
}
