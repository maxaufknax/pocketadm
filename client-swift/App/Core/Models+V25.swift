import Foundation

// Responses added with server 0.25: the watch's channel, push to phones, the
// file browser's start and its changes. Same lenient decoding as the rest
// (Models+Ops.swift): a field this build does not know never blanks a screen.

// MARK: - The watch's channel

struct WatchChannelPage: Decodable {
    let messages: [WatchMessage]
    /// Older messages exist (scroll back with `before`).
    let more: Bool
    let unread: Int
    let read: Double
    /// The watch is writing an answer right now.
    let replying: Bool
    let status: ChannelStatus?

    enum CodingKeys: String, CodingKey { case messages, more, unread, read, replying, status }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        messages = c.get(.messages, [])
        more = c.get(.more, false)
        unread = c.get(.unread, 0)
        read = c.get(.read, 0)
        replying = c.get(.replying, false)
        status = c.opt(.status)
    }
}

struct WatchMessage: Decodable, Identifiable, Hashable {
    let id: String
    let t: Double
    /// watch, user or system.
    let role: String
    let text: String
    /// Background the watch keeps out of the message itself (numbers, the
    /// log line, the steps) — shown on request.
    let detail: String
    let title: String
    /// critical, important or info.
    let importance: String
    let topic: String
    /// observe, incident, weekly, test, chat or system.
    let kind: String
    let actions: [AlertAction]
    let feedback: String
    let replyTo: String

    var date: Date { Date(timeIntervalSince1970: t) }
    var isUser: Bool { role == "user" }
    var isSystem: Bool { role == "system" }

    var severity: Severity {
        switch importance {
        case "critical":  return .crit
        case "important": return .warn
        default:          return .info
        }
    }

    enum CodingKeys: String, CodingKey {
        case id, t, role, text, detail, title, importance, topic, kind, actions, feedback
        case replyTo = "reply_to"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.get(.id, UUID().uuidString)
        t = c.get(.t, 0)
        role = c.get(.role, "watch")
        text = c.get(.text, "")
        detail = c.get(.detail, "")
        title = c.get(.title, "")
        importance = c.get(.importance, "info")
        topic = c.get(.topic, "")
        kind = c.get(.kind, "")
        actions = c.get(.actions, [])
        feedback = c.get(.feedback, "")
        replyTo = c.get(.replyTo, "")
    }
}

struct ChannelStatus: Decodable {
    let enabled: Bool
    let paused: Bool
    let running: Bool
    let quietNow: Bool
    let route: WatchStatus.Route?
    let nextRound: Double?
    let lastRound: Double?

    enum CodingKeys: String, CodingKey {
        case enabled, paused, running, route
        case quietNow = "quiet_now"
        case nextRound = "next_round"
        case lastRound = "last_round"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        enabled = c.get(.enabled, false)
        paused = c.get(.paused, false)
        running = c.get(.running, false)
        quietNow = c.get(.quietNow, false)
        route = c.opt(.route)
        nextRound = c.opt(.nextRound)
        lastRound = c.opt(.lastRound)
    }
}

struct ChannelChatResponse: Decodable {
    let message: WatchMessage?
    let replying: Bool

    enum CodingKeys: String, CodingKey { case message, replying }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        message = c.opt(.message)
        replying = c.get(.replying, false)
    }
}

// MARK: - Push to phones

struct PushStatus: Decodable {
    let relay: String
    let devices: [PushDevice]

    enum CodingKeys: String, CodingKey { case relay, devices }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        relay = c.get(.relay, "")
        devices = c.get(.devices, [])
    }
}

struct PushDevice: Decodable, Identifiable, Hashable {
    let id: String
    let name: String
    let platform: String
    /// info, important or critical — the least important watch message it gets.
    let min: String
    let assistant: Bool
    let preview: Bool
    let added: Double
    let lastOK: Double
    let lastError: String

    enum CodingKeys: String, CodingKey {
        case id, name, platform, min, assistant, preview, added
        case lastOK = "last_ok"
        case lastError = "last_error"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.get(.id, "")
        name = c.get(.name, "")
        platform = c.get(.platform, "ios")
        min = c.get(.min, "info")
        assistant = c.get(.assistant, true)
        preview = c.get(.preview, true)
        added = c.get(.added, 0)
        lastOK = c.get(.lastOK, 0)
        lastError = c.get(.lastError, "")
    }
}

// MARK: - Files

/// Where the file browser opens: "/" when PocketADM sees the whole server.
struct FSStart: Decodable {
    let path: String
    let display: String
    let roots: [String]
    let wholeServer: Bool

    enum CodingKeys: String, CodingKey {
        case path, display, roots
        case wholeServer = "whole_server"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        path = c.get(.path, "")
        display = c.get(.display, "")
        roots = c.get(.roots, [])
        wholeServer = c.get(.wholeServer, false)
    }
}

/// A file after a change: its new state, and for a save the version kept for
/// an undo.
struct FSChange: Decodable {
    let path: String
    let display: String
    let size: Int64
    let modified: Double
    let version: String

    enum CodingKeys: String, CodingKey { case path, display, size, modified, version }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        path = c.get(.path, "")
        display = c.get(.display, "")
        size = c.get(.size, 0)
        modified = c.get(.modified, 0)
        version = c.get(.version, "")
    }
}
