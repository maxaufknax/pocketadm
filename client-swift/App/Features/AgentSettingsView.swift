import SwiftUI

/// How the assistant behaves on this server: what it knows (its notes, the
/// how-tos it saved, the server map it starts every chat with), the house
/// rules it follows, and which tools it may use.
struct AgentSettingsView: View {
    @EnvironmentObject private var app: AppState

    @State private var notes: AgentNotes?
    @State private var skills: [AgentSkill] = []
    @State private var instructions = ""
    @State private var tools: [AgentTool] = []
    @State private var loaded = false
    @State private var legacyMemory = ""
    @State private var toast: Toast?

    private var hasNotes: Bool { app.supports("notes") }

    var body: some View {
        ThemedList {
            Section {
                knowledgeHeader
            }
            .listRowBackground(Color.clear)
            .listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 0))

            Section {
                NavigationLink {
                    if hasNotes {
                        AgentNotesView(onChange: { notes = $0 })
                    } else {
                        LegacyMemoryView(memory: $legacyMemory)
                    }
                } label: {
                    NavRow(symbol: "brain.head.profile", title: "Notes",
                           subtitle: notesSubtitle,
                           badge: (notes?.stats.stale ?? 0) > 0 ? "\(notes?.stats.stale ?? 0)" : "",
                           badgeTint: Theme.warn, tint: .purple)
                }
                NavigationLink {
                    SkillsView(skills: $skills)
                } label: {
                    NavRow(symbol: "list.bullet.clipboard.fill", title: "How-tos",
                           subtitle: skills.isEmpty ? "Procedures it works out are saved here"
                                                    : "\(skills.count) procedure\(skills.count == 1 ? "" : "s") for this server",
                           tint: .orange)
                }
                NavigationLink {
                    ServerMapView()
                } label: {
                    NavRow(symbol: "map.fill", title: "Server map",
                           subtitle: "What it sees at the start of every chat", tint: .teal)
                }
            } header: {
                Text("What it knows")
            } footer: {
                Text("The assistant reads all of this before it answers, so it starts from facts about your server instead of guessing. Claude Code, Codex and Vibe get the same.")
            }

            Section {
                NavigationLink {
                    InstructionsView(instructions: $instructions)
                } label: {
                    NavRow(symbol: "text.quote", title: "Standing instructions",
                           subtitle: instructionsSubtitle, tint: .indigo)
                }
            } header: {
                Text("House rules")
            }

            if !tools.isEmpty {
                toolSection(title: "Tools that only look",
                            footer: "These run without asking.",
                            items: tools.filter(\.safe))
                toolSection(title: "Tools that change things",
                            footer: "Each use asks you first in Agent mode. A switched-off tool is not offered to the model at all.",
                            items: tools.filter { !$0.safe })
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Assistant behaviour")
        .navigationBarTitleDisplayMode(.inline)
        .toast($toast)
        .task { if !loaded { await load() } }
        .refreshable { await load() }
    }

    // MARK: - Header

    private var knowledgeHeader: some View {
        HStack(alignment: .center, spacing: 16) {
            ZStack {
                Circle().fill(Theme.gradient).frame(width: 58, height: 58)
                Image(systemName: "sparkles")
                    .font(.system(size: 26, weight: .semibold))
                    .foregroundStyle(.white)
            }
            VStack(alignment: .leading, spacing: 5) {
                Text(headline)
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                if let stats = notes?.stats, hasNotes {
                    ProgressView(value: stats.fill)
                        .tint(stats.fill > 0.9 ? Theme.warn : Theme.accent)
                    Text("Notes use \(Self.kb(stats.chars)) of \(Self.kb(stats.budget)) in every conversation")
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                } else {
                    Text("Notes, how-tos and the server map")
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(16)
        .background(Theme.bg2, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
    }

    private var headline: String {
        guard let notes, hasNotes else { return "What your assistant knows" }
        let topics = Set(notes.notes.map(\.topic)).count
        if notes.notes.isEmpty { return "Nothing noted yet" }
        return "\(notes.notes.count) note\(notes.notes.count == 1 ? "" : "s") in \(topics) topic\(topics == 1 ? "" : "s")"
    }

    private var notesSubtitle: String {
        guard let notes, hasNotes else { return "Facts it learned about this server" }
        if notes.notes.isEmpty { return "It writes down what it learns about your server" }
        let pinned = notes.stats.pinned > 0 ? " · \(notes.stats.pinned) pinned" : ""
        return "\(notes.notes.count) facts by topic\(pinned)"
    }

    private var instructionsSubtitle: String {
        let first = instructions.split(separator: "\n").first.map(String.init) ?? ""
        return first.isEmpty ? "None yet — rules for every chat" : first
    }

    static func kb(_ chars: Int) -> String {
        chars < 1000 ? "\(chars) characters" : String(format: "%.1f KB", Double(chars) / 1000)
    }

    // MARK: - Tools

    private func toolSection(title: String, footer: String, items: [AgentTool]) -> some View {
        Section {
            ForEach(items) { tool in
                Toggle(isOn: Binding(
                    get: { tool.enabled },
                    set: { newValue in Task { await toggle(tool, enabled: newValue) } }
                )) {
                    HStack(spacing: 12) {
                        IconTile(symbol: Self.symbol(for: tool.name),
                                 color: tool.safe ? .blue : .orange, size: 30)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(Self.title(for: tool.name))
                                .font(.subheadline.weight(.medium))
                                .foregroundStyle(Theme.text)
                            Text(tool.description)
                                .font(.caption)
                                .foregroundStyle(Theme.muted)
                                .lineLimit(2)
                        }
                    }
                }
                .tint(Theme.accent)
            }
        } header: {
            Text(title)
        } footer: {
            Text(footer)
        }
    }

    static func title(for tool: String) -> String {
        switch tool {
        case "run_command":         return "Run commands"
        case "read_file":           return "Read files"
        case "write_file":          return "Write files"
        case "edit_file":           return "Edit files"
        case "list_dir":            return "List folders"
        case "search_files":        return "Search in files"
        case "fetch_url":           return "Fetch web pages"
        case "integration_request": return "Use connected APIs"
        case "update_plan":         return "Show a plan"
        case "read_skill":          return "Read how-tos"
        case "save_skill":          return "Save how-tos"
        case "pocketadm":           return "Read PocketADM's records"
        case "remember":            return "Take notes"
        case "forget":              return "Remove notes"
        case "update_memory":       return "Update memory"
        default:                    return tool.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    static func symbol(for tool: String) -> String {
        switch tool {
        case "run_command":                 return "terminal.fill"
        case "read_file", "read_skill":     return "doc.text.fill"
        case "write_file", "edit_file":     return "square.and.pencil"
        case "list_dir":                    return "folder.fill"
        case "search_files":                return "magnifyingglass"
        case "fetch_url":                   return "globe"
        case "integration_request":         return "link"
        case "update_plan":                 return "checklist"
        case "save_skill":                  return "list.bullet.clipboard.fill"
        case "pocketadm":                   return "clock.arrow.circlepath"
        case "remember", "update_memory":   return "brain.head.profile"
        case "forget":                      return "trash"
        default:                            return "wrench.and.screwdriver.fill"
        }
    }

    // MARK: - Loading

    private func load() async {
        guard let client = app.client else { return }
        defer { loaded = true }
        if app.me == nil { await app.refreshMe() }
        if hasNotes {
            notes = try? await client.agentNotes()
        } else {
            legacyMemory = (try? await client.agentMemory()) ?? ""
        }
        skills = (try? await client.agentSkills()) ?? []
        instructions = (try? await client.agentInstructions()) ?? ""
        tools = (try? await client.agentTools()) ?? []
    }

    private func toggle(_ tool: AgentTool, enabled: Bool) async {
        guard let client = app.client else { return }
        do {
            try await client.setAgentTool(tool.name, enabled: enabled)
            tools = (try? await client.agentTools()) ?? tools
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
            app.handle(error)
        }
    }
}

// MARK: - Standing instructions

struct InstructionsView: View {
    @Binding var instructions: String

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss
    @State private var draft = ""
    @State private var saving = false
    @State private var toast: Toast?
    @FocusState private var focused: Bool

    private let examples = [
        "Answer in German.",
        "Never restart Nextcloud without asking me first.",
        "Don't touch the stacks in /srv/cloud-server/minecraft.",
        "Keep answers short — I read them on my phone.",
    ]

    var body: some View {
        ThemedList {
            Section {
                TextEditor(text: $draft)
                    .focused($focused)
                    .frame(minHeight: 180)
                    .font(.body)
                    .scrollContentBackground(.hidden)
                    .foregroundStyle(Theme.text)
            } footer: {
                Text("Given to the assistant at the start of every chat, before anything else. Good for house rules: what not to touch, which language to answer in.")
            }

            Section("Ideas") {
                ForEach(examples, id: \.self) { example in
                    Button {
                        draft = draft.isEmpty ? example : draft + "\n" + example
                    } label: {
                        Label(example, systemImage: "plus.circle")
                            .foregroundStyle(Theme.text)
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Instructions")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button(saving ? "Saving…" : "Save") { Task { await save() } }
                    .disabled(saving || draft == instructions)
            }
        }
        .toast($toast)
        .onAppear { draft = instructions }
    }

    private func save() async {
        guard let client = app.client else { return }
        saving = true
        defer { saving = false }
        do {
            try await client.saveAgentInstructions(draft)
            instructions = draft
            focused = false
            toast = Toast(text: "Instructions saved")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
            app.handle(error)
        }
    }
}

// MARK: - How-tos

struct SkillsView: View {
    @Binding var skills: [AgentSkill]
    @EnvironmentObject private var app: AppState
    @State private var toast: Toast?

    var body: some View {
        ThemedList {
            if skills.isEmpty {
                Section {
                    MessageState(symbol: "list.bullet.clipboard",
                                 title: "No how-tos yet",
                                 message: "When the assistant works out a procedure — how a site is deployed, how a tricky fix went — it saves the steps here and follows them next time.")
                }
                .listRowBackground(Color.clear)
            } else {
                Section {
                    ForEach(skills) { skill in
                        NavigationLink {
                            SkillDetailView(skill: skill)
                        } label: {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(skill.title)
                                    .font(.body.weight(.medium))
                                    .foregroundStyle(Theme.text)
                                if !skill.description.isEmpty {
                                    Text(skill.description)
                                        .font(.footnote)
                                        .foregroundStyle(Theme.muted)
                                        .lineLimit(2)
                                }
                            }
                            .padding(.vertical, 2)
                        }
                    }
                    .onDelete { offsets in
                        let names = offsets.map { skills[$0].name }
                        Task { await delete(names) }
                    }
                } footer: {
                    Text("Saved by the assistant, read before a task one of them covers. Swipe to delete one that is wrong.")
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("How-tos")
        .navigationBarTitleDisplayMode(.inline)
        .toast($toast)
    }

    private func delete(_ names: [String]) async {
        guard let client = app.client else { return }
        for name in names {
            do {
                try await client.deleteAgentSkill(name)
                skills.removeAll { $0.name == name }
            } catch {
                toast = Toast(text: error.localizedDescription, isError: true)
            }
        }
    }
}

struct SkillDetailView: View {
    let skill: AgentSkill
    @EnvironmentObject private var app: AppState
    @State private var content = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if content.isEmpty {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, 40)
                } else {
                    MarkdownText(text: content, font: .body)
                        .textSelection(.enabled)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .screenBackground()
        .navigationTitle(skill.title)
        .navigationBarTitleDisplayMode(.inline)
        .task { content = (try? await app.client?.agentSkill(skill.name)) ?? "" }
    }
}

// MARK: - The server map

struct ServerMapView: View {
    @EnvironmentObject private var app: AppState
    @State private var map: ServerMapText?
    @State private var refreshing = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if let map {
                    Toggle(isOn: Binding(get: { map.enabled }, set: { value in
                        Task { await setEnabled(value) }
                    })) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Give the assistant the map")
                                .foregroundStyle(Theme.text)
                            Text("Stacks, domains, systemd units, timers, drives — generated from the server itself.")
                                .font(.caption)
                                .foregroundStyle(Theme.muted)
                        }
                    }
                    .tint(Theme.accent)
                    .card(padding: 14)

                    Text(map.text.isEmpty ? "Nothing to show yet." : map.text)
                        .font(.system(size: 12, design: .monospaced))
                        .foregroundStyle(Theme.text)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .card(padding: 14)
                } else {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, 40)
                }
            }
            .padding(16)
        }
        .screenBackground()
        .navigationTitle("Server map")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    Task { await load(refresh: true) }
                } label: {
                    if refreshing { ProgressView() } else { Image(systemName: "arrow.clockwise") }
                }
                .disabled(refreshing)
            }
        }
        .task { await load(refresh: false) }
    }

    private func load(refresh: Bool) async {
        refreshing = refresh
        defer { refreshing = false }
        map = (try? await app.client?.serverMap(refresh: refresh)) ?? map
    }

    private func setEnabled(_ value: Bool) async {
        try? await app.client?.setServerMap(enabled: value)
        await load(refresh: false)
    }
}

// MARK: - Before server 0.26: memory as one text

struct LegacyMemoryView: View {
    @Binding var memory: String
    @EnvironmentObject private var app: AppState
    @State private var saving = false
    @State private var toast: Toast?

    var body: some View {
        ThemedList {
            Section {
                TextEditor(text: $memory)
                    .frame(minHeight: 260)
                    .font(.system(size: 13, design: .monospaced))
                    .scrollContentBackground(.hidden)
                    .foregroundStyle(Theme.text)
            } footer: {
                Text("Update the server to PocketADM 0.26 to see these as tidy notes by topic.")
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Memory")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button(saving ? "Saving…" : "Save") {
                    Task {
                        saving = true
                        defer { saving = false }
                        do {
                            try await app.client?.saveAgentMemory(memory)
                            toast = Toast(text: "Memory saved")
                        } catch {
                            toast = Toast(text: error.localizedDescription, isError: true)
                        }
                    }
                }
                .disabled(saving)
            }
        }
        .toast($toast)
    }
}
