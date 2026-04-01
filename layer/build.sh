#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
rm -rf python/

# Build inside Docker to get Linux x86_64 binaries (pydantic_core etc.)
docker run --rm -v "$(pwd)":/layer -w /layer \
  --platform linux/amd64 \
  public.ecr.aws/sam/build-python3.11:latest \
  pip install -r requirements.txt -t python/ --quiet

echo "Layer built: $(du -sh python/ | cut -f1)"
