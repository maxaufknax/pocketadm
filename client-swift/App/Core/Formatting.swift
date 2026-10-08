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

    /// Below a kilobyte a rate is shown in bytes: a quiet link reading
    /// "0 KB/s" looks like a dead one.
    static func rate(_ bytesPerSecond: Double) -> String {
        let value = max(0, bytesPerSecond)
        if value < 1024 { return "\(Int(value.rounded())) B/s" }
        return bytes(Int64(value)) + "/s"
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

    /// Docker and the registries speak ISO 8601 in several shapes ("…Z",
    /// fractional seconds, nanoseconds, no zone at all). Docker's "zero time"
    /// for a container that never stopped reads as nil.
    static func isoDate(_ text: String) -> Date? {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        guard trimmed.count >= 19, !trimmed.hasPrefix("0001") else { return nil }
        let withFraction = ISO8601DateFormatter()
        withFraction.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = withFraction.date(from: trimmed) { return date }
        let plain = ISO8601DateFormatter()
        if let date = plain.date(from: trimmed) { return date }
        // nanoseconds, or no zone: read the first 19 characters as UTC
        let head = String(trimmed.prefix(19))
        return plain.date(from: head + "Z")
    }

    /// "12 Oct" this year, "12 Oct 2025" before.
    static func shortDate(_ date: Date) -> String {
        let f = DateFormatter()
        let thisYear = Calendar.current.isDate(date, equalTo: Date(), toGranularity: .year)
        f.setLocalizedDateFormatFromTemplate(thisYear ? "d MMM" : "d MMM yyyy")
        return f.string(from: date)
    }
}
