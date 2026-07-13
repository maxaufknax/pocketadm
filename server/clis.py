"""Coding-agent CLIs in the built-in terminal (Claude Code, OpenAI Codex).

They are installed into DATA_DIR/home/.local/bin — the persistent HOME the
local terminal shell uses (see terminal.PERSIST_HOME) — so the binaries AND
their login sessions survive container updates. This lets users drive an
existing Claude Pro/Max or ChatGPT subscription from PocketADM's terminal:
sign in once with `claude` / `codex`, no API key needed.
"""
import asyncio
import os
import platform
import stat
import tarfile
import tempfile
from pathlib import Path

import httpx

from . import jobs, terminal

BIN_DIR = terminal.PERSIST_HOME / ".local" / "bin"

CLIS = {
    "claude": {
        "id": "claude", "name": "Claude Code", "vendor": "Anthropic",
        "bin": "claude", "launch": "claude",
        "subscription": "Claude Pro / Max (or an Anthropic API key)",
        "tagline": "Anthropic's terminal coding agent",
        "site": "https://claude.com/claude-code",
    },
    "codex": {
        "id": "codex", "name": "Codex CLI", "vendor": "OpenAI",
        "bin": "codex", "launch": "codex",
        "subscription": "ChatGPT Plus / Pro (or an OpenAI API key)",
        "tagline": "OpenAI's terminal coding agent",
        "site": "https://github.com/openai/codex",
    },
}


def _env() -> dict:
    env = dict(os.environ)
    env["HOME"] = str(terminal.PERSIST_HOME)
    env["PATH"] = f"{BIN_DIR}:" + env.get("PATH", "")
    return env


async def _version_of(binary: Path) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            str(binary), "--version", env=_env(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), 10)
        lines = [l.strip() for l in out.decode("utf-8", "replace").splitlines()
                 if l.strip() and not l.upper().startswith("WARNING")]
        return lines[-1][:60] if lines else ""
    except Exception:
        return ""


async def status() -> list[dict]:
    out = []
    for c in CLIS.values():
        path = BIN_DIR / c["bin"]
        installed = path.is_file()
        entry = {**c, "installed": installed, "version": ""}
        if installed:
            entry["version"] = await _version_of(path)
        out.append(entry)
    return out


def start_install_job(tool: str) -> jobs.Job:
    if tool not in CLIS:
        raise ValueError("unknown tool")
    meta = CLIS[tool]

    async def work(job: jobs.Job) -> None:
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        if tool == "claude":
            await _install_claude(job)
        else:
            await _install_codex(job)
        version = await _version_of(BIN_DIR / meta["bin"])
        job.log(f"✓ {meta['name']} ready ({version or 'installed'})")
        job.log(f"Open the Terminal tab and type `{meta['launch']}` — the first run "
                f"walks you through signing in with your {meta['subscription']}.")
        job.finish(True)

    return jobs.start(f"Install {meta['name']}", "install", work)


async def _install_claude(job: jobs.Job) -> None:
    """Official native installer — installs to $HOME/.local/bin (persistent)."""
    job.log("⬇ Running the official Claude Code installer …")
    proc = await asyncio.create_subprocess_shell(
        "curl -fsSL https://claude.ai/install.sh | bash",
        env=_env(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    assert proc.stdout
    async for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").rstrip()
        if line:
            job.log("  " + line[:300])
    await proc.wait()
    if proc.returncode != 0 or not (BIN_DIR / "claude").is_file():
        raise RuntimeError("installer failed — see log above")


async def _install_codex(job: jobs.Job) -> None:
    """Standalone binary from the latest GitHub release (no Node.js needed)."""
    machine = platform.machine().lower()
    triple = {"x86_64": "x86_64-unknown-linux-musl", "amd64": "x86_64-unknown-linux-musl",
              "aarch64": "aarch64-unknown-linux-musl", "arm64": "aarch64-unknown-linux-musl",
              }.get(machine)
    if not triple:
        raise RuntimeError(f"unsupported architecture: {machine}")
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        job.log("⬇ Looking up the latest Codex CLI release …")
        r = await client.get("https://api.github.com/repos/openai/codex/releases/latest",
                             headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        rel = r.json()
        wanted = f"codex-{triple}.tar.gz"
        asset = next((a for a in rel.get("assets", []) if a.get("name") == wanted), None)
        if not asset:
            raise RuntimeError(f"no linux build ({triple}) in release {rel.get('tag_name')}")
        job.log(f"⬇ Downloading {asset['name']} ({rel.get('tag_name')}) …")
        data = (await client.get(asset["browser_download_url"])).content
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / asset["name"]
        archive.write_bytes(data)
        with tarfile.open(archive) as tar:
            tar.extractall(tmp, filter="data")
        binary = next((p for p in Path(tmp).rglob("codex*") if p.is_file()
                       and not p.name.endswith(".tar.gz")), None)
        if not binary:
            raise RuntimeError("binary not found in the release archive")
        target = BIN_DIR / "codex"
        target.write_bytes(binary.read_bytes())
        target.chmod(target.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    job.log("✓ Codex CLI installed")
