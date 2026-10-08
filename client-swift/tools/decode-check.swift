import Foundation

// Decodes every captured server response into the app's models, on Linux, with
// a real Swift compiler.
//
// This is the closest thing to a test suite this project can run without a Mac:
// the models are the one place where a mistake is invisible at compile time and
// blanks a whole screen at runtime. The fixtures under tools/fixtures are real
// responses from a running server (the demo instance, so no secrets), captured
// with the same endpoints the app calls.
//
//   ./tools/decode-check.sh

let root = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "tools/fixtures"
var failures = 0
var checks = 0

func load(_ name: String) -> Data? {
    let url = URL(fileURLWithPath: "\(root)/\(name).json")
    guard let data = try? Data(contentsOf: url) else {
        print("  ?  \(name).json missing — skipped")
        return nil
    }
    return data
}

/// Decodes one fixture and runs an assertion over the result, so a model that
/// decodes into all-defaults (the failure mode lenient decoding introduces)
/// still fails the check.
func check<T: Decodable>(_ name: String, _ type: T.Type, _ assertion: (T) -> Bool = { _ in true }) {
    guard let data = load(name) else { return }
    checks += 1
    do {
        let value = try JSONDecoder().decode(T.self, from: data)
        if assertion(value) {
            print("  ✓  \(name) → \(T.self)")
        } else {
            print("  ✗  \(name) → \(T.self): decoded, but the content assertion failed")
            failures += 1
        }
    } catch {
        print("  ✗  \(name) → \(T.self): \(error)")
        failures += 1
    }
}

print("Decoding captured server responses:")

check("info", ServerInfo.self) { $0.helmsman && !$0.version.isEmpty }
check("me", MeResponse.self) { $0.ok && !$0.workspaces.isEmpty && !$0.hostname.isEmpty }
check("system", SystemSnapshot.self) { $0.cpuCount > 0 && $0.memory.total > 0 && $0.docker != nil }
check("containers", [Container].self) { !$0.isEmpty && $0.contains { !$0.ports.isEmpty } }
check("updates", UpdatesResponse.self) { !$0.pending.isEmpty && $0.pending.contains { $0.security } }
check("apps", AppsResponse.self) {
    $0.catalog.count > 10 && !$0.installed.isEmpty && $0.catalog.contains { !$0.fields.isEmpty }
}
check("reports", ReportsIndex.self) { !$0.reports.isEmpty && $0.config.intervalMin > 0 }
check("reports_latest", Report.self) {
    !$0.checks.isEmpty && $0.counts.total > 0 && $0.checks.contains { $0.recommendation != nil }
}
check("notifications", NotificationFeed.self) {
    !$0.items.isEmpty && $0.items.contains { $0.status == .crit }
}
check("audit", AuditFeed.self) { !$0.meta.actions.isEmpty }
check("snapshots", SnapshotList.self)
check("jobs", [JobStatus].self)
check("localai", LocalAIStatus.self) { !$0.installed.isEmpty && !$0.recommended.isEmpty && $0.ramGB > 0 }
check("clis", CLIList.self) { $0.clis.count >= 3 && $0.clis.contains { !$0.launch.isEmpty } }
check("chats", ChatIndex.self)
check("fs", FSListing.self) { !$0.roots.isEmpty }
check("users", ServerUsers.self) { !$0.users.isEmpty && !$0.identity.hostname.isEmpty }
check("identity", ServerIdentity.self) { !$0.hostname.isEmpty }
check("ai_models", AIModels.self)
check("ai_usage", AIUsage.self)
check("agent_tools", AgentToolList.self) { !$0.tools.isEmpty && $0.tools.contains { $0.safe } }
check("terminal_targets", TerminalTargets.self) { !$0.groups.isEmpty }
check("terminal_sessions", TerminalSessionList.self) { $0.maxLive > 0 }

// The three null-valued shapes a real server genuinely returns. A synthesised
// decoder throws on every one of them, and each blanks a screen the app needs.
print("Null-path fixtures:")

func inline<T: Decodable>(_ label: String, _ json: String, _ type: T.Type,
                          _ assertion: (T) -> Bool = { _ in true }) {
    checks += 1
    do {
        let value = try JSONDecoder().decode(T.self, from: Data(json.utf8))
        if assertion(value) {
            print("  ✓  \(label)")
        } else {
            print("  ✗  \(label): assertion failed")
            failures += 1
        }
    } catch {
        print("  ✗  \(label): \(error)")
        failures += 1
    }
}

inline("system with docker:null and net:null", """
{"hostname":"box","cpu_percent":3.5,"cpu_count":4,
 "memory":{"total":100,"used":50,"available":50,"percent":50.0},
 "disk":{"total":100,"used":10,"free":90,"percent":10.0},
 "load":[0.1,0.2,0.3],"uptime":900.0,"docker":null,"net":null}
""", SystemSnapshot.self) { $0.docker == nil && $0.net == nil && $0.disk.spare == 90 }

inline("net rates with ping:null", """
{"hostname":"box","cpu_percent":0,"cpu_count":1,
 "memory":{"total":1,"used":0,"percent":0},
 "disk":{"total":1,"used":0,"percent":0},
 "load":[],"uptime":0,"net":{"rx":10,"tx":20,"ping":null}}
""", SystemSnapshot.self) { $0.net?.ping == nil && $0.net?.rx == 10 }

inline("update entry missing every catalog field", """
[{"image":"redis:7","used_by":["cache"],"update_available":true}]
""", [DockerUpdate].self) { $0.first?.displayName == "redis:7" && $0.first?.security == false }

inline("report check without a recommendation", """
{"time":1,"duration":0.5,"trigger":"manual","score":"ok",
 "counts":{"ok":1,"info":0,"warn":0,"crit":0},
 "checks":[{"id":"a","group":"G","title":"T","icon":"","status":"ok","summary":"fine"}]}
""", Report.self) { $0.checks.first?.recommendation == nil && $0.score == .ok }

inline("me from an older server that lacks the AI fields", """
{"ok":true,"version":"0.19.0","demo":false,"hostname":"h","server_name":"s",
 "totp_enabled":false,"can_pair":true}
""", MeResponse.self) { !$0.aiConfigured && $0.reportConfig.intervalMin == 360 }

inline("status word this build has never seen", """
{"items":[{"id":"x","time":1,"source":"s","status":"emergency","title":"t","body":"b","count":1}],
 "unseen":1}
""", NotificationFeed.self) { $0.items.first?.status == .info }

// The chat protocol is parsed by hand rather than by Codable, so it needs its
// own coverage.
print("Chat protocol:")

func expect(_ label: String, _ condition: Bool) {
    checks += 1
    if condition {
        print("  ✓  \(label)")
    } else {
        print("  ✗  \(label)")
        failures += 1
    }
}

let snapshotFrame = """
{"type":"chat","id":"c1","title":"Disk check","running":true,"paused":false,
 "config":{"mode":"agent","provider":"anthropic","model":"claude-sonnet-5","workdir":"/srv","thinking":false},
 "plan":[{"title":"Look at disk","status":"done"},{"title":"Prune","status":"in_progress"}],
 "pause":{},
 "events":[{"t":"user","text":"why is disk full"},
           {"t":"assistant","text":"Checking."},
           {"t":"tool","name":"run_command","args":{"command":"df -h"},"output":"/dev/sda1 90%"}],
 "live":[{"type":"text","delta":"Almost "},{"type":"text","delta":"done."}]}
"""
if case .snapshot(let snap) = ChatProtocol.parse(snapshotFrame) {
    expect("snapshot replays history", snap.items.count == 4)
    expect("snapshot keeps the user ordinal", snap.items.first?.ordinal == 0)
    expect("snapshot carries the tool output", snap.items[2].tool?.output == "/dev/sda1 90%")
    expect("snapshot headline is the command", snap.items[2].tool?.headline == "df -h")
    expect("snapshot replays the live buffer", snap.items[3].text == "Almost done.")
    expect("snapshot reads the config", snap.config.mode == .agent && snap.config.workdir == "/srv")
    expect("snapshot reads the plan", snap.plan.count == 2 && snap.plan[1].active)
    expect("an empty pause object is no pause", snap.pause == nil)
} else {
    expect("snapshot frame parses", false)
}

if case .toolRequest(let call) = ChatProtocol.parse(
    #"{"type":"tool_request","id":"t1","name":"edit_file","args":{"path":"/srv/a.yml"}}"#) {
    expect("tool request is a write", call.isWrite && call.state == .requested)
    expect("tool request headline is the path", call.headline == "/srv/a.yml")
} else {
    expect("tool_request parses", false)
}

if case .runState(let running, let paused) = ChatProtocol.parse(
    #"{"type":"run_state","running":true,"paused":true}"#) {
    expect("run_state parses", running && paused)
} else {
    expect("run_state parses", false)
}

if case .failure(let message) = ChatProtocol.parse(#"{"type":"error","message":"no key"}"#) {
    expect("error carries its message", message == "no key")
} else {
    expect("error parses", false)
}

if case .unknown = ChatProtocol.parse(#"{"type":"invented_in_a_later_version"}"#) {
    expect("an unknown frame is ignored, not fatal", true)
} else {
    expect("unknown frame is ignored", false)
}

expect("garbage is not fatal", {
    if case .unknown = ChatProtocol.parse("<html>not json</html>") { return true }
    return false
}())

// Outgoing frames have to match what sessions.py switches on.
func field(_ json: String, _ key: String) -> Any? {
    guard let raw = try? JSONSerialization.jsonObject(with: Data(json.utf8)),
          let object = raw as? [String: Any] else { return nil }
    return object[key]
}

expect("user frame", field(ChatProtocol.user(text: "hi"), "type") as? String == "user")
expect("approve frame carries the call id",
       field(ChatProtocol.approve(callID: "t1", approved: true), "id") as? String == "t1")
expect("config frame sends the mode as a string",
       field(ChatProtocol.config(ChatConfig()), "mode") as? String == "agent")
expect("stop frame", field(ChatProtocol.stop(), "type") as? String == "stop")
expect("resume uses the server's word",
       field(ChatProtocol.resume(), "type") as? String == "continue")
expect("rewind carries the ordinal",
       field(ChatProtocol.rewind(ordinal: 2, text: "edited"), "ordinal") as? Int == 2)

// URL derivation for the two WebSocket endpoints.
print("WebSocket URLs:")

let httpsClient = APIClient(baseURL: URL(string: "https://pocket.example.com")!, token: "t")
let wss = httpsClient.webSocketURL(path: "/ws/chat",
                                   credential: URLQueryItem(name: "ticket", value: "abc"))
expect("https becomes wss", wss?.scheme == "wss")
expect("the single-use ticket rides as a query item",
       wss?.absoluteString.contains("ticket=abc") == true)
expect("the long-lived token is not in a ticket URL",
       wss?.absoluteString.contains("token=") == false)

let httpClient = APIClient(baseURL: URL(string: "http://192.168.1.10:8090")!, token: "t")
let ws = httpClient.webSocketURL(path: "/ws/terminal",
                                 credential: URLQueryItem(name: "ticket", value: "abc"),
                                 extra: [URLQueryItem(name: "session", value: "s1")])
expect("http becomes ws", ws?.scheme == "ws")
expect("the port survives", ws?.port == 8090)
expect("extra query items survive", ws?.absoluteString.contains("session=s1") == true)

// Pairing QR codes: every encoding any PocketADM client or installer prints.
print("Pairing payloads:")
let installer = PairingPayload(scanned: "https://203.0.113.10:8443/?pair=C0DE&fp=KEYfp_-")
expect("installer link: server", installer?.serverURL.absoluteString == "https://203.0.113.10:8443")
expect("installer link: code", installer?.code == "C0DE")
expect("installer link: fingerprint", installer?.fingerprint == "KEYfp_-")
let oldApp = PairingPayload(scanned: "https://box.example.com/pair?code=C0DE")
expect("pre-2.0 app link", oldApp?.code == "C0DE" && oldApp?.fingerprint == nil
       && oldApp?.serverURL.absoluteString == "https://box.example.com")
let json = PairingPayload(scanned: #"{"h":"pair","u":"https://box.example.com:8090/x","c":"C0DE"}"#)
expect("pre-0.23 web JSON", json?.code == "C0DE"
       && json?.serverURL.absoluteString == "https://box.example.com:8090")
let handoff = PairingPayload(scanned: "https://box.example.com/?pair=C0DE&c=chat-1")
expect("handoff link pairs too", handoff?.code == "C0DE")
expect("a fingerprint on plain http is ignored",
       PairingPayload(scanned: "http://192.168.1.10:8090/?pair=C&fp=K")?.fingerprint == nil)
expect("foreign QR codes are refused", PairingPayload(scanned: "https://example.com/?x=1") == nil)
expect("other schemes are refused", PairingPayload(scanned: "ftp://box/?pair=C") == nil)
expect("plain text is refused", PairingPayload(scanned: "hello") == nil)
let link = PairingPayload.link(serverURL: URL(string: "https://203.0.113.10:8443")!,
                               code: "C0DE", fingerprint: "KEY")
expect("this app prints the shared format", link == "https://203.0.113.10:8443/?pair=C0DE&fp=KEY")
expect("what it prints, it reads", PairingPayload(scanned: link)?.fingerprint == "KEY")

// Address normalisation from the Connect screen.
print("Address normalisation:")
expect("a bare host tries https first",
       ServerURL.candidates(from: "box.example.com").first?.scheme == "https")
expect("a bare host falls back to http",
       ServerURL.candidates(from: "box.example.com").last?.scheme == "http")
expect("an explicit scheme is taken as given",
       ServerURL.candidates(from: "http://192.168.1.10:8090").count == 1)
expect("a trailing slash is dropped",
       ServerURL.candidates(from: "box.example.com/").first?.absoluteString == "https://box.example.com")
expect("empty input yields nothing", ServerURL.candidates(from: "   ").isEmpty)

// Server 0.24: apps, the live container detail, drives, activity, the watch,
// AI accounts and sign-in. Captured from the demo instance.
print("Server 0.24 responses:")
check("me_v2", MeResponse.self) { $0.supports("services") && $0.supports("watch") && $0.watchEnabled }
check("containers_v2", [Container].self) {
    $0.allSatisfy { $0.groupID != nil && $0.role != nil } && Set($0.map(\.displayName)).count == $0.count
}
check("services", ServicesResponse.self) {
    $0.groups.contains { $0.total > 1 && $0.containers.count == $0.total && !$0.iconNames.isEmpty }
        && $0.groups.contains { $0.state == "stopped" }
}
check("container_detail_v2", ContainerDetail.self) {
    $0.env.contains { $0.secret && $0.value != "never-shown" } && !$0.networkDetails.isEmpty
        && $0.resources != nil && !$0.composeDir.isEmpty
}
check("container_stats_live", ContainerStats.self) { $0.memLimit > 0 && $0.netRxRate != nil && $0.pids > 0 }
check("container_top", ContainerTop.self) {
    !$0.processes.isEmpty && !$0.value($0.processes[0], "COMMAND", "CMD").isEmpty
}
check("container_events", ContainerEventList.self) { !$0.events.isEmpty && $0.events[0].t > 0 }
check("storage", StorageResponse.self) {
    $0.filesystems.contains { $0.external && $0.total > 0 } && $0.filesystems.contains { $0.mount == "/" }
}
check("activity", ActivityFeed.self) {
    !$0.events.isEmpty && !$0.categories.isEmpty && $0.events.contains { $0.severity == .warn }
}
check("watch", WatchStatus.self) {
    $0.settings.enabled && !$0.route.label.isEmpty && !$0.runs.isEmpty && $0.settings.quietStart == "23:00"
}
check("ai_accounts", AIAccounts.self) {
    $0.accounts.contains { $0.signedIn && $0.canSubscribe } && $0.routes["watch"] != nil
        && $0.accounts.contains { $0.id == "mistral" && $0.engine == "mistral-vibe" }
}
check("reports_latest_v2", Report.self) {
    $0.points != nil && $0.checks.contains { !$0.actions.isEmpty && !$0.explain.isEmpty }
        && $0.checks.allSatisfy { !$0.category.isEmpty }
}
check("notifications_v2", NotificationFeed.self) {
    $0.items.contains { $0.isWatch && !$0.actions.isEmpty && !$0.importance.isEmpty }
}
check("chats_v2", ChatIndex.self) { $0.chats.allSatisfy { !$0.preview.isEmpty || $0.messageCount == 0 } }
check("fs_v2", FSListing.self) {
    $0.hidden >= 1 && $0.fileEntries.contains { $0.name == ".env" } && !$0.display.isEmpty
        && $0.fileEntries.allSatisfy { $0.modified > 0 && !$0.mode.isEmpty }
}
check("metrics_history", MetricsHistory.self)
check("update_detail_v2", UpdateDetail.self) { !$0.impact.isEmpty && !$0.label.isEmpty }
check("signin_flow", SignInFlowEnvelope.self) { $0.flow.state == "waiting_code" && !$0.flow.url.isEmpty }

// Older servers keep working: a container without the 0.24 fields still
// decodes, and its display name falls back to the catalog label.
inline("container from 0.23", """
    {"id":"abc","name":"nextcloud","image":"nextcloud:29","state":"running","status":"Up",
     "health":"","ports":[],"compose_project":"cloud","compose_service":"app","created":0,
     "mounts_docker_sock":false,"service":{"label":"Nextcloud","icon":"","category":"Files"}}
    """, Container.self) { $0.displayName == "Nextcloud" && $0.groupID == nil }

// The transcript folds runs of tool calls into one group.
print("Chat timeline:")
func toolItem(_ name: String, _ state: ToolCall.State = .finished) -> ChatItem {
    var call = ToolCall(callID: UUID().uuidString, name: name, headline: "x", detail: "")
    call.state = state
    return ChatItem(id: call.callID, kind: .tool, text: name, tool: call)
}
let userItem = ChatItem(kind: .user, text: "why?")
let answer = ChatItem(kind: .assistant, text: "Because.")
let thought = ChatItem(kind: .thinking, text: "hmm")
let folded = ChatTimeline.rows([userItem, toolItem("run_command"), thought, toolItem("run_command"),
                                toolItem("read_file"), thought, answer])
expect("a run of tools becomes one group between the question and the answer",
       folded.count == 4 && { if case .tools(let g) = folded[1] { return g.calls.count == 3 } else { return false } }())
expect("reasoning after the last call stays outside the group",
       { if case .item(let i) = folded[2] { return i.kind == .thinking } else { return false } }())
if case .tools(let g) = folded[1] {
    expect("the group says what was done", g.summary == "Ran 2 commands · read 1 file")
}
let single = ChatTimeline.rows([userItem, toolItem("run_command"), answer])
expect("a single call stays a card", single.count == 3)
let waiting = ChatTimeline.rows([toolItem("run_command"), toolItem("run_command", .requested)])
expect("a call waiting for approval is never folded away", waiting.count == 2)
let running = ChatTimeline.rows([toolItem("read_file"), toolItem("run_command", .running)])
if case .tools(let g) = running.first {
    expect("a running group shows its running call", g.isRunning && g.current?.name == "run_command")
} else {
    expect("a running run is still a group", false)
}
let progress = PlanProgress([PlanStep(title: "a", status: "done"), PlanStep(title: "b", status: "in_progress"),
                             PlanStep(title: "c", status: "pending")])
expect("plan progress names the active step", progress.done == 1 && progress.total == 3 && progress.current == "b")
expect("activity stream lines decode",
       ActivityEvent.fromStreamLine("data: {\"id\":\"a\",\"t\":1,\"category\":\"security\",\"kind\":\"ssh.login\",\"title\":\"x\",\"severity\":\"warn\"}")?.severity == .warn
       && ActivityEvent.fromStreamLine(": ping") == nil)

print("")
if failures == 0 {
    print("✓ \(checks) checks passed")
    exit(0)
} else {
    print("✗ \(failures) of \(checks) checks failed")
    exit(1)
}
