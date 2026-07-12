# ⎈ Helmsman

**Your server, in your pocket.** An open-source, self-hosted command center for your server —
installable as a mobile app (PWA), with an AI engineer built in.

Born from a simple pain point: *"I can only work on my server via VS Code + SSH from my desk —
I want Claude-Code-style vibe coding, monitoring and one-click app installs from my phone."*

## Features

- **📊 Dashboard** — live CPU / RAM / disk / **internet latency & throughput**; every stat
  tile opens an interactive history graph (2 h in-memory sampling). All Docker containers
  grouped by stack (collapsible), with start / stop / restart / logs and one-tap port links.
- **✦ Vibe Code** — chat with an AI agent that works *directly on your server* via tools:
  `run_command`, `read_file`, `write_file`, `edit_file`, `list_dir`, `search_files`,
  `fetch_url`, `integration_request` and a **persistent memory** it maintains about your server
  (Claude-Code-style, editable under *More → Agent*). Modes: Chat / Plan / Agent / Auto with
  per-action approval, extended-thinking streaming (💭), a stop button, a folder browser to pick
  the workspace, and collapsible tool/output cards. Any command the agent runs has an **“open in
  terminal”** button so you can watch it yourself. Bring your own key — **Anthropic, OpenRouter,
  OpenAI** — or run a model **locally** (see below).
- **🧠 Local AI** — run models *on your own hardware* via **Ollama**. Helmsman auto-detects a
  running Ollama (or connects to your existing container non-destructively / installs one in a
  tap), recommends models that fit your RAM, downloads them with live progress, and wires them
  straight into the chat model picker. Private, free, offline — no cloud key required.
- **💬 Ask AI everywhere** — every update, health check, container, log view, metric and
  app-store error has a one-tap "Ask AI / Fix with AI" button that hands full context to the
  agent. Tips become actions.
- **❯_ Terminal** — a real terminal on your phone (xterm.js + PTY over WebSocket),
  with a mobile key bar (esc/tab/ctrl/arrows) and one-tap `docker exec` into any container.
- **◲ App Store** — 19 curated apps with plain-language "what's in it for me" explanations,
  one-tap installs as clean compose projects — and it **detects apps you already run**
  outside Helmsman (shown as *self-managed* instead of installable).
- **⟳ Updates** — registry digest comparison (no pull needed) with priority classification,
  grouped compactly (available / up-to-date / ignored folds), **Update all**, live job logs
  with heartbeat + post-update health wait, and AI explanations of release notes in *your*
  language. Failed jobs offer *Fix with AI*.
- **♥ Checks** — script-based security & ops checks (SSH hardening, fail2ban, auth.log,
  ports, restarts …) on a schedule, grouped report UI with per-finding *Fix with AI*.
- **🛠 Server settings** — rename your server, change the admin password, edit agent
  memory & workspaces — plus a first-run onboarding wizard.
- **📱 PWA** — add to home screen, dark, fast, offline shell. One codebase, phone + desktop.
- **💬 Live, device-independent sessions** — the agent runs **server-side**, decoupled from the
  connection: close the app or lock your phone and it keeps working; reopen and it’s still
  streaming. Open the **same chat on several devices** at once and watch it type live, send a
  message **while it’s working** (steering / a live queue), and **hand a session off** to another
  device by QR (`/remote`). Multiple chats with archive & delete, slash commands (`/agent`,
  `/auto`, `/terminal`, `/remote` …), and an ⌁ “instruct the agent” button reachable anywhere.
- **🆕 Service detection** — when the agent (or you) brings up a new service, Helmsman spots the
  new container, surfaces it in the chat with one-tap **Open**, **Logs**, and **✦ Finish setup**
  (reverse-proxy + HTTPS + backup) actions.
- **🛰 Sentinel loops** — background AI agents on a schedule (security watch, update watch,
  health digest, or a custom prompt). Findings land under the 🔔 bell and can be **pushed
  to your phone via ntfy** (priority-filtered), each with a "Discuss & fix" hand-off to chat.
- **🔌 Integrations** — connect deSEC / IONOS / GoDaddy / Cloudflare or any generic API once;
  the agent gets an `integration_request` tool with server-side credential injection
  ("add an A record for blog.example.com" just works — the AI never sees your token).
- **↩ Snapshot before update** — before any image update, the running image is pinned as a
  restore point; if the new version misbehaves, **roll back in one tap** (Health → Updates →
  Restore points) and Helmsman recreates the containers on the previous image.
- **🖧 Multi-server & pairing** — manage several servers from one app. Add a server by URL +
  password, or **pair a new device by scanning a QR code** (one-time code, 10-min expiry).
  Switch servers from the header; each keeps its own token on your device.
- **🛰 Sentinel de-duplication** — the same recurring finding is folded into one notification
  with a `×N` counter instead of spamming the bell, and won't buzz your phone again unless it
  changes (a persistent *crit* re-reminds at most once a day).
- **◲ Online app catalog** — the App Store is served from an online catalog, so new apps
  appear without updating Helmsman. Point it at your own JSON to add private apps.
- **🎡 Demo mode** (`HELMSMAN_DEMO=1`) — a public, read-only playground with believable sample
  data and no host access, for showing the whole UI without a real server.

## Quick start

One-liner on a fresh server (installs Docker if needed, prints the admin password):

```bash
curl -fsSL https://raw.githubusercontent.com/maxaufknax/helmsman/main/install.sh | bash
```

Or manually:

```bash
git clone <this repo> && cd helmsman
docker compose up -d --build
docker compose logs helmsman   # shows the generated admin password on first run
```

Prefer a prebuilt image? CI publishes a **multi-arch image (amd64 + arm64)** to
`ghcr.io/maxaufknax/helmsman:latest` on every push — so it runs on a Raspberry Pi or ARM VPS
unchanged. Point the `image:` in `docker-compose.yml` at it and drop `build: .`.

Want to try it first? Spin up the read-only demo (password `demo`):

```bash
docker compose -f docker-compose.demo.yml up -d   # http://<server>:8091
```

Open `http://<your-server>:8090`, sign in, and on your phone use
*Add to Home Screen* to install it as an app.

> **Note:** for camera/clipboard/PWA install on iOS you'll want HTTPS — put Helmsman
> behind your reverse proxy (Caddy/Traefik/nginx) like any other service.

### Configuration (all optional)

| Env | Purpose |
| --- | --- |
| `ADMIN_PASSWORD` | Set your own password (otherwise generated + printed on first run) |
| `AI_PROVIDER` / `AI_API_KEY` / `AI_MODEL` / `AI_BASE_URL` | AI config via env (can also be set in the UI, stored on your server) |
| `HOST_SSH` | `user@host` — adds a *real host shell* option to the terminal (mount your SSH key) |
| `HELMSMAN_WORKDIR` | Working dir for AI tools & terminal (default `/host`) |
| `HELMSMAN_CATALOG_URL` | Override the online App Store catalog (`""` disables remote fetch) |
| `OLLAMA_HOST` | Point Local AI at a specific Ollama endpoint (otherwise auto-detected) |
| `HELMSMAN_DEMO` | `1` = read-only public demo with sample data (password `demo`, no host access) |

### Dev mode (no Docker)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
ADMIN_PASSWORD=dev .venv/bin/uvicorn server.main:app --reload --port 8090
```

Running natively, the terminal and AI agent operate directly on the host and
apt update checks work too.

## Architecture

```
┌─────────────── phone / desktop browser (PWA) ───────────────┐
│  Dashboard · Vibe Code chat · Terminal · App Store · Updates │
└──────────────┬────────────────────┬──────────────────────────┘
               │ REST (token auth)  │ WebSockets (chat, PTY)
┌──────────────┴────────────────────┴──────────────────────────┐
│                    Helmsman container (FastAPI)               │
│  auth · sysinfo(/proc) · docker API (unix socket) · updates  │
│  appstore (compose projects) · AI agent loop (BYO key)       │
└──────┬──────────────────┬───────────────────┬────────────────┘
       │ /var/run/docker.sock   │ /host (ro)  │ https to AI provider
       ▼                  ▼                   ▼   + registries (digest check)
   Docker engine     host filesystem      Anthropic / OpenRouter / …
```

**Security model:** single-admin password (scrypt-hashed), HMAC-signed expiring tokens,
login rate-limiting. The container has the Docker socket (= root-equivalent on the host) —
that is the point of a server manager, the same trust level as Portainer. AI tool calls
require explicit in-chat approval unless you enable auto-mode; the API key never leaves
your server.

## Roadmap

- [x] Background loops (Sentinel: security/update/health watchers + ntfy push) — v0.4
- [x] DNS/API integrations with server-side credential injection — v0.4
- [x] Persistent multi-chat conversations — v0.4
- [x] Security hardening: TOTP 2FA, token revocation, append-only audit log — v0.5
- [x] Snapshot-before-update + one-tap rollback — v0.7
- [x] Multi-server support + QR device pairing — v0.7
- [x] CI multi-arch image (amd64 + arm64) + read-only demo mode — v0.7
- [x] **Device-independent live sessions** — agent runs server-side, streams to every device,
  survives disconnects, steering + queue, chat handoff — **v0.8**
- [x] **Local AI** — run models on your own hardware via Ollama, RAM-aware recommendations — **v0.8**
- [x] **Service detection** — spot new containers the agent brings up and help finish setup — **v0.8**
- [ ] Native mobile app shell (Capacitor) with push notifications for updates/alerts
- [ ] Domain / reverse-proxy automation: choose "reachable at sub.domain.tld" at install
  time, Helmsman wires up the proxy + DNS (script first, AI agent as fallback)
- [ ] Backups: scheduled, verifiable snapshots of volumes + configs (biggest gap)
- [ ] Service integrations: read Grafana/Portainer/Uptime-Kuma APIs and render them natively
- [ ] Scheduled update auto-apply + notification digest
- [ ] Agent skills (self-created runbooks à la hermes-agent/agentskills.io)
- [ ] SSH-only bootstrap: enter host + domain + API keys in the app, Helmsman installs
  itself on the server over SSH (Termius-style onboarding)
- [ ] Premium hosted AI option (no own API key needed) — the open-source core stays free

## Contributing

Issues and PRs welcome. The stack is deliberately dependency-light: a single FastAPI app
(`server/`) plus a vanilla-JS PWA (`web/`, xterm.js vendored) — no build step, no framework.
`docker compose up -d --build` and you're running the whole thing.

## License

[MIT](LICENSE) © Maximilian Paasch
