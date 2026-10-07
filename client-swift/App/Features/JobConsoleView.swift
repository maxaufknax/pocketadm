import SwiftUI

/// Live log of one server-side job.
///
/// Every long action in the app (pull an image, install an app, roll back,
/// install a coding CLI) is a job, and they all end here. Showing the real
/// output matters: "installing…" for four minutes is indistinguishable from a
/// hang, while `docker pull` progress is not.
struct JobConsoleView: View {
    let jobID: String
    let title: String
    /// Called once the job settles, so the screen underneath can reload.
    var onFinish: ((Bool) -> Void)? = nil

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss
    @StateObject private var runner = JobRunner()

    var body: some View {
        NavigationStack {
            VStack(spacing: 14) {
                statusBar
                LogConsole(lines: runner.lines, height: nil)
                    .frame(maxHeight: .infinity)
            }
            .padding(16)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(runner.isRunning ? "Hide" : "Done") { dismiss() }
                        .tint(Theme.accent)
                }
            }
            .task {
                runner.start(jobID: jobID, title: title, app: app)
            }
            .onDisappear {
                // The job keeps running on the server; only this device stops
                // listening. Saying otherwise would be a lie about what "Hide"
                // does.
                runner.cancelFollowing()
            }
            .onChange(of: runner.outcome) { _, outcome in
                switch outcome {
                case .running:    break
                case .succeeded:  onFinish?(true)
                case .failed:     onFinish?(false)
                }
            }
        }
        .interactiveDismissDisabled(false)
    }

    @ViewBuilder
    private var statusBar: some View {
        switch runner.outcome {
        case .running:
            HStack(spacing: 10) {
                ProgressView()
                Text("Working — this keeps running even if you close the app.")
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
                Spacer()
            }
        case .succeeded:
            Label("Finished", systemImage: "checkmark.circle.fill")
                .font(.subheadline.weight(.medium))
                .foregroundStyle(Theme.accent2)
                .frame(maxWidth: .infinity, alignment: .leading)
        case .failed(let message):
            VStack(alignment: .leading, spacing: 4) {
                Label("Failed", systemImage: "xmark.octagon.fill")
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(Theme.danger)
                Text(message)
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
                    .lineLimit(3)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// What a screen needs to hand `JobConsoleView` — `Identifiable` so it can drive
/// `.sheet(item:)` directly.
struct PendingJob: Identifiable, Equatable {
    let id: String
    let title: String
}
