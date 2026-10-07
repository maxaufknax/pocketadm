import SwiftUI

/// What the agent knows about this server between conversations, and which
/// tools it is allowed to reach for.
struct AgentSettingsView: View {
    @EnvironmentObject private var app: AppState

    @State private var memory = ""
    @State private var instructions = ""
    @State private var tools: [AgentTool] = []
    @State private var loaded = false
    @State private var savingMemory = false
    @State private var savingInstructions = false
    @State private var toast: Toast?

    var body: some View {
        List {
            Section {
                TextEditor(text: $instructions)
                    .frame(minHeight: 110)
                    .font(.system(size: 13))
                    .scrollContentBackground(.hidden)
                    .background(Theme.bg3,
                                in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    .foregroundStyle(Theme.text)

                Button {
                    Task { await saveInstructions() }
                } label: {
                    HStack {
                        Spacer()
                        if savingInstructions {
                            ProgressView().tint(Theme.accent)
                        } else {
                            Text("Save instructions")
                        }
                        Spacer()
                    }
                }
                .tint(Theme.accent)
                .disabled(savingInstructions)
            } header: {
                SectionCaption(text: "Custom instructions")
            } footer: {
                Text("Prepended to every conversation. Good for house rules — which stacks not to touch, which language to answer in.")
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
            }
            .listRowBackground(Theme.bg2)

            Section {
                TextEditor(text: $memory)
                    .frame(minHeight: 140)
                    .font(.system(size: 13, design: .monospaced))
                    .scrollContentBackground(.hidden)
                    .background(Theme.bg3,
                                in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    .foregroundStyle(Theme.text)

                Button {
                    Task { await saveMemory() }
                } label: {
                    HStack {
                        Spacer()
                        if savingMemory {
                            ProgressView().tint(Theme.accent)
                        } else {
                            Text("Save memory")
                        }
                        Spacer()
                    }
                }
                .tint(Theme.accent)
                .disabled(savingMemory)
            } header: {
                SectionCaption(text: "Memory")
            } footer: {
                Text("The agent writes here itself as it learns about the server. Editing by hand is allowed — it is a plain notes file.")
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
            }
            .listRowBackground(Theme.bg2)

            if !tools.isEmpty {
                Section {
                    ForEach(tools) { tool in
                        Toggle(isOn: Binding(
                            get: { tool.enabled },
                            set: { newValue in Task { await toggle(tool, enabled: newValue) } }
                        )) {
                            VStack(alignment: .leading, spacing: 2) {
                                HStack(spacing: 6) {
                                    Text(tool.name)
                                        .font(.subheadline.weight(.medium))
                                        .foregroundStyle(Theme.text)
                                    if !tool.safe {
                                        StatusPill(text: "writes", tint: Theme.warn)
                                    }
                                }
                                Text(tool.description)
                                    .font(.caption)
                                    .foregroundStyle(Theme.muted)
                                    .lineLimit(2)
                            }
                        }
                        .tint(Theme.accent)
                    }
                    .listRowBackground(Theme.bg2)
                } header: {
                    SectionCaption(text: "Tools")
                } footer: {
                    Text("A disabled tool is not offered to the model at all, in any mode.")
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                }
            }
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
        .background(Theme.bg)
        .navigationTitle("Agent")
        .screenBackground()
        .toast($toast)
        .task { if !loaded { await load() } }
    }

    private func load() async {
        guard let client = app.client else { return }
        defer { loaded = true }
        memory = (try? await client.agentMemory()) ?? ""
        instructions = (try? await client.agentInstructions()) ?? ""
        tools = (try? await client.agentTools()) ?? []
    }

    private func saveMemory() async {
        guard let client = app.client else { return }
        savingMemory = true
        defer { savingMemory = false }
        do {
            try await client.saveAgentMemory(memory)
            toast = Toast(text: "Memory saved")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
            app.handle(error)
        }
    }

    private func saveInstructions() async {
        guard let client = app.client else { return }
        savingInstructions = true
        defer { savingInstructions = false }
        do {
            try await client.saveAgentInstructions(instructions)
            toast = Toast(text: "Instructions saved")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
            app.handle(error)
        }
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
