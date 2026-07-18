"""Skills — reusable how-to recipes the agent (and the user) can maintain.

Memory stores *facts* about the server; a skill stores a *procedure*: the
known-good way to do a recurring task here (deploy this stack, add a reverse
proxy host, clean up disk). The agent sees an index of skill names +
one-liners in its system prompt, reads the full recipe with read_skill before
doing such a task, and — hermes-agent style — writes/updates skills itself
after figuring out something non-obvious, so the next session starts smart.

Skills are plain markdown files in /data/skills, fully visible and editable
under Settings → AI, so everything the agent "knows" stays inspectable.
"""

from __future__ import annotations

import re

from . import config

SKILLS_DIR = config.DATA_DIR / "skills"
MAX_SKILL_CHARS = 20000
MAX_SKILLS = 100
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", (name or "").lower().strip()).strip("-")[:64]
    if not _NAME_RE.match(s or ""):
        raise ValueError("Skill names are lowercase words joined by dashes, e.g. deploy-my-site")
    return s


def list_skills() -> list[dict]:
    if not SKILLS_DIR.is_dir():
        return []
    out = []
    for p in sorted(SKILLS_DIR.glob("*.md")):
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        out.append({"name": p.stem, "description": _describe(text),
                    "chars": len(text)})
    return out


def read(name: str) -> str:
    p = SKILLS_DIR / (_slug(name) + ".md")
    if not p.is_file():
        have = ", ".join(s["name"] for s in list_skills()) or "none yet"
        raise FileNotFoundError(f"No skill named '{name}'. Available: {have}")
    return p.read_text(errors="replace")


def save(name: str, content: str) -> str:
    slug = _slug(name)
    content = (content or "").strip()
    if not content:
        raise ValueError("Skill content is empty")
    if len(content) > MAX_SKILL_CHARS:
        content = content[:MAX_SKILL_CHARS]
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    is_new = not (SKILLS_DIR / (slug + ".md")).exists()
    if is_new and len(list_skills()) >= MAX_SKILLS:
        raise ValueError(f"Too many skills (max {MAX_SKILLS}) — delete or merge some first")
    (SKILLS_DIR / (slug + ".md")).write_text(content + "\n")
    return slug


def delete(name: str) -> None:
    p = SKILLS_DIR / (_slug(name) + ".md")
    if p.is_file():
        p.unlink()


def prompt_index() -> str:
    """The compact skills list for the system prompt."""
    skills = list_skills()
    if not skills:
        return ""
    lines = [f"- {s['name']} — {s['description']}" for s in skills[:MAX_SKILLS]]
    return "\n".join(lines)


def _describe(text: str) -> str:
    """One-liner for the index: the first `> …` quote line, else the first
    body paragraph line."""
    fallback = ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            return line.lstrip("> ").strip()[:160]
        if line.startswith("#"):
            continue
        if not fallback:
            fallback = line[:160]
    return fallback


def seed_defaults() -> None:
    """First start: install the starter skills. Never overwrites user files."""
    if SKILLS_DIR.is_dir():
        return
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    for name, content in _DEFAULTS.items():
        (SKILLS_DIR / (name + ".md")).write_text(content.strip() + "\n")


_DEFAULTS = {
    "change-a-compose-service": """
# Change a compose-managed service

> Edit, rebuild and redeploy a service that belongs to a docker compose stack — the right way.

1. Find the stack: the server map lists `project @ host dir`. Or:
   `docker inspect <container> --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'`
2. Edit the source/config under that directory (file tools can write host paths directly).
3. Rebuild/redeploy from the stack dir, e.g.
   `cd <dir> && docker compose build <service> && docker compose up -d <service>`
   (add `--profile <p>` if the compose file uses profiles — check with `docker compose config --profiles`).
4. Verify: `docker compose ps` + `docker logs <container> --tail 20` + curl the service.

Never `docker stop` + `docker run` a replacement for a compose service: the new container
loses its compose labels, and future `compose up`/updates will conflict with or miss it.
""",
    "debug-a-web-service": """
# Debug a web service that doesn't respond

> Systematic 4-step check before changing anything: state, logs, port, network.

1. State: `docker ps -a --filter name=<svc>` — restarting? exited? healthcheck failing?
2. Logs: `docker logs <svc> --tail 50` — crash loops and bind errors show here.
3. Port: `ss -tlnp | grep <port>` on the host; then `curl -sv http://localhost:<port>/` .
4. From inside its network (if host curl fails):
   `docker run --rm --network <net> curlimages/curl -s http://<container>:<port>/`
If it sits behind a reverse proxy, test the container directly first, then through the proxy —
that splits "app broken" from "proxy misrouted".
""",
    "free-up-disk-space": """
# Free up disk space safely

> Find the hogs, then reclaim in order of safety. Ask before anything destructive.

1. Overview: `df -h /` and `docker system df`.
2. Hogs: `du -xh --max-depth=2 /var/lib/docker /srv 2>/dev/null | sort -hr | head -25`.
3. Container logs: `find /var/lib/docker/containers -name '*-json.log' -size +100M -exec ls -lh {} +`
   — truncate big ones with `truncate -s 0 <file>` (safe, containers keep running).
4. Unused images: `docker image prune -a -f` reclaims most, but re-pulling costs bandwidth,
   and it removes rollback snapshots unless they're tagged — confirm with the user first.
5. Build cache: `docker builder prune -f`.
Never delete volumes without listing what's in them and getting explicit confirmation.
""",
}
