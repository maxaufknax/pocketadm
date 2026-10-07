import SwiftUI

/// A labelled statistic with a capacity bar, for the few places that show
/// one number on its own card (a container's CPU and memory).
struct MetricTile: View {
    let title: String
    let value: String
    let detail: String
    let fraction: Double
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.footnote.weight(.medium))
                .foregroundStyle(Theme.muted)

            Text(value)
                .font(.system(.title2, design: .rounded).weight(.semibold))
                .foregroundStyle(Theme.text)
                .contentTransition(.numericText())

            ProgressView(value: max(0, min(1, fraction)))
                .tint(tint)

            if !detail.isEmpty {
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
                    .lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card(padding: 14)
    }
}

/// A short tag ("security", "default", "rw"): tinted text on a faint capsule.
struct StatusPill: View {
    let text: String
    let tint: Color

    var body: some View {
        Text(text)
            .font(.caption2.weight(.semibold))
            .foregroundStyle(tint)
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(tint.opacity(0.15), in: Capsule())
    }
}

/// A state ("Running", "Exited"): a coloured dot and the word, the way iOS
/// shows connection states.
struct StatusDot: View {
    let text: String
    let tint: Color

    var body: some View {
        HStack(spacing: 5) {
            Circle().fill(tint).frame(width: 7, height: 7)
            Text(text)
                .font(.footnote)
                .foregroundStyle(Theme.muted)
        }
    }
}

/// Empty and error states, as the system draws them.
struct MessageState: View {
    let symbol: String
    let title: String
    var message: String? = nil
    var tint: Color = Theme.muted
    var retry: (() -> Void)? = nil

    var body: some View {
        ContentUnavailableView {
            Label {
                Text(title)
            } icon: {
                Image(systemName: symbol).foregroundStyle(tint)
            }
        } description: {
            if let message, !message.isEmpty {
                Text(message)
            }
        } actions: {
            if let retry {
                Button("Try again", action: retry)
                    .buttonStyle(.borderedProminent)
                    .buttonBorderShape(.capsule)
            }
        }
    }
}

/// The app's primary button: full width, filled with the accent, a capsule
/// like the system's own prominent buttons.
struct PrimaryButtonStyle: ButtonStyle {
    var enabled: Bool = true

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 15)
            .background(enabled ? Theme.accent : Theme.bg3, in: Capsule())
            .foregroundStyle(enabled ? Theme.onAccent : Theme.muted)
            .opacity(configuration.isPressed ? 0.8 : 1)
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.snappy(duration: 0.15), value: configuration.isPressed)
    }
}

/// The page colour behind screens that are not lists. Navigation bars are
/// left to the system, which gives them the right material in every iOS.
struct ScreenBackground: ViewModifier {
    func body(content: Content) -> some View {
        content.background(Theme.bg.ignoresSafeArea())
    }
}

extension View {
    func screenBackground() -> some View { modifier(ScreenBackground()) }
}
