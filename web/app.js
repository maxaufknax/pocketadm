/* Helmsman SPA */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

/* -------------------------------------------------- multi-server store */
/* Helmsman can talk to more than one server. Each record is
   { id, name, base, token, demo? } where base "" means "the instance that
   served this app" (relative URLs). The active server's token backs
   state.token, so every api()/wsUrl() call transparently targets it. */

const servers = {
  all: JSON.parse(localStorage.getItem("helmsman_servers") || "[]"),
  activeId: localStorage.getItem("helmsman_active") || "",
};

// migrate a pre-multi-server single token into a "local" record
(() => {
  const legacy = localStorage.getItem("helmsman_token");
  if (legacy && !servers.all.some((s) => s.id === "local")) {
    servers.all.unshift({ id: "local", name: "This server", base: "", token: legacy });
    if (!servers.activeId) servers.activeId = "local";
    localStorage.removeItem("helmsman_token");
    persistServers();
  }
})();

function persistServers() {
  localStorage.setItem("helmsman_servers", JSON.stringify(servers.all));
  localStorage.setItem("helmsman_active", servers.activeId);
}
function activeServer() {
  return servers.all.find((s) => s.id === servers.activeId) || servers.all[0] || null;
}
function apiBase() {
  const s = activeServer();
  return s && s.base ? s.base.replace(/\/+$/, "") : "";
}
function serverHost() {
  const s = activeServer();
  try { return s && s.base ? new URL(s.base).hostname : location.hostname; }
  catch { return location.hostname; }
}
function setActiveToken(token) {
  const s = activeServer();
  if (s) { s.token = token; persistServers(); }
}
function upsertLocalServer(token) {
  let s = servers.all.find((x) => x.id === "local");
  if (!s) { s = { id: "local", name: "This server", base: "", token }; servers.all.unshift(s); }
  else s.token = token;
  servers.activeId = "local";
  persistServers();
}
function addRemoteServer(base, token, name, demo) {
  base = base.replace(/\/+$/, "");
  let s = servers.all.find((x) => x.base === base);
  if (!s) { s = { id: "srv-" + Math.random().toString(36).slice(2, 8), base }; servers.all.push(s); }
  Object.assign(s, { token, name: name || s.name || base, demo: !!demo });
  servers.activeId = s.id;
  persistServers();
  return s;
}

const state = {
  view: "dashboard",
  healthView: "updates",
  aiConfigured: false,
  me: null,
  chatWs: null,
  chatId: localStorage.getItem("helmsman_chat") || "",
  chatMode: "agent",
  chatModel: "",       // "provider|model"
  chatWorkdir: "",
  chatThinking: false,
  chatRunning: false,
  termWs: null,
  term: null,
  fitAddon: null,
  ctrlArmed: false,
  dashTimer: null,
  streamEl: null,
  streamRaw: "",
  thinkEl: null,
  thinkRaw: "",
  containers: [],
  updates: null,
  svcTechnical: false,
  svcClosed: new Set(JSON.parse(localStorage.getItem("helmsman_svc_closed") || "[]")),
};

// token always reflects the active server, so callers stay server-agnostic
Object.defineProperty(state, "token", { get: () => activeServer()?.token || "" });

/* ---------------------------------------------------------- helpers */

async function api(path, opts = {}) {
  const res = await fetch(apiBase() + "/api" + path, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + state.token,
      ...(opts.headers || {}),
    },
  });
  if (res.status === 401) { onAuthLost(); throw new Error("unauthorized"); }
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}

function wsUrl(path, params = {}) {
  const base = apiBase();
  let origin;
  if (base) {
    const u = new URL(base);
    origin = (u.protocol === "https:" ? "wss:" : "ws:") + "//" + u.host;
  } else {
    origin = (location.protocol === "https:" ? "wss:" : "ws:") + "//" + location.host;
  }
  const qs = new URLSearchParams({ token: state.token, ...params });
  return `${origin}${path}?${qs}`;
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

function fmtRate(b) { return fmtBytes(b) + "/s"; }

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

/* ---------------------------------------------------------- theming */

const THEMES = {
  "deep-sea": { label: "Deep Sea", p: ["#0b0f14", "#121821", "#4da3ff", "#7ee0b8"] },
  "midnight": { label: "Midnight", p: ["#000000", "#0b0b10", "#22d3ee", "#34d399"] },
  "aurora":   { label: "Aurora",   p: ["#0c0a13", "#14111f", "#a78bfa", "#5eead4"] },
  "ember":    { label: "Ember",    p: ["#14100b", "#1b1611", "#fb923c", "#86efac"] },
  "daybreak": { label: "Daybreak", p: ["#eef1f6", "#ffffff", "#2563eb", "#0d9488"] },
};

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function chartColor(c) { return c.startsWith("--") ? cssVar(c) : c; }

function storedTheme() { return localStorage.getItem("helmsman_theme") || "auto"; }

function resolveTheme(choice) {
  if (choice !== "auto" && THEMES[choice]) return choice;
  return matchMedia("(prefers-color-scheme: light)").matches ? "daybreak" : "deep-sea";
}

function applyTheme(choice, animate = false) {
  const root = document.documentElement;
  if (animate) {
    root.classList.add("theme-anim");
    setTimeout(() => root.classList.remove("theme-anim"), 420);
  }
  root.dataset.theme = resolveTheme(choice);
  document.querySelector('meta[name="theme-color"]')
    .setAttribute("content", cssVar("--bg2"));
  if (state.term) state.term.options.theme = termTheme();
}

matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
  if (storedTheme() === "auto") applyTheme("auto", true);
});
applyTheme(storedTheme());

function renderThemeGrid() {
  const grid = $("#theme-grid");
  if (!grid) return;
  grid.innerHTML = "";
  const current = storedTheme();
  const card = (key, label, preview) => el("button", {
    class: "theme-card" + (current === key ? " active" : ""),
    onclick: () => {
      localStorage.setItem("helmsman_theme", key);
      applyTheme(key, true);
      renderThemeGrid();
    },
  }, preview, el("div", { class: "tn" }, label));
  // Auto = split dark/light preview
  grid.append(card("auto", "Auto", el("div", { class: "tp",
    style: "background:linear-gradient(105deg,#0b0f14 49.6%,#eef1f6 50.4%)" },
    el("span", { class: "tp-bar",
      style: "background:linear-gradient(105deg,#4da3ff 49.6%,#2563eb 50.4%)" }))));
  for (const [key, t] of Object.entries(THEMES)) {
    grid.append(card(key, t.label, el("div", { class: "tp", style: `background:${t.p[0]}` },
      el("span", { class: "tp-card", style: `background:${t.p[1]}` }),
      el("span", { class: "tp-bar", style: `background:${t.p[2]}` }),
      el("span", { class: "tp-dot", style: `background:${t.p[3]}` }))));
  }
}

/* Category → tint class for icon tiles (stable pseudo-random). */
function tintFor(text) {
  let h = 0;
  for (const ch of String(text || "")) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return "tint-" + (h % 7);
}

function iconTile(icon, category, size = "") {
  return el("span", { class: `tile-icon ${size} ${tintFor(category)}` }, icon || "📦");
}

/* Markdown: headings, lists, bold, code (long fences collapsible), links */
function renderMarkdown(text) {
  const escape = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s) => s
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
             '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const parts = text.split(/```(\w*)\n?/);
  let html = "";
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 1) {
      const code = (parts[i + 1] || "").replace(/\n$/, "");
      const lines = code.split("\n").length;
      const pre = `<pre><code>${escape(code)}</code></pre>`;
      html += lines > 14
        ? `<details class="codefold"><summary>▸ code${parts[i] ? " · " + parts[i] : ""} (${lines} lines)</summary>${pre}</details>`
        : pre;
      i++;
    } else {
      const lines = escape(parts[i]).split("\n");
      let out = [], list = null;
      const flushList = () => { if (list) { out.push(`<ul>${list.join("")}</ul>`); list = null; } };
      for (const line of lines) {
        const m = line.match(/^\s*[-*•] (.+)$/);
        const h = line.match(/^#{1,4} (.+)$/);
        const n = line.match(/^\s*(\d+)\. (.+)$/);
        if (m) { (list ||= []).push(`<li>${inline(m[1])}</li>`); }
        else if (n) { (list ||= []).push(`<li>${inline(n[2])}</li>`); }
        else if (h) { flushList(); out.push(`<h4>${inline(h[1])}</h4>`); }
        else { flushList(); out.push(inline(line)); }
      }
      flushList();
      // join plain lines with \n (pre-wrap renders them); block elements need none
      html += out.map((seg, idx) => {
        const block = seg.startsWith("<ul>") || seg.startsWith("<h4>");
        const nextBlock = idx + 1 < out.length &&
          (out[idx + 1].startsWith("<ul>") || out[idx + 1].startsWith("<h4>"));
        return seg + (block || nextBlock || idx === out.length - 1 ? "" : "\n");
      }).join("");
    }
  }
  return html;
}

function mdDiv(text, cls = "") {
  const div = el("div", { class: cls, style: "white-space:pre-wrap" });
  div.innerHTML = renderMarkdown(text);
  return div;
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

let toastTimer = null;
function toast(msg) {
  let t = $("#toast");
  if (!t) { t = el("div", { id: "toast", class: "toast" }); document.body.append(t); }
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}

/* ------------------------------------------------ ask the AI anywhere */

function openVibeWith(text) {
  closeModal();
  showView("vibe");
  const input = $("#chat-input");
  input.value = text;
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 120) + "px";
  setTimeout(() => input.focus(), 60);
}

function askAiButton(prompt, label = "💬 Ask AI") {
  return el("button", { class: "btn small ai", onclick: (e) => {
    e.stopPropagation();
    openVibeWith(prompt);
  } }, label);
}

/* Instruct the agent right now, from anywhere — a quick composer that sends
   straight to the current chat (creating one if needed) and opens it to watch.
   `prefill`/`context` let callers seed it with what the user is looking at. */
function openInstruct(prefill = "", context = "") {
  const ta = el("textarea", { rows: "3", placeholder: "Tell the agent what to do…" }, prefill);
  const modeSel = el("div", { class: "seg instruct-modes" },
    el("button", { class: "active", "data-m": "agent" }, "Agent"),
    el("button", { "data-m": "auto" }, "Auto"));
  let mode = loadChatPrefs().mode === "auto" ? "auto" : "agent";
  [...modeSel.children].forEach((b) => {
    b.classList.toggle("active", b.dataset.m === mode);
    b.addEventListener("click", () => {
      mode = b.dataset.m;
      [...modeSel.children].forEach((x) => x.classList.toggle("active", x === b));
    });
  });
  const send = () => {
    const text = ta.value.trim();
    if (!text) return;
    closeModal();
    setChatMode(mode);
    showView("vibe");
    sendChatConfig();
    setChatRunning(true);
    sendChat({ type: "user", text });
    toast("Sent to the agent");
  };
  const body = el("div", { class: "instruct-box" },
    context ? el("p", { class: "muted", style: "margin-bottom:8px" }, context) : null,
    ta,
    el("div", { class: "instruct-row" }, modeSel,
      el("span", { class: "spacer" }),
      el("button", { class: "btn primary", onclick: send }, "▷ Send")));
  openModal("Instruct the agent", body);
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send(); }
  });
  setTimeout(() => ta.focus(), 60);
}

$("#instruct-btn").addEventListener("click", () => openInstruct());

/* ---------------------------------------------------------- auth */

function logout() {
  // sign out of the active server; drop remotes entirely, just clear local's token
  const s = activeServer();
  if (s && s.id !== "local") {
    servers.all = servers.all.filter((x) => x.id !== s.id);
  } else if (s) {
    s.token = "";
  }
  servers.activeId = (servers.all.find((x) => x.id === "local") || servers.all[0] || {}).id || "";
  persistServers();
  location.reload();
}

// a 401 during use: the active server's token expired/was revoked. Fall back
// to another authenticated server, else drop to the connect/login screen.
function onAuthLost() {
  const s = activeServer();
  if (s && s.id !== "local") {
    servers.all = servers.all.filter((x) => x.id !== s.id);
    const local = servers.all.find((x) => x.id === "local" && x.token);
    servers.activeId = (local || {}).id || "";
    persistServers();
  }
  location.reload();
}

async function tryAuth() {
  if (!state.token) return false;
  try {
    const me = await api("/me");
    state.me = me;
    $("#host-name-text").textContent = me.server_name || me.hostname;
    state.aiConfigured = me.ai_configured;
    $("#vibe-no-ai")?.classList.toggle("hidden", me.ai_configured);
    applyServerChrome(me);
    return true;
  } catch { return false; }
}

// per-server chrome: demo banner + hostname acts as a server switcher
function applyServerChrome(me) {
  document.body.classList.toggle("demo", !!me.demo);
  let banner = $("#demo-banner");
  if (me.demo && !banner) {
    banner = el("div", { id: "demo-banner", class: "demo-banner" },
      "🎡 Demo mode — this is a read-only playground. Changes are disabled.");
    $("#app").prepend(banner);
  } else if (!me.demo && banner) {
    banner.remove();
  }
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#login-error");
  err.classList.add("hidden");
  try {
    // the login screen always targets the local (own) server
    const res = await fetch("/api/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        password: $("#login-password").value,
        totp: $("#login-totp").value.trim(),
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      if (data.totp) {   // second factor required
        const totp = $("#login-totp");
        totp.classList.remove("hidden");
        totp.focus();
        throw new Error(data.detail || "Enter your 2FA code");
      }
      throw new Error(data.detail || "Login failed");
    }
    upsertLocalServer(data.token);
    $("#login-totp").value = "";
    $("#login-totp").classList.add("hidden");
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
  if (name === "vibe") {
    initVibeControls();
    if (!$("#chat-log").children.length) renderChatEvents([]);
    setTimeout(() => $("#chat-input").focus(), 50);
  }
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
    if (s.net) {
      $("#stat-net").textContent = s.net.ping == null ? "offline" : s.net.ping + " ms";
      $("#stat-net").style.color = s.net.ping == null ? "var(--danger)" : "";
      $("#stat-net-sub").textContent = `↓ ${fmtRate(s.net.rx)} · ↑ ${fmtRate(s.net.tx)}`;
    } else {
      $("#stat-net").textContent = "…";
      $("#stat-net-sub").textContent = "measuring";
    }
    state.containers = await api("/containers");
    renderServices();
  } catch (e) {
    $("#conn-dot").className = "dot bad";
  }
  clearTimeout(state.dashTimer);
  if (state.view === "dashboard") state.dashTimer = setTimeout(refreshDashboard, 6000);
}

/* ----- metric graphs ----- */

const METRICS = {
  cpu:  { title: "CPU", unit: "%", max: 100, series: [{ key: "cpu", color: "--chart-1", label: "CPU %" }] },
  mem:  { title: "Memory", unit: "%", max: 100, series: [{ key: "mem", color: "--chart-2", label: "RAM %" }] },
  disk: { title: "Disk", unit: "%", max: 100, series: [{ key: "disk", color: "--chart-3", label: "Disk %" }] },
  load: { title: "CPU load", unit: "", series: [{ key: "load", color: "--chart-1", label: "load (1 min)" }] },
  net:  { title: "Internet & network", unit: "", fmt: fmtRate,
          series: [{ key: "rx", color: "--chart-1", label: "↓ download" },
                   { key: "tx", color: "--chart-2", label: "↑ upload" }],
          extra: { key: "ping", color: "--chart-3", label: "latency ms" } },
};

function drawGraph(canvas, seriesList, points, opts = {}) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 560, h = canvas.clientHeight || 160;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  const padL = 6, padR = 6, padT = 10, padB = 6;
  const vals = seriesList.flatMap((s) => points.map((p) => p[s.key]).filter((v) => v != null));
  if (!vals.length) {
    ctx.fillStyle = cssVar("--muted"); ctx.font = "12px sans-serif";
    ctx.fillText("collecting data — check back in a minute", 14, h / 2);
    return;
  }
  let lo = opts.min != null ? opts.min : Math.min(...vals);
  let hi = opts.max != null ? opts.max : Math.max(...vals);
  if (hi - lo < 1e-9) { hi = lo + 1; }
  hi *= 1.05;
  const t0 = points[0].t, t1 = points[points.length - 1].t || t0 + 1;
  const x = (t) => padL + ((t - t0) / Math.max(1, t1 - t0)) * (w - padL - padR);
  const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (h - padT - padB);
  // grid
  ctx.strokeStyle = chartColor("--border") + "cc";
  ctx.lineWidth = 1;
  for (const frac of [0.25, 0.5, 0.75]) {
    ctx.beginPath(); ctx.moveTo(padL, padT + frac * (h - padT - padB));
    ctx.lineTo(w - padR, padT + frac * (h - padT - padB)); ctx.stroke();
  }
  for (const s of seriesList) {
    const col = chartColor(s.color);
    ctx.beginPath();
    let started = false;
    for (const p of points) {
      if (p[s.key] == null) continue;
      const px = x(p.t), py = y(p[s.key]);
      started ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
      started = true;
    }
    ctx.strokeStyle = col; ctx.lineWidth = 1.8; ctx.stroke();
    if (seriesList.length === 1) {
      ctx.lineTo(x(t1), y(lo)); ctx.lineTo(x(t0), y(lo)); ctx.closePath();
      ctx.fillStyle = col + "22"; ctx.fill();
    }
  }
  // hi/lo labels
  ctx.fillStyle = cssVar("--muted"); ctx.font = "10.5px sans-serif";
  const fmt = opts.fmt || ((v) => Math.round(v * 10) / 10 + (opts.unit || ""));
  ctx.fillText(fmt(hi), padL + 2, padT + 9);
  ctx.fillText(fmt(lo), padL + 2, h - padB - 3);
}

async function openMetricModal(kind) {
  const cfg = METRICS[kind];
  if (!cfg) return;
  let minutes = 30;
  const body = el("div", {});
  const kpis = el("div", { class: "metric-kpis" });
  const canvas = el("canvas", { class: "graph-canvas" });
  const canvas2 = cfg.extra ? el("canvas", { class: "graph-canvas", style: "height:110px" }) : null;
  const rangeSeg = el("div", { class: "seg", style: "margin-bottom:10px" });
  for (const [label, m] of [["15 min", 15], ["30 min", 30], ["2 h", 120]]) {
    const b = el("button", { class: m === minutes ? "active" : "", onclick: () => {
      minutes = m;
      rangeSeg.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
      render();
    } }, label);
    rangeSeg.append(b);
  }
  const legend = el("div", { class: "muted", style: "margin-bottom:6px" },
    cfg.series.map((s) => s.label).join(" · ") + (cfg.extra ? " · " + cfg.extra.label : ""));
  body.append(rangeSeg, kpis, canvas);
  if (canvas2) body.append(canvas2);
  body.append(legend);
  body.append(el("div", { class: "card-actions" },
    askAiButton(`Look at my server's ${cfg.title} usage and tell me if anything is unusual. ` +
                `Check top consumers and give me plain-language advice.`, "💬 Ask AI about this")));
  openModal(cfg.title, body);

  async function render() {
    try {
      const { points } = await api(`/metrics/history?minutes=${minutes}`);
      kpis.innerHTML = "";
      for (const s of cfg.series.concat(cfg.extra ? [cfg.extra] : [])) {
        const vals = points.map((p) => p[s.key]).filter((v) => v != null);
        if (!vals.length) continue;
        const cur = vals[vals.length - 1], avg = vals.reduce((a, b) => a + b, 0) / vals.length;
        const fmt = cfg.fmt && s.key !== "ping" ? cfg.fmt
          : (v) => Math.round(v * 10) / 10 + (s.key === "ping" ? " ms" : cfg.unit || "");
        kpis.append(el("div", { class: "kpi" }, el("b", {}, fmt(cur)), `${s.label} · avg ${fmt(avg)}`));
      }
      drawGraph(canvas, cfg.series, points,
        { max: cfg.max, unit: cfg.unit, fmt: cfg.fmt });
      if (canvas2 && cfg.extra) {
        drawGraph(canvas2, [cfg.extra], points, { unit: " ms" });
      }
    } catch (e) {
      kpis.innerHTML = "";
      kpis.append(el("div", { class: "error" }, e.message));
    }
  }
  render();
}

$$(".stat").forEach((s) => s.addEventListener("click", () => openMetricModal(s.dataset.metric)));

/* ----- services ----- */

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
    const problems = containers.filter((c) => c.health === "unhealthy" ||
      ["exited", "dead", "restarting"].includes(c.state)).length;
    const closed = state.svcClosed.has(label);
    const group = el("div", { class: "svc-group" + (closed ? " closed" : "") });
    const head = el("button", { class: "svc-group-head", onclick: () => {
      group.classList.toggle("closed");
      group.classList.contains("closed") ? state.svcClosed.add(label) : state.svcClosed.delete(label);
      localStorage.setItem("helmsman_svc_closed", JSON.stringify([...state.svcClosed]));
    } },
      el("span", {}, label),
      el("span", { class: "count" }, `${running}/${containers.length}`),
      problems ? el("span", { class: "pill crit" }, `${problems} ⚠`) : null,
      el("span", { class: "chev" }, "▾"));
    group.append(head, el("div", { class: "svc-cards" },
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
  if (c.health === "unhealthy") chips.push(el("span", { class: "pill crit" }, "unhealthy"));
  for (const p of c.ports.slice(0, 2)) {
    chips.push(el("a", {
      class: "chip", href: `http://${serverHost()}:${p.public}`, target: "_blank",
      onclick: (e) => e.stopPropagation(),
    }, `:${p.public}`));
  }
  return el("div", { class: "card svc-card", onclick: () => showContainerDetail(c) },
    el("div", { class: "card-row" },
      iconTile(svc.icon, svc.category || svc.label),
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

/* ----- container detail ----- */

async function showContainerDetail(c) {
  const svc = c.service || { label: c.name, icon: "📦", category: "" };
  openModal(svc.label, el("div", { class: "thinking" }, "loading"));
  let d;
  try { d = await api(`/containers/${c.id}/detail`); }
  catch (e) { setModalBody(el("div", { class: "error" }, e.message)); return; }

  const upd = state.updates?.docker?.find((u) => u.image === d.image);
  const body = el("div", {});

  // header
  const statePill = d.state === "running"
    ? el("span", { class: "pill ok" }, d.health ? `running · ${d.health}` : "running")
    : el("span", { class: "pill crit" }, d.state);
  body.append(el("div", { class: "cd-head" },
    iconTile(svc.icon, svc.category || svc.label, "lg"),
    el("div", { style: "min-width:0;flex:1" },
      el("div", { class: "cd-title" }, d.name),
      el("div", { class: "cd-sub" }, d.image)),
    statePill));

  // live stats
  const stats = el("div", { class: "cd-stats" });
  const stat = (lbl, val) => el("div", { class: "cd-stat" },
    el("div", { class: "lbl" }, lbl), el("div", { class: "val" }, val));
  stats.append(stat("CPU", "…"), stat("RAM", "…"),
    stat("Restarts", String(d.restart_count || 0)),
    stat("Started", d.started_at && d.state === "running"
      ? timeAgo(new Date(d.started_at).getTime() / 1000).replace(" ago", "") : "—"));
  body.append(stats);
  if (d.state === "running") {
    api(`/containers/${c.id}/stats`).then((s) => {
      stats.children[0].querySelector(".val").textContent = s.cpu_percent + "%";
      stats.children[1].querySelector(".val").textContent = fmtBytes(s.mem_usage);
    }).catch(() => {});
  }

  // actions
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
  if (d.state === "running") {
    actions.append(el("button", { class: "btn small", title: "open a shell inside this container",
      onclick: () => openTerminalFor(c) }, "❯_ terminal"));
  }
  if (upd?.update_available) {
    actions.append(el("button", {
      class: "btn small primary",
      onclick: () => { closeModal(); startUpdateJob(upd); },
    }, "⬆ update available"));
  }
  body.append(actions);

  // AI row
  const aiBox = el("div", {});
  body.append(el("div", { class: "card-actions", style: "margin:0 0 12px" },
    el("button", { class: "btn small ai", onclick: async function () {
      this.disabled = true;
      aiBox.innerHTML = "";
      aiBox.append(el("div", { class: "thinking" }, "AI is looking at this container"));
      try {
        const r = await api(`/containers/${c.id}/describe`, {
          method: "POST", body: JSON.stringify({ lang: navigator.language }) });
        aiBox.innerHTML = "";
        aiBox.append(el("div", { class: "app-why" }, mdDiv(r.description)));
      } catch (e) {
        aiBox.innerHTML = "";
        aiBox.append(el("div", { class: "error" }, "⚠ " + e.message));
      }
      this.disabled = false;
    } }, "✦ What is this?"),
    askAiButton(`About the container "${d.name}" on my server (image ${d.image}, ` +
      `state: ${d.state}${d.health ? "/" + d.health : ""}, restarts: ${d.restart_count}). ` +
      `Please check how it's doing and whether its configuration can be improved.`,
      "💬 Open in Vibe Chat")));
  body.append(aiBox);

  // one-tap prompts so common tasks don't need typing
  const quick = el("div", { class: "card-actions", style: "margin:0 0 12px" });
  const qchip = (label, prompt) => el("button", { class: "chip qchip",
    onclick: () => openVibeWith(prompt) }, label);
  quick.append(
    qchip("🔍 check logs", `Look at the recent logs of container "${d.name}" on my server ` +
      `and tell me if anything needs attention.`),
    qchip("🛠 troubleshoot", `Container "${d.name}" (${d.image}): please diagnose it — ` +
      `state, logs, restarts, resource usage — and fix what's wrong.`),
    qchip("⚡ optimize", `Can the configuration of container "${d.name}" be improved ` +
      `(resources, restart policy, volumes, security)? Propose concrete changes.`),
    qchip("💾 backup", `What data does container "${d.name}" store and how would I back it up properly?`));
  body.append(quick);

  // technical details (folded)
  const grid = el("dl", { class: "detail-grid" });
  const row = (k, v) => { if (v || v === 0) grid.append(el("dt", {}, k), el("dd", {}, String(v))); };
  row("Container", d.name + " (" + d.id + ")");
  row("Image", d.image);
  row("State", d.state + (d.health ? ` (${d.health})` : ""));
  row("Restart policy", d.restart_policy || "none");
  if (d.privileged) row("Privileged", "yes ⚠");
  row("Networks", d.networks.join(", "));
  row("Env vars", d.env_count);
  if (d.cmd) row("Command", d.cmd);
  if (d.labels["com.docker.compose.project"]) {
    row("Compose project", d.labels["com.docker.compose.project"]);
    row("Compose service", d.labels["com.docker.compose.service"]);
  }
  const techBody = el("div", {}, grid);

  if (c.ports.length) {
    techBody.append(el("div", { class: "card-sub", style: "margin-bottom:4px" }, "Published ports"));
    const pr = el("div", { class: "card-actions", style: "margin:0 0 10px" });
    for (const p of c.ports) {
      pr.append(el("a", { class: "chip", target: "_blank",
        href: `http://${serverHost()}:${p.public}` },
        `${p.public} → ${p.private}/${p.type}${p.ip && p.ip !== "0.0.0.0" ? " (" + p.ip + ")" : ""}`));
    }
    techBody.append(pr);
  }
  if (d.mounts.length) {
    techBody.append(el("div", { class: "card-sub" }, "Mounts"));
    techBody.append(el("ul", { class: "mount-list" },
      ...d.mounts.slice(0, 10).map((m) =>
        el("li", {}, `${m.source || m.type} → ${m.dest}${m.rw ? "" : " (ro)"}`))));
  }
  body.append(fold("Technical details", techBody, false));

  setModalBody(body);
}

function fold(title, bodyNode, open = false, extraHead = null) {
  const f = el("div", { class: "fold" + (open ? " open" : "") });
  f.append(
    el("button", { class: "fold-head", onclick: () => f.classList.toggle("open") },
      el("span", {}, title), extraHead, el("span", { class: "chev" }, "▸")),
    el("div", { class: "fold-body" }, bodyNode));
  return f;
}

async function showLogs(c) {
  openModal(`Logs · ${c.name}`, el("pre", {}, "loading…"));
  try {
    const r = await api(`/containers/${c.id}/logs?tail=300`);
    const logs = r.logs || "[no output]";
    const wrap = el("div", {});
    const pre = el("pre", {}, logs);
    wrap.append(pre, el("div", { class: "card-actions" },
      askAiButton(`Here are the recent logs of my container "${c.name}" ` +
        `(image ${c.image}):\n\n\`\`\`\n${logs.slice(-2500)}\n\`\`\`\n` +
        `Explain what's going on and whether anything needs fixing.`, "💬 Explain logs with AI")));
    setModalBody(wrap);
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

// Populate the chat model picker eagerly (at boot) so options exist by the
// time a session snapshot arrives — keeps the displayed model in sync with the
// session's actual model. Re-runnable when local models change.
let modelsReady = false;
async function populateModels() {
  if (modelsReady) return;
  try {
    const { providers, default: def } = await api("/ai/models");
    const sel = $("#chat-model");
    sel.innerHTML = "";
    for (const p of providers) {
      const label = p.local ? "local (Ollama)" : p.provider;
      const og = el("optgroup", { label });
      for (const m of p.models) og.append(el("option", { value: `${p.provider}|${m.id}` }, m.name || m.id));
      if (p.models.length) sel.append(og);
    }
    state.defaultModel = def.provider ? `${def.provider}|${def.model}` : "";
    modelsReady = true;
    if (!state.chatModel) {
      const prefs = loadChatPrefs();
      const has = (v) => v && [...sel.options].some((o) => o.value === v);
      state.chatModel = [prefs.model, state.defaultModel].find(has) ||
        (sel.options[0] && sel.options[0].value) || "";
    }
    syncModelSelect();
  } catch {}
}

async function initVibeControls() {
  if (vibeControlsReady) return;
  vibeControlsReady = true;
  const prefs = loadChatPrefs();
  if (prefs.mode) setChatMode(prefs.mode);
  if (prefs.thinking) { state.chatThinking = true; $("#chat-thinking").classList.add("on"); }
  if (!state.chatWorkdir) state.chatWorkdir = state.me?.default_workspace || state.me?.workspaces?.[0] || "";
  updateWsLabel();
  await populateModels();
}

function updateWsLabel() {
  let p = (state.chatWorkdir || "…").replace(/^\/host/, "") || "/";
  if (p.length > 26) p = "…" + p.slice(-25);
  $("#ws-label").textContent = p;
}

function sendChatConfig() {
  const [provider, ...rest] = (state.chatModel || "|").split("|");
  sendChat({
    type: "config",
    mode: state.chatMode,
    provider: provider || undefined,
    model: rest.join("|") || undefined,
    workdir: state.chatWorkdir || undefined,
    thinking: state.chatThinking,
  });
}

$$("#mode-seg button").forEach((b) => b.addEventListener("click", () => {
  setChatMode(b.dataset.mode);
  sendChatConfig();
}));
$("#chat-model").addEventListener("change", function () {
  state.chatModel = this.value;
  saveChatPrefs();
  sendChatConfig();
});
$("#chat-thinking").addEventListener("click", function () {
  state.chatThinking = !state.chatThinking;
  this.classList.toggle("on", state.chatThinking);
  saveChatPrefs();
  sendChatConfig();
});

/* This device's last-used chat settings, so a new chat resumes them. */
const CHAT_PREFS_KEY = "helmsman_chat_prefs";
function loadChatPrefs() {
  try { return JSON.parse(localStorage.getItem(CHAT_PREFS_KEY) || "{}"); } catch { return {}; }
}
function saveChatPrefs() {
  localStorage.setItem(CHAT_PREFS_KEY, JSON.stringify({
    mode: state.chatMode, model: state.chatModel, thinking: state.chatThinking }));
}
function setChatMode(mode) {
  state.chatMode = mode;
  $$("#mode-seg button").forEach((x) => x.classList.toggle("active", x.dataset.mode === mode));
  saveChatPrefs();
}
function syncModelSelect() {
  const sel = $("#chat-model");
  if (!sel || !sel.options.length || !state.chatModel) return;
  if (![...sel.options].some((o) => o.value === state.chatModel)) {
    const [prov, ...rest] = state.chatModel.split("|");
    sel.append(el("option", { value: state.chatModel }, `${rest.join("|")} (${prov})`));
  }
  sel.value = state.chatModel;
}
// reflect the true session config (multi-device) into this device's controls
function adoptConfig(cfg) {
  if (!cfg) return;
  if (cfg.mode) setChatMode(cfg.mode);
  if (cfg.provider) {
    state.chatModel = `${cfg.provider}|${cfg.model || ""}`;
    syncModelSelect();
  }
  if (cfg.workdir != null) { state.chatWorkdir = cfg.workdir; updateWsLabel(); }
  if (cfg.thinking != null) {
    state.chatThinking = !!cfg.thinking;
    $("#chat-thinking").classList.toggle("on", state.chatThinking);
  }
  saveChatPrefs();
}

/* ----- chats: welcome, history, list ----- */

const SUGGESTIONS = [
  { icon: "🛡", label: "Security check",
    text: "Run a quick security check on my server: SSH hardening, failed logins, fail2ban, exposed ports. Give me a prioritized list of what to fix." },
  { icon: "🐳", label: "What runs here?",
    text: "Give me an overview of everything running on this server — grouped by purpose, in plain language. Flag anything that looks unused or unhealthy." },
  { icon: "🧹", label: "Free disk space",
    text: "Find out what is using the most disk space (docker images, volumes, logs) and propose a safe cleanup. Ask before deleting anything." },
  { icon: "⬆️", label: "Update plan",
    text: "Look at my pending updates and tell me which to apply first, what's risky, and then help me apply the important ones." },
  { icon: "🚀", label: "Performance",
    text: "Check CPU, RAM and I/O usage and which containers consume the most. Is anything worth optimizing?" },
  { icon: "💾", label: "Backup advice",
    text: "Assess my backup situation: what is currently backed up, what's missing, and what setup would you recommend for this server?" },
];

function renderWelcome() {
  const wrap = el("div", { class: "chat-welcome" },
    el("div", { class: "logo-big" }, "⎈"),
    el("h2", {}, "Vibe Code"),
    el("p", { class: "muted" }, "Chat with an AI engineer that works directly on your server."),
    el("p", { class: "muted modes-hint" }));
  wrap.querySelector(".modes-hint").innerHTML =
    "<b>Chat</b> talk only · <b>Plan</b> read-only · <b>Agent</b> asks before actions · <b>Auto</b> free rein";
  const grid = el("div", { class: "suggest-grid" });
  for (const s of SUGGESTIONS) {
    grid.append(el("button", { class: "suggest", onclick: () => {
      const input = $("#chat-input");
      input.value = s.text;
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 120) + "px";
      input.focus();
    } }, el("span", { class: "s-ico" }, s.icon), el("span", {}, s.label)));
  }
  wrap.append(grid);
  if (!state.aiConfigured) {
    wrap.append(el("p", { class: "warn", id: "vibe-no-ai" },
      "No AI key configured yet → More"));
  }
  return wrap;
}

function staticToolCard(evt) {
  const card = el("div", { class: "tool-card approved" });
  const head = el("div", { class: "tool-head", onclick: () => card.classList.toggle("open") },
    el("span", { class: "tool-status" }, "✓"),
    el("span", { class: "tool-name" }, evt.name),
    el("span", { class: "tool-summary" }, toolSummary(evt.name, evt.args || {})),
    evt.name === "run_command"
      ? el("button", { class: "tool-term", title: "open this command in the terminal",
          onclick: (e) => { e.stopPropagation(); openTerminalWithCommand((evt.args || {}).command || ""); } }, "❯_")
      : null,
    el("span", { class: "chev" }, "▸"));
  const body = el("div", { class: "tool-body" },
    el("pre", {}, JSON.stringify(evt.args || {}, null, 1).slice(0, 900)));
  if (evt.output) body.append(el("div", { class: "tool-output" }, evt.output));
  card.append(head, body);
  return card;
}

function renderChatEvents(events) {
  const log = chatLogEl();
  log.innerHTML = "";
  state.streamEl = null; state.streamRaw = "";
  state.thinkEl = null; state.thinkRaw = "";
  if (!events || !events.length) {
    log.append(renderWelcome());
    return;
  }
  for (const evt of events) {
    if (evt.t === "user") {
      log.append(el("div", { class: "msg user" }, evt.text));
    } else if (evt.t === "assistant") {
      const div = el("div", { class: "msg assistant" });
      div.innerHTML = renderMarkdown(evt.text);
      log.append(div);
    } else if (evt.t === "tool") {
      log.append(staticToolCard(evt));
    }
  }
  scrollChat(true);
}

function setChatTitle(title) {
  $("#chat-title").textContent = title === "New chat" ? "" : title;
}

function newChat() {
  sendChat({ type: "open", id: "" });
  $("#usage-row").classList.add("hidden");
}

$("#chat-new").addEventListener("click", newChat);
$("#chat-list-btn").addEventListener("click", openChatList);

async function openChatList() {
  const body = el("div", {});
  openModal("Your chats", body);
  async function render() {
    body.innerHTML = "";
    let r;
    try { r = await api("/chats"); }
    catch (e) { body.append(el("div", { class: "error" }, e.message)); return; }
    body.append(el("button", { class: "btn primary wide", style: "margin-bottom:10px",
      onclick: () => { closeModal(); showView("vibe"); newChat(); } }, "＋ Start a new chat"));
    const active = r.chats.filter((c) => !c.archived);
    const archived = r.chats.filter((c) => c.archived);
    const row = (c) => {
      const r2 = el("div", { class: "chat-row" + (c.id === state.chatId ? " current" : "") },
        el("button", { class: "chat-open", onclick: () => {
          closeModal(); showView("vibe");
          sendChat({ type: "open", id: c.id });
        } },
          el("div", { class: "name" }, c.title || "New chat"),
          el("div", { class: "sub" }, `${timeAgo(c.updated)} · ${c.message_count} messages`)),
        el("button", { class: "btn small", title: c.archived ? "unarchive" : "archive",
          onclick: async () => {
            await api(`/chats/${c.id}/archive`, { method: "POST",
              body: JSON.stringify({ archived: !c.archived }) });
            render();
          } }, c.archived ? "↩" : "🗄"),
        el("button", { class: "btn small", title: "delete", onclick: async () => {
          if (!confirm(`Delete chat "${c.title}"?`)) return;
          await api(`/chats/${c.id}`, { method: "DELETE" });
          if (c.id === state.chatId) newChat();
          render();
        } }, "🗑"));
      return r2;
    };
    if (!active.length && !archived.length) {
      body.append(el("p", { class: "muted" }, "No chats yet — start one!"));
    }
    for (const c of active) body.append(row(c));
    if (archived.length) {
      const rows = el("div", {});
      for (const c of archived) rows.append(row(c));
      body.append(fold(`🗄 Archived (${archived.length})`, rows));
    }
  }
  render();
}

/* ----- workspace browser ----- */

$("#chat-workspace-btn").addEventListener("click", () => openFolderPicker({
  title: "📁 Working folder",
  start: state.chatWorkdir || "",
  intro: "Pick where the agent should work. These are your allowed workspaces " +
    "(configure under More → Default workspace).",
  chooseLabel: (p) => `Work here: ${p}`,
  onChoose: (path) => { state.chatWorkdir = path; updateWsLabel(); sendChatConfig(); },
}));

async function openFolderPicker({ title, start, intro, chooseLabel, onChoose }) {
  const body = el("div", {});
  openModal(title, body);

  async function render(path) {
    body.innerHTML = "";
    let r;
    try { r = await api("/fs" + (path ? `?path=${encodeURIComponent(path)}` : "")); }
    catch (e) { body.append(el("div", { class: "error" }, e.message)); return; }

    if (r.path) {
      const crumbs = el("div", { class: "ws-crumbs" });
      crumbs.append(el("button", { onclick: () => render("") }, "⌂ roots"));
      const root = r.roots.find((x) => r.path === x || r.path.startsWith(x + "/"));
      if (root) {
        crumbs.append(el("span", { class: "muted" }, "›"),
          el("button", { onclick: () => render(root) }, root.replace(/^\/host/, "") || "/"));
        const rel = r.path.slice(root.length).split("/").filter(Boolean);
        let acc = root;
        for (const seg of rel) {
          acc += "/" + seg;
          const target = acc;
          crumbs.append(el("span", { class: "muted" }, "›"),
            el("button", { onclick: () => render(target) }, seg));
        }
      }
      body.append(crumbs);
      const shown = r.path.replace(/^\/host/, "") || "/";
      body.append(el("button", {
        class: "btn primary wide", style: "margin-bottom:10px",
        onclick: () => { onChoose(r.path); closeModal(); },
      }, chooseLabel(shown)));
    } else if (intro) {
      body.append(el("p", { class: "muted", style: "margin-bottom:8px" }, intro));
    }
    const list = el("div", { class: "ws-dirlist" });
    if (r.parent) {
      list.append(el("button", { class: "ws-dir", onclick: () => render(r.parent) }, "↩ .."));
    }
    for (const d of r.dirs) {
      list.append(el("button", { class: "ws-dir", onclick: () => render(d.path) },
        "📁 " + (r.path ? d.name : (d.path.replace(/^\/host/, "") || "/"))));
    }
    if (!r.dirs.length) list.append(el("div", { class: "ws-dir muted" }, "no subfolders"));
    body.append(list);
    if (r.path && r.files != null) {
      body.append(el("div", { class: "muted" }, `${r.dirs.length} folders · ${r.files} files`));
    }
  }
  render(start || "");
}

/* ----- default workspace ----- */

function setDefaultWsLabel(path) {
  const shown = (path || "").replace(/^\/host/, "") || "not set";
  $("#default-ws-path").textContent = shown;
}

$("#default-ws-change").addEventListener("click", () => openFolderPicker({
  title: "📁 Default workspace",
  start: state.me?.default_workspace || (state.me?.workspaces || [])[0] || "",
  intro: "Choose the folder new agent chats should start in.",
  chooseLabel: (p) => `Set default: ${p}`,
  onChoose: async (path) => {
    try {
      const r = await api("/settings/default-workspace", { method: "POST",
        body: JSON.stringify({ path }) });
      if (state.me) state.me.default_workspace = r.default_workspace;
      setDefaultWsLabel(r.default_workspace);
    } catch (e) { alert(e.message); }
  },
}));

/* ----- chat stream ----- */

function chatConnect() {
  if (state.chatWs && state.chatWs.readyState <= 1) return;
  const ws = new WebSocket(wsUrl("/ws/chat"));
  state.chatWs = ws;
  ws.onopen = () => {
    // re-attach to the last conversation — the server replays finished turns
    // AND any in-flight turn (it keeps running while we were away), then
    // streams live. Config comes back in the snapshot (multi-device truth).
    ws.send(JSON.stringify({ type: "open", id: state.chatId || "" }));
  };
  ws.onmessage = (ev) => handleChatEvent(JSON.parse(ev.data));
  ws.onclose = () => {
    state.chatWs = null;
    // auto-resume: the session keeps running server-side, so reconnect and pick
    // the live stream back up (unless the user signed out)
    if (state.token && !state.chatReconnectTimer) {
      state.chatReconnectTimer = setTimeout(() => {
        state.chatReconnectTimer = null;
        chatConnect();
      }, 2000);
    }
  };
}

// coming back to the foreground (phone unlocked / tab refocused): make sure the
// live chat stream is attached again so an in-flight agent turn keeps streaming
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.token &&
      (!state.chatWs || state.chatWs.readyState > 1)) {
    chatConnect();
  }
});

function chatLogEl() { return $("#chat-log"); }
function scrollChat(force = false) {
  const log = chatLogEl();
  // don't yank the view down while the user is reading older messages
  if (force || log.scrollHeight - log.scrollTop - log.clientHeight < 180) {
    log.scrollTop = log.scrollHeight;
  }
}
function removeThinking() { $$(".thinking").forEach((n) => n.remove()); }

function toolSummary(name, args) {
  if (name === "run_command") return args.command || "";
  if (name === "search_files") return args.pattern || "";
  if (name === "fetch_url") return args.url || "";
  if (name === "update_memory") return (args.mode || "append") + " memory";
  return args.path || JSON.stringify(args).slice(0, 90);
}

function toolCardEl(ev) {
  let card = document.getElementById("tool-" + ev.id);
  if (card) return card;
  card = el("div", { class: "tool-card", id: "tool-" + ev.id });
  const head = el("div", { class: "tool-head", onclick: () => card.classList.toggle("open") },
    el("span", { class: "tool-status" }, "⏳"),
    el("span", { class: "tool-name" }, ev.name),
    el("span", { class: "tool-summary" }, toolSummary(ev.name, ev.args)),
    ev.name === "run_command"
      ? el("button", { class: "tool-term", title: "open this command in the terminal",
          onclick: (e) => { e.stopPropagation(); openTerminalWithCommand(ev.args.command || ""); } }, "❯_")
      : null,
    el("span", { class: "chev" }, "▸"));
  const bodyPre = el("pre", {},
    ev.name === "run_command" ? ev.args.command
      : ev.name === "write_file" || ev.name === "edit_file"
        ? ev.args.path + "\n" + (ev.args.content || ev.args.new_text || "").slice(0, 1200)
        : JSON.stringify(ev.args, null, 1).slice(0, 1200));
  card.append(head, el("div", { class: "tool-body" }, bodyPre));
  chatLogEl().append(card);
  return card;
}

function setToolStatus(card, icon, cls) {
  card.querySelector(".tool-status").textContent = icon;
  card.classList.remove("approved", "denied");
  if (cls) card.classList.add(cls);
}

function ensureThinkBlock() {
  if (state.thinkEl) return state.thinkEl;
  const block = el("div", { class: "think-block" });
  const head = el("div", { class: "think-head", onclick: () => block.classList.toggle("open") },
    el("span", { class: "spin" }, "💭"), el("span", { class: "think-label" }, "thinking…"));
  const bodyEl = el("div", { class: "think-body" });
  block.append(head, bodyEl);
  chatLogEl().append(block);
  state.thinkEl = block;
  state.thinkRaw = "";
  return block;
}

function finishThinkBlock() {
  if (!state.thinkEl) return;
  const head = state.thinkEl.querySelector(".think-head .spin");
  if (head) head.classList.remove("spin");
  const label = state.thinkEl.querySelector(".think-label");
  if (label) label.textContent = `thought for a bit — tap to ${state.thinkEl.classList.contains("open") ? "hide" : "view"}`;
  state.thinkEl.classList.remove("open");
  state.thinkEl = null;
}

// the agent just brought up one or more services — surface them with actions
function renderServiceCards(items) {
  if (!items || !items.length) return;
  const log = chatLogEl();
  $(".chat-welcome")?.remove();
  for (const s of items) {
    const port = s.primary_port;
    const sub = [s.container, s.count > 1 ? `${s.count} containers` : null,
      port ? `port ${port}` : null].filter(Boolean).join(" · ");
    const actions = el("div", { class: "svc-actions" });
    if (port) {
      actions.append(el("button", { class: "btn small primary", onclick: () =>
        window.open(`http://${serverHost()}:${port}`, "_blank") }, "↗ Open"));
    }
    actions.append(el("button", { class: "btn small ai", onclick: () => openVibeWith(
      `The service "${s.label}" (container ${s.container}${port ? `, port ${port}` : ""}) is now ` +
      `running. Please finish setting it up properly for production: make sure it restarts on ` +
      `boot, put it behind my reverse proxy with HTTPS if I have one (otherwise tell me my ` +
      `options), and explain how to back up its data.`) }, "✦ Finish setup"));
    actions.append(el("button", { class: "btn small", onclick: () =>
      showLogs({ id: s.id, name: s.container, service: { icon: s.icon, label: s.label } }) }, "Logs"));
    log.append(el("div", { class: "svc-detected" },
      el("div", { class: "svc-detected-head" },
        iconTile(s.icon, s.label, "sm"),
        el("div", { style: "flex:1;min-width:0" },
          el("div", { class: "svc-detected-title" }, "New service · " + s.label),
          el("div", { class: "svc-detected-sub muted" }, sub)),
        el("span", { class: "dot " + (s.running ? "ok" : "warn") })),
      actions));
  }
  scrollChat();
}

function handleChatEvent(ev) {
  const log = chatLogEl();
  if (ev.type === "chat") {
    state.chatId = ev.id;
    localStorage.setItem("helmsman_chat", ev.id);
    setChatTitle(ev.title);
    renderChatEvents(ev.events);
    if (!ev.events || !ev.events.length) sendChatConfig();  // new chat: apply my prefs
    else adoptConfig(ev.config);                            // ongoing: reflect the truth
    setChatRunning(!!ev.running);
    if (ev.running && ev.live) ev.live.forEach(handleChatEvent);  // replay in-flight turn
    return;
  } else if (ev.type === "chat_meta") {
    state.chatId = ev.id;
    localStorage.setItem("helmsman_chat", ev.id);
    setChatTitle(ev.title);
    return;
  } else if (ev.type === "config") {
    adoptConfig(ev);
    return;
  } else if (ev.type === "run_state") {
    setChatRunning(!!ev.running);
    if (!ev.running) { removeThinking(); finishThinkBlock(); state.streamEl = null; }
    return;
  } else if (ev.type === "user_echo") {
    $(".chat-welcome")?.remove();
    removeThinking();
    state.streamEl = null;
    const bubble = el("div", { class: "msg user" }, ev.text);
    if (ev.queued) bubble.append(el("span", { class: "queued-tag" }, " · queued"));
    log.append(bubble);
    scrollChat(true);
    return;
  } else if (ev.type === "services") {
    renderServiceCards(ev.items);
    return;
  }
  if (ev.type === "text") {
    removeThinking();
    finishThinkBlock();
    if (!state.streamEl) {
      state.streamEl = el("div", { class: "msg assistant" });
      state.streamRaw = "";
      log.append(state.streamEl);
    }
    state.streamRaw += ev.delta;
    state.streamEl.innerHTML = renderMarkdown(state.streamRaw);
    scrollChat();
  } else if (ev.type === "thinking") {
    removeThinking();
    const block = ensureThinkBlock();
    state.thinkRaw += ev.delta;
    block.querySelector(".think-body").textContent = state.thinkRaw;
    scrollChat();
  } else if (ev.type === "tool_request" || ev.type === "tool_start") {
    removeThinking();
    finishThinkBlock();
    state.streamEl = null;
    const card = toolCardEl(ev);
    card.querySelector(".approve-row")?.remove();
    if (ev.type === "tool_request") {
      setToolStatus(card, "🖐", "");
      card.classList.add("open");
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
      setToolStatus(card, "⏳", "approved");
      card.classList.remove("open");
    }
    scrollChat();
  } else if (ev.type === "tool_result") {
    const card = document.getElementById("tool-" + ev.id);
    if (card) {
      card.querySelector(".approve-row")?.remove();
      const denied = ev.output === "[denied by user]";
      setToolStatus(card, denied ? "✕" : "✓", denied ? "denied" : "approved");
      card.classList.remove("open");
      card.querySelector(".tool-body").append(el("div", { class: "tool-output" },
        ev.output.length > 2200 ? ev.output.slice(0, 2200) + " …" : ev.output));
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
  } else if (ev.type === "stopped") {
    removeThinking();
    finishThinkBlock();
    log.append(el("div", { class: "msg note" }, "⏹ stopped"));
  } else if (ev.type === "done") {
    removeThinking();
    finishThinkBlock();
    state.streamEl = null;
    setChatRunning(false);
  } else if (ev.type === "error") {
    removeThinking();
    finishThinkBlock();
    state.streamEl = null;
    log.append(el("div", { class: "msg error" }, "⚠ " + ev.message));
    setChatRunning(false);
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

function setChatRunning(running) {
  state.chatRunning = running;
  $("#run-status").classList.toggle("hidden", !running);
}

function submitChat() {
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text) return;
  hideSlashHints();
  if (text.startsWith("/") && handleSlash(text)) {
    input.value = ""; input.style.height = "auto";
    return;
  }
  input.value = ""; input.style.height = "auto";
  // the server echoes the message to every device; while it runs the message
  // is queued/steered. The run-status bar is the "working" indicator.
  setChatRunning(true);
  sendChat({ type: "user", text });
}

$("#chat-send").addEventListener("click", submitChat);
$("#chat-stop").addEventListener("click", () => sendChat({ type: "stop" }));
$("#chat-handoff").addEventListener("click", openHandoff);
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !("ontouchstart" in window)) {
    e.preventDefault(); submitChat();
  }
});
$("#chat-input").addEventListener("input", function () {
  this.style.height = "auto";
  this.style.height = Math.min(this.scrollHeight, 120) + "px";
  renderSlashHints(this.value);
});

/* ----- slash commands ----- */

const SLASH = [
  { cmd: "/new", desc: "start a fresh chat", run: () => newChat() },
  { cmd: "/agent", desc: "Agent mode — asks before actions", run: () => switchMode("agent") },
  { cmd: "/auto", desc: "Auto mode — runs without asking", run: () => switchMode("auto") },
  { cmd: "/plan", desc: "Plan mode — read-only, proposes a plan", run: () => switchMode("plan") },
  { cmd: "/chat", desc: "Chat mode — talk only, no tools", run: () => switchMode("chat") },
  { cmd: "/terminal", desc: "open the terminal (e.g. /terminal htop)",
    run: (rest) => rest ? openTerminalWithCommand(rest) : showView("terminal") },
  { cmd: "/remote", desc: "continue this chat on another device", run: () => openHandoff() },
  { cmd: "/help", desc: "show what these commands do", run: () => showSlashHelp() },
];

function switchMode(mode) { setChatMode(mode); sendChatConfig(); toast(`Mode: ${mode}`); }

function handleSlash(text) {
  let [word, ...restArr] = text.split(" ");
  word = word.toLowerCase();
  if (word === "/remote-control" || word === "/handoff") word = "/remote";
  if (word === "/clear") word = "/new";
  const c = SLASH.find((s) => s.cmd === word);
  if (!c) return false;
  c.run(restArr.join(" ").trim());
  return true;
}

function renderSlashHints(value) {
  const box = $("#slash-hints");
  if (!value.startsWith("/") || value.includes("\n")) return hideSlashHints();
  const q = value.split(" ")[0].toLowerCase();
  const matches = SLASH.filter((s) => s.cmd.startsWith(q));
  if (!matches.length || (matches.length === 1 && matches[0].cmd === q && value.includes(" ")))
    return hideSlashHints();
  box.innerHTML = "";
  for (const s of matches) {
    box.append(el("button", { class: "slash-hint", onclick: () => {
      const input = $("#chat-input");
      // commands that take an argument keep the input open for typing
      if (s.cmd === "/terminal") { input.value = s.cmd + " "; input.focus(); renderSlashHints(input.value); }
      else { input.value = ""; hideSlashHints(); s.run(""); }
    } }, el("b", {}, s.cmd), el("span", { class: "muted" }, s.desc)));
  }
  box.classList.remove("hidden");
}
function hideSlashHints() { $("#slash-hints").classList.add("hidden"); }

function showSlashHelp() {
  const body = el("div", {});
  for (const s of SLASH) {
    body.append(el("div", { class: "slash-hint static" },
      el("b", {}, s.cmd), el("span", { class: "muted" }, s.desc)));
  }
  openModal("Slash commands", body);
}

/* ---------------------------------------------------------- terminal */

async function initTerminal() {
  await loadTermContexts();
  if (state.term && state.termWs && state.termWs.readyState <= 1) {
    state.fitAddon.fit();
    return;
  }
  connectTerminal();
}

async function openTerminalFor(c) {
  closeModal();
  await loadTermContexts();
  const sel = $("#term-context");
  const val = "container:" + c.id;
  if (![...sel.options].some((o) => o.value === val)) {
    sel.append(el("option", { value: val }, `${c.service?.icon || "🐳"} ${c.name}`));
  }
  sel.value = val;
  showView("terminal");        // initTerminal connects with the selected context
  connectTerminal();
}

// open the terminal and drop a command onto the prompt (ready to run & watch)
function openTerminalWithCommand(cmd) {
  closeModal();
  const sel = $("#term-context");
  const localLive = sel.value === "local" && state.termWs && state.termWs.readyState <= 1;
  sel.value = "local";
  showView("terminal");
  if (!localLive) connectTerminal();
  let tries = 0;
  const type = () => {
    if (state.termWs && state.termWs.readyState === 1) {
      // Ctrl-U clears any half-typed line first; no newline so the user runs it
      state.termWs.send(JSON.stringify({ type: "input", data: "\x15" + (cmd || "") }));
      state.term?.focus();
      toast("Command placed in terminal — press Enter to run");
    } else if (tries++ < 50) { setTimeout(type, 100); }
  };
  setTimeout(type, 120);
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

function termTheme() {
  return {
    background: cssVar("--term-bg"),
    foreground: cssVar("--term-fg"),
    cursor: cssVar("--accent"),
    selectionBackground: cssVar("--accent") + "44",
  };
}

function connectTerminal() {
  if (state.termWs) { try { state.termWs.close(); } catch {} }
  if (!state.term) {
    state.term = new Terminal({
      fontSize: 13.5,
      fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
      theme: termTheme(),
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
    api("/snapshots").then((r) => { state.snapshots = r.snapshots; renderUpdates(); }).catch(() => {});
    renderUpdates();
  } catch (e) {
    summary.textContent = "⚠ " + e.message;
  }
}

function renderUpdates() {
  const r = state.updates;
  if (!r) return;
  const wrap = $("#updates-list");
  const summary = $("#updates-summary");
  wrap.innerHTML = "";

  const docker = r.docker || [];
  const avail = docker.filter((u) => u.update_available && !u.ignored);
  const uptodate = docker.filter((u) => !u.update_available && !u.ignored && !u.error);
  const problems = docker.filter((u) => u.error && !u.ignored);
  const ignored = docker.filter((u) => u.ignored);

  summary.textContent = avail.length
    ? `${avail.length} update${avail.length > 1 ? "s" : ""} available`
    : "everything up to date ✓";
  updateHealthBadge();

  // --- available updates: dedupe by repo (several tags of the same app) ---
  if (avail.length) {
    const byRepo = new Map();
    for (const u of avail) {
      if (!byRepo.has(u.repo)) byRepo.set(u.repo, []);
      byRepo.get(u.repo).push(u);
    }
    const cards = el("div", { class: "cards", style: "margin-bottom:12px" });
    for (const group of byRepo.values()) cards.append(updateCard(group));
    if (avail.length > 1) {
      wrap.append(el("div", { class: "card-actions", style: "margin:0 0 10px" },
        el("button", {
          class: "btn primary", onclick: () => startUpdateAllJob(avail.map((u) => u.image)),
        }, `⬆ Update all (${avail.length})`),
        askAiButton("I have these pending updates on my server: " +
          avail.map((u) => `${u.label} (${u.image}, priority ${u.priority})`).join(", ") +
          ". Which should I apply first and is anything risky?", "✦ Which first?")));
    }
    wrap.append(cards);
  } else {
    wrap.append(el("div", { class: "card", style: "margin-bottom:12px" },
      el("div", { class: "card-row" },
        el("span", { style: "font-size:22px" }, "✅"),
        el("div", {},
          el("div", { class: "card-title" }, "All images up to date"),
          el("div", { class: "card-sub" }, "Helmsman checks the registries every 30 minutes")))));
  }

  // --- compact folds ---
  if (uptodate.length) {
    const rows = el("div", {});
    for (const u of uptodate) rows.append(miniUpdateRow(u));
    wrap.append(fold(`✓ Up to date (${uptodate.length})`, rows));
  }
  if (problems.length) {
    const rows = el("div", {});
    for (const u of problems) rows.append(miniUpdateRow(u));
    wrap.append(fold(`◌ Can't check (${problems.length})`, rows, false,
      el("span", { class: "muted", style: "font-weight:400;text-transform:none" }, "local builds etc.")));
  }
  if (ignored.length) {
    const rows = el("div", {});
    for (const u of ignored) rows.append(miniUpdateRow(u));
    wrap.append(fold(`🔕 Ignored (${ignored.length})`, rows));
  }

  // --- apt ---
  if (r.apt?.available && r.apt.packages.length) {
    const rows = el("div", {});
    for (const p of r.apt.packages.slice(0, 60)) {
      rows.append(el("div", { class: "mini-row" },
        el("span", {}, "📦"),
        el("div", { style: "min-width:0;flex:1" },
          el("div", { class: "name" }, p.package),
          el("div", { class: "sub" }, `${p.current} → ${p.new}`)),
        el("button", { class: "btn small", onclick: () => explainUpdate(p.package, "apt") }, "✦")));
    }
    rows.append(el("div", { class: "card-actions", style: "margin-top:8px" },
      askAiButton(`My server has ${r.apt.packages.length} upgradable system packages ` +
        `(apt). Please update them for me and tell me if a reboot is needed.`,
        "💬 Update with AI")));
    wrap.append(fold(`🖥 System packages (${r.apt.packages.length})`, rows));
  }

  // --- restore points (snapshots taken before updates) ---
  const snaps = state.snapshots || [];
  if (snaps.length) {
    const rows = el("div", {});
    for (const s of snaps) rows.append(snapshotRow(s));
    wrap.append(fold(`↩ Restore points (${snaps.length})`, rows, false,
      el("span", { class: "muted", style: "font-weight:400;text-transform:none" },
        "roll back a bad update")));
  }
}

function snapshotRow(s) {
  const svc = state.updates?.docker?.find((u) => u.image === s.image);
  return el("div", { class: "mini-row" },
    iconTile(svc?.icon || "📸", svc?.category || "Snapshot", "sm"),
    el("div", { style: "min-width:0;flex:1" },
      el("div", { class: "name" }, svc?.label || s.image),
      el("div", { class: "sub" }, `${s.image_id} · ${timeAgo(s.time)}` +
        (s.containers.length ? ` · ${s.containers.join(", ")}` : ""))),
    el("button", { class: "btn small primary", onclick: () => rollbackSnapshot(s) }, "↩ Roll back"),
    el("button", { class: "btn small", title: "delete restore point", onclick: async () => {
      if (!confirm("Delete this restore point? The pinned image is removed.")) return;
      await api(`/snapshots/${s.id}`, { method: "DELETE" }).catch((e) => alert(e.message));
      loadUpdates();
    } }, "✕"));
}

async function rollbackSnapshot(s) {
  if (!confirm(`Roll ${s.containers.join(", ") || s.image} back to the version from ` +
    `${new Date(s.time * 1000).toLocaleString()}?`)) return;
  const { job_id } = await api(`/snapshots/${s.id}/rollback`, { method: "POST" })
    .catch((e) => { alert(e.message); return {}; });
  if (!job_id) return;
  jobModal(`↩ Roll back ${s.image}`, job_id,
    `Recreates ${s.containers.join(", ") || "the containers"} on the previous image.`);
}

function miniUpdateRow(u) {
  return el("div", { class: "mini-row" },
    iconTile(u.icon, u.category, "sm"),
    el("div", { style: "min-width:0;flex:1" },
      el("div", { class: "name" }, u.label),
      el("div", { class: "sub" }, `${u.image}${u.error ? " · " + u.error : ""}`)),
    u.age_days != null ? el("span", { class: "chip" }, `${u.age_days}d`) : null,
    el("button", {
      class: "btn small", onclick: async () => {
        await api("/updates/ignore", { method: "POST",
          body: JSON.stringify({ image: u.image, ignored: !u.ignored }) });
        loadUpdates();
      },
    }, u.ignored ? "unignore" : "🔕"));
}

function updateCard(group) {
  const u = group[0];
  const multi = group.length > 1;
  const actions = el("div", { class: "card-actions" });
  if (!multi) {
    actions.append(el("button", {
      class: "btn small primary", onclick: () => startUpdateJob(u),
    }, "⬆ Update"));
  }
  actions.append(el("button", {
    class: "btn small ai", onclick: () => explainUpdate(u.image, "docker"),
  }, "✦ Explain"));
  if (u.links?.changelog) {
    actions.append(el("a", { class: "chip", href: u.links.changelog, target: "_blank" }, "changelog"));
  }
  actions.append(el("button", {
    class: "btn small", style: "margin-left:auto",
    onclick: async () => {
      await api("/updates/ignore", { method: "POST",
        body: JSON.stringify({ image: u.image, ignored: true }) });
      loadUpdates();
    },
  }, "🔕 ignore"));

  const card = el("div", { class: "card" },
    el("div", { class: "card-row" },
      iconTile(u.icon, u.category),
      el("div", { style: "min-width:0;flex:1" },
        el("div", { class: "card-title" }, `${u.label} `,
          el("span", { class: "prio " + (u.priority || "low") }, u.priority || "low")),
        el("div", { class: "card-sub" },
          multi ? `${group.length} versions of this app are running` : `new version available · used by ${u.used_by.join(", ")}`),
        el("div", { class: "card-sub", style: "opacity:.75" }, multi ? u.repo : u.image))));

  if (multi) {
    const rows = el("div", { style: "margin-top:8px" });
    for (const g of group.sort((a, b) => a.tag.localeCompare(b.tag))) {
      rows.append(el("div", { class: "mini-row" },
        el("span", { class: "chip" }, g.tag),
        el("div", { style: "min-width:0;flex:1" },
          el("div", { class: "sub" }, `used by ${g.used_by.join(", ")}`)),
        el("button", { class: "btn small primary", onclick: () => startUpdateJob(g) }, "⬆")));
    }
    card.append(rows,
      el("div", { class: "card-sub", style: "white-space:normal;margin-top:6px" },
        "ℹ Two containers run different versions of the same app — each updates within its own version line."));
  }
  card.append(actions);
  return card;
}

async function startUpdateJob(u) {
  const { job_id } = await api("/updates/apply", {
    method: "POST", body: JSON.stringify({ image: u.image, recreate: true }),
  }).catch((e) => { alert(e.message); return {}; });
  if (!job_id) return;
  jobModal(`⬆ ${u.label}`, job_id,
    `Pulls the new image and recreates: ${u.used_by.join(", ")}`);
}

async function startUpdateAllJob(images) {
  const { job_id } = await api("/updates/apply-all", {
    method: "POST", body: JSON.stringify({ images }),
  }).catch((e) => { alert(e.message); return {}; });
  if (!job_id) return;
  jobModal(`⬆ Update all (${images.length})`, job_id,
    "Updates run one after another — you can lock your phone, the job continues on the server.");
}

function jobModal(title, jobId, note, onDone) {
  const finish = onDone || (() => { loadUpdates(true); refreshDashboard(); });
  const logBox = el("div", { class: "joblog" }, "starting…\n");
  const done = el("div", {});
  openModal(title, el("div", {},
    note ? el("p", { class: "muted", style: "margin-bottom:4px" }, note) : null,
    logBox, done));
  followJob(jobId, logBox).then(() => {
    done.append(el("button", {
      class: "btn primary wide", style: "margin-top:6px",
      onclick: () => { closeModal(); finish(); },
    }, "Done — refresh"));
  }).catch(() => {
    const tail = logBox.textContent.split("\n").slice(-14).join("\n");
    done.append(
      el("div", { class: "error", style: "margin:6px 0" }, "The job hit a problem."),
      el("div", { class: "card-actions" },
        askAiButton(`An update job in Helmsman failed on my server. Job: ${title}. ` +
          `Here is the end of the log:\n\`\`\`\n${tail}\n\`\`\`\n` +
          `Please investigate the cause and fix it.`, "🛠 Fix with AI"),
        el("button", { class: "btn small", onclick: () => { closeModal(); loadUpdates(true); } }, "Close")));
  });
}

async function followJob(jobId, logBox) {
  const res = await fetch(apiBase() + `/api/jobs/${jobId}/stream`, {
    headers: { Authorization: "Bearer " + state.token },
  });
  if (!res.ok) throw new Error("stream failed");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  const lines = [];
  const foldable = (l) => l.startsWith("Layers ") || l.startsWith("⏳");
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
      if (foldable(line) && lines.length && foldable(lines[lines.length - 1]) &&
          line[0] === lines[lines.length - 1][0]) {
        lines[lines.length - 1] = line;
      } else {
        lines.push(line);
      }
    }
    render();
  }
  render();
  if (lines.some((l) => l.includes("[job error]"))) throw new Error("job failed");
}

$("#updates-refresh").addEventListener("click", () => loadUpdates(true));

async function explainUpdate(subject, kind) {
  openModal("✦ AI explanation", el("div", { class: "thinking" }, "Researching " + subject));
  try {
    const r = await api("/updates/explain", {
      method: "POST",
      body: JSON.stringify({ subject, kind, lang: navigator.language }),
    });
    const wrap = el("div", {});
    wrap.append(mdDiv(r.explanation));
    wrap.append(el("div", { class: "card-actions", style: "margin-top:12px" },
      askAiButton(`About the pending update for "${subject}" on my server — ` +
        `the summary said:\n${r.explanation.slice(0, 900)}\n\n` +
        `I have follow-up questions / want help applying it safely.`, "💬 Continue in chat")));
    setModalBody(wrap);
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
    el("span", { style: "font-size:26px" }, icons[r.score]),
    el("div", { style: "flex:1" },
      el("b", {}, r.score === "ok" ? "All good" : r.score === "warn" ? "Needs attention" : "Critical issues"),
      el("div", { class: "score-counts" },
        r.counts.crit ? el("span", { class: "pill crit" }, `${r.counts.crit} critical`) : null,
        r.counts.warn ? el("span", { class: "pill warn" }, `${r.counts.warn} warnings`) : null,
        el("span", { class: "pill ok" }, `${r.counts.ok} ok`))));

  const list = $("#checks-list");
  list.innerHTML = "";
  const order = { crit: 0, warn: 1, info: 2, ok: 3 };

  // group by check group, order groups by worst status inside
  const groups = new Map();
  for (const c of r.checks) {
    if (!groups.has(c.group)) groups.set(c.group, []);
    groups.get(c.group).push(c);
  }
  const sortedGroups = [...groups.entries()].sort((a, b) =>
    Math.min(...a[1].map((c) => order[c.status])) - Math.min(...b[1].map((c) => order[c.status])));

  for (const [group, checks] of sortedGroups) {
    list.append(el("div", { class: "check-group-head" }, group));
    for (const c of checks.sort((a, b) => order[a.status] - order[b.status])) {
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
      if (c.status === "warn" || c.status === "crit") {
        card.append(el("div", { class: "card-actions" },
          askAiButton(`The health check "${c.title}" on my server reports (${c.status}): ` +
            `${c.summary}. ${c.details ? "Details: " + c.details + ". " : ""}` +
            `${c.recommendation ? "Suggested fix: " + c.recommendation : ""}\n` +
            `Please investigate and fix this for me — explain what you do.`, "🛠 Fix with AI")));
      }
      list.append(card);
    }
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
    const wrap = el("div", {});
    wrap.append(mdDiv(r.analysis));
    wrap.append(el("div", { class: "card-actions", style: "margin-top:12px" },
      askAiButton("The AI analysis of my latest server health report said:\n" +
        r.analysis.slice(0, 1200) +
        "\n\nLet's work through the important points together — start with the most critical one.",
        "💬 Work through it in chat")));
    setModalBody(wrap);
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
    const { catalog, installed, catalog_info } = await api("/apps");
    wrap.append(catalogBar(catalog_info, catalog.length));
    const onServer = catalog.filter((a) => installed[a.id]);
    const available = catalog.filter((a) => !installed[a.id]);

    if (onServer.length) {
      wrap.append(el("div", { class: "check-group-head" }, "On this server"));
      const cards = el("div", { class: "cards", style: "margin-bottom:6px" });
      for (const app of onServer) cards.append(appCard(app, installed[app.id]));
      wrap.append(cards);
    }

    const byCat = new Map();
    for (const app of available) {
      if (!byCat.has(app.category)) byCat.set(app.category, []);
      byCat.get(app.category).push(app);
    }
    for (const [cat, apps] of [...byCat.entries()].sort((a, b) => b[1].length - a[1].length)) {
      wrap.append(el("div", { class: "check-group-head" }, cat));
      const cards = el("div", { class: "cards", style: "margin-bottom:6px" });
      for (const app of apps) cards.append(appCard(app, null));
      wrap.append(cards);
    }
  } catch (e) {
    wrap.append(el("div", { class: "card" }, "Failed to load: " + e.message));
  }
}

function catalogBar(info, total) {
  const bar = el("div", { class: "catalog-bar" });
  const extra = info?.remote_count || 0;
  bar.append(el("span", { class: "muted" },
    `${total} apps` + (extra ? ` · ${extra} from the online catalog` : "")));
  if (info?.enabled) {
    const btn = el("button", { class: "btn small", title: "refresh online catalog" }, "⟳");
    btn.addEventListener("click", async () => {
      btn.textContent = "…"; btn.disabled = true;
      try { await api("/apps/catalog/refresh", { method: "POST" }); await loadApps(); }
      catch (e) { alert(e.message); btn.textContent = "⟳"; btn.disabled = false; }
    });
    bar.append(el("span", { class: "spacer" }));
    if (info.error) bar.append(el("span", { class: "chip", title: info.error }, "⚠ offline"));
    bar.append(btn);
  }
  return bar;
}

function appCard(app, inst) {
  const chips = [];
  if (inst) {
    chips.push(el("span", { class: "pill " + (inst.running ? "ok" : "crit") },
      inst.running ? "running" : "stopped"));
    if (inst.source === "external") chips.push(el("span", { class: "pill dim" }, "self-managed"));
  }
  const card = el("div", { class: "card app-card" });
  const head = el("div", { class: "card-row" },
    iconTile(app.icon, app.category),
    el("div", { style: "min-width:0;flex:1" },
      el("div", { class: "card-title" }, app.name),
      el("div", { class: "card-sub app-tagline" }, app.tagline || app.description)),
    ...chips,
    el("span", { class: "chev muted", style: "font-size:11px" }, "▸"));

  const details = el("div", { class: "app-details" });
  if (app.why) details.append(el("div", { class: "app-why" }, app.why));
  details.append(el("div", { class: "card-sub", style: "white-space:normal;margin-bottom:8px" }, app.description));

  const actions = el("div", { class: "card-actions", style: "margin-top:4px" });
  if (inst) {
    for (const p of inst.ports || []) {
      actions.append(el("a", { class: "chip", href: `http://${serverHost()}:${p}`,
        target: "_blank", onclick: (e) => e.stopPropagation() }, `open :${p}`));
    }
    if (inst.source === "helmsman") {
      actions.append(el("button", { class: "btn small danger",
        onclick: (e) => { e.stopPropagation(); uninstallApp(app); } }, "Uninstall"));
    } else {
      details.append(el("div", { class: "card-sub", style: "white-space:normal;margin-bottom:6px" },
        `ℹ Already running on your server (installed outside Helmsman: ${inst.containers.join(", ")}). ` +
        `Helmsman keeps it updated via Health → Updates.`));
    }
    actions.append(askAiButton(`Tell me about "${app.name}" running on my server ` +
      `(containers: ${(inst.containers || []).join(", ")}). How is it doing, is it configured well?`));
  } else {
    actions.append(el("button", { class: "btn small primary",
      onclick: (e) => { e.stopPropagation(); installDialog(app); } }, "Install"));
    actions.append(askAiButton(`Should I install "${app.name}" on my server? ` +
      `(${app.tagline || app.description}) What do I need to know, and can you help me set it up nicely?`,
      "✦ Ask first"));
  }
  if (app.website) {
    actions.append(el("a", { class: "chip", href: app.website, target: "_blank",
      onclick: (e) => e.stopPropagation() }, "website"));
  }
  for (const cl of app.clients || []) {
    actions.append(el("a", { class: "chip", href: cl.url, target: "_blank",
      onclick: (e) => e.stopPropagation() }, "📱 " + cl.name));
  }
  details.append(actions);
  card.append(head, details);
  card.addEventListener("click", (e) => {
    if (e.target.closest("a,button")) return;
    card.classList.toggle("open");
    head.querySelector(".chev").style.transform =
      card.classList.contains("open") ? "rotate(90deg)" : "";
  });
  return card;
}

function installDialog(app) {
  const inputs = {};
  const form = el("div", {});
  if (app.why) form.append(el("div", { class: "app-why" }, app.why));
  form.append(el("p", { class: "muted", style: "margin-bottom:12px" }, app.description));
  for (const f of app.fields || []) {
    const input = el("input", { type: "text", value: f.default });
    inputs[f.key] = input;
    form.append(el("label", {}, f.label, input));
  }
  const status = el("div", { class: "muted", style: "margin-top:8px" });
  const extra = el("div", {});
  const btn = el("button", {
    class: "btn primary wide",
    onclick: async () => {
      btn.disabled = true;
      status.textContent = "Deploying… (pulling image, this can take a few minutes)";
      try {
        const values = Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, i.value]));
        await api(`/apps/${app.id}/install`, { method: "POST", body: JSON.stringify({ values }) });
        status.textContent = "✓ Installed!";
        const port = values.PORT;
        if (port) {
          extra.append(el("a", { class: "btn wide", style: "display:block;text-align:center;margin-top:8px",
            href: `http://${serverHost()}:${port}`, target: "_blank" }, `Open ${app.name} →`));
        }
        extra.append(el("button", { class: "btn small", style: "margin-top:8px",
          onclick: () => { closeModal(); loadApps(); } }, "Back to App Store"));
      } catch (e) {
        status.textContent = "⚠ " + e.message;
        extra.innerHTML = "";
        extra.append(el("div", { class: "card-actions", style: "margin-top:8px" },
          askAiButton(`Installing "${app.name}" via the Helmsman app store failed with:\n` +
            `${String(e.message).slice(0, 1200)}\n` +
            `The compose file is at /data/apps/${app.id}/docker-compose.yml inside the helmsman ` +
            `container (project helmsman-${app.id}). Please find the cause and fix the installation.`,
            "🛠 Fix with AI")));
        btn.disabled = false;
      }
    },
  }, `Install ${app.name}`);
  form.append(btn, status, extra);
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
    $("#server-name").value = me.server_name || "";
    for (const p of ["anthropic", "openrouter", "openai"]) {
      $("#key-" + p).placeholder = me.ai_providers.includes(p)
        ? "•••••• configured" : $("#key-" + p).placeholder;
    }
    $("#ai-status").textContent = me.ai_configured
      ? `✓ configured: ${me.ai_providers.join(", ")}`
      : "No AI provider yet — Vibe Code, Explain and AI analysis need a key.";
    $("#workspaces-input").value = (me.workspaces || []).join("\n");
    setDefaultWsLabel(me.default_workspace || (me.workspaces || [])[0] || "");
    $("#report-auto").checked = me.report_config.auto;
    $("#report-interval").value = String(me.report_config.interval_min);
    $("#app-version").textContent = me.version ? "v" + me.version : "";
    loadCatalogSetting();
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
    // agent instructions + memory
    api("/agent/instructions").then((r) => { $("#agent-instructions").value = r.instructions || ""; }).catch(() => {});
    api("/agent/memory").then((r) => { $("#agent-memory").value = r.memory; }).catch(() => {});
    loadToolToggles();
    loadServerIdentity();
    loadUsers();
    loadUsage();
    loadLocalAI();
    renderThemeGrid();
    renderSecurity();
    loadLoops();
    loadIntegrations();
  } catch {}
}

/* ----- app catalog setting ----- */

async function loadCatalogSetting() {
  try {
    const { catalog_info } = await api("/apps");
    $("#catalog-url").value = catalog_info?.url || "";
    catalogStatus(catalog_info);
  } catch {}
}

function catalogStatus(info) {
  const s = $("#catalog-status");
  if (!info || !info.enabled) { s.textContent = "Using the built-in catalog only."; return; }
  if (info.error) { s.textContent = "⚠ " + info.error; return; }
  s.textContent = info.fetched
    ? `✓ ${info.remote_count} apps from the online catalog · updated ${timeAgo(info.fetched)}`
    : "Configured — will fetch shortly.";
}

$("#catalog-save").addEventListener("click", async function () {
  this.textContent = "…";
  try {
    const info = await api("/apps/catalog/url", {
      method: "POST", body: JSON.stringify({ url: $("#catalog-url").value }) });
    catalogStatus(info);
    this.textContent = "✓ saved";
  } catch (e) { $("#catalog-status").textContent = "⚠ " + e.message; this.textContent = "Save"; }
  setTimeout(() => (this.textContent = "Save"), 1200);
});

$("#catalog-refresh").addEventListener("click", async function () {
  this.textContent = "…"; this.disabled = true;
  try { catalogStatus(await api("/apps/catalog/refresh", { method: "POST" })); }
  catch (e) { $("#catalog-status").textContent = "⚠ " + e.message; }
  this.textContent = "⟳ Refresh now"; this.disabled = false;
});

/* ----- local AI (Ollama) ----- */

async function loadLocalAI() {
  const box = $("#localai-body");
  let st;
  try { st = await api("/localai/status"); }
  catch (e) { box.innerHTML = ""; box.append(el("div", { class: "error" }, e.message)); return; }
  box.innerHTML = "";

  if (!st.running) {
    if (st.existing) {
      // an Ollama container exists but isn't reachable — offer to connect, not reinstall
      box.append(el("p", { class: "muted", style: "margin-bottom:8px" },
        `You already run Ollama (container “${st.existing.name}”), but Helmsman can’t reach it — ` +
        "it’s on a different Docker network or bound to localhost. Connect Helmsman to it in one tap."));
      box.append(el("button", { class: "btn primary", onclick: (e) => connectLocalAI(e.target) },
        "🔗 Connect to my Ollama"));
      box.append(localAiEndpointFold(st));
      return;
    }
    box.append(el("p", { class: "muted", style: "margin-bottom:8px" },
      `No local model runtime detected yet. This server has ${st.ram_gb} GB RAM · ${st.cpu_count} CPU cores.`));
    if (st.can_install) {
      box.append(el("button", { class: "btn primary", onclick: installLocalAI },
        "⤓ Set up local AI (install Ollama)"));
      box.append(el("p", { class: "muted", style: "margin-top:8px" },
        "One tap — Helmsman runs Ollama as a container. The first download is ~1 GB; then pick a model below."));
    } else {
      box.append(el("p", { class: "muted" },
        "Install Ollama on your server (or point Helmsman at one below) and it’ll appear here."));
    }
    box.append(localAiEndpointFold(st));
    return;
  }

  box.append(el("div", { class: "localai-status" },
    el("span", { class: "pill ok" }, "● running"),
    el("span", { class: "muted" }, `Ollama ${st.version || ""} · ` +
      (st.base || "").replace(/^https?:\/\//, ""))));

  if (st.installed.length) {
    box.append(el("div", { class: "sec-title", style: "margin-top:12px" }, "Installed models"));
    const list = el("div", { class: "localai-models" });
    for (const m of st.installed) {
      list.append(el("div", { class: "localai-model" },
        el("div", { style: "flex:1;min-width:0" },
          el("div", { class: "card-title" }, m.name),
          el("div", { class: "card-sub muted" },
            [m.params, m.size ? fmtBytes(m.size) : null].filter(Boolean).join(" · "))),
        el("button", { class: "btn small danger", onclick: () => deleteLocalModel(m.name) }, "Delete")));
    }
    box.append(list);
  } else {
    box.append(el("p", { class: "muted", style: "margin-top:6px" },
      "No models installed yet — download one below to start chatting locally."));
  }

  const recs = (st.recommended || []).filter((r) => !r.installed);
  if (recs.length) {
    box.append(el("div", { class: "sec-title", style: "margin-top:14px" }, "Recommended for your hardware"));
    const wrap = el("div", { class: "localai-recs" });
    for (const r of recs) {
      const badge = r.suggested ? el("span", { class: "chip ok" }, "Best fit")
        : !r.fits ? el("span", { class: "chip warn" }, `needs ~${r.min_ram} GB`) : null;
      wrap.append(el("div", { class: "localai-rec" + (r.suggested ? " suggested" : "") },
        el("div", { class: "localai-rec-head" }, el("b", {}, r.label), badge),
        el("div", { class: "muted", style: "margin:2px 0 8px" }, r.blurb),
        el("div", { class: "localai-rec-foot" },
          el("span", { class: "muted" }, `${r.params} · ${r.size}`),
          el("button", { class: "btn small primary", onclick: () => pullLocalModel(r.name) }, "⤓ Download"))));
    }
    box.append(wrap);
  }
  box.append(localAiEndpointFold(st));
}

function localAiEndpointFold(st) {
  const input = el("input", { type: "url", placeholder: "http://host:11434",
    inputmode: "url", autocapitalize: "off",
    value: st.base && !st.base.includes("127.0.0.1") ? st.base : "" });
  return fold("Advanced: custom endpoint", el("div", {},
    el("p", { class: "muted", style: "margin:6px 0" },
      "Point Helmsman at an Ollama running elsewhere (another host or a shared container network). " +
      "Leave blank to auto-detect."),
    input,
    el("button", { class: "btn small", style: "margin-top:8px", onclick: async () => {
      try {
        await api("/localai/base", { method: "POST", body: JSON.stringify({ base: input.value.trim() }) });
        toast("Saved"); refreshLocalAndModels();
      } catch (e) { alert(e.message); }
    } }, "Save endpoint")));
}

async function installLocalAI() {
  const { job_id } = await api("/localai/install", { method: "POST" })
    .catch((e) => { alert(e.message); return {}; });
  if (!job_id) return;
  jobModal("⤓ Install local AI", job_id,
    "Sets up Ollama on your server. The first image pull is ~1 GB — you can lock your phone, it keeps going.",
    refreshLocalAndModels);
}

async function connectLocalAI(btn) {
  if (btn) { btn.textContent = "connecting…"; btn.disabled = true; }
  try {
    await api("/localai/connect", { method: "POST" });
    toast("Connected to Ollama");
    refreshLocalAndModels();
  } catch (e) {
    alert(e.message);
    if (btn) { btn.textContent = "🔗 Connect to my Ollama"; btn.disabled = false; }
  }
}

async function pullLocalModel(name) {
  const { job_id } = await api("/localai/pull", { method: "POST", body: JSON.stringify({ model: name }) })
    .catch((e) => { alert(e.message); return {}; });
  if (!job_id) return;
  jobModal(`⤓ Download ${name}`, job_id,
    "Downloads the model onto your server. Bigger models take a while — the job runs server-side.",
    refreshLocalAndModels);
}

async function deleteLocalModel(name) {
  if (!confirm(`Delete the local model “${name}”? You can download it again later.`)) return;
  try { await api("/localai/delete", { method: "POST", body: JSON.stringify({ model: name }) }); }
  catch (e) { alert(e.message); return; }
  toast("Model removed");
  refreshLocalAndModels();
}

function refreshLocalAndModels() {
  loadLocalAI();
  modelsReady = false;         // repopulate the chat model picker with local models
  populateModels();
}

/* ----- server identity ----- */

async function loadServerIdentity() {
  const box = $("#server-identity");
  let d;
  try { d = await api("/server/identity"); }
  catch (e) { box.innerHTML = ""; box.append(el("div", { class: "error" }, e.message)); return; }
  box.innerHTML = "";
  const rows = [
    ["🖥", "Hostname", d.hostname],
    ["💿", "Operating system", d.os],
    ["🧩", "Kernel", `${d.kernel} · ${d.arch}`],
    ["👥", "Accounts", `${d.counts.human} people · ${d.counts.admins} admin · ${d.counts.system} service`],
  ];
  const grid = el("div", { class: "ident-grid" });
  for (const [ico, k, v] of rows) {
    grid.append(el("div", { class: "ident-row" },
      el("span", { class: "ident-ico" }, ico),
      el("div", { style: "min-width:0" },
        el("div", { class: "ident-k" }, k),
        el("div", { class: "ident-v" }, v || "—"))));
  }
  box.append(grid);
  const pill = d.host_access
    ? el("span", { class: "pill ok" }, "full control")
    : el("span", { class: "pill dim" }, "read-only");
  box.append(el("div", { class: "ident-foot" },
    el("span", { class: "muted" }, d.in_container ? "Helmsman runs in a container with host access" : "Helmsman runs directly on the host"),
    pill));
}

$("#server-name-save").addEventListener("click", async function () {
  try {
    const r = await api("/settings/server", {
      method: "POST", body: JSON.stringify({ name: $("#server-name").value }) });
    $("#host-name-text").textContent = r.server_name;
    this.textContent = "✓ saved";
    setTimeout(() => (this.textContent = "Save name"), 1200);
  } catch (e) { $("#server-status").textContent = "⚠ " + e.message; }
});

$("#pw-save").addEventListener("click", async () => {
  const status = $("#pw-status");
  try {
    const r = await api("/settings/password", {
      method: "POST",
      body: JSON.stringify({ current: $("#pw-current").value, new: $("#pw-new").value }),
    });
    $("#pw-current").value = $("#pw-new").value = "";
    // password change revoked all sessions — keep *this* one alive with the fresh token
    if (r.token) setActiveToken(r.token);
    status.textContent = "✓ password changed — other devices were signed out";
  } catch (e) { status.textContent = "⚠ " + e.message; }
});

$("#agent-instructions-save").addEventListener("click", async function () {
  try {
    await api("/agent/instructions", {
      method: "POST", body: JSON.stringify({ instructions: $("#agent-instructions").value }) });
    this.textContent = "✓ saved";
    $("#agent-status").textContent = "✓ Instructions saved — the agent will follow them from the next message.";
    setTimeout(() => (this.textContent = "Save instructions"), 1200);
  } catch (e) { $("#agent-status").textContent = "⚠ " + e.message; }
});

$("#agent-memory-save").addEventListener("click", async function () {
  try {
    await api("/agent/memory", {
      method: "POST", body: JSON.stringify({ memory: $("#agent-memory").value }) });
    this.textContent = "✓ saved";
    setTimeout(() => (this.textContent = "Save memory"), 1200);
  } catch (e) { alert(e.message); }
});

/* ----- agent tools (on/off) ----- */

async function loadToolToggles() {
  const box = $("#agent-tools");
  let tools;
  try { ({ tools } = await api("/agent/tools")); }
  catch (e) { box.innerHTML = ""; box.append(el("div", { class: "error" }, e.message)); return; }
  box.innerHTML = "";
  for (const t of tools) {
    const sw = el("input", { type: "checkbox" });
    sw.checked = t.enabled;
    sw.addEventListener("change", async () => {
      sw.disabled = true;
      try {
        await api(`/agent/tools/${t.name}`, { method: "POST",
          body: JSON.stringify({ enabled: sw.checked }) });
        row.classList.toggle("off", !sw.checked);
      } catch (e) { sw.checked = !sw.checked; alert(e.message); }
      sw.disabled = false;
    });
    const row = el("div", { class: "tool-toggle" + (t.enabled ? "" : " off") },
      el("div", { class: "tt-main" },
        el("div", { class: "tt-name" },
          el("b", { class: t.safe ? "safe" : "" }, t.name),
          t.safe ? el("span", { class: "pill ok" }, "read-only")
                 : el("span", { class: "pill dim" }, "asks first")),
        el("div", { class: "tt-desc muted" }, t.description)),
      el("label", { class: "switch" }, sw, el("span", { class: "slider" })));
    box.append(row);
  }
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
    $("#vibe-no-ai")?.classList.toggle("hidden", state.aiConfigured);
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
    setTimeout(() => (this.textContent = "Save allowed folders"), 1200);
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

/* ------------------------------------------------- people & access */

let usersState = { canManage: false };

async function loadUsers() {
  const list = $("#users-list");
  const addRow = $("#users-add");
  let r;
  try { r = await api("/server/users"); }
  catch (e) { list.innerHTML = ""; list.append(el("div", { class: "error" }, e.message)); return; }
  usersState = { canManage: r.can_manage, users: r.users };
  const humans = r.users.filter((u) => u.kind === "human");
  const system = r.users.filter((u) => u.kind === "system");
  $("#users-count").textContent = `${humans.length} people · ${system.length} service`;
  $("#users-manage-note").textContent = r.can_manage
    ? "You can change passwords and access rights right here."
    : (r.reason || "Accounts are read-only in this environment.");

  list.innerHTML = "";
  for (const u of humans) list.append(userRow(u));
  if (system.length) {
    const rows = el("div", {});
    for (const u of system) rows.append(userRow(u));
    list.append(fold(`⚙ Service accounts (${system.length})`, rows, false,
      el("span", { class: "muted", style: "font-weight:400;text-transform:none" }, "background workers, not people")));
  }

  addRow.innerHTML = "";
  if (r.can_manage) {
    addRow.append(el("button", { class: "btn small primary", onclick: () => createUserModal() }, "＋ Add a person"));
  }
  addRow.append(askAiButton("Explain the user accounts on my server in plain language — " +
    "who can log in, who has admin rights, and whether anything looks risky or unused.",
    "✦ Explain my users"));
}

function userIcon(u) {
  if (u.locked) return "🔒";
  if (u.is_root) return "👑";
  if (u.is_admin) return "🛡";
  if (u.kind === "system") return "⚙️";
  return "👤";
}

function userRow(u) {
  const chips = [];
  if (u.is_admin) chips.push(el("span", { class: "pill warn" }, "admin"));
  if (u.locked) chips.push(el("span", { class: "pill crit" }, "locked"));
  else if (!u.can_login) chips.push(el("span", { class: "pill dim" }, "no login"));
  if (u.in_docker) chips.push(el("span", { class: "pill dim" }, "docker"));
  return el("button", { class: "user-row", onclick: () => manageUserModal(u) },
    el("span", { class: "user-ico" }, userIcon(u)),
    el("div", { style: "min-width:0;flex:1;text-align:left" },
      el("div", { class: "user-name" }, u.name,
        u.gecos ? el("span", { class: "muted", style: "font-weight:400" }, "  " + u.gecos) : null),
      el("div", { class: "user-role" }, u.role)),
    el("div", { class: "user-chips" }, ...chips),
    el("span", { class: "chev" }, "▸"));
}

function manageUserModal(u) {
  const body = el("div", {});
  const status = el("div", { class: "muted", style: "margin-top:8px" });

  body.append(el("div", { class: "cd-head" },
    el("span", { class: "user-ico lg" }, userIcon(u)),
    el("div", { style: "min-width:0;flex:1" },
      el("div", { class: "cd-title" }, u.name),
      el("div", { class: "cd-sub" }, u.role))));

  const grid = el("dl", { class: "detail-grid" });
  const row = (k, v) => { if (v || v === 0) grid.append(el("dt", {}, k), el("dd", {}, String(v))); };
  row("User ID", u.uid);
  row("Home", u.home);
  row("Login shell", u.shell);
  row("Can sign in", u.can_login ? "yes" : "no");
  row("Administrator", u.is_admin ? "yes (can use sudo)" : "no");
  row("Groups", u.groups.join(", "));
  body.append(grid);

  if (usersState.canManage && !u.is_root) {
    const refresh = () => { closeModal(); loadUsers(); };
    // change password
    const pwIn = el("input", { type: "password", placeholder: "New password (min. 6)", autocomplete: "new-password" });
    body.append(el("div", { class: "sec-title", style: "margin-top:6px" }, "Change password"),
      el("div", { class: "user-action" }, pwIn,
        el("button", { class: "btn small primary", onclick: async function () {
          if (!pwIn.value) { status.textContent = "Enter a new password first."; return; }
          this.disabled = true; status.textContent = "updating…";
          try {
            const res = await api(`/server/users/${encodeURIComponent(u.name)}/password`,
              { method: "POST", body: JSON.stringify({ password: pwIn.value }) });
            status.textContent = "✓ " + res.message; pwIn.value = "";
          } catch (e) { status.textContent = "⚠ " + e.message; }
          this.disabled = false;
        } }, "Set")));
    // lock / admin toggles
    const actions = el("div", { class: "card-actions", style: "margin-top:12px" });
    actions.append(
      el("button", { class: "btn small", onclick: () => userAction(u, "lock", !u.locked,
        u.locked ? `Unlock ${u.name} so they can sign in again?` : `Lock ${u.name} out of signing in?`, refresh) },
        u.locked ? "🔓 Unlock login" : "🔒 Lock login"),
      el("button", { class: "btn small", onclick: () => userAction(u, "admin", !u.is_admin,
        u.is_admin ? `Remove administrator rights from ${u.name}?` : `Make ${u.name} an administrator (sudo)?`, refresh) },
        u.is_admin ? "Revoke admin" : "👑 Make admin"));
    body.append(actions);
  } else if (!u.is_root) {
    body.append(el("p", { class: "muted", style: "margin-top:8px" },
      usersState.canManage ? "" : "This environment is read-only for accounts. You can still ask the AI to help."));
  }

  // AI fallback — always available
  body.append(el("div", { class: "card-actions", style: "margin-top:12px" },
    askAiButton(`Help me manage the Linux user "${u.name}" on my server ` +
      `(uid ${u.uid}, ${u.is_admin ? "administrator" : "standard user"}, ` +
      `login ${u.can_login ? "enabled" : "disabled"}). What would you like to do?`,
      "💬 Manage with AI")));
  body.append(status);
  openModal("Manage user", body);
}

async function userAction(u, kind, value, confirmMsg, done) {
  if (!confirm(confirmMsg)) return;
  try {
    await api(`/server/users/${encodeURIComponent(u.name)}/${kind}`,
      { method: "POST", body: JSON.stringify({ value }) });
    done();
  } catch (e) { alert(e.message); }
}

function createUserModal() {
  const nameIn = el("input", { type: "text", placeholder: "username (letters/digits, e.g. anna)" });
  const pwIn = el("input", { type: "password", placeholder: "password (min. 6)", autocomplete: "new-password" });
  const adminCb = el("input", { type: "checkbox" });
  const status = el("div", { class: "muted", style: "margin-top:8px" });
  openModal("＋ Add a person", el("div", {},
    el("p", { class: "muted", style: "margin-bottom:10px" },
      "Creates a real Linux login account on your server with its own home folder."),
    el("label", {}, "Username", nameIn),
    el("label", {}, "Password", pwIn),
    el("label", { class: "row-label" }, adminCb, "Make this person an administrator (can use sudo)"),
    el("button", { class: "btn primary wide", style: "margin-top:8px", onclick: async function () {
      this.disabled = true; status.textContent = "creating…";
      try {
        const r = await api("/server/users", { method: "POST", body: JSON.stringify({
          name: nameIn.value.trim(), password: pwIn.value, admin: adminCb.checked }) });
        status.textContent = "✓ " + r.message;
        setTimeout(() => { closeModal(); loadUsers(); }, 700);
      } catch (e) { status.textContent = "⚠ " + e.message; this.disabled = false; }
    } }, "Create account"),
    status));
}

/* ------------------------------------------------------- AI usage */

const usageState = { days: 30, metric: "cost", data: null };

async function loadUsage() {
  try {
    usageState.data = await api(`/ai/usage/series?days=${usageState.days}`);
    renderUsage();
  } catch (e) {
    $("#usage-models").innerHTML = "";
    $("#usage-models").append(el("div", { class: "error" }, e.message));
  }
}

function fmtCost(c) { return c == null ? "$0" : "$" + (c < 0.01 && c > 0 ? c.toFixed(4) : c.toFixed(2)); }
function fmtUsageVal(v, metric) { return metric === "cost" ? fmtCost(v) : fmtTokens(v) + " tok"; }

function renderUsage() {
  const d = usageState.data;
  if (!d) return;
  const m = usageState.metric;
  const tokTotal = (s) => s.input + s.output;
  $("#usage-today").textContent = m === "cost" ? fmtCost(d.today.cost) : fmtTokens(tokTotal(d.today)) + " tok";
  $("#usage-month").textContent = m === "cost" ? fmtCost(d.month.cost) : fmtTokens(tokTotal(d.month)) + " tok";
  $("#usage-range-total").textContent = m === "cost" ? fmtCost(d.total.cost) : fmtTokens(tokTotal(d.total)) + " tok";
  $("#usage-range-label").textContent = `last ${d.range_days} days · ${d.total.requests} requests`;
  drawUsageBars($("#usage-canvas"), d.days, m);
  // per-model breakdown
  const box = $("#usage-models");
  box.innerHTML = "";
  if (!d.models.length) {
    box.append(el("p", { class: "muted", style: "margin-top:8px" },
      "No AI usage recorded in this window yet."));
    return;
  }
  box.append(el("div", { class: "check-group-head", style: "margin:14px 2px 6px" }, "By model"));
  const maxV = Math.max(...d.models.map((x) => m === "cost" ? x.cost : tokTotal(x)), 1e-9);
  for (const mdl of d.models) {
    const v = m === "cost" ? mdl.cost : tokTotal(mdl);
    const bar = el("div", { class: "umbar" });
    bar.style.width = Math.max(3, (v / maxV) * 100) + "%";
    box.append(el("div", { class: "usage-model" },
      el("div", { class: "um-top" },
        el("span", { class: "um-name" }, mdl.model.split("/").pop()),
        el("span", { class: "um-val" }, fmtUsageVal(v, m))),
      el("div", { class: "um-track" }, bar),
      el("div", { class: "um-sub muted" },
        `${fmtTokens(mdl.input)}→${fmtTokens(mdl.output)} tok · ${mdl.requests} req` +
        (m === "tokens" && mdl.cost ? ` · ${fmtCost(mdl.cost)}` : ""))));
  }
  if (!d.priced) {
    box.append(el("p", { class: "muted", style: "margin-top:8px" },
      "ℹ Costs are estimates; models without known pricing show tokens only."));
  }
}

function drawUsageBars(canvas, series, metric) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 560, h = canvas.clientHeight || 160;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  const val = (s) => metric === "cost" ? s.cost : s.input + s.output;
  const vals = series.map(val);
  const max = Math.max(...vals, metric === "cost" ? 0.001 : 1);
  const padT = 14, padB = 16, padL = 4, padR = 4;
  const n = series.length;
  const bw = (w - padL - padR) / n;
  const barW = Math.max(1.5, bw * 0.68);
  const y = (v) => padT + (1 - v / max) * (h - padT - padB);
  // grid line at top
  ctx.strokeStyle = chartColor("--border") + "cc"; ctx.lineWidth = 1;
  for (const frac of [0, 0.5]) {
    ctx.beginPath(); ctx.moveTo(padL, padT + frac * (h - padT - padB));
    ctx.lineTo(w - padR, padT + frac * (h - padT - padB)); ctx.stroke();
  }
  const accent = chartColor("--chart-1"), accent2 = chartColor("--chart-2");
  series.forEach((s, i) => {
    const v = val(s);
    const x = padL + i * bw + (bw - barW) / 2;
    const barH = Math.max(v > 0 ? 2 : 0, h - padB - y(v));
    ctx.fillStyle = i === n - 1 ? accent2 : accent;
    if (v <= 0) ctx.fillStyle = chartColor("--border");
    const yy = h - padB - barH;
    const rr = Math.min(3, barW / 2);
    ctx.beginPath();
    ctx.moveTo(x, h - padB);
    ctx.lineTo(x, yy + rr);
    ctx.quadraticCurveTo(x, yy, x + rr, yy);
    ctx.lineTo(x + barW - rr, yy);
    ctx.quadraticCurveTo(x + barW, yy, x + barW, yy + rr);
    ctx.lineTo(x + barW, h - padB);
    ctx.closePath(); ctx.fill();
  });
  // labels: max value (top) + first/last date
  ctx.fillStyle = cssVar("--muted"); ctx.font = "10.5px sans-serif";
  ctx.fillText(metric === "cost" ? fmtCost(max) : fmtTokens(max), padL + 2, padT - 3);
  ctx.textAlign = "left";
  ctx.fillText(shortDate(series[0].date), padL, h - 4);
  ctx.textAlign = "right";
  ctx.fillText(shortDate(series[n - 1].date), w - padR, h - 4);
  ctx.textAlign = "left";
}

function shortDate(iso) {
  const p = (iso || "").split("-");
  return p.length === 3 ? `${p[2]}.${p[1]}` : iso;
}

$$("#usage-range-seg button").forEach((b) => b.addEventListener("click", () => {
  $$("#usage-range-seg button").forEach((x) => x.classList.toggle("active", x === b));
  usageState.days = parseInt(b.dataset.days, 10);
  loadUsage();
}));
$$("#usage-metric-seg button").forEach((b) => b.addEventListener("click", () => {
  $$("#usage-metric-seg button").forEach((x) => x.classList.toggle("active", x === b));
  usageState.metric = b.dataset.umetric;
  renderUsage();
}));

/* ---------------------------------------------------------- security */

function renderSecurity() {
  const on = state.me?.totp_enabled;
  $("#twofa-state").textContent = on
    ? "✓ Enabled — a code from your authenticator is required to sign in."
    : "Off — add a second factor (Google Authenticator, Aegis, 1Password …).";
  const btn = $("#twofa-btn");
  btn.textContent = on ? "Disable" : "Enable 2FA";
  btn.classList.toggle("primary", !on);
  btn.classList.toggle("danger", on);
}

$("#twofa-btn").addEventListener("click", () => {
  if (state.me?.totp_enabled) disable2FA(); else enable2FA();
});

async function enable2FA() {
  const body = el("div", { class: "thinking" }, "preparing");
  openModal("🛡 Enable two-factor", body);
  let setup;
  try { setup = await api("/settings/2fa/setup"); }
  catch (e) { setModalBody(el("div", { class: "error" }, e.message)); return; }

  const codeInput = el("input", { type: "text", inputmode: "numeric",
    maxlength: "6", placeholder: "6-digit code", autocomplete: "one-time-code" });
  const status = el("div", { class: "muted", style: "margin-top:6px" });
  const wrap = el("div", {});
  wrap.append(el("p", { class: "muted", style: "margin-bottom:10px" },
    "Scan this with your authenticator app, then enter the 6-digit code to confirm."));
  if (setup.svg) {
    const qr = el("div", { class: "qr-box" });
    qr.innerHTML = setup.svg;
    wrap.append(qr);
  }
  wrap.append(el("p", { class: "muted", style: "margin:8px 0 4px" },
    "Can't scan? Enter this key manually:"),
    el("pre", { class: "totp-secret" }, setup.secret.replace(/(.{4})/g, "$1 ").trim()),
    el("label", {}, "Confirmation code", codeInput),
    el("button", { class: "btn primary wide", onclick: async function () {
      this.disabled = true; status.textContent = "checking…";
      try {
        await api("/settings/2fa/enable", { method: "POST",
          body: JSON.stringify({ secret: setup.secret, code: codeInput.value.trim() }) });
        state.me.totp_enabled = true;
        renderSecurity();
        closeModal();
        $("#sec-status").textContent = "✓ Two-factor is on. You'll need a code next sign-in.";
      } catch (e) { status.textContent = "⚠ " + e.message; this.disabled = false; }
    } }, "Turn on 2FA"),
    status);
  setModalBody(wrap);
  setTimeout(() => codeInput.focus(), 60);
}

function disable2FA() {
  const pw = el("input", { type: "password", placeholder: "Current password",
    autocomplete: "current-password" });
  const code = el("input", { type: "text", inputmode: "numeric", maxlength: "6",
    placeholder: "Current 2FA code", autocomplete: "one-time-code" });
  const status = el("div", { class: "muted", style: "margin-top:6px" });
  openModal("Disable two-factor", el("div", {},
    el("p", { class: "muted", style: "margin-bottom:10px" },
      "Confirm with your password and a current code to switch 2FA off."),
    el("label", {}, "Password", pw),
    el("label", {}, "2FA code", code),
    el("button", { class: "btn danger wide", onclick: async function () {
      this.disabled = true; status.textContent = "…";
      try {
        await api("/settings/2fa/disable", { method: "POST",
          body: JSON.stringify({ password: pw.value, code: code.value.trim() }) });
        state.me.totp_enabled = false;
        renderSecurity();
        closeModal();
        $("#sec-status").textContent = "Two-factor disabled.";
      } catch (e) { status.textContent = "⚠ " + e.message; this.disabled = false; }
    } }, "Disable 2FA"),
    status));
}

$("#revoke-sessions").addEventListener("click", async function () {
  if (!confirm("Sign out every other device? You'll stay signed in here.")) return;
  this.disabled = true;
  try {
    const r = await api("/settings/sessions/revoke", { method: "POST" });
    if (r.token) setActiveToken(r.token);
    $("#sec-status").textContent = "✓ All other sessions signed out.";
  } catch (e) { $("#sec-status").textContent = "⚠ " + e.message; }
  this.disabled = false;
});

$("#audit-open").addEventListener("click", openAuditLog);

async function openAuditLog() {
  const body = el("div", {});
  openModal("Activity log", body);
  const list = el("div", {});
  let meta = {};
  const filterSel = el("select", { class: "select", style: "margin-bottom:10px",
    onchange: () => load(true) },
    el("option", { value: "" }, "All activity"),
    el("option", { value: "src:agent" }, "🤖 AI agent actions"),
    el("option", { value: "src:sentinel" }, "🛰 Sentinel loops"),
    el("option", { value: "src:auto" }, "🤖 AI (auto-mode)"),
    el("option", { value: "container_action" }, "🐳 Container control"),
    el("option", { value: "login" }, "🔓 Sign-ins"),
    el("option", { value: "login_failed" }, "⛔ Failed sign-ins"));
  body.append(filterSel, list);
  const moreBtn = el("button", { class: "btn wide", style: "margin-top:8px" }, "Load more");
  let cursor = null;

  async function load(reset) {
    if (reset) { list.innerHTML = ""; cursor = null; }
    const f = filterSel.value;
    const p = new URLSearchParams({ limit: "60" });
    if (f.startsWith("src:")) p.set("source", f.slice(4));
    else if (f) p.set("action", f);
    if (cursor) p.set("before", cursor);
    let r;
    try { r = await api("/audit?" + p); }
    catch (e) { list.append(el("div", { class: "error" }, e.message)); return; }
    meta = r.meta?.actions || meta;
    if (reset && !r.events.length) {
      list.append(el("p", { class: "muted" }, "No activity recorded yet."));
    }
    for (const e of r.events) list.append(auditRow(e, meta));
    cursor = r.cursor;
    moreBtn.classList.toggle("hidden", !cursor);
  }
  body.append(moreBtn);
  moreBtn.addEventListener("click", () => load(false));
  load(true);
}

function auditRow(e, meta) {
  const m = meta[e.action] || { icon: "•", label: e.action };
  const srcTag = { agent: "AI", auto: "AI·auto", sentinel: "Sentinel" }[e.source];
  return el("div", { class: "mini-row audit-row" + (e.status === "warn" || e.status === "error" ? " bad" : "") },
    el("span", { title: e.source }, m.icon),
    el("div", { style: "min-width:0;flex:1" },
      el("div", { class: "name" }, m.label,
        e.target ? el("span", { class: "muted", style: "font-weight:400" }, "  " + e.target) : null,
        srcTag ? el("span", { class: "pill dim", style: "margin-left:6px" }, srcTag) : null),
      e.detail ? el("div", { class: "sub" }, e.detail) : null),
    el("span", { class: "muted", style: "font-size:11px;flex:none" }, timeAgo(e.t)));
}

/* ------------------------------------------------------ notifications */

async function refreshNotifs() {
  try {
    const r = await api("/notifications");
    const badge = $("#notif-badge");
    badge.textContent = r.unseen > 9 ? "9+" : r.unseen;
    badge.classList.toggle("hidden", !r.unseen);
    return r;
  } catch { return { items: [], unseen: 0 }; }
}

$("#notif-btn").addEventListener("click", openNotifications);

async function openNotifications() {
  const body = el("div", {});
  openModal("🔔 Notifications", body);
  const r = await refreshNotifs();
  api("/notifications/seen", { method: "POST" })
    .then(() => $("#notif-badge").classList.add("hidden")).catch(() => {});
  body.append(el("div", { class: "card-actions", style: "margin-bottom:10px" },
    el("button", { class: "btn small", onclick: () => {
      closeModal(); showView("settings");
      setTimeout(() => $("#loops-list")?.scrollIntoView({ behavior: "smooth" }), 150);
    } }, "⚙ Configure background agents")));
  if (!r.items.length) {
    body.append(el("p", { class: "muted" },
      "Nothing yet. Enable a Sentinel loop under More → Background agents — " +
      "it will watch your server and report here (and via ntfy push, if configured)."));
    return;
  }
  const icons = { ok: "✅", info: "ℹ️", warn: "⚠️", crit: "🚨" };
  for (const n of r.items) {
    const repeated = (n.count || 1) > 1;
    const sub = repeated
      ? `${n.source} · seen ${n.count}× · last ${timeAgo(n.last_seen || n.time)}`
      : `${n.source} · ${timeAgo(n.time)}`;
    const card = el("div", { class: "card check-card " + (n.status === "ok" ? "ok" : n.status),
      style: "margin-bottom:8px" },
      el("div", { class: "card-row" },
        el("span", { style: "font-size:18px" }, icons[n.status] || "🔔"),
        el("div", { style: "min-width:0;flex:1" },
          el("div", { class: "card-title" }, n.title,
            repeated ? el("span", { class: "chip", style: "margin-left:6px" }, `×${n.count}`) : null),
          el("div", { class: "card-sub" }, sub))));
    if (n.body) {
      const md = mdDiv(n.body, "notif-body");
      card.append(n.body.length > 500 ? fold("details", md) : md);
    }
    card.append(el("div", { class: "card-actions" },
      askAiButton(`My background agent "${n.source}" reported (${n.status}): ${n.title}\n\n` +
        `${n.body.slice(0, 1500)}\n\nLet's look into this together — investigate and help me fix it.`,
        "💬 Discuss & fix")));
    body.append(card);
  }
}

/* --------------------------------------------------- background agents */

let loopsCache = [];

async function loadLoops() {
  const list = $("#loops-list");
  const addRow = $("#loops-add");
  let presets;
  try {
    const r = await api("/agents/loops");
    loopsCache = r.loops;
    presets = r.presets;
  } catch (e) { list.textContent = "⚠ " + e.message; return; }

  const save = async () => {
    $("#loops-status").textContent = "saving…";
    try {
      const r = await api("/agents/loops", { method: "POST",
        body: JSON.stringify({ loops: loopsCache }) });
      loopsCache = r.loops;
      $("#loops-status").textContent = "✓ saved";
      setTimeout(() => ($("#loops-status").textContent = ""), 1500);
      render();
    } catch (e) { $("#loops-status").textContent = "⚠ " + e.message; }
  };

  const INTERVALS = [[30, "every 30 min"], [60, "hourly"], [180, "every 3 hours"],
                     [360, "every 6 hours"], [720, "every 12 hours"], [1440, "once a day"]];
  const STATUS_ICON = { ok: "✅", info: "ℹ️", warn: "⚠️", crit: "🚨" };

  function nextRunText(lp) {
    if (!lp.enabled) return "paused";
    if (!lp.last_run) return "runs shortly";
    const due = lp.last_run + lp.interval_min * 60 - Date.now() / 1000;
    return due <= 0 ? "due now" : "next in ~" + fmtUptime(due);
  }

  function render() {
    list.innerHTML = "";
    if (!loopsCache.length) {
      list.append(el("p", { class: "muted", style: "margin-bottom:6px" },
        "No watchmen yet. Pick one below to get started 👇"));
    }
    for (const lp of loopsCache) {
      const preset = presets[lp.preset] || presets.custom;
      const card = el("div", { class: "loop-card" + (lp.enabled ? " on" : "") });

      const toggle = el("input", { type: "checkbox" });
      toggle.checked = lp.enabled;
      toggle.addEventListener("change", () => { lp.enabled = toggle.checked; save(); });

      card.append(el("div", { class: "loop-head" },
        el("span", { class: "loop-ico" }, preset.icon),
        el("div", { class: "loop-headmain", onclick: () => card.classList.toggle("open") },
          el("div", { class: "loop-name" }, lp.name),
          el("div", { class: "loop-when muted" }, nextRunText(lp))),
        el("label", { class: "switch" }, toggle, el("span", { class: "slider" }))));

      card.append(el("div", { class: "loop-desc muted" }, preset.desc || "Custom watch."));
      if (lp.last_run) {
        card.append(el("div", { class: "loop-last " + (lp.last_status || "info") },
          el("span", {}, STATUS_ICON[lp.last_status] || "•"),
          el("span", { class: "ll-title" }, lp.last_title || "reported"),
          el("span", { class: "muted ll-time" }, timeAgo(lp.last_run))));
      }

      const settings = el("div", { class: "loop-settings" });
      const nameIn = el("input", { type: "text", value: lp.name });
      nameIn.addEventListener("change", () => { lp.name = nameIn.value.trim() || preset.name; save(); });
      settings.append(el("label", { class: "loop-field" }, "Name", nameIn));
      if (lp.preset === "custom") {
        const ta = el("textarea", { rows: 2, placeholder:
          "What should it watch? e.g. 'Check my Minecraft server logs for errors and griefing'" });
        ta.value = lp.prompt || "";
        ta.addEventListener("change", () => { lp.prompt = ta.value; save(); });
        settings.append(el("label", { class: "loop-field" }, "What to check", ta));
      }
      const intervalSel = el("select", { class: "select" });
      for (const [m, label] of INTERVALS) intervalSel.append(el("option", { value: m }, label));
      intervalSel.value = String(lp.interval_min);
      if (![...intervalSel.options].some((o) => o.value === intervalSel.value)) {
        intervalSel.append(el("option", { value: lp.interval_min }, lp.interval_min + " min"));
        intervalSel.value = String(lp.interval_min);
      }
      intervalSel.addEventListener("change", () => { lp.interval_min = parseInt(intervalSel.value, 10); save(); });
      settings.append(el("label", { class: "loop-field" }, "How often", intervalSel));
      const ntfy = el("input", { type: "text",
        placeholder: "https://ntfy.sh/my-secret-topic (optional)" });
      ntfy.value = lp.ntfy_url || "";
      ntfy.addEventListener("change", () => { lp.ntfy_url = ntfy.value; save(); });
      settings.append(el("label", { class: "loop-field" },
        el("span", {}, "Phone push via ntfy ",
          el("span", { class: "muted", style: "font-weight:400" },
            "— install the free ntfy app, pick a secret topic")), ntfy));
      const notifySel = el("select", { class: "select" },
        el("option", { value: "all" }, "push every result"),
        el("option", { value: "info" }, "push info and up"),
        el("option", { value: "warn" }, "push warnings & critical"),
        el("option", { value: "crit" }, "push only critical"));
      notifySel.value = lp.notify_min || "warn";
      notifySel.addEventListener("change", () => { lp.notify_min = notifySel.value; save(); });
      settings.append(el("label", { class: "loop-field" }, "When to push", notifySel));
      settings.append(el("div", { class: "card-actions", style: "margin-top:6px" },
        el("button", { class: "btn small primary", onclick: async function () {
          this.disabled = true; this.textContent = "⏳ running…";
          try { await api(`/agents/loops/${lp.id}/run`, { method: "POST" }); } catch {}
          $("#loops-status").textContent = "Running now — the result lands under 🔔 in a minute.";
          setTimeout(() => { this.disabled = false; this.textContent = "▶ Run now"; }, 2500);
        } }, "▶ Run now"),
        el("button", { class: "btn small danger", onclick: () => {
          if (!confirm(`Remove the "${lp.name}" watchman?`)) return;
          loopsCache = loopsCache.filter((x) => x.id !== lp.id); save();
        } }, "Remove")));
      card.append(settings);
      list.append(card);
    }

    addRow.innerHTML = "";
    addRow.append(el("div", { class: "muted", style: "margin:2px 2px 6px;width:100%" }, "Add a watchman:"));
    for (const [key, p] of Object.entries(presets)) {
      addRow.append(el("button", { class: "loop-add-btn", title: p.desc, onclick: () => {
        loopsCache.push({ preset: key, name: p.name, interval_min: p.interval_min,
                          enabled: true, ntfy_url: "", notify_min: "warn" });
        save();
      } },
        el("span", { class: "lab-ico" }, p.icon),
        el("span", { class: "lab-txt" },
          el("b", {}, p.name),
          el("span", { class: "muted" }, p.desc || ""))));
    }
  }
  render();
}

/* ------------------------------------------------------- integrations */

async function loadIntegrations() {
  const list = $("#integrations-list");
  const formBox = $("#integration-form");
  let r;
  try { r = await api("/integrations"); }
  catch (e) { list.textContent = "⚠ " + e.message; return; }

  list.innerHTML = "";
  if (!r.integrations.length) {
    list.append(el("p", { class: "muted", style: "margin:4px 0 8px" },
      "Nothing connected yet."));
  }
  for (const it of r.integrations) {
    const status = el("span", { class: "muted integ-status" });
    const card = el("div", { class: "integ-card" + (it.enabled ? "" : " off") });
    // enable/disable switch — instantly cuts the agent's access
    const sw = el("input", { type: "checkbox" });
    sw.checked = it.enabled;
    sw.addEventListener("change", async () => {
      sw.disabled = true;
      try {
        await api(`/integrations/${encodeURIComponent(it.name)}/enabled`, { method: "POST",
          body: JSON.stringify({ enabled: sw.checked }) });
        card.classList.toggle("off", !sw.checked);
      } catch (e) { sw.checked = !sw.checked; alert(e.message); }
      sw.disabled = false;
    });
    const secretHint = it.has_secret
      ? `🔒 token stored (${"•".repeat(Math.min(8, it.secret_len))}, ${it.secret_len} chars)`
      : "⚠ no token";
    card.append(el("div", { class: "integ-top" },
      el("span", { class: "integ-ico" }, "🔌"),
      el("div", { style: "min-width:0;flex:1" },
        el("div", { class: "name" }, `${it.name} `, el("span", { class: "pill dim" }, it.type_label)),
        el("div", { class: "sub" }, it.note || it.base_url)),
      el("label", { class: "switch", title: it.enabled ? "AI access on" : "AI access off" },
        sw, el("span", { class: "slider" }))));
    card.append(el("div", { class: "integ-meta" },
      el("span", { class: "muted" }, secretHint),
      it.last_used ? el("span", { class: "muted" }, "· used " + timeAgo(it.last_used)) : null,
      status));
    card.append(el("div", { class: "card-actions", style: "margin-top:8px" },
      el("button", { class: "btn small", onclick: async function () {
        status.textContent = "testing…"; status.style.color = "";
        try {
          const t = await api(`/integrations/${encodeURIComponent(it.name)}/test`, { method: "POST" });
          status.textContent = t.ok ? "✓ reachable" : `✕ ${t.status || "failed"}`;
          status.style.color = t.ok ? "var(--accent2)" : "var(--danger)";
        } catch (e) { status.textContent = "✕ " + e.message; status.style.color = "var(--danger)"; }
      } }, "Test connection"),
      el("button", { class: "btn small danger", onclick: async () => {
        if (!confirm(`Remove integration "${it.name}"? The stored token is deleted.`)) return;
        await api(`/integrations/${encodeURIComponent(it.name)}`, { method: "DELETE" });
        loadIntegrations();
      } }, "Remove")));
    list.append(card);
  }

  // add form
  formBox.innerHTML = "";
  const typeSel = el("select", { class: "select wide" });
  for (const [k, t] of Object.entries(r.types)) {
    typeSel.append(el("option", { value: k }, t.label));
  }
  const hint = el("p", { class: "muted", style: "margin:4px 0 8px" });
  const nameIn = el("input", { type: "text", placeholder: "name, e.g. desec or dns" });
  const secretIn = el("input", { type: "password", placeholder: "API token / secret",
    autocomplete: "off" });
  const baseIn = el("input", { type: "text", placeholder: "base URL, e.g. http://grafana:3000/api" });
  const headerIn = el("input", { type: "text", placeholder: "auth header name (default: Authorization)" });
  const noteIn = el("input", { type: "text", placeholder: "note for the AI, e.g. 'manages maxaufknax.de'" });
  const genericOnly = el("div", {}, el("label", {}, "Base URL", baseIn),
    el("label", {}, "Auth header", headerIn));
  const syncHint = () => {
    const t = r.types[typeSel.value];
    hint.textContent = t.hint || "";
    genericOnly.classList.toggle("hidden", typeSel.value !== "generic");
  };
  typeSel.addEventListener("change", syncHint);
  const status = el("p", { class: "muted" });
  formBox.append(fold("＋ Connect a service", el("div", {},
    el("label", {}, "Type", typeSel), hint,
    el("label", {}, "Name", nameIn),
    el("label", {}, "Token", secretIn),
    genericOnly,
    el("label", {}, "Note (optional)", noteIn),
    el("button", { class: "btn primary", onclick: async () => {
      status.textContent = "saving…";
      try {
        await api("/integrations", { method: "POST", body: JSON.stringify({
          name: nameIn.value, type: typeSel.value, secret: secretIn.value,
          base_url: baseIn.value, auth_header_name: headerIn.value, note: noteIn.value,
        }) });
        status.textContent = "";
        loadIntegrations();
      } catch (e) { status.textContent = "⚠ " + e.message; }
    } }, "Connect"), status)));
  syncHint();
}

/* ---------------------------------------------------------- onboarding */

function openOnboarding() {
  let step = 0;
  const nameInput = el("input", { type: "text", value: state.me?.server_name || "",
    placeholder: "e.g. Homebase" });
  const provSel = el("select", { class: "select wide" },
    el("option", { value: "openrouter" }, "OpenRouter (one key, many models)"),
    el("option", { value: "anthropic" }, "Anthropic (Claude)"),
    el("option", { value: "openai" }, "OpenAI"));
  const keyInput = el("input", { type: "password", placeholder: "API key (optional — skip if unsure)" });

  const steps = [
    () => el("div", {},
      el("div", { class: "center", style: "font-size:40px;margin:6px 0" }, "⎈"),
      el("h4", { style: "text-align:center" }, "Welcome to Helmsman"),
      el("p", { class: "muted center", style: "margin-bottom:14px" },
        "Your server, as easy as a second phone. Let's set up two things — takes 30 seconds."),
      el("label", {}, "What should your server be called?", nameInput)),
    () => el("div", {},
      el("h4", {}, "✦ Enable the AI copilot"),
      el("p", { class: "muted", style: "margin-bottom:12px" },
        "With an AI key, Helmsman can explain updates, analyze problems and fix things " +
        "for you in chat. The key is stored only on your server. You can add it later under More."),
      el("label", {}, "Provider", provSel),
      el("label", {}, "API key", keyInput)),
    () => el("div", {},
      el("h4", {}, "You're set 🎉"),
      el("div", { class: "app-why", style: "margin-top:8px" },
        "▦ Home — live health of your server & services"),
      el("div", { class: "app-why" },
        "✦ Vibe — chat with the AI engineer (it can act, with your approval)"),
      el("div", { class: "app-why" },
        "♥ Health — updates & security checks, with one-tap fixes"),
      el("div", { class: "app-why" },
        "◲ Apps — install Nextcloud, Jellyfin & more with one tap")),
  ];

  const body = el("div", {});
  const dots = el("div", { class: "onb-dots" }, ...steps.map((_, i) => el("span", {})));
  const content = el("div", {});
  const nav = el("div", { class: "card-actions", style: "margin-top:12px" });
  body.append(content, dots, nav);

  async function finish() {
    try {
      if (nameInput.value.trim()) {
        const r = await api("/settings/server", {
          method: "POST", body: JSON.stringify({ name: nameInput.value }) });
        $("#host-name-text").textContent = r.server_name;
      }
      if (keyInput.value.trim()) {
        await api("/settings/ai", { method: "POST", body: JSON.stringify({
          keys: { [provSel.value]: keyInput.value.trim() },
          default_provider: provSel.value, default_model: "",
        }) });
        state.aiConfigured = true;
        $("#vibe-no-ai")?.classList.add("hidden");
      }
      await api("/settings/onboarded", { method: "POST" });
    } catch {}
    closeModal();
  }

  function render() {
    content.innerHTML = "";
    content.append(steps[step]());
    [...dots.children].forEach((d, i) => d.classList.toggle("on", i === step));
    nav.innerHTML = "";
    if (step > 0) nav.append(el("button", { class: "btn", onclick: () => { step--; render(); } }, "Back"));
    nav.append(el("span", { class: "spacer" }));
    if (step < steps.length - 1) {
      nav.append(el("button", { class: "btn primary", onclick: () => { step++; render(); } }, "Next"));
    } else {
      nav.append(el("button", { class: "btn primary", onclick: finish }, "Start using Helmsman"));
    }
  }
  render();
  openModal("Setup", body);
}

/* ------------------------------------------------- server switcher UI */

$("#host-name").addEventListener("click", openServerSwitcher);

async function openServerSwitcher() {
  const body = el("div", {});
  openModal("Servers", body);
  const render = () => {
    body.innerHTML = "";
    for (const s of servers.all) {
      const active = s.id === servers.activeId;
      const row = el("div", { class: "srv-row" + (active ? " active" : "") },
        el("span", { class: "srv-dot" + (s.token ? " on" : "") }),
        el("div", { class: "srv-main", onclick: () => switchServer(s.id) },
          el("div", { class: "srv-name" }, s.name || s.base || "This server"),
          el("div", { class: "srv-sub muted" },
            (s.base || location.host) + (active ? " · active" : (s.token ? "" : " · signed out")))),
        active ? el("span", { class: "chip ok" }, "current") : null,
        s.id !== "local" ? el("button", { class: "btn small", title: "remove",
          onclick: (e) => { e.stopPropagation(); removeServer(s.id, render); } }, "✕") : null);
      body.append(row);
    }
    body.append(el("div", { class: "card-actions", style: "margin-top:12px" },
      el("button", { class: "btn primary", onclick: openConnectModal }, "＋ Add a server"),
      el("button", { class: "btn", onclick: openPairModal }, "📷 Scan pairing QR")));
    if (state.me && state.me.can_pair) {
      body.append(el("div", { class: "card-actions", style: "margin-top:6px" },
        el("button", { class: "btn small", onclick: showPairingQR },
          "🔗 Pair another device with this server")));
    }
  };
  render();
}

function switchServer(id) {
  if (id === servers.activeId) { closeModal(); return; }
  const s = servers.all.find((x) => x.id === id);
  if (!s || !s.token) { openConnectModal(s); return; }
  servers.activeId = id;
  persistServers();
  location.reload();
}

function removeServer(id, after) {
  if (!confirm("Remove this server from the list on this device?")) return;
  servers.all = servers.all.filter((x) => x.id !== id);
  if (servers.activeId === id) {
    servers.activeId = (servers.all[0] || {}).id || "";
    persistServers();
    location.reload();
    return;
  }
  persistServers();
  after && after();
}

// add a remote server by URL + password (or open a signed-out one)
function openConnectModal(existing) {
  const urlIn = el("input", { type: "url", placeholder: "https://server.example.com:8090",
    value: existing?.base || "", inputmode: "url", autocapitalize: "off" });
  const pwIn = el("input", { type: "password", placeholder: "Admin password", autocomplete: "current-password" });
  const totpIn = el("input", { type: "text", class: "hidden", placeholder: "6-digit 2FA code",
    inputmode: "numeric", maxlength: "6" });
  const status = el("p", { class: "muted" });
  const nameHint = el("p", { class: "muted", style: "margin-top:-4px" });

  urlIn.addEventListener("change", async () => {
    const base = normalizeBase(urlIn.value);
    if (!base) return;
    try {
      const info = await (await fetch(base + "/api/info")).json();
      if (info.helmsman) {
        nameHint.textContent = `✓ ${info.server_name} · Helmsman ${info.version}` +
          (info.demo ? " (demo)" : "");
        totpIn.classList.toggle("hidden", !info.totp_required);
      }
    } catch { nameHint.textContent = "⚠ couldn't reach a Helmsman server at that address"; }
  });

  const connect = async () => {
    const base = normalizeBase(urlIn.value);
    if (!base) { status.textContent = "Enter the server’s full address."; return; }
    status.textContent = "connecting…";
    try {
      const res = await fetch(base + "/api/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: pwIn.value, totp: totpIn.value.trim() }),
      });
      const data = await res.json();
      if (!res.ok) {
        if (data.totp) { totpIn.classList.remove("hidden"); totpIn.focus(); }
        throw new Error(data.detail || "Login failed");
      }
      let name = base;
      try { name = (await (await fetch(base + "/api/info")).json()).server_name || base; } catch {}
      addRemoteServer(base, data.token, name, data.demo);
      closeModal();
      location.reload();
    } catch (e) { status.textContent = "⚠ " + e.message; }
  };

  openModal("Add a server", el("div", {},
    el("p", { class: "muted", style: "margin-bottom:10px" },
      "Connect to another Helmsman server — your phone can manage all of them from one app."),
    el("label", {}, "Server address", urlIn), nameHint,
    el("label", {}, "Password", pwIn),
    totpIn,
    el("button", { class: "btn primary wide", style: "margin-top:10px", onclick: connect }, "Connect"),
    status,
    el("hr", { class: "sep" }),
    el("button", { class: "btn wide", onclick: openPairModal }, "📷 Or scan a pairing QR code")));
  setTimeout(() => urlIn.focus(), 60);
}

function normalizeBase(v) {
  v = (v || "").trim();
  if (!v) return "";
  if (!/^https?:\/\//i.test(v)) v = "http://" + v;
  try { return new URL(v).origin; } catch { return ""; }
}

/* ---- pairing: show a QR (this server) & scan one (add a server) ---- */

async function showPairingQR() {
  const body = el("div", { class: "center" }, el("p", { class: "muted" }, "generating…"));
  openModal("Pair a device", body);
  try {
    const { code } = await api("/pair/new", { method: "POST" });
    const payload = JSON.stringify({ h: "pair", u: location.origin, c: code });
    const { svg } = await api("/qr", { method: "POST", body: JSON.stringify({ text: payload }) });
    body.innerHTML = "";
    const holder = el("div", { class: "qr-holder" });
    holder.innerHTML = svg;
    body.append(
      el("p", { class: "muted", style: "margin-bottom:10px" },
        "On your other device, open Helmsman → Servers → “Scan pairing QR” and point it here. " +
        "The code works once and expires in 10 minutes."),
      holder,
      el("p", { class: "muted center", style: "margin-top:10px;word-break:break-all" },
        "Manual code: ", el("code", {}, code)));
  } catch (e) {
    body.innerHTML = "";
    body.append(el("div", { class: "error" }, e.message));
  }
}

// hand this exact chat off to another device: it opens the same live session
// and keeps streaming the agent. Uses a one-time pairing code + a deep link.
async function openHandoff() {
  if (!state.chatId) { toast("Start a chat first"); return; }
  const body = el("div", { class: "center" }, el("p", { class: "muted" }, "generating…"));
  openModal("Continue on another device", body);
  try {
    let code = "";
    if (state.me && state.me.can_pair) {
      try { code = (await api("/pair/new", { method: "POST" })).code; } catch {}
    }
    const origin = apiBase() || location.origin;
    const link = origin + "/?" + (code ? "pair=" + encodeURIComponent(code) + "&" : "") +
      "c=" + encodeURIComponent(state.chatId);
    const { svg } = await api("/qr", { method: "POST", body: JSON.stringify({ text: link }) });
    body.innerHTML = "";
    const holder = el("div", { class: "qr-holder" });
    holder.innerHTML = svg;
    body.append(
      el("p", { class: "muted", style: "margin-bottom:10px" },
        "Scan with another device to open this exact chat there and keep watching the agent " +
        "work — live. " + (code ? "It signs the device in automatically (code valid 10 min)."
          : "You'll sign in on that device.")),
      holder,
      el("p", { class: "center", style: "margin-top:10px;word-break:break-all" },
        el("a", { href: link, target: "_blank", rel: "noopener" }, link)),
      el("button", { class: "btn small wide", style: "margin-top:8px", onclick: () => {
        navigator.clipboard?.writeText(link); toast("Link copied");
      } }, "Copy link"));
  } catch (e) {
    body.innerHTML = "";
    body.append(el("div", { class: "error" }, e.message));
  }
}

async function openPairModal() {
  const status = el("p", { class: "muted" });
  const video = el("video", { class: "qr-video", playsinline: "", muted: "" });
  const manual = el("input", { type: "text", placeholder: "…or paste the manual code" });
  const manualUrl = el("input", { type: "url", placeholder: "server address (for manual code)",
    inputmode: "url", autocapitalize: "off" });

  const claim = async (base, code, chat) => {
    status.textContent = "pairing…";
    try {
      const res = await fetch(base + "/api/pair/claim", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Pairing failed");
      addRemoteServer(base, data.token, data.server_name, false);
      if (chat) sessionStorage.setItem("helmsman_open_chat", chat);  // handoff → open it
      stopScan();
      closeModal();
      location.reload();
    } catch (e) { status.textContent = "⚠ " + e.message; }
  };

  let stream = null, scanning = false;
  const stopScan = () => { scanning = false; if (stream) stream.getTracks().forEach((t) => t.stop()); };

  const startScan = async () => {
    if (!("BarcodeDetector" in window)) {
      status.textContent = "This device can’t scan QR codes in-browser — use the manual code below.";
      video.classList.add("hidden");
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
      video.srcObject = stream;
      await video.play();
      scanning = true;
      const detector = new window.BarcodeDetector({ formats: ["qr_code"] });
      const tick = async () => {
        if (!scanning) return;
        try {
          const codes = await detector.detect(video);
          if (codes.length) {
            const parsed = parsePairPayload(codes[0].rawValue);
            if (parsed) { stopScan(); return claim(parsed.u, parsed.c, parsed.chat); }
          }
        } catch {}
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    } catch (e) {
      status.textContent = "⚠ camera unavailable — use the manual code below";
      video.classList.add("hidden");
    }
  };

  const body = el("div", {},
    el("p", { class: "muted", style: "margin-bottom:10px" },
      "Point your camera at the pairing QR shown on the other device (Servers → Pair a device)."),
    video, status,
    el("hr", { class: "sep" }),
    el("label", {}, "Manual pairing", manualUrl), manual,
    el("button", { class: "btn wide", style: "margin-top:8px", onclick: () => {
      const base = normalizeBase(manualUrl.value);
      if (base && manual.value.trim()) claim(base, manual.value.trim());
      else status.textContent = "Enter both the server address and the code.";
    } }, "Pair with code"));

  openModal("Scan pairing QR", body);
  $("#modal-close").addEventListener("click", stopScan, { once: true });
  startScan();
}

function parsePairPayload(raw) {
  try {
    const o = JSON.parse(raw);
    if (o && o.h === "pair" && o.u && o.c) return o;
  } catch {}
  try {   // handoff QR is a plain deep link: https://host/?pair=CODE&c=CHAT
    const u = new URL(raw);
    const code = u.searchParams.get("pair");
    if (code) return { u: u.origin, c: code, chat: u.searchParams.get("c") || "" };
  } catch {}
  return null;
}

/* ---------------------------------------------------------- boot */

// deep link from a handoff QR/link: /?pair=CODE&c=CHATID — claim a token for
// the server that served this app, and remember which chat to open.
async function handleDeepLink() {
  const p = new URLSearchParams(location.search);
  const code = p.get("pair");
  const chat = p.get("c") || p.get("chat");
  if (!code && !chat) return;
  history.replaceState(null, "", location.pathname);   // don't re-trigger on refresh
  if (chat) sessionStorage.setItem("helmsman_open_chat", chat);
  if (code && !state.token) {
    try {
      const res = await fetch("/api/pair/claim", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const data = await res.json();
      if (res.ok) upsertLocalServer(data.token);
    } catch {}
  }
}

async function boot() {
  await handleDeepLink();
  const ok = await tryAuth();
  $("#login-screen").classList.toggle("hidden", ok);
  $("#app").classList.toggle("hidden", !ok);
  if (ok) {
    const openChat = sessionStorage.getItem("helmsman_open_chat");
    if (openChat) {
      sessionStorage.removeItem("helmsman_open_chat");
      state.chatId = openChat;
      localStorage.setItem("helmsman_chat", openChat);
    }
    await populateModels();   // picker ready before the first session snapshot
    showView(openChat ? "vibe" : "dashboard");
    chatConnect();
    // preload updates so the health badge is meaningful
    api("/updates").then((r) => { state.updates = r; updateHealthBadge(); }).catch(() => {});
    refreshNotifs();
    setInterval(refreshNotifs, 120000);
    if (!state.me.onboarded && !state.me.demo) setTimeout(openOnboarding, 400);
  } else {
    setTimeout(() => $("#login-password").focus(), 100);
  }
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
boot();
