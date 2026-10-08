import SafariServices
import SwiftUI
import UIKit

/// Every AI the server can use, in one place: connect a subscription you
/// already pay for (Claude, ChatGPT, Mistral) with a sign-in on this phone, or
/// paste an API key — and choose which one the assistant, the watch and the
/// explanations run on.
struct AIAccountsView: View {
    @EnvironmentObject private var app: AppState

    @State private var accounts: AIAccounts?
    @State private var usage: AIUsage?
    @State private var loaded = false
    @State private var error: String?
    @State private var signIn: AIAccounts.Account?

    var body: some View {
        ThemedList {
            if let accounts {
                Section {
                    ForEach(accounts.accounts) { account in
                        NavigationLink {
                            AccountDetailView(accountID: account.id, initial: account) {
                                await load()
                            }
                        } label: {
                            AccountRow(account: account)
                        }
                    }
                    NavigationLink {
                        LocalAIView()
                    } label: {
                        HStack(spacing: 14) {
                            IconTile(symbol: "cpu", color: .teal, size: 36)
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Local models").foregroundStyle(Theme.text)
                                Text(accounts.local.running
                                     ? "\(accounts.local.models) on this server — no keys, no cost"
                                     : "Run a model on the server itself")
                                    .font(.footnote)
                                    .foregroundStyle(Theme.muted)
                            }
                        }
                    }
                } header: {
                    Text("Accounts")
                } footer: {
                    Text("Subscriptions connect through the provider's own coding agent on your server (Claude Code, Codex, Mistral Vibe). Keys and logins stay on your server.")
                }

                Section {
                    ForEach(["assistant", "watch", "insights"], id: \.self) { feature in
                        if let route = accounts.routes[feature] {
                            NavigationLink {
                                RoutePickerView(feature: feature, title: route.label)
                            } label: {
                                HStack {
                                    IconTile(symbol: Self.symbol(for: feature), color: Self.color(for: feature))
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(route.label).foregroundStyle(Theme.text)
                                        Text(Self.blurb(for: feature))
                                            .font(.caption)
                                            .foregroundStyle(Theme.muted)
                                    }
                                    Spacer(minLength: 8)
                                    Text(routeText(route, feature: feature))
                                        .font(.subheadline)
                                        .foregroundStyle(Theme.muted)
                                        .lineLimit(1)
                                }
                            }
                        }
                    }
                } header: {
                    Text("What runs on what")
                }
            } else if loaded {
                Section {
                    // A server older than 0.24 has no accounts endpoint: the
                    // key screen still works there.
                    NavigationLink {
                        AISettingsView()
                    } label: {
                        NavRow(symbol: "key.fill", title: "API keys and default model",
                               subtitle: error ?? "", tint: .purple)
                    }
                } footer: {
                    Text("Connecting subscriptions from the phone needs PocketADM 0.24 on the server.")
                }
            }

            Section {
                NavigationLink {
                    AgentSettingsView()
                } label: {
                    NavRow(symbol: "wrench.and.screwdriver.fill", title: "Assistant behaviour",
                           subtitle: "Memory, instructions and which tools it may use", tint: .indigo)
                }
                NavigationLink {
                    CLIsView()
                } label: {
                    NavRow(symbol: "chevron.left.forwardslash.chevron.right", title: "Coding agents",
                           subtitle: "Installed versions of Claude Code, Codex and Vibe", tint: .teal)
                }
            }

            if let usage {
                Section("Usage of API keys") {
                    FactRow(label: "Today",
                            value: "\(Fmt.money(usage.today.cost)) · \(usage.today.requests) requests")
                    FactRow(label: "This month",
                            value: "\(Fmt.money(usage.month.cost)) · \(usage.month.requests) requests")
                    FactRow(label: "Tokens this month",
                            value: "\(Fmt.count(usage.month.input)) in · \(Fmt.count(usage.month.output)) out")
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("AI accounts")
        .navigationBarTitleDisplayMode(.large)
        .overlay {
            if !loaded { ProgressView() }
        }
        .task { await load() }
        .refreshable { await load() }
    }

    private func routeText(_ route: AIAccounts.Route, feature: String) -> String {
        if feature != "assistant" && !route.custom { return "Like the assistant" }
        if route.providerLabel.isEmpty { return "Not chosen" }
        return route.providerLabel
    }

    static func symbol(for feature: String) -> String {
        switch feature {
        case "assistant": return "sparkles"
        case "watch":     return "eye.fill"
        default:          return "text.bubble.fill"
        }
    }

    static func color(for feature: String) -> Color {
        switch feature {
        case "assistant": return .purple
        case "watch":     return .indigo
        default:          return .blue
        }
    }

    static func blurb(for feature: String) -> String {
        switch feature {
        case "assistant": return "Chats in the Assistant tab"
        case "watch":     return "Background checks and alerts"
        default:          return "Explains containers, updates, health"
        }
    }

    private func load() async {
        guard let client = app.client else { return }
        defer { loaded = true }
        if app.me == nil { await app.refreshMe() }
        if app.supports("accounts") {
            do {
                accounts = try await client.aiAccounts()
                error = nil
            } catch {
                self.error = error.localizedDescription
            }
        }
        usage = try? await client.aiUsage()
        await app.refreshMe()
    }
}

struct AccountRow: View {
    let account: AIAccounts.Account

    var body: some View {
        HStack(spacing: 14) {
            ServiceIcon(names: [account.brand, account.name, account.vendor], category: "AI", size: 36)
            VStack(alignment: .leading, spacing: 2) {
                Text(account.name).foregroundStyle(Theme.text)
                Text(statusText)
                    .font(.footnote)
                    .foregroundStyle(account.connected ? Theme.muted : Color(uiColor: .tertiaryLabel))
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
            if account.connected {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
            }
        }
    }

    private var statusText: String {
        var parts: [String] = []
        if account.signedIn { parts.append(account.plan.isEmpty ? "Subscription" : "Subscription · \(account.plan.capitalized)") }
        if account.keySet { parts.append("API key") }
        if parts.isEmpty { return account.canSubscribe ? "Not connected — sign in or add a key" : "Not connected" }
        if !account.usedFor.isEmpty { parts.append("used for " + account.usedFor.joined(separator: ", ").lowercased()) }
        return parts.joined(separator: " · ")
    }
}

// MARK: - One account

struct AccountDetailView: View {
    let accountID: String
    let initial: AIAccounts.Account
    let onChange: () async -> Void

    @EnvironmentObject private var app: AppState
    @State private var account: AIAccounts.Account?
    @State private var key = ""
    @State private var saving = false
    @State private var signingIn = false
    @State private var confirmSignOut = false
    @State private var toast: Toast?

    private var current: AIAccounts.Account { account ?? initial }

    var body: some View {
        ThemedList {
            Section {
                VStack(spacing: 10) {
                    ServiceIcon(names: [current.brand, current.name, current.vendor], category: "AI", size: 64)
                    Text(current.name)
                        .font(.title2.weight(.bold))
                        .foregroundStyle(Theme.text)
                    Text(current.connected ? "Connected" : "Not connected")
                        .font(.subheadline)
                        .foregroundStyle(current.connected ? .green : Theme.muted)
                }
                .frame(maxWidth: .infinity)
            }
            .listRowBackground(Color.clear)

            if current.canSubscribe {
                Section {
                    if current.signedIn {
                        Label(current.detail.isEmpty ? "Signed in" : current.detail,
                              systemImage: "checkmark.seal.fill")
                            .foregroundStyle(Theme.text)
                        if !current.plan.isEmpty {
                            FactRow(label: "Plan", value: current.plan.capitalized)
                        }
                        if !current.cliVersion.isEmpty {
                            FactRow(label: engineLabel, value: current.cliVersion)
                        }
                        Button("Sign out", role: .destructive) { confirmSignOut = true }
                    } else {
                        Button {
                            signingIn = true
                        } label: {
                            Label("Connect your \(current.subscription)", systemImage: "person.crop.circle.badge.checkmark")
                                .font(.body.weight(.semibold))
                        }
                        .disabled(app.me?.demo == true)
                    }
                } header: {
                    Text("Subscription")
                } footer: {
                    Text(current.signedIn
                         ? "\(engineLabel) runs on your server with this login. Choose it in the assistant's model menu."
                         : "You sign in on \(current.vendor)'s own page. \(engineLabel) is installed on your server if it is not there yet.")
                }
            }

            Section {
                SecureField(current.keySet ? "Replace the key" : "Paste an API key", text: $key)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .font(.system(.body, design: .monospaced))
                if !key.isEmpty {
                    Button(saving ? "Saving…" : "Save key") { Task { await saveKey(key) } }
                        .disabled(saving)
                }
                if current.keySet && !current.keyFromEnv {
                    Button("Remove key", role: .destructive) { Task { await saveKey("-") } }
                }
            } header: {
                Text("API key")
            } footer: {
                if current.keyFromEnv {
                    Text("This key is set in the server's environment (\(current.keyProvider.uppercased())_API_KEY in the compose file), so it cannot be removed here.")
                } else {
                    Text(current.keyHint.isEmpty ? "Billed by the provider per use." : "\(current.keyHint). Billed by the provider per use.")
                }
            }

            if !current.usedFor.isEmpty {
                Section("Used for") {
                    ForEach(current.usedFor, id: \.self) { use in
                        Text(use)
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle(current.name)
        .navigationBarTitleDisplayMode(.inline)
        .toast($toast)
        .sheet(isPresented: $signingIn) {
            SignInSheet(engine: current.engine, name: current.name, subscription: current.subscription) {
                Task { await refresh() }
            }
        }
        .confirmationDialog("Sign \(engineLabel) out?", isPresented: $confirmSignOut, titleVisibility: .visible) {
            Button("Sign out", role: .destructive) { Task { await signOut() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Chats and the watch that use it stop working until you connect again.")
        }
    }

    private var engineLabel: String {
        switch current.engine {
        case "claude-code":  return "Claude Code"
        case "codex":        return "Codex"
        case "mistral-vibe": return "Mistral Vibe"
        default:             return current.engine
        }
    }

    private func refresh() async {
        await onChange()
        if let fresh = try? await app.client?.aiAccounts() {
            account = fresh.accounts.first { $0.id == accountID }
        }
        await app.refreshMe()
    }

    private func saveKey(_ value: String) async {
        guard let client = app.client else { return }
        saving = true
        defer { saving = false }
        do {
            try await client.saveAISettings(keys: [current.keyProvider: value])
            key = ""
            toast = Toast(text: value == "-" ? "Key removed" : "Key saved")
            await refresh()
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func signOut() async {
        guard let client = app.client else { return }
        do {
            let fresh = try await client.signOut(engine: current.engine)
            account = fresh.accounts.first { $0.id == accountID }
            await onChange()
            toast = Toast(text: "Signed out")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }
}

// MARK: - Sign-in

/// Connects a subscription: the server drives the provider's CLI, this sheet
/// shows the one step only a person can do — signing in on the provider's
/// page (and, for Claude, pasting back the code it shows).
struct SignInSheet: View {
    let engine: String
    let name: String
    let subscription: String
    let onDone: () -> Void

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss
    @State private var flow: SignInFlow?
    @State private var error: String?
    @State private var code = ""
    @State private var browser: URL?
    @State private var poller: Task<Void, Never>?
    @State private var submitting = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    ServiceIcon(names: [name], category: "AI", size: 64)
                        .padding(.top, 20)
                    Text("Connect \(name)")
                        .font(.title2.weight(.bold))
                    content
                }
                .padding(20)
                .frame(maxWidth: .infinity)
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(flow?.state == "done" ? "Done" : "Cancel") { close() }
                }
            }
            .sheet(item: Binding(get: { browser.map { BrowserLink(url: $0) } },
                                 set: { browser = $0?.url })) { link in
                SafariView(url: link.url)
                    .ignoresSafeArea()
            }
        }
        .interactiveDismissDisabled(flow.map { !$0.isOver } ?? false)
        .task { await start() }
        .onDisappear { poller?.cancel() }
    }

    @ViewBuilder
    private var content: some View {
        if let error {
            stateMessage(symbol: "exclamationmark.triangle.fill", tint: Theme.danger, text: error)
            Button("Try again") { Task { await start() } }
                .buttonStyle(PrimaryButtonStyle())
        } else if let flow {
            switch flow.state {
            case "done":
                stateMessage(symbol: "checkmark.circle.fill", tint: .green,
                             text: flow.message.isEmpty ? "\(name) is connected." : flow.message)
                Button("Use it for the assistant") { Task { await useForAssistant() } }
                    .buttonStyle(PrimaryButtonStyle())
                Button("Done") { close() }
                    .buttonStyle(SecondaryButtonStyle())
            case "failed", "cancelled":
                stateMessage(symbol: "xmark.octagon.fill", tint: Theme.danger,
                             text: flow.error.isEmpty ? "The sign-in did not finish." : flow.error)
                Button("Try again") { Task { await start() } }
                    .buttonStyle(PrimaryButtonStyle())
            case "waiting_code":
                if !flow.message.isEmpty && (flow.attempt > 1 || Self.isProblem(flow.message)) {
                    callout(flow.message, tint: flow.attempt > 1 ? Theme.warn : Theme.danger,
                            symbol: flow.attempt > 1 ? "arrow.clockwise.circle.fill" : "exclamationmark.triangle.fill")
                }
                steps([
                    flow.attempt > 1 ? "Open the new sign-in page and sign in with your \(subscription)."
                                     : "Open the sign-in page and sign in with your \(subscription).",
                    "Tap Copy Code on the page that follows — the whole code, it has a # in it.",
                    "Come back here and paste it.",
                ])
                openButton(flow)
                VStack(spacing: 10) {
                    TextField("Paste the code", text: $code)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.system(.body, design: .monospaced))
                        .padding(12)
                        .background(Theme.bg2, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    HStack {
                        PasteButton(payloadType: String.self) { strings in
                            if let first = strings.first { code = first.trimmingCharacters(in: .whitespacesAndNewlines) }
                        }
                        .buttonBorderShape(.capsule)
                        Spacer()
                        Button(submitting ? "Checking…" : "Connect") { Task { await submit() } }
                            .buttonStyle(.borderedProminent)
                            .buttonBorderShape(.capsule)
                            .disabled(code.isEmpty || submitting)
                    }
                }
                if engine == "claude-code" {
                    Text("Already ran `claude setup-token` on a computer? Paste that token (sk-ant-oat…) instead of a code.")
                        .font(.footnote)
                        .foregroundStyle(Theme.muted)
                        .multilineTextAlignment(.center)
                }
            case "waiting_browser":
                if !flow.userCode.isEmpty {
                    steps(["Open the sign-in page.", "Enter this code there:", "Sign in — this screen notices by itself."])
                    Text(flow.userCode)
                        .font(.system(size: 34, weight: .bold, design: .monospaced))
                        .textSelection(.enabled)
                        .padding(.vertical, 6)
                    Button {
                        UIPasteboard.general.string = flow.userCode
                    } label: { Label("Copy code", systemImage: "doc.on.doc") }
                        .buttonStyle(.bordered)
                        .buttonBorderShape(.capsule)
                } else {
                    steps(["Open the sign-in page.", "Sign in with your \(subscription).", "Come back — this screen notices by itself."])
                }
                openButton(flow)
                HStack(spacing: 8) {
                    ProgressView()
                    Text("Waiting for the sign-in…").font(.footnote).foregroundStyle(Theme.muted)
                }
            case "verifying":
                VStack(spacing: 12) {
                    ProgressView()
                    Text(flow.message.isEmpty ? "Checking the code with \(name)…" : flow.message)
                        .font(.subheadline)
                        .foregroundStyle(Theme.muted)
                        .multilineTextAlignment(.center)
                    Text("This can take up to a minute.")
                        .font(.footnote)
                        .foregroundStyle(Theme.muted)
                }
                .padding(.top, 20)
            default:
                VStack(spacing: 12) {
                    ProgressView()
                    Text(flow.message.isEmpty ? "Getting ready…" : flow.message)
                        .font(.subheadline)
                        .foregroundStyle(Theme.muted)
                        .multilineTextAlignment(.center)
                }
                .padding(.top, 20)
            }
        } else {
            ProgressView().padding(.top, 30)
        }
    }

    static func isProblem(_ message: String) -> Bool {
        let low = message.lowercased()
        return ["did not", "not accept", "only part", "older", "api key", "too long", "error", "failed"]
            .contains { low.contains($0) }
    }

    private func callout(_ text: String, tint: Color, symbol: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: symbol)
                .foregroundStyle(tint)
                .font(.title3)
            Text(text)
                .font(.subheadline)
                .foregroundStyle(Theme.text)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(14)
        .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
    }

    private func steps(_ lines: [String]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(lines.enumerated()), id: \.offset) { index, line in
                HStack(alignment: .top, spacing: 10) {
                    Text("\(index + 1)")
                        .font(.footnote.weight(.bold))
                        .foregroundStyle(Theme.onAccent)
                        .frame(width: 22, height: 22)
                        .background(Theme.accent, in: Circle())
                    Text(line)
                        .font(.subheadline)
                        .foregroundStyle(Theme.text)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
            }
        }
        .padding(14)
        .background(Theme.bg2, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
    }

    private func openButton(_ flow: SignInFlow) -> some View {
        Button {
            if let url = URL(string: flow.url) { browser = url }
        } label: {
            Label("Open the sign-in page", systemImage: "safari")
        }
        .buttonStyle(PrimaryButtonStyle())
        .disabled(URL(string: flow.url) == nil)
    }

    private func stateMessage(symbol: String, tint: Color, text: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 44))
                .foregroundStyle(tint)
            Text(text)
                .font(.subheadline)
                .foregroundStyle(Theme.text)
                .multilineTextAlignment(.center)
        }
        .padding(.vertical, 10)
    }

    // MARK: - Flow

    private func start() async {
        guard let client = app.client else { return }
        error = nil
        code = ""
        do {
            flow = try await client.startSignIn(engine: engine)
            poll()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func poll() {
        poller?.cancel()
        poller = Task {
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1.5))
                guard let client = app.client, let id = flow?.id,
                      let fresh = try? await client.signInFlow(id) else { continue }
                // a code being typed must not be cleared by a poll — unless the
                // page it came from is gone (Claude renewed it after a failure)
                if let old = flow, fresh.attempt > old.attempt { code = "" }
                if fresh != flow { flow = fresh }
                if fresh.state == "done" {
                    browser = nil
                    onDone()
                }
                if fresh.isOver { return }
            }
        }
    }

    private func submit() async {
        guard let client = app.client, let id = flow?.id else { return }
        submitting = true
        defer { submitting = false }
        do {
            flow = try await client.submitSignInCode(id, code: code.trimmingCharacters(in: .whitespacesAndNewlines))
            browser = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func useForAssistant() async {
        guard let client = app.client else { return }
        _ = try? await client.setRoute(feature: "assistant", provider: engine, model: "default")
        onDone()
        close()
    }

    private func close() {
        poller?.cancel()
        if let flow, !flow.isOver, let client = app.client {
            Task { try? await client.cancelSignIn(flow.id) }
        }
        dismiss()
    }
}

/// The provider's sign-in page inside the app, so copying a code and coming
/// back is one swipe, not an app switch.
struct SafariView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        SFSafariViewController(url: url)
    }

    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}
}

struct BrowserLink: Identifiable {
    let url: URL
    var id: String { url.absoluteString }
}

// MARK: - Which AI does what

/// Choose the provider and model for one feature: the assistant, the watch,
/// or the explanations.
struct RoutePickerView: View {
    let feature: String
    let title: String

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss
    @State private var models: AIModels?
    @State private var route: AIAccounts.Route?
    @State private var saving = false
    @State private var toast: Toast?

    var body: some View {
        ThemedList {
            if feature != "assistant" {
                Section {
                    Button {
                        Task { await choose(provider: "", model: "") }
                    } label: {
                        HStack {
                            Text("Same as the assistant").foregroundStyle(Theme.text)
                            Spacer()
                            if route?.custom == false {
                                Image(systemName: "checkmark").foregroundStyle(Theme.accent)
                            }
                        }
                    }
                } footer: {
                    Text(feature == "watch"
                         ? "The watch runs every few hours: a small, cheap model is usually enough — a subscription costs nothing extra."
                         : "Explanations are short: a small, fast model is enough.")
                }
            }

            if let models {
                ForEach(models.providers) { entry in
                    Section {
                        ForEach(entry.models) { model in
                            Button {
                                Task { await choose(provider: entry.provider, model: model.id) }
                            } label: {
                                HStack {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(model.name).foregroundStyle(Theme.text)
                                        if feature == "watch" && !model.tools && !entry.agent {
                                            Text("cannot use tools — it would only guess")
                                                .font(.caption2)
                                                .foregroundStyle(Theme.warn)
                                        } else if !model.hint.isEmpty {
                                            Text(model.hint)
                                                .font(.caption2)
                                                .foregroundStyle(model.billedPerUse ? Theme.warn : Theme.muted)
                                        }
                                    }
                                    Spacer()
                                    if model.free { StatusPill(text: "free", tint: Theme.accent2) }
                                    if model.billedPerUse { StatusPill(text: "API key", tint: Theme.warn) }
                                    if isChosen(entry.provider, model.id) {
                                        Image(systemName: "checkmark").foregroundStyle(Theme.accent)
                                    }
                                }
                            }
                            .disabled(entry.agent && !entry.signedIn)
                        }
                    } header: {
                        HStack(spacing: 8) {
                            ServiceIcon(names: [entry.provider == "codex" ? "openai" : entry.provider, entry.label],
                                        category: "AI", size: 22)
                            Text(entry.displayName)
                            if entry.agent {
                                Text(entry.signedIn ? "· subscription" : "· not signed in")
                            }
                        }
                        .textCase(nil)
                    }
                }
            } else {
                Section { ProgressView().frame(maxWidth: .infinity) }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .toast($toast)
        .overlay { if saving { ProgressView() } }
        .task {
            guard let client = app.client else { return }
            async let models = client.aiModels()
            async let accounts = client.aiAccounts()
            self.models = try? await models
            route = (try? await accounts)?.routes[feature]
        }
    }

    private func isChosen(_ provider: String, _ model: String) -> Bool {
        guard let route, route.custom || feature == "assistant" else { return false }
        return route.provider == provider && (route.model == model || (model == "default" && route.model.isEmpty))
    }

    private func choose(provider: String, model: String) async {
        guard let client = app.client else { return }
        saving = true
        defer { saving = false }
        do {
            let routes = try await client.setRoute(feature: feature, provider: provider, model: model)
            route = routes[feature]
            await app.refreshMe()
            toast = Toast(text: "Saved")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }
}
