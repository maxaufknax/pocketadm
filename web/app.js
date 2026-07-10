/* Helmsman SPA */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const state = {
  token: localStorage.getItem("helmsman_token") || "",
  view: "dashboard",
  aiConfigured: false,
  chatWs: null,
  termWs: null,
  term: null,
  fitAddon: null,
  ctrlArmed: false,
  dashTimer: null,
  streamEl: null,   // current streaming assistant bubble
  streamRaw: "",
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

/* Minimal markdown: code fences, inline code, bold, links, lists */
function renderMarkdown(text) {
  const escape = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const parts = text.split(/```(\w*)\n?/);
  let html = "";
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 1) { // language marker — next part is code
      html += `<pre><code>${escape(parts[i + 1] || "")}</code></pre>`;
      i++;
    } else {
      let t = escape(parts[i]);
      t = t.replace(/`([^`\n]+)`/g, "<code>$1</code>")
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
  if (name === "updates") loadUpdates();
  if (name === "settings") loadSettings();
  if (name === "vibe") setTimeout(() => $("#chat-input").focus(), 50);
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
    renderContainers(await api("/containers"));
  } catch (e) {
    $("#conn-dot").className = "dot bad";
  }
  clearTimeout(state.dashTimer);
  if (state.view === "dashboard") state.dashTimer = setTimeout(refreshDashboard, 5000);
}

function renderContainers(list) {
  $("#container-count").textContent =
    `${list.filter((c) => c.state === "running").length}/${list.length} running`;
  const wrap = $("#container-list");
  wrap.innerHTML = "";
  for (const c of list) {
    const ports = c.ports.map((p) =>
      el("a", { class: "chip", href: `http://${location.hostname}:${p.public}`, target: "_blank" },
        `:${p.public}`));
    const actions = el("div", { class: "card-actions" },
      c.state === "running"
        ? el("button", { class: "btn small", onclick: () => containerAction(c, "restart") }, "restart")
        : el("button", { class: "btn small", onclick: () => containerAction(c, "start") }, "start"),
      c.state === "running"
        ? el("button", { class: "btn small", onclick: () => containerAction(c, "stop") }, "stop") : null,
      el("button", { class: "btn small", onclick: () => showLogs(c) }, "logs"),
      ...ports);
    wrap.append(el("div", { class: "card" },
      el("div", { class: "card-row" },
        el("span", { class: "state-dot " + c.state }),
        el("div", { style: "min-width:0" },
          el("div", { class: "card-title" }, c.name),
          el("div", { class: "card-sub" }, `${c.image} · ${c.status}`))),
      actions));
  }
}

async function containerAction(c, action) {
  try { await api(`/containers/${c.id}/${action}`, { method: "POST" }); }
  catch (e) { alert(`${action} failed: ${e.message}`); }
  refreshDashboard();
}

async function showLogs(c) {
  openModal(`Logs · ${c.name}`, el("pre", {}, "loading…"));
  try {
    const r = await api(`/containers/${c.id}/logs?tail=300`);
    const pre = el("pre", {}, r.logs || "[no output]");
    $("#modal-body").innerHTML = "";
    $("#modal-body").append(pre);
    pre.scrollTop = pre.scrollHeight;
  } catch (e) { $("#modal-body").textContent = e.message; }
}

/* ---------------------------------------------------------- vibe chat */

function chatConnect() {
  if (state.chatWs && state.chatWs.readyState <= 1) return;
  const ws = new WebSocket(wsUrl("/ws/chat"));
  state.chatWs = ws;
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
$("#chat-auto").addEventListener("change", function () {
  sendChat({ type: "set_auto", auto: this.checked });
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
    const containers = await api("/containers");
    for (const c of containers.filter((x) => x.state === "running")) {
      sel.append(el("option", { value: "container:" + c.id }, "🐳 " + c.name));
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

/* ---------------------------------------------------------- updates */

async function loadUpdates(force = false) {
  const list = $("#updates-list");
  const summary = $("#updates-summary");
  summary.textContent = "Checking registries…";
  list.innerHTML = "";
  try {
    const r = await api("/updates" + (force ? "?force=true" : ""));
    const avail = r.docker.filter((u) => u.update_available);
    summary.textContent = avail.length
      ? `${avail.length} image update${avail.length > 1 ? "s" : ""} available`
      : "Everything is up to date ✓";
    const badge = $("#upd-badge");
    badge.textContent = avail.length;
    badge.classList.toggle("hidden", avail.length === 0);

    for (const u of r.docker) {
      const actions = el("div", { class: "card-actions" });
      if (u.update_available) {
        actions.append(
          el("button", {
            class: "btn small primary",
            onclick: async function () {
              this.disabled = true; this.textContent = "pulling…";
              try { await api("/updates/pull", { method: "POST", body: JSON.stringify({ image: u.image }) }); loadUpdates(); }
              catch (e) { alert(e.message); this.disabled = false; this.textContent = "Pull update"; }
            },
          }, "Pull update"),
          el("button", { class: "btn small", onclick: () => explainUpdate(u.image, "docker") }, "✦ Explain"));
      }
      actions.append(el("span", { class: "chip" }, u.used_by.join(", ")));
      wrapCard(list, u.update_available ? "🔔" : u.error ? "◌" : "✓", u.image,
        u.update_available ? "Update available" : u.error || "Up to date", actions);
    }
    if (r.apt.available && r.apt.packages.length) {
      $("#apt-section").classList.remove("hidden");
      const aptList = $("#apt-list");
      aptList.innerHTML = "";
      for (const p of r.apt.packages.slice(0, 40)) {
        wrapCard(aptList, "📦", p.package, `${p.current} → ${p.new}`,
          el("div", { class: "card-actions" },
            el("button", { class: "btn small", onclick: () => explainUpdate(p.package, "apt") }, "✦ Explain")));
      }
    } else {
      $("#apt-section").classList.add("hidden");
    }
  } catch (e) {
    summary.textContent = "⚠ " + e.message;
  }
}

function wrapCard(parent, icon, title, sub, actions) {
  parent.append(el("div", { class: "card" },
    el("div", { class: "card-row" },
      el("span", { class: "app-icon", style: "font-size:18px" }, icon),
      el("div", { style: "min-width:0" },
        el("div", { class: "card-title" }, title),
        el("div", { class: "card-sub" }, sub))),
    actions));
}

async function explainUpdate(subject, kind) {
  openModal("✦ AI explanation", el("div", { class: "thinking" }, "Researching " + subject));
  try {
    const r = await api("/updates/explain", {
      method: "POST",
      body: JSON.stringify({ subject, kind, lang: navigator.language }),
    });
    const div = el("div", {});
    div.innerHTML = renderMarkdown(r.explanation);
    $("#modal-body").innerHTML = "";
    $("#modal-body").append(div);
  } catch (e) {
    $("#modal-body").textContent = "⚠ " + e.message +
      (state.aiConfigured ? "" : " — configure an AI key under Settings first.");
  }
}

$("#updates-refresh").addEventListener("click", () => loadUpdates(true));

/* ---------------------------------------------------------- settings */

async function loadSettings() {
  try {
    const me = await api("/me");
    $("#ai-provider").value = me.ai_provider || "";
    $("#ai-model").value = me.ai_model || "";
    $("#ai-status").textContent = me.ai_configured
      ? `✓ AI configured (${me.ai_provider})` : "AI not configured — features like Vibe Code and Explain need a key.";
  } catch {}
}

$("#ai-save").addEventListener("click", async () => {
  try {
    await api("/settings/ai", {
      method: "POST",
      body: JSON.stringify({
        provider: $("#ai-provider").value,
        api_key: $("#ai-key").value,
        model: $("#ai-model").value,
        base_url: $("#ai-baseurl").value,
      }),
    });
    $("#ai-key").value = "";
    state.aiConfigured = true;
    $("#vibe-no-ai").classList.add("hidden");
    loadSettings();
  } catch (e) { $("#ai-status").textContent = "⚠ " + e.message; }
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
  } else {
    setTimeout(() => $("#login-password").focus(), 100);
  }
}

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
boot();
