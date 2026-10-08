import Charts
import SwiftUI

/// The dashboard's history graph: CPU and memory, network traffic, disk
/// activity or the internet connection's latency and outages — over an hour,
/// six hours, a day or a week.
struct HistoryCard: View {
    let points: [MetricsHistory.Point]
    @Binding var range: Int
    let reload: () -> Void

    @AppStorage("pocketadm.dashboard.metric") private var metric = "cpu"

    private static let ranges: [(Int, String)] = [
        (60, "1 hour"), (360, "6 hours"), (1440, "24 hours"), (10080, "7 days"),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Picker("Show", selection: $metric) {
                Text("CPU & RAM").tag("cpu")
                Text("Network").tag("net")
                Text("Disk").tag("disk")
                Text("Internet").tag("ping")
            }
            .pickerStyle(.segmented)

            chart
                .frame(height: 170)

            HStack(alignment: .firstTextBaseline, spacing: 14) {
                legend
                Spacer(minLength: 6)
                Menu {
                    Picker("Range", selection: $range) {
                        ForEach(Self.ranges, id: \.0) { option in
                            Text(option.1).tag(option.0)
                        }
                    }
                } label: {
                    HStack(spacing: 3) {
                        Text(Self.ranges.first { $0.0 == range }?.1 ?? "1 hour")
                        Image(systemName: "chevron.up.chevron.down")
                    }
                    .font(.caption.weight(.semibold))
                }
            }

            if !summary.isEmpty {
                Text(summary)
                    .font(.footnote)
                    .foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .card()
        .onChange(of: range) { _, _ in reload() }
    }

    // MARK: - Charts

    @ViewBuilder
    private var chart: some View {
        switch metric {
        case "net":  networkChart
        case "disk": diskChart
        case "ping": internetChart
        default:     cpuChart
        }
    }

    private var cpuChart: some View {
        Chart {
            ForEach(points) { point in
                AreaMark(x: .value("Time", point.date), y: .value("CPU", point.cpu))
                    .foregroundStyle(.linearGradient(colors: [Theme.accent.opacity(0.30), Theme.accent.opacity(0.0)],
                                                     startPoint: .top, endPoint: .bottom))
                    .interpolationMethod(.monotone)
                LineMark(x: .value("Time", point.date), y: .value("CPU", point.cpu),
                         series: .value("Series", "CPU"))
                    .foregroundStyle(Theme.accent)
                    .lineStyle(StrokeStyle(lineWidth: 2))
                    .interpolationMethod(.monotone)
            }
            ForEach(points) { point in
                LineMark(x: .value("Time", point.date), y: .value("Memory", point.mem),
                         series: .value("Series", "Memory"))
                    .foregroundStyle(Color.purple)
                    .lineStyle(StrokeStyle(lineWidth: 2))
                    .interpolationMethod(.monotone)
            }
        }
        .chartYScale(domain: 0...100)
        .chartYAxis { percentAxis }
        .chartXAxis { timeAxis }
    }

    private var networkChart: some View {
        Chart {
            ForEach(points) { point in
                AreaMark(x: .value("Time", point.date), y: .value("Download", point.rx))
                    .foregroundStyle(.linearGradient(colors: [Color.blue.opacity(0.35), Color.blue.opacity(0.02)],
                                                     startPoint: .top, endPoint: .bottom))
                    .interpolationMethod(.monotone)
            }
            ForEach(points) { point in
                LineMark(x: .value("Time", point.date), y: .value("Upload", point.tx),
                         series: .value("Series", "Upload"))
                    .foregroundStyle(Color.orange)
                    .lineStyle(StrokeStyle(lineWidth: 1.6))
                    .interpolationMethod(.monotone)
            }
        }
        .chartYAxis { rateAxis }
        .chartXAxis { timeAxis }
    }

    private var hasDiskIO: Bool { points.contains { $0.dr != nil } }

    @ViewBuilder
    private var diskChart: some View {
        if hasDiskIO {
            Chart {
                ForEach(points) { point in
                    AreaMark(x: .value("Time", point.date), y: .value("Read", point.dr ?? 0))
                        .foregroundStyle(.linearGradient(colors: [Color.teal.opacity(0.35), Color.teal.opacity(0.02)],
                                                         startPoint: .top, endPoint: .bottom))
                        .interpolationMethod(.monotone)
                }
                ForEach(points) { point in
                    LineMark(x: .value("Time", point.date), y: .value("Write", point.dw ?? 0),
                             series: .value("Series", "Write"))
                        .foregroundStyle(Color.purple)
                        .lineStyle(StrokeStyle(lineWidth: 1.6))
                        .interpolationMethod(.monotone)
                }
            }
            .chartYAxis { rateAxis }
            .chartXAxis { timeAxis }
        } else {
            Chart(points) { point in
                LineMark(x: .value("Time", point.date), y: .value("Used", point.disk))
                    .foregroundStyle(Color.gray)
                    .interpolationMethod(.monotone)
            }
            .chartYScale(domain: 0...100)
            .chartYAxis { percentAxis }
            .chartXAxis { timeAxis }
        }
    }

    private var outages: [MetricsHistory.Point] {
        points.filter { $0.ping == nil || ($0.loss ?? 0) > 0 }
    }

    private var internetChart: some View {
        Chart {
            ForEach(outages) { point in
                RuleMark(x: .value("Time", point.date))
                    .foregroundStyle(Color.red.opacity(0.35 + 0.5 * min(1, point.loss ?? 1)))
                    .lineStyle(StrokeStyle(lineWidth: 2))
            }
            ForEach(points.filter { $0.ping != nil }) { point in
                LineMark(x: .value("Time", point.date), y: .value("Latency", point.ping ?? 0),
                         series: .value("Series", "Latency"))
                    .foregroundStyle(Color.green)
                    .lineStyle(StrokeStyle(lineWidth: 1.6))
                    .interpolationMethod(.monotone)
            }
        }
        .chartYAxis {
            AxisMarks(position: .leading, values: .automatic(desiredCount: 3)) { value in
                AxisGridLine()
                AxisValueLabel { Text("\(Int(value.as(Double.self) ?? 0)) ms") }
            }
        }
        .chartXAxis { timeAxis }
    }

    // MARK: - Axes and legend

    private var percentAxis: some AxisContent {
        AxisMarks(position: .leading, values: [0.0, 50.0, 100.0]) { value in
            AxisGridLine()
            AxisValueLabel { Text("\(Int(value.as(Double.self) ?? 0))%") }
        }
    }

    private var rateAxis: some AxisContent {
        AxisMarks(position: .leading, values: .automatic(desiredCount: 3)) { value in
            AxisGridLine()
            AxisValueLabel { Text(Fmt.rate(value.as(Double.self) ?? 0)) }
        }
    }

    private var timeAxis: some AxisContent {
        AxisMarks(values: .automatic(desiredCount: 4)) {
            AxisGridLine()
            AxisValueLabel(format: timeFormat)
        }
    }

    /// Weekdays on the week view, hours and minutes on the shorter ranges.
    private var timeFormat: Date.FormatStyle {
        range > 1440 ? Date.FormatStyle().weekday(.abbreviated) : Date.FormatStyle().hour().minute()
    }

    @ViewBuilder
    private var legend: some View {
        switch metric {
        case "net":
            legendItem(.blue, "Download")
            legendItem(.orange, "Upload")
        case "disk":
            if hasDiskIO {
                legendItem(.teal, "Read")
                legendItem(.purple, "Write")
            } else {
                legendItem(.gray, "Used")
            }
        case "ping":
            legendItem(.green, "Latency")
            legendItem(.red, "No connection")
        default:
            legendItem(Theme.accent, "CPU")
            legendItem(.purple, "Memory")
        }
    }

    private func legendItem(_ color: Color, _ label: String) -> some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(label).font(.caption).foregroundStyle(Theme.muted)
        }
    }

    // MARK: - In words

    private var summary: String {
        guard !points.isEmpty else { return "" }
        switch metric {
        case "net":
            let peak = points.map(\.rx).max() ?? 0
            let up = points.map(\.tx).max() ?? 0
            let avg = points.map(\.rx).reduce(0, +) / Double(points.count)
            return "Peak \(Fmt.rate(peak)) down and \(Fmt.rate(up)) up · average \(Fmt.rate(avg)) down."
        case "disk":
            let used = points.last?.disk ?? 0
            guard hasDiskIO else { return "The system disk is \(Int(used)) % full." }
            let read = points.compactMap(\.dr).max() ?? 0
            let write = points.compactMap(\.dw).max() ?? 0
            return "System disk \(Int(used)) % full · busiest moment \(Fmt.rate(read)) read, \(Fmt.rate(write)) written."
        case "ping":
            let good = points.compactMap(\.ping).sorted()
            let failed = points.filter { $0.ping == nil }.count
            let lossy = points.filter { ($0.loss ?? 0) > 0 }.count
            let median = good.isEmpty ? 0 : good[good.count / 2]
            if failed == 0 && lossy == 0 {
                return "Stable: no dropouts · median latency \(Int(median)) ms."
            }
            let gaps = failed + lossy
            return "\(gaps) moment\(gaps == 1 ? "" : "s") without a connection · median latency \(Int(median)) ms."
        default:
            let peak = points.map(\.cpu).max() ?? 0
            let avg = points.map(\.cpu).reduce(0, +) / Double(points.count)
            let mem = points.last?.mem ?? 0
            return "CPU averaged \(Int(avg)) %, peaking at \(Int(peak)) % · memory \(Int(mem)) % in use."
        }
    }
}
