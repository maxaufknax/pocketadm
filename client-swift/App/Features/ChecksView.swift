import Charts
import SwiftUI
import UIKit

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
            report = viewing == nil ? try await client.latestReport()
                                    : try await client.report(named: viewing ?? "")
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

    func open(_ name: String?, app: AppState) async {
        guard let client = app.client else { return }
        viewing = name
        if let name {
            report = try? await client.report(named: name)
        } else {
            report = try? await client.latestReport()
        }
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

/// The server's own health checks as one screen you can read in ten seconds:
/// a score, what needs you, and what to do about each finding.
struct ChecksView: View {
    @EnvironmentObject private var app: AppState
    @StateObject private var model = ChecksModel()

    @State private var analysis: String?
    @State private var analysing = false
    @State private var showConfig = false
    @State private var category: String?
    @State private var selected: Report.Check?
    @State private var route: MoreRoute?
    @State private var job: PendingJob?
    @State private var toast: Toast?
    @State private var historyOpen = false

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
        .navigationTitle("Health")
        .navigationBarTitleDisplayMode(.large)
        .screenBackground()
        .toast($toast)
        .task { if !model.loaded { await model.load(app) } }
        .sheet(isPresented: $showConfig) {
            ReportScheduleSheet(config: model.index?.config ?? ReportConfig()) { interval, auto in
                await save(interval: interval, auto: auto)
            }
        }
        .sheet(item: $selected) { check in
            CheckDetailSheet(check: check) { action in
                Task { await perform(action, on: check) }
            }
            .presentationDetents([.medium, .large])
        }
        .sheet(item: $job) { pending in
            JobConsoleView(jobID: pending.id, title: pending.title) { _ in
                Task { await model.runNow(app) }
            }
        }
        .navigationDestination(item: $route) { route in
            MoreDestination(route: route)
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button {
                        Task { await model.runNow(app) }
                    } label: { Label("Check again now", systemImage: "play.circle") }

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
            VStack(alignment: .leading, spacing: 18) {
                scoreCard(report)

                if model.viewing != nil {
                    Button {
                        Task { await model.open(nil, app: app) }
                    } label: {
                        Label("You are looking at an earlier check — back to the latest", systemImage: "clock.arrow.circlepath")
                            .font(.footnote.weight(.semibold))
                    }
                }

                areaGrid(report)

                if analysing || analysis != nil {
                    analysisCard
                }

                let attention = filtered(report.needsAttention)
                if !attention.isEmpty {
                    section(title: category.map { "\($0) · needs you" } ?? "Needs you", count: attention.count) {
                        ForEach(Array(attention.enumerated()), id: \.element.id) { index, check in
                            if index > 0 { Divider().padding(.leading, 48) }
                            CheckRow(check: check, quick: quickAction(for: check).map { action in
                                { Task { await perform(action, on: check) } }
                            }) { selected = check }
                        }
                    }
                } else if category == nil {
                    allClear
                }

                // everything that does not need you, folded: open it when you want to read it
                let info = filtered(report.informational)
                let accepted = filtered(report.accepted)
                let passing = filtered(report.passing)
                if !(info.isEmpty && accepted.isEmpty && passing.isEmpty) {
                    collapsible(title: attention.isEmpty ? "Details" : "Everything else",
                                count: info.count + accepted.count + passing.count,
                                startOpen: category != nil && attention.isEmpty) {
                        ForEach(Array((info + accepted + passing).enumerated()), id: \.element.id) { index, check in
                            if index > 0 { Divider().padding(.leading, 48) }
                            CheckRow(check: check) { selected = check }
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

    private func filtered(_ checks: [Report.Check]) -> [Report.Check] {
        guard let category else { return checks }
        return checks.filter { $0.category == category }
    }

    // MARK: - Score

    private func scoreCard(_ report: Report) -> some View {
        let score = report.score100
        let tint: Color = score >= 85 ? .green : score >= 60 ? .orange : Theme.danger
        return HStack(spacing: 18) {
            ZStack {
                Circle().stroke(tint.opacity(0.18), lineWidth: 8)
                Circle()
                    .trim(from: 0, to: max(0.02, Double(score) / 100))
                    .stroke(tint.gradient, style: StrokeStyle(lineWidth: 8, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                VStack(spacing: 0) {
                    Text("\(score)")
                        .font(.system(size: 25, weight: .bold, design: .rounded))
                        .foregroundStyle(Theme.text)
                        .contentTransition(.numericText())
                    Text("of 100")
                        .font(.caption2)
                        .foregroundStyle(Theme.muted)
                }
            }
            .frame(width: 78, height: 78)
            .animation(.smooth, value: score)

            VStack(alignment: .leading, spacing: 6) {
                Text(verdict(report))
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
                Text("Checked \(Fmt.ago(report.date))")
                    .font(.footnote)
                    .foregroundStyle(Theme.muted)
                Button {
                    Task { await model.runNow(app) }
                } label: {
                    HStack(spacing: 6) {
                        if model.running { ProgressView().controlSize(.mini) }
                        Text(model.running ? "Checking…" : "Check again")
                    }
                    .font(.footnote.weight(.semibold))
                }
                .buttonStyle(.bordered)
                .buttonBorderShape(.capsule)
                .disabled(model.running)
            }
            Spacer(minLength: 0)
        }
        .card()
    }

    private func verdict(_ report: Report) -> String {
        let crit = report.needsAttention.filter { $0.status == .crit }.count
        let warn = report.needsAttention.count - crit
        if crit > 0 {
            return crit == 1 ? "One thing needs you now" : "\(crit) things need you now"
        }
        if warn > 0 {
            return warn == 1 ? "Healthy — one thing to look at" : "Healthy — \(warn) things to look at"
        }
        return "All clear"
    }

    private var allClear: some View {
        HStack(spacing: 12) {
            Image(systemName: "checkmark.seal.fill")
                .font(.title2)
                .foregroundStyle(.green)
            Text("Nothing needs you right now.")
                .font(.subheadline)
                .foregroundStyle(Theme.text)
            Spacer()
        }
        .card()
    }

    // MARK: - Areas

    private static let areas: [(String, String, Color)] = [
        ("Security", "lock.shield.fill", .blue),
        ("Stability", "waveform.path.ecg", .green),
        ("Storage", "internaldrive.fill", .gray),
        ("Updates", "arrow.triangle.2.circlepath", .orange),
        ("Backups", "externaldrive.fill.badge.timemachine", .teal),
        ("Assistant", "sparkles", .purple),
    ]

    /// The areas at a glance — each tile says whether anything there needs
    /// you, and filters the page to it on a tap.
    private func areaGrid(_ report: Report) -> some View {
        let shown = Self.areas.filter { area in report.checks.contains { $0.category == area.0 } }
        return LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 3), spacing: 8) {
            ForEach(shown, id: \.0) { area in
                let checks = report.checks.filter { $0.category == area.0 }
                let open = checks.filter { !$0.muted && ($0.status == .warn || $0.status == .crit) }
                let worst = open.map(\.status).min { $0.weight < $1.weight }
                Button {
                    withAnimation(.snappy) { category = category == area.0 ? nil : area.0 }
                } label: {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Image(systemName: area.1)
                                .font(.footnote.weight(.semibold))
                                .foregroundStyle(category == area.0 ? Theme.onAccent : area.2)
                            Spacer(minLength: 0)
                            Image(systemName: worst == nil ? "checkmark.circle.fill" : worst!.symbol)
                                .font(.caption)
                                .foregroundStyle(category == area.0 ? Theme.onAccent
                                                 : (worst?.tint ?? Color.green))
                        }
                        Text(area.0)
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(category == area.0 ? Theme.onAccent : Theme.text)
                            .lineLimit(1)
                            .minimumScaleFactor(0.8)
                        Text(open.isEmpty ? "OK" : open.count == 1 ? "1 to look at" : "\(open.count) to look at")
                            .font(.caption2)
                            .foregroundStyle(category == area.0 ? Theme.onAccent.opacity(0.85) : Theme.muted)
                            .lineLimit(1)
                    }
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(category == area.0 ? area.2 : Theme.bg2,
                                in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                }
                .buttonStyle(.plain)
            }
        }
    }

    /// The one thing to do about a finding, right on its row: the first action
    /// that is not "accept" or "dismiss".
    private func quickAction(for check: Report.Check) -> AlertAction? {
        check.actions.first { !["mute", "unmute", "dismiss"].contains($0.kind) }
    }

    // MARK: - Sections

    private func section<Content: View>(title: String, count: Int,
                                        @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                SectionCaption(text: title)
                Spacer()
                Text("\(count)").font(.footnote).foregroundStyle(Theme.muted)
            }
            .padding(.horizontal, 4)
            VStack(spacing: 0) { content() }
                .background(Theme.bg2, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        }
    }

    private func collapsible<Content: View>(title: String, count: Int, startOpen: Bool,
                                            @ViewBuilder content: @escaping () -> Content) -> some View {
        CollapsibleCard(title: title, count: count, startOpen: startOpen, content: content)
    }

    private var analysisCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("AI summary", systemImage: "sparkles")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Theme.muted)
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
        let recent = Array(reports.prefix(20))
        return VStack(alignment: .leading, spacing: 10) {
            Button {
                withAnimation(.snappy) { historyOpen.toggle() }
            } label: {
                HStack {
                    SectionCaption(text: "Over time")
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Theme.muted)
                        .rotationEffect(.degrees(historyOpen ? 90 : 0))
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if recent.contains(where: { $0.points != nil }) {
                Chart(recent) { summary in
                    LineMark(x: .value("When", summary.date),
                             y: .value("Score", summary.points ?? 0))
                        .interpolationMethod(.monotone)
                        .foregroundStyle(Theme.accent)
                    PointMark(x: .value("When", summary.date),
                              y: .value("Score", summary.points ?? 0))
                        .foregroundStyle(summary.score.tint)
                        .symbolSize(30)
                }
                .chartYScale(domain: 0...100)
                .chartYAxis {
                    AxisMarks(position: .leading, values: [0, 50, 100])
                }
                .frame(height: historyOpen ? 110 : 56)
            }
            if historyOpen {
            ForEach(reports.prefix(6)) { summary in
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
                        Text(summary.points.map { "\($0)/100" }
                             ?? "\(summary.counts.crit + summary.counts.warn) findings")
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
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    // MARK: - Actions

    private func perform(_ action: AlertAction, on check: Report.Check) async {
        guard let client = app.client else { return }
        switch action.kind {
        case "open":
            selected = nil
            if action.target == "containers" {
                app.selectedTab = .containers
            } else {
                route = MoreRoute.from(target: action.target)
            }
        case "assistant":
            selected = nil
            app.ask(action.prompt)
        case "copy":
            UIPasteboard.general.string = action.command
            toast = Toast(text: "Command copied — run it in the terminal")
        case "job":
            selected = nil
            if action.job == "prune_images", let id = try? await client.pruneImages() {
                job = PendingJob(id: id, title: "Remove unused images")
            }
        case "mute", "unmute":
            selected = nil
            do {
                try await client.muteCheck(check.id, muted: action.kind == "mute")
                await model.load(app)
                toast = Toast(text: action.kind == "mute" ? "Accepted — it no longer counts" : "Watching it again")
            } catch {
                toast = Toast(text: error.localizedDescription, isError: true)
            }
        case "dismiss":
            selected = nil
            try? await client.dismissPermissions(action.ids)
            await model.runNow(app)
        default:
            break
        }
    }

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

/// A titled card that opens and closes, with a count.
struct CollapsibleCard<Content: View>: View {
    let title: String
    let count: Int
    let content: () -> Content
    @State private var open: Bool

    init(title: String, count: Int, startOpen: Bool, @ViewBuilder content: @escaping () -> Content) {
        self.title = title
        self.count = count
        self.content = content
        _open = State(initialValue: startOpen)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation(.snappy) { open.toggle() }
            } label: {
                HStack {
                    SectionCaption(text: title)
                    Text("\(count)").font(.footnote).foregroundStyle(Theme.muted)
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Theme.muted)
                        .rotationEffect(.degrees(open ? 90 : 0))
                }
                .padding(.horizontal, 4)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open {
                VStack(spacing: 0) { content() }
                    .background(Theme.bg2, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
                    .transition(.opacity)
            }
        }
    }
}

/// One finding, compact: its state, its title, the one line that matters —
/// and, where there is one, the thing to do about it right on the row.
struct CheckRow: View {
    let check: Report.Check
    var quick: (() -> Void)? = nil
    let open: () -> Void

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            Button(action: open) {
                HStack(alignment: .center, spacing: 12) {
                    Image(systemName: check.muted ? "checkmark.circle" : check.status.symbol)
                        .foregroundStyle(check.muted ? Theme.muted : check.status.tint)
                        .font(.body)
                        .frame(width: 22)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(check.title)
                            .font(.subheadline.weight(.medium))
                            .foregroundStyle(Theme.text)
                            .lineLimit(1)
                        Text(check.muted && !check.mutedNote.isEmpty ? check.mutedNote : check.summary)
                            .font(.caption)
                            .foregroundStyle(Theme.muted)
                            .lineLimit(1)
                    }
                    Spacer(minLength: 6)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if let quick, let action = check.actions.first(where: { !["mute", "unmute", "dismiss"].contains($0.kind) }) {
                Button(action: quick) {
                    Text(Self.shortLabel(action))
                        .font(.caption.weight(.semibold))
                        .lineLimit(1)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(check.status.tint.opacity(0.14), in: Capsule())
                        .foregroundStyle(check.status.tint)
                }
                .buttonStyle(.borderless)
            } else {
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color(uiColor: .tertiaryLabel))
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 11)
    }

    /// "Ask the assistant to fix it" fits a row as "Fix it".
    static func shortLabel(_ action: AlertAction) -> String {
        switch action.kind {
        case "assistant": return "Fix it"
        case "copy":      return "Copy fix"
        case "job":       return action.label.count <= 14 ? action.label : "Run"
        default:          return action.label.count <= 14 ? action.label : "Open"
        }
    }
}

/// Everything about one finding: what it means, why it matters, what to do —
/// with the doing one tap away.
struct CheckDetailSheet: View {
    let check: Report.Check
    let perform: (AlertAction) -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(spacing: 10) {
                        Image(systemName: check.muted ? "checkmark.circle" : check.status.symbol)
                            .font(.title2)
                            .foregroundStyle(check.muted ? Theme.muted : check.status.tint)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(check.title)
                                .font(.title3.weight(.semibold))
                                .foregroundStyle(Theme.text)
                            Text(check.muted ? "Accepted by you · \(check.category)"
                                 : "\((check.originalStatus ?? check.status).label) · \(check.category)")
                                .font(.footnote)
                                .foregroundStyle(Theme.muted)
                        }
                    }

                    Text(check.summary)
                        .font(.body)
                        .foregroundStyle(Theme.text)
                        .textSelection(.enabled)

                    if !check.explain.isEmpty {
                        VStack(alignment: .leading, spacing: 6) {
                            SectionCaption(text: "What this means")
                            Text(check.explain)
                                .font(.subheadline)
                                .foregroundStyle(Theme.text)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }

                    if let recommendation = check.recommendation {
                        VStack(alignment: .leading, spacing: 6) {
                            SectionCaption(text: "What to do")
                            MarkdownText(text: recommendation, font: .subheadline)
                        }
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(check.status.tint.opacity(0.10),
                                    in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }

                    if check.muted && !check.mutedNote.isEmpty {
                        Label(check.mutedNote, systemImage: "text.quote")
                            .font(.footnote)
                            .foregroundStyle(Theme.muted)
                    }

                    if !check.actions.isEmpty {
                        VStack(spacing: 10) {
                            ForEach(check.actions) { action in
                                if action.kind == "mute" || action.kind == "unmute" || action.kind == "dismiss" {
                                    Button(action.label) { perform(action) }
                                        .buttonStyle(SecondaryButtonStyle(tint: Theme.muted))
                                } else {
                                    Button {
                                        perform(action)
                                    } label: {
                                        Label(action.label, systemImage: symbol(action))
                                    }
                                    .buttonStyle(PrimaryButtonStyle())
                                }
                            }
                        }
                        .padding(.top, 4)
                    }
                }
                .padding(20)
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    private func symbol(_ action: AlertAction) -> String {
        switch action.kind {
        case "assistant": return "sparkles"
        case "copy":      return "doc.on.doc"
        case "job":       return "trash"
        default:          return "arrow.right"
        }
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
            ThemedList {
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
                    Text("The checks themselves run without AI and cost nothing. The watch reads their result.")
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
