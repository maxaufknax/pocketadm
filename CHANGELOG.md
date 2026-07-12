# Changelog

All notable changes to Helmsman. Versions are the app version reported at
`/api/info` and shown in *More → About*.

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
