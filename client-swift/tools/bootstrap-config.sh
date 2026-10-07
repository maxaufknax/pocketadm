#!/usr/bin/env bash
# Creates the generated xcconfig that project.yml expects, so `xcodegen
# generate` works on a checkout that has never been through CI.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p Config
# $1 = build number, $2 = bundle id (default: the App Store app)
cat > Config/Build.xcconfig <<EOF
CURRENT_PROJECT_VERSION = ${1:-1}
PRODUCT_BUNDLE_IDENTIFIER = ${2:-de.maxaufknax.pocketadm}
EOF
echo "wrote Config/Build.xcconfig"
