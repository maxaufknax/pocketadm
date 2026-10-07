import SwiftUI

/// Coding agents that can be installed onto the server and then driven from the
/// Terminal tab.
struct CLIsView: View {
    @EnvironmentObject private var app: AppState

    @State private var tools: [CLITool] = []
    @State private var loaded = false
    @State private var job: PendingJob?
    @State private var toast: Toast?

    var body: some View {
        List {
            Section {
                ForEach(tools) { tool in
                    HStack(alignment: .top, spacing: 12) {
                        VStack(alignment: .leading, spacing: 3) {
                            HStack(spacing: 6) {
                                Text(tool.name)
                                    .font(.subheadline.weight(.medium))
                                    .foregroundStyle(Theme.text)
                                if tool.installed {
                                    StatusPill(text: tool.version.isEmpty ? "installed" : tool.version,
                                               tint: Theme.accent2)
                                }
                            }
                            Text(tool.tagline)
                                .font(.caption)
                                .foregroundStyle(Theme.muted)
                            Text("Needs \(tool.subscription)")
                                .font(.caption2)
                                .foregroundStyle(Theme.muted)
                        }

                        Spacer(minLength: 0)

                        if tool.installed {
                            Text("`\(tool.launch)`")
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundStyle(Theme.accent)
                        } else {
                            Button {
                                Task { await install(tool) }
                            } label: {
                                Image(systemName: "arrow.down.circle").font(.title3)
                            }
                            .buttonStyle(.plain)
                            .tint(Theme.accent)
                        }
                    }
                    .padding(.vertical, 3)
                }
            } header: {
                SectionCaption(text: "Coding agents")
            } footer: {
                Text("Installed onto the server itself. Start one from the Terminal tab and sign in there — the app never sees those credentials.")
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Coding agents")
        .screenBackground()
        .toast($toast)
        .task { if !loaded { await load() } }
        .sheet(item: $job) { pending in
            JobConsoleView(jobID: pending.id, title: pending.title) { _ in
                Task { await load() }
            }
        }
    }

    private func load() async {
        guard let client = app.client else { return }
        defer { loaded = true }
        tools = (try? await client.codingCLIs()) ?? []
    }

    private func install(_ tool: CLITool) async {
        guard let client = app.client else { return }
        do {
            let jobID = try await client.installCLI(tool.id)
            job = PendingJob(id: jobID, title: "Install \(tool.name)")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
            app.handle(error)
        }
    }
}
