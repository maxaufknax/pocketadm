import SwiftUI

/// API keys, the default model, and what it has cost so far.
struct AISettingsView: View {
    @EnvironmentObject private var app: AppState

    /// Provider -> the key the user typed. Never pre-filled: the server does
    /// not hand keys back out, and an empty field means "leave it alone".
    @State private var keys: [String: String] = [:]
    @State private var models: AIModels?
    @State private var usage: AIUsage?
    @State private var defaultProvider = ""
    @State private var defaultModel = ""
    @State private var saving = false
    @State private var toast: Toast?

    private static let providers = ["anthropic", "openai", "openrouter", "mistral"]

    private static let labels = [
        "anthropic": "Anthropic",
        "openai": "OpenAI",
        "openrouter": "OpenRouter",
        "mistral": "Mistral",
    ]

    private static let hints = [
        "anthropic": "console.anthropic.com → API keys",
        "openai": "platform.openai.com → API keys",
        "openrouter": "openrouter.ai → Keys · one key, many models, has a free tier",
        "mistral": "console.mistral.ai → API keys",
    ]

    var body: some View {
        ThemedList {
            Section {
                ForEach(Self.providers, id: \.self) { provider in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 12) {
                            ServiceIcon(names: [provider], category: "AI", size: 30)
                            Text(Self.labels[provider] ?? provider)
                                .foregroundStyle(Theme.text)
                            Spacer()
                            if configured.contains(provider) {
                                StatusDot(text: "Key set", tint: .green)
                            }
                        }
                        SecureField(configured.contains(provider) ? "Replace key" : "Paste key",
                                    text: Binding(get: { keys[provider] ?? "" },
                                                  set: { keys[provider] = $0 }))
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .font(.system(.subheadline, design: .monospaced))
                            .padding(.horizontal, 12)
                            .padding(.vertical, 10)
                            .background(Theme.bg3,
                                        in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                            .foregroundStyle(Theme.text)
                        Text(Self.hints[provider] ?? "")
                            .font(.caption)
                            .foregroundStyle(Theme.muted)
                        if configured.contains(provider) {
                            Button("Remove key") {
                                // "-" is the server's clear sentinel; "" means
                                // "keep what you have".
                                keys[provider] = "-"
                            }
                            .font(.caption)
                            .tint(Theme.danger)
                        }
                    }
                    .padding(.vertical, 4)
                }
            } header: {
                SectionCaption(text: "Providers")
            } footer: {
                Text("Keys are stored on your server, not on this phone, and are never sent anywhere else.")
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
            }

            if let models, !models.providers.isEmpty {
                Section {
                    ForEach(models.providers) { entry in
                        DisclosureGroup {
                            ForEach(entry.models) { model in
                                Button {
                                    defaultProvider = entry.provider
                                    defaultModel = model.id
                                } label: {
                                    HStack {
                                        Text(model.name)
                                            .foregroundStyle(Theme.text)
                                        Spacer()
                                        if defaultProvider == entry.provider
                                            && defaultModel == model.id {
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
                                    // a coding agent CLI on the server, using
                                    // its own login and subscription
                                    StatusPill(text: "your subscription", tint: Theme.accent)
                                }
                            }
                        }
                    }
                } header: {
                    SectionCaption(text: "Default model")
                } footer: {
                    Text(defaultModel.isEmpty ? "No default set."
                         : "New chats start on \(defaultModel).")
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                }
            }

            Section {
                NavigationLink {
                    LocalAIView()
                } label: {
                    NavRow(symbol: "cpu", title: "Local models",
                           subtitle: "Run a model on the server itself — no keys, no cost",
                           tint: .teal)
                }

                NavigationLink {
                    AgentSettingsView()
                } label: {
                    NavRow(symbol: "wrench.and.screwdriver.fill", title: "Agent behaviour",
                           subtitle: "Memory, instructions and which tools it may use",
                           tint: .indigo)
                }
            }

            if let usage {
                Section("Usage") {
                    FactRow(label: "Today",
                            value: "\(Fmt.money(usage.today.cost)) · \(usage.today.requests) requests")
                    FactRow(label: "This month",
                            value: "\(Fmt.money(usage.month.cost)) · \(usage.month.requests) requests")
                    FactRow(label: "Tokens this month",
                            value: "\(Fmt.count(usage.month.input)) in · \(Fmt.count(usage.month.output)) out")
                }
            }

        }
        .listStyle(.insetGrouped)
        .navigationTitle("AI models")
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                if saving {
                    ProgressView()
                } else {
                    Button("Save") { Task { await save() } }
                        .fontWeight(.semibold)
                }
            }
        }
        .screenBackground()
        .toast($toast)
        .task { await load() }
    }

    private var configured: [String] { app.me?.aiProviders ?? [] }

    private func load() async {
        guard let client = app.client else { return }
        await app.refreshMe()
        defaultProvider = app.me?.aiDefault.provider ?? ""
        defaultModel = app.me?.aiDefault.model ?? ""
        models = try? await client.aiModels()
        usage = try? await client.aiUsage()
    }

    private func save() async {
        guard let client = app.client else { return }
        saving = true
        defer { saving = false }
        // Only send fields that were actually touched — an empty string means
        // "keep the stored key", so posting all four would be harmless but
        // sending nothing at all when nothing changed is clearer.
        let changes = keys.filter { !$0.value.isEmpty }
        do {
            try await client.saveAISettings(keys: changes,
                                            defaultProvider: defaultProvider,
                                            defaultModel: defaultModel)
            keys = [:]
            await load()
            toast = Toast(text: "Saved")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
            app.handle(error)
        }
    }
}
