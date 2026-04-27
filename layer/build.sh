#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
rm -rf python/

# Build everything inside ONE Docker invocation so ownership stays consistent.
# - `pip install` runs as root inside the container (root-owned files appear
#   in python/ on the host — fine because rm -rf can still unlink them next run).
# - `cp` of ../shared also runs as root inside the same container — bypasses
#   the "host can't write into root-owned python/" failure that bit GHA before.
# - Two volume mounts: $(pwd)→/layer (writable) and $(pwd)/..→/repo (read-only)
#   so we can read shared/ from the parent dir without escaping the mount.
#
# Why shared/ is bundled at all: every Lambda that does `from shared.*`
# (ws_connect, ws_disconnect, ws_route, score_batch, etc.) needs this on
# the Lambda runtime PYTHONPATH. Layer mounts at /opt/python at runtime.
docker run --rm \
  -v "$(pwd)":/layer \
  -v "$(pwd)/..":/repo:ro \
  -w /layer \
  --platform linux/amd64 \
  public.ecr.aws/sam/build-python3.11:latest \
  bash -c "pip install -r requirements.txt -t python/ --quiet && \
           cp -r /repo/shared python/shared && \
           find python/shared -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true"

echo "Layer built: $(du -sh python/ | cut -f1)"
echo "shared/ files in layer:"
ls python/shared/
