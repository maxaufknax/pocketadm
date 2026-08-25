import SwiftUI

struct ContainerDetailView: View {
    let container: Container
    let perform: (ContainerAction) async -> Void

    @EnvironmentObject private var app: AppState
    @State private var detail: ContainerDetail?
    @State private var logs: String = ""
    @State private var loadingLogs = true
    @State private var error: String?
    @State private var confirming: ContainerAction?

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                actionBar
                factsCard
                logsCard
            }
            .padding(16)
        }
        .background(Theme.bg.ignoresSafeArea())
        .navigationTitle(container.displayName)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Theme.bg2, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .task { await load() }
        .refreshable { await load() }
        .confirmationDialog(
            confirming.map { "\($0.label) \(container.displayName)?" } ?? "",
            isPresented: .init(get: { confirming != nil }, set: { if !$0 { confirming = nil } }),
            titleVisibility: .visible
        ) {
            if let action = confirming {
                Button(action.label, role: action == .start ? nil : ButtonRole.destructive) {
                    Task { await perform(action); await load() }
                }
            }
            Button("Cancel", role: .cancel) {}
        }
    }

    private var actionBar: some View {
        HStack(spacing: 10) {
            ForEach(available, id: \.self) { action in
                Button {
                    // Stopping something on a live server deserves a beat of
                    // friction; starting does not.
                    if action == .start {
                        Task { await perform(action); await load() }
                    } else {
                        confirming = action
                    }
                } label: {
                    Label(action.label, systemImage: action.symbol)
                        .font(.subheadline.weight(.medium))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 11)
                }
                .background(Theme.bg3, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .foregroundStyle(action == .stop ? Theme.danger : Theme.text)
            }
        }
    }

    private var available: [ContainerAction] {
        container.isRunning ? [.restart, .stop] : [.start]
    }

    private var factsCard: some View {
        VStack(spacing: 0) {
            fact("Image", container.image)
            fact("State", detail?.state ?? container.state)
            if let health = detail?.health, !health.isEmpty { fact("Health", health) }
            if !container.composeService.isEmpty { fact("Service", container.composeService) }
            if let policy = detail?.restartPolicy, !policy.isEmpty { fact("Restart policy", policy) }
            if let count = detail?.restartCount { fact("Restarts", String(count)) }
            if !container.ports.isEmpty { fact("Ports", portSummary) }
            if let networks = detail?.networks, !networks.isEmpty {
                fact("Networks", networks.joined(separator: ", "))
            }
            if container.mountsDockerSock {
                // Worth calling out: a container with the socket mounted is
                // effectively root on the host.
                fact("Docker socket", "mounted — full host control", tint: Theme.warn)
            }
        }
        .card(padding: 0)
    }

    private var portSummary: String {
        container.ports.map { port in
            if let published = port.publicPort { return "\(published)→\(port.privatePort)" }
            return "\(port.privatePort)"
        }
        .joined(separator: ", ")
    }

    private func fact(_ label: String, _ value: String, tint: Color = Theme.text) -> some View {
        VStack(spacing: 0) {
            HStack(alignment: .top) {
                Text(label)
                    .font(.subheadline)
                    .foregroundStyle(Theme.muted)
                Spacer(minLength: 16)
                Text(value)
                    .font(.subheadline)
                    .foregroundStyle(tint)
                    .multilineTextAlignment(.trailing)
                    .textSelection(.enabled)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 11)
            Divider().overlay(Theme.border)
        }
    }

    private var logsCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("LOGS")
                    .font(.caption2.weight(.semibold))
                    .tracking(0.6)
                    .foregroundStyle(Theme.muted)
                Spacer()
                Button {
                    Task { await loadLogs() }
                } label: {
                    Image(systemName: "arrow.clockwise").font(.caption)
                }
                .tint(Theme.accent)
            }

            if loadingLogs {
                ProgressView().tint(Theme.muted).frame(maxWidth: .infinity).padding(.vertical, 20)
            } else if logs.isEmpty {
                Text(error ?? "No log output.")
                    .font(.caption)
                    .foregroundStyle(error == nil ? Theme.muted : Theme.danger)
                    .padding(.vertical, 10)
            } else {
                // Log lines are long and must not wrap into unreadable mush,
                // so the console scrolls horizontally inside its own box.
                ScrollView([.horizontal, .vertical]) {
                    Text(logs)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(Theme.termFg)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(10)
                }
                .frame(height: 260)
                .background(Theme.termBg, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            }
        }
        .card()
    }

    private func load() async {
        guard let client = app.client else { return }
        detail = try? await client.containerDetail(container.id)
        await loadLogs()
    }

    private func loadLogs() async {
        guard let client = app.client else { return }
        loadingLogs = true
        defer { loadingLogs = false }
        do {
            logs = try await client.containerLogs(container.id, tail: 300)
            error = nil
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
    }
}
