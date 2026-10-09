#!/usr/bin/env bash
# Run what CI runs, in CI's order, before pushing.
#
# Written on 2026-10-08 after getting the verification population wrong twice
# in one session:
#
#   * 2668 tests passed locally and CI went red on tests/contract/ — four
#     council doubles whose signature had gone stale. The local command was
#     `tests/unit tests/integration`; CI runs `tests/unit/ tests/contract/`.
#     A green run over the wrong population is CLAUDE.md #7.
#   * then lint-and-build went red on `F401 pytest imported but unused`, the
#     exact failure class CLAUDE.md's pre-commit section names, because ruff
#     was never run at all.
#
# Keep the commands below identical to .github/workflows/test.yml. If CI gains
# a job, add it here; a verification script that checks less than CI does is
# the same lie as a status that cannot fail.
set -uo pipefail
cd "$(dirname "$0")/.."

fail=0
step() {
  printf '\n\033[1m=== %s\033[0m\n' "$1"; shift
  if "$@"; then
    printf '\033[32mok\033[0m\n'
  else
    printf '\033[31mFAILED: %s\033[0m\n' "$*"; fail=1
  fi
}

PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

# lint-and-build
step "ruff (lambdas/ tests/ app.py)" $PY -m ruff check lambdas/ tests/ app.py
step "web build" bash -c 'cd web && npm run build --silent'
step "web tests" bash -c 'cd web && npx vitest run'

# unit-tests — the job that caught the stale doubles
step "pytest unit + contract" $PY -m pytest tests/unit/ tests/contract/ -q
step "pytest integration"     $PY -m pytest tests/integration/ -q
step "pytest security"        $PY -m pytest tests/security/ -q
step "pytest quality"         $PY -m pytest tests/quality/ -q

# deploy readiness: `sam build` is not `sam deploy`, and plain `sam validate`
# does not catch dependency cycles — only --lint does (cfn-lint E3004).
if command -v sam >/dev/null; then
  step "sam validate --lint" sam validate --lint
else
  printf '\n\033[33mskipped: sam not installed (CI still runs it)\033[0m\n'
fi

printf '\n'
if [ "$fail" -eq 0 ]; then
  printf '\033[32mall green\033[0m\n'
else
  printf '\033[31mSOMETHING FAILED — see above\033[0m\n'
fi
exit "$fail"
