#!/usr/bin/env bash
# Type-checks the platform-independent half of the app with a real Swift
# compiler, on a machine that has no Mac.
#
# Everything listed in tools/linux-src.sh compiles against plain Foundation,
# which the swift image provides. That catches the errors a brace-counter cannot
# — wrong types, missing arguments, bad Codable conformances — before burning a
# 20-minute Codemagic run.
#
# The SwiftUI layer still needs Xcode; `ios-native-check` is the only thing that
# can prove that half.
#
#   ./tools/typecheck-core.sh
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${SWIFT_IMAGE:-swift:6.2-noble}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
./tools/linux-src.sh "$tmp"

docker run --rm -v "$tmp:/src" -w /src "$IMAGE" \
  bash -c 'swiftc -swift-version 5 -typecheck /src/*.swift' 

echo "✓ Core type-checks clean"
