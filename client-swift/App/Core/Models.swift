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
    /// Set when the server offers "Sign in with …" (OpenID Connect, 0.22+);
    /// absent or null otherwise, and on older servers.
    let sso: SSOInfo?

    struct SSOInfo: Decodable, Equatable {
        let label: String
    }

    enum CodingKeys: String, CodingKey {
        case helmsman, version, demo, sso
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

/// GET /api/me — the signed-in server's capabilities. This is the screen-gating
/// payload: it decides which features are even offered (AI configured? pairing
/// allowed? is this a demo?), so every field it can omit has a default here
/// rather than failing the request that unlocks the whole app.
struct MeResponse: Decodable {
    let ok: Bool
    let version: String
    let demo: Bool
    let hostname: String
    let serverName: String
    let totpEnabled: Bool
    let canPair: Bool
    let onboarded: Bool
    /// False until an API key or a local model exists — the assistant tab says
    /// so instead of opening a socket that can only fail.
    let aiConfigured: Bool
    let aiDefault: ModelChoice
    let aiProviders: [String]
    let workspaces: [String]
    let defaultWorkspace: String
    let reportConfig: ReportConfig
    /// The server believes it is reachable from the public internet. Combined
    /// with 2FA off, that is the one thing worth shouting about on a root
    /// gateway — `exposureAck` records that the admin already knows.
    let publicExposure: Bool
    let exposureAck: Bool
    /// What the server can do (0.24+), so this app offers only what works.
    let features: Set<String>
    let watchEnabled: Bool

    func supports(_ feature: String) -> Bool { features.contains(feature) }

    /// Warn only when the risk is real and unacknowledged.
    var shouldWarnAboutExposure: Bool {
        publicExposure && !totpEnabled && !exposureAck && !demo
    }

    enum CodingKeys: String, CodingKey {
        case ok, version, demo, hostname, onboarded, workspaces
        case serverName = "server_name"
        case totpEnabled = "totp_enabled"
        case canPair = "can_pair"
        case aiConfigured = "ai_configured"
        case aiDefault = "ai_default"
        case aiProviders = "ai_providers"
        case defaultWorkspace = "default_workspace"
        case reportConfig = "report_config"
        case publicExposure = "public_exposure"
        case exposureAck = "exposure_ack"
        case features
        case watchEnabled = "watch_enabled"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = c.get(.ok, true)
        version = c.get(.version, "")
        demo = c.get(.demo, false)
        hostname = c.get(.hostname, "")
        serverName = c.get(.serverName, "")
        totpEnabled = c.get(.totpEnabled, false)
        canPair = c.get(.canPair, false)
        onboarded = c.get(.onboarded, true)
        aiConfigured = c.get(.aiConfigured, false)
        aiDefault = c.opt(.aiDefault) ?? ModelChoice()
        aiProviders = c.get(.aiProviders, [])
        workspaces = c.get(.workspaces, [])
        defaultWorkspace = c.get(.defaultWorkspace, "")
        reportConfig = c.opt(.reportConfig) ?? ReportConfig()
        publicExposure = c.get(.publicExposure, false)
        exposureAck = c.get(.exposureAck, false)
        features = Set(c.get(.features, [String]()))
        watchEnabled = c.get(.watchEnabled, false)
    }
}

/// POST /api/pair/new — the code a signed-in device turns into a QR.
struct PairCode: Decodable {
    let code: String
    let ttl: Int
    let serverName: String?
    /// The server's own TLS key fingerprint (0.23+), empty without HTTPS. It
    /// goes into the QR so the new device can pin a self-signed server.
    let tlsFingerprint: String?

    enum CodingKeys: String, CodingKey {
        case code, ttl
        case serverName = "server_name"
        case tlsFingerprint = "tls_fingerprint"
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
        /// Disk reads and writes in bytes/s (0.24+); absent on older servers.
        let dr: Double?
        let dw: Double?
        /// Share of failed internet probes in a 5-minute average (long ranges).
        let loss: Double?

        var id: Double { t }
        var date: Date { Date(timeIntervalSince1970: t) }

        enum CodingKeys: String, CodingKey { case t, cpu, mem, disk, load, rx, tx, ping, dr, dw, loss }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            t = c.get(.t, 0)
            cpu = c.get(.cpu, 0)
            mem = c.get(.mem, 0)
            disk = c.get(.disk, 0)
            load = c.get(.load, 0)
            rx = c.get(.rx, 0)
            tx = c.get(.tx, 0)
            ping = c.opt(.ping)
            dr = c.opt(.dr)
            dw = c.opt(.dw)
            loss = c.opt(.loss)
        }
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
    /// Since 0.24 the server groups containers into apps and names each one so
    /// it can be told apart ("Authentik · Worker"). Older servers send none.
    let serverDisplayName: String?
    let role: String?
    let groupID: String?
    let groupName: String?

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

    /// What to show as the container's headline: the server's unique name
    /// ("Authentik · Worker"), else the catalog label ("Gitea"), else the name.
    var displayName: String {
        if let serverDisplayName, !serverDisplayName.isEmpty { return serverDisplayName }
        return service?.label ?? name
    }

    var isHealthy: Bool { health != "unhealthy" }

    /// Running but failing its health check, restarting, or dead: the states
    /// worth a second look.
    var hasProblem: Bool {
        (isRunning && health == "unhealthy") || state == "restarting" || state == "dead"
    }

    /// Containers are grouped by their compose project on the dashboard, the
    /// same way the web UI stacks them. Ones outside a project fall into a
    /// single bucket rather than each becoming its own one-item group.
    var stack: String { composeProject.isEmpty ? "Ungrouped" : composeProject }

    enum CodingKeys: String, CodingKey {
        case id, name, image, state, status, health, ports, created, service, role
        case composeProject = "compose_project"
        case composeService = "compose_service"
        case mountsDockerSock = "mounts_docker_sock"
        case serverDisplayName = "display_name"
        case groupID = "group_id"
        case groupName = "group_name"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = c.get(.name, "")
        image = c.get(.image, "")
        state = c.get(.state, "")
        status = c.get(.status, "")
        health = c.get(.health, "")
        ports = c.get(.ports, [])
        composeProject = c.get(.composeProject, "")
        composeService = c.get(.composeService, "")
        created = c.get(.created, 0)
        mountsDockerSock = c.get(.mountsDockerSock, false)
        service = c.opt(.service)
        serverDisplayName = c.opt(.serverDisplayName)
        role = c.opt(.role)
        groupID = c.opt(.groupID)
        groupName = c.opt(.groupName)
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
    // 0.24
    let finishedAt: String
    let exitCode: Int?
    let oomKilled: Bool
    let error: String
    let healthLog: [HealthProbe]
    let env: [EnvVar]
    let command: String
    let entrypoint: String
    let workingDir: String
    let user: String
    let networkDetails: [NetworkInfo]
    let networkMode: String
    let portBindings: [PortBinding]
    let resources: Resources?
    let logDriver: String
    let composeDir: String
    let composeFiles: String
    let imageID: String

    struct Mount: Decodable, Hashable {
        let source: String
        let dest: String
        let rw: Bool
        let type: String
        let name: String

        enum CodingKeys: String, CodingKey { case source, dest, rw, type, name }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            source = c.get(.source, "")
            dest = c.get(.dest, "")
            rw = c.get(.rw, true)
            type = c.get(.type, "")
            name = c.get(.name, "")
        }
    }

    struct HealthProbe: Decodable, Hashable {
        let start: String
        let exitCode: Int
        let output: String

        enum CodingKeys: String, CodingKey { case start, output; case exitCode = "exit_code" }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            start = c.get(.start, "")
            exitCode = c.get(.exitCode, 0)
            output = c.get(.output, "")
        }
    }

    struct EnvVar: Decodable, Hashable, Identifiable {
        let key: String
        let value: String
        /// The server masks anything that could be a credential.
        let secret: Bool
        var id: String { key }

        enum CodingKeys: String, CodingKey { case key, value, secret }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            key = c.get(.key, "")
            value = c.get(.value, "")
            secret = c.get(.secret, false)
        }
    }

    struct NetworkInfo: Decodable, Hashable, Identifiable {
        let name: String
        let ip: String
        let gateway: String
        let aliases: [String]
        var id: String { name }

        enum CodingKeys: String, CodingKey { case name, ip, gateway, aliases }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            name = c.get(.name, "")
            ip = c.get(.ip, "")
            gateway = c.get(.gateway, "")
            aliases = c.get(.aliases, [])
        }
    }

    struct PortBinding: Decodable, Hashable {
        let privatePort: Int
        let proto: String
        let publicPort: Int?
        let ip: String

        /// Published on every interface — reachable from the network.
        var isOpen: Bool { publicPort != nil && (ip.isEmpty || ip == "0.0.0.0" || ip == "::") }

        enum CodingKeys: String, CodingKey {
            case proto, ip
            case privatePort = "private"
            case publicPort = "public"
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            privatePort = c.get(.privatePort, 0)
            proto = c.get(.proto, "tcp")
            publicPort = c.opt(.publicPort)
            ip = c.get(.ip, "")
        }
    }

    struct Resources: Decodable, Hashable {
        let memoryLimit: Int64
        let cpus: Double
        let pidsLimit: Int

        enum CodingKeys: String, CodingKey {
            case cpus
            case memoryLimit = "memory_limit"
            case pidsLimit = "pids_limit"
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            memoryLimit = c.get(.memoryLimit, 0)
            cpus = c.get(.cpus, 0)
            pidsLimit = c.get(.pidsLimit, 0)
        }
    }

    enum CodingKeys: String, CodingKey {
        case id, name, image, state, health, mounts, networks, privileged, env, entrypoint, user
        case resources, error
        case restartCount = "restart_count"
        case restartPolicy = "restart_policy"
        case envCount = "env_count"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
        case exitCode = "exit_code"
        case oomKilled = "oom_killed"
        case healthLog = "health_log"
        case command = "cmd"
        case workingDir = "working_dir"
        case networkDetails = "network_details"
        case networkMode = "network_mode"
        case portBindings = "ports"
        case logDriver = "log_driver"
        case composeDir = "compose_dir"
        case composeFiles = "compose_files"
        case imageID = "image_id"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.get(.id, "")
        name = c.get(.name, "")
        image = c.get(.image, "")
        state = c.get(.state, "")
        health = c.opt(.health)
        restartCount = c.opt(.restartCount)
        restartPolicy = c.opt(.restartPolicy)
        privileged = c.opt(.privileged)
        envCount = c.opt(.envCount)
        startedAt = c.opt(.startedAt)
        mounts = c.opt(.mounts)
        networks = c.opt(.networks)
        finishedAt = c.get(.finishedAt, "")
        exitCode = c.opt(.exitCode)
        oomKilled = c.get(.oomKilled, false)
        error = c.get(.error, "")
        healthLog = c.get(.healthLog, [])
        env = c.get(.env, [])
        command = c.get(.command, "")
        entrypoint = c.get(.entrypoint, "")
        workingDir = c.get(.workingDir, "")
        user = c.get(.user, "")
        networkDetails = c.get(.networkDetails, [])
        networkMode = c.get(.networkMode, "")
        portBindings = c.get(.portBindings, [])
        resources = c.opt(.resources)
        logDriver = c.get(.logDriver, "")
        composeDir = c.get(.composeDir, "")
        composeFiles = c.get(.composeFiles, "")
        imageID = c.get(.imageID, "")
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
