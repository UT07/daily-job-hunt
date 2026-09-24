#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
rm -rf python/

# First-party packages every zip-based pipeline Lambda needs on its Python
# path. Layer mounts at /opt/python at runtime, so anything imported by a
# zip Lambda (e.g. `from shared.*`) must be copied in here — it is NOT
# enough for it to exist at the repo root or in Dockerfile.lambda (that only
# covers the container-image Lambda). See tests/unit/test_deploy_path_parity.py
# for the parity check across both deploy paths.
#
# `agents/` deliberately does NOT belong here. It moved to
# lambdas/pipeline/agents/ (2026-09-23) specifically so it ships inside the
# pipeline functions' own CodeUri, which SAM hashes from real packaged file
# content. This layer's logical ID, by contrast, is a content hash SAM's
# transform computes from the resolved template at deploy time, AFTER
# AutoPublishAlias has already decided whether to publish a new version —
# so a layer-only content change (e.g. editing agents/ without touching
# template.yaml) never bumped the function's published version, and the
# `live` alias silently kept invoking stale code against a stale layer.
# First-party APPLICATION code (changes every commit) belongs in the
# function's CodeUri; only genuine third-party DEPENDENCIES belong here.
# See test_agents_not_in_layer_build_first_party_list in
# tests/unit/test_deploy_path_parity.py for the regression test.
#
# The platform-upgrade plan (docs/superpowers/plans/2026-09-22-ey-genai-
# platform-upgrade.md) names guardrails/, retrieval/, evals/ and
# mcp_server/ as future additive packages alongside agents/ — do NOT add
# any of them here when they land. They are first-party application code
# like agents/ was, so they belong inside whichever function's CodeUri
# actually imports them (see lambdas/pipeline/agents/ for the pattern),
# not in this layer.
FIRST_PARTY="shared"

# Build everything inside ONE Docker invocation so ownership stays consistent.
# - `pip install` runs as root inside the container (root-owned files appear
#   in python/ on the host — fine because rm -rf can still unlink them next run).
# - `cp` of each FIRST_PARTY package also runs as root inside the same
#   container — bypasses the "host can't write into root-owned python/"
#   failure that bit GHA before.
# - Two volume mounts: $(pwd)→/layer (writable) and $(pwd)/..→/repo (read-only)
#   so we can read first-party packages from the parent dir without escaping
#   the mount.
# - FIRST_PARTY is passed in via -e and expanded inside the container (single-
#   quoted bash -c body) rather than on the host, so the list lives in exactly
#   one place above.
docker run --rm \
  -v "$(pwd)":/layer \
  -v "$(pwd)/..":/repo:ro \
  -w /layer \
  -e FIRST_PARTY="$FIRST_PARTY" \
  --platform linux/amd64 \
  public.ecr.aws/sam/build-python3.11:latest \
  bash -c 'pip install -r requirements.txt -t python/ --quiet && \
           for pkg in $FIRST_PARTY; do \
             cp -r "/repo/$pkg" "python/$pkg" && \
             find "python/$pkg" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true; \
           done'

echo "Layer built: $(du -sh python/ | cut -f1)"
echo "first-party packages in layer:"
for pkg in $FIRST_PARTY; do
  echo "  $pkg/:"
  ls "python/$pkg/"
done
