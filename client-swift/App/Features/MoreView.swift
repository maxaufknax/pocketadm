import SwiftUI

/// The hub for everything that does not earn a tab of its own.
///
/// Five tabs is the practical limit on a phone before the bar becomes a row of
/// unreadable icons, and the four that made the cut are the ones you open
/// repeatedly. These are the screens you visit on purpose.
struct MoreView: View {
    @EnvironmentObject private var app: AppState

    @State private var updateCount = 0
    @State private var checkScore: Severity?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    NavigationLink {
                        UpdatesView()
                    } label: {
                        NavRow(symbol: "arrow.triangle.2.circlepath",
                               title: "Updates",
                               subtitle: updateCount > 0
                                 ? "\(updateCount) image\(updateCount == 1 ? "" : "s") ready to update"
                                 : "Container images and rollback points",
                               badge: updateCount > 0 ? String(updateCount) : "",
                               badgeTint: Theme.accent)
                    }

                    NavigationLink {
                        ChecksView()
                    } label: {
                        NavRow(symbol: "checkmark.shield",
                               title: "Checks",
                               subtitle: "Security and health of this server",
                               badge: checkBadge,
                               badgeTint: checkScore?.tint ?? Theme.accent)
                    }

                    NavigationLink {
                        AppsView()
                    } label: {
                        NavRow(symbol: "square.grid.2x2",
                               title: "Apps",
                               subtitle: "Install services in one tap")
                    }

                    NavigationLink {
                        NotificationsView()
                    } label: {
                        NavRow(symbol: "bell",
                               title: "Alerts",
                               subtitle: "What the server has flagged",
                               badge: app.unseenAlerts > 0 ? String(app.unseenAlerts) : "",
                               badgeTint: Theme.danger)
                    }
                } header: {
                    SectionCaption(text: "Operate")
                }
                .listRowBackground(Theme.bg2)

                Section {
                    NavigationLink {
                        FilesView()
                    } label: {
                        NavRow(symbol: "folder", title: "Files",
                               subtitle: "Browse the configured workspaces")
                    }

                    NavigationLink {
                        UsersView()
                    } label: {
                        NavRow(symbol: "person.2", title: "Users",
                               subtitle: "Linux accounts on the host")
                    }

                    NavigationLink {
                        CLIsView()
                    } label: {
                        NavRow(symbol: "chevron.left.forwardslash.chevron.right",
                               title: "Coding agents",
                               subtitle: "Claude Code, Codex, Vibe")
                    }
                } header: {
                    SectionCaption(text: "The machine")
                }
                .listRowBackground(Theme.bg2)

                Section {
                    NavigationLink {
                        AISettingsView()
                    } label: {
                        NavRow(symbol: "sparkles", title: "AI",
                               subtitle: app.me?.aiConfigured == true
                                 ? "Keys, default model and usage"
                                 : "Not configured yet")
                    }

                    NavigationLink {
                        SecurityView()
                    } label: {
                        NavRow(symbol: "lock.shield", title: "Security",
                               subtitle: app.me?.totpEnabled == true
                                 ? "Password, 2FA on, devices"
                                 : "Password, 2FA off, devices",
                               badge: app.me?.shouldWarnAboutExposure == true ? "!" : "",
                               badgeTint: Theme.danger)
                    }

                    NavigationLink {
                        SettingsView()
                    } label: {
                        NavRow(symbol: "gearshape", title: "Settings",
                               subtitle: "Connection, pairing and this device")
                    }
                } header: {
                    SectionCaption(text: "Configure")
                }
                .listRowBackground(Theme.bg2)
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(Theme.bg)
            .navigationTitle("More")
            .screenBackground()
        }
        .task { await loadBadges() }
    }

    private var checkBadge: String {
        switch checkScore {
        case .crit: return "!"
        case .warn: return "•"
        default:    return ""
        }
    }

    /// Badges are advisory. Both calls can be slow (a registry round trip, a
    /// report read) and neither is worth blocking the hub on, so failures are
    /// silent and simply leave the badge off.
    private func loadBadges() async {
        guard let client = app.client else { return }
        await app.refreshMe()
        await app.refreshAlerts()
        if let updates = try? await client.updates() {
            updateCount = updates.pending.count
        }
        if let report = try? await client.latestReport() {
            checkScore = report.score
        }
    }
}
