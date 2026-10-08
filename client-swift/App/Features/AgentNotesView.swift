import SwiftUI

/// The assistant's notes about this server, by topic: what it learned on its
/// own and what you told it. Pin what must never be dropped, edit or delete
/// what is wrong, and let it tidy the list up (merge duplicates, drop what is
/// outdated) with one undo.
struct AgentNotesView: View {
    var onChange: (AgentNotes) -> Void = { _ in }

    @EnvironmentObject private var app: AppState
    @State private var data: AgentNotes?
    @State private var loaded = false
    @State private var query = ""
    @State private var editing: NoteDraft?
    @State private var tidying = false
    @State private var confirmTidy = false
    @State private var confirmClear = false
    @State private var toast: Toast?

    var body: some View {
        ThemedList {
            if let data {
                Section {
                    summary(data)
                }

                if data.notes.isEmpty {
                    Section {
                        MessageState(symbol: "brain.head.profile",
                                     title: "No notes yet",
                                     message: "The assistant writes down what it learns about your server — paths, conventions, how things are deployed, what you prefer. You can add a note yourself, too.")
                    }
                    .listRowBackground(Color.clear)
                }

                ForEach(groups(data), id: \.topic.id) { group in
                    Section {
                        ForEach(group.notes) { note in
                            NoteRow(note: note)
                                .contentShape(Rectangle())
                                .onTapGesture { editing = NoteDraft(note: note) }
                                .swipeActions(edge: .trailing) {
                                    Button(role: .destructive) {
                                        Task { await delete(note) }
                                    } label: { Label("Delete", systemImage: "trash") }
                                }
                                .swipeActions(edge: .leading) {
                                    Button {
                                        Task { await pin(note, !note.pinned) }
                                    } label: {
                                        Label(note.pinned ? "Unpin" : "Pin",
                                              systemImage: note.pinned ? "pin.slash" : "pin")
                                    }
                                    .tint(.orange)
                                }
                                .contextMenu {
                                    Button { editing = NoteDraft(note: note) } label: {
                                        Label("Edit", systemImage: "pencil")
                                    }
                                    Button { Task { await pin(note, !note.pinned) } } label: {
                                        Label(note.pinned ? "Unpin" : "Pin — always keep",
                                              systemImage: note.pinned ? "pin.slash" : "pin")
                                    }
                                    Button { UIPasteboard.general.string = note.text } label: {
                                        Label("Copy", systemImage: "doc.on.doc")
                                    }
                                    Button(role: .destructive) { Task { await delete(note) } } label: {
                                        Label("Delete", systemImage: "trash")
                                    }
                                }
                        }
                    } header: {
                        Label("\(group.topic.label) · \(group.notes.count)", systemImage: group.topic.symbol)
                            .textCase(nil)
                    }
                }
            } else if loaded {
                Section {
                    MessageState(symbol: "exclamationmark.triangle", title: "Notes could not be loaded",
                                 retry: { Task { await load() } })
                }
                .listRowBackground(Color.clear)
            }
        }
        .listStyle(.insetGrouped)
        .searchable(text: $query, prompt: "Search notes")
        .navigationTitle("Notes")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Menu {
                    Button { confirmTidy = true } label: {
                        Label("Tidy up", systemImage: "wand.and.stars")
                    }
                    .disabled((data?.notes.count ?? 0) < 2 || tidying)
                    if data?.stats.canUndo == true {
                        Button { Task { await undo() } } label: {
                            Label("Undo last tidy-up", systemImage: "arrow.uturn.backward")
                        }
                    }
                    Divider()
                    Button(role: .destructive) { confirmClear = true } label: {
                        Label("Delete all notes", systemImage: "trash")
                    }
                    .disabled(data?.notes.isEmpty ?? true)
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
                Button {
                    editing = NoteDraft()
                } label: {
                    Image(systemName: "plus")
                }
                .disabled(app.me?.demo == true)
            }
        }
        .overlay {
            if !loaded { ProgressView() }
            if tidying {
                VStack(spacing: 12) {
                    ProgressView()
                    Text("Tidying up the notes…")
                        .font(.subheadline)
                        .foregroundStyle(Theme.muted)
                }
                .padding(24)
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            }
        }
        .sheet(item: $editing) { draft in
            NoteEditor(draft: draft, topics: data?.topics ?? NoteTopic.fallback) { result in
                apply(result)
            }
        }
        .confirmationDialog("Tidy up the notes?", isPresented: $confirmTidy, titleVisibility: .visible) {
            Button("Tidy up") { Task { await tidy() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Duplicates are merged, outdated and temporary notes are dropped, pinned notes stay. You can undo it.")
        }
        .confirmationDialog("Delete every note?", isPresented: $confirmClear, titleVisibility: .visible) {
            Button("Delete all", role: .destructive) { Task { await clear() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The assistant starts again from what it sees on the server. You can undo it once.")
        }
        .toast($toast)
        .task { if !loaded { await load() } }
        .refreshable { await load() }
    }

    // MARK: - Pieces

    private func summary(_ data: AgentNotes) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 18) {
                stat("\(data.stats.count)", "notes")
                stat("\(data.stats.pinned)", "pinned")
                stat("\(data.notes.filter(\.byYou).count)", "by you")
                if data.stats.stale > 0 {
                    stat("\(data.stats.stale)", "to check", tint: Theme.warn)
                }
                Spacer(minLength: 0)
            }
            ProgressView(value: data.stats.fill)
                .tint(data.stats.fill > 0.9 ? Theme.warn : Theme.accent)
            Text(data.stats.fill >= 1
                 ? "More notes than fit in a conversation: the oldest are left out. Tidy up to make room."
                 : "Every conversation carries \(AgentSettingsView.kb(data.stats.chars)) of notes (room for \(AgentSettingsView.kb(data.stats.budget))).")
                .font(.caption)
                .foregroundStyle(Theme.muted)
            if data.notes.count >= 2 {
                Button {
                    confirmTidy = true
                } label: {
                    Label("Tidy up with AI", systemImage: "wand.and.stars")
                        .font(.subheadline.weight(.semibold))
                }
                .buttonStyle(.bordered)
                .buttonBorderShape(.capsule)
                .disabled(tidying || app.me?.demo == true)
            }
        }
        .padding(.vertical, 4)
    }

    private func stat(_ value: String, _ label: String, tint: Color = Theme.text) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(value)
                .font(.title3.weight(.semibold).monospacedDigit())
                .foregroundStyle(tint)
            Text(label)
                .font(.caption2)
                .foregroundStyle(Theme.muted)
        }
    }

    private struct TopicGroup { let topic: NoteTopic; let notes: [AgentNote] }

    private func groups(_ data: AgentNotes) -> [TopicGroup] {
        let needle = query.trimmingCharacters(in: .whitespaces).lowercased()
        let shown = data.notes.filter { note in
            needle.isEmpty || note.text.lowercased().contains(needle) || note.subject.lowercased().contains(needle)
        }
        return data.topics.compactMap { topic in
            let notes = shown.filter { $0.topic == topic.id }.sorted { a, b in
                if a.pinned != b.pinned { return a.pinned }
                if a.subject != b.subject { return a.subject < b.subject }   // one project together
                return a.updated > b.updated
            }
            return notes.isEmpty ? nil : TopicGroup(topic: topic, notes: notes)
        }
    }

    // MARK: - Changes

    private func load() async {
        guard let client = app.client else { return }
        defer { loaded = true }
        if let fresh = try? await client.agentNotes() { set(fresh) }
    }

    private func set(_ fresh: AgentNotes) {
        withAnimation(.snappy) { data = fresh }
        onChange(fresh)
    }

    private func apply(_ result: Result<AgentNotes, Error>) {
        switch result {
        case .success(let fresh):
            set(fresh)
            toast = Toast(text: fresh.result == "updated" ? "Already known — note refreshed" : "Saved")
        case .failure(let error):
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func pin(_ note: AgentNote, _ value: Bool) async {
        guard let client = app.client else { return }
        do {
            set(try await client.editAgentNote(note.id, pinned: value))
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func delete(_ note: AgentNote) async {
        guard let client = app.client else { return }
        do {
            set(try await client.deleteAgentNote(note.id))
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func tidy() async {
        guard let client = app.client else { return }
        tidying = true
        defer { tidying = false }
        do {
            let fresh = try await client.tidyAgentNotes()
            set(fresh)
            if let t = fresh.tidy {
                toast = Toast(text: t.removed > 0 ? "\(t.before) → \(t.after) notes" : "Already tidy")
            }
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func undo() async {
        guard let client = app.client else { return }
        do {
            set(try await client.undoAgentNotes())
            toast = Toast(text: "Restored")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func clear() async {
        guard let client = app.client else { return }
        do {
            set(try await client.clearAgentNotes())
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }
}

/// One note: what it is about, the fact, and where it came from.
struct NoteRow: View {
    let note: AgentNote

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            if !note.subject.isEmpty {
                Text(note.subject)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.accent)
                    .lineLimit(1)
            }
            Text(MarkdownText.attributed(note.text))
                .font(.subheadline)
                .foregroundStyle(Theme.text)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 6) {
                Image(systemName: note.byYou ? "person.fill" : "sparkles")
                Text(note.byYou ? "You" : "Learned")
                Text("·")
                Text(Fmt.ago(note.updatedDate))
                if note.isStale {
                    StatusPill(text: "check", tint: Theme.warn)
                }
                Spacer(minLength: 0)
                if note.pinned {
                    Image(systemName: "pin.fill")
                        .foregroundStyle(.orange)
                }
            }
            .font(.caption2)
            .foregroundStyle(Theme.muted)
        }
        .padding(.vertical, 3)
    }
}

/// A note being added or edited.
struct NoteDraft: Identifiable {
    let id = UUID()
    var noteID: String? = nil
    var text = ""
    var topic = "other"
    var subject = ""
    var pinned = false

    init() {}

    init(note: AgentNote) {
        noteID = note.id
        text = note.text
        topic = note.topic
        subject = note.subject
        pinned = note.pinned
    }
}

struct NoteEditor: View {
    @State var draft: NoteDraft
    let topics: [NoteTopic]
    let onDone: (Result<AgentNotes, Error>) -> Void

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss
    @State private var saving = false
    @State private var error: String?
    @FocusState private var focused: Bool

    var body: some View {
        NavigationStack {
            ThemedList {
                Section {
                    TextField("A fact about the server, short", text: $draft.text, axis: .vertical)
                        .lineLimit(3...10)
                        .focused($focused)
                } footer: {
                    Text("One fact per note. Never a password, key or token — note where it is kept instead.")
                }
                Section {
                    Picker("Topic", selection: $draft.topic) {
                        ForEach(topics) { topic in
                            Label(topic.label, systemImage: topic.symbol).tag(topic.id)
                        }
                    }
                    TextField("About (optional), e.g. Nextcloud", text: $draft.subject)
                    Toggle("Pinned — always kept", isOn: $draft.pinned)
                        .tint(Theme.accent)
                }
                if let error {
                    Section {
                        Label(error, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(Theme.danger)
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle(draft.noteID == nil ? "New note" : "Edit note")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(saving ? "Saving…" : (draft.noteID == nil ? "Add" : "Save")) {
                        Task { await save() }
                    }
                    .disabled(saving || draft.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .onAppear { if draft.noteID == nil { focused = true } }
        }
        .presentationDetents([.medium, .large])
    }

    private func save() async {
        guard let client = app.client else { return }
        saving = true
        defer { saving = false }
        do {
            let result: AgentNotes
            if let id = draft.noteID {
                result = try await client.editAgentNote(id, text: draft.text, topic: draft.topic,
                                                        subject: draft.subject, pinned: draft.pinned)
            } else {
                var added = try await client.addAgentNote(text: draft.text, topic: draft.topic,
                                                          subject: draft.subject)
                if draft.pinned, let note = added.note {
                    added = try await client.editAgentNote(note.id, pinned: true)
                }
                result = added
            }
            onDone(.success(result))
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
