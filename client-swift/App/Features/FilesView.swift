import SwiftUI

/// Read-only browser over the server's configured workspaces.
///
/// Deliberately read-only: editing a compose file from a phone with no diff and
/// no undo is how servers break. Reading one at 3am is how they get fixed.
struct FilesView: View {
    @EnvironmentObject private var app: AppState

    @State private var path = ""
    @State private var listing: FSListing?
    @State private var loading = true
    @State private var error: String?
    @State private var preview: FSListing.FileEntry?

    var body: some View {
        Group {
            if loading && listing == nil {
                ProgressView().tint(Theme.accent).frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let listing {
                content(listing)
            } else {
                MessageState(symbol: "folder.badge.questionmark",
                             title: "Cannot list files",
                             message: error,
                             tint: Theme.danger,
                             retry: { Task { await load(path) } })
            }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .screenBackground()
        .task { if listing == nil { await load("") } }
        .sheet(item: $preview) { entry in
            FilePreviewSheet(entry: entry)
        }
    }

    private var title: String {
        guard let listing, !listing.path.isEmpty else { return "Files" }
        return (listing.path as NSString).lastPathComponent
    }

    private func content(_ listing: FSListing) -> some View {
        List {
            if !listing.path.isEmpty {
                Section {
                    Text(listing.path)
                        .font(.system(size: 12, design: .monospaced))
                        .foregroundStyle(Theme.muted)
                        .textSelection(.enabled)
                }
                .listRowBackground(Theme.bg2)
            }

            if !listing.parent.isEmpty {
                Section {
                    Button {
                        Task { await load(listing.parent) }
                    } label: {
                        Label("Up one level", systemImage: "arrow.up.left")
                            .foregroundStyle(Theme.accent)
                    }
                }
                .listRowBackground(Theme.bg2)
            }

            if !listing.dirs.isEmpty {
                Section {
                    ForEach(listing.dirs) { dir in
                        Button {
                            Task { await load(dir.path) }
                        } label: {
                            HStack(spacing: 10) {
                                Image(systemName: "folder.fill")
                                    .foregroundStyle(Theme.accent)
                                    .frame(width: 20)
                                Text(dir.name)
                                    .font(.subheadline)
                                    .foregroundStyle(Theme.text)
                                Spacer()
                                Image(systemName: "chevron.right")
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(Theme.muted)
                            }
                        }
                    }
                    .listRowBackground(Theme.bg2)
                } header: {
                    SectionCaption(text: listing.path.isEmpty ? "Workspaces" : "Folders")
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
                            HStack(spacing: 10) {
                                Image(systemName: file.text ? "doc.text" : "doc")
                                    .foregroundStyle(file.text ? Theme.muted : Theme.border)
                                    .frame(width: 20)
                                Text(file.name)
                                    .font(.subheadline)
                                    .foregroundStyle(file.text ? Theme.text : Theme.muted)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                                Spacer()
                                Text(Fmt.bytes(file.size))
                                    .font(.caption)
                                    .foregroundStyle(Theme.muted)
                            }
                        }
                        .disabled(!file.text)
                    }
                    .listRowBackground(Theme.bg2)
                } header: {
                    SectionCaption(text: "\(listing.files) files")
                }
            }
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
        .background(Theme.bg)
        .refreshable { await load(path) }
    }

    private func load(_ next: String) async {
        guard let client = app.client else { return }
        loading = true
        defer { loading = false }
        do {
            listing = try await client.listDirectory(next)
            path = next
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
                    ProgressView().tint(Theme.accent).frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle(entry.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.bg2, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
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
