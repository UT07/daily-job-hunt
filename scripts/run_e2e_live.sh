#!/usr/bin/env bash
# Run the LIVE end-to-end browser suite (tests/e2e_live) against PRODUCTION.
#
# Configuration is loaded here, explicitly, and handed to pytest as E2E_LIVE_*
# variables (CLAUDE.md verification rule 8). The suite itself never reads .env
# and never imports app.py; without these variables it refuses to start.
#
#   SUPABASE_URL, SUPABASE_SERVICE_KEY  <- .env                 (repo root)
#   VITE_SUPABASE_ANON_KEY, VITE_API_URL <- web/.env.production
#   site URL                             <- app.py CORS allow_origins (the one
#                                           origin the production API accepts)
#   S3 bucket                            <- app.py's S3_BUCKET default
#
# Any variable already set in the environment wins, so CI or another
# environment can supply its own. Extra arguments go to pytest, e.g.
#   scripts/run_e2e_live.sh -k dashboard
#   E2E_LIVE_HEADED=1 scripts/run_e2e_live.sh
#   E2E_LIVE_FIND_CONTACTS=1 scripts/run_e2e_live.sh      # opt-in, costs money
#
# Cost per default run: ~25-35 min wall clock; one single-job Step Functions
# execution (tailor + council + compile), ~6 scoring calls, 3 one-shot LLM
# calls (research, interview prep, email), 1 suggestions call, 3 LaTeX
# compiles. No scrapers, no Apify, no email.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

die() { printf '\033[31mrun_e2e_live: %s\033[0m\n' "$*" >&2; exit 2; }

# A git worktree has no .env or web/.env.production of its own (both are
# gitignored); fall back to the main checkout's copies and say which was used.
MAIN_ROOT="$(cd "$(git rev-parse --path-format=absolute --git-common-dir)/.." && pwd)"
pick() {
  local rel="$1"
  if [ -f "$ROOT/$rel" ]; then echo "$ROOT/$rel"; elif [ -f "$MAIN_ROOT/$rel" ]; then echo "$MAIN_ROOT/$rel"; fi
}
ENV_FILE="${E2E_LIVE_ENV_FILE:-$(pick .env)}"
WEB_ENV_FILE="${E2E_LIVE_WEB_ENV_FILE:-$(pick web/.env.production)}"
[ -n "$ENV_FILE" ] && [ -f "$ENV_FILE" ] || die ".env not found (set E2E_LIVE_ENV_FILE)"
[ -n "$WEB_ENV_FILE" ] && [ -f "$WEB_ENV_FILE" ] || die "web/.env.production not found (set E2E_LIVE_WEB_ENV_FILE)"

# KEY=value lookup without `source`: values are data, never shell.
val() { grep -E "^$1=" "$2" | tail -1 | cut -d= -f2- | sed -E 's/^["'\'']//; s/["'\'']$//'; }

export E2E_LIVE_SUPABASE_URL="${E2E_LIVE_SUPABASE_URL:-$(val SUPABASE_URL "$ENV_FILE")}"
export E2E_LIVE_SUPABASE_SERVICE_KEY="${E2E_LIVE_SUPABASE_SERVICE_KEY:-$(val SUPABASE_SERVICE_KEY "$ENV_FILE")}"
export E2E_LIVE_SUPABASE_ANON_KEY="${E2E_LIVE_SUPABASE_ANON_KEY:-$(val VITE_SUPABASE_ANON_KEY "$WEB_ENV_FILE")}"
export E2E_LIVE_API_URL="${E2E_LIVE_API_URL:-$(val VITE_API_URL "$WEB_ENV_FILE")}"
export E2E_LIVE_SITE_URL="${E2E_LIVE_SITE_URL:-$(grep -oE 'allow_origins=\["https://[^"]+"' app.py | head -1 | grep -oE 'https://[^"]+')}"
export E2E_LIVE_S3_BUCKET="${E2E_LIVE_S3_BUCKET:-$(grep -oE 'os.environ.get\("S3_BUCKET_NAME", "[^"]+"\)' app.py | head -1 | grep -oE '"[a-z0-9.-]+"\)$' | tr -d '")')}"

web_supabase="$(val VITE_SUPABASE_URL "$WEB_ENV_FILE")"
[ "$web_supabase" = "$E2E_LIVE_SUPABASE_URL" ] \
  || die "the frontend is built against a different Supabase project than .env's SUPABASE_URL"

for v in E2E_LIVE_SITE_URL E2E_LIVE_API_URL E2E_LIVE_SUPABASE_URL E2E_LIVE_SUPABASE_ANON_KEY \
         E2E_LIVE_SUPABASE_SERVICE_KEY E2E_LIVE_S3_BUCKET; do
  [ -n "${!v:-}" ] || die "could not determine $v"
done

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$MAIN_ROOT/.venv/bin/python"
[ -x "$PY" ] || die "no .venv python found"

printf 'e2e_live: site=%s api=%s supabase=%s bucket=%s\n' \
  "$E2E_LIVE_SITE_URL" "${E2E_LIVE_API_URL%%.*}..." "${E2E_LIVE_SUPABASE_URL%%.*}..." "$E2E_LIVE_S3_BUCKET"
printf 'e2e_live: config from %s and %s\n' "$ENV_FILE" "$WEB_ENV_FILE"

exec "$PY" -m pytest tests/e2e_live -o python_files='live_*.py' -p no:cacheprovider -rA "$@"
