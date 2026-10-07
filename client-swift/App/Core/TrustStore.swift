import CryptoKit
import Foundation
import Security

/// Which TLS keys this device trusts beyond the system's own CAs.
///
/// A fresh PocketADM install has no domain, so it serves HTTPS with its own
/// certificate. The installer prints a pairing QR that carries — next to the
/// address and a one-time code — the SHA-256 of the server's public key. When
/// that QR is scanned, the fingerprint is stored here for the server's
/// host:port, and from then on the app accepts that server's certificate only
/// if its key matches. Scanning is the trust decision; nothing is ever
/// accepted on first sight.
///
/// A certificate the system trusts (Let's Encrypt behind a domain) needs no
/// pin and keeps working as usual. Fingerprints are not secret, so they live
/// in UserDefaults next to the server address.
enum TrustStore {
    private static let defaultsKey = "pocketadm.tlsPins"

    private static func key(host: String, port: Int) -> String {
        "\(host.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "[]"))):\(port)"
    }

    private static func port(of url: URL) -> Int {
        url.port ?? (url.scheme?.lowercased() == "http" ? 80 : 443)
    }

    static func pin(host: String, port: Int) -> String? {
        let pins = UserDefaults.standard.dictionary(forKey: defaultsKey) as? [String: String]
        return pins?[key(host: host, port: port)]
    }

    /// Stores (or with nil removes) the pin for the server at `url`.
    static func setPin(_ fingerprint: String?, for url: URL) {
        guard let host = url.host else { return }
        var pins = (UserDefaults.standard.dictionary(forKey: defaultsKey) as? [String: String]) ?? [:]
        pins[key(host: host, port: port(of: url))] = fingerprint
        UserDefaults.standard.set(pins, forKey: defaultsKey)
    }

    static func hasPin(for url: URL) -> Bool {
        guard let host = url.host else { return false }
        return pin(host: host, port: port(of: url)) != nil
    }

    /// base64url(SHA-256(public key)) of the certificate the server presented.
    /// For the EC P-256 keys PocketADM generates, SecKeyCopyExternalRepresentation
    /// is the uncompressed point 0x04‖X‖Y — the same bytes server/tls.py hashes.
    static func fingerprint(of trust: SecTrust) -> String? {
        guard let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
              let leaf = chain.first,
              let publicKey = SecCertificateCopyKey(leaf),
              let raw = SecKeyCopyExternalRepresentation(publicKey, nil) as Data?
        else { return nil }
        return Data(SHA256.hash(data: raw)).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

/// Answers every TLS challenge for the app's sessions: the system's verdict
/// first, then this device's pins, otherwise the connection is refused.
///
/// The async forms of the challenge methods on purpose: the completion-handler
/// forms carry `@Sendable` in current SDKs, and a witness that only *nearly*
/// matches an optional Objective-C requirement is silently never called — the
/// pin would simply not exist.
final class PinningDelegate: NSObject, URLSessionTaskDelegate {
    static let shared = PinningDelegate()

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge) async
        -> (URLSession.AuthChallengeDisposition, URLCredential?) {
        Self.decide(challenge)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    didReceive challenge: URLAuthenticationChallenge) async
        -> (URLSession.AuthChallengeDisposition, URLCredential?) {
        Self.decide(challenge)
    }

    static func decide(_ challenge: URLAuthenticationChallenge)
        -> (URLSession.AuthChallengeDisposition, URLCredential?) {
        let space = challenge.protectionSpace
        guard space.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = space.serverTrust else {
            return (.performDefaultHandling, nil)
        }
        // A certificate the system already trusts (hostname included) — done.
        if SecTrustEvaluateWithError(trust, nil) {
            return (.performDefaultHandling, nil)
        }
        // Otherwise only the exact key a pairing QR vouched for.
        if let pinned = TrustStore.pin(host: space.host, port: space.port),
           let presented = TrustStore.fingerprint(of: trust),
           pinned == presented {
            return (.useCredential, URLCredential(trust: trust))
        }
        return (.cancelAuthenticationChallenge, nil)
    }
}

/// The only way the app makes URLSessions, so every request — API calls,
/// WebSockets, job log streams — goes through the same trust decision.
enum NetworkSession {
    static let shared = NetworkSession.make(.default)

    static func make(_ configuration: URLSessionConfiguration) -> URLSession {
        URLSession(configuration: configuration, delegate: PinningDelegate.shared, delegateQueue: nil)
    }
}
