import Charts
import SwiftUI

@MainActor
final class DashboardModel: ObservableObject {
    @Published var system: SystemSnapshot?
    @Published var history: [MetricsHistory.Point] = []
    @Published var error: String?
    @Published var loaded = false

    /// Glance data. Fetched once per appearance rather than on the 5s tick: an
    /// update check can hit a registry and the report is a file read, and
    /// neither changes between two heartbeats.
    @Published var pendingUpdates = 0
    @Published var health: Severity?
    @Published var healthPoints: Int?
    @Published var drives: [Filesystem] = []
    /// Minutes of history the chart shows (60 = the last hour).
    @Published var range = 60

    private var ticker: Task<Void, Never>?

    /// The server samples every ~10s, so polling faster only burns battery.
    private let pollInterval: Duration = .seconds(5)

    func start(_ app: AppState) {
        guard ticker == nil else { return }
        ticker = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh(app)
                try? await Task.sleep(for: self?.pollInterval ?? .seconds(5))
            }
        }
    }

    func stop() {
        ticker?.cancel()
        ticker = nil
    }

    /// Long ranges change slowly and are large; they are fetched once a minute.
    private var lastHistory = Date.distantPast
    private var lastRange = 60

    func refresh(_ app: AppState) async {
        guard let client = app.client else { return }
        do {
            self.system = try await client.system()
            let stale = Date().timeIntervalSince(lastHistory) > (range <= 60 ? 0 : 60)
            if stale || range != lastRange {
                let historyResult = try await client.metricsHistory(minutes: range)
                self.history = historyResult.points
                lastHistory = Date()
                lastRange = range
            }
            self.error = nil
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
        loaded = true
    }

    func refreshGlance(_ app: AppState) async {
        guard let client = app.client else { return }
        await app.refreshAlerts()
        if let updates = try? await client.updates() { pendingUpdates = updates.pending.count }
        // A 404 here simply means no check has ever run.
        let report = try? await client.latestReport()
        health = report?.score
        healthPoints = report?.score100
        if app.supports("storage") {
            drives = ((try? await client.storage()) ?? []).filter { $0.kind != "boot" }
        }
    }
}

struct DashboardView: View {
    @EnvironmentObject private var app: AppState
    @StateObject private var model = DashboardModel()

    var body: some View {
        NavigationStack {
            Group {
                if !model.loaded {
                    ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let system = model.system {
                    content(system)
                } else {
                    MessageState(
                        symbol: "exclamationmark.triangle",
                        title: "Cannot read the server",
                        message: model.error,
                        tint: Theme.danger,
                        retry: { Task { await model.refresh(app) } }
                    )
                }
            }
            .navigationTitle(app.serverName.isEmpty ? "Dashboard" : app.serverName)
            .navigationBarTitleDisplayMode(.large)
            .screenBackground()
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    NavigationLink {
                        AlertsView()
                    } label: {
                        Image(systemName: app.unseenAlerts > 0 ? "bell.badge" : "bell")
                            .symbolRenderingMode(.hierarchical)
                            .accessibilityLabel(alertsLabel)
                    }
                }
            }
        }
        .task {
            model.start(app)
            await model.refreshGlance(app)
        }
        .onDisappear { model.stop() }
    }

    private var alertsLabel: String {
        app.unseenAlerts > 0 ? "Alerts, \(app.unseenAlerts) new" : "Alerts"
    }

    private func content(_ system: SystemSnapshot) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                if app.me?.shouldWarnAboutExposure == true { exposureWarning }

                gauges(system)

                VStack(alignment: .leading, spacing: 10) {
                    sectionTitle("Status")
                    statusCard(system)
                }

                if !model.history.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        sectionTitle(model.range == 60 ? "Last hour"
                                     : model.range == 360 ? "Last 6 hours"
                                     : model.range == 1440 ? "Last 24 hours" : "Last 7 days")
                        historyChart
                    }
                }

                VStack(alignment: .leading, spacing: 10) {
                    sectionTitle("System")
                    hostCard(system)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 4)
            .padding(.bottom, 24)
        }
        .refreshable {
            await model.refresh(app)
            await model.refreshGlance(app)
        }
    }

    private func sectionTitle(_ text: String) -> some View {
        Text(text)
            .font(.title3.weight(.semibold))
            .foregroundStyle(Theme.text)
            .padding(.leading, 4)
    }

    private var exposureWarning: some View {
        NavigationLink {
            SecurityView()
        } label: {
            // No action button here: the whole banner is already the link, and
            // a Button nested inside a NavigationLink label swallows the tap.
            WarningBanner(
                title: "Public and unprotected",
                message: "Reachable from the internet with 2FA off. PocketADM can open a root shell — tap to fix.",
                tint: Theme.danger
            )
        }
        .buttonStyle(.plain)
    }

    // MARK: - Gauges

    private func gauges(_ system: SystemSnapshot) -> some View {
        VStack(spacing: 16) {
            HStack(alignment: .top, spacing: 8) {
                RingGauge(title: "CPU", value: Fmt.percent(system.cpuPercent),
                          detail: "\(system.cpuCount) cores",
                          fraction: system.cpuPercent / 100, tint: tint(for: system.cpuPercent))
                RingGauge(title: "Memory", value: Fmt.percent(system.memory.percent),
                          detail: "\(Fmt.bytes(system.memory.used)) of \(Fmt.bytes(system.memory.total))",
                          fraction: system.memory.percent / 100, tint: tint(for: system.memory.percent))
                RingGauge(title: "Disk", value: Fmt.percent(system.disk.percent),
                          detail: "\(Fmt.bytes(system.disk.spare)) free",
                          fraction: system.disk.percent / 100, tint: tint(for: system.disk.percent))
            }

            Divider()

            HStack(spacing: 0) {
                networkStat(symbol: "arrow.down", label: "Download", value: system.net.map { Fmt.rate($0.rx) } ?? "—")
                networkStat(symbol: "arrow.up", label: "Upload", value: system.net.map { Fmt.rate($0.tx) } ?? "—")
                networkStat(symbol: "dot.radiowaves.left.and.right", label: "Latency",
                            value: system.net?.ping.map { String(format: "%.0f ms", $0) } ?? "—")
            }
        }
        .card()
    }

    private func networkStat(symbol: String, label: String, value: String) -> some View {
        VStack(spacing: 3) {
            Label(label, systemImage: symbol)
                .font(.caption)
                .foregroundStyle(Theme.muted)
            Text(value)
                .font(.subheadline.weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(Theme.text)
                .contentTransition(.numericText())
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Status

    /// What needs you, one row each: alerts, updates, health, containers.
    private func statusCard(_ system: SystemSnapshot) -> some View {
        VStack(spacing: 0) {
            NavigationLink {
                AlertsView()
            } label: {
                statusRow(symbol: "bell.fill", color: .red, title: "Alerts",
                          value: app.unseenAlerts == 0 ? (app.me?.watchEnabled == true ? "Watching" : "None new")
                                                       : "\(app.unseenAlerts) new",
                          valueTint: app.unseenAlerts == 0 ? Theme.muted : Theme.danger)
            }
            Divider().padding(.leading, 60)
            NavigationLink {
                UpdatesView()
            } label: {
                statusRow(symbol: "arrow.triangle.2.circlepath", color: .orange, title: "Updates",
                          value: model.pendingUpdates == 0 ? "Up to date" : "\(model.pendingUpdates) available",
                          valueTint: model.pendingUpdates == 0 ? Theme.muted : Theme.warn)
            }
            Divider().padding(.leading, 60)
            NavigationLink {
                ChecksView()
            } label: {
                statusRow(symbol: "checkmark.shield.fill", color: .green, title: "Health",
                          value: healthText,
                          valueTint: model.health.map { $0 == .ok ? Theme.muted : $0.tint } ?? Theme.muted)
            }
            if let docker = system.docker {
                Divider().padding(.leading, 60)
                Button {
                    app.selectedTab = .containers
                } label: {
                    statusRow(symbol: "shippingbox.fill", color: .brown, title: "Containers",
                              value: "\(docker.running) of \(docker.containers) running",
                              valueTint: docker.running < docker.containers ? Theme.warn : Theme.muted)
                }
            }
            if !model.drives.isEmpty {
                Divider().padding(.leading, 60)
                NavigationLink {
                    FilesHomeView()
                } label: {
                    statusRow(symbol: "internaldrive.fill", color: .gray, title: "Storage",
                              value: storageText, valueTint: storageTint)
                }
            }
        }
        .buttonStyle(PressableRowStyle())
        .background(Theme.bg2)
        .clipShape(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
    }

    private var healthText: String {
        guard let health = model.health else { return "Not checked yet" }
        if let points = model.healthPoints, app.supports("health_v2") {
            return "\(points)/100 · \(health == .ok ? "All clear" : health.label)"
        }
        return health.label
    }

    /// The fullest drive, the way you would say it: "SSD 53 % · T5 41 %".
    private var storageText: String {
        model.drives.prefix(2).map { "\($0.external ? "USB" : "Disk") \(Int($0.percent)) %" }
            .joined(separator: " · ")
    }

    private var storageTint: Color {
        let worst = model.drives.map(\.percent).max() ?? 0
        return worst >= 90 ? Theme.danger : worst >= 80 ? Theme.warn : Theme.muted
    }

    private func statusRow(symbol: String, color: Color, title: String, value: String,
                           valueTint: Color, chevron: Bool = true) -> some View {
        HStack(spacing: 14) {
            IconTile(symbol: symbol, color: color)
            Text(title)
                .foregroundStyle(Theme.text)
            Spacer(minLength: 8)
            Text(value)
                .foregroundStyle(valueTint)
                .lineLimit(1)
            if chevron {
                Image(systemName: "chevron.right")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Color(uiColor: .tertiaryLabel))
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 11)
        .contentShape(Rectangle())
    }

    // MARK: - History

    private var historyChart: some View {
        HistoryCard(points: model.history, range: $model.range) {
            Task { await model.refresh(app) }
        }
    }

    // MARK: - System

    private func hostCard(_ system: SystemSnapshot) -> some View {
        FactsCard {
            FactRow(label: "Host", value: system.hostname)
            HairlineDivider()
            FactRow(label: "Uptime", value: Fmt.uptime(system.uptime))
            HairlineDivider()
            FactRow(label: "Load",
                    value: system.load.prefix(3)
                        .map { String(format: "%.2f", $0) }
                        .joined(separator: "  "))
            if let docker = system.docker {
                HairlineDivider()
                FactRow(label: "Docker", value: docker.version)
                HairlineDivider()
                FactRow(label: "Images", value: String(docker.images))
            }
        }
    }

    // MARK: - Helpers

    /// Green below 70, amber to 90, red above — matching how the web UI reads.
    private func tint(for percent: Double) -> Color {
        switch percent {
        case ..<70:  return Theme.accent2
        case ..<90:  return Theme.warn
        default:     return Theme.danger
        }
    }
}

/// A ring that fills to `fraction`, the value in its middle — the Activity
/// app's shape, for the three numbers that have a natural ceiling.
struct RingGauge: View {
    let title: String
    let value: String
    var detail: String = ""
    let fraction: Double
    let tint: Color

    var body: some View {
        VStack(spacing: 8) {
            ZStack {
                Circle()
                    .stroke(tint.opacity(0.18), lineWidth: 9)
                Circle()
                    .trim(from: 0, to: max(0.002, min(1, fraction)))
                    .stroke(tint.gradient, style: StrokeStyle(lineWidth: 9, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                Text(value)
                    .font(.system(.headline, design: .rounded).weight(.semibold))
                    .monospacedDigit()
                    .foregroundStyle(Theme.text)
                    .contentTransition(.numericText())
                    .minimumScaleFactor(0.7)
                    .lineLimit(1)
                    .padding(.horizontal, 10)
            }
            .frame(width: 78, height: 78)
            .animation(.smooth, value: fraction)

            VStack(spacing: 1) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.text)
                if !detail.isEmpty {
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                }
            }
        }
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .combine)
    }
}
