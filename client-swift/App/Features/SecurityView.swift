import CoreImage.CIFilterBuiltins
import SwiftUI
import UIKit

/// Password, two-factor and other devices.
///
/// Two operations here invalidate every token on the account — including this
/// phone's. The server hands back a replacement in the same response, and
/// `app.replaceToken` stores it; dropping it would sign the admin out of the
/// device they are holding.
struct SecurityView: View {
    @EnvironmentObject private var app: AppState

    @State private var currentPassword = ""
    @State private var newPassword = ""
    @State private var confirmPassword = ""
    @State private var changing = false

    @State private var showTOTPSetup = false
    @State private var confirmDisable = false
    @State private var disablePassword = ""
    @State private var disableCode = ""

    @State private var confirmRevoke = false
    @State private var toast: Toast?

    private var totpEnabled: Bool { app.me?.totpEnabled ?? false }

    var body: some View {
        ThemedList {
            if app.me?.shouldWarnAboutExposure == true {
                Section {
                    WarningBanner(
                        title: "Reachable from the internet without 2FA",
                        message: "This app can open a root shell. On a public address, a password alone is the only thing between the internet and your server.",
                        tint: Theme.danger,
                        actionTitle: "Turn on 2FA",
                        action: { showTOTPSetup = true }
                    )
                    Button("I know — stop warning me") {
                        Task { await acknowledgeExposure() }
                    }
                    .font(.caption)
                    .tint(Theme.muted)
                }
                .listRowBackground(Theme.bg)
            }

            Section {
                SecureField("Current password", text: $currentPassword)
                    .textContentType(.password)
                SecureField("New password", text: $newPassword)
                    .textContentType(.newPassword)
                SecureField("Repeat new password", text: $confirmPassword)
                    .textContentType(.newPassword)

                Button {
                    Task { await changePassword() }
                } label: {
                    HStack {
                        Spacer()
                        if changing { ProgressView() } else { Text("Change password") }
                        Spacer()
                    }
                }
                .tint(Theme.accent)
                .disabled(!canChangePassword || changing)
            } header: {
                SectionCaption(text: "Password")
            } footer: {
                Text(passwordFooter)
                    .font(.caption)
                    .foregroundStyle(passwordMismatch ? Theme.danger : Theme.muted)
            }

            Section {
                HStack(spacing: 14) {
                    IconTile(symbol: totpEnabled ? "lock.shield.fill" : "lock.shield",
                             color: totpEnabled ? .green : .orange)
                    Text("Code from an authenticator app")
                        .foregroundStyle(Theme.text)
                    Spacer()
                    Text(totpEnabled ? "On" : "Off")
                        .foregroundStyle(totpEnabled ? Color.green : Theme.muted)
                }

                if totpEnabled {
                    Button("Turn off 2FA", role: .destructive) { confirmDisable = true }
                } else {
                    Button("Set up 2FA") { showTOTPSetup = true }
                        .tint(Theme.accent)
                }
            } header: {
                SectionCaption(text: "Two-factor authentication")
            } footer: {
                Text("A code from an authenticator app, on top of the password. Paired devices keep working — pairing already proves possession.")
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
            }

            Section {
                Button("Sign out all other devices", role: .destructive) { confirmRevoke = true }
            } header: {
                SectionCaption(text: "Devices")
            } footer: {
                Text("Every other phone, tablet and browser is signed out immediately. This device stays signed in.")
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Password & 2FA")
        .navigationBarTitleDisplayMode(.inline)
        .toast($toast)
        .task { await app.refreshMe() }
        .sheet(isPresented: $showTOTPSetup) {
            TOTPSetupSheet {
                Task { await app.refreshMe() }
                toast = Toast(text: "2FA is on")
            }
        }
        .alert("Turn off 2FA?", isPresented: $confirmDisable) {
            SecureField("Password", text: $disablePassword)
            TextField("Current 6-digit code", text: $disableCode)
            Button("Turn off", role: .destructive) { Task { await disableTOTP() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The server asks for both your password and a current code before it will disable this.")
        }
        .confirmationDialog("Sign out all other devices?",
                            isPresented: $confirmRevoke, titleVisibility: .visible) {
            Button("Sign them out", role: .destructive) { Task { await revoke() } }
            Button("Cancel", role: .cancel) {}
        }
    }

    // MARK: - Password

    private var passwordMismatch: Bool {
        !confirmPassword.isEmpty && newPassword != confirmPassword
    }

    private var canChangePassword: Bool {
        !currentPassword.isEmpty && newPassword.count >= 8 && newPassword == confirmPassword
    }

    private var passwordFooter: String {
        if passwordMismatch { return "The two new passwords do not match." }
        if !newPassword.isEmpty && newPassword.count < 8 {
            return "At least 8 characters — the server rejects anything shorter."
        }
        return "Changing the password signs out every other device."
    }

    private func changePassword() async {
        guard let client = app.client else { return }
        changing = true
        defer { changing = false }
        do {
            if let token = try await client.changePassword(current: currentPassword,
                                                           new: newPassword) {
                app.replaceToken(token)
            }
            currentPassword = ""
            newPassword = ""
            confirmPassword = ""
            toast = Toast(text: "Password changed")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
            // Deliberately not app.handle(): a 403 here means "wrong current
            // password", not a dead session, and signing out would be absurd.
        }
    }

    // MARK: - 2FA & sessions

    private func disableTOTP() async {
        guard let client = app.client else { return }
        do {
            try await client.disableTOTP(password: disablePassword, code: disableCode)
            disablePassword = ""
            disableCode = ""
            await app.refreshMe()
            toast = Toast(text: "2FA is off")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func revoke() async {
        guard let client = app.client else { return }
        do {
            if let token = try await client.revokeOtherSessions() {
                app.replaceToken(token)
            }
            toast = Toast(text: "Other devices signed out")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func acknowledgeExposure() async {
        guard let client = app.client else { return }
        try? await client.acknowledgeExposure()
        await app.refreshMe()
    }
}

/// Scan-or-type enrolment. The QR is drawn on-device from the otpauth:// URI —
/// the server also returns an SVG, but rendering it would mean shipping an SVG
/// parser for something CoreImage already does.
struct TOTPSetupSheet: View {
    let onEnabled: () -> Void

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var setup: TOTPSetup?
    @State private var code = ""
    @State private var error: String?
    @State private var working = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 18) {
                    if let setup {
                        if let image = QRRenderer.image(for: setup.uri) {
                            Image(uiImage: image)
                                .interpolation(.none)
                                .resizable()
                                .scaledToFit()
                                .frame(width: 220, height: 220)
                                .padding(14)
                                .background(.white,
                                            in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                        }

                        Text("Scan this with your authenticator app")
                            .font(.subheadline)
                            .foregroundStyle(Theme.text)

                        VStack(spacing: 6) {
                            Text("Or type this secret")
                                .font(.caption)
                                .foregroundStyle(Theme.muted)
                            Text(setup.secret)
                                .font(.system(size: 14, design: .monospaced))
                                .foregroundStyle(Theme.text)
                                .textSelection(.enabled)
                                .padding(10)
                                .background(Theme.bg3,
                                            in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                        }

                        FieldBox {
                            TextField("6-digit code", text: $code)
                                .keyboardType(.numberPad)
                                .textContentType(.oneTimeCode)
                        }

                        Button {
                            Task { await enable() }
                        } label: {
                            if working { ProgressView().tint(Theme.onAccent) } else { Text("Turn on 2FA") }
                        }
                        .buttonStyle(PrimaryButtonStyle(enabled: code.count >= 6 && !working))
                        .disabled(code.count < 6 || working)

                        if let error {
                            Text(error)
                                .font(.footnote)
                                .foregroundStyle(Theme.danger)
                                .multilineTextAlignment(.center)
                        }

                        Text("Store the secret somewhere safe. Losing the authenticator without it means the password alone will no longer get you in.")
                            .font(.caption)
                            .foregroundStyle(Theme.warn)
                            .multilineTextAlignment(.center)
                    } else if let error {
                        MessageState(symbol: "exclamationmark.triangle",
                                     title: "Cannot start setup",
                                     message: error,
                                     tint: Theme.danger,
                                     retry: { Task { await load() } })
                    } else {
                        ProgressView().padding(.top, 60)
                    }
                }
                .padding(20)
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle("Two-factor")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Cancel") { dismiss() }.tint(Theme.muted)
                }
            }
            .task { await load() }
        }
    }

    private func load() async {
        guard let client = app.client else { return }
        do {
            setup = try await client.totpSetup()
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func enable() async {
        guard let client = app.client, let setup else { return }
        working = true
        defer { working = false }
        do {
            try await client.enableTOTP(secret: setup.secret, code: code)
            onEnabled()
            dismiss()
        } catch {
            self.error = error.localizedDescription
            code = ""
        }
    }
}

/// Shared QR drawing. Two screens render codes (pairing and 2FA enrolment) and
/// both want the same crisp, non-interpolated output.
enum QRRenderer {
    static func image(for text: String) -> UIImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(text.utf8)
        filter.correctionLevel = "M"
        guard let output = filter.outputImage else { return nil }
        // The generator emits about one pixel per module; scaling before
        // rasterising is what keeps it sharp rather than a blurry mess.
        let scaled = output.transformed(by: CGAffineTransform(scaleX: 10, y: 10))
        let context = CIContext()
        guard let cgImage = context.createCGImage(scaled, from: scaled.extent) else { return nil }
        return UIImage(cgImage: cgImage)
    }
}
