import SwiftUI
import UIKit

/// The agent screen: a conversation that can actually operate the server.
///
/// The session lives on the server, so this view is a window onto it rather
/// than its owner — closing the app does not stop a run, and reopening replays
/// what happened meanwhile.
struct ChatView: View {
    @EnvironmentObject private var app: AppState
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var socket = ChatSocket()

    @State private var draft = ""
    @State private var showSettings = false
    @State private var showChats = false
    @State private var models: AIModels?
    /// The user message being edited: resending it rewinds the chat to there.
    @State private var editing: ChatItem?
    @State private var renaming = false
    @State private var newTitle = ""
    @State private var shareText: ShareText?
    @State private var toast: Toast?
    @FocusState private var composerFocused: Bool

    var body: some View {
        NavigationStack {
            Group {
                // The demo has no AI key on purpose, but it carries a recorded
                // agent session: show that instead of a setup prompt.
                if app.me?.aiConfigured == false && app.me?.demo != true {
                    notConfigured
                } else {
                    transcript
                }
            }
            .navigationTitle(socket.title.isEmpty ? "Assistant" : socket.title)
            .navigationBarTitleDisplayMode(.inline)
            // A conversation sits on the plain page colour, like Messages.
            .background(Color(uiColor: .systemBackground).ignoresSafeArea())
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        showChats = true
                    } label: { Image(systemName: "list.bullet") }
                        .accessibilityLabel("All chats")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button {
                            startNewChat()
                        } label: { Label("New chat", systemImage: "square.and.pencil") }

                        Button {
                            showSettings = true
                        } label: { Label("Mode & model", systemImage: "slider.horizontal.3") }

                        if !socket.chatID.isEmpty {
                            Button {
                                newTitle = socket.title
                                renaming = true
                            } label: { Label("Rename chat", systemImage: "pencil") }

                            Button {
                                Task { await exportChat() }
                            } label: { Label("Share chat", systemImage: "square.and.arrow.up") }
                        }

                        if socket.running {
                            Button(role: .destructive) {
                                socket.stop()
                            } label: { Label("Stop", systemImage: "stop.circle") }
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
            .safeAreaInset(edge: .bottom) {
                if app.me?.aiConfigured != false || app.me?.demo == true { composer }
            }
            .sheet(isPresented: $showSettings) {
                ChatSettingsSheet(config: socket.config, models: models,
                                  workspaces: app.me?.workspaces ?? []) { updated in
                    socket.apply(config: updated)
                }
            }
            .sheet(isPresented: $showChats) {
                ChatsListView(currentID: socket.chatID) { id in
                    socket.openChat(id)
                    showChats = false
                } onNew: {
                    startNewChat()
                    showChats = false
                }
            }
            .sheet(item: $shareText) { item in
                ShareSheet(items: [item.text])
            }
            .alert("Rename chat", isPresented: $renaming) {
                TextField("Title", text: $newTitle)
                Button("Save") { Task { await rename() } }
                Button("Cancel", role: .cancel) {}
            }
            .toast($toast)
        }
        .task {
            await app.refreshMe()
            models = try? await app.client?.aiModels()
            // switching tabs keeps the connection; only a first appearance
            // (or a socket that gave up) connects
            if !socket.isActive { connect() }
        }
        .onAppear { takePendingPrompt() }
        .onChange(of: app.pendingPrompt) { _, _ in takePendingPrompt() }
        .onChange(of: app.pendingChat) { _, _ in takePendingChat() }
        // The run lives on the server. Leaving the app keeps the socket for the
        // seconds iOS allows, coming back re-attaches and replays what
        // happened meanwhile — the chat never just says "disconnected".
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .background: socket.enterBackground()
            case .active:     socket.enterForeground()
            default:          break
            }
        }
        .onChange(of: app.phase) { _, phase in
            if phase != .ready { socket.disconnect() }
        }
    }

    // MARK: - States

    private var notConfigured: some View {
        VStack(spacing: 16) {
            MessageState(symbol: "sparkles",
                         title: "No AI connected",
                         message: "Connect your Claude, ChatGPT or Mistral subscription — or add an API key, or a local model — and the assistant can read this server and act on it.")
            NavigationLink {
                AIAccountsView()
            } label: {
                Text("Connect an AI")
            }
            .buttonStyle(PrimaryButtonStyle())
            .padding(.horizontal, 40)
        }
        .frame(maxHeight: .infinity)
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if socket.items.isEmpty { intro }

                    ForEach(ChatTimeline.rows(socket.items)) { row in
                        switch row {
                        case .item(let item):
                            ChatRow(item: item, canEdit: !socket.running) { action in
                                handle(action, on: item)
                            }
                            .id(row.id)
                        case .tools(let group):
                            ToolGroupCard(group: group)
                                .id(row.id)
                        }
                    }

                    if let call = socket.awaitingApproval {
                        ApprovalCard(call: call) { approved in
                            socket.answer(call, approved: approved)
                        }
                        .id("approval")
                    }

                    if let pause = socket.pauseInfo, socket.paused {
                        PauseCard(info: pause) { socket.resume() }
                            .id("pause")
                    }

                    if socket.running && socket.awaitingApproval == nil {
                        HStack(spacing: 8) {
                            ProgressView().controlSize(.small)
                            Text(workingLabel).font(.footnote).foregroundStyle(Theme.muted)
                        }
                        .id("working")
                        .transition(.opacity)
                    }

                    if case .failed(let message) = socket.status {
                        WarningBanner(title: "Connection lost",
                                      message: "\(message) — the assistant keeps working on the server.",
                                      tint: Theme.danger,
                                      actionTitle: "Reconnect",
                                      action: { socket.retry() })
                    }

                    // Anchor: scrolling to the last item lands short when that
                    // item is taller than the viewport.
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(16)
                .animation(.snappy, value: socket.items.count)
            }
            .scrollDismissesKeyboard(.interactively)
            // The plan stays in sight for the whole run, not only at the top
            // of the conversation: a bar that opens into the checklist.
            .safeAreaInset(edge: .top, spacing: 0) {
                VStack(spacing: 0) {
                    if !socket.plan.isEmpty {
                        PlanBar(steps: socket.plan, running: socket.running)
                    }
                    if socket.status == .reconnecting {
                        HStack(spacing: 8) {
                            ProgressView().controlSize(.mini)
                            Text("Reconnecting…")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(Theme.muted)
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)
                        .background(.regularMaterial, in: Capsule())
                        .padding(.top, 6)
                        .transition(.move(edge: .top).combined(with: .opacity))
                    }
                }
                .animation(.snappy, value: socket.status)
            }
            .onChange(of: socket.items.count) { _, _ in scrollDown(proxy) }
            .onChange(of: socket.awaitingApproval?.callID) { _, _ in scrollDown(proxy) }
            .onChange(of: composerFocused) { _, focused in if focused { scrollDown(proxy) } }
        }
    }

    /// "Working…" says more when it can name the step.
    private var workingLabel: String {
        let progress = PlanProgress(socket.plan)
        if !progress.current.isEmpty { return progress.current }
        return "Working…"
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 14) {
            IconTile(symbol: "sparkles", color: .purple, size: 44)
            Text("Ask about this server")
                .font(.title2.weight(.bold))
                .foregroundStyle(Theme.text)
            Text("It reads logs, inspects containers and — in Agent mode — fixes things, asking before every change.")
                .font(.body)
                .foregroundStyle(Theme.muted)
            VStack(alignment: .leading, spacing: 8) {
                ForEach(Self.suggestions, id: \.self) { suggestion in
                    Button {
                        draft = suggestion
                        composerFocused = true
                    } label: {
                        HStack {
                            Text(suggestion)
                                .font(.subheadline)
                                .foregroundStyle(Theme.text)
                                .multilineTextAlignment(.leading)
                            Spacer(minLength: 8)
                            Image(systemName: "arrow.up.right")
                                .font(.footnote.weight(.semibold))
                                .foregroundStyle(Theme.accent)
                        }
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                        .background(Color(uiColor: .secondarySystemBackground),
                                    in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.top, 4)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 24)
    }

    private static let suggestions = [
        "Why is disk usage climbing?",
        "Check the last 50 lines of the nextcloud logs for errors.",
        "Which containers are not on a restart policy?",
    ]

    // MARK: - Composer

    private var composer: some View {
        VStack(spacing: 0) {
            if let editing {
                HStack(spacing: 8) {
                    Image(systemName: "pencil")
                        .foregroundStyle(Theme.accent)
                    VStack(alignment: .leading, spacing: 1) {
                        Text("Editing your message")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(Theme.text)
                        Text("Sending replaces it and everything after it.")
                            .font(.caption2)
                            .foregroundStyle(Theme.muted)
                            .lineLimit(1)
                    }
                    Spacer()
                    Button("Cancel") {
                        self.editing = nil
                        draft = ""
                    }
                    .font(.caption.weight(.semibold))
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
                .background(Theme.accent.opacity(0.08))
                .id(editing.id)
            }

            HStack(alignment: .bottom, spacing: 10) {
                HStack(alignment: .bottom, spacing: 8) {
                    TextField("Message", text: $draft, axis: .vertical)
                        .lineLimit(1...5)
                        .focused($composerFocused)
                        .foregroundStyle(Theme.text)
                        .submitLabel(.send)

                    Menu {
                        ForEach(ChatMode.allCases) { mode in
                            Button {
                                var updated = socket.config
                                updated.mode = mode
                                socket.apply(config: updated)
                            } label: {
                                Label(mode.title, systemImage: mode.symbol)
                            }
                        }
                    } label: {
                        Text(socket.config.mode.title)
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(socket.config.mode.isDangerous ? Theme.warn : Theme.muted)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(Color(uiColor: .secondarySystemBackground),
                            in: RoundedRectangle(cornerRadius: 20, style: .continuous))

                Button {
                    if socket.running {
                        socket.stop()
                    } else {
                        send()
                    }
                } label: {
                    Image(systemName: socket.running ? "stop.fill" : "arrow.up")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(Theme.onAccent)
                        .frame(width: 36, height: 36)
                        .background(sendEnabled ? Theme.accent : Theme.bg3, in: Circle())
                }
                .disabled(!sendEnabled)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
        }
        .background(.bar)
        .overlay(alignment: .top) { Divider() }
    }

    private var sendEnabled: Bool {
        socket.running || !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        if let editing, let ordinal = editing.ordinal {
            socket.rewind(to: ordinal, text: text)
            self.editing = nil
        } else {
            socket.submit(text)
        }
        draft = ""
    }

    // MARK: - Message actions

    private func handle(_ action: ChatRow.Action, on item: ChatItem) {
        switch action {
        case .copy:
            UIPasteboard.general.string = item.text
            toast = Toast(text: "Copied")
        case .edit:
            editing = item
            draft = item.text
            composerFocused = true
        case .retract:
            if let ordinal = item.ordinal {
                socket.rewind(to: ordinal)
                toast = Toast(text: "Message taken back")
            }
        case .share:
            shareText = ShareText(text: item.text)
        }
    }

    // MARK: - Plumbing

    private func startNewChat() {
        editing = nil
        socket.startNewChat()
    }

    /// "Ask the assistant" elsewhere in the app lands here: a fresh chat with
    /// the question in the composer, ready to send or adjust.
    private func takePendingPrompt() {
        guard let prompt = app.pendingPrompt else { return }
        app.pendingPrompt = nil
        startNewChat()
        draft = prompt
        composerFocused = true
    }

    private func rename() async {
        guard let client = app.client, !socket.chatID.isEmpty else { return }
        let title = newTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return }
        do {
            // the server tells every device on this chat (chat_meta)
            try await client.renameChat(socket.chatID, title: title)
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func exportChat() async {
        guard let client = app.client, !socket.chatID.isEmpty else { return }
        do {
            shareText = ShareText(text: try await client.exportChat(socket.chatID))
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func connect() {
        guard let client = app.client else { return }
        let server = app.serverURL?.host ?? "server"
        Task {
            var chatID = socket.chatID
            // The demo cannot run the agent (read-only, no key), so it opens
            // its recorded sample session rather than an empty chat.
            if chatID.isEmpty, app.me?.demo == true,
               let sample = try? await client.chats().first(where: { $0.messageCount > 0 }) {
                chatID = sample.id
            }
            if let wanted = app.pendingChat {
                app.pendingChat = nil
                chatID = wanted
            }
            // a single-use ticket per connect (APIClient.liveWebSocketURL) —
            // fetched again for every reconnect
            socket.connect(chatID: chatID, server: server) { [weak app] in
                do {
                    return try await client.liveWebSocketURL(path: "/ws/chat")
                } catch {
                    await MainActor.run { app?.handle(error) }
                    throw error
                }
            }
        }
    }

    /// A tapped notification ("the assistant is waiting for your OK") names
    /// the chat it is about.
    private func takePendingChat() {
        guard let id = app.pendingChat else { return }
        app.pendingChat = nil
        if socket.isActive {
            socket.openChat(id)
        } else {
            app.pendingChat = id
            connect()
        }
    }

    private func scrollDown(_ proxy: ScrollViewProxy) {
        withAnimation(.easeOut(duration: 0.2)) {
            proxy.scrollTo("bottom", anchor: .bottom)
        }
    }
}

/// Text for the share sheet, identifiable so it can drive `.sheet(item:)`.
struct ShareText: Identifiable {
    let id = UUID()
    let text: String
}

/// The system share sheet, for text or files.
struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}

// MARK: - Transcript rows

struct ChatRow: View {
    enum Action { case copy, edit, retract, share }

    let item: ChatItem
    var canEdit = true
    var perform: (Action) -> Void = { _ in }

    var body: some View {
        switch item.kind {
        case .user:
            HStack {
                Spacer(minLength: 48)
                Text(item.text)
                    .foregroundStyle(Theme.onAccent)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(Theme.accent,
                                in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                    .contextMenu {
                        Button { perform(.copy) } label: { Label("Copy", systemImage: "doc.on.doc") }
                        if canEdit && item.ordinal != nil {
                            Button { perform(.edit) } label: {
                                Label("Edit and resend", systemImage: "pencil")
                            }
                            Button(role: .destructive) { perform(.retract) } label: {
                                Label("Take back", systemImage: "arrow.uturn.backward")
                            }
                        }
                    }
            }

        case .assistant:
            MarkdownText(text: item.text, font: .body)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contextMenu {
                    Button { perform(.copy) } label: { Label("Copy", systemImage: "doc.on.doc") }
                    Button { perform(.share) } label: { Label("Share", systemImage: "square.and.arrow.up") }
                }

        case .thinking:
            ThinkingRow(text: item.text)

        case .tool:
            if let call = item.tool { ToolCard(call: call) }

        case .error:
            Label(item.text, systemImage: "exclamationmark.triangle.fill")
                .font(.footnote)
                .foregroundStyle(Theme.danger)
                .frame(maxWidth: .infinity, alignment: .leading)

        case .notice:
            Text(item.text)
                .font(.footnote)
                .foregroundStyle(Theme.muted)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// A run of tool calls folded into one row: what was done, the step in
/// progress, and every call one tap away.
struct ToolGroupCard: View {
    let group: ToolGroup
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(.snappy) { expanded.toggle() }
            } label: {
                HStack(spacing: 10) {
                    ZStack {
                        Circle().fill(Theme.accent.opacity(0.14)).frame(width: 28, height: 28)
                        if group.isRunning {
                            ProgressView().controlSize(.mini)
                        } else {
                            Image(systemName: "terminal")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(Theme.accent)
                        }
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text(group.summary)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(Theme.text)
                            .lineLimit(1)
                        if let current = group.current, !expanded {
                            Text(current.headline.isEmpty ? current.name : current.headline)
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundStyle(Theme.muted)
                                .lineLimit(1)
                                .truncationMode(.middle)
                        }
                    }
                    Spacer(minLength: 6)
                    if group.deniedCount > 0 {
                        StatusPill(text: "\(group.deniedCount) declined", tint: Theme.danger)
                    }
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Theme.muted)
                        .rotationEffect(.degrees(expanded ? 90 : 0))
                }
                .padding(12)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if expanded {
                Divider().padding(.leading, 12)
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(group.items) { item in
                        if item.kind == .thinking {
                            ThinkingRow(text: item.text)
                        } else if let call = item.tool {
                            ToolCard(call: call)
                        }
                    }
                }
                .padding(10)
                .transition(.opacity)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(uiColor: .secondarySystemBackground),
                    in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

/// The plan, pinned above the conversation while it exists: progress and the
/// current step in one line, the whole checklist on a tap.
struct PlanBar: View {
    let steps: [PlanStep]
    let running: Bool
    @State private var expanded = false

    var body: some View {
        let progress = PlanProgress(steps)
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(.snappy) { expanded.toggle() }
            } label: {
                HStack(spacing: 10) {
                    ZStack {
                        Circle().stroke(Theme.accent.opacity(0.2), lineWidth: 3)
                        Circle()
                            .trim(from: 0, to: max(0.02, progress.fraction))
                            .stroke(progress.isFinished ? Theme.accent2 : Theme.accent,
                                    style: StrokeStyle(lineWidth: 3, lineCap: .round))
                            .rotationEffect(.degrees(-90))
                    }
                    .frame(width: 20, height: 20)
                    .animation(.smooth, value: progress.fraction)

                    VStack(alignment: .leading, spacing: 1) {
                        Text(progress.isFinished ? "Plan done" : "Plan · \(progress.done) of \(progress.total)")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(Theme.muted)
                        if !progress.current.isEmpty && !expanded {
                            Text(progress.current)
                                .font(.subheadline)
                                .foregroundStyle(Theme.text)
                                .lineLimit(1)
                        }
                    }
                    Spacer(minLength: 6)
                    Image(systemName: expanded ? "chevron.up" : "chevron.down")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Theme.muted)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 9)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if expanded {
                VStack(alignment: .leading, spacing: 7) {
                    ForEach(steps) { step in
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: step.done ? "checkmark.circle.fill"
                                  : step.active ? "circle.dotted" : "circle")
                                .font(.caption)
                                .foregroundStyle(step.done ? Theme.accent2
                                                 : step.active ? Theme.accent : Theme.muted)
                                .padding(.top, 2)
                            Text(step.title)
                                .font(.subheadline)
                                .foregroundStyle(step.done ? Theme.muted : Theme.text)
                                .strikethrough(step.done, color: Theme.muted)
                                .fixedSize(horizontal: false, vertical: true)
                            Spacer(minLength: 0)
                        }
                    }
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 10)
                .transition(.opacity)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.bar)
        .overlay(alignment: .bottom) { Divider() }
    }
}

/// Extended thinking, collapsed by default: it is long, it is not the answer,
/// and on a phone it buries everything else.
struct ThinkingRow: View {
    let text: String
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button {
                expanded.toggle()
            } label: {
                Label(expanded ? "Hide reasoning" : "Reasoning",
                      systemImage: expanded ? "chevron.down" : "chevron.right")
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
            }
            if expanded {
                Text(text)
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
                    .italic()
                    .textSelection(.enabled)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// One tool call: what it ran, and what came back. Output is collapsed unless
/// it is short — a `docker logs` dump would otherwise be the whole screen.
struct ToolCard: View {
    let call: ToolCall
    @State private var expanded = false

    private var isShort: Bool { call.output.count < 240 && !call.output.contains("\n\n") }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: call.symbol)
                    .font(.caption)
                    .foregroundStyle(tint)
                Text(call.name)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(tint)
                Spacer()
                stateBadge
            }

            if !call.headline.isEmpty {
                Text(call.headline)
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(Theme.text)
                    .lineLimit(expanded ? nil : 3)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            if !call.diff.isEmpty {
                DiffView(diff: call.diff)
            }

            if !call.output.isEmpty {
                if expanded || isShort {
                    ScrollView(.horizontal, showsIndicators: false) {
                        Text(call.output)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(Theme.muted)
                            .textSelection(.enabled)
                    }
                    .frame(maxHeight: expanded ? 320 : 120)
                } else {
                    Button("Show output (\(call.output.count) characters)") { expanded = true }
                        .font(.caption)
                        .tint(Theme.accent)
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(call.state == .denied ? Theme.danger.opacity(0.10) : Color(uiColor: .secondarySystemBackground),
                    in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .onTapGesture { withAnimation(.snappy) { expanded.toggle() } }
    }

    private var tint: Color {
        switch call.state {
        case .denied:    return Theme.danger
        case .requested: return Theme.warn
        default:         return call.isWrite ? Theme.warn : Theme.accent
        }
    }

    @ViewBuilder
    private var stateBadge: some View {
        switch call.state {
        case .requested: StatusPill(text: "waiting", tint: Theme.warn)
        case .running:   ProgressView().tint(Theme.muted).controlSize(.mini)
        case .denied:    StatusPill(text: "declined", tint: Theme.danger)
        case .finished:
            if !call.autoNote.isEmpty {
                StatusPill(text: call.autoNote, tint: Theme.muted)
            }
        }
    }
}

/// Unified diff, coloured. The agent's file edits are the one place where
/// seeing exactly what changed matters more than reading prose about it.
struct DiffView: View {
    let diff: String

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(diff.components(separatedBy: .newlines).prefix(120).enumerated()),
                        id: \.offset) { _, line in
                    Text(line)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(colour(for: line))
                }
            }
            .padding(8)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.termBg, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    private func colour(for line: String) -> Color {
        if line.hasPrefix("+") && !line.hasPrefix("+++") { return Theme.accent2 }
        if line.hasPrefix("-") && !line.hasPrefix("---") { return Theme.danger }
        if line.hasPrefix("@@") { return Theme.accent }
        return Theme.muted
    }
}

/// The approval gate. Deliberately loud, and deliberately showing the exact
/// command: this is the last point at which a mistake is cheap.
struct ApprovalCard: View {
    let call: ToolCall
    let answer: (Bool) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Run this?", systemImage: "hand.raised.fill")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Theme.warn)

            Text(call.name)
                .font(.caption.weight(.semibold))
                .foregroundStyle(Theme.muted)

            ScrollView(.horizontal, showsIndicators: false) {
                Text(call.headline.isEmpty ? call.detail : call.headline)
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(Theme.text)
                    .textSelection(.enabled)
                    .padding(10)
            }
            .background(Theme.termBg, in: RoundedRectangle(cornerRadius: 8, style: .continuous))

            HStack(spacing: 10) {
                Button("Decline") { answer(false) }
                    .buttonStyle(SecondaryButtonStyle(tint: Theme.danger))
                Button("Allow") { answer(true) }
                    .buttonStyle(PrimaryButtonStyle())
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.warn.opacity(0.12),
                    in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .sensoryFeedback(.warning, trigger: call.callID)
    }
}

/// The agent checkpoints itself after enough steps and waits. Showing what it
/// has done so far is the point — "continue?" with no context is unanswerable.
struct PauseCard: View {
    let info: PauseInfo
    let onContinue: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(info.title, systemImage: "pause.circle.fill")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Theme.accent)
            if !info.why.isEmpty {
                Text(info.why).font(.caption).foregroundStyle(Theme.muted)
            }
            if !info.last.isEmpty {
                Text(info.last)
                    .font(.caption)
                    .foregroundStyle(Theme.text)
                    .lineLimit(6)
            }
            Button("Continue", action: onContinue)
                .buttonStyle(PrimaryButtonStyle())
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.accent.opacity(0.10),
                    in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
    }
}

