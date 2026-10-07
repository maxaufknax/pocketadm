#!/usr/bin/env bash
# Shared by typecheck-core.sh and decode-check.sh: assembles a Linux-buildable
# copy of the platform-independent sources into $1.
#
# Two differences between iOS's Foundation and swift-corelibs-foundation have to
# be papered over, and both are papered over *here* rather than in the shipped
# sources — the app must not carry portability scaffolding for its own test rig.
#
#   1. URLSession lives in FoundationNetworking on Linux.
#   2. URLSessionConfiguration.waitsForConnectivity is settable on Darwin and
#      get-only on Linux. Its Darwin default is already false, so dropping the
#      line changes nothing about what is being verified.
set -euo pipefail
dest="$1"
src="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p "$dest"
cat > "$dest/prelude.swift" <<'PRELUDE'
#if canImport(FoundationNetworking)
@_exported import FoundationNetworking
#endif
PRELUDE

for file in Models Models+Ops ChatProtocol Formatting ServerURL APIClient APIClient+Ops; do
  sed 's|^\( *\)cfg.waitsForConnectivity = false|\1// (dropped for the Linux harness — Darwin default is already false)|' \
    "$src/App/Core/$file.swift" > "$dest/$file.swift"
done
