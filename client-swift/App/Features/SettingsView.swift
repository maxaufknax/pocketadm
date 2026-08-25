import CoreImage.CIFilterBuiltins
import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var app: AppState

    @State private var me: MeResponse?
    @State private var showPairSheet = false
    @State private var confirmForget = false

    var body: some View {
        NavigationStack {
            List {
                Section {
                    row("Server", app.serverName.isEmpty ? "—" : app.serverName)
                    row("Address", app.serverURL?.absoluteString ?? "—")
                    if let me {
                        row("Version", me.version)
                        row("Hostname", me.hostname)
                        row("2FA", me.totpEnabled ? "on" : "off")
                    }
                } header: {
                    Text("CONNECTION").font(.caption2.weight(.semibold)).foregroundStyle(Theme.muted)
                }
                .listRowBackground(Theme.bg2)

                Section {
                    Button {
                        showPairSheet = true
                    } label: {
                        Label("Pair another device", systemImage: "qrcode")
                            .foregroundStyle(Theme.accent)
                    }
                    .disabled(me?.canPair == false)
                } header: {
                    Text("DEVICES").font(.caption2.weight(.semibold)).foregroundStyle(Theme.muted)
                } footer: {
                    Text(me?.canPair == false
                         ? "This server has pairing disabled."
                         : "Shows a QR code another phone can scan to sign itself in — no password needed.")
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                }
                .listRowBackground(Theme.bg2)

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
                    Text("Native SwiftUI preview build. Dashboard, containers and terminal are live; Vibe Code, App Store, Updates and Checks are not built yet.")
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
            .sheet(isPresented: $showPairSheet) { PairCodeSheet() }
            .alert("Forget this server?", isPresented: $confirmForget) {
                Button("Forget", role: .destructive) { app.forgetServer() }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("The address and token are removed from this device.")
            }
            .task {
                guard let client = app.client else { return }
                me = try? await client.me()
            }
        }
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).foregroundStyle(Theme.muted)
            Spacer()
            Text(value)
                .foregroundStyle(Theme.text)
                .lineLimit(1)
                .truncationMode(.middle)
        }
        .font(.subheadline)
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
                if let pair, let image = qrImage(for: payload(pair)) {
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
        let origin = app.serverURL?.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")) ?? ""
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

    /// Rendered on-device. The server offers POST /api/qr, but a round trip for
    /// something CoreImage draws locally would only add a failure mode.
    private func qrImage(for text: String) -> UIImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(text.utf8)
        filter.correctionLevel = "M"
        guard let output = filter.outputImage else { return nil }
        // The generator emits roughly one pixel per module; scaling up before
        // rasterising is what keeps the code crisp instead of a blurry mess.
        let scaled = output.transformed(by: CGAffineTransform(scaleX: 10, y: 10))
        let context = CIContext()
        guard let cgImage = context.createCGImage(scaled, from: scaled.extent) else { return nil }
        return UIImage(cgImage: cgImage)
    }
}
