import SwiftUI

/// A labelled statistic with a filled progress track — the dashboard's main
/// unit. The bar is what makes "72%" legible at a glance.
struct MetricTile: View {
    let title: String
    let value: String
    let detail: String
    let fraction: Double
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title.uppercased())
                .font(.caption2.weight(.semibold))
                .tracking(0.6)
                .foregroundStyle(Theme.muted)

            Text(value)
                .font(.system(.title2, design: .rounded).weight(.semibold))
                .foregroundStyle(Theme.text)
                .contentTransition(.numericText())

            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Theme.bg3)
                    Capsule()
                        .fill(tint)
                        .frame(width: max(0, min(1, fraction)) * geo.size.width)
                }
            }
            .frame(height: 5)

            Text(detail)
                .font(.caption)
                .foregroundStyle(Theme.muted)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }
}

/// Pill used for container state. Colour carries the meaning, so the text
/// stays short.
struct StatusPill: View {
    let text: String
    let tint: Color

    var body: some View {
        Text(text)
            .font(.caption2.weight(.semibold))
            .foregroundStyle(tint)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(tint.opacity(0.14), in: Capsule())
    }
}

/// One consistent empty/error state instead of each screen inventing its own.
struct MessageState: View {
    let symbol: String
    let title: String
    var message: String? = nil
    var tint: Color = Theme.muted
    var retry: (() -> Void)? = nil

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 34))
                .foregroundStyle(tint)
            Text(title)
                .font(.headline)
                .foregroundStyle(Theme.text)
                .multilineTextAlignment(.center)
            if let message {
                Text(message)
                    .font(.subheadline)
                    .foregroundStyle(Theme.muted)
                    .multilineTextAlignment(.center)
            }
            if let retry {
                Button("Try again", action: retry)
                    .buttonStyle(.borderedProminent)
                    .tint(Theme.accent)
                    .foregroundStyle(Theme.onAccent)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(28)
    }
}

/// The app's primary button. `.borderedProminent` alone would tint the label
/// white, which is unreadable on the light-blue accent.
struct PrimaryButtonStyle: ButtonStyle {
    var enabled: Bool = true

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(enabled ? Theme.accent : Theme.bg3,
                        in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .foregroundStyle(enabled ? Theme.onAccent : Theme.muted)
            .opacity(configuration.isPressed ? 0.85 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

/// Paints the palette's page colour edge to edge and keeps the status-bar text
/// light. Applied once per screen root.
struct ScreenBackground: ViewModifier {
    func body(content: Content) -> some View {
        content
            .background(Theme.bg.ignoresSafeArea())
            .toolbarBackground(Theme.bg2, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
    }
}

extension View {
    func screenBackground() -> some View { modifier(ScreenBackground()) }
}
