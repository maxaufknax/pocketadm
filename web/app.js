/* Helmsman SPA */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const state = {
  token: localStorage.getItem("helmsman_token") || "",
  view: "dashboard",
  healthView: "updates",
  aiConfigured: false,
  me: null,
  chatWs: null,
  chatMode: "agent",
  chatModel: "",       // "provider|model"
  chatWorkdir: "",
  termWs: null,
  term: null,
  fitAddon: null,
  ctrlArmed: false,
  dashTimer: null,
  streamEl: null,
  streamRaw: "",
  containers: [],
  updates: null,
  updFilter: "all",
  svcTechnical: false,
};

/* ---------------------------------------------------------- helpers */

async function api(path, opts = {}) {
  const res = await fetch("/api" + path, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + state.token,
      ...(opts.headers || {}),
    },
  });
  if (res.status === 401) { logout(); throw new Error("unauthorized"); }
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}

function wsUrl(path, params = {}) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const qs = new URLSearchParams({ token: state.token, ...params });
  return `${proto}://${location.host}${path}?${qs}`;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    node.append(c.nodeType ? c : document.createTextNode(c));
  }
  return node;
}

function fmtBytes(b) {
  if (!b && b !== 0) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
  return b.toFixed(b >= 100 || i === 0 ? 0 : 1) + " " + units[i];
}

function fmtUptime(s) {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function fmtTokens(n) {
  return n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n);
}

function timeAgo(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return "just now";
  if (s < 5400) return Math.round(s / 60) + " min ago";
  if (s < 129600) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " d ago";
}

/* Minimal markdown: code fences, inline code, bold, headings, links, lists */
function renderMarkdown(text) {
  const escape = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const parts = text.split(/```(\w*)\n?/);
  let html = "";
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 1) {
      html += `<pre><code>${escape(parts[i + 1] || "")}</code></pre>`;
      i++;
    } else {
      let t = escape(parts[i]);
      t = t.replace(/`([^`\n]+)`/g, "<code>$1</code>")
           .replace(/^#{1,4} (.+)$/gm, "<b>$1</b>")
           .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
           .replace(/^[-*] (.+)$/gm, "• $1")
           .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
                    '<a href="$2" target="_blank" rel="noopener">$1</a>');
      html += t;
    }
  }
  return html;
}

/* ---------------------------------------------------------- modal */

function openModal(title, bodyNode) {
  $("#modal-title").textContent = title;
  const body = $("#modal-body");
  body.innerHTML = "";
  body.append(bodyNode);
  $("#modal").classList.remove("hidden");
}
function setModalBody(node) {
  $("#modal-body").innerHTML = "";
  $("#modal-body").append(node);
}
function closeModal() { $("#modal").classList.add("hidden"); }
$("#modal-close").addEventListener("click", closeModal);
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });

/* ---------------------------------------------------------- auth */

function logout() {
  localStorage.removeItem("helmsman_token");
  state.token = "";
  location.reload();
}

async function tryAuth() {
  if (!state.token) return false;
  try {
    const me = await api("/me");
    state.me = me;
    $("#host-name").textContent = me.hostname;
    state.aiConfigured = me.ai_configured;
    $("#vibe-no-ai").classList.toggle("hidden", me.ai_configured);
    return true;
  } catch { return false; }
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#login-error");
  err.classList.add("hidden");
  try {
    const res = await fetch("/api/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: $("#login-password").value }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || "Login failed");
    state.token = (await res.json()).token;
    localStorage.setItem("helmsman_token", state.token);
    await boot();
  } catch (ex) {
    err.textContent = ex.message;
    err.classList.remove("hidden");
  }
});

/* ---------------------------------------------------------- router */

function showView(name) {
  state.view = name;
  $$(".view").forEach((v) => v.classList.toggle("hidden", v.id !== "view-" + name));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === name));
  if (name === "dashboard") refreshDashboard();
  if (name === "terminal") initTerminal();
  if (name === "apps") loadApps();
  if (name === "health") loadHealth();
  if (name === "settings") loadSettings();
  if (name === "vibe") { initVibeControls(); setTimeout(() => $("#chat-input").focus(), 50); }
}
$$(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));

/* ---------------------------------------------------------- dashboard */

async function refreshDashboard() {
  try {
    const s = await api("/system");
    $("#conn-dot").className = "dot ok";
    $("#stat-cpu").textContent = s.cpu_percent + "%";
    $("#stat-mem").textContent = s.memory.percent + "%";
    $("#stat-disk").textContent = s.disk.percent + "%";
    $("#stat-uptime").textContent = fmtUptime(s.uptime);
    $("#stat-load").textContent = "load " + s.load.map((x) => x.toFixed(2)).join(" ");
    for (const [id, pct] of [["cpu", s.cpu_percent], ["mem", s.memory.percent], ["disk", s.disk.percent]]) {
      const bar = $("#bar-" + id);
      bar.style.width = pct + "%";
      bar.classList.toggle("hot", pct > 85);
    }
    state.containers = await api("/containers");
    renderServices();
  } catch (e) {
    $("#conn-dot").className = "dot bad";
  }
  clearTimeout(state.dashTimer);
  if (state.view === "dashboard") state.dashTimer = setTimeout(refreshDashboard, 6000);
}

const GROUP_LABELS = { "": "Standalone" };

function stackLabel(project) {
  if (!project) return "Standalone";
  if (project.startsWith("helmsman-")) return "⎈ Installed via Helmsman";
  return project;
}

function renderServices() {
  const list = state.containers;
  $("#container-count").textContent =
    `${list.filter((c) => c.state === "running").length}/${list.length} running`;
  const wrap = $("#service-list");
  wrap.innerHTML = "";

  if (state.svcTechnical) {
    const cards = el("div", { class: "cards" });
    for (const c of list) cards.append(serviceCard(c, true));
    wrap.append(cards);
    return;
  }

  const groups = new Map();
  for (const c of list) {
    const key = stackLabel(c.compose_project);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(c);
  }
  const sorted = [...groups.entries()].sort((a, b) => {
    if (a[0] === "Standalone") return 1;
    if (b[0] === "Standalone") return -1;
    return b[1].length - a[1].length;
  });
  for (const [label, containers] of sorted) {
    const running = containers.filter((c) => c.state === "running").length;
    const group = el("div", { class: "svc-group" },
      el("div", { class: "svc-group-head" },
        el("span", {}, label),
        el("span", { class: "count" }, `${running}/${containers.length}`)),
      el("div", { class: "svc-cards" },
        ...containers.map((c) => serviceCard(c, false))));
    wrap.append(group);
  }
}

function serviceCard(c, technical) {
  const svc = c.service || { label: c.name, icon: "📦", category: "" };
  const title = technical ? c.name : svc.label + (multiOfKind(c) ? ` · ${c.name}` : "");
  const sub = technical
    ? `${c.image} · ${c.status}`
    : `${svc.category}${c.compose_service && c.compose_service !== c.name ? " · " + c.compose_service : ""} · ${c.status.replace("Up ", "up ")}`;
  const chips = [];
  if (c.health === "unhealthy") chips.push(el("span", { class: "chip", style: "color:var(--danger)" }, "unhealthy"));
  for (const p of c.ports.slice(0, 3)) {
    chips.push(el("a", {
      class: "chip", href: `http://${location.hostname}:${p.public}`, target: "_blank",
      onclick: (e) => e.stopPropagation(),
    }, `:${p.public}`));
  }
  return el("div", { class: "card svc-card", onclick: () => showContainerDetail(c) },
    el("div", { class: "card-row" },
      el("span", { class: "svc-icon" }, svc.icon),
      el("div", { style: "min-width:0;flex:1" },
        el("div", { class: "card-title" },
          el("span", { class: "state-dot " + c.state, style: "display:inline-block;margin-right:7px" }),
          title),
        el("div", { class: "card-sub" }, sub)),
      el("div", { class: "svc-meta" }, ...chips)));
}

function multiOfKind(c) {
  const label = c.service?.label;
  return state.containers.filter((x) => x.service?.label === label).length > 1;
}

async function showContainerDetail(c) {
  const svc = c.service || { label: c.name, icon: "📦" };
  openModal(`${svc.icon} ${svc.label}`, el("div", { class: "thinking" }, "loading"));
  let d;
  try { d = await api(`/containers/${c.id}/detail`); }
  catch (e) { setModalBody(el("div", { class: "error" }, e.message)); return; }

  const upd = state.updates?.docker?.find((u) => u.image === d.image);
  const body = el("div", {});

  // action row
  const actions = el("div", { class: "card-actions", style: "margin:0 0 12px" });
  const act = async (action) => {
    try { await api(`/containers/${c.id}/${action}`, { method: "POST" }); }
    catch (e) { alert(`${action} failed: ${e.message}`); }
    closeModal(); refreshDashboard();
  };
  if (d.state === "running") {
    actions.append(
      el("button", { class: "btn small", onclick: () => act("restart") }, "restart"),
      el("button", { class: "btn small", onclick: () => act("stop") }, "stop"));
  } else {
    actions.append(el("button", { class: "btn small primary", onclick: () => act("start") }, "start"));
  }
  actions.append(el("button", { class: "btn small", onclick: () => showLogs(c) }, "logs"));
  if (upd?.update_available) {
    actions.append(el("button", {
      class: "btn small primary",
      onclick: () => { closeModal(); startUpdateJob(upd); },
    }, "⬆ update available"));
  }
  body.append(actions);

  const grid = el("dl", { class: "detail-grid" });
  const row = (k, v) => { if (v || v === 0) grid.append(el("dt", {}, k), el("dd", {}, String(v))); };
  row("Container", d.name);
  row("Image", d.image);
  row("State", d.state + (d.health ? ` (${d.health})` : ""));
  if (d.started_at && d.state === "running")
    row("Started", timeAgo(new Date(d.started_at).getTime() / 1000));
  row("Restart policy", d.restart_policy || "none");
  if (d.restart_count) row("Restarts", d.restart_count);
  if (d.privileged) row("Privileged", "yes ⚠");
  row("Networks", d.networks.join(", "));
  row("Env vars", d.env_count);
  if (d.labels["com.docker.compose.project"]) {
    row("Compose project", d.labels["com.docker.compose.project"]);
    row("Compose service", d.labels["com.docker.compose.service"]);
  }
  body.append(grid);

  if (c.ports.length) {
    body.append(el("div", { class: "card-sub", style: "margin-bottom:4px" }, "Published ports"));
    const pr = el("div", { class: "card-actions", style: "margin:0 0 10px" });
    for (const p of c.ports) {
      pr.append(el("a", { class: "chip", target: "_blank",
        href: `http://${location.hostname}:${p.public}` },
        `${p.public} → ${p.private}/${p.type}${p.ip && p.ip !== "0.0.0.0" ? " (" + p.ip + ")" : ""}`));
    }
    body.append(pr);
  }
  if (d.mounts.length) {
    body.append(el("div", { class: "card-sub" }, "Mounts"));
    body.append(el("ul", { class: "mount-list" },
      ...d.mounts.slice(0, 8).map((m) =>
        el("li", {}, `${m.source || m.type} → ${m.dest}${m.rw ? "" : " (ro)"}`))));
  }
  // live stats
  const statsLine = el("div", { class: "muted", style: "margin-top:10px" }, "…");
  body.append(statsLine);
  api(`/containers/${c.id}/stats`).then((s) => {
    statsLine.textContent =
      `CPU ${s.cpu_percent}% · RAM ${fmtBytes(s.mem_usage)}${s.mem_limit ? " / " + fmtBytes(s.mem_limit) : ""}`;
  }).catch(() => { statsLine.textContent = ""; });

  setModalBody(body);
}

async function showLogs(c) {
  openModal(`Logs · ${c.name}`, el("pre", {}, "loading…"));
  try {
    const r = await api(`/containers/${c.id}/logs?tail=300`);
    const pre = el("pre", {}, r.logs || "[no output]");
    setModalBody(pre);
    pre.scrollTop = pre.scrollHeight;
  } catch (e) { setModalBody(el("div", { class: "error" }, e.message)); }
}

$("#svc-viewmode").addEventListener("click", () => {
  state.svcTechnical = !state.svcTechnical;
  $("#svc-viewmode").classList.toggle("primary", state.svcTechnical);
  renderServices();
});

/* ---------------------------------------------------------- vibe chat */

let vibeControlsReady = false;

async function initVibeControls() {
  if (vibeControlsReady) return;
  vibeControlsReady = true;
  // workspaces
  const wsSel = $("#chat-workspace");
  wsSel.innerHTML = "";
  for (const w of state.me?.workspaces || []) {
    wsSel.append(el("option", { value: w }, "📁 " + w.replace(/^\/host/, "") || "/"));
  }
  state.chatWorkdir = wsSel.value || "";
  // models
  try {
    const { providers, default: def } = await api("/ai/models");
    const sel = $("#chat-model");
    sel.innerHTML = "";
    for (const p of providers) {
      const og = el("optgroup", { label: p.provider });
      for (const m of p.models) {
        og.append(el("option", { value: `${p.provider}|${m.id}` }, m.name || m.id));
      }
      sel.append(og);
    }
    const wanted = `${def.provider}|${def.model}`;
    if ([...sel.options].some((o) => o.value === wanted)) sel.value = wanted;
    else if (def.provider && def.model) {
      sel.append(el("option", { value: wanted }, `${def.model} (default)`));
      sel.value = wanted;
    }
    state.chatModel = sel.value;
  } catch {}
  sendChatConfig();
}

function sendChatConfig() {
  const [provider, ...rest] = (state.chatModel || "|").split("|");
  sendChat({
    type: "config",
    mode: state.chatMode,
    provider: provider || undefined,
    model: rest.join("|") || undefined,
    workdir: state.chatWorkdir || undefined,
  });
}

$$("#mode-seg button").forEach((b) => b.addEventListener("click", () => {
  $$("#mode-seg button").forEach((x) => x.classList.toggle("active", x === b));
  state.chatMode = b.dataset.mode;
  sendChatConfig();
}));
$("#chat-model").addEventListener("change", function () {
  state.chatModel = this.value;
  sendChatConfig();
});
$("#chat-workspace").addEventListener("change", function () {
  state.chatWorkdir = this.value;
  sendChatConfig();
});
$("#chat-reset").addEventListener("click", () => {
  sendChat({ type: "reset" });
  $("#chat-log").innerHTML = "";
  $("#usage-row").classList.add("hidden");
});

function chatConnect() {
  if (state.chatWs && state.chatWs.readyState <= 1) return;
  const ws = new WebSocket(wsUrl("/ws/chat"));
  state.chatWs = ws;
  ws.onopen = () => sendChatConfig();
  ws.onmessage = (ev) => handleChatEvent(JSON.parse(ev.data));
  ws.onclose = () => { state.chatWs = null; };
}

function chatLogEl() { return $("#chat-log"); }
function scrollChat() { const log = chatLogEl(); log.scrollTop = log.scrollHeight; }
function removeThinking() { $$(".thinking").forEach((n) => n.remove()); }

function handleChatEvent(ev) {
  const log = chatLogEl();
  if (ev.type === "text") {
    removeThinking();
    if (!state.streamEl) {
      state.streamEl = el("div", { class: "msg assistant" });
      state.streamRaw = "";
      log.append(state.streamEl);
    }
    state.streamRaw += ev.delta;
    state.streamEl.innerHTML = renderMarkdown(state.streamRaw);
    scrollChat();
  } else if (ev.type === "tool_request" || ev.type === "tool_start") {
    removeThinking();
    state.streamEl = null;
    let card = document.getElementById("tool-" + ev.id);
    if (!card) {
      card = el("div", { class: "tool-card", id: "tool-" + ev.id },
        el("div", { class: "tool-name" }, "⚙ " + ev.name),
        el("pre", {}, ev.name === "run_command" ? ev.args.command
          : ev.name === "write_file" ? ev.args.path
          : JSON.stringify(ev.args, null, 1).slice(0, 800)));
      log.append(card);
    }
    card.querySelector(".approve-row")?.remove();
    if (ev.type === "tool_request") {
      card.append(el("div", { class: "approve-row" },
        el("button", {
          class: "btn small primary",
          onclick: () => { sendChat({ type: "approve", id: ev.id, approved: true }); },
        }, "Allow"),
        el("button", {
          class: "btn small danger",
          onclick: () => { sendChat({ type: "approve", id: ev.id, approved: false }); },
        }, "Deny")));
    } else {
      card.classList.add("approved");
    }
    scrollChat();
  } else if (ev.type === "tool_result") {
    const card = document.getElementById("tool-" + ev.id);
    if (card) {
      card.querySelector(".approve-row")?.remove();
      card.classList.add("approved");
      card.append(el("div", { class: "tool-output" },
        ev.output.length > 1500 ? ev.output.slice(0, 1500) + " …" : ev.output));
    }
    log.append(el("div", { class: "thinking" }, "working"));
    scrollChat();
  } else if (ev.type === "usage") {
    const t = ev.turn, s = ev.session;
    const cost = (c) => c == null ? "" : ` · $${c < 0.01 ? c.toFixed(4) : c.toFixed(3)}`;
    $("#usage-row").classList.remove("hidden");
    $("#usage-text").textContent =
      `turn ${fmtTokens(t.input)}→${fmtTokens(t.output)} tok${cost(t.cost)}   |   ` +
      `session ${fmtTokens(s.input)}→${fmtTokens(s.output)} tok` +
      (s.cost ? ` · ~$${s.cost.toFixed(3)}` : "");
  } else if (ev.type === "done") {
    removeThinking();
    state.streamEl = null;
    $("#chat-send").disabled = false;
  } else if (ev.type === "error") {
    removeThinking();
    state.streamEl = null;
    log.append(el("div", { class: "msg error" }, "⚠ " + ev.message));
    $("#chat-send").disabled = false;
    scrollChat();
  }
}

function sendChat(obj) {
  chatConnect();
  const trySend = () => {
    if (!state.chatWs) return;
    if (state.chatWs.readyState === 1) state.chatWs.send(JSON.stringify(obj));
    else setTimeout(trySend, 120);
  };
  trySend();
}

function submitChat() {
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text) return;
  $(".chat-welcome")?.remove();
  chatLogEl().append(el("div", { class: "msg user" }, text));
  chatLogEl().append(el("div", { class: "thinking" }, "thinking"));
  scrollChat();
  input.value = "";
  input.style.height = "auto";
  $("#chat-send").disabled = true;
  sendChat({ type: "user", text });
}

$("#chat-send").addEventListener("click", submitChat);
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !("ontouchstart" in window)) {
    e.preventDefault(); submitChat();
  }
});
$("#chat-input").addEventListener("input", function () {
  this.style.height = "auto";
  this.style.height = Math.min(this.scrollHeight, 120) + "px";
});

/* ---------------------------------------------------------- terminal */

async function initTerminal() {
  await loadTermContexts();
  if (state.term && state.termWs && state.termWs.readyState <= 1) {
    state.fitAddon.fit();
    return;
  }
  connectTerminal();
}

async function loadTermContexts() {
  const sel = $("#term-context");
  if (sel.dataset.loaded) return;
  try {
    const containers = state.containers.length ? state.containers : await api("/containers");
    for (const c of containers.filter((x) => x.state === "running")) {
      sel.append(el("option", { value: "container:" + c.id },
        `${c.service?.icon || "🐳"} ${c.name}`));
    }
    sel.dataset.loaded = "1";
  } catch {}
}

function connectTerminal() {
  if (state.termWs) { try { state.termWs.close(); } catch {} }
  if (!state.term) {
    state.term = new Terminal({
      fontSize: 13.5,
      fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
      theme: { background: "#0d1117", foreground: "#e6edf3", cursor: "#4da3ff" },
      cursorBlink: true,
      allowProposedApi: true,
    });
    state.fitAddon = new FitAddon.FitAddon();
    state.term.loadAddon(state.fitAddon);
    state.term.open($("#terminal"));
    state.term.onData((data) => {
      if (state.ctrlArmed && data.length === 1) {
        const code = data.toUpperCase().charCodeAt(0) - 64;
        if (code >= 1 && code <= 26) data = String.fromCharCode(code);
        setCtrl(false);
      }
      state.termWs?.readyState === 1 &&
        state.termWs.send(JSON.stringify({ type: "input", data }));
    });
    state.term.onResize(({ cols, rows }) => {
      state.termWs?.readyState === 1 &&
        state.termWs.send(JSON.stringify({ type: "resize", cols, rows }));
    });
    new ResizeObserver(() => { if (state.view === "terminal") state.fitAddon.fit(); })
      .observe($("#terminal"));
  }
  state.term.reset();
  const ctx = $("#term-context").value;
  const ws = new WebSocket(wsUrl("/ws/terminal", { context: ctx }));
  state.termWs = ws;
  ws.onopen = () => {
    state.fitAddon.fit();
    ws.send(JSON.stringify({ type: "resize", cols: state.term.cols, rows: state.term.rows }));
  };
  ws.onmessage = (ev) => state.term.write(ev.data);
  ws.onclose = () => state.term.write("\r\n\x1b[90m[disconnected — tap Reconnect]\x1b[0m\r\n");
}

function setCtrl(on) {
  state.ctrlArmed = on;
  $("#key-ctrl").classList.toggle("on", on);
}

$("#term-reconnect").addEventListener("click", connectTerminal);
$("#term-context").addEventListener("change", connectTerminal);
$$(".key-bar button").forEach((btn) => btn.addEventListener("click", () => {
  if (btn.dataset.mod === "ctrl") { setCtrl(!state.ctrlArmed); return; }
  let seq = btn.dataset.seq;
  if (btn.dataset.key === "Escape") seq = "\x1b";
  if (btn.dataset.key === "Tab") seq = "\t";
  state.termWs?.readyState === 1 &&
    state.termWs.send(JSON.stringify({ type: "input", data: seq }));
  state.term?.focus();
}));

/* ---------------------------------------------------------- health */

function loadHealth() {
  if (state.healthView === "updates") loadUpdates();
  else loadChecks();
}

$$("#health-seg button").forEach((b) => b.addEventListener("click", () => {
  $$("#health-seg button").forEach((x) => x.classList.toggle("active", x === b));
  state.healthView = b.dataset.hview;
  $("#health-updates").classList.toggle("hidden", state.healthView !== "updates");
  $("#health-checks").classList.toggle("hidden", state.healthView !== "checks");
  loadHealth();
}));

/* ----- updates ----- */

async function loadUpdates(force = false) {
  const summary = $("#updates-summary");
  summary.textContent = "checking…";
  try {
    state.updates = await api("/updates" + (force ? "?force=true" : ""));
    renderUpdates();
  } catch (e) {
    summary.textContent = "⚠ " + e.message;
  }
}

function renderUpdates() {
  const r = state.updates;
  if (!r) return;
  const list = $("#updates-list");
  const summary = $("#updates-summary");
  list.innerHTML = "";
  const avail = r.docker.filter((u) => u.update_available && !u.ignored);
  summary.textContent = avail.length
    ? `${avail.length} update${avail.length > 1 ? "s" : ""} available`
    : "everything up to date ✓";
  updateHealthBadge();

  const filtered = r.docker.filter((u) => {
    if (state.updFilter === "updates") return u.update_available && !u.ignored;
    if (state.updFilter === "uptodate") return !u.update_available && !u.ignored && !u.error;
    if (state.updFilter === "ignored") return u.ignored;
    return !u.ignored || state.updFilter === "all";
  });

  for (const u of filtered) list.append(updateCard(u));
  if (!filtered.length) {
    list.append(el("div", { class: "card muted" }, "nothing here"));
  }

  if (r.apt.available && r.apt.packages.length) {
    $("#apt-section").classList.remove("hidden");
    const aptList = $("#apt-list");
    aptList.innerHTML = "";
    for (const p of r.apt.packages.slice(0, 40)) {
      aptList.append(el("div", { class: "card" },
        el("div", { class: "card-row" },
          el("span", { class: "app-icon", style: "font-size:18px" }, "📦"),
          el("div", { style: "min-width:0" },
            el("div", { class: "card-title" }, p.package),
            el("div", { class: "card-sub" }, `${p.current} → ${p.new}`))),
        el("div", { class: "card-actions" },
          el("button", { class: "btn small", onclick: () => explainUpdate(p.package, "apt") }, "✦ Explain"))));
    }
  } else {
    $("#apt-section").classList.add("hidden");
  }
}

function updateCard(u) {
  const chips = [el("span", { class: "chip" }, u.tag)];
  if (u.age_days != null) chips.push(el("span", { class: "chip" }, `image ${u.age_days}d old`));
  chips.push(el("span", { class: "chip" }, u.used_by.join(", ")));

  const actions = el("div", { class: "card-actions" });
  if (u.update_available && !u.ignored) {
    actions.append(el("button", {
      class: "btn small primary", onclick: () => startUpdateJob(u),
    }, "⬆ Update"));
    actions.append(el("button", {
      class: "btn small", onclick: () => explainUpdate(u.image, "docker"),
    }, "✦ Explain"));
  }
  if (u.links?.changelog) {
    actions.append(el("a", { class: "chip", href: u.links.changelog, target: "_blank" }, "changelog"));
  }
  if (u.links?.hub) {
    actions.append(el("a", { class: "chip", href: u.links.hub, target: "_blank" }, "registry"));
  }
  actions.append(el("button", {
    class: "btn small", style: "margin-left:auto",
    onclick: async function () {
      await api("/updates/ignore", { method: "POST", body: JSON.stringify({ image: u.image, ignored: !u.ignored }) });
      loadUpdates();
    },
  }, u.ignored ? "unignore" : "ignore"));

  const statusIcon = u.ignored ? "🔕" : u.update_available ? "🔔" : u.error ? "◌" : "✓";
  const sub = u.ignored ? "ignored" :
    u.update_available ? `Update available (${u.current_digest.slice(7, 15)} → ${u.remote_digest.slice(7, 15)})`
    : u.error || "Up to date";

  return el("div", { class: "card" },
    el("div", { class: "card-row" },
      el("span", { class: "svc-icon" }, u.icon || "📦"),
      el("div", { style: "min-width:0;flex:1" },
        el("div", { class: "card-title" }, `${u.label} `,
          u.update_available && !u.ignored
            ? el("span", { class: "prio " + (u.priority || "low") }, u.priority || "low") : null),
        el("div", { class: "card-sub" }, `${statusIcon} ${sub}`),
        el("div", { class: "card-sub", style: "opacity:.75" }, u.image)),
    ),
    el("div", { class: "card-actions" }, ...chips),
    actions);
}

async function startUpdateJob(u) {
  const logBox = el("div", { class: "joblog" }, "starting update…\n");
  const done = el("div", {});
  openModal(`⬆ ${u.label}`, el("div", {},
    el("p", { class: "muted", style: "margin-bottom:4px" },
      `Pulls the new image and recreates: ${u.used_by.join(", ")}`),
    logBox, done));
  try {
    const { job_id } = await api("/updates/apply", {
      method: "POST", body: JSON.stringify({ image: u.image, recreate: true }),
    });
    await followJob(job_id, logBox);
    done.append(el("button", {
      class: "btn primary wide", style: "margin-top:6px",
      onclick: () => { closeModal(); loadUpdates(true); refreshDashboard(); },
    }, "Done — refresh"));
  } catch (e) {
    logBox.append(el("div", { class: "err-line" }, "✗ " + e.message));
  }
}

async function followJob(jobId, logBox) {
  const res = await fetch(`/api/jobs/${jobId}/stream`, {
    headers: { Authorization: "Bearer " + state.token },
  });
  if (!res.ok) throw new Error("stream failed");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  const lines = [];
  const render = () => {
    logBox.textContent = lines.join("\n");
    logBox.scrollTop = logBox.scrollHeight;
  };
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, idx);
      buf = buf.slice(idx + 1);
      if (!line) continue;
      if (line.startsWith("Layers ") && lines.length && lines[lines.length - 1].startsWith("Layers ")) {
        lines[lines.length - 1] = line;
      } else {
        lines.push(line);
      }
    }
    render();
  }
  render();
  if (lines.some((l) => l.includes("[job error]"))) throw new Error("update failed — see log");
}

$$("#upd-filters .chip-btn").forEach((b) => b.addEventListener("click", () => {
  $$("#upd-filters .chip-btn").forEach((x) => x.classList.toggle("active", x === b));
  state.updFilter = b.dataset.filter;
  renderUpdates();
}));

$("#updates-refresh").addEventListener("click", () => loadUpdates(true));

async function explainUpdate(subject, kind) {
  openModal("✦ AI explanation", el("div", { class: "thinking" }, "Researching " + subject));
  try {
    const r = await api("/updates/explain", {
      method: "POST",
      body: JSON.stringify({ subject, kind, lang: navigator.language }),
    });
    const div = el("div", {});
    div.innerHTML = renderMarkdown(r.explanation);
    setModalBody(div);
  } catch (e) {
    setModalBody(el("div", { class: "error" }, "⚠ " + e.message +
      (state.aiConfigured ? "" : " — configure an AI key under More first.")));
  }
}

/* ----- checks / reports ----- */

async function loadChecks() {
  try {
    renderReport(await api("/reports/latest"));
  } catch {
    $("#checks-list").innerHTML = "";
    $("#checks-list").append(el("div", { class: "card muted" },
      "No report yet — run your first server check."));
    $("#report-time").textContent = "";
  }
  const cfg = state.me?.report_config;
  if (cfg) {
    $("#report-schedule-note").textContent = cfg.auto
      ? `Checks run automatically every ${cfg.interval_min >= 60 ? (cfg.interval_min / 60) + " h" : cfg.interval_min + " min"} — configure under More.`
      : "Automatic checks are off — enable them under More.";
  }
}

function renderReport(r) {
  $("#report-time").textContent = timeAgo(r.time) + ` · ${r.duration}s`;
  const banner = $("#report-score");
  banner.className = "score-banner " + r.score;
  banner.classList.remove("hidden");
  banner.innerHTML = "";
  const icons = { ok: "✅", warn: "⚠️", crit: "🚨" };
  banner.append(
    el("span", { style: "font-size:22px" }, icons[r.score]),
    el("div", {},
      el("b", {}, r.score === "ok" ? "All good" : r.score === "warn" ? "Needs attention" : "Critical issues"),
      el("div", { class: "muted" },
        `${r.counts.crit} critical · ${r.counts.warn} warnings · ${r.counts.ok} ok`)));

  const list = $("#checks-list");
  list.innerHTML = "";
  const order = { crit: 0, warn: 1, info: 2, ok: 3 };
  const checks = [...r.checks].sort((a, b) => order[a.status] - order[b.status]);
  for (const c of checks) {
    const card = el("div", { class: "card check-card " + c.status },
      el("div", { class: "card-row" },
        el("span", { class: "app-icon", style: "font-size:18px" }, c.icon),
        el("div", { style: "min-width:0;flex:1" },
          el("div", { class: "card-title" }, c.title),
          el("div", { class: "card-sub", style: "white-space:normal" }, c.summary)),
        el("span", { class: "check-status" }, c.status)));
    if (c.details) {
      card.append(el("div", { class: "card-sub", style: "white-space:normal;margin-top:6px;opacity:.8" }, c.details));
    }
    if (c.recommendation) {
      card.append(el("div", { class: "check-rec" }, "💡 " + c.recommendation));
    }
    list.append(card);
  }
  updateHealthBadge(r);
}

$("#report-run").addEventListener("click", async function () {
  this.disabled = true;
  this.textContent = "running…";
  try { renderReport(await api("/reports/run", { method: "POST" })); }
  catch (e) { alert(e.message); }
  this.disabled = false;
  this.textContent = "Run now";
});

$("#report-analyze").addEventListener("click", async () => {
  openModal("✦ AI analysis", el("div", { class: "thinking" }, "Analyzing your server"));
  try {
    const r = await api("/reports/analyze", {
      method: "POST", body: JSON.stringify({ lang: navigator.language }),
    });
    const div = el("div", {});
    div.innerHTML = renderMarkdown(r.analysis);
    setModalBody(div);
  } catch (e) {
    setModalBody(el("div", { class: "error" }, "⚠ " + e.message +
      (state.aiConfigured ? "" : " — configure an AI key under More first.")));
  }
});

function updateHealthBadge(report) {
  const badge = $("#health-badge");
  const updates = state.updates?.docker?.filter((u) => u.update_available && !u.ignored).length || 0;
  const crit = report?.counts?.crit || 0;
  const n = updates + crit;
  badge.textContent = n;
  badge.classList.toggle("hidden", n === 0);
}

/* ---------------------------------------------------------- apps */

async function loadApps() {
  const wrap = $("#app-list");
  wrap.innerHTML = "";
  try {
    const { catalog, installed } = await api("/apps");
    for (const app of catalog) {
      const inst = installed[app.id];
      const chips = [el("span", { class: "chip" }, app.category)];
      if (inst) {
        chips.push(el("span", { class: "chip", style: inst.running ? "color:var(--accent2)" : "color:var(--danger)" },
          inst.running ? "running" : "stopped"));
        for (const p of inst.ports || []) {
          chips.push(el("a", { class: "chip", href: `http://${location.hostname}:${p}`, target: "_blank" }, `open :${p}`));
        }
      }
      if (app.website) {
        chips.push(el("a", { class: "chip", href: app.website, target: "_blank" }, "website"));
      }
      for (const cl of app.clients || []) {
        chips.push(el("a", { class: "chip", href: cl.url, target: "_blank" }, "📱 " + cl.name));
      }
      wrap.append(el("div", { class: "card" },
        el("div", { class: "card-row" },
          el("span", { class: "app-icon" }, app.icon),
          el("div", { style: "min-width:0" },
            el("div", { class: "card-title" }, app.name),
            el("div", { class: "card-sub", style: "white-space:normal" }, app.description))),
        el("div", { class: "card-actions" },
          inst
            ? el("button", { class: "btn small danger", onclick: () => uninstallApp(app) }, "Uninstall")
            : el("button", { class: "btn small primary", onclick: () => installDialog(app) }, "Install"),
          ...chips)));
    }
  } catch (e) {
    wrap.append(el("div", { class: "card" }, "Failed to load: " + e.message));
  }
}

function installDialog(app) {
  const inputs = {};
  const form = el("div", {});
  form.append(el("p", { class: "muted", style: "margin-bottom:12px" }, app.description));
  for (const f of app.fields || []) {
    const input = el("input", { type: "text", value: f.default });
    inputs[f.key] = input;
    form.append(el("label", {}, f.label, input));
  }
  const status = el("div", { class: "muted", style: "margin-top:8px" });
  const btn = el("button", {
    class: "btn primary wide",
    onclick: async () => {
      btn.disabled = true;
      status.textContent = "Deploying… (pulling image, this can take a minute)";
      try {
        const values = Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, i.value]));
        await api(`/apps/${app.id}/install`, { method: "POST", body: JSON.stringify({ values }) });
        status.textContent = "✓ Installed!";
        setTimeout(() => { closeModal(); loadApps(); }, 900);
      } catch (e) {
        status.textContent = "⚠ " + e.message;
        btn.disabled = false;
      }
    },
  }, `Install ${app.name}`);
  form.append(btn, status);
  openModal(`${app.icon} ${app.name}`, form);
}

async function uninstallApp(app) {
  if (!confirm(`Uninstall ${app.name}? (data volumes are kept)`)) return;
  try { await api(`/apps/${app.id}/uninstall`, { method: "POST" }); loadApps(); }
  catch (e) { alert(e.message); }
}

/* ---------------------------------------------------------- settings */

async function loadSettings() {
  try {
    const me = await api("/me");
    state.me = me;
    for (const p of ["anthropic", "openrouter", "openai"]) {
      $("#key-" + p).placeholder = me.ai_providers.includes(p)
        ? "•••••• configured" : $("#key-" + p).placeholder;
    }
    $("#ai-status").textContent = me.ai_configured
      ? `✓ configured: ${me.ai_providers.join(", ")}`
      : "No AI provider yet — Vibe Code, Explain and AI analysis need a key.";
    $("#workspaces-input").value = (me.workspaces || []).join("\n");
    $("#report-auto").checked = me.report_config.auto;
    $("#report-interval").value = String(me.report_config.interval_min);
    // default model selector
    const sel = $("#ai-default-model");
    sel.innerHTML = "";
    try {
      const { providers, default: def } = await api("/ai/models");
      for (const p of providers) {
        const og = el("optgroup", { label: p.provider });
        for (const m of p.models) og.append(el("option", { value: `${p.provider}|${m.id}` }, m.name || m.id));
        sel.append(og);
      }
      const wanted = `${def.provider}|${def.model}`;
      if ([...sel.options].some((o) => o.value === wanted)) sel.value = wanted;
    } catch {}
    const u = await api("/ai/usage");
    $("#usage-summary").innerHTML =
      `Today: ${fmtTokens(u.today.input)}→${fmtTokens(u.today.output)} tokens, ${u.today.requests} requests` +
      (u.today.cost ? `, ~$${u.today.cost.toFixed(3)}` : "") +
      `<br>This month: ${fmtTokens(u.month.input)}→${fmtTokens(u.month.output)} tokens` +
      (u.month.cost ? `, ~$${u.month.cost.toFixed(2)}` : "");
  } catch {}
}

$("#ai-save").addEventListener("click", async () => {
  const [prov, ...rest] = ($("#ai-default-model").value || "|").split("|");
  try {
    await api("/settings/ai", {
      method: "POST",
      body: JSON.stringify({
        keys: {
          anthropic: $("#key-anthropic").value.trim(),
          openrouter: $("#key-openrouter").value.trim(),
          openai: $("#key-openai").value.trim(),
        },
        default_provider: prov,
        default_model: rest.join("|"),
      }),
    });
    for (const p of ["anthropic", "openrouter", "openai"]) $("#key-" + p).value = "";
    vibeControlsReady = false;
    await loadSettings();
    state.aiConfigured = state.me.ai_configured;
    $("#vibe-no-ai").classList.toggle("hidden", state.aiConfigured);
  } catch (e) { $("#ai-status").textContent = "⚠ " + e.message; }
});

$("#workspaces-save").addEventListener("click", async function () {
  try {
    const r = await api("/settings/workspaces", {
      method: "POST",
      body: JSON.stringify({ paths: $("#workspaces-input").value.split("\n").map((s) => s.trim()).filter(Boolean) }),
    });
    $("#workspaces-input").value = r.workspaces.join("\n");
    state.me.workspaces = r.workspaces;
    vibeControlsReady = false;
    this.textContent = "✓ saved";
    setTimeout(() => (this.textContent = "Save workspaces"), 1200);
  } catch (e) { alert(e.message); }
});

$("#report-config-save").addEventListener("click", async function () {
  try {
    await api("/reports/config", {
      method: "POST",
      body: JSON.stringify({
        interval_min: parseInt($("#report-interval").value, 10),
        auto: $("#report-auto").checked,
      }),
    });
    if (state.me) state.me.report_config = {
      interval_min: parseInt($("#report-interval").value, 10),
      auto: $("#report-auto").checked,
    };
    this.textContent = "✓ saved";
    setTimeout(() => (this.textContent = "Save"), 1200);
  } catch (e) { alert(e.message); }
});

$("#logout").addEventListener("click", logout);

/* ---------------------------------------------------------- boot */

async function boot() {
  const ok = await tryAuth();
  $("#login-screen").classList.toggle("hidden", ok);
  $("#app").classList.toggle("hidden", !ok);
  if (ok) {
    showView("dashboard");
    chatConnect();
    // preload updates so the health badge is meaningful
    api("/updates").then((r) => { state.updates = r; updateHealthBadge(); }).catch(() => {});
  } else {
    setTimeout(() => $("#login-password").focus(), 100);
  }
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
boot();
