import Foundation
import SwiftUI

/// Follows one background job on the server and publishes its log as it
/// arrives.
///
/// Pulling an image, installing an app, rolling back a snapshot and installing
/// a coding CLI all answer with a job id and then stream `text/plain` from
/// `/api/jobs/{id}/stream`. The stream is *not* Server-Sent Events: it is bare
/// lines, with an empty chunk every 15 seconds as a keep-alive.
@MainActor
final class JobRunner: ObservableObject {

    enum Outcome: Equatable {
        case running
        case succeeded
        case failed(String)
    }

    @Published private(set) var lines: [String] = []
    @Published private(set) var outcome: Outcome = .running
    @Published private(set) var title = ""

    private var streamTask: Task<Void, Never>?

    var isRunning: Bool { outcome == .running }

    /// The whole log as one string, for copying out or for a `Text` view.
    var text: String { lines.joined(separator: "\n") }

    func start(jobID: String, title: String, app: AppState) {
        guard streamTask == nil else { return }
        self.title = title
        lines = []
        outcome = .running

        streamTask = Task { [weak self] in
            await self?.follow(jobID: jobID, app: app)
        }
    }

    func cancelFollowing() {
        streamTask?.cancel()
        streamTask = nil
    }

    private func follow(jobID: String, app: AppState) async {
        guard let client = app.client else {
            outcome = .failed("Not connected.")
            return
        }

        do {
            let request = try await client.jobStreamRequest(jobID)
            let (bytes, response) = try await URLSession.shared.bytes(for: request)
            if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                outcome = .failed("The server rejected the log stream (\(http.statusCode)).")
                return
            }
            for try await line in bytes.lines {
                if Task.isCancelled { return }
                // Keep-alives arrive as empty chunks; a blank row per 15s of
                // waiting would push the real output off the screen.
                if line.isEmpty { continue }
                lines.append(line)
                if lines.count > 1200 { lines.removeFirst(lines.count - 1200) }
            }
        } catch {
            // A dropped stream is not a failed job — the work continues on the
            // server, so fall through to asking for the real status.
            lines.append("— log stream ended —")
        }

        await settle(jobID: jobID, app: app)
    }

    /// The stream ends when the job does, but it never says how it went. The
    /// job record is the only place that carries the verdict.
    private func settle(jobID: String, app: AppState) async {
        guard let client = app.client else {
            outcome = .failed("Not connected.")
            return
        }
        // The record flips to its final state a beat after the stream closes.
        for attempt in 0..<3 {
            if attempt > 0 { try? await Task.sleep(for: .milliseconds(700)) }
            guard let status = try? await client.job(jobID) else { continue }
            if status.isRunning { continue }
            outcome = status.succeeded
                ? .succeeded
                : .failed(status.logTail.last ?? "The job failed.")
            return
        }
        // Still running after the stream closed: unusual, but not a failure to
        // report as one.
        outcome = .running
    }
}
