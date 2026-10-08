import SwiftUI

/// Follows the server's live activity stream (Server-Sent Events) and keeps
/// the newest events first. Reconnects after a dropped connection.
@MainActor
final class ActivityStream: ObservableObject {
    @Published var events: [ActivityEvent] = []
    @Published var stats = ActivityFeed.Stats()
    @Published var categories: [String: String] = [:]
    @Published var cursor: Double?
    @Published var live = false
    @Published var loaded = false
    @Published var error: String?
    private var task: Task<Void, Never>?

    func load(_ app: AppState, categories filter: [String]) async {
        guard let client = app.client else { return }
        defer { loaded = true }
        do {
            let feed = try await client.activity(limit: 120, categories: filter)
            events = feed.events
            cursor = feed.cursor
            stats = feed.stats
            if !feed.categories.isEmpty { categories = feed.categories }
            error = nil
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
    }

    func loadMore(_ app: AppState, categories filter: [String]) async {
        guard let client = app.client, let cursor else { return }
        guard let feed = try? await client.activity(limit: 120, before: cursor, categories: filter) else { return }
        let known = Set(events.map(\.id))
        events.append(contentsOf: feed.events.filter { !known.contains($0.id) })
        // A nil cursor is the end of the log; keeping the old one would loop
        // the same page forever.
        self.cursor = feed.cursor
    }

    func start(_ app: AppState, categories filter: [String]) {
        stop()
        task = Task { [weak self] in
            while !Task.isCancelled {
                guard let client = app.client,
                      let request = try? await client.activityStreamRequest(categories: filter) else { return }
                do {
                    let (bytes, response) = try await NetworkSession.shared.bytes(for: request)
                    if let http = response as? HTTPURLResponse, http.statusCode != 200 {
                        self?.live = false
                        return
                    }
                    self?.live = true
                    for try await line in bytes.lines {
                        if Task.isCancelled { break }
                        guard let event = ActivityEvent.fromStreamLine(line) else { continue }
                        self?.insert(event)
                    }
                } catch {
                    if Task.isCancelled { break }
                }
                self?.live = false
                try? await Task.sleep(for: .seconds(3))
            }
        }
    }

    func stop() {
        task?.cancel()
        task = nil
        live = false
    }

    private func insert(_ event: ActivityEvent) {
        guard !events.contains(where: { $0.id == event.id }) else { return }
        withAnimation(.snappy) { events.insert(event, at: 0) }
        stats = ActivityFeed.Stats.bumped(stats, with: event)
        if events.count > 1000 { events.removeLast(events.count - 1000) }
    }
}

extension ActivityFeed.Stats {
    static func bumped(_ stats: ActivityFeed.Stats, with event: ActivityEvent) -> ActivityFeed.Stats {
        var counts = stats.counts
        counts[event.category, default: 0] += 1
        let json: [String: Any] = ["hours": stats.hours, "counts": counts,
                                   "problems": stats.problems + (event.severity == .warn || event.severity == .crit ? 1 : 0)]
        guard let data = try? JSONSerialization.data(withJSONObject: json),
              let fresh = try? JSONDecoder().decode(ActivityFeed.Stats.self, from: data) else { return stats }
        return fresh
    }
}

/// Two halves: **Server** — everything that happens on the machine, live:
/// containers starting and dying, SSH logins and break-in attempts, sudo, apt,
/// the kernel, the internet connection — and **PocketADM** — what was done in
/// and through this app: sign-ins, container actions, the assistant's steps,
/// file changes, updates.
struct ActivityView: View {
    @EnvironmentObject private var app: AppState
    @StateObject private var stream = ActivityStream()
    @State private var filter: String?
    @State private var selected: ActivityEvent?
    @AppStorage("pocketadm.activity.scope") private var scope = "server"

    /// The server's own categories; "app" is the PocketADM half.
    private static let order = ["containers", "security", "system", "network", "updates"]

    private var showsServer: Bool { app.supports("activity") && scope == "server" }

    var body: some View {
        Group {
            if !showsServer {
                AuditView()
            } else if !stream.loaded {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                list
            }
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            if app.supports("activity") {
                Picker("Show", selection: $scope) {
                    Text("Server").tag("server")
                    Text("PocketADM").tag("app")
                }
                .pickerStyle(.segmented)
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
                .background(.bar)
            }
        }
        .navigationTitle("Activity")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            if showsServer {
                ToolbarItem(placement: .topBarTrailing) {
                    HStack(spacing: 5) {
                        Circle()
                            .fill(stream.live ? Color.green : Color(uiColor: .systemGray3))
                            .frame(width: 8, height: 8)
                        Text(stream.live ? "Live" : "Offline")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(stream.live ? .green : Theme.muted)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
        }
        .task {
            if app.me == nil { await app.refreshMe() }
            guard app.supports("activity") else { return }
            await stream.load(app, categories: filterList)
            stream.start(app, categories: filterList)
        }
        .onChange(of: filter) { _, _ in
            Task {
                await stream.load(app, categories: filterList)
                stream.start(app, categories: filterList)
            }
        }
        .onDisappear { stream.stop() }
        .sheet(item: $selected) { event in
            ActivityDetailSheet(event: event)
                .presentationDetents([.medium])
        }
    }

    /// Without a chip chosen: every server category, PocketADM's own actions
    /// live in the other half.
    private var filterList: [String] { filter.map { [$0] } ?? Self.order }

    private var list: some View {
        List {
            Section {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        FilterChip(title: "All", count: nil, selected: filter == nil) { filter = nil }
                        ForEach(Self.order, id: \.self) { category in
                            FilterChip(title: label(category),
                                       count: stream.stats.counts[category].flatMap { $0 > 0 ? $0 : nil },
                                       tint: ActivityStyle.color(category),
                                       selected: filter == category) {
                                filter = filter == category ? nil : category
                            }
                        }
                    }
                }
            } footer: {
                Text(statsLine)
            }
            .listRowBackground(Color.clear)
            .listRowInsets(EdgeInsets())

            if stream.events.isEmpty {
                Section {
                    Text(stream.error ?? "Nothing has happened yet. Containers, logins, sudo, packages, the kernel and the internet connection appear here the moment something happens.")
                        .foregroundStyle(stream.error == nil ? Theme.muted : Theme.danger)
                }
            }

            ForEach(days, id: \.0) { day in
                Section(day.0) {
                    ForEach(day.1) { event in
                        Button { selected = event } label: { ActivityRow(event: event) }
                    }
                }
            }

            if stream.cursor != nil {
                Button("Load older") {
                    Task { await stream.loadMore(app, categories: filterList) }
                }
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await stream.load(app, categories: filterList) }
    }

    private var statsLine: String {
        let total = stream.stats.counts.filter { $0.key != "app" }.values.reduce(0, +)
        var line = "\(total) events in the last \(stream.stats.hours) hours"
        if stream.stats.problems > 0 { line += " · \(stream.stats.problems) worth a look" }
        return line
    }

    private func label(_ category: String) -> String {
        switch category {
        case "containers": return "Containers"
        case "security":   return "Security"
        case "system":     return "System"
        case "network":    return "Network"
        case "updates":    return "Updates"
        case "app":        return "PocketADM"
        default:           return stream.categories[category] ?? category.capitalized
        }
    }

    /// Events by day, newest first: "Today", "Yesterday", "Mon 6 Oct".
    private var days: [(String, [ActivityEvent])] {
        let calendar = Calendar.current
        var out: [(String, [ActivityEvent])] = []
        for event in stream.events {
            let title: String
            if calendar.isDateInToday(event.date) {
                title = "Today"
            } else if calendar.isDateInYesterday(event.date) {
                title = "Yesterday"
            } else {
                title = event.date.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated))
            }
            if out.last?.0 == title {
                out[out.count - 1].1.append(event)
            } else {
                out.append((title, [event]))
            }
        }
        return out
    }
}

struct ActivityRow: View {
    let event: ActivityEvent

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            IconTile(symbol: ActivityStyle.symbol(event), color: ActivityStyle.tint(event), size: 30)
            VStack(alignment: .leading, spacing: 2) {
                Text(event.title)
                    .font(.subheadline)
                    .foregroundStyle(event.severity == .crit ? Theme.danger : Theme.text)
                    .lineLimit(2)
                if !event.detail.isEmpty {
                    Text(event.detail)
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                        .lineLimit(2)
                }
            }
            Spacer(minLength: 6)
            Text(event.date.formatted(date: .omitted, time: .shortened))
                .font(.caption.monospacedDigit())
                .foregroundStyle(Theme.muted)
        }
        .padding(.vertical, 1)
    }
}

struct ActivityDetailSheet: View {
    let event: ActivityEvent

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    HStack(spacing: 12) {
                        IconTile(symbol: ActivityStyle.symbol(event), color: ActivityStyle.tint(event), size: 40)
                        Text(event.title)
                            .font(.headline)
                            .foregroundStyle(Theme.text)
                    }
                    if !event.detail.isEmpty {
                        Text(event.detail)
                            .font(.subheadline)
                            .textSelection(.enabled)
                    }
                }
                Section {
                    FactRow(label: "When", value: event.date.formatted(date: .abbreviated, time: .standard))
                    if !event.target.isEmpty { FactRow(label: "About", value: event.target, selectable: true) }
                    FactRow(label: "Reported by", value: source)
                    FactRow(label: "Kind", value: event.kind)
                }
                if event.severity == .warn || event.severity == .crit {
                    Section {
                        Button {
                            dismiss()
                            app.ask("This just happened on the server: \"\(event.title)\""
                                    + (event.detail.isEmpty ? "" : " (\(event.detail))")
                                    + ". What does it mean, and do I need to do something?")
                        } label: {
                            Label("Ask the assistant about it", systemImage: "sparkles")
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
    }

    private var source: String {
        switch event.source {
        case "docker":    return "Docker"
        case "host":      return "The server's logs"
        case "pocketadm": return "PocketADM"
        case "metrics":   return "Connection monitor"
        case "watch":     return "The watch"
        default:          return event.source
        }
    }
}

/// Symbols and colours for activity events, by category and kind.
enum ActivityStyle {
    static func color(_ category: String) -> Color {
        switch category {
        case "containers": return .brown
        case "security":   return .red
        case "system":     return .gray
        case "network":    return .blue
        case "updates":    return .orange
        default:           return .purple
        }
    }

    static func tint(_ event: ActivityEvent) -> Color {
        switch event.severity {
        case .crit: return .red
        case .warn: return .orange
        default:    return color(event.category)
        }
    }

    static func symbol(_ event: ActivityEvent) -> String {
        let kind = event.kind
        if kind.hasPrefix("docker.die") { return "stop.circle.fill" }
        if kind.hasPrefix("docker.start") { return "play.circle.fill" }
        if kind.hasPrefix("docker.health") { return "cross.case.fill" }
        if kind.hasPrefix("docker.oom") || kind == "kernel.oom" { return "memorychip.fill" }
        if kind.hasPrefix("docker.image") { return "arrow.down.circle.fill" }
        if kind.hasPrefix("docker.") { return "shippingbox.fill" }
        if kind == "ssh.login" { return "person.badge.key.fill" }
        if kind == "ssh.failed" { return "lock.trianglebadge.exclamationmark.fill" }
        if kind == "sudo" { return "terminal.fill" }
        if kind.hasPrefix("fail2ban") { return "shield.lefthalf.filled" }
        if kind.hasPrefix("user.") { return "person.fill" }
        if kind.hasPrefix("apt") { return "shippingbox.and.arrow.backward.fill" }
        if kind.hasPrefix("net.down") { return "wifi.slash" }
        if kind.hasPrefix("net.") { return "wifi" }
        if kind.hasPrefix("disk") || kind.hasPrefix("usb") { return "externaldrive.fill" }
        if kind.hasPrefix("power") { return "power" }
        if kind.hasPrefix("systemd") { return "gearshape.2.fill" }
        if kind.hasPrefix("watch") { return "eye.fill" }
        if kind.hasPrefix("pocketadm.agent") { return "sparkles" }
        if kind.hasPrefix("pocketadm.login") { return "iphone" }
        if kind.hasPrefix("pocketadm.") { return "app.badge.fill" }
        return "circle.fill"
    }
}
