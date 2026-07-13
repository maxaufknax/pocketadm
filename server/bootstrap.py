"""Provision PocketADM onto another machine over SSH.

An already-running PocketADM can act as a "bootstrapper": give it SSH access to
a fresh server and it runs the one-line installer there, streaming the output
live (as a job) and parsing out the resulting URL + generated admin password so
the new server can be added to the multi-server list with a single tap.

Password auth uses paramiko; the SSH password doubles as the sudo password when
the login user is not root. Nothing is persisted — credentials live only for the
duration of the install and never touch disk or the audit detail.
"""
from __future__ import annotations

import os
import re
import shlex

from . import jobs

try:
    import paramiko  # type: ignore
    HAVE_PARAMIKO = True
except Exception:  # noqa: BLE001
    HAVE_PARAMIKO = False

# where the installer lives; overridable so private forks work too
INSTALLER_URL = os.environ.get(
    "POCKETADM_INSTALLER_URL",
    "https://raw.githubusercontent.com/maxaufknax/helmsman/main/install.sh")

_PW_RE = re.compile(r"admin password:\s*(\S+)", re.I)
_URL_RE = re.compile(r"https?://[^\s\"']+")


def available() -> bool:
    return HAVE_PARAMIKO


def _remote_command(port: int, as_root: bool) -> str:
    inner = f"curl -fsSL {shlex.quote(INSTALLER_URL)} | HELMSMAN_PORT={int(port)} bash"
    if as_root:
        return inner
    # feed the login password to sudo on stdin (-S), suppress the prompt (-p '')
    return f"sudo -S -p '' bash -lc {shlex.quote(inner)}"


def start_job(host: str, user: str, *, password: str = "", key: str = "",
              port: int = 22, install_port: int = 8090) -> jobs.Job:
    """Kick off an SSH install as a followable job. Returns the Job immediately.
    On success the job's final lines carry `RESULT_URL=` / `RESULT_PW=` markers
    the client uses to pre-fill the 'add server' step."""

    async def work(job: jobs.Job) -> None:
        if not HAVE_PARAMIKO:
            job.finish(False, "✗ SSH support unavailable (paramiko not installed on this server)")
            return
        import asyncio

        def run() -> tuple[bool, str, str]:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            connect_kw: dict = {"hostname": host, "port": int(port), "username": user,
                                "timeout": 20, "banner_timeout": 20, "auth_timeout": 20}
            pkey = None
            if key.strip():
                for loader in (paramiko.Ed25519Key, paramiko.RSAKey,
                               paramiko.ECDSAKey):
                    try:
                        import io
                        pkey = loader.from_private_key(io.StringIO(key))
                        break
                    except Exception:  # noqa: BLE001
                        continue
            if pkey is not None:
                connect_kw["pkey"] = pkey
            elif password:
                connect_kw["password"] = password
                connect_kw["look_for_keys"] = False
            client.connect(**connect_kw)

            as_root = (user == "root")
            chan = client.get_transport().open_session()
            chan.get_pty()
            chan.exec_command(_remote_command(install_port, as_root))
            if not as_root and password:
                try:
                    chan.send(password + "\n")   # sudo -S reads it from stdin
                except Exception:  # noqa: BLE001
                    pass

            found_url, found_pw = "", ""
            buf = b""
            while True:
                if chan.recv_ready():
                    buf += chan.recv(4096)
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        text = line.decode("utf-8", "replace").rstrip("\r")
                        if not text.strip():
                            continue
                        job.log(text)
                        m = _PW_RE.search(text)
                        if m:
                            found_pw = m.group(1)
                        if "Open:" in text or "Password:" in text:
                            um = _URL_RE.search(text)
                            if um:
                                found_url = um.group(0)
                            pm = re.search(r"Password:\s*(\S+)", text)
                            if pm and not found_pw:
                                found_pw = pm.group(1)
                elif chan.exit_status_ready():
                    break
                else:
                    import time
                    time.sleep(0.15)
            if buf.strip():
                job.log(buf.decode("utf-8", "replace"))
            rc = chan.recv_exit_status()
            client.close()
            return rc == 0, found_url, found_pw

        try:
            ok, url, pw = await asyncio.to_thread(run)
        except Exception as e:  # noqa: BLE001
            job.finish(False, f"✗ SSH failed: {type(e).__name__}: {e}")
            return
        if not ok:
            job.finish(False, "✗ installer exited with an error — check the log above")
            return
        # fall back to host:port if the installer couldn't print a public URL
        if not url:
            url = f"http://{host}:{int(install_port)}"
        job.log(f"RESULT_URL={url}")
        if pw:
            job.log(f"RESULT_PW={pw}")
        job.finish(True, "✓ PocketADM installed — add it as a server below")

    return jobs.start(f"Install PocketADM on {host}", "bootstrap", work)
