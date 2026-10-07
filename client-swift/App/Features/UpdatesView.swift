import SwiftUI

@MainActor
final class UpdatesModel: ObservableObject {
    @Published var updates: UpdatesResponse?
    @Published var snapshots: [Snapshot] = []
    @Published var loaded = false
    @Published var checking = false
    @Published var error: String?

    func load(_ app: AppState, force: Bool = false) async {
        guard let client = app.client else { return }
        if force { checking = true }
        defer { checking = false; loaded = true }
        do {
            // Snapshots are cheap and always current; the update check may hit
            // a registry, so a failure there must not hide the rollback list.
            async let updates = client.updates(force: force)
            async let snapshots = client.snapshots()
            self.snapshots = (try? await snapshots) ?? []
            self.updates = try await updates
            error = nil
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
    }
}

struct UpdatesView: View {
    @EnvironmentObject private var app: AppState
    @StateObject private var model = UpdatesModel()

    /// One sheet, not two.
    ///
    /// Applying an update dismisses the detail sheet and opens the job console
    /// in the same tick. With two `.sheet` modifiers SwiftUI drops the second
    /// presentation when the first is still animating out, and the update
    /// appears to do nothing. A single enum makes it one transition.
    @State private var sheet: Sheet?
    @State private var confirmAll = false
    @State private var toast: Toast?

    enum Sheet: Identifiable {
        case detail(DockerUpdate)
        case job(PendingJob)

        var id: String {
            switch self {
            case .detail(let update): return "detail-" + update.image
            case .job(let job):       return "job-" + job.id
            }
        }
    }

    var body: some View {
        Group {
            if !model.loaded {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let updates = model.updates {
                content(updates)
            } else {
                MessageState(symbol: "exclamationmark.triangle",
                             title: "Cannot check for updates",
                             message: model.error,
                             tint: Theme.danger,
                             retry: { Task { await model.load(app) } })
            }
        }
        .navigationTitle("Updates")
        .navigationBarTitleDisplayMode(.large)
        .toast($toast)
        .task { if !model.loaded { await model.load(app) } }
        .sheet(item: $sheet) { which in
            switch which {
            case .detail(let update):
                UpdateDetailSheet(update: update) { image in
                    await apply(images: [image], title: update.displayName)
                } onIgnore: { image, ignored in
                    await setIgnored(image: image, ignored: ignored)
                }
            case .job(let pending):
                JobConsoleView(jobID: pending.id, title: pending.title) { _ in
                    Task { await model.load(app) }
                }
            }
        }
        .confirmationDialog("Update everything?", isPresented: $confirmAll, titleVisibility: .visible) {
            Button("Update \(pendingCount) images", role: .destructive) {
                Task { await apply(images: pendingImages, title: "All updates") }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Each image is pulled and its containers recreated. A snapshot is taken first, so any of them can be rolled back.")
        }
    }

    private var pendingImages: [String] { model.updates?.pending.map(\.image) ?? [] }
    private var pendingCount: Int { pendingImages.count }

    private func content(_ updates: UpdatesResponse) -> some View {
        List {
            if !updates.pending.isEmpty {
                Section {
                    ForEach(updates.pending) { update in
                        Button { sheet = .detail(update) } label: { UpdateRow(update: update) }
                            .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                Button {
                                    Task { await setIgnored(image: update.image, ignored: true) }
                                } label: { Label("Ignore", systemImage: "bell.slash") }
                                    .tint(Theme.muted)
                            }
                    }
                } header: {
                    HStack {
                        Text("\(updates.pending.count) available")
                        Spacer()
                        if updates.pending.count > 1 {
                            Button("Update all") { confirmAll = true }
                                .font(.footnote.weight(.semibold))
                                .textCase(nil)
                        }
                    }
                }
            } else {
                Section {
                    HStack(spacing: 14) {
                        IconTile(symbol: "checkmark", color: .green)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Everything is up to date")
                                .foregroundStyle(Theme.text)
                            Text("Checked against each image's registry")
                                .font(.footnote)
                                .foregroundStyle(Theme.muted)
                        }
                    }
                }
            }

            if updates.apt.available && !updates.apt.packages.isEmpty {
                Section {
                    ForEach(updates.apt.packages) { package in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(package.package)
                                .font(.subheadline.weight(.medium))
                                .foregroundStyle(Theme.text)
                            Text("\(package.current) → \(package.new)")
                                .font(.footnote)
                                .foregroundStyle(Theme.muted)
                        }
                    }
                } header: {
                    SectionCaption(text: "Host packages")
                } footer: {
                    // No apply button on purpose: apt on the host is outside
                    // what this app can safely drive from a phone, and the
                    // server offers no endpoint for it either.
                    Text("Apply these over SSH — `apt upgrade` is not run from the app.")
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                }
            }

            if !model.snapshots.isEmpty {
                Section {
                    ForEach(model.snapshots) { snapshot in
                        SnapshotRow(snapshot: snapshot) {
                            await rollback(snapshot)
                        } onDelete: {
                            await deleteSnapshot(snapshot)
                        }
                    }
                } header: {
                    SectionCaption(text: "Rollback points")
                } footer: {
                    Text("Taken automatically before each update. Rolling back retags the old image and recreates its containers.")
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                }
            }

            if !updates.ignored.isEmpty {
                Section {
                    ForEach(updates.ignored) { update in
                        HStack {
                            Text(update.displayName)
                                .font(.subheadline)
                                .foregroundStyle(Theme.muted)
                            Spacer()
                            Button("Watch again") {
                                Task { await setIgnored(image: update.image, ignored: false) }
                            }
                            .font(.caption)
                            .tint(Theme.accent)
                        }
                    }
                } header: {
                    SectionCaption(text: "Ignored")
                }
            }

            if !updates.upToDate.isEmpty {
                Section {
                    DisclosureGroup {
                        ForEach(updates.upToDate) { update in
                            HStack(spacing: 12) {
                                ServiceIcon(names: [update.label, update.image], category: update.category, size: 28)
                                Text(update.displayName)
                                    .foregroundStyle(Theme.text)
                                Spacer()
                                Text(update.tag)
                                    .font(.footnote)
                                    .foregroundStyle(Theme.muted)
                            }
                        }
                    } label: {
                        Text("\(updates.upToDate.count) images up to date")
                            .foregroundStyle(Theme.text)
                    }
                }
            }
        }
        .refreshable { await model.load(app, force: true) }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                if model.checking {
                    ProgressView()
                } else {
                    Button {
                        Task { await model.load(app, force: true) }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .accessibilityLabel("Check again")
                }
            }
        }
    }

    // MARK: - Actions

    private func apply(images: [String], title: String) async {
        guard let client = app.client, !images.isEmpty else { return }
        do {
            let jobID = images.count == 1
                ? try await client.applyUpdate(image: images[0])
                : try await client.applyAllUpdates(images: images)
            // Replaces the detail sheet rather than closing and reopening.
            sheet = .job(PendingJob(id: jobID, title: title))
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
            app.handle(error)
        }
    }

    private func setIgnored(image: String, ignored: Bool) async {
        guard let client = app.client else { return }
        do {
            try await client.setUpdateIgnored(image: image, ignored: ignored)
            sheet = nil
            toast = Toast(text: ignored ? "Ignored" : "Watching again")
            await model.load(app)
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
            app.handle(error)
        }
    }

    private func rollback(_ snapshot: Snapshot) async {
        guard let client = app.client else { return }
        do {
            let jobID = try await client.rollbackSnapshot(snapshot.id)
            sheet = .job(PendingJob(id: jobID, title: "Roll back \(snapshot.image)"))
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
            app.handle(error)
        }
    }

    private func deleteSnapshot(_ snapshot: Snapshot) async {
        guard let client = app.client else { return }
        do {
            try await client.deleteSnapshot(snapshot.id)
            await model.load(app)
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
            app.handle(error)
        }
    }
}

// MARK: - Rows

struct UpdateRow: View {
    let update: DockerUpdate

    var body: some View {
        HStack(spacing: 14) {
            ServiceIcon(names: [update.label, update.image], category: update.category)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(update.displayName)
                        .foregroundStyle(Theme.text)
                    if update.security {
                        // The catalog flags images whose updates usually carry
                        // security fixes — worth a badge, not a separate list.
                        StatusPill(text: "security", tint: Theme.danger)
                    }
                }
                Text(subtitle)
                    .font(.footnote)
                    .foregroundStyle(Theme.muted)
                    .lineLimit(1)
            }

            Spacer(minLength: 8)

            Image(systemName: "chevron.right")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(Color(uiColor: .tertiaryLabel))
        }
        .padding(.vertical, 2)
    }

    private var subtitle: String {
        var parts: [String] = []
        if !update.tag.isEmpty { parts.append(update.tag) }
        if let age = update.ageDays, age > 0 { parts.append("\(age)d old") }
        if !update.usedBy.isEmpty { parts.append(update.usedBy.joined(separator: ", ")) }
        return parts.joined(separator: " · ")
    }
}

struct SnapshotRow: View {
    let snapshot: Snapshot
    let onRollback: () async -> Void
    let onDelete: () async -> Void

    @State private var confirming = false

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 3) {
                Text(snapshot.image)
                    .foregroundStyle(Theme.text)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Text("\(Fmt.ago(snapshot.date)) · \(snapshot.imageID)")
                    .font(.footnote)
                    .foregroundStyle(Theme.muted)
            }
            Spacer()
            Button("Roll back") { confirming = true }
                .font(.footnote.weight(.semibold))
                .buttonStyle(.bordered)
                .buttonBorderShape(.capsule)
                .tint(.orange)
        }
        .swipeActions(edge: .trailing) {
            Button(role: .destructive) {
                Task { await onDelete() }
            } label: { Label("Delete", systemImage: "trash") }
        }
        .confirmationDialog("Roll back \(snapshot.image)?",
                            isPresented: $confirming, titleVisibility: .visible) {
            Button("Roll back", role: .destructive) { Task { await onRollback() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The old image is retagged and its containers are recreated. Data in named volumes is untouched.")
        }
    }
}

// MARK: - Detail sheet

struct UpdateDetailSheet: View {
    let update: DockerUpdate
    let onApply: (String) async -> Void
    let onIgnore: (String, Bool) async -> Void

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var detail: UpdateDetail?
    @State private var loading = true
    @State private var applying = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    header

                    FactsCard {
                        FactRow(label: "Image", value: update.image, selectable: true)
                        HairlineDivider()
                        FactRow(label: "Tag", value: update.tag.isEmpty ? "—" : update.tag)
                        if let local = detail?.local, !local.version.isEmpty {
                            HairlineDivider()
                            FactRow(label: "Installed", value: local.version)
                        }
                        if let created = detail?.local.created, !created.isEmpty {
                            HairlineDivider()
                            FactRow(label: "Built", value: created)
                        }
                        if !update.usedBy.isEmpty {
                            HairlineDivider()
                            FactRow(label: "Used by", value: update.usedBy.joined(separator: ", "))
                        }
                        if !update.error.isEmpty {
                            HairlineDivider()
                            FactRow(label: "Check failed", value: update.error, tint: Theme.danger)
                        }
                    }

                    Button {
                        applying = true
                        Task { await onApply(update.image) }
                    } label: {
                        if applying {
                            ProgressView().tint(Theme.onAccent)
                        } else {
                            Label("Update now", systemImage: "arrow.down.circle.fill")
                        }
                    }
                    .buttonStyle(PrimaryButtonStyle(enabled: !applying))
                    .disabled(applying)

                    Text("A snapshot of the running image is taken first, so this can be undone from the Updates screen.")
                        .font(.caption)
                        .foregroundStyle(Theme.muted)

                    releases

                    Button(update.ignored ? "Watch this image again" : "Ignore this image") {
                        Task { await onIgnore(update.image, !update.ignored) }
                    }
                    .buttonStyle(SecondaryButtonStyle(tint: Theme.muted))
                }
                .padding(16)
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle(update.displayName)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .task { await load() }
        }
    }

    private var header: some View {
        HStack(spacing: 14) {
            ServiceIcon(names: [update.label, update.image], category: update.category, size: 56)
            VStack(alignment: .leading, spacing: 3) {
                Text(update.displayName)
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(Theme.text)
                if !update.category.isEmpty {
                    Text(update.category)
                        .font(.subheadline)
                        .foregroundStyle(Theme.muted)
                }
            }
            Spacer()
            if update.isHighPriority {
                StatusPill(text: "high priority", tint: Theme.warn)
            }
        }
    }

    @ViewBuilder
    private var releases: some View {
        if loading {
            HStack {
                ProgressView().tint(Theme.muted)
                Text("Looking up release notes…")
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
            }
        } else if let releases = detail?.releases, !releases.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                SectionCaption(text: "What's new upstream")
                ForEach(releases) { release in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text(release.name.isEmpty ? release.tag : release.name)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(Theme.text)
                            if release.prerelease {
                                StatusPill(text: "pre", tint: Theme.warn)
                            }
                            Spacer()
                            Text(release.date)
                                .font(.caption)
                                .foregroundStyle(Theme.muted)
                        }
                        if !release.notes.isEmpty {
                            MarkdownText(text: String(release.notes.prefix(900)), font: .caption)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .card()
                }
            }
        }
    }

    private func load() async {
        guard let client = app.client else { loading = false; return }
        defer { loading = false }
        // Release notes come from GitHub and are best-effort; the sheet is
        // useful without them, so a failure is silent.
        detail = try? await client.updateDetail(image: update.image)
    }
}
