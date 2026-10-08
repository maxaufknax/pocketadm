"""The push relay on pocketadm.com: phones register a token and get a
capability, servers send with it, Apple's answers are honoured, nothing is kept
that does not have to be."""
import asyncio
import base64
import importlib.util
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

RELAY = pathlib.Path(__file__).resolve().parents[1] / "push-relay" / "relay.py"
TOKEN = "ab" * 32


@pytest.fixture
def relay(tmp_path, monkeypatch):
    monkeypatch.setenv("RELAY_DB", str(tmp_path / "relay.db"))
    monkeypatch.setenv("RELAY_BUNDLES", "de.maxaufknax.pocketadm")
    monkeypatch.setenv("APNS_KEY_FILE", str(tmp_path / "key.p8"))
    monkeypatch.setenv("APNS_KEY_ID", "KEY1234567")
    monkeypatch.setenv("APNS_TEAM_ID", "TEAM123456")
    monkeypatch.setenv("RELAY_PER_HOUR", "3")
    spec = importlib.util.spec_from_file_location("push_relay_under_test", RELAY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, TestClient(mod.app), tmp_path


def _key(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    (tmp_path / "key.p8").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return key


def test_register_is_stable_and_validated(relay):
    mod, client, _ = relay
    a = client.post("/v1/register", json={"token": TOKEN, "bundle": "de.maxaufknax.pocketadm"}).json()
    b = client.post("/v1/register", json={"token": TOKEN.upper(), "bundle": "de.maxaufknax.pocketadm"}).json()
    assert a["relay_id"] == b["relay_id"] and len(a["relay_id"]) >= 43
    assert client.post("/v1/register", json={"token": TOKEN, "bundle": "com.evil.app"}).status_code == 400
    assert client.post("/v1/register", json={"token": "nothex", "bundle": "de.maxaufknax.pocketadm"}).status_code == 400


def test_without_a_key_sends_say_so(relay):
    mod, client, _ = relay
    rid = client.post("/v1/register", json={"token": TOKEN, "bundle": "de.maxaufknax.pocketadm"}).json()["relay_id"]
    r = client.post("/v1/send", json={"relay_id": rid, "title": "t", "body": "b"})
    assert r.status_code == 503 and "no Apple key" in r.json()["detail"]
    assert client.post("/v1/send", json={"relay_id": "x" * 43}).status_code == 410
    assert client.get("/v1/health").json() == {"ok": True, "apns": False, "devices": 1}


def test_send_signs_for_apple_and_honours_its_answers(relay, monkeypatch):
    mod, client, tmp_path = relay
    key = _key(tmp_path)
    rid = client.post("/v1/register", json={"token": TOKEN, "bundle": "de.maxaufknax.pocketadm"}).json()["relay_id"]
    seen = []

    async def fake_deliver(token, bundle, env, payload):
        seen.append((token, bundle, env, payload))
        return (410, "Unregistered") if len(seen) == 2 else (200, "")
    monkeypatch.setattr(mod, "deliver", fake_deliver)
    r = client.post("/v1/send", json={"relay_id": rid, "title": "Nextcloud down", "subtitle": "box",
                                      "body": "502 since 14:02", "thread": "watch",
                                      "level": "time-sensitive", "data": {"message": "m1", "x": {"no": 1}}})
    assert r.status_code == 200
    token, bundle, env, payload = seen[0]
    assert token == TOKEN and bundle == "de.maxaufknax.pocketadm" and env == "production"
    assert payload["aps"]["alert"] == {"title": "Nextcloud down", "body": "502 since 14:02", "subtitle": "box"}
    assert payload["aps"]["interruption-level"] == "time-sensitive" and payload["aps"]["thread-id"] == "watch"
    assert payload["pocketadm"] == {"message": "m1"}              # only flat values
    # the phone was deleted: the relay forgets it and tells the server
    assert client.post("/v1/send", json={"relay_id": rid, "body": "b"}).status_code == 410
    assert client.post("/v1/send", json={"relay_id": rid, "body": "b"}).status_code == 410
    assert client.get("/v1/health").json()["devices"] == 0

    # the provider token is a valid ES256 signature by the configured key
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    jwt = mod.provider_token()
    head, claims, sig = jwt.split(".")
    pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    assert json.loads(pad(head)) == {"alg": "ES256", "kid": "KEY1234567"}
    assert json.loads(pad(claims))["iss"] == "TEAM123456"
    raw = pad(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key.public_key().verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))


def test_rate_limit_per_phone(relay, monkeypatch):
    mod, client, tmp_path = relay
    _key(tmp_path)
    rid = client.post("/v1/register", json={"token": TOKEN, "bundle": "de.maxaufknax.pocketadm"}).json()["relay_id"]

    async def ok(*a):
        return 200, ""
    monkeypatch.setattr(mod, "deliver", ok)
    codes = [client.post("/v1/send", json={"relay_id": rid, "body": "b"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
