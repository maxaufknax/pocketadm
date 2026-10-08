import SwiftUI

@main
struct PocketADMApp: App {
    /// UIKit's side of notifications (the push token arrives there).
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var app = AppState()
    @ObservedObject private var push = PushManager.shared

    /// "system", "light" or "dark" — Settings → Appearance. The system bars,
    /// lists and colours follow it on their own; nothing is painted over them.
    @AppStorage("pocketadm.appearance") private var appearance = "system"

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(app)
                .environmentObject(push)
                .preferredColorScheme(colorScheme)
                .tint(Theme.accent)
                .task {
                    push.app = app
                    await push.refreshAuthorization()
                }
        }
    }

    private var colorScheme: ColorScheme? {
        switch appearance {
        case "light": return .light
        case "dark":  return .dark
        default:      return nil
        }
    }
}
