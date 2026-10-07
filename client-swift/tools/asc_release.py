#!/usr/bin/env python3
"""Get the App Store version ready for review — and stop there.

Runs at the end of the `ios-native-release` Codemagic workflow, after the IPA
has been uploaded:

  1. waits until App Store Connect has processed the build (VALID);
  2. hands it to TestFlight: "What to Test" from the What's New texts, and the
     build added to every internal tester group that does not get each new
     build automatically (internal testing needs no beta review);
  3. finds the version being prepared, or creates it — a new version inherits
     description, keywords, screenshots and review contact from the live one;
  4. sets its version string, *then* attaches the build (the other order is
     refused: the build has to match the version);
  5. writes "What's New" for every localization (required for an update) and
     the App Review notes with the demo account;
  6. prints where to press "Submit for Review".

It never submits, and it refuses to touch anything while a version is in
review or waiting for release. Credentials come from the Codemagic App Store
Connect integration (APP_STORE_CONNECT_ISSUER_ID, _KEY_IDENTIFIER and
_PRIVATE_KEY); nothing secret lives in this repository.

    python3 tools/asc_release.py --bundle-id de.maxaufknax.pocketadm \\
        --version 2.0.0 --build-number 202610071230 --texts AppStore
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import jwt  # PyJWT, installed by the workflow next to `cryptography`

API = "https://api.appstoreconnect.apple.com"
# states a version can be edited in (not yet submitted, or sent back)
EDITABLE = {"PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED", "REJECTED",
            "METADATA_REJECTED", "INVALID_BINARY"}
# states in which a version must not be touched by a script
HANDS_OFF = {"WAITING_FOR_REVIEW", "IN_REVIEW", "READY_FOR_REVIEW", "PENDING_APPLE_RELEASE",
             "PENDING_DEVELOPER_RELEASE", "PROCESSING_FOR_APP_STORE",
             "PROCESSING_FOR_DISTRIBUTION", "WAITING_FOR_EXPORT_COMPLIANCE", "ACCEPTED"}


class ASC:
    def __init__(self) -> None:
        self.issuer = os.environ["APP_STORE_CONNECT_ISSUER_ID"]
        self.key_id = os.environ["APP_STORE_CONNECT_KEY_IDENTIFIER"]
        self.key = os.environ["APP_STORE_CONNECT_PRIVATE_KEY"]
        self._token, self._exp = "", 0.0

    def token(self) -> str:
        now = time.time()
        if now > self._exp - 60:
            self._exp = now + 15 * 60
            self._token = jwt.encode(
                {"iss": self.issuer, "iat": int(now), "exp": int(self._exp),
                 "aud": "appstoreconnect-v1"},
                self.key, algorithm="ES256", headers={"kid": self.key_id, "typ": "JWT"})
        return self._token

    def call(self, method: str, path: str, body: dict | None = None,
             params: dict | None = None, ok404: bool = False) -> dict | None:
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token(), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 404 and ok404:
                return None
            detail = e.read().decode("utf-8", "replace")
            try:
                errors = json.loads(detail).get("errors", [])
                detail = "; ".join(f"{x.get('code')}: {x.get('detail')}" for x in errors) or detail
            except ValueError:
                pass
            raise SystemExit(f"✗ {method} {path} → HTTP {e.code}: {detail[:800]}") from None


def state_of(version: dict) -> str:
    a = version.get("attributes") or {}
    return a.get("appVersionState") or a.get("appStoreState") or ""


def wait_for_build(asc: ASC, app_id: str, version: str, number: str, minutes: int) -> dict:
    deadline = time.time() + minutes * 60
    print(f"… waiting for build {number} ({version}) to finish processing")
    while True:
        res = asc.call("GET", "/v1/builds", params={
            "filter[app]": app_id, "filter[version]": number,
            "filter[preReleaseVersion.version]": version, "limit": "1"})
        builds = res.get("data") or []
        if builds:
            b = builds[0]
            state = (b.get("attributes") or {}).get("processingState", "")
            if state == "VALID":
                print(f"✓ build {number} is processed")
                return b
            if state in ("FAILED", "INVALID"):
                raise SystemExit(f"✗ App Store Connect rejected build {number} ({state}); "
                                 "Apple sends the reason by e-mail.")
        if time.time() > deadline:
            raise SystemExit(f"✗ build {number} was not processed within {minutes} min. "
                             "Re-run only this step later; the upload is done.")
        time.sleep(30)


def soft(what: str, step, *args) -> bool:
    """Runs a step whose failure must not cost the App Store preparation —
    TestFlight extras are worth a warning, not a stop."""
    try:
        step(*args)
        return True
    except SystemExit as e:
        print(f"! {what} skipped: {e}")
        return False


def what_to_test(asc: ASC, build_id: str, texts: Path) -> None:
    """TestFlight's "What to Test", one per What's New text (English first)."""
    files = sorted((texts / "whats-new").glob("*.txt"), key=lambda p: (p.stem != "en-US", p.stem))
    existing = {(x.get("attributes") or {}).get("locale"): x["id"] for x in
                (asc.call("GET", f"/v1/builds/{build_id}/betaBuildLocalizations") or {}).get("data") or []}
    done = []
    for path in files:
        locale, attrs = path.stem, {"whatsNew": path.read_text(encoding="utf-8").strip()[:4000]}
        if locale in existing:
            call = ("PATCH", f"/v1/betaBuildLocalizations/{existing[locale]}", {"data": {
                "type": "betaBuildLocalizations", "id": existing[locale], "attributes": attrs}})
        else:
            call = ("POST", "/v1/betaBuildLocalizations", {"data": {
                "type": "betaBuildLocalizations", "attributes": {"locale": locale, **attrs},
                "relationships": {"build": {"data": {"type": "builds", "id": build_id}}}}})
        if soft(f"What to Test ({locale})", asc.call, *call):
            done.append(locale)
    if done:
        print(f"✓ TestFlight “What to Test” ({', '.join(done)})")


def add_to_internal_groups(asc: ASC, app_id: str, build_id: str) -> None:
    """Internal testers get the build without a beta review. Groups that take
    every build automatically already have it; the others get it added here.
    External groups are left alone — a build there means a Beta App Review."""
    groups = (asc.call("GET", f"/v1/apps/{app_id}/betaGroups", params={"limit": "50"})
              or {}).get("data") or []
    internal = [g for g in groups if (g.get("attributes") or {}).get("isInternalGroup")]
    for g in internal:
        a = g.get("attributes") or {}
        if a.get("hasAccessToAllBuilds"):
            print(f"✓ TestFlight group “{a.get('name')}” gets every build automatically")
            continue
        asc.call("POST", f"/v1/betaGroups/{g['id']}/relationships/builds",
                 {"data": [{"type": "builds", "id": build_id}]})
        print(f"✓ TestFlight group “{a.get('name')}”: build added")
    if not internal:
        print("! no internal TestFlight group yet — App Store Connect → TestFlight → Internal "
              "Testing → “+”, add yourself; the build is already there to pick")


def testflight_state(asc: ASC, build_id: str) -> None:
    res = asc.call("GET", f"/v1/builds/{build_id}/buildBetaDetail", ok404=True)
    state = (((res or {}).get("data") or {}).get("attributes") or {}).get("internalBuildState", "")
    if state:
        print(f"{'✓' if state in ('READY_FOR_BETA_TESTING', 'IN_BETA_TESTING') else '!'} "
              f"TestFlight internal testing: {state}")


def versions_or_stop(asc: ASC, app_id: str) -> list[dict]:
    """The app's iOS versions — or a stop, before anything is changed, when one
    of them is in review or waiting for release."""
    res = asc.call("GET", f"/v1/apps/{app_id}/appStoreVersions",
                   params={"filter[platform]": "IOS", "limit": "20"})
    versions = res.get("data") or []
    for v in versions:
        if state_of(v) in HANDS_OFF:
            raise SystemExit(f"✗ version {v['attributes'].get('versionString')} is "
                             f"{state_of(v)} — not touching anything while it is in flight.")
    return versions


def find_or_create_version(asc: ASC, app_id: str, version: str, release_type: str) -> dict:
    versions = versions_or_stop(asc, app_id)
    editable = next((v for v in versions if state_of(v) in EDITABLE), None)
    if editable:
        if editable["attributes"].get("versionString") != version:
            print(f"… renaming the version in preparation "
                  f"({editable['attributes'].get('versionString')} → {version})")
            asc.call("PATCH", f"/v1/appStoreVersions/{editable['id']}", {"data": {
                "type": "appStoreVersions", "id": editable["id"],
                "attributes": {"versionString": version}}})
        else:
            print(f"✓ version {version} is already being prepared")
        return editable
    print(f"… creating version {version}")
    res = asc.call("POST", "/v1/appStoreVersions", {"data": {
        "type": "appStoreVersions",
        "attributes": {"platform": "IOS", "versionString": version, "releaseType": release_type},
        "relationships": {"app": {"data": {"type": "apps", "id": app_id}}}}})
    print(f"✓ version {version} created (it inherits the live version's listing)")
    return res["data"]


def live_version(asc: ASC, app_id: str) -> dict | None:
    res = asc.call("GET", f"/v1/apps/{app_id}/appStoreVersions",
                   params={"filter[platform]": "IOS", "filter[appStoreState]": "READY_FOR_SALE",
                           "limit": "1"})
    return (res.get("data") or [None])[0]


def set_whats_new(asc: ASC, version_id: str, texts: Path) -> None:
    res = asc.call("GET", f"/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations",
                   params={"limit": "50"})
    fallback = (texts / "whats-new" / "en-US.txt").read_text(encoding="utf-8").strip()
    for loc in res.get("data") or []:
        locale = loc["attributes"].get("locale", "")
        path = texts / "whats-new" / f"{locale}.txt"
        text = path.read_text(encoding="utf-8").strip() if path.exists() else fallback
        asc.call("PATCH", f"/v1/appStoreVersionLocalizations/{loc['id']}", {"data": {
            "type": "appStoreVersionLocalizations", "id": loc["id"],
            "attributes": {"whatsNew": text[:4000]}}})
        print(f"✓ What's New ({locale}{'' if path.exists() else ', English text'})")


def set_review_notes(asc: ASC, app_id: str, version_id: str, notes: str) -> None:
    attrs = {"notes": notes[:4000], "demoAccountRequired": True,
             "demoAccountName": "demo", "demoAccountPassword": "demo"}
    res = asc.call("GET", f"/v1/appStoreVersions/{version_id}/appStoreReviewDetail", ok404=True)
    detail = (res or {}).get("data")
    if detail:
        asc.call("PATCH", f"/v1/appStoreReviewDetails/{detail['id']}", {"data": {
            "type": "appStoreReviewDetails", "id": detail["id"], "attributes": attrs}})
        print("✓ App Review notes and demo account")
        return
    # A version created over the API may start without review details: copy the
    # contact person from the live version rather than keeping one in this repo.
    live = live_version(asc, app_id)
    contact = {}
    if live:
        prev = asc.call("GET", f"/v1/appStoreVersions/{live['id']}/appStoreReviewDetail", ok404=True)
        pa = ((prev or {}).get("data") or {}).get("attributes") or {}
        contact = {k: pa.get(k) for k in ("contactFirstName", "contactLastName",
                                          "contactPhone", "contactEmail") if pa.get(k)}
    asc.call("POST", "/v1/appStoreReviewDetails", {"data": {
        "type": "appStoreReviewDetails", "attributes": {**contact, **attrs},
        "relationships": {"appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}}}}})
    print("✓ App Review notes and demo account (contact copied from the live version)"
          if contact else "! App Review notes set — add the contact person in App Store Connect")


SCREENSHOT_SLOT = "APP_IPHONE_65"     # 1284 x 2778, the slot the listing has always used


def put_bytes(op: dict, blob: bytes) -> None:
    """One upload operation: a presigned PUT of a slice of the file."""
    part = blob[op["offset"]:op["offset"] + op["length"]]
    req = urllib.request.Request(op["url"], data=part, method=op.get("method", "PUT"),
                                 headers={h["name"]: h["value"] for h in op.get("requestHeaders") or []})
    with urllib.request.urlopen(req, timeout=120):
        pass


def upload_screenshot(asc: ASC, set_id: str, path: Path) -> str:
    blob = path.read_bytes()
    res = asc.call("POST", "/v1/appScreenshots", {"data": {
        "type": "appScreenshots", "attributes": {"fileName": path.name, "fileSize": len(blob)},
        "relationships": {"appScreenshotSet": {"data": {"type": "appScreenshotSets", "id": set_id}}}}})
    shot = res["data"]
    for op in shot["attributes"].get("uploadOperations") or []:
        put_bytes(op, blob)
    asc.call("PATCH", f"/v1/appScreenshots/{shot['id']}", {"data": {
        "type": "appScreenshots", "id": shot["id"],
        "attributes": {"uploaded": True, "sourceFileChecksum": hashlib.md5(blob).hexdigest()}}})
    for _ in range(40):                     # Apple processes the image after the upload
        state = (((asc.call("GET", f"/v1/appScreenshots/{shot['id']}") or {}).get("data") or {})
                 .get("attributes") or {}).get("assetDeliveryState") or {}
        if state.get("state") == "COMPLETE":
            break
        if state.get("state") == "FAILED":
            raise SystemExit(f"✗ App Store Connect could not process {path.name}: {state.get('errors')}")
        time.sleep(3)
    return shot["id"]


def replace_screenshots(asc: ASC, version_id: str, shots: Path) -> None:
    """Puts shots/<locale>/*.png into each localization's iPhone set of this
    version: upload all new ones first, then remove the old ones, so the set
    is never empty. Only the version being prepared changes — the live one
    keeps its images until this version is approved."""
    res = asc.call("GET", f"/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations",
                   params={"limit": "50"})
    for loc in res.get("data") or []:
        locale = loc["attributes"].get("locale", "")
        folder = shots / locale
        if not folder.is_dir():
            folder = shots / "en-US"
        files = sorted(folder.glob("*.png"))
        if not files:
            continue
        sets = (asc.call("GET", f"/v1/appStoreVersionLocalizations/{loc['id']}/appScreenshotSets")
                or {}).get("data") or []
        target = next((x for x in sets if x["attributes"].get("screenshotDisplayType") == SCREENSHOT_SLOT), None)
        if target is None:
            target = asc.call("POST", "/v1/appScreenshotSets", {"data": {
                "type": "appScreenshotSets", "attributes": {"screenshotDisplayType": SCREENSHOT_SLOT},
                "relationships": {"appStoreVersionLocalization": {"data": {
                    "type": "appStoreVersionLocalizations", "id": loc["id"]}}}}})["data"]
        old = [x["id"] for x in (asc.call("GET", f"/v1/appScreenshotSets/{target['id']}/appScreenshots")
                                 or {}).get("data") or []]
        new = [upload_screenshot(asc, target["id"], f) for f in files]
        for shot_id in old:
            asc.call("DELETE", f"/v1/appScreenshots/{shot_id}")
        asc.call("PATCH", f"/v1/appScreenshotSets/{target['id']}/relationships/appScreenshots",
                 {"data": [{"type": "appScreenshots", "id": i} for i in new]})
        # other iPhone sizes would keep showing the old app on those phones
        for other in sets:
            kind = other["attributes"].get("screenshotDisplayType", "")
            if kind.startswith("APP_IPHONE_") and kind != SCREENSHOT_SLOT:
                asc.call("DELETE", f"/v1/appScreenshotSets/{other['id']}")
        print(f"✓ screenshots ({locale}{'' if folder.name == locale else ', English images'}): "
              f"{len(new)} new, {len(old)} replaced")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--bundle-id", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--build-number", required=True)
    p.add_argument("--texts", required=True, help="folder with whats-new/<locale>.txt and review-notes.txt")
    p.add_argument("--wait-minutes", type=int, default=60)
    p.add_argument("--release-type", default="AFTER_APPROVAL", choices=["AFTER_APPROVAL", "MANUAL"])
    p.add_argument("--screenshots", help="folder with <locale>/*.png (tools/store-shots.py output)")
    args = p.parse_args()
    texts = Path(args.texts)

    asc = ASC()
    apps = asc.call("GET", "/v1/apps", params={"filter[bundleId]": args.bundle_id}).get("data") or []
    if not apps:
        raise SystemExit(f"✗ no App Store Connect app with bundle id {args.bundle_id}")
    app_id = apps[0]["id"]
    versions_or_stop(asc, app_id)          # before waiting, before changing anything

    build = wait_for_build(asc, app_id, args.version, args.build_number, args.wait_minutes)
    if (build.get("attributes") or {}).get("usesNonExemptEncryption") is None:
        # Info.plist says ITSAppUsesNonExemptEncryption=false; make sure ASC agrees
        asc.call("PATCH", f"/v1/builds/{build['id']}", {"data": {
            "type": "builds", "id": build["id"], "attributes": {"usesNonExemptEncryption": False}}})
        print("✓ export compliance: standard encryption only")

    soft("TestFlight “What to Test”", what_to_test, asc, build["id"], texts)
    soft("TestFlight groups", add_to_internal_groups, asc, app_id, build["id"])
    soft("TestFlight state", testflight_state, asc, build["id"])

    version = find_or_create_version(asc, app_id, args.version, args.release_type)
    asc.call("PATCH", f"/v1/appStoreVersions/{version['id']}/relationships/build",
             {"data": {"type": "builds", "id": build["id"]}})
    print(f"✓ build {args.build_number} attached to {args.version}")

    set_whats_new(asc, version["id"], texts)
    if args.screenshots:
        replace_screenshots(asc, version["id"], Path(args.screenshots))
    set_review_notes(asc, app_id, version["id"],
                     (texts / "review-notes.txt").read_text(encoding="utf-8").strip())

    print()
    print(f"Ready. Nothing was submitted. To send {args.version} to App Review:")
    print(f"  https://appstoreconnect.apple.com/apps/{app_id}/distribution/ios/version/inflight")
    print("  → check the page once, then press “Add for Review” / “Submit for Review”.")


if __name__ == "__main__":
    try:
        main()
    except KeyError as e:
        sys.exit(f"✗ missing environment variable {e} — is the App Store Connect "
                 "integration enabled for this workflow?")
