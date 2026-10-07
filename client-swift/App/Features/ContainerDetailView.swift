import SwiftUI
import UIKit

struct ContainerDetailView: View {
    let container: Container
    let perform: (ContainerAction) async -> Void

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var detail: ContainerDetail?
    @State private var stats: ContainerStats?
    @State private var logs: String = ""
    @State private var logTail = 300
    @State private var loadingLogs = true
    @State private var error: String?
    @State private var confirming: ContainerAction?
    @State private var confirmRemove = false
    @State private var describing = false
    @State private var description: String?
    @State private var toast: Toast?

    var body: some View {
        List {
            Section { header }
                .listRowBackground(Color.clear)

            if let stats, container.isRunning {
                Section("Usage") {
                    HStack(alignment: .top) {
                        RingGauge(title: "CPU", value: Fmt.percent(stats.cpuPercent),
                                  fraction: stats.cpuPercent / 100,
                                  tint: stats.cpuPercent > 80 ? .orange : Theme.accent)
                        RingGauge(title: "Memory", value: Fmt.bytes(stats.memUsage),
                                  detail: stats.memLimit > 0 ? "of \(Fmt.bytes(stats.memLimit))" : "",
                                  fraction: stats.memPercent / 100,
                                  tint: stats.memPercent > 85 ? .orange : .purple)
                    }
                    .padding(.vertical, 8)
                }
            }

            if describing || description != nil {
                Section {
                    if let description {
                        MarkdownText(text: description)
                    } else {
                        HStack(spacing: 10) {
                            ProgressView()
                            Text("Reading the container's config and recent logs…")
                                .font(.footnote)
                                .foregroundStyle(Theme.muted)
                        }
                    }
                } header: {
                    Label("What this is", systemImage: "sparkles")
                }
            }

            Section("Details") { facts }

            if let mounts = detail?.mounts, !mounts.isEmpty {
                Section("Mounts") {
                    ForEach(Array(mounts.enumerated()), id: \.offset) { _, mount in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                Text(mount.dest)
                                    .font(.system(.subheadline, design: .monospaced))
                                    .foregroundStyle(Theme.text)
                                Spacer()
                                StatusPill(text: mount.rw ? "read-write" : "read-only",
                                           tint: mount.rw ? .orange : Theme.muted)
                            }
                            Text(mount.source)
                                .font(.caption)
                                .foregroundStyle(Theme.muted)
                                .lineLimit(1)
                                .truncationMode(.middle)
                        }
                        .padding(.vertical, 2)
                    }
                }
            }

            Section {
                logsContent
            } header: {
                HStack {
                    Text("Logs · last \(logTail) lines")
                    Spacer()
                    Button {
                        UIPasteboard.general.string = logs
                        toast = Toast(text: "Logs copied")
                    } label: {
                        Image(systemName: "doc.on.doc")
                    }
                    .disabled(logs.isEmpty)
                    .accessibilityLabel("Copy logs")
                    Button {
                        Task { await loadLogs() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .accessibilityLabel("Reload logs")
                }
                .textCase(nil)
            }

            Section {
                Button(role: .destructive) {
                    confirmRemove = true
                } label: {
                    Label("Remove container", systemImage: "trash")
                }
            } footer: {
                Text("Named volumes stay on disk, so the service's data survives.")
            }
        }
        .navigationTitle(container.displayName)
        .navigationBarTitleDisplayMode(.inline)
        .toast($toast)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    if app.me?.aiConfigured == true {
                        Button {
                            Task { await describe() }
                        } label: { Label("Explain this container", systemImage: "sparkles") }
                    }
                    Button {
                        UIPasteboard.general.string = container.id
                        toast = Toast(text: "Container id copied")
                    } label: { Label("Copy id", systemImage: "doc.on.doc") }

                    Menu {
                        ForEach([100, 300, 1000, 2000], id: \.self) { count in
                            Button {
                                logTail = count
                                Task { await loadLogs() }
                            } label: {
                                if count == logTail {
                                    Label("\(count) lines", systemImage: "checkmark")
                                } else {
                                    Text("\(count) lines")
                                }
                            }
                        }
                    } label: {
                        Label("Log lines", systemImage: "text.alignleft")
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
            }
        }
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
        .confirmationDialog("Remove \(container.displayName)?",
                            isPresented: $confirmRemove, titleVisibility: .visible) {
            Button("Remove", role: .destructive) { Task { await remove(force: false) } }
            Button("Force remove", role: .destructive) { Task { await remove(force: true) } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The container is deleted. Named volumes stay on disk, so the service's data survives — recreating it from the same compose file picks up where this left off.")
        }
    }

    /// Icon, name, state and the actions that fit the state.
    private var header: some View {
        VStack(spacing: 10) {
            ServiceIcon(names: [container.service?.label ?? "", container.name, container.image],
                        category: container.service?.category ?? "", size: 68)
            VStack(spacing: 4) {
                Text(container.displayName)
                    .font(.title2.weight(.bold))
                    .foregroundStyle(Theme.text)
                    .multilineTextAlignment(.center)
                StatusDot(text: container.status.isEmpty ? container.state : container.status,
                          tint: stateTint)
            }
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
                            .font(.subheadline.weight(.semibold))
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .buttonBorderShape(.capsule)
                    .controlSize(.large)
                    .tint(action == .stop ? .red : Theme.accent)
                }
            }
            .padding(.top, 6)
        }
        .frame(maxWidth: .infinity)
    }

    private var stateTint: Color {
        switch container.state {
        case "running":              return container.health == "unhealthy" ? .orange : .green
        case "restarting", "paused": return .orange
        default:                     return Color(uiColor: .systemGray3)
        }
    }

    private var available: [ContainerAction] {
        container.isRunning ? [.restart, .stop] : [.start]
    }

    @ViewBuilder
    private var facts: some View {
        FactRow(label: "Image", value: container.image, selectable: true)
        FactRow(label: "State", value: detail?.state ?? container.state)
        if let health = detail?.health, !health.isEmpty {
            FactRow(label: "Health", value: health,
                    tint: health == "healthy" ? .green : .orange)
        }
        if !container.composeProject.isEmpty {
            FactRow(label: "Stack", value: container.composeProject)
        }
        if !container.composeService.isEmpty {
            FactRow(label: "Service", value: container.composeService)
        }
        if let policy = detail?.restartPolicy, !policy.isEmpty {
            FactRow(label: "Restart policy", value: policy)
        }
        if let count = detail?.restartCount {
            FactRow(label: "Restarts", value: String(count),
                    tint: count > 5 ? .orange : Theme.muted)
        }
        if let started = detail?.startedAt, !started.isEmpty {
            FactRow(label: "Started", value: String(started.prefix(19)).replacingOccurrences(of: "T", with: " "))
        }
        if !container.ports.isEmpty {
            FactRow(label: "Ports", value: portSummary)
        }
        if let networks = detail?.networks, !networks.isEmpty {
            FactRow(label: "Networks", value: networks.joined(separator: ", "))
        }
        if detail?.privileged == true {
            FactRow(label: "Privileged", value: "Unrestricted on the host", tint: .red)
        }
        if container.mountsDockerSock {
            // Worth calling out: a container with the socket mounted is
            // effectively root on the host.
            FactRow(label: "Docker socket", value: "Full host control", tint: .orange)
        }
    }

    private var portSummary: String {
        container.ports.map { port in
            if let published = port.publicPort { return "\(published)→\(port.privatePort)" }
            return "\(port.privatePort)"
        }
        .joined(separator: ", ")
    }

    @ViewBuilder
    private var logsContent: some View {
        if loadingLogs {
            HStack {
                Spacer()
                ProgressView()
                Spacer()
            }
            .padding(.vertical, 20)
        } else if logs.isEmpty {
            Text(error ?? "No log output.")
                .font(.footnote)
                .foregroundStyle(error == nil ? Theme.muted : Theme.danger)
        } else {
            // Log lines are long and must not wrap into unreadable mush,
            // so the console scrolls horizontally inside its own box.
            LogConsole(lines: logs.components(separatedBy: .newlines),
                       height: 320, follow: false)
                .listRowInsets(EdgeInsets())
                .listRowBackground(Theme.termBg)
        }
    }

    // MARK: - Loading

    private func load() async {
        guard let client = app.client else { return }
        detail = try? await client.containerDetail(container.id)
        if container.isRunning {
            stats = try? await client.containerStats(container.id)
        }
        await loadLogs()
    }

    private func loadLogs() async {
        guard let client = app.client else { return }
        loadingLogs = true
        defer { loadingLogs = false }
        do {
            logs = try await client.containerLogs(container.id, tail: logTail)
            error = nil
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
    }

    private func describe() async {
        guard let client = app.client, !describing else { return }
        describing = true
        description = nil
        defer { describing = false }
        do {
            description = try await client.describeContainer(container.id)
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func remove(force: Bool) async {
        guard let client = app.client else { return }
        do {
            try await client.removeContainer(container.id, force: force)
            dismiss()
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
            app.handle(error)
        }
    }
}
