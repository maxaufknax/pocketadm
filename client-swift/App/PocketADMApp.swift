import SwiftUI
import UIKit

@main
struct PocketADMApp: App {
    @StateObject private var app = AppState()

    init() {
        Self.applyAppearance()
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(app)
                // The palette is a dark one by design; letting the system flip
                // it to light would render the whole design system unreadable.
                .preferredColorScheme(.dark)
                .tint(Theme.accent)
        }
    }

    /// SwiftUI cannot fully colour UIKit-backed chrome (the tab bar's blur, the
    /// grouped list background), so those are set through the appearance proxies
    /// once at launch. Without this the bars keep the system's grey and the app
    /// looks like two different products stitched together.
    private static func applyAppearance() {
        let bg2 = UIColor(Theme.bg2)

        let tabBar = UITabBarAppearance()
        tabBar.configureWithOpaqueBackground()
        tabBar.backgroundColor = bg2
        UITabBar.appearance().standardAppearance = tabBar
        UITabBar.appearance().scrollEdgeAppearance = tabBar

        let navBar = UINavigationBarAppearance()
        navBar.configureWithOpaqueBackground()
        navBar.backgroundColor = bg2
        navBar.titleTextAttributes = [.foregroundColor: UIColor(Theme.text)]
        navBar.largeTitleTextAttributes = [.foregroundColor: UIColor(Theme.text)]
        UINavigationBar.appearance().standardAppearance = navBar
        UINavigationBar.appearance().scrollEdgeAppearance = navBar
        UINavigationBar.appearance().compactAppearance = navBar

        UITableView.appearance().backgroundColor = UIColor(Theme.bg)
    }
}
