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
                            HStack(alignment: .top, spacing: 12) {
                                Image(systemName: mode.symbol)
                                    .foregroundStyle(mode.isDangerous ? Theme.warn : Theme.accent)
                                    .frame(width: 22)
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(mode.title)
                                        .font(.subheadline.weight(.medium))
                                        .foregroundStyle(Theme.text)
                                    Text(mode.blurb)
                                        .font(.caption)
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
                    .listRowBackground(Theme.bg2)
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
                                                    .font(.subheadline)
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
                                HStack {
                                    Text(entry.provider.capitalized)
                                        .font(.subheadline.weight(.medium))
                                        .foregroundStyle(Theme.text)
                                    if entry.local {
                                        StatusPill(text: "local", tint: Theme.accent2)
                                    }
                                }
                            }
                        }
                        .listRowBackground(Theme.bg2)
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
                        .listRowBackground(Theme.bg2)
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
                        .tint(Theme.accent)
                        .listRowBackground(Theme.bg2)
                } footer: {
                    Text("Slower and more expensive, but better at multi-step reasoning. Only some models support it.")
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(Theme.bg)
            .navigationTitle("Session")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.bg2, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
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
                    ProgressView().tint(Theme.accent).frame(maxWidth: .infinity, maxHeight: .infinity)
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
            .toolbarBackground(Theme.bg2, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
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
                .listRowBackground(Theme.bg2)
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
        .scrollContentBackground(.hidden)
        .background(Theme.bg)
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
