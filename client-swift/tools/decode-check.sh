#!/usr/bin/env bash
# Decodes every captured server response into the app's models with a real Swift
# compiler, and exercises the hand-written chat protocol. See the header of
# tools/decode-check.swift for why this is the closest thing to a test suite
# that can run without a Mac.
#
#   ./tools/decode-check.sh
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${SWIFT_IMAGE:-swift:6.2-noble}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
./tools/linux-src.sh "$tmp"
# Top-level statements are only legal in a file called main.swift.
cp tools/decode-check.swift "$tmp/main.swift"

docker run --rm -v "$tmp:/src" -v "$PWD/tools/fixtures:/fixtures" -w /src "$IMAGE" bash -c '
  set -e
  swiftc -swift-version 5 -O -o /tmp/decode-check /src/*.swift
  /tmp/decode-check /fixtures
'
