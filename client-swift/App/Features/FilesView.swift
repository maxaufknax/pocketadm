import QuickLook
import SwiftUI
import UIKit

/// Where the file browser starts: the server's drives with how full each is —
/// the system disk, a USB drive plugged in for backups, network shares — and
/// the places people look for most.
///
/// Read-only on purpose: editing a compose file from a phone with no diff and
/// no undo is how servers break. Reading one at 3am is how they get fixed.
struct FilesHomeView: View {
    @EnvironmentObject private var app: AppState

    @State private var drives: [Filesystem] = []
    @State private var roots: [String] = []
    @State private var loaded = false
    @State private var error: String?

    /// The places worth a shortcut, on any Linux server.
    private static let places: [(String, String, String, Color)] = [
        ("Whole server", "/", "server.rack", .gray),
        ("Home folders", "/home", "house.fill", .blue),
        ("Services", "/srv", "shippingbox.fill", .brown),
        ("Docker volumes", "/var/lib/docker/volumes", "cylinder.split.1x2.fill", .cyan),
        ("Logs", "/var/log", "doc.text.fill", .orange),
        ("Configuration", "/etc", "gearshape.fill", .gray),
    ]

    var body: some View {
        List {
            if !drives.isEmpty {
                Section {
                    ForEach(drives) { drive in
                        DriveRow(drive: drive)
                    }
                } header: {
                    Text("Drives")
                } footer: {
                    Text("Tap a drive to browse it, or see what fills it.")
                }
            }

            if app.supports("files_v2") {
                Section("Places") {
                    ForEach(Self.places, id: \.1) { place in
                        NavigationLink {
                            FolderView(path: hostPath(place.1), title: place.0)
                        } label: {
                            HStack(spacing: 14) {
                                IconTile(symbol: place.2, color: place.3)
                                Text(place.0).foregroundStyle(Theme.text)
                                Spacer()
                                Text(place.1)
                                    .font(.footnote.monospaced())
                                    .foregroundStyle(Theme.muted)
                            }
                        }
                    }
                }
            }

            if !roots.isEmpty {
                Section {
                    ForEach(roots, id: \.self) { root in
                        NavigationLink {
                            FolderView(path: root)
                        } label: {
                            HStack(spacing: 14) {
                                IconTile(symbol: "folder.fill", color: .cyan)
                                Text(display(root)).foregroundStyle(Theme.text)
                            }
                        }
                    }
                } header: {
                    Text(app.supports("files_v2") ? "Workspaces" : "Folders")
                } footer: {
                    Text("The folders PocketADM may show, set under Settings → Workspaces.")
                }
            }

            if let error {
                Section {
                    Text(error).foregroundStyle(Theme.danger)
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Files")
        .navigationBarTitleDisplayMode(.large)
        .overlay { if !loaded { ProgressView() } }
        .task { if !loaded { await load() } }
        .refreshable { await load() }
    }

    /// A host path in the form this server serves it: containerised servers
    /// show the host under /host.
    private func hostPath(_ path: String) -> String {
        guard let first = roots.first, first.hasPrefix("/host") else { return path }
        return path == "/" ? "/host" : "/host" + path
    }

    private func display(_ path: String) -> String {
        if path == "/host" { return "/" }
        if path.hasPrefix("/host/") { return String(path.dropFirst(5)) }
        return path
    }

    private func load() async {
        guard let client = app.client else { return }
        defer { loaded = true }
        if app.me == nil { await app.refreshMe() }
        do {
            roots = try await client.listDirectory("").roots
            error = nil
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
        if app.supports("storage") {
            drives = ((try? await client.storage()) ?? []).filter { $0.kind != "boot" }
        }
    }
}

/// A drive: what it is, how full, and the two ways into it.
struct DriveRow: View {
    let drive: Filesystem

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 14) {
                IconTile(symbol: symbol, color: tint, size: 36)
                VStack(alignment: .leading, spacing: 2) {
                    Text(drive.title)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(Theme.text)
                    Text(subtitle)
                        .font(.footnote)
                        .foregroundStyle(Theme.muted)
                        .lineLimit(1)
                }
                Spacer(minLength: 6)
                Text("\(Int(drive.percent)) %")
                    .font(.subheadline.weight(.semibold).monospacedDigit())
                    .foregroundStyle(barTint)
            }
            ProgressView(value: min(1, drive.percent / 100))
                .tint(barTint)
            HStack {
                Text("\(Fmt.bytes(drive.free)) free of \(Fmt.bytes(drive.total))")
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
                Spacer()
                if drive.browsable {
                    NavigationLink {
                        FolderUsageView(path: drive.path, title: drive.title)
                    } label: {
                        Text("What fills it")
                            .font(.caption.weight(.semibold))
                    }
                    .buttonStyle(.borderless)
                    NavigationLink {
                        FolderView(path: drive.path, title: drive.title)
                    } label: {
                        Text("Browse")
                            .font(.caption.weight(.semibold))
                    }
                    .buttonStyle(.borderless)
                }
            }
        }
        .padding(.vertical, 4)
    }

    private var symbol: String {
        switch drive.kind {
        case "external": return "externaldrive.fill"
        case "network":  return "network"
        case "system":   return "internaldrive.fill"
        default:         return "internaldrive"
        }
    }

    private var tint: Color {
        switch drive.kind {
        case "external": return .orange
        case "network":  return .indigo
        default:         return .gray
        }
    }

    private var barTint: Color {
        drive.percent >= 90 ? Theme.danger : drive.percent >= 80 ? Theme.warn : Theme.accent
    }

    private var subtitle: String {
        var parts = [drive.mount]
        if drive.external { parts.append("USB") }
        parts.append(drive.fstype)
        return parts.joined(separator: " · ")
    }
}

// MARK: - A folder

enum FileSort: String, CaseIterable {
    case name, size, date

    var title: String {
        switch self {
        case .name: return "Name"
        case .size: return "Size"
        case .date: return "Date"
        }
    }
}

/// One folder, as its own pushed screen — going up is the back button.
struct FolderView: View {
    let path: String
    var title: String? = nil

    @EnvironmentObject private var app: AppState
    @AppStorage("pocketadm.files.sort") private var sort = FileSort.name.rawValue
    @AppStorage("pocketadm.files.hidden") private var showHidden = false

    @State private var listing: FSListing?
    @State private var loading = true
    @State private var error: String?
    @State private var filter = ""
    @State private var preview: FSListing.FileEntry?
    @State private var searching = false
    @State private var results: FileSearchResult?

    var body: some View {
        Group {
            if loading && listing == nil {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let listing {
                content(listing)
            } else {
                MessageState(symbol: "folder.badge.questionmark",
                             title: "Cannot open this folder",
                             message: error,
                             tint: Theme.danger,
                             retry: { Task { await load() } })
            }
        }
        .navigationTitle(title ?? name)
        .navigationBarTitleDisplayMode(.inline)
        .searchable(text: $filter, prompt: "Filter this folder")
        .onSubmit(of: .search) { Task { await searchDeep() } }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Picker("Sort by", selection: $sort) {
                        ForEach(FileSort.allCases, id: \.rawValue) { option in
                            Text(option.title).tag(option.rawValue)
                        }
                    }
                    if app.supports("files_v2") {
                        Toggle(isOn: $showHidden) {
                            Label("Show hidden files", systemImage: "eye")
                        }
                        NavigationLink {
                            FolderUsageView(path: listing?.path ?? path, title: title ?? name)
                        } label: {
                            Label("What fills this folder", systemImage: "chart.bar.xaxis")
                        }
                    }
                    Button {
                        UIPasteboard.general.string = listing?.shownPath ?? path
                    } label: {
                        Label("Copy path", systemImage: "doc.on.doc")
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
            }
        }
        .task { if listing == nil { await load() } }
        .onChange(of: showHidden) { _, _ in Task { await load() } }
        .sheet(item: $preview) { entry in
            FilePreviewSheet(entry: entry)
        }
    }

    private var name: String {
        let shown = listing?.shownPath ?? path
        if shown == "/" || shown == "/host" { return "Server" }
        return (shown as NSString).lastPathComponent
    }

    private func sorted<T>(_ items: [T], name: (T) -> String, size: (T) -> Int64, date: (T) -> Double) -> [T] {
        switch FileSort(rawValue: sort) ?? .name {
        case .name: return items.sorted { name($0).localizedStandardCompare(name($1)) == .orderedAscending }
        case .size: return items.sorted { size($0) > size($1) }
        case .date: return items.sorted { date($0) > date($1) }
        }
    }

    private func content(_ listing: FSListing) -> some View {
        let dirs = sorted(listing.dirs.filter { filter.isEmpty || $0.name.localizedCaseInsensitiveContains(filter) },
                          name: \.name, size: { _ in 0 }, date: \.modified)
        let files = sorted(listing.fileEntries.filter { filter.isEmpty || $0.name.localizedCaseInsensitiveContains(filter) },
                           name: \.name, size: \.size, date: \.modified)
        return List {
            Section {
                VStack(alignment: .leading, spacing: 4) {
                    Text(listing.shownPath)
                        .font(.system(.footnote, design: .monospaced))
                        .foregroundStyle(Theme.muted)
                        .textSelection(.enabled)
                    Text(folderFacts(listing))
                        .font(.caption)
                        .foregroundStyle(Color(uiColor: .tertiaryLabel))
                }
            }
            .listRowBackground(Color.clear)

            if !filter.isEmpty {
                Section {
                    Button {
                        Task { await searchDeep() }
                    } label: {
                        HStack {
                            Label("Search all folders below for “\(filter)”", systemImage: "magnifyingglass")
                            Spacer()
                            if searching { ProgressView() }
                        }
                    }
                    .disabled(!app.supports("files_v2") || filter.count < 2)
                    if let results {
                        ForEach(results.hits) { hit in
                            if hit.dir {
                                NavigationLink {
                                    FolderView(path: hit.path)
                                } label: {
                                    SearchHitRow(hit: hit)
                                }
                            } else {
                                Button { preview = entry(for: hit) } label: { SearchHitRow(hit: hit) }
                            }
                        }
                        if results.hits.isEmpty {
                            Text("Nothing found.").foregroundStyle(Theme.muted)
                        } else if !results.complete {
                            Text("Showing the first matches — narrow the search for more.")
                                .font(.caption)
                                .foregroundStyle(Theme.muted)
                        }
                    }
                }
            }

            if dirs.isEmpty && files.isEmpty && filter.isEmpty {
                Section {
                    Text("This folder is empty.")
                        .foregroundStyle(Theme.muted)
                }
            }

            if !dirs.isEmpty {
                Section {
                    ForEach(dirs) { dir in
                        NavigationLink {
                            FolderView(path: dir.path)
                        } label: {
                            HStack(spacing: 12) {
                                Image(systemName: dir.link ? "folder.badge.gearshape" : "folder.fill")
                                    .foregroundStyle(.blue)
                                    .frame(width: 26)
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(dir.name)
                                        .foregroundStyle(dir.name.hasPrefix(".") ? Theme.muted : Theme.text)
                                        .lineLimit(1)
                                    if dir.modified > 0 {
                                        Text(Fmt.ago(Date(timeIntervalSince1970: dir.modified))
                                             + (dir.owner.isEmpty ? "" : " · \(dir.owner)"))
                                            .font(.caption)
                                            .foregroundStyle(Theme.muted)
                                    }
                                }
                            }
                        }
                    }
                } header: {
                    Text("\(dirs.count) folders")
                }
            }

            if !files.isEmpty {
                Section {
                    ForEach(files) { file in
                        Button {
                            preview = file
                        } label: {
                            FileRow(file: file)
                        }
                    }
                } header: {
                    Text("\(listing.files) files")
                } footer: {
                    if listing.truncated {
                        Text("Only the first 2000 entries are shown.")
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await load() }
    }

    private func folderFacts(_ listing: FSListing) -> String {
        var parts = ["\(listing.dirs.count) folders", "\(listing.files) files"]
        if listing.hidden > 0 && !showHidden { parts.append("\(listing.hidden) hidden") }
        if listing.free > 0 { parts.append("\(Fmt.bytes(listing.free)) free on this drive") }
        return parts.joined(separator: " · ")
    }

    private func entry(for hit: FileSearchResult.Hit) -> FSListing.FileEntry? {
        let json: [String: Any] = ["name": hit.name, "path": hit.path, "display": hit.display,
                                   "size": hit.size, "text": hit.text]
        guard let data = try? JSONSerialization.data(withJSONObject: json) else { return nil }
        return try? JSONDecoder().decode(FSListing.FileEntry.self, from: data)
    }

    private func load() async {
        guard let client = app.client else { return }
        loading = true
        defer { loading = false }
        do {
            if app.supports("files_v2") {
                listing = try await client.listDirectory(path, hidden: showHidden)
            } else {
                listing = try await client.listDirectory(path)
            }
            error = nil
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
    }

    private func searchDeep() async {
        guard let client = app.client, app.supports("files_v2"), filter.count >= 2 else { return }
        searching = true
        defer { searching = false }
        results = try? await client.searchFiles(in: listing?.path ?? path, query: filter)
    }
}

struct FileRow: View {
    let file: FSListing.FileEntry

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: FileKind.symbol(for: file.name, text: file.text))
                .foregroundStyle(FileKind.tint(for: file.name, text: file.text))
                .frame(width: 26)
            VStack(alignment: .leading, spacing: 1) {
                Text(file.name)
                    .foregroundStyle(file.name.hasPrefix(".") ? Theme.muted : Theme.text)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Text(([Fmt.bytes(file.size)] + (file.modified > 0 ? [Fmt.ago(file.date)] : []))
                        .joined(separator: " · "))
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
            }
            Spacer(minLength: 0)
        }
    }
}

struct SearchHitRow: View {
    let hit: FileSearchResult.Hit

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: hit.dir ? "folder.fill" : FileKind.symbol(for: hit.name, text: hit.text))
                .foregroundStyle(hit.dir ? .blue : FileKind.tint(for: hit.name, text: hit.text))
                .frame(width: 26)
            VStack(alignment: .leading, spacing: 1) {
                Text(hit.name).foregroundStyle(Theme.text).lineLimit(1)
                Text(hit.display.isEmpty ? hit.path : hit.display)
                    .font(.caption.monospaced())
                    .foregroundStyle(Theme.muted)
                    .lineLimit(1)
                    .truncationMode(.head)
            }
        }
    }
}

/// A symbol and colour per kind of file.
enum FileKind {
    static let images: Set<String> = ["png", "jpg", "jpeg", "gif", "heic", "webp", "bmp", "tiff", "svg"]
    static let video: Set<String> = ["mp4", "mov", "mkv", "avi", "webm", "m4v"]
    static let audio: Set<String> = ["mp3", "flac", "wav", "m4a", "aac", "ogg", "opus"]
    static let archives: Set<String> = ["zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar", "zst"]

    static func ext(_ name: String) -> String { (name as NSString).pathExtension.lowercased() }

    static func symbol(for name: String, text: Bool) -> String {
        let e = ext(name)
        if images.contains(e) { return "photo.fill" }
        if video.contains(e) { return "film.fill" }
        if audio.contains(e) { return "music.note" }
        if archives.contains(e) { return "doc.zipper" }
        if e == "pdf" { return "doc.richtext.fill" }
        if ["yml", "yaml", "json", "toml", "conf", "ini", "env"].contains(e) || name == ".env" {
            return "gearshape.fill"
        }
        if ["sh", "py", "js", "ts", "go", "rs", "swift", "php", "rb"].contains(e) {
            return "chevron.left.forwardslash.chevron.right"
        }
        return text ? "doc.text.fill" : "doc.fill"
    }

    static func tint(for name: String, text: Bool) -> Color {
        let e = ext(name)
        if images.contains(e) { return .pink }
        if video.contains(e) { return .purple }
        if audio.contains(e) { return .orange }
        if archives.contains(e) { return .brown }
        if e == "pdf" { return .red }
        return text ? .gray : Color(uiColor: .systemGray3)
    }

    /// Whether QuickLook can show it better than the text view.
    static func previewable(_ name: String) -> Bool {
        let e = ext(name)
        return images.contains(e) || video.contains(e) || audio.contains(e)
            || ["pdf", "docx", "xlsx", "pptx", "doc", "xls", "ppt", "pages", "numbers", "key", "rtf", "csv", "html"].contains(e)
    }
}

// MARK: - Preview

/// A file from the server: text in the console view, everything else through
/// QuickLook after a download — and either way, to the share sheet.
struct FilePreviewSheet: View {
    let entry: FSListing.FileEntry

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var content: FileContent?
    @State private var downloaded: URL?
    @State private var progress: String?
    @State private var error: String?
    @State private var quickLook: URL?

    private var wantsQuickLook: Bool {
        FileKind.previewable(entry.name) || (!entry.text && app.supports("files_v2"))
    }

    var body: some View {
        NavigationStack {
            Group {
                if let content {
                    ScrollView([.horizontal, .vertical]) {
                        Text(content.content)
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundStyle(Theme.termFg)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(12)
                    }
                    .background(Theme.termBg)
                } else if let downloaded {
                    VStack(spacing: 16) {
                        Image(systemName: FileKind.symbol(for: entry.name, text: entry.text))
                            .font(.system(size: 54))
                            .foregroundStyle(FileKind.tint(for: entry.name, text: entry.text))
                        Text(entry.name).font(.headline).multilineTextAlignment(.center)
                        Text(Fmt.bytes(entry.size)).foregroundStyle(Theme.muted)
                        Button("Open") { quickLook = downloaded }
                            .buttonStyle(PrimaryButtonStyle())
                            .padding(.horizontal, 40)
                        ShareLink(item: downloaded) {
                            Label("Share or save", systemImage: "square.and.arrow.up")
                        }
                    }
                    .padding()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let error {
                    MessageState(symbol: "exclamationmark.triangle",
                                 title: "Cannot open this file",
                                 message: error,
                                 tint: Theme.danger)
                } else {
                    VStack(spacing: 10) {
                        ProgressView()
                        if let progress {
                            Text(progress).font(.footnote).foregroundStyle(Theme.muted)
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle(entry.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    if let content {
                        ShareLink(item: content.content) {
                            Image(systemName: "square.and.arrow.up")
                        }
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }.tint(Theme.accent)
                }
            }
            .safeAreaInset(edge: .bottom) {
                if let content, content.truncated {
                    Text("Showing the first 512 KB.")
                        .font(.caption)
                        .foregroundStyle(Theme.warn)
                        .frame(maxWidth: .infinity)
                        .padding(8)
                        .background(Theme.bg2)
                }
            }
            .quickLookPreview($quickLook)
            .task { await load() }
        }
    }

    private func load() async {
        guard let client = app.client else { return }
        if wantsQuickLook {
            await download(client)
            return
        }
        do {
            let result = try await client.readFile(entry.path)
            if result.binary {
                if app.supports("files_v2") {
                    await download(client)
                } else {
                    error = "This file is binary."
                }
            } else {
                content = result
            }
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// Fetches the file into a temporary folder, under its own name so
    /// QuickLook and the share sheet know what it is.
    private func download(_ client: APIClient) async {
        progress = entry.size > 0 ? "Downloading \(Fmt.bytes(entry.size))…" : "Downloading…"
        do {
            let request = try await client.fileRequest(entry.path)
            let (temp, response) = try await NetworkSession.shared.download(for: request)
            if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                error = http.statusCode == 403 ? "PocketADM does not hand out this file." : "The server said \(http.statusCode)."
                return
            }
            let folder = FileManager.default.temporaryDirectory
                .appendingPathComponent("files-\(UUID().uuidString)", isDirectory: true)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let target = folder.appendingPathComponent(entry.name)
            try FileManager.default.moveItem(at: temp, to: target)
            downloaded = target
            if FileKind.previewable(entry.name) { quickLook = target }
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: - What fills a folder

/// The size of everything directly inside a folder, biggest first.
struct FolderUsageView: View {
    let path: String
    let title: String

    @EnvironmentObject private var app: AppState
    @State private var usage: FolderUsage?
    @State private var error: String?

    var body: some View {
        List {
            if let usage {
                Section {
                    FactRow(label: "In total", value: Fmt.bytes(usage.total))
                    if usage.partial {
                        Text("Counting took too long — the numbers below are what was counted so far.")
                            .font(.caption)
                            .foregroundStyle(Theme.warn)
                    }
                }
                Section("Biggest first") {
                    ForEach(usage.children) { child in
                        NavigationLink {
                            FolderUsageView(path: child.path, title: child.name)
                        } label: {
                            VStack(alignment: .leading, spacing: 6) {
                                HStack {
                                    Text(child.name).foregroundStyle(Theme.text).lineLimit(1)
                                    Spacer()
                                    Text(Fmt.bytes(child.bytes))
                                        .font(.subheadline.monospacedDigit())
                                        .foregroundStyle(Theme.muted)
                                }
                                ProgressView(value: usage.total > 0 ? Double(child.bytes) / Double(usage.total) : 0)
                                    .tint(Theme.accent)
                            }
                        }
                    }
                }
            } else if let error {
                Section { Text(error).foregroundStyle(Theme.danger) }
            } else {
                Section {
                    HStack(spacing: 10) {
                        ProgressView()
                        Text("Counting… on a big drive this takes a moment.")
                            .font(.footnote)
                            .foregroundStyle(Theme.muted)
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink {
                    FolderView(path: path, title: title)
                } label: {
                    Image(systemName: "folder")
                }
            }
        }
        .task {
            guard usage == nil, let client = app.client else { return }
            do {
                usage = try await client.folderUsage(path)
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

/// The More tab's entry point: drives and places on a 0.24 server, the
/// workspace list on an older one.
struct FilesView: View {
    var body: some View { FilesHomeView() }
}
