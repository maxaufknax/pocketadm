import Foundation

// What the assistant's transcript shows, as opposed to what happened.
//
// A coding agent at work runs command after command — twenty `docker logs`,
// `grep` and `cat` calls are normal for one question. Shown one card each,
// they bury the conversation. Consecutive tool calls (and the reasoning
// between them) fold into one group that says what was done ("Ran 5 commands
// · read 2 files") and opens on a tap. A single call stays a card of its own;
// a call that waits for approval is never folded away.
//
// Pure translation over ChatItem, so it is type-checked and tested on Linux.

struct ToolGroup: Identifiable, Hashable {
    /// The first item's id: stable while the group grows at its end.
    let id: String
    let items: [ChatItem]

    var calls: [ToolCall] { items.compactMap(\.tool) }

    var isRunning: Bool { calls.contains { $0.state == .running || $0.state == .requested } }
    var isWaiting: Bool { calls.contains { $0.state == .requested } }
    var deniedCount: Int { calls.filter { $0.state == .denied }.count }

    /// The call in progress, else the last one — what the collapsed header shows.
    var current: ToolCall? {
        calls.last { $0.state == .running || $0.state == .requested } ?? calls.last
    }

    /// "Ran 5 commands · read 2 files · changed 1 file"
    var summary: String {
        var counts: [String: Int] = [:]
        for call in calls { counts[ToolGroup.kind(of: call.name), default: 0] += 1 }
        let order = ["command", "read", "change", "search", "fetch", "other"]
        let parts = order.compactMap { kind -> String? in
            guard let n = counts[kind], n > 0 else { return nil }
            switch kind {
            case "command": return n == 1 ? "Ran 1 command" : "Ran \(n) commands"
            case "read":    return n == 1 ? "read 1 file" : "read \(n) files"
            case "change":  return n == 1 ? "changed 1 file" : "changed \(n) files"
            case "search":  return n == 1 ? "1 search" : "\(n) searches"
            case "fetch":   return n == 1 ? "fetched 1 page" : "fetched \(n) pages"
            default:        return n == 1 ? "1 other step" : "\(n) other steps"
            }
        }
        guard let first = parts.first else { return "No steps" }
        let head = first.prefix(1).uppercased() + first.dropFirst()
        return ([head] + parts.dropFirst()).joined(separator: " · ")
    }

    static func kind(of tool: String) -> String {
        switch tool {
        case "run_command":                      return "command"
        case "read_file", "read_skill":          return "read"
        case "write_file", "edit_file":          return "change"
        case "list_dir", "search_files":         return "search"
        case "fetch_url":                        return "fetch"
        default:                                 return "other"
        }
    }
}

enum TimelineRow: Identifiable, Hashable {
    case item(ChatItem)
    case tools(ToolGroup)

    var id: String {
        switch self {
        case .item(let item):   return item.id
        case .tools(let group): return "group-" + group.id
        }
    }
}

enum ChatTimeline {
    /// Folds runs of at least `minimum` tool calls into groups. Reasoning rows
    /// inside a run belong to the run; a run ends at the first row that is
    /// neither a tool call nor reasoning.
    static func rows(_ items: [ChatItem], minimum: Int = 2) -> [TimelineRow] {
        var out: [TimelineRow] = []
        var run: [ChatItem] = []

        func flush() {
            let tools = run.filter { $0.kind == .tool }
            let waiting = tools.contains { $0.tool?.state == .requested }
            if tools.count >= minimum && !waiting {
                out.append(.tools(ToolGroup(id: run[0].id, items: run)))
            } else {
                out.append(contentsOf: run.map { TimelineRow.item($0) })
            }
            run = []
        }

        /// Reasoning after the last call of a run is the start of the answer
        /// that follows, not part of the work: it stays outside the group.
        func closeRun() {
            var tail: [ChatItem] = []
            while let last = run.last, last.kind == .thinking {
                tail.insert(run.removeLast(), at: 0)
            }
            if !run.isEmpty { flush() }
            out.append(contentsOf: tail.map { TimelineRow.item($0) })
        }

        for item in items {
            switch item.kind {
            case .tool:
                run.append(item)
            case .thinking where !run.isEmpty:
                run.append(item)
            default:
                if !run.isEmpty { closeRun() }
                out.append(.item(item))
            }
        }
        if !run.isEmpty { closeRun() }
        return out
    }
}

/// The plan's progress in words, for the bar above the conversation.
struct PlanProgress: Equatable {
    let done: Int
    let total: Int
    let current: String

    init(_ steps: [PlanStep]) {
        done = steps.filter(\.done).count
        total = steps.count
        current = steps.first(where: \.active)?.title
            ?? steps.first(where: { !$0.done })?.title
            ?? ""
    }

    var isFinished: Bool { total > 0 && done == total }
    var fraction: Double { total == 0 ? 0 : Double(done) / Double(total) }
}
