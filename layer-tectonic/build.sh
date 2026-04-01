#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
rm -rf bin/
mkdir -p bin/
# Download static musl-linked tectonic binary (same as Dockerfile.lambda stage 1)
wget -qO /tmp/tectonic.tar.gz https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.15.0/tectonic-0.15.0-x86_64-unknown-linux-musl.tar.gz
tar xzf /tmp/tectonic.tar.gz -C bin/
chmod +x bin/tectonic
echo "Tectonic layer built: $(ls -lh bin/tectonic)"
