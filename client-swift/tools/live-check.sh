#!/usr/bin/env bash
# Drives the real APIClient against a real PocketADM server. Read-only.
#
#   ./tools/live-check.sh http://127.0.0.1:8091 demo
#
# Uses the host network so 127.0.0.1 means the same thing inside the container
# as it does outside it.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${SWIFT_IMAGE:-swift:6.2-noble}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
./tools/linux-src.sh "$tmp"
cp tools/live-check.swift "$tmp/main.swift"

docker run --rm --network host -v "$tmp:/src" -w /src "$IMAGE" bash -c "
  set -e
  swiftc -swift-version 5 -o /tmp/live-check /src/*.swift
  /tmp/live-check '$1' '$2' '${3:-}'
"
