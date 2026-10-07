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
                ProgressView().tint(Theme.accent).frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if events.isEmpty {
                MessageState(symbol: error == nil ? "list.bullet.rectangle" : "exclamationmark.triangle",
                             title: error == nil ? "Nothing logged yet" : "Cannot read the log",
                             message: error,
                             tint: error == nil ? Theme.muted : Theme.danger,
                             retry: { Task { await reload() } })
            } else {
                list
            }
        }
        .navigationTitle("Activity")
        .screenBackground()
        .task { if !loaded { await reload() } }
    }

    private var list: some View {
        List {
            ForEach(events) { event in
                HStack(alignment: .top, spacing: 10) {
                    Text(meta[event.action]?.icon ?? "•")
                        .frame(width: 22)

                    VStack(alignment: .leading, spacing: 3) {
                        Text(meta[event.action]?.label ?? event.action)
                            .font(.subheadline.weight(.medium))
                            .foregroundStyle(event.status == .ok ? Theme.text : event.status.tint)
                        if !event.target.isEmpty {
                            Text(event.target)
                                .font(.caption)
                                .foregroundStyle(Theme.text)
                                .lineLimit(1)
                                .truncationMode(.middle)
                        }
                        if !event.detail.isEmpty {
                            Text(event.detail)
                                .font(.caption)
                                .foregroundStyle(Theme.muted)
                                .lineLimit(2)
                        }
                        Text("\(Fmt.ago(event.date)) · \(event.source)")
                            .font(.caption2)
                            .foregroundStyle(Theme.muted)
                    }
                    Spacer(minLength: 0)
                }
                .padding(.vertical, 3)
                .listRowBackground(Theme.bg2)
            }

            if cursor != nil {
                Button {
                    Task { await loadMore() }
                } label: {
                    HStack {
                        Spacer()
                        if loadingMore {
                            ProgressView().tint(Theme.muted)
                        } else {
                            Text("Load older").font(.subheadline)
                        }
                        Spacer()
                    }
                }
                .tint(Theme.accent)
                .listRowBackground(Theme.bg2)
            }
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
        .background(Theme.bg)
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
