import SwiftUI

/// A compose stack and its containers.
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
    @Published var error: String?
    @Published var loaded = false
    /// Container ids with an action in flight, so a row can show a spinner and
    /// refuse a second tap without freezing the whole list.
    @Published var busy: Set<String> = []

    var stacks: [Stack] {
        Dictionary(grouping: containers, by: \.stack)
            .map { Stack(name: $0.key, containers: $0.value.sorted { $0.displayName < $1.displayName }) }
            .sorted { $0.name.lowercased() < $1.name.lowercased() }
    }

    func load(_ app: AppState) async {
        guard let client = app.client else { return }
        do {
            containers = try await client.containers()
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
}

struct ContainersView: View {
    @EnvironmentObject private var app: AppState
    @StateObject private var model = ContainersModel()
    @State private var search = ""
    @State private var path: [Container] = []

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
                        retry: { Task { await model.load(app) } }
                    )
                } else {
                    list
                }
            }
            .navigationTitle("Containers")
            .navigationBarTitleDisplayMode(.large)
            .navigationDestination(for: Container.self) { container in
                ContainerDetailView(container: container) { action in
                    await model.perform(action, on: container, app: app)
                }
            }
        }
        .task {
            await model.load(app)
            // Screenshot runs: `-PocketADMScreenshotRoute detail` opens the
            // first running container.
            if AppState.screenshotTab == MainTab.containers.rawValue, AppState.screenshotRoute == "detail",
               path.isEmpty, let first = model.containers.first(where: \.isRunning) {
                path = [first]
            }
        }
    }

    private var filtered: [Stack] {
        guard !search.isEmpty else { return model.stacks }
        let needle = search.lowercased()
        return model.stacks.compactMap { stack in
            let hits = stack.containers.filter {
                $0.name.lowercased().contains(needle)
                    || $0.displayName.lowercased().contains(needle)
                    || $0.image.lowercased().contains(needle)
            }
            return hits.isEmpty ? nil : Stack(name: stack.name, containers: hits)
        }
    }

    private var list: some View {
        List {
            ForEach(filtered) { stack in
                Section(stack.name) {
                    ForEach(stack.containers) { container in
                        NavigationLink(value: container) {
                            ContainerRow(container: container, busy: model.busy.contains(container.id))
                        }
                        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                            actions(for: container)
                        }
                        .contextMenu {
                            actions(for: container)
                        }
                    }
                }
            }
        }
        .searchable(text: $search, prompt: "Search containers")
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

struct ContainerRow: View {
    let container: Container
    var busy: Bool

    var body: some View {
        HStack(spacing: 14) {
            ServiceIcon(names: [container.service?.label ?? "", container.name, container.image],
                        category: container.service?.category ?? "")

            VStack(alignment: .leading, spacing: 2) {
                Text(container.displayName)
                    .foregroundStyle(Theme.text)
                    .lineLimit(1)
                Text(container.status)
                    .font(.footnote)
                    .foregroundStyle(Theme.muted)
                    .lineLimit(1)
            }

            Spacer(minLength: 8)

            if busy {
                ProgressView()
            } else {
                StatusDot(text: stateLabel, tint: stateTint)
            }
        }
        .padding(.vertical, 2)
    }

    private var stateLabel: String {
        if container.state == "running", container.health == "unhealthy" { return "Unhealthy" }
        return container.state.prefix(1).uppercased() + container.state.dropFirst()
    }

    private var stateTint: Color {
        switch container.state {
        case "running":              return container.health == "unhealthy" ? .orange : .green
        case "restarting", "paused": return .orange
        case "dead":                 return .red
        default:                     return Color(uiColor: .systemGray3)
        }
    }
}
