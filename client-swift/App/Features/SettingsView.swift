import SwiftUI
import UIKit

/// Connection, identity and this device.
///
/// Pushed from the More hub, so it deliberately has no `NavigationStack` of its
/// own — nesting one inside another breaks the back button and the large-title
/// collapse.
struct SettingsView: View {
    @EnvironmentObject private var app: AppState

    @State private var showPairSheet = false
    @State private var confirmForget = false
    @State private var editingName = false
    @State private var serverName = ""
    @State private var toast: Toast?

    var body: some View {
        List {
            Section {
                FactRow(label: "Server", value: app.serverName.isEmpty ? "—" : app.serverName)
                HairlineDivider()
                FactRow(label: "Address",
                        value: app.serverURL?.absoluteString ?? "—",
                        selectable: true)
                if let me = app.me {
                    HairlineDivider()
                    FactRow(label: "Version", value: me.version)
                    HairlineDivider()
                    FactRow(label: "Hostname", value: me.hostname)
                    if me.demo {
                        HairlineDivider()
                        FactRow(label: "Mode", value: "demo — data is simulated", tint: Theme.warn)
                    }
                }
            } header: {
                SectionCaption(text: "Connection")
            }
            .listRowBackground(Theme.bg2)

            Section {
                Button {
                    serverName = app.serverName
                    editingName = true
                } label: {
                    NavRow(symbol: "tag", title: "Rename this server",
                           subtitle: "Shown on every device that connects")
                }

                Button {
                    showPairSheet = true
                } label: {
                    NavRow(symbol: "qrcode", title: "Pair another device",
                           subtitle: app.me?.canPair == false
                             ? "Disabled on this server"
                             : "Sign in a second phone with no password")
                }
                .disabled(app.me?.canPair == false)
            } header: {
                SectionCaption(text: "Devices")
            } footer: {
                Text("A pairing code works once and grants full access. Anyone who scans it is in.")
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
            }
            .listRowBackground(Theme.bg2)

            if let workspaces = app.me?.workspaces, !workspaces.isEmpty {
                Section {
                    ForEach(workspaces, id: \.self) { path in
                        HStack {
                            Text(path)
                                .font(.system(size: 13, design: .monospaced))
                                .foregroundStyle(Theme.text)
                            Spacer()
                            if path == app.me?.defaultWorkspace {
                                StatusPill(text: "default", tint: Theme.accent)
                            }
                        }
                    }
                } header: {
                    SectionCaption(text: "Workspaces")
                } footer: {
                    Text("The only directories the agent's file tools and the file browser can reach.")
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                }
                .listRowBackground(Theme.bg2)
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
            }
            .listRowBackground(Theme.bg2)

            Section {
                Text("PocketADM native · built with SwiftUI. The terminal is a real xterm emulator and the assistant runs on your server, not on this phone.")
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
            }
            .listRowBackground(Theme.bg)
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
        .background(Theme.bg)
        .navigationTitle("Settings")
        .screenBackground()
        .toast($toast)
        .sheet(isPresented: $showPairSheet) { PairCodeSheet() }
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

                    Text("The code works once. Anyone who scans it gets full access to this server.")
                        .font(.caption)
                        .foregroundStyle(Theme.warn)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 30)
                } else if let error {
                    MessageState(symbol: "exclamationmark.triangle",
                                 title: "Could not create a code",
                                 message: error,
                                 tint: Theme.danger,
                                 retry: { Task { await load() } })
                } else {
                    ProgressView().tint(Theme.accent)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle("Pair a device")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.bg2, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }.tint(Theme.accent)
                }
            }
            .task { await load() }
        }
    }

    /// Must match what the web client encodes — `<origin>/pair?code=<code>` —
    /// so a code from either client scans on either client.
    private func payload(_ pair: PairCode) -> String {
        let origin = app.serverURL?.absoluteString
            .trimmingCharacters(in: CharacterSet(charactersIn: "/")) ?? ""
        return "\(origin)/pair?code=\(pair.code)"
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
