"""Update detection: docker images (registry digest comparison, no pull needed)
and host apt packages (when accessible). Optional AI explanations."""
import asyncio
import json
import re
import time

import httpx

from . import ai, dockerapi

_cache: dict = {"time": 0, "result": None}
CACHE_TTL = 1800

ACCEPT = ("application/vnd.docker.distribution.manifest.list.v2+json, "
          "application/vnd.oci.image.index.v1+json, "
          "application/vnd.docker.distribution.manifest.v2+json, "
          "application/vnd.oci.image.manifest.v1+json")


def parse_image_ref(ref: str) -> tuple[str, str, str]:
    """'grafana/grafana:10.2' -> (registry, repository, tag)."""
    ref = ref.split("@")[0]
    tag = "latest"
    if ":" in ref.rsplit("/", 1)[-1]:
        ref, tag = ref.rsplit(":", 1)
    parts = ref.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry, repo = parts[0], "/".join(parts[1:])
    else:
        registry, repo = "registry-1.docker.io", ref if "/" in ref else f"library/{ref}"
    return registry, repo, tag


async def remote_digest(client: httpx.AsyncClient, registry: str, repo: str, tag: str) -> str | None:
    url = f"https://{registry}/v2/{repo}/manifests/{tag}"
    headers = {"Accept": ACCEPT}
    r = await client.head(url, headers=headers)
    if r.status_code == 401:
        # Token dance (works for docker hub, ghcr, lscr, quay …)
        www = r.headers.get("www-authenticate", "")
        m = dict(re.findall(r'(\w+)="([^"]*)"', www))
        if "realm" not in m:
            return None
        tr = await client.get(m["realm"], params={k: v for k, v in
                                                  [("service", m.get("service", "")),
                                                   ("scope", m.get("scope", f"repository:{repo}:pull"))] if v})
        if tr.status_code != 200:
            return None
        headers["Authorization"] = f"Bearer {tr.json().get('token', tr.json().get('access_token', ''))}"
        r = await client.head(url, headers=headers)
    if r.status_code != 200:
        return None
    return r.headers.get("docker-content-digest")


async def check_docker_updates(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and _cache["result"] is not None and now - _cache["time"] < CACHE_TTL:
        return _cache["result"]

    containers = await dockerapi.list_containers(all_=False)
    images: dict[str, list[str]] = {}
    for c in containers:
        if not c["image"].startswith("sha256:"):
            images.setdefault(c["image"], []).append(c["name"])

    results: list[dict] = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        async def check_one(image: str, used_by: list[str]) -> None:
            entry = {"image": image, "used_by": used_by, "update_available": False,
                     "current_digest": "", "remote_digest": "", "error": ""}
            try:
                info = await dockerapi.inspect_image(image)
                if not info:
                    entry["error"] = "image not found locally"
                    results.append(entry)
                    return
                local_digests = {d.split("@")[1] for d in info.get("RepoDigests", []) if "@" in d}
                if not local_digests:
                    entry["error"] = "locally built image"
                    results.append(entry)
                    return
                registry, repo, tag = parse_image_ref(image)
                remote = await remote_digest(client, registry, repo, tag)
                if remote is None:
                    entry["error"] = "registry check failed"
                else:
                    entry["current_digest"] = sorted(local_digests)[0][:19]
                    entry["remote_digest"] = remote[:19]
                    entry["update_available"] = remote not in local_digests
            except Exception as e:
                entry["error"] = str(e)[:200]
            results.append(entry)

        await asyncio.gather(*(check_one(img, names) for img, names in images.items()))

    results.sort(key=lambda x: (not x["update_available"], x["image"]))
    _cache.update(time=now, result=results)
    return results


async def check_apt_updates() -> dict:
    """Host package updates — only meaningful when running natively on the host."""
    try:
        proc = await asyncio.create_subprocess_shell(
            "apt list --upgradable 2>/dev/null | tail -n +2",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 30)
        pkgs = []
        for line in out.decode().splitlines():
            m = re.match(r"([^/]+)/\S+\s+(\S+)\s+\S+\s+\[upgradable from:\s*([^\]]+)\]", line)
            if m:
                pkgs.append({"package": m.group(1), "new": m.group(2), "current": m.group(3)})
        return {"available": True, "packages": pkgs}
    except Exception:
        return {"available": False, "packages": []}


async def pull_image(image: str) -> str:
    """Pull the newer image. (Recreating the container is up to compose/user.)"""
    proc = await asyncio.create_subprocess_exec(
        "docker", "pull", image,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await asyncio.wait_for(proc.communicate(), 600)
    text = out.decode("utf-8", "replace")[-3000:]
    if proc.returncode != 0:
        raise RuntimeError(text)
    _cache["time"] = 0  # invalidate
    return text


EXPLAIN_SYSTEM = ("You explain software updates to self-hosting users who are not sysadmins. "
                  "Be concise (max ~150 words), friendly, and concrete: what is this software, "
                  "what does an update typically bring, is there any risk, and what should the "
                  "user do. Answer in the user's language if specified.")


async def explain_update(subject: str, kind: str, lang: str = "") -> str:
    changelog = ""
    if kind == "docker":
        _, repo, tag = parse_image_ref(subject)
        changelog = await _try_github_release_notes(repo)
    prompt = (f"A new update is available for {'docker image' if kind == 'docker' else 'package'} "
              f"'{subject}'.\n")
    if changelog:
        prompt += f"\nRecent release notes:\n{changelog[:4000]}\n"
    if lang:
        prompt += f"\nAnswer in language: {lang}"
    prompt += "\nExplain this update to the user."
    return await ai.one_shot(prompt, EXPLAIN_SYSTEM)


async def _try_github_release_notes(repo: str) -> str:
    """Many images map 1:1 to a github repo (grafana/grafana …) — best effort."""
    if repo.startswith("library/") or repo.count("/") != 1:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json"})
            if r.status_code == 200:
                d = r.json()
                return f"{d.get('name', d.get('tag_name', ''))}\n{d.get('body', '')}"
    except Exception:
        pass
    return ""
