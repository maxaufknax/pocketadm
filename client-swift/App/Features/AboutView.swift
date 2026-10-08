import SwiftUI

/// Version, links and credits.
struct AboutView: View {
    @EnvironmentObject private var app: AppState

    private var appVersion: String {
        let info = Bundle.main.infoDictionary
        let version = info?["CFBundleShortVersionString"] as? String ?? "—"
        let build = info?["CFBundleVersion"] as? String ?? ""
        return build.isEmpty ? version : "\(version) (\(build))"
    }

    var body: some View {
        ThemedList {
            Section {
                VStack(spacing: 10) {
                    ServiceIcon(names: ["pocketadm"], size: 84)
                        .shadow(color: .black.opacity(0.12), radius: 10, y: 4)
                    Text("PocketADM")
                        .font(.title2.weight(.bold))
                    Text("Your server, in your pocket.")
                        .font(.subheadline)
                        .foregroundStyle(Theme.muted)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
                .listRowBackground(Color.clear)
            }

            Section {
                FactRow(label: "App", value: appVersion)
                if let me = app.me {
                    FactRow(label: "Server", value: me.version)
                }
            }

            Section {
                linkRow("Website", symbol: "globe", url: "https://pocketadm.com")
                linkRow("Help & installation", symbol: "questionmark.circle", url: "https://pocketadm.com/support/")
                linkRow("Privacy policy", symbol: "hand.raised", url: "https://pocketadm.com/privacy/")
                linkRow("Source code", symbol: "chevron.left.forwardslash.chevron.right",
                        url: "https://github.com/maxaufknax/pocketadm")
            }

            Section {
                Text("The terminal is SwiftTerm by Miguel de Icaza (MIT). Service logos are Simple Icons (CC0); they identify third-party services, and all trademarks belong to their owners.")
                    .font(.footnote)
                    .foregroundStyle(Theme.muted)
            } header: {
                Text("Acknowledgements")
            }
        }
        .navigationTitle("About")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func linkRow(_ title: String, symbol: String, url: String) -> some View {
        Link(destination: URL(string: url)!) {
            HStack {
                Label(title, systemImage: symbol)
                    .foregroundStyle(Theme.text)
                Spacer()
                Image(systemName: "arrow.up.right")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Theme.muted)
            }
        }
    }
}
