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
                    HStack(alignment: .top, spacing: 14) {
                        ServiceIcon(names: [tool.vendor, tool.name], category: "Development", size: 40)

                        VStack(alignment: .leading, spacing: 3) {
                            HStack(spacing: 6) {
                                Text(tool.name)
                                    .foregroundStyle(Theme.text)
                                if tool.installed && !tool.version.isEmpty {
                                    StatusPill(text: tool.version, tint: .green)
                                }
                            }
                            Text(tool.tagline)
                                .font(.footnote)
                                .foregroundStyle(Theme.muted)
                            if tool.installed {
                                Text("Start it in the Terminal with `\(tool.launch)`")
                                    .font(.caption)
                                    .foregroundStyle(Theme.muted)
                            } else {
                                Text("Needs \(tool.subscription)")
                                    .font(.caption)
                                    .foregroundStyle(Theme.muted)
                            }
                        }

                        Spacer(minLength: 8)

                        if tool.installed {
                            Image(systemName: "checkmark.circle.fill")
                                .font(.title3)
                                .foregroundStyle(.green)
                                .accessibilityLabel("Installed")
                        } else {
                            Button {
                                Task { await install(tool) }
                            } label: {
                                Text("Install")
                                    .font(.subheadline.weight(.semibold))
                                    .padding(.horizontal, 14)
                                    .padding(.vertical, 5)
                                    .background(Theme.bg3, in: Capsule())
                            }
                            .buttonStyle(.plain)
                            .foregroundStyle(Theme.accent)
                        }
                    }
                    .padding(.vertical, 4)
                }
            } footer: {
                Text("Installed onto the server itself. Start one from the Terminal tab and sign in there — the app never sees those credentials.")
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Coding agents")
        .navigationBarTitleDisplayMode(.inline)
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
