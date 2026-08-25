import Charts
import SwiftUI

@MainActor
final class DashboardModel: ObservableObject {
    @Published var system: SystemSnapshot?
    @Published var history: [MetricsHistory.Point] = []
    @Published var error: String?
    @Published var loaded = false

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

    func refresh(_ app: AppState) async {
        guard let client = app.client else { return }
        do {
            async let system = client.system()
            async let history = client.metricsHistory(minutes: 60)
            self.system = try await system
            // Bind first, then read `.points` — reaching through the async let
            // binding in one expression is needlessly subtle.
            let historyResult = try await history
            self.history = historyResult.points
            self.error = nil
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
        loaded = true
    }
}

struct DashboardView: View {
    @EnvironmentObject private var app: AppState
    @StateObject private var model = DashboardModel()

    private let columns = [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)]

    var body: some View {
        NavigationStack {
            Group {
                if !model.loaded {
                    ProgressView().tint(Theme.accent).frame(maxWidth: .infinity, maxHeight: .infinity)
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
                    .frame(maxHeight: .infinity)
                }
            }
            .navigationTitle(app.serverName.isEmpty ? "Dashboard" : app.serverName)
            .navigationBarTitleDisplayMode(.large)
            .screenBackground()
        }
        .task { model.start(app) }
        .onDisappear { model.stop() }
    }

    private func content(_ system: SystemSnapshot) -> some View {
        ScrollView {
            VStack(spacing: 14) {
                LazyVGrid(columns: columns, spacing: 12) {
                    MetricTile(
                        title: "CPU",
                        value: Fmt.percent(system.cpuPercent),
                        detail: "\(system.cpuCount) cores · load \(String(format: "%.2f", system.load.first ?? 0))",
                        fraction: system.cpuPercent / 100,
                        tint: tint(for: system.cpuPercent)
                    )
                    MetricTile(
                        title: "Memory",
                        value: Fmt.percent(system.memory.percent),
                        detail: "\(Fmt.bytes(system.memory.used)) of \(Fmt.bytes(system.memory.total))",
                        fraction: system.memory.percent / 100,
                        tint: tint(for: system.memory.percent)
                    )
                    MetricTile(
                        title: "Disk",
                        value: Fmt.percent(system.disk.percent),
                        detail: "\(Fmt.bytes(system.disk.spare)) free",
                        fraction: system.disk.percent / 100,
                        tint: tint(for: system.disk.percent)
                    )
                    MetricTile(
                        title: "Network",
                        value: networkValue(system.net),
                        detail: networkDetail(system.net),
                        // Network has no natural ceiling, so the bar would be
                        // meaningless — it stays empty on purpose.
                        fraction: 0,
                        tint: Theme.accent2
                    )
                }

                if !model.history.isEmpty { historyChart }

                hostCard(system)
            }
            .padding(16)
        }
        .refreshable { await model.refresh(app) }
    }

    private var historyChart: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("LAST HOUR")
                .font(.caption2.weight(.semibold))
                .tracking(0.6)
                .foregroundStyle(Theme.muted)

            Chart {
                ForEach(model.history) { point in
                    AreaMark(x: .value("Time", point.date), y: .value("CPU", point.cpu))
                        .foregroundStyle(
                            .linearGradient(
                                colors: [Theme.accent.opacity(0.35), Theme.accent.opacity(0.02)],
                                startPoint: .top, endPoint: .bottom
                            )
                        )
                    LineMark(x: .value("Time", point.date), y: .value("CPU", point.cpu))
                        .foregroundStyle(Theme.accent)
                        .interpolationMethod(.monotone)
                }
                ForEach(model.history) { point in
                    LineMark(x: .value("Time", point.date), y: .value("Memory", point.mem))
                        .foregroundStyle(Theme.accent2)
                        .interpolationMethod(.monotone)
                }
            }
            .chartYScale(domain: 0...100)
            .chartYAxis {
                AxisMarks(values: [0.0, 50.0, 100.0]) {
                    AxisGridLine().foregroundStyle(Theme.border)
                    AxisValueLabel().foregroundStyle(Theme.muted)
                }
            }
            .chartXAxis {
                AxisMarks(values: .automatic(desiredCount: 4)) {
                    AxisGridLine().foregroundStyle(Theme.border.opacity(0.5))
                    AxisValueLabel(format: .dateTime.hour().minute())
                        .foregroundStyle(Theme.muted)
                }
            }
            .frame(height: 160)

            HStack(spacing: 16) {
                legend(color: Theme.accent, label: "CPU")
                legend(color: Theme.accent2, label: "Memory")
            }
        }
        .card()
    }

    private func legend(color: Color, label: String) -> some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(label).font(.caption).foregroundStyle(Theme.muted)
        }
    }

    private func hostCard(_ system: SystemSnapshot) -> some View {
        VStack(spacing: 0) {
            row("Host", system.hostname)
            Divider().overlay(Theme.border)
            row("Uptime", Fmt.uptime(system.uptime))
            if let docker = system.docker {
                Divider().overlay(Theme.border)
                row("Docker", "\(docker.running) of \(docker.containers) running")
                Divider().overlay(Theme.border)
                row("Engine", docker.version)
            }
        }
        .card(padding: 0)
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).foregroundStyle(Theme.muted)
            Spacer()
            Text(value).foregroundStyle(Theme.text).lineLimit(1).truncationMode(.middle)
        }
        .font(.subheadline)
        .padding(.horizontal, 14)
        .padding(.vertical, 11)
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

    private func networkValue(_ net: SystemSnapshot.NetRates?) -> String {
        guard let net else { return "—" }
        return Fmt.rate(net.rx)
    }

    private func networkDetail(_ net: SystemSnapshot.NetRates?) -> String {
        guard let net else { return "no samples yet" }
        // ping is null whenever the latency probe failed; that must not blank
        // the whole tile, so it degrades to just the transfer rates.
        let up = "↑ \(Fmt.rate(net.tx))"
        guard let ping = net.ping else { return up }
        return "\(up) · \(String(format: "%.0f ms", ping))"
    }
}
