import Foundation

// Models for everything the first preview left out: updates, snapshots, the
// app catalog, health reports, notifications, the audit log, jobs, local AI,
// coding CLIs, the file browser and host users.
//
// All shapes were captured from a live server. The handlers return plain dicts
// rather than Pydantic models, so `/openapi.json` documents the routes but not
// the payloads — the fixtures under tools/fixtures are the contract.

// MARK: - Lenient decoding

extension KeyedDecodingContainer {
    /// A key that may be absent, null, or (after a server upgrade) a type this
    /// build does not expect, resolved to a fallback instead of failing the
    /// whole response.
    ///
    /// PocketADM's server evolves independently of the app — a single new
    /// nullable field must not blank an entire screen, which is exactly what a
    /// synthesised `init(from:)` would do.
    func get<T: Decodable>(_ key: Key, _ fallback: T) -> T {
        ((try? decodeIfPresent(T.self, forKey: key)) ?? nil) ?? fallback
    }

    func opt<T: Decodable>(_ key: Key) -> T? {
        (try? decodeIfPresent(T.self, forKey: key)) ?? nil
    }
}

/// The server's four-level status vocabulary, shared by reports, notifications
/// and the audit log. Colours live in the SwiftUI layer.
enum Severity: String, Decodable, Hashable {
    case ok, info, warn, crit

    init(_ raw: String) {
        self = Severity(rawValue: raw.lowercased()) ?? .info
    }

    /// Sorting weight — worst first, which is the only order that makes sense
    /// for a screen you glance at.
    var weight: Int {
        switch self {
        case .crit: return 0
        case .warn: return 1
        case .info: return 2
        case .ok:   return 3
        }
    }

    var symbol: String {
        switch self {
        case .ok:   return "checkmark.circle.fill"
        case .info: return "info.circle.fill"
        case .warn: return "exclamationmark.triangle.fill"
        case .crit: return "xmark.octagon.fill"
        }
    }
}

// MARK: - Notifications

struct NotificationFeed: Decodable {
    let items: [Item]
    let unseen: Int

    struct Item: Decodable, Identifiable, Hashable {
        let id: String
        let time: Double
        let source: String
        let status: Severity
        let title: String
        let body: String
        /// How often the same alert has repeated — the server de-duplicates by
        /// fingerprint rather than spamming one row per occurrence.
        let count: Int

        var date: Date { Date(timeIntervalSince1970: time) }

        enum CodingKeys: String, CodingKey {
            case id, time, source, status, title, body, count
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            id = c.get(.id, UUID().uuidString)
            time = c.get(.time, 0)
            source = c.get(.source, "")
            status = Severity(c.get(.status, "info"))
            title = c.get(.title, "")
            body = c.get(.body, "")
            count = c.get(.count, 1)
        }
    }

    enum CodingKeys: String, CodingKey { case items, unseen }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        items = c.get(.items, [])
        unseen = c.get(.unseen, 0)
    }
}

// MARK: - Updates

struct UpdatesResponse: Decodable {
    let docker: [DockerUpdate]
    let apt: AptStatus

    enum CodingKeys: String, CodingKey { case docker, apt }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        docker = c.get(.docker, [])
        apt = c.opt(.apt) ?? AptStatus.unavailable
    }

    var pending: [DockerUpdate] { docker.filter { $0.updateAvailable && !$0.ignored } }
    var ignored: [DockerUpdate] { docker.filter { $0.ignored } }
    var upToDate: [DockerUpdate] { docker.filter { !$0.updateAvailable && !$0.ignored } }
}

struct DockerUpdate: Decodable, Identifiable, Hashable {
    let image: String
    let usedBy: [String]
    let updateAvailable: Bool
    let error: String
    let ignored: Bool
    let ageDays: Int?
    let tag: String
    let repo: String
    let label: String
    let icon: String
    let category: String
    /// The catalog marks images whose updates usually carry security fixes.
    let security: Bool
    let version: String
    /// "high" / "normal" — the server's own ranking, not a guess made here.
    let priority: String

    var id: String { image }
    var displayName: String { label.isEmpty ? image : label }
    var isHighPriority: Bool { priority == "high" }

    enum CodingKeys: String, CodingKey {
        case image, error, ignored, tag, repo, label, icon, category
        case security, version, priority
        case usedBy = "used_by"
        case updateAvailable = "update_available"
        case ageDays = "age_days"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        image = c.get(.image, "")
        usedBy = c.get(.usedBy, [])
        updateAvailable = c.get(.updateAvailable, false)
        error = c.get(.error, "")
        ignored = c.get(.ignored, false)
        ageDays = c.opt(.ageDays)
        tag = c.get(.tag, "")
        repo = c.get(.repo, "")
        label = c.get(.label, "")
        icon = c.get(.icon, "")
        category = c.get(.category, "")
        security = c.get(.security, false)
        version = c.get(.version, "")
        priority = c.get(.priority, "normal")
    }
}

struct AptStatus: Decodable {
    let available: Bool
    let packages: [Package]

    static let unavailable = AptStatus(available: false, packages: [])

    init(available: Bool, packages: [Package]) {
        self.available = available
        self.packages = packages
    }

    struct Package: Decodable, Identifiable, Hashable {
        let package: String
        let new: String
        let current: String
        var id: String { package }

        enum CodingKeys: String, CodingKey { case package, new, current }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            package = c.get(.package, "")
            new = c.get(.new, "")
            current = c.get(.current, "")
        }
    }

    enum CodingKeys: String, CodingKey { case available, packages }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        available = c.get(.available, false)
        packages = c.get(.packages, [])
    }
}

/// GET /api/updates/detail?image= — what changed upstream, for the sheet you
/// read before pulling a new image onto a live service.
struct UpdateDetail: Decodable {
    let image: String
    let local: Local
    let releases: [Release]
    let label: String
    let icon: String

    struct Local: Decodable, Hashable {
        let version: String
        let created: String
        let tag: String
        let digest: String

        enum CodingKeys: String, CodingKey { case version, created, tag, digest }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            version = c.get(.version, "")
            created = c.get(.created, "")
            tag = c.get(.tag, "")
            digest = c.get(.digest, "")
        }

        init() { version = ""; created = ""; tag = ""; digest = "" }
    }

    struct Release: Decodable, Identifiable, Hashable {
        let tag: String
        let name: String
        let date: String
        let prerelease: Bool
        let notes: String
        let url: String
        var id: String { tag + date }

        enum CodingKeys: String, CodingKey { case tag, name, date, prerelease, notes, url }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            tag = c.get(.tag, "")
            name = c.get(.name, "")
            date = c.get(.date, "")
            prerelease = c.get(.prerelease, false)
            notes = c.get(.notes, "")
            url = c.get(.url, "")
        }
    }

    enum CodingKeys: String, CodingKey { case image, local, releases, label, icon }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        image = c.get(.image, "")
        local = c.opt(.local) ?? Local()
        releases = c.get(.releases, [])
        label = c.get(.label, "")
        icon = c.get(.icon, "")
    }
}

// MARK: - Jobs

/// Every long-running action (pull an image, install an app, roll back) answers
/// with a job id and streams its log from /api/jobs/{id}/stream.
struct JobRef: Decodable {
    let jobID: String
    enum CodingKeys: String, CodingKey { case jobID = "job_id" }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        jobID = c.get(.jobID, "")
    }
}

struct JobStatus: Decodable, Identifiable, Hashable {
    let id: String
    let title: String
    let kind: String
    /// running / done / failed
    let status: String
    let created: Double
    let finished: Double?
    let logTail: [String]

    var isRunning: Bool { status == "running" }
    var succeeded: Bool { status == "done" }

    enum CodingKeys: String, CodingKey {
        case id, title, kind, status, created, finished
        case logTail = "log_tail"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.get(.id, "")
        title = c.get(.title, "")
        kind = c.get(.kind, "")
        status = c.get(.status, "running")
        created = c.get(.created, 0)
        finished = c.opt(.finished)
        logTail = c.get(.logTail, [])
    }
}

// MARK: - Snapshots

/// An image pinned under a snapshot tag before an update, so a bad pull can be
/// undone without hunting for the old digest.
struct SnapshotList: Decodable {
    let snapshots: [Snapshot]

    enum CodingKeys: String, CodingKey { case snapshots }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        snapshots = c.get(.snapshots, [])
    }
}

struct Snapshot: Decodable, Identifiable, Hashable {
    let id: String
    let time: Double
    let image: String
    let imageID: String
    let ref: String
    let containers: [String]

    var date: Date { Date(timeIntervalSince1970: time) }

    enum CodingKeys: String, CodingKey {
        case id, time, image, ref, containers
        case imageID = "image_id"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.get(.id, "")
        time = c.get(.time, 0)
        image = c.get(.image, "")
        imageID = c.get(.imageID, "")
        ref = c.get(.ref, "")
        containers = c.get(.containers, [])
    }
}

// MARK: - App catalog

struct AppsResponse: Decodable {
    let catalog: [CatalogApp]
    /// Keyed by catalog id. Present means "already running on this host",
    /// whether PocketADM installed it or it was there first (`source`).
    let installed: [String: InstalledApp]
    let catalogInfo: CatalogInfo

    var categories: [String] {
        Array(Set(catalog.map(\.category))).sorted()
    }

    enum CodingKeys: String, CodingKey {
        case catalog, installed
        case catalogInfo = "catalog_info"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        catalog = c.get(.catalog, [])
        installed = c.get(.installed, [:])
        catalogInfo = c.opt(.catalogInfo) ?? CatalogInfo()
    }
}

struct CatalogInfo: Decodable {
    let url: String
    let enabled: Bool
    let remoteCount: Int
    let error: String

    enum CodingKeys: String, CodingKey {
        case url, enabled, error
        case remoteCount = "remote_count"
    }

    init() { url = ""; enabled = false; remoteCount = 0; error = "" }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        url = c.get(.url, "")
        enabled = c.get(.enabled, false)
        remoteCount = c.get(.remoteCount, 0)
        error = c.get(.error, "")
    }
}

struct InstalledApp: Decodable, Hashable {
    /// "helmsman" when this app installed it, "external" when it was already
    /// running — uninstall is only offered for the former.
    let source: String
    let running: Bool
    let containers: [String]
    let ports: [Int]

    var managed: Bool { source == "helmsman" }

    enum CodingKeys: String, CodingKey { case source, running, containers, ports }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        source = c.get(.source, "external")
        running = c.get(.running, false)
        containers = c.get(.containers, [])
        ports = c.get(.ports, [])
    }
}

struct CatalogApp: Decodable, Identifiable, Hashable {
    let id: String
    let name: String
    let icon: String
    let category: String
    let tagline: String
    let description: String
    /// The catalog's plain-language "why would I want this" paragraph — the
    /// thing that makes the store readable by someone who is not a sysadmin.
    let why: String
    let fields: [Field]
    let website: String
    let docs: String

    struct Field: Decodable, Identifiable, Hashable {
        let key: String
        let label: String
        let defaultValue: String
        var id: String { key }

        enum CodingKeys: String, CodingKey {
            case key, label
            case defaultValue = "default"
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            key = c.get(.key, "")
            label = c.get(.label, "")
            defaultValue = c.get(.defaultValue, "")
        }
    }

    enum CodingKeys: String, CodingKey {
        case id, name, icon, category, tagline, description, why, fields, website, docs
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.get(.id, "")
        name = c.get(.name, "")
        icon = c.get(.icon, "")
        category = c.get(.category, "Other")
        tagline = c.get(.tagline, "")
        description = c.get(.description, "")
        why = c.get(.why, "")
        fields = c.get(.fields, [])
        website = c.get(.website, "")
        docs = c.get(.docs, "")
    }
}

struct CommandOutput: Decodable {
    let output: String
    enum CodingKeys: String, CodingKey { case output }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        output = c.get(.output, "")
    }
}

// MARK: - Checks & reports

struct ReportsIndex: Decodable {
    let reports: [ReportSummary]
    let config: ReportConfig

    enum CodingKeys: String, CodingKey { case reports, config }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        reports = c.get(.reports, [])
        config = c.opt(.config) ?? ReportConfig()
    }
}

struct ReportConfig: Decodable, Hashable {
    let intervalMin: Int
    let auto: Bool

    init() { intervalMin = 360; auto = true }
    init(intervalMin: Int, auto: Bool) { self.intervalMin = intervalMin; self.auto = auto }

    enum CodingKeys: String, CodingKey {
        case auto
        case intervalMin = "interval_min"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        intervalMin = c.get(.intervalMin, 360)
        auto = c.get(.auto, true)
    }
}

struct ReportCounts: Decodable, Hashable {
    let ok: Int
    let info: Int
    let warn: Int
    let crit: Int

    init() { ok = 0; info = 0; warn = 0; crit = 0 }

    enum CodingKeys: String, CodingKey { case ok, info, warn, crit }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = c.get(.ok, 0)
        info = c.get(.info, 0)
        warn = c.get(.warn, 0)
        crit = c.get(.crit, 0)
    }

    var total: Int { ok + info + warn + crit }
}

struct ReportSummary: Decodable, Identifiable, Hashable {
    /// The file stem, which is also the id used by GET /api/reports/{name}.
    let file: String
    let time: Double
    let score: Severity
    let counts: ReportCounts
    let trigger: String

    var id: String { file }
    var date: Date { Date(timeIntervalSince1970: time) }

    enum CodingKeys: String, CodingKey { case file, time, score, counts, trigger }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        file = c.get(.file, "")
        time = c.get(.time, 0)
        score = Severity(c.get(.score, "info"))
        counts = c.opt(.counts) ?? ReportCounts()
        trigger = c.get(.trigger, "")
    }
}

struct Report: Decodable {
    let time: Double
    let duration: Double
    let trigger: String
    let counts: ReportCounts
    let score: Severity
    let checks: [Check]

    var date: Date { Date(timeIntervalSince1970: time) }

    /// Checks grouped the way the server labelled them, worst group first.
    var groups: [CheckGroup] {
        let buckets = Dictionary(grouping: checks, by: \.group)
        return buckets.map { name, items in
            CheckGroup(name: name, checks: items.sorted { $0.status.weight < $1.status.weight })
        }
        .sorted { lhs, rhs in
            let l = lhs.checks.first?.status.weight ?? 9
            let r = rhs.checks.first?.status.weight ?? 9
            return l == r ? lhs.name < rhs.name : l < r
        }
    }

    struct Check: Decodable, Identifiable, Hashable {
        let id: String
        let group: String
        let title: String
        let icon: String
        let status: Severity
        let summary: String
        /// Present only when something needs doing — the actionable half.
        let recommendation: String?

        enum CodingKeys: String, CodingKey {
            case id, group, title, icon, status, summary, recommendation
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            id = c.get(.id, UUID().uuidString)
            group = c.get(.group, "Other")
            title = c.get(.title, "")
            icon = c.get(.icon, "")
            status = Severity(c.get(.status, "info"))
            summary = c.get(.summary, "")
            let hint: String = c.get(.recommendation, "")
            recommendation = hint.isEmpty ? nil : hint
        }
    }

    enum CodingKeys: String, CodingKey {
        case time, duration, trigger, counts, score, checks
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        time = c.get(.time, 0)
        duration = c.get(.duration, 0)
        trigger = c.get(.trigger, "")
        counts = c.opt(.counts) ?? ReportCounts()
        score = Severity(c.get(.score, "info"))
        checks = c.get(.checks, [])
    }
}

struct CheckGroup: Identifiable, Hashable {
    let name: String
    let checks: [Report.Check]
    var id: String { name }
}

struct AnalysisResponse: Decodable {
    let analysis: String
    enum CodingKeys: String, CodingKey { case analysis }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        analysis = c.get(.analysis, "")
    }
}

// MARK: - Audit log

struct AuditFeed: Decodable {
    let events: [Event]
    /// Timestamp to pass back as `before=` for the next page; nil at the end.
    let cursor: Double?
    let meta: Meta

    struct Event: Decodable, Identifiable, Hashable {
        let t: Double
        let action: String
        let target: String
        let source: String
        let detail: String
        let status: Severity
        let actor: String

        var id: String { "\(t)-\(action)-\(target)" }
        var date: Date { Date(timeIntervalSince1970: t) }

        enum CodingKeys: String, CodingKey {
            case t, action, target, source, detail, status, actor
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            t = c.get(.t, 0)
            action = c.get(.action, "")
            target = c.get(.target, "")
            source = c.get(.source, "")
            detail = c.get(.detail, "")
            status = Severity(c.get(.status, "ok"))
            actor = c.get(.actor, "")
        }
    }

    struct Meta: Decodable {
        /// action id -> {icon, label}, so the app renders the server's own
        /// vocabulary instead of maintaining a second copy that drifts.
        let actions: [String: ActionMeta]

        init() { actions = [:] }

        enum CodingKeys: String, CodingKey { case actions }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            actions = c.get(.actions, [:])
        }
    }

    struct ActionMeta: Decodable, Hashable {
        let icon: String
        let label: String

        enum CodingKeys: String, CodingKey { case icon, label }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            icon = c.get(.icon, "•")
            label = c.get(.label, "")
        }
    }

    enum CodingKeys: String, CodingKey { case events, cursor, meta }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        events = c.get(.events, [])
        cursor = c.opt(.cursor)
        meta = c.opt(.meta) ?? Meta()
    }
}

// MARK: - Local AI (Ollama)

struct LocalAIStatus: Decodable {
    let running: Bool
    let base: String
    let version: String
    let canInstall: Bool
    let ramGB: Double
    let cpuCount: Int
    let installed: [InstalledModel]
    let recommended: [RecommendedModel]

    struct InstalledModel: Decodable, Identifiable, Hashable {
        let name: String
        let size: Int64
        let params: String
        let quant: String
        var id: String { name }

        enum CodingKeys: String, CodingKey { case name, size, params, quant }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            name = c.get(.name, "")
            size = c.get(.size, 0)
            params = c.get(.params, "")
            quant = c.get(.quant, "")
        }
    }

    struct RecommendedModel: Decodable, Identifiable, Hashable {
        let name: String
        let label: String
        let params: String
        let size: String
        let minRAM: Int
        let blurb: String
        let installed: Bool
        /// Whether this box has the RAM for it — the server decides, because it
        /// is the one that knows how much is actually free.
        let fits: Bool
        let suggested: Bool
        let coder: Bool
        var id: String { name }

        enum CodingKeys: String, CodingKey {
            case name, label, params, size, blurb, installed, fits, suggested, coder
            case minRAM = "min_ram"
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            name = c.get(.name, "")
            label = c.get(.label, "")
            params = c.get(.params, "")
            size = c.get(.size, "")
            minRAM = c.get(.minRAM, 0)
            blurb = c.get(.blurb, "")
            installed = c.get(.installed, false)
            fits = c.get(.fits, true)
            suggested = c.get(.suggested, false)
            coder = c.get(.coder, false)
        }
    }

    enum CodingKeys: String, CodingKey {
        case running, base, version, installed, recommended
        case canInstall = "can_install"
        case ramGB = "ram_gb"
        case cpuCount = "cpu_count"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        running = c.get(.running, false)
        base = c.get(.base, "")
        version = c.get(.version, "")
        canInstall = c.get(.canInstall, false)
        ramGB = c.get(.ramGB, 0)
        cpuCount = c.get(.cpuCount, 0)
        installed = c.get(.installed, [])
        recommended = c.get(.recommended, [])
    }
}

// MARK: - Coding CLIs

struct CLIList: Decodable {
    let clis: [CLITool]

    enum CodingKeys: String, CodingKey { case clis }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        clis = c.get(.clis, [])
    }
}

struct CLITool: Decodable, Identifiable, Hashable {
    let id: String
    let name: String
    let vendor: String
    let launch: String
    let subscription: String
    let tagline: String
    let site: String
    let installed: Bool
    let version: String

    enum CodingKeys: String, CodingKey {
        case id, name, vendor, launch, subscription, tagline, site, installed, version
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.get(.id, "")
        name = c.get(.name, "")
        vendor = c.get(.vendor, "")
        launch = c.get(.launch, "")
        subscription = c.get(.subscription, "")
        tagline = c.get(.tagline, "")
        site = c.get(.site, "")
        installed = c.get(.installed, false)
        version = c.get(.version, "")
    }
}

// MARK: - File browser

struct FSListing: Decodable {
    let path: String
    /// "" at a workspace root — there is nowhere further up to go.
    let parent: String
    let dirs: [Entry]
    let fileEntries: [FileEntry]
    let files: Int
    let roots: [String]

    struct Entry: Decodable, Identifiable, Hashable {
        let name: String
        let path: String
        var id: String { path }

        enum CodingKeys: String, CodingKey { case name, path }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            name = c.get(.name, "")
            path = c.get(.path, "")
        }
    }

    struct FileEntry: Decodable, Identifiable, Hashable {
        let name: String
        let path: String
        let size: Int64
        /// The server's own guess at whether a preview would be readable —
        /// opening a binary just to discover it is binary wastes a round trip.
        let text: Bool
        var id: String { path }

        enum CodingKeys: String, CodingKey { case name, path, size, text }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            name = c.get(.name, "")
            path = c.get(.path, "")
            size = c.get(.size, 0)
            text = c.get(.text, false)
        }
    }

    enum CodingKeys: String, CodingKey {
        case path, parent, dirs, files, roots
        case fileEntries = "file_entries"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        path = c.get(.path, "")
        parent = c.get(.parent, "")
        dirs = c.get(.dirs, [])
        fileEntries = c.get(.fileEntries, [])
        files = c.get(.files, 0)
        roots = c.get(.roots, [])
    }
}

struct FileContent: Decodable {
    let path: String
    let size: Int64
    let binary: Bool
    let truncated: Bool
    let content: String

    enum CodingKeys: String, CodingKey { case path, size, binary, truncated, content }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        path = c.get(.path, "")
        size = c.get(.size, 0)
        binary = c.get(.binary, false)
        truncated = c.get(.truncated, false)
        content = c.get(.content, "")
    }
}

// MARK: - Host users

struct ServerUsers: Decodable {
    let users: [HostUser]
    let identity: ServerIdentity
    /// False inside a container without host access — the UI must say *why*
    /// rather than showing buttons that always fail.
    let canManage: Bool
    let reason: String

    enum CodingKeys: String, CodingKey {
        case users, identity, reason
        case canManage = "can_manage"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        users = c.get(.users, [])
        identity = c.opt(.identity) ?? ServerIdentity()
        canManage = c.get(.canManage, false)
        reason = c.get(.reason, "")
    }
}

struct HostUser: Decodable, Identifiable, Hashable {
    let name: String
    let uid: Int
    let shell: String
    let home: String
    let groups: [String]
    /// "human" / "system" — the server classifies; the app just filters.
    let kind: String
    let isAdmin: Bool
    let isRoot: Bool
    let locked: Bool
    let canLogin: Bool
    let role: String

    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name, uid, shell, home, groups, kind, locked, role
        case isAdmin = "is_admin"
        case isRoot = "is_root"
        case canLogin = "can_login"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.get(.name, "")
        uid = c.get(.uid, 0)
        shell = c.get(.shell, "")
        home = c.get(.home, "")
        groups = c.get(.groups, [])
        kind = c.get(.kind, "system")
        isAdmin = c.get(.isAdmin, false)
        isRoot = c.get(.isRoot, false)
        locked = c.get(.locked, false)
        canLogin = c.get(.canLogin, false)
        role = c.get(.role, "")
    }
}

struct ServerIdentity: Decodable {
    let hostname: String
    let os: String
    let kernel: String
    let arch: String
    let inContainer: Bool
    let hostAccess: Bool
    let displayName: String

    init() {
        hostname = ""; os = ""; kernel = ""; arch = ""
        inContainer = false; hostAccess = false; displayName = ""
    }

    enum CodingKeys: String, CodingKey {
        case hostname, os, kernel, arch
        case inContainer = "in_container"
        case hostAccess = "host_access"
        case displayName = "display_name"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        hostname = c.get(.hostname, "")
        os = c.get(.os, "")
        kernel = c.get(.kernel, "")
        arch = c.get(.arch, "")
        inContainer = c.get(.inContainer, false)
        hostAccess = c.get(.hostAccess, false)
        displayName = c.get(.displayName, "")
    }
}

// MARK: - AI configuration

struct AIModels: Decodable {
    let providers: [ProviderModels]
    let defaultChoice: ModelChoice

    struct ProviderModels: Decodable, Identifiable, Hashable {
        let provider: String
        let models: [Model]
        let local: Bool
        /// Display name from the server ("Claude Code", "Codex"); older
        /// servers send none, and the provider id is shown instead.
        let label: String
        /// A coding agent CLI on the server (Claude Code, Codex) rather than
        /// a model API: it uses that CLI's own login and subscription.
        let agent: Bool
        var id: String { provider }
        var displayName: String { label.isEmpty ? provider.capitalized : label }

        enum CodingKeys: String, CodingKey { case provider, models, local, label, agent }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            provider = c.get(.provider, "")
            models = c.get(.models, [])
            local = c.get(.local, false)
            label = c.get(.label, "")
            agent = c.get(.agent, false)
        }
    }

    struct Model: Decodable, Identifiable, Hashable {
        let id: String
        let name: String
        let free: Bool
        let tools: Bool

        enum CodingKeys: String, CodingKey { case id, name, free, tools }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            id = c.get(.id, "")
            name = c.get(.name, "")
            free = c.get(.free, false)
            // Absent means "unknown", and every non-OpenRouter provider omits
            // it — defaulting to false would grey out every usable model.
            tools = c.get(.tools, true)
        }
    }

    enum CodingKeys: String, CodingKey {
        case providers
        case defaultChoice = "default"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        providers = c.get(.providers, [])
        defaultChoice = c.opt(.defaultChoice) ?? ModelChoice()
    }
}

struct ModelChoice: Decodable, Hashable {
    let provider: String
    let model: String

    init() { provider = ""; model = "" }
    init(provider: String, model: String) { self.provider = provider; self.model = model }

    enum CodingKeys: String, CodingKey { case provider, model }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        provider = c.get(.provider, "")
        model = c.get(.model, "")
    }
}

struct AIUsage: Decodable {
    let today: Slot
    let month: Slot

    struct Slot: Decodable, Hashable {
        let input: Int
        let output: Int
        let cost: Double
        let requests: Int

        init() { input = 0; output = 0; cost = 0; requests = 0 }

        enum CodingKeys: String, CodingKey { case input, output, cost, requests }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            input = c.get(.input, 0)
            output = c.get(.output, 0)
            cost = c.get(.cost, 0)
            requests = c.get(.requests, 0)
        }
    }

    enum CodingKeys: String, CodingKey { case today, month }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        today = c.opt(.today) ?? Slot()
        month = c.opt(.month) ?? Slot()
    }
}

// MARK: - Agent configuration

struct AgentToolList: Decodable {
    let tools: [AgentTool]

    enum CodingKeys: String, CodingKey { case tools }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tools = c.get(.tools, [])
    }
}

struct AgentTool: Decodable, Identifiable, Hashable {
    let name: String
    let description: String
    /// Read-only tools run without asking; the rest need approval per call.
    let safe: Bool
    let enabled: Bool
    var id: String { name }

    enum CodingKeys: String, CodingKey { case name, description, safe, enabled }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.get(.name, "")
        description = c.get(.description, "")
        safe = c.get(.safe, false)
        enabled = c.get(.enabled, true)
    }
}

struct TextPayload: Decodable {
    let memory: String
    let instructions: String

    enum CodingKeys: String, CodingKey { case memory, instructions }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        memory = c.get(.memory, "")
        instructions = c.get(.instructions, "")
    }
}

// MARK: - Chats

struct ChatIndex: Decodable {
    let chats: [ChatSummary]

    enum CodingKeys: String, CodingKey { case chats }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        chats = c.get(.chats, [])
    }
}

struct ChatSummary: Decodable, Identifiable, Hashable {
    let id: String
    let title: String
    let created: Double
    let updated: Double
    let archived: Bool
    let messageCount: Int

    var date: Date { Date(timeIntervalSince1970: updated) }

    enum CodingKeys: String, CodingKey {
        case id, title, created, updated, archived
        case messageCount = "message_count"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.get(.id, "")
        title = c.get(.title, "")
        created = c.get(.created, 0)
        updated = c.get(.updated, 0)
        archived = c.get(.archived, false)
        messageCount = c.get(.messageCount, 0)
    }
}

// MARK: - Container stats

struct ContainerStats: Decodable {
    let cpuPercent: Double
    let memUsage: Int64
    let memLimit: Int64

    var memPercent: Double {
        memLimit > 0 ? Double(memUsage) / Double(memLimit) * 100 : 0
    }

    enum CodingKeys: String, CodingKey {
        case cpuPercent = "cpu_percent"
        case memUsage = "mem_usage"
        case memLimit = "mem_limit"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        cpuPercent = c.get(.cpuPercent, 0)
        memUsage = c.get(.memUsage, 0)
        memLimit = c.get(.memLimit, 0)
    }
}

/// The plain `{"ok": true}` many mutating endpoints answer with, plus the
/// optional replacement token the ones that invalidate sessions hand back.
struct OKResponse: Decodable {
    let ok: Bool
    let token: String?
    let message: String

    enum CodingKeys: String, CodingKey { case ok, token, message }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = c.get(.ok, true)
        let fresh: String = c.get(.token, "")
        token = fresh.isEmpty ? nil : fresh
        message = c.get(.message, "")
    }
}

/// GET /api/settings/2fa/setup — the secret plus its otpauth:// URI. The QR is
/// drawn on-device from `uri`; the server also returns an SVG the app ignores.
struct TOTPSetup: Decodable {
    let secret: String
    let uri: String

    enum CodingKeys: String, CodingKey { case secret, uri }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        secret = c.get(.secret, "")
        uri = c.get(.uri, "")
    }
}
