"""Local AI — run models on the server itself via Ollama.

Besides bring-your-own cloud keys, Helmsman can talk to a local Ollama daemon
(OpenAI-compatible at {base}/v1). We auto-detect where it lives (host gateway,
a sibling container, localhost …), recommend models that fit the box's RAM,
install Ollama in one tap, and pull models with live progress — then the local
models show up in the normal chat model picker as provider "ollama".
"""
import asyncio
import os
import shutil
import time

import httpx

from . import config, jobs, sysinfo

DEFAULT_PORT = 11434
_cache: dict = {"base": None, "time": 0.0}
_CACHE_TTL = 20  # seconds

# Curated, RAM-aware suggestions. Names are real Ollama tags. `min_ram` is a
# comfortable lower bound in GB (model + working set) so we don't suggest a
# model that will swap the machine to death.
CATALOG = [
    {"name": "llama3.2:1b", "label": "Llama 3.2 · 1B", "params": "1B", "size": "~1.3 GB",
     "min_ram": 3, "blurb": "Tiny and fast. Great for quick Q&A on low-RAM boxes."},
    {"name": "llama3.2:3b", "label": "Llama 3.2 · 3B", "params": "3B", "size": "~2.0 GB",
     "min_ram": 5, "blurb": "Balanced all-rounder — good chat quality on modest hardware."},
    {"name": "qwen2.5:3b", "label": "Qwen 2.5 · 3B", "params": "3B", "size": "~1.9 GB",
     "min_ram": 5, "blurb": "Strong small model, follows instructions well."},
    {"name": "qwen2.5-coder:7b", "label": "Qwen 2.5 Coder · 7B", "params": "7B", "size": "~4.7 GB",
     "min_ram": 9, "coder": True,
     "blurb": "Best small coding model — the sweet spot for Vibe Code tasks."},
    {"name": "mistral:7b", "label": "Mistral · 7B", "params": "7B", "size": "~4.1 GB",
     "min_ram": 9, "blurb": "Fast, solid general-purpose model."},
    {"name": "llama3.1:8b", "label": "Llama 3.1 · 8B", "params": "8B", "size": "~4.9 GB",
     "min_ram": 11, "blurb": "Capable general model when you have RAM to spare."},
    {"name": "qwen2.5-coder:14b", "label": "Qwen 2.5 Coder · 14B", "params": "14B", "size": "~9 GB",
     "min_ram": 18, "coder": True, "blurb": "Stronger coding model for bigger servers."},
    {"name": "gpt-oss:20b", "label": "GPT-OSS · 20B", "params": "20B", "size": "~14 GB",
     "min_ram": 26, "blurb": "Large open model — needs a beefy machine or a GPU."},
]


# ---------------------------------------------------------------- detection

def _norm(host: str) -> str:
    host = (host or "").strip().rstrip("/")
    if not host:
        return ""
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    # append the default port when the author gave only a host
    tail = host.split("//", 1)[1]
    if ":" not in tail.split("/", 1)[0]:
        host = f"{host}:{DEFAULT_PORT}"
    return host


def candidates() -> list[str]:
    raw = [os.environ.get("OLLAMA_HOST", ""), config.get_ollama_base(),
           "http://host.docker.internal:11434", "http://172.17.0.1:11434",
           "http://ollama:11434", "http://127.0.0.1:11434"]
    seen, out = set(), []
    for h in (_norm(x) for x in raw):
        if h and h not in seen:
            seen.add(h)
            out.append(h)
    return out


async def _probe(base: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=1.5) as c:
            if (await c.get(base + "/api/version")).status_code == 200:
                return base
    except Exception:
        pass
    return None


async def base_url(force: bool = False) -> str | None:
    """Where Ollama answers, or None. Probes candidates concurrently and caches
    the winner (fastest to respond) for a short while."""
    if not force and _cache["base"] and time.time() - _cache["time"] < _CACHE_TTL:
        return _cache["base"]
    tasks = [asyncio.ensure_future(_probe(b)) for b in candidates()]
    found = None
    try:
        for fut in asyncio.as_completed(tasks):
            res = await fut
            if res:
                found = res
                break
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
    _cache.update(base=found, time=time.time())
    if found and _norm(config.get_ollama_base()) != found:
        config.set_ollama_base(found)   # remember for the sync path (_cfg_for)
    return found


def resolved_base_sync() -> str | None:
    """Best-effort base without probing — used by the synchronous ai._cfg_for.
    Warmed by any prior base_url()/status() call or the persisted setting."""
    return _cache["base"] or (_norm(config.get_ollama_base()) or None)


def openai_base_sync() -> str | None:
    b = resolved_base_sync()
    return (b + "/v1") if b else None


def can_install() -> bool:
    return bool(shutil.which("docker")) and os.path.exists("/var/run/docker.sock")


# --------------------------------------- reach an existing Ollama container

def own_container_name() -> str:
    """This container's id/name (its hostname is the short container id)."""
    try:
        return open("/etc/hostname").read().strip() or os.uname().nodename
    except OSError:
        return os.uname().nodename


async def _run_capture(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return proc.returncode, out.decode("utf-8", "replace")


async def find_ollama_container() -> dict | None:
    """A running Ollama container on this host — even one we can't reach yet
    (e.g. bound to loopback or on a different Docker network)."""
    if not can_install():
        return None
    rc, out = await _run_capture(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"])
    if rc != 0:
        return None
    name = None
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and ("ollama" in parts[1].lower() or parts[0].lower() == "ollama"):
            name = parts[0]
            break
    if not name:
        return None
    rc, nets = await _run_capture(
        ["docker", "inspect", name, "--format",
         "{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}"])
    return {"name": name, "networks": nets.split() if rc == 0 else []}


async def connect_network(network: str) -> None:
    """Join this container to `network` so container DNS (e.g. 'ollama') resolves."""
    rc, out = await _run_capture(
        ["docker", "network", "connect", network, own_container_name()])
    if rc != 0 and "already exists" not in out.lower() and "already in network" not in out.lower():
        raise RuntimeError(out.strip()[:200] or "network connect failed")
    config.set_ollama_network(network)   # reconnect on startup after redeploys


async def connect_existing() -> str:
    existing = await find_ollama_container()
    if not existing:
        raise RuntimeError("No Ollama container found on this server")
    for net in existing["networks"]:
        try:
            await connect_network(net)
        except Exception:
            continue
        if await base_url(force=True):
            return _cache["base"]
    raise RuntimeError(
        f"Found the Ollama container “{existing['name']}” but still can’t reach it. "
        "Make sure it publishes port 11434 (on 0.0.0.0) or shares a Docker network.")


async def reconnect_on_startup() -> None:
    """If we previously joined an Ollama container's network, rejoin it (a
    redeploy recreates this container on its own network only)."""
    net = config.get_ollama_network()
    if not net:
        return
    try:
        if not await base_url():
            await connect_network(net)
            await base_url(force=True)
    except Exception:
        pass


# ------------------------------------------------------------------ queries

async def _version(base: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            return (await c.get(base + "/api/version")).json().get("version", "")
    except Exception:
        return ""


async def tags(base: str | None = None) -> list[dict]:
    base = base or await base_url()
    if not base:
        return []
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            data = (await c.get(base + "/api/tags")).json()
        out = []
        for m in data.get("models", []):
            det = m.get("details") or {}
            out.append({"name": m.get("name", ""), "size": m.get("size", 0),
                        "params": det.get("parameter_size", ""),
                        "quant": det.get("quantization_level", "")})
        return sorted(out, key=lambda m: m["name"])
    except Exception:
        return []


async def available() -> bool:
    return await base_url() is not None


async def model_options() -> list[dict]:
    """For the chat model picker: installed local models as {id,name}."""
    return [{"id": m["name"], "name": m["name"] + (f" · {m['params']}" if m["params"] else "")}
            for m in await tags()]


def _ram_gb() -> float:
    try:
        return round(sysinfo.memory()["total"] / (1024 ** 3), 1)
    except Exception:
        return 0.0


def recommend(installed_names: set[str], ram_gb: float | None = None) -> list[dict]:
    ram = ram_gb if ram_gb is not None else _ram_gb()
    out = []
    for m in CATALOG:
        fits = ram >= m["min_ram"] if ram else True
        out.append({**m, "installed": m["name"] in installed_names, "fits": fits})
    # suggest the strongest coder model that fits and isn't installed yet
    suggestable = [m for m in out if m["fits"] and not m["installed"]]
    suggested = None
    for pref in (True, False):  # prefer coder models
        cand = [m for m in suggestable if bool(m.get("coder")) == pref]
        if cand:
            suggested = max(cand, key=lambda m: m["min_ram"])["name"]
            break
    for m in out:
        m["suggested"] = (m["name"] == suggested)
    return out


async def status() -> dict:
    if config.DEMO:
        # never probe sibling containers from the public demo — canned data only
        installed = [
            {"name": "llama3.2:3b", "size": 2019393189, "params": "3.2B", "quant": "Q4_K_M"},
            {"name": "qwen2.5:7b", "size": 4683073184, "params": "7.6B", "quant": "Q4_K_M"},
        ]
        return {"running": True, "base": "http://ollama:11434 (demo)",
                "version": "0.3.0 (demo)", "can_install": False, "existing": None,
                "ram_gb": 8.0, "cpu_count": 4, "installed": installed,
                "recommended": recommend({m["name"] for m in installed})}
    base = await base_url()
    installed = await tags(base) if base else []
    # an Ollama container that exists but we can't reach → offer "Connect"
    existing = None if base else await find_ollama_container()
    return {
        "running": base is not None,
        "base": base,
        "version": await _version(base) if base else "",
        "can_install": can_install(),
        "existing": existing,
        "ram_gb": _ram_gb(),
        "cpu_count": os.cpu_count(),
        "installed": installed,
        "recommended": recommend({m["name"] for m in installed}),
    }


# --------------------------------------------------------------- mutations

def start_install_job() -> jobs.Job:
    return jobs.start("Install local AI (Ollama)", "localai", _install_work)


async def _install_work(job: jobs.Job) -> None:
    if not can_install():
        raise RuntimeError("Docker is not available to install Ollama")
    if await base_url(force=True):
        job.finish(True, "✓ Ollama is already running")
        return
    # never clobber an Ollama the user already runs — connect to it instead
    existing = await find_ollama_container()
    if existing:
        async with job.step(f"Found your Ollama container “{existing['name']}” — "
                            "connecting Helmsman to it…"):
            for net in existing["networks"]:
                try:
                    await connect_network(net)
                except Exception:
                    continue
                if await base_url(force=True):
                    job.finish(True, "✓ Connected to your existing Ollama")
                    return
        raise RuntimeError(
            f"Ollama container “{existing['name']}” is running but unreachable. Publish its "
            "port on 0.0.0.0:11434, or put it on a shared Docker network, then try again.")
    async with job.step("Pulling ollama/ollama image (first run can take a few minutes)…"):
        await _run(["docker", "pull", "ollama/ollama"], job)
    async with job.step("Starting Ollama container…"):
        await _run(["docker", "run", "-d", "--name", "ollama", "--restart", "unless-stopped",
                    "-v", "ollama:/root/.ollama", "-p", f"{DEFAULT_PORT}:{DEFAULT_PORT}",
                    "ollama/ollama"], job)
    async with job.step("Waiting for the Ollama API…"):
        for _ in range(30):
            if await base_url(force=True):
                job.finish(True, "✓ Ollama is running. Now download a model below.")
                return
            await asyncio.sleep(2)
    raise RuntimeError("Ollama started but its API never came up on :11434")


async def _rm_quiet(name: str) -> None:
    try:
        p = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", name,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await p.communicate()
    except Exception:
        pass


async def _run(cmd: list[str], job: jobs.Job) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(out.decode("utf-8", "replace")[-600:] or f"{cmd[0]} failed")


def start_pull_job(model: str) -> jobs.Job:
    return jobs.start(f"Download model · {model}", "localai",
                      lambda j: _pull_work(j, model))


async def _pull_work(job: jobs.Job, model: str) -> None:
    base = await base_url()
    if not base:
        raise RuntimeError("Ollama is not reachable — set it up first")
    job.log(f"Downloading {model} …")
    async with httpx.AsyncClient(timeout=None) as c:
        async with c.stream("POST", base + "/api/pull",
                            json={"name": model, "stream": True}) as r:
            if r.status_code >= 400:
                raise RuntimeError((await r.aread()).decode()[:300])
            import json as _json
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                try:
                    ev = _json.loads(line)
                except ValueError:
                    continue
                if ev.get("error"):
                    raise RuntimeError(ev["error"])
                st = ev.get("status", "")
                if ev.get("total"):
                    pct = int(ev.get("completed", 0) * 100 / ev["total"])
                    job.log(f"\r{st} — {pct}%")
                elif st:
                    job.log(st)
    _cache["time"] = 0  # force a fresh tags() next time
    job.finish(True, f"✓ {model} is ready to use")


async def delete_model(model: str) -> bool:
    base = await base_url()
    if not base:
        raise RuntimeError("Ollama is not reachable")
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.request("DELETE", base + "/api/delete", json={"name": model})
    _cache["time"] = 0
    return r.status_code < 400
