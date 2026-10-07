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
    /// Bound to the stack so opening a target can push straight into the new
    /// session instead of making you tap it again in the list.
    @State private var path = NavigationPath()

    var body: some View {
        NavigationStack(path: $path) {
            Group {
                if !loaded {
                    ProgressView().tint(Theme.accent).frame(maxWidth: .infinity, maxHeight: .infinity)
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
            .screenBackground()
            .navigationDestination(for: TerminalSession.self) { session in
                TerminalSessionView(session: session)
            }
        }
        .task { await load() }
    }

    private func list(_ targets: TerminalTargets) -> some View {
        List {
            if !sessions.isEmpty {
                Section {
                    ForEach(sessions) { session in
                        NavigationLink(value: session) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(session.title.isEmpty ? session.context : session.title)
                                    .font(.subheadline.weight(.medium))
                                    .foregroundStyle(Theme.text)
                                Text(session.alive
                                     ? "active · \(session.clients) attached"
                                     : "ended")
                                    .font(.caption)
                                    .foregroundStyle(session.alive ? Theme.accent2 : Theme.muted)
                            }
                        }
                        .listRowBackground(Theme.bg2)
                    }
                    .onDelete { offsets in
                        Task { await close(offsets.map { sessions[$0] }) }
                    }
                } header: {
                    // Sessions live on the server, so this is genuinely "still
                    // running", not "recently viewed".
                    Text("RUNNING SESSIONS")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(Theme.muted)
                }
            }

            ForEach(targets.groups) { group in
                Section {
                    ForEach(group.targets) { target in
                        Button {
                            Task { await open(target) }
                        } label: {
                            HStack(spacing: 12) {
                                icon(for: target).frame(width: 26)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(target.label)
                                        .font(.subheadline.weight(.medium))
                                        .foregroundStyle(Theme.text)
                                    if let sub = target.sub, !sub.isEmpty {
                                        Text(sub)
                                            .font(.caption)
                                            .foregroundStyle(Theme.muted)
                                            .lineLimit(1)
                                    }
                                }
                                Spacer()
                                if opening == target.id {
                                    ProgressView().tint(Theme.muted)
                                } else {
                                    Image(systemName: "chevron.right")
                                        .font(.caption.weight(.semibold))
                                        .foregroundStyle(Theme.muted)
                                }
                            }
                        }
                        .listRowBackground(Theme.bg2)
                    }
                } header: {
                    Text(group.label.uppercased())
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(Theme.muted)
                }
            }
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
        .background(Theme.bg)
        .refreshable { await load() }
    }

    @ViewBuilder
    private func icon(for target: TerminalTargets.Target) -> some View {
        // The server sends either an SF Symbol name ("box") or an emoji.
        // Emoji have no symbol of that name, so the fallback matters.
        if let raw = target.icon, !raw.isEmpty {
            if UIImage(systemName: raw) != nil {
                Image(systemName: raw).foregroundStyle(Theme.accent)
            } else {
                Text(raw).font(.title3)
            }
        } else {
            Image(systemName: "terminal").foregroundStyle(Theme.accent)
        }
    }

    // MARK: - Actions

    private func load() async {
        guard let client = app.client else { return }
        do {
            async let targets = client.terminalTargets()
            async let sessions = client.terminalSessions()
            self.targets = try await targets
            self.sessions = try await sessions.sessions
            error = nil
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
        loaded = true
    }

    private func open(_ target: TerminalTargets.Target) async {
        guard let client = app.client, opening == nil else { return }
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
            .toolbarBackground(Theme.bg2, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    switch socket.status {
                    case .connected: StatusPill(text: "live", tint: Theme.accent2)
                    case .connecting: ProgressView().tint(Theme.muted)
                    case .closed:    StatusPill(text: "closed", tint: Theme.danger)
                    case .idle:      EmptyView()
                    }
                }
            }
            .onAppear { connect() }
            .onDisappear { socket.disconnect() }
    }

    private func connect() {
        guard let client = app.client,
              let token = app.currentToken,
              let url = client.webSocketURL(
                  path: "/ws/terminal",
                  token: token,
                  extra: [URLQueryItem(name: "session", value: session.id)]
              )
        else { return }

        socket.onOutput = { text in
            terminal.view?.feed(text: text)
        }
        socket.connect(to: url)
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
        DispatchQueue.main.async { _ = view.becomeFirstResponder() }
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
