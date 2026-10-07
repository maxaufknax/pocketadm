import Foundation

/// What a pairing QR says: which server, the one-time code, and — for a
/// server with its own certificate — the fingerprint of its TLS key.
///
/// Three encodings exist in the wild, newest first:
///
///     https://host:8443/?pair=CODE&fp=KEY       installer, web app, this app
///     https://host/pair?code=CODE[&fp=KEY]      this app before 2.0
///     {"h":"pair","u":"https://host","c":"CODE"} web app before 0.23
///
/// One scan yields both the server address and the credential, which is why
/// pairing, not the password, is the good first-run path.
struct PairingPayload: Equatable {
    let serverURL: URL
    let code: String
    /// base64url SHA-256 of the server's public key, when the QR carries one.
    let fingerprint: String?

    init(serverURL: URL, code: String, fingerprint: String? = nil) {
        self.serverURL = serverURL
        self.code = code
        self.fingerprint = fingerprint
    }

    init?(scanned raw: String) {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)

        if text.hasPrefix("{"),
           let data = text.data(using: .utf8),
           let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           object["h"] as? String == "pair",
           let base = object["u"] as? String,
           let code = object["c"] as? String, !code.isEmpty,
           let origin = Self.origin(of: base) {
            self.init(serverURL: origin, code: code)
            return
        }

        guard let comps = URLComponents(string: text),
              let scheme = comps.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let origin = Self.origin(of: text) else { return nil }
        let items = comps.queryItems ?? []
        func value(_ name: String) -> String? {
            items.first(where: { $0.name == name })?.value.flatMap { $0.isEmpty ? nil : $0 }
        }
        let path = comps.path.hasSuffix("/") ? String(comps.path.dropLast()) : comps.path
        guard let code = value("pair") ?? (path == "/pair" ? value("code") : nil) else { return nil }
        // a fingerprint can only pin an https server
        let fingerprint = scheme == "https" ? value("fp") : nil
        self.init(serverURL: origin, code: code, fingerprint: fingerprint)
    }

    /// The link this device shows when it pairs another one. Every client
    /// reads this form, including the 1.0 App Store build.
    static func link(serverURL: URL, code: String, fingerprint: String?) -> String {
        var comps = URLComponents()
        comps.scheme = serverURL.scheme
        comps.host = serverURL.host
        comps.port = serverURL.port
        comps.path = "/"
        var items = [URLQueryItem(name: "pair", value: code)]
        if let fingerprint, !fingerprint.isEmpty, serverURL.scheme?.lowercased() == "https" {
            items.append(URLQueryItem(name: "fp", value: fingerprint))
        }
        comps.queryItems = items
        return comps.string ?? ""
    }

    /// scheme://host[:port] — the path belongs to the web UI, not the API.
    private static func origin(of string: String) -> URL? {
        guard let comps = URLComponents(string: string),
              let scheme = comps.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = comps.host, !host.isEmpty else { return nil }
        var origin = URLComponents()
        origin.scheme = scheme
        origin.host = host
        origin.port = comps.port
        return origin.url
    }
}
