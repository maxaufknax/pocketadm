# Changelog

All notable changes to Helmsman. Versions are the app version reported at
`/api/info` and shown in *More → About*.

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
