import Foundation

// The wire protocol of `/ws/chat`, kept free of SwiftUI so it can be
// type-checked (and eyeballed) without a Mac. `ChatSocket` owns the connection;
// everything here is pure translation.
//
// Shape of the conversation, from server/sessions.py:
//
//   client → server   {"type":"open","id":"<chat>"} | {"type":"reset"}
//                     {"type":"user","text":…,"context":…}
//                     {"type":"config","mode":…,"provider":…,"model":…,"workdir":…}
//                     {"type":"approve","id":"<tool call>","approved":true}
//                     {"type":"continue"} | {"type":"stop"}
//                     {"type":"rewind","ordinal":N,"text":…}
//                     {"type":"away"} | {"type":"back"}      (0.25: presence)
//
//   server → client   one JSON object per frame, discriminated by "type".
//
// The session lives on the *server*: it keeps running when the app is
// backgrounded and re-attaching replays a snapshot. That is why there is no
// local persistence here — the server is the source of truth.

// MARK: - Configuration

/// How much rope the agent gets. The server enforces this; the picker only
/// chooses.
enum ChatMode: String, CaseIterable, Identifiable, Hashable {
    /// No tools at all — a plain conversation about the box.
    case chat
    /// Read-only tools, approved automatically; anything that writes is refused.
    case plan
    /// Full tool set, but every mutating call asks first.
    case agent
    /// Full tool set with no prompts. Powerful and genuinely dangerous.
    case auto

    var id: String { rawValue }

    var title: String {
        switch self {
        case .chat:  return "Chat"
        case .plan:  return "Plan"
        case .agent: return "Agent"
        case .auto:  return "Auto"
        }
    }

    var blurb: String {
        switch self {
        case .chat:  return "Just talk. No commands are run."
        case .plan:  return "Can look around — read files, list directories, run read-only commands."
        case .agent: return "Can change things, but asks before every action that writes."
        case .auto:  return "Runs everything without asking. Only on a server you can restore."
        }
    }

    var symbol: String {
        switch self {
        case .chat:  return "bubble.left.and.bubble.right"
        case .plan:  return "map"
        case .agent: return "hand.raised"
        case .auto:  return "bolt.fill"
        }
    }

    /// Auto mode can delete things unattended; the UI marks it accordingly.
    var isDangerous: Bool { self == .auto }
}

struct ChatConfig: Hashable {
    var mode: ChatMode = .agent
    var provider: String = ""
    var model: String = ""
    var workdir: String = ""
    var thinking: Bool = false

    init() {}

    init(json: [String: Any]) {
        mode = ChatMode(rawValue: json["mode"] as? String ?? "") ?? .agent
        provider = json["provider"] as? String ?? ""
        model = json["model"] as? String ?? ""
        workdir = json["workdir"] as? String ?? ""
        thinking = json["thinking"] as? Bool ?? false
    }
}

// MARK: - Transcript items

struct PlanStep: Identifiable, Hashable {
    let title: String
    /// pending / in_progress / done
    let status: String
    var id: String { title }

    var done: Bool { status == "done" }
    var active: Bool { status == "in_progress" }
}

struct PauseInfo: Hashable {
    let title: String
    let why: String
    let task: String
    let last: String
    let steps: Int

    init(json: [String: Any]) {
        title = json["title"] as? String ?? "Agent paused"
        why = json["why"] as? String ?? ""
        task = json["task"] as? String ?? ""
        last = json["last"] as? String ?? ""
        steps = json["steps"] as? Int ?? 0
    }
}

struct ToolCall: Hashable {
    enum State: Hashable {
        /// Waiting for the user to approve or decline.
        case requested
        case running
        case finished
        case denied
    }

    let callID: String
    let name: String
    /// The one argument worth showing on a phone — the command, the path — not
    /// the whole JSON blob.
    let headline: String
    /// Every argument, pretty-printed, for the expanded view.
    let detail: String
    var output: String = ""
    var diff: String = ""
    var state: State = .running
    /// Set when the server auto-approved a read-only command in agent mode.
    var autoNote: String = ""

    var isWrite: Bool { !ToolCall.readOnlyTools.contains(name) }

    /// Mirrors `SAFE_TOOLS` on the server. Only used to colour the card — the
    /// server, not this list, decides what actually needs approval.
    static let readOnlyTools: Set<String> = [
        "read_file", "list_dir", "search_files", "fetch_url", "update_plan",
        "read_skill", "docker_ps", "container_logs", "system_info",
    ]

    var symbol: String {
        switch name {
        case "run_command":                   return "terminal"
        case "read_file", "read_skill":       return "doc.text"
        case "write_file", "edit_file":       return "square.and.pencil"
        case "list_dir", "search_files":      return "folder"
        case "fetch_url":                     return "globe"
        case "update_plan":                   return "checklist"
        default:                              return isWrite ? "wrench.and.screwdriver" : "eye"
        }
    }
}

/// One row in the transcript. Deliberately flat: SwiftUI renders a list, and a
/// tree of nested message parts would buy nothing on a phone screen.
struct ChatItem: Identifiable, Hashable {
    enum Kind: Hashable {
        case user
        case assistant
        /// Extended-thinking output, collapsed by default.
        case thinking
        case tool
        case error
        /// Server-side notices: a permission request, "stopped", a rewind.
        case notice
    }

    let id: String
    var kind: Kind
    var text: String
    var tool: ToolCall?
    /// Set on user rows so "edit and resend" can tell the server which message
    /// to truncate at (it counts only visible user messages).
    var ordinal: Int?
    /// On user rows: what was attached (files, pictures, context) — 0.26.
    var attachments: [ChatAttachmentLabel] = []
    /// On assistant rows: who answered ("Mistral Vibe · GLM 5.3") — 0.26.
    var by: String = ""

    init(id: String = UUID().uuidString, kind: Kind, text: String,
         tool: ToolCall? = nil, ordinal: Int? = nil,
         attachments: [ChatAttachmentLabel] = [], by: String = "") {
        self.id = id
        self.kind = kind
        self.text = text
        self.tool = tool
        self.ordinal = ordinal
        self.attachments = attachments
        self.by = by
    }
}

/// One attachment as a sent message shows it: its name and kind (image,
/// file, folder, service, system, health …).
struct ChatAttachmentLabel: Hashable {
    let name: String
    let kind: String

    static func list(_ raw: Any?) -> [ChatAttachmentLabel] {
        (raw as? [Any] ?? []).compactMap { entry in
            guard let a = entry as? [String: Any], let name = a["name"] as? String, !name.isEmpty
            else { return nil }
            return ChatAttachmentLabel(name: name, kind: a["kind"] as? String ?? "")
        }
    }

    var symbol: String {
        switch kind {
        case "image":   return "photo"
        case "text", "file": return "doc.text"
        case "folder":  return "folder"
        case "service": return "shippingbox"
        case "system":  return "gauge.with.dots.needle.33percent"
        case "health":  return "checkmark.shield"
        case "unit":    return "gearshape.2"
        default:        return "paperclip"
        }
    }
}

// MARK: - Incoming events

struct ChatSnapshot {
    let chatID: String
    let title: String
    let items: [ChatItem]
    let config: ChatConfig
    let running: Bool
    let paused: Bool
    let plan: [PlanStep]
    let pause: PauseInfo?
}

enum ChatServerEvent {
    case snapshot(ChatSnapshot)
    case userEcho(text: String, queued: Bool, attachments: [ChatAttachmentLabel])
    /// Who wrote the answer that just finished (from the turn's usage, 0.26).
    case answeredBy(String)
    /// A remark from the server about the turn ("this model cannot see pictures").
    case notice(String)
    case assistantDelta(String)
    case thinkingDelta(String)
    case toolRequest(ToolCall)
    case toolStart(ToolCall)
    case toolResult(id: String, output: String, diff: String)
    case plan([PlanStep])
    case runState(running: Bool, paused: Bool)
    case paused(PauseInfo)
    case config(ChatConfig)
    case chatMeta(id: String, title: String)
    case permission(title: String, detail: String)
    case failure(String)
    case rewound(String)
    case stopped
    case done
    /// A frame this build does not know about. Ignored rather than treated as
    /// an error: the server may be newer than the app.
    case unknown(String)
}

enum ChatProtocol {

    // MARK: Parsing

    static func parse(_ raw: String) -> ChatServerEvent {
        guard let data = raw.data(using: .utf8),
              let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let type = json["type"] as? String else {
            return .unknown("")
        }

        switch type {
        case "chat":
            return .snapshot(snapshot(from: json))

        case "user_echo":
            return .userEcho(text: json["text"] as? String ?? "",
                             queued: json["queued"] as? Bool ?? false,
                             attachments: ChatAttachmentLabel.list(json["attachments"]))

        case "text":
            return .assistantDelta(json["delta"] as? String ?? "")

        case "thinking":
            return .thinkingDelta(json["delta"] as? String ?? "")

        case "tool_request":
            var call = toolCall(from: json)
            call.state = .requested
            return .toolRequest(call)

        case "tool_start":
            var call = toolCall(from: json)
            call.state = .running
            call.autoNote = json["auto"] as? String ?? ""
            return .toolStart(call)

        case "tool_result":
            return .toolResult(id: json["id"] as? String ?? "",
                               output: json["output"] as? String ?? "",
                               diff: json["diff"] as? String ?? "")

        case "plan":
            return .plan(planSteps(json["items"]))

        case "run_state":
            return .runState(running: json["running"] as? Bool ?? false,
                             paused: json["paused"] as? Bool ?? false)

        case "paused":
            return .paused(PauseInfo(json: json))

        case "config":
            return .config(ChatConfig(json: json))

        case "chat_meta":
            return .chatMeta(id: json["id"] as? String ?? "",
                             title: json["title"] as? String ?? "")

        case "permission":
            let req = json["request"] as? [String: Any] ?? [:]
            return .permission(title: req["title"] as? String ?? "Permission needed",
                               detail: req["explanation"] as? String
                                    ?? req["detail"] as? String ?? "")

        case "error":
            return .failure(json["message"] as? String ?? "The server reported an error.")

        case "rewound":
            return .rewound(json["text"] as? String ?? "")

        case "stopped":
            return .stopped

        case "done":
            return .done

        case "usage":
            // the label under the answer; tokens and cost have no place here
            let turn = json["turn"] as? [String: Any] ?? [:]
            if let by = turn["by"] as? String, !by.isEmpty { return .answeredBy(by) }
            return .unknown(type)

        case "notice":
            return .notice(json["text"] as? String ?? "")

        // Service pushes exist but have no place in the transcript.
        case "services":
            return .unknown(type)

        default:
            return .unknown(type)
        }
    }

    /// A freshly-attached device gets the whole conversation replayed, plus any
    /// deltas buffered while a run is in flight (`live`).
    private static func snapshot(from json: [String: Any]) -> ChatSnapshot {
        var items: [ChatItem] = []
        var userOrdinal = 0

        for case let event as [String: Any] in (json["events"] as? [Any] ?? []) {
            switch event["t"] as? String {
            case "user":
                items.append(ChatItem(kind: .user,
                                      text: event["text"] as? String ?? "",
                                      ordinal: userOrdinal,
                                      attachments: ChatAttachmentLabel.list(event["attachments"])))
                userOrdinal += 1
            case "assistant":
                items.append(ChatItem(kind: .assistant, text: event["text"] as? String ?? "",
                                      by: event["by"] as? String ?? ""))
            case "tool":
                let name = event["name"] as? String ?? "?"
                let args = event["args"] as? [String: Any] ?? [:]
                var call = ToolCall(callID: UUID().uuidString,
                                    name: name,
                                    headline: headline(tool: name, args: args),
                                    detail: prettyArgs(args))
                call.output = event["output"] as? String ?? ""
                call.state = .finished
                items.append(ChatItem(kind: .tool, text: name, tool: call))
            default:
                break
            }
        }

        // Replay the live buffer — the turn in flight — so a reconnect mid-run
        // shows what is happening: the text so far, the commands with their
        // output, and above all a call still waiting for its OK. Without that
        // card a run that needs you looks frozen, and nothing can unblock it.
        var assistantBuffer = ""
        var thinkingBuffer = ""
        func flushText() {
            if !thinkingBuffer.isEmpty {
                items.append(ChatItem(kind: .thinking, text: thinkingBuffer))
                thinkingBuffer = ""
            }
            if !assistantBuffer.isEmpty {
                items.append(ChatItem(kind: .assistant, text: assistantBuffer))
                assistantBuffer = ""
            }
        }
        func index(of id: String) -> Int? {
            items.lastIndex { $0.tool?.callID == id }
        }
        for case let event as [String: Any] in (json["live"] as? [Any] ?? []) {
            switch event["type"] as? String {
            case "text":
                if !thinkingBuffer.isEmpty, assistantBuffer.isEmpty {
                    items.append(ChatItem(kind: .thinking, text: thinkingBuffer))
                    thinkingBuffer = ""
                }
                assistantBuffer += event["delta"] as? String ?? ""
            case "thinking":
                thinkingBuffer += event["delta"] as? String ?? ""
            case "tool_request":
                flushText()
                var call = toolCall(from: event)
                call.state = .requested
                items.append(ChatItem(id: call.callID, kind: .tool, text: call.name, tool: call))
            case "tool_start":
                flushText()
                var call = toolCall(from: event)
                call.state = .running
                call.autoNote = event["auto"] as? String ?? ""
                if let i = index(of: call.callID) {
                    items[i].tool = call
                } else {
                    items.append(ChatItem(id: call.callID, kind: .tool, text: call.name, tool: call))
                }
            case "tool_result":
                flushText()
                let id = event["id"] as? String ?? ""
                let output = event["output"] as? String ?? ""
                if let i = index(of: id), var call = items[i].tool {
                    call.output = output
                    call.diff = event["diff"] as? String ?? ""
                    call.state = output == "[denied by user]" ? .denied : .finished
                    items[i].tool = call
                }
            default:
                break
            }
        }
        flushText()

        let pauseJSON = json["pause"] as? [String: Any] ?? [:]
        return ChatSnapshot(
            chatID: json["id"] as? String ?? "",
            title: json["title"] as? String ?? "",
            items: items,
            config: ChatConfig(json: json["config"] as? [String: Any] ?? [:]),
            running: json["running"] as? Bool ?? false,
            paused: json["paused"] as? Bool ?? false,
            plan: planSteps(json["plan"]),
            pause: pauseJSON.isEmpty ? nil : PauseInfo(json: pauseJSON))
    }

    private static func toolCall(from json: [String: Any]) -> ToolCall {
        let name = json["name"] as? String ?? "?"
        let args = json["args"] as? [String: Any] ?? [:]
        return ToolCall(callID: json["id"] as? String ?? UUID().uuidString,
                        name: name,
                        headline: headline(tool: name, args: args),
                        detail: prettyArgs(args))
    }

    private static func planSteps(_ raw: Any?) -> [PlanStep] {
        (raw as? [Any] ?? []).compactMap { entry in
            guard let step = entry as? [String: Any],
                  let title = step["title"] as? String else { return nil }
            return PlanStep(title: title, status: step["status"] as? String ?? "pending")
        }
    }

    // MARK: Argument rendering

    /// The one line worth showing on a phone. A tool card that leads with
    /// `{"command":"docker ps -a","timeout":60}` is unreadable; the command
    /// itself is the whole point.
    static func headline(tool: String, args: [String: Any]) -> String {
        let preferred = ["command", "path", "file", "url", "query", "pattern", "name"]
        for key in preferred {
            if let value = args[key] as? String, !value.isEmpty {
                return value
            }
        }
        if let steps = args["steps"] as? [Any] { return "\(steps.count) steps" }
        return args.isEmpty ? "" : prettyArgs(args)
    }

    static func prettyArgs(_ args: [String: Any]) -> String {
        guard !args.isEmpty else { return "" }
        if let data = try? JSONSerialization.data(withJSONObject: args,
                                                  options: [.prettyPrinted, .sortedKeys]),
           let text = String(data: data, encoding: .utf8) {
            return text
        }
        return String(describing: args)
    }

    // MARK: Outgoing frames

    static func open(chatID: String) -> String {
        encode(["type": "open", "id": chatID])
    }

    static func reset() -> String {
        encode(["type": "reset"])
    }

    /// `images`: uploaded pictures (server paths) for models that see images;
    /// `attachments`: name and kind of everything attached, for the chips.
    static func user(text: String, context: String = "", images: [String] = [],
                     attachments: [ChatAttachmentLabel] = []) -> String {
        var frame: [String: Any] = ["type": "user", "text": text, "context": context]
        if !images.isEmpty { frame["images"] = images }
        if !attachments.isEmpty {
            frame["attachments"] = attachments.map { ["name": $0.name, "kind": $0.kind] }
        }
        return encode(frame)
    }

    static func config(_ config: ChatConfig) -> String {
        encode([
            "type": "config",
            "mode": config.mode.rawValue,
            "provider": config.provider,
            "model": config.model,
            "workdir": config.workdir,
            "thinking": config.thinking,
        ])
    }

    static func approve(callID: String, approved: Bool) -> String {
        encode(["type": "approve", "id": callID, "approved": approved])
    }

    static func stop() -> String { encode(["type": "stop"]) }

    /// The phone is about to suspend the app: the socket may linger, but
    /// nobody is looking — the server pushes "waiting for your OK" instead
    /// (server 0.25; older servers ignore unknown frames).
    static func away() -> String { encode(["type": "away"]) }

    /// Back in front.
    static func back() -> String { encode(["type": "back"]) }

    static func resume() -> String { encode(["type": "continue"]) }

    /// Truncate the conversation at the Nth visible user message. With `text`,
    /// the edited version is resent immediately; without it, the message is
    /// simply retracted.
    static func rewind(ordinal: Int, text: String = "") -> String {
        encode(["type": "rewind", "ordinal": ordinal, "text": text])
    }

    private static func encode(_ object: [String: Any]) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: object),
              let text = String(data: data, encoding: .utf8) else { return "{}" }
        return text
    }
}
