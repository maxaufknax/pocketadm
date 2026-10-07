import AuthenticationServices
import UIKit

/// "Sign in with Authentik" (or any OpenID Connect provider the server is set
/// up for) through the system's own web authentication sheet.
///
/// The server runs the whole OIDC flow; the app only opens
/// /api/auth/oidc/start?client=app and waits for the redirect to
/// pocketadm://sso?code=… (or ?error=…). ASWebAuthenticationSession catches
/// that redirect itself, so no other app can receive the code, and the code
/// is worth nothing after one claim or 60 seconds. The sheet shares Safari's
/// cookies, so a provider you are already signed in to does not ask again.
final class SSOSignIn: NSObject, ASWebAuthenticationPresentationContextProviding {
    struct Failure: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    private var session: ASWebAuthenticationSession?
    /// The window the sheet presents over, captured on the main actor when
    /// the sign-in starts.
    private var anchor: ASPresentationAnchor?

    /// Runs the provider round trip and returns the one-time sign-in code.
    @MainActor
    func run(startURL: URL) async throws -> String {
        anchor = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .first(where: \.isKeyWindow)
        return try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<String, Error>) in
            let finish: (URL?, Error?) -> Void = { callback, error in
                if let error {
                    if (error as? ASWebAuthenticationSessionError)?.code == .canceledLogin {
                        continuation.resume(throwing: Failure(message: "Sign-in was cancelled."))
                    } else {
                        continuation.resume(throwing: error)
                    }
                    return
                }
                let items = callback.flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false) }?
                    .queryItems ?? []
                if let code = items.first(where: { $0.name == "code" })?.value, !code.isEmpty {
                    continuation.resume(returning: code)
                } else {
                    let reason = items.first(where: { $0.name == "error" })?.value
                    continuation.resume(throwing: Failure(message: reason ?? "The sign-in did not complete."))
                }
            }
            let session: ASWebAuthenticationSession
            if #available(iOS 17.4, *) {
                session = ASWebAuthenticationSession(url: startURL, callback: .customScheme("pocketadm"),
                                                     completionHandler: finish)
            } else {
                session = ASWebAuthenticationSession(url: startURL, callbackURLScheme: "pocketadm",
                                                     completionHandler: finish)
            }
            session.presentationContextProvider = self
            self.session = session
            if !session.start() {
                continuation.resume(throwing: Failure(message: "Could not open the sign-in page."))
            }
        }
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        anchor ?? ASPresentationAnchor()
    }
}
