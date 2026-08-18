"""Catalog templates must not publish admin UIs on every interface.

A one-click install is the moment PocketADM decides, on the user's behalf,
who can reach the thing it just started. Docker's ``"8080:80"`` binds
0.0.0.0 — and because Docker's DNAT lands in FORWARD, a host firewall
(ufw/firewalld) does *not* filter it. So a template that ships a bare
``PORT:CONTAINER`` mapping silently puts a fresh, default-credentialed
Vaultwarden / Portainer / filebrowser on the LAN, and on the public internet
wherever the host has a routable address.

The fix is a policy, and this test pins it:

  * the **admin/web UI** port (``{{PORT}}``) binds ``127.0.0.1`` — reachable
    only through the reverse proxy, which is where TLS and auth live;
  * a **protocol** port that other machines must speak to directly (DNS 53,
    WireGuard 51820, Syncthing 22000, BitTorrent 6881, Git-over-SSH) stays
    public, because loopback would simply break it.

The dangerous direction is a new template landing with a bare mapping, so the
assertion is on *every* app rather than a curated list.
"""
import json
import pathlib
import re

import pytest

CATALOG = json.loads(
    (pathlib.Path(__file__).parent.parent / "server" / "catalog.json").read_text()
)
APPS = CATALOG["apps"]

# Ports whose whole job is to be reachable from another machine.
PUBLIC_BY_DESIGN = {"53", "51820", "22000", "6881", "{{SSH_PORT}}"}

_PORT_LINE = re.compile(r'^\s*-\s*"([^"]+)"$')


def _port_specs(app):
    """Every ``ports:`` entry in the template, as written."""
    specs = []
    in_ports = False
    for line in app["compose"].split("\n"):
        if re.match(r"^\s*ports:\s*$", line):
            in_ports = True
            continue
        m = _PORT_LINE.match(line)
        if in_ports and m and re.search(r":\d+(/\w+)?$", m.group(1)):
            specs.append(m.group(1))
        elif in_ports and not m:
            in_ports = False
    return specs


def test_catalog_is_non_empty():
    assert len(APPS) > 10, "catalog looks truncated"


@pytest.mark.parametrize("app", APPS, ids=[a["id"] for a in APPS])
def test_admin_ports_bind_loopback(app):
    for spec in _port_specs(app):
        host = spec.split(":")[0]
        if host in PUBLIC_BY_DESIGN:
            continue
        assert spec.startswith("127.0.0.1:"), (
            f"{app['id']}: port mapping {spec!r} publishes on 0.0.0.0. "
            f"Bind it to 127.0.0.1 and reach it through the reverse proxy, "
            f"or add its port to PUBLIC_BY_DESIGN if it is a protocol port."
        )


@pytest.mark.parametrize("app", APPS, ids=[a["id"] for a in APPS])
def test_protocol_ports_stay_reachable(app):
    """Loopback-binding a protocol port breaks the service — guard the reverse."""
    for spec in _port_specs(app):
        parts = spec.split(":")
        if parts[0] == "127.0.0.1" and parts[1] in PUBLIC_BY_DESIGN - {"{{SSH_PORT}}"}:
            pytest.fail(f"{app['id']}: {spec!r} puts a protocol port on loopback")
