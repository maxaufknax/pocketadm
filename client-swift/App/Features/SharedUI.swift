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

struct SectionCaption: View {
    let text: String

    var body: some View {
        Text(text.uppercased())
            .font(.caption2.weight(.semibold))
            .tracking(0.6)
            .foregroundStyle(Theme.muted)
    }
}

/// Label on the left, value on the right — the shape every facts card uses.
struct FactRow: View {
    let label: String
    let value: String
    var tint: Color = Theme.text
    var selectable = false

    var body: some View {
        HStack(alignment: .top) {
            Text(label)
                .font(.subheadline)
                .foregroundStyle(Theme.muted)
            Spacer(minLength: 16)
            Group {
                if selectable {
                    Text(value).textSelection(.enabled)
                } else {
                    Text(value)
                }
            }
            .font(.subheadline)
            .foregroundStyle(tint)
            .multilineTextAlignment(.trailing)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 11)
    }
}

/// A stack of `FactRow`s with hairlines between them, as one card.
struct FactsCard<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        VStack(spacing: 0) {
            content
        }
        .card(padding: 0)
    }
}

struct HairlineDivider: View {
    var body: some View { Divider().overlay(Theme.border) }
}

/// Navigation row used by the More hub and the settings screens.
struct NavRow: View {
    let symbol: String
    let title: String
    var subtitle: String = ""
    var badge: String = ""
    var badgeTint: Color = Theme.accent

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 15))
                .foregroundStyle(Theme.accent)
                .frame(width: 26)

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(Theme.text)
                if !subtitle.isEmpty {
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                        .lineLimit(1)
                }
            }

            Spacer()

            if !badge.isEmpty {
                Text(badge)
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(Theme.onAccent)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(badgeTint, in: Capsule())
            }
        }
        .padding(.vertical, 3)
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

/// Secondary action button: readable on the dark palette without competing with
/// the accent-filled primary.
struct SecondaryButtonStyle: ButtonStyle {
    var tint: Color = Theme.text

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.medium))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 11)
            .background(Theme.bg3, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .foregroundStyle(tint)
            .opacity(configuration.isPressed ? 0.75 : 1)
    }
}

/// A text field that matches the palette. SwiftUI's `.textFieldStyle(.roundedBorder)`
/// paints a light chrome that is invisible against the dark background.
struct FieldBox<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        content
            .padding(12)
            .background(Theme.bg3, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
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
        VStack(alignment: .leading, spacing: 8) {
            Label(title, systemImage: "exclamationmark.triangle.fill")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(tint)
            Text(message)
                .font(.caption)
                .foregroundStyle(Theme.muted)
                .fixedSize(horizontal: false, vertical: true)
            if let actionTitle, let action {
                Button(actionTitle, action: action)
                    .font(.caption.weight(.semibold))
                    .tint(tint)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(tint.opacity(0.10),
                    in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
                .stroke(tint.opacity(0.35), lineWidth: 1)
        )
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
                Text(toast.text)
                    .font(.footnote.weight(.medium))
                    .foregroundStyle(toast.isError ? Theme.danger : Theme.text)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                    .background(Theme.bg3, in: Capsule())
                    .overlay(Capsule().stroke(Theme.border, lineWidth: 1))
                    .padding(.bottom, 24)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                    .task(id: toast.id) {
                        try? await Task.sleep(for: .seconds(2.5))
                        withAnimation { self.toast = nil }
                    }
            }
        }
        .animation(.easeOut(duration: 0.2), value: toast)
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
