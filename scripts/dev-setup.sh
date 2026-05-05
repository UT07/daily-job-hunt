#!/usr/bin/env bash
# Idempotent dev environment setup. Safe to run from the main repo OR a worktree.
#
# What it does:
#   1. Ensures the main repo's .venv exists (creates it if missing).
#   2. Installs requirements.txt + requirements-dev.txt into that venv.
#   3. Tells you the one-liner to activate the pre-commit hook.
#
# Why hook activation is opt-in:
#   The repo currently has format drift across many files (the hook was never
#   active, so ruff-format was never enforced). Activating the hook today
#   would block any commit until that drift is fixed. To enable safely, run
#   the printed `pre-commit run --all-files` first, commit the cleanup as
#   its own PR, then run `pre-commit install --install-hooks`.
#
# Why this matters:
#   Worktrees inherit core.hooksPath but NOT the venv. Without pre-commit in
#   the active venv, `git commit` silently skips the hook -- the failure mode
#   that leaked an unused-pytest-import to CI in commit 5861e56.
set -euo pipefail

# Find the main repo root (the worktree this script lives in may be nested).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GIT_COMMON_DIR="$(git -C "$REPO_ROOT" rev-parse --git-common-dir)"
MAIN_ROOT="$(cd "$GIT_COMMON_DIR/.." && pwd)"
VENV_DIR="$MAIN_ROOT/.venv"

if [ ! -d "$VENV_DIR" ]; then
  echo "Creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

echo "Upgrading pip + installing deps..."
pip install --upgrade pip --quiet
pip install -r "$MAIN_ROOT/requirements.txt" --quiet
pip install -r "$MAIN_ROOT/requirements-dev.txt" --quiet

echo
echo "Done. To activate the venv in your shell:"
echo "  source $VENV_DIR/bin/activate"
echo
echo "To enable the pre-commit hook (run from $MAIN_ROOT):"
echo "  pre-commit run --all-files     # see/fix existing drift"
echo "  pre-commit install --install-hooks   # then activate"
