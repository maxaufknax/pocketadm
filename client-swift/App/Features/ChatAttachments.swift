import PhotosUI
import SwiftUI
import UIKit
import UniformTypeIdentifiers

// Attaching things to a message for the assistant: a picture or a file from
// this phone (uploaded to the server, where every assistant can open it), or
// context from the server itself — a file or folder, a container with its
// latest log lines, the overview, the health check's findings. Attachments
// show as chips above the composer; the context goes along with the message
// without cluttering the transcript.

/// Something attached to the message being written.
struct PendingAttachment: Identifiable, Hashable {
    let id = UUID()
    let name: String
    /// image, text, file, folder, service, system, health
    let kind: String
    /// What the assistant gets to read (quoted into the message).
    let context: String
    /// An uploaded picture's path on the server, for models that see images.
    var imagePath: String = ""

    var label: ChatAttachmentLabel { ChatAttachmentLabel(name: name, kind: kind) }

    static let contextOpen = "[Attached context — provided by the user for this request]"
    static let contextClose = "[/Attached context]"

    /// The preamble the server puts in front of the message (and the
    /// transcript hides again, chats.py).
    static func preamble(_ items: [PendingAttachment]) -> String {
        guard !items.isEmpty else { return "" }
        return contextOpen + "\n\n" + items.map(\.context).joined(separator: "\n\n---\n\n")
            + "\n\n" + contextClose
    }
}

/// The chips above the composer, each with a remove button.
struct AttachmentChips: View {
    @Binding var items: [PendingAttachment]
    var busy: String = ""

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                if !busy.isEmpty {
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.mini)
                        Text(busy).font(.caption.weight(.medium)).foregroundStyle(Theme.muted)
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(Theme.bubble, in: Capsule())
                }
                ForEach(items) { item in
                    HStack(spacing: 6) {
                        Image(systemName: item.label.symbol)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(Theme.accent)
                        Text(item.name)
                            .font(.caption.weight(.medium))
                            .foregroundStyle(Theme.text)
                            .lineLimit(1)
                            .frame(maxWidth: 160)
                        Button {
                            withAnimation(.snappy) { items.removeAll { $0.id == item.id } }
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .foregroundStyle(Theme.muted)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Remove \(item.name)")
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(Theme.bubble, in: Capsule())
                }
            }
            .padding(.horizontal, 14)
            .padding(.top, 8)
        }
    }
}

/// The chips under a sent message.
struct SentAttachments: View {
    let items: [ChatAttachmentLabel]

    var body: some View {
        HStack(spacing: 6) {
            ForEach(items, id: \.self) { item in
                Label(item.name, systemImage: item.symbol)
                    .font(.caption2.weight(.medium))
                    .lineLimit(1)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Theme.bubble, in: Capsule())
                    .foregroundStyle(Theme.muted)
            }
        }
    }
}

/// The paperclip: every way to attach something.
struct AttachButton: View {
    @Binding var items: [PendingAttachment]
    @Binding var busy: String
    var onError: (String) -> Void

    @EnvironmentObject private var app: AppState
    @State private var photos: [PhotosPickerItem] = []
    @State private var showPhotos = false
    @State private var showFiles = false
    @State private var showServerFiles = false
    @State private var showContainers = false

    var body: some View {
        Menu {
            Section("From this iPhone") {
                Button { showPhotos = true } label: { Label("Photo or screenshot", systemImage: "photo") }
                Button { showFiles = true } label: { Label("File", systemImage: "doc") }
            }
            Section("From the server") {
                Button { showServerFiles = true } label: { Label("File or folder", systemImage: "folder") }
                Button { showContainers = true } label: { Label("Container and its logs", systemImage: "shippingbox") }
                Button { Task { await attachOverview() } } label: {
                    Label("Server overview", systemImage: "gauge.with.dots.needle.33percent")
                }
                Button { Task { await attachHealth() } } label: {
                    Label("Health check findings", systemImage: "checkmark.shield")
                }
            }
        } label: {
            Image(systemName: "plus")
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(Theme.muted)
                .frame(width: 32, height: 32)
                .background(Theme.bubble, in: Circle())
        }
        .disabled(!app.supports("chat_attachments") || app.me?.demo == true)
        .accessibilityLabel("Attach")
        .photosPicker(isPresented: $showPhotos, selection: $photos, maxSelectionCount: 4, matching: .images)
        .onChange(of: photos) { _, picked in
            guard !picked.isEmpty else { return }
            let chosen = picked
            photos = []
            Task { await uploadPhotos(chosen) }
        }
        .fileImporter(isPresented: $showFiles, allowedContentTypes: [.item], allowsMultipleSelection: false) { result in
            if case .success(let urls) = result, let url = urls.first {
                Task { await uploadFile(url) }
            }
        }
        .sheet(isPresented: $showServerFiles) {
            ServerPathPicker { attachment in items.append(attachment) }
        }
        .sheet(isPresented: $showContainers) {
            ContainerContextPicker { attachment in items.append(attachment) }
        }
    }

    // MARK: - From the phone

    private func uploadPhotos(_ picked: [PhotosPickerItem]) async {
        guard let client = app.client else { return }
        for (index, item) in picked.enumerated() {
            busy = picked.count > 1 ? "Uploading \(index + 1) of \(picked.count)…" : "Uploading picture…"
            do {
                guard let raw = try await item.loadTransferable(type: Data.self),
                      let jpeg = Self.compressed(raw) else { continue }
                let name = "photo-\(Self.stamp())\(picked.count > 1 ? "-\(index + 1)" : "").jpg"
                let up = try await client.uploadChatFile(jpeg, name: name)
                items.append(PendingAttachment(
                    name: up.name, kind: "image",
                    context: "Picture from the user's phone, stored on the server at \(up.path).",
                    imagePath: up.path))
            } catch {
                onError(error.localizedDescription)
            }
        }
        busy = ""
    }

    private func uploadFile(_ url: URL) async {
        guard let client = app.client else { return }
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        busy = "Uploading \(url.lastPathComponent)…"
        defer { busy = "" }
        do {
            let data = try Data(contentsOf: url)
            guard data.count <= 25 * 1024 * 1024 else {
                onError("Files can be up to 25 MB.")
                return
            }
            let up = try await client.uploadChatFile(data, name: url.lastPathComponent)
            items.append(PendingAttachment(name: up.name, kind: up.kind == "image" ? "image" : up.kind,
                                           context: Self.describe(up),
                                           imagePath: up.kind == "image" ? up.path : ""))
        } catch {
            onError(error.localizedDescription)
        }
    }

    static func describe(_ up: ChatUpload) -> String {
        switch up.kind {
        case "image":
            return "Picture from the user's phone, stored on the server at \(up.path)."
        case "text":
            return "File from the user's phone, stored on the server at \(up.path):\n\n```\n\(up.text)\n```"
                + (up.truncated ? "\n[only the beginning — read the rest from the file]" : "")
        default:
            return "File from the user's phone (\(up.mediaType), \(Fmt.bytes(up.size))), stored on the server at \(up.path)."
        }
    }

    /// At most 1600 px on the long side, as JPEG: plenty for a screenshot of
    /// an error, and small enough for any model.
    static func compressed(_ data: Data) -> Data? {
        guard let image = UIImage(data: data) else { return nil }
        let longest = max(image.size.width, image.size.height)
        let scale = min(1, 1600 / max(longest, 1))
        let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let resized = UIGraphicsImageRenderer(size: size, format: format).image { _ in
            image.draw(in: CGRect(origin: .zero, size: size))
        }
        return resized.jpegData(compressionQuality: 0.8)
    }

    static func stamp() -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyyMMdd-HHmmss"
        return f.string(from: Date())
    }

    // MARK: - From the server

    private func attachOverview() async {
        guard let client = app.client else { return }
        busy = "Reading the server…"
        defer { busy = "" }
        var lines: [String] = []
        if let s = try? await client.system() {
            lines.append("Server \(s.hostname): CPU \(Fmt.percent(s.cpuPercent)), memory \(Fmt.percent(s.memory.percent)), "
                         + "disk \(Fmt.percent(s.disk.percent)), load \(s.load.map { String(format: "%.2f", $0) }.joined(separator: " ")), "
                         + "up \(Fmt.uptime(s.uptime)).")
        }
        if let containers = try? await client.containers() {
            let down = containers.filter { !$0.isRunning || $0.health == "unhealthy" }
            lines.append("Containers: \(containers.filter(\.isRunning).count) of \(containers.count) running."
                         + (down.isEmpty ? "" : " Not fine: " + down.map { "\($0.name) (\($0.health.isEmpty ? $0.state : $0.health))" }.joined(separator: ", ")))
        }
        if app.supports("inventory"), let inv = try? await client.inventory() {
            let failed = inv.failedUnits.map(\.unit)
            lines.append("Domains: \(inv.domains.count). Systemd units watched: \(inv.services.count + inv.timers.count)"
                         + (failed.isEmpty ? "." : ", failed: \(failed.joined(separator: ", ")).")
                         + " Drives: " + inv.drives.filter { $0.kind != "boot" }
                            .map { "\($0.mount) \(Int($0.percent))%" }.joined(separator: ", "))
        }
        guard !lines.isEmpty else {
            onError("The server overview could not be read.")
            return
        }
        items.append(PendingAttachment(name: "Server overview", kind: "system",
                                       context: "Server overview (from PocketADM, just now):\n" + lines.joined(separator: "\n")))
    }

    private func attachHealth() async {
        guard let client = app.client else { return }
        busy = "Reading the health check…"
        defer { busy = "" }
        do {
            let report = try await client.latestReport()
            let findings = report.needsAttention
            let text = findings.isEmpty
                ? "The last health check (\(Fmt.ago(report.date))) found nothing that needs attention."
                : "Findings of the last health check (\(Fmt.ago(report.date))):\n" + findings.map {
                    "- [\($0.status.label)] \($0.title): \($0.summary)"
                        + (($0.recommendation ?? "").isEmpty ? "" : " → \($0.recommendation ?? "")")
                }.joined(separator: "\n")
            items.append(PendingAttachment(name: findings.isEmpty ? "Health: all good" : "\(findings.count) health findings",
                                           kind: "health", context: text))
        } catch {
            onError(error.localizedDescription)
        }
    }
}

// MARK: - A file or folder on the server

struct ServerPathPicker: View {
    let onPick: (PendingAttachment) -> Void

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss
    @State private var path = ""
    @State private var listing: FSListing?
    @State private var error: String?
    @State private var loading = false

    var body: some View {
        NavigationStack {
            ThemedList {
                if let listing {
                    Section {
                        if !listing.parent.isEmpty {
                            Button {
                                Task { await open(listing.parent) }
                            } label: {
                                Label("Up one level", systemImage: "arrow.turn.left.up")
                            }
                        }
                        Button {
                            Task { await attachFolder(listing) }
                        } label: {
                            Label("Attach this folder's contents", systemImage: "folder.badge.plus")
                                .font(.subheadline.weight(.semibold))
                        }
                    } header: {
                        Text(listing.shownPath.isEmpty ? "/" : listing.shownPath)
                            .textCase(nil)
                            .lineLimit(1)
                            .truncationMode(.head)
                    }
                    Section {
                        ForEach(listing.dirs) { dir in
                            Button {
                                Task { await open(dir.path) }
                            } label: {
                                Label(dir.name, systemImage: "folder.fill")
                                    .foregroundStyle(Theme.text)
                            }
                        }
                        ForEach(listing.fileEntries) { file in
                            Button {
                                Task { await attachFile(file.path, name: file.name) }
                            } label: {
                                HStack {
                                    Label(file.name, systemImage: "doc.text")
                                        .foregroundStyle(Theme.text)
                                    Spacer()
                                    Text(Fmt.bytes(file.size))
                                        .font(.caption)
                                        .foregroundStyle(Theme.muted)
                                }
                            }
                        }
                    } footer: {
                        Text("A text file is quoted into your message; for anything else the assistant gets its path.")
                    }
                } else if let error {
                    Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(Theme.danger) }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Attach from the server")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            }
            .overlay { if loading { ProgressView() } }
            .task {
                guard listing == nil, let client = app.client else { return }
                let start = (try? await client.fsStart())?.path ?? ""
                await open(start)
            }
        }
    }

    private func open(_ target: String) async {
        guard let client = app.client else { return }
        loading = true
        defer { loading = false }
        do {
            listing = try await client.listDirectory(target, files: true)
            path = target
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func attachFile(_ filePath: String, name: String) async {
        guard let client = app.client else { return }
        loading = true
        defer { loading = false }
        do {
            let file = try await client.readFile(filePath)
            let shown = file.path.hasPrefix("/host/") ? String(file.path.dropFirst(5)) : file.path
            let context = file.binary
                ? "File on the server: \(shown) (binary, \(Fmt.bytes(file.size)))."
                : "File on the server: \(shown)\n\n```\n\(file.content)\n```" + (file.truncated ? "\n[truncated]" : "")
            onPick(PendingAttachment(name: name, kind: "file", context: context))
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func attachFolder(_ listing: FSListing) async {
        let names = listing.dirs.map { $0.name + "/" } + listing.fileEntries.map(\.name)
        let shown = listing.shownPath.isEmpty ? "/" : listing.shownPath
        onPick(PendingAttachment(name: (shown as NSString).lastPathComponent.isEmpty ? "/" : (shown as NSString).lastPathComponent,
                                 kind: "folder",
                                 context: "Folder on the server: \(shown)\nContents:\n" + names.prefix(300).joined(separator: "\n")))
        dismiss()
    }
}

// MARK: - A container with its recent logs

struct ContainerContextPicker: View {
    let onPick: (PendingAttachment) -> Void

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss
    @State private var containers: [Container] = []
    @State private var loading = true
    @State private var query = ""

    var body: some View {
        NavigationStack {
            ThemedList {
                ForEach(containers.filter { query.isEmpty || $0.displayName.localizedCaseInsensitiveContains(query) }) { c in
                    Button {
                        Task { await attach(c) }
                    } label: {
                        HStack(spacing: 12) {
                            ServiceIcon(names: [c.composeService, c.name, c.image], size: 30)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(c.displayName).foregroundStyle(Theme.text)
                                Text(c.status).font(.caption).foregroundStyle(Theme.muted).lineLimit(1)
                            }
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .searchable(text: $query)
            .navigationTitle("Attach a container")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            }
            .overlay { if loading { ProgressView() } }
            .task {
                containers = (try? await app.client?.containers()) ?? []
                loading = false
            }
        }
    }

    private func attach(_ c: Container) async {
        loading = true
        let logs = (try? await app.client?.containerLogs(c.id, tail: 40)) ?? ""
        let ports = c.ports.compactMap(\.publicPort).map(String.init).joined(separator: ", ")
        var text = "Container \(c.name)\nImage: \(c.image)\nState: \(c.state) (\(c.status))"
        if !c.composeProject.isEmpty { text += "\nCompose project: \(c.composeProject)" }
        if !ports.isEmpty { text += "\nPublished ports: \(ports)" }
        if !logs.isEmpty { text += "\nLast log lines:\n```\n\(logs.suffix(6000))\n```" }
        onPick(PendingAttachment(name: c.displayName, kind: "service", context: text))
        loading = false
        dismiss()
    }
}
