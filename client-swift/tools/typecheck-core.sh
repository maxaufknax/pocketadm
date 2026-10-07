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

# The SwiftUI half cannot be type-checked here, but it can be *parsed*: a
# stray brace or a broken expression fails here in seconds instead of after a
# ten-minute Codemagic build.
docker run --rm -v "$PWD/App:/app:ro" "$IMAGE" bash -c '
  status=0
  for f in $(find /app -name "*.swift"); do
    swiftc -parse -swift-version 5 "$f" || status=1
  done
  exit $status'

echo "✓ Every Swift file parses"
