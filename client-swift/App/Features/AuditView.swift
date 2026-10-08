import SwiftUI

/// Who did what on this server, newest first — including everything this app
/// itself did. Paginated with the server's `before=` cursor.
struct AuditView: View {
    @EnvironmentObject private var app: AppState

    @State private var events: [AuditFeed.Event] = []
    @State private var meta: [String: AuditFeed.ActionMeta] = [:]
    @State private var cursor: Double?
    @State private var loaded = false
    @State private var loadingMore = false
    @State private var error: String?

    var body: some View {
        Group {
            if !loaded {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if events.isEmpty {
                MessageState(symbol: error == nil ? "list.bullet.rectangle" : "exclamationmark.triangle",
                             title: error == nil ? "Nothing logged yet" : "Cannot read the log",
                             message: error,
                             tint: error == nil ? Theme.muted : Theme.danger,
                             retry: retryIfFailed)
            } else {
                list
            }
        }
        .navigationTitle("Activity")
        .navigationBarTitleDisplayMode(.large)
        .task { if !loaded { await reload() } }
    }

    /// Offered only when loading failed — an empty log needs no retry.
    private var retryIfFailed: (() -> Void)? {
        guard error != nil else { return nil }
        return { Task { await reload() } }
    }

    private var list: some View {
        ThemedList {
            ForEach(events) { event in
                HStack(alignment: .top, spacing: 14) {
                    let style = AuditStyle.of(event.action)
                    IconTile(symbol: style.symbol, color: event.status == .ok ? style.color : event.status.tint)

                    VStack(alignment: .leading, spacing: 2) {
                        Text(meta[event.action]?.label ?? event.action)
                            .foregroundStyle(event.status == .ok ? Theme.text : event.status.tint)
                        if !event.target.isEmpty {
                            Text(event.target)
                                .font(.footnote)
                                .foregroundStyle(Theme.text)
                                .lineLimit(1)
                                .truncationMode(.middle)
                        }
                        if !event.detail.isEmpty {
                            Text(event.detail)
                                .font(.footnote)
                                .foregroundStyle(Theme.muted)
                                .lineLimit(2)
                        }
                        Text("\(Fmt.ago(event.date)) · \(event.source)")
                            .font(.caption)
                            .foregroundStyle(Theme.muted)
                    }
                    Spacer(minLength: 0)
                }
                .padding(.vertical, 2)
            }

            if cursor != nil {
                Button {
                    Task { await loadMore() }
                } label: {
                    HStack {
                        Spacer()
                        if loadingMore {
                            ProgressView()
                        } else {
                            Text("Load older")
                        }
                        Spacer()
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await reload() }
    }

    private func reload() async {
        guard let client = app.client else { return }
        defer { loaded = true }
        do {
            let feed = try await client.audit(limit: 80)
            events = feed.events
            meta = feed.meta.actions
            cursor = feed.cursor
            error = nil
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
    }

    private func loadMore() async {
        guard let client = app.client, let cursor, !loadingMore else { return }
        loadingMore = true
        defer { loadingMore = false }
        guard let feed = try? await client.audit(limit: 80, before: cursor) else { return }
        events.append(contentsOf: feed.events)
        // A nil cursor is the end of the log; keeping the old one would loop
        // the same page forever.
        self.cursor = feed.cursor
    }
}

/// A symbol per logged action — the server sends emoji, which cannot be
/// tinted and look different on every row.
enum AuditStyle {
    struct Style {
        let symbol: String
        let color: Color
    }

    static func of(_ action: String) -> Style {
        switch action {
        case "login":                        return Style(symbol: "lock.open.fill", color: .green)
        case "login_failed":                 return Style(symbol: "xmark.octagon.fill", color: .red)
        case "logout_all":                   return Style(symbol: "rectangle.portrait.and.arrow.right", color: .orange)
        case "password_change", "user_password": return Style(symbol: "key.fill", color: .gray)
        case "user_lock":                    return Style(symbol: "lock.fill", color: .orange)
        case "user_admin":                   return Style(symbol: "crown.fill", color: .yellow)
        case "user_create":                  return Style(symbol: "person.fill.badge.plus", color: .blue)
        case "2fa_enable":                   return Style(symbol: "lock.shield.fill", color: .green)
        case "2fa_disable":                  return Style(symbol: "lock.shield", color: .orange)
        case "container_action":             return Style(symbol: "shippingbox.fill", color: .brown)
        case "container_remove":             return Style(symbol: "trash.fill", color: .red)
        case "cli_install":                  return Style(symbol: "chevron.left.forwardslash.chevron.right", color: .teal)
        case "terminal", "terminal_kill":    return Style(symbol: "terminal.fill", color: .gray)
        case "update_apply":                 return Style(symbol: "arrow.down.circle.fill", color: .orange)
        case "app_install", "app_uninstall": return Style(symbol: "square.grid.2x2.fill", color: .blue)
        case "integration_save", "integration_delete":
            return Style(symbol: "puzzlepiece.extension.fill", color: .indigo)
        case "loop_save", "loop_run":        return Style(symbol: "arrow.triangle.2.circlepath", color: .purple)
        case "agent_tool":                   return Style(symbol: "sparkles", color: .purple)
        case "settings":                     return Style(symbol: "gearshape.fill", color: .gray)
        default:                             return Style(symbol: "circle.fill", color: .gray)
        }
    }
}
