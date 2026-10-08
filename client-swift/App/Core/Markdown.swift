import Foundation

/// Block-level Markdown, as the assistant and the explainers write it.
///
/// `AttributedString(markdown:)` only knows inline syntax well enough for
/// SwiftUI: a table arrives as a wall of pipes, a heading as "## Heading", a
/// nested list as one run-on paragraph. This splits a reply into blocks —
/// headings, paragraphs, lists (nested, numbered, checklists), quotes, code,
/// tables, rules — and leaves the inline part (bold, code spans, links) to
/// `AttributedString` inside each block.
///
/// It is forgiving on purpose. Replies stream in token by token, so an
/// unclosed code fence is code up to the end and a table with only its header
/// is a table with no rows yet — never an error, never a flicker into prose.
enum MarkdownBlock: Equatable {
    case heading(level: Int, text: String)
    case paragraph(String)
    case list(ordered: Bool, start: Int, items: [MarkdownListItem])
    case quote([MarkdownBlock])
    case code(language: String, text: String)
    case table(MarkdownTable)
    case rule
}

struct MarkdownListItem: Equatable {
    /// nil for an ordinary item, true/false for "- [x]" / "- [ ]".
    var checked: Bool?
    var blocks: [MarkdownBlock]
}

struct MarkdownTable: Equatable {
    enum Alignment: Equatable { case leading, center, trailing }

    var header: [String]
    var alignments: [Alignment]
    var rows: [[String]]

    var columnCount: Int { header.count }
}

enum Markdown {

    static func parse(_ text: String) -> [MarkdownBlock] {
        let lines = text.replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
            .components(separatedBy: "\n")
        return parse(lines: lines[...])
    }

    // MARK: - Blocks

    private static func parse(lines: ArraySlice<String>, depth: Int = 0) -> [MarkdownBlock] {
        var blocks: [MarkdownBlock] = []
        var paragraph: [String] = []
        var i = lines.startIndex

        func flushParagraph() {
            let joined = paragraph.map { $0.trimmingCharacters(in: .whitespaces) }
                .joined(separator: "\n")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if !joined.isEmpty { blocks.append(.paragraph(joined)) }
            paragraph = []
        }

        while i < lines.endIndex {
            let line = lines[i]
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            if trimmed.isEmpty {
                flushParagraph()
                i += 1
                continue
            }

            // ``` or ~~~ fences, up to three spaces in
            if let fence = fenceOpening(line) {
                flushParagraph()
                var body: [String] = []
                var j = i + 1
                while j < lines.endIndex {
                    if isFenceClosing(lines[j], fence: fence.marker) { break }
                    body.append(removeIndent(lines[j], upTo: fence.indent))
                    j += 1
                }
                blocks.append(.code(language: fence.language, text: body.joined(separator: "\n")))
                i = min(j + 1, lines.endIndex)
                continue
            }

            if let heading = atxHeading(line) {
                flushParagraph()
                blocks.append(.heading(level: heading.level, text: heading.text))
                i += 1
                continue
            }

            // "Title\n=====" and "Title\n-----"
            if !paragraph.isEmpty, let level = setextLevel(trimmed) {
                let title = paragraph.map { $0.trimmingCharacters(in: .whitespaces) }.joined(separator: " ")
                paragraph = []
                blocks.append(.heading(level: level, text: title))
                i += 1
                continue
            }

            if isRule(trimmed) {
                flushParagraph()
                blocks.append(.rule)
                i += 1
                continue
            }

            if i + 1 < lines.endIndex, line.contains("|"),
               let alignments = delimiterRow(lines[i + 1]) {
                let header = cells(line)
                if header.count == alignments.count || (alignments.count > 1 && header.count > 1) {
                    flushParagraph()
                    let width = max(header.count, alignments.count)
                    var rows: [[String]] = []
                    var j = i + 2
                    while j < lines.endIndex {
                        let row = lines[j]
                        let rowTrimmed = row.trimmingCharacters(in: .whitespaces)
                        if rowTrimmed.isEmpty || !row.contains("|") { break }
                        rows.append(pad(cells(row), to: width))
                        j += 1
                    }
                    blocks.append(.table(MarkdownTable(header: pad(header, to: width),
                                                       alignments: pad(alignments, to: width, with: .leading),
                                                       rows: rows)))
                    i = j
                    continue
                }
            }

            if quoteContent(line) != nil {
                flushParagraph()
                var inner: [String] = []
                var j = i
                while j < lines.endIndex, let content = quoteContent(lines[j]) {
                    inner.append(content)
                    j += 1
                }
                blocks.append(.quote(depth < 8 ? parse(lines: inner[...], depth: depth + 1)
                                                : [.paragraph(inner.joined(separator: "\n"))]))
                i = j
                continue
            }

            if let marker = listMarker(line) {
                flushParagraph()
                let (block, next) = parseList(lines, from: i, first: marker, depth: depth)
                blocks.append(block)
                i = next
                continue
            }

            paragraph.append(line)
            i += 1
        }
        flushParagraph()
        return blocks
    }

    // MARK: - Lists

    struct ListMarker: Equatable {
        let indent: Int
        let ordered: Bool
        let number: Int
        let bullet: Character
        /// Column where the item's text starts.
        let contentColumn: Int
        let content: String
    }

    static func listMarker(_ line: String) -> ListMarker? {
        let indent = leadingSpaces(line)
        let rest = line.dropFirst(min(indent, line.count))
        guard let first = rest.first else { return nil }
        if "-*+".contains(first) {
            let after = rest.dropFirst()
            guard after.isEmpty || after.first == " " || after.first == "\t" else { return nil }
            // "---" and "* * *" are rules, not items
            if isRule(line.trimmingCharacters(in: .whitespaces)) { return nil }
            let spaces = after.prefix(while: { $0 == " " }).count
            let content = String(after.dropFirst(spaces))
            return ListMarker(indent: indent, ordered: false, number: 0, bullet: first,
                              contentColumn: indent + 1 + max(1, min(spaces, 4)), content: content)
        }
        let digits = rest.prefix(while: { $0.isASCII && $0.isNumber })
        guard !digits.isEmpty, digits.count <= 9 else { return nil }
        let afterDigits = rest.dropFirst(digits.count)
        guard let delimiter = afterDigits.first, delimiter == "." || delimiter == ")" else { return nil }
        let after = afterDigits.dropFirst()
        guard after.isEmpty || after.first == " " else { return nil }
        let spaces = after.prefix(while: { $0 == " " }).count
        return ListMarker(indent: indent, ordered: true, number: Int(digits) ?? 1, bullet: delimiter,
                          contentColumn: indent + digits.count + 1 + max(1, min(spaces, 4)),
                          content: String(after.dropFirst(spaces)))
    }

    private static func parseList(_ lines: ArraySlice<String>, from start: Int, first: ListMarker,
                                  depth: Int) -> (MarkdownBlock, Int) {
        var items: [MarkdownListItem] = []
        var i = start
        while i < lines.endIndex, let marker = listMarker(lines[i]),
              marker.ordered == first.ordered, marker.indent <= first.indent + 1,
              marker.indent + 1 >= first.indent {
            var body: [String] = [marker.content]
            var j = i + 1
            var sawBlank = false
            while j < lines.endIndex {
                let line = lines[j]
                if line.trimmingCharacters(in: .whitespaces).isEmpty {
                    sawBlank = true
                    body.append("")
                    j += 1
                    continue
                }
                let indent = leadingSpaces(line)
                if let next = listMarker(line), next.indent <= marker.indent { break }
                if indent >= marker.contentColumn || (indent > marker.indent && listMarker(line) != nil) {
                    body.append(removeIndent(line, upTo: min(indent, marker.contentColumn)))
                } else if !sawBlank && indent > marker.indent {
                    body.append(line.trimmingCharacters(in: .whitespaces))
                } else if !sawBlank && !startsBlock(line) {
                    // a lazy continuation line of the item's paragraph
                    body.append(line.trimmingCharacters(in: .whitespaces))
                } else {
                    break
                }
                sawBlank = false
                j += 1
            }
            while body.last?.isEmpty == true { body.removeLast() }
            var checked: Bool?
            if let firstLine = body.first {
                let lower = firstLine.lowercased()
                if lower.hasPrefix("[ ] ") || lower == "[ ]" {
                    checked = false
                    body[0] = String(firstLine.dropFirst(min(4, firstLine.count)))
                } else if lower.hasPrefix("[x] ") || lower == "[x]" {
                    checked = true
                    body[0] = String(firstLine.dropFirst(min(4, firstLine.count)))
                }
            }
            let blocks = depth < 8 ? parse(lines: body[...], depth: depth + 1)
                                   : [.paragraph(body.joined(separator: "\n"))]
            items.append(MarkdownListItem(checked: checked, blocks: blocks))
            // a blank line between items keeps the list going
            var k = j
            while k < lines.endIndex, lines[k].trimmingCharacters(in: .whitespaces).isEmpty { k += 1 }
            if k < lines.endIndex, let next = listMarker(lines[k]), next.ordered == first.ordered,
               next.indent <= first.indent + 1, next.indent + 1 >= first.indent {
                i = k
            } else {
                i = j
                break
            }
        }
        return (.list(ordered: first.ordered, start: first.number, items: items), i)
    }

    // MARK: - Line kinds

    private static func startsBlock(_ line: String) -> Bool {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        return fenceOpening(line) != nil || atxHeading(line) != nil || isRule(trimmed)
            || quoteContent(line) != nil || listMarker(line) != nil
    }

    private struct Fence { let marker: String; let indent: Int; let language: String }

    private static func fenceOpening(_ line: String) -> Fence? {
        let indent = leadingSpaces(line)
        guard indent <= 3 else { return nil }
        let rest = String(line.dropFirst(indent))
        for char in ["`", "~"] {
            let run = rest.prefix(while: { String($0) == char })
            if run.count >= 3 {
                let info = rest.dropFirst(run.count).trimmingCharacters(in: .whitespaces)
                if char == "`" && info.contains("`") { return nil }
                let language = info.split(separator: " ").first.map(String.init) ?? ""
                return Fence(marker: String(run), indent: indent, language: language)
            }
        }
        return nil
    }

    private static func isFenceClosing(_ line: String, fence: String) -> Bool {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard let char = fence.first, leadingSpaces(line) <= 3 else { return false }
        let run = trimmed.prefix(while: { $0 == char })
        return run.count >= fence.count && trimmed.count == run.count
    }

    private static func atxHeading(_ line: String) -> (level: Int, text: String)? {
        let indent = leadingSpaces(line)
        guard indent <= 3 else { return nil }
        let rest = line.dropFirst(indent)
        let hashes = rest.prefix(while: { $0 == "#" }).count
        guard (1...6).contains(hashes) else { return nil }
        let after = rest.dropFirst(hashes)
        guard after.isEmpty || after.first == " " || after.first == "\t" else { return nil }
        var text = after.trimmingCharacters(in: .whitespaces)
        // closing hashes: "## Title ##"
        while text.hasSuffix("#") { text.removeLast() }
        return (hashes, text.trimmingCharacters(in: .whitespaces))
    }

    private static func setextLevel(_ trimmed: String) -> Int? {
        guard !trimmed.isEmpty else { return nil }
        if trimmed.allSatisfy({ $0 == "=" }) { return 1 }
        if trimmed.count >= 2, trimmed.allSatisfy({ $0 == "-" }) { return 2 }
        return nil
    }

    static func isRule(_ trimmed: String) -> Bool {
        let compact = trimmed.filter { $0 != " " && $0 != "\t" }
        guard compact.count >= 3, let first = compact.first, "-*_".contains(first) else { return false }
        return compact.allSatisfy { $0 == first }
    }

    private static func quoteContent(_ line: String) -> String? {
        let indent = leadingSpaces(line)
        guard indent <= 3 else { return nil }
        let rest = line.dropFirst(indent)
        guard rest.first == ">" else { return nil }
        let after = rest.dropFirst()
        return String(after.first == " " ? after.dropFirst() : after)
    }

    // MARK: - Tables

    /// "| --- | :---: | ---: |" -> alignments, or nil when the line is not one.
    static func delimiterRow(_ line: String) -> [MarkdownTable.Alignment]? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard trimmed.contains("-"), trimmed.contains("|") || trimmed.hasPrefix(":") || trimmed.hasPrefix("-")
        else { return nil }
        let parts = cells(line)
        guard !parts.isEmpty else { return nil }
        var out: [MarkdownTable.Alignment] = []
        for part in parts {
            let p = part.trimmingCharacters(in: .whitespaces)
            let core = p.trimmingCharacters(in: CharacterSet(charactersIn: ":"))
            guard !core.isEmpty, core.allSatisfy({ $0 == "-" }) else { return nil }
            let left = p.hasPrefix(":"), right = p.hasSuffix(":")
            out.append(left && right ? .center : right ? .trailing : .leading)
        }
        // a lone "---" is a rule or a setext underline, not a table
        if out.count == 1 && !trimmed.contains("|") { return nil }
        return out
    }

    /// The cells of a table row: split on pipes that are neither escaped nor
    /// inside a code span, outer pipes dropped.
    static func cells(_ line: String) -> [String] {
        var trimmed = line.trimmingCharacters(in: .whitespaces)
        if trimmed.hasPrefix("|") { trimmed.removeFirst() }
        if trimmed.hasSuffix("|") && !trimmed.hasSuffix("\\|") { trimmed.removeLast() }
        var out: [String] = []
        var current = ""
        var inCode = false
        var escaped = false
        for char in trimmed {
            if escaped {
                current.append(char == "|" ? "|" : "\\\(char)")
                escaped = false
                continue
            }
            switch char {
            case "\\": escaped = true
            case "`":
                inCode.toggle()
                current.append(char)
            case "|" where !inCode:
                out.append(current.trimmingCharacters(in: .whitespaces))
                current = ""
            default:
                current.append(char)
            }
        }
        if escaped { current.append("\\") }
        out.append(current.trimmingCharacters(in: .whitespaces))
        return out
    }

    private static func pad<T>(_ items: [T], to width: Int, with filler: T) -> [T] {
        if items.count >= width { return Array(items.prefix(width)) }
        return items + Array(repeating: filler, count: width - items.count)
    }

    private static func pad(_ items: [String], to width: Int) -> [String] {
        pad(items, to: width, with: "")
    }

    // MARK: - Helpers

    private static func leadingSpaces(_ line: String) -> Int {
        var count = 0
        for char in line {
            if char == " " { count += 1 } else if char == "\t" { count += 4 } else { break }
        }
        return count
    }

    private static func removeIndent(_ line: String, upTo amount: Int) -> String {
        var removed = 0
        var index = line.startIndex
        while index < line.endIndex, removed < amount {
            let char = line[index]
            if char == " " { removed += 1 } else if char == "\t" { removed += 4 } else { break }
            index = line.index(after: index)
        }
        return String(line[index...])
    }
}
