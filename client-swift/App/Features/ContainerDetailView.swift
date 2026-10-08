import Charts
import SwiftUI
import UIKit

/// Polls a container's resource use while its screen is open: the cheap
/// one-shot form, every three seconds, with a short history for sparklines.
@MainActor
final class ContainerLiveStats: ObservableObject {
    @Published var stats: ContainerStats?
    @Published var cpu: [Double] = []
    @Published var memory: [Double] = []
    private var ticker: Task<Void, Never>?

    func start(_ cid: String, app: AppState) {
        guard ticker == nil else { return }
        let live = app.supports("container_live")
        ticker = Task { [weak self] in
            while !Task.isCancelled {
                guard let client = app.client else { return }
                let sample: ContainerStats?
                if live {
                    sample = try? await client.containerStats(cid, live: true)
                } else {
                    sample = try? await client.containerStats(cid)
                }
                if let sample { self?.add(sample) }
                // older servers sample for a full second per call: poll less
                try? await Task.sleep(for: .seconds(live ? 3 : 10))
            }
        }
    }

    func stop() {
        ticker?.cancel()
        ticker = nil
    }

    private func add(_ sample: ContainerStats) {
        stats = sample
        cpu.append(sample.cpuPercent)
        memory.append(sample.memPercent)
        if cpu.count > 40 { cpu.removeFirst(cpu.count - 40) }
        if memory.count > 40 { memory.removeFirst(memory.count - 40) }
    }
}

struct ContainerDetailView: View {
    let container: Container
    let perform: (ContainerAction) async -> Void

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss
    @StateObject private var live = ContainerLiveStats()

    @State private var detail: ContainerDetail?
    @State private var events: [ContainerEvent] = []
    @State private var logs: String = ""
    @State private var loadingLogs = true
    @State private var error: String?
    @State private var confirming: ContainerAction?
    @State private var confirmKill = false
    @State private var confirmRemove = false
    @State private var describing = false
    @State private var description: String?
    @State private var showProcesses = false
    @State private var shell: TerminalSession?
    @State private var openingShell = false
    @State private var working = false
    @State private var toast: Toast?

    /// The state as last read from the server — the value this screen was
    /// opened with goes stale after the first action.
    private var state: String { detail?.state ?? container.state }
    private var isRunning: Bool { state == "running" }
    private var isPaused: Bool { state == "paused" }

    var body: some View {
        ThemedList {
            Section { header }
                .listRowBackground(Color.clear)

            Section { quickActions }
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets(top: 0, leading: 0, bottom: 0, trailing: 0))

            problemSection

            if isRunning, let stats = live.stats {
                Section {
                    usage(stats)
                } header: {
                    HStack {
                        Text("Live")
                        Circle().fill(.green).frame(width: 6, height: 6)
                    }
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

            networkSection

            if let mounts = detail?.mounts, !mounts.isEmpty {
                Section("Storage") {
                    ForEach(Array(mounts.enumerated()), id: \.offset) { _, mount in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                Text(mount.dest)
                                    .font(.system(.subheadline, design: .monospaced))
                                    .foregroundStyle(Theme.text)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                                Spacer()
                                StatusPill(text: mount.rw ? "read-write" : "read-only",
                                           tint: mount.rw ? .orange : Theme.muted)
                            }
                            Text(mount.type == "volume" && !mount.name.isEmpty ? "volume \(mount.name)" : mount.source)
                                .font(.caption)
                                .foregroundStyle(Theme.muted)
                                .lineLimit(1)
                                .truncationMode(.middle)
                        }
                        .padding(.vertical, 2)
                    }
                }
            }

            if let env = detail?.env, !env.isEmpty {
                Section {
                    DisclosureGroup("\(env.count) environment variables") {
                        ForEach(env) { variable in
                            VStack(alignment: .leading, spacing: 2) {
                                HStack(spacing: 6) {
                                    Text(variable.key)
                                        .font(.system(.caption, design: .monospaced).weight(.semibold))
                                        .foregroundStyle(Theme.text)
                                    if variable.secret {
                                        Image(systemName: "lock.fill")
                                            .font(.caption2)
                                            .foregroundStyle(Theme.muted)
                                    }
                                }
                                Text(variable.value.isEmpty ? "—" : variable.value)
                                    .font(.system(.caption, design: .monospaced))
                                    .foregroundStyle(Theme.muted)
                                    .textSelection(.enabled)
                                    .lineLimit(3)
                            }
                        }
                    }
                } footer: {
                    Text("Values that could be a password or token are hidden.")
                }
            }

            if !events.isEmpty {
                Section("Lately") {
                    ForEach(events.prefix(8)) { event in
                        HStack(spacing: 10) {
                            Image(systemName: event.severity.symbol)
                                .foregroundStyle(event.severity == .ok ? Theme.muted : event.severity.tint)
                                .font(.footnote)
                            Text(event.summary.prefix(1).uppercased() + event.summary.dropFirst())
                                .foregroundStyle(Theme.text)
                            Spacer()
                            Text(Fmt.ago(event.date))
                                .font(.footnote)
                                .foregroundStyle(Theme.muted)
                        }
                    }
                }
            }

            Section {
                logsContent
                NavigationLink {
                    ContainerLogsView(container: container)
                } label: {
                    Label("All logs, live", systemImage: "text.alignleft")
                }
            } header: {
                HStack {
                    Text("Recent logs")
                    Spacer()
                    Button {
                        UIPasteboard.general.string = logs
                        toast = Toast(text: "Logs copied")
                    } label: {
                        Image(systemName: "doc.on.doc")
                    }
                    .disabled(logs.isEmpty)
                    .accessibilityLabel("Copy logs")
                }
                .textCase(nil)
            }

            if let detail, !detail.composeDir.isEmpty || !container.composeProject.isEmpty {
                Section("Defined in") {
                    if !container.composeProject.isEmpty {
                        FactRow(label: "Stack", value: container.composeProject)
                    }
                    if !container.composeService.isEmpty {
                        FactRow(label: "Service", value: container.composeService)
                    }
                    if !detail.composeDir.isEmpty {
                        NavigationLink {
                            FolderView(path: detail.composeDir)
                        } label: {
                            FactRow(label: "Folder", value: detail.composeDir)
                        }
                    }
                }
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
            ToolbarItem(placement: .topBarTrailing) { moreMenu }
        }
        .task { await load() }
        .onAppear { live.start(container.id, app: app) }
        .onDisappear { live.stop() }
        .refreshable { await load() }
        .sheet(isPresented: $showProcesses) {
            ProcessesSheet(container: container)
        }
        .navigationDestination(item: $shell) { session in
            TerminalSessionView(session: session)
        }
        .confirmationDialog(
            confirming.map { "\($0.label) \(container.displayName)?" } ?? "",
            isPresented: .init(get: { confirming != nil }, set: { if !$0 { confirming = nil } }),
            titleVisibility: .visible
        ) {
            if let action = confirming {
                Button(action.label, role: action == .start ? nil : ButtonRole.destructive) {
                    Task { await run(action) }
                }
            }
            Button("Cancel", role: .cancel) {}
        }
        .confirmationDialog("Kill \(container.displayName)?", isPresented: $confirmKill,
                            titleVisibility: .visible) {
            Button("Kill", role: .destructive) { Task { await command("kill") } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Stops it at once, without letting it save its work. For a container that does not react to Stop.")
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

    // MARK: - Header and actions

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
                StatusDot(text: statusText, tint: stateTint)
                if let group = container.groupName, let role = container.role, role != "App" {
                    Text("\(role) of \(group)")
                        .font(.footnote)
                        .foregroundStyle(Theme.muted)
                }
            }
            HStack(spacing: 10) {
                ForEach(available, id: \.self) { action in
                    Button {
                        // Stopping something on a live server deserves a beat of
                        // friction; starting does not.
                        if action == .start {
                            Task { await run(action) }
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
                    .disabled(working)
                }
                if isPaused {
                    Button {
                        Task { await command("unpause") }
                    } label: {
                        Label("Resume", systemImage: "playpause.fill")
                            .font(.subheadline.weight(.semibold))
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .buttonBorderShape(.capsule)
                    .controlSize(.large)
                    .disabled(working)
                }
            }
            .padding(.top, 6)
        }
        .frame(maxWidth: .infinity)
    }

    private var available: [ContainerAction] {
        if isPaused { return [] }
        return isRunning ? [.restart, .stop] : [.start]
    }

    private var statusText: String {
        if let detail, detail.state != container.state {
            return detail.state.prefix(1).uppercased() + detail.state.dropFirst()
        }
        return container.status.isEmpty ? container.state : container.status
    }

    private var stateTint: Color {
        switch state {
        case "running":              return (detail?.health ?? container.health) == "unhealthy" ? .orange : .green
        case "restarting", "paused": return .orange
        case "dead":                 return .red
        default:                     return Color(uiColor: .systemGray3)
        }
    }

    private var moreMenu: some View {
        Menu {
            if isRunning {
                Button {
                    Task { await command("pause") }
                } label: { Label("Pause", systemImage: "pause.fill") }
                Button(role: .destructive) {
                    confirmKill = true
                } label: { Label("Kill", systemImage: "bolt.slash.fill") }
            }
            if isPaused {
                Button {
                    Task { await command("unpause") }
                } label: { Label("Resume", systemImage: "playpause.fill") }
            }
            if app.supports("container_live") {
                Menu {
                    ForEach(Self.policies, id: \.0) { policy in
                        Button {
                            Task { await setPolicy(policy.0) }
                        } label: {
                            if detail?.restartPolicy == policy.0 {
                                Label(policy.1, systemImage: "checkmark")
                            } else {
                                Text(policy.1)
                            }
                        }
                    }
                } label: {
                    Label("When it stops…", systemImage: "arrow.triangle.2.circlepath")
                }
            }
            Divider()
            if app.me?.aiConfigured == true {
                Button {
                    Task { await describe() }
                } label: { Label("Explain this container", systemImage: "sparkles") }
            }
            Button {
                UIPasteboard.general.string = container.id
                toast = Toast(text: "Container id copied")
            } label: { Label("Copy id", systemImage: "doc.on.doc") }
        } label: {
            Image(systemName: "ellipsis.circle")
        }
    }

    private static let policies: [(String, String)] = [
        ("unless-stopped", "Restart unless I stopped it"),
        ("always", "Always restart"),
        ("on-failure", "Restart only after a crash"),
        ("no", "Never restart"),
    ]

    private var quickActions: some View {
        HStack(spacing: 10) {
            QuickActionTile(symbol: "terminal.fill", title: "Shell", tint: .gray,
                            busy: openingShell, disabled: !isRunning) {
                Task { await openShell() }
            }
            NavigationLink {
                ContainerLogsView(container: container)
            } label: {
                QuickActionLabel(symbol: "text.alignleft", title: "Logs", tint: .indigo)
            }
            .buttonStyle(.plain)
            QuickActionTile(symbol: "list.number", title: "Processes", tint: .teal,
                            disabled: !isRunning) {
                showProcesses = true
            }
            QuickActionTile(symbol: "sparkles", title: "Ask AI", tint: .purple,
                            disabled: app.me?.aiConfigured != true) {
                app.ask(assistantPrompt)
            }
        }
    }

    private var assistantPrompt: String {
        var text = "Look at the container \(container.name) (image \(container.image)) on this server. "
        text += "It is \(statusText.lowercased())."
        if let health = detail?.health, !health.isEmpty { text += " Health: \(health)." }
        text += " Check its logs and configuration and tell me whether something is wrong and what to do."
        return text
    }

    // MARK: - Sections

    @ViewBuilder
    private var problemSection: some View {
        if let detail {
            if detail.health == "unhealthy", let probe = detail.healthLog.first {
                Section {
                    VStack(alignment: .leading, spacing: 6) {
                        Label("Its health check fails", systemImage: "cross.case.fill")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.orange)
                        Text(probe.output.isEmpty ? "The check returned code \(probe.exitCode)." : probe.output)
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(Theme.muted)
                            .textSelection(.enabled)
                            .lineLimit(6)
                    }
                }
            } else if !isRunning && !isPaused && (detail.oomKilled || (detail.exitCode ?? 0) != 0 || !detail.error.isEmpty) {
                Section {
                    VStack(alignment: .leading, spacing: 6) {
                        Label(detail.oomKilled ? "It ran out of memory" : "It stopped with an error",
                              systemImage: "exclamationmark.octagon.fill")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(Theme.danger)
                        Text(problemText(detail))
                            .font(.footnote)
                            .foregroundStyle(Theme.muted)
                            .textSelection(.enabled)
                    }
                }
            }
        }
    }

    private func problemText(_ detail: ContainerDetail) -> String {
        var parts: [String] = []
        if let code = detail.exitCode, code != 0 { parts.append("Exit code \(code).") }
        if detail.oomKilled { parts.append("The kernel killed it because the memory ran out.") }
        if !detail.error.isEmpty { parts.append(detail.error) }
        if let finished = Fmt.isoDate(detail.finishedAt) { parts.append("Stopped \(Fmt.ago(finished)).") }
        return parts.joined(separator: " ")
    }

    private func usage(_ stats: ContainerStats) -> some View {
        VStack(spacing: 14) {
            HStack(alignment: .top) {
                RingGauge(title: "CPU", value: Fmt.percent(stats.cpuPercent),
                          fraction: min(1, stats.cpuPercent / 100),
                          tint: stats.cpuPercent > 80 ? .orange : Theme.accent)
                RingGauge(title: "Memory", value: Fmt.bytes(stats.memUsage),
                          detail: stats.memLimit > 0 ? "of \(Fmt.bytes(stats.memLimit))" : "",
                          fraction: stats.memPercent / 100,
                          tint: stats.memPercent > 85 ? .orange : .purple)
            }
            if live.cpu.count > 2 {
                Chart {
                    ForEach(Array(live.cpu.enumerated()), id: \.offset) { index, value in
                        LineMark(x: .value("t", index), y: .value("CPU", value),
                                 series: .value("Series", "CPU"))
                            .foregroundStyle(Theme.accent)
                            .interpolationMethod(.monotone)
                    }
                    ForEach(Array(live.memory.enumerated()), id: \.offset) { index, value in
                        LineMark(x: .value("t", index), y: .value("Memory", value),
                                 series: .value("Series", "Memory"))
                            .foregroundStyle(Color.purple)
                            .interpolationMethod(.monotone)
                    }
                }
                .chartXAxis(.hidden)
                .chartYAxis {
                    AxisMarks(position: .leading, values: .automatic(desiredCount: 3)) { value in
                        AxisGridLine()
                        AxisValueLabel { Text("\(Int(value.as(Double.self) ?? 0))%") }
                    }
                }
                .frame(height: 80)
            }
            HStack(spacing: 0) {
                miniStat("arrow.down", "In", stats.netRxRate.map { Fmt.rate($0) } ?? Fmt.bytes(stats.netRx))
                miniStat("arrow.up", "Out", stats.netTxRate.map { Fmt.rate($0) } ?? Fmt.bytes(stats.netTx))
                miniStat("internaldrive", "Disk I/O", Fmt.bytes(stats.blockRead + stats.blockWrite))
                if stats.pids > 0 {
                    Button { showProcesses = true } label: {
                        miniStat("list.number", "Processes", String(stats.pids))
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .padding(.vertical, 6)
    }

    private func miniStat(_ symbol: String, _ label: String, _ value: String) -> some View {
        VStack(spacing: 3) {
            Label(label, systemImage: symbol)
                .font(.caption2)
                .foregroundStyle(Theme.muted)
            Text(value)
                .font(.footnote.weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(Theme.text)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder
    private var facts: some View {
        FactRow(label: "Image", value: container.image, selectable: true)
        if let health = detail?.health, !health.isEmpty {
            FactRow(label: "Health", value: health,
                    tint: health == "healthy" ? .green : .orange)
        }
        if let started = detail?.startedAt, isRunning, let date = Fmt.isoDate(started) {
            FactRow(label: "Running since", value: Fmt.ago(date))
        }
        if let policy = detail?.restartPolicy {
            FactRow(label: "When it stops", value: Self.policies.first { $0.0 == policy }?.1
                    ?? (policy.isEmpty ? "Never restart" : policy),
                    tint: policy.isEmpty || policy == "no" ? .orange : Theme.muted)
        }
        if let count = detail?.restartCount, count > 0 {
            FactRow(label: "Restarts", value: String(count),
                    tint: count > 5 ? .orange : Theme.muted)
        }
        if let detail {
            if !detail.command.isEmpty {
                FactRow(label: "Command", value: detail.command, selectable: true)
            }
            if !detail.workingDir.isEmpty {
                FactRow(label: "Working folder", value: detail.workingDir)
            }
            if !detail.user.isEmpty {
                FactRow(label: "Runs as", value: detail.user)
            }
            if let resources = detail.resources, resources.memoryLimit > 0 || resources.cpus > 0 {
                FactRow(label: "Limits", value: [
                    resources.memoryLimit > 0 ? "\(Fmt.bytes(resources.memoryLimit)) memory" : nil,
                    resources.cpus > 0 ? String(format: "%.1f CPUs", resources.cpus) : nil,
                ].compactMap { $0 }.joined(separator: " · "))
            }
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

    @ViewBuilder
    private var networkSection: some View {
        let bindings = detail?.portBindings ?? []
        let networks = detail?.networkDetails ?? []
        if !bindings.isEmpty || !networks.isEmpty || !container.ports.isEmpty {
            Section("Network") {
                if !bindings.isEmpty {
                    ForEach(Array(bindings.enumerated()), id: \.offset) { _, port in
                        portRow(port)
                    }
                } else {
                    ForEach(Array(container.ports.enumerated()), id: \.offset) { _, port in
                        FactRow(label: "Port \(port.privatePort)",
                                value: port.publicPort.map { "published on \($0)" } ?? "internal")
                    }
                }
                ForEach(networks) { network in
                    HStack {
                        Text(network.name)
                            .foregroundStyle(Theme.text)
                            .lineLimit(1)
                        Spacer()
                        Text(network.ip.isEmpty ? "—" : network.ip)
                            .font(.system(.subheadline, design: .monospaced))
                            .foregroundStyle(Theme.muted)
                            .textSelection(.enabled)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func portRow(_ port: ContainerDetail.PortBinding) -> some View {
        if let published = port.publicPort, port.isOpen,
           let host = app.serverURL?.host, let url = URL(string: "http://\(host):\(published)") {
            Link(destination: url) {
                HStack {
                    Text("\(published) → \(port.privatePort)/\(port.proto)")
                        .font(.system(.subheadline, design: .monospaced))
                    Spacer()
                    Label("Open", systemImage: "safari")
                        .font(.footnote)
                }
            }
        } else {
            HStack {
                Text(port.publicPort.map { "\($0) → \(port.privatePort)/\(port.proto)" }
                     ?? "\(port.privatePort)/\(port.proto)")
                    .font(.system(.subheadline, design: .monospaced))
                    .foregroundStyle(Theme.text)
                Spacer()
                Text(port.publicPort == nil ? "inside Docker only"
                     : port.ip == "127.0.0.1" ? "this server only" : port.ip)
                    .font(.footnote)
                    .foregroundStyle(Theme.muted)
            }
        }
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
                       height: 220, follow: false)
                .listRowInsets(EdgeInsets())
                .listRowBackground(Theme.termBg)
        }
    }

    // MARK: - Loading and actions

    private func load() async {
        guard let client = app.client else { return }
        detail = try? await client.containerDetail(container.id)
        if app.supports("container_live") {
            events = (try? await client.containerEvents(container.id)) ?? []
        }
        await loadLogs()
    }

    private func loadLogs() async {
        guard let client = app.client else { return }
        loadingLogs = true
        defer { loadingLogs = false }
        do {
            logs = try await client.containerLogs(container.id, tail: 60)
            error = nil
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
    }

    private func run(_ action: ContainerAction) async {
        working = true
        defer { working = false }
        await perform(action)
        await load()
        toast = Toast(text: action.pastTense)
    }

    private func command(_ name: String) async {
        guard let client = app.client else { return }
        working = true
        defer { working = false }
        do {
            try await client.containerCommand(container.id, name)
            try? await Task.sleep(for: .milliseconds(500))
            await load()
            toast = Toast(text: ["pause": "Paused", "unpause": "Resumed", "kill": "Killed"][name] ?? "Done")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
            app.handle(error)
        }
    }

    private func setPolicy(_ policy: String) async {
        guard let client = app.client else { return }
        do {
            try await client.setRestartPolicy(container.id, policy: policy)
            await load()
            toast = Toast(text: "Saved")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func openShell() async {
        guard !openingShell else { return }
        // The public demo has no shell to open; it serves a simulated one on
        // the socket itself (see TerminalHomeView.open).
        if app.me?.demo == true || app.serverInfo?.demo == true {
            let now = Date().timeIntervalSince1970
            shell = TerminalSession(id: "demo-container", title: container.displayName,
                                    context: "container:" + container.id, created: now,
                                    lastActive: now, alive: true, clients: 1)
            return
        }
        guard let client = app.client else { return }
        openingShell = true
        defer { openingShell = false }
        do {
            shell = try await client.createTerminalSession(context: "container:" + container.id,
                                                           title: container.displayName)
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
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

/// A square button with a symbol and a word, four to a row.
struct QuickActionTile: View {
    let symbol: String
    let title: String
    var tint: Color = Theme.accent
    var busy = false
    var disabled = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            QuickActionLabel(symbol: symbol, title: title, tint: tint, busy: busy)
        }
        .buttonStyle(.plain)
        .disabled(disabled || busy)
        .opacity(disabled ? 0.45 : 1)
    }
}

struct QuickActionLabel: View {
    let symbol: String
    let title: String
    var tint: Color = Theme.accent
    var busy = false

    var body: some View {
        VStack(spacing: 6) {
            ZStack {
                if busy {
                    ProgressView()
                } else {
                    Image(systemName: symbol)
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundStyle(tint)
                }
            }
            .frame(height: 22)
            Text(title)
                .font(.caption.weight(.medium))
                .foregroundStyle(Theme.text)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 12)
        .background(Theme.bg2, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

/// What runs inside a container (`docker top`).
struct ProcessesSheet: View {
    let container: Container

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss
    @State private var top: ContainerTop?
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Group {
                if let top {
                    if top.processes.isEmpty {
                        MessageState(symbol: "list.number", title: "No processes",
                                     message: top.note.isEmpty ? nil : top.note)
                    } else {
                        List(Array(top.processes.enumerated()), id: \.offset) { _, row in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(top.value(row, "COMMAND", "CMD", "ARGS"))
                                    .font(.system(.caption, design: .monospaced))
                                    .foregroundStyle(Theme.text)
                                    .lineLimit(3)
                                HStack(spacing: 12) {
                                    Text("PID \(top.value(row, "PID"))")
                                    let cpu = top.value(row, "%CPU", "C")
                                    if !cpu.isEmpty { Text("CPU \(cpu)%") }
                                    let mem = top.value(row, "%MEM")
                                    if !mem.isEmpty { Text("Memory \(mem)%") }
                                    let user = top.value(row, "USER", "UID")
                                    if !user.isEmpty { Text(user) }
                                }
                                .font(.caption2)
                                .foregroundStyle(Theme.muted)
                            }
                        }
                        .listStyle(.insetGrouped)
                    }
                } else if let error {
                    MessageState(symbol: "exclamationmark.triangle", title: "Cannot list processes",
                                 message: error, tint: Theme.danger)
                } else {
                    ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .navigationTitle("Processes")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .task {
                do {
                    top = try await app.client?.containerTop(container.id)
                } catch {
                    self.error = error.localizedDescription
                }
            }
        }
    }
}

// MARK: - Logs

/// A container's logs, full screen: filter, colour by level, follow live.
struct ContainerLogsView: View {
    let container: Container

    @EnvironmentObject private var app: AppState
    @State private var lines: [String] = []
    @State private var loading = true
    @State private var error: String?
    @State private var search = ""
    @State private var follow = false
    @State private var tail = 300
    @State private var since = 0
    @State private var timestamps = false
    @State private var onlyProblems = false
    @State private var followTask: Task<Void, Never>?
    @State private var shareText: ShareText?

    private var shown: [LogLine] {
        lines.enumerated().compactMap { index, line in
            if onlyProblems && LogLevel.of(line) == .plain { return nil }
            guard search.isEmpty || line.localizedCaseInsensitiveContains(search) else { return nil }
            return LogLine(id: index, text: line)
        }
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView([.vertical, .horizontal]) {
                LazyVStack(alignment: .leading, spacing: 1) {
                    if loading && lines.isEmpty {
                        ProgressView().tint(Theme.termFg).padding(30)
                    } else if lines.isEmpty {
                        Text(error ?? "No log output.")
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundStyle(error == nil ? Theme.termFg.opacity(0.6) : .red)
                            .padding(16)
                    }
                    ForEach(shown) { line in
                        Text(line.text)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(LogLevel.of(line.text).color)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: true, vertical: false)
                            .id(line.id)
                    }
                    Color.clear.frame(height: 1).id("end")
                }
                .padding(10)
            }
            .background(Theme.termBg.ignoresSafeArea())
            .onChange(of: lines.count) { _, _ in
                if follow { proxy.scrollTo("end", anchor: .bottom) }
            }
            .onChange(of: loading) { _, isLoading in
                if !isLoading { proxy.scrollTo("end", anchor: .bottom) }
            }
        }
        .searchable(text: $search, prompt: "Filter lines")
        .navigationTitle("Logs · \(container.displayName)")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Theme.termBg, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button {
                    follow.toggle()
                } label: {
                    Image(systemName: follow ? "pause.circle.fill" : "play.circle")
                }
                .accessibilityLabel(follow ? "Stop following" : "Follow live")
                Menu {
                    Toggle(isOn: $onlyProblems) { Label("Only warnings and errors", systemImage: "exclamationmark.triangle") }
                    Toggle(isOn: $timestamps) { Label("Timestamps", systemImage: "clock") }
                    Picker("Since", selection: $since) {
                        Text("All of it").tag(0)
                        Text("Last 15 minutes").tag(900)
                        Text("Last hour").tag(3600)
                        Text("Last 24 hours").tag(86400)
                    }
                    Picker("Lines", selection: $tail) {
                        Text("100 lines").tag(100)
                        Text("300 lines").tag(300)
                        Text("1000 lines").tag(1000)
                        Text("5000 lines").tag(5000)
                    }
                    Divider()
                    Button {
                        UIPasteboard.general.string = lines.joined(separator: "\n")
                    } label: { Label("Copy all", systemImage: "doc.on.doc") }
                    Button {
                        shareText = ShareText(text: lines.joined(separator: "\n"))
                    } label: { Label("Share", systemImage: "square.and.arrow.up") }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
            }
        }
        .sheet(item: $shareText) { item in ShareSheet(items: [item.text]) }
        .task(id: "\(tail)-\(since)-\(timestamps)") { await load() }
        .onChange(of: follow) { _, on in on ? startFollowing() : stopFollowing() }
        .onDisappear { stopFollowing() }
    }

    private func load() async {
        guard let client = app.client else { return }
        loading = true
        defer { loading = false }
        do {
            let text: String
            if app.supports("container_live") {
                text = try await client.containerLogs(container.id, tail: tail, since: since,
                                                      timestamps: timestamps)
            } else {
                text = try await client.containerLogs(container.id, tail: tail)
            }
            lines = text.components(separatedBy: .newlines).filter { !$0.isEmpty }
            error = nil
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
    }

    /// New lines as the container writes them, until the screen closes or the
    /// toggle goes off. The server ends a stream after fifteen minutes; this
    /// reconnects.
    private func startFollowing() {
        stopFollowing()
        guard app.supports("container_live") else {
            follow = false
            return
        }
        followTask = Task {
            while !Task.isCancelled && follow {
                guard let client = app.client,
                      let request = try? await client.containerLogStreamRequest(container.id) else { return }
                do {
                    let (bytes, _) = try await NetworkSession.shared.bytes(for: request)
                    for try await line in bytes.lines {
                        if Task.isCancelled { return }
                        if line.isEmpty { continue }
                        lines.append(line)
                        if lines.count > 5000 { lines.removeFirst(lines.count - 5000) }
                    }
                } catch {
                    if Task.isCancelled { return }
                }
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    private func stopFollowing() {
        followTask?.cancel()
        followTask = nil
    }
}

struct LogLine: Identifiable {
    let id: Int
    let text: String
}

/// How a log line reads at a glance: errors red, warnings amber.
enum LogLevel {
    case error, warning, plain

    static func of(_ line: String) -> LogLevel {
        let lower = line.lowercased()
        if lower.contains("error") || lower.contains("fatal") || lower.contains("panic")
            || lower.contains("exception") || lower.contains(" crit") || lower.contains("[err") {
            return .error
        }
        if lower.contains("warn") { return .warning }
        return .plain
    }

    var color: Color {
        switch self {
        case .error:   return Color(red: 1.0, green: 0.45, blue: 0.42)
        case .warning: return Color(red: 0.98, green: 0.75, blue: 0.35)
        case .plain:   return Theme.termFg
        }
    }
}
