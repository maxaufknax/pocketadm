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
            List {
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
                                                }
                                            }
                                            Spacer()
                                            if model.free {
                                                StatusPill(text: "free", tint: Theme.accent2)
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
                             : "Using \(draft.model).")
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
}

/// Past conversations. They live on the server and are shared across devices,
/// so this is genuinely "all my chats", not this phone's history.
struct ChatHistorySheet: View {
    let onOpen: (String) -> Void

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var chats: [ChatSummary] = []
    @State private var loaded = false
    @State private var showArchived = false

    var body: some View {
        NavigationStack {
            Group {
                if !loaded {
                    ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if visible.isEmpty {
                    MessageState(symbol: "bubble.left",
                                 title: "No chats yet",
                                 message: "Conversations you start show up here.")
                } else {
                    list
                }
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle("Chats")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Close") { dismiss() }.tint(Theme.muted)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button(showArchived ? "Hide archived" : "Archived") {
                        showArchived.toggle()
                    }
                    .font(.caption)
                    .tint(Theme.accent)
                }
            }
            .task { await load() }
        }
    }

    private var visible: [ChatSummary] {
        chats.filter { showArchived ? true : !$0.archived }
            .sorted { $0.updated > $1.updated }
    }

    private var list: some View {
        List {
            ForEach(visible) { chat in
                Button { onOpen(chat.id) } label: {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(chat.title.isEmpty ? "Untitled" : chat.title)
                            .font(.subheadline.weight(.medium))
                            .foregroundStyle(Theme.text)
                            .lineLimit(1)
                        Text("\(chat.messageCount) messages · \(Fmt.ago(chat.date))")
                            .font(.caption)
                            .foregroundStyle(Theme.muted)
                    }
                }
                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                    Button(role: .destructive) {
                        Task { await delete(chat) }
                    } label: { Label("Delete", systemImage: "trash") }

                    Button {
                        Task { await archive(chat) }
                    } label: {
                        Label(chat.archived ? "Unarchive" : "Archive",
                              systemImage: chat.archived ? "tray.and.arrow.up" : "archivebox")
                    }
                    .tint(Theme.muted)
                }
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await load() }
    }

    private func load() async {
        defer { loaded = true }
        guard let client = app.client else { return }
        chats = (try? await client.chats()) ?? []
    }

    private func delete(_ chat: ChatSummary) async {
        guard let client = app.client else { return }
        try? await client.deleteChat(chat.id)
        await load()
    }

    private func archive(_ chat: ChatSummary) async {
        guard let client = app.client else { return }
        try? await client.archiveChat(chat.id, archived: !chat.archived)
        await load()
    }
}
