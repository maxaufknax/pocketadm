import Foundation

enum Fmt {
    /// Docker and psutil both report bytes; every size in the UI goes through
    /// here so "11.4 GB" never sits next to "10.6 GiB".
    static func bytes(_ value: Int64) -> String {
        let f = ByteCountFormatter()
        f.countStyle = .binary
        f.allowedUnits = [.useKB, .useMB, .useGB, .useTB]
        return f.string(fromByteCount: value)
    }

    static func rate(_ bytesPerSecond: Double) -> String {
        bytes(Int64(max(0, bytesPerSecond))) + "/s"
    }

    static func percent(_ value: Double) -> String {
        String(format: "%.0f%%", value)
    }

    /// Uptime as the coarse "how long has this box been up" answer, not a
    /// precise duration — days and hours are what anyone actually reads.
    static func uptime(_ seconds: Double) -> String {
        let total = Int(max(0, seconds))
        let days = total / 86400
        let hours = (total % 86400) / 3600
        let minutes = (total % 3600) / 60
        if days > 0 { return "\(days)d \(hours)h" }
        if hours > 0 { return "\(hours)h \(minutes)m" }
        return "\(minutes)m"
    }

}
