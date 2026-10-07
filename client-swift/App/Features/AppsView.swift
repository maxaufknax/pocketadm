import SwiftUI

@MainActor
final class AppCatalogModel: ObservableObject {
    @Published var response: AppsResponse?
    @Published var loaded = false
    @Published var error: String?

    func load(_ app: AppState) async {
        guard let client = app.client else { return }
        defer { loaded = true }
        do {
            response = try await client.apps()
            error = nil
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
    }

    func installedState(_ id: String) -> InstalledApp? { response?.installed[id] }
}

/// The one-tap catalog: pick a service, answer at most a couple of questions,
/// and the server writes a compose file and brings it up.
struct AppsView: View {
    @EnvironmentObject private var app: AppState
    @StateObject private var model = AppCatalogModel()

    @State private var search = ""
    @State private var category = "All"
    @State private var selected: CatalogApp?
    @State private var toast: Toast?

    var body: some View {
        Group {
            if !model.loaded {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let response = model.response, !response.catalog.isEmpty {
                content(response)
            } else {
                MessageState(symbol: "square.grid.2x2",
                             title: "No catalog",
                             message: model.error ?? "This server's app catalog is empty.",
                             tint: model.error == nil ? Theme.muted : Theme.danger,
                             retry: { Task { await model.load(app) } })
            }
        }
        .navigationTitle("Apps")
        .navigationBarTitleDisplayMode(.large)
        .toast($toast)
        .task { if !model.loaded { await model.load(app) } }
        .sheet(item: $selected) { entry in
            AppDetailSheet(entry: entry,
                           installed: model.installedState(entry.id)) { message in
                toast = Toast(text: message)
                Task { await model.load(app) }
            }
        }
    }

    private func content(_ response: AppsResponse) -> some View {
        List {
            if !installed(response).isEmpty {
                Section {
                    ForEach(installed(response)) { entry in
                        Button { selected = entry } label: {
                            AppRow(entry: entry, installed: response.installed[entry.id])
                        }
                    }
                } header: {
                    SectionCaption(text: "Running on this server")
                }
            }

            Section {
                ForEach(available(response)) { entry in
                    Button { selected = entry } label: {
                        AppRow(entry: entry, installed: nil)
                    }
                }
            } header: {
                SectionCaption(text: category == "All" ? "Catalog" : category)
            } footer: {
                if available(response).isEmpty {
                    Text("Nothing matches.")
                }
            }
        }
        .searchable(text: $search, prompt: "Search apps")
        .refreshable { await model.load(app) }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Picker("Category", selection: $category) {
                        Text("All").tag("All")
                        ForEach(response.categories, id: \.self) { name in
                            Text(name).tag(name)
                        }
                    }
                } label: {
                    Image(systemName: category == "All"
                          ? "line.3.horizontal.decrease.circle"
                          : "line.3.horizontal.decrease.circle.fill")
                        .accessibilityLabel("Filter by category")
                }
            }
        }
    }

    private func installed(_ response: AppsResponse) -> [CatalogApp] {
        filtered(response).filter { response.installed[$0.id] != nil }
    }

    private func available(_ response: AppsResponse) -> [CatalogApp] {
        filtered(response).filter { response.installed[$0.id] == nil }
    }

    private func filtered(_ response: AppsResponse) -> [CatalogApp] {
        response.catalog.filter { entry in
            let matchesCategory = category == "All" || entry.category == category
            guard matchesCategory else { return false }
            guard !search.isEmpty else { return true }
            let needle = search.lowercased()
            return entry.name.lowercased().contains(needle)
                || entry.tagline.lowercased().contains(needle)
                || entry.category.lowercased().contains(needle)
        }
    }
}

struct AppRow: View {
    let entry: CatalogApp
    let installed: InstalledApp?

    var body: some View {
        HStack(spacing: 14) {
            ServiceIcon(names: [entry.name, entry.id], category: entry.category, size: 44)

            VStack(alignment: .leading, spacing: 2) {
                Text(entry.name)
                    .foregroundStyle(Theme.text)
                Text(entry.tagline)
                    .font(.footnote)
                    .foregroundStyle(Theme.muted)
                    .lineLimit(2)
            }

            Spacer(minLength: 8)

            if let installed {
                StatusDot(text: installed.running ? "Running" : "Stopped",
                          tint: installed.running ? .green : Color(uiColor: .systemGray3))
            } else {
                // The App Store's "Get": the row opens the sheet that installs.
                Text("Install")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.accent)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 5)
                    .background(Theme.bg3, in: Capsule())
            }
        }
        .padding(.vertical, 2)
    }
}

/// Install/uninstall plus the catalog's own explanation of why you might want
/// the thing — the part that makes this readable by someone who is not a
/// sysadmin.
struct AppDetailSheet: View {
    let entry: CatalogApp
    let installed: InstalledApp?
    let onChange: (String) -> Void

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL

    @State private var values: [String: String] = [:]
    @State private var working = false
    @State private var output = ""
    @State private var failure: String?
    @State private var confirmUninstall = false
    /// `installed` is captured when the sheet opens, so after a successful
    /// install it still says "not installed" and the form is still on screen —
    /// which reads as the install having done nothing.
    @State private var finished: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    header

                    if !entry.why.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            SectionCaption(text: "Why you might want this")
                            Text(entry.why)
                                .font(.subheadline)
                                .foregroundStyle(Theme.text)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .card()
                    }

                    if let finished {
                        doneCard(finished)
                    } else if let installed {
                        installedCard(installed)
                    } else {
                        installForm
                    }

                    if !output.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            SectionCaption(text: "Output")
                            LogConsole(lines: output.components(separatedBy: .newlines),
                                       height: 200, follow: false)
                        }
                    }

                    if let failure {
                        Text(failure)
                            .font(.footnote)
                            .foregroundStyle(Theme.danger)
                    }

                    links
                }
                .padding(16)
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle(entry.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .onAppear {
                // Seed every field with the catalog's default so a one-tap
                // install really is one tap.
                for field in entry.fields where values[field.key] == nil {
                    values[field.key] = field.defaultValue
                }
            }
            .confirmationDialog("Remove \(entry.name)?",
                                isPresented: $confirmUninstall, titleVisibility: .visible) {
                Button("Remove, keep data", role: .destructive) {
                    Task { await uninstall(removeData: false) }
                }
                Button("Remove and delete data", role: .destructive) {
                    Task { await uninstall(removeData: true) }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Containers are stopped and removed. Keeping the data means the volumes stay on disk and a later reinstall picks up where this left off.")
            }
        }
    }

    private var header: some View {
        HStack(spacing: 16) {
            ServiceIcon(names: [entry.name, entry.id], category: entry.category, size: 72)
            VStack(alignment: .leading, spacing: 4) {
                Text(entry.name)
                    .font(.title2.weight(.bold))
                    .foregroundStyle(Theme.text)
                Text(entry.tagline)
                    .font(.subheadline)
                    .foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
                Text(entry.category)
                    .font(.footnote.weight(.medium))
                    .foregroundStyle(Theme.accent)
            }
            Spacer(minLength: 0)
        }
    }

    @ViewBuilder
    private var installForm: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !entry.description.isEmpty {
                Text(entry.description)
                    .font(.subheadline)
                    .foregroundStyle(Theme.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }

            ForEach(entry.fields) { field in
                VStack(alignment: .leading, spacing: 6) {
                    Text(field.label)
                        .font(.footnote.weight(.medium))
                        .foregroundStyle(Theme.muted)
                    FieldBox {
                        TextField(field.defaultValue,
                                  text: Binding(
                                    get: { values[field.key] ?? field.defaultValue },
                                    set: { values[field.key] = $0 }))
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                    }
                }
            }

            Button {
                Task { await install() }
            } label: {
                if working {
                    ProgressView().tint(Theme.onAccent)
                } else {
                    Label("Install", systemImage: "arrow.down.circle.fill")
                }
            }
            .buttonStyle(PrimaryButtonStyle(enabled: !working))
            .disabled(working)

            Text("The server writes a compose file and starts it. Ports are published on localhost — put a reverse proxy in front before exposing anything.")
                .font(.caption)
                .foregroundStyle(Theme.muted)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    private func doneCard(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(message, systemImage: "checkmark.circle.fill")
                .font(.subheadline.weight(.medium))
                .foregroundStyle(Theme.accent2)
            Text("The output below is what the server did. Close this to see it in the list.")
                .font(.caption)
                .foregroundStyle(Theme.muted)
            Button("Close") { dismiss() }
                .buttonStyle(SecondaryButtonStyle(tint: Theme.accent))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    private func installedCard(_ installed: InstalledApp) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            FactsCard {
                FactRow(label: "Status",
                        value: installed.running ? "Running" : "Stopped",
                        tint: installed.running ? .green : Theme.muted)
                if !installed.containers.isEmpty {
                    HairlineDivider()
                    FactRow(label: "Containers",
                            value: installed.containers.joined(separator: ", "))
                }
                if !installed.ports.isEmpty {
                    HairlineDivider()
                    FactRow(label: "Ports",
                            value: installed.ports.map(String.init).joined(separator: ", "))
                }
                HairlineDivider()
                FactRow(label: "Managed by",
                        value: installed.managed ? "PocketADM" : "you (external)")
            }

            if installed.managed {
                Button(role: .destructive) {
                    confirmUninstall = true
                } label: {
                    if working {
                        ProgressView().tint(Theme.danger)
                    } else {
                        Text("Remove")
                    }
                }
                .buttonStyle(SecondaryButtonStyle(tint: Theme.danger))
                .disabled(working)
            } else {
                // Removing something this app did not create would delete a
                // compose file it has never seen. The server refuses, and
                // offering the button anyway would just produce an error.
                Text("This was already running before PocketADM saw it, so it is managed outside the app. Remove it where you defined it.")
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
            }
        }
    }

    @ViewBuilder
    private var links: some View {
        HStack(spacing: 10) {
            if !entry.website.isEmpty, let url = URL(string: entry.website) {
                Button { openURL(url) } label: { Label("Website", systemImage: "safari") }
                    .buttonStyle(.bordered)
                    .buttonBorderShape(.capsule)
            }
            if !entry.docs.isEmpty, let url = URL(string: entry.docs) {
                Button { openURL(url) } label: { Label("Documentation", systemImage: "book") }
                    .buttonStyle(.bordered)
                    .buttonBorderShape(.capsule)
            }
            Spacer()
        }
        .font(.subheadline)
    }

    private func install() async {
        guard let client = app.client else { return }
        working = true
        failure = nil
        defer { working = false }
        do {
            output = try await client.installApp(entry.id, values: values)
            finished = "\(entry.name) installed"
            onChange("\(entry.name) installed")
        } catch {
            failure = error.localizedDescription
            app.handle(error)
        }
    }

    private func uninstall(removeData: Bool) async {
        guard let client = app.client else { return }
        working = true
        failure = nil
        defer { working = false }
        do {
            output = try await client.uninstallApp(entry.id, removeData: removeData)
            finished = "\(entry.name) removed"
            onChange("\(entry.name) removed")
        } catch {
            failure = error.localizedDescription
            app.handle(error)
        }
    }
}
