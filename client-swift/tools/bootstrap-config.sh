#!/usr/bin/env bash
# Creates the generated xcconfig that project.yml expects, so `xcodegen
# generate` works on a checkout that has never been through CI.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p Config
cat > Config/Build.xcconfig <<EOF
CURRENT_PROJECT_VERSION = ${1:-1}
EOF
echo "wrote Config/Build.xcconfig"
