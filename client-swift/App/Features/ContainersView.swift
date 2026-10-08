import SwiftUI

/// A compose stack and its containers — the grouping an older server gets.
///
/// Deliberately a struct rather than the tuple this started as: `ForEach` needs
/// an identity, and Swift key paths cannot address tuple elements — `\.name` on
/// a `(name:containers:)` tuple does not compile.
struct Stack: Identifiable {
    let name: String
    let containers: [Container]
    var id: String { name }
}

@MainActor
final class ContainersModel: ObservableObject {
    @Published var containers: [Container] = []
    /// The containers as apps (server 0.24+ groups them; older servers by stack).
    @Published var groups: [AppGroup] = []
    @Published var error: String?
    @Published var loaded = false
    /// Container ids (and "app:<id>") with an action in flight, so a row can
    /// show a spinner and refuse a second tap without freezing the whole list.
    @Published var busy: Set<String> = []

    func load(_ app: AppState) async {
        guard let client = app.client else { return }
        if app.me == nil { await app.refreshMe() }
        do {
            if app.supports("services") {
                groups = try await client.services()
                containers = groups.flatMap(\.containers)
                    .sorted { $0.displayName.localizedStandardCompare($1.displayName) == .orderedAscending }
            } else {
                containers = try await client.containers()
                groups = Dictionary(grouping: containers, by: \.stack)
                    .map { AppGroup(fallbackFrom: $0.value, name: $0.key) }
                    .sorted { $0.name.lowercased() < $1.name.lowercased() }
            }
            error = nil
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
        loaded = true
    }

    func perform(_ action: ContainerAction, on container: Container, app: AppState) async {
        guard let client = app.client, !busy.contains(container.id) else { return }
        busy.insert(container.id)
        defer { busy.remove(container.id) }
        do {
            try await client.containerAction(container.id, action)
            // Docker reports the new state a beat after the call returns;
            // reloading immediately would show the old one.
            try? await Task.sleep(for: .milliseconds(600))
            await load(app)
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
    }

    /// Start, stop or restart a whole app. Returns a line for a toast.
    func perform(_ action: ContainerAction, onApp group: AppGroup, app: AppState) async -> String {
        guard let client = app.client, !busy.contains("app:" + group.id) else { return "" }
        busy.insert("app:" + group.id)
        defer { busy.remove("app:" + group.id) }
        do {
            if app.supports("services") {
                let result = try await client.groupAction(group.id, action)
                try? await Task.sleep(for: .milliseconds(600))
                await load(app)
                if !result.failed.isEmpty {
                    return "Some did not \(action.rawValue): \(result.failed.joined(separator: ", "))"
                }
                return result.done.isEmpty ? "Nothing to \(action.rawValue)" : "\(action.pastTense) \(result.done.count)"
            }
            for container in group.containers {
                try await client.containerAction(container.id, action)
            }
            await load(app)
            return "\(action.pastTense) \(group.containers.count)"
        } catch {
            app.handle(error)
            return error.localizedDescription
        }
    }
}

extension ContainerAction {
    var pastTense: String {
        switch self {
        case .start:   return "Started"
        case .stop:    return "Stopped"
        case .restart: return "Restarted"
        }
    }
}

enum ContainerFilter: String, CaseIterable, Identifiable {
    case all, problems, running, stopped
    var id: String { rawValue }

    var title: String {
        switch self {
        case .all:      return "All"
        case .problems: return "Problems"
        case .running:  return "Running"
        case .stopped:  return "Stopped"
        }
    }

    func matches(_ c: Container) -> Bool {
        switch self {
        case .all:      return true
        case .problems: return c.hasProblem
        case .running:  return c.isRunning
        case .stopped:  return !c.isRunning
        }
    }

    func matches(_ g: AppGroup) -> Bool {
        switch self {
        case .all:      return true
        case .problems: return g.hasProblem
        case .running:  return g.isRunning
        case .stopped:  return g.state == "stopped" || g.state == "partial"
        }
    }
}

struct ContainersView: View {
    @EnvironmentObject private var app: AppState
    @StateObject private var model = ContainersModel()
    @AppStorage("pocketadm.containers.view") private var viewMode = "apps"
    @AppStorage("pocketadm.containers.byCategory") private var byCategory = false
    @State private var filter: ContainerFilter = .all
    @State private var search = ""
    @State private var path = NavigationPath()
    @State private var toast: Toast?

    var body: some View {
        NavigationStack(path: $path) {
            Group {
                if !model.loaded {
                    ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if model.containers.isEmpty {
                    MessageState(
                        symbol: model.error == nil ? "shippingbox" : "exclamationmark.triangle",
                        title: model.error == nil ? "No containers" : "Cannot list containers",
                        message: model.error ?? "Nothing is running in Docker on this server yet.",
                        tint: model.error == nil ? Theme.muted : Theme.danger,
                        retry: retryIfFailed
                    )
                } else if viewMode == "apps" {
                    appsView
                } else {
                    containersList
                }
            }
            .navigationTitle("Containers")
            .navigationBarTitleDisplayMode(.large)
            .searchable(text: $search, prompt: viewMode == "apps" ? "Search apps" : "Search containers")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Picker("Show", selection: $viewMode) {
                            Label("Apps", systemImage: "square.grid.2x2").tag("apps")
                            Label("Containers", systemImage: "list.bullet").tag("containers")
                        }
                        Toggle(isOn: $byCategory) {
                            Label(viewMode == "apps" ? "Group by category" : "Group by app",
                                  systemImage: "rectangle.3.group")
                        }
                    } label: {
                        Image(systemName: "line.3.horizontal.decrease.circle")
                    }
                }
            }
            .navigationDestination(for: AppGroup.self) { group in
                AppGroupView(groupID: group.id, initial: group, model: model)
            }
            .navigationDestination(for: Container.self) { container in
                ContainerDetailView(container: container) { action in
                    await model.perform(action, on: container, app: app)
                }
            }
            .toast($toast)
        }
        .task {
            await model.load(app)
            // Screenshot runs: `-PocketADMScreenshotRoute detail` opens the
            // first running container.
            if AppState.screenshotTab == MainTab.containers.rawValue, AppState.screenshotRoute == "detail",
               path.isEmpty, let first = model.containers.first(where: \.isRunning) {
                path.append(first)
            }
        }
    }

    /// Offered only when listing failed — no containers needs no retry.
    private var retryIfFailed: (() -> Void)? {
        guard model.error != nil else { return nil }
        return { Task { await model.load(app) } }
    }

    // MARK: - Controls

    private var controls: some View {
        VStack(spacing: 12) {
            Picker("Show", selection: $viewMode) {
                Text("Apps").tag("apps")
                Text("Containers").tag("containers")
            }
            .pickerStyle(.segmented)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(ContainerFilter.allCases) { option in
                        FilterChip(title: option.title, count: count(option),
                                   tint: option == .problems && count(option) > 0 ? Theme.warn : Theme.accent,
                                   selected: filter == option) {
                            withAnimation(.snappy) { filter = option }
                        }
                    }
                }
            }
        }
    }

    private func count(_ option: ContainerFilter) -> Int {
        viewMode == "apps"
            ? model.groups.filter { option.matches($0) }.count
            : model.containers.filter { option.matches($0) }.count
    }

    // MARK: - Apps

    private var filteredGroups: [AppGroup] {
        let needle = search.lowercased()
        return model.groups.filter { group in
            guard filter.matches(group) else { return false }
            guard !needle.isEmpty else { return true }
            return group.name.lowercased().contains(needle)
                || group.category.lowercased().contains(needle)
                || group.containers.contains { $0.name.lowercased().contains(needle)
                    || $0.image.lowercased().contains(needle) }
        }
    }

    private var appSections: [AppSection] {
        let groups = filteredGroups
        let byName: (AppGroup, AppGroup) -> Bool = {
            $0.name.localizedStandardCompare($1.name) == .orderedAscending
        }
        guard byCategory else {
            let problems = groups.filter(\.hasProblem)
            let rest = groups.filter { !$0.hasProblem }.sorted(by: byName)
            return (problems.isEmpty ? [] : [AppSection(title: "Needs a look", groups: problems)])
                + [AppSection(title: "", groups: rest)]
        }
        let buckets = Dictionary(grouping: groups) { $0.category.isEmpty ? "Other" : $0.category }
        return buckets.keys.sorted().map { key in
            AppSection(title: key, groups: (buckets[key] ?? []).sorted(by: byName))
        }
    }

    private var appsView: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                controls
                if filteredGroups.isEmpty {
                    Text("Nothing matches.")
                        .font(.subheadline)
                        .foregroundStyle(Theme.muted)
                        .frame(maxWidth: .infinity)
                        .padding(.top, 30)
                }
                ForEach(appSections) { section in
                    if !section.groups.isEmpty {
                        VStack(alignment: .leading, spacing: 10) {
                            if !section.title.isEmpty {
                                SectionCaption(text: section.title)
                                    .padding(.leading, 4)
                            }
                            LazyVGrid(columns: [GridItem(.adaptive(minimum: 158), spacing: 12)],
                                      spacing: 12) {
                                ForEach(section.groups) { group in
                                    NavigationLink(value: group) {
                                        AppCard(group: group, busy: model.busy.contains("app:" + group.id))
                                    }
                                    .buttonStyle(.plain)
                                    .contextMenu { appActions(group) }
                                }
                            }
                        }
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
        }
        .background(Theme.bg.ignoresSafeArea())
        .refreshable { await model.load(app) }
    }

    @ViewBuilder
    private func appActions(_ group: AppGroup) -> some View {
        if group.isRunning {
            Button {
                Task { toast = Toast(text: await model.perform(.restart, onApp: group, app: app)) }
            } label: { Label("Restart app", systemImage: "arrow.clockwise") }
            Button(role: .destructive) {
                Task { toast = Toast(text: await model.perform(.stop, onApp: group, app: app)) }
            } label: { Label("Stop app", systemImage: "stop.fill") }
        }
        if group.state != "running" {
            Button {
                Task { toast = Toast(text: await model.perform(.start, onApp: group, app: app)) }
            } label: { Label("Start app", systemImage: "play.fill") }
        }
        Button {
            app.ask(AppGroupView.assistantPrompt(group))
        } label: { Label("Ask the assistant", systemImage: "sparkles") }
    }

    // MARK: - Containers

    private var filteredContainers: [Container] {
        let needle = search.lowercased()
        return model.containers.filter { c in
            guard filter.matches(c) else { return false }
            guard !needle.isEmpty else { return true }
            return c.name.lowercased().contains(needle)
                || c.displayName.lowercased().contains(needle)
                || c.image.lowercased().contains(needle)
                || (c.groupName ?? "").lowercased().contains(needle)
        }
    }

    private var containerSections: [Stack] {
        let items = filteredContainers
        guard byCategory else { return [Stack(name: "", containers: items)] }
        let buckets = Dictionary(grouping: items) { $0.groupName ?? $0.stack }
        return buckets.keys.sorted { $0.lowercased() < $1.lowercased() }
            .map { Stack(name: $0, containers: buckets[$0] ?? []) }
    }

    private var containersList: some View {
        List {
            Section {
                EmptyView()
            } header: {
                controls
                    .textCase(nil)
                    .padding(.bottom, 4)
            }

            ForEach(containerSections) { stack in
                Section {
                    ForEach(stack.containers) { container in
                        NavigationLink(value: container) {
                            ContainerRow(container: container, busy: model.busy.contains(container.id),
                                         showApp: !byCategory)
                        }
                        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                            actions(for: container)
                        }
                        .contextMenu {
                            actions(for: container)
                        }
                    }
                } header: {
                    if !stack.name.isEmpty { Text(stack.name) }
                }
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await model.load(app) }
        .animation(.default, value: model.containers)
    }

    @ViewBuilder
    private func actions(for container: Container) -> some View {
        if container.isRunning {
            Button {
                Task { await model.perform(.restart, on: container, app: app) }
            } label: { Label("Restart", systemImage: "arrow.clockwise") }
                .tint(.orange)

            Button(role: .destructive) {
                Task { await model.perform(.stop, on: container, app: app) }
            } label: { Label("Stop", systemImage: "stop.fill") }
                .tint(.red)
        } else {
            Button {
                Task { await model.perform(.start, on: container, app: app) }
            } label: { Label("Start", systemImage: "play.fill") }
                .tint(.green)
        }
    }
}

/// A titled run of app tiles.
struct AppSection: Identifiable {
    let title: String
    let groups: [AppGroup]
    var id: String { title.isEmpty ? "_all" : title }
}

/// A rounded filter button with a count, like Mail's filters.
struct FilterChip: View {
    let title: String
    var count: Int? = nil
    var tint: Color = Theme.accent
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Text(title)
                if let count {
                    Text("\(count)")
                        .monospacedDigit()
                        .foregroundStyle(selected ? Theme.onAccent.opacity(0.85) : Theme.muted)
                }
            }
            .font(.subheadline.weight(.medium))
            .padding(.horizontal, 12)
            .padding(.vertical, 7)
            .foregroundStyle(selected ? Theme.onAccent : Theme.text)
            .background(selected ? tint : Theme.bg2, in: Capsule())
        }
        .buttonStyle(.plain)
    }
}

/// One app as a tile: its icon, its name, how many of its containers run.
struct AppCard: View {
    let group: AppGroup
    var busy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                ServiceIcon(names: group.iconNames, category: group.category, size: 42)
                Spacer(minLength: 4)
                if busy {
                    ProgressView().controlSize(.small)
                } else {
                    Circle().fill(AppCard.tint(for: group.state)).frame(width: 10, height: 10)
                        .padding(.top, 4)
                }
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(group.name)
                    .font(.headline)
                    .foregroundStyle(Theme.text)
                    .lineLimit(1)
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(Theme.muted)
                    .lineLimit(1)
            }
            if group.total > 1 {
                HStack(spacing: 4) {
                    ForEach(group.containers) { c in
                        Capsule()
                            .fill(ContainerRow.tint(for: c))
                            .frame(height: 5)
                    }
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, minHeight: 128, alignment: .topLeading)
        .background(Theme.bg2, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .overlay {
            if group.hasProblem {
                RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
                    .strokeBorder(AppCard.tint(for: group.state).opacity(0.6), lineWidth: 1.5)
            }
        }
        .contentShape(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .accessibilityElement(children: .combine)
    }

    private var subtitle: String {
        switch group.state {
        case "running":
            if group.total == 1 { return group.category.isEmpty ? "Running" : group.category }
            return "\(group.total) containers"
        case "stopped":    return "Stopped"
        case "unhealthy":  return "\(group.unhealthy) unhealthy"
        case "restarting": return "Restarting"
        default:           return "\(group.running) of \(group.total) running"
        }
    }

    static func tint(for state: String) -> Color {
        switch state {
        case "running":               return .green
        case "partial", "unhealthy":  return .orange
        case "restarting":            return .red
        default:                      return Color(uiColor: .systemGray3)
        }
    }
}

struct ContainerRow: View {
    let container: Container
    var busy: Bool
    /// Name the app the container belongs to (in a flat list).
    var showApp = true

    var body: some View {
        HStack(spacing: 14) {
            ServiceIcon(names: [container.service?.label ?? "", container.name, container.image],
                        category: container.service?.category ?? "")

            VStack(alignment: .leading, spacing: 2) {
                Text(container.displayName)
                    .foregroundStyle(Theme.text)
                    .lineLimit(1)
                Text(subtitle)
                    .font(.footnote)
                    .foregroundStyle(Theme.muted)
                    .lineLimit(1)
            }

            Spacer(minLength: 8)

            if busy {
                ProgressView()
            } else {
                StatusDot(text: stateLabel, tint: ContainerRow.tint(for: container))
            }
        }
        .padding(.vertical, 2)
    }

    private var subtitle: String {
        var parts: [String] = []
        if showApp, let group = container.groupName, group != container.displayName,
           !container.displayName.hasPrefix(group) {
            parts.append(group)
        }
        parts.append(container.status)
        return parts.joined(separator: " · ")
    }

    private var stateLabel: String {
        if container.state == "running", container.health == "unhealthy" { return "Unhealthy" }
        return container.state.prefix(1).uppercased() + container.state.dropFirst()
    }

    static func tint(for container: Container) -> Color {
        switch container.state {
        case "running":              return container.health == "unhealthy" ? .orange : .green
        case "restarting", "paused": return .orange
        case "dead":                 return .red
        default:                     return Color(uiColor: .systemGray3)
        }
    }
}

// MARK: - One app

/// An app and everything it is made of: its containers with their roles, the
/// addresses it answers on, pending updates, and actions for all of it at once.
struct AppGroupView: View {
    let groupID: String
    let initial: AppGroup
    @ObservedObject var model: ContainersModel

    @EnvironmentObject private var app: AppState
    @State private var updates: [DockerUpdate] = []
    @State private var updateSheet: DockerUpdate?
    @State private var job: PendingJob?
    @State private var confirming: ContainerAction?
    @State private var toast: Toast?

    /// The live group from the model, falling back to the one tapped.
    private var group: AppGroup { model.groups.first { $0.id == groupID } ?? initial }

    var body: some View {
        List {
            Section { header }
                .listRowBackground(Color.clear)

            if !group.reachablePorts.isEmpty, let host = app.serverURL?.host {
                Section("Open") {
                    ForEach(group.reachablePorts, id: \.self) { port in
                        if let url = URL(string: "http://\(host):\(port)") {
                            Link(destination: url) {
                                Label("\(host):\(port)", systemImage: "safari")
                            }
                        }
                    }
                }
            }

            Section(group.total == 1 ? "Container" : "\(group.total) containers") {
                ForEach(group.containers) { container in
                    NavigationLink(value: container) {
                        HStack(spacing: 14) {
                            ServiceIcon(names: [container.service?.label ?? "", container.name, container.image],
                                        category: container.service?.category ?? "", size: 32)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(roleTitle(container))
                                    .foregroundStyle(Theme.text)
                                Text("\(container.name) · \(container.status)")
                                    .font(.footnote)
                                    .foregroundStyle(Theme.muted)
                                    .lineLimit(1)
                            }
                            Spacer(minLength: 6)
                            Circle().fill(ContainerRow.tint(for: container)).frame(width: 8, height: 8)
                        }
                    }
                }
            }

            if !updates.isEmpty {
                Section("Updates") {
                    ForEach(updates) { update in
                        Button { updateSheet = update } label: { UpdateRow(update: update) }
                    }
                }
            }

            if !group.composeProject.isEmpty || !group.composeDir.isEmpty {
                Section("Defined in") {
                    if !group.composeProject.isEmpty {
                        FactRow(label: "Compose project", value: group.composeProject)
                    }
                    if !group.composeDir.isEmpty {
                        NavigationLink {
                            FolderView(path: group.composeDir)
                        } label: {
                            FactRow(label: "Folder", value: group.composeDir)
                        }
                    }
                }
            }

            Section {
                Button {
                    app.ask(Self.assistantPrompt(group))
                } label: {
                    Label("Ask the assistant about \(group.name)", systemImage: "sparkles")
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle(group.name)
        .navigationBarTitleDisplayMode(.inline)
        .refreshable {
            await model.load(app)
            await loadUpdates()
        }
        .task { await loadUpdates() }
        .toast($toast)
        .sheet(item: $updateSheet) { update in
            UpdateDetailSheet(update: update) { image in
                await apply(image: image, title: update.displayName)
            } onIgnore: { image, ignored in
                try? await app.client?.setUpdateIgnored(image: image, ignored: ignored)
                updateSheet = nil
                await loadUpdates()
            }
        }
        .sheet(item: $job) { pending in
            JobConsoleView(jobID: pending.id, title: pending.title) { _ in
                Task {
                    await model.load(app)
                    await loadUpdates()
                }
            }
        }
        .confirmationDialog(confirming.map { "\($0.label) all of \(group.name)?" } ?? "",
                            isPresented: Binding(get: { confirming != nil },
                                                 set: { if !$0 { confirming = nil } }),
                            titleVisibility: .visible) {
            if let action = confirming {
                Button("\(action.label) \(group.total) containers", role: .destructive) {
                    Task { toast = Toast(text: await model.perform(action, onApp: group, app: app)) }
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(confirming == .stop
                 ? "Everything that uses \(group.name) stops working until you start it again."
                 : "Databases and caches go first on the way up and last on the way down.")
        }
    }

    private func roleTitle(_ container: Container) -> String {
        guard let role = container.role, role != "App" else { return container.displayName }
        return role
    }

    private var header: some View {
        VStack(spacing: 10) {
            ServiceIcon(names: group.iconNames, category: group.category, size: 68)
            VStack(spacing: 4) {
                Text(group.name)
                    .font(.title2.weight(.bold))
                    .foregroundStyle(Theme.text)
                StatusDot(text: stateText, tint: AppCard.tint(for: group.state))
                if !group.category.isEmpty {
                    Text(group.category)
                        .font(.footnote)
                        .foregroundStyle(Theme.muted)
                }
            }
            HStack(spacing: 10) {
                if group.isRunning {
                    actionButton(.restart, tint: Theme.accent)
                    actionButton(.stop, tint: .red)
                }
                if group.state != "running" {
                    actionButton(.start, tint: .green)
                }
            }
            .padding(.top, 6)
        }
        .frame(maxWidth: .infinity)
    }

    private func actionButton(_ action: ContainerAction, tint: Color) -> some View {
        Button {
            if action == .start {
                Task { toast = Toast(text: await model.perform(action, onApp: group, app: app)) }
            } else {
                confirming = action
            }
        } label: {
            Label(action.label, systemImage: action.symbol)
                .font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.bordered)
        .buttonBorderShape(.capsule)
        .controlSize(.large)
        .tint(tint)
        .disabled(model.busy.contains("app:" + group.id))
    }

    private var stateText: String {
        switch group.state {
        case "running":    return group.total == 1 ? "Running" : "All \(group.total) running"
        case "stopped":    return "Stopped"
        case "unhealthy":  return "\(group.unhealthy) unhealthy"
        case "restarting": return "Restarting"
        default:           return "\(group.running) of \(group.total) running"
        }
    }

    static func assistantPrompt(_ group: AppGroup) -> String {
        let names = group.containers.map(\.name).joined(separator: ", ")
        return "Check the \(group.name) app on this server (containers: \(names)). "
            + "Is everything healthy? Look at their recent logs for errors and tell me what you find."
    }

    private func loadUpdates() async {
        guard let client = app.client, let response = try? await client.updates() else { return }
        let names = Set(group.containers.map(\.name))
        updates = response.pending.filter { !Set($0.usedBy).isDisjoint(with: names) }
    }

    private func apply(image: String, title: String) async {
        guard let client = app.client else { return }
        do {
            let id = try await client.applyUpdate(image: image)
            updateSheet = nil
            job = PendingJob(id: id, title: title)
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }
}
