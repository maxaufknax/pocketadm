# PocketADM — the native SwiftUI client

A rewrite of the PocketADM client as a real SwiftUI app. It started as a
proof of concept for four screens; it now covers everything the web client
does that makes sense on a phone (see §3).

It talks to the **same, unmodified backend** as the PWA. Nothing in `server/`,
`web/`, `client/` or `docker-compose.yml` was touched.

---

## 1. Releasing a build

**The one-time setup is done.** The App Store Connect record for
`de.maxaufknax.pocketadm.native` exists (app id `6805165975`), the bundle id is
registered in the Developer Portal, and build `202608251936` (0.1.0) is already
in TestFlight. Nothing here is manual any more.

To ship a new build:

1. Push to `experiment/*` → `ios-native-check` proves it compiles against the
   simulator SDK. **Do this first, always** — the SwiftUI half cannot be checked
   on the server (§4), so this is the only compiler that sees it.
2. Bump `MARKETING_VERSION` in `project.yml`.
3. Tag `native-v<version>` and push the tag → `ios-native-preview` signs and
   uploads to TestFlight. The build number is a timestamp, generated in CI.
4. TestFlight → **Internal Testing**. Internal builds need no beta review and
   appear within minutes of processing.

Automatic triggering on push is *not* configured for this app in the Codemagic
UI, so a push alone starts nothing. Start a run explicitly:

```bash
curl -X POST -H "x-auth-token: $CM_TOKEN" -H 'Content-Type: application/json' \
  -d '{"appId":"6a56048d02f1cc13c7696155","workflowId":"ios-native-check",
       "branch":"experiment/swift-native"}' \
  https://api.codemagic.io/builds
```

Swap `workflowId` for `ios-native-preview` and `branch` for `"tag":"native-v…"`
to release. A restarted build reuses the **same commit**, so after a YAML fix a
*new* build has to be started rather than the old one retried.

### Why the App ID was never the manual part

`app-store-connect fetch-signing-files "$BUNDLE_ID" --create` registers the
bundle identifier in the Developer Portal if it does not exist. It cannot create
the App Store Connect *app record* — those are two different things in two
different systems, and only the second one was ever manual. That is why the
very first `native-v*` run was expected to fail at the upload step with *"Cannot
determine the Apple ID from Bundle ID"*: it registered the id so the record
could then be created. Both are done; that failure will not recur.

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

**Core**

- **Connect** — enter a host; probes `GET /api/info` and refuses anything that
  is not a PocketADM. Without a scheme it tries `https://` then `http://`.
- **Pairing by QR** (the primary path) — scans `<origin>/pair?code=…`, which
  carries the server address *and* the credential, then `POST /api/pair/claim`.
  The reverse direction works too: Settings → *Pair another device* mints a code
  and renders the QR on-device.
- **Password login** with correct **2FA** handling.
- **Dashboard** — CPU / memory / disk / network tiles, a one-hour Swift Charts
  history graph, host and Docker facts, an alert bell, the public-exposure
  warning, and a glance row for pending updates and the health score. Polls
  every 5s.
- **Containers** — grouped by compose stack, searchable, swipe to
  start/stop/restart; detail with live stats, mounts, networks, restart policy,
  logs at four tail depths, an AI explanation, and removal.
- **Terminal** — the real thing: server-side sessions, a live PTY over
  `/ws/terminal`, rendered by SwiftTerm (a genuine xterm emulator, so colors,
  curses apps and escape sequences work rather than printing as garbage).

**Assistant** (`/ws/chat`)

The full agent loop, not a chat box: streaming replies, collapsible reasoning,
tool cards with coloured diffs and collapsed output, the per-call approval gate,
checkpoint pauses with a Continue button, live plans, the four modes
(Chat/Plan/Agent/Auto), a provider+model picker, the working directory, and the
chat history the server keeps across devices.

**Operate**

- **Updates** — pending images with upstream release notes, single or bulk
  apply streamed as a live job log, an ignore list, host `apt` packages
  (read-only, on purpose), and the snapshots that make a bad pull undoable.
- **Apps** — the catalog with search and category filter, install with the
  fields an app declares, and uninstall that distinguishes "PocketADM put this
  here" from "it was already running".
- **Checks** — the health report grouped and ranked worst-first, run-now, the
  schedule, earlier runs, and the AI explanation of a report.
- **Alerts** — the notification feed; opening it clears the badge.

**The machine**

- **Files** — read-only browser over the configured workspaces, with text
  preview. Read-only on purpose: editing a compose file from a phone with no
  diff and no undo is how servers break.
- **Users** — host accounts, with password/lock/admin/create where the server
  reports it can actually reach the host, and a plain explanation where it
  cannot.
- **Coding agents** — install Claude Code / Codex / Vibe onto the server as a
  streamed job.

**Configure**

- **AI** — per-provider keys (write-only), the default model, and usage/cost.
- **Local models** — install Ollama, pull and delete models, connect an
  existing instance.
- **Agent** — memory, custom instructions, and per-tool on/off.
- **Security** — password change, 2FA enrolment and removal, sign out other
  devices, the exposure warning, and the activity log.
- **Settings** — connection facts, rename the server, pair a device,
  workspaces, sign out, forget server.

### Not built

Integrations (DNS providers), agent loops and their permission queue, skills,
the server map, and backup export/restore. All of them are configuration
surfaces that are genuinely better on a big screen; nothing in the day-to-day
operating path is missing.

### Structure

Five tabs — Dashboard, Containers, Terminal, Assistant, More — with **More** as
the hub for everything that does not earn a tab of its own. Five is the
practical limit before the bar becomes a row of unreadable icons.

Screens pushed from More carry **no `NavigationStack` of their own**; nesting
one inside another breaks the back button and the large-title collapse. Tab
roots own theirs.

### Decoding is deliberately lenient

Every model added here has a hand-written `init(from:)` that falls back rather
than throwing (`KeyedDecodingContainer.get(_:_:)` in `Models+Ops.swift`). The
server evolves on its own schedule, and a synthesised decoder turns one
unexpected `null` into a blank screen. Proven by fixtures: `me` from a 0.19
server still decodes, and a severity word this build has never seen reads as
`info`.

## 4. Verification actually performed

There is no Mac and no iOS simulator on this server, so this is what could and
could not be proven.

**Proven, by two scripts that run here**

```bash
./tools/typecheck-core.sh     # real Swift compiler over the Foundation-only layer
./tools/decode-check.sh       # 59 assertions against captured server responses
```

Both run the `swift:6.2-noble` container. `tools/linux-src.sh` assembles the
sources they can build and papers over the two Linux/Darwin Foundation
differences **there** rather than in the app — the shipped code must not carry
scaffolding for its own test rig.

`decode-check` covers:

- **23 real responses** captured from a running server (`tools/fixtures/`, from
  the demo instance, so no secrets), each with a content assertion — decoding
  into all-defaults is the failure mode lenient decoding introduces, so
  "it decoded" alone is not enough.
- **Null and older-server paths**: `docker: null`, `net: null`, `ping: null`,
  an update entry with no catalog metadata, a check with no recommendation,
  `me` from a server that predates the AI fields, and an unknown severity word.
- **The chat protocol**, which is parsed by hand rather than by `Codable`:
  snapshot replay including the live buffer and user ordinals, tool requests,
  run state, errors, unknown frames, garbage input, and every outgoing frame
  against what `sessions.py` switches on.
- **WebSocket URL derivation** (`https→wss`, `http→ws`, port preserved, token
  and extra query items present) and address normalisation.

Also proven: the app icon is 1024×1024 **RGB with no alpha channel** — an alpha
channel is the `ITMS-90717` rejection that the Capacitor pipeline needs a `sips`
round-trip to undo.

**Proven by CI**

- **The whole app compiles.** `ios-native-check` builds it against the iOS
  Simulator SDK, which needs macOS and cannot be checked here.

**Not proven — verify on device**

- **Safe areas on a Face ID device.** See below.
- Every screen's *behaviour*. The models and the protocol are tested; no view
  has been rendered anywhere.

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
- **Five tabs** (Dashboard / Containers / Terminal / Assistant / More) instead
  of the web app's larger nav. Five is the practical limit before the tab bar
  becomes a row of unreadable icons, so everything else lives behind **More**:
  the screens you open on purpose rather than repeatedly.
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
- **The file browser is read-only.** The server exposes only `GET /api/fs` and
  `/api/fs/read`, and that is the right shape: editing a compose file from a
  phone with no diff and no undo is how servers break. Reading one at 3am is how
  they get fixed.
- **Container stats are a one-shot sample, not a live graph.** `docker stats`
  costs the server a full second of CPU-delta sampling per call; polling it from
  a detail screen would be a real cost for a number that barely moves.
- **The assistant shows the tool output collapsed** unless it is short, and
  reasoning collapsed always. Both are long, neither is the answer, and on a
  phone they bury everything else.
- **Auto mode is marked, not hidden.** It runs destructive commands without
  asking. Removing it would be dishonest about what the server can do; the
  picker says what it is and colours it as a warning.
- **`apt` updates are listed but not applied.** The server has no endpoint for
  it, and a half-supervised `apt upgrade` from a phone is worse than none.

## 6. One thing to tighten before this is ever more than a private build

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

Without a Mac, these three run anywhere Docker does and are worth running
before every push:

```bash
python3 tools/swift-sanity.py App   # braces and string literals, in seconds
./tools/typecheck-core.sh           # real compiler over the Foundation layer
./tools/decode-check.sh             # models against captured server responses
```

The `.xcodeproj` is generated and gitignored, so `project.yml` is the source of
truth — edit that, not the Xcode project.
