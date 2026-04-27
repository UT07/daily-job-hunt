#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
rm -rf python/

# 1. Build pip deps inside Docker to get Linux x86_64 binaries (pydantic_core etc.)
docker run --rm -v "$(pwd)":/layer -w /layer \
  --platform linux/amd64 \
  public.ecr.aws/sam/build-python3.11:latest \
  pip install -r requirements.txt -t python/ --quiet

# 2. Bundle the repo's `shared/` package into the layer.
# Every Lambda that does `from shared.*` (ws_connect, ws_disconnect, ws_route,
# score_batch, etc.) relies on this being on the Lambda runtime PYTHONPATH
# (Lambda mounts the layer at /opt/python). Without this step the imports
# crash at runtime with ModuleNotFoundError — unit tests pass locally only
# because the repo root is on the local PYTHONPATH.
cp -r ../shared python/shared
# Strip local __pycache__ (host's Python version may differ from Lambda's 3.11)
find python/shared -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

echo "Layer built: $(du -sh python/ | cut -f1)"
echo "shared/ files in layer:"
ls python/shared/
