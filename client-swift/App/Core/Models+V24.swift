import Foundation

// Shapes added with server 0.24: apps (grouped containers), the live container
// detail, drives and folder sizes, the activity feed, the watch, AI accounts
// and their sign-in flows. Lenient like the rest (see Models+Ops.swift): the
// server evolves on its own schedule.

// MARK: - Apps

/// GET /api/services — containers grouped into apps, problems first.
struct ServicesResponse: Decodable {
    let groups: [AppGroup]

    enum CodingKeys: String, CodingKey { case groups }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        groups = c.get(.groups, [])
    }
}

struct AppGroup: Decodable, Identifiable, Hashable {
    let id: String
    let name: String
    let category: String
    /// Names to match a brand icon against, best first.
    let iconNames: [String]
    let primary: String
    let containers: [Container]
    let running: Int
    let total: Int
    let unhealthy: Int
    /// running / partial / stopped / unhealthy / restarting
    let state: String
    let ports: [Int]
    let reachablePorts: [Int]
    let composeProject: String
    let composeDir: String
    let security: Bool

    var hasProblem: Bool { state == "unhealthy" || state == "restarting" || state == "partial" }
    var isRunning: Bool { state == "running" || state == "unhealthy" || state == "partial" }

    enum CodingKeys: String, CodingKey {
        case id, name, category, primary, containers, running, total, unhealthy, state, ports, security
        case iconNames = "icon_names"
        case reachablePorts = "reachable_ports"
        case composeProject = "compose_project"
        case composeDir = "compose_dir"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.get(.id, UUID().uuidString)
        name = c.get(.name, "")
        category = c.get(.category, "")
        iconNames = c.get(.iconNames, [])
        primary = c.get(.primary, "")
        containers = c.get(.containers, [])
        running = c.get(.running, 0)
        total = c.get(.total, 0)
        unhealthy = c.get(.unhealthy, 0)
        state = c.get(.state, "running")
        ports = c.get(.ports, [])
        reachablePorts = c.get(.reachablePorts, [])
        composeProject = c.get(.composeProject, "")
        composeDir = c.get(.composeDir, "")
        security = c.get(.security, false)
    }

    /// Groups built on the phone for a server older than 0.24 — by compose
    /// project, the way the previous app showed them.
    init(fallbackFrom containers: [Container], name: String) {
        id = "stack-" + name
        self.name = name
        category = ""
        iconNames = containers.first.map { [$0.displayName, $0.name, $0.image] } ?? []
        primary = containers.first?.id ?? ""
        self.containers = containers
        running = containers.filter(\.isRunning).count
        total = containers.count
        unhealthy = containers.filter { $0.health == "unhealthy" }.count
        state = unhealthy > 0 ? "unhealthy" : running == total ? "running" : running == 0 ? "stopped" : "partial"
        ports = Array(Set(containers.flatMap { $0.ports.compactMap(\.publicPort) })).sorted()
        reachablePorts = []
        composeProject = name
        composeDir = ""
        security = false
    }
}

// MARK: - Container detail extras

struct ContainerTop: Decodable {
    let titles: [String]
    let processes: [[String]]
    let note: String

    enum CodingKeys: String, CodingKey { case titles, processes, note }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        titles = c.get(.titles, [])
        processes = c.get(.processes, [])
        note = c.get(.note, "")
    }

    /// One process, by column name — `docker top` columns depend on ps.
    func value(_ row: [String], _ names: String...) -> String {
        for name in names {
            if let i = titles.firstIndex(where: { $0.uppercased() == name.uppercased() }), i < row.count {
                return row[i]
            }
        }
        return ""
    }
}

struct ContainerEventList: Decodable {
    let events: [ContainerEvent]

    enum CodingKeys: String, CodingKey { case events }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        events = c.get(.events, [])
    }
}

struct ContainerEvent: Decodable, Identifiable, Hashable {
    let t: Double
    let action: String
    let summary: String
    let severity: Severity
    var id: String { "\(t)-\(action)" }
    var date: Date { Date(timeIntervalSince1970: t) }

    enum CodingKeys: String, CodingKey { case t, action, summary, severity }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        t = c.get(.t, 0)
        action = c.get(.action, "")
        summary = c.get(.summary, "")
        severity = Severity(c.get(.severity, "ok"))
    }
}

/// POST /api/services/{id}/action
struct GroupActionResult: Decodable {
    let ok: Bool
    let done: [String]
    let failed: [String]

    enum CodingKeys: String, CodingKey { case ok, done, failed }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = c.get(.ok, true)
        done = c.get(.done, [])
        failed = c.get(.failed, [])
    }
}

// MARK: - Drives and folders

struct StorageResponse: Decodable {
    let filesystems: [Filesystem]

    enum CodingKeys: String, CodingKey { case filesystems }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        filesystems = c.get(.filesystems, [])
    }
}

struct Filesystem: Decodable, Identifiable, Hashable {
    let mount: String
    /// Where the app browses it (/host/… on a containerised server).
    let path: String
    let device: String
    let fstype: String
    let label: String
    let model: String
    let transport: String
    let external: Bool
    /// system / data / external / network / boot
    let kind: String
    let total: Int64
    let used: Int64
    let free: Int64
    let percent: Double
    let browsable: Bool
    var id: String { mount }

    /// What a person calls it: the label, the model, or the mount point.
    var title: String {
        if mount == "/" { return "System disk" }
        if !label.isEmpty { return label }
        if !model.isEmpty { return model }
        return mount
    }

    enum CodingKeys: String, CodingKey {
        case mount, path, device, fstype, label, model, transport, external, kind
        case total, used, free, percent, browsable
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        mount = c.get(.mount, "")
        path = c.get(.path, "")
        device = c.get(.device, "")
        fstype = c.get(.fstype, "")
        label = c.get(.label, "")
        model = c.get(.model, "")
        transport = c.get(.transport, "")
        external = c.get(.external, false)
        kind = c.get(.kind, "data")
        total = c.get(.total, 0)
        used = c.get(.used, 0)
        free = c.get(.free, 0)
        percent = c.get(.percent, 0)
        browsable = c.get(.browsable, false)
    }
}

/// GET /api/fs/usage — what fills a folder.
struct FolderUsage: Decodable {
    let path: String
    let display: String
    let total: Int64
    let children: [Child]
    let partial: Bool

    struct Child: Decodable, Identifiable, Hashable {
        let name: String
        let path: String
        let display: String
        let bytes: Int64
        var id: String { path }

        enum CodingKeys: String, CodingKey { case name, path, display, bytes }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            name = c.get(.name, "")
            path = c.get(.path, "")
            display = c.get(.display, "")
            bytes = c.get(.bytes, 0)
        }
    }

    enum CodingKeys: String, CodingKey { case path, display, total, children, partial }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        path = c.get(.path, "")
        display = c.get(.display, "")
        total = c.get(.total, 0)
        children = c.get(.children, [])
        partial = c.get(.partial, false)
    }
}

/// GET /api/fs/search
struct FileSearchResult: Decodable {
    let hits: [Hit]
    let complete: Bool

    struct Hit: Decodable, Identifiable, Hashable {
        let name: String
        let path: String
        let display: String
        let dir: Bool
        let size: Int64
        let text: Bool
        var id: String { path }

        enum CodingKeys: String, CodingKey { case name, path, display, dir, size, text }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            name = c.get(.name, "")
            path = c.get(.path, "")
            display = c.get(.display, "")
            dir = c.get(.dir, false)
            size = c.get(.size, 0)
            text = c.get(.text, false)
        }
    }

    enum CodingKeys: String, CodingKey { case hits, complete }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        hits = c.get(.hits, [])
        complete = c.get(.complete, true)
    }
}

// MARK: - Activity

struct ActivityFeed: Decodable {
    let events: [ActivityEvent]
    let cursor: Double?
    /// category id -> label
    let categories: [String: String]
    let stats: Stats

    struct Stats: Decodable {
        let hours: Int
        let counts: [String: Int]
        let problems: Int

        init() { hours = 24; counts = [:]; problems = 0 }

        enum CodingKeys: String, CodingKey { case hours, counts, problems }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            hours = c.get(.hours, 24)
            counts = c.get(.counts, [:])
            problems = c.get(.problems, 0)
        }
    }

    enum CodingKeys: String, CodingKey { case events, cursor, categories, stats }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        events = c.get(.events, [])
        cursor = c.opt(.cursor)
        categories = c.get(.categories, [:])
        stats = c.opt(.stats) ?? Stats()
    }
}

struct ActivityEvent: Decodable, Identifiable, Hashable {
    let id: String
    let t: Double
    /// containers / security / system / network / updates / app
    let category: String
    let kind: String
    let title: String
    let detail: String
    let severity: Severity
    let target: String
    let source: String
    var date: Date { Date(timeIntervalSince1970: t) }

    enum CodingKeys: String, CodingKey {
        case id, t, category, kind, title, detail, severity, target, source
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.get(.id, UUID().uuidString)
        t = c.get(.t, 0)
        category = c.get(.category, "system")
        kind = c.get(.kind, "")
        title = c.get(.title, "")
        detail = c.get(.detail, "")
        severity = Severity(c.get(.severity, "info"))
        target = c.get(.target, "")
        source = c.get(.source, "")
    }

    /// One line of the live stream ("data: {…}"), or nil for comments and
    /// heartbeats.
    static func fromStreamLine(_ line: String) -> ActivityEvent? {
        guard line.hasPrefix("data:") else { return nil }
        let json = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
        guard let data = json.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(ActivityEvent.self, from: data)
    }
}

// MARK: - The watch

struct WatchStatus: Decodable {
    let settings: WatchSettings
    let route: Route
    let running: Bool
    let paused: Bool
    let quietNow: Bool
    let lastRound: Double?
    let nextRound: Double?
    let sentToday: [String: Int]
    let spent30d: Double
    let budgetLeft: Double?
    let pendingEvents: Int
    let runs: [Run]
    let memory: [String]

    struct Route: Decodable {
        let provider: String
        let model: String
        let label: String
        let usable: Bool

        init() { provider = ""; model = ""; label = ""; usable = false }

        enum CodingKeys: String, CodingKey { case provider, model, label, usable }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            provider = c.get(.provider, "")
            model = c.get(.model, "")
            label = c.get(.label, "")
            usable = c.get(.usable, false)
        }
    }

    struct Run: Decodable, Identifiable, Hashable {
        let t: Double
        let kind: String
        /// notify / silent / held / skipped / error / busy
        let decision: String
        let reason: String
        let error: String
        let topic: String
        let importance: String
        let cost: Double
        var id: Double { t }
        var date: Date { Date(timeIntervalSince1970: t) }

        enum CodingKeys: String, CodingKey { case t, kind, decision, reason, error, topic, importance, cost }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            t = c.get(.t, 0)
            kind = c.get(.kind, "")
            decision = c.get(.decision, "")
            reason = c.get(.reason, "")
            error = c.get(.error, "")
            topic = c.get(.topic, "")
            importance = c.get(.importance, "")
            cost = c.get(.cost, 0)
        }
    }

    enum CodingKeys: String, CodingKey {
        case settings, route, running, paused, runs, memory
        case quietNow = "quiet_now"
        case lastRound = "last_round"
        case nextRound = "next_round"
        case sentToday = "sent_today"
        case spent30d = "spent_30d"
        case budgetLeft = "budget_left"
        case pendingEvents = "pending_events"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        settings = c.opt(.settings) ?? WatchSettings()
        route = c.opt(.route) ?? Route()
        running = c.get(.running, false)
        paused = c.get(.paused, false)
        quietNow = c.get(.quietNow, false)
        lastRound = c.opt(.lastRound)
        nextRound = c.opt(.nextRound)
        sentToday = c.get(.sentToday, [:])
        spent30d = c.get(.spent30d, 0)
        budgetLeft = c.opt(.budgetLeft)
        pendingEvents = c.get(.pendingEvents, 0)
        runs = c.get(.runs, [])
        memory = c.get(.memory, [])
    }
}

/// The watch's settings, editable on the phone. `matrixToken` is write-only:
/// the server says whether one is set, never what it is.
struct WatchSettings: Decodable, Equatable {
    var enabled = false
    var lang = ""
    var timezone = ""
    var intervalMin = 180
    var quietStart = "23:00"
    var quietEnd = "07:30"
    var infoPerDay = 3
    var importantPerDay = 8
    var weekly = true
    var knowledge = ""
    var budgetUSD = 5.0
    var pushMin = "important"
    var ntfyURL = ""
    var matrixHomeserver = ""
    var matrixRoom = ""
    var matrixTokenSet = false
    var pausedUntil: Double = 0
    var mutes: [Mute] = []

    struct Mute: Decodable, Equatable, Identifiable {
        let topic: String
        let until: Double
        let note: String
        var id: String { topic }

        enum CodingKeys: String, CodingKey { case topic, until, note }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            topic = c.get(.topic, "")
            until = c.get(.until, 0)
            note = c.get(.note, "")
        }
    }

    init() {}

    enum CodingKeys: String, CodingKey {
        case enabled, lang, timezone, weekly, knowledge, mutes
        case intervalMin = "interval_min"
        case quietStart = "quiet_start"
        case quietEnd = "quiet_end"
        case infoPerDay = "info_per_day"
        case importantPerDay = "important_per_day"
        case budgetUSD = "budget_usd"
        case pushMin = "push_min"
        case ntfyURL = "ntfy_url"
        case matrixHomeserver = "matrix_homeserver"
        case matrixRoom = "matrix_room"
        case matrixTokenSet = "matrix_token_set"
        case pausedUntil = "paused_until"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        enabled = c.get(.enabled, false)
        lang = c.get(.lang, "")
        timezone = c.get(.timezone, "")
        intervalMin = c.get(.intervalMin, 180)
        quietStart = c.get(.quietStart, "23:00")
        quietEnd = c.get(.quietEnd, "07:30")
        infoPerDay = c.get(.infoPerDay, 3)
        importantPerDay = c.get(.importantPerDay, 8)
        weekly = c.get(.weekly, true)
        knowledge = c.get(.knowledge, "")
        budgetUSD = c.get(.budgetUSD, 5.0)
        pushMin = c.get(.pushMin, "important")
        ntfyURL = c.get(.ntfyURL, "")
        matrixHomeserver = c.get(.matrixHomeserver, "")
        matrixRoom = c.get(.matrixRoom, "")
        matrixTokenSet = c.get(.matrixTokenSet, false)
        pausedUntil = c.get(.pausedUntil, 0)
        mutes = c.get(.mutes, [])
    }

    /// The fields the server accepts on POST /api/watch, in its own names.
    func changes(matrixToken: String) -> [String: WatchValue] {
        var out: [String: WatchValue] = [
            "enabled": .bool(enabled), "lang": .string(lang), "timezone": .string(timezone),
            "interval_min": .int(intervalMin), "quiet_start": .string(quietStart),
            "quiet_end": .string(quietEnd), "info_per_day": .int(infoPerDay),
            "important_per_day": .int(importantPerDay), "weekly": .bool(weekly),
            "knowledge": .string(knowledge), "budget_usd": .double(budgetUSD),
            "push_min": .string(pushMin), "ntfy_url": .string(ntfyURL),
            "matrix_homeserver": .string(matrixHomeserver), "matrix_room": .string(matrixRoom),
        ]
        if !matrixToken.isEmpty { out["matrix_token"] = .string(matrixToken) }
        return out
    }
}

/// A JSON value for the watch's settings dictionary.
enum WatchValue: Encodable, Equatable {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let v): try c.encode(v)
        case .int(let v):    try c.encode(v)
        case .double(let v): try c.encode(v)
        case .bool(let v):   try c.encode(v)
        }
    }
}

// MARK: - AI accounts

struct AIAccounts: Decodable {
    let accounts: [Account]
    let local: Local
    let routes: [String: Route]

    struct Account: Decodable, Identifiable, Hashable {
        let id: String
        let name: String
        let vendor: String
        /// The coding CLI that carries the subscription ("" for key-only).
        let engine: String
        let keyProvider: String
        let subscription: String
        let keyHint: String
        let brand: String
        let keySet: Bool
        let canSubscribe: Bool
        let cliInstalled: Bool
        let cliVersion: String
        let signedIn: Bool
        let detail: String
        let plan: String
        let connected: Bool
        let usedFor: [String]

        enum CodingKeys: String, CodingKey {
            case id, name, vendor, engine, subscription, brand, detail, plan, connected
            case keyProvider = "key_provider"
            case keyHint = "key_hint"
            case keySet = "key_set"
            case canSubscribe = "can_subscribe"
            case cliInstalled = "cli_installed"
            case cliVersion = "cli_version"
            case signedIn = "signed_in"
            case usedFor = "used_for"
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            id = c.get(.id, "")
            name = c.get(.name, "")
            vendor = c.get(.vendor, "")
            engine = c.get(.engine, "")
            keyProvider = c.get(.keyProvider, "")
            subscription = c.get(.subscription, "")
            keyHint = c.get(.keyHint, "")
            brand = c.get(.brand, "")
            keySet = c.get(.keySet, false)
            canSubscribe = c.get(.canSubscribe, false)
            cliInstalled = c.get(.cliInstalled, false)
            cliVersion = c.get(.cliVersion, "")
            signedIn = c.get(.signedIn, false)
            detail = c.get(.detail, "")
            plan = c.get(.plan, "")
            connected = c.get(.connected, false)
            usedFor = c.get(.usedFor, [])
        }
    }

    struct Local: Decodable {
        let running: Bool
        let models: Int
        let usedFor: [String]

        init() { running = false; models = 0; usedFor = [] }

        enum CodingKeys: String, CodingKey { case running, models; case usedFor = "used_for" }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            running = c.get(.running, false)
            models = c.get(.models, 0)
            usedFor = c.get(.usedFor, [])
        }
    }

    struct Route: Decodable, Hashable {
        let label: String
        let provider: String
        let model: String
        /// false = "same as the assistant"
        let custom: Bool
        let providerLabel: String

        enum CodingKeys: String, CodingKey {
            case label, provider, model, custom
            case providerLabel = "provider_label"
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            label = c.get(.label, "")
            provider = c.get(.provider, "")
            model = c.get(.model, "")
            custom = c.get(.custom, false)
            providerLabel = c.get(.providerLabel, "")
        }
    }

    enum CodingKeys: String, CodingKey { case accounts, local, routes }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        accounts = c.get(.accounts, [])
        local = c.opt(.local) ?? Local()
        routes = c.get(.routes, [:])
    }
}

struct RoutesResponse: Decodable {
    let routes: [String: AIAccounts.Route]

    enum CodingKeys: String, CodingKey { case routes }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        routes = c.get(.routes, [:])
    }
}

/// A sign-in in progress (POST /api/ai/accounts/{engine}/signin and polls).
struct SignInFlow: Decodable, Equatable {
    let id: String
    let engine: String
    /// starting / installing / waiting_browser / waiting_code / verifying /
    /// done / failed / cancelled
    let state: String
    let url: String
    let userCode: String
    let message: String
    let error: String
    let label: String

    var isOver: Bool { ["done", "failed", "cancelled"].contains(state) }

    enum CodingKeys: String, CodingKey {
        case id, engine, state, url, message, error, label
        case userCode = "user_code"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.get(.id, "")
        engine = c.get(.engine, "")
        state = c.get(.state, "starting")
        url = c.get(.url, "")
        userCode = c.get(.userCode, "")
        message = c.get(.message, "")
        error = c.get(.error, "")
        label = c.get(.label, "")
    }
}

struct SignInFlowEnvelope: Decodable {
    let flow: SignInFlow
}

struct ChatExport: Decodable {
    let markdown: String

    enum CodingKeys: String, CodingKey { case markdown }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        markdown = c.get(.markdown, "")
    }
}
