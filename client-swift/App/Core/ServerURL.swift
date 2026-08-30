import Foundation

enum ServerURL {
    /// Turns whatever someone typed into candidate URLs to probe, in order.
    ///
    /// People type `192.168.1.10:8090`, `myserver.duckdns.org`, or a full URL.
    /// With no scheme, https is tried first and http second — a LAN box on a
    /// plain port is the common self-hosted case and must still work, but it
    /// should never win over a working TLS endpoint.
    static func candidates(from raw: String) -> [URL] {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return [] }

        let stripped = trimmed.hasSuffix("/") ? String(trimmed.dropLast()) : trimmed
        if stripped.lowercased().hasPrefix("http://") || stripped.lowercased().hasPrefix("https://") {
            return URL(string: stripped).map { [$0] } ?? []
        }
        return ["https://\(stripped)", "http://\(stripped)"].compactMap(URL.init(string:))
    }
}
