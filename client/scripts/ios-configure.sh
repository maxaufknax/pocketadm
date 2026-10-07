#!/usr/bin/env bash
# Applies PocketADM's native iOS settings to the Capacitor-generated Info.plist
# and project.pbxproj. Runs on the Codemagic macOS VM after `cap add/sync ios`,
# so it works even when the ios/ project is regenerated fresh each build (no Mac
# needed locally).
#
# Every write is read back and asserted at the end. PlistBuddy reports failure
# on stderr and by exit status, both of which are easy to lose in a pipeline —
# and the keys written here are not cosmetic: without NSCameraUsageDescription
# iOS refuses the camera outright, which App Review reports as "no purpose
# string permission modal was prompted". A build that cannot prove it wrote
# them is worth failing.
set -euo pipefail

PLIST="ios/App/App/Info.plist"
[ -f "$PLIST" ] || { echo "!! $PLIST not found"; exit 1; }

pb() { /usr/libexec/PlistBuddy -c "$1" "$PLIST" 2>/dev/null || true; }
# `Add` fails when the key already exists and `Set` fails when it does not, so
# both run and neither is trusted — the verification pass below is the check.
bool() { pb "Add :$1 bool $2"; pb "Set :$1 $2"; }

# Strings go through plutil, not PlistBuddy: PlistBuddy takes one command
# *string* and has to re-parse the value out of it, so a value with spaces or
# punctuation depends on that parser. plutil takes the value as its own argv
# element, which cannot be misread. -replace and -insert cover "key exists"
# and "key doesn't" between them, in either order depending on macOS version.
str() {
  plutil -replace "$1" -string "$2" "$PLIST" 2>/dev/null \
    || plutil -insert "$1" -string "$2" "$PLIST"
}

read_key() { /usr/libexec/PlistBuddy -c "Print :$1" "$PLIST" 2>/dev/null || true; }

FAILED=0
expect() {   # expect <key> <expected-value> — exact match
  local got; got="$(read_key "$1")"
  if [ "$got" != "$2" ]; then
    echo "!! Info.plist :$1 is '$got', expected '$2'"; FAILED=1
  fi
}
expect_nonempty() {   # expect_nonempty <key> — present and not blank
  local got; got="$(read_key "$1")"
  if [ -z "$got" ]; then echo "!! Info.plist :$1 is missing or empty"; FAILED=1; fi
}

# Display name + skip the export-compliance prompt (standard HTTPS only)
DISPLAY_NAME="PocketADM"
str  CFBundleDisplayName "$DISPLAY_NAME"
bool ITSAppUsesNonExemptEncryption false

# Permission strings. iOS kills the process the moment a privacy-sensitive API
# is touched without one, so a missing string here is not a warning — it is a
# crash on the reviewer's device, or (when the caller checks first) a camera
# that never opens and never explains why.
CAMERA_DESC="PocketADM uses the camera to scan pairing QR codes when you connect a server."
LOCALNET_DESC="PocketADM connects to servers you add on your local network."
PHOTOADD_DESC="PocketADM can save QR codes and exported backups to your photos."
str NSCameraUsageDescription "$CAMERA_DESC"
str NSLocalNetworkUsageDescription "$LOCALNET_DESC"
str NSPhotoLibraryAddUsageDescription "$PHOTOADD_DESC"

# App Transport Security: PocketADM is a self-hosting admin client, so it must
# reach servers the user specifies — local IPs, .local hosts, and custom or
# self-signed domains that may not present a public CA cert. (Justify in the
# App Review notes: "connects only to servers the user explicitly adds.")
pb "Add :NSAppTransportSecurity dict"
bool NSAppTransportSecurity:NSAllowsArbitraryLoads true
bool NSAppTransportSecurity:NSAllowsLocalNetworking true

# Minimum iOS version. Capacitor 6 generates iOS 13.0, but Apple now rejects
# uploads with a MinimumOSVersion below 15.0 (ITMS-90068). The Info.plist key
# must match the build setting, otherwise Xcode's build-setting validation
# (IPHONEOS_DEPLOYMENT_TARGET) fails the build before it even uploads.
MIN_IOS="15.0"
str MinimumOSVersion "$MIN_IOS"

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

# ------------------------------------------------------------ verification
expect CFBundleDisplayName "$DISPLAY_NAME"
expect ITSAppUsesNonExemptEncryption false
expect NSCameraUsageDescription "$CAMERA_DESC"
expect NSLocalNetworkUsageDescription "$LOCALNET_DESC"
expect NSPhotoLibraryAddUsageDescription "$PHOTOADD_DESC"
expect NSAppTransportSecurity:NSAllowsArbitraryLoads true
expect NSAppTransportSecurity:NSAllowsLocalNetworking true
expect MinimumOSVersion "$MIN_IOS"
expect CFBundleURLTypes:0:CFBundleURLSchemes:0 pocketadm
expect UISupportedInterfaceOrientations:0 UIInterfaceOrientationPortrait
expect_nonempty CFBundleIdentifier

if [ "$FAILED" -ne 0 ]; then
  echo "!! Info.plist verification failed — dumping the file for diagnosis:"
  /usr/libexec/PlistBuddy -c "Print" "$PLIST" || true
  exit 1
fi
echo "✓ Info.plist configured and verified for PocketADM"

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

# Minimum deployment target, matching the MinimumOSVersion written into
# Info.plist above. Capacitor 6 generates IPHONEOS_DEPLOYMENT_TARGET = 13.0,
# and a mismatched Info.plist either gets overwritten back to 13.0 at build
# time or fails Xcode's build-setting validation. Patch every occurrence and
# reject any leftover below our floor — silently shipping 13.0 is exactly the
# ITMS-90068 rejection this exists to prevent.
sed -i '' -E 's/IPHONEOS_DEPLOYMENT_TARGET = 1[0-4]\.0;/IPHONEOS_DEPLOYMENT_TARGET = 15.0;/g' "$PROJ"
if grep -n 'IPHONEOS_DEPLOYMENT_TARGET = 1[0-4]\.' "$PROJ"; then
  echo "!! IPHONEOS_DEPLOYMENT_TARGET still below 15.0 in $PROJ — Capacitor changed its template?"
  exit 1
fi
if ! grep -q 'IPHONEOS_DEPLOYMENT_TARGET = 15.0;' "$PROJ"; then
  echo "!! no IPHONEOS_DEPLOYMENT_TARGET = 15.0 in $PROJ — Capacitor changed its template?"
  grep -n "IPHONEOS_DEPLOYMENT_TARGET" "$PROJ" || true
  exit 1
fi
echo "✓ minimum iOS version raised to $MIN_IOS ($(grep -c 'IPHONEOS_DEPLOYMENT_TARGET = 15.0;' "$PROJ") build configs)"
