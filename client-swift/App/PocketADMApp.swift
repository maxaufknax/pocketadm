import SwiftUI

@main
struct PocketADMApp: App {
    /// UIKit's side of notifications (the push token arrives there).
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var app = AppState()
    @ObservedObject private var push = PushManager.shared

    /// "system", "light" or "dark" — More → Appearance. Applies to the default
    /// theme; the other themes bring the appearance they were drawn for.
    @AppStorage("pocketadm.appearance") private var appearance = "system"
    /// The palette (Theme.swift): "system" is the iOS look.
    @AppStorage(ThemeStore.key) private var themeID = "system"

    var body: some Scene {
        WindowGroup {
            // The theme's colours are read while views are built, so a new
            // theme rebuilds the screens (the app state, signed-in server and
            // open chats live above this and stay).
            let theme = Self.apply(themeID)
            RootView()
                .id(theme.id)
                .environmentObject(app)
                .environmentObject(push)
                .preferredColorScheme(theme.scheme ?? colorScheme)
                .tint(Theme.accent)
                .task {
                    push.app = app
                    await push.refreshAuthorization()
                }
        }
    }

    private static func apply(_ id: String) -> AppTheme {
        let theme = AppTheme.named(id)
        ThemeStore.current = theme
        return theme
    }

    private var colorScheme: ColorScheme? {
        switch appearance {
        case "light": return .light
        case "dark":  return .dark
        default:      return nil
        }
    }
}
