import SwiftTerm
import SwiftUI
import UIKit

/// Picks *where* to open a shell. The server already groups and labels the
/// targets (`/api/terminal/targets`), so this screen renders its answer rather
/// than inventing its own taxonomy.
struct TerminalHomeView: View {
    @EnvironmentObject private var app: AppState

    @State private var targets: TerminalTargets?
    @State private var sessions: [TerminalSession] = []
    @State private var error: String?
    @State private var loaded = false
    @State private var opening: String?
    /// Container id -> its app (server 0.24+), to list shells by app.
    @State private var apps: [String: AppGroup] = [:]
    @State private var search = ""
    /// Bound to the stack so opening a target can push straight into the new
    /// session instead of making you tap it again in the list.
    @State private var path = NavigationPath()

    var body: some View {
        NavigationStack(path: $path) {
            Group {
                if !loaded {
                    ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let targets {
                    list(targets)
                } else {
                    MessageState(
                        symbol: "exclamationmark.triangle",
                        title: "Cannot reach the terminal",
                        message: error,
                        tint: Theme.danger,
                        retry: { Task { await load() } }
                    )
                }
            }
            .navigationTitle("Terminal")
            .navigationBarTitleDisplayMode(.large)
            .searchable(text: $search, prompt: "Find a shell")
            .navigationDestination(for: TerminalSession.self) { session in
                TerminalSessionView(session: session)
            }
        }
        .task {
            await load()
            // Screenshot runs: `-PocketADMScreenshotRoute session` opens a shell.
            if AppState.screenshotTab == MainTab.terminal.rawValue, AppState.screenshotRoute == "session",
               path.isEmpty, let first = targets?.groups.first?.targets.first {
                await open(first)
            }
        }
    }

    private func list(_ targets: TerminalTargets) -> some View {
        List {
            if !sessions.isEmpty {
                // Sessions live on the server, so this is genuinely "still
                // running", not "recently viewed".
                Section("Open sessions") {
                    ForEach(sessions) { session in
                        NavigationLink(value: session) {
                            HStack(spacing: 14) {
                                IconTile(symbol: "terminal.fill",
                                         color: session.alive ? .green : Color(uiColor: .systemGray))
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(session.title.isEmpty ? session.context : session.title)
                                        .foregroundStyle(Theme.text)
                                    Text(session.alive
                                         ? "Active · \(session.clients) attached"
                                         : "Ended")
                                        .font(.footnote)
                                        .foregroundStyle(Theme.muted)
                                }
                            }
                        }
                    }
                    .onDelete { offsets in
                        Task { await close(offsets.map { sessions[$0] }) }
                    }
                }
            }

            ForEach(targets.groups) { group in
                if !apps.isEmpty && group.targets.contains(where: { $0.container == true }) {
                    appSections(group)
                } else {
                    let shown = group.targets.filter(matches)
                    if !shown.isEmpty {
                        Section(group.label) {
                            ForEach(shown) { target in targetRow(target) }
                        }
                    }
                }
            }
        }
        .refreshable { await load() }
    }

    private func matches(_ target: TerminalTargets.Target) -> Bool {
        guard !search.isEmpty else { return true }
        let app = apps[containerID(target)]?.name ?? ""
        return target.label.localizedCaseInsensitiveContains(search)
            || (target.sub ?? "").localizedCaseInsensitiveContains(search)
            || app.localizedCaseInsensitiveContains(search)
    }

    private func containerID(_ target: TerminalTargets.Target) -> String {
        target.id.hasPrefix("container:") ? String(target.id.dropFirst("container:".count)) : ""
    }

    /// Service containers by app: an app with one container is one row, an
    /// app with several opens into its parts.
    @ViewBuilder
    private func appSections(_ group: TerminalTargets.Group) -> some View {
        let shown = group.targets.filter(matches)
        let byApp = Dictionary(grouping: shown) { apps[containerID($0)]?.name ?? $0.label }
        let names = byApp.keys.sorted { $0.localizedStandardCompare($1) == .orderedAscending }
        if !names.isEmpty {
            Section {
                ForEach(names, id: \.self) { name in
                    let members = byApp[name] ?? []
                    if members.count == 1, let only = members.first {
                        targetRow(only, title: name)
                    } else {
                        DisclosureGroup {
                            ForEach(members) { target in targetRow(target, title: roleTitle(target)) }
                        } label: {
                            HStack(spacing: 14) {
                                ServiceIcon(names: apps[containerID(members[0])]?.iconNames ?? [name])
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(name).foregroundStyle(Theme.text)
                                    Text("\(members.count) containers")
                                        .font(.footnote)
                                        .foregroundStyle(Theme.muted)
                                }
                            }
                        }
                    }
                }
            } header: {
                Text("Apps")
            } footer: {
                Text("A shell inside a container sees only that container — its files, its processes.")
            }
        }
    }

    private func roleTitle(_ target: TerminalTargets.Target) -> String {
        let id = containerID(target)
        guard let group = apps[id], let container = group.containers.first(where: { $0.id == id }) else {
            return target.label
        }
        if let role = container.role, role != "App" { return role }
        return container.displayName
    }

    private func targetRow(_ target: TerminalTargets.Target, title: String? = nil) -> some View {
        Button {
            Task { await open(target) }
        } label: {
            HStack(spacing: 14) {
                icon(for: target)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title ?? target.label)
                        .foregroundStyle(Theme.text)
                    if let sub = target.sub, !sub.isEmpty {
                        Text(sub)
                            .font(.footnote)
                            .foregroundStyle(Theme.muted)
                            .lineLimit(1)
                    }
                }
                Spacer(minLength: 8)
                if opening == target.id {
                    ProgressView()
                } else {
                    Image(systemName: "chevron.right")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(Color(uiColor: .tertiaryLabel))
                }
            }
        }
    }

    /// The app's own shell gets the PocketADM icon, containers their brand
    /// mark — the server's own icon names and emoji are never drawn as text.
    @ViewBuilder
    private func icon(for target: TerminalTargets.Target) -> some View {
        if target.container == true || ServiceIcon.isPocketADM(target.label) {
            ServiceIcon(names: [target.label, target.sub ?? ""])
        } else {
            IconTile(symbol: ServerSymbol.sfSymbol(for: target.icon ?? "") ?? "terminal.fill",
                     color: .gray, size: 34)
        }
    }

    // MARK: - Actions

    private func load() async {
        guard let client = app.client else { return }
        if app.me == nil { await app.refreshMe() }
        do {
            async let targets = client.terminalTargets()
            async let sessions = client.terminalSessions()
            self.targets = try await targets
            self.sessions = try await sessions.sessions
            error = nil
            if app.supports("services"), let groups = try? await client.services() {
                var map: [String: AppGroup] = [:]
                for group in groups {
                    for container in group.containers { map[container.id] = group }
                }
                apps = map
            }
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
        loaded = true
    }

    private func open(_ target: TerminalTargets.Target) async {
        guard opening == nil else { return }
        // The public demo is read-only and has no host to open a shell on: it
        // serves a simulated shell on the socket itself, so there is no
        // session to create — asking for one is refused.
        if app.me?.demo == true || app.serverInfo?.demo == true {
            let now = Date().timeIntervalSince1970
            path.append(TerminalSession(id: "demo-\(target.id)", title: target.label, context: target.id,
                                        created: now, lastActive: now, alive: true, clients: 1))
            return
        }
        guard let client = app.client else { return }
        opening = target.id
        defer { opening = nil }
        do {
            let session = try await client.createTerminalSession(context: target.id,
                                                                 title: target.label)
            sessions.insert(session, at: 0)
            path.append(session)
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
    }

    private func close(_ doomed: [TerminalSession]) async {
        guard let client = app.client else { return }
        for session in doomed {
            try? await client.closeTerminalSession(session.id)
        }
        await load()
    }

}

/// A live PTY. The bytes on `/ws/terminal` are a real terminal stream, so this
/// hands them to a real emulator — anything less renders escape sequences as
/// garbage the first time a colored prompt or `htop` shows up.
struct TerminalSessionView: View {
    let session: TerminalSession

    @EnvironmentObject private var app: AppState
    @StateObject private var socket = TerminalSocket()
    @State private var terminal = TerminalHost()

    var body: some View {
        SwiftTermView(host: terminal, socket: socket)
            // The emulator paints its own background; letting it run under the
            // home indicator keeps the scrollback from ending in a grey band,
            // while the keyboard accessory bar stays inside the safe area
            // because UIKit positions input accessories for us.
            .ignoresSafeArea(.container, edges: .bottom)
            .background(Theme.termBg.ignoresSafeArea())
            .navigationTitle(session.title.isEmpty ? session.context : session.title)
            .navigationBarTitleDisplayMode(.inline)
            // A terminal is dark in either appearance, and so is its bar.
            .toolbarBackground(Theme.termBg, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar(.hidden, for: .tabBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    switch socket.status {
                    case .connected:
                        Image(systemName: "circle.fill")
                            .font(.system(size: 9))
                            .foregroundStyle(.green)
                            .accessibilityLabel("Connected")
                    case .connecting:
                        ProgressView()
                    case .closed:
                        Image(systemName: "bolt.horizontal.circle")
                            .foregroundStyle(.red)
                            .accessibilityLabel("Disconnected")
                    case .idle:
                        EmptyView()
                    }
                }
            }
            .onAppear { connect() }
            .onDisappear { socket.disconnect() }
            .onChange(of: socket.status) { _, status in
                // Screenshot runs show a terminal with something in it —
                // commands whose output fits a phone's width.
                guard status == .connected, AppState.screenshotRoute == "session" else { return }
                Task {
                    for command in ["uname -a", "free", "df -h", "docker images"] {
                        try? await Task.sleep(for: .milliseconds(700))
                        socket.send(input: command + "\r")
                    }
                }
            }
    }

    private func connect() {
        guard let client = app.client else { return }
        socket.onOutput = { text in
            terminal.view?.feed(text: text)
        }
        Task {
            do {
                // a single-use ticket, fetched per connect (APIClient.liveWebSocketURL)
                let url = try await client.liveWebSocketURL(
                    path: "/ws/terminal",
                    extra: [URLQueryItem(name: "session", value: session.id)])
                socket.connect(to: url)
            } catch {
                socket.fail(error.localizedDescription)
                app.handle(error)
            }
        }
    }
}

/// Holds the emulator across SwiftUI re-renders. A `@State` struct body is
/// rebuilt constantly; without a stable box the terminal would be recreated
/// and the scrollback thrown away mid-session.
final class TerminalHost {
    weak var view: SwiftTerm.TerminalView?
}

struct SwiftTermView: UIViewRepresentable {
    let host: TerminalHost
    let socket: TerminalSocket

    func makeCoordinator() -> Coordinator { Coordinator(socket: socket) }

    func makeUIView(context: Context) -> SwiftTerm.TerminalView {
        let view = SwiftTerm.TerminalView(frame: .zero)
        view.terminalDelegate = context.coordinator
        view.nativeForegroundColor = UIColor(Theme.termFg)
        // The backdrop has to go on the layer, not `nativeBackgroundColor`.
        // SwiftTerm keeps that one clear on purpose and paints glyph cells with
        // a transparent backdrop so the layer colour shows through the gaps;
        // giving it an opaque colour instead breaks that compositing and leaves
        // striped garbage behind while scrolling.
        view.layer.backgroundColor = UIColor(Theme.termBg).cgColor

        // A phone keyboard has no Esc, Ctrl or arrows; SwiftTerm's own input
        // accessory supplies them, and UIKit floats it above the keyboard and
        // clear of the home indicator without any manual inset maths.
        //
        // Deferred: a view that is not in the window hierarchy yet cannot
        // become first responder, and makeUIView runs before it is installed.
        // (Not in screenshot runs: a fresh simulator's keyboard opens with a
        // tutorial card over half the screen.)
        if AppState.screenshotRoute != "session" {
            DispatchQueue.main.async { _ = view.becomeFirstResponder() }
        }
        host.view = view
        return view
    }

    func updateUIView(_ view: SwiftTerm.TerminalView, context: Context) {}

    static func dismantleUIView(_ view: SwiftTerm.TerminalView, coordinator: Coordinator) {
        view.resignFirstResponder()
    }

    final class Coordinator: NSObject, TerminalViewDelegate {
        private let socket: TerminalSocket

        init(socket: TerminalSocket) { self.socket = socket }

        /// Keystrokes leave the emulator as raw PTY bytes.
        func send(source: SwiftTerm.TerminalView, data: ArraySlice<UInt8>) {
            guard let text = String(bytes: data, encoding: .utf8) else { return }
            Task { @MainActor in socket.send(input: text) }
        }

        /// Rotating the phone or opening the keyboard changes the grid; the PTY
        /// has to be told, or full-screen programs keep drawing at the old size.
        func sizeChanged(source: SwiftTerm.TerminalView, newCols: Int, newRows: Int) {
            Task { @MainActor in socket.send(cols: newCols, rows: newRows) }
        }

        func setTerminalTitle(source: SwiftTerm.TerminalView, title: String) {}
        func hostCurrentDirectoryUpdate(source: SwiftTerm.TerminalView, directory: String?) {}
        func scrolled(source: SwiftTerm.TerminalView, position: Double) {}
        func rangeChanged(source: SwiftTerm.TerminalView, startY: Int, endY: Int) {}

        func requestOpenLink(source: SwiftTerm.TerminalView, link: String, params: [String: String]) {
            guard let url = URL(string: link), UIApplication.shared.canOpenURL(url) else { return }
            UIApplication.shared.open(url)
        }
    }
}
