# PocketADM — native SwiftUI preview

A proof-of-concept rewrite of the PocketADM client as a real SwiftUI app,
built to answer one question: *what would this feel like if it were native?*

It talks to the **same, unmodified backend** as the PWA. Nothing in `server/`,
`web/`, `client/` or `docker-compose.yml` was touched.

---

## 1. What you still have to do by hand

Everything else is automated. This one step is not, because Apple's API cannot
do it:

> **Create the App Store Connect app record.**
>
> App Store Connect → **Apps** → **+** → **New App**
> - Platform: **iOS**
> - Name: **PocketADM Native** (must be unique across the store; if it is
>   taken, anything works — the name here is not the bundle id)
> - Primary language: English
> - Bundle ID: **`de.maxaufknax.pocketadm.native`**
> - SKU: `pocketadm-native`

**When to do it:** after the first `ios-native-preview` run, not before. That
run registers the bundle id in the Developer Portal for you (see below), and
the id has to exist in the portal before it appears in this dropdown.

So the order is:

1. Push the branch → `ios-native-check` proves it compiles.
2. Tag `native-v0.1.0` → `ios-native-preview` runs. It **registers the App ID**
   and creates the signing certificate + profile, builds a signed IPA, and then
   **fails at the upload** with *"Cannot determine the Apple ID from Bundle ID"*.
   That failure is expected and harmless — the build log warns about it up front.
3. Do the manual step above (the bundle id is now in the dropdown).
4. Re-tag (`native-v0.1.1`) → this run uploads to TestFlight.
5. TestFlight → **Internal Testing** → add yourself as a tester. Internal builds
   need no beta review and appear within minutes of processing.

### Why the App ID itself is *not* manual

`app-store-connect fetch-signing-files "$BUNDLE_ID" --create` registers the
bundle identifier in the Developer Portal if it does not exist. It cannot
create the App Store Connect *app record* — those are two different things in
two different systems, and only the second one is manual.

### What was reused rather than recreated

- **ASC API key** — the workflow references the existing `PocketADM ASC key`
  integration. An App Store Connect key belongs to the *account*, not an app.
  Do not add a second one; if Codemagic says *"integration does not exist"*,
  it is a spelling mistake in the YAML.
- **`CERTIFICATE_PRIVATE_KEY_B64`** — read from the existing `ios-release`
  variable group. Apple caps distribution certificates per account, so both
  apps deliberately sign with the same key. Reading a group cannot affect the
  other workflow.

---

## 2. Isolation from the app in review

| | Capacitor app (in review) | This preview |
|---|---|---|
| Bundle ID | `de.maxaufknax.pocketadm` | `de.maxaufknax.pocketadm.native` |
| Source | `client/` | `client-swift/` |
| Workflow | `ios-release` | `ios-native-preview` |
| Trigger | manual | tag `native-v*` |
| `submit_to_app_store` | `false` | `false` |

`ios-release` was **not edited** — the new workflows are appended below it, and
the original file is a byte-for-byte prefix of the new one. Its
`submit_to_app_store` flag (the only thing that could disturb a pending review)
is untouched at `false`.

Two things worth knowing:

- `ios-native-check` triggers on push, but is scoped to `experiment/*` branches
  so pushes to `main` do not start macOS builds.
- `ios-release` has no `triggering:` block, so it does not auto-build on push.
  **If you have configured automatic triggering for it in the Codemagic web UI,
  that setting is not visible in this file** — check it before pushing, since
  the YAML cannot show it.

---

## 3. Status

### Working

- **Connect** — enter a host; probes `GET /api/info` and refuses anything that
  is not a PocketADM. Without a scheme it tries `https://` then `http://`.
- **Pairing by QR** (the primary path) — scans `<origin>/pair?code=…`, which
  carries the server address *and* the credential, then `POST /api/pair/claim`.
  The reverse direction works too: Settings → *Pair another device* mints a code
  and renders the QR on-device.
- **Password login** with correct **2FA** handling.
- **Dashboard** — CPU / memory / disk / network tiles, a one-hour Swift Charts
  history graph, host and Docker facts. Polls every 5s.
- **Containers** — grouped by compose stack, searchable, swipe to
  start/stop/restart, detail sheet with facts and logs.
- **Terminal** — the real thing: server-side sessions, a live PTY over
  `/ws/terminal`, rendered by SwiftTerm (a genuine xterm emulator, so colors,
  curses apps and escape sequences work rather than printing as garbage).
- **Settings** — connection info, pair a device, sign out, forget server.

### Not built

Vibe Code / AI chat (`/ws/chat`), Local AI / Ollama, App Store catalog,
Updates, Checks & Reports, and the deeper Settings (password change, 2FA setup,
session list, audit log). The scope here was four core screens plus
what they needed.

---

## 4. Verification actually performed

There is no Mac and no iOS simulator on this server, so this is what could and
could not be proven:

**Proven**
- Every `Codable` model decodes **real JSON captured from the running server**
  (the demo instance on `:8091`), including hand-made null-path fixtures for
  `docker: null`, `net: null` and `ping: null` — all three of which the real
  server genuinely returns. Compiled and run against the Swift 6 toolchain.
- `APIClient` type-checks under Swift 6, and the WebSocket URL derivation is
  asserted: `https→wss`, `http→ws`, port preserved, `token` and `session`
  present as query parameters.
- The app icon is 1024×1024 **RGB with no alpha channel** — an alpha channel is
  the `ITMS-90717` rejection that the Capacitor pipeline needs a `sips`
  round-trip to undo.
- Brace/string sanity across all 18 Swift files.

- **The whole app compiles.** `ios-native-check` is green on Codemagic
  (Xcode, iOS Simulator target), so the SwiftUI and UIKit layer builds — that
  needed macOS and could not be checked here.

**Not proven — verify on device**
- **Safe areas on a Face ID device.** See below.

### Safe areas

This is the one part that could not be tested here. What the
design does about it:

- `TabView` + `NavigationStack` inset for the notch and home indicator natively;
  there is no hand-rolled `--safe-top` equivalent to get wrong.
- The terminal's key bar is **SwiftTerm's own `inputAccessoryView`**, a
  `UIInputView`. UIKit positions those above the keyboard, and above the home
  indicator when the keyboard is down — the exact case that broke in the web
  build is handled by the system rather than by CSS.
- The terminal deliberately draws its background under the home indicator
  (`.ignoresSafeArea(.container, edges: .bottom)`) so scrollback does not end in
  a grey band. The accessory bar is unaffected, being UIKit-positioned.
- Text entry screens are `ScrollView`s with `.scrollDismissesKeyboard(.interactively)`.

**Check on device:** the terminal with the keyboard both up and down, in
portrait *and* landscape, on an iPhone 15/16.

### CI gotchas already hit and fixed

- **Metal toolchain.** SwiftTerm lists `Apple/Metal/Shaders.metal` as an
  unconditional package resource, so the `metal` compiler must exist at build
  time even though the Metal renderer is only an optional runtime fast path —
  there is no build flag to opt out. Since Xcode 16.3 that toolchain is a
  separate downloadable component and is *not* on the Codemagic image; without
  it the build dies with `cannot execute tool 'metal'`. Both native workflows
  now run `xcodebuild -downloadComponent MetalToolchain` first. It is
  idempotent, but it re-downloads on every fresh VM, so builds take noticeably
  longer. Fallback if it ever proves unreliable: pin SwiftTerm to 1.11.2, the
  last release before the Metal backend landed in 1.12.0.
- **`-skipPackagePluginValidation`.** SwiftTerm also ships a build-tool plugin,
  and Xcode refuses to run an unvalidated plugin non-interactively. Already
  passed in both workflows.

---

## 5. Design decisions

- **Palette taken verbatim** from `web/style.css` ("Deep Sea": `#0b0f14`,
  `#121821`, accent `#4da3ff`). The web app offers several themes per device;
  this preview commits to the default one and forces `.preferredColorScheme(.dark)` —
  the palette is unreadable if the system flips it to light.
- **Four tabs** (Dashboard / Containers / Terminal / Settings) instead of the
  web app's larger nav. The unbuilt features would slot in as more tabs or a
  "More" tab.
- **Containers grouped by compose stack**, mirroring the web UI; containers
  without a project fall into one "Ungrouped" bucket rather than each becoming
  a one-item group.
- **Metric colour thresholds** guessed at green <70%, amber <90%, red above.
- **The network tile has no progress bar** — network throughput has no ceiling,
  so a bar would be meaningless.
- **Stop and restart ask for confirmation; start does not.** Asymmetric on
  purpose: only one direction can take a service down.
- **QR rendered on-device** with CoreImage rather than via `POST /api/qr`. The
  server endpoint works, but a round trip for something CoreImage draws locally
  only adds a failure mode.
- **Icon**: a shell prompt (chevron + cursor) on the palette's gradient. It
  stays legible at 60px where a server-rack drawing would not. Regenerate with
  `tools/make-icon.py`.
- **App name "PocketADM"** on the home screen — same as the Capacitor app. If
  having two identically-named icons is confusing, change `CFBundleDisplayName`
  in `project.yml` to something like "PocketADM β".

## 6. One thing to tighten before this is ever more than a preview

`NSAppTransportSecurity.NSAllowsArbitraryLoads` is **true** in `project.yml`.
Self-hosted servers legitimately live at `http://192.168.1.10:8090` or behind a
private CA, and ATS cannot know those are trusted. TestFlight does not care,
but an App Store submission requires a written justification for this key — and
the better answer is probably to allow cleartext only for local-network
literals and require TLS for public hosts.

---

## 7. Building it locally (on a Mac)

```bash
cd client-swift
./tools/bootstrap-config.sh     # writes the generated Config/Build.xcconfig
xcodegen generate               # produces PocketADMNative.xcodeproj
open PocketADMNative.xcodeproj
```

The `.xcodeproj` is generated and gitignored, so `project.yml` is the source of
truth — edit that, not the Xcode project.
