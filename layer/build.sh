#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
rm -rf python/
pip install -r requirements.txt -t python/ --quiet
echo "Layer built: $(du -sh python/ | cut -f1)"
