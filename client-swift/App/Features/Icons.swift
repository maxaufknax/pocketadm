import SwiftUI

// One icon language for the whole app: a white glyph on a coloured rounded
// square, the shape iOS Settings uses. Services get their brand mark when
// PocketADM knows the brand (Brands.swift, shared with the web client) and a
// symbol for their category otherwise — never an emoji, which renders in a
// different style on every row and cannot be tinted.

/// A white SF Symbol on a coloured rounded square.
struct IconTile: View {
    let symbol: String
    var color: Color = Theme.accent
    var size: CGFloat = 30

    var body: some View {
        Image(systemName: symbol)
            .font(.system(size: size * 0.5, weight: .semibold))
            .symbolRenderingMode(.monochrome)
            .foregroundStyle(.white)
            .frame(width: size, height: size)
            .background(color.gradient, in: RoundedRectangle(cornerRadius: size * 0.27, style: .continuous))
            .accessibilityHidden(true)
    }
}

/// The icon of a service, container, image or app.
///
/// `names` are tried in order — catalog label, container name, image — the
/// same inputs and the same matching as the web client's tiles.
struct ServiceIcon: View {
    var names: [String]
    var category: String = ""
    var size: CGFloat = 34

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: size * 0.27, style: .continuous)
        if names.contains(where: Self.isPocketADM) {
            Image("pocketadm-mark")
                .resizable()
                .scaledToFill()
                .frame(width: size, height: size)
                .clipShape(shape)
                .accessibilityHidden(true)
        } else if let slug = Brands.slug(for: names), let hex = Brands.colors[slug] {
            Image("brand-\(slug)")
                .renderingMode(.template)
                .resizable()
                .scaledToFit()
                .foregroundStyle(.white)
                .padding(size * 0.21)
                .frame(width: size, height: size)
                .background(BrandTile.gradient(hex), in: shape)
                .accessibilityHidden(true)
        } else {
            let style = ServiceCategory.style(for: category, seed: names.first ?? "")
            IconTile(symbol: style.symbol, color: style.color, size: size)
        }
    }

    static func isPocketADM(_ name: String) -> Bool {
        let lower = name.lowercased()
        return lower.contains("pocketadm") || lower.contains("helmsman")
    }
}

/// Brand colours as tile backgrounds, by the web client's rules: very dark
/// marks are lifted so the tile reads in dark mode, very light ones dimmed so
/// the white glyph keeps its contrast, the rest get a soft sheen.
enum BrandTile {
    static func gradient(_ hex: UInt32) -> LinearGradient {
        let base = rgb(hex)
        let luminance = 0.2126 * base.r + 0.7152 * base.g + 0.0722 * base.b
        let colors: [Color]
        if luminance < 0.16 {
            colors = [color(base), color(mix(base, rgb(0x6b7684), 0.62))]
        } else if luminance > 0.82 {
            colors = [color(mix(base, rgb(0x3b4552), 0.80)), color(mix(base, rgb(0x232b36), 0.55))]
        } else {
            colors = [color(mix(base, (1, 1, 1), 0.88)), color(base)]
        }
        return LinearGradient(colors: colors, startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    private typealias RGB = (r: Double, g: Double, b: Double)

    private static func rgb(_ hex: UInt32) -> RGB {
        (Double((hex >> 16) & 0xff) / 255, Double((hex >> 8) & 0xff) / 255, Double(hex & 0xff) / 255)
    }

    /// `share` of `a`, the rest `b` — CSS color-mix in sRGB.
    private static func mix(_ a: RGB, _ b: RGB, _ share: Double) -> RGB {
        (a.r * share + b.r * (1 - share), a.g * share + b.g * (1 - share), a.b * share + b.b * (1 - share))
    }

    private static func color(_ c: RGB) -> Color {
        Color(.sRGB, red: c.r, green: c.g, blue: c.b, opacity: 1)
    }
}

/// A symbol and colour per catalog category, for services without a brand mark.
enum ServiceCategory {
    struct Style {
        let symbol: String
        let color: Color
    }

    static func style(for category: String, seed: String) -> Style {
        switch category.lowercased() {
        case "database":             return Style(symbol: "cylinder.split.1x2.fill", color: .blue)
        case "cache":                return Style(symbol: "bolt.fill", color: .orange)
        case "monitoring":           return Style(symbol: "waveform.path.ecg", color: .orange)
        case "analytics":            return Style(symbol: "chart.bar.fill", color: .blue)
        case "media":                return Style(symbol: "play.rectangle.fill", color: .purple)
        case "photos":               return Style(symbol: "photo.on.rectangle.angled", color: .pink)
        case "files & sync":         return Style(symbol: "folder.fill", color: .cyan)
        case "backup":               return Style(symbol: "externaldrive.fill", color: .green)
        case "downloads":            return Style(symbol: "arrow.down.circle.fill", color: .green)
        case "productivity":         return Style(symbol: "checklist", color: .indigo)
        case "documentation":        return Style(symbol: "book.fill", color: .brown)
        case "development":          return Style(symbol: "chevron.left.forwardslash.chevron.right", color: .teal)
        case "automation":           return Style(symbol: "gearshape.2.fill", color: .orange)
        case "smart home":           return Style(symbol: "house.fill", color: .orange)
        case "network":              return Style(symbol: "network", color: .blue)
        case "dns / adblock":        return Style(symbol: "shield.lefthalf.filled", color: .red)
        case "security":             return Style(symbol: "lock.fill", color: .red)
        case "passwords":            return Style(symbol: "key.fill", color: .gray)
        case "notifications":        return Style(symbol: "bell.badge.fill", color: .red)
        case "finance":              return Style(symbol: "banknote.fill", color: .green)
        case "search":               return Style(symbol: "magnifyingglass", color: .gray)
        case "ai":                   return Style(symbol: "sparkles", color: .purple)
        case "management":           return Style(symbol: "slider.horizontal.3", color: .indigo)
        case "utilities":            return Style(symbol: "wrench.and.screwdriver.fill", color: .gray)
        default:
            // Unknown and uncategorised: a box, in a colour that stays the
            // same for the same name so a list does not look random.
            let palette: [Color] = [.blue, .indigo, .teal, .orange, .purple, .pink, .green]
            let hash = seed.unicodeScalars.reduce(UInt32(0)) { ($0 &* 31) &+ $1.value }
            return Style(symbol: "shippingbox.fill", color: palette[Int(hash % UInt32(palette.count))])
        }
    }
}

/// The few icon names the server sends from the web client's icon set
/// ("box" for the app's own shell), as SF Symbols.
enum ServerSymbol {
    static func sfSymbol(for name: String) -> String? {
        switch name {
        case "box", "package": return "shippingbox.fill"
        case "zap":            return "bolt.fill"
        case "server":         return "server.rack"
        case "terminal":       return "terminal.fill"
        case "database":       return "cylinder.split.1x2.fill"
        case "globe":          return "globe"
        default:               return nil
        }
    }
}
