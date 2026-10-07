import SwiftUI

/// Read-only browser over the server's configured workspaces.
///
/// Deliberately read-only: editing a compose file from a phone with no diff and
/// no undo is how servers break. Reading one at 3am is how they get fixed.
struct FilesView: View {
    /// The folder this screen shows; "" is the list of workspaces. Every
    /// folder is its own screen, pushed like in the Files app, so going up is
    /// the back button and the swipe.
    var start: String = ""

    @EnvironmentObject private var app: AppState

    @State private var listing: FSListing?
    @State private var loading = true
    @State private var error: String?
    @State private var preview: FSListing.FileEntry?

    var body: some View {
        Group {
            if loading && listing == nil {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let listing {
                content(listing)
            } else {
                MessageState(symbol: "folder.badge.questionmark",
                             title: "Cannot list files",
                             message: error,
                             tint: Theme.danger,
                             retry: { Task { await load() } })
            }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(start.isEmpty ? .large : .inline)
        .task { if listing == nil { await load() } }
        .sheet(item: $preview) { entry in
            FilePreviewSheet(entry: entry)
        }
    }

    private var title: String {
        guard !start.isEmpty else { return "Files" }
        return (start as NSString).lastPathComponent
    }

    private func content(_ listing: FSListing) -> some View {
        List {
            if listing.dirs.isEmpty && listing.fileEntries.isEmpty {
                Section {
                    Text("This folder is empty.")
                        .foregroundStyle(Theme.muted)
                }
            }

            if !listing.dirs.isEmpty {
                Section {
                    ForEach(listing.dirs) { dir in
                        NavigationLink {
                            FilesView(start: dir.path)
                        } label: {
                            Label {
                                Text(dir.name)
                                    .foregroundStyle(Theme.text)
                            } icon: {
                                Image(systemName: "folder.fill")
                                    .foregroundStyle(.blue)
                            }
                        }
                    }
                } header: {
                    Text(listing.path.isEmpty ? "Workspaces" : "Folders")
                }
            }

            if !listing.fileEntries.isEmpty {
                Section {
                    ForEach(listing.fileEntries) { file in
                        Button {
                            // Binaries have nothing to show; the server already
                            // told us which is which.
                            if file.text { preview = file }
                        } label: {
                            HStack(spacing: 12) {
                                Image(systemName: file.text ? "doc.text.fill" : "doc.fill")
                                    .foregroundStyle(file.text ? Color.gray : Color(uiColor: .systemGray3))
                                    .frame(width: 24)
                                Text(file.name)
                                    .foregroundStyle(file.text ? Theme.text : Theme.muted)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                                Spacer()
                                Text(Fmt.bytes(file.size))
                                    .font(.footnote)
                                    .foregroundStyle(Theme.muted)
                            }
                        }
                        .disabled(!file.text)
                    }
                } header: {
                    Text("\(listing.files) files")
                }
            }

            if !listing.path.isEmpty {
                Section {
                    Text(listing.path)
                        .font(.system(.footnote, design: .monospaced))
                        .foregroundStyle(Theme.muted)
                        .textSelection(.enabled)
                } header: {
                    Text("Path")
                }
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await load() }
    }

    private func load() async {
        guard let client = app.client else { return }
        loading = true
        defer { loading = false }
        do {
            listing = try await client.listDirectory(start)
            error = nil
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
    }
}

struct FilePreviewSheet: View {
    let entry: FSListing.FileEntry

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var content: FileContent?
    @State private var error: String?

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
                } else if let error {
                    MessageState(symbol: "exclamationmark.triangle",
                                 title: "Cannot read this file",
                                 message: error,
                                 tint: Theme.danger)
                } else {
                    ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle(entry.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
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
            .task { await load() }
        }
    }

    private func load() async {
        guard let client = app.client else { return }
        do {
            let result = try await client.readFile(entry.path)
            if result.binary {
                error = "This file is binary."
            } else {
                content = result
            }
        } catch {
            self.error = error.localizedDescription
        }
    }
}
