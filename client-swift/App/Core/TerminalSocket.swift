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

    private var task: URLSessionWebSocketTask?
    private let session = NetworkSession.make(.default)

    /// The socket could not be opened at all (no ticket, no network).
    func fail(_ message: String) {
        task = nil
        status = .closed(message)
    }

    func connect(to url: URL) {
        disconnect()
        status = .connecting
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
                    self.status = .closed(error.localizedDescription)
                    self.task = nil
                }
            }
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
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        if status != .idle { status = .closed(nil) }
    }
}
