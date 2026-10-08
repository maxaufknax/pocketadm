#!/usr/bin/env python3
"""Make sure the app id can receive push notifications — before signing.

The app asks Apple for push (the aps-environment entitlement), and an App Store
profile only carries that entitlement when the app id has the Push
Notifications capability. This script, run in the Codemagic release workflow
before the Xcode project is generated:

  1. enables PUSH_NOTIFICATIONS on the bundle id if it is not on yet;
  2. deletes App Store profiles of that bundle id that lack the entitlement —
     Apple usually invalidates them itself when a capability changes, but a
     stale active one would be picked by `fetch-signing-files` and fail the
     archive — so the signing step creates a fresh profile that has it;
  3. writes PUSH_READY=1 (or 0) to $CM_ENV.

With PUSH_READY=0 the workflow builds without the entitlement: the app then
falls back to local notifications, and a release is never blocked by push.

    python3 tools/asc_push.py --bundle-id de.maxaufknax.pocketadm
"""
from __future__ import annotations

import argparse
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from asc_release import ASC  # noqa: E402 — same credentials, same client


def bundle_id(asc: ASC, identifier: str) -> str:
    data = asc.call("GET", "/v1/bundleIds", params={"filter[identifier]": identifier, "limit": "20"})
    for item in data.get("data", []):
        if item["attributes"].get("identifier") == identifier:
            return item["id"]
    raise SystemExit(f"✗ no bundle id {identifier} in this developer account")


def ensure_capability(asc: ASC, bid: str) -> bool:
    caps = asc.call("GET", f"/v1/bundleIds/{bid}/bundleIdCapabilities", params={"limit": "200"})
    kinds = {c["attributes"].get("capabilityType") for c in caps.get("data", [])}
    if "PUSH_NOTIFICATIONS" in kinds:
        print("✓ Push Notifications already enabled on the app id")
        return False
    asc.call("POST", "/v1/bundleIdCapabilities", body={"data": {
        "type": "bundleIdCapabilities",
        "attributes": {"capabilityType": "PUSH_NOTIFICATIONS"},
        "relationships": {"bundleId": {"data": {"type": "bundleIds", "id": bid}}}}})
    print("✓ enabled Push Notifications on the app id")
    return True


def drop_profiles_without_push(asc: ASC, bid: str) -> int:
    data = asc.call("GET", f"/v1/bundleIds/{bid}/profiles", params={"limit": "200"})
    dropped = 0
    for profile in data.get("data", []):
        attrs = profile["attributes"]
        if attrs.get("profileType") != "IOS_APP_STORE" or attrs.get("profileState") != "ACTIVE":
            continue
        content = base64.b64decode(attrs.get("profileContent") or "")
        if b"aps-environment" in content:
            continue
        asc.call("DELETE", f"/v1/profiles/{profile['id']}")
        dropped += 1
        print(f"✓ deleted profile {attrs.get('name')} (no push entitlement) — signing creates a new one")
    return dropped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-id", required=True)
    args = parser.parse_args()
    ready = "0"
    try:
        asc = ASC()
        bid = bundle_id(asc, args.bundle_id)
        ensure_capability(asc, bid)
        drop_profiles_without_push(asc, bid)
        ready = "1"
    except SystemExit as e:
        print(f"! push could not be prepared ({e}) — building without the push entitlement")
    except Exception as e:  # noqa: BLE001 — never block a release on push
        print(f"! push could not be prepared ({type(e).__name__}: {e}) — building without it")
    env = os.environ.get("CM_ENV")
    if env:
        with open(env, "a") as fh:
            fh.write(f"PUSH_READY={ready}\n")
    print(f"PUSH_READY={ready}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
