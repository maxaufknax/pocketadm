import SwiftUI

/// Password fallback for the very first device, before any other device exists
/// to pair from.
struct LoginView: View {
    let info: ServerInfo
    @EnvironmentObject private var app: AppState

    @State private var password = ""
    @State private var totp = ""
    /// Set once the server has told us a code is needed. `info.totpRequired`
    /// seeds it so the field is already there on a known-2FA server.
    @State private var needsTOTP = false
    @State private var busy = false
    @State private var error: String?

    @FocusState private var focus: Field?
    private enum Field { case password, totp }

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                header

                VStack(spacing: 14) {
                    SecureField("Password", text: $password)
                        .textContentType(.password)
                        .submitLabel(needsTOTP ? .next : .go)
                        .focused($focus, equals: .password)
                        .onSubmit {
                            if needsTOTP { focus = .totp } else { Task { await signIn() } }
                        }
                        .padding(12)
                        .background(Theme.bg3, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                        .foregroundStyle(Theme.text)

                    if needsTOTP {
                        TextField("6-digit code", text: $totp)
                            .keyboardType(.numberPad)
                            .textContentType(.oneTimeCode)
                            .focused($focus, equals: .totp)
                            .padding(12)
                            .background(Theme.bg3, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                            .foregroundStyle(Theme.text)
                            .transition(.opacity.combined(with: .move(edge: .top)))
                    }

                    Button {
                        Task { await signIn() }
                    } label: {
                        if busy { ProgressView().tint(Theme.onAccent) } else { Text("Sign in") }
                    }
                    .buttonStyle(PrimaryButtonStyle(enabled: canSubmit))
                    .disabled(!canSubmit)
                }
                .card(padding: 18)

                if let error {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(Theme.danger)
                        .multilineTextAlignment(.center)
                }

                Button("Use a different server") { app.forgetServer() }
                    .font(.footnote)
                    .tint(Theme.muted)
            }
            .padding(20)
        }
        .background(Theme.bg.ignoresSafeArea())
        .scrollDismissesKeyboard(.interactively)
        .animation(.easeOut(duration: 0.2), value: needsTOTP)
        .onAppear {
            needsTOTP = info.totpRequired
            error = app.signOutReason
            app.signOutReason = nil
        }
    }

    private var header: some View {
        VStack(spacing: 8) {
            Image(systemName: "lock.shield")
                .font(.system(size: 40, weight: .light))
                .foregroundStyle(Theme.accent)
            Text(info.serverName)
                .font(.system(.title2, design: .rounded).weight(.semibold))
                .foregroundStyle(Theme.text)
            Text("PocketADM \(info.version)")
                .font(.caption)
                .foregroundStyle(Theme.muted)
            if info.demo {
                StatusPill(text: "DEMO SERVER", tint: Theme.warn).padding(.top, 4)
            }
        }
        .padding(.top, 32)
    }

    private var canSubmit: Bool {
        !busy && !password.isEmpty && (!needsTOTP || totp.count >= 6)
    }

    private func signIn() async {
        focus = nil
        busy = true
        error = nil
        defer { busy = false }

        guard let serverURL = app.serverURL else { return }
        do {
            let result = try await APIClient(baseURL: serverURL)
                .login(password: password, totp: needsTOTP ? totp : "")
            app.signIn(token: result.token, serverName: result.serverName ?? info.serverName)
        } catch APIClient.APIError.totpRequired {
            // The password was accepted; the server is only asking for the
            // second factor. Reveal the field instead of reporting a failure —
            // this arrives as a 401 and reads exactly like a wrong password if
            // it is not special-cased.
            needsTOTP = true
            focus = .totp
        } catch APIClient.APIError.wrongTOTP(let message) {
            totp = ""
            focus = .totp
            error = message
        } catch let failure {
            // Named explicitly: a bare `catch` binds the thrown error to a
            // constant called `error`, which shadows this view's @State of the
            // same name — the assignment then targets the immutable constant.
            error = failure.localizedDescription
        }
    }
}
