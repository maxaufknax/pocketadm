import SwiftUI
import UIKit

/// Colours by role. The default theme ("PocketADM") is the system's own
/// palette, resolved for light and dark mode, so the app sits in iOS like
/// Settings or Files do; the other themes replace page, card and text colours
/// and the accent with a palette of their own, and fix the appearance to the
/// one they were drawn for.
///
/// Every screen reads these tokens instead of naming colours, so a theme is
/// one place. The app rebuilds its screens when the theme changes (see
/// PocketADMApp) — the tokens are read again then.
enum Theme {
    static var bg: Color       { ThemeStore.current.page ?? Color(uiColor: .systemGroupedBackground) }
    static var bg2: Color      { ThemeStore.current.card ?? Color(uiColor: .secondarySystemGroupedBackground) }
    static var bg3: Color      { ThemeStore.current.field ?? Color(uiColor: .tertiarySystemFill) }
    static var border: Color   { ThemeStore.current.separator ?? Color(uiColor: .separator) }
    static var text: Color     { ThemeStore.current.text ?? Color(uiColor: .label) }
    static var muted: Color    { ThemeStore.current.muted ?? Color(uiColor: .secondaryLabel) }
    static var accent: Color   { ThemeStore.current.accent }
    static var accent2: Color  { ThemeStore.current.accent2 ?? Color(uiColor: .systemGreen) }   // success / "safe"
    static var danger: Color   { ThemeStore.current.danger ?? Color(uiColor: .systemRed) }
    static var warn: Color     { ThemeStore.current.warn ?? Color(uiColor: .systemOrange) }
    static var onAccent: Color { ThemeStore.current.onAccent }                                  // text on accent surfaces

    /// Pages that are not grouped lists (the chat, the watch's channel), and
    /// the bubbles on them.
    static var chatBg: Color   { ThemeStore.current.page ?? Color(uiColor: .systemBackground) }
    static var bubble: Color   { ThemeStore.current.card ?? Color(uiColor: .secondarySystemBackground) }

    /// The brand gradient of the app icon and the store images.
    static let brandGradient = LinearGradient(colors: [Color(hex: 0x4FE3E0), Color(hex: 0x1D62D8)],
                                              startPoint: .topLeading, endPoint: .bottomTrailing)

    /// The current theme's own gradient (headers, the theme picker).
    static var gradient: LinearGradient {
        LinearGradient(colors: ThemeStore.current.gradient, startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    /// Consoles stay dark in either appearance, like every terminal app.
    static var termBg: Color { ThemeStore.current.termBg }
    static var termFg: Color { ThemeStore.current.termFg }

    static let radius: CGFloat = 18
}

/// One palette. `nil` colours fall back to the system's (the default theme).
struct AppTheme: Identifiable, Hashable {
    let id: String
    let name: String
    let blurb: String
    /// The appearance the palette was drawn for; nil follows the system (or
    /// the Light / Dark choice).
    let scheme: ColorScheme?
    let accent: Color
    var accent2: Color? = nil
    var warn: Color? = nil
    var danger: Color? = nil
    var page: Color? = nil
    var card: Color? = nil
    var field: Color? = nil
    var text: Color? = nil
    var muted: Color? = nil
    var separator: Color? = nil
    var onAccent: Color = .white
    var gradient: [Color]
    var termBg: Color = Color(hex: 0x0d1117)
    var termFg: Color = Color(hex: 0xe6edf3)

    var isSystem: Bool { page == nil }

    static func == (a: AppTheme, b: AppTheme) -> Bool { a.id == b.id }
    func hash(into hasher: inout Hasher) { hasher.combine(id) }
}

extension AppTheme {
    /// PocketADM's blue, tuned per appearance so white text on it stays readable.
    static let system = AppTheme(
        id: "system", name: "PocketADM", blurb: "The iOS look, light or dark", scheme: nil,
        accent: Color(uiColor: UIColor { traits in
            traits.userInterfaceStyle == .dark
                ? UIColor(red: 0.239, green: 0.576, blue: 1.000, alpha: 1)   // #3D93FF
                : UIColor(red: 0.000, green: 0.412, blue: 0.878, alpha: 1)   // #0069E0
        }),
        gradient: [Color(hex: 0x4FE3E0), Color(hex: 0x1D62D8)])

    static let midnight = AppTheme(
        id: "midnight", name: "Midnight", blurb: "True black for OLED screens", scheme: .dark,
        accent: Color(hex: 0x3D9BFF), accent2: Color(hex: 0x30D158),
        page: Color(hex: 0x000000), card: Color(hex: 0x111114), field: Color(hex: 0x1D1D23),
        text: Color(hex: 0xF2F2F7), muted: Color(hex: 0x8E8E9A), separator: Color(hex: 0x2A2A31),
        gradient: [Color(hex: 0x1B1B24), Color(hex: 0x3D9BFF)],
        termBg: Color(hex: 0x000000), termFg: Color(hex: 0xE6EDF3))

    static let ocean = AppTheme(
        id: "ocean", name: "Ocean", blurb: "Deep blue with a teal glow", scheme: .dark,
        accent: Color(hex: 0x2EC4D6), accent2: Color(hex: 0x3DDC97),
        page: Color(hex: 0x06141F), card: Color(hex: 0x0D2231), field: Color(hex: 0x15324A),
        text: Color(hex: 0xE6F3FF), muted: Color(hex: 0x8FB3CC), separator: Color(hex: 0x1C3A52),
        onAccent: Color(hex: 0x03212A),
        gradient: [Color(hex: 0x0B3A5B), Color(hex: 0x2EC4D6)],
        termBg: Color(hex: 0x041019), termFg: Color(hex: 0xD6F1FF))

    static let aurora = AppTheme(
        id: "aurora", name: "Aurora", blurb: "Violet night, northern lights", scheme: .dark,
        accent: Color(hex: 0x9D7BFF), accent2: Color(hex: 0x34D399),
        page: Color(hex: 0x0B0B1A), card: Color(hex: 0x16152C), field: Color(hex: 0x221F40),
        text: Color(hex: 0xEEEAFE), muted: Color(hex: 0xA39FC9), separator: Color(hex: 0x2A2850),
        gradient: [Color(hex: 0x6D3EF0), Color(hex: 0x22D3A6)],
        termBg: Color(hex: 0x090816), termFg: Color(hex: 0xE4DEFF))

    static let forest = AppTheme(
        id: "forest", name: "Forest", blurb: "Calm greens, easy on the eyes", scheme: .dark,
        accent: Color(hex: 0x3DD68C), accent2: Color(hex: 0xA3E635),
        page: Color(hex: 0x0B1510), card: Color(hex: 0x13231A), field: Color(hex: 0x1C3326),
        text: Color(hex: 0xE7F5EC), muted: Color(hex: 0x93B8A0), separator: Color(hex: 0x21382B),
        onAccent: Color(hex: 0x05210F),
        gradient: [Color(hex: 0x14532D), Color(hex: 0x3DD68C)],
        termBg: Color(hex: 0x07100B), termFg: Color(hex: 0xD9F7E3))

    static let sunset = AppTheme(
        id: "sunset", name: "Sunset", blurb: "Warm plum with coral light", scheme: .dark,
        accent: Color(hex: 0xFF7A59), accent2: Color(hex: 0x4ADE80), warn: Color(hex: 0xFFC14D),
        page: Color(hex: 0x1A0F14), card: Color(hex: 0x27161F), field: Color(hex: 0x36212C),
        text: Color(hex: 0xFFEFF2), muted: Color(hex: 0xC9A3AE), separator: Color(hex: 0x3D2531),
        gradient: [Color(hex: 0x7C2D52), Color(hex: 0xFF9A5A)],
        termBg: Color(hex: 0x14090E), termFg: Color(hex: 0xFFE6EC))

    static let terminal = AppTheme(
        id: "terminal", name: "Terminal", blurb: "Green on black, like the old days", scheme: .dark,
        accent: Color(hex: 0x4ADE80), accent2: Color(hex: 0xA3E635), warn: Color(hex: 0xFACC15),
        danger: Color(hex: 0xF87171),
        page: Color(hex: 0x050805), card: Color(hex: 0x0C140C), field: Color(hex: 0x142114),
        text: Color(hex: 0xC8F7C5), muted: Color(hex: 0x6FA86B), separator: Color(hex: 0x1B2E1B),
        onAccent: Color(hex: 0x031503),
        gradient: [Color(hex: 0x052E16), Color(hex: 0x4ADE80)],
        termBg: Color(hex: 0x020402), termFg: Color(hex: 0x7CF59A))

    static let nord = AppTheme(
        id: "nord", name: "Nord", blurb: "Arctic blue-grey, the Nord palette", scheme: .dark,
        accent: Color(hex: 0x88C0D0), accent2: Color(hex: 0xA3BE8C), warn: Color(hex: 0xEBCB8B),
        danger: Color(hex: 0xBF616A),
        page: Color(hex: 0x2E3440), card: Color(hex: 0x3B4252), field: Color(hex: 0x434C5E),
        text: Color(hex: 0xECEFF4), muted: Color(hex: 0xA7B1C2), separator: Color(hex: 0x4C566A),
        onAccent: Color(hex: 0x2E3440),
        gradient: [Color(hex: 0x5E81AC), Color(hex: 0x88C0D0)],
        termBg: Color(hex: 0x272C36), termFg: Color(hex: 0xD8DEE9))

    static let paper = AppTheme(
        id: "paper", name: "Paper", blurb: "Warm cream, ink and terracotta", scheme: .light,
        accent: Color(hex: 0xC2551F), accent2: Color(hex: 0x3E8E5E), warn: Color(hex: 0xC27C0E),
        danger: Color(hex: 0xC0392B),
        page: Color(hex: 0xF4EFE6), card: Color(hex: 0xFFFCF6), field: Color(hex: 0xEAE3D6),
        text: Color(hex: 0x2B2620), muted: Color(hex: 0x7D7366), separator: Color(hex: 0xDDD4C4),
        gradient: [Color(hex: 0xF3D9B1), Color(hex: 0xC2551F)],
        termBg: Color(hex: 0x2B2620), termFg: Color(hex: 0xF4EFE6))

    static let arctic = AppTheme(
        id: "arctic", name: "Arctic", blurb: "Cool white and glacier blue", scheme: .light,
        accent: Color(hex: 0x0A84C6), accent2: Color(hex: 0x12A37F),
        page: Color(hex: 0xEEF4F8), card: Color(hex: 0xFFFFFF), field: Color(hex: 0xE1EAF1),
        text: Color(hex: 0x0F1C26), muted: Color(hex: 0x5E7383), separator: Color(hex: 0xD3DFE8),
        gradient: [Color(hex: 0xBFE6FF), Color(hex: 0x0A84C6)],
        termBg: Color(hex: 0x0F1C26), termFg: Color(hex: 0xE3F2FD))

    static let rose = AppTheme(
        id: "rose", name: "Rosé", blurb: "Soft pink with raspberry", scheme: .light,
        accent: Color(hex: 0xD6336C), accent2: Color(hex: 0x2F9E44),
        page: Color(hex: 0xFBF1F3), card: Color(hex: 0xFFFFFF), field: Color(hex: 0xF3E1E6),
        text: Color(hex: 0x2A1A20), muted: Color(hex: 0x8A6A75), separator: Color(hex: 0xEED5DC),
        gradient: [Color(hex: 0xFFC2D4), Color(hex: 0xD6336C)],
        termBg: Color(hex: 0x2A1A20), termFg: Color(hex: 0xFDE7EE))

    static let all: [AppTheme] = [.system, .midnight, .ocean, .aurora, .forest, .sunset,
                                  .terminal, .nord, .paper, .arctic, .rose]

    static func named(_ id: String) -> AppTheme {
        all.first { $0.id == id } ?? .system
    }
}

/// The theme in use. Set by the app from the stored choice before any screen
/// draws (PocketADMApp.body), read by the `Theme` tokens.
enum ThemeStore {
    static let key = "pocketadm.theme"
    static var current: AppTheme = AppTheme.named(
        UserDefaults.standard.string(forKey: key) ?? "system")
}

extension Color {
    init(hex: UInt32) {
        self.init(
            .sRGB,
            red:   Double((hex >> 16) & 0xff) / 255,
            green: Double((hex >> 8) & 0xff) / 255,
            blue:  Double(hex & 0xff) / 255,
            opacity: 1
        )
    }
}

/// A card surface for the few screens that are not lists (the dashboard, the
/// pairing sheet): the same fill and corner shape as a grouped list's rows,
/// so cards and lists read as one family.
struct CardBackground: ViewModifier {
    var padding: CGFloat = 16

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(Theme.bg2, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
    }
}

extension View {
    func card(padding: CGFloat = 16) -> some View { modifier(CardBackground(padding: padding)) }
}

/// A grouped list in the current theme: the system's own list for the
/// default theme, the theme's page and card colours otherwise. Every list in
/// the app is one of these, so a theme reaches every screen.
struct ThemedList<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        if ThemeStore.current.isSystem {
            List { content }
        } else {
            List {
                content
                    .listRowBackground(Theme.bg2)
                    .listRowSeparatorTint(Theme.border)
            }
            .scrollContentBackground(.hidden)
            .background(Theme.bg)
        }
    }
}
