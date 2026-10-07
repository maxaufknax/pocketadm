import SwiftUI

/// Everything that does not earn a tab of its own, ordered the way Settings
/// orders things: who you are connected to first, then what you do with the
/// server, then how it is watched, then the assistant, security, and the app.
struct MoreView: View {
    @EnvironmentObject private var app: AppState

    @State private var path: [MoreRoute] = []
    @State private var updateCount = 0
    @State private var checkScore: Severity?
    @State private var showPairSheet = false

    var body: some View {
        NavigationStack(path: $path) {
            List {
                Section {
                    NavigationLink(value: MoreRoute.server) { serverCard }
                }

                Section("Manage") {
                    link(.updates, NavRow(symbol: "arrow.triangle.2.circlepath", title: "Updates",
                                          subtitle: updateCount > 0
                                            ? "\(updateCount) image\(updateCount == 1 ? "" : "s") ready"
                                            : "Images and rollback points",
                                          badge: updateCount > 0 ? String(updateCount) : "",
                                          tint: .orange))
                    link(.apps, NavRow(symbol: "square.grid.2x2.fill", title: "Apps",
                                       subtitle: "Install services in one tap", tint: .blue))
                    link(.files, NavRow(symbol: "folder.fill", title: "Files",
                                        subtitle: "Browse the workspaces", tint: .cyan))
                    link(.users, NavRow(symbol: "person.2.fill", title: "Users",
                                        subtitle: "Linux accounts on the host", tint: .gray))
                }

                Section("Monitor") {
                    link(.alerts, NavRow(symbol: "bell.fill", title: "Alerts",
                                         subtitle: "What the server has flagged",
                                         badge: app.unseenAlerts > 0 ? String(app.unseenAlerts) : "",
                                         tint: .red))
                    link(.checks, NavRow(symbol: "checkmark.shield.fill", title: "Health checks",
                                         subtitle: "Security and health of this server",
                                         badge: checkBadge, badgeTint: checkScore?.tint ?? Theme.danger,
                                         tint: .green))
                    link(.activity, NavRow(symbol: "clock.arrow.circlepath", title: "Activity",
                                           subtitle: "Every action taken on this server", tint: .indigo))
                }

                Section("Assistant") {
                    link(.ai, NavRow(symbol: "sparkles", title: "AI models",
                                     subtitle: app.me?.aiConfigured == true
                                       ? "Providers, default model and usage"
                                       : "Not set up yet",
                                     tint: .purple))
                    link(.agents, NavRow(symbol: "chevron.left.forwardslash.chevron.right",
                                         title: "Coding agents",
                                         subtitle: "Claude Code, Codex and more", tint: .teal))
                }

                Section("Security") {
                    link(.security, NavRow(symbol: "lock.shield.fill", title: "Password & 2FA",
                                           subtitle: app.me?.totpEnabled == true
                                             ? "Two-factor is on"
                                             : "Two-factor is off",
                                           badge: app.me?.shouldWarnAboutExposure == true ? "!" : "",
                                           tint: .blue))
                    Button {
                        showPairSheet = true
                    } label: {
                        NavRow(symbol: "qrcode", title: "Pair a device",
                               subtitle: app.me?.canPair == false
                                 ? "Turned off on this server"
                                 : "Sign in another phone by QR code",
                               tint: .green)
                    }
                    .disabled(app.me?.canPair == false)
                }

                Section("App") {
                    AppearancePicker()
                    link(.about, NavRow(symbol: "info.circle.fill", title: "About PocketADM",
                                        tint: .gray))
                }
            }
            .navigationTitle("More")
            .navigationBarTitleDisplayMode(.large)
            .navigationDestination(for: MoreRoute.self) { route in
                destination(route)
            }
            .sheet(isPresented: $showPairSheet) { PairCodeSheet() }
            .refreshable { await loadBadges() }
        }
        .task {
            await loadBadges()
            openScreenshotRoute()
        }
    }

    private func link(_ route: MoreRoute, _ row: NavRow) -> some View {
        NavigationLink(value: route) { row }
    }

    /// The server you are signed in to, the way Settings shows your account.
    private var serverCard: some View {
        HStack(spacing: 14) {
            ServiceIcon(names: ["pocketadm"], size: 56)
            VStack(alignment: .leading, spacing: 3) {
                Text(app.serverName.isEmpty ? "Server" : app.serverName)
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(Theme.text)
                Text(app.serverURL?.host ?? "")
                    .font(.footnote)
                    .foregroundStyle(Theme.muted)
                    .lineLimit(1)
                if let me = app.me {
                    Text(me.demo ? "Demo · PocketADM \(me.version)" : "PocketADM \(me.version)")
                        .font(.footnote)
                        .foregroundStyle(Theme.muted)
                }
            }
        }
        .padding(.vertical, 6)
    }

    @ViewBuilder
    private func destination(_ route: MoreRoute) -> some View {
        switch route {
        case .server:   SettingsView()
        case .updates:  UpdatesView()
        case .apps:     AppsView()
        case .files:    FilesView()
        case .users:    UsersView()
        case .alerts:   NotificationsView()
        case .checks:   ChecksView()
        case .activity: AuditView()
        case .ai:       AISettingsView()
        case .agents:   CLIsView()
        case .security: SecurityView()
        case .about:    AboutView()
        }
    }

    private var checkBadge: String {
        switch checkScore {
        case .crit, .warn: return "!"
        default:           return ""
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

    /// Screenshot runs: `-PocketADMScreenshotRoute updates` opens that screen.
    private func openScreenshotRoute() {
        guard AppState.screenshotTab == MainTab.more.rawValue, path.isEmpty,
              let raw = AppState.screenshotRoute, let route = MoreRoute(rawValue: raw) else { return }
        path = [route]
    }
}

/// The screens behind the More tab, by value so a screenshot run (or a later
/// deep link) can open one directly.
enum MoreRoute: String, Hashable {
    case server, updates, apps, files, users, alerts, checks, activity, ai, agents, security, about
}

/// System, light or dark — stored per device.
struct AppearancePicker: View {
    @AppStorage("pocketadm.appearance") private var appearance = "system"

    var body: some View {
        Picker(selection: $appearance) {
            Text("System").tag("system")
            Text("Light").tag("light")
            Text("Dark").tag("dark")
        } label: {
            HStack(spacing: 14) {
                IconTile(symbol: "circle.lefthalf.filled", color: .indigo)
                Text("Appearance")
                    .foregroundStyle(Theme.text)
            }
        }
        .pickerStyle(.menu)
    }
}
