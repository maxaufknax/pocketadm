import SwiftUI
import UIKit
import UserNotifications

/// How the watch behaves: which AI it thinks with, how often it looks, when
/// it stays quiet, where its messages go and what it should know.
struct WatchSettingsView: View {
    @EnvironmentObject private var app: AppState
    @EnvironmentObject private var push: PushManager

    @State private var status: WatchStatus?
    @State private var draft = WatchSettings()
    @State private var matrixToken = ""
    @State private var saving = false
    @State private var loaded = false
    @State private var toast: Toast?
    @State private var confirmForget = false

    private static let intervals: [(Int, String)] = [
        (0, "Only when something happens"), (60, "Every hour"), (120, "Every 2 hours"),
        (180, "Every 3 hours"), (360, "Every 6 hours"), (720, "Twice a day"), (1440, "Once a day"),
    ]
    private static let languages: [(String, String)] = [
        ("", "English"), ("de", "Deutsch"), ("fr", "Français"), ("es", "Español"),
        ("it", "Italiano"), ("nl", "Nederlands"), ("pt", "Português"), ("pl", "Polski"),
    ]

    var body: some View {
        Form {
            Section {
                Toggle(isOn: $draft.enabled) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Watch this server")
                        Text("Writes only when something is worth knowing.")
                            .font(.footnote)
                            .foregroundStyle(Theme.muted)
                    }
                }
            }

            Section {
                NavigationLink {
                    RoutePickerView(feature: "watch", title: "AI for the watch")
                } label: {
                    HStack {
                        Text("Thinks with")
                        Spacer()
                        Text(status?.route.label.isEmpty == false ? status!.route.label : "Not chosen")
                            .foregroundStyle(status?.route.usable == true ? Theme.muted : Theme.warn)
                    }
                }
                Picker("Writes in", selection: $draft.lang) {
                    ForEach(Self.languages, id: \.0) { lang in
                        Text(lang.1).tag(lang.0)
                    }
                }
            } header: {
                Text("AI")
            } footer: {
                Text("A subscription (Claude, ChatGPT, Mistral) costs nothing extra per message; an API key is billed per use — see the budget below.")
            }

            Section {
                Picker("Looks around", selection: $draft.intervalMin) {
                    ForEach(Self.intervals, id: \.0) { option in
                        Text(option.1).tag(option.0)
                    }
                }
                Toggle("Weekly look back on Sundays", isOn: $draft.weekly)
            } header: {
                Text("How often")
            } footer: {
                Text("Besides its rounds it reacts within minutes when a container crashes, a service fails, the disk fills up or the internet drops.")
            }

            Section {
                timeRow("Quiet from", text: $draft.quietStart)
                timeRow("Until", text: $draft.quietEnd)
                Stepper("At most \(draft.infoPerDay) small notes a day", value: $draft.infoPerDay, in: 0...10)
                Stepper("At most \(draft.importantPerDay) important ones", value: $draft.importantPerDay, in: 0...20)
            } header: {
                Text("Staying quiet")
            } footer: {
                Text("At night only critical messages get through. A topic it already told you about comes up again only if it gets worse.")
            }

            Section {
                HStack {
                    Text("Budget per 30 days")
                    Spacer()
                    TextField("5.00", value: $draft.budgetUSD, format: .number.precision(.fractionLength(2)))
                        .keyboardType(.decimalPad)
                        .multilineTextAlignment(.trailing)
                        .frame(width: 80)
                    Text("$").foregroundStyle(Theme.muted)
                }
                if let status {
                    FactRow(label: "Spent so far", value: String(format: "$%.2f", status.spent30d))
                }
            } header: {
                Text("Cost")
            } footer: {
                Text("0 means no limit. When the budget runs out it stops its rounds but still reacts to incidents only if money is left.")
            }

            PhoneNotificationsSection()

            Section {
                Picker("Send to ntfy and Matrix", selection: $draft.pushMin) {
                    Text("Critical only").tag("critical")
                    Text("Important and critical").tag("important")
                    Text("Everything").tag("info")
                }
                TextField("ntfy topic URL (https://ntfy.sh/…)", text: $draft.ntfyURL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
            } header: {
                Text("Elsewhere")
            } footer: {
                Text("Optional: the same messages in the ntfy app or a Matrix room (Element).")
            }

            Section {
                TextField("Homeserver (https://matrix.example.com)", text: $draft.matrixHomeserver)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                TextField("Room ID (!abc:example.com)", text: $draft.matrixRoom)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                SecureField(draft.matrixTokenSet ? "Access token saved — paste to replace" : "Access token of the bot account",
                            text: $matrixToken)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Button("Send a test message") { Task { await testDelivery() } }
                    .disabled(app.me?.demo == true)
            } header: {
                Text("Matrix (Element)")
            } footer: {
                Text("Like a bot that writes to you in Element. Use a separate account for it and invite it to a room with you.")
            }

            Section {
                TextEditor(text: $draft.knowledge)
                    .frame(minHeight: 110)
                    .font(.subheadline)
            } header: {
                Text("What the watch should know")
            } footer: {
                Text("For example: “Minecraft is stopped on purpose.” “The backup disk is only plugged in on Sundays.” “Nextcloud is used by my family.”")
            }

            if let status, !status.settings.mutes.isEmpty {
                Section("Muted topics") {
                    ForEach(status.settings.mutes) { mute in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(mute.topic).foregroundStyle(Theme.text)
                            Text("until \(Date(timeIntervalSince1970: mute.until).formatted(date: .abbreviated, time: .omitted))"
                                 + (mute.note.isEmpty ? "" : " · \(mute.note)"))
                                .font(.footnote)
                                .foregroundStyle(Theme.muted)
                        }
                        .swipeActions {
                            Button("Unmute") { Task { await unmute(mute.topic) } }
                                .tint(Theme.accent)
                        }
                    }
                }
            }

            if let status, !status.memory.isEmpty {
                Section {
                    ForEach(status.memory, id: \.self) { note in
                        Text(note)
                            .font(.footnote)
                            .foregroundStyle(Theme.text)
                    }
                    Button("Forget all of it", role: .destructive) { confirmForget = true }
                } header: {
                    Text("What it remembers")
                } footer: {
                    Text("Notes it keeps between rounds, like a baseline to compare against.")
                }
            }

            if let status, !status.runs.isEmpty {
                Section("Recent looks") {
                    ForEach(status.runs) { run in
                        HStack(alignment: .top, spacing: 10) {
                            Image(systemName: symbol(for: run))
                                .foregroundStyle(tint(for: run))
                                .frame(width: 20)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(title(for: run))
                                    .font(.subheadline)
                                    .foregroundStyle(Theme.text)
                                Text(([Fmt.ago(run.date), run.kind] + (run.cost > 0 ? [String(format: "$%.3f", run.cost)] : []))
                                        .joined(separator: " · "))
                                    .font(.caption)
                                    .foregroundStyle(Theme.muted)
                            }
                        }
                    }
                }
            }
        }
        .navigationTitle("Watch")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                if saving {
                    ProgressView()
                } else {
                    Button("Save") { Task { await save() } }
                        .fontWeight(.semibold)
                        .disabled(app.me?.demo == true)
                }
            }
        }
        .task { if !loaded { await load() } }
        .toast($toast)
        .confirmationDialog("Forget everything the watch remembers?", isPresented: $confirmForget,
                            titleVisibility: .visible) {
            Button("Forget", role: .destructive) { Task { await forget() } }
            Button("Cancel", role: .cancel) {}
        }
    }

    private func timeRow(_ label: String, text: Binding<String>) -> some View {
        DatePicker(label, selection: Binding(
            get: { Self.date(from: text.wrappedValue) },
            set: { text.wrappedValue = Self.string(from: $0) }
        ), displayedComponents: .hourAndMinute)
    }

    private static func date(from hhmm: String) -> Date {
        let parts = hhmm.split(separator: ":").compactMap { Int($0) }
        var components = DateComponents()
        components.hour = parts.first ?? 23
        components.minute = parts.count > 1 ? parts[1] : 0
        return Calendar.current.date(from: components) ?? Date()
    }

    private static func string(from date: Date) -> String {
        let c = Calendar.current.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", c.hour ?? 0, c.minute ?? 0)
    }

    private func symbol(for run: WatchStatus.Run) -> String {
        switch run.decision {
        case "notify":  return "envelope.fill"
        case "silent":  return "checkmark.circle"
        case "held":    return "hand.raised.fill"
        case "error":   return "exclamationmark.triangle.fill"
        default:        return "circle"
        }
    }

    private func tint(for run: WatchStatus.Run) -> Color {
        switch run.decision {
        case "notify": return Theme.accent
        case "error":  return Theme.danger
        case "held":   return Theme.warn
        default:       return Theme.muted
        }
    }

    private func title(for run: WatchStatus.Run) -> String {
        switch run.decision {
        case "notify":  return "Wrote to you" + (run.topic.isEmpty ? "" : " about \(run.topic)")
        case "silent":  return "Nothing worth a message" + (run.reason.isEmpty ? "" : " — \(run.reason)")
        case "held":    return "Kept a message back (\(run.reason))"
        case "skipped": return "Skipped — \(run.reason)"
        case "error":   return run.error.isEmpty ? "Could not look" : run.error
        default:        return run.decision
        }
    }

    // MARK: - Loading and saving

    private func load() async {
        guard let client = app.client else { return }
        defer { loaded = true }
        if let status = try? await client.watchStatus() {
            self.status = status
            draft = status.settings
            // first time: write in the phone's language, quiet by its clock
            if !status.settings.enabled && status.settings.lang.isEmpty,
               let code = Locale.current.language.languageCode?.identifier,
               Self.languages.contains(where: { $0.0 == code }) {
                draft.lang = code == "en" ? "" : code
            }
        }
    }

    private func save() async {
        guard let client = app.client else { return }
        saving = true
        defer { saving = false }
        if draft.timezone.isEmpty || draft.timezone != TimeZone.current.identifier {
            draft.timezone = TimeZone.current.identifier
        }
        do {
            let fresh = try await client.saveWatch(draft.changes(matrixToken: matrixToken))
            status = fresh
            draft = fresh.settings
            matrixToken = ""
            await app.refreshMe()
            toast = Toast(text: draft.enabled ? "The watch is on" : "Saved")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
            app.handle(error)
        }
    }

    private func testDelivery() async {
        guard let client = app.client else { return }
        // what is on screen has to be saved before it can be tested
        await save()
        do {
            try await client.testWatchDelivery()
            toast = Toast(text: "Sent — check your phone and Element")
        } catch {
            toast = Toast(text: error.localizedDescription, isError: true)
        }
    }

    private func unmute(_ topic: String) async {
        guard let client = app.client else { return }
        status = try? await client.muteWatchTopic(topic, hours: 0)
    }

    private func forget() async {
        guard let client = app.client else { return }
        status = try? await client.clearWatchMemory()
    }
}


/// Notifications on this iPhone: on or off, from which importance, the
/// assistant, previews — and whether real push reaches this phone or the
/// messages come with background refresh.
struct PhoneNotificationsSection: View {
    @EnvironmentObject private var app: AppState
    @EnvironmentObject private var push: PushManager
    @State private var testing = false
    /// The answer to the last tap, shown under the section (a toast on a
    /// form section would draw on every row).
    @State private var note: String?

    var body: some View {
        Section {
            Toggle(isOn: Binding(get: { push.allowed && push.wanted },
                                 set: { on in Task { await toggle(on) } })) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Notifications on this iPhone")
                    Text(stateLine)
                        .font(.footnote)
                        .foregroundStyle(stateTint)
                }
            }
            .disabled(app.me?.demo == true)

            if push.authorization == .denied {
                Button("Allow notifications in Settings") {
                    if let url = URL(string: UIApplication.openSettingsURLString) {
                        UIApplication.shared.open(url)
                    }
                }
            }

            if push.allowed && push.wanted {
                Picker("Watch messages", selection: Binding(get: { push.minimum },
                                                            set: { push.minimum = $0; sync() })) {
                    Text("All").tag("info")
                    Text("Important and critical").tag("important")
                    Text("Critical only").tag("critical")
                }
                Toggle("When the assistant needs you", isOn: Binding(get: { push.assistant },
                                                                    set: { push.assistant = $0; sync() }))
                Toggle("Show the message text", isOn: Binding(get: { push.preview },
                                                             set: { push.preview = $0; sync() }))
                Button {
                    Task { await test() }
                } label: {
                    HStack {
                        Text("Send a test notification")
                        if testing { Spacer(); ProgressView() }
                    }
                }
                .disabled(testing)
            }
        } header: {
            Text("This iPhone")
        } footer: {
            VStack(alignment: .leading, spacing: 4) {
                if let note { Text(note).foregroundStyle(Theme.accent) }
                Text("Messages always appear in the watch's channel. With the text hidden, a notification only says that there is news.")
            }
        }
    }

    private var stateLine: String {
        if push.authorization == .denied { return "Turned off in the iOS settings" }
        guard push.allowed && push.wanted else { return "The watch's messages and the assistant waiting for your OK" }
        if push.pushActive { return "Push is on — messages arrive right away" }
        if !app.supports("push") { return "This server sends no push yet — background refresh brings messages, a few minutes late" }
        if push.relayID != nil && !push.relayReady {
            return "Push is not live on the relay yet — background refresh brings messages, a few minutes late"
        }
        if let error = push.lastError, !error.isEmpty { return "Background refresh only (\(error))" }
        return "Setting up…"
    }

    private var stateTint: Color {
        if push.authorization == .denied { return Theme.warn }
        return push.pushActive ? Theme.accent2 : Theme.muted
    }

    private func toggle(_ on: Bool) async {
        if on {
            if push.authorization == .denied, let url = URL(string: UIApplication.openSettingsURLString) {
                _ = await UIApplication.shared.open(url)
                return
            }
            if await push.enable() { note = "Notifications are on." }
        } else {
            await push.disable()
        }
    }

    private func sync() {
        Task { await push.registerWithServer() }
    }

    private func test() async {
        testing = true
        defer { testing = false }
        if push.pushActive, let client = app.client {
            do {
                try await client.testPush()
                note = "Sent — it should arrive in a moment."
            } catch {
                note = error.localizedDescription
            }
        } else {
            let content = UNMutableNotificationContent()
            content.title = "Test notification"
            content.body = "This is how the watch's messages look on this iPhone."
            content.sound = .default
            content.userInfo = ["pocketadm": ["kind": "watch"]]
            let request = UNNotificationRequest(identifier: UUID().uuidString, content: content,
                                                trigger: UNTimeIntervalNotificationTrigger(timeInterval: 3, repeats: false))
            try? await UNUserNotificationCenter.current().add(request)
            note = "Lock the phone — it arrives in three seconds."
        }
    }
}
