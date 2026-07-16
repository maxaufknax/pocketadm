#!/usr/bin/env bash
# Applies PocketADM's native iOS settings to the Capacitor-generated Info.plist.
# Runs on the Codemagic macOS VM after `cap add/sync ios`, so it works even when
# the ios/ project is regenerated fresh each build (no Mac needed locally).
set -euo pipefail

PLIST="ios/App/App/Info.plist"
[ -f "$PLIST" ] || { echo "!! $PLIST not found"; exit 1; }

pb() { /usr/libexec/PlistBuddy -c "$1" "$PLIST" 2>/dev/null || true; }
str() { pb "Add :$1 string $2"; pb "Set :$1 $2"; }
bool() { pb "Add :$1 bool $2"; pb "Set :$1 $2"; }

# Display name + skip the export-compliance prompt (standard HTTPS only)
str  CFBundleDisplayName "PocketADM"
bool ITSAppUsesNonExemptEncryption false

# Permission strings (Apple rejects missing usage descriptions)
str NSCameraUsageDescription \
  "PocketADM uses the camera to scan pairing QR codes when you connect a server."
str NSLocalNetworkUsageDescription \
  "PocketADM connects to servers you add on your local network."
str NSPhotoLibraryAddUsageDescription \
  "PocketADM can save QR codes and exported backups to your photos."

# App Transport Security: PocketADM is a self-hosting admin client, so it must
# reach servers the user specifies — local IPs, .local hosts, and custom or
# self-signed domains that may not present a public CA cert. (Justify in the
# App Review notes: "connects only to servers the user explicitly adds.")
pb "Add :NSAppTransportSecurity dict"
bool NSAppTransportSecurity:NSAllowsArbitraryLoads true
bool NSAppTransportSecurity:NSAllowsLocalNetworking true

# pocketadm:// URL scheme for pairing / handoff deep links
pb "Add :CFBundleURLTypes array"
pb "Add :CFBundleURLTypes:0 dict"
pb "Add :CFBundleURLTypes:0:CFBundleURLName string de.maxaufknax.pocketadm"
pb "Add :CFBundleURLTypes:0:CFBundleURLSchemes array"
pb "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string pocketadm"

# Portrait only. Fine for an iPhone-only app (see device family below).
pb "Delete :UISupportedInterfaceOrientations"
pb "Add :UISupportedInterfaceOrientations array"
pb "Add :UISupportedInterfaceOrientations:0 string UIInterfaceOrientationPortrait"

echo "✓ Info.plist configured for PocketADM"

# ---------------------------------------------------------------- iPhone only
# Capacitor generates a universal app (TARGETED_DEVICE_FAMILY = "1,2"). That
# made App Store Connect demand iPad screenshots (APP_IPAD_PRO_3GEN_129) for an
# app that is portrait-locked and has never been laid out or tested on an iPad
# -- which is also a classic rejection. v1.0 ships iPhone-only; it still runs on
# iPad in compatibility mode. Revisit with real iPad layouts + landscape.
#
# This is a build setting, not an Info.plist key, so it has to be patched in the
# pbxproj. Do NOT let it fail quietly: if Capacitor ever changes the generated
# value, a silent no-op would put the iPad requirement back without telling us.
PROJ="ios/App/App.xcodeproj/project.pbxproj"
[ -f "$PROJ" ] || { echo "!! $PROJ not found"; exit 1; }
sed -i '' -E 's/TARGETED_DEVICE_FAMILY = "?1,2"?;/TARGETED_DEVICE_FAMILY = "1";/g' "$PROJ"
if grep -q 'TARGETED_DEVICE_FAMILY = "\?1,2"\?;' "$PROJ"; then
  echo "!! TARGETED_DEVICE_FAMILY is still universal — iPad screenshots would be required"
  exit 1
fi
if ! grep -q 'TARGETED_DEVICE_FAMILY = "1";' "$PROJ"; then
  echo "!! no TARGETED_DEVICE_FAMILY = \"1\" in $PROJ — Capacitor changed its template?"
  grep -n "TARGETED_DEVICE_FAMILY" "$PROJ" || true
  exit 1
fi
echo "✓ device family pinned to iPhone ($(grep -c 'TARGETED_DEVICE_FAMILY = "1";' "$PROJ") build configs)"
