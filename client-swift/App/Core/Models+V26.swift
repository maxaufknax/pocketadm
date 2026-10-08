import Foundation

// Responses added with server 0.26: the assistant's notes, the server
// inventory with its systemd units, and suggestions for the assistant. Same
// lenient decoding as the rest (Models+Ops.swift).

// MARK: - The assistant's notes

struct AgentNotes: Decodable {
    let notes: [AgentNote]
    let topics: [NoteTopic]
    let stats: NoteStats
    /// After an add or edit: the note concerned.
    let note: AgentNote?
    /// After an add: "added", or "updated" when the same fact was known.
    let result: String
    let tidy: TidyResult?

    enum CodingKeys: String, CodingKey { case notes, topics, stats, note, result, tidy }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        notes = c.get(.notes, [])
        topics = c.get(.topics, NoteTopic.fallback)
        stats = c.opt(.stats) ?? NoteStats()
        note = c.opt(.note)
        result = c.get(.result, "")
        tidy = c.opt(.tidy)
    }

    func label(of topic: String) -> String {
        topics.first { $0.id == topic }?.label ?? topic.capitalized
    }
}

struct AgentNote: Decodable, Identifiable, Hashable {
    let id: String
    let text: String
    let topic: String
    /// What the note is about, when not obvious ("Projekt StudGo").
    let subject: String
    /// assistant, you or import.
    let source: String
    let created: Double
    let updated: Double
    let pinned: Bool

    var updatedDate: Date { Date(timeIntervalSince1970: updated) }
    var byYou: Bool { source == "you" }
    /// Not confirmed for three months: worth a look.
    var isStale: Bool { Date().timeIntervalSince1970 - updated > 90 * 86400 }

    enum CodingKeys: String, CodingKey { case id, text, topic, subject, source, created, updated, pinned }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.get(.id, UUID().uuidString)
        text = c.get(.text, "")
        topic = c.get(.topic, "other")
        subject = c.get(.subject, "")
        source = c.get(.source, "assistant")
        created = c.get(.created, 0)
        updated = c.get(.updated, 0)
        pinned = c.get(.pinned, false)
    }
}

struct NoteTopic: Decodable, Identifiable, Hashable {
    let id: String
    let label: String

    static let fallback: [NoteTopic] = [
        ("server", "Server & hardware"), ("services", "Apps & services"),
        ("network", "Network & domains"), ("storage", "Storage & backups"),
        ("security", "Access & security"), ("procedures", "How things are done"),
        ("projects", "Projects"), ("preferences", "Preferences"), ("other", "Other"),
    ].map { NoteTopic(id: $0.0, label: $0.1) }

    init(id: String, label: String) {
        self.id = id
        self.label = label
    }

    enum CodingKeys: String, CodingKey { case id, label }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.get(.id, "other")
        label = c.get(.label, "Other")
    }

    var symbol: String {
        switch id {
        case "server":      return "cpu"
        case "services":    return "shippingbox.fill"
        case "network":     return "network"
        case "storage":     return "externaldrive.fill"
        case "security":    return "lock.shield.fill"
        case "procedures":  return "list.number"
        case "projects":    return "folder.fill"
        case "preferences": return "person.crop.circle.fill"
        default:            return "note.text"
        }
    }
}

struct NoteStats: Decodable, Hashable {
    let count: Int
    let pinned: Int
    let stale: Int
    /// Characters the notes take in the assistant's instructions.
    let chars: Int
    let budget: Int
    let canUndo: Bool

    init() {
        count = 0; pinned = 0; stale = 0; chars = 0; budget = 6000; canUndo = false
    }

    /// How full the space for notes in every conversation is.
    var fill: Double { budget > 0 ? min(1, Double(chars) / Double(budget)) : 0 }

    enum CodingKeys: String, CodingKey {
        case count, pinned, stale, chars, budget
        case canUndo = "can_undo"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        count = c.get(.count, 0)
        pinned = c.get(.pinned, 0)
        stale = c.get(.stale, 0)
        chars = c.get(.chars, 0)
        budget = c.get(.budget, 6000)
        canUndo = c.get(.canUndo, false)
    }
}

struct TidyResult: Decodable, Hashable {
    let before: Int
    let after: Int
    let usedAI: Bool
    let removed: Int

    enum CodingKeys: String, CodingKey {
        case before, after, removed
        case usedAI = "used_ai"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        before = c.get(.before, 0)
        after = c.get(.after, 0)
        usedAI = c.get(.usedAI, false)
        removed = c.get(.removed, 0)
    }
}

// MARK: - The server inventory

struct ServerInventory: Decodable {
    let time: Double
    let host: InventoryHost
    let stacks: [InventoryStack]
    let containers: [InventoryService]
    let domains: [InventoryDomain]
    let services: [SystemUnit]
    let timers: [SystemUnit]
    let cron: [CronJob]
    let drives: [InventoryDrive]

    var failedUnits: [SystemUnit] { (services + timers).filter(\.isFailed) }

    enum CodingKeys: String, CodingKey {
        case time, host, stacks, containers, domains, services, timers, cron, drives
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        time = c.get(.time, 0)
        host = c.opt(.host) ?? InventoryHost()
        stacks = c.get(.stacks, [])
        containers = c.get(.containers, [])
        domains = c.get(.domains, [])
        services = c.get(.services, [])
        timers = c.get(.timers, [])
        cron = c.get(.cron, [])
        drives = c.get(.drives, [])
    }
}

struct InventoryHost: Decodable, Hashable {
    let hostname: String
    let os: String
    let kernel: String
    let arch: String
    let cores: Int
    let memory: Double
    let uptime: Double

    init() {
        hostname = ""; os = ""; kernel = ""; arch = ""; cores = 0; memory = 0; uptime = 0
    }

    enum CodingKeys: String, CodingKey { case hostname, os, kernel, arch, cores, memory, uptime }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        hostname = c.get(.hostname, "")
        os = c.get(.os, "")
        kernel = c.get(.kernel, "")
        arch = c.get(.arch, "")
        cores = c.get(.cores, 0)
        memory = c.get(.memory, 0)
        uptime = c.get(.uptime, 0)
    }
}

struct InventoryStack: Decodable, Identifiable, Hashable {
    let project: String
    let dir: String
    let services: [InventoryService]
    var id: String { project }

    var running: Int { services.filter { $0.state == "running" }.count }

    enum CodingKeys: String, CodingKey { case project, dir, services }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        project = c.get(.project, "")
        dir = c.get(.dir, "")
        services = c.get(.services, [])
    }
}

struct InventoryService: Decodable, Identifiable, Hashable {
    let name: String
    let state: String
    let health: String
    let image: String
    var id: String { name }

    enum CodingKeys: String, CodingKey { case name, state, health, image }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.get(.name, "")
        state = c.get(.state, "")
        health = c.get(.health, "")
        image = c.get(.image, "")
    }
}

struct InventoryDomain: Decodable, Identifiable, Hashable {
    let domain: String
    let target: String
    /// The container the domain leads to ("" for a host process or elsewhere).
    let service: String
    let tls: Bool
    let enabled: Bool
    /// Where it was found: Nginx Proxy Manager, Caddy, Traefik, nginx.
    let source: String
    let redirect: Bool
    var id: String { domain }

    var url: URL? { URL(string: (tls ? "https://" : "http://") + domain) }

    enum CodingKeys: String, CodingKey { case domain, target, service, tls, enabled, source, redirect }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        domain = c.get(.domain, "")
        target = c.get(.target, "")
        service = c.get(.service, "")
        tls = c.get(.tls, false)
        enabled = c.get(.enabled, true)
        source = c.get(.source, "")
        redirect = c.get(.redirect, false)
    }
}

/// A systemd service or timer on the host.
struct SystemUnit: Decodable, Identifiable, Hashable {
    let unit: String
    /// service or timer.
    let kind: String
    let description: String
    /// active, inactive, failed, activating …
    let active: String
    /// running, exited, dead, waiting …
    let sub: String
    /// enabled, disabled, static …
    let enabled: String
    let result: String
    let since: String
    let nextRun: String
    let lastRun: String
    let triggers: String
    let triggeredBy: String
    let memory: Double
    /// Added by the admin (not shipped with the system).
    let custom: Bool
    let path: String
    /// The journal, on the detail call only.
    let logs: String
    var id: String { unit }

    var name: String {
        unit.replacingOccurrences(of: ".service", with: "").replacingOccurrences(of: ".timer", with: "")
    }
    var isTimer: Bool { kind == "timer" }
    var isFailed: Bool { active == "failed" }
    var isRunning: Bool { active == "active" && (sub == "running" || sub == "waiting" || sub == "listening") }

    /// "Running", "Waiting", "Ran and finished", "Stopped", "Failed".
    var stateText: String {
        switch (active, sub) {
        case ("failed", _):          return "Failed"
        case ("active", "running"):  return "Running"
        case ("active", "waiting"):  return "Waiting"
        case ("active", "exited"):   return "Done (stays active)"
        case ("activating", _):      return "Starting"
        case ("deactivating", _):    return "Stopping"
        case ("inactive", _):        return triggeredBy.isEmpty ? "Stopped" : "Runs when triggered"
        default:                     return active.capitalized
        }
    }

    enum CodingKeys: String, CodingKey {
        case unit, kind, description, active, sub, enabled, result, since, triggers, memory
        case custom, path, logs
        case nextRun = "next_run"
        case lastRun = "last_run"
        case triggeredBy = "triggered_by"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        unit = c.get(.unit, "")
        kind = c.get(.kind, "service")
        description = c.get(.description, "")
        active = c.get(.active, "")
        sub = c.get(.sub, "")
        enabled = c.get(.enabled, "")
        result = c.get(.result, "")
        since = c.get(.since, "")
        nextRun = c.get(.nextRun, "")
        lastRun = c.get(.lastRun, "")
        triggers = c.get(.triggers, "")
        triggeredBy = c.get(.triggeredBy, "")
        memory = c.get(.memory, 0)
        custom = c.get(.custom, false)
        path = c.get(.path, "")
        logs = c.get(.logs, "")
    }
}

struct CronJob: Decodable, Identifiable, Hashable {
    let schedule: String
    let user: String
    let command: String
    let file: String
    var id: String { file + "|" + schedule + "|" + command }

    /// "every 15 minutes", "daily at 06:25", or the raw schedule.
    var readableSchedule: String {
        switch schedule {
        case "@reboot":  return "at every boot"
        case "@hourly":  return "every hour"
        case "@daily", "@midnight": return "every day"
        case "@weekly":  return "every week"
        case "@monthly": return "every month"
        default: break
        }
        let f = schedule.split(separator: " ").map(String.init)
        guard f.count == 5 else { return schedule }
        let (minute, hour, dom, month, dow) = (f[0], f[1], f[2], f[3], f[4])
        if minute.hasPrefix("*/"), hour == "*", dom == "*", month == "*", dow == "*" {
            return "every \(minute.dropFirst(2)) minutes"
        }
        if Int(minute) != nil, hour == "*", dom == "*", month == "*", dow == "*" {
            return "every hour at :\(minute.count == 1 ? "0" + minute : minute)"
        }
        if let m = Int(minute), let h = Int(hour), month == "*" {
            let time = String(format: "%02d:%02d", h, m)
            if dom == "*" && dow == "*" { return "daily at \(time)" }
            if dom == "*" { return "weekly (day \(dow)) at \(time)" }
            if dow == "*" { return "monthly (day \(dom)) at \(time)" }
        }
        return schedule
    }

    enum CodingKeys: String, CodingKey { case schedule, user, command, file }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        schedule = c.get(.schedule, "")
        user = c.get(.user, "")
        command = c.get(.command, "")
        file = c.get(.file, "")
    }
}

struct InventoryDrive: Decodable, Identifiable, Hashable {
    let mount: String
    let kind: String
    let fstype: String
    let label: String
    let model: String
    let total: Double
    let used: Double
    let free: Double
    let percent: Double
    var id: String { mount }

    enum CodingKeys: String, CodingKey { case mount, kind, fstype, label, model, total, used, free, percent }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        mount = c.get(.mount, "")
        kind = c.get(.kind, "")
        fstype = c.get(.fstype, "")
        label = c.get(.label, "")
        model = c.get(.model, "")
        total = c.get(.total, 0)
        used = c.get(.used, 0)
        free = c.get(.free, 0)
        percent = c.get(.percent, 0)
    }
}

struct UnitActionResult: Decodable {
    let ok: Bool
    let output: String

    enum CodingKeys: String, CodingKey { case ok, output }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = c.get(.ok, false)
        output = c.get(.output, "")
    }
}

// MARK: - Suggestions for the assistant

struct AISuggestions: Decodable {
    let suggestions: [String]

    enum CodingKeys: String, CodingKey { case suggestions }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        suggestions = c.get(.suggestions, [])
    }
}

// MARK: - How-tos and the server map

struct AgentSkillList: Decodable {
    let skills: [AgentSkill]

    enum CodingKeys: String, CodingKey { case skills }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        skills = c.get(.skills, [])
    }
}

struct AgentSkill: Decodable, Identifiable, Hashable {
    let name: String
    let description: String
    let chars: Int
    var id: String { name }

    /// deploy-hub-landing → Deploy hub landing
    var title: String {
        let words = name.replacingOccurrences(of: "-", with: " ")
        return words.prefix(1).uppercased() + words.dropFirst()
    }

    enum CodingKeys: String, CodingKey { case name, description, chars }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.get(.name, "")
        description = c.get(.description, "")
        chars = c.get(.chars, 0)
    }
}

struct SkillContent: Decodable {
    let content: String

    enum CodingKeys: String, CodingKey { case content }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        content = c.get(.content, "")
    }
}

struct ServerMapText: Decodable {
    let text: String
    let enabled: Bool

    enum CodingKeys: String, CodingKey { case text, enabled }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        text = c.get(.text, "")
        enabled = c.get(.enabled, true)
    }
}

// MARK: - Chat attachments

struct ChatUpload: Decodable, Hashable {
    let name: String
    /// Where it is on the server, as the assistant opens it.
    let path: String
    let size: Int64
    let mediaType: String
    /// image, text or file.
    let kind: String
    /// The beginning of a text file, quoted into the message.
    let text: String
    let truncated: Bool

    enum CodingKeys: String, CodingKey {
        case name, path, size, kind, text, truncated
        case mediaType = "media_type"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.get(.name, "file")
        path = c.get(.path, "")
        size = c.get(.size, 0)
        mediaType = c.get(.mediaType, "")
        kind = c.get(.kind, "file")
        text = c.get(.text, "")
        truncated = c.get(.truncated, false)
    }
}
