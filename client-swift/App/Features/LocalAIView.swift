import SwiftUI

/// Ollama on the server: install it, pull a model, and the assistant runs with
/// no API key and no per-token cost.
struct LocalAIView: View {
    @EnvironmentObject private var app: AppState

    @State private var status: LocalAIStatus?
    @State private var loaded = false
    @State private var error: String?
    @State private var job: PendingJob?
    @State private var toast: Toast?
    @State private var confirmDelete: LocalAIStatus.InstalledModel?

    var body: some View {
        Group {
            if !loaded {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let status {
                content(status)
            } else {
                MessageState(symbol: "exclamationmark.triangle",
                             title: "Cannot reach local AI",
                             message: error,
                             tint: Theme.danger,
                             retry: { Task { await load() } })
            }
        }
        .navigationTitle("Local models")
        .screenBackground()
        .toast($toast)
        .task { if !loaded { await load() } }
        .sheet(item: $job) { pending in
            JobConsoleView(jobID: pending.id, title: pending.title) { _ in
                Task { await load() }
            }
        }
        .confirmationDialog("Delete \(confirmDelete?.name ?? "")?",
                            isPresented: Binding(get: { confirmDelete != nil },
                                                 set: { if !$0 { confirmDelete = nil } }),
                            titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                if let model = confirmDelete { Task { await delete(model) } }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The model files are removed from the server. It can be pulled again later.")
        }
    }

    private func content(_ status: LocalAIStatus) -> some View {
        ThemedList {
            Section {
                FactRow(label: "Ollama",
                        value: status.running ? "running" : "not running",
                        tint: status.running ? Theme.accent2 : Theme.muted)
                if !status.version.isEmpty {
                    FactRow(label: "Version", value: status.version)
                }
                if !status.base.isEmpty {
                    FactRow(label: "Address", value: status.base, selectable: true)
                }
                FactRow(label: "Hardware",
                        value: String(format: "%.0f GB RAM · %d cores", status.ramGB, status.cpuCount))
            }

            if !status.running {
                Section {
                    if status.canInstall {
                        Button {
                            Task { await install() }
                        } label: {
                            Label("Install Ollama", systemImage: "arrow.down.circle")
                                .foregroundStyle(Theme.accent)
                        }
                    }
                    Button {
                        Task { await connect() }
                    } label: {
                        Label("Connect to an existing Ollama", systemImage: "link")
                            .foregroundStyle(Theme.accent)
                    }
                } header: {
                    SectionCaption(text: "Set up")
                } footer: {
                    Text(status.canInstall
                         ? "Installing runs Ollama as a container next to PocketADM."
                         : "Docker is not reachable from here, so this server cannot install it itself.")
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                }
            }

            if !status.installed.isEmpty {
                Section {
                    ForEach(status.installed) { model in
                        HStack {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(model.name)
                                    .font(.subheadline.weight(.medium))
                                    .foregroundStyle(Theme.text)
                                Text([model.params, model.quant, Fmt.bytes(model.size)]
                                        .filter { !$0.isEmpty }
                                        .joined(separator: " · "))
                                    .font(.caption)
                                    .foregroundStyle(Theme.muted)
                            }
                            Spacer()
                        }
                        .swipeActions(edge: .trailing) {
                            Button(role: .destructive) {
                                confirmDelete = model
                            } label: { Label("Delete", systemImage: "trash") }
                        }
                    }
                } header: {
                    SectionCaption(text: "Installed")
                }
            }

            Section {
                ForEach(status.recommended) { model in
                    RecommendedModelRow(model: model) {
                        await pull(model.name)
                    }
                }
            } header: {
                SectionCaption(text: "Recommended for this box")
            } footer: {
                Text("Sizes are the download. A model needs roughly its own size in free RAM to run at a usable speed.")
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await load() }
    }

    // MARK: - Actions

    private func load() async {
        guard let client = app.client else { return }
        defer { loaded = true }
        do {
            status = try await client.localAI()
            error = nil
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
    }

    private func install() async {
        guard let client = app.client else { return }
        do {
            let jobID = try await client.installLocalAI()
            job = PendingJob(id: jobID, title: "Install Ollama")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func connect() async {
        guard let client = app.client else { return }
        do {
            status = try await client.connectLocalAI()
            toast = Toast(text: "Connected")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func pull(_ name: String) async {
        guard let client = app.client else { return }
        do {
            let jobID = try await client.pullModel(name)
            job = PendingJob(id: jobID, title: "Pull \(name)")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func delete(_ model: LocalAIStatus.InstalledModel) async {
        guard let client = app.client else { return }
        confirmDelete = nil
        do {
            try await client.deleteModel(model.name)
            await load()
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }
}

struct RecommendedModelRow: View {
    let model: LocalAIStatus.RecommendedModel
    let onPull: () async -> Void

    @State private var starting = false

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(model.label.isEmpty ? model.name : model.label)
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(Theme.text)
                    if model.coder { StatusPill(text: "coding", tint: Theme.accent) }
                    if model.suggested { StatusPill(text: "pick", tint: Theme.accent2) }
                }
                Text(model.blurb)
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
                Text("\(model.size) · needs \(model.minRAM) GB RAM")
                    .font(.caption2)
                    .foregroundStyle(model.fits ? Theme.muted : Theme.warn)
            }

            Spacer(minLength: 0)

            if model.installed {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(Theme.accent2)
            } else if starting {
                ProgressView().tint(Theme.muted)
            } else {
                Button {
                    starting = true
                    Task { await onPull(); starting = false }
                } label: {
                    Image(systemName: "arrow.down.circle")
                        .font(.title3)
                }
                .buttonStyle(.plain)
                .tint(model.fits ? Theme.accent : Theme.warn)
            }
        }
        .padding(.vertical, 3)
    }
}
