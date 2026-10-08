import SwiftUI

/// More → Server overview: everything on the server at a glance, found on the
/// server itself — the domains and where they lead, the stacks, the systemd
/// services and timers an admin set up, cron jobs, and the drives. Each row
/// leads on: a domain opens, a service shows its journal and can be started,
/// stopped or restarted, a stack opens in Containers.
struct ServerOverviewView: View {
    @EnvironmentObject private var app: AppState
    @State private var inventory: ServerInventory?
    @State private var error: String?
    @State private var loading = false
    @State private var showAllServices = false
    @State private var browser: URL?

    var body: some View {
        ThemedList {
            if let inv = inventory {
                Section { header(inv) }
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 0))

                if !inv.failedUnits.isEmpty {
                    Section {
                        ForEach(inv.failedUnits) { unit in
                            unitLink(unit)
                        }
                    } header: {
                        Label("Needs attention", systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(Theme.danger)
                            .textCase(nil)
                    }
                }

                if !inv.domains.isEmpty {
                    Section {
                        ForEach(inv.domains) { domain in
                            Button {
                                if let url = domain.url { browser = url }
                            } label: {
                                DomainRow(domain: domain)
                            }
                            .disabled(!domain.enabled || domain.url == nil)
                        }
                    } header: {
                        Text("Domains · \(inv.domains.count)")
                    } footer: {
                        Text("Found in \(Set(inv.domains.map(\.source)).sorted().joined(separator: ", ")). Tap one to open it.")
                    }
                }

                let services = inv.services.filter { !$0.isFailed }
                if !services.isEmpty {
                    let custom = services.filter(\.custom)
                    let system = services.filter { !$0.custom }
                    Section {
                        ForEach(custom) { unit in unitLink(unit) }
                        if showAllServices || custom.isEmpty {
                            ForEach(system) { unit in unitLink(unit) }
                        } else if !system.isEmpty {
                            Button {
                                withAnimation(.snappy) { showAllServices = true }
                            } label: {
                                Label("Show \(system.count) system services", systemImage: "chevron.down")
                                    .font(.subheadline)
                            }
                        }
                    } header: {
                        Text("Services · \(services.count)")
                    } footer: {
                        Text(custom.isEmpty ? "Infrastructure running on the host."
                                            : "The ones set up on this server first, then infrastructure such as SSH, Docker and the firewall.")
                    }
                }

                if !inv.timers.isEmpty || !inv.cron.isEmpty {
                    Section {
                        ForEach(inv.timers.filter { !$0.isFailed }) { unit in unitLink(unit) }
                        ForEach(inv.cron) { job in
                            CronRow(job: job)
                        }
                    } header: {
                        Text("Scheduled · \(inv.timers.count + inv.cron.count)")
                    } footer: {
                        Text("Systemd timers and cron jobs.")
                    }
                }

                if !inv.stacks.isEmpty {
                    Section {
                        ForEach(inv.stacks) { stack in
                            Button {
                                app.selectedTab = .containers
                            } label: {
                                HStack(spacing: 12) {
                                    IconTile(symbol: "square.stack.3d.up.fill", color: .blue, size: 30)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(stack.project).foregroundStyle(Theme.text)
                                        Text(stack.dir.isEmpty ? "compose stack" : stack.dir)
                                            .font(.caption)
                                            .foregroundStyle(Theme.muted)
                                            .lineLimit(1)
                                            .truncationMode(.middle)
                                    }
                                    Spacer(minLength: 8)
                                    Text("\(stack.running)/\(stack.services.count)")
                                        .font(.subheadline.monospacedDigit())
                                        .foregroundStyle(stack.running == stack.services.count ? Theme.muted : Theme.warn)
                                }
                            }
                        }
                    } header: {
                        Text("Docker stacks · \(inv.stacks.count)")
                    }
                }

                if !inv.drives.isEmpty {
                    Section {
                        ForEach(inv.drives.filter { $0.kind != "boot" }) { drive in
                            VStack(alignment: .leading, spacing: 6) {
                                HStack {
                                    Label(drive.mount, systemImage: drive.kind == "external" ? "externaldrive.fill"
                                          : drive.kind == "network" ? "network" : "internaldrive.fill")
                                        .foregroundStyle(Theme.text)
                                    Spacer()
                                    Text("\(Fmt.bytes(Int64(drive.free))) free")
                                        .font(.footnote)
                                        .foregroundStyle(Theme.muted)
                                }
                                ProgressView(value: min(1, drive.percent / 100))
                                    .tint(drive.percent > 90 ? Theme.danger : drive.percent > 80 ? Theme.warn : Theme.accent)
                                Text("\(Fmt.bytes(Int64(drive.used))) of \(Fmt.bytes(Int64(drive.total))) · \(drive.fstype)"
                                     + (drive.label.isEmpty ? "" : " · \(drive.label)"))
                                    .font(.caption)
                                    .foregroundStyle(Theme.muted)
                            }
                            .padding(.vertical, 2)
                        }
                    } header: {
                        Text("Drives")
                    }
                }
            } else if let error {
                Section {
                    MessageState(symbol: "server.rack", title: "The overview could not be read",
                                 message: error, retry: { Task { await load(refresh: true) } })
                }
                .listRowBackground(Color.clear)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Server overview")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    app.ask("Look at the whole server (pocketadm overview) and tell me what deserves attention.")
                } label: {
                    Image(systemName: "sparkles")
                }
                .accessibilityLabel("Ask the assistant")
            }
        }
        .overlay { if inventory == nil && error == nil { ProgressView() } }
        .refreshable { await load(refresh: true) }
        .task { if inventory == nil { await load(refresh: false) } }
        .sheet(item: Binding(get: { browser.map { BrowserLink(url: $0) } },
                             set: { browser = $0?.url })) { link in
            SafariView(url: link.url).ignoresSafeArea()
        }
    }

    private func header(_ inv: ServerInventory) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 14) {
                ZStack {
                    RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Theme.gradient)
                        .frame(width: 54, height: 54)
                    Image(systemName: "server.rack")
                        .font(.system(size: 24, weight: .semibold))
                        .foregroundStyle(.white)
                }
                VStack(alignment: .leading, spacing: 3) {
                    Text(inv.host.hostname.isEmpty ? (app.serverName.isEmpty ? "Server" : app.serverName) : inv.host.hostname)
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(Theme.text)
                    Text([inv.host.os, inv.host.arch].filter { !$0.isEmpty }.joined(separator: " · "))
                        .font(.footnote)
                        .foregroundStyle(Theme.muted)
                    if inv.host.uptime > 0 {
                        Text("Up \(Fmt.uptime(inv.host.uptime))"
                             + (inv.host.cores > 0 ? " · \(inv.host.cores) cores" : "")
                             + (inv.host.memory > 0 ? " · \(Fmt.bytes(Int64(inv.host.memory))) RAM" : ""))
                            .font(.footnote)
                            .foregroundStyle(Theme.muted)
                    }
                }
            }
            HStack(spacing: 10) {
                stat(inv.domains.count, "domains", "globe")
                stat(inv.stacks.reduce(0) { $0 + $1.services.count } + inv.containers.count, "containers", "shippingbox")
                stat(inv.services.count, "services", "gearshape.2")
                stat(inv.timers.count + inv.cron.count, "scheduled", "clock")
            }
        }
        .padding(16)
        .background(Theme.bg2, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
    }

    private func stat(_ value: Int, _ label: String, _ symbol: String) -> some View {
        VStack(spacing: 3) {
            Image(systemName: symbol).font(.caption).foregroundStyle(Theme.accent)
            Text("\(value)").font(.headline.monospacedDigit()).foregroundStyle(Theme.text)
            Text(label).font(.caption2).foregroundStyle(Theme.muted)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .background(Theme.bg3.opacity(0.6), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    private func unitLink(_ unit: SystemUnit) -> some View {
        NavigationLink {
            UnitDetailView(unit: unit) { Task { await load(refresh: true) } }
        } label: {
            UnitRow(unit: unit)
        }
    }

    private func load(refresh: Bool) async {
        guard let client = app.client else { return }
        loading = true
        defer { loading = false }
        do {
            inventory = try await client.inventory(refresh: refresh)
            error = nil
        } catch {
            if inventory == nil { self.error = error.localizedDescription }
        }
    }
}

struct DomainRow: View {
    let domain: InventoryDomain

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: domain.tls ? "lock.fill" : "lock.open")
                .font(.footnote)
                .foregroundStyle(domain.tls ? Theme.accent2 : Theme.warn)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 2) {
                Text(domain.domain)
                    .foregroundStyle(domain.enabled ? Theme.text : Theme.muted)
                    .lineLimit(1)
                Text(domain.redirect ? domain.target
                     : (domain.service.isEmpty ? domain.target : "→ \(domain.service)"))
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer(minLength: 8)
            if !domain.enabled {
                StatusPill(text: "off", tint: Theme.muted)
            }
        }
    }
}

struct UnitRow: View {
    let unit: SystemUnit

    var tint: Color {
        if unit.isFailed { return Theme.danger }
        if unit.isRunning || unit.active == "active" { return Theme.accent2 }
        return Theme.muted
    }

    var body: some View {
        HStack(spacing: 12) {
            IconTile(symbol: unit.isTimer ? "clock.fill" : "gearshape.2.fill",
                     color: unit.isFailed ? .red : unit.isTimer ? .orange : .gray, size: 30)
            VStack(alignment: .leading, spacing: 2) {
                Text(unit.name)
                    .foregroundStyle(Theme.text)
                    .lineLimit(1)
                Text(unit.description.isEmpty ? unit.unit : unit.description)
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
                    .lineLimit(1)
                if unit.isTimer && !unit.nextRun.isEmpty {
                    Text("Next: \(unit.nextRun)")
                        .font(.caption2)
                        .foregroundStyle(Theme.muted)
                        .lineLimit(1)
                }
            }
            Spacer(minLength: 6)
            StatusDot(text: unit.stateText, tint: tint)
        }
    }
}

struct CronRow: View {
    let job: CronJob

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            IconTile(symbol: "calendar.badge.clock", color: .purple, size: 30)
            VStack(alignment: .leading, spacing: 2) {
                Text(job.readableSchedule.prefix(1).uppercased() + job.readableSchedule.dropFirst())
                    .foregroundStyle(Theme.text)
                Text(job.command)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(Theme.muted)
                    .lineLimit(2)
                Text("cron · \(job.user.isEmpty ? "" : job.user + " · ")\(job.file)")
                    .font(.caption2)
                    .foregroundStyle(Theme.muted)
                    .lineLimit(1)
            }
        }
        .padding(.vertical, 2)
    }
}

/// One systemd unit: what it is, how it is doing, its journal, and the
/// buttons to start, stop, restart, enable or disable it.
struct UnitDetailView: View {
    let unit: SystemUnit
    var onChange: () -> Void = {}

    @EnvironmentObject private var app: AppState
    @State private var detail: SystemUnit?
    @State private var busy = ""
    @State private var confirm: String?
    @State private var toast: Toast?

    private var current: SystemUnit { detail ?? unit }

    var body: some View {
        ThemedList {
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 12) {
                        IconTile(symbol: current.isTimer ? "clock.fill" : "gearshape.2.fill",
                                 color: current.isFailed ? .red : current.isTimer ? .orange : .gray, size: 40)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(current.unit)
                                .font(.headline)
                                .foregroundStyle(Theme.text)
                            StatusDot(text: current.stateText,
                                      tint: current.isFailed ? Theme.danger
                                        : current.active == "active" ? Theme.accent2 : Theme.muted)
                        }
                    }
                    if !current.description.isEmpty {
                        Text(current.description)
                            .font(.subheadline)
                            .foregroundStyle(Theme.muted)
                    }
                }
                .padding(.vertical, 4)
            }

            Section {
                if !current.since.isEmpty { FactRow(label: "Since", value: current.since) }
                if !current.enabled.isEmpty { FactRow(label: "At boot", value: current.enabled) }
                if !current.nextRun.isEmpty { FactRow(label: "Next run", value: current.nextRun) }
                if !current.lastRun.isEmpty { FactRow(label: "Last run", value: current.lastRun) }
                if !current.triggers.isEmpty { FactRow(label: "Starts", value: current.triggers) }
                if !current.triggeredBy.isEmpty { FactRow(label: "Started by", value: current.triggeredBy) }
                if current.memory > 0 { FactRow(label: "Memory", value: Fmt.bytes(Int64(current.memory))) }
                if !current.result.isEmpty && current.result != "success" {
                    FactRow(label: "Last result", value: current.result, tint: Theme.danger)
                }
                if !current.path.isEmpty { FactRow(label: "File", value: current.path, selectable: true) }
            }

            Section {
                if current.active == "active" || current.active == "activating" {
                    actionButton("Restart", "arrow.clockwise", "restart")
                    actionButton("Stop", "stop.fill", "stop", destructive: true)
                } else {
                    actionButton(current.isTimer ? "Start timer" : "Start", "play.fill", "start")
                }
                if current.enabled == "enabled" {
                    actionButton("Don't start at boot", "power", "disable", destructive: true)
                } else if current.enabled == "disabled" {
                    actionButton("Start at boot", "power", "enable")
                }
            } header: {
                Text("Actions")
            } footer: {
                Text("Runs systemctl on the server. Every action is recorded under Activity.")
            }

            Section {
                if let logs = detail?.logs, !logs.isEmpty {
                    Text(logs)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(Theme.termFg)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(10)
                        .background(Theme.termBg, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                        .listRowInsets(EdgeInsets(top: 6, leading: 8, bottom: 6, trailing: 8))
                } else if detail == nil {
                    ProgressView().frame(maxWidth: .infinity)
                } else {
                    Text("No journal entries.").foregroundStyle(Theme.muted)
                }
                Button {
                    app.ask("Look at the systemd unit \(current.unit) (status and `journalctl -u \(current.unit)`) and tell me whether it is healthy and what to do if not.")
                } label: {
                    Label("Ask the assistant about it", systemImage: "sparkles")
                }
            } header: {
                Text("Journal")
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle(current.name)
        .navigationBarTitleDisplayMode(.inline)
        .overlay {
            if !busy.isEmpty {
                VStack(spacing: 10) {
                    ProgressView()
                    Text(busy).font(.subheadline).foregroundStyle(Theme.muted)
                }
                .padding(22)
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            }
        }
        .confirmationDialog(confirmTitle, isPresented: Binding(get: { confirm != nil }, set: { if !$0 { confirm = nil } }),
                            titleVisibility: .visible) {
            if let action = confirm {
                Button(confirmButton(action), role: action == "stop" || action == "disable" ? .destructive : nil) {
                    Task { await run(action) }
                }
            }
            Button("Cancel", role: .cancel) { confirm = nil }
        }
        .toast($toast)
        .refreshable { await load() }
        .task { await load() }
    }

    private var confirmTitle: String {
        guard let action = confirm else { return "" }
        return "\(confirmButton(action)) \(current.unit)?"
    }

    private func confirmButton(_ action: String) -> String {
        switch action {
        case "restart": return "Restart"
        case "stop":    return "Stop"
        case "start":   return "Start"
        case "enable":  return "Start at boot"
        case "disable": return "Don't start at boot"
        default:        return action.capitalized
        }
    }

    private func actionButton(_ title: String, _ symbol: String, _ action: String,
                              destructive: Bool = false) -> some View {
        Button(role: destructive ? .destructive : nil) {
            confirm = action
        } label: {
            Label(title, systemImage: symbol)
        }
        .disabled(!busy.isEmpty || app.me?.demo == true)
    }

    private func load() async {
        detail = (try? await app.client?.systemUnit(unit.unit)) ?? detail
    }

    private func run(_ action: String) async {
        guard let client = app.client else { return }
        confirm = nil
        busy = "\(confirmButton(action))…"
        defer { busy = "" }
        do {
            detail = try await client.systemUnitAction(unit.unit, action: action)
            toast = Toast(text: "Done")
            onChange()
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }
}
