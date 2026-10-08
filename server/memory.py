"""What the assistant knows about this server between conversations.

Until 0.26 this was one Markdown file the agent appended to. It grew into a
wall of text: the same fact three times, notes that were true a month ago,
headings in two languages — hard to read on a phone and costly to send along
with every message. Memory is now a list of short facts ("notes"), each with
a topic, who wrote it, and when it was last confirmed:

  * the assistant saves one fact at a time (`remember`) and corrects or drops
    a note by its id (`remember(replaces=…)`, `forget`), instead of rewriting
    the whole file;
  * a fact that is nearly the same as an existing note updates that note
    rather than adding a duplicate;
  * the prompt gets the notes grouped by topic, pinned and recent ones first,
    within a fixed budget;
  * "Tidy up" merges duplicates and drops what is outdated, with one undo.

The coding agents (Claude Code, Codex, Vibe) read the same notes in their
context and save new ones through the `pocketadm` command (server/cli.py), so
this file is re-read on every use and written under a lock.
"""
from __future__ import annotations

import contextlib
import difflib
import fcntl
import json
import os
import re
import secrets
import time

from . import config

FILE = config.DATA_DIR / "agent-memory.json"
PREVIOUS = config.DATA_DIR / "agent-memory.prev.json"   # for undo after tidy/import
LEGACY = config.DATA_DIR / "agent-memory.md"
LOCK = config.DATA_DIR / ".agent-memory.lock"

PROMPT_BUDGET = 6000        # characters of notes in a system prompt
MAX_NOTE = 600              # characters per note
MAX_NOTES = 300
STALE_DAYS = 90

TOPICS = [
    ("server", "Server & hardware"),
    ("services", "Apps & services"),
    ("network", "Network & domains"),
    ("storage", "Storage & backups"),
    ("security", "Access & security"),
    ("procedures", "How things are done"),
    ("projects", "Projects"),
    ("preferences", "Preferences"),
    ("other", "Other"),
]
TOPIC_IDS = [t for t, _ in TOPICS]
TOPIC_LABELS = dict(TOPICS)

# words that place a note in a topic — English and German, because people
# (and their assistants) write notes in their own language
_KEYWORDS = {
    "preferences": ("prefer", "always ", "never ", "don't ", "do not", "please", "language",
                    "answer in", "user wants", "user likes", "bevorzug", "immer ", "niemals",
                    "möchte", "will ", "wünsch", "sprache", "antworte"),
    "procedures": ("to deploy", "deploy", "rebuild", "how to", "steps", "run `", "procedure",
                   "restart with", "after changing", "nach änderung", "so geht", "befehl",
                   "docker compose up", "compose build"),
    "security": ("password", "passwort", "2fa", "totp", "ssh", "firewall", "ufw", "fail2ban",
                 "authentik", "oidc", "sso", "vpn", "wireguard", "token", "key rotation",
                 "secret", "zugang", "login"),
    "network": ("domain", "dns", "subdomain", "nginx", "proxy", "npm ", "caddy", "traefik",
                "certificate", "zertifikat", "ipv4", "ipv6", "port ", "desec", "cloudflare",
                "fritzbox", "router", "vps", "tunnel"),
    "storage": ("disk", "ssd", "hdd", "backup", "snapshot", "mount", "volume", "nas", "raid",
                "festplatte", "speicher", "sicherung", "/mnt/", "t5"),
    "projects": ("project", "projekt", "app store", "testflight", "repo", "github", "codemagic",
                 "landing page", "website"),
    "services": ("container", "compose", "stack", "service", "nextcloud", "jellyfin", "matrix",
                 "synapse", "gitea", "vaultwarden", "portainer", "prometheus", "grafana",
                 "dienst", "image"),
    "server": ("cpu", "ram", "memory", "kernel", "ubuntu", "debian", "hostname", "hardware",
               "bios", "gpu", "intel", "amd", "hp ", "nuc", "os "),
}


# ------------------------------------------------------------------ storage

@contextlib.contextmanager
def _locked():
    LOCK.touch(exist_ok=True)
    with open(LOCK, "r+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _read() -> list[dict]:
    try:
        data = json.loads(FILE.read_text())
        return [n for n in data.get("notes", []) if isinstance(n, dict) and n.get("text")]
    except (OSError, ValueError, AttributeError):
        return []


def _write(notes: list[dict]) -> None:
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "notes": notes}, ensure_ascii=False, indent=1))
    os.replace(tmp, FILE)


def _keep_previous() -> None:
    with contextlib.suppress(OSError):
        if FILE.exists():
            PREVIOUS.write_text(FILE.read_text())


def _migrate() -> None:
    """The old Markdown memory becomes notes, once. Never call it while holding
    the lock (flock does not nest)."""
    if FILE.exists() or not LEGACY.exists():
        return
    with _locked():
        if not FILE.exists() and LEGACY.exists():
            _write(parse_markdown(LEGACY.read_text(errors="replace"), source="assistant"))
            with contextlib.suppress(OSError):
                LEGACY.rename(LEGACY.with_suffix(".md.bak"))


def notes() -> list[dict]:
    """All notes, migrating the old Markdown memory on first use."""
    _migrate()
    return _read()


# ------------------------------------------------------------------ helpers

def _new_id() -> str:
    return "m" + secrets.token_hex(3)


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text.lower()).strip()


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


# the specific parts of a fact: numbers, paths, hosts, versions, identifiers
_SPECIFIC = re.compile(r"[\w./:@-]*[\d/:@][\w./:@-]*")


def same_fact(a: str, b: str) -> bool:
    """Two wordings of one fact — not two facts that merely look alike.
    "Service A on port 9000" and "Service B on port 9001" stay apart; a
    correction ("backups at 04:00, not 03:00") is what remember(replaces=…)
    is for."""
    if _norm(a) == _norm(b):
        return True
    if similarity(a, b) < 0.9:
        return False
    return sorted(_SPECIFIC.findall(a.lower())) == sorted(_SPECIFIC.findall(b.lower()))


def classify(text: str) -> str:
    low = " " + text.lower() + " "
    best, score = "other", 0
    for topic, words in _KEYWORDS.items():
        hits = sum(1 for w in words if w in low)
        if hits > score:
            best, score = topic, hits
    return best


def clean_text(text: str) -> str:
    """One fact, one paragraph: no list markers or headings, no runaway length."""
    text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", (text or "").strip())
    text = re.sub(r"^#+\s*", "", text)
    text = re.sub(r"\s+\n", "\n", text).strip()
    if len(text) > MAX_NOTE:
        text = text[:MAX_NOTE - 1].rstrip() + "…"
    return text


def clean_subject(subject: str) -> str:
    """"Projekt "StudGo" — iOS-App (Stand 18.09.2026)" → "Projekt StudGo — iOS-App"."""
    subject = re.sub(r"[*_`\"]", "", (subject or "").strip())
    subject = re.sub(r"\s*\((?:Stand|as of|state)[^)]*\)", "", subject, flags=re.I)
    return subject.strip(" :—–-")[:60]


# a note must never carry a credential: the memory is shown in the app and
# sent to whichever model answers
_SECRET = re.compile(
    r"(sk-[A-Za-z0-9_\-]{16,}|ghp_[A-Za-z0-9]{20,}|xox[bap]-[A-Za-z0-9\-]{10,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:password|passwort|passwd|pw|secret|token|api[_ -]?key)\s*[:=]\s*\S{6,})", re.I)


def has_secret(text: str) -> bool:
    return bool(_SECRET.search(text or ""))


# ------------------------------------------------------------------ changes

def add(text: str, topic: str = "", source: str = "assistant", pinned: bool = False,
        replaces: str = "", subject: str = "") -> tuple[dict, str]:
    """Save a fact. Returns (note, what happened): "added", "updated" (a nearly
    identical note was refreshed instead) or "replaced" (by id)."""
    text = clean_text(text)
    if not text:
        raise ValueError("empty note")
    if has_secret(text):
        raise ValueError("Notes must not contain passwords, keys or tokens. Note where the "
                         "secret is kept instead.")
    topic = topic if topic in TOPIC_IDS else classify((subject + " " + text).strip())
    subject = clean_subject(subject)
    now = time.time()
    _migrate()
    with _locked():
        current = _read()
        if replaces:
            for n in current:
                if n["id"] == replaces:
                    n.update(text=text, topic=topic, updated=now)
                    if subject:
                        n["subject"] = subject
                    if source == "you":
                        n["source"] = "you"
                    _write(current)
                    return n, "replaced"
        for n in current:
            if same_fact(n["text"], text):
                # the same fact again: keep the newer wording, refresh the date
                n.update(text=text if len(text) >= len(n["text"]) * 0.6 else n["text"],
                         updated=now, topic=n.get("topic") or topic)
                _write(current)
                return n, "updated"
        note = {"id": _new_id(), "text": text, "topic": topic, "source": source,
                "created": now, "updated": now, "pinned": bool(pinned)}
        if subject:
            note["subject"] = subject
        current.append(note)
        if len(current) > MAX_NOTES:
            # drop the oldest unpinned notes first
            keep = sorted(current, key=lambda n: (n.get("pinned", False), n.get("updated", 0)),
                          reverse=True)[:MAX_NOTES]
            current = [n for n in current if n in keep]
        _write(current)
        return note, "added"


def edit(note_id: str, text: str | None = None, topic: str | None = None,
         pinned: bool | None = None, subject: str | None = None) -> dict | None:
    _migrate()
    with _locked():
        current = _read()
        for n in current:
            if n["id"] != note_id:
                continue
            if text is not None:
                text = clean_text(text)
                if not text:
                    raise ValueError("empty note")
                if has_secret(text):
                    raise ValueError("Notes must not contain passwords, keys or tokens.")
                n["text"] = text
                n["source"] = "you"
            if topic is not None and topic in TOPIC_IDS:
                n["topic"] = topic
            if pinned is not None:
                n["pinned"] = bool(pinned)
            if subject is not None:
                if clean_subject(subject):
                    n["subject"] = clean_subject(subject)
                else:
                    n.pop("subject", None)
            n["updated"] = time.time()
            _write(current)
            return n
    return None


def forget(note_id: str) -> bool:
    _migrate()
    with _locked():
        current = _read()
        kept = [n for n in current if n["id"] != note_id]
        if len(kept) == len(current):
            return False
        _write(kept)
        return True


def clear() -> None:
    with _locked():
        _keep_previous()
        _write([])


def replace_all(new_notes: list[dict]) -> None:
    with _locked():
        _keep_previous()
        _write(new_notes)


def undo() -> bool:
    """Back to the notes before the last tidy-up, import or clear."""
    with _locked():
        try:
            previous = json.loads(PREVIOUS.read_text())
        except (OSError, ValueError):
            return False
        _write([n for n in previous.get("notes", []) if isinstance(n, dict)])
        with contextlib.suppress(OSError):
            PREVIOUS.unlink()
        return True


def can_undo() -> bool:
    return PREVIOUS.exists()


# ------------------------------------------------------------------ Markdown (old apps, import)

def parse_markdown(text: str, source: str = "you") -> list[dict]:
    """Notes out of free text: each list item or paragraph is a note, a
    heading names the topic (or is kept as context when it names a project)."""
    out: list[dict] = []
    heading = ""
    paragraph: list[str] = []
    now = time.time()

    def push(chunk: str) -> None:
        chunk = clean_text(chunk)
        if not chunk or has_secret(chunk):
            return
        named = _topic_for_heading(heading)
        topic = named or classify((heading + " " + chunk).strip())
        subject = clean_subject(heading) if heading and named in ("", "projects") else ""
        if subject and _topic_for_heading(heading) == "" and "projekt" in heading.lower() + "project":
            topic = "projects"
        if any(same_fact(chunk, n["text"]) for n in out):
            return
        note = {"id": _new_id(), "text": chunk, "topic": topic, "source": source,
                "created": now, "updated": now, "pinned": False}
        if subject:
            note["subject"] = subject
        out.append(note)

    def flush() -> None:
        if paragraph:
            push(" ".join(paragraph))
            paragraph.clear()

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        if re.match(r"^\s*#{1,6}\s", line):
            flush()
            heading = re.sub(r"^\s*#+\s*", "", line).strip()
            heading = re.sub(r"\s*[—–-]\s*Stand.*$", "", heading).strip().strip('"')
            continue
        if re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", line):
            flush()
            if re.match(r"^\s{2,}", raw) and out:
                # an indented sub-item belongs to the note above it
                out[-1]["text"] = clean_text(out[-1]["text"] + " " + clean_text(line))
                continue
            push(line)
            continue
        paragraph.append(line.strip())
    flush()
    return out[:MAX_NOTES]


def _topic_for_heading(heading: str) -> str:
    low = heading.lower()
    for topic, label in TOPICS:
        if topic in low or label.lower() in low:
            return topic
    if "projekt" in low or "project" in low:
        return "projects"
    return ""


def render_markdown(items: list[dict] | None = None) -> str:
    """The notes as Markdown, grouped by topic (for older apps and export)."""
    items = notes() if items is None else items
    lines: list[str] = []
    for topic, label in TOPICS:
        group = [n for n in items if n.get("topic", "other") == topic]
        if not group:
            continue
        lines.append(f"## {label}")
        lines += [f"- {_with_subject(n)}" for n in _ordered(group)]
        lines.append("")
    return "\n".join(lines).strip() + ("\n" if lines else "")


# ------------------------------------------------------------------ prompt

def _with_subject(n: dict) -> str:
    return f"{n['subject']}: {n['text']}" if n.get("subject") else n["text"]


def _ordered(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda n: (not n.get("pinned"), -float(n.get("updated", 0))))


def prompt_block(budget: int = PROMPT_BUDGET) -> str:
    """The notes for a system prompt: grouped by topic, pinned and recent
    first, each with its id so the assistant can correct it. Older notes past
    the budget are left out with a count."""
    items = _ordered(notes())
    if not items:
        return ""
    chosen: list[dict] = []
    used = 0
    for n in items:
        cost = len(_with_subject(n)) + 14
        if used + cost > budget and chosen:
            continue
        chosen.append(n)
        used += cost
    lines: list[str] = []
    for topic, label in TOPICS:
        group = [n for n in chosen if n.get("topic", "other") == topic]
        if not group:
            continue
        lines.append(f"{label}:")
        for n in group:
            age = (time.time() - float(n.get("updated", 0))) / 86400
            stale = " (unconfirmed for %d days)" % age if age > STALE_DAYS else ""
            lines.append(f"- [{n['id']}] {_with_subject(n)}{stale}")
    left = len(items) - len(chosen)
    if left:
        lines.append(f"({left} older notes are not shown here.)")
    return "\n".join(lines)


def stats(items: list[dict] | None = None) -> dict:
    items = notes() if items is None else items
    now = time.time()
    by_topic = {t: 0 for t in TOPIC_IDS}
    for n in items:
        by_topic[n.get("topic", "other") if n.get("topic") in by_topic else "other"] += 1
    chars = sum(len(_with_subject(n)) + 14 for n in items)
    return {
        "count": len(items),
        "pinned": sum(1 for n in items if n.get("pinned")),
        "stale": sum(1 for n in items if now - float(n.get("updated", 0)) > STALE_DAYS * 86400),
        "chars": chars,
        "budget": PROMPT_BUDGET,
        "in_prompt": min(chars, PROMPT_BUDGET),
        "by_topic": by_topic,
        "can_undo": can_undo(),
    }


def overview() -> dict:
    items = notes()
    return {"notes": _ordered(items), "topics": [{"id": t, "label": l} for t, l in TOPICS],
            "stats": stats(items)}


# ------------------------------------------------------------------ tidy up

_TIDY_PROMPT = """You maintain the long-term memory of an AI assistant that manages one server.
Below are its notes as JSON. Rewrite them into a clean, compact set:

- merge notes that say the same thing; keep the most specific, most recent wording
- drop notes that are temporary (one-off task status, "done today", pending reminders that
  were resolved), contradicted by a newer note, or contain credentials
- keep facts that stay true: layout, paths, conventions, how things are deployed, the
  owner's preferences, ongoing projects
- one fact per note, at most two short sentences, in the language the notes are written in
- keep each note's topic, or fix it; topics: {topics}
- keep "pinned": true notes unchanged

- "subject" names what a note is about (a project, an app) when that is not obvious; keep it short

Answer with JSON only: {{"notes": [{{"text": "...", "topic": "...", "subject": "", "pinned": false, "from": ["id", ...]}}]}}
where "from" lists the ids of the original notes each new note comes from.

Notes:
{notes}"""


def _local_tidy(items: list[dict]) -> list[dict]:
    """Without a model: drop exact and near duplicates, keep the newest."""
    out: list[dict] = []
    for n in sorted(items, key=lambda n: -float(n.get("updated", 0))):
        if any(same_fact(n["text"], k["text"]) for k in out):
            continue
        out.append(n)
    return out


async def tidy() -> dict:
    """Merge, shorten and prune the notes with the explanations model, keeping
    the old set for undo. Falls back to removing duplicates when no model is
    available or its answer is unusable."""
    from . import ai
    before = notes()
    if not before:
        return {"before": 0, "after": 0, "used_ai": False, "removed": 0}
    compact = [{"id": n["id"], "text": n["text"], "topic": n.get("topic", "other"),
                "subject": n.get("subject", ""), "pinned": bool(n.get("pinned"))} for n in before]
    used_ai = False
    result: list[dict] = []
    try:
        answer = await ai.one_shot(
            _TIDY_PROMPT.format(topics=", ".join(TOPIC_IDS),
                                notes=json.dumps(compact, ensure_ascii=False)),
            system="You return strict JSON and nothing else.", feature="insights")
        start, end = answer.find("{"), answer.rfind("}")
        data = json.loads(answer[start:end + 1]) if start >= 0 else {}
        by_id = {n["id"]: n for n in before}
        now = time.time()
        for row in data.get("notes", []):
            text = clean_text(str(row.get("text", "")))
            if not text or has_secret(text):
                continue
            sources = [by_id[i] for i in row.get("from", []) if i in by_id]
            topic = row.get("topic") if row.get("topic") in TOPIC_IDS else classify(text)
            subject = clean_subject(str(row.get("subject") or "")) or \
                next((s.get("subject", "") for s in sources if s.get("subject")), "")
            result.append({
                "id": sources[0]["id"] if sources else _new_id(), "text": text, "topic": topic,
                **({"subject": subject} if subject else {}),
                "source": "you" if any(s.get("source") == "you" for s in sources) else "assistant",
                "created": min((s.get("created", now) for s in sources), default=now),
                "updated": max((s.get("updated", now) for s in sources), default=now),
                "pinned": bool(row.get("pinned")) or any(s.get("pinned") for s in sources)})
        # pinned notes always survive
        kept_ids = {r["id"] for r in result}
        result += [n for n in before if n.get("pinned") and n["id"] not in kept_ids]
        used_ai = bool(result)
    except Exception:
        result = []
    if not result:
        result = _local_tidy(before)
    # the model must not have thrown nearly everything away
    if len(result) < max(1, len(before) // 4):
        result, used_ai = _local_tidy(before), False
    replace_all(result)
    return {"before": len(before), "after": len(result), "used_ai": used_ai,
            "removed": max(0, len(before) - len(result))}
