"""Command-line helpers for the server, run inside the container.

    docker exec -it helmsman python -m server.cli pair [--url URL]
    pocketadm <topic> [--days N] [--search TEXT]      (inside the container)
    pocketadm remember "fact" [--topic T] | forget ID | notes | map | host CMD

The `pocketadm` command (a wrapper the image installs) is how the coding
agents — Claude Code, Codex, Vibe — reach what the built-in assistant has as
tools: PocketADM's records (updates and how they ended, activity, metrics,
health …, see records.py), its notes about the server (memory.py), the
server map, and running a command on the host itself (`host`), because the
agents' own shell runs inside PocketADM's container.

`pair` prints a QR code (and the link it encodes) that the PocketADM app scans
to sign in: the server's address, a one-time pairing code that expires in ten
minutes, and — on the self-signed HTTPS port — the fingerprint of this
server's TLS key, so the app trusts exactly this server and nothing that
merely claims its address. It is what the installer shows at the end, and
what to run when you want to add another phone later.

The pairing code has to come from the *running* server (codes live in its
memory), so this asks it over the loopback port with a short-lived token it
signs itself — anyone who can `docker exec` into this container is root on
the host already.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

from . import auth


def pairing_link(base_url: str, code: str, fingerprint: str = "") -> str:
    """The one string the QR carries: `<server>/?pair=CODE&fp=FINGERPRINT`.

    Every client reads this form — the iOS app, the web app's scanner, and
    the 1.0.x App Store build — and opened in a browser it signs that browser
    in through the web app's deep link. `fp` only makes sense on https."""
    query = {"pair": code}
    if fingerprint and base_url.lower().startswith("https://"):
        query["fp"] = fingerprint
    return base_url.rstrip("/") + "/?" + urlencode(query)


def _local(path: str, token: str) -> dict:
    port = os.environ.get("HELMSMAN_HTTP_PORT", "8080")
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST",
                                 headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _print_qr(text: str) -> None:
    try:
        import segno
        segno.make(text, error="m").terminal(out=sys.stdout, compact=True)
    except Exception:          # no QR library: the link alone still works
        print("(QR rendering unavailable — use the link below)")


def cmd_pair(args: argparse.Namespace) -> int:
    url = (args.url or os.environ.get("POCKETADM_PUBLIC_URL", "")).strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        print("No public address is set. Pass the address your phone will use, e.g.\n"
              "  python -m server.cli pair --url https://203.0.113.10:8443", file=sys.stderr)
        return 2
    try:
        data = _local("/api/pair/new", auth.issue_token(ttl=120))
    except (urllib.error.URLError, OSError) as e:
        print(f"The server is not answering on its local port yet ({e}). "
              "Wait a few seconds and run this again.", file=sys.stderr)
        return 1
    link = pairing_link(url, data["code"], data.get("tls_fingerprint", ""))
    if not args.link_only:
        print()
        _print_qr(link)
        print("Scan this with the PocketADM app (Connect → Scan pairing code).")
        print(f"It signs one phone in, works once and expires in {data.get('ttl', 600) // 60} minutes.")
        print()
    # machine-readable: the SSH installer in the app picks this line up
    print("PAIRING_LINK: " + link, flush=True)
    return 0


def cmd_records(args: argparse.Namespace) -> int:
    import asyncio
    from . import records
    print(asyncio.run(records.query(args.command, days=args.days or 0, search=args.search or "")))
    return 0


def cmd_notes(args: argparse.Namespace) -> int:
    from . import memory
    print(memory.prompt_block() or "No notes yet.")
    return 0


def cmd_remember(args: argparse.Namespace) -> int:
    from . import memory
    try:
        note, what = memory.add(" ".join(args.fact), topic=args.topic or "",
                                subject=args.subject or "", replaces=args.replaces or "")
    except ValueError as e:
        print(f"Not saved: {e}", file=sys.stderr)
        return 1
    print({"added": "Saved as note", "updated": "Already known, refreshed note",
           "replaced": "Corrected note"}[what], note["id"])
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    from . import memory
    if memory.forget(args.id):
        print("Note removed.")
        return 0
    print("No note with that id.", file=sys.stderr)
    return 1


def cmd_map(args: argparse.Namespace) -> int:
    import asyncio
    from . import servermap
    print(asyncio.run(servermap.get(force=True)))
    return 0


def cmd_host(args: argparse.Namespace) -> int:
    import asyncio
    from . import hostrun
    command = " ".join(args.cmd).strip()
    if not command:
        print("usage: pocketadm host <command>", file=sys.stderr)
        return 2
    if not hostrun.available():
        print("Host access is not available in this container.", file=sys.stderr)
        return 1
    rc, out = asyncio.run(hostrun.run(command, timeout=args.timeout))
    sys.stdout.write(out)
    return rc


def main(argv: list[str] | None = None) -> int:
    from . import memory, records
    parser = argparse.ArgumentParser(prog="pocketadm")
    sub = parser.add_subparsers(dest="command", required=True)
    pair = sub.add_parser("pair", help="show a QR code that signs a phone in")
    pair.add_argument("--url", help="address the phone uses (default: POCKETADM_PUBLIC_URL)")
    pair.add_argument("--link-only", action="store_true", help="print only the link")
    pair.set_defaults(func=cmd_pair)
    for topic, about in records.TOPICS.items():
        p = sub.add_parser(topic, help=about)
        p.add_argument("--days", type=float, default=0)
        p.add_argument("--search", default="")
        p.set_defaults(func=cmd_records)
    sub.add_parser("notes", help="the assistant's notes about this server").set_defaults(func=cmd_notes)
    sub.add_parser("map", help="the server map: stacks, domains, units, drives").set_defaults(func=cmd_map)
    rem = sub.add_parser("remember", help="save a fact to the assistant's notes")
    rem.add_argument("fact", nargs="+")
    rem.add_argument("--topic", choices=memory.TOPIC_IDS)
    rem.add_argument("--subject", default="")
    rem.add_argument("--replaces", default="")
    rem.set_defaults(func=cmd_remember)
    fg = sub.add_parser("forget", help="remove a note by its id")
    fg.add_argument("id")
    fg.set_defaults(func=cmd_forget)
    host = sub.add_parser("host", help="run a command on the host (not in this container)")
    host.add_argument("--timeout", type=int, default=120)
    host.add_argument("cmd", nargs=argparse.REMAINDER)
    host.set_defaults(func=cmd_host)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
