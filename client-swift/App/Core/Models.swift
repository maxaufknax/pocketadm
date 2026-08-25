import Foundation

// Every shape here was captured from a live server (`/openapi.json` declares
// the routes but the handlers return plain dicts, so the schema block is
// empty). Fields the server can hand back as null are optional here — notably
// `docker` and `net` on /api/system, which are null whenever the Docker socket
// is missing or the latency probe has not completed a cycle yet.

// MARK: - Connect / identity

/// GET /api/info — unauthenticated. The Connect screen uses it to prove the
/// URL really is a PocketADM server before asking for a password.
// Equatable so AppState.Phase (which carries one) can synthesise its own
// conformance — SwiftUI needs it for `.animation(value: app.phase)`.
struct ServerInfo: Decodable, Equatable {
    let helmsman: Bool
    let version: String
    let serverName: String
    let demo: Bool
    let totpRequired: Bool

    enum CodingKeys: String, CodingKey {
        case helmsman, version, demo
        case serverName = "server_name"
        case totpRequired = "totp_required"
    }
}

/// POST /api/login and POST /api/pair/claim both return this.
struct TokenResponse: Decodable {
    let token: String
    let serverName: String?

    enum CodingKeys: String, CodingKey {
        case token
        case serverName = "server_name"
    }
}

/// GET /api/me — the signed-in server's capabilities.
struct MeResponse: Decodable {
    let ok: Bool
    let version: String
    let demo: Bool
    let hostname: String
    let serverName: String
    let totpEnabled: Bool
    let canPair: Bool

    enum CodingKeys: String, CodingKey {
        case ok, version, demo, hostname
        case serverName = "server_name"
        case totpEnabled = "totp_enabled"
        case canPair = "can_pair"
    }
}

/// POST /api/pair/new — the code a signed-in device turns into a QR.
struct PairCode: Decodable {
    let code: String
    let ttl: Int
    let serverName: String?

    enum CodingKeys: String, CodingKey {
        case code, ttl
        case serverName = "server_name"
    }
}

// MARK: - System metrics

struct SystemSnapshot: Decodable {
    let hostname: String
    let cpuPercent: Double
    let cpuCount: Int
    let memory: Usage
    let disk: Usage
    let load: [Double]
    let uptime: Double
    /// null when no Docker socket is mounted (the public demo, for instance).
    let docker: DockerInfo?
    /// null until the metrics loop has completed one sampling interval.
    let net: NetRates?

    struct Usage: Decodable {
        let total: Int64
        let used: Int64
        let percent: Double
        /// memory calls it `available`, disk calls it `free`; neither is
        /// present on the other, so both are optional and `spare` picks one.
        let available: Int64?
        let free: Int64?
        var spare: Int64 { available ?? free ?? max(0, total - used) }
    }

    struct DockerInfo: Decodable {
        let containers: Int
        let running: Int
        let images: Int
        let version: String
        let os: String
    }

    struct NetRates: Decodable {
        let rx: Double
        let tx: Double
        /// null when the latency probe failed — a dropped probe must not blank
        /// out the whole tile.
        let ping: Double?
    }

    enum CodingKeys: String, CodingKey {
        case hostname, memory, disk, load, uptime, docker, net
        case cpuPercent = "cpu_percent"
        case cpuCount = "cpu_count"
    }
}

/// GET /api/metrics/history?minutes=N
struct MetricsHistory: Decodable {
    let points: [Point]

    struct Point: Decodable, Identifiable {
        let t: Double
        let cpu: Double
        let mem: Double
        let disk: Double
        let load: Double
        let rx: Double
        let tx: Double
        let ping: Double?

        var id: Double { t }
        var date: Date { Date(timeIntervalSince1970: t) }
    }
}

// MARK: - Containers

struct Container: Decodable, Identifiable, Hashable {
    let id: String
    let name: String
    let image: String
    let state: String
    let status: String
    let health: String
    let ports: [Port]
    let composeProject: String
    let composeService: String
    let created: Double
    let mountsDockerSock: Bool
    let service: ServiceMeta?

    struct Port: Decodable, Hashable {
        // `private` and `public` are Swift keywords, hence the rename.
        let privatePort: Int
        let publicPort: Int?
        let type: String?
        let ip: String?

        enum CodingKeys: String, CodingKey {
            case privatePort = "private"
            case publicPort = "public"
            case type, ip
        }
    }

    struct ServiceMeta: Decodable, Hashable {
        let label: String?
        let icon: String?
        let category: String?
        let security: Bool?
    }

    var isRunning: Bool { state == "running" }

    /// What to show as the container's headline. The catalog label ("Gitea")
    /// reads better than the raw container name when it exists.
    var displayName: String { service?.label ?? name }

    /// Containers are grouped by their compose project on the dashboard, the
    /// same way the web UI stacks them. Ones outside a project fall into a
    /// single bucket rather than each becoming its own one-item group.
    var stack: String { composeProject.isEmpty ? "Ungrouped" : composeProject }

    enum CodingKeys: String, CodingKey {
        case id, name, image, state, status, health, ports, created, service
        case composeProject = "compose_project"
        case composeService = "compose_service"
        case mountsDockerSock = "mounts_docker_sock"
    }
}

struct ContainerLogs: Decodable {
    let logs: String
}

struct ContainerDetail: Decodable {
    let id: String
    let name: String
    let image: String
    let state: String
    let health: String?
    let restartCount: Int?
    let restartPolicy: String?
    let privileged: Bool?
    let envCount: Int?
    let startedAt: String?
    let mounts: [Mount]?
    let networks: [String]?

    struct Mount: Decodable, Hashable {
        let source: String
        let dest: String
        let rw: Bool
        let type: String
    }

    enum CodingKeys: String, CodingKey {
        case id, name, image, state, health, mounts, networks, privileged
        case restartCount = "restart_count"
        case restartPolicy = "restart_policy"
        case envCount = "env_count"
        case startedAt = "started_at"
    }
}

// MARK: - Terminal

/// GET /api/terminal/targets — already grouped and labelled by the server, so
/// the client just renders it rather than inventing its own grouping.
struct TerminalTargets: Decodable {
    let groups: [Group]

    struct Group: Decodable, Identifiable {
        let label: String
        let targets: [Target]
        var id: String { label }
    }

    struct Target: Decodable, Identifiable, Hashable {
        let id: String
        let label: String
        let sub: String?
        let icon: String?
        let container: Bool?
    }
}

/// GET/POST /api/terminal/sessions — sessions outlive the app, so the list is
/// the real source of truth for "what shells do I have open".
struct TerminalSessionList: Decodable {
    let sessions: [TerminalSession]
    let maxLive: Int

    enum CodingKeys: String, CodingKey {
        case sessions
        case maxLive = "max_live"
    }
}

struct TerminalSession: Decodable, Identifiable, Hashable {
    let id: String
    let title: String
    let context: String
    let created: Double
    let lastActive: Double
    let alive: Bool
    let clients: Int

    enum CodingKeys: String, CodingKey {
        case id, title, context, created, alive, clients
        case lastActive = "last_active"
    }
}

struct TerminalSessionCreated: Decodable {
    let session: TerminalSession
}
