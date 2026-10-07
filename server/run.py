"""Process entry point: the app over plain HTTP and, unless switched off, HTTPS.

  * :8080  HTTP  — for a reverse proxy on the same host (Caddy, NPM, Traefik)
    and the loopback port the installer binds by default.
  * :8443  HTTPS — the direct door for phones when there is no domain: a
    self-signed certificate whose key fingerprint travels in the pairing QR
    (see tls.py).

Both listeners serve the same app object in one process, so sessions, the
agent and the job queue are shared. uvicorn installs signal handlers per
server and only the last one installed would hear SIGTERM, so this runner owns
signal handling and stops both.

    python -m server.run          (the container's CMD)
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal

import uvicorn

from . import auth, tls

log = logging.getLogger("pocketadm")


class _Server(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):     # the runner below handles SIGINT/SIGTERM
        yield


async def _renew_daily(https: uvicorn.Config) -> None:
    """Re-issue the certificate (same key, so pins keep working) before it
    expires, and hand it to the running listener without a restart."""
    while True:
        await asyncio.sleep(24 * 3600)
        try:
            if tls._cert_needs_renewal():
                paths = tls.ensure()
                if paths and https.ssl is not None:
                    https.ssl.load_cert_chain(str(paths[0]), str(paths[1]))
                    log.info("TLS certificate renewed")
        except Exception as e:     # never take the listener down over this
            log.warning("TLS renewal failed: %s", e)


async def main() -> None:
    from .main import app

    http_port = int(os.environ.get("HELMSMAN_HTTP_PORT", "8080"))
    servers = [_Server(uvicorn.Config(app, host="0.0.0.0", port=http_port))]
    https = None
    try:
        paths = tls.ensure()
    except Exception as e:
        paths = None
        print(f"PocketADM: HTTPS listener disabled — could not set up TLS ({e})", flush=True)
    if paths:
        # lifespan off: startup/shutdown hooks run once, from the HTTP server
        https = uvicorn.Config(app, host="0.0.0.0", port=tls.PORT, lifespan="off",
                               ssl_certfile=str(paths[0]), ssl_keyfile=str(paths[1]))
        servers.append(_Server(https))
    # uvicorn.Config sets up logging; mask credentials again on top of it
    auth.install_log_redaction()

    loop = asyncio.get_running_loop()

    def stop() -> None:
        for s in servers:
            if s.should_exit:
                s.force_exit = True
            s.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)
    tasks = [asyncio.ensure_future(s.serve()) for s in servers]
    if https is not None:
        renew = asyncio.ensure_future(_renew_daily(https))
    await asyncio.gather(*tasks)
    if https is not None:
        renew.cancel()


if __name__ == "__main__":
    asyncio.run(main())
