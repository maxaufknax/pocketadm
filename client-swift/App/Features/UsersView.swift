import SwiftUI

/// Linux accounts on the host.
///
/// Only shown as manageable when the server says it can reach the host — inside
/// a container without host access every button here would fail, and offering
/// them anyway teaches people to distrust the app.
struct UsersView: View {
    @EnvironmentObject private var app: AppState

    @State private var data: ServerUsers?
    @State private var loaded = false
    @State private var error: String?
    @State private var showSystem = false
    @State private var selected: HostUser?
    @State private var showCreate = false
    @State private var toast: Toast?

    var body: some View {
        Group {
            if !loaded {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let data {
                content(data)
            } else {
                MessageState(symbol: "person.2.slash",
                             title: "Cannot read users",
                             message: error,
                             tint: Theme.danger,
                             retry: { Task { await load() } })
            }
        }
        .navigationTitle("Users")
        .navigationBarTitleDisplayMode(.large)
        .toast($toast)
        .toolbar {
            if data?.canManage == true {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showCreate = true } label: { Image(systemName: "plus") }
                        .accessibilityLabel("Add a user")
                }
            }
        }
        .task { if !loaded { await load() } }
        .sheet(item: $selected) { user in
            UserSheet(user: user, canManage: data?.canManage ?? false) { message in
                toast = Toast(text: message)
                Task { await load() }
            }
        }
        .sheet(isPresented: $showCreate) {
            CreateUserSheet { message in
                toast = Toast(text: message)
                Task { await load() }
            }
        }
    }

    private func content(_ data: ServerUsers) -> some View {
        ThemedList {
            Section("This machine") {
                FactRow(label: "Host", value: data.identity.hostname)
                FactRow(label: "System", value: data.identity.os)
                FactRow(label: "Kernel", value: "\(data.identity.kernel) · \(data.identity.arch)")
            }

            if !data.canManage {
                Section {
                    WarningBanner(title: "Read-only",
                                  message: data.reason.isEmpty
                                    ? "This server cannot manage host accounts from where it runs."
                                    : data.reason)
                }
                .listRowBackground(Theme.bg)
            }

            Section {
                ForEach(humans(data)) { user in
                    Button { selected = user } label: { UserRow(user: user) }
                }
            } header: {
                Text("People")
            }

            Section {
                DisclosureGroup(isExpanded: $showSystem) {
                    ForEach(system(data)) { user in
                        HStack {
                            Text(user.name)
                                .font(.system(.subheadline, design: .monospaced))
                                .foregroundStyle(Theme.text)
                            Spacer()
                            Text("uid \(user.uid)")
                                .font(.footnote)
                                .foregroundStyle(Theme.muted)
                        }
                    }
                } label: {
                    Text("\(system(data).count) system accounts")
                        .foregroundStyle(Theme.text)
                }
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await load() }
    }

    private func humans(_ data: ServerUsers) -> [HostUser] {
        data.users.filter { $0.kind == "human" || $0.isRoot }
    }

    private func system(_ data: ServerUsers) -> [HostUser] {
        data.users.filter { !($0.kind == "human" || $0.isRoot) }
    }

    private func load() async {
        guard let client = app.client else { return }
        defer { loaded = true }
        do {
            data = try await client.serverUsers()
            error = nil
        } catch {
            self.error = error.localizedDescription
            app.handle(error)
        }
    }
}

struct UserRow: View {
    let user: HostUser

    var body: some View {
        HStack(spacing: 14) {
            UserAvatar(user: user)

            VStack(alignment: .leading, spacing: 2) {
                Text(user.name)
                    .foregroundStyle(Theme.text)
                Text(user.role.isEmpty ? user.shell : user.role)
                    .font(.footnote)
                    .foregroundStyle(Theme.muted)
                    .lineLimit(1)
            }

            Spacer()

            if user.locked {
                StatusPill(text: "locked", tint: Theme.danger)
            } else if !user.canLogin {
                StatusPill(text: "no login", tint: Theme.muted)
            }
        }
        .padding(.vertical, 2)
    }
}

struct UserSheet: View {
    let user: HostUser
    let canManage: Bool
    let onChange: (String) -> Void

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var newPassword = ""
    @State private var working = false
    @State private var failure: String?

    var body: some View {
        NavigationStack {
            ThemedList {
                Section {
                    FactRow(label: "User", value: user.name)
                    FactRow(label: "UID", value: String(user.uid))
                    FactRow(label: "Home", value: user.home, selectable: true)
                    FactRow(label: "Shell", value: user.shell)
                    if !user.groups.isEmpty {
                        FactRow(label: "Groups", value: user.groups.joined(separator: ", "))
                    }
                }

                if canManage && !user.isRoot {
                    Section {
                        SecureField("New password", text: $newPassword)
                        Button("Set password") {
                            Task { await run { try await $0.setUserPassword(user.name, password: newPassword) } }
                        }
                        .tint(Theme.accent)
                        .disabled(newPassword.count < 8 || working)
                    } header: {
                        SectionCaption(text: "Password")
                    } footer: {
                        Text("At least 8 characters.")
                            .font(.caption)
                            .foregroundStyle(Theme.muted)
                    }

                    Section {
                        Button(user.locked ? "Unlock account" : "Lock account",
                               role: user.locked ? nil : ButtonRole.destructive) {
                            Task { await run { try await $0.setUserLocked(user.name, locked: !user.locked) } }
                        }
                        Button(user.isAdmin ? "Revoke admin" : "Grant admin",
                               role: user.isAdmin ? ButtonRole.destructive : nil) {
                            Task { await run { try await $0.setUserAdmin(user.name, admin: !user.isAdmin) } }
                        }
                    } header: {
                        SectionCaption(text: "Access")
                    } footer: {
                        Text("Admin means membership of the host's sudo group — full control of the machine.")
                            .font(.caption)
                            .foregroundStyle(Theme.muted)
                    }
                }

                if let failure {
                    Section {
                        Text(failure)
                            .font(.caption)
                            .foregroundStyle(Theme.danger)
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle(user.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }.tint(Theme.accent)
                }
            }
        }
    }

    /// Every action here is the same shape: call, report, close. Threading them
    /// through one helper keeps the buttons to one line each.
    private func run(_ body: @escaping (APIClient) async throws -> String) async {
        guard let client = app.client else { return }
        working = true
        failure = nil
        defer { working = false }
        do {
            let message = try await body(client)
            newPassword = ""
            onChange(message.isEmpty ? "Done" : message)
            dismiss()
        } catch {
            failure = error.localizedDescription
        }
    }
}

struct CreateUserSheet: View {
    let onCreated: (String) -> Void

    @EnvironmentObject private var app: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var name = ""
    @State private var password = ""
    @State private var admin = false
    @State private var working = false
    @State private var failure: String?

    var body: some View {
        NavigationStack {
            ThemedList {
                Section {
                    TextField("Username", text: $name)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("Password", text: $password)
                    Toggle("Grant admin", isOn: $admin)
                } footer: {
                    Text("Creates a real Linux account on the host, with a home directory and a login shell.")
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                }

                if let failure {
                    Section {
                        Text(failure).font(.caption).foregroundStyle(Theme.danger)
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("New user")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { dismiss() }.tint(Theme.muted)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Create") { Task { await create() } }
                        .tint(Theme.accent)
                        .disabled(name.isEmpty || password.count < 8 || working)
                }
            }
        }
    }

    private func create() async {
        guard let client = app.client else { return }
        working = true
        failure = nil
        defer { working = false }
        do {
            let message = try await client.createUser(name, password: password, admin: admin)
            onCreated(message.isEmpty ? "User created" : message)
            dismiss()
        } catch {
            failure = error.localizedDescription
        }
    }
}

/// A Contacts-style avatar: the initial on a gradient disc — orange with a
/// crown for root, blue for admins.
struct UserAvatar: View {
    let user: HostUser
    var size: CGFloat = 38

    var body: some View {
        ZStack {
            Circle().fill(LinearGradient(colors: colors, startPoint: .top, endPoint: .bottom))
            if user.isRoot {
                Image(systemName: "crown.fill")
                    .font(.system(size: size * 0.42, weight: .semibold))
                    .foregroundStyle(.white)
            } else {
                Text(String(user.name.prefix(1)).uppercased())
                    .font(.system(size: size * 0.45, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white)
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }

    private var colors: [Color] {
        if user.isRoot { return [Color.orange.opacity(0.85), .orange] }
        if user.isAdmin { return [Color.blue.opacity(0.75), .blue] }
        return [Color(uiColor: .systemGray2), Color(uiColor: .systemGray)]
    }
}
