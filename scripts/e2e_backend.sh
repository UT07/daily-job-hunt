#!/usr/bin/env bash
#
# Start app.py locally for the live E2E project.
#
# Playwright's `webServer` cannot source a .env for you, and app.py only
# auto-loads a .env sitting next to itself -- which a git worktree does not
# have. So this script exports one explicitly before handing over to uvicorn.
#
# Point NAUKRIBABA_ENV_FILE at the .env to use; it defaults to the one beside
# this repo's root. Nothing is written to it.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${NAUKRIBABA_ENV_FILE:-$REPO_ROOT/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "e2e_backend: no env file at $ENV_FILE." >&2
  echo "  Set NAUKRIBABA_ENV_FILE, or export SUPABASE_URL / SUPABASE_SERVICE_KEY /" >&2
  echo "  SUPABASE_JWT_SECRET yourself and start uvicorn by hand." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# A test run should not emit events into the real analytics project.
export POSTHOG_API_KEY=""

PYTHON="${E2E_PYTHON:-$REPO_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then PYTHON="$(command -v python3)"; fi

cd "$REPO_ROOT"
exec "$PYTHON" -m uvicorn app:app --host 127.0.0.1 --port "${E2E_API_PORT:-8000}"
