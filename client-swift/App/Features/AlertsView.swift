import SwiftUI
import UIKit

/// What the watch wrote, and what the server flagged before it existed.
///
/// The watch (server 0.24) is an agent that looks at the server on its own and
/// writes a few sentences when something is worth knowing — like a colleague
/// keeping an eye on it. This screen reads like that: messages in prose, each
/// with the step it suggests one tap away, and a way to say "not useful" or
/// "stop telling me about this".
struct AlertsView: View {
    @EnvironmentObject private var app: AppState

    @State private var feed: NotificationFeed?
    @State private var watch: WatchStatus?
    @State private var loaded = false
    @State private var error: String?
    @State private var filter = "all"
    @State private var checking = false
    @State private var container: Container?
    @State private var route: MoreRoute?
    @State private var toast: Toast?

    var body: some View {
        Group {
            if !loaded {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                content
            }
        }
        .navigationTitle("Alerts")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            if app.supports("watch") {
                ToolbarItem(placement: .topBarTrailing) {
                    NavigationLink {
                        WatchSettingsView()
                    } label: {
                        Image(systemName: "gearshape")
                    }
                    .accessibilityLabel("Watch settings")
                }
            }
        }
        .task { await load() }
        .refreshable { await load() }
        .toast($toast)
        .navigationDestination(item: $container) { container in
            ContainerDetailView(container: container) { action in
                try? await app.client?.containerAction(container.id, action)
            }
        }
        .navigationDestination(item: $route) { route in
            MoreDestination(route: route)
        }
    }

    // MARK: - Content

    private var items: [NotificationFeed.Item] {
        let all = feed?.items ?? []
        switch filter {
        case "important": return all.filter { $0.status == .warn || $0.status == .crit }
        case "critical":  return all.filter { $0.status == .crit }
        default:          return all
        }
    }

    private var content: some View {
        List {
            if app.supports("watch") {
                Section { watchCard }
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
            }

            if let feed, !feed.items.isEmpty {
                Section {
                    Picker("Show", selection: $filter) {
                        Text("All").tag("all")
                        Text("Important").tag("important")
                        Text("Critical").tag("critical")
                    }
                    .pickerStyle(.segmented)
                }
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets())

                Section {
                    ForEach(items) { item in
                        AlertCard(item: item) { action in
                            Task { await perform(action, on: item) }
                        }
                        .swipeActions(edge: .trailing) {
                            Button(role: .destructive) {
                                Task { await delete(item) }
                            } label: { Label("Delete", systemImage: "trash") }
                        }
                    }
                } footer: {
                    if items.isEmpty {
                        Text("Nothing at this level.")
                    }
                }
            } else {
                Section {
                    VStack(spacing: 10) {
                        Image(systemName: error == nil ? "bell.slash" : "exclamationmark.triangle")
                            .font(.largeTitle)
                            .foregroundStyle(error == nil ? Theme.muted : Theme.danger)
                        Text(error == nil ? "Nothing to report" : "Cannot load alerts")
                            .font(.headline)
                        Text(error ?? (watch?.settings.enabled == true
                                       ? "The watch writes here when something is worth knowing."
                                       : "When the watch is on, it writes here when something is worth knowing."))
                            .font(.subheadline)
                            .foregroundStyle(Theme.muted)
                            .multilineTextAlignment(.center)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 30)
                }
                .listRowBackground(Color.clear)
            }
        }
        .listStyle(.insetGrouped)
    }

    // MARK: - The watch card

    @ViewBuilder
    private var watchCard: some View {
        if let watch {
            if watch.settings.enabled {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(spacing: 12) {
                        IconTile(symbol: "eye.fill", color: watch.paused ? .gray : .indigo, size: 40)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(watch.paused ? "The watch is paused" : "The watch is on")
                                .font(.headline)
                                .foregroundStyle(Theme.text)
                            Text(watchLine(watch))
                                .font(.footnote)
                                .foregroundStyle(Theme.muted)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer(minLength: 0)
                    }
                    if !watch.route.usable {
                        Label("Its AI is not connected — choose one in its settings.",
                              systemImage: "exclamationmark.triangle.fill")
                            .font(.footnote)
                            .foregroundStyle(Theme.warn)
                    }
                    HStack(spacing: 10) {
                        Button {
                            Task { await checkNow() }
                        } label: {
                            HStack {
                                if checking || watch.running { ProgressView().controlSize(.small) }
                                Text(checking || watch.running ? "Looking…" : "Look now")
                            }
                            .font(.subheadline.weight(.semibold))
                            .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .buttonBorderShape(.capsule)
                        .disabled(checking || watch.running || !watch.route.usable || app.me?.demo == true)

                        Menu {
                            if watch.paused {
                                Button { Task { await pause(0) } } label: { Label("Resume", systemImage: "play.fill") }
                            } else {
                                Button { Task { await pause(60) } } label: { Text("Pause for an hour") }
                                Button { Task { await pause(8 * 60) } } label: { Text("Pause for 8 hours") }
                                Button { Task { await pause(24 * 60) } } label: { Text("Pause for a day") }
                            }
                        } label: {
                            Text(watch.paused ? "Paused" : "Pause")
                                .font(.subheadline.weight(.semibold))
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .buttonBorderShape(.capsule)
                        .disabled(app.me?.demo == true)
                    }
                }
                .padding(16)
                .background(Theme.bg2, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
            } else {
                NavigationLink {
                    WatchSettingsView()
                } label: {
                    HStack(alignment: .top, spacing: 12) {
                        IconTile(symbol: "eye.fill", color: .indigo, size: 40)
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Let the watch look after this server")
                                .font(.headline)
                                .foregroundStyle(Theme.text)
                            Text("An AI checks the server every few hours and right after anything breaks — and writes to you only when it is worth knowing. Quiet at night, never more than a few messages a day.")
                                .font(.footnote)
                                .foregroundStyle(Theme.muted)
                                .fixedSize(horizontal: false, vertical: true)
                            Text("Turn it on")
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(Theme.accent)
                                .padding(.top, 2)
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(16)
                    .background(Theme.bg2, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func watchLine(_ watch: WatchStatus) -> String {
        var parts: [String] = []
        if !watch.route.label.isEmpty { parts.append(watch.route.label) }
        if watch.paused {
            let until = Date(timeIntervalSince1970: watch.settings.pausedUntil)
            parts.append("until \(until.formatted(date: .omitted, time: .shortened))")
        } else if watch.quietNow {
            parts.append("quiet hours — only critical messages")
        } else if let next = watch.nextRound {
            parts.append("next look \(Fmt.ago(Date(timeIntervalSince1970: next)))")
        } else {
            parts.append("reacts to events")
        }
        if let left = watch.budgetLeft {
            parts.append(String(format: "$%.2f of budget left", max(0, left)))
        }
        return parts.joined(separator: " · ")
    }

    // MARK: - Actions

    private func load() async {
        guard let client = app.client else { return }
        defer { loaded = true }
        if app.me == nil { await app.refreshMe() }
        do {
            feed = try await client.notifications()
            error = nil
            // Opening the screen *is* reading them; marking seen here is what
            // clears the badge on the dashboard.
            try? await client.markNotificationsSeen()
            app.clearAlertBadge()
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
        if app.supports("watch") {
            watch = try? await client.watchStatus()
        }
    }

    private func perform(_ action: AlertAction, on item: NotificationFeed.Item) async {
        guard let client = app.client else { return }
        switch action.kind {
        case "assistant":
            app.ask(action.prompt.isEmpty ? "The watch wrote: \"\(item.body)\" — what should I do?" : action.prompt)
        case "container":
            if let found = try? await client.containers().first(where: { $0.name == action.target }) {
                container = found
            } else {
                toast = Toast(text: "\(action.target) is not there any more", isError: true)
            }
        case "open":
            route = MoreRoute.from(target: action.target)
        case "mute":
            let hours = Double(action.target) ?? 24 * 7
            if (try? await client.muteWatchTopic(item.topic.isEmpty ? item.title : item.topic, hours: hours)) != nil {
                toast = Toast(text: "You will not hear about this for \(hours >= 24 * 365 ? "a year" : hours >= 24 * 7 ? "a week" : "a day")")
            }
        case "helpful", "not_helpful":
            try? await client.alertFeedback(item.id, helpful: action.kind == "helpful")
            toast = Toast(text: action.kind == "helpful" ? "Thanks — noted" : "Noted — it will write less like this")
            await load()
        default:
            break
        }
    }

    private func delete(_ item: NotificationFeed.Item) async {
        guard let client = app.client else { return }
        do {
            try await client.deleteAlert(item.id)
            await load()
        } catch {
            toast = Toast(text: "Deleting alerts needs PocketADM 0.24 on the server", isError: true)
        }
    }

    private func checkNow() async {
        guard let client = app.client else { return }
        checking = true
        defer { checking = false }
        do {
            try await client.runWatch(kind: "test")
            toast = Toast(text: "The watch is looking — its answer arrives here")
            // a look takes from a few seconds to a couple of minutes
            for _ in 0..<40 {
                try? await Task.sleep(for: .seconds(4))
                guard let status = try? await client.watchStatus() else { break }
                watch = status
                if !status.running { break }
            }
            await load()
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func pause(_ minutes: Int) async {
        guard let client = app.client else { return }
        watch = try? await client.pauseWatch(minutes: minutes)
        toast = Toast(text: minutes == 0 ? "The watch is back" : "Paused")
    }
}

/// One message: what it is about, how much it matters, what to do.
struct AlertCard: View {
    let item: NotificationFeed.Item
    let perform: (AlertAction) -> Void
    @State private var showSteps = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: item.status.symbol)
                    .foregroundStyle(item.status.tint)
                Text(levelText)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(item.status.tint)
                if item.count > 1 {
                    // The server de-duplicates by fingerprint, so a recurring
                    // alert is one row with a count rather than forty identical ones.
                    StatusPill(text: "×\(item.count)", tint: Theme.muted)
                }
                Spacer()
                Text(Fmt.ago(item.date))
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
            }

            Text(item.title)
                .font(.headline)
                .foregroundStyle(Theme.text)
                .fixedSize(horizontal: false, vertical: true)

            if !item.body.isEmpty {
                MarkdownText(text: item.body, font: .subheadline)
            }

            if !item.actions.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(item.actions) { action in
                            Button {
                                perform(action)
                            } label: {
                                Label(action.label, systemImage: symbol(for: action))
                                    .font(.footnote.weight(.semibold))
                                    .padding(.horizontal, 11)
                                    .padding(.vertical, 6)
                                    .background(Theme.accent.opacity(0.12), in: Capsule())
                                    .foregroundStyle(Theme.accent)
                            }
                            .buttonStyle(.borderless)
                        }
                    }
                }
            }

            if item.isWatch {
                HStack(spacing: 14) {
                    if !item.steps.isEmpty {
                        Button {
                            withAnimation(.snappy) { showSteps.toggle() }
                        } label: {
                            Label(showSteps ? "Hide what it checked" : "What it checked",
                                  systemImage: "magnifyingglass")
                        }
                        .buttonStyle(.borderless)
                    }
                    Spacer()
                    Menu {
                        Button { perform(mute(24)) } label: { Text("Mute this topic for a day") }
                        Button { perform(mute(24 * 7)) } label: { Text("Mute it for a week") }
                        Button { perform(mute(24 * 365)) } label: { Text("Never about this again") }
                    } label: {
                        Image(systemName: "bell.slash")
                    }
                    .accessibilityLabel("Mute this topic")
                    Button {
                        perform(feedback(true))
                    } label: {
                        Image(systemName: item.feedback == "helpful" ? "hand.thumbsup.fill" : "hand.thumbsup")
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel("Helpful")
                    Button {
                        perform(feedback(false))
                    } label: {
                        Image(systemName: item.feedback == "not_helpful" ? "hand.thumbsdown.fill" : "hand.thumbsdown")
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel("Not helpful")
                }
                .font(.footnote)
                .foregroundStyle(Theme.muted)

                if showSteps {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(Array(item.steps.enumerated()), id: \.offset) { _, step in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(step.detail.isEmpty ? step.tool : step.detail)
                                    .font(.system(size: 11, design: .monospaced))
                                    .foregroundStyle(Theme.text)
                                if !step.output.isEmpty {
                                    Text(step.output)
                                        .font(.system(size: 10, design: .monospaced))
                                        .foregroundStyle(Theme.muted)
                                        .lineLimit(4)
                                }
                            }
                        }
                    }
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.bg3, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                }
            }
        }
        .padding(.vertical, 6)
    }

    private var levelText: String {
        if !item.importance.isEmpty { return item.importance.capitalized }
        return item.status.label
    }

    private func symbol(for action: AlertAction) -> String {
        switch action.kind {
        case "container": return "shippingbox"
        case "assistant": return "sparkles"
        case "open":
            switch action.target {
            case "updates":  return "arrow.triangle.2.circlepath"
            case "storage":  return "internaldrive"
            case "checks":   return "checkmark.shield"
            case "activity": return "clock.arrow.circlepath"
            default:         return "arrow.right"
            }
        default: return "arrow.right"
        }
    }

    private func mute(_ hours: Double) -> AlertAction {
        AlertAction.make(kind: "mute", target: String(Int(hours)))
    }

    private func feedback(_ helpful: Bool) -> AlertAction {
        AlertAction.make(kind: helpful ? "helpful" : "not_helpful")
    }
}

extension AlertAction {
    /// An action made on the phone (mute, feedback) rather than sent by the server.
    static func make(kind: String, target: String = "") -> AlertAction {
        let json = "{\"kind\":\"\(kind)\",\"target\":\"\(target)\"}"
        // the type is Decodable only; building it through its own decoder keeps
        // one initialiser and the lenient defaults
        return try! JSONDecoder().decode(AlertAction.self, from: Data(json.utf8))
    }
}

extension MoreRoute {
    /// The screen a server-sent link names.
    static func from(target: String) -> MoreRoute? {
        switch target {
        case "updates":            return .updates
        case "storage", "files":   return .files
        case "checks", "health":   return .checks
        case "activity":           return .activity
        case "security":           return .security
        case "alerts", "watch":    return .alerts
        case "ai", "accounts":     return .ai
        case "apps":               return .apps
        default:                   return nil
        }
    }
}
