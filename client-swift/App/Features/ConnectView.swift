import SwiftUI
import UIKit

/// First run. PocketADM is self-hosted, so there is no server to assume — the
/// app has to be told where to look, and then prove it found the right thing.
struct ConnectView: View {
    @EnvironmentObject private var app: AppState

    @State private var address = ""
    @State private var probing = false
    @State private var error: String?
    @State private var showScanner = false
    @State private var claiming = false
    @State private var openingDemo = false
    @FocusState private var addressFocused: Bool

    var body: some View {
        ScrollView {
            VStack(spacing: 22) {
                header

                VStack(spacing: 14) {
                    Button {
                        error = nil
                        showScanner = true
                    } label: {
                        Label("Scan pairing code", systemImage: "qrcode.viewfinder")
                    }
                    .buttonStyle(PrimaryButtonStyle())

                    Text("Scan the QR your server's installer printed — or, on a device that is already signed in, open Settings → Pair another device.")
                        .font(.footnote)
                        .foregroundStyle(Theme.muted)
                        .multilineTextAlignment(.center)
                }
                .card(padding: 18)

                dividerRow

                addressEntry

                if let error {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(Theme.danger)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }

                demoEntry

                installHint
            }
            .padding(20)
        }
        .background(Theme.bg.ignoresSafeArea())
        .scrollDismissesKeyboard(.interactively)
        .sheet(isPresented: $showScanner) {
            PairScanSheet(isBusy: $claiming) { payload in
                Task { await claim(payload) }
            }
        }
    }

    private var header: some View {
        VStack(spacing: 10) {
            Image(systemName: "server.rack")
                .font(.system(size: 44, weight: .light))
                .foregroundStyle(Theme.accent)
            Text("PocketADM")
                .font(.system(.largeTitle, design: .rounded).weight(.bold))
                .foregroundStyle(Theme.text)
            Text("Connect to your server")
                .font(.subheadline)
                .foregroundStyle(Theme.muted)
        }
        .padding(.top, 28)
        .padding(.bottom, 4)
    }

    private var dividerRow: some View {
        HStack(spacing: 12) {
            Rectangle().fill(Theme.border).frame(height: 1)
            Text("or enter the address")
                .font(.caption)
                .foregroundStyle(Theme.muted)
                .fixedSize()
            Rectangle().fill(Theme.border).frame(height: 1)
        }
    }

    private var addressEntry: some View {
        VStack(alignment: .leading, spacing: 12) {
            TextField("192.168.1.10:8090", text: $address)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .submitLabel(.go)
                .focused($addressFocused)
                .onSubmit { Task { await probe() } }
                .padding(12)
                .background(Theme.bg3, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .foregroundStyle(Theme.text)

            Button {
                Task { await probe() }
            } label: {
                if probing {
                    ProgressView().tint(Theme.onAccent)
                } else {
                    Text("Continue")
                }
            }
            .buttonStyle(PrimaryButtonStyle(enabled: !address.isEmpty && !probing))
            .disabled(address.isEmpty || probing)

            Text("Enter a hostname or IP. Without http:// or https://, both are tried.")
                .font(.caption)
                .foregroundStyle(Theme.muted)
        }
        .card(padding: 18)
    }

    /// The one path on this screen that needs nothing but the internet: a real,
    /// running server with sample data. Someone evaluating the app — App Review
    /// included — otherwise meets only buttons that demand a server they lack.
    private var demoEntry: some View {
        VStack(spacing: 8) {
            Button {
                Task { await openDemo() }
            } label: {
                if openingDemo {
                    ProgressView().tint(Theme.accent)
                } else {
                    Label("Try the live demo", systemImage: "play.circle")
                }
            }
            .buttonStyle(SecondaryButtonStyle())
            .disabled(openingDemo)

            Text("A real PocketADM server, read-only. No signup, nothing to install.")
                .font(.caption)
                .foregroundStyle(Theme.muted)
                .multilineTextAlignment(.center)
        }
        .padding(.top, 4)
    }

    private var installHint: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("No server yet?")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Theme.text)
            Text("Run this on any Linux server. It installs Docker if needed, starts PocketADM over HTTPS and ends with a QR code to scan here.")
                .font(.caption)
                .foregroundStyle(Theme.muted)
            Text("curl -fsSL https://raw.githubusercontent.com/maxaufknax/pocketadm/main/install.sh | bash")
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(Theme.text)
                .textSelection(.enabled)
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.bg3, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
        .card(padding: 16)
    }

    // MARK: - Actions

    private func openDemo() async {
        openingDemo = true
        error = nil
        defer { openingDemo = false }
        do {
            try await app.openDemo()
        } catch let failure {
            // the button stays usable: a flaky network deserves a second tap
            error = "The demo server did not answer (\(failure.localizedDescription)). Try again in a moment."
        }
    }

    /// Walks the candidate URLs and keeps the first that answers /api/info as a
    /// real PocketADM. Probing *before* asking for a password means a typo
    /// reads as "wrong address" instead of "wrong password".
    private func probe() async {
        addressFocused = false
        probing = true
        error = nil
        defer { probing = false }

        let candidates = ServerURL.candidates(from: address)
        guard !candidates.isEmpty else {
            error = "That does not look like a server address."
            return
        }

        var lastError: String?
        for url in candidates {
            do {
                let info = try await APIClient(baseURL: url).info()
                app.adopt(url: url, info: info)
                return
            } catch {
                lastError = error.localizedDescription
            }
        }
        error = lastError ?? "Could not reach that server."
    }

    private func claim(_ payload: PairingPayload) async {
        claiming = true
        defer { claiming = false }
        // The QR is the trust decision: pin the key it names *before* the first
        // request, so even the claim only ever talks to that exact server.
        if let fingerprint = payload.fingerprint {
            TrustStore.setPin(fingerprint, for: payload.serverURL)
        }
        do {
            let client = APIClient(baseURL: payload.serverURL)
            // Identify the server first so its name and 2FA status are known
            // even though pairing skips the login screen entirely.
            let info = try? await client.info()
            let result = try await client.pairClaim(code: payload.code)
            if let info {
                app.adopt(url: payload.serverURL, info: info)
            }
            app.signIn(token: result.token, serverName: result.serverName ?? info?.serverName)
            showScanner = false
        } catch {
            showScanner = false
            self.error = error.localizedDescription
        }
    }
}

/// The scanner in a sheet, with the camera behind a dark chrome so the cutout
/// reads as a viewfinder rather than a broken full-screen video.
struct PairScanSheet: View {
    @Binding var isBusy: Bool
    var onPayload: (PairingPayload) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()

                QRScannerView(
                    onScan: { raw in
                        guard let payload = PairingPayload(scanned: raw) else {
                            problem = "That QR code is not a PocketADM pairing code."
                            return
                        }
                        onPayload(payload)
                    },
                    onError: { problem = $0 }
                )
                .ignoresSafeArea()

                viewfinder

                if isBusy {
                    ProgressView("Pairing…")
                        .tint(.white)
                        .foregroundStyle(.white)
                        .padding(20)
                        .background(.black.opacity(0.7), in: RoundedRectangle(cornerRadius: 12))
                }
            }
            .navigationTitle("Scan to pair")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { dismiss() }
                        .tint(Theme.accent)
                }
                // The installer also prints the link as text: copied from an SSH
                // session (Universal Clipboard, a message to yourself) it pairs
                // without a camera.
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        pasteLink()
                    } label: {
                        Label("Paste pairing link", systemImage: "doc.on.clipboard")
                    }
                    .tint(Theme.accent)
                }
            }
            .toolbarBackground(Theme.bg2, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .alert("Cannot scan", isPresented: .constant(problem != nil)) {
                Button("OK") { problem = nil }
            } message: {
                Text(problem ?? "")
            }
        }
    }

    private func pasteLink() {
        guard let text = UIPasteboard.general.string,
              let payload = PairingPayload(scanned: text) else {
            problem = "The clipboard holds no PocketADM pairing link. The installer prints one "
                + "after \u{201C}PAIRING_LINK:\u{201D}."
            return
        }
        onPayload(payload)
    }

    private var viewfinder: some View {
        VStack {
            Spacer()
            RoundedRectangle(cornerRadius: 20, style: .continuous)
                .stroke(Theme.accent, lineWidth: 3)
                .frame(width: 230, height: 230)
            Spacer()
            Text("Point the camera at the QR code from your server's installer, or the one a signed-in device shows.")
                .font(.footnote)
                .foregroundStyle(.white.opacity(0.85))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)
                // Keeps the caption off the home indicator; the scanner itself
                // deliberately runs edge to edge behind it.
                .padding(.bottom, 28)
        }
    }
}
