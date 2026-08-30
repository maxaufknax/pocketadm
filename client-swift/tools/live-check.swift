import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// Drives the real APIClient against a real PocketADM server.
//
// decode-check proves the models match captured JSON. This proves the *client*
// matches the server: a mistyped path, a wrong query parameter or a body field
// FastAPI silently ignores all decode perfectly against a fixture and fail only
// on a device. Every call here goes through the same actor the app uses.
//
// Read-only by design — it is meant to be pointed at a live box.
//
//   ./tools/live-check.sh http://127.0.0.1:8091 demo

let arguments = CommandLine.arguments
guard arguments.count >= 3, let base = URL(string: arguments[1]) else {
    print("usage: live-check <base-url> <password> [totp]")
    exit(2)
}
let password = arguments[2]
let totp = arguments.count > 3 ? arguments[3] : ""

var passed = 0
var failed = 0

@MainActor
func report(_ label: String, _ detail: String) {
    passed += 1
    print("  ✓  \(label): \(detail)")
}

@MainActor
func fail(_ label: String, _ error: Error) {
    failed += 1
    print("  ✗  \(label): \(error)")
}

/// Runs one call, reporting a one-line summary of what actually came back.
/// Summarising rather than just "ok" is the point: a 200 with an empty body
/// decodes fine and tells you nothing.
@MainActor
func probe<T>(_ label: String, _ call: () async throws -> T,
              _ describe: (T) -> String) async {
    do {
        report(label, describe(try await call()))
    } catch {
        fail(label, error)
    }
}

@MainActor
func run() async {
    print("Probing \(base.absoluteString)")

    let anonymous = APIClient(baseURL: base)

    var token = ""
    do {
        let info = try await anonymous.info()
        report("GET /api/info", "\(info.serverName) · \(info.version)\(info.demo ? " · demo" : "")")
        let session = try await anonymous.login(password: password, totp: totp)
        token = session.token
        report("POST /api/login", "token issued (\(session.token.count) chars)")
    } catch {
        fail("sign in", error)
        print("\ncannot continue without a token")
        exit(1)
    }

    let client = APIClient(baseURL: base, token: token)

    await probe("GET /api/me", { try await client.me() }) {
        "\($0.hostname) · AI \($0.aiConfigured ? "on" : "off") · \($0.workspaces.count) workspaces"
    }
    await probe("GET /api/system", { try await client.system() }) {
        "cpu \(Int($0.cpuPercent))% · mem \(Int($0.memory.percent))% · docker \($0.docker?.running ?? 0) running"
    }
    await probe("GET /api/metrics/history", { try await client.metricsHistory(minutes: 60) }) {
        "\($0.points.count) points"
    }

    var firstContainer: Container?
    await probe("GET /api/containers", { try await client.containers() }) {
        firstContainer = $0.first
        return "\($0.count) containers in \(Set($0.map(\.stack)).count) stacks"
    }
    if let container = firstContainer {
        await probe("GET /api/containers/{id}/detail",
                    { try await client.containerDetail(container.id) }) {
            "\($0.name) · \($0.mounts?.count ?? 0) mounts · \($0.networks?.count ?? 0) networks"
        }
        await probe("GET /api/containers/{id}/logs",
                    { try await client.containerLogs(container.id, tail: 20) }) {
            "\($0.components(separatedBy: "\n").count) lines"
        }
        if container.isRunning {
            await probe("GET /api/containers/{id}/stats",
                        { try await client.containerStats(container.id) }) {
                "cpu \($0.cpuPercent)% · mem \(Fmt.bytes($0.memUsage))"
            }
        }
    }

    var firstUpdate: DockerUpdate?
    await probe("GET /api/updates", { try await client.updates() }) {
        firstUpdate = $0.pending.first
        return "\($0.pending.count) pending · \($0.upToDate.count) current · apt \($0.apt.packages.count)"
    }
    if let update = firstUpdate {
        await probe("GET /api/updates/detail",
                    { try await client.updateDetail(image: update.image) }) {
            "\($0.image) · \($0.releases.count) releases"
        }
    }

    await probe("GET /api/snapshots", { try await client.snapshots() }) { "\($0.count) snapshots" }
    await probe("GET /api/jobs", { try await client.jobs() }) { "\($0.count) jobs" }
    await probe("GET /api/apps", { try await client.apps() }) {
        "\($0.catalog.count) in catalog · \($0.installed.count) installed"
    }
    await probe("GET /api/reports", { try await client.reports() }) {
        "\($0.reports.count) reports · every \($0.config.intervalMin) min"
    }
    await probe("GET /api/reports/latest", { try await client.latestReport() }) {
        "\($0.score.rawValue) · \($0.checks.count) checks in \($0.groups.count) groups"
    }
    await probe("GET /api/notifications", { try await client.notifications() }) {
        "\($0.items.count) items · \($0.unseen) unseen"
    }
    await probe("GET /api/audit", { try await client.audit(limit: 20) }) {
        "\($0.events.count) events · \($0.meta.actions.count) known actions"
    }
    await probe("GET /api/localai/status", { try await client.localAI() }) {
        "\($0.running ? "running" : "stopped") · \($0.installed.count) models"
    }
    await probe("GET /api/clis", { try await client.codingCLIs() }) { "\($0.count) tools" }
    await probe("GET /api/fs", { try await client.listDirectory("") }) {
        "\($0.roots.count) roots · \($0.dirs.count) entries"
    }
    await probe("GET /api/server/users", { try await client.serverUsers() }) {
        "\($0.users.count) accounts · manage \($0.canManage)"
    }
    await probe("GET /api/server/identity", { try await client.serverIdentity() }) { $0.os }
    await probe("GET /api/ai/models", { try await client.aiModels() }) {
        "\($0.providers.count) providers"
    }
    await probe("GET /api/ai/usage", { try await client.aiUsage() }) {
        "today \($0.today.requests) requests"
    }
    await probe("GET /api/agent/tools", { try await client.agentTools() }) { "\($0.count) tools" }
    await probe("GET /api/agent/memory", { try await client.agentMemory() }) { "\($0.count) chars" }
    await probe("GET /api/agent/instructions",
                { try await client.agentInstructions() }) { "\($0.count) chars" }
    await probe("GET /api/chats", { try await client.chats() }) { "\($0.count) chats" }
    await probe("GET /api/terminal/targets", { try await client.terminalTargets() }) {
        "\($0.groups.count) groups · \($0.groups.reduce(0) { $0 + $1.targets.count }) targets"
    }
    await probe("GET /api/terminal/sessions", { try await client.terminalSessions() }) {
        "\($0.sessions.count) live · max \($0.maxLive)"
    }
    await probe("GET /api/settings/2fa/setup", { try await client.totpSetup() }) {
        "secret \($0.secret.count) chars · uri \($0.uri.hasPrefix("otpauth://") ? "ok" : "unexpected")"
    }

    print("")
    if failed == 0 {
        print("✓ \(passed) endpoints answered as the app expects")
    } else {
        print("✗ \(failed) of \(passed + failed) endpoints failed")
    }
    exit(failed == 0 ? 0 : 1)
}

await run()
