import SwiftUI

/// What the server's background agents have flagged since you last looked:
/// pending updates, failed backups, health findings.
struct NotificationsView: View {
    @EnvironmentObject private var app: AppState

    @State private var feed: NotificationFeed?
    @State private var loaded = false
    @State private var error: String?

    var body: some View {
        Group {
            if !loaded {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let feed, !feed.items.isEmpty {
                list(feed)
            } else {
                MessageState(symbol: error == nil ? "bell.slash" : "exclamationmark.triangle",
                             title: error == nil ? "Nothing to report" : "Cannot load alerts",
                             message: error ?? "The server has raised no alerts.",
                             tint: error == nil ? Theme.muted : Theme.danger,
                             retry: error == nil ? nil : { Task { await load() } })
            }
        }
        .navigationTitle("Alerts")
        .navigationBarTitleDisplayMode(.large)
        .task { await load() }
    }

    private func list(_ feed: NotificationFeed) -> some View {
        List {
            ForEach(feed.items) { item in
                HStack(alignment: .top, spacing: 14) {
                    IconTile(symbol: item.status.symbol, color: item.status.tint)
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(alignment: .firstTextBaseline) {
                            Text(item.title)
                                .font(.body.weight(.semibold))
                                .foregroundStyle(Theme.text)
                            Spacer(minLength: 8)
                            if item.count > 1 {
                                // The server de-duplicates by fingerprint, so a
                                // recurring alert is one row with a count rather
                                // than forty identical ones.
                                StatusPill(text: "×\(item.count)", tint: Theme.muted)
                            }
                        }
                        Text(item.body)
                            .font(.subheadline)
                            .foregroundStyle(Theme.muted)
                            .fixedSize(horizontal: false, vertical: true)
                        Text("\(item.source) · \(Fmt.ago(item.date))")
                            .font(.caption)
                            .foregroundStyle(Color(uiColor: .tertiaryLabel))
                    }
                }
                .padding(.vertical, 2)
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await load() }
    }

    private func load() async {
        guard let client = app.client else { return }
        defer { loaded = true }
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
    }
}
