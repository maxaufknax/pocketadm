import SwiftUI

@main
struct PocketADMApp: App {
    @StateObject private var app = AppState()

    /// "system", "light" or "dark" — Settings → Appearance. The system bars,
    /// lists and colours follow it on their own; nothing is painted over them.
    @AppStorage("pocketadm.appearance") private var appearance = "system"

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(app)
                .preferredColorScheme(colorScheme)
                .tint(Theme.accent)
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
