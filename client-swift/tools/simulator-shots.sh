#!/usr/bin/env bash
# Photographs the app on a fresh simulator against the live demo server.
#
#   tools/simulator-shots.sh <App.app> <bundle-id> <out-dir> <shot-list>
#
# Each shot-list line is "<name> <light|dark> <tab> [route]"; the app opens the
# demo on that tab (and route) by itself — see AppState.screenshotTab. Used by
# ios-native-shots (every screen, both appearances) and ios-native-release (the
# five store images).
set -euo pipefail
APP="$1"; BUNDLE="$2"; OUT="$3"; LIST="$4"
DEVICE="${SIM_DEVICE:-iPhone 17 Pro Max}"
mkdir -p "$OUT"

UDID=$(xcrun simctl create pocketadm-shots "$DEVICE")
trap 'xcrun simctl shutdown "$UDID" >/dev/null 2>&1 || true; xcrun simctl delete "$UDID" >/dev/null 2>&1 || true' EXIT
xcrun simctl boot "$UDID"
xcrun simctl bootstatus "$UDID" -b >/dev/null
xcrun simctl status_bar "$UDID" override --time "9:41" --dataNetwork wifi --wifiMode active \
  --wifiBars 3 --cellularMode active --cellularBars 4 --batteryState charged --batteryLevel 100
xcrun simctl install "$UDID" "$APP"

# A fresh simulator greets with system notifications ("Ready for Apple
# Intelligence") during its first minute. Let them come and go before the
# first picture, or one lands on top of the app.
sleep "${SHOTS_WARMUP:-80}"

current=""
# the list is read on fd 3, so nothing in the loop can swallow its lines
while read -r name appearance tab route <&3; do
  if [ -z "${name:-}" ] || [[ "$name" == \#* ]]; then continue; fi
  if [ "$appearance" != "$current" ]; then
    xcrun simctl ui "$UDID" appearance "$appearance"
    current="$appearance"
  fi
  args=(-PocketADMScreenshotTab "$tab")
  if [ -n "${route:-}" ]; then args+=(-PocketADMScreenshotRoute "$route"); fi
  xcrun simctl launch --terminate-running-process "$UDID" "$BUNDLE" "${args[@]}" >/dev/null
  sleep "${SHOTS_WAIT:-12}"
  xcrun simctl io "$UDID" screenshot "$OUT/$name.png" >/dev/null 2>&1
  echo "✓ $name"
done 3< "$LIST"
