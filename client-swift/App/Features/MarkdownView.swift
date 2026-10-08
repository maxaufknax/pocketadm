import SwiftUI
import UIKit

/// Renders the markdown the assistant and the explainers write — headings,
/// lists, checklists, quotes, tables and code blocks as such, not as the raw
/// symbols (Core/Markdown.swift splits the blocks; inline syntax stays with
/// `AttributedString`).
struct MarkdownText: View {
    let text: String
    var font: Font = .subheadline

    var body: some View {
        MarkdownBlocks(blocks: Markdown.parse(text), font: font)
    }

    /// `interpretedSyntax: .inlineOnlyPreservingWhitespace` keeps the line
    /// breaks inside a paragraph; the default collapses them.
    static func attributed(_ raw: String) -> AttributedString {
        (try? AttributedString(
            markdown: raw,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        )) ?? AttributedString(raw)
    }
}

struct MarkdownBlocks: View {
    let blocks: [MarkdownBlock]
    var font: Font = .subheadline

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                MarkdownBlockView(block: block, font: font)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct MarkdownBlockView: View {
    let block: MarkdownBlock
    let font: Font

    var body: some View {
        switch block {
        case .heading(let level, let text):
            Text(MarkdownText.attributed(text))
                .font(headingFont(level))
                .foregroundStyle(Theme.text)
                .textSelection(.enabled)
                .padding(.top, level <= 2 ? 4 : 2)
                .frame(maxWidth: .infinity, alignment: .leading)

        case .paragraph(let text):
            Text(MarkdownText.attributed(text))
                .font(font)
                .foregroundStyle(Theme.text)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)

        case .list(let ordered, let start, let items):
            VStack(alignment: .leading, spacing: 6) {
                ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        marker(ordered: ordered, number: start + index, checked: item.checked)
                        MarkdownBlocks(blocks: item.blocks, font: font)
                    }
                }
            }

        case .quote(let inner):
            HStack(alignment: .top, spacing: 10) {
                RoundedRectangle(cornerRadius: 1.5)
                    .fill(Theme.accent.opacity(0.5))
                    .frame(width: 3)
                MarkdownBlocks(blocks: inner, font: font)
                    .foregroundStyle(Theme.muted)
            }
            .fixedSize(horizontal: false, vertical: true)

        case .code(let language, let text):
            CodeBlockView(language: language, code: text)

        case .table(let table):
            MarkdownTableView(table: table, font: font)

        case .rule:
            Divider().padding(.vertical, 2)
        }
    }

    private func headingFont(_ level: Int) -> Font {
        switch level {
        case 1:  return .title3.weight(.bold)
        case 2:  return .headline
        default: return .subheadline.weight(.semibold)
        }
    }

    @ViewBuilder
    private func marker(ordered: Bool, number: Int, checked: Bool?) -> some View {
        if let checked {
            Image(systemName: checked ? "checkmark.square.fill" : "square")
                .font(font)
                .foregroundStyle(checked ? Theme.accent2 : Theme.muted)
        } else if ordered {
            Text("\(number).")
                .font(font.monospacedDigit())
                .foregroundStyle(Theme.muted)
                .frame(minWidth: 18, alignment: .trailing)
        } else {
            Text("•")
                .font(font)
                .foregroundStyle(Theme.muted)
        }
    }
}

/// A fenced block: dark like a console, the language on top, copy one tap away.
struct CodeBlockView: View {
    let language: String
    let code: String
    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(language.isEmpty ? "code" : language)
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(Theme.termFg.opacity(0.6))
                Spacer()
                Button {
                    UIPasteboard.general.string = code
                    copied = true
                    Task {
                        try? await Task.sleep(for: .seconds(1.5))
                        copied = false
                    }
                } label: {
                    Label(copied ? "Copied" : "Copy", systemImage: copied ? "checkmark" : "doc.on.doc")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(Theme.termFg.opacity(0.75))
                }
                .buttonStyle(.plain)
                .sensoryFeedback(.success, trigger: copied) { _, new in new }
            }
            .padding(.horizontal, 10)
            .padding(.top, 7)
            .padding(.bottom, 4)

            ScrollView(.horizontal, showsIndicators: false) {
                Text(code)
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(Theme.termFg)
                    .textSelection(.enabled)
                    .padding(.horizontal, 10)
                    .padding(.bottom, 10)
            }
        }
        .background(Theme.termBg, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}

/// A table: header row set off, rows striped, each column as wide as its
/// longest cell (within reason) and the whole table scrolling sideways when it
/// is wider than the screen — the way a table reads in a good markdown viewer.
///
/// Fixed column widths rather than a Grid: inside a horizontal scroll view
/// nothing proposes a width, and text without a proposed width never wraps.
struct MarkdownTableView: View {
    let table: MarkdownTable
    var font: Font = .subheadline

    private static let cellPadding: CGFloat = 10

    var body: some View {
        let widths = columnWidths
        ScrollView(.horizontal, showsIndicators: false) {
            VStack(alignment: .leading, spacing: 0) {
                row(table.header, widths: widths, header: true)
                    .background(Theme.bg3)
                ForEach(Array(table.rows.enumerated()), id: \.offset) { index, cells in
                    Divider()
                    row(cells, widths: widths, header: false)
                        .background(index.isMultiple(of: 2) ? Color.clear : Theme.bg3.opacity(0.45))
                }
            }
            .frame(width: widths.reduce(0, +))
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .strokeBorder(Theme.border, lineWidth: 0.5)
            )
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        }
    }

    private func row(_ cells: [String], widths: [CGFloat], header: Bool) -> some View {
        HStack(alignment: .top, spacing: 0) {
            ForEach(0..<table.columnCount, id: \.self) { column in
                let alignment = column < table.alignments.count ? table.alignments[column] : .leading
                Text(MarkdownText.attributed(column < cells.count ? cells[column] : ""))
                    .font(header ? font.weight(.semibold) : font)
                    .foregroundStyle(Theme.text)
                    .multilineTextAlignment(textAlignment(alignment))
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(width: max(20, widths[column] - 2 * Self.cellPadding), alignment: frameAlignment(alignment))
                    .padding(.horizontal, Self.cellPadding)
                    .padding(.vertical, 7)
            }
        }
    }

    /// Roughly the width of each column's longest cell, between 56 and 240 points.
    private var columnWidths: [CGFloat] {
        (0..<table.columnCount).map { column in
            let texts = [table.header[column]] + table.rows.map { column < $0.count ? $0[column] : "" }
            let longest = texts.map(Self.visibleLength).max() ?? 0
            return min(240, max(56, CGFloat(longest) * 7.6 + 2 * Self.cellPadding + 4))
        }
    }

    private static func visibleLength(_ text: String) -> Int {
        var plain = text
        for token in ["**", "__", "`", "~~"] { plain = plain.replacingOccurrences(of: token, with: "") }
        // a link shows its text, not its address
        if let regex = try? NSRegularExpression(pattern: #"\[([^\]]*)\]\([^)]*\)"#) {
            plain = regex.stringByReplacingMatches(in: plain, range: NSRange(plain.startIndex..., in: plain),
                                                   withTemplate: "$1")
        }
        return plain.count
    }

    private func frameAlignment(_ alignment: MarkdownTable.Alignment) -> Alignment {
        switch alignment {
        case .leading:  return .leading
        case .center:   return .center
        case .trailing: return .trailing
        }
    }

    private func textAlignment(_ alignment: MarkdownTable.Alignment) -> TextAlignment {
        switch alignment {
        case .leading:  return .leading
        case .center:   return .center
        case .trailing: return .trailing
        }
    }
}
