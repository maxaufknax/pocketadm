import SwiftUI

@MainActor
final class ChecksModel: ObservableObject {
    @Published var report: Report?
    @Published var index: ReportsIndex?
    @Published var loaded = false
    @Published var running = false
    @Published var error: String?
    /// nil = viewing the latest; otherwise the file stem being shown.
    @Published var viewing: String?

    func load(_ app: AppState) async {
        guard let client = app.client else { return }
        defer { loaded = true }
        index = try? await client.reports()
        do {
            report = try await client.latestReport()
            error = nil
        } catch let failure as APIClient.APIError {
            // 404 means "never run", which is an empty state and not a fault.
            if case .http(404, _) = failure {
                report = nil
                error = nil
            } else {
                error = failure.localizedDescription
                app.handle(failure)
            }
        } catch {
            self.error = error.localizedDescription
        }
    }

    func open(_ name: String, app: AppState) async {
        guard let client = app.client else { return }
        viewing = name
        report = try? await client.report(named: name)
    }

    func runNow(_ app: AppState) async {
        guard let client = app.client, !running else { return }
        running = true
        defer { running = false }
        do {
            report = try await client.runReport()
            viewing = nil
            index = try? await client.reports()
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
    }
}

/// The server's own health checks — SSH exposure, fail2ban, disk, backups,
/// pending updates — as one screen you can read in ten seconds.
struct ChecksView: View {
    @EnvironmentObject private var app: AppState
    @StateObject private var model = ChecksModel()

    @State private var analysis: String?
    @State private var analysing = false
    @State private var showConfig = false
    @State private var toast: Toast?

    var body: some View {
        Group {
            if !model.loaded {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let report = model.report {
                content(report)
            } else {
                MessageState(symbol: "checkmark.shield",
                             title: model.error == nil ? "No checks yet" : "Cannot load checks",
                             message: model.error ?? "Run the first health check to see how this server is doing.",
                             tint: model.error == nil ? Theme.muted : Theme.danger,
                             retry: { Task { await model.runNow(app) } },
                             retryTitle: model.error == nil ? "Run checks now" : "Try again")
            }
        }
        .navigationTitle("Health checks")
        .navigationBarTitleDisplayMode(.large)
        .screenBackground()
        .toast($toast)
        .task { if !model.loaded { await model.load(app) } }
        .sheet(isPresented: $showConfig) {
            ReportScheduleSheet(config: model.index?.config ?? ReportConfig()) { interval, auto in
                await save(interval: interval, auto: auto)
            }
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button {
                        Task { await model.runNow(app) }
                    } label: { Label("Run checks now", systemImage: "play.circle") }

                    Button {
                        showConfig = true
                    } label: { Label("Schedule…", systemImage: "clock") }

                    if app.me?.aiConfigured == true {
                        Button {
                            Task { await analyse() }
                        } label: { Label("Explain with AI", systemImage: "sparkles") }
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
                .tint(Theme.accent)
            }
        }
    }

    private func content(_ report: Report) -> some View {
        ScrollView {
            VStack(spacing: 14) {
                scoreCard(report)

                if model.running {
                    HStack(spacing: 10) {
                        ProgressView()
                        Text("Running checks…").font(.caption).foregroundStyle(Theme.muted)
                        Spacer()
                    }
                    .card()
                }

                if analysing || analysis != nil {
                    analysisCard
                }

                ForEach(report.groups) { group in
                    VStack(alignment: .leading, spacing: 10) {
                        SectionCaption(text: group.name)
                        ForEach(group.checks) { check in
                            CheckCard(check: check)
                        }
                    }
                }

                if let reports = model.index?.reports, reports.count > 1 {
                    historyCard(reports)
                }
            }
            .padding(16)
        }
        .refreshable { await model.load(app) }
    }

    private func scoreCard(_ report: Report) -> some View {
        VStack(spacing: 12) {
            HStack(spacing: 12) {
                Image(systemName: report.score.symbol)
                    .font(.system(size: 30))
                    .foregroundStyle(report.score.tint)
                VStack(alignment: .leading, spacing: 3) {
                    Text(headline(report))
                        .font(.headline)
                        .foregroundStyle(Theme.text)
                    Text("\(Fmt.ago(report.date)) · \(report.trigger) · \(String(format: "%.1fs", report.duration))")
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                }
                Spacer()
            }

            HStack(spacing: 8) {
                countPill(report.counts.crit, "critical", Theme.danger)
                countPill(report.counts.warn, "warnings", Theme.warn)
                countPill(report.counts.ok, "passing", Theme.accent2)
            }
        }
        .card()
    }

    private func headline(_ report: Report) -> String {
        switch report.score {
        case .crit: return "Something needs attention"
        case .warn: return "A few things to look at"
        case .info: return "Informational findings"
        case .ok:   return "All clear"
        }
    }

    private func countPill(_ value: Int, _ label: String, _ tint: Color) -> some View {
        VStack(spacing: 2) {
            Text(String(value))
                .font(.system(.title3, design: .rounded).weight(.semibold))
                .foregroundStyle(value > 0 ? tint : Theme.muted)
            Text(label)
                .font(.caption2)
                .foregroundStyle(Theme.muted)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .background(Theme.bg3, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
    }

    private var analysisCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                SectionCaption(text: "AI summary")
                Spacer()
                if analysing { ProgressView().tint(Theme.muted) }
            }
            if let analysis {
                MarkdownText(text: analysis)
            } else {
                Text("Asking the model to read the report…")
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    private func historyCard(_ reports: [ReportSummary]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionCaption(text: "Earlier runs")
            ForEach(reports.prefix(12)) { summary in
                Button {
                    Task { await model.open(summary.file, app: app) }
                } label: {
                    HStack(spacing: 10) {
                        Image(systemName: summary.score.symbol)
                            .foregroundStyle(summary.score.tint)
                            .font(.caption)
                        Text(Fmt.ago(summary.date))
                            .font(.subheadline)
                            .foregroundStyle(Theme.text)
                        Spacer()
                        Text("\(summary.counts.crit + summary.counts.warn) findings")
                            .font(.caption)
                            .foregroundStyle(Theme.muted)
                        if model.viewing == summary.file {
                            Image(systemName: "eye")
                                .font(.caption)
                                .foregroundStyle(Theme.accent)
                        }
                    }
                    .padding(.vertical, 6)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    // MARK: - Actions

    private func analyse() async {
        guard let client = app.client, !analysing else { return }
        analysing = true
        analysis = nil
        defer { analysing = false }
        do {
            analysis = try await client.analyzeReport(named: model.viewing ?? "")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
            app.handle(error)
        }
    }

    private func save(interval: Int, auto: Bool) async {
        guard let client = app.client else { return }
        do {
            try await client.setReportConfig(intervalMin: interval, auto: auto)
            model.index = try? await client.reports()
            showConfig = false
            toast = Toast(text: "Schedule saved")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
            app.handle(error)
        }
    }
}

struct CheckCard: View {
    let check: Report.Check
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: check.status.symbol)
                    .foregroundStyle(check.status.tint)
                    .font(.subheadline)
                    .frame(width: 18)

                VStack(alignment: .leading, spacing: 4) {
                    Text(check.title)
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(Theme.text)
                    Text(check.summary)
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Spacer(minLength: 0)
            }

            if let recommendation = check.recommendation {
                // The fix is the reason the check exists, so it is never hidden
                // behind a tap for anything that is not already green.
                if check.status == .ok && !expanded {
                    Button("Show suggestion") { expanded = true }
                        .font(.caption)
                        .tint(Theme.accent)
                } else {
                    MarkdownText(text: recommendation, font: .caption)
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(check.status.tint.opacity(0.10),
                                    in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }
}

struct ReportScheduleSheet: View {
    let config: ReportConfig
    let onSave: (Int, Bool) async -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var interval: Int = 360
    @State private var auto = true
    @State private var saving = false

    private let options: [(String, Int)] = [
        ("Every hour", 60),
        ("Every 3 hours", 180),
        ("Every 6 hours", 360),
        ("Every 12 hours", 720),
        ("Once a day", 1440),
    ]

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Toggle("Run automatically", isOn: $auto)
                }

                Section {
                    Picker("Interval", selection: $interval) {
                        ForEach(options, id: \.1) { option in
                            Text(option.0).tag(option.1)
                        }
                    }
                    .pickerStyle(.inline)
                    .disabled(!auto)
                } header: {
                    SectionCaption(text: "How often")
                } footer: {
                    Text("Findings also arrive as alerts, so a shorter interval means more notifications rather than more information.")
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Schedule")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { dismiss() }.tint(Theme.muted)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Save") {
                        saving = true
                        Task { await onSave(interval, auto); saving = false }
                    }
                    .tint(Theme.accent)
                    .disabled(saving)
                }
            }
            .onAppear {
                interval = config.intervalMin
                auto = config.auto
            }
        }
    }
}
