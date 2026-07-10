# ⎈ Helmsman

**Your server, in your pocket.** An open-source, self-hosted command center for your server —
installable as a mobile app (PWA), with an AI engineer built in.

Born from a simple pain point: *"I can only work on my server via VS Code + SSH from my desk —
I want Claude-Code-style vibe coding, monitoring and one-click app installs from my phone."*

## Features

- **📊 Dashboard** — live CPU / RAM / disk / uptime, all Docker containers with
  start / stop / restart / logs, one tap to open exposed ports.
- **✦ Vibe Code** — chat with an AI agent that works *directly on your server* via tools
  (run commands, read/write files). Every action needs your approval — or flip on
  auto-mode. Bring your own API key: **Anthropic (Claude), OpenRouter, or any
  OpenAI-compatible endpoint**. No key? Everything else still works.
- **❯_ Terminal** — a real terminal on your phone (xterm.js + PTY over WebSocket),
  with a mobile key bar (esc/tab/ctrl/arrows) and one-tap `docker exec` into any container.
- **◲ App Store** — one-click deploys for popular self-hosted apps (Portainer, Grafana,
  Uptime Kuma, Vaultwarden, Jellyfin, Navidrome, Nextcloud, Gitea, code-server, n8n, …)
  with friendly config forms. Each app is a clean docker-compose project.
- **⟳ Updates** — detects newer Docker images by comparing registry digests (no pull needed)
  plus host apt packages. Tap **✦ Explain** and the AI researches release notes and explains
  the update in plain language — in *your* language.
- **📱 PWA** — add to home screen, dark, fast, offline shell. One codebase, phone + desktop.

## Quick start

```bash
git clone <this repo> && cd helmsman
docker compose up -d --build
docker compose logs helmsman   # shows the generated admin password on first run
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

- [ ] Native mobile app shell (Capacitor) with push notifications for updates/alerts
- [ ] Service integrations: read Grafana/Portainer/Uptime-Kuma APIs and render them natively
- [ ] Update auto-apply (compose pull + recreate) with scheduled checks + notification digest
- [ ] Multi-server support (one app, many helmsmen)
- [ ] SSH-only mode (manage a server with nothing installed on it)
- [ ] Premium hosted AI option (no own API key needed) — the open-source core stays free

## License

MIT
