import SwiftUI

/// The "Deep Sea" palette from web/style.css, carried over verbatim so the
/// native app is recognisably the same product as the PWA. The web build lets
/// you switch themes per device; this preview commits to the default one.
enum Theme {
    static let bg      = Color(hex: 0x0b0f14)   // page
    static let bg2     = Color(hex: 0x121821)   // cards, bars
    static let bg3     = Color(hex: 0x1a2230)   // inputs, nested surfaces
    static let border  = Color(hex: 0x243044)
    static let text    = Color(hex: 0xe6edf3)
    static let muted   = Color(hex: 0x8b98a9)
    static let accent  = Color(hex: 0x4da3ff)
    static let accent2 = Color(hex: 0x7ee0b8)   // success / "safe"
    static let danger  = Color(hex: 0xff6b6b)
    static let warn    = Color(hex: 0xffc24d)
    static let onAccent = Color(hex: 0x04121f)  // text on accent surfaces

    /// Console surfaces stay dark in every web theme, so they are their own tokens.
    static let termBg  = Color(hex: 0x0d1117)
    static let termFg  = Color(hex: 0xe6edf3)

    static let radius: CGFloat = 14
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

/// A card surface. Used everywhere instead of SwiftUI's default grouped
/// background, which would paint the system's grey and break the palette.
struct CardBackground: ViewModifier {
    var padding: CGFloat = 14

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(Theme.bg2)
            .clipShape(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
                    .stroke(Theme.border, lineWidth: 1)
            )
    }
}

extension View {
    func card(padding: CGFloat = 14) -> some View { modifier(CardBackground(padding: padding)) }
}
