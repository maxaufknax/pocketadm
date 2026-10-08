# PocketADM

**Your server, in your pocket.** An open-source, self-hosted command center for your server: a native iPhone app ([App Store](https://apps.apple.com/app/pocketadm/id6790731797)) and a web app, with an AI engineer built in — your own API key, a local model, or the Claude Code / Codex subscription you already have.

Born from a simple pain point: *"I can only work on my server via VS Code + SSH from my desk. I want Claude-Code-style vibe coding, monitoring, one-click app installs and understanding of my whole server from my phone."*

## Features

- **Dashboard:** live CPU / RAM / disk / **internet latency & throughput** of the host's real
  network interfaces and disks; history graphs for CPU and memory, network, disk I/O and the
  internet connection (latency and outages) over an hour, a day or a week.

- **Apps, not container lists:** containers that belong together are shown as one app
  (Authentik = server + worker + database + cache; Synapse + Element = Matrix), each container
  with a role and a name you can tell apart. Start, stop or restart a whole app in a working
  order; every container has live usage, a shell, its processes, recent events, health-check
  output, ports and networks, masked environment, restart policy and a live log tail.

- **The watch:** an AI agent that keeps an eye on the server like a colleague — rounds every
  few hours, an investigation within minutes when something breaks (a crashed container, a
  failed unit, a filling disk, the internet dropping), a weekly look back — and writes only
  when it is worth knowing: a short message with the details one tap away and the next step as
  a button. Its messages form a **conversation** in the app: ask it anything ("why is the disk
  so full?"), or tell it to be quiet about a topic or to pause, and it answers there. Quiet
  hours, daily limits, muted topics, pauses and a monthly budget are enforced in code; it can
  only read. Messages arrive as **push notifications** on the iPhone, and optionally in ntfy
  and a Matrix room (Element).

- **AI accounts:** connect your **Claude, ChatGPT or Mistral subscription from the phone** —
  PocketADM drives the provider's own CLI sign-in (Claude Code's `setup-token`, Codex's device
  login, Mistral Vibe's delegated sign-in) — or add API keys; then choose which AI runs the
  assistant, the watch and the explanations.

- **Activity, live:** what happens on the server — Docker events, SSH logins and failed
  attempts, sudo, apt, kernel out-of-memory kills, drives plugged in, systemd failures, internet
  outages — and, as its own half, everything done in and through PocketADM.

- **Files like an editor's explorer:** opens on `/` (or the folders a server allows), folders
  unfold in place, every drive on top with how full it is. Text files open in an editor (the
  previous version is kept for an undo, and a file changed meanwhile is never overwritten),
  everything else in a preview; upload from Files or Photos, create, rename, move, copy, unpack,
  download a folder as a zip, change permissions, delete — system folders and PocketADM's own
  credentials stay protected.

- **Update notes that say what changes:** the installed and the new version (read from the
  image's labels or its own `*_VERSION` variables), only the upstream releases in between —
  release candidates left out, a rebuild of the same version called one — and an AI summary of
  just those changes with the risk and a recommendation.
  
- **Coding agents on your subscription:** run **Claude Code**, **Codex** or **Mistral Vibe**
  from the Assistant / Vibe chat, signed in with your own Claude, ChatGPT or Mistral plan — no
  API key. The CLI does the work on your server; PocketADM streams it to every device, turns
  each of its permission questions into an approval tap on your phone, and applies its own
  rules on top (reads that stay on the box run, anything touching the internet asks). Connect
  them under *More → AI accounts* and pick them in the model menu.

- **✦ Vibe Code:** chat with an AI agent that works *directly on your server* via tools:
  `run_command`, `read_file`, `write_file`, `edit_file`, `list_dir`, `search_files`,
  `fetch_url`, `integration_request` and a **persistent memory** it maintains about your server
  (Claude-Code-style, editable under *Settings → Agent*). Modes: Chat / Plan / Agent / Auto with
  per-action approval, extended-thinking streaming (💭), a stop button, a folder browser to pick
  the workspace, and collapsible tool/output cards. Any command the agent runs has an **“open in
  terminal”** button so you can watch it yourself. Bring your own key — **Anthropic, OpenRouter,
  OpenAI:** or run a model **locally** (see below).

- **Local AI:** run models *on your own hardware* via **Ollama**. PocketADM auto-detects a
  running Ollama (or connects to your existing container non-destructively / installs one in a
  tap), recommends models that fit your RAM, downloads them with live progress, and wires them
  straight into the chat model picker. Private, free, offline — no cloud key required.

- **Ask AI everywhere:** every update, health check, container, log view, metric and
  app-store error has a one-tap "Ask AI / Fix with AI" button that hands full context to the
  agent. Tips become actions.

- **Terminal:** a real terminal on your phone (xterm.js + PTY over WebSocket),
  with a mobile key bar (esc/tab/ctrl/arrows) and one-tap `docker exec` into any container.

- **App Store:** 45+ curated apps with plain-language "what's in it for me" explanations,
  one-tap installs as clean compose projects — and it **detects apps you already run**
  outside PocketADM (shown as *self-managed* instead of installable).

- **Updates:** registry digest comparison (no pull needed) with priority classification,
  grouped compactly (available / up-to-date / ignored folds), **Update all**, live job logs
  with heartbeat + post-update health wait, and AI explanations of release notes in *your*
  language. Failed jobs offer *Fix with AI*.

- **Health:** script-based security & ops checks (SSH hardening, fail2ban, auth.log, ports,
  restarts, backups incl. systemd timers …) on a schedule, as a 0–100 score with five areas,
  a plain explanation and one-tap actions per finding, and findings you accept on purpose.

- **Server settings:** rename your server, change the admin password, edit agent
  memory & workspaces — plus a first-run onboarding wizard.

- **iPhone app:** native SwiftUI client in the App Store — pair by scanning the QR the
  installer prints, real terminal emulator, live charts, approvals for the agent, single
  sign-on. The web app (PWA) keeps working in any browser, phone or desktop.

- **Live, device-independent sessions:** the agent runs **server-side**, decoupled from the
  connection: close the app or lock your phone and it keeps working; reopen and it’s still
  streaming. Open the **same chat on several devices** at once and watch it type live, send a
  message **while it’s working** (steering / a live queue), and **hand a session off** to another
  device by QR (`/remote`). Multiple chats with archive & delete, slash commands (`/agent`,
  `/auto`, `/terminal`, `/remote` …), and an ⌁ “instruct the agent” button reachable anywhere.

- **Service detection:** when the agent (or you) brings up a new service, PocketADM spots the
  new container, surfaces it in the chat with one-tap **Open**, **Logs**, and **✦ Finish setup**
  (reverse-proxy + HTTPS + backup) actions.

- **Sentinel loops:** background AI agents on a schedule (security watch, update watch,
  health digest, or a custom prompt). Findings land under the 🔔 bell and can be **pushed
  to your phone via ntfy** (priority-filtered), each with a "Discuss & fix" hand-off to chat.

- **Integrations:** connect deSEC / IONOS / GoDaddy / Cloudflare or any generic API once;
  the agent gets an `integration_request` tool with server-side credential injection
  ("add an A record for blog.example.com" just works — the AI never sees your token).

- **Snapshot before update:** before any image update, the running image is pinned as a
  restore point; if the new version misbehaves, **roll back in one tap** (Health → Updates →
  Restore points) and PocketADM recreates the containers on the previous image.

- **Multi-server & pairing:** manage several servers from one app. Add a server by URL +
  password, or **pair a new device by scanning a QR code** (one-time code, 10-min expiry).
  Switch servers from the header; each keeps its own token on your device.

- **Sentinel de-duplication:** the same recurring finding is folded into one notification
  with a `×N` counter instead of spamming the bell, and won't buzz your phone again unless it
  changes (a persistent *crit* re-reminds at most once a day).

- **Online app catalog:** the App Store is served from an online catalog, so new apps
  appear without updating PocketADM. Point it at your own JSON to add private apps.

- **Demo mode** (`HELMSMAN_DEMO=1`) — a public, read-only playground with believable sample
  data and no host access, for showing the whole UI without a real server.

## Quick start

One line on a fresh server (Linux with Docker, or it installs Docker for you):

```bash
curl -fsSL https://raw.githubusercontent.com/maxaufknax/pocketadm/main/install.sh | bash
```

It ends with a **QR code**. Open the [PocketADM app](https://apps.apple.com/app/pocketadm/id6790731797),
tap *Scan pairing code*, done — signed in over HTTPS, without a domain:

- **No domain (default):** PocketADM serves HTTPS on port **8443** with its own certificate.
  The QR carries the fingerprint of that certificate's key, and the app trusts exactly that key
  — the scan *is* the trust decision. A browser warns once about the certificate; the app does not.
- **`--domain pocketadm.example.com`:** Caddy gets a Let's Encrypt certificate (ports 80/443
  free, DNS pointing at the server). Everything trusts it.
- **`--behind-proxy https://pocketadm.example.com`:** you run a reverse proxy already;
  PocketADM stays on `127.0.0.1:8090` and the QR uses your address.

```bash
curl -fsSL https://raw.githubusercontent.com/maxaufknax/pocketadm/main/install.sh | bash -s -- --domain pocketadm.example.com
```

A second phone later: `sudo docker exec helmsman python -m server.cli pair`. Re-running the
installer updates an existing install and keeps its settings (`.env`).

Or manually:

```bash
git clone https://github.com/maxaufknax/pocketadm.git && cd pocketadm
docker compose up -d --build
docker compose logs helmsman   # shows the generated admin password on first run
```

Prefer a prebuilt image? CI publishes a **multi-arch image (amd64 + arm64)** to
`ghcr.io/maxaufknax/pocketadm` — so it runs on a Raspberry Pi or ARM VPS unchanged. Point the
`image:` in `docker-compose.yml` at it and drop `build: .`.

| Tag | What you get |
| --- | --- |
| `:latest` | the newest commit on `main` |
| `:0.25.0` | that exact release (versioned tags exist from v0.23.0 on) — **pin this** if you want to choose when to move |
| `:0.23` | the newest 0.23.x patch |

Pinning a version is the honest default for a server tool: `:latest` means a `docker compose
pull` can change your admin panel underneath you.

Want to try it first? Spin up the read-only demo (password `demo`):

```bash
docker compose -f docker-compose.demo.yml up -d   # http://<server>:8091
```

Without the installer, the app port is bound to `127.0.0.1:8090` on purpose: it is a root shell
on the host and must not answer the open internet in plain HTTP. Publish HTTPS with
`HELMSMAN_TLS_BIND=0.0.0.0` (port 8443, own certificate, pair by QR), or put your reverse proxy
(Caddy/Traefik/nginx) in front of `127.0.0.1:8090`.

### Configuration (all optional)

| Env | Purpose |
| --- | --- |
| `ADMIN_PASSWORD` | Set your own password (otherwise generated + printed on first run) |
| `AI_PROVIDER` / `AI_API_KEY` / `AI_MODEL` / `AI_BASE_URL` | AI config via env (can also be set in the UI, stored on your server) |
| `HOST_SSH` | `user@host` adds a *real host shell* option to the terminal (mount your SSH key) |
| `HELMSMAN_WORKDIR` | Working dir for AI tools & terminal (default `/host`) |
| `HELMSMAN_CATALOG_URL` | Override the online App Store catalog (`""` disables remote fetch) |
| `OLLAMA_HOST` | Point Local AI at a specific Ollama endpoint (otherwise auto-detected) |
| `HELMSMAN_DEMO` | `1` = read-only public demo with sample data (password `demo`, no host access) |

### Single sign-on (Authentik, Authelia, Keycloak …)

*Settings → Security → Single sign-on* adds a **"Sign in with Authentik"** button next to
the password form, the way Nextcloud does it. It works with any OpenID Connect provider:
Authentik, Authelia, Keycloak, Pocket ID, Zitadel and others. Password and 2FA keep working,
so an unreachable provider can never lock you out.

1. In the provider, create an OAuth2/OpenID application as a **confidential** client. Register
   the redirect URI the settings screen shows (`https://<your-host>/api/auth/oidc/callback`).
   In Authentik, add a policy binding so only your admin group gets in.
2. Enter the issuer URL (in Authentik: `https://auth.example.com/application/o/<slug>/`),
   client ID, client secret and **who may sign in**: groups or usernames. The allow-list is
   required. PocketADM is root on the host, so it does not rely on the provider's access rules
   alone. Email addresses are never matched, because many providers let users change their own.

Under the hood this is the authorization code flow with PKCE, a nonce, and state bound to
the browser that started the sign-in. The provider's MFA applies instead of PocketADM's 2FA.

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
│                    PocketADM container (FastAPI)             │
│  auth · sysinfo(/proc) · docker API (unix socket) · updates  │
│  appstore (compose projects) · AI agent loop (BYO key)       │
└──────┬──────────────────┬───────────────────┬────────────────┘
       │ /var/run/docker.sock   │ /host (ro)  │ https to AI provider
       ▼                  ▼                   ▼   + registries (digest check)
   Docker engine     host filesystem      Anthropic / OpenRouter / …
```

**Security model:** single-admin password (scrypt-hashed), HMAC-signed expiring tokens,
login rate-limiting, optional TOTP 2FA and optional single sign-on through an OpenID Connect
provider (see above). The container has the Docker socket (= root-equivalent on the host) —
that is the point of a server manager, the same trust level as Portainer. So:

- **The agent asks before it acts.** In Agent mode only read-only commands run without a tap,
  and "read-only" includes *stays on this server*: anything that talks to the internet — `curl`,
  `wget`, `dig`, `ping` to a public host, `fetch_url` — asks first, because a URL or a DNS name
  can carry a secret as well as a POST body can. Plan/Chat cannot change anything; Auto runs
  everything, by your choice. PocketADM's own credentials are not readable by the agent.
- **Sentinel loops only look** — enforced in code, not just in the prompt: read-only, local
  commands, no `fetch_url`.
- **Credentials stay out of URLs and logs.** WebSockets connect with a single-use ticket that
  expires in 30 seconds; the 30-day token never appears in a proxy log.
- **The web UI runs under a Content-Security-Policy** without inline script, and model output
  is rendered with everything escaped.
- API keys never leave your server; Claude Code and Codex use their own logins on the server.

## Roadmap

- [x] Background loops (Sentinel: security/update/health watchers + ntfy push) — v0.4
- [x] DNS/API integrations with server-side credential injection — v0.4
- [x] Persistent multi-chat conversations — v0.4
- [x] Security hardening: TOTP 2FA, token revocation, append-only audit log — v0.5
- [x] Snapshot-before-update + one-tap rollback — v0.7
- [x] Multi-server support + QR device pairing — v0.7
- [x] CI multi-arch image (amd64 + arm64) + read-only demo mode — v0.7
- [x] **Device-independent live sessions** so agent runs server-side, streams to every device,
  survives disconnects, steering + queue, chat handoff — **v0.8**
- [x] **Local AI** — run models on your own hardware via Ollama, RAM-aware recommendations — **v0.8**
- [x] **Service detection** spot new containers the agent brings up and help finish setup — **v0.8**
- [x] **Single sign-on** via OpenID Connect (Authentik, Authelia, Keycloak …) next to password + 2FA — **v0.22**
- [x] **Native iPhone app** (SwiftUI) in the App Store, pairing by QR with a pinned certificate — **2.0 / v0.23**
- [x] **HTTPS from the first minute**: installer with own certificate or Let's Encrypt + pairing QR — **v0.23**
- [x] **Claude Code & Codex as agent engines** on your own subscription — **v0.23**
- [x] Agent skills (self-created runbooks à la hermes-agent/agentskills.io)
- [x] **The watch** (an agent that writes only when it is worth knowing), **AI accounts**
  (subscriptions connected from the phone), **Mistral Vibe** engine, apps view, live activity,
  drives — **2.0 / v0.24**
- [x] **The watch as a conversation**, push notifications through a relay that never sees
  which server a phone belongs to, files you can manage, reconnecting chats — **2.0 / v0.25**
- [ ] Domain / reverse-proxy automation: choose "reachable at sub.domain.tld" at install
  time, PocketADM wires up the proxy + DNS (script first, AI agent as fallback)
- [ ] Backups: scheduled, verifiable snapshots of volumes + configs (biggest gap)
- [ ] Service integrations: read Grafana/Portainer/Uptime-Kuma APIs and render them natively
- [ ] Scheduled update auto-apply + notification digest
- [ ] SSH-only bootstrap: enter host + domain + API keys in the app, PocketADM installs
  itself on the server over SSH (Termius-style onboarding)
- [ ] Premium hosted AI option (no own API key needed) — the open-source core stays free

## Contributing

Issues and PRs welcome. The stack is deliberately dependency-light: a single FastAPI app
(`server/`) plus a vanilla-JS PWA (`web/`, xterm.js vendored) no build step, no framework.
`docker compose up -d --build` and you're running the whole thing.

## License

[MIT](LICENSE) © Maximilian Paasch
