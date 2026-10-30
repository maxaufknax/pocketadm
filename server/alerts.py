"""Inbound webhook for external monitoring (Prometheus Alertmanager).

Prometheus evaluates the alert rules in docker/prometheus/rules; Alertmanager
groups, deduplicates and forwards every firing/resolved alert here. Alertmanager
cannot log in, so the webhook is authorised by a shared-secret token
(X-Alert-Token) generated on first use and stored in settings.json.

Each alert becomes one activity event in the live feed; warn/crit events flow
into the watch module, which bundles them and pushes to the phone.
"""
from __future__ import annotations

import hmac
import secrets

from . import activity, config

SEVERITIES = {"critical": "crit", "warning": "warn", "info": "info"}


def token() -> str:
    """The shared secret Alertmanager sends as X-Alert-Token."""
    tok = str(config.settings.get("alerts_token") or "").strip()
    if not tok:
        tok = secrets.token_urlsafe(24)
        config.settings["alerts_token"] = tok
        config.save_settings(config.settings)
    return tok


def rotate() -> str:
    config.settings["alerts_token"] = secrets.token_urlsafe(24)
    config.save_settings(config.settings)
    return config.settings["alerts_token"]


def verify(provided: str) -> bool:
    expected = str(config.settings.get("alerts_token") or "").strip()
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected, provided)


def ingest(payload: dict) -> dict:
    """Turn one Alertmanager webhook payload into activity events."""
    fired = resolved = 0
    alerts = payload.get("alerts") if isinstance(payload, dict) else None
    for alert in alerts if isinstance(alerts, list) else []:
        if not isinstance(alert, dict):
            continue
        labels = alert.get("labels") or {}
        annotations = alert.get("annotations") or {}
        name = str(labels.get("alertname") or "Alert").strip() or "Alert"
        severity = SEVERITIES.get(str(labels.get("severity") or "").lower(), "info")
        detail = str(annotations.get("description") or annotations.get("summary") or "").strip()
        target = str(labels.get("instance") or labels.get("job") or "").strip()
        if alert.get("status") == "resolved":
            activity.push("system", "alert-resolved", f"{name} resolved",
                          detail, "ok", target=target, source="prometheus")
            resolved += 1
        else:
            activity.push("system", "alert", name, detail, severity,
                          target=target, source="prometheus")
            fired += 1
    return {"fired": fired, "resolved": resolved}
