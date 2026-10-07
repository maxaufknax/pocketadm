import SwiftUI

// Building blocks shared by the screens added beyond the first preview. Each
// one exists because three or more places wanted it; nothing here is a wrapper
// for its own sake.

// MARK: - Severity

extension Severity {
    var tint: Color {
        switch self {
        case .ok:   return Theme.accent2
        case .info: return Theme.accent
        case .warn: return Theme.warn
        case .crit: return Theme.danger
        }
    }

    var label: String {
        switch self {
        case .ok:   return "OK"
        case .info: return "Info"
        case .warn: return "Warning"
        case .crit: return "Critical"
        }
    }
}

// MARK: - Text

/// Renders the markdown the server's AI features produce.
///
/// Fenced code blocks are pulled out and drawn as a console: an AI answer about
/// a server is mostly commands, and `AttributedString`'s inline markdown
/// flattens them into prose you cannot copy cleanly.
struct MarkdownText: View {
    let text: String
    var font: Font = .subheadline

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                if block.isCode {
                    ScrollView(.horizontal, showsIndicators: false) {
                        Text(block.text)
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundStyle(Theme.termFg)
                            .textSelection(.enabled)
                            .padding(10)
                    }
                    .background(Theme.termBg,
                                in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                } else {
                    Text(Self.attributed(block.text))
                        .font(font)
                        .foregroundStyle(Theme.text)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }

    private struct Block {
        let text: String
        let isCode: Bool
    }

    private var blocks: [Block] {
        var result: [Block] = []
        var inCode = false
        var buffer: [String] = []

        func flush() {
            let joined = buffer.joined(separator: "\n")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if !joined.isEmpty { result.append(Block(text: joined, isCode: inCode)) }
            buffer = []
        }

        for line in text.components(separatedBy: .newlines) {
            if line.hasPrefix("```") {
                flush()
                inCode.toggle()
            } else {
                buffer.append(line)
            }
        }
        flush()
        return result
    }

    /// `interpretedSyntax: .inlineOnlyPreservingWhitespace` keeps the line
    /// breaks. The default collapses a bulleted list into one run-on line.
    static func attributed(_ raw: String) -> AttributedString {
        (try? AttributedString(
            markdown: raw,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        )) ?? AttributedString(raw)
    }
}

// MARK: - Rows and headers

/// A section title: the system's section-header look, in a list or above a card.
struct SectionCaption: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.footnote.weight(.semibold))
            .foregroundStyle(Theme.muted)
    }
}

private struct InFactsCardKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    /// Set by `FactsCard`: its rows pad themselves and draw separators, which
    /// a List does on its own.
    var inFactsCard: Bool {
        get { self[InFactsCardKey.self] }
        set { self[InFactsCardKey.self] = newValue }
    }
}

/// Label on the left, value on the right — a list row's "Version  0.23.0".
struct FactRow: View {
    let label: String
    let value: String
    var tint: Color = Theme.muted
    var selectable = false

    @Environment(\.inFactsCard) private var inCard

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label)
                .foregroundStyle(Theme.text)
            Spacer(minLength: 16)
            Group {
                if selectable {
                    Text(value).textSelection(.enabled)
                } else {
                    Text(value)
                }
            }
            .foregroundStyle(tint)
            .multilineTextAlignment(.trailing)
        }
        .font(inCard ? Font.subheadline : Font.body)
        .padding(.horizontal, inCard ? 16 : 0)
        .padding(.vertical, inCard ? 12 : 0)
    }
}

/// A stack of `FactRow`s with inset separators between them, as one card —
/// for screens that are not lists.
struct FactsCard<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        VStack(spacing: 0) {
            content
        }
        .environment(\.inFactsCard, true)
        .card(padding: 0)
    }
}

/// The separator between `FactRow`s in a `FactsCard`. Lists draw their own.
struct HairlineDivider: View {
    @Environment(\.inFactsCard) private var inCard

    var body: some View {
        if inCard {
            Divider().padding(.leading, 16)
        }
    }
}

/// A navigation row the way Settings draws them: an icon tile, the title, an
/// optional line under it, and a count badge.
struct NavRow: View {
    let symbol: String
    let title: String
    var subtitle: String = ""
    var badge: String = ""
    var badgeTint: Color = Theme.danger
    var tint: Color = Theme.accent

    var body: some View {
        HStack(spacing: 14) {
            IconTile(symbol: symbol, color: tint)

            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .foregroundStyle(Theme.text)
                if !subtitle.isEmpty {
                    Text(subtitle)
                        .font(.footnote)
                        .foregroundStyle(Theme.muted)
                        .lineLimit(1)
                }
            }

            Spacer(minLength: 8)

            if !badge.isEmpty {
                Text(badge)
                    .font(.footnote.weight(.semibold))
                    .monospacedDigit()
                    .foregroundStyle(.white)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 2)
                    .frame(minWidth: 22)
                    .background(badgeTint, in: Capsule())
            }
        }
    }
}

// MARK: - Console

/// Monospaced output that scrolls both ways and sticks to the bottom while new
/// lines arrive. Log text must not wrap: a wrapped `docker pull` progress line
/// is unreadable.
struct LogConsole: View {
    let lines: [String]
    /// nil lets the console fill whatever space it is given — `.frame(height:)`
    /// takes an optional, and passing `.infinity` there collapses the view.
    var height: CGFloat? = 280
    var follow = true

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView([.horizontal, .vertical]) {
                VStack(alignment: .leading, spacing: 1) {
                    ForEach(Array(lines.enumerated()), id: \.offset) { index, line in
                        Text(line)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(Theme.termFg)
                            .textSelection(.enabled)
                            .id(index)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(10)
            }
            .frame(height: height)
            .background(Theme.termBg, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .onChange(of: lines.count) { _, count in
                guard follow, count > 0 else { return }
                withAnimation(.easeOut(duration: 0.15)) {
                    proxy.scrollTo(count - 1, anchor: .bottom)
                }
            }
        }
    }
}

// MARK: - Controls

/// Secondary action button: a grey capsule that does not compete with the
/// accent-filled primary.
struct SecondaryButtonStyle: ButtonStyle {
    var tint: Color = Theme.text

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.body.weight(.medium))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 13)
            .background(Theme.bg3, in: Capsule())
            .foregroundStyle(tint)
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}

/// A text field outside a list, filled like a search field.
struct FieldBox<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        content
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(Theme.bg3, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .foregroundStyle(Theme.text)
    }
}

/// Full-width banner for the one warning that matters on a root gateway.
struct WarningBanner: View {
    let title: String
    let message: String
    var tint: Color = Theme.warn
    var actionTitle: String? = nil
    var action: (() -> Void)? = nil

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.title3)
                .foregroundStyle(tint)
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.text)
                Text(message)
                    .font(.footnote)
                    .foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
                if let actionTitle, let action {
                    Button(actionTitle, action: action)
                        .font(.footnote.weight(.semibold))
                        .tint(tint)
                        .padding(.top, 2)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(14)
        .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
    }
}

// MARK: - Feedback

/// Short-lived confirmation ("Applied", "Copied") — a full alert for something
/// that worked is noise.
struct Toast: Equatable, Identifiable {
    let id = UUID()
    let text: String
    var isError = false
}

struct ToastOverlay: ViewModifier {
    @Binding var toast: Toast?

    func body(content: Content) -> some View {
        content.overlay(alignment: .bottom) {
            if let toast {
                Label(toast.text, systemImage: toast.isError ? "exclamationmark.circle.fill" : "checkmark.circle.fill")
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(toast.isError ? Theme.danger : Theme.text)
                    .padding(.horizontal, 18)
                    .padding(.vertical, 12)
                    .background(.regularMaterial, in: Capsule())
                    .shadow(color: .black.opacity(0.15), radius: 12, y: 4)
                    .padding(.bottom, 24)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                    .task(id: toast.id) {
                        try? await Task.sleep(for: .seconds(2.5))
                        withAnimation { self.toast = nil }
                    }
            }
        }
        .animation(.snappy, value: toast)
        .sensoryFeedback(trigger: toast) { _, new in
            guard let new else { return nil }
            return new.isError ? .error : .success
        }
    }
}

extension View {
    func toast(_ toast: Binding<Toast?>) -> some View { modifier(ToastOverlay(toast: toast)) }
}

// MARK: - Formatting helpers used across screens

extension Fmt {
    /// Timestamps in this app are always "how long ago", never a wall clock —
    /// the useful question about a log line or a check is its age.
    static func ago(_ date: Date) -> String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .abbreviated
        return formatter.localizedString(for: date, relativeTo: Date())
    }

    static func money(_ value: Double) -> String {
        value >= 0.01 ? String(format: "$%.2f", value) : String(format: "$%.4f", value)
    }

    static func count(_ value: Int) -> String {
        value >= 1_000_000 ? String(format: "%.1fM", Double(value) / 1_000_000)
            : value >= 1_000 ? String(format: "%.1fk", Double(value) / 1_000)
            : String(value)
    }
}
