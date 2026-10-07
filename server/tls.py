"""HTTPS for a server that has no domain (yet).

A fresh install should be reachable from the phone the moment the installer
finishes, and it must not be plain HTTP: the box behind it is root on its
host. Without a domain there is no certificate a phone already trusts, so
PocketADM makes its own and the *pairing QR* carries the trust instead. The
installer prints a QR that holds the address, a one-time pairing code and the
fingerprint of this server's TLS key; the app accepts this server's
certificate only if its key matches that fingerprint. Scanning the QR is the
"trust on first use" step, done with the eyes instead of a warning dialog.

The fingerprint covers the *key*, not the certificate: the certificate is
re-issued with the same key before it expires, so a phone paired once stays
paired. With a domain and a real certificate (Caddy profile, or your own
reverse proxy) the system's own trust store takes over and the pin is only a
fallback.

The key lives in DATA_DIR/tls/, next to the other credentials on the data
volume, and survives container updates.
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlsplit

from . import config

TLS_DIR = config.DATA_DIR / "tls"
CERT = TLS_DIR / "cert.pem"
KEY = TLS_DIR / "key.pem"
PORT = 8443                     # inside the container; compose maps it
VALIDITY_DAYS = 825             # Apple's ceiling for TLS server certificates
RENEW_BEFORE_DAYS = 30


def enabled() -> bool:
    return os.environ.get("HELMSMAN_TLS", "1") != "0"


def _load_or_make_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    if KEY.exists():
        try:
            key = serialization.load_pem_private_key(KEY.read_bytes(), password=None)
            if isinstance(key, ec.EllipticCurvePrivateKey):
                return key
        except Exception:      # unreadable key: replace it (the pin changes)
            pass
    key = ec.generate_private_key(ec.SECP256R1())
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(TLS_DIR, 0o700)
    KEY.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                      serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    os.chmod(KEY, 0o600)
    return key


def _names() -> tuple[list[str], list[str]]:
    """DNS names and IP addresses for the certificate. A browser still warns
    (the certificate is self-signed), but the names it shows are right."""
    dns = {"localhost", "pocketadm.local"}
    ips = {"127.0.0.1", "::1"}
    host = urlsplit(os.environ.get("POCKETADM_PUBLIC_URL", "")).hostname or ""
    for name in (host, socket.gethostname()):
        if not name:
            continue
        try:
            ips.add(str(ipaddress.ip_address(name)))
        except ValueError:
            dns.add(name.lower())
    return sorted(dns), sorted(ips)


def _cert_needs_renewal() -> bool:
    from cryptography import x509
    try:
        cert = x509.load_pem_x509_certificate(CERT.read_bytes())
    except Exception:
        return True
    left = cert.not_valid_after_utc - _dt.datetime.now(_dt.timezone.utc)
    return left < _dt.timedelta(days=RENEW_BEFORE_DAYS)


def ensure() -> tuple[Path, Path] | None:
    """Make sure a key and a current certificate exist. Returns their paths,
    or None when TLS is switched off or cannot be set up."""
    if not enabled():
        return None
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.x509.oid import NameOID
    except Exception:          # pragma: no cover - cryptography ships with paramiko
        return None
    key = _load_or_make_key()
    if CERT.exists() and not _cert_needs_renewal():
        return CERT, KEY
    dns, ips = _names()
    now = _dt.datetime.now(_dt.timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PocketADM"),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PocketADM self-signed")])
    san = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - _dt.timedelta(minutes=5))
            .not_valid_after(now + _dt.timedelta(days=VALIDITY_DAYS))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return CERT, KEY


def fingerprint() -> str:
    """base64url(SHA-256(uncompressed EC public point)) of this server's TLS
    key, or "" without one. The iOS app computes the same value from
    SecKeyCopyExternalRepresentation, which yields exactly that point."""
    if not enabled() or not KEY.exists():
        return ""
    try:
        from cryptography.hazmat.primitives import serialization
        key = serialization.load_pem_private_key(KEY.read_bytes(), password=None)
        point = key.public_key().public_bytes(serialization.Encoding.X962,
                                              serialization.PublicFormat.UncompressedPoint)
    except Exception:
        return ""
    return base64.urlsafe_b64encode(hashlib.sha256(point).digest()).decode().rstrip("=")
