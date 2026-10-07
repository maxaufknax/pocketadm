import SwiftUI

/// The agent screen: a conversation that can actually operate the server.
///
/// The session lives on the server, so this view is a window onto it rather
/// than its owner — closing the app does not stop a run, and reopening replays
/// what happened meanwhile.
struct ChatView: View {
    @EnvironmentObject private var app: AppState
    @StateObject private var socket = ChatSocket()

    @State private var draft = ""
    @State private var showSettings = false
    @State private var showHistory = false
    @State private var models: AIModels?
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
            .screenBackground()
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        showHistory = true
                    } label: { Image(systemName: "clock.arrow.circlepath") }
                        .tint(Theme.accent)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button {
                            socket.startNewChat()
                        } label: { Label("New chat", systemImage: "square.and.pencil") }

                        Button {
                            showSettings = true
                        } label: { Label("Mode & model", systemImage: "slider.horizontal.3") }

                        if socket.running {
                            Button(role: .destructive) {
                                socket.stop()
                            } label: { Label("Stop", systemImage: "stop.circle") }
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                    .tint(Theme.accent)
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
            .sheet(isPresented: $showHistory) {
                ChatHistorySheet { id in
                    socket.openChat(id)
                    showHistory = false
                }
            }
        }
        .task {
            await app.refreshMe()
            models = try? await app.client?.aiModels()
            connect()
        }
        .onDisappear { socket.disconnect() }
    }

    // MARK: - States

    private var notConfigured: some View {
        VStack(spacing: 16) {
            MessageState(symbol: "sparkles",
                         title: "No AI configured",
                         message: "Add an API key, or install a local model, and the assistant can read this server and act on it.")
            NavigationLink {
                AISettingsView()
            } label: {
                Text("Set up AI")
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

                    if !socket.plan.isEmpty { PlanCard(steps: socket.plan) }

                    ForEach(socket.items) { item in
                        ChatRow(item: item)
                            .id(item.id)
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
                            ProgressView().tint(Theme.muted).controlSize(.small)
                            Text("Working…").font(.caption).foregroundStyle(Theme.muted)
                        }
                        .id("working")
                    }

                    if case .failed(let message) = socket.status {
                        WarningBanner(title: "Disconnected",
                                      message: message,
                                      tint: Theme.danger,
                                      actionTitle: "Reconnect",
                                      action: connect)
                    }

                    // Anchor: scrolling to the last item lands short when that
                    // item is taller than the viewport.
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(16)
            }
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: socket.items.count) { _, _ in scrollDown(proxy) }
            .onChange(of: socket.awaitingApproval?.callID) { _, _ in scrollDown(proxy) }
            .onChange(of: composerFocused) { _, focused in if focused { scrollDown(proxy) } }
        }
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Ask about this server")
                .font(.headline)
                .foregroundStyle(Theme.text)
            Text("It can read logs, inspect containers and — in Agent mode — fix things, asking before every change.")
                .font(.subheadline)
                .foregroundStyle(Theme.muted)
            VStack(alignment: .leading, spacing: 6) {
                ForEach(Self.suggestions, id: \.self) { suggestion in
                    Button {
                        draft = suggestion
                        composerFocused = true
                    } label: {
                        Text(suggestion)
                            .font(.caption)
                            .foregroundStyle(Theme.accent)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            .padding(.top, 4)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    private static let suggestions = [
        "Why is disk usage climbing?",
        "Check the last 50 lines of the nextcloud logs for errors.",
        "Which containers are not on a restart policy?",
    ]

    // MARK: - Composer

    private var composer: some View {
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
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(Theme.bg3, in: RoundedRectangle(cornerRadius: 18, style: .continuous))

            Button {
                if socket.running {
                    socket.stop()
                } else {
                    socket.submit(draft)
                    draft = ""
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
        .background(Theme.bg2)
        .overlay(alignment: .top) { Divider().overlay(Theme.border) }
    }

    private var sendEnabled: Bool {
        socket.running || !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    // MARK: - Plumbing

    private func connect() {
        guard let client = app.client else { return }
        Task {
            do {
                var chatID = socket.chatID
                // The demo cannot run the agent (read-only, no key), so it opens
                // its recorded sample session rather than an empty chat.
                if chatID.isEmpty, app.me?.demo == true,
                   let sample = try? await client.chats().first(where: { $0.messageCount > 0 }) {
                    chatID = sample.id
                }
                // a single-use ticket, fetched per connect (APIClient.liveWebSocketURL)
                let url = try await client.liveWebSocketURL(path: "/ws/chat")
                socket.connect(to: url, chatID: chatID)
            } catch {
                socket.fail(error.localizedDescription)
                app.handle(error)
            }
        }
    }

    private func scrollDown(_ proxy: ScrollViewProxy) {
        withAnimation(.easeOut(duration: 0.2)) {
            proxy.scrollTo("bottom", anchor: .bottom)
        }
    }
}

// MARK: - Transcript rows

struct ChatRow: View {
    let item: ChatItem

    var body: some View {
        switch item.kind {
        case .user:
            HStack {
                Spacer(minLength: 40)
                Text(item.text)
                    .font(.subheadline)
                    .foregroundStyle(Theme.onAccent)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(Theme.accent,
                                in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                    .textSelection(.enabled)
            }

        case .assistant:
            MarkdownText(text: item.text)
                .frame(maxWidth: .infinity, alignment: .leading)

        case .thinking:
            ThinkingRow(text: item.text)

        case .tool:
            if let call = item.tool { ToolCard(call: call) }

        case .error:
            Label(item.text, systemImage: "exclamationmark.triangle.fill")
                .font(.caption)
                .foregroundStyle(Theme.danger)
                .frame(maxWidth: .infinity, alignment: .leading)

        case .notice:
            Text(item.text)
                .font(.caption)
                .foregroundStyle(Theme.muted)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
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
        .background(Theme.bg2, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(call.state == .denied ? Theme.danger.opacity(0.4) : Theme.border,
                        lineWidth: 1)
        )
        .onTapGesture { withAnimation(.easeOut(duration: 0.15)) { expanded.toggle() } }
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
        .background(Theme.warn.opacity(0.10),
                    in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
                .stroke(Theme.warn.opacity(0.4), lineWidth: 1)
        )
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
        .background(Theme.bg2, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
                .stroke(Theme.accent.opacity(0.4), lineWidth: 1)
        )
    }
}

struct PlanCard: View {
    let steps: [PlanStep]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionCaption(text: "Plan")
            ForEach(steps) { step in
                HStack(spacing: 8) {
                    Image(systemName: step.done ? "checkmark.circle.fill"
                          : step.active ? "circle.dotted" : "circle")
                        .font(.caption)
                        .foregroundStyle(step.done ? Theme.accent2
                                         : step.active ? Theme.accent : Theme.muted)
                    Text(step.title)
                        .font(.caption)
                        .foregroundStyle(step.done ? Theme.muted : Theme.text)
                        .strikethrough(step.done, color: Theme.muted)
                    Spacer(minLength: 0)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }
}
