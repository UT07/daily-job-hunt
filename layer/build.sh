#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
rm -rf python/

# NO first-party code ships in this layer. FIRST_PARTY is kept, empty, so the
# tests and scripts/preflight_deploy.sh can assert it STAYS empty.
#
# shared/ used to be copied in here. It moved out on 2026-10-08 for the same
# reason agents/ did on 2026-09-23 (below): a layer-only change does not
# change the function artifact. Measured with `sam build CompileLatexFunction`
# before and after a one-line edit to shared/tex_utils.py, hashed with SAM's
# own dir_checksum (the md5 that becomes the packaged CodeUri S3 key): it was
# 13ef36ce... both times. PRs #180, #184, #191, #200 and #202 changed only
# shared/, so their deploys could leave the `live` alias on old code.
#
# shared/ now reaches zip Lambdas through the symlinks
# lambdas/pipeline/shared and lambdas/scrapers/shared (-> ../../shared).
# `sam build` copies a symlinked directory as real files, so shared/ is part
# of every function's artifact and hash: the same edit now moves it
# 018c4ddc... -> 48ff37b4..., and an unedited rebuild reproduces 018c4ddc....
# Regression tests: test_package_not_in_layer_build_script and
# test_zip_functions_importing_shared_carry_it_in_their_codeuri in
# tests/unit/test_deploy_path_parity.py.
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
FIRST_PARTY=""

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
# Two pip flags, both added 2026-09-30 after the layer build failed on
# "ERROR: Failed building wheel for Pillow" when pdfplumber joined the layer.
#
#   --upgrade pip      the image ships an older pip, and Pillow 12.x publishes
#                      its cp311 x86_64 wheels under compound PEP 600 tags
#                      (manylinux2014_x86_64.manylinux_2_17_x86_64). A pip that
#                      cannot match the tag silently falls back to the sdist and
#                      tries to COMPILE Pillow, which fails in a container with
#                      no image-library headers.
#
#   --only-binary=:all:  makes that fallback impossible. If no wheel matches,
#                      the build fails immediately naming the package, instead
#                      of emitting two hundred lines of compiler output for a
#                      dependency nobody deliberately added. A layer build that
#                      compiles from source is slow, non-reproducible, and a
#                      sign something is wrong.
#
docker run --rm \
  -v "$(pwd)":/layer \
  -v "$(pwd)/..":/repo:ro \
  -w /layer \
  -e FIRST_PARTY="$FIRST_PARTY" \
  --platform linux/amd64 \
  public.ecr.aws/sam/build-python3.11:latest \
  bash -c 'pip install --quiet --upgrade pip && \
           pip install -r requirements.txt -t python/ --quiet --only-binary=:all: && \
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
