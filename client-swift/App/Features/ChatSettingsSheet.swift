import SwiftUI

/// Mode, model and working directory for the current agent session. These are
/// per-session settings the server keeps; the defaults live in AI settings.
struct ChatSettingsSheet: View {
    let config: ChatConfig
    let models: AIModels?
    let workspaces: [String]
    let onSave: (ChatConfig) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var draft = ChatConfig()

    var body: some View {
        NavigationStack {
            ThemedList {
                Section {
                    ForEach(ChatMode.allCases) { mode in
                        Button {
                            draft.mode = mode
                        } label: {
                            HStack(alignment: .top, spacing: 14) {
                                IconTile(symbol: mode.symbol, color: mode.isDangerous ? .orange : .blue)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(mode.title)
                                        .foregroundStyle(Theme.text)
                                    Text(mode.blurb)
                                        .font(.footnote)
                                        .foregroundStyle(Theme.muted)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                                Spacer(minLength: 0)
                                if draft.mode == mode {
                                    Image(systemName: "checkmark")
                                        .foregroundStyle(Theme.accent)
                                }
                            }
                            .padding(.vertical, 2)
                        }
                    }
                } header: {
                    SectionCaption(text: "Mode")
                } footer: {
                    if draft.mode.isDangerous {
                        Text("Auto mode runs destructive commands without asking. Use it only where you can restore the box.")
                            .font(.caption)
                            .foregroundStyle(Theme.warn)
                    }
                }

                if let models, !models.providers.isEmpty {
                    Section {
                        ForEach(models.providers) { entry in
                            DisclosureGroup {
                                ForEach(entry.models) { model in
                                    Button {
                                        draft.provider = entry.provider
                                        draft.model = model.id
                                    } label: {
                                        HStack {
                                            VStack(alignment: .leading, spacing: 2) {
                                                Text(model.name)
                                                    .foregroundStyle(Theme.text)
                                                if !model.tools {
                                                    // Without tool calling the
                                                    // agent modes are inert, so
                                                    // this is not a detail.
                                                    Text("no tool support — chat only")
                                                        .font(.caption2)
                                                        .foregroundStyle(Theme.warn)
                                                } else if !model.hint.isEmpty {
                                                    Text(model.hint)
                                                        .font(.caption2)
                                                        .foregroundStyle(model.billedPerUse ? Theme.warn : Theme.muted)
                                                }
                                            }
                                            Spacer()
                                            if model.free {
                                                StatusPill(text: "free", tint: Theme.accent2)
                                            }
                                            if model.billedPerUse {
                                                StatusPill(text: "API key", tint: Theme.warn)
                                            }
                                            if draft.provider == entry.provider
                                                && draft.model == model.id {
                                                Image(systemName: "checkmark")
                                                    .foregroundStyle(Theme.accent)
                                            }
                                        }
                                    }
                                }
                            } label: {
                                HStack(spacing: 12) {
                                    ServiceIcon(names: [entry.provider == "codex" ? "openai" : entry.provider,
                                                        entry.label],
                                                category: "AI", size: 30)
                                    Text(entry.displayName)
                                        .foregroundStyle(Theme.text)
                                    if entry.agent {
                                        // runs the CLI's own agent with its
                                        // own login — no API key involved
                                        StatusPill(text: "your subscription", tint: Theme.accent)
                                    } else if entry.local {
                                        StatusPill(text: "local", tint: Theme.accent2)
                                    }
                                }
                            }
                        }
                    } header: {
                        SectionCaption(text: "Model")
                    } footer: {
                        Text(draft.model.isEmpty
                             ? "Using the server's default model."
                             : "Using \(Self.modelName(draft, in: models)).")
                            .font(.caption)
                            .foregroundStyle(Theme.muted)
                    }
                }

                if !workspaces.isEmpty {
                    Section {
                        Picker("Working directory", selection: $draft.workdir) {
                            ForEach(workspaces, id: \.self) { path in
                                Text(path).tag(path)
                            }
                        }
                        .pickerStyle(.inline)
                    } header: {
                        SectionCaption(text: "Where it works")
                    } footer: {
                        Text("File tools are confined to this directory and the other configured workspaces.")
                            .font(.caption)
                            .foregroundStyle(Theme.muted)
                    }
                }

                Section {
                    Toggle("Extended thinking", isOn: $draft.thinking)
                } footer: {
                    Text("Slower and more expensive, but better at multi-step reasoning. Only some models support it.")
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Session")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { dismiss() }.tint(Theme.muted)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Apply") {
                        onSave(draft)
                        dismiss()
                    }
                    .tint(Theme.accent)
                }
            }
            .onAppear { draft = config }
        }
    }

    /// The chosen model by the name the menu shows ("GLM 5.3"), not its id.
    static func modelName(_ config: ChatConfig, in models: AIModels) -> String {
        let entry = models.providers.first { $0.provider == config.provider }
        let name = entry?.models.first { $0.id == config.model }?.name ?? config.model
        return entry.map { "\($0.displayName) · \(name)" } ?? name
    }
}

/// Every conversation, to find, open and keep tidy. Chats live on the server
/// and are shared across devices, so this is genuinely "all my chats", not
/// this phone's history.
struct ChatsListView: View {
    let currentID: String
    let onOpen: (String) -> Void
    let onNew: () -> Void

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var chats: [ChatSummary] = []
    @State private var loaded = false
    @State private var search = ""
    @State private var showArchived = false
    @State private var selection = Set<String>()
    @State private var editMode: EditMode = .inactive
    @State private var renaming: ChatSummary?
    @State private var newTitle = ""
    @State private var confirmDelete: [String] = []
    @State private var shareText: ShareText?
    @State private var toast: Toast?

    var body: some View {
        NavigationStack {
            Group {
                if !loaded {
                    ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if visible.isEmpty {
                    MessageState(symbol: search.isEmpty ? "bubble.left.and.bubble.right" : "magnifyingglass",
                                 title: search.isEmpty ? "No chats yet" : "Nothing found",
                                 message: search.isEmpty
                                    ? "Conversations you start show up here, on every device."
                                    : "No chat mentions “\(search)”.")
                } else {
                    list
                }
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle("Chats")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $search, placement: .navigationBarDrawer(displayMode: .always),
                        prompt: "Search titles and messages")
            .environment(\.editMode, $editMode)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    if editMode.isEditing {
                        Button("Done") {
                            editMode = .inactive
                            selection = []
                        }
                    } else {
                        Button("Close") { dismiss() }.tint(Theme.muted)
                    }
                }
                ToolbarItemGroup(placement: .topBarTrailing) {
                    if !editMode.isEditing {
                        Menu {
                            Button {
                                editMode = .active
                            } label: { Label("Select", systemImage: "checkmark.circle") }
                            Toggle(isOn: $showArchived) {
                                Label("Show archived", systemImage: "archivebox")
                            }
                        } label: {
                            Image(systemName: "ellipsis.circle")
                        }
                        Button {
                            onNew()
                        } label: { Image(systemName: "square.and.pencil") }
                            .accessibilityLabel("New chat")
                    }
                }
                if editMode.isEditing {
                    ToolbarItemGroup(placement: .bottomBar) {
                        Button {
                            Task { await archive(Array(selection), archived: true) }
                        } label: { Text("Archive") }
                            .disabled(selection.isEmpty)
                        Spacer()
                        Button(role: .destructive) {
                            confirmDelete = Array(selection)
                        } label: { Text(selection.isEmpty ? "Delete" : "Delete (\(selection.count))") }
                            .disabled(selection.isEmpty)
                    }
                }
            }
            .task(id: search) {
                // the server searches message text too; wait for typing to pause
                if !search.isEmpty { try? await Task.sleep(for: .milliseconds(300)) }
                await load()
            }
            .alert("Rename chat", isPresented: Binding(get: { renaming != nil },
                                                        set: { if !$0 { renaming = nil } })) {
                TextField("Title", text: $newTitle)
                Button("Save") {
                    if let chat = renaming { Task { await rename(chat) } }
                }
                Button("Cancel", role: .cancel) {}
            }
            .confirmationDialog(confirmDelete.count == 1 ? "Delete this chat?" : "Delete \(confirmDelete.count) chats?",
                                isPresented: Binding(get: { !confirmDelete.isEmpty },
                                                     set: { if !$0 { confirmDelete = [] } }),
                                titleVisibility: .visible) {
                Button("Delete", role: .destructive) {
                    let ids = confirmDelete
                    Task { await delete(ids) }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Deleted chats are gone on every device.")
            }
            .sheet(item: $shareText) { item in ShareSheet(items: [item.text]) }
            .toast($toast)
        }
    }

    private var visible: [ChatSummary] {
        chats.filter { showArchived || !$0.archived || !search.isEmpty }
    }

    private var pinned: [ChatSummary] { visible.filter { $0.pinned && !$0.archived } }
    private var recent: [ChatSummary] { visible.filter { !$0.pinned && !$0.archived } }
    private var archived: [ChatSummary] { visible.filter(\.archived) }

    private var list: some View {
        List(selection: $selection) {
            if !pinned.isEmpty {
                Section("Pinned") { rows(pinned) }
            }
            if !recent.isEmpty {
                Section {
                    rows(recent)
                } header: {
                    if !pinned.isEmpty { Text("Recent") }
                }
            }
            if !archived.isEmpty {
                Section("Archived") { rows(archived) }
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await load() }
    }

    @ViewBuilder
    private func rows(_ items: [ChatSummary]) -> some View {
        ForEach(items) { chat in
            Button {
                guard !editMode.isEditing else { return }
                onOpen(chat.id)
            } label: {
                ChatSummaryRow(chat: chat, current: chat.id == currentID, searching: !search.isEmpty)
            }
            .tag(chat.id)
            .swipeActions(edge: .leading) {
                Button {
                    Task { await pin(chat) }
                } label: {
                    Label(chat.pinned ? "Unpin" : "Pin", systemImage: chat.pinned ? "pin.slash" : "pin")
                }
                .tint(.orange)
            }
            .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                Button(role: .destructive) {
                    confirmDelete = [chat.id]
                } label: { Label("Delete", systemImage: "trash") }

                Button {
                    Task { await archive([chat.id], archived: !chat.archived) }
                } label: {
                    Label(chat.archived ? "Unarchive" : "Archive",
                          systemImage: chat.archived ? "tray.and.arrow.up" : "archivebox")
                }
                .tint(Theme.muted)
            }
            .contextMenu {
                Button { onOpen(chat.id) } label: { Label("Open", systemImage: "bubble.left") }
                Button {
                    newTitle = chat.title
                    renaming = chat
                } label: { Label("Rename", systemImage: "pencil") }
                Button {
                    Task { await pin(chat) }
                } label: { Label(chat.pinned ? "Unpin" : "Pin", systemImage: chat.pinned ? "pin.slash" : "pin") }
                Button {
                    Task { await share(chat) }
                } label: { Label("Share", systemImage: "square.and.arrow.up") }
                Button {
                    Task { await archive([chat.id], archived: !chat.archived) }
                } label: {
                    Label(chat.archived ? "Unarchive" : "Archive",
                          systemImage: chat.archived ? "tray.and.arrow.up" : "archivebox")
                }
                Divider()
                Button(role: .destructive) {
                    confirmDelete = [chat.id]
                } label: { Label("Delete", systemImage: "trash") }
            }
        }
    }

    // MARK: - Actions

    private func load() async {
        defer { loaded = true }
        guard let client = app.client else { return }
        if app.supports("chat_manage") {
            if let found = try? await client.chats(search: search) { chats = found }
        } else {
            let all = (try? await client.chats()) ?? []
            chats = search.isEmpty ? all
                : all.filter { $0.title.localizedCaseInsensitiveContains(search) }
        }
    }

    private func rename(_ chat: ChatSummary) async {
        guard let client = app.client else { return }
        let title = newTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return }
        do {
            try await client.renameChat(chat.id, title: title)
            await load()
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func pin(_ chat: ChatSummary) async {
        guard let client = app.client else { return }
        do {
            try await client.pinChat(chat.id, pinned: !chat.pinned)
            await load()
        } catch {
            toast = Toast(text: "Pinning needs PocketADM 0.24 on the server", isError: true)
        }
    }

    private func archive(_ ids: [String], archived: Bool) async {
        guard let client = app.client else { return }
        for id in ids { try? await client.archiveChat(id, archived: archived) }
        selection = []
        editMode = .inactive
        toast = Toast(text: archived ? "Archived" : "Back in your chats")
        await load()
    }

    private func delete(_ ids: [String]) async {
        guard let client = app.client else { return }
        if app.supports("chat_manage") && ids.count > 1 {
            try? await client.deleteChats(ids)
        } else {
            for id in ids { try? await client.deleteChat(id) }
        }
        confirmDelete = []
        selection = []
        editMode = .inactive
        await load()
    }

    private func share(_ chat: ChatSummary) async {
        guard let client = app.client else { return }
        do {
            shareText = ShareText(text: try await client.exportChat(chat.id))
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }
}

struct ChatSummaryRow: View {
    let chat: ChatSummary
    var current = false
    var searching = false

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            IconTile(symbol: chat.pinned ? "pin.fill" : "bubble.left.fill",
                     color: chat.pinned ? .orange : (current ? Theme.accent : .gray), size: 32)
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline) {
                    Text(chat.title.isEmpty ? "Untitled" : chat.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Theme.text)
                        .lineLimit(1)
                    Spacer(minLength: 6)
                    Text(Fmt.ago(chat.date))
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                }
                let line = searching && !chat.snippet.isEmpty ? chat.snippet : chat.preview
                if !line.isEmpty {
                    Text(line)
                        .font(.footnote)
                        .foregroundStyle(Theme.muted)
                        .lineLimit(2)
                }
                HStack(spacing: 6) {
                    if chat.waiting {
                        Label("Waiting for your OK", systemImage: "hand.raised.fill")
                            .foregroundStyle(Theme.warn)
                    } else if chat.running {
                        HStack(spacing: 4) {
                            ProgressView().controlSize(.mini)
                            Text("Working")
                        }
                        .foregroundStyle(Theme.accent)
                    }
                    Text("\(chat.messageCount) messages")
                    if chat.toolCount > 0 { Text("· \(chat.toolCount) steps") }
                    if current { Text("· open") .foregroundStyle(Theme.accent) }
                }
                .font(.caption2)
                .foregroundStyle(Color(uiColor: .tertiaryLabel))
            }
        }
        .padding(.vertical, 2)
    }
}
