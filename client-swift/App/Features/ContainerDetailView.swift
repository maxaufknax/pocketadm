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
        ScrollView {
            VStack(spacing: 14) {
                actionBar
                if let stats, container.isRunning { statsCard(stats) }
                if describing || description != nil { descriptionCard }
                factsCard
                mountsCard
                logsCard
                dangerZone
            }
            .padding(16)
        }
        .background(Theme.bg.ignoresSafeArea())
        .navigationTitle(container.displayName)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Theme.bg2, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
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

                    Menu("Log lines") {
                        ForEach([100, 300, 1000, 2000], id: \.self) { count in
                            Button("\(count)") {
                                logTail = count
                                Task { await loadLogs() }
                            }
                        }
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
                .tint(Theme.accent)
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

    /// A one-shot sample rather than a live graph: `docker stats` costs a full
    /// second of CPU-delta sampling per call on the server, so polling it from
    /// a detail screen would be a real cost for a number that barely moves.
    private func statsCard(_ stats: ContainerStats) -> some View {
        HStack(spacing: 12) {
            miniTile(title: "CPU",
                     value: Fmt.percent(stats.cpuPercent),
                     tint: stats.cpuPercent > 80 ? Theme.warn : Theme.accent)
            miniTile(title: "Memory",
                     value: Fmt.bytes(stats.memUsage),
                     detail: stats.memLimit > 0 ? "of \(Fmt.bytes(stats.memLimit))" : "",
                     tint: stats.memPercent > 85 ? Theme.warn : Theme.accent2)
        }
    }

    private func miniTile(title: String, value: String, detail: String = "",
                          tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title.uppercased())
                .font(.caption2.weight(.semibold))
                .tracking(0.6)
                .foregroundStyle(Theme.muted)
            Text(value)
                .font(.system(.title3, design: .rounded).weight(.semibold))
                .foregroundStyle(tint)
            if !detail.isEmpty {
                Text(detail).font(.caption2).foregroundStyle(Theme.muted)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card(padding: 12)
    }

    private var descriptionCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                SectionCaption(text: "What this is")
                Spacer()
                if describing { ProgressView().tint(Theme.muted).controlSize(.small) }
            }
            if let description {
                MarkdownText(text: description)
            } else {
                Text("Reading the container's config and recent logs…")
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    private var factsCard: some View {
        FactsCard {
            FactRow(label: "Image", value: container.image, selectable: true)
            HairlineDivider()
            FactRow(label: "State", value: detail?.state ?? container.state)
            if let health = detail?.health, !health.isEmpty {
                HairlineDivider()
                FactRow(label: "Health", value: health,
                        tint: health == "healthy" ? Theme.accent2 : Theme.warn)
            }
            if !container.composeProject.isEmpty {
                HairlineDivider()
                FactRow(label: "Stack", value: container.composeProject)
            }
            if !container.composeService.isEmpty {
                HairlineDivider()
                FactRow(label: "Service", value: container.composeService)
            }
            if let policy = detail?.restartPolicy, !policy.isEmpty {
                HairlineDivider()
                FactRow(label: "Restart policy", value: policy)
            }
            if let count = detail?.restartCount {
                HairlineDivider()
                FactRow(label: "Restarts", value: String(count),
                        tint: count > 5 ? Theme.warn : Theme.text)
            }
            if let started = detail?.startedAt, !started.isEmpty {
                HairlineDivider()
                FactRow(label: "Started", value: String(started.prefix(19)))
            }
            if !container.ports.isEmpty {
                HairlineDivider()
                FactRow(label: "Ports", value: portSummary)
            }
            if let networks = detail?.networks, !networks.isEmpty {
                HairlineDivider()
                FactRow(label: "Networks", value: networks.joined(separator: ", "))
            }
            if detail?.privileged == true {
                HairlineDivider()
                FactRow(label: "Privileged", value: "yes — unrestricted on the host",
                        tint: Theme.danger)
            }
            if container.mountsDockerSock {
                // Worth calling out: a container with the socket mounted is
                // effectively root on the host.
                HairlineDivider()
                FactRow(label: "Docker socket", value: "mounted — full host control",
                        tint: Theme.warn)
            }
        }
    }

    @ViewBuilder
    private var mountsCard: some View {
        if let mounts = detail?.mounts, !mounts.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                SectionCaption(text: "Mounts")
                ForEach(Array(mounts.enumerated()), id: \.offset) { _, mount in
                    VStack(alignment: .leading, spacing: 3) {
                        Text(mount.dest)
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundStyle(Theme.text)
                        HStack(spacing: 6) {
                            Text(mount.source)
                                .font(.caption2)
                                .foregroundStyle(Theme.muted)
                                .lineLimit(1)
                                .truncationMode(.middle)
                            StatusPill(text: mount.rw ? "rw" : "ro",
                                       tint: mount.rw ? Theme.warn : Theme.muted)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .card()
        }
    }

    private var portSummary: String {
        container.ports.map { port in
            if let published = port.publicPort { return "\(published)→\(port.privatePort)" }
            return "\(port.privatePort)"
        }
        .joined(separator: ", ")
    }

    private var logsCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                SectionCaption(text: "Logs · last \(logTail)")
                Spacer()
                Button {
                    UIPasteboard.general.string = logs
                    toast = Toast(text: "Logs copied")
                } label: {
                    Image(systemName: "doc.on.doc").font(.caption)
                }
                .tint(Theme.accent)
                .disabled(logs.isEmpty)

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
                LogConsole(lines: logs.components(separatedBy: .newlines),
                           height: 300, follow: false)
            }
        }
        .card()
    }

    private var dangerZone: some View {
        Button(role: .destructive) {
            confirmRemove = true
        } label: {
            Label("Remove container", systemImage: "trash")
        }
        .buttonStyle(SecondaryButtonStyle(tint: Theme.danger))
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
