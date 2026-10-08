import SwiftUI

/// More → Appearance: the themes, each shown as a small picture of the app in
/// its colours, and Light / Dark / System for the default theme.
struct AppearanceView: View {
    @EnvironmentObject private var app: AppState
    @AppStorage(ThemeStore.key) private var themeID = "system"
    @AppStorage("pocketadm.appearance") private var appearance = "system"

    private let columns = [GridItem(.flexible(), spacing: 14), GridItem(.flexible(), spacing: 14)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                if ThemeStore.current.isSystem {
                    VStack(alignment: .leading, spacing: 8) {
                        SectionCaption(text: "APPEARANCE")
                        Picker("Appearance", selection: $appearance) {
                            Text("System").tag("system")
                            Text("Light").tag("light")
                            Text("Dark").tag("dark")
                        }
                        .pickerStyle(.segmented)
                        Text("The PocketADM theme follows iOS, or stays light or dark.")
                            .font(.footnote)
                            .foregroundStyle(Theme.muted)
                    }
                }

                VStack(alignment: .leading, spacing: 10) {
                    SectionCaption(text: "THEMES")
                    LazyVGrid(columns: columns, spacing: 14) {
                        ForEach(AppTheme.all) { theme in
                            Button {
                                choose(theme)
                            } label: {
                                ThemeTile(theme: theme, selected: theme.id == ThemeStore.current.id)
                            }
                            .buttonStyle(PressableRowStyle())
                            .accessibilityLabel("\(theme.name) theme")
                            .accessibilityAddTraits(theme.id == ThemeStore.current.id ? .isSelected : [])
                        }
                    }
                    Text("Themes change the colours of every screen on this phone. Each server you connect to looks the same.")
                        .font(.footnote)
                        .foregroundStyle(Theme.muted)
                }
            }
            .padding(16)
        }
        .screenBackground()
        .navigationTitle("Appearance")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func choose(_ theme: AppTheme) {
        guard theme.id != ThemeStore.current.id else { return }
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        // the screens are rebuilt in the new colours — come back to this one
        app.selectedTab = .more
        app.pendingRoute = .appearance
        withAnimation(.easeInOut(duration: 0.25)) {
            themeID = theme.id
        }
    }
}

/// A theme as a miniature of the app: page, a card with a row and a toggle,
/// the accent button, and its name.
struct ThemeTile: View {
    let theme: AppTheme
    let selected: Bool

    @Environment(\.colorScheme) private var scheme

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            preview
                .frame(height: 118)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .strokeBorder(selected ? Theme.accent : Theme.border.opacity(0.6),
                                      lineWidth: selected ? 2.5 : 1)
                )
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(theme.name)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Theme.text)
                    Text(theme.blurb)
                        .font(.caption)
                        .foregroundStyle(Theme.muted)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
                if selected {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(Theme.accent)
                }
            }
        }
        .contentShape(Rectangle())
    }

    /// The default theme follows the system, so its picture does too.
    private var dark: Bool { theme.scheme == .dark || (theme.scheme == nil && scheme == .dark) }
    private var page: Color { theme.page ?? (dark ? Color(hex: 0x000000) : Color(hex: 0xF2F2F7)) }
    private var card: Color { theme.card ?? (dark ? Color(hex: 0x1C1C1E) : .white) }
    private var text: Color { theme.text ?? (dark ? .white : .black) }
    private var muted: Color { theme.muted ?? (dark ? Color(hex: 0x8E8E93) : Color(hex: 0x8A8A8E)) }
    private var accent: Color { theme.accent }

    private var preview: some View {
        ZStack(alignment: .topLeading) {
            page
            LinearGradient(colors: theme.gradient.map { $0.opacity(0.55) }, startPoint: .topLeading,
                           endPoint: .bottomTrailing)
                .frame(height: 34)
            VStack(alignment: .leading, spacing: 7) {
                HStack(spacing: 5) {
                    Circle().fill(accent).frame(width: 9, height: 9)
                    Capsule().fill(text.opacity(0.85)).frame(width: 46, height: 6)
                    Spacer()
                }
                .padding(.top, 2)
                VStack(alignment: .leading, spacing: 6) {
                    row(width: 58, on: true)
                    row(width: 40, on: false)
                }
                .padding(8)
                .background(card, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                Capsule()
                    .fill(accent)
                    .frame(width: 54, height: 13)
                    .overlay(Capsule().fill(theme.onAccent.opacity(0.85)).frame(width: 26, height: 3.5))
            }
            .padding(10)
        }
    }

    private func row(width: CGFloat, on: Bool) -> some View {
        HStack(spacing: 6) {
            RoundedRectangle(cornerRadius: 3).fill(accent.opacity(on ? 1 : 0.45)).frame(width: 11, height: 11)
            VStack(alignment: .leading, spacing: 3) {
                Capsule().fill(text.opacity(0.8)).frame(width: width, height: 4)
                Capsule().fill(muted.opacity(0.7)).frame(width: width * 0.6, height: 3)
            }
            Spacer(minLength: 4)
            Capsule()
                .fill(on ? (theme.accent2 ?? Color(hex: 0x34C759)) : muted.opacity(0.35))
                .frame(width: 18, height: 10)
                .overlay(Circle().fill(.white).frame(width: 8, height: 8)
                    .offset(x: on ? 4 : -4))
        }
    }
}
