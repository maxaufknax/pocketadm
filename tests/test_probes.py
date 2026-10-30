"""The public sites, looked at from outside: one failed visit is the internet,
two in a row are an outage, the all-clear only follows an outage the user
heard about, and a certificate counts by what the site actually serves."""
import asyncio

import pytest

from server import probes

T = 1_800_000_000.0


@pytest.fixture
def world(monkeypatch):
    """Two sites whose answers and certificates the test decides."""
    w = {"up": {"studgo.de": True, "pocketadm.com": True}, "cert_days": 60, "certs_asked": 0}

    async def sites():
        return list(w["up"])

    async def check(client, host):
        ok = w["up"][host]
        return {"ok": ok, "status": 200 if ok else 0, "ms": 5, "via": "IPv4",
                "error": "" if ok else "no answer within 10 seconds"}

    async def cert(host):
        w["certs_asked"] += 1
        return {"expires": T + w["cert_days"] * 86400 + 60, "names": [host]}
    monkeypatch.setattr(probes, "sites", sites)
    monkeypatch.setattr(probes, "check_site", check)
    monkeypatch.setattr(probes, "served_cert", cert)
    return w


def look(state, at):
    return asyncio.run(probes.tick(state, at))


def test_hosts_from_the_settings_text():
    assert probes.parse_hosts("studgo.de, https://pocketadm.com/x  homelab.example:443 bad nope.") \
        == ["studgo.de", "pocketadm.com", "homelab.example"]


def test_two_failures_make_an_outage_and_an_answer_ends_it(world):
    state = {}
    assert look(state, T) == {"events": [], "back": []}
    assert look(state, T + 60) == {"events": [], "back": []}          # not due yet
    world["up"]["studgo.de"] = False
    assert look(state, T + 300)["events"] == []                       # one timeout
    down = look(state, T + 600)["events"]
    assert [(e["kind"], e["target"]) for e in down] == [("site.down", "studgo.de")]
    assert state["sites"]["studgo.de"]["down_since"] == T + 300
    world["up"]["studgo.de"] = True
    out = look(state, T + 900)
    assert [e["kind"] for e in out["events"]] == ["site.up"]
    assert out["back"] == []                                          # nobody had been told


def test_the_all_clear_follows_an_outage_the_user_was_told_about(world):
    state = {}
    world["up"]["pocketadm.com"] = False
    look(state, T)
    look(state, T + 300)
    state["sites"]["pocketadm.com"]["announced"] = True               # the watch wrote about it
    world["up"]["pocketadm.com"] = True
    assert look(state, T + 1500)["back"] == [{"host": "pocketadm.com", "since": T, "lasted": 1500.0}]
    assert "announced" not in state["sites"]["pocketadm.com"]


def test_a_served_certificate_that_runs_out_warns_once_a_day(world):
    state = {}
    world["cert_days"] = 10
    first = look(state, T)["events"]
    assert {(e["kind"], e["severity"]) for e in first} == {("cert.expiring", "warn")}
    assert state["sites"]["studgo.de"]["cert_days"] == 10
    asked = world["certs_asked"]
    assert look(state, T + 300)["events"] == []                       # certificates hourly
    assert world["certs_asked"] == asked
    assert look(state, T + 3600)["events"] == []                      # and warned once a day
    world["cert_days"] = 2
    assert look(state, T + 90000)["events"][0]["severity"] == "crit"


def test_what_the_watch_is_told(world):
    state = {}
    world["up"]["studgo.de"] = False
    look(state, T)
    look(state, T + 300)
    lines = probes.context_lines(state, lambda t: "12:22")
    assert "over IPv4" in lines[0]
    assert "studgo.de DOWN since 12:22 (no answer within 10 seconds)" in lines[0]
    assert "pocketadm.com ok (HTTP 200, 5 ms)" in lines[0]
    assert "pocketadm.com: 60 days left" in lines[1] and "noise" in lines[1]


def test_reasons_read_like_a_visitor_would_say_them():
    assert probes._reason(Exception("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                                    "certificate has expired")) == "the certificate has expired"
    assert probes._reason(Exception("Hostname mismatch, certificate is not valid for "
                                    "'pocketadm.com'")) == "the certificate is for a different name"
    assert probes._reason(TimeoutError("timed out")) == "no answer within 10 seconds"
    assert probes._reason(OSError("[Errno -2] Name or service not known")) == "the name does not resolve"
