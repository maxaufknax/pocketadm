import PhotosUI
import QuickLook
import SwiftUI
import UniformTypeIdentifiers
import UIKit

// The file explorer, the way an editor shows a project: it opens on "/" (or
// on the folders this server lets PocketADM see), folders unfold in place, and
// every file opens — text in an editor, everything else in a preview — and can
// be renamed, moved, copied, uploaded, downloaded or deleted. Works the same on
// any server: where it starts comes from the server (GET /api/fs/start).

// MARK: - Model

/// A file or folder in the tree.
struct FSNode: Identifiable, Hashable {
    let name: String
    let path: String
    let display: String
    let isDir: Bool
    let size: Int64
    let modified: Double
    let mode: String
    let owner: String
    let link: Bool
    let kind: String
    let text: Bool
    let drive: FSListing.DriveMark?

    var id: String { path }
    var shownPath: String { display.isEmpty ? path : display }
    var date: Date { Date(timeIntervalSince1970: modified) }
    var hidden: Bool { name.hasPrefix(".") }
    var isArchive: Bool {
        let lower = name.lowercased()
        return [".zip", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".tar.bz2"].contains { lower.hasSuffix($0) }
    }

    init(dir: FSListing.Entry) {
        name = dir.name; path = dir.path; display = dir.display; isDir = true; size = 0
        modified = dir.modified; mode = dir.mode; owner = dir.owner; link = dir.link
        kind = "folder"; text = false; drive = dir.drive
    }

    init(file: FSListing.FileEntry) {
        name = file.name; path = file.path; display = file.display; isDir = false; size = file.size
        modified = file.modified; mode = file.mode; owner = file.owner; link = file.link
        kind = file.kind; text = file.text; drive = nil
    }

    /// A root shown without a listing of its parent (the virtual top).
    init(root path: String, display: String) {
        name = display == "/" ? "/" : (display as NSString).lastPathComponent
        self.path = path; self.display = display; isDir = true; size = 0; modified = 0
        mode = ""; owner = ""; link = false; kind = "folder"; text = false; drive = nil
    }

    var fileEntry: FSListing.FileEntry {
        FSListing.FileEntry(name: name, path: path, display: display, size: size, text: text,
                            modified: modified, kind: kind)
    }
}

/// One visible line of the tree.
struct TreeRow: Identifiable {
    let node: FSNode
    let depth: Int
    var id: String { node.id }
}

@MainActor
final class FileTreeModel: ObservableObject {
    @Published private(set) var children: [String: [FSNode]] = [:]
    @Published var expanded: Set<String> = []
    @Published private(set) var loading: Set<String> = []
    @Published private(set) var errors: [String: String] = [:]
    @Published private(set) var free: [String: Int64] = [:]

    var showHidden = false
    var sort = FileSort.name
    /// Only folders (the move/copy destination picker).
    var foldersOnly = false

    func isLoaded(_ path: String) -> Bool { children[path] != nil }

    func load(_ path: String, app: AppState, force: Bool = false) async {
        guard let client = app.client, force || children[path] == nil, !loading.contains(path) else { return }
        loading.insert(path)
        defer { loading.remove(path) }
        do {
            let listing = try await client.listDirectory(path, hidden: showHidden)
            var nodes = listing.dirs.map(FSNode.init(dir:))
            if !foldersOnly { nodes += listing.fileEntries.map(FSNode.init(file:)) }
            children[path] = sorted(nodes)
            free[path] = listing.free
            errors[path] = nil
        } catch {
            errors[path] = error.localizedDescription
            app.handle(error)
        }
    }

    func toggle(_ path: String, app: AppState) async {
        if expanded.contains(path) {
            expanded.remove(path)
        } else {
            expanded.insert(path)
            await load(path, app: app)
        }
    }

    /// After a change: reload the folders that changed, keep the rest.
    func refresh(_ paths: [String], app: AppState) async {
        for path in Set(paths) where children[path] != nil {
            await load(path, app: app, force: true)
        }
    }

    func reloadAll(app: AppState) async {
        let open = Array(children.keys)
        children = [:]
        for path in open where expanded.contains(path) || open.count == 1 {
            await load(path, app: app, force: true)
        }
    }

    func collapseAll() { expanded = [] }

    func resort() {
        for (key, nodes) in children { children[key] = sorted(nodes) }
    }

    private func sorted(_ nodes: [FSNode]) -> [FSNode] {
        let dirs = nodes.filter(\.isDir), files = nodes.filter { !$0.isDir }
        func order(_ items: [FSNode]) -> [FSNode] {
            switch sort {
            case .name: return items.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
            case .size: return items.sorted { $0.size > $1.size }
            case .date: return items.sorted { $0.modified > $1.modified }
            }
        }
        return order(dirs) + order(files)
    }

    /// The tree as rows, depth first through every open folder.
    func rows(under root: String, filter: String = "") -> [TreeRow] {
        var out: [TreeRow] = []
        func walk(_ path: String, depth: Int) {
            for node in children[path] ?? [] {
                let matches = filter.isEmpty || node.name.localizedCaseInsensitiveContains(filter)
                if matches || (node.isDir && expanded.contains(node.path)) { out.append(TreeRow(node: node, depth: depth)) }
                if node.isDir, expanded.contains(node.path), depth < 30 { walk(node.path, depth: depth + 1) }
            }
        }
        walk(root, depth: 0)
        return out
    }

    func parent(of path: String) -> String {
        for (folder, nodes) in children where nodes.contains(where: { $0.path == path }) { return folder }
        return (path as NSString).deletingLastPathComponent
    }
}

// MARK: - The explorer

/// The route behind Files: the explorer on a 0.25 server, the 0.24 browser before.
struct FilesEntry: View {
    @EnvironmentObject private var app: AppState

    var body: some View {
        if app.supports("files_manage") {
            FileExplorerView()
        } else {
            FilesHomeView()
        }
    }
}

struct FileExplorerView: View {
    /// nil: where the server says to start ("/" on a whole-server install).
    var rootPath: String? = nil
    var rootDisplay: String? = nil

    @EnvironmentObject private var app: AppState
    @StateObject private var tree = FileTreeModel()
    @AppStorage("pocketadm.files.sort") private var sort = FileSort.name.rawValue
    @AppStorage("pocketadm.files.hidden") private var showHidden = false

    @State private var root = ""
    @State private var rootShown = ""
    /// More than one allowed folder and no whole server: they are the top.
    @State private var roots: [String] = []
    @State private var drives: [Filesystem] = []
    @State private var started = false
    @State private var startError: String?
    @State private var filter = ""
    @State private var results: FileSearchResult?
    @State private var searching = false

    @State private var opened: FSNode?
    @State private var editing: FSNode?
    @State private var focus: FSNode?
    @State private var naming: NameRequest?
    @State private var nameDraft = ""
    @State private var confirmDelete: FSNode?
    @State private var picking: PickRequest?
    @State private var permissions: FSNode?
    @State private var importing = false
    @State private var importTarget = ""
    @State private var photoItems: [PhotosPickerItem] = []
    @State private var photoTarget = ""
    @State private var showPhotos = false
    @State private var busy: String?
    @State private var shared: URL?
    @State private var usage: FSNode?
    @State private var toast: Toast?

    var body: some View {
        Group {
            if !started {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let startError, root.isEmpty && roots.isEmpty {
                MessageState(symbol: "folder.badge.questionmark", title: "Cannot open the files",
                             message: startError, tint: Theme.danger,
                             retry: { Task { await start() } })
            } else {
                explorer
            }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .searchable(text: $filter, prompt: "Filter, or search below")
        .onSubmit(of: .search) { Task { await searchDeep() } }
        .toolbar { toolbar }
        .task { if !started { await start() } }
        .onChange(of: showHidden) { _, value in
            tree.showHidden = value
            Task { await tree.reloadAll(app: app) }
        }
        .onChange(of: sort) { _, value in
            tree.sort = FileSort(rawValue: value) ?? .name
            tree.resort()
        }
        .sheet(item: $opened) { node in
            FilePreviewSheet(entry: node.fileEntry)
        }
        .fullScreenCover(item: $editing) { node in
            FileEditorView(node: node) { Task { await tree.refresh([tree.parent(of: node.path)], app: app) } }
        }
        .sheet(item: $picking) { request in
            FolderPickerSheet(request: request, startPath: root.isEmpty ? (roots.first ?? "") : root,
                              startDisplay: rootShown) { destination in
                Task { await transfer(request, to: destination) }
            }
        }
        .sheet(item: $permissions) { node in
            PermissionsSheet(node: node) { mode, recursive in
                Task { await chmod(node, mode: mode, recursive: recursive) }
            }
            .presentationDetents([.medium])
        }
        .sheet(item: Binding(get: { shared.map(SharedFile.init) }, set: { if $0 == nil { shared = nil } })) { file in
            ShareSheet(items: [file.url])
        }
        .navigationDestination(item: $focus) { node in
            FileExplorerView(rootPath: node.path, rootDisplay: node.shownPath)
        }
        .navigationDestination(item: $usage) { node in
            FolderUsageView(path: node.path, title: node.name == "/" ? "Server" : node.name)
        }
        .alert(naming?.title ?? "", isPresented: Binding(get: { naming != nil }, set: { if !$0 { naming = nil } })) {
            TextField("Name", text: $nameDraft)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            Button(naming?.confirm ?? "OK") {
                if let request = naming { Task { await submit(request, name: nameDraft) } }
            }
            Button("Cancel", role: .cancel) {}
        }
        .confirmationDialog(confirmDelete.map { "Delete “\($0.name)”?" } ?? "",
                            isPresented: Binding(get: { confirmDelete != nil }, set: { if !$0 { confirmDelete = nil } }),
                            titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                if let node = confirmDelete { Task { await delete(node) } }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(confirmDelete?.isDir == true ? "The folder and everything in it are deleted on the server. This cannot be undone."
                                              : "The file is deleted on the server. This cannot be undone.")
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.item], allowsMultipleSelection: true) { result in
            if case .success(let urls) = result {
                Task { await upload(urls, to: importTarget, scoped: true) }
            }
        }
        .photosPicker(isPresented: $showPhotos, selection: $photoItems, maxSelectionCount: 20,
                      matching: .any(of: [.images, .videos]))
        .onChange(of: photoItems) { _, items in
            guard !items.isEmpty else { return }
            Task { await uploadPhotos(items) }
        }
        .overlay(alignment: .bottom) {
            if let busy {
                HStack(spacing: 10) {
                    ProgressView()
                    Text(busy).font(.subheadline.weight(.medium))
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 12)
                .background(.regularMaterial, in: Capsule())
                .padding(.bottom, 24)
            }
        }
        .toast($toast)
    }

    private var title: String {
        if rootShown.isEmpty { return "Files" }
        if rootShown == "/" { return app.serverName.isEmpty ? "/" : app.serverName }
        return (rootShown as NSString).lastPathComponent
    }

    // MARK: Layout

    private var explorer: some View {
        List {
            if rootPath == nil && !drives.isEmpty && filter.isEmpty {
                Section { driveStrip }
                    .listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 0))
                    .listRowBackground(Color.clear)
            }

            Section {
                pathBar
            }
            .listRowBackground(Color.clear)
            .listRowInsets(EdgeInsets(top: 0, leading: 16, bottom: 0, trailing: 16))

            if !filter.isEmpty {
                deepSearchSection
            }

            Section {
                if root.isEmpty {
                    // several allowed folders: they are the top of the tree
                    ForEach(roots, id: \.self) { path in
                        let node = FSNode(root: path, display: displayName(path))
                        treeRow(node, depth: 0)
                        if tree.expanded.contains(path) {
                            ForEach(tree.rows(under: path, filter: filter)) { row in
                                treeRow(row.node, depth: row.depth + 1)
                            }
                        }
                    }
                } else {
                    let rows = tree.rows(under: root, filter: filter)
                    if let error = tree.errors[root] {
                        Label(error, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(Theme.danger)
                    } else if tree.isLoaded(root) && rows.isEmpty && filter.isEmpty {
                        Text("This folder is empty.").foregroundStyle(Theme.muted)
                    }
                    ForEach(rows) { row in
                        treeRow(row.node, depth: row.depth)
                    }
                }
            } footer: {
                if let free = tree.free[root], free > 0 {
                    Text("\(Fmt.bytes(free)) free on this drive")
                }
            }
        }
        .listStyle(.insetGrouped)
        .environment(\.defaultMinListRowHeight, 38)
        .refreshable { await tree.reloadAll(app: app) }
    }

    /// The drives at a glance; a tap opens one.
    private var driveStrip: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(drives) { drive in
                    Button {
                        if drive.mount == "/" {
                            Task { await reroot(drive.path, display: "/") }
                        } else {
                            focus = FSNode(root: drive.path, display: drive.mount)
                        }
                    } label: {
                        DriveChip(drive: drive)
                    }
                    .buttonStyle(.plain)
                    .contextMenu {
                        Button {
                            usage = FSNode(root: drive.path, display: drive.mount)
                        } label: { Label("What fills it", systemImage: "chart.bar.xaxis") }
                    }
                }
            }
            .padding(.horizontal, 16)
        }
    }

    /// Where you are, each part tappable — up is one tap.
    private var pathBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 2) {
                let parts = crumbs
                ForEach(Array(parts.enumerated()), id: \.offset) { index, crumb in
                    if index > 0 {
                        Image(systemName: "chevron.compact.right")
                            .font(.caption)
                            .foregroundStyle(Theme.muted)
                    }
                    Button(crumb.label) {
                        Task { await reroot(crumb.path, display: crumb.display) }
                    }
                    .font(.system(.footnote, design: .monospaced).weight(index == parts.count - 1 ? .semibold : .regular))
                    .foregroundStyle(index == parts.count - 1 ? Theme.text : Theme.accent)
                    .buttonStyle(.borderless)
                }
            }
            .padding(.vertical, 2)
        }
    }

    private struct Crumb { let label: String; let path: String; let display: String }

    /// "/", "srv", "cloud-server" — mapped back to the server's own paths
    /// (a containerised server serves the host under /host).
    private var crumbs: [Crumb] {
        guard !rootShown.isEmpty else { return [Crumb(label: "Folders", path: "", display: "")] }
        let prefix = root.hasSuffix(rootShown) && rootShown != "/" ? String(root.dropLast(rootShown.count))
                   : (rootShown == "/" ? root : "")
        var out = [Crumb(label: "/", path: prefix.isEmpty ? "/" : prefix, display: "/")]
        if rootShown == "/" { return out }
        var shown = ""
        for part in rootShown.split(separator: "/") {
            shown += "/" + part
            out.append(Crumb(label: String(part), path: prefix + shown, display: shown))
        }
        return out
    }

    @ViewBuilder
    private var deepSearchSection: some View {
        Section {
            Button {
                Task { await searchDeep() }
            } label: {
                HStack {
                    Label("Search every folder below for “\(filter)”", systemImage: "magnifyingglass")
                    Spacer()
                    if searching { ProgressView() }
                }
            }
            .disabled(filter.count < 2)
            if let results {
                ForEach(results.hits) { hit in
                    Button {
                        if hit.dir {
                            focus = FSNode(root: hit.path, display: hit.display)
                        } else {
                            open(FSNode(file: FSListing.FileEntry(name: hit.name, path: hit.path, display: hit.display,
                                                                  size: hit.size, text: hit.text)))
                        }
                    } label: { SearchHitRow(hit: hit) }
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

    private func treeRow(_ node: FSNode, depth: Int) -> some View {
        FileTreeRow(node: node, depth: depth,
                    expanded: tree.expanded.contains(node.path),
                    loading: tree.loading.contains(node.path))
            .contentShape(Rectangle())
            .onTapGesture {
                if node.isDir {
                    Task { await tree.toggle(node.path, app: app) }
                } else {
                    open(node)
                }
            }
            .contextMenu { menu(for: node) }
            .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                Button(role: .destructive) { confirmDelete = node } label: { Label("Delete", systemImage: "trash") }
                    .disabled(app.me?.demo == true)
                Button { rename(node) } label: { Label("Rename", systemImage: "pencil") }
                    .tint(.orange)
            }
            .listRowInsets(EdgeInsets(top: 0, leading: 12 + CGFloat(min(depth, 12)) * 16, bottom: 0, trailing: 12))
    }

    // MARK: Menus

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItemGroup(placement: .topBarTrailing) {
            Menu {
                Button { newFile(in: currentFolder) } label: { Label("New file", systemImage: "doc.badge.plus") }
                Button { newFolder(in: currentFolder) } label: { Label("New folder", systemImage: "folder.badge.plus") }
                Divider()
                Button { importTarget = currentFolder; importing = true } label: {
                    Label("Upload from Files", systemImage: "square.and.arrow.up.on.square")
                }
                Button { photoTarget = currentFolder; showPhotos = true } label: {
                    Label("Upload photos or videos", systemImage: "photo.on.rectangle")
                }
            } label: {
                Image(systemName: "plus")
            }
            .disabled(currentFolder.isEmpty || app.me?.demo == true)
            .accessibilityLabel("Add")

            Menu {
                Picker("Sort by", selection: $sort) {
                    ForEach(FileSort.allCases, id: \.rawValue) { option in
                        Text(option.title).tag(option.rawValue)
                    }
                }
                Toggle(isOn: $showHidden) { Label("Show hidden files", systemImage: "eye") }
                Button { tree.collapseAll() } label: { Label("Collapse all", systemImage: "rectangle.compress.vertical") }
                Divider()
                if !currentFolder.isEmpty {
                    Button {
                        usage = FSNode(root: currentFolder, display: rootShown)
                    } label: { Label("What fills this folder", systemImage: "chart.bar.xaxis") }
                    Button {
                        Task { await downloadFolder(currentFolder, name: title) }
                    } label: { Label("Download as ZIP", systemImage: "arrow.down.doc") }
                    Button {
                        UIPasteboard.general.string = rootShown
                        toast = Toast(text: "Path copied")
                    } label: { Label("Copy path", systemImage: "doc.on.doc") }
                }
            } label: {
                Image(systemName: "ellipsis.circle")
            }
        }
    }

    /// Where "+" puts things: the top of this explorer.
    private var currentFolder: String { root }

    @ViewBuilder
    private func menu(for node: FSNode) -> some View {
        let demo = app.me?.demo == true
        if node.isDir {
            Button { focus = node } label: { Label("Open as the top", systemImage: "arrow.up.forward.square") }
            Divider()
            Button { newFile(in: node.path) } label: { Label("New file here", systemImage: "doc.badge.plus") }
                .disabled(demo)
            Button { newFolder(in: node.path) } label: { Label("New folder here", systemImage: "folder.badge.plus") }
                .disabled(demo)
            Button { importTarget = node.path; importing = true } label: {
                Label("Upload here", systemImage: "square.and.arrow.up.on.square")
            }
            .disabled(demo)
            Button {
                Task { await downloadFolder(node.path, name: node.name) }
            } label: { Label("Download as ZIP", systemImage: "arrow.down.doc") }
            Button { usage = node } label: { Label("What fills it", systemImage: "chart.bar.xaxis") }
        } else {
            Button { open(node) } label: { Label("Open", systemImage: "eye") }
            if node.text {
                Button { editing = node } label: { Label("Edit", systemImage: "pencil.line") }
                    .disabled(demo)
            }
            Button {
                Task { await shareFile(node) }
            } label: { Label("Share or save", systemImage: "square.and.arrow.up") }
            if node.isArchive {
                Button {
                    Task { await extract(node) }
                } label: { Label("Unpack here", systemImage: "archivebox") }
                .disabled(demo)
            }
        }
        Divider()
        Button { rename(node) } label: { Label("Rename", systemImage: "pencil") }
            .disabled(demo)
        Button { picking = PickRequest(nodes: [node], copy: false) } label: {
            Label("Move to…", systemImage: "folder")
        }
        .disabled(demo)
        Button { picking = PickRequest(nodes: [node], copy: true) } label: {
            Label("Copy to…", systemImage: "plus.square.on.square")
        }
        .disabled(demo)
        Button { permissions = node } label: { Label("Permissions", systemImage: "lock") }
            .disabled(demo)
        Button {
            UIPasteboard.general.string = node.shownPath
            toast = Toast(text: "Path copied")
        } label: { Label("Copy path", systemImage: "doc.on.doc") }
        Button {
            app.ask("Look at \(node.isDir ? "the folder" : "the file") \(node.shownPath) on the server and tell me what it is and whether anything needs attention.")
        } label: { Label("Ask the assistant", systemImage: "sparkles") }
        Divider()
        Button(role: .destructive) { confirmDelete = node } label: { Label("Delete", systemImage: "trash") }
            .disabled(demo)
    }

    // MARK: Start

    private func start() async {
        guard let client = app.client else { return }
        if app.me == nil { await app.refreshMe() }
        tree.showHidden = showHidden
        tree.sort = FileSort(rawValue: sort) ?? .name
        if let rootPath {
            await reroot(rootPath, display: rootDisplay ?? displayName(rootPath))
        } else {
            do {
                let start = try await client.fsStart()
                roots = start.roots
                if start.path.isEmpty {
                    root = ""
                    rootShown = ""
                } else {
                    await reroot(start.path, display: start.display)
                }
                startError = nil
            } catch {
                startError = error.localizedDescription
                app.handle(error)
            }
            drives = ((try? await client.storage()) ?? []).filter { $0.kind != "boot" && $0.browsable }
        }
        started = true
        // Screenshot runs: a tree with something open in it
        if AppState.screenshotRoute == "files", !root.isEmpty {
            for name in ["srv", "nextcloud"] {
                if let node = tree.rows(under: root).first(where: { $0.node.isDir && $0.node.name == name })?.node {
                    await tree.toggle(node.path, app: app)
                }
            }
        }
    }

    private func reroot(_ path: String, display: String) async {
        guard !path.isEmpty else {
            root = ""
            rootShown = ""
            return
        }
        root = path
        rootShown = display
        await tree.load(path, app: app)
    }

    private func displayName(_ path: String) -> String {
        if path == "/host" { return "/" }
        if path.hasPrefix("/host/") { return String(path.dropFirst(5)) }
        return path
    }

    // MARK: Actions

    private func open(_ node: FSNode) {
        if node.text && !FileKind.previewable(node.name) {
            editing = node
        } else {
            opened = node
        }
    }

    private func newFile(in folder: String) {
        nameDraft = ""
        naming = NameRequest(kind: .newFile, folder: folder)
    }

    private func newFolder(in folder: String) {
        nameDraft = ""
        naming = NameRequest(kind: .newFolder, folder: folder)
    }

    private func rename(_ node: FSNode) {
        nameDraft = node.name
        naming = NameRequest(kind: .rename(node), folder: tree.parent(of: node.path))
    }

    private func submit(_ request: NameRequest, name: String) async {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, let client = app.client else { return }
        do {
            switch request.kind {
            case .newFile:
                let created = try await client.writeFile(request.folder + "/" + name, content: "",
                                                         expectedModified: nil, create: true)
                await tree.refresh([request.folder], app: app)
                tree.expanded.insert(request.folder)
                editing = FSNode(file: FSListing.FileEntry(name: name, path: created.path, display: created.display,
                                                           size: 0, text: true, modified: created.modified))
            case .newFolder:
                _ = try await client.makeFolder(in: request.folder, name: name)
                await tree.refresh([request.folder], app: app)
                tree.expanded.insert(request.folder)
                toast = Toast(text: "Folder created")
            case .rename(let node):
                guard name != node.name else { return }
                _ = try await client.renameFile(node.path, to: name)
                tree.expanded.remove(node.path)
                await tree.refresh([request.folder], app: app)
                toast = Toast(text: "Renamed")
            }
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func delete(_ node: FSNode) async {
        guard let client = app.client else { return }
        do {
            try await client.deleteFiles([node.path])
            tree.expanded.remove(node.path)
            await tree.refresh([tree.parent(of: node.path)], app: app)
            toast = Toast(text: "Deleted")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func transfer(_ request: PickRequest, to destination: String) async {
        guard let client = app.client else { return }
        busy = request.copy ? "Copying…" : "Moving…"
        defer { busy = nil }
        do {
            try await client.moveFiles(request.nodes.map(\.path), to: destination, copy: request.copy)
            let sources = request.nodes.map { tree.parent(of: $0.path) }
            await tree.refresh(sources + [destination], app: app)
            toast = Toast(text: request.copy ? "Copied" : "Moved")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func chmod(_ node: FSNode, mode: String, recursive: Bool) async {
        guard let client = app.client else { return }
        do {
            _ = try await client.changeMode(node.path, mode: mode, recursive: recursive)
            await tree.refresh([tree.parent(of: node.path)], app: app)
            toast = Toast(text: "Permissions set to \(mode)")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func extract(_ node: FSNode) async {
        guard let client = app.client else { return }
        busy = "Unpacking…"
        defer { busy = nil }
        do {
            let result = try await client.extractArchive(node.path)
            await tree.refresh([tree.parent(of: node.path)], app: app)
            toast = Toast(text: "Unpacked into \((result.display as NSString).lastPathComponent)")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func upload(_ urls: [URL], to folder: String, scoped: Bool) async {
        guard let client = app.client, !folder.isEmpty else { return }
        var done = 0
        for (index, url) in urls.enumerated() {
            busy = urls.count > 1 ? "Uploading \(index + 1) of \(urls.count)…" : "Uploading \(url.lastPathComponent)…"
            let access = scoped && url.startAccessingSecurityScopedResource()
            defer { if access { url.stopAccessingSecurityScopedResource() } }
            do {
                // a copy first: the picked file may live in a provider's sandbox
                let local = try Self.temporaryCopy(of: url)
                _ = try await client.uploadFile(local, to: folder, name: url.lastPathComponent)
                try? FileManager.default.removeItem(at: local.deletingLastPathComponent())
                done += 1
            } catch {
                toast = Toast(text: "\(url.lastPathComponent): \(error.localizedDescription)", isError: true)
            }
        }
        busy = nil
        await tree.refresh([folder], app: app)
        tree.expanded.insert(folder)
        if done > 0 { toast = Toast(text: done == 1 ? "Uploaded" : "\(done) files uploaded") }
    }

    private func uploadPhotos(_ items: [PhotosPickerItem]) async {
        var urls: [URL] = []
        busy = "Preparing…"
        for item in items {
            if let file = try? await item.loadTransferable(type: ImportedFile.self) {
                urls.append(file.url)
            } else if let data = try? await item.loadTransferable(type: Data.self) {
                let ext = item.supportedContentTypes.first?.preferredFilenameExtension ?? "jpg"
                let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
                try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                let url = folder.appendingPathComponent("photo-\(Int(Date().timeIntervalSince1970))-\(urls.count).\(ext)")
                if (try? data.write(to: url)) != nil { urls.append(url) }
            }
        }
        photoItems = []
        await upload(urls, to: photoTarget, scoped: false)
    }

    static func temporaryCopy(of url: URL) throws -> URL {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("upload-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let target = folder.appendingPathComponent(url.lastPathComponent)
        try FileManager.default.copyItem(at: url, to: target)
        return target
    }

    private func shareFile(_ node: FSNode) async {
        guard let client = app.client else { return }
        busy = node.size > 0 ? "Downloading \(Fmt.bytes(node.size))…" : "Downloading…"
        defer { busy = nil }
        do {
            shared = try await Self.download(try await client.fileRequest(node.path, download: true), name: node.name)
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func downloadFolder(_ path: String, name: String) async {
        guard let client = app.client else { return }
        busy = "Packing \(name)…"
        defer { busy = nil }
        do {
            let base = name == "/" || name.isEmpty ? "server" : name
            shared = try await Self.download(try await client.folderArchiveRequest(path), name: base + ".zip")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    /// Fetches a file into a temporary folder, under its own name so the
    /// share sheet and QuickLook know what it is.
    static func download(_ request: URLRequest, name: String) async throws -> URL {
        let (temp, response) = try await NetworkSession.shared.download(for: request)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw APIClient.APIError.http(http.statusCode, http.statusCode == 403
                                          ? "PocketADM does not hand out this file." : "The server said \(http.statusCode).")
        }
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("files-\(UUID().uuidString)",
                                                                                   isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let target = folder.appendingPathComponent(name)
        try FileManager.default.moveItem(at: temp, to: target)
        return target
    }

    private func searchDeep() async {
        guard let client = app.client, filter.count >= 2 else { return }
        let base = root.isEmpty ? (roots.first ?? "") : root
        guard !base.isEmpty else { return }
        searching = true
        defer { searching = false }
        results = try? await client.searchFiles(in: base, query: filter)
    }
}

// MARK: - Pieces

struct NameRequest: Identifiable {
    enum Kind { case newFile, newFolder, rename(FSNode) }
    let id = UUID()
    let kind: Kind
    let folder: String

    var title: String {
        switch kind {
        case .newFile:   return "New file"
        case .newFolder: return "New folder"
        case .rename:    return "Rename"
        }
    }

    var confirm: String {
        switch kind {
        case .rename: return "Rename"
        default:      return "Create"
        }
    }
}

struct PickRequest: Identifiable {
    let id = UUID()
    let nodes: [FSNode]
    let copy: Bool
}

private struct SharedFile: Identifiable {
    let url: URL
    var id: String { url.path }
}

/// One row of the tree: the disclosure chevron, the icon, the name.
struct FileTreeRow: View {
    let node: FSNode
    let depth: Int
    var expanded = false
    var loading = false

    var body: some View {
        HStack(spacing: 8) {
            Group {
                if node.isDir {
                    if loading {
                        ProgressView().controlSize(.mini)
                    } else {
                        Image(systemName: "chevron.right")
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(Theme.muted)
                            .rotationEffect(.degrees(expanded ? 90 : 0))
                            .animation(.snappy(duration: 0.18), value: expanded)
                    }
                } else {
                    Color.clear
                }
            }
            .frame(width: 12)

            Image(systemName: symbol)
                .font(.body)
                .foregroundStyle(tint)
                .frame(width: 24)

            VStack(alignment: .leading, spacing: 1) {
                Text(node.name)
                    .font(.subheadline)
                    .foregroundStyle(node.hidden ? Theme.muted : Theme.text)
                    .lineLimit(1)
                    .truncationMode(.middle)
                if let drive = node.drive {
                    Text("\(drive.label.isEmpty ? driveName(drive.kind) : drive.label) · \(Int(drive.percent)) % full")
                        .font(.caption2)
                        .foregroundStyle(drive.percent >= 90 ? Theme.danger : Theme.muted)
                } else if !node.isDir && node.modified > 0 {
                    Text("\(Fmt.bytes(node.size)) · \(Fmt.ago(node.date))")
                        .font(.caption2)
                        .foregroundStyle(Theme.muted)
                }
            }
            Spacer(minLength: 0)
            if node.link {
                Image(systemName: "arrow.turn.up.right")
                    .font(.caption2)
                    .foregroundStyle(Theme.muted)
            }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
        .accessibilityHint(node.isDir ? (expanded ? "Folder, open" : "Folder") : "File")
    }

    private var symbol: String {
        if let drive = node.drive {
            return drive.kind == "external" ? "externaldrive.fill" : drive.kind == "network" ? "network" : "internaldrive.fill"
        }
        if node.isDir { return "folder.fill" }
        switch node.kind {
        case "image": return "photo.fill"
        case "video": return "film.fill"
        case "audio": return "music.note"
        case "pdf": return "doc.richtext.fill"
        case "archive": return "doc.zipper"
        case "document": return "doc.text.image.fill"
        default: return FileKind.symbol(for: node.name, text: node.text)
        }
    }

    private var tint: Color {
        if let drive = node.drive { return drive.kind == "external" ? .orange : drive.kind == "network" ? .indigo : .gray }
        if node.isDir { return node.hidden ? .blue.opacity(0.55) : .blue }
        switch node.kind {
        case "image": return .pink
        case "video": return .purple
        case "audio": return .orange
        case "pdf": return .red
        case "archive": return .brown
        case "document": return .teal
        default: return FileKind.tint(for: node.name, text: node.text)
        }
    }

    private func driveName(_ kind: String) -> String {
        switch kind {
        case "external": return "External drive"
        case "network": return "Network share"
        default: return "Drive"
        }
    }
}

/// A drive as a chip: what it is and how full.
struct DriveChip: View {
    let drive: Filesystem

    var body: some View {
        HStack(spacing: 10) {
            ZStack {
                Circle().stroke(tint.opacity(0.2), lineWidth: 3.5)
                Circle()
                    .trim(from: 0, to: max(0.02, min(1, drive.percent / 100)))
                    .stroke(tint, style: StrokeStyle(lineWidth: 3.5, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                Image(systemName: drive.kind == "external" ? "externaldrive.fill"
                      : drive.kind == "network" ? "network" : "internaldrive.fill")
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.muted)
            }
            .frame(width: 30, height: 30)
            VStack(alignment: .leading, spacing: 1) {
                Text(drive.title)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Theme.text)
                    .lineLimit(1)
                Text("\(Fmt.bytes(drive.free)) free")
                    .font(.caption2)
                    .foregroundStyle(Theme.muted)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Theme.bg2, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private var tint: Color {
        drive.percent >= 90 ? Theme.danger : drive.percent >= 80 ? Theme.warn : Theme.accent
    }
}

/// Where to move or copy something: the folders only, unfolding in place.
struct FolderPickerSheet: View {
    let request: PickRequest
    let startPath: String
    let startDisplay: String
    let choose: (String) -> Void

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss
    @StateObject private var tree = FileTreeModel()
    @State private var selected = ""

    var body: some View {
        NavigationStack {
            List {
                Section {
                    row(FSNode(root: startPath, display: startDisplay.isEmpty ? startPath : startDisplay), depth: 0)
                    ForEach(tree.rows(under: startPath)) { item in
                        row(item.node, depth: item.depth + 1)
                    }
                } footer: {
                    Text(request.nodes.count == 1 ? "\(request.copy ? "Copy" : "Move") “\(request.nodes[0].name)” into the selected folder."
                                                  : "\(request.nodes.count) items")
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle(request.copy ? "Copy to" : "Move to")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(request.copy ? "Copy here" : "Move here") {
                        choose(selected)
                        dismiss()
                    }
                    .disabled(selected.isEmpty)
                }
            }
            .task {
                tree.foldersOnly = true
                selected = startPath
                tree.expanded.insert(startPath)
                await tree.load(startPath, app: app)
            }
        }
    }

    private func row(_ node: FSNode, depth: Int) -> some View {
        HStack(spacing: 8) {
            Button {
                Task { await tree.toggle(node.path, app: app) }
            } label: {
                Image(systemName: "chevron.right")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(Theme.muted)
                    .rotationEffect(.degrees(tree.expanded.contains(node.path) ? 90 : 0))
                    .frame(width: 18, height: 28)
            }
            .buttonStyle(.borderless)
            Image(systemName: "folder.fill").foregroundStyle(.blue)
            Text(node.name).foregroundStyle(Theme.text).lineLimit(1)
            Spacer()
            if selected == node.path {
                Image(systemName: "checkmark").foregroundStyle(Theme.accent)
            }
        }
        .contentShape(Rectangle())
        .onTapGesture { selected = node.path }
        .listRowInsets(EdgeInsets(top: 0, leading: 10 + CGFloat(min(depth, 12)) * 16, bottom: 0, trailing: 12))
    }
}

/// Read, write, run — for the owner, the group and everyone else.
struct PermissionsSheet: View {
    let node: FSNode
    let apply: (String, Bool) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var bits: [Bool] = Array(repeating: false, count: 9)
    @State private var recursive = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    FactRow(label: "Owner", value: node.owner.isEmpty ? "—" : node.owner)
                    FactRow(label: "Now", value: node.mode.isEmpty ? "—" : node.mode, selectable: true)
                }
                Section {
                    Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 10) {
                        GridRow {
                            Text("")
                            Text("Read").font(.caption.weight(.semibold))
                            Text("Write").font(.caption.weight(.semibold))
                            Text(node.isDir ? "Open" : "Run").font(.caption.weight(.semibold))
                        }
                        ForEach(0..<3) { who in
                            GridRow {
                                Text(["Owner", "Group", "Everyone"][who]).font(.subheadline)
                                ForEach(0..<3) { what in
                                    Toggle("", isOn: $bits[who * 3 + what])
                                        .labelsHidden()
                                        .toggleStyle(CheckboxToggle())
                                }
                            }
                        }
                    }
                    .padding(.vertical, 4)
                } footer: {
                    Text("As a number: \(octal)")
                }
                if node.isDir {
                    Section {
                        Toggle("Also for everything inside", isOn: $recursive)
                    }
                }
            }
            .navigationTitle(node.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Apply") {
                        apply(octal, recursive)
                        dismiss()
                    }
                }
            }
            .onAppear { bits = Self.bits(from: node.mode) }
        }
    }

    private var octal: String {
        (0..<3).map { who in
            String((bits[who * 3] ? 4 : 0) + (bits[who * 3 + 1] ? 2 : 0) + (bits[who * 3 + 2] ? 1 : 0))
        }.joined()
    }

    /// "-rwxr-x---" → the nine switches.
    static func bits(from mode: String) -> [Bool] {
        let chars = Array(mode)
        guard chars.count >= 10 else { return [true, true, false, true, false, false, true, false, false] }
        return (1...9).map { chars[$0] != "-" }
    }
}

struct CheckboxToggle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        Button {
            configuration.isOn.toggle()
        } label: {
            Image(systemName: configuration.isOn ? "checkmark.square.fill" : "square")
                .font(.title3)
                .foregroundStyle(configuration.isOn ? Theme.accent : Theme.muted)
        }
        .buttonStyle(.borderless)
    }
}

/// A photo or video from the library, copied to a file the upload can stream.
struct ImportedFile: Transferable {
    let url: URL

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(importedContentType: .item) { received in
            let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let target = folder.appendingPathComponent(received.file.lastPathComponent)
            try FileManager.default.copyItem(at: received.file, to: target)
            return ImportedFile(url: target)
        }
    }
}

// MARK: - The editor

/// A text file: read it, change it, save it — the previous version stays on
/// the server for an undo, and a file that changed meanwhile is never
/// overwritten without asking.
struct FileEditorView: View {
    let node: FSNode
    var saved: () -> Void = {}

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var content: FileContent?
    @State private var text = ""
    @State private var original = ""
    @State private var editing = false
    @State private var saving = false
    @State private var error: String?
    @State private var conflict = false
    @State private var lastVersion = ""
    @State private var modified: Double = 0
    @State private var markdownPreview = false
    @State private var confirmDiscard = false
    @State private var toast: Toast?
    @FocusState private var focused: Bool

    private var dirty: Bool { text != original }
    private var isMarkdown: Bool { ["md", "markdown"].contains((node.name as NSString).pathExtension.lowercased()) }

    var body: some View {
        NavigationStack {
            Group {
                if let content {
                    if content.binary {
                        MessageState(symbol: "doc.questionmark", title: "Not a text file",
                                     message: "Open it from the file list to preview or share it.")
                    } else if editing {
                        TextEditor(text: $text)
                            .font(.system(size: 13, design: .monospaced))
                            .autocorrectionDisabled()
                            .textInputAutocapitalization(.never)
                            .keyboardType(.asciiCapable)
                            .scrollContentBackground(.hidden)
                            .background(Theme.termBg)
                            .foregroundStyle(Theme.termFg)
                            .focused($focused)
                    } else if markdownPreview && isMarkdown {
                        ScrollView {
                            MarkdownText(text: text, font: .body)
                                .padding(16)
                        }
                    } else {
                        CodeViewer(text: text)
                    }
                } else if let error {
                    MessageState(symbol: "exclamationmark.triangle", title: "Cannot open this file",
                                 message: error, tint: Theme.danger)
                } else {
                    ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .background((editing || !(markdownPreview && isMarkdown) ? Theme.termBg : Theme.bg).ignoresSafeArea())
            .navigationTitle(node.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbar { toolbar }
            .safeAreaInset(edge: .bottom) { footer }
            .alert("Changed on the server", isPresented: $conflict) {
                Button("Overwrite with mine", role: .destructive) { Task { await save(force: true) } }
                Button("Load theirs") { Task { await load() } }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Someone or something changed this file since you opened it.")
            }
            .confirmationDialog("Discard your changes?", isPresented: $confirmDiscard, titleVisibility: .visible) {
                Button("Discard", role: .destructive) { dismiss() }
                Button("Keep editing", role: .cancel) {}
            }
            .toast($toast)
            .task { await load() }
        }
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .cancellationAction) {
            Button(dirty ? "Cancel" : "Done") {
                if dirty { confirmDiscard = true } else { dismiss() }
            }
        }
        ToolbarItemGroup(placement: .primaryAction) {
            if editing {
                Button {
                    Task { await save(force: false) }
                } label: {
                    if saving { ProgressView() } else { Text("Save").fontWeight(.semibold) }
                }
                .disabled(!dirty || saving)
            } else if let content, !content.binary {
                if isMarkdown {
                    Button {
                        markdownPreview.toggle()
                    } label: {
                        Image(systemName: markdownPreview ? "chevron.left.forwardslash.chevron.right" : "eye")
                    }
                    .accessibilityLabel(markdownPreview ? "Show source" : "Preview")
                }
                Button("Edit") {
                    editing = true
                    markdownPreview = false
                    focused = true
                }
                .disabled(!canEdit)
            }
        }
    }

    @ViewBuilder
    private var footer: some View {
        if let content {
            HStack(spacing: 8) {
                Text(content.truncated ? "Showing the first 512 KB — too large to edit here"
                     : [node.shownPath, Fmt.bytes(Int64(text.utf8.count)), content.mode].filter { !$0.isEmpty }.joined(separator: " · "))
                    .lineLimit(1)
                    .truncationMode(.head)
                Spacer()
                if !lastVersion.isEmpty {
                    Button("Undo save") { Task { await undo() } }
                        .font(.caption.weight(.semibold))
                }
            }
            .font(.caption.monospaced())
            .foregroundStyle(content.truncated ? Theme.warn : Theme.muted)
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(.bar)
        }
    }

    private var canEdit: Bool {
        guard let content else { return false }
        return !content.binary && !content.truncated && content.writable && app.me?.demo != true
    }

    private func load() async {
        guard let client = app.client else { return }
        do {
            let result = try await client.readFile(node.path)
            content = result
            text = result.content
            original = result.content
            modified = result.modified
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func save(force: Bool) async {
        guard let client = app.client else { return }
        saving = true
        defer { saving = false }
        do {
            let result = try await client.writeFile(node.path, content: text,
                                                    expectedModified: force || modified == 0 ? nil : modified)
            original = text
            modified = result.modified
            lastVersion = result.version
            editing = false
            focused = false
            saved()
            toast = Toast(text: "Saved")
        } catch APIClient.APIError.http(409, _) {
            conflict = true
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func undo() async {
        guard let client = app.client, !lastVersion.isEmpty else { return }
        do {
            let result = try await client.restoreFile(version: lastVersion)
            lastVersion = ""
            modified = result.modified
            await load()
            saved()
            toast = Toast(text: "The previous version is back")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }
}

/// Source with line numbers, scrolling both ways: long lines stay one line.
struct CodeViewer: View {
    let text: String

    var body: some View {
        let lines = text.components(separatedBy: "\n")
        let width = CGFloat(String(lines.count).count) * 8 + 10
        ScrollView([.vertical, .horizontal]) {
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(Array(lines.enumerated()), id: \.offset) { index, line in
                    HStack(alignment: .top, spacing: 10) {
                        Text("\(index + 1)")
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(Theme.termFg.opacity(0.35))
                            .frame(width: width, alignment: .trailing)
                        Text(line.isEmpty ? " " : line)
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundStyle(Theme.termFg)
                            .fixedSize(horizontal: true, vertical: false)
                    }
                    .padding(.vertical, 1)
                }
            }
            .padding(.vertical, 10)
            .padding(.trailing, 16)
            .textSelection(.enabled)
        }
        .background(Theme.termBg)
    }
}
