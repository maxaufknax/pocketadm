"""The watch: a background agent that writes to you when something on the
server is worth knowing — like a colleague who keeps an eye on it for you.

It replaces the fixed Sentinel loops (a security, update or health digest every
few hours, each one a report you stop reading) with one agent that decides for
itself whether a message is worth sending:

  * Rounds — every few hours it looks around with read-only tools and either
    writes a short message or stays silent. Most rounds end silent.
  * Incidents — warnings in the live activity feed (a container that died, a
    unit that failed, the internet that dropped, a burst of failed logins) and
    thresholds (a filling disk, memory pressure) start an investigation within
    minutes; related events are bundled into one look.
  * A short look back on Sunday evening, if there is something to say.

Guardrails live in code, not in the prompt: quiet hours (only critical messages
get through), a daily limit per importance, pauses, muted topics, a monthly
budget, and no second message on a topic within twelve hours unless it got
worse. The agent can only read — every command passes the same read-only gate
as the Sentinel loops (agents.sentinel_may_run).

Messages land in the app's Alerts screen and, if set up, in ntfy and a Matrix
room (Element). The agent runs on whatever AI the user chose for it (the
"watch" route): an API key, a local model, or a subscription through its CLI.
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from datetime import datetime, timedelta

import httpx

from . import accounts, activity, agents, ai, audit, config, engines

STATE_FILE = config.DATA_DIR / "watch_state.json"

DEFAULTS = {
    "enabled": False,
    "lang": "",                  # "" = English; "de", "fr", … — the app proposes the phone's
    "timezone": "",              # IANA name for quiet hours; "" = the server's own
    "interval_min": 180,         # rounds; 0 = only react to events
    "quiet_start": "23:00",
    "quiet_end": "07:30",
    "info_per_day": 3,
    "important_per_day": 8,
    "weekly": True,
    "knowledge": "",             # what the user wants it to know about this server
    "budget_usd": 5.0,           # per 30 days; 0 = no limit
    "push_min": "important",     # lowest importance pushed to ntfy/Matrix
    "ntfy_url": "",
    "matrix_homeserver": "",
    "matrix_room": "",
    "matrix_token": "",          # secret: never sent back to a client
    "paused_until": 0,
    "mutes": [],                 # [{"topic", "until", "note"}]
}
IMPORTANCE = ("critical", "important", "info")
STATUS_OF = {"critical": "crit", "important": "warn", "info": "info"}
RANK = {"info": 0, "important": 1, "critical": 2}
REPEAT_WINDOW = 12 * 3600
DEBOUNCE = 180                   # seconds of collecting related events before a look
MAX_STEPS = 10

LENSES = [
    "containers: crashes, restarts, unhealthy services and error bursts in their logs",
    "capacity: disk, memory and CPU — is something filling up or getting slower?",
    "security: logins, failed attempts, bans, exposed ports, unexpected new users",
    "updates: pending image and package updates, especially security-relevant ones",
    "backups: do they run, and when did the last one finish?",
    "network: internet stability, latency, DNS and the reverse proxy",
    "the services people use most — are they healthy and reachable?",
]

LANGUAGES = {"de": "German", "en": "English", "fr": "French", "es": "Spanish",
             "it": "Italian", "nl": "Dutch", "pt": "Portuguese", "pl": "Polish"}

_task: asyncio.Task | None = None
_running = False


# ------------------------------------------------------------------ settings & state

def settings() -> dict:
    return {**DEFAULTS, **(config.settings.get("watch") or {})}


def public_settings() -> dict:
    s = settings()
    s["matrix_token_set"] = bool(s.pop("matrix_token", ""))
    s["mutes"] = [m for m in s.get("mutes") or [] if m.get("until", 0) > time.time()]
    return s


def update_settings(changes: dict) -> dict:
    current = settings()
    for key, value in changes.items():
        if key not in DEFAULTS or key in ("mutes", "paused_until"):
            continue
        if key == "matrix_token" and value == "":
            continue                     # "" keeps the stored token, "-" clears it
        if key == "matrix_token" and value == "-":
            value = ""
        default = DEFAULTS[key]
        if isinstance(default, bool):
            value = bool(value)
        elif isinstance(default, int) and not isinstance(default, bool):
            value = max(0, int(value))
        elif isinstance(default, float):
            value = max(0.0, float(value))
        else:
            value = str(value)[:4000 if key == "knowledge" else 300].strip()
        current[key] = value
    if current["push_min"] not in IMPORTANCE:
        current["push_min"] = "important"
    for key in ("quiet_start", "quiet_end"):
        if not re.fullmatch(r"\d{1,2}:\d{2}", current[key] or ""):
            current[key] = DEFAULTS[key]
    config.settings["watch"] = current
    config.save_settings(config.settings)
    return public_settings()


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    for key, cap in (("sent", 200), ("memory", 40), ("ledger", 600), ("runs", 60),
                     ("feedback", 100), ("incidents", 40)):
        if key in state:
            state[key] = state[key][-cap:]
    try:
        STATE_FILE.write_text(json.dumps(state))
    except OSError:
        pass


# ------------------------------------------------------------------ guardrails

def _tz(s: dict):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(s.get("timezone") or "UTC") if s.get("timezone") else None
    except Exception:
        return None


def local_now(s: dict, now: float | None = None) -> datetime:
    tz = _tz(s)
    return datetime.fromtimestamp(now or time.time(), tz) if tz else datetime.fromtimestamp(now or time.time())


def _minutes(hhmm: str) -> int:
    h, _, m = hhmm.partition(":")
    return int(h) * 60 + int(m or 0)


def in_quiet_hours(s: dict, now: float | None = None) -> bool:
    t = local_now(s, now)
    minute = t.hour * 60 + t.minute
    start, end = _minutes(s["quiet_start"]), _minutes(s["quiet_end"])
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def sent_today(state: dict, s: dict, importance: str, now: float | None = None) -> int:
    day = local_now(s, now).date()
    return sum(1 for m in state.get("sent", [])
               if m.get("importance") == importance and local_now(s, m["t"]).date() == day)


def spent(state: dict, days: int = 30, now: float | None = None) -> float:
    cutoff = (now or time.time()) - days * 86400
    return round(sum(e.get("cost") or 0 for e in state.get("ledger", []) if e["t"] >= cutoff), 4)


def muted(s: dict, topic: str, now: float | None = None) -> bool:
    now = now or time.time()
    topic = (topic or "").lower()
    return any(m.get("until", 0) > now and m.get("topic", "").lower() in topic
               for m in s.get("mutes") or [] if m.get("topic"))


def may_send(s: dict, state: dict, importance: str, topic: str,
             now: float | None = None) -> tuple[bool, str]:
    """Whether a message may go out now, and why not."""
    now = now or time.time()
    if importance not in IMPORTANCE:
        importance = "info"
    if s.get("paused_until", 0) > now and importance != "critical":
        return False, "paused"
    if muted(s, topic, now):
        return False, "muted topic"
    if importance != "critical" and in_quiet_hours(s, now):
        return False, "quiet hours"
    limit = {"info": s["info_per_day"], "important": s["important_per_day"]}.get(importance)
    if limit is not None and sent_today(state, s, importance, now) >= limit:
        return False, "daily limit"
    for m in reversed(state.get("sent", [])):
        if now - m["t"] > REPEAT_WINDOW:
            break
        if topic and m.get("topic") == topic and RANK[importance] <= RANK.get(m.get("importance"), 0):
            return False, "said that already"
    return True, ""


def budget_left(s: dict, state: dict, now: float | None = None) -> float | None:
    if not s.get("budget_usd"):
        return None
    return round(s["budget_usd"] - spent(state, 30, now), 4)


# ------------------------------------------------------------------ incidents

INVESTIGATED_FOR = 1800       # the same thing is looked into at most every 30 minutes


def _incident_key(event: dict) -> str:
    return f"{event.get('kind', '')}:{event.get('target', '')}"


def note_event(event: dict) -> None:
    """activity.push hands every warning here; the scheduler bundles them.
    PocketADM's own actions are not incidents (the rounds see them), and a
    container that keeps crashing is looked into once, not every minute."""
    if event.get("severity") not in ("warn", "crit") or event.get("source") in ("watch", "pocketadm"):
        return
    if not settings()["enabled"]:
        return
    state = load_state()
    key = _incident_key(event)
    if time.time() - (state.get("investigated") or {}).get(key, 0) < INVESTIGATED_FOR:
        return
    incidents = state.setdefault("incidents", [])
    if not incidents:
        state["first_incident_at"] = time.time()
    if any(_incident_key(e) == key for e in incidents) and len(incidents) > 5:
        return
    incidents.append({k: event.get(k) for k in ("t", "category", "kind", "title", "detail",
                                                  "severity", "target")})
    save_state(state)


def threshold_events(snapshot: dict) -> list[dict]:
    """Thresholds nobody logs: a filling disk, memory pressure."""
    out = []
    disk = (snapshot.get("disk") or {}).get("percent", 0)
    if disk >= 92:
        out.append({"category": "system", "kind": "disk.full", "severity": "crit",
                    "title": f"The system disk is {disk:.0f}% full", "target": "disk"})
    elif disk >= 85:
        out.append({"category": "system", "kind": "disk.high", "severity": "warn",
                    "title": f"The system disk is {disk:.0f}% full", "target": "disk"})
    mem = (snapshot.get("memory") or {}).get("percent", 0)
    if mem >= 95:
        out.append({"category": "system", "kind": "memory.high", "severity": "warn",
                    "title": f"Memory is {mem:.0f}% used", "target": "memory"})
    return out


# ------------------------------------------------------------------ prompts

SYSTEM = """You are the watch of PocketADM: a background agent that keeps an eye on the \
user's self-hosted server and writes to them only when something is worth knowing. The user \
runs this server for themselves (and maybe family or a few users); they are not necessarily \
a sysadmin. They want to understand what is going on and whether they need to do something.

How you write:
- {language}, informal and direct — like a short message from a capable friend who looks after \
the server.
- Plain prose. No headings, no markdown symbols like # or **, no tables, no emojis, no greeting, \
no sign-off.
- Short: usually 2–4 sentences. The most important thing first, then cause or context, then — \
only if needed — what to do. Commands to copy in `backticks`.
- Concrete numbers, times and comparisons ("since 14:20", "three times the usual") instead of \
vague words.
- Vary how you start and phrase things; do not repeat what the user already knows.
- Honest: only facts from your tools and the context below. Mark guesses as guesses. Never invent \
numbers.

You can only look, never change anything. If something needs doing, name the exact step or \
command the user can run — or suggest asking PocketADM's assistant to do it.
"""

KIND_PROMPTS = {
    "observe": """Your job now: a round. Check what is going on and decide whether the user \
should know.

Worth a message: problems and risks (outages, error bursts, failed backups or timers, security \
events with real risk, filling disks, expiring certificates, reachability problems); notable \
developments (a trend with a forecast like "the disk is full in about six weeks", a service \
using far more resources than before); now and then something genuinely useful.
Not worth a message: the normal state ("everything is fine"), things you already reported \
without anything new, states the user told you are intentional, scanner noise that got nowhere.

The focus suggestion is there for variety — if something more important is going on elsewhere, \
follow that. Rather one thing thoroughly than everything superficially. Most rounds end silent, \
and that is good.""",
    "incident": """Your job now: events need a look. The live monitors flagged the events \
listed below. Find out with your tools what happened: the cause (logs, health checks, exit codes, \
out-of-memory, metrics), the impact (which service, who is affected) and whether it has resolved \
itself. Events with a common cause belong in one message. Then write a short explanation and — \
if needed — one concrete next step. For a false alarm, a harmless self-healing or a known state: \
stay silent, or use importance "info". "critical" only for a real outage of something important \
or a suspected break-in.""",
    "weekly": """Your job now: the look back on the week. It is Sunday evening. Look at the \
past week (the activity, the health report, updates, anything that stood out) and write a short \
review in 3–6 sentences of prose: what was notable (good or bad), a comparison where it helps, and \
— if useful — one recommendation. If the week was uneventful, one sentence is enough, or stay \
silent. Importance "info".""",
    "test": """Your job now: the user asked you to check now. Look at the server's current \
state and ALWAYS reply (decision "notify"): if something deserves attention, say what; if all is \
well, say so in one or two sentences with one concrete detail that shows you looked. Importance \
"info" unless something is genuinely wrong.""",
}

DECISION_TEXT = """
When you are done, end your reply with exactly one line of JSON and nothing after it:
{"decision": "notify" or "silent", "importance": "critical" or "important" or "info", \
"topic": "<short-topic-key like disk-root or nextcloud-down>", \
"title": "<at most 8 words>", "message": "<the message text>", "remember": "<optional note \
for your future self, or empty>"}
"""

DECISION_TOOLS = [
    {"name": "notify",
     "description": "Send the user a message and end this run.",
     "parameters": {"type": "object", "properties": {
         "text": {"type": "string", "description": "The finished message: plain prose, short, "
                                                   "no headings, no emojis."},
         "importance": {"type": "string", "enum": list(IMPORTANCE)},
         "topic": {"type": "string", "description": "Short topic key, e.g. disk-root, backup, "
                                                    "nextcloud-down"},
         "title": {"type": "string", "description": "At most 8 words."}},
         "required": ["text", "importance", "topic"]}},
    {"name": "stay_silent",
     "description": "Send nothing and end this run.",
     "parameters": {"type": "object", "properties": {
         "reason": {"type": "string", "description": "Short reason (internal, the user does not see it)."}},
         "required": ["reason"]}},
    {"name": "remember",
     "description": "Keep a short note for future runs (a baseline to compare against, "
                    "something the user said is intentional).",
     "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                    "required": ["text"]}},
]
READ_TOOLS = ["run_command", "read_file", "list_dir", "search_files"]


async def context_text(kind: str, incidents: list[dict]) -> str:
    """What the agent should not have to rediscover: a cheap picture of the
    server, what was already said, and what the user asked for."""
    from . import dockerapi, metrics, reports, sysinfo, updates
    parts: list[str] = []
    try:
        snap = await asyncio.to_thread(sysinfo.snapshot)
        parts.append(f"Now: CPU {snap['cpu_percent']}%, memory {snap['memory']['percent']}%, "
                     f"disk {snap['disk']['percent']}% ({snap['disk']['free'] // 10**9} GB free), "
                     f"load {snap['load'][0]:.2f} on {snap['cpu_count']} cores, "
                     f"up {int(snap['uptime'] // 86400)} days.")
    except Exception:
        pass
    try:
        hist = metrics.history(60)
        pings = [p["ping"] for p in hist if "ping" in p]
        if pings:
            lost = sum(1 for p in pings if p is None)
            good = [p for p in pings if p is not None]
            parts.append(f"Internet, last hour: {lost} of {len(pings)} probes failed"
                         + (f", median latency {sorted(good)[len(good) // 2]:.0f} ms" if good else ""))
    except Exception:
        pass
    try:
        containers = await dockerapi.list_containers(all_=True)
        bad = [f"{c['name']} ({c['state']}{'/' + c['health'] if c['health'] else ''})"
               for c in containers if c["state"] != "running" or c["health"] == "unhealthy"]
        parts.append(f"Containers: {sum(c['state'] == 'running' for c in containers)}"
                     f"/{len(containers)} running" + (f"; not healthy: {', '.join(bad[:12])}" if bad else ""))
    except Exception:
        pass
    try:
        ups = [u for u in await updates.check_docker_updates()
               if u["update_available"] and not u["ignored"]]
        if ups:
            parts.append("Pending image updates: " + ", ".join(
                f"{u['label']} ({u.get('priority', 'low')}, {u.get('age_days') or '?'} days old)"
                for u in ups[:15]))
    except Exception:
        pass
    try:
        r = reports.latest_report()
        if r:
            issues = [f"{c['title']} [{c['status']}]: {c['summary'][:120]}" for c in r["checks"]
                      if c["status"] in ("warn", "crit") and not c.get("muted")]
            parts.append(f"Last health check ({_ago(r['time'])}): score {r.get('points', '?')}/100"
                         + ("; " + "; ".join(issues[:8]) if issues else ""))
    except Exception:
        pass
    recent = [e for e in list(activity.EVENTS)[-400:]
              if e["severity"] in ("warn", "crit") and time.time() - e["t"] < 6 * 3600]
    if recent and kind != "incident":
        parts.append("Warnings in the activity feed, last 6 hours: " + "; ".join(
            f"{_clock(e['t'])} {e['title']}" for e in recent[-12:]))
    if incidents:
        parts.append("EVENTS TO INVESTIGATE:\n" + "\n".join(
            f"- {_clock(e.get('t') or time.time())} [{e.get('severity')}] {e.get('title')}"
            + (f" — {e.get('detail')}" if e.get('detail') else "") for e in incidents[-20:]))
    state = load_state()
    sent = state.get("sent", [])[-10:]
    if sent:
        parts.append("What you already told the user recently (do not repeat without news):\n"
                     + "\n".join(f"- {_ago(m['t'])}: [{m.get('importance')}] {m.get('topic')}: "
                                 f"{m.get('text', '')[:160]}" for m in sent))
    s = settings()
    mutes = [m for m in s.get("mutes") or [] if m.get("until", 0) > time.time()]
    if mutes:
        parts.append("Topics the user muted (do not report): " + ", ".join(
            m["topic"] + (f" ({m['note']})" if m.get("note") else "") for m in mutes))
    unhelpful = [f["topic"] for f in state.get("feedback", [])[-20:] if not f.get("helpful")]
    if unhelpful:
        parts.append("The user found messages on these topics not helpful: "
                     + ", ".join(sorted(set(unhelpful))[:10]))
    memory = state.get("memory", [])[-15:]
    if memory:
        parts.append("Your notes from earlier runs:\n" + "\n".join(
            f"- {_day(m['t'])}: {m['text']}" for m in memory))
    if s.get("knowledge"):
        parts.append("What the user wants you to know about this server:\n" + s["knowledge"])
    return "\n\n".join(parts)


def _ago(t: float) -> str:
    minutes = int((time.time() - t) / 60)
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // 1440} days ago"


def _clock(t: float) -> str:
    return local_now(settings(), t).strftime("%a %H:%M")


def _day(t: float) -> str:
    return local_now(settings(), t).strftime("%d.%m.")


def system_prompt(kind: str, engine: bool) -> str:
    s = settings()
    language = LANGUAGES.get((s.get("lang") or "en")[:2].lower(), s.get("lang") or "English")
    text = SYSTEM.format(language=f"Write in {language}") + "\n" + KIND_PROMPTS[kind]
    if engine:
        text += "\n" + DECISION_TEXT
    else:
        text += ("\n\nFinish with exactly one call of notify or stay_silent. Use remember for "
                 "notes worth keeping for later runs.")
    return text


# ------------------------------------------------------------------ decisions

_JSON_LINE = re.compile(r"\{[^{}]*\"decision\"[^{}]*\}\s*$", re.S)


def parse_decision(text: str) -> dict:
    """The JSON line an engine ends with; a reply without one is treated as a
    message only when it is clearly meant as one (test runs)."""
    match = _JSON_LINE.search((text or "").strip())
    if not match:
        return {"decision": "silent", "reason": "no decision line"}
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return {"decision": "silent", "reason": "unreadable decision line"}
    decision = "notify" if str(data.get("decision", "")).lower() == "notify" else "silent"
    importance = str(data.get("importance", "info")).lower()
    return {"decision": decision,
            "importance": importance if importance in IMPORTANCE else "info",
            "topic": str(data.get("topic") or "general")[:60],
            "title": str(data.get("title") or "")[:80],
            "text": str(data.get("message") or "").strip(),
            "remember": str(data.get("remember") or "")[:300]}


_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍⌀-⏿]")


def clean_text(text: str) -> str:
    text = _EMOJI.sub("", text or "")
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def title_of(decision: dict) -> str:
    if decision.get("title"):
        return decision["title"]
    first = re.split(r"(?<=[.!?])\s", decision.get("text", ""), 1)[0]
    return (first[:77] + "…") if len(first) > 78 else first


async def _run_api(route: dict, kind: str, prompt: str, trace: list) -> tuple[dict, float]:
    """A run on an API provider or a local model: read-only tools plus the
    decision tools, like Hermes."""
    cfg = ai._cfg_for(route["provider"], route["model"])
    messages: list[dict] = [{"role": "user", "content": prompt}]
    usage = {"input": 0, "output": 0}
    decision: dict | None = None
    tools = READ_TOOLS + DECISION_TOOLS
    sysprompt = system_prompt(kind, engine=False)
    final_text = ""
    for step in range(MAX_STEPS):
        text_parts, calls, blocks = [], [], []
        async for what, payload in ai.get_stream(cfg, messages, sysprompt, tools):
            if what == "text":
                text_parts.append(payload)
            elif what == "tool_call":
                calls.append(payload)
            elif what == "thinking_block":
                blocks.append(payload)
            elif what == "usage":
                usage["input"] += payload["input"]
                usage["output"] += payload["output"]
        msg: dict = {"role": "assistant", "content": "".join(text_parts), "tool_calls": calls}
        if blocks:
            msg["thinking_blocks"] = blocks
        messages.append(msg)
        if "".join(text_parts).strip():
            final_text = "".join(text_parts)
        if not calls:
            if step < MAX_STEPS - 1:
                messages.append({"role": "user", "content": "Finish now with notify or stay_silent."})
                continue
            break
        for tc in calls:
            name, args = tc["name"], tc.get("args") or {}
            if name == "notify":
                decision = {"decision": "notify", "text": str(args.get("text", "")),
                            "importance": args.get("importance", "info"),
                            "topic": str(args.get("topic") or "general")[:60],
                            "title": str(args.get("title") or "")[:80]}
                out = "sent"
            elif name == "stay_silent":
                decision = {"decision": "silent", "reason": str(args.get("reason", ""))[:200]}
                out = "ok"
            elif name == "remember":
                _remember(str(args.get("text", "")))
                out = "noted"
            elif agents.sentinel_may_run(name, args):
                t0 = time.time()
                out = await ai.execute_tool(name, args, ai.DEFAULT_WORKDIR)
                trace.append({"tool": name, "detail": agents._args_summary(name, args),
                              "output": str(out)[:400], "ms": int((time.time() - t0) * 1000)})
            else:
                out = agents.SENTINEL_BLOCKED
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(out)})
        if decision:
            break
    cost = ai.estimate_cost(cfg["provider"], cfg["model"], usage["input"], usage["output"]) or 0.0
    ai._persist_usage(cfg, usage, cost)
    if not decision:
        decision = parse_decision(final_text) if final_text else {"decision": "silent",
                                                                  "reason": "no decision"}
    return decision, cost


async def _run_engine(route: dict, kind: str, prompt: str, trace: list) -> tuple[dict, float]:
    """A run on a subscription: the CLI investigates in plan mode (it may look,
    never change) and ends with the decision as a line of JSON."""
    result = await engines.run_headless(
        route["provider"], system_prompt(kind, engine=True) + "\n\n" + prompt,
        model=route.get("model", ""), mode="plan", timeout=900)
    trace.extend(result.get("steps", [])[:30])
    decision = parse_decision(result.get("text", ""))
    if decision.get("remember"):
        _remember(decision["remember"])
    return decision, 0.0


def _remember(text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    state = load_state()
    state.setdefault("memory", []).append({"t": time.time(), "text": text[:300]})
    save_state(state)


# ------------------------------------------------------------------ delivery

def actions_for(text: str, topic: str) -> list[dict]:
    """Links from a message into the app: the container it names, updates,
    storage, health, the assistant."""
    out: list[dict] = []
    hay = f"{topic} {text}".lower()
    try:
        names = [e["target"] for e in list(activity.EVENTS)[-300:]
                 if e["category"] == "containers" and e.get("target")]
    except Exception:
        names = []
    seen = set()
    for name in sorted(set(names), key=len, reverse=True):
        if len(name) > 2 and name.lower() in hay and name not in seen:
            out.append({"kind": "container", "label": f"Open {name}", "target": name})
            seen.add(name)
        if len(out) >= 2:
            break
    if any(w in hay for w in ("update", "image", "version")):
        out.append({"kind": "open", "label": "Updates", "target": "updates"})
    if any(w in hay for w in ("disk", "storage", "space", "full")):
        out.append({"kind": "open", "label": "Storage", "target": "storage"})
    if any(w in hay for w in ("backup",)):
        out.append({"kind": "open", "label": "Health", "target": "checks"})
    if any(w in hay for w in ("login", "ssh", "sudo", "banned", "attack")):
        out.append({"kind": "open", "label": "Activity", "target": "activity"})
    out.append({"kind": "assistant", "label": "Ask the assistant",
                "prompt": f"The watch wrote: \"{text[:600]}\" — look into it and tell me what to do."})
    return out[:4]


async def deliver(decision: dict, kind: str, trace: list, state: dict, s: dict) -> dict:
    text = clean_text(decision.get("text", ""))
    importance = decision.get("importance", "info")
    topic = decision.get("topic", "general")
    title = title_of({**decision, "text": text})
    notif = agents.add_notification("watch", STATUS_OF.get(importance, "info"), title, text,
                                    steps=trace)
    notif.pop("_repeat", None)
    agents.annotate_notification(notif["id"], kind="watch", importance=importance, topic=topic,
                                 run=kind, actions=actions_for(text, topic))
    state.setdefault("sent", []).append({"t": time.time(), "importance": importance,
                                         "topic": topic, "text": text[:300], "id": notif["id"]})
    activity.push("app", "watch.message", "Watch: " + title, detail=text[:300],
                  severity={"critical": "crit", "important": "warn"}.get(importance, "info"),
                  source="watch")
    if RANK.get(importance, 0) >= RANK.get(s.get("push_min", "important"), 1):
        await push_external(s, title, text, importance)
    return notif


async def push_external(s: dict, title: str, text: str, importance: str) -> list[str]:
    """ntfy and Matrix, best effort. Returns where it went."""
    sent = []
    name = config.get_server_name() or "PocketADM"
    ntfy = s.get("ntfy_url") or config.get_ntfy_url()
    if ntfy:
        await agents._push_ntfy(ntfy, STATUS_OF.get(importance, "info"), f"{name}: {title}", text)
        sent.append("ntfy")
    if s.get("matrix_homeserver") and s.get("matrix_room") and s.get("matrix_token"):
        if await send_matrix(s["matrix_homeserver"], s["matrix_room"], s["matrix_token"],
                             f"{text}" if importance == "info" else f"{title}\n\n{text}"):
            sent.append("matrix")
    return sent


async def send_matrix(homeserver: str, room: str, token: str, text: str) -> bool:
    """One message into a Matrix room (Element shows it like any chat)."""
    url = (homeserver.rstrip("/") + "/_matrix/client/v3/rooms/" + _quote(room)
           + "/send/m.room.message/" + secrets.token_hex(8))
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.put(url, json={"msgtype": "m.text", "body": text},
                                 headers={"Authorization": f"Bearer {token}"})
            return r.status_code < 300
    except Exception:
        return False


def _quote(room: str) -> str:
    from urllib.parse import quote
    return quote(room, safe="")


# ------------------------------------------------------------------ runs

async def run(kind: str = "observe", incidents: list[dict] | None = None,
              force: bool = False) -> dict:
    """One look at the server. Returns what happened (for the app and tests)."""
    global _running
    if _running and not force:
        return {"decision": "busy"}
    s = settings()
    state = load_state()
    route = config.get_ai_route("watch")
    record = {"t": time.time(), "kind": kind, "provider": route.get("provider", ""),
              "model": route.get("model", "")}
    if not route.get("provider") or not accounts.usable(route["provider"]):
        record.update(decision="error", error="No AI is connected for the watch.")
        state.setdefault("runs", []).append(record)
        save_state(state)
        return record
    left = budget_left(s, state)
    if left is not None and left <= 0 and kind not in ("test",):
        record.update(decision="skipped", reason="budget used up")
        state.setdefault("runs", []).append(record)
        save_state(state)
        return record
    _running = True
    trace: list = []
    try:
        lens = LENSES[int(time.time() // 3600) % len(LENSES)]
        prompt = (f"{'Round' if kind == 'observe' else kind.capitalize()} at "
                  f"{local_now(s).strftime('%A %d.%m. %H:%M')}."
                  + (f" Focus suggestion: {lens}." if kind == "observe" else "")
                  + "\n\nWhat PocketADM already knows:\n" + await context_text(kind, incidents or []))
        engine = route["provider"] in engines.ENGINES
        runner = _run_engine if engine else _run_api
        decision, cost = await asyncio.wait_for(runner(route, kind, prompt, trace), 1200)
    except Exception as e:  # noqa: BLE001 — recorded and shown on the watch screen
        record.update(decision="error", error=f"{type(e).__name__}: {str(e)[:300]}")
        state = load_state()
        state.setdefault("runs", []).append(record)
        save_state(state)
        return record
    finally:
        _running = False
    state = load_state()
    state.setdefault("ledger", []).append({"t": time.time(), "cost": cost, "kind": kind})
    record["cost"] = cost
    if kind == "test" and decision.get("decision") != "notify" and decision.get("text"):
        decision["decision"] = "notify"
    if decision.get("decision") == "notify" and decision.get("text"):
        importance = decision.get("importance", "info")
        allowed, why = (True, "") if kind == "test" else may_send(s, state, importance,
                                                                  decision.get("topic", ""))
        if allowed:
            notif = await deliver(decision, kind, trace, state, s)
            record.update(decision="notify", importance=importance,
                          topic=decision.get("topic"), notification=notif["id"])
            audit.record("watch_message", target=decision.get("topic", ""), source="watch",
                         detail=title_of(decision), status=STATUS_OF.get(importance, "info"))
        else:
            record.update(decision="held", reason=why, topic=decision.get("topic"),
                          importance=importance)
            state.setdefault("held", []).append({"t": time.time(), **{k: decision.get(k) for k in
                                                                      ("importance", "topic", "text", "title")}})
            state["held"] = state["held"][-20:]
    else:
        record.update(decision="silent", reason=decision.get("reason", ""))
    state.setdefault("runs", []).append(record)
    save_state(state)
    return record


# ------------------------------------------------------------------ scheduler

def _week_key(dt: datetime) -> str:
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


async def tick(now: float | None = None) -> str | None:
    """One scheduler step: run what is due. Returns the kind it ran."""
    from . import sysinfo
    now = now or time.time()
    s = settings()
    if not s["enabled"] or config.DEMO:
        return None
    state = load_state()
    try:
        snap = await asyncio.to_thread(sysinfo.snapshot)
        for event in threshold_events(snap):
            key = event["kind"]
            last = state.get("thresholds", {}).get(key, 0)
            if now - last > 6 * 3600:          # a full disk is news once, not every minute
                state.setdefault("thresholds", {})[key] = now
                state.setdefault("incidents", []).append({**event, "t": now})
                state.setdefault("first_incident_at", now)
        save_state(state)
    except Exception:
        pass
    incidents = state.get("incidents") or []
    if incidents and now - state.get("first_incident_at", now) >= DEBOUNCE:
        state["incidents"], state["first_incident_at"] = [], 0
        seen = state.setdefault("investigated", {})
        for e in incidents:
            seen[_incident_key(e)] = now
        state["investigated"] = {k: v for k, v in seen.items() if now - v < 86400}
        save_state(state)
        await run("incident", incidents)
        return "incident"
    if s.get("paused_until", 0) > now:
        return None
    local = local_now(s, now)
    if s.get("weekly") and local.weekday() == 6 and local.hour * 60 + local.minute >= 18 * 60 + 30 \
            and state.get("last_weekly") != _week_key(local):
        state["last_weekly"] = _week_key(local)
        save_state(state)
        await run("weekly")
        return "weekly"
    interval = s.get("interval_min", 0)
    if interval and now - state.get("last_round", 0) >= interval * 60 and not in_quiet_hours(s, now):
        left = budget_left(s, state, now)
        # spend evenly: a round is skipped while the month's spending runs ahead
        if left is not None and s["budget_usd"]:
            pace = spent(state, 30, now) / s["budget_usd"]
            if pace > 0.9:
                return None
        state["last_round"] = now
        save_state(state)
        await run("observe")
        return "observe"
    return None


async def _scheduler() -> None:
    await asyncio.sleep(120)
    while True:
        try:
            await tick()
        except Exception:
            pass
        await asyncio.sleep(60)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.ensure_future(_scheduler())


# ------------------------------------------------------------------ status for the app

def status() -> dict:
    s = settings()
    state = load_state()
    route = config.get_ai_route("watch")
    now = time.time()
    interval = s.get("interval_min", 0)
    next_round = (state.get("last_round", 0) + interval * 60) if interval else None
    runs = list(reversed(state.get("runs", [])))[:15]
    return {
        "settings": public_settings(),
        "route": {"provider": route.get("provider", ""), "model": route.get("model", ""),
                  "label": accounts.provider_label(route.get("provider", "")),
                  "usable": accounts.usable(route.get("provider", ""))},
        "running": _running,
        "paused": s.get("paused_until", 0) > now,
        "quiet_now": in_quiet_hours(s, now),
        "last_round": state.get("last_round") or None,
        "next_round": max(next_round, now) if next_round else None,
        "sent_today": {i: sent_today(state, s, i, now) for i in IMPORTANCE},
        "spent_30d": spent(state, 30, now),
        "budget_left": budget_left(s, state, now),
        "pending_events": len(state.get("incidents") or []),
        "runs": runs,
        "held": list(reversed(state.get("held", [])))[:5],
        "memory": [m["text"] for m in state.get("memory", [])[-10:]],
    }


def pause(minutes: int) -> dict:
    s = settings()
    s["paused_until"] = time.time() + max(0, minutes) * 60 if minutes else 0
    config.settings["watch"] = s
    config.save_settings(config.settings)
    return status()


def mute(topic: str, hours: float, note: str = "") -> dict:
    s = settings()
    topic = (topic or "").strip()[:60]
    mutes = [m for m in s.get("mutes") or [] if m.get("topic") != topic and m.get("until", 0) > time.time()]
    if hours > 0 and topic:
        mutes.append({"topic": topic, "until": time.time() + hours * 3600, "note": note[:120]})
    s["mutes"] = mutes
    config.settings["watch"] = s
    config.save_settings(config.settings)
    return status()


def feedback(notification_id: str, helpful: bool) -> None:
    state = load_state()
    topic = next((m.get("topic") for m in state.get("sent", []) if m.get("id") == notification_id), "")
    state.setdefault("feedback", []).append({"t": time.time(), "id": notification_id,
                                             "topic": topic, "helpful": bool(helpful)})
    save_state(state)
    agents.annotate_notification(notification_id, feedback="helpful" if helpful else "not_helpful")


def forget_memory() -> dict:
    state = load_state()
    state["memory"] = []
    save_state(state)
    return status()
