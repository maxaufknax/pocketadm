import SwiftUI
import UIKit

/// The server this device is connected to: what it is, its name, the
/// directories the agent may touch, and leaving it.
///
/// Pushed from the More tab, so it deliberately has no `NavigationStack` of its
/// own — nesting one inside another breaks the back button and the large-title
/// collapse.
struct SettingsView: View {
    @EnvironmentObject private var app: AppState

    @State private var confirmForget = false
    @State private var editingName = false
    @State private var serverName = ""
    @State private var toast: Toast?

    var body: some View {
        List {
            Section("Connection") {
                FactRow(label: "Name", value: app.serverName.isEmpty ? "—" : app.serverName)
                FactRow(label: "Address", value: app.serverURL?.absoluteString ?? "—", selectable: true)
                if let me = app.me {
                    FactRow(label: "Version", value: me.version)
                    FactRow(label: "Hostname", value: me.hostname)
                    if me.demo {
                        FactRow(label: "Mode", value: "Demo with sample data", tint: .orange)
                    }
                }
            }

            Section {
                Button {
                    serverName = app.serverName
                    editingName = true
                } label: {
                    Label("Rename server", systemImage: "pencil")
                }
            } footer: {
                Text("The name every device shows for this server.")
            }

            if let workspaces = app.me?.workspaces, !workspaces.isEmpty {
                Section {
                    ForEach(workspaces, id: \.self) { path in
                        HStack {
                            Label {
                                Text(path)
                                    .font(.system(.body, design: .monospaced))
                                    .foregroundStyle(Theme.text)
                            } icon: {
                                Image(systemName: "folder")
                            }
                            Spacer()
                            if path == app.me?.defaultWorkspace {
                                StatusPill(text: "default", tint: Theme.accent)
                            }
                        }
                    }
                } header: {
                    Text("Workspaces")
                } footer: {
                    Text("The only directories the agent's file tools and the file browser can reach.")
                }
            }

            Section {
                Button(role: .destructive) {
                    app.signOut()
                } label: {
                    Label("Sign out", systemImage: "rectangle.portrait.and.arrow.right")
                }
                Button(role: .destructive) {
                    confirmForget = true
                } label: {
                    Label("Forget this server", systemImage: "trash")
                }
            } footer: {
                Text("Signing out keeps the address for next time. Forgetting removes the server from this device.")
            }
        }
        .navigationTitle("Server")
        .navigationBarTitleDisplayMode(.inline)
        .toast($toast)
        .alert("Rename server", isPresented: $editingName) {
            TextField("Name", text: $serverName)
            Button("Save") { Task { await rename() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This is the name every client shows for this box.")
        }
        .alert("Forget this server?", isPresented: $confirmForget) {
            Button("Forget", role: .destructive) { app.forgetServer() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The address and token are removed from this device.")
        }
        .task { await app.refreshMe() }
    }

    private func rename() async {
        guard let client = app.client else { return }
        do {
            _ = try await client.setServerName(serverName)
            await app.refreshMe()
            toast = Toast(text: "Renamed")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
            app.handle(error)
        }
    }
}

/// The other half of pairing: this device mints a code and renders the QR the
/// new device scans.
struct PairCodeSheet: View {
    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var pair: PairCode?
    @State private var error: String?
    @State private var expiresAt: Date?

    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                if let pair, let image = QRRenderer.image(for: payload(pair)) {
                    Image(uiImage: image)
                        .interpolation(.none)
                        .resizable()
                        .scaledToFit()
                        .frame(width: 250, height: 250)
                        .padding(14)
                        .background(.white, in: RoundedRectangle(cornerRadius: 14, style: .continuous))

                    VStack(spacing: 6) {
                        Text("Scan this on the new device")
                            .font(.headline)
                            .foregroundStyle(Theme.text)
                        if let expiresAt {
                            Text("Expires \(expiresAt, style: .relative) from now")
                                .font(.caption)
                                .foregroundStyle(Theme.muted)
                        }
                    }

                    Label("The code works once. Anyone who scans it gets full access to this server.",
                          systemImage: "exclamationmark.shield.fill")
                        .font(.footnote)
                        .foregroundStyle(.orange)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 30)
                } else if let error {
                    MessageState(symbol: "exclamationmark.triangle",
                                 title: "Could not create a code",
                                 message: error,
                                 tint: Theme.danger,
                                 retry: { Task { await load() } })
                } else {
                    ProgressView()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle("Pair a device")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .task { await load() }
        }
    }

    /// The link every client reads — `<origin>/?pair=CODE&fp=KEY` — so a code
    /// from this app scans in the web app and in the 1.0 App Store build too.
    /// `fp` lets the new device pin a server that uses its own certificate.
    private func payload(_ pair: PairCode) -> String {
        guard let server = app.serverURL else { return "" }
        return PairingPayload.link(serverURL: server, code: pair.code,
                                   fingerprint: pair.tlsFingerprint)
    }

    private func load() async {
        guard let client = app.client else { return }
        do {
            let pair = try await client.pairNew()
            self.pair = pair
            expiresAt = Date().addingTimeInterval(TimeInterval(pair.ttl))
            error = nil
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
    }
}
