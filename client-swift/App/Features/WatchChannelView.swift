import SwiftUI
import UIKit

/// The watch as a conversation — like a bot in a Matrix room, not a list of
/// reports: short messages from someone who looks after the server, newest at
/// the bottom, each with the next step one tap away, and a reply field to ask
/// it something or tell it to be quiet about a topic.
struct WatchChannelView: View {
    @EnvironmentObject private var app: AppState
    @EnvironmentObject private var push: PushManager

    @State private var messages: [WatchMessage] = []
    @State private var status: ChannelStatus?
    @State private var more = false
    @State private var replying = false
    @State private var loaded = false
    @State private var error: String?
    @State private var draft = ""
    @State private var sending = false
    @State private var looking = false
    @State private var container: Container?
    @State private var route: MoreRoute?
    @State private var toast: Toast?
    @State private var showSettings = false
    @FocusState private var composerFocused: Bool

    var body: some View {
        Group {
            if !loaded {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                conversation
            }
        }
        .background(Theme.chatBg.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) { titleView }
            ToolbarItem(placement: .topBarTrailing) { menu }
        }
        .safeAreaInset(edge: .bottom) { composer }
        .toast($toast)
        .navigationDestination(item: $container) { container in
            ContainerDetailView(container: container) { action in
                try? await app.client?.containerAction(container.id, action)
            }
        }
        .navigationDestination(item: $route) { route in
            MoreDestination(route: route)
        }
        .navigationDestination(isPresented: $showSettings) {
            WatchSettingsView()
        }
        .task { await poll() }
        .onAppear { push.channelVisible = true }
        .onDisappear { push.channelVisible = false }
    }

    // MARK: - Header

    private var titleView: some View {
        VStack(spacing: 0) {
            Text("Watch")
                .font(.headline)
                .foregroundStyle(Theme.text)
            Text(statusLine)
                .font(.caption2)
                .foregroundStyle(statusTint)
                .lineLimit(1)
        }
        .accessibilityElement(children: .combine)
    }

    private var statusLine: String {
        guard let status else { return "" }
        if replying { return "writing…" }
        if status.running || looking { return "looking at the server…" }
        if !status.enabled { return "off" }
        if status.route?.usable == false { return "no AI connected" }
        if status.paused { return "paused" }
        if status.quietNow { return "quiet hours · only critical" }
        if let label = status.route?.label, !label.isEmpty { return "on · \(label)" }
        return "on"
    }

    private var statusTint: Color {
        guard let status else { return Theme.muted }
        if replying || status.running || looking { return Theme.accent }
        if !status.enabled || status.route?.usable == false { return Theme.warn }
        return Theme.muted
    }

    private var menu: some View {
        Menu {
            Button {
                Task { await lookNow() }
            } label: { Label("Look at the server now", systemImage: "eye") }
                .disabled(!canAct || looking || status?.running == true)

            if status?.paused == true {
                Button { Task { await pause(0) } } label: { Label("Resume", systemImage: "play.fill") }
                    .disabled(!canAct)
            } else {
                Menu {
                    Button("For an hour") { Task { await pause(60) } }
                    Button("For 8 hours") { Task { await pause(8 * 60) } }
                    Button("Until tomorrow") { Task { await pause(24 * 60) } }
                } label: { Label("Pause", systemImage: "pause") }
                    .disabled(!canAct)
            }

            Button {
                showSettings = true
            } label: { Label("Watch settings", systemImage: "gearshape") }
        } label: {
            Image(systemName: "ellipsis.circle")
        }
    }

    private var canAct: Bool { app.me?.demo != true && status?.enabled == true }

    // MARK: - Conversation

    private var conversation: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 4) {
                    banners

                    if more {
                        Button {
                            Task { await loadEarlier() }
                        } label: {
                            Text("Earlier messages")
                                .font(.footnote.weight(.semibold))
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 8)
                        }
                    }

                    if messages.isEmpty && error == nil {
                        emptyState
                    }

                    ForEach(Array(messages.enumerated()), id: \.element.id) { index, message in
                        if startsDay(index) {
                            DaySeparator(date: message.date)
                                .padding(.top, index == 0 ? 4 : 12)
                                .padding(.bottom, 4)
                        }
                        WatchBubble(message: message,
                                    firstInGroup: startsGroup(index),
                                    perform: { action in Task { await perform(action, on: message) } },
                                    menu: { choice in Task { await handle(choice, on: message) } })
                            .id(message.id)
                            .padding(.top, startsGroup(index) && !startsDay(index) ? 8 : 0)
                    }

                    if replying {
                        TypingBubble().id("typing").padding(.top, 8)
                    }

                    if let error {
                        Label(error, systemImage: "exclamationmark.triangle.fill")
                            .font(.footnote)
                            .foregroundStyle(Theme.danger)
                            .padding(.top, 8)
                    }

                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
            }
            .scrollDismissesKeyboard(.interactively)
            .defaultScrollAnchor(.bottom)
            .refreshable { await reload() }
            .onChange(of: messages.last?.id) { _, _ in scrollDown(proxy) }
            .onChange(of: replying) { _, now in if now { scrollDown(proxy) } }
            .onChange(of: composerFocused) { _, focused in if focused { scrollDown(proxy) } }
        }
    }

    @ViewBuilder
    private var banners: some View {
        if let status, !status.enabled {
            NavigationLink {
                WatchSettingsView()
            } label: {
                ChannelBanner(symbol: "eye.fill", tint: .indigo,
                              title: "Let the watch look after this server",
                              text: "An AI checks the server every few hours and right after anything breaks, and writes here only when it is worth knowing.",
                              action: "Turn it on")
            }
            .buttonStyle(.plain)
            .padding(.bottom, 8)
        } else if let status, status.route?.usable == false {
            NavigationLink {
                WatchSettingsView()
            } label: {
                ChannelBanner(symbol: "exclamationmark.triangle.fill", tint: Theme.warn,
                              title: "The watch has no AI",
                              text: "Choose which AI it runs on — Mistral, Claude, ChatGPT or a local model.",
                              action: "Choose one")
            }
            .buttonStyle(.plain)
            .padding(.bottom, 8)
        }

        if status?.enabled == true && !(push.allowed && push.wanted) && app.me?.demo != true {
            Button {
                Task { await turnOnNotifications() }
            } label: {
                ChannelBanner(symbol: "bell.badge.fill", tint: .red,
                              title: push.authorization == .denied ? "Notifications are off for PocketADM"
                                                                   : "Get these messages on your lock screen",
                              text: push.authorization == .denied
                                ? "Allow them in the iOS settings to hear from the watch when the app is closed."
                                : "Like a chat app: the watch's messages, and the assistant when it waits for your OK.",
                              action: push.authorization == .denied ? "Open Settings" : "Turn on notifications")
            }
            .buttonStyle(.plain)
            .padding(.bottom, 8)
        }
    }

    private var emptyState: some View {
        VStack(spacing: 10) {
            IconTile(symbol: "eye.fill", color: .indigo, size: 52)
            Text("Nothing to report")
                .font(.headline)
                .foregroundStyle(Theme.text)
            Text("When something on the server is worth knowing, the watch writes here. You can also ask it anything.")
                .font(.subheadline)
                .foregroundStyle(Theme.muted)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 40)
    }

    private func startsDay(_ index: Int) -> Bool {
        index == 0 || !Calendar.current.isDate(messages[index].date, inSameDayAs: messages[index - 1].date)
    }

    /// Messages from the same side within a few minutes read as one group.
    private func startsGroup(_ index: Int) -> Bool {
        guard index > 0 else { return true }
        let previous = messages[index - 1], current = messages[index]
        return previous.role != current.role || current.t - previous.t > 300
    }

    private func scrollDown(_ proxy: ScrollViewProxy) {
        withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo("bottom", anchor: .bottom) }
    }

    // MARK: - Composer

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 10) {
            TextField(placeholder, text: $draft, axis: .vertical)
                .lineLimit(1...5)
                .focused($composerFocused)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(Theme.bubble,
                            in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                .disabled(!canWrite)

            Button {
                Task { await sendDraft() }
            } label: {
                Image(systemName: "arrow.up")
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(Theme.onAccent)
                    .frame(width: 36, height: 36)
                    .background(sendEnabled ? Theme.accent : Theme.bg3, in: Circle())
            }
            .disabled(!sendEnabled)
            .accessibilityLabel("Send")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(.bar)
        .overlay(alignment: .top) { Divider() }
    }

    private var placeholder: String {
        if app.me?.demo == true { return "The demo is read-only" }
        if status?.enabled != true { return "Turn the watch on to chat with it" }
        if status?.route?.usable == false { return "Choose an AI for the watch first" }
        return "Message the watch"
    }

    private var canWrite: Bool {
        app.me?.demo != true && status?.enabled == true && status?.route?.usable != false
    }

    private var sendEnabled: Bool {
        canWrite && !sending && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    // MARK: - Loading

    /// Polls while the screen is open: quickly while the watch writes, slowly
    /// otherwise. The task ends when the view goes away.
    private func poll() async {
        await reload()
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(replying || looking || status?.running == true ? 2 : 8))
            if Task.isCancelled { break }
            await fetchNew()
        }
    }

    private func reload() async {
        guard let client = app.client else { return }
        if app.me == nil { await app.refreshMe() }
        do {
            let page = try await client.watchChannel(limit: 60)
            messages = page.messages
            more = page.more
            replying = page.replying
            status = page.status
            error = nil
            await markRead()
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
        loaded = true
    }

    private func fetchNew() async {
        guard let client = app.client else { return }
        let newest = messages.last?.t ?? 0
        guard let page = try? await client.watchChannel(after: newest, limit: 60) else { return }
        let known = Set(messages.map(\.id))
        let fresh = page.messages.filter { !known.contains($0.id) }
        if !fresh.isEmpty {
            withAnimation(.snappy) { messages.append(contentsOf: fresh) }
            await markRead()
        }
        replying = page.replying
        status = page.status ?? status
        if looking, status?.running == false, !fresh.isEmpty { looking = false }
    }

    private func loadEarlier() async {
        guard let client = app.client, let oldest = messages.first?.t,
              let page = try? await client.watchChannel(before: oldest, limit: 60) else { return }
        let known = Set(messages.map(\.id))
        messages.insert(contentsOf: page.messages.filter { !known.contains($0.id) }, at: 0)
        more = page.more
    }

    private func markRead() async {
        guard let newest = messages.last?.t else { return }
        try? await app.client?.markChannelRead(upTo: newest)
        app.clearAlertBadge()
        push.markWatchSeen(upTo: newest)
    }

    // MARK: - Actions

    private func sendDraft() async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, let client = app.client else { return }
        sending = true
        defer { sending = false }
        do {
            let response = try await client.sendToWatch(text)
            draft = ""
            if let message = response.message, !messages.contains(where: { $0.id == message.id }) {
                withAnimation(.snappy) { messages.append(message) }
            }
            replying = response.replying
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func lookNow() async {
        guard let client = app.client else { return }
        looking = true
        do {
            try await client.runWatch(kind: "test")
        } catch {
            looking = false
            toast = Toast(text: error.localizedDescription, isError: true)
        }
        // the answer arrives through the poll; stop the indicator after a while
        try? await Task.sleep(for: .seconds(150))
        looking = false
    }

    private func pause(_ minutes: Int) async {
        guard let client = app.client else { return }
        _ = try? await client.pauseWatch(minutes: minutes)
        toast = Toast(text: minutes == 0 ? "The watch is back" : "Paused — critical messages still come through")
        await fetchNew()
        if let page = try? await client.watchChannel(after: messages.last?.t ?? 0) { status = page.status ?? status }
    }

    private func turnOnNotifications() async {
        if push.authorization == .denied {
            if let url = URL(string: UIApplication.openSettingsURLString) {
                _ = await UIApplication.shared.open(url)
            }
            return
        }
        if await push.enable() {
            toast = Toast(text: "Notifications are on")
        }
    }

    private func perform(_ action: AlertAction, on message: WatchMessage) async {
        guard let client = app.client else { return }
        switch action.kind {
        case "assistant":
            app.ask(action.prompt.isEmpty ? "The watch wrote: \"\(message.text)\" — look into it and tell me what to do." : action.prompt)
        case "container":
            if let found = try? await client.containers().first(where: { $0.name == action.target }) {
                container = found
            } else {
                toast = Toast(text: "\(action.target) is not there any more", isError: true)
            }
        case "open":
            if action.target == "containers" {
                app.selectedTab = .containers
            } else {
                route = MoreRoute.from(target: action.target)
            }
        default:
            break
        }
    }

    private func handle(_ choice: WatchBubble.Choice, on message: WatchMessage) async {
        guard let client = app.client else { return }
        switch choice {
        case .copy:
            UIPasteboard.general.string = message.detail.isEmpty ? message.text : "\(message.text)\n\n\(message.detail)"
            toast = Toast(text: "Copied")
        case .helpful(let helpful):
            try? await client.channelFeedback(message.id, helpful: helpful)
            toast = Toast(text: helpful ? "Thanks — noted" : "Noted — it will write less like this")
        case .mute(let hours):
            let topic = message.topic.isEmpty ? message.title : message.topic
            guard !topic.isEmpty else { return }
            if (try? await client.muteWatchTopic(topic, hours: hours)) != nil {
                toast = Toast(text: hours >= 24 * 365 ? "Never about this again" : hours >= 24 * 7
                              ? "Quiet about this for a week" : "Quiet about this for a day")
            }
        case .ask:
            app.ask("The watch wrote: \"\(message.text)\(message.detail.isEmpty ? "" : " — \(message.detail)")\" — look into it and tell me what to do.")
        case .delete:
            do {
                try await client.deleteChannelMessage(message.id)
                withAnimation(.snappy) { messages.removeAll { $0.id == message.id } }
            } catch {
                toast = Toast(text: error.localizedDescription, isError: true)
            }
        }
    }
}

// MARK: - Bubbles

/// One message. The watch's on the left with its importance as a coloured
/// edge, yours on the right, notes from PocketADM itself centred.
struct WatchBubble: View {
    enum Choice { case copy, helpful(Bool), mute(Double), ask, delete }

    let message: WatchMessage
    var firstInGroup = true
    let perform: (AlertAction) -> Void
    let menu: (Choice) -> Void
    @State private var showDetail = false

    var body: some View {
        if message.isSystem {
            Text(message.text)
                .font(.footnote)
                .foregroundStyle(Theme.muted)
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 6)
        } else if message.isUser {
            HStack {
                Spacer(minLength: 56)
                VStack(alignment: .trailing, spacing: 3) {
                    Text(message.text)
                        .foregroundStyle(Theme.onAccent)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 9)
                        .background(Theme.accent, in: RoundedRectangle(cornerRadius: 19, style: .continuous))
                        .contextMenu { Button { menu(.copy) } label: { Label("Copy", systemImage: "doc.on.doc") } }
                    Text(message.date.formatted(date: .omitted, time: .shortened))
                        .font(.caption2)
                        .foregroundStyle(Theme.muted)
                }
            }
        } else {
            watchMessage
        }
    }

    private var watchMessage: some View {
        HStack(alignment: .top, spacing: 8) {
            if firstInGroup {
                IconTile(symbol: "eye.fill", color: .indigo, size: 28)
            } else {
                Color.clear.frame(width: 28, height: 1)
            }
            VStack(alignment: .leading, spacing: 6) {
                VStack(alignment: .leading, spacing: 6) {
                    if message.severity == .crit || message.severity == .warn {
                        Label(message.severity == .crit ? "Critical" : "Important",
                              systemImage: message.severity.symbol)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(message.severity.tint)
                    }
                    MarkdownText(text: message.text, font: .body)
                    if !message.detail.isEmpty {
                        if showDetail {
                            MarkdownText(text: message.detail, font: .footnote)
                                .foregroundStyle(Theme.muted)
                                .transition(.opacity)
                        }
                        Button {
                            withAnimation(.snappy) { showDetail.toggle() }
                        } label: {
                            Text(showDetail ? "Less" : "Details")
                                .font(.footnote.weight(.semibold))
                        }
                        .buttonStyle(.borderless)
                    }
                }
                .padding(.horizontal, 13)
                .padding(.vertical, 9)
                .background(Theme.bubble,
                            in: RoundedRectangle(cornerRadius: 19, style: .continuous))
                .overlay(alignment: .leading) {
                    if message.severity == .crit || message.severity == .warn {
                        RoundedRectangle(cornerRadius: 2)
                            .fill(message.severity.tint)
                            .frame(width: 3)
                            .padding(.vertical, 10)
                    }
                }
                .contextMenu { contextMenu }

                if !message.actions.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(message.actions) { action in
                                Button {
                                    perform(action)
                                } label: {
                                    Label(action.label, systemImage: ChannelActions.symbol(for: action))
                                        .font(.footnote.weight(.semibold))
                                        .padding(.horizontal, 10)
                                        .padding(.vertical, 6)
                                        .background(Theme.accent.opacity(0.12), in: Capsule())
                                        .foregroundStyle(Theme.accent)
                                }
                                .buttonStyle(.borderless)
                            }
                        }
                    }
                }

                HStack(spacing: 6) {
                    Text(message.date.formatted(date: .omitted, time: .shortened))
                    if message.feedback == "helpful" {
                        Image(systemName: "hand.thumbsup.fill")
                    } else if message.feedback == "not_helpful" {
                        Image(systemName: "hand.thumbsdown.fill")
                    }
                }
                .font(.caption2)
                .foregroundStyle(Theme.muted)
                .padding(.leading, 4)
            }
            Spacer(minLength: 28)
        }
    }

    @ViewBuilder
    private var contextMenu: some View {
        Button { menu(.copy) } label: { Label("Copy", systemImage: "doc.on.doc") }
        if message.kind != "chat" && message.kind != "system" {
            Button { menu(.helpful(true)) } label: { Label("Helpful", systemImage: "hand.thumbsup") }
            Button { menu(.helpful(false)) } label: { Label("Not helpful", systemImage: "hand.thumbsdown") }
            if !message.topic.isEmpty || !message.title.isEmpty {
                Menu {
                    Button("For a day") { menu(.mute(24)) }
                    Button("For a week") { menu(.mute(24 * 7)) }
                    Button("Never again") { menu(.mute(24 * 365)) }
                } label: { Label("Quiet about this", systemImage: "bell.slash") }
            }
        }
        Button { menu(.ask) } label: { Label("Ask the assistant", systemImage: "sparkles") }
        Button(role: .destructive) { menu(.delete) } label: { Label("Delete", systemImage: "trash") }
    }
}

enum ChannelActions {
    static func symbol(for action: AlertAction) -> String {
        switch action.kind {
        case "container": return "shippingbox"
        case "assistant": return "sparkles"
        case "open":
            switch action.target {
            case "updates":  return "arrow.triangle.2.circlepath"
            case "storage":  return "internaldrive"
            case "checks":   return "checkmark.shield"
            case "activity": return "clock.arrow.circlepath"
            default:         return "arrow.right"
            }
        default: return "arrow.right"
        }
    }
}

/// "Today", "Yesterday", "Monday 6 October" between days.
struct DaySeparator: View {
    let date: Date

    var body: some View {
        Text(label)
            .font(.caption.weight(.semibold))
            .foregroundStyle(Theme.muted)
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .background(Theme.bg3, in: Capsule())
            .frame(maxWidth: .infinity)
    }

    private var label: String {
        let calendar = Calendar.current
        if calendar.isDateInToday(date) { return "Today" }
        if calendar.isDateInYesterday(date) { return "Yesterday" }
        if let days = calendar.dateComponents([.day], from: date, to: Date()).day, days < 7 {
            return date.formatted(.dateTime.weekday(.wide))
        }
        return date.formatted(.dateTime.weekday(.abbreviated).day().month(.wide))
    }
}

/// Three dots while the watch writes an answer.
struct TypingBubble: View {
    @State private var phase = 0

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            IconTile(symbol: "eye.fill", color: .indigo, size: 28)
            HStack(spacing: 4) {
                ForEach(0..<3) { i in
                    Circle()
                        .fill(Theme.muted)
                        .frame(width: 7, height: 7)
                        .opacity(phase == i ? 1 : 0.35)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 13)
            .background(Theme.bubble,
                        in: RoundedRectangle(cornerRadius: 19, style: .continuous))
            Spacer()
        }
        .task {
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(380))
                withAnimation(.easeInOut(duration: 0.2)) { phase = (phase + 1) % 3 }
            }
        }
        .accessibilityLabel("The watch is writing")
    }
}

/// A card above the conversation: turn the watch on, choose its AI, allow
/// notifications.
struct ChannelBanner: View {
    let symbol: String
    let tint: Color
    let title: String
    let text: String
    let action: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            IconTile(symbol: symbol, color: tint, size: 34)
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.text)
                Text(text)
                    .font(.footnote)
                    .foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
                Text(action)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Theme.accent)
                    .padding(.top, 2)
            }
            Spacer(minLength: 0)
        }
        .padding(14)
        .background(Theme.bg2, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
            .strokeBorder(Theme.border.opacity(0.5), lineWidth: 0.5))
    }
}

/// The Alerts route: the conversation on a 0.25 server, the 0.24 list before.
struct AlertsHome: View {
    @EnvironmentObject private var app: AppState

    var body: some View {
        if app.supports("watch_channel") {
            WatchChannelView()
        } else {
            AlertsView()
        }
    }
}
