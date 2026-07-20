# Changelog

All notable changes to PocketADM. Versions are the app version reported at
`/api/info` and shown in *Settings → About*.

## v0.19.0 — Don't get owned

PocketADM is a root-on-host gateway by design (Docker socket, the whole
filesystem, a real host shell). That trust model is honest and documented — but
it means the app has to be loud about the one deployment mistake that turns it
into a liability: hanging open on the public internet with only a password in
front of it. This release makes that mistake visible and hard to make by
accident, and hardens the last line of defense.

- **Public-exposure warning.** When the app is reached over an internet-facing
  host and no second factor is set, a persistent red banner now says so
  plainly — *anyone who guesses the password gets root* — and right after
  onboarding a gate forces a conscious choice: turn on 2FA (≈20 seconds with
  any authenticator) or explicitly accept the risk. Enabling 2FA clears it;
  the acknowledgement is remembered so it never nags twice. 2FA was always
  implemented — now the app actually pushes you to use it when it matters.
- **Login rate-limit is now persistent and stricter.** The brute-force
  limiter used to live only in memory, so every `docker restart` handed an
  attacker a fresh set of attempts. Failures are now stored on disk and
  survive restarts, and for a root gateway the threshold is tighter: 5 tries
  in 5 minutes trips a cool-off, 15 in an hour a longer ban. The public demo
  stays deliberately lenient.
- **Docs/CI:** clarified that the release pipeline ships to TestFlight only and
  never auto-submits to App Review.

## v0.18.1 — Mistral, and a hidden Haiku

- **Mistral as a fourth AI provider.** Add a key under *Settings → AI providers*
  and its models (mistral-large, magistral, codestral, ministral, …) show up
  in the Vibe model picker alongside Anthropic/OpenRouter/OpenAI — same
  tool-calling agent loop, since Mistral's API is OpenAI-compatible.
- **Fixed: Claude Haiku missing from the model picker.** The live Anthropic
  model list was truncated to the first 20 entries in API order (newest
  snapshot first), and with enough dated/legacy models on the account, Haiku
  fell past the cutoff and simply never appeared. Curated picks (Sonnet,
  Opus, Haiku) are now pinned to the front of the list regardless of raw API
  order, and the cutoff was raised to 40.

## v0.18.0 — The agent knows your server

Real chat transcripts showed where the time went: the agent spent 25 commands
(and half a million tokens) rediscovering facts the app already knew — which
compose stack a container belongs to, where its sources live — then asked for a
tap before every single `docker ps`. This release closes that gap.

- **Server map.** A live, auto-generated snapshot of the server — host facts,
  every compose stack with its host directory and services, standalone
  containers — is injected into the agent's system prompt. It stops exploring
  and starts acting. Fully visible (and switchable) under *Settings → Agent →
  Server knowledge*: what the agent knows is exactly what you can read there.
- **Skills.** Reusable how-to recipes (`/data/skills/*.md`), hermes-agent
  style: the agent reads the matching skill before a task and saves new ones
  after working out something non-obvious — so the next session starts smart.
  Three starter skills ship (compose deploys, web-service debugging, disk
  cleanup); view, edit, add or delete them in Settings. New tools:
  `read_skill`, `save_skill`.
- **Commands run on the host now.** `run_command` executes on the host as root
  (throwaway chroot helper — the same trusted door the terminal's host shells
  use), with real paths like `/srv/…`, a writable filesystem, and `docker
  compose` that resolves relative mounts correctly. File tools read and write
  host paths directly; new files inherit the folder's owner instead of
  becoming root's. `where:"app"` still targets the app container.
- **Read-only commands skip the approval tap.** A conservative classifier
  (`cmdpolicy.py`) recognises inspection commands — `docker ps/logs/inspect`,
  `ls`, `df`, `du`, `grep`, plus `docker exec` when the inner command is
  read-only — and runs them immediately in Agent and Plan mode, marked with an
  **auto** chip in chat and still written to the audit log. Anything mutating
  asks first, exactly as before. Toggle under *Settings → Agent → Autonomy*.
- **Prompt caching.** Anthropic (and Claude-via-OpenRouter) requests now set
  cache breakpoints, so each agent iteration re-reads the conversation at ~10%
  of the token price instead of full price. Cache reads/writes are folded into
  the usage numbers and priced correctly.
- **Desktop layout is fluid.** Views were capped at 1160px and the chat column
  at 860px — on a 1920px screen the app sat in a box unless you zoomed to
  140%. The Vibe chat now scales with the window (~77–85% of the pane, capped
  at 1400px) and the other views widen to 1440px.

## v0.17.0 — It stops feeling like a web page

Five things gave the app away as a website wearing an app's icon. They are the
kind of detail nobody names — you just come away thinking it's *a bit off*.

- **Fixed: haptics did nothing on iPhone.** Every tap was meant to answer with a
  small tick. None of them did. The plugin was in the binary the whole time; the
  app was looking for it in the wrong place. `Capacitor.Plugins` is filled in by
  each plugin's JavaScript package as it gets imported — and this app has no
  bundler and imports nothing, so that shelf was simply bare. It now asks the
  bridge for the plugin directly. iOS has no vibration API to fall back on, so
  there was nothing to notice failing: the phone just sat there. *Settings →
  Appearance* now states, on the device, whether the Taptic engine is connected —
  the one question no test on a desktop can answer.
- **Tabs switch instantly.** They used to cross-fade for 220ms. iOS tab bars cut
  straight to the next tab; the fade was the loudest tell in the app. Motion is
  now reserved for hierarchy, where it means something.
- **Settings is a page you push, not a tab you land on.** It slides in from the
  right off the gear, the bar turns into *‹ Home · Settings*, and it slides back
  out the way it came. **Swipe from the left edge** to go back — the gesture you
  try without being told, and whose absence reads as "this is a web page". A
  swipe that starts away from the edge still belongs to the content.
- **The tab bar stays where you left it.** It used to hide as you scrolled down
  and come back as you scrolled up. That is *browser* behaviour — Safari does it,
  iOS apps do not, and a tab bar that runs from your thumb is not a feature. It
  also animated a layout property on every direction flip, relaying out the whole
  shell mid-scroll. The keyboard is now the only thing that takes it away, since
  there it earns the room for the composer.
- **A tap outside a text field puts the keyboard away.** There was no way out of
  one: the grey *‹ › Done* accessory bar is deliberately off (it looks like a web
  form, which is the thing we're avoiding) and an iOS field has no Return that
  dismisses. Every field in Settings was a trap. Controls stay exempt — tapping
  one either moves focus itself or, like *send*, means to keep the keyboard up.
- **Home: the summary rows have a name and some air.** *56 of 58 running* and its
  two neighbours sat flush against the metric tiles, unlabelled — neither the
  grid nor the rows carried a margin between them. They are now **At a glance**,
  set off the tiles the way **Latest findings** is set off them.

## v0.16.0 — Home is a home again, and the header clears the notch

- **Fixed: the header hid under the Dynamic Island.** v0.15 gave the top bar to
  the Server tab alone, but the top bar was the only thing reserving the strip
  iOS keeps for the notch — so on Vibe, Term, Health and Apps the first row
  climbed underneath the hardware, and Vibe's *Chats* / *New* buttons were
  unreachable. Only the native app could show this: in a browser that inset is
  zero. The shell now keeps a backdrop where the top bar was, so every view
  starts below the status bar and collapses it to nothing on a device with no
  inset.
- **Home is back, and Settings moved behind a gear.** Merging Home into Settings
  meant the app opened on a settings form. The first tab is **Home** again: the
  vitals you had before, then a doorway into whatever needs you. Settings are
  one tap away on the gear, top right — a page you visit and leave, not a tab.
- **Latest findings on Home.** What the background agents actually found now
  greets you instead of hiding behind a bell you had to think to press.
  Nothing to report, nothing shown.
- **Denser, in the places that were costing you a screen.** The vitals are three
  across instead of two (five numbers, two rows, no scrolling). Apps lost a
  redundant heading and a lede and now carries its counts in the filters
  themselves. Update cards fit their actions on one row — *ignore* moved into
  the ⋯ sheet with the other ways to apply it. Health and Apps got a compact
  sticky page header instead of the top bar.
- **Fixed: the demo's Terminal tab showed an error.** It asked the server for a
  session first, which the read-only guard refused, so a visitor met *"couldn't
  open a session"* over an empty black rectangle — and that was the App Store
  screenshot. The demo never needed one: its shell is simulated.

## v0.15.0 — One server, five tabs, and reports you can actually read

- **Home and More are gone; Server took their place.** Once the vitals moved to
  Health and the services moved to Apps, Home had nothing of its own left and
  two "meta" tabs sat side by side. There are now five tabs — **Server · Vibe ·
  Term · Health · Apps** — and Server opens with a glance (vitals, what's
  running, updates, checks) where every row is a doorway to the tab that owns
  the detail, followed by every setting.
- **Services and the App Store are one tab.** "What is on my server" and "what
  can I put on my server" were two halves of one question living on different
  tabs. Apps now has *On this server* (everything running, grouped by what it
  does, in plain language) and *Add new* (the store).
- **The vitals live on Health.** CPU / RAM / disk / internet sit above the
  updates and checks that explain them. Tiles still open their history graphs.
- **The top bar only appears on Server.** Terminal, Vibe, Apps and Health get
  those pixels back. The notification count moves onto the Server tab so
  nothing raised while you're elsewhere goes unseen.
- **The tab bar hides as you scroll down** a long list and comes back the
  moment you scroll up.
- **Pull-to-refresh actually tracks your finger.** It had a permanent CSS
  transition on it, so every touch move animated *toward* your finger with an
  overshoot curve instead of following it — the wobble. Its opacity was also
  computed as a length, which is invalid, so the browser dropped it. It is now
  a ring that fills as you pull, on Health, Apps and Server.
- **Background agent reports are readable.** The Sentinel loops recorded only
  a verdict; the run itself was thrown away, so "your SSH is exposed" was a
  claim you had to take on faith, and the only thing you could do with it was
  chat to a *different* agent about it. Loops now record every step they take,
  and **Read report** shows the finding, the write-up, and each command with its
  output — plus *Run again*.
- **A terminal with no session shows a launcher**, not a dead black rectangle:
  the shells still running server-side (walk back into one, or end it), the
  places worth one tap, and a way to the file browser.
- **Three ways to apply an update**, matching how the rest of the app works: let
  it do it (snapshot, pull, recreate, health-check), do it yourself in the
  terminal, or hand it to the agent. The terminal option offers the real
  `docker compose pull && up -d` for compose-managed stacks — in a *host* shell,
  because running compose from our container would resolve the stack's relative
  bind mounts under `/host`.
- **OpenRouter's free model router.** `openrouter/free` costs nothing and still
  supports tool calls, so the agent works on it. Free models were previously
  filtered out entirely; they now get their own group in the model picker, and
  only tool-capable ones are offered — a free model that can't call tools is a
  dead end here.
- Fixed: the public demo served a months-old image because
  `docker-compose.demo.yml` paired `build: .` with a registry `image:` tag that
  stopped being published when the repo was renamed — so `up -d` silently reused
  a stale pull. The demo image is now a local build artifact.
- Rebrand leftovers: the README pointed at `ghcr.io/maxaufknax/helmsman`, which
  froze at the last pre-rename build (GHCR, unlike git, does not redirect), so
  "prefer a prebuilt image?" handed people v0.8.

## v0.14.0 — Terminal sessions that survive, and graphs that explain themselves

- **Persistent terminal sessions.** The shell now lives on the *server*
  (like the Vibe chats) — websockets only attach to it. Lock your phone,
  switch to the laptop, come back: the session is still there, scrollback
  replays, and several devices can watch the same session live. Claude Code
  or a long build keeps running while the app is closed. The picker
  (toolbar title) lists open sessions first — attach, or end them — then
  everywhere a new shell can open. Sessions end on server restart; the UI
  says so.
- **Launch coding agents where you mean.** *Launch* on Claude Code / Codex /
  Mistral Vibe now asks **who runs it** (PocketADM's box or a real host
  login like `maxaufknax@stream`) and **which folder it starts in**
  (defaults to your workspace) — no more landing as the app user inside
  PocketADM's own home.
- **Metric graphs grew up.** New 6 h / 24 h / 7 d ranges backed by a
  5-minute long-history that survives restarts (7 days, persisted to the
  data volume). KPIs now show low/high next to current/average; the legend
  is color-keyed; the crosshair tooltip carries the date on long ranges.
- **Scrub haptics.** Dragging a finger across a graph ticks softly as the
  crosshair snaps from sample to sample — and taps firmer when it lands on
  an anomaly marker.
- **Anomaly markers explain themselves.** The red/blue dots are now
  tappable: PocketADM pulls the Docker engine events and its own action log
  from around that moment ("nextcloud restarted", "update applied …") and
  shows them under the graph — plus *Ask AI what caused this*, prefilled
  with the anomaly and those events.
- **Remove anything.** Containers and whole service units can be removed
  from their detail sheets — including self-managed ones, not just App
  Store installs (with a clear explanation that named volumes stay).
  PocketADM refuses to remove itself.
- **Services on Home, tidied.** Colored running/issues summary in the
  header, a proper search field, sticky group headers, status as a badge on
  the service icon, one clean port chip and a chevron — cards read calmer
  and tap better.
- **Pull-to-refresh** on Home, with the arming tick and spinner you expect
  from a native app; over-scroll chaining and browser bounce are gone
  app-wide.
- **Keyboard & composer.** While typing, the mode/model strip and plan
  panel fold away so the conversation gets every pixel; tapping Send no
  longer steals focus (the keyboard stays up between messages).
- **Native QR scanning.** The pairing scanner uses the official Capacitor
  barcode plugin in the iOS/Android shell (`BarcodeDetector` doesn't exist
  in WKWebView); the browser/PWA keeps the web path.
- **Desktop rail fix.** The active tab no longer shows a stray transparent
  square around its icon — the rail row highlight is the indicator.
- Fixes: `docker events` queries are clamped to the past (a future bound
  made the engine hold the request open); PTY children are reaped (no more
  zombie processes from closed shells); connection dot moved into the
  server switcher.

## v0.13.0 — It feels like a phone app now

The whole release is about one thing: PocketADM on a phone should feel as
good as ChatGPT or Claude — especially the keyboard, the composer and the
way the app responds to your fingers.

- **Native bridge (`native.js`).** A small platform layer that wires up the
  Capacitor shell — keyboard events, status bar, Taptic engine — with clean
  fallbacks for the browser/PWA (visualViewport keyboard tracking,
  `navigator.vibrate`). The web app itself never talks to Capacitor.
- **The keyboard finally behaves.** Opening the keyboard slides the tab bar
  away so the composer sits directly on top of the keyboard (in the native
  app the webview resizes with it — no more covered input). The transcript
  keeps the newest message in view, and dragging the chat dismisses the
  keyboard, exactly like the big chat apps. Text fields are 16px on touch
  devices so iOS never zoom-jumps into a focused field; the native shell
  additionally pins the viewport zoom.
- **A real composer.** Attach, input and send now live in one soft pill that
  grows with your message (up to ~5 lines). The send circle is dimmed while
  empty, springs in when there's something to send — and morphs into a red
  stop button while the agent is working (typing a steer flips it back).
- **Haptics, everywhere it matters.** A barely-there tick on every control,
  a slightly firmer tap on tab switches and toggles, and real feedback at the
  moments that count: message sent, approval requested, Allow tapped, agent
  finished (success), checkpoint reached (warning), error (buzz). Tunable
  under *More → Appearance*; fully respects the new toggle and never fires
  on desktops.
- **Status bar & keyboard match the theme.** Light text over dark themes,
  dark over Daybreak — updated live when you switch, including the iOS
  keyboard appearance and the Android status-bar color.
- **Touch ergonomics.** Allow/Deny are now full-width, thumb-sized buttons;
  message actions, mode buttons and top-bar icons got honest hit areas; tap
  highlights, long-press callouts and accidental text selection on chrome are
  gone. A floating "jump to latest" bubble appears when you scroll up.
- **Android ready.** Hardware back closes modals / returns Home before
  minimizing, and the app reconnects its live streams when foregrounded.

## v0.12.1 — Visual polish, everywhere

- **App-wide design refresh** (pure CSS layer, all five themes): ambient
  theme-aware depth glow behind every screen, springy tab-bar with a glowing
  active pill, gentle cross-fades between views, gradient metric bars with a
  soft glow, satisfying press feedback on every button, message/tool-card
  entrance animations in the chat, a glassy login card, blurred modal backdrop
  with a spring slide-up, and input focus rings. Fully respects
  `prefers-reduced-motion`.
- **pocketadm.com relaunch of the hero**: the phone mockup is now a faithful,
  *interactive* recreation of the real app — five screens (Home, Vibe Code,
  Terminal, Health, Apps) with their own animations: metrics count up and bars
  fill, the agent run plays out step by step (plan → tools → a pulsing Allow
  that gets tapped → result), the terminal *types live* as
  `maxaufknax@stream:~$` and launches `claude`, the Redis update runs and
  finishes, app rows rise in. 3D cursor tilt with a moving glare, swipe to
  switch screens on touch, auto-rotation that stops when you interact.
  Fixed the landing container serving a stale named volume instead of the
  bind-mounted `public/` directory.

## v0.12.0 — A terminal you can actually use on a phone

- **Real host-user shells.** The terminal can now open a genuine login shell as
  any of your host's Linux users — e.g. `maxaufknax@stream` — not just the app's
  own container. This is what lets you run the exact commands the agent suggests
  (files in your home, `sudo`, the host's `docker`). It uses the same audited
  chroot-into-host door the account manager already uses; no new trust boundary.
- **A clear, grouped "Open a shell" picker** (`/api/terminal/targets`) replaces
  the long flat dropdown: *This server* (the PocketADM app box + host logins)
  and *Service containers*, each with an icon, name and one-line description.
- **`claude` / `codex` now actually launch.** The base image's `/etc/profile`
  reset `PATH` for login shells and dropped `~/.local/bin`, so installed agents
  were "command not found". The persistent HOME now ships a `.bash_profile`
  that restores the path (and a calm `pocketadm:` prompt via `.bashrc`).
- **Mistral Vibe CLI** joins Claude Code and Codex in *Terminal → Agents*
  (`vibe`, Devstral-powered; sign in with a Mistral account or API key).
- **Mobile copy/paste.** A dedicated **Paste** button (with a long-press
  fallback sheet when the browser blocks clipboard reads), plus **Copy
  selection** in the overflow menu.
- **Redesigned, phone-first terminal toolbar.** The Agent/Reconnect buttons no
  longer run off-screen: a flexible target picker + compact icon buttons +
  an overflow menu. **Simple / Advanced display modes** (Advanced adds control
  keys: ^D ^Z ^R ^L ^A ^E, Home/End, PgUp/PgDn, `|`, `~`) and a text-size
  control. The terminal also **auto-reconnects** when you reopen the app.
- **See what the agent changes to files.** `write_file` / `edit_file` tool
  cards now show a **+added / −removed** stat and an expandable, colored diff
  — like a code review — for live turns and on reload.
- **"What is this?" streams live.** The explainer now shows the answer forming
  token-by-token (so you can tell it's working) and offers **Continue in chat**,
  which carries the explanation into a Vibe chat as context.

## v0.11.0 — Polish for release: edit messages, richer updates, backups, coding CLIs

- **Edit & retract sent messages** (Claude-Code-style rewind): tap any of your
  messages → *Edit* (rewinds the chat to that point and resends your new
  version) or *Rewind* (takes the message back into the composer). Works across
  devices — the server truncates the shared history.
- **Name your chats**: tap the title in the chat header, or the ✎ in the chat
  list. Renames sync live to every connected device.
- **Extended thinking got effort tiers**: off → low → medium → high, cycling on
  the 💭 button — mapped to each provider's real knob (Anthropic thinking
  budgets, OpenRouter/OpenAI reasoning effort). The button only appears for
  models that support it, and the default is now **off**.
- **Quick-instruct (⌁) grew up**: pick Agent/Auto/Plan, and an optional
  *Model & thinking* fold with the full model list — without leaving the sheet.
- **Updates tell you much more**: installed version (from the image's OCI
  label) and build date on every row, plus a **Details** sheet per app with the
  latest upstream releases and their notes (GitHub), links, and one-tap update.
- **Checks got smarter and broader**: unused-image/orphaned-volume detection,
  PocketADM's own security posture (2FA, failed logins), backup-tooling
  detection — and a new **Agent tasks** group that surfaces open permission
  requests and background-agent findings so nothing the agent hit mid-session
  gets forgotten.
- **App Store more than doubled**: 43 curated apps (Immich, Pi-hole, wg-easy,
  ntfy, Duplicati, Mealie, Actual Budget, BookStack, Umami, Netdata, SearXNG,
  Open WebUI, …), each with plain-language what/why. Every install dialog now
  offers **“Set up with AI instead”** — the agent asks about ports, storage,
  reverse proxy and backups, then installs and verifies.
- **Backups**: More → Backup exports everything PocketADM knows (settings,
  keys, chats, memory, app definitions, audit log) as one archive — and
  restores it. The Checks tab nags about missing *data* backups separately.
- **Claude Code & Codex in the terminal**: Terminal → *Agents* installs
  Anthropic's/OpenAI's coding CLIs onto the data volume (they and their logins
  survive updates). Sign in with your existing Claude Pro/Max or ChatGPT
  subscription — no API key needed. The local shell now uses a persistent HOME.
- **Fixes**: “What is this?” buttons no longer fail with *bad action* (route
  shadowing); the dead gap between composer/terminal key bar and the tab bar is
  gone (double safe-area inset); QR handoff button uses a real QR icon.

## v0.10.0 — Services, not containers: a unified Home

- **Home groups by what things *do*, not by Docker plumbing.** A homeserver runs
  dozens of containers, but most belong together — `nextcloud` + `nextcloud-db` +
  `nextcloud-redis` + `nextcloud-cron` are *one* service. Home now folds containers
  into **service units** (one user-facing app plus its database/cache/worker
  dependencies) and files each under a **functional category** (Files & Sync,
  Media, Monitoring, Passwords…). A 56-container box that used to render as one
  giant "docker" list of 50+ rows now reads as ~30 tidy, categorised services.
  Distinct apps that merely share a compose project (grafana + prometheus in a
  `monitoring` stack) stay separate; multi-container apps with default
  `project-service-N` names still fold correctly.
- **Three groupings, one tap apart.** *Function* (default, by category), *Stack*
  (by Docker compose project) and *Raw* (every container, unfolded) — full
  technical transparency is always one switch away.
- **Service detail = the whole app.** Opening a service shows a rolled-up state,
  aggregate **Start/Stop/Restart all**, every published port, its App Store
  linkage (open · uninstall · website · mobile clients), AI helpers for the whole
  service, and the list of member containers — tap any to drop into the full
  per-container view.
- **Home ↔ App Store, de-duplicated.** Installed apps in the store get a **Manage**
  button that jumps straight to their unified service view in Home; the service
  view links back to the store. Locally-built images get clean names
  (`docker-ops-api` → *Ops Api*) instead of raw image strings.

## v0.9.0 — Store track, explorer, context & a lighter More tab

- **Cold-start Connect screen.** When the app isn't served by a server (the native
  client shell, or any device with no server behind it), PocketADM now opens a
  first-run Connect screen: add a server by address + password, scan a pairing QR,
  or set up a brand-new server. The server-hosted PWA still shows the normal login.
- **SSH bootstrap.** Install PocketADM onto another Linux machine over SSH, straight
  from a server you're already signed into. The installer runs remotely and streams
  its log; the resulting URL + admin password are captured so the fresh server is one
  tap away in your list. Credentials are used once and never stored (`bootstrap.py`,
  `POST /api/bootstrap/ssh`).
- **Capacitor client scaffold.** `client/` packages the PWA as a native iOS/Android
  app for the App Store / Play Store — the UI is unchanged, the shell just bundles it
  and boots into the Connect screen. See `client/README.md`.
- **File Explorer.** Browse your server's files (within the allowed workspace roots)
  from the Terminal tab: navigate folders, preview text files, jump into the terminal
  at any path, or attach a file/folder to a chat. Backed by an extended `/api/fs`
  (now returns file entries) and a new `/api/fs/read`.
- **Attach context to chats.** Instead of stuffing prompts, attach real things — a
  service/container, an app, a file or folder, or the server overview — as first-class
  context shown to the agent (chips above the composer, `＋` button). App and container
  cards get a direct "Attach to chat".
- **Compacted More tab.** The long settings scroll is now grouped behind a compact
  chip nav (Server · Security · AI · Agent · Automation · Look) that shows one section
  at a time.
- **Apps & Services filters.** The App Store gets search + category chips (incl.
  Installed); the Services list gets All/Running/Issues + a live search.

## v0.8.0 — Watch it, keep working, run it locally

- **Device-independent live sessions.** The agent now runs as a server-side task
  per chat, decoupled from the WebSocket: closing the app or locking the phone no
  longer kills the work. The same chat streams live to every connected device,
  reconnects replay finished turns plus the in-flight turn, and the chat WS
  auto-reconnects on focus.
- **Steering + queue.** Send a message *while the agent is working* — it's injected
  mid-turn or queued for the next one. Dedicated stop button; the send button always
  sends.
- **Local AI (Ollama).** Auto-detect a running Ollama, connect to an existing
  container non-destructively (joins its Docker network, survives redeploys) or
  install one in a tap. RAM-aware model recommendations, downloads with live
  progress, and local models wired straight into the chat picker. No cloud key needed.
- **Service detection.** New containers the agent brings up are surfaced in the chat
  with Open / Logs / ✦ Finish setup actions.
- **Open commands in the terminal.** Every `run_command` tool card can drop its
  command onto the terminal prompt to run and watch yourself.
- **Instruct anywhere + slash commands.** An ⌁ button sends an instruction to the
  agent from any screen; `/agent /auto /plan /chat /terminal /remote /new /help`.
- **Remote handoff.** `/remote` (or the 🔗 button) shows a QR/deep-link that opens
  the exact live chat on another device and keeps streaming.

## v0.7.0 — Snapshots, multi-server, demo & CI

- Snapshot-before-update with one-tap rollback (restore points).
- Multi-server support with QR device pairing; per-server tokens.
- Sentinel de-duplication (folded findings, `×N`, no repeat push spam).
- Online app catalog (remote-served, override with your own JSON).
- Read-only demo mode (`HELMSMAN_DEMO=1`) with believable sample data.
- CI: multi-arch image (amd64 + arm64) published to GHCR.

## v0.6.0 — Settings & server management

- Real server identity + Linux user management (create, lock, admin, password).
- AI usage charts (per-model breakdown, 7/30/90-day ranges).
- Custom instructions separate from auto-memory; per-tool on/off switches.
- Default workspace picker; friendlier Sentinel and integration cards.

## v0.5.0 — Security hardening

- TOTP 2FA (stdlib), token revocation via generation counter, "sign out others".
- Append-only audit log of every action, including the AI's tool calls.

## v0.4.0 — Persistent chats, Sentinel, integrations

- Server-persisted multi-chat conversations with archive/delete.
- Sentinel background loops (security / updates / health / custom) + ntfy push.
- DNS/API integrations with server-side credential injection (`integration_request`).

## v0.3.0 — Agent tools, memory & proactive UI

- More agent tools (`edit_file`, `search_files`, `fetch_url`, `update_memory`) and a
  persistent memory file injected into every prompt.
- Thinking streaming, stop button, workspace browser.
- "Ask AI / Fix with AI" buttons across updates, checks, containers, logs, metrics.

## v0.2.0 — Modes, updates, health

- Chat / Plan / Agent / Auto modes, per-provider model picker, token/cost tracking.
- Background update jobs with live logs and rollback; script-based health checks.

## v0.1.0 — MVP

- Dashboard, container management, Vibe Code agent, web terminal, app store.
