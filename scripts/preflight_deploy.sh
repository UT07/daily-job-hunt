#!/usr/bin/env bash
# Preflight gate: proves the deployed Lambda artifact can actually IMPORT
# before anyone runs `sam deploy`.
#
# Why this exists (2026-09-23 COUNCIL_ENGINE=langgraph incident):
#   The alias was on the right version, the layer (v65) genuinely contained
#   agents/, shared/, langgraph and langchain_core -- every static presence
#   check available at the time said the deploy was fine. Production still
#   ModuleNotFoundError'd, because agents/providers.py and agents/nodes.py
#   imported `lambdas.pipeline.ai_helper` directly. That resolves fine from
#   the repo root (pytest, local dev) and in the container-image Lambda, but
#   NOT in a zip-based pipeline Lambda: template.yaml gives those functions
#   `CodeUri: lambdas/pipeline/`, which SAM/CFN FLATTENS into /var/task, so
#   there is no `lambdas` package there at all -- just a flat ai_helper.py.
#   All 872 tests passed. Nothing had ever actually tried to import the
#   artifact the way the deployed Lambda imports it.
#
# What this script does that a local `python -c "import agents.graph"`
# cannot: runs that import inside a linux/amd64 container matching the
# Lambda runtime. THE CONTAINER IS NOT OPTIONAL. The shared layer ships a
# native extension compiled for manylinux x86_64
# (_pydantic_core.cpython-311-x86_64-linux-gnu.so, a transitive dependency
# of langgraph/langchain-core via pydantic). A macOS host -- Apple Silicon
# doubly so -- cannot load that .so. A bare-host "simulation" of this same
# import either errors on the wrong thing or never gets far enough to
# exercise the actual bug. That is exactly how the flat-import bug this
# script is named for slipped past manual verification the first time.
#
# Usage (from anywhere -- the script cd's to the repo root itself):
#   bash scripts/preflight_deploy.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

fail() {
  # $1 = human message, $2 = which check failed (for grep-ability in CI logs)
  echo "" >&2
  echo "PREFLIGHT FAILED: ${1}" >&2
  echo "  (failed check: ${2})" >&2
  exit 1
}

echo "=== preflight_deploy: ${REPO_ROOT} ==="
echo ""

# ---------------------------------------------------------------------------
# Check 1/4 -- layer/python/ exists at all (i.e. layer/build.sh has been run)
# ---------------------------------------------------------------------------
echo "[1/4] layer/python/ exists..."
if [ ! -d "layer/python" ]; then
  fail "layer/python/ does not exist -- run ./layer/build.sh first" "layer-built"
fi
echo "      ok"
echo ""

# ---------------------------------------------------------------------------
# Check 2/4 -- every first-party package layer/build.sh copies is actually
# sitting in layer/python/. Parsed straight out of build.sh's own
# FIRST_PARTY="pkg1 pkg2" assignment (anchored to start-of-line so it can't
# also match the later `-e FIRST_PARTY="$FIRST_PARTY"` docker arg further
# down that same file) so this can never silently drift from what build.sh
# actually copies.
# ---------------------------------------------------------------------------
echo "[2/4] first-party packages present in layer/python/..."
FIRST_PARTY_LINE="$(grep -m1 -oE '^FIRST_PARTY="[^"]*"' layer/build.sh || true)"
if [ -z "$FIRST_PARTY_LINE" ]; then
  fail "layer/build.sh has no top-level FIRST_PARTY=\"...\" assignment to parse" "first-party-parse"
fi
FIRST_PARTY_PKGS="${FIRST_PARTY_LINE#FIRST_PARTY=\"}"
FIRST_PARTY_PKGS="${FIRST_PARTY_PKGS%\"}"
if [ -z "$FIRST_PARTY_PKGS" ]; then
  fail "parsed an empty package list out of layer/build.sh's FIRST_PARTY assignment" "first-party-parse"
fi
for pkg in $FIRST_PARTY_PKGS; do
  if [ ! -d "layer/python/${pkg}" ]; then
    fail "layer/python/${pkg}/ is missing (layer/build.sh's FIRST_PARTY list expects it) -- rebuild with ./layer/build.sh" "first-party-present:${pkg}"
  fi
  echo "      ${pkg}/ ok"
done
echo ""

# ---------------------------------------------------------------------------
# Check 3/4 -- langgraph + langchain_core present in layer/python/.
#
# langgraph ships as a PEP 420 implicit NAMESPACE package: it has NO
# __init__.py anywhere, only subpackages. Checking for langgraph/__init__.py
# would report it missing EVEN WHEN THE INSTALL IS CORRECT -- a false
# failure. Check for the directory only, same as every other package here.
# (langchain_core, for contrast, is a normal package and does ship an
# __init__.py -- but there is no need to special-case that; the directory
# check below is sufficient for both.)
# ---------------------------------------------------------------------------
echo "[3/4] langgraph + langchain_core present in layer/python/..."
for pkg in langgraph langchain_core; do
  if [ ! -d "layer/python/${pkg}" ]; then
    fail "layer/python/${pkg}/ is missing -- rebuild with ./layer/build.sh (check langgraph/langchain-core are in layer/requirements.txt)" "langgraph-deps-present:${pkg}"
  fi
  echo "      ${pkg}/ ok"
done
echo ""

# ---------------------------------------------------------------------------
# Check 4/4 -- THE check. Actually import the deployed artifact's entry
# points inside a linux/amd64 container matching the Lambda runtime, with
# PYTHONPATH built in the same order the Lambda runtime builds sys.path:
# layer first, task second.
#
#   /repo/layer/python     <-> /opt/python   (the shared layer)
#   /repo/lambdas/pipeline <-> /var/task     (CodeUri flattens this dir here)
#
# public.ecr.aws/sam/build-python3.11 is the same family of image SAM itself
# builds with, so this mirrors the actual Lambda Python 3.11 runtime closely
# enough to load the same native extensions the real one would -- which is
# the entire reason this has to run in Docker instead of on the host.
# ---------------------------------------------------------------------------
echo "[4/4] importing the deployed artifact inside linux/amd64 (may take a moment)..."

if ! command -v docker >/dev/null 2>&1; then
  fail "docker is not installed / not on PATH -- this check cannot be skipped (see the comments at the top of this script for why)" "docker-available"
fi
if ! docker info >/dev/null 2>&1; then
  fail "docker daemon is not reachable -- is Docker Desktop running?" "docker-daemon"
fi

# Mirrors the real Lambda import chain: ai_helper.council_complete() lazily
# imports agents.graph only when COUNCIL_ENGINE=langgraph, so importing
# ai_helper ALONE (as every pipeline Lambda already does today) would not
# have caught this bug -- agents.graph has to be imported explicitly too.
#
# `-v "$PWD":/repo:ro` mounts the WHOLE repo, not just the two flattened
# pieces -- which means /repo itself still has a real, importable `lambdas`
# package sitting right next to layer/ and lambdas/pipeline/. `python3 -c`
# normally prepends the current directory to sys.path, and `-w /repo` makes
# that current directory /repo, so WITHOUT the `-P` flag below this probe
# would let `from lambdas.pipeline.ai_helper import ...` resolve via that
# accidental cwd entry -- passing even against the ORIGINAL BUGGY code, and
# proving nothing. `-P` (PYTHONSAFEPATH, Python 3.11+) disables exactly that
# automatic cwd/script-dir prepend while leaving the explicit PYTHONPATH
# entries below untouched, so the only way anything resolves is through
# /repo/layer/python and /repo/lambdas/pipeline -- the same two entries, in
# the same order, that the deployed Lambda actually gets. Verified by hand
# against the pre-fix code: without -P this probe prints
# "BAD-IMPORT-SUCCEEDED-VIA-CWD"; with -P it reproduces production's exact
# `ModuleNotFoundError: No module named 'lambdas'`.
IMPORT_PROBE='
import sys
print("[container] sys.path:", sys.path)
print("[container] importing ai_helper ...")
import ai_helper
print("[container] importing agents.graph ...")
import agents.graph
print("[container] calling ai_helper._council_engine() ...")
engine = ai_helper._council_engine()
print(f"[container] ai_helper._council_engine() -> {engine!r}")
print("[container] OK -- deployed artifact imports cleanly end to end")
'

set +e
DOCKER_OUTPUT="$(docker run --rm --platform linux/amd64 \
  -v "$PWD":/repo:ro \
  -w /repo \
  -e PYTHONPATH=/repo/layer/python:/repo/lambdas/pipeline \
  public.ecr.aws/sam/build-python3.11:latest \
  python3 -P -c "$IMPORT_PROBE" 2>&1)"
DOCKER_EXIT=$?
set -e

echo "$DOCKER_OUTPUT" | sed 's/^/      /'

if [ "$DOCKER_EXIT" -ne 0 ]; then
  fail "the container could not import ai_helper / agents.graph the way the deployed Lambda would -- this is exactly the ModuleNotFoundError class of bug this gate exists to catch (full traceback printed above)" "container-import"
fi
echo ""

echo "=== preflight_deploy: ALL CHECKS PASSED ==="
