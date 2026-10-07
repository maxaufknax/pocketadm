"""HTTPS from the first minute, and a QR that signs the phone in.

Since 0.19 the app's own port is bound to loopback (a root shell must not
answer the open internet in plain HTTP), but the installer kept telling people
to open http://<server>:8090, which then could not be reached. Now the
installer ends with a QR: the server's https address, a one-time pairing code
and the fingerprint of the server's own TLS key, which the iOS app pins.

Pinned here: the key outlives certificate renewals (or every paired phone
would be locked out), the fingerprint is exactly what iOS computes from the
key, every client reads the one link format, and the SSH installer picks the
link up.
"""
import base64
import datetime as dt
import hashlib
import json
import pathlib
import shutil
import subprocess

import pytest

from server import bootstrap, cli, config, tls

ROOT = pathlib.Path(__file__).parent.parent


@pytest.fixture
def tls_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tls, "TLS_DIR", tmp_path / "tls")
    monkeypatch.setattr(tls, "CERT", tmp_path / "tls" / "cert.pem")
    monkeypatch.setattr(tls, "KEY", tmp_path / "tls" / "key.pem")
    monkeypatch.delenv("HELMSMAN_TLS", raising=False)
    return tmp_path / "tls"


def _cert(path):
    from cryptography import x509
    return x509.load_pem_x509_certificate(path.read_bytes())


def test_ensure_creates_a_p256_key_and_a_short_lived_cert(tls_dir):
    cert_path, key_path = tls.ensure()
    assert oct(key_path.stat().st_mode & 0o777) == "0o600"
    assert oct(tls_dir.stat().st_mode & 0o777) == "0o700"
    cert = _cert(cert_path)
    days = (cert.not_valid_after_utc - cert.not_valid_before_utc).days
    assert days <= 826, "Apple refuses TLS server certificates valid for more than 825 days"
    assert cert.public_key().curve.name == "secp256r1"
    assert cert.signature_hash_algorithm.name == "sha256"


def test_fingerprint_is_sha256_of_the_uncompressed_point(tls_dir):
    """iOS hashes SecKeyCopyExternalRepresentation, i.e. 0x04||X||Y."""
    from cryptography.hazmat.primitives import serialization
    tls.ensure()
    key = serialization.load_pem_private_key(tls.KEY.read_bytes(), password=None)
    point = key.public_key().public_bytes(serialization.Encoding.X962,
                                          serialization.PublicFormat.UncompressedPoint)
    assert len(point) == 65 and point[0] == 4
    expected = base64.urlsafe_b64encode(hashlib.sha256(point).digest()).decode().rstrip("=")
    assert tls.fingerprint() == expected
    assert len(expected) == 43 and "=" not in expected


def test_renewal_keeps_the_key_so_paired_phones_stay_paired(tls_dir, monkeypatch):
    # a certificate with ten days left is inside the renewal window
    monkeypatch.setattr(tls, "VALIDITY_DAYS", 10)
    tls.ensure()
    before = tls.fingerprint()
    old_serial = _cert(tls.CERT).serial_number
    assert tls._cert_needs_renewal()
    monkeypatch.setattr(tls, "VALIDITY_DAYS", 825)
    tls.ensure()
    assert not tls._cert_needs_renewal()
    assert _cert(tls.CERT).serial_number != old_serial, "certificate was not renewed"
    assert tls.fingerprint() == before, "renewal changed the key — every pin would break"


def test_existing_cert_is_reused(tls_dir):
    tls.ensure()
    serial = _cert(tls.CERT).serial_number
    tls.ensure()
    assert _cert(tls.CERT).serial_number == serial


def test_tls_can_be_switched_off(tls_dir, monkeypatch):
    monkeypatch.setenv("HELMSMAN_TLS", "0")
    assert tls.ensure() is None
    assert tls.fingerprint() == ""


def test_public_url_host_lands_in_the_certificate(tls_dir, monkeypatch):
    from cryptography import x509
    monkeypatch.setenv("POCKETADM_PUBLIC_URL", "https://203.0.113.10:8443")
    tls.ensure()
    san = _cert(tls.CERT).extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "203.0.113.10" in [str(i) for i in san.get_values_for_type(x509.IPAddress)]


# ------------------------------------------------------------- the link

def test_pairing_link_format():
    link = cli.pairing_link("https://203.0.113.10:8443/", "CODE123", "FPabc")
    assert link == "https://203.0.113.10:8443/?pair=CODE123&fp=FPabc"
    # a fingerprint means nothing over plain http
    assert cli.pairing_link("http://192.168.1.5:8090", "C", "FP") == "http://192.168.1.5:8090/?pair=C"


def test_cli_refuses_without_an_address(monkeypatch, capsys):
    monkeypatch.delenv("POCKETADM_PUBLIC_URL", raising=False)
    assert cli.main(["pair"]) == 2
    assert "--url" in capsys.readouterr().err


def test_pair_new_reports_the_fingerprint(clean_settings, tls_dir, monkeypatch):
    from starlette.testclient import TestClient
    from server import auth, main
    monkeypatch.setattr(config, "DEMO", False)
    tls.ensure()
    r = TestClient(main.app).post("/api/pair/new",
                                  headers={"Authorization": "Bearer " + auth.issue_token()})
    assert r.status_code == 200
    assert r.json()["tls_fingerprint"] == tls.fingerprint()


def _parse(raw: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    src = (ROOT / "web" / "app.js").read_text()
    start = src.index("function parsePairPayload(")
    end = src.index("\n}\n", start) + 3
    script = src[start:end] + f"\nprocess.stdout.write(JSON.stringify(parsePairPayload({json.dumps(raw)})));"
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.parametrize("raw,origin,code", [
    ("https://203.0.113.10:8443/?pair=ABC&fp=XYZ", "https://203.0.113.10:8443", "ABC"),
    ("https://box.example.com/?pair=ABC&c=chat1", "https://box.example.com", "ABC"),
    ("https://box.example.com/pair?code=ABC", "https://box.example.com", "ABC"),   # iOS app < 2.0
    ('{"h":"pair","u":"https://box.example.com","c":"ABC"}', "https://box.example.com", "ABC"),
])
def test_web_scanner_reads_every_link_format(raw, origin, code):
    parsed = _parse(raw)
    assert parsed["u"] == origin and parsed["c"] == code


@pytest.mark.parametrize("raw", ["hello", "https://box.example.com/?x=1", "javascript:alert(1)?pair=x"])
def test_web_scanner_ignores_other_codes(raw):
    assert _parse(raw) is None


def test_cli_link_is_read_by_the_web_scanner():
    link = cli.pairing_link("https://203.0.113.10:8443", "C0DE", "FP")
    assert _parse(link)["c"] == "C0DE"


# ------------------------------------------------------------- installer + SSH bootstrap

def test_ssh_bootstrap_picks_up_the_pairing_link():
    line = "PAIRING_LINK: https://203.0.113.10:8443/?pair=ABC&fp=XYZ"
    assert bootstrap._PAIR_RE.search(line).group(1) == "https://203.0.113.10:8443/?pair=ABC&fp=XYZ"
    cmd = bootstrap._remote_command(8443, as_root=True)
    assert "POCKETADM_NONINTERACTIVE=1" in cmd and "POCKETADM_TLS_PORT=8443" in cmd


def test_installer_prints_the_qr_and_no_dead_address():
    script = (ROOT / "install.sh").read_text()
    assert "python -m server.cli pair" in script
    assert "http://${IP" not in script and "Open:      http://" not in script
    assert "Helmsman is running" not in script
    assert subprocess.run(["bash", "-n", str(ROOT / "install.sh")]).returncode == 0


def test_compose_publishes_https_only_where_the_installer_says():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert '"${HELMSMAN_TLS_BIND:-127.0.0.1}:${HELMSMAN_TLS_PORT:-8443}:8443"' in compose, (
        "an existing install without .env must not suddenly expose a port")
    assert 'profiles: ["domain"]' in compose


def test_container_starts_both_listeners():
    assert 'CMD ["python", "-m", "server.run"]' in (ROOT / "Dockerfile").read_text()
