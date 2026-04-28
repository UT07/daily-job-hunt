# Phase 5 — Error Tracking via Sentry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture every uncaught exception in FastAPI, Lambda handlers (browser + pipeline), and the React frontend to Sentry — tagged with `environment` (`staging`/`prod`), `release` (git SHA), `user.id` (Supabase UID), and (post-Phase-4) `request_id` — with PII (email/phone) scrubbed before send and frontend errors mapped to original source via uploaded source maps.

**Architecture:** One shared `config/sentry_config.py` exposes `init_sentry_fastapi()` (called once near the top of `app.py`) and `init_sentry_lambda()` (called once at module load in every Lambda handler). Both read `SENTRY_DSN`, `SENTRY_RELEASE`, and `STAGE` from env, set `traces_sample_rate=0.1`, and install a `before_send` hook that drops `email`/`phone`/`user_email`/`user_phone` keys plus regex-scrubs email/phone patterns from message text. Frontend mirrors via `web/src/lib/sentry.ts` (`@sentry/react` + `@sentry/vite-plugin` for source map upload). User context bound after Supabase auth on both sides; scrubber strips emails before send, leaving the UUID for attribution. `deploy.yml` injects `SENTRY_RELEASE=${{ github.sha }}` into Lambda env via SAM `--parameter-overrides` and into the Netlify Vite build env.

**Tech Stack:** Python 3.12, `sentry-sdk[fastapi]>=2.20.0` (covers FastAPI + AWS Lambda from the same package), pytest, React 19, `@sentry/react@^8.0.0`, `@sentry/vite-plugin@^2.0.0`, GitHub Actions, AWS SAM.

**Spec:** Roadmap `docs/superpowers/plans/2026-04-27-deployment-safety-roadmap.md` § Phase 5.

**Cross-phase coordination:**
- Parallel-safe with all other phases.
- Phase 1 (flags) provides `user_id`; Phase 5 uses the same `id` (Supabase `auth.users.id` UUID).
- Phase 4 (Observability) provides `request_id` via structlog. Phase 5 leaves a `# TODO(phase4): bind request_id` comment in `_common_kwargs` — Task 3 Step 3. Acceptable because Phase 4 is genuinely deferred work, not a placeholder for current task.
- Phase 3 (Staging) provides the `Stage` env var. Today single-stack, so `STAGE=prod` default is correct; staging job lands when Phase 3 ships.

**Out of scope:** Slack alert routing (email-only v1). Session replay (`replaysSessionSampleRate=0`). Performance-budget tuning.

---

## Manual Prerequisites (do these before Task 0)

One-time human actions outside the codebase. Required before any task below.

1. Sign up / log in to https://sentry.io (Developer free tier: 5k errors + 10k tx/mo).
2. Note the org slug (e.g. `naukribaba`).
3. Create two projects: `naukribaba-backend` (Python → FastAPI) and `naukribaba-frontend` (JavaScript → React). Capture both DSNs.
4. Create one auth token (Settings → Account → API → Auth Tokens) with scopes `project:releases`, `org:read`, `project:write`. Used by `@sentry/vite-plugin` and `deploy.yml`.
5. Add GitHub repository secrets: `SENTRY_DSN_BACKEND`, `SENTRY_DSN_FRONTEND`, `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT_FRONTEND` (=`naukribaba-frontend`).
6. Add Netlify env vars (Site settings → Build & deploy → Environment): `VITE_SENTRY_DSN` (= `SENTRY_DSN_FRONTEND`), `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT` (=`naukribaba-frontend`).
7. Add GitHub secrets `NETLIFY_AUTH_TOKEN` (Netlify User settings → OAuth → New access token) and `NETLIFY_SITE_ID` (Site settings → General → API ID) — used by Task 11's per-deploy release-tag step.

First deploy will fail with a clear error if any secret is missing.

---

## File Structure

```
config/
  __init__.py                       (CREATE) package marker
  sentry_config.py                  (CREATE) init_sentry_fastapi + init_sentry_lambda + scrub_pii
requirements.txt                    (MODIFY) sentry-sdk[fastapi]>=2.20.0
app.py                              (MODIFY) init_sentry_fastapi() + user-bind dep + /sentry-debug
lambdas/browser/ws_connect.py       (MODIFY) init_sentry_lambda + handler wrapper
lambdas/browser/ws_disconnect.py    (MODIFY) same
lambdas/browser/ws_route.py         (MODIFY) same
lambdas/pipeline/                   (MODIFY 21 modules — bulk pattern in Task 6;
                                     full file list there)
tests/unit/test_sentry_pii_scrubber.py   (CREATE) 5 cases
tests/unit/test_sentry_init.py           (CREATE) 3 cases
web/package.json                    (MODIFY) @sentry/react@^8.0.0, @sentry/vite-plugin@^2.0.0
web/src/lib/sentry.ts               (CREATE) initSentry + SentryErrorBoundary + setSentryUser
web/src/main.jsx                    (MODIFY) initSentry() + wrap <App /> in <SentryErrorBoundary>
web/vite.config.js                  (MODIFY) sentryVitePlugin in plugins
.github/workflows/deploy.yml        (MODIFY) SENTRY_RELEASE + sam --parameter-overrides + Netlify env step
template.yaml                       (MODIFY) SentryDsn/SentryRelease/Stage parameters → Globals.Function.Environment
layer/build.sh                      (MODIFY) bundle config/ into the layer (Task 12)
docs/superpowers/specs/2026-04-27-error-tracking-decision.md  (CREATE) ADR
```

---

## Task 0: Add the Sentry SDK to requirements

**Files:** Modify `requirements.txt`. **Estimated time:** 3 min.

- [ ] **Step 1: Append the SDK pin**

After `pdfplumber>=0.10.0`:

```
# Error tracking (Phase 5). The [fastapi] extra pulls Starlette; same SDK
# covers Lambda handlers via AwsLambdaIntegration.
sentry-sdk[fastapi]>=2.20.0
```

- [ ] **Step 2: Install + verify**

```bash
cd /Users/ut/code/naukribaba && source .venv/bin/activate && pip install -r requirements.txt
```

Expected: `Successfully installed sentry-sdk-2.X.Y` (X >= 20).

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore(deps): add sentry-sdk[fastapi]>=2.20.0 for Phase 5 error tracking"
```

---

## Task 1: PII scrubber TDD — write the failing tests

**Files:** Create `config/__init__.py`, `tests/unit/test_sentry_pii_scrubber.py`. **Estimated time:** 10 min.

- [ ] **Step 1: Create the package marker**

```bash
mkdir -p /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/config
```

`config/__init__.py`:

```python
"""Backend configuration package — Sentry, future feature flags, etc."""
```

- [ ] **Step 2: Write the test file**

`tests/unit/test_sentry_pii_scrubber.py`:

```python
"""Scrubber must drop email/phone keys at every depth + regex-scrub email/phone
patterns from message text. Allowed keys (id/request_id/level/tags) retained."""
from config.sentry_config import scrub_pii


def test_email_field_stripped_from_user_block():
    event = {"user": {"id": "u-123", "email": "alice@example.com"}, "level": "error"}
    result = scrub_pii(event, hint=None)
    assert "email" not in result["user"]
    assert result["user"]["id"] == "u-123"


def test_phone_field_stripped_from_extra_block():
    event = {"extra": {"user_phone": "+353-1-555-0123", "request_id": "req-abc"}}
    result = scrub_pii(event, hint=None)
    assert "user_phone" not in result["extra"]
    assert result["extra"]["request_id"] == "req-abc"


def test_email_in_message_text_is_redacted():
    event = {"message": "Auth failed for alice@example.com — bad token"}
    result = scrub_pii(event, hint=None)
    assert "alice@example.com" not in result["message"]
    assert "[email]" in result["message"]


def test_phone_in_message_text_is_redacted():
    event = {"message": "SMS verify failed for +353 87 555 1234 after 3 retries"}
    result = scrub_pii(event, hint=None)
    assert "555 1234" not in result["message"]
    assert "[phone]" in result["message"]


def test_allowed_keys_retained():
    event = {
        "user": {"id": "u-123"},
        "extra": {"request_id": "req-abc", "tier": "S"},
        "tags": {"environment": "prod", "release": "abc1234"},
        "level": "error",
        "message": "Tailoring failed for resume_id=r-9 (no email here)",
    }
    result = scrub_pii(event, hint=None)
    assert result["user"] == {"id": "u-123"}
    assert result["extra"] == {"request_id": "req-abc", "tier": "S"}
    assert result["tags"] == {"environment": "prod", "release": "abc1234"}
    assert result["level"] == "error"
    assert result["message"] == "Tailoring failed for resume_id=r-9 (no email here)"
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
cd /Users/ut/code/naukribaba && source .venv/bin/activate && pytest tests/unit/test_sentry_pii_scrubber.py -v
```

Expected: 5 FAILS — `ModuleNotFoundError: No module named 'config.sentry_config'`.

- [ ] **Step 4: Commit the failing tests**

```bash
git add config/__init__.py tests/unit/test_sentry_pii_scrubber.py
git commit -m "test(sentry): pin PII scrubber contract — 5 cases (failing)"
```

---

## Task 2: PII scrubber implementation

**Files:** Create `config/sentry_config.py` (scrubber only — init helpers added in Task 3). **Estimated time:** 10 min.

- [ ] **Step 1: Create `config/sentry_config.py` with the scrubber only**

```python
"""Sentry init for FastAPI + AWS Lambda + PII scrubber.

Two init functions added in Task 3 (init_sentry_fastapi, init_sentry_lambda).
Both no-op when SENTRY_DSN is unset (local dev / CI / unit tests).
"""
from __future__ import annotations

import os
import re
from typing import Any, Mapping, MutableMapping, Optional

# Conservative patterns; false-positives on long digit runs are acceptable —
# we'd rather over-scrub than leak PII.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d[\d\s\-().]{7,}\d")
_DENY_KEYS = frozenset({"email", "phone", "user_email", "user_phone"})


def _scrub_mapping(m: Optional[MutableMapping[str, Any]]) -> None:
    """Recursively drop deny-listed keys from `m` in place."""
    if not isinstance(m, MutableMapping):
        return
    for k in list(m.keys()):
        if k in _DENY_KEYS:
            m.pop(k, None)
            continue
        v = m[k]
        if isinstance(v, MutableMapping):
            _scrub_mapping(v)


def scrub_pii(event: MutableMapping[str, Any], hint: Optional[Mapping[str, Any]]) -> Optional[MutableMapping[str, Any]]:
    """Sentry before_send hook. Always returns the (modified) event; never drops."""
    _scrub_mapping(event)
    msg = event.get("message")
    if isinstance(msg, str):
        msg = _EMAIL_RE.sub("[email]", msg)
        msg = _PHONE_RE.sub("[phone]", msg)
        event["message"] = msg
    return event
```

- [ ] **Step 2: Run tests — they should now pass**

```bash
pytest tests/unit/test_sentry_pii_scrubber.py -v
```

Expected: 5 PASS.

- [ ] **Step 3: Commit**

```bash
git add config/sentry_config.py
git commit -m "feat(sentry): PII scrubber for before_send hook (5 unit tests pass)"
```

---

## Task 3: Init helpers — TDD then implement

**Files:** Create `tests/unit/test_sentry_init.py`; modify `config/sentry_config.py` (append helpers). **Estimated time:** 25 min.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_sentry_init.py`:

```python
"""init helpers must no-op without DSN, and pass traces_sample_rate=0.1 +
environment ($STAGE, default 'prod') + release ($SENTRY_RELEASE) when set."""
from unittest.mock import patch
from config.sentry_config import init_sentry_fastapi, init_sentry_lambda


def test_no_dsn_is_noop(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    with patch("config.sentry_config.sentry_sdk.init") as init:
        assert init_sentry_fastapi() is False
        assert init_sentry_lambda() is False
        init.assert_not_called()


def test_fastapi_init_passes_sample_rate_and_release(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://k@sentry.io/1")
    monkeypatch.setenv("SENTRY_RELEASE", "abc1234")
    monkeypatch.setenv("STAGE", "prod")
    with patch("config.sentry_config.sentry_sdk.init") as init:
        assert init_sentry_fastapi() is True
        kw = init.call_args.kwargs
        assert kw["dsn"] == "https://k@sentry.io/1"
        assert kw["traces_sample_rate"] == 0.1
        assert kw["release"] == "abc1234"
        assert kw["environment"] == "prod"
        assert kw["before_send"] is not None


def test_lambda_init_environment_defaults_to_prod_when_stage_unset(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://k@sentry.io/1")
    monkeypatch.setenv("SENTRY_RELEASE", "abc1234")
    monkeypatch.delenv("STAGE", raising=False)
    with patch("config.sentry_config.sentry_sdk.init") as init:
        assert init_sentry_lambda() is True
        assert init.call_args.kwargs["environment"] == "prod"
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/unit/test_sentry_init.py -v
```

Expected: 3 FAILS — `ImportError: cannot import name 'init_sentry_fastapi'`.

- [ ] **Step 3: Append the init helpers to `config/sentry_config.py`**

```python
# ---------------------------------------------------------------------------
# Init helpers — called from app.py (FastAPI) and every Lambda handler.
# ---------------------------------------------------------------------------

import sentry_sdk  # noqa: E402  (kept low so tests can patch this symbol)
from sentry_sdk.integrations.aws_lambda import AwsLambdaIntegration  # noqa: E402
from sentry_sdk.integrations.starlette import StarletteIntegration  # noqa: E402
from sentry_sdk.integrations.fastapi import FastApiIntegration  # noqa: E402


def _common_kwargs() -> dict:
    return {
        "dsn": os.environ.get("SENTRY_DSN", ""),
        "environment": os.environ.get("STAGE", "prod"),
        "release": os.environ.get("SENTRY_RELEASE", ""),
        "traces_sample_rate": 0.1,
        "before_send": scrub_pii,
        # TODO(phase4): bind structlog request_id as a Sentry tag once
        # config/observability.py exists.
    }


def init_sentry_fastapi() -> bool:
    """Init Sentry for FastAPI. Call ONCE before FastAPI() construction. No-op if SENTRY_DSN unset."""
    if not os.environ.get("SENTRY_DSN"):
        return False
    kwargs = _common_kwargs()
    kwargs["integrations"] = [
        StarletteIntegration(transaction_style="endpoint"),
        FastApiIntegration(transaction_style="endpoint"),
    ]
    sentry_sdk.init(**kwargs)
    return True


def init_sentry_lambda() -> bool:
    """Init Sentry for a Lambda handler module. Call ONCE at module top (not inside handler)."""
    if not os.environ.get("SENTRY_DSN"):
        return False
    kwargs = _common_kwargs()
    kwargs["integrations"] = [AwsLambdaIntegration(timeout_warning=True)]
    sentry_sdk.init(**kwargs)
    return True
```

- [ ] **Step 4: Run all sentry tests — verify pass**

```bash
pytest tests/unit/test_sentry_pii_scrubber.py tests/unit/test_sentry_init.py -v
```

Expected: 8 PASS (5 + 3).

- [ ] **Step 5: Commit**

```bash
git add config/sentry_config.py tests/unit/test_sentry_init.py
git commit -m "feat(sentry): init_sentry_fastapi + init_sentry_lambda helpers (3 tests pass)"
```

---

## Task 4: Wire Sentry into FastAPI (`app.py`)

**Files:** Modify `app.py`. **Estimated time:** 15 min.

- [ ] **Step 1: Early init call**

Imports end at line 47 (`from typing import Optional`). Insert directly after line 47 (above `import boto3`):

```python
# Init Sentry BEFORE FastAPI() so the SDK wraps the ASGI app on first request.
from config.sentry_config import init_sentry_fastapi  # noqa: E402

init_sentry_fastapi()
```

- [ ] **Step 2: Add the user-bind dependency**

Right after `from auth import AuthUser, get_current_user` (line 71), add:

```python
import sentry_sdk  # noqa: E402


def get_current_user_with_sentry(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """Wraps get_current_user to attach the user to the active Sentry scope.

    PII scrubber strips `email` before send (intentional); `id` stays for attribution.
    """
    sentry_sdk.set_user({"id": user.id, "email": user.email})
    return user
```

Rationale: do NOT replace `get_current_user` everywhere — that would couple Sentry into auth. Apply only to the highest-value endpoints (the apply pair). Other endpoints continue with `get_current_user`; their errors emit without `user.id` — fine for MVP.

- [ ] **Step 3: Re-route the two apply endpoints**

The two apply endpoints (around lines 2418 + 2472) each have `user: AuthUser = Depends(get_current_user)`. Change the dep on just those two lines:

```python
def apply_eligibility(job_id: str, user: AuthUser = Depends(get_current_user_with_sentry)):
```

```python
def apply_preview(job_id: str, user: AuthUser = Depends(get_current_user_with_sentry)):
```

- [ ] **Step 4: Add the temporary `/sentry-debug` endpoint**

Below `@app.get("/api/health")` (around line 348):

```python
@app.get("/sentry-debug")
def sentry_debug():
    """TEMPORARY: validates Sentry capture in prod. Removed in Task 16."""
    raise RuntimeError("sentry-debug: synthetic 500 for capture validation")
```

- [ ] **Step 5: Smoke test + run tests**

```bash
cd /Users/ut/code/naukribaba && source .venv/bin/activate
SENTRY_DSN="" uvicorn app:app --port 8000 &
sleep 2 && curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/sentry-debug
kill %1
pytest tests/unit/ tests/contract/ -v --tb=short
```

Expected: curl prints `500`; pytest all green. (Unit tests don't set `SENTRY_DSN` so init is no-op — no pollution.)

- [ ] **Step 6: Commit**

```bash
git add app.py
git commit -m "feat(sentry): init Sentry in app.py + bind user.id on apply endpoints

get_current_user_with_sentry wraps get_current_user to call set_user({id,email});
scrubber strips email before send, id remains for attribution.
/sentry-debug endpoint is temporary — removed in Task 16."
```

---

## Task 5: Wire Sentry into the 3 browser Lambda handlers

**Files:** Modify `lambdas/browser/ws_connect.py`, `lambdas/browser/ws_disconnect.py`, `lambdas/browser/ws_route.py`. **Estimated time:** 15 min.

Same pattern for all three: module-top init + explicit wrapper around the handler. The AwsLambdaIntegration already captures uncaught exceptions, but the explicit wrapper survives any future refactor that catches/re-raises inside the handler.

- [ ] **Step 1: Edit `ws_connect.py`**

After `import logging`, add:

```python
import sentry_sdk

from config.sentry_config import init_sentry_lambda

init_sentry_lambda()
```

Rename the existing `def handler(event, context):` to `def _handler(event, context):`. At the very end of the file, append:

```python
def handler(event, context):
    """Sentry-wrapped public entrypoint."""
    try:
        return _handler(event, context)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise
```

- [ ] **Step 2: Apply the same three edits to `ws_disconnect.py` and `ws_route.py`**

No behavioral differences — wrapper only adds Sentry capture.

- [ ] **Step 3: Run scoped tests**

```bash
pytest tests/unit/ tests/contract/ -v --tb=short -k "ws or browser"
```

Expected: all green. Tests that patch `lambdas.browser.ws_route.handler` now bind to the wrapper, which is the desired behavior — no test edit required.

- [ ] **Step 4: Commit**

```bash
git add lambdas/browser/ws_connect.py lambdas/browser/ws_disconnect.py lambdas/browser/ws_route.py
git commit -m "feat(sentry): wrap WS Lambda handlers with init + capture_exception"
```

---

## Task 6: Bulk-edit Sentry into the 21 pipeline Lambda handlers

**Files — 20 modules under `lambdas/pipeline/`:** `aggregate_scores.py`, `check_expiry.py`, `chunk_hashes.py`, `compile_latex.py`, `find_contacts.py`, `generate_cover_letter.py`, `load_config.py`, `merge_dedup.py`, `notify_error.py`, `parse_sections.py`, `post_score.py`, `save_job.py`, `save_metrics.py`, `score_batch.py`, `self_improve.py`, `self_improver.py`, `send_email.py`, `send_followup_reminders.py`, `send_stale_nudges.py`, `tailor_resume.py`.

(`__init__.py` and `ai_helper.py` are skipped — not handlers.)

**Estimated time:** 30 min (sequential edits with the same pattern).

- [ ] **Step 1: Representative edit — `score_batch.py`**

Currently begins:

```python
import json
import logging
import random
import statistics
import uuid
from datetime import datetime


from ai_helper import ai_complete_cached, get_supabase
from shared.apply_platform import classify_apply_platform

logger = logging.getLogger()
```

After edit:

```python
import json
import logging
import random
import statistics
import uuid
from datetime import datetime

import sentry_sdk

from ai_helper import ai_complete_cached, get_supabase
from config.sentry_config import init_sentry_lambda
from shared.apply_platform import classify_apply_platform

init_sentry_lambda()

logger = logging.getLogger()
```

Then rename the public `def handler(event, context):` to `def _handler(event, context):` and append at end of file:

```python
def handler(event, context):
    """Sentry-wrapped public entrypoint."""
    try:
        return _handler(event, context)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise
```

- [ ] **Step 2: Apply the same three changes to the remaining 19 modules**

For each module in the file list above:
1. Add `import sentry_sdk` (stdlib-style block) and `from config.sentry_config import init_sentry_lambda` (project block).
2. Add `init_sentry_lambda()` on its own line after all imports, before any `logger = ...` or `def`.
3. Rename `def handler(event, context):` → `def _handler(event, context):` and append the wrapper at end of file.

Use the `Edit` tool — surrounding imports differ per file, don't script via shell. If a module also exposes utility functions next to `handler`, only rename the one named `handler`.

- [ ] **Step 3: Verify all 20 modules import cleanly**

```bash
cd /Users/ut/code/naukribaba && source .venv/bin/activate
python -c "
import importlib, sys
sys.path.insert(0, 'lambdas/pipeline')
for mod in ['aggregate_scores','check_expiry','chunk_hashes','compile_latex','find_contacts','generate_cover_letter','load_config','merge_dedup','notify_error','parse_sections','post_score','save_job','save_metrics','score_batch','self_improve','self_improver','send_email','send_followup_reminders','send_stale_nudges','tailor_resume']:
    importlib.import_module(mod); print(f'OK {mod}')
"
```

Expected: 20 lines `OK <module>`. If any raises `ModuleNotFoundError: No module named 'config'` — the layer doesn't carry it yet; that's fixed by Task 12.

- [ ] **Step 4: Run the full test suite**

```bash
pytest tests/unit/ tests/contract/ -v --tb=short
```

Expected: all green. No test should reference `_handler`; wrapper preserves the contract.

- [ ] **Step 5: Commit**

```bash
git add lambdas/pipeline/
git commit -m "feat(sentry): wrap 21 pipeline Lambda handlers with init + capture (same pattern as Task 5)"
```

---

## Task 7: Frontend — install `@sentry/react` + `@sentry/vite-plugin`

**Files:** Modify `web/package.json`. **Estimated time:** 5 min.

- [ ] **Step 1: Add the deps**

In `web/package.json`, add `"@sentry/react": "^8.0.0"` to `dependencies` (alphabetical, before `@supabase/supabase-js`). Add `"@sentry/vite-plugin": "^2.0.0"` to `devDependencies` (alphabetical, between `@eslint/js` and `@tailwindcss/vite`).

- [ ] **Step 2: Install**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/web && npm install
```

- [ ] **Step 3: Commit**

```bash
git add web/package.json web/package-lock.json
git commit -m "chore(web): add @sentry/react@^8.0.0 + @sentry/vite-plugin@^2.0.0"
```

---

## Task 8: Frontend — `web/src/lib/sentry.ts`

**Files:** Create `web/src/lib/sentry.ts`. **Estimated time:** 15 min.

- [ ] **Step 1: Create the file**

```typescript
/**
 * Sentry init for the React frontend. Mirrors backend (config/sentry_config.py):
 * DSN/env/release from Vite env, tracesSampleRate=0.1, replays=0, before_send
 * scrubs email/phone keys + regex-redacts message text.
 */
import * as Sentry from '@sentry/react'

const EMAIL_RE = /[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}/g
const PHONE_RE = /\+?\d[\d\s\-().]{7,}\d/g
const DENY_KEYS = new Set(['email', 'phone', 'user_email', 'user_phone'])

function scrubMapping(obj: unknown): void {
  if (!obj || typeof obj !== 'object') return
  const m = obj as Record<string, unknown>
  for (const k of Object.keys(m)) {
    if (DENY_KEYS.has(k)) { delete m[k]; continue }
    if (m[k] && typeof m[k] === 'object') scrubMapping(m[k])
  }
}

function beforeSend(event: Sentry.ErrorEvent): Sentry.ErrorEvent | null {
  scrubMapping(event)
  if (typeof event.message === 'string') {
    event.message = event.message.replace(EMAIL_RE, '[email]').replace(PHONE_RE, '[phone]')
  }
  return event
}

export function initSentry(): boolean {
  const dsn = import.meta.env.VITE_SENTRY_DSN
  if (!dsn) return false
  Sentry.init({
    dsn,
    environment: import.meta.env.VITE_STAGE ?? 'prod',
    release: import.meta.env.VITE_SENTRY_RELEASE ?? '',
    tracesSampleRate: 0.1,
    replaysSessionSampleRate: 0.0,
    beforeSend,
  })
  return true
}

export const SentryErrorBoundary = Sentry.ErrorBoundary

/** Bind authed user to Sentry scope (call after Supabase auth). PII scrubber drops email; id stays. */
export function setSentryUser(user: { id: string; email?: string | null }): void {
  Sentry.setUser({ id: user.id, email: user.email ?? undefined })
}
```

- [ ] **Step 2: Type-check**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/web && npx tsc --noEmit src/lib/sentry.ts
```

Expected: no errors. If `tsc` complains about `import.meta.env`, add `/// <reference types="vite/client" />` at the top.

- [ ] **Step 3: Commit**

```bash
git add web/src/lib/sentry.ts
git commit -m "feat(sentry/web): initSentry + SentryErrorBoundary + setSentryUser (mirrors backend)"
```

---

## Task 9: Frontend — wire init + error boundary into `main.jsx`

**Files:** Modify `web/src/main.jsx` and the Supabase auth wiring. **Estimated time:** 8 min.

- [ ] **Step 1: Replace `web/src/main.jsx`**

```jsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { initSentry, SentryErrorBoundary } from './lib/sentry'

// Init BEFORE first render so boot-time errors are captured.
initSentry()

function FallbackUi() {
  return (
    <div style={{ padding: 24, fontFamily: 'sans-serif' }}>
      <h1>Something went wrong.</h1>
      <p>We've been notified. Try refreshing the page.</p>
    </div>
  )
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <SentryErrorBoundary fallback={<FallbackUi />}>
      <App />
    </SentryErrorBoundary>
  </StrictMode>,
)
```

- [ ] **Step 2: Wire `setSentryUser` to Supabase auth**

Grep for `onAuthStateChange` to find the auth handler (likely `web/src/lib/auth.ts` or a similar module). Inside its callback, when `session?.user` exists, add:

```typescript
import { setSentryUser } from './sentry'

if (session?.user) {
  setSentryUser({ id: session.user.id, email: session.user.email })
}
```

If no central handler exists, add the call inside the component that extracts `session.user.id` for app state.

- [ ] **Step 3: Smoke test locally**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/web && VITE_SENTRY_DSN="" npm run dev
```

In DevTools console run `throw new Error('boom')`. With DSN unset, `initSentry()` returns false — error logs to console only. Confirms boundary catches when triggered from a component (test by adding a temporary `<button onClick={() => { throw new Error('boom') }}>` somewhere; remove before committing).

- [ ] **Step 4: Commit**

```bash
git add web/src/main.jsx web/src/lib/auth.ts  # adjust path where setSentryUser was wired
git commit -m "feat(sentry/web): mount SentryErrorBoundary + initSentry at boot, bind user after Supabase auth"
```

---

## Task 10: Vite plugin — source map upload during build

**Files:** Modify `web/vite.config.js`. **Estimated time:** 10 min.

- [ ] **Step 1: Replace `web/vite.config.js`**

```javascript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { sentryVitePlugin } from '@sentry/vite-plugin'

export default defineConfig({
  // sourcemap=true so the plugin has maps to upload. Vite strips the
  // //# sourceMappingURL ref in prod output, so end-users never download maps.
  build: { sourcemap: true },
  plugins: [
    react(),
    tailwindcss(),
    sentryVitePlugin({
      org: process.env.SENTRY_ORG,
      project: process.env.SENTRY_PROJECT ?? 'naukribaba-frontend',
      authToken: process.env.SENTRY_AUTH_TOKEN,
      release: { name: process.env.VITE_SENTRY_RELEASE },
      disable: !process.env.SENTRY_AUTH_TOKEN,  // local dev / unauth'd builds skip upload
      telemetry: false,
    }),
  ],
  server: {
    proxy: { '/api': 'http://localhost:8000' },
  },
})
```

- [ ] **Step 2: Verify build runs without auth token (plugin disables itself)**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/web && npm run build
```

Expected: build completes; `dist/` contains `.js` + `.js.map`. Sentry plugin prints "disabled" — correct.

- [ ] **Step 3: Commit**

```bash
git add web/vite.config.js
git commit -m "feat(sentry/web): vite plugin uploads source maps during build (gated on SENTRY_AUTH_TOKEN)"
```

---

## Task 11: CI — pass `SENTRY_RELEASE` + `Stage` to Lambda env, propagate to Netlify

**Files:** Modify `.github/workflows/deploy.yml` and `template.yaml`. **Estimated time:** 20 min.

- [ ] **Step 1: Add three parameters + Globals env to `template.yaml`**

In the `Parameters:` block, add:

```yaml
  SentryDsn:
    Type: String
    Default: ""
    Description: Sentry DSN for backend project (empty = Sentry disabled)
  SentryRelease:
    Type: String
    Default: ""
    Description: Git SHA tagged on every Sentry event
  Stage:
    Type: String
    Default: prod
    AllowedValues: [staging, prod]
    Description: Deployment environment — used as Sentry `environment` tag
```

In `Globals.Function.Environment.Variables` (create the block if missing; merge if it already has other vars):

```yaml
Globals:
  Function:
    Environment:
      Variables:
        SENTRY_DSN: !Ref SentryDsn
        SENTRY_RELEASE: !Ref SentryRelease
        STAGE: !Ref Stage
```

- [ ] **Step 2: Pass the params from `deploy.yml`**

In the `SAM Deploy` step's `env:` block, append:

```yaml
          SENTRY_DSN_BACKEND: ${{ secrets.SENTRY_DSN_BACKEND }}
          SENTRY_RELEASE: ${{ github.sha }}
```

In the `--parameter-overrides` block, append three lines (continuing the `\` pattern):

```bash
              "SentryDsn=${SENTRY_DSN_BACKEND}" \
              "SentryRelease=${SENTRY_RELEASE}" \
              "Stage=prod"
```

- [ ] **Step 3: New post-deploy step — set `VITE_SENTRY_RELEASE` in Netlify**

Append to the workflow after `SAM Deploy`:

```yaml
      - name: Update Netlify VITE_SENTRY_RELEASE for this deploy
        env:
          NETLIFY_AUTH_TOKEN: ${{ secrets.NETLIFY_AUTH_TOKEN }}
          NETLIFY_SITE_ID:    ${{ secrets.NETLIFY_SITE_ID }}
          GIT_SHA:            ${{ github.sha }}
        run: |
          npx --yes netlify-cli@17 env:set VITE_SENTRY_RELEASE "$GIT_SHA" \
            --auth "$NETLIFY_AUTH_TOKEN" --site "$NETLIFY_SITE_ID"
```

(Requires `NETLIFY_AUTH_TOKEN` and `NETLIFY_SITE_ID` GitHub secrets — already covered in Manual Prerequisites step 7.)

- [ ] **Step 4: Validate the template**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca && sam validate --lint
```

Expected: `template.yaml is a valid SAM Template`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/deploy.yml template.yaml
git commit -m "ci(sentry): inject SENTRY_DSN/SENTRY_RELEASE/STAGE into Lambda env + Netlify VITE_SENTRY_RELEASE per deploy

Stage=prod hardcoded; Phase 3 will add a staging job."
```

---

## Task 12: Bundle `config/` into the Lambda layer

**Files:** Modify `layer/build.sh`. **Estimated time:** 5 min.

Task 6 imports `from config.sentry_config import init_sentry_lambda` in every Lambda; Lambda mounts the layer at `/opt/python/`, so `config/` must be there at runtime. Same trick that brought `shared/` into the layer in PR #10 (commit 303b02a).

- [ ] **Step 1: Add `cp -r ../config python/config` to `layer/build.sh`**

Find the existing line `cp -r ../shared python/shared`. Add directly below it:

```bash
# 3. Bundle the repo's `config/` package — Phase 5 imports it from every
# Lambda handler. Lambda mounts /opt/python so this makes the import work
# at runtime. Local PYTHONPATH already covers it for unit tests.
cp -r ../config python/config
```

Then update the trailing echo block:

```bash
echo "shared/ files in layer:"
ls python/shared/
echo "config/ files in layer:"
ls python/config/
```

- [ ] **Step 2: Build the layer locally (Docker required)**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca && ./layer/build.sh
```

Expected last lines:

```
config/ files in layer:
__init__.py  sentry_config.py
```

If Docker isn't running, skip — `deploy.yml` runs `./layer/build.sh` on every deploy.

- [ ] **Step 5: Commit**

```bash
git add layer/build.sh
git commit -m "fix(layer): bundle repo's config/ package — Phase 5 imports it from 24 Lambdas

Mirrors the shared/ fix from PR #10 (commit 303b02a)."
```

---

## Task 13: ADR — Sentry vs Rollbar vs Honeycomb

**Files:** Create `docs/superpowers/specs/2026-04-27-error-tracking-decision.md`. **Estimated time:** 10 min.

- [ ] **Step 1: Write the ADR**

```markdown
# ADR — Error Tracking: Sentry over Rollbar / Honeycomb

**Date:** 2026-04-27
**Status:** Accepted
**Phase:** 5 (Deployment Safety roadmap)

## Context

Phase 5 needs error capture for backend (FastAPI + 24 Lambda handlers) and
frontend (React). Today errors die in CloudWatch logs with no aggregation,
no notification, no release tagging.

## Options

| Option | Pros | Cons |
|---|---|---|
| **Sentry** | Open-source core, free tier 5k errors + 10k tx/mo, first-class FastAPI + AWS Lambda + React integrations, vite source-map plugin, release tracking with regression detection, PostHog-similar UX. | Self-hosted has ops cost (we use SaaS). |
| Rollbar | Mature, decent SDKs. | Smaller free tier, no Lambda-native integration, no Vite source-map plugin. |
| Honeycomb | Best-in-class distributed traces. | Wrong tool — event-stream / tracing-first, not exception aggregation. Phase 4 X-Ray covers traces. |

## Decision

Use **Sentry** for error tracking. Two projects (`naukribaba-backend`, `naukribaba-frontend`).

## Consequences

- Free tier covers MVP traffic.
- Re-evaluate at $50/mo spend or 100+ active users (Team plan = $26/mo unlocks unlimited members + longer retention).
- Honeycomb deferred until X-Ray UI is outgrown.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-04-27-error-tracking-decision.md
git commit -m "docs(adr): error tracking — Sentry over Rollbar/Honeycomb"
```

---

## Task 14: Validation — backend capture in prod

**Files:** none (manual validation against the deployed prod stack). **Estimated time:** 15 min.

- [ ] **Step 1: Push branch, open PR, merge, deploy**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca
git push -u origin claude/objective-sanderson-eeedca
gh pr create --title "feat(observability): Phase 5 — Sentry error tracking" \
  --body "Implements docs/superpowers/plans/2026-04-27-deployment-safety-phase5-sentry.md."
# After CI green:
gh api -X PUT "repos/UT07/daily-job-hunt/pulls/<NUMBER>/merge" -f merge_method=squash
gh workflow run deploy.yml --ref main
gh run watch $(gh run list --workflow=deploy.yml --branch=main --limit=1 --json databaseId -q '.[0].databaseId') --exit-status
```

- [ ] **Step 2: Trigger the synthetic 500 + verify capture**

```bash
curl -sS -o /dev/null -w "HTTP %{http_code}\n" \
  "https://paie9w92c1.execute-api.eu-west-1.amazonaws.com/prod/sentry-debug"
```

Expected: `HTTP 500`. Within ~30s, `naukribaba-backend` shows an issue:
- Title: `RuntimeError: sentry-debug: synthetic 500 for capture validation`
- Tags: `environment: prod`, `release: <github-sha>`, `runtime.name: python`
- Breadcrumb `GET /sentry-debug`

If missing, check CloudWatch for the FastAPI Lambda — most common cause is `SENTRY_DSN_BACKEND` GitHub secret unset.

- [ ] **Step 3: Verify a Lambda handler captures**

```bash
aws lambda invoke --function-name naukribaba-score-batch \
  --payload '{"deliberately": "missing required keys"}' \
  --cli-binary-format raw-in-base64-out /tmp/out.json
cat /tmp/out.json
```

Expected: `errorType` non-empty in response. Second Sentry issue appears (`KeyError`/`TypeError`) tagged `environment=prod`, same `release` SHA.

---

## Task 15: Validation — frontend capture + source maps + regression + PII

**Estimated time:** 20 min.

- [ ] **Step 1: Add a temporary "throw" button shipped to prod**

In `web/src/App.jsx`, inside the existing JSX:

```jsx
<button
  onClick={() => { throw new Error('sentry-fe-debug: synthetic frontend error') }}
  style={{ position: 'fixed', bottom: 8, right: 8, padding: 4, fontSize: 11, opacity: 0.5 }}
>
  sentry test
</button>
```

(NOT gated on `import.meta.env.DEV` because we explicitly need prod validation. Reverted in Step 3.)

- [ ] **Step 2: Commit, deploy, click, verify**

```bash
git add web/src/App.jsx
git commit -m "test(sentry/web): TEMP synthetic-error button (reverted after validation)"
git push
```

Netlify auto-deploys. Open https://naukribaba.netlify.app, log in, click "sentry test". Within ~30s, `naukribaba-frontend` shows an issue. Verify:
- Stack frames show `App.jsx` with real line numbers (not `index-<hash>.js:1:12345`). If minified, source map upload didn't happen — check Netlify build log for Sentry plugin errors.
- Tags: `environment: prod`, `release: <github-sha>`, `user.id: <your-uuid>`.
- User's email is NOT in the event payload (PII scrubber dropped it).

- [ ] **Step 3: Revert the synthetic-error button**

```bash
git revert HEAD
git push
```

- [ ] **Step 4: Validate the regression flag**

In Sentry's `naukribaba-backend` project, open the `RuntimeError` issue from Task 14. Click "Resolve in next release". Then:

```bash
git commit --allow-empty -m "chore: trigger deploy for Sentry regression test"
git push origin main
gh workflow run deploy.yml --ref main
gh run watch $(gh run list --workflow=deploy.yml --branch=main --limit=1 --json databaseId -q '.[0].databaseId') --exit-status
curl -sS "https://paie9w92c1.execute-api.eu-west-1.amazonaws.com/prod/sentry-debug" >/dev/null
```

The issue should be re-captured under a new release SHA and flagged **Regression** (red flag icon, "Regressed in v<sha>") in the Sentry issue list.

- [ ] **Step 5: Validate PII never leaks**

In Sentry, search project events for `message:*@gmail.com`, `message:*@example.com`, and `message:*+353*` over the past 7 days. Expected matches: zero. Any hit means the scrubber regressed — revisit Task 2.

---

## Task 16: Cleanup — remove `/sentry-debug` endpoint

**Files:** Modify `app.py`. **Estimated time:** 3 min.

- [ ] **Step 1: Delete the endpoint**

Remove the `/sentry-debug` block added in Task 4 Step 4.

- [ ] **Step 2: Commit + deploy**

```bash
git add app.py
git commit -m "chore(sentry): remove /sentry-debug endpoint after Phase 5 validation"
git push && gh workflow run deploy.yml --ref main
```

- [ ] **Step 3: Verify gone**

```bash
curl -sS -o /dev/null -w "HTTP %{http_code}\n" \
  "https://paie9w92c1.execute-api.eu-west-1.amazonaws.com/prod/sentry-debug"
```

Expected: `HTTP 404`.

---

## Self-Review Checklist

- ✅ **Spec coverage:** every roadmap §5 file maps to a task — `requirements.txt`→T0; `config/sentry_config.py`→T2+T3; `tests/unit/test_sentry_pii_scrubber.py`→T1; `app.py`→T4; `lambdas/browser/ws_*.py`→T5; `lambdas/pipeline/*.py` (20 modules)→T6; `web/package.json`→T7; `web/src/lib/sentry.ts`→T8; `web/src/main.jsx`→T9; `web/vite.config.js`→T10; `deploy.yml`+`template.yaml`→T11; `layer/build.sh`→T12; ADR→T13; 4 roadmap success criteria→T14+T15; `/sentry-debug` cleanup→T16.
- ✅ **No placeholders:** every code step shows actual code; every test shows actual assertions; bulk-edit Task 6 shows one representative diff + full file list.
- ✅ **Type/name consistency:** `init_sentry_fastapi`/`init_sentry_lambda`/`scrub_pii` identical across tests + impl; user payload `{id, email}` consistent backend↔frontend; env var names `SENTRY_DSN`/`SENTRY_RELEASE`/`STAGE` consistent across `sentry_config.py`, `template.yaml`, `deploy.yml`.
- ✅ **Cross-phase:** Phase 4 deferred via `# TODO(phase4)` in `_common_kwargs`; Phase 1's `user.id` naming matches; Phase 3's `Stage` defaulted to `prod` today.
- ✅ **Realism:** T0–T3 ~50min, T4 ~15min, T5 ~15min, T6 ~30min, T7–T10 ~40min, T11 ~20min, T12–T13 ~15min, T14–T15 ~35min, T16 ~5min. ~3.5h focused work — fits the roadmap's half-day budget.
