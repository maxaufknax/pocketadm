import SwiftUI

/// Routes between the three phases of the app. Each is a full-screen takeover
/// rather than a sheet: you are either connected or you are not.
struct RootView: View {
    @EnvironmentObject private var app: AppState

    var body: some View {
        Group {
            switch app.phase {
            case .connect:
                ConnectView()
            case .authenticate(let info):
                LoginView(info: info)
            case .ready:
                MainTabs()
            }
        }
        .animation(.easeInOut(duration: 0.25), value: app.phase)
        .task {
            // A screenshot run (release and preview workflows) opens the demo
            // on its own — or stays on the screens before signing in.
            if let shot = AppState.screenshotTab, app.phase == .connect {
                switch shot {
                case "connect": break
                case "login":   try? await app.identifyDemo()
                default:        try? await app.openDemo()
                }
                return
            }
            // A server restored from the keychain has no ServerInfo yet, so the
            // login screen would not know whether to show the 2FA field.
            await app.refreshServerInfo()
        }
    }
}

/// The five tabs. Raw values double as the screenshot workflow's
/// `-PocketADMScreenshotTab` argument.
enum MainTab: String, Hashable {
    case dashboard, containers, terminal, assistant, more
}

struct MainTabs: View {
    @EnvironmentObject private var app: AppState

    var body: some View {
        TabView(selection: $app.selectedTab) {
            DashboardView()
                .tabItem { Label("Dashboard", systemImage: "gauge.with.dots.needle.33percent") }
                .tag(MainTab.dashboard)

            ContainersView()
                .tabItem { Label("Containers", systemImage: "shippingbox") }
                .tag(MainTab.containers)

            TerminalHomeView()
                .tabItem { Label("Terminal", systemImage: "terminal") }
                .tag(MainTab.terminal)

            ChatView()
                .tabItem { Label("Assistant", systemImage: "sparkles") }
                .tag(MainTab.assistant)

            MoreView()
                .tabItem { Label("More", systemImage: "ellipsis.circle") }
                // One badge for everything behind the hub: without it an alert
                // raised while you are on another tab is invisible.
                .badge(app.unseenAlerts)
                .tag(MainTab.more)
        }
        .tint(Theme.accent)
        .task {
            // Capabilities gate half the UI (the assistant tab, pairing, the
            // exposure warning), so they are fetched once the tabs appear
            // rather than lazily per screen.
            await app.refreshMe()
            await app.refreshAlerts()
        }
    }
}
