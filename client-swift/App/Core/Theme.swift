import SwiftUI
import UIKit

/// Colours by role, resolved by the system for light and dark mode, so the app
/// sits in iOS like Settings or Files do instead of painting its own world.
/// Only the accent is PocketADM's own: its blue, tuned per appearance so white
/// text on it stays readable.
enum Theme {
    static let bg      = Color(uiColor: .systemGroupedBackground)           // page
    static let bg2     = Color(uiColor: .secondarySystemGroupedBackground)  // cards, rows
    static let bg3     = Color(uiColor: .tertiarySystemFill)                // inputs, tracks
    static let border  = Color(uiColor: .separator)
    static let text    = Color(uiColor: .label)
    static let muted   = Color(uiColor: .secondaryLabel)
    static let accent  = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.239, green: 0.576, blue: 1.000, alpha: 1)   // #3D93FF
            : UIColor(red: 0.000, green: 0.412, blue: 0.878, alpha: 1)   // #0069E0
    })
    static let accent2 = Color(uiColor: .systemGreen)    // success / "safe"
    static let danger  = Color(uiColor: .systemRed)
    static let warn    = Color(uiColor: .systemOrange)
    static let onAccent = Color.white                    // text on accent surfaces

    /// The brand gradient of the app icon and the store images.
    static let brandGradient = LinearGradient(colors: [Color(hex: 0x4FE3E0), Color(hex: 0x1D62D8)],
                                              startPoint: .topLeading, endPoint: .bottomTrailing)

    /// Consoles stay dark in either appearance, like every terminal app.
    static let termBg  = Color(hex: 0x0d1117)
    static let termFg  = Color(hex: 0xe6edf3)

    static let radius: CGFloat = 18
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
