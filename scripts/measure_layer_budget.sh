#!/usr/bin/env bash
# Measure whether langgraph + langchain-core fit the remaining Lambda budget.
set -euo pipefail

PROBE=$(mktemp -d)
trap 'rm -rf "$PROBE"' EXIT

python3 -m pip install --quiet --target "$PROBE" \
  --platform manylinux2014_x86_64 --only-binary=:all: \
  --python-version 3.11 \
  langgraph langchain-core

PROBE_MB=$(du -sm "$PROBE" | cut -f1)
LAYER_MB=$(du -sm layer | cut -f1)
TECTONIC_MB=$(du -sm layer-tectonic | cut -f1)
TOTAL=$((PROBE_MB + LAYER_MB + TECTONIC_MB))

echo "langgraph+langchain-core: ${PROBE_MB}MB"
echo "layer:                    ${LAYER_MB}MB"
echo "layer-tectonic:           ${TECTONIC_MB}MB"
echo "TOTAL:                    ${TOTAL}MB / 250MB"

if [ "$TOTAL" -lt 230 ]; then
  echo "VERDICT: PASS — proceed with zip layer packaging"
else
  echo "VERDICT: FAIL — move the council into the container-image Lambda"
  exit 1
fi
