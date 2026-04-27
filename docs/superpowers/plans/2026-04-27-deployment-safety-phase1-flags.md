# Deployment Safety — Phase 1: Feature Flags via PostHog

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple *deploying* code from *releasing* it to users by gating five risky write paths (`auto_apply`, `council_scoring`, `tailor_full_rewrite`, `scraper_glassdoor`, `scraper_gradireland`) behind PostHog boolean flags. All flags default-off; flips happen in the PostHog UI without redeploys; kill-switch flips affect prod within 30s via the SDK's 30-second poll.

**Architecture:** One PostHog cloud project (free tier: 1M events/mo, unlimited flags). A backend wrapper `config/feature_flags.py` exposes `is_enabled(flag, user_id, default=False)` with **local evaluation** — the SDK fetches a flag definitions snapshot every 30s, so per-request calls are zero-network. A frontend wrapper `web/src/lib/featureFlags.ts` exposes `useFeatureFlag(flag)` backed by `posthog-js`, identifying the user with their Supabase JWT `sub` (UUID) after auth. Both wrappers DI-inject the underlying client so unit tests stub a fake without touching network or env vars.

**Tech Stack:** Python 3.12, `posthog>=4.0.0` (PostHog Python SDK), pytest. React 19, `posthog-js@^1.180.0`, `posthog-js/react`, vitest.

**Spec:** [`2026-04-27-deployment-safety-roadmap.md`](./2026-04-27-deployment-safety-roadmap.md) — Phase 1 section.

**Pulled out of scope (handled in later phases):** Sentry user identification (Phase 5 will reuse the auth context from this phase), structured-log enrichment with flag values (Phase 4), multivariate flags / A-B experiments (deferred — boolean only for v1).

---

## Manual Prerequisite (NOT scripted — do this BEFORE Task 1)

This is the only step that cannot be done from the worktree. Budget ~10 minutes.

1. **Sign up / sign in** to PostHog Cloud at <https://us.posthog.com> (or <https://eu.posthog.com> if you prefer EU residency — pick one and stick to it; the SDK host URL must match).
2. **Create a new project** named `naukribaba`.
3. **Capture two keys** from `Project Settings → Project API Key`:
   - `Project API Key` (begins with `phc_…`) — this is *public*; safe in frontend bundles.
   - From `Personal Settings → Personal API Keys → New personal API key` with scope `feature_flag:read` — this is *private*; backend only. Required for local evaluation (the Python SDK uses it to download the flag definitions snapshot).
4. **Create the five flags** in `Feature Flags → New feature flag`. For each:
   - Key: exact name from list below
   - Type: `Boolean`
   - Rollout: `0%` (default-off)
   - Persistence: `Persisted across authenticated sessions`

   | Flag key | Description |
   |---|---|
   | `auto_apply` | Gates the auto-apply endpoints + AutoApplyButton |
   | `council_scoring` | Gates 3-perspective scoring vs single-perspective fast path |
   | `tailor_full_rewrite` | Gates heavy/full-rewrite tailoring vs light/moderate |
   | `scraper_glassdoor` | Gates the Glassdoor scraper (currently dormant) |
   | `scraper_gradireland` | Gates the GradIreland scraper (currently 0-job) |

5. **Add GitHub repo secrets** at <https://github.com/UT07/daily-job-hunt/settings/secrets/actions>:
   - `POSTHOG_PROJECT_KEY` = the `phc_…` key (also exposed as `VITE_POSTHOG_KEY` to Netlify; same value, different name)
   - `POSTHOG_PERSONAL_API_KEY` = the personal API key (backend only)
   - `POSTHOG_HOST` = `https://us.i.posthog.com` (or EU equivalent)
6. **Add Netlify env var** at <https://app.netlify.com/sites/naukribaba/configuration/env>:
   - `VITE_POSTHOG_KEY` = same `phc_…` value
   - `VITE_POSTHOG_HOST` = same host as above

When all six items above are confirmed, proceed to Task 1. Do not start any task without the keys in hand — the unit tests stub the SDK so they don't need keys, but Task 5 (CI wiring) will block on missing secrets and Task 8 (manual smoke) requires real keys to verify flag flips affect prod.

---

## File Structure

```
config/
  __init__.py                                   (CREATE) marks config/ as a package
  feature_flags.py                              (CREATE) backend wrapper, ~110 LOC
requirements.txt                                (MODIFY) add posthog>=4.0.0
app.py                                          (MODIFY @ ~line 2418, ~line 2472) gate /api/apply/eligibility + /api/apply/preview
lambdas/browser/
  ws_route.py                                   (MODIFY @ handler entry) gate apply-relay path
lambdas/pipeline/
  score_batch.py                                (MODIFY @ line 136) gate council vs single-perspective
  tailor_resume.py                              (MODIFY @ ~line 348) gate heavy depth → moderate fallback
web/package.json                                (MODIFY) add posthog-js@^1.180.0
web/src/lib/
  featureFlags.ts                               (CREATE) PostHogProvider wrapper + useFeatureFlag hook
web/src/main.jsx                                (MODIFY) wrap <App/> in <PostHogProvider>
web/src/components/
  AutoApplyButton.jsx                           (CREATE) flag-gated button (replaces inline buttons in JobWorkspace later)
tests/unit/
  test_feature_flags.py                         (CREATE) 6 cases
web/src/lib/
  featureFlags.test.tsx                         (CREATE) vitest hook test
.github/workflows/
  deploy.yml                                    (MODIFY @ SAM Deploy step) pass POSTHOG_* to Lambda env
template.yaml                                   (MODIFY @ Globals.Function.Environment) declare PostHog env vars + parameters
docs/superpowers/specs/
  2026-04-27-feature-flags-decision.md          (CREATE) ADR: PostHog vs LaunchDarkly vs Unleash
CLAUDE.md                                       (MODIFY) append "Feature Flags" section
```

---

## Task 1: Backend wrapper — TDD scaffolding (~15 min)

**Files:**
- Create: `tests/unit/test_feature_flags.py`
- Create: `config/__init__.py`
- Create: `config/feature_flags.py`

This task lays down all six failing tests, then the minimal scaffolding that makes the *imports* resolve (but tests still fail). Implementation lands in Task 2.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_feature_flags.py`:

```python
"""Tests for config.feature_flags.

The wrapper must:
- Return the flag value when the client says enabled/disabled
- Fall back to `default` on any client error (network, bad key, init failure)
- Propagate `user_id` to the underlying client
- Honor an env-based no-op (NAUKRIBABA_FLAGS_DISABLED=1) so tests don't poll PostHog
- Provide a @flag_gated decorator that 503s the request when off
- Treat the kill-switch (rollout=0%) as off — no special-casing
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_client():
    """A stand-in for posthog.Posthog.feature_enabled."""
    return MagicMock()


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("NAUKRIBABA_FLAGS_DISABLED", raising=False)
    monkeypatch.delenv("POSTHOG_PROJECT_KEY", raising=False)
    monkeypatch.delenv("POSTHOG_PERSONAL_API_KEY", raising=False)


def test_is_enabled_returns_true_when_client_says_true(fake_client):
    """Flag on for this user → True, regardless of default."""
    from config.feature_flags import is_enabled
    fake_client.feature_enabled.return_value = True
    assert is_enabled("auto_apply", "user-123", default=False, _client=fake_client) is True
    fake_client.feature_enabled.assert_called_once_with("auto_apply", "user-123")


def test_is_enabled_returns_false_when_client_says_false(fake_client):
    """Kill-switch (rollout=0%) → False even if default=True."""
    from config.feature_flags import is_enabled
    fake_client.feature_enabled.return_value = False
    # default=True must not override an explicit False from PostHog (kill-switch behavior)
    assert is_enabled("auto_apply", "user-123", default=True, _client=fake_client) is False


def test_is_enabled_falls_back_to_default_on_client_error(fake_client):
    """Network/SDK failure → default. NEVER raise to caller."""
    from config.feature_flags import is_enabled
    fake_client.feature_enabled.side_effect = ConnectionError("posthog unreachable")
    assert is_enabled("auto_apply", "user-123", default=False, _client=fake_client) is False
    assert is_enabled("auto_apply", "user-123", default=True, _client=fake_client) is True


def test_is_enabled_propagates_user_id(fake_client):
    """The user_id must reach the SDK so per-user rollouts work."""
    from config.feature_flags import is_enabled
    fake_client.feature_enabled.return_value = None  # PostHog returns None when no rule matches
    is_enabled("auto_apply", "user-abc-123", default=False, _client=fake_client)
    args, kwargs = fake_client.feature_enabled.call_args
    assert "user-abc-123" in args or kwargs.get("distinct_id") == "user-abc-123"


def test_env_flag_disabled_returns_default_without_calling_client(fake_client, monkeypatch):
    """In test mode (NAUKRIBABA_FLAGS_DISABLED=1) the wrapper short-circuits."""
    from config.feature_flags import is_enabled
    monkeypatch.setenv("NAUKRIBABA_FLAGS_DISABLED", "1")
    assert is_enabled("auto_apply", "user-123", default=False, _client=fake_client) is False
    assert is_enabled("auto_apply", "user-123", default=True, _client=fake_client) is True
    fake_client.feature_enabled.assert_not_called()


def test_decorator_returns_503_when_flag_off(fake_client):
    """@flag_gated('auto_apply') must short-circuit with HTTPException(503)."""
    from fastapi import HTTPException
    from config.feature_flags import flag_gated

    fake_client.feature_enabled.return_value = False

    @flag_gated("auto_apply", _client=fake_client)
    def endpoint(user_id: str):
        return {"ok": True}

    with pytest.raises(HTTPException) as ei:
        endpoint(user_id="user-123")
    assert ei.value.status_code == 503
    assert ei.value.detail == "feature_disabled"


def test_decorator_calls_inner_when_flag_on(fake_client):
    from config.feature_flags import flag_gated

    fake_client.feature_enabled.return_value = True

    @flag_gated("auto_apply", _client=fake_client)
    def endpoint(user_id: str):
        return {"ok": True, "user": user_id}

    assert endpoint(user_id="user-123") == {"ok": True, "user": "user-123"}
```

- [ ] **Step 2: Create the package marker and stub**

Create `config/__init__.py` (empty file):

```python
"""Backend cross-cutting config (feature flags, observability later)."""
```

Create `config/feature_flags.py` with stubs that make imports succeed but tests still fail:

```python
"""Stubs — implementation in Task 2."""
from __future__ import annotations


def is_enabled(flag, user_id, default=False, _client=None):  # type: ignore[no-untyped-def]
    raise NotImplementedError


def flag_gated(flag, _client=None):  # type: ignore[no-untyped-def]
    raise NotImplementedError
```

- [ ] **Step 3: Run the tests to verify they fail for the right reason**

Run:
```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca && source .venv/bin/activate
pytest tests/unit/test_feature_flags.py -v
```

Expected: 7 FAIL with `NotImplementedError` (or, for the decorator test, `NotImplementedError` raised at decoration time). All seven test names appear in the output. No `ImportError` / `ModuleNotFoundError`.

- [ ] **Step 4: Commit the failing tests + stub**

```bash
git add tests/unit/test_feature_flags.py config/__init__.py config/feature_flags.py
git commit -m "test(flags): add 7 failing tests for config.feature_flags wrapper

TDD scaffolding. is_enabled() + @flag_gated stubs raise NotImplementedError.
Implementation lands in next commit. Tests cover: enabled/disabled paths,
default fallback on network error, user_id propagation, NAUKRIBABA_FLAGS_DISABLED
short-circuit, decorator 503 + pass-through behavior."
```

---

## Task 2: Backend wrapper — implementation (~20 min)

**Files:**
- Modify: `config/feature_flags.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add posthog to requirements.txt**

Open `requirements.txt`. Find the existing `# Supabase` block near the bottom, then add a new section above it:

```
# Feature flags (PostHog — local evaluation, polls every 30s)
posthog>=4.0.0
```

- [ ] **Step 2: Install locally**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca && source .venv/bin/activate
pip install 'posthog>=4.0.0'
```

Expected: `Successfully installed posthog-…` (version 4.x). No errors.

- [ ] **Step 3: Implement `config/feature_flags.py`**

Replace the stub with the full implementation:

```python
"""Backend feature flag wrapper around PostHog Python SDK (local evaluation).

Local evaluation means the SDK fetches a flag definitions snapshot from
PostHog every ~30s and evaluates rules against `user_id` in-process. Per-call
latency is zero-network. Trade-off: a flag flip in the UI takes up to 30s
to propagate. That window is documented in CLAUDE.md and acceptable.

This module MUST NOT raise — every code path returns `default` on error.
A failing PostHog must never break a user request.

Initialization is lazy: the first call to `is_enabled` constructs the
shared client. Tests pass `_client=…` to bypass init entirely; in CI/test
environments `NAUKRIBABA_FLAGS_DISABLED=1` short-circuits without ever
touching the network.
"""
from __future__ import annotations

import functools
import logging
import os
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_client_lock = threading.Lock()
_global_client: Any = None


def _build_default_client() -> Any:
    """Construct a singleton PostHog client. Returns None if config missing.

    Caller must handle None — `is_enabled` does so by returning `default`.
    """
    project_key = os.environ.get("POSTHOG_PROJECT_KEY")
    personal_key = os.environ.get("POSTHOG_PERSONAL_API_KEY")
    host = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com")
    if not project_key or not personal_key:
        logger.warning(
            "[flags] POSTHOG_PROJECT_KEY or POSTHOG_PERSONAL_API_KEY missing; "
            "flag checks will return their default value."
        )
        return None
    try:
        from posthog import Posthog  # noqa: WPS433  (runtime import keeps test isolation)
        return Posthog(
            project_api_key=project_key,
            personal_api_key=personal_key,
            host=host,
            # Local eval — refresh definitions every 30s
            poll_interval=30,
            # Don't block app startup waiting for network
            sync_mode=False,
        )
    except Exception as exc:  # pragma: no cover  (init-path edge cases)
        logger.exception("[flags] PostHog client init failed: %s", exc)
        return None


def _get_client() -> Any:
    """Return the singleton client, building it on first call. May return None."""
    global _global_client
    if _global_client is not None:
        return _global_client
    with _client_lock:
        if _global_client is None:
            _global_client = _build_default_client()
        return _global_client


def is_enabled(
    flag: str,
    user_id: Optional[str],
    default: bool = False,
    _client: Any = None,
) -> bool:
    """Return True iff the flag is on for this user.

    Parameters
    ----------
    flag : str
        Exact PostHog flag key (e.g. "auto_apply").
    user_id : str | None
        Stable distinct_id. Pass the Supabase auth UUID. None → uses
        a literal "anonymous" — meaning per-user rollouts won't apply,
        so this should only happen for unauthenticated paths.
    default : bool
        Returned when (a) the env short-circuit is set, (b) the client
        couldn't be built, (c) the SDK call raises, or (d) the SDK
        returns None (no rule matched).
    _client : Any
        Test seam. Production callers leave this None.
    """
    if os.environ.get("NAUKRIBABA_FLAGS_DISABLED") == "1":
        return default

    client = _client if _client is not None else _get_client()
    if client is None:
        return default

    distinct_id = user_id or "anonymous"
    try:
        result = client.feature_enabled(flag, distinct_id)
    except Exception as exc:
        logger.warning("[flags] PostHog check failed for %s/%s: %s", flag, distinct_id, exc)
        return default
    if result is None:
        return default
    return bool(result)


def flag_gated(flag: str, _client: Any = None) -> Callable:
    """FastAPI/handler decorator that 503s when the flag is off.

    Usage:
        @flag_gated("auto_apply")
        def apply_eligibility(user_id: str, ...): ...

    The decorated function MUST accept a kwarg named `user_id`. The
    wrapper extracts it for the flag check and passes everything through.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            from fastapi import HTTPException

            user_id = kwargs.get("user_id")
            if not is_enabled(flag, user_id, default=False, _client=_client):
                raise HTTPException(status_code=503, detail="feature_disabled")
            return func(*args, **kwargs)
        return wrapper
    return decorator
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/unit/test_feature_flags.py -v
```

Expected: `7 passed`. No skips, no warnings except possibly Pydantic deprecation noise from FastAPI imports.

- [ ] **Step 5: Run the full unit suite to verify no regressions**

```bash
pytest tests/unit/ -x --tb=short
```

Expected: all green. (The new module isn't imported anywhere yet, so the only changes to the test surface are the 7 new passing tests.)

- [ ] **Step 6: Commit**

```bash
git add config/feature_flags.py requirements.txt
git commit -m "feat(flags): implement PostHog wrapper with local eval + decorator

config.feature_flags.is_enabled(flag, user_id, default=False) — never raises,
falls back to default on any error. Singleton lazy client init; reads
POSTHOG_PROJECT_KEY + POSTHOG_PERSONAL_API_KEY from env. NAUKRIBABA_FLAGS_DISABLED=1
short-circuits to default in test/CI. @flag_gated('flag_name') decorator
raises HTTPException(503, 'feature_disabled') when off.

Local-eval mode: SDK polls flag definitions every 30s; per-call latency is
zero-network. Flag flips propagate within 30s.

7/7 unit tests pass."
```

---

## Task 3: Gate `app.py` apply endpoints (~10 min)

**Files:**
- Modify: `app.py` @ `apply_eligibility` (line 2407) and `apply_preview` (line 2461)
- Modify: `tests/unit/test_apply_endpoints.py`

The two apply endpoints are HTTP-callable today. We gate the entire endpoint behind `auto_apply` — when off, the user sees a 503 and the frontend hides the button (Task 6) so they never even hit the URL.

- [ ] **Step 1: Read both endpoint signatures to confirm shape**

```bash
sed -n '2406,2410p;2460,2464p' app.py
```

Expected (verified 2026-04-27):

```
2406:@app.get("/api/apply/eligibility/{job_id}")
2407:def apply_eligibility(job_id: str, user: AuthUser = Depends(get_current_user)):
...
2460:@app.get("/api/apply/preview/{job_id}")
2461:def apply_preview(job_id: str, user: AuthUser = Depends(get_current_user)):
```

The `user` dependency has `.id` (Supabase UUID). The `@flag_gated` decorator from Task 2 expects `user_id` as a kwarg, so we won't use the decorator here — instead inline the `is_enabled` check using `user.id`. (The decorator works on internal helpers; FastAPI signatures with `Depends` need the inline form.)

- [ ] **Step 2: Write the failing tests**

Add to the end of `tests/unit/test_apply_endpoints.py` (do not delete existing tests):

```python
def test_eligibility_503s_when_auto_apply_flag_off(client, monkeypatch):
    """auto_apply=off → 503 feature_disabled, no DB lookup attempted."""
    from unittest.mock import patch
    c, db = client
    monkeypatch.setattr(
        "config.feature_flags.is_enabled",
        lambda flag, user_id, default=False, _client=None: False,
    )
    with patch("shared.load_job.load_job") as load_mock:
        r = c.get("/api/apply/eligibility/j1")
    assert r.status_code == 503
    assert r.json() == {"detail": "feature_disabled"}
    load_mock.assert_not_called()


def test_preview_503s_when_auto_apply_flag_off(client, monkeypatch):
    from unittest.mock import patch
    c, _ = client
    monkeypatch.setattr(
        "config.feature_flags.is_enabled",
        lambda flag, user_id, default=False, _client=None: False,
    )
    with patch("shared.load_job.load_job") as load_mock:
        r = c.get("/api/apply/preview/j1")
    assert r.status_code == 503
    assert r.json() == {"detail": "feature_disabled"}
    load_mock.assert_not_called()


def test_eligibility_passes_when_auto_apply_flag_on(client, monkeypatch):
    """auto_apply=on → endpoint runs as before."""
    from unittest.mock import patch
    c, db = client
    _no_existing_apps(db)
    db.get_user.return_value = _complete_user()
    monkeypatch.setattr(
        "config.feature_flags.is_enabled",
        lambda flag, user_id, default=False, _client=None: True,
    )
    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.get("/api/apply/eligibility/j1")
    assert r.status_code == 200
    assert r.json()["eligible"] is True
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/unit/test_apply_endpoints.py -v -k "flag"
```

Expected: 3 FAIL — current `app.py` has no flag check; the off-flag tests get 200, the on-flag test passes only if other infra cooperates.

- [ ] **Step 4: Add the gate to both endpoints in `app.py`**

At `app.py` line 2407 (after the `def apply_eligibility(...)` line, before the existing `from shared.load_job import load_job` line), insert:

```python
def apply_eligibility(job_id: str, user: AuthUser = Depends(get_current_user)):
    """Per-job eligibility — no AI, no network calls to platforms."""
    from config.feature_flags import is_enabled
    if not is_enabled("auto_apply", user.id, default=False):
        raise HTTPException(status_code=503, detail="feature_disabled")
    from shared.load_job import load_job
    from shared.profile_completeness import check_profile_completeness
```

(Concretely: add the two lines `from config.feature_flags import is_enabled` and `if not is_enabled(...): raise HTTPException(...)` between the existing docstring and the existing `from shared.load_job` line.)

At `app.py` line 2461 (the `apply_preview` function), do the same:

```python
def apply_preview(job_id: str, user: AuthUser = Depends(get_current_user)):
    """Apply preview snapshot. Plan 3a returns no AI answers; Plan 3b will
    populate `questions` (platform metadata) and `answers` (AI-generated)
    without changing this response shape."""
    from config.feature_flags import is_enabled
    if not is_enabled("auto_apply", user.id, default=False):
        raise HTTPException(status_code=503, detail="feature_disabled")
    from shared.load_job import load_job
    from shared.profile_completeness import check_profile_completeness
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/test_apply_endpoints.py -v
```

Expected: all green (the 3 new flag tests + every existing test in the file).

- [ ] **Step 6: Commit**

```bash
git add app.py tests/unit/test_apply_endpoints.py
git commit -m "feat(flags): gate /api/apply/eligibility + /api/apply/preview behind auto_apply flag

Both endpoints now 503 with detail='feature_disabled' when the auto_apply
PostHog flag is off for the calling user. Default-off, so the merge of
this commit is dark — frontend (Task 6) hides the button so users never
hit the URL. Flip on per-user from PostHog UI to release.

3 new tests + existing suite green."
```

---

## Task 4: Gate `lambdas/browser/ws_route.py` (~10 min)

**Files:**
- Modify: `lambdas/browser/ws_route.py`
- Modify: `tests/unit/test_ws_route.py`

`ws_route.py` is the WebSocket `$default` relay between the React app and the Fargate browser. Today it relays any text/JSON, including `apply.start` control messages once Plan 3b lands. We gate the relay entirely on `auto_apply` — when off, the Lambda still ACKs the WS frame (so the connection stays open) but drops the relay and posts a `feature_disabled` notice back to the sender.

The user_id is not in the WS event directly; we read it from the session record. The `browser_sessions.find_session_by_connection` already returns the session, which holds `user_id`.

- [ ] **Step 1: Read the existing handler shape**

Verified 2026-04-27, `ws_route.py:25-54`:

```python
def handler(event, context):
    body = event.get("body", "") or ""
    if len(body) > _MAX_BODY:
        return {"statusCode": 413, "body": "payload too large"}

    connection_id = event.get("requestContext", {}).get("connectionId", "")
    found = browser_sessions.find_session_by_connection(connection_id)
    if not found:
        return {"statusCode": 200, "body": "noop"}

    session, sender_role = found
    ...
```

`session` is a dict with a `user_id` key (per `shared/browser_sessions.py`). We add the flag check immediately after `session` is unpacked.

- [ ] **Step 2: Write the failing test**

Add to `tests/unit/test_ws_route.py` (file already exists from PR #8):

```python
def test_ws_route_returns_feature_disabled_when_flag_off(monkeypatch):
    """auto_apply=off → relay drops to 200 noop, no PostToConnection call."""
    from lambdas.browser import ws_route
    from unittest.mock import patch

    fake_session = {"session_id": "s1", "user_id": "u-1", "ws_connection_browser": "B"}
    monkeypatch.setattr(
        "config.feature_flags.is_enabled",
        lambda flag, user_id, default=False, _client=None: False,
    )
    with patch.object(
        ws_route.browser_sessions, "find_session_by_connection",
        return_value=(fake_session, "frontend"),
    ), patch.object(ws_route.browser_sessions, "post_to_connection") as post_mock:
        result = ws_route.handler(
            {"body": '{"type":"apply.start"}', "requestContext": {"connectionId": "F"}},
            None,
        )
    assert result["statusCode"] == 200
    assert "feature_disabled" in result["body"]
    post_mock.assert_not_called()
```

Run: `pytest tests/unit/test_ws_route.py -v -k flag` — expected FAIL.

- [ ] **Step 3: Add the gate**

Edit `lambdas/browser/ws_route.py`. After the `session, sender_role = found` line, insert:

```python
    session, sender_role = found

    # auto_apply kill-switch — when flag off, drop the relay entirely.
    # Connection stays open (the WS frame still 200s) but no message reaches the peer.
    from config.feature_flags import is_enabled
    if not is_enabled("auto_apply", session.get("user_id"), default=False):
        return {"statusCode": 200, "body": "feature_disabled"}

    peer_role = "browser" if sender_role == "frontend" else "frontend"
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/unit/test_ws_route.py -v
```

Expected: all green (new test + existing tests).

- [ ] **Step 5: Commit**

```bash
git add lambdas/browser/ws_route.py tests/unit/test_ws_route.py
git commit -m "feat(flags): gate ws_route relay behind auto_apply flag

When the flag is off for the session's user_id, the Lambda still ACKs the
WS frame (keeps the connection alive) but drops the relay — no PostToConnection
to the peer. Body returns 'feature_disabled' so the frontend can show a
graceful error instead of waiting on a phantom apply."
```

---

## Task 5: Gate `score_batch.py` council vs single-perspective (~10 min)

**Files:**
- Modify: `lambdas/pipeline/score_batch.py` @ line 136
- Modify: `tests/unit/test_score_batch.py`

The pipeline currently calls `score_single_job_deterministic` (the 3-perspective "council" path with median dampening) for every candidate. We add a single-perspective fast path and gate which one runs on `council_scoring`. Default-off → single-perspective is the production path; flip on to switch back to council.

- [ ] **Step 1: Read the call site**

Verified 2026-04-27, `score_batch.py:130-136`:

```python
    matched_items = []
    skipped_count = 0
    for job in jobs:
        skip_status = should_skip_scoring(job)
        if skip_status:
            ...
            continue
        score_result = score_single_job_deterministic(job, resume_tex)
```

The fast path is `score_single_job(job, resume_tex)` (defined at line 281, single AI call, no median).

- [ ] **Step 2: Write the failing test**

Add to `tests/unit/test_score_batch.py`:

```python
def test_score_batch_uses_single_perspective_when_council_flag_off(monkeypatch):
    """council_scoring=off → score_single_job (1 AI call), not the deterministic
    median path (3+ calls)."""
    from unittest.mock import patch, MagicMock
    from lambdas.pipeline import score_batch

    monkeypatch.setattr(
        "config.feature_flags.is_enabled",
        lambda flag, user_id, default=False, _client=None: False,
    )

    fake_job = {
        "job_hash": "h1", "title": "SRE", "company": "Acme",
        "description": "infra work", "apply_url": "https://example.com",
        "source": "test",
    }
    fake_score = {"match_score": 75, "ats_score": 80, "hiring_manager_score": 70,
                  "tech_recruiter_score": 75, "key_matches": [], "gaps": [],
                  "reasoning": "", "archetype": "", "seniority": "",
                  "remote": "", "requirement_map": [],
                  "provider": "p", "model": "m"}

    db_mock = MagicMock()
    db_mock.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [fake_job]
    db_mock.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {"tex_content": "% resume", "resume_type": "base"},
    ]
    db_mock.table.return_value.insert.return_value.execute.return_value = MagicMock()

    with patch.object(score_batch, "get_supabase", return_value=db_mock), \
         patch.object(score_batch, "should_skip_scoring", return_value=None), \
         patch.object(score_batch, "score_single_job", return_value=fake_score) as fast_path, \
         patch.object(score_batch, "score_single_job_deterministic") as council_path:
        score_batch.handler(
            {"user_id": "u-1", "new_job_hashes": ["h1"], "min_match_score": 50},
            None,
        )

    fast_path.assert_called_once()
    council_path.assert_not_called()


def test_score_batch_uses_council_when_flag_on(monkeypatch):
    """council_scoring=on → score_single_job_deterministic (median path)."""
    from unittest.mock import patch, MagicMock
    from lambdas.pipeline import score_batch

    monkeypatch.setattr(
        "config.feature_flags.is_enabled",
        lambda flag, user_id, default=False, _client=None: True,
    )

    fake_job = {
        "job_hash": "h1", "title": "SRE", "company": "Acme",
        "description": "infra work", "apply_url": "https://example.com",
        "source": "test",
    }
    fake_score = {"match_score": 75, "ats_score": 80, "hiring_manager_score": 70,
                  "tech_recruiter_score": 75, "key_matches": [], "gaps": [],
                  "reasoning": "", "archetype": "", "seniority": "",
                  "remote": "", "requirement_map": [],
                  "provider": "p", "model": "m"}

    db_mock = MagicMock()
    db_mock.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [fake_job]
    db_mock.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {"tex_content": "% resume", "resume_type": "base"},
    ]
    db_mock.table.return_value.insert.return_value.execute.return_value = MagicMock()

    with patch.object(score_batch, "get_supabase", return_value=db_mock), \
         patch.object(score_batch, "should_skip_scoring", return_value=None), \
         patch.object(score_batch, "score_single_job") as fast_path, \
         patch.object(score_batch, "score_single_job_deterministic", return_value=fake_score) as council_path:
        score_batch.handler(
            {"user_id": "u-1", "new_job_hashes": ["h1"], "min_match_score": 50},
            None,
        )

    council_path.assert_called_once()
    fast_path.assert_not_called()
```

Run: `pytest tests/unit/test_score_batch.py -v -k "flag\|council"` — expected 2 FAIL.

- [ ] **Step 3: Wire the gate into `score_batch.py`**

Edit `lambdas/pipeline/score_batch.py`. After the existing `from shared.apply_platform import classify_apply_platform` line (line 10), add:

```python
from config.feature_flags import is_enabled
```

Then replace the call at line 136:

```python
        score_result = score_single_job_deterministic(job, resume_tex)
```

with:

```python
        if is_enabled("council_scoring", user_id, default=False):
            score_result = score_single_job_deterministic(job, resume_tex)
        else:
            score_result = score_single_job(job, resume_tex)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/unit/test_score_batch.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add lambdas/pipeline/score_batch.py tests/unit/test_score_batch.py
git commit -m "feat(flags): gate council scoring behind council_scoring flag

Default off (single-perspective fast path). When on, falls back to the
3-perspective deterministic median (current production behavior).
Flag-flip propagates to the next pipeline run within 30s; mid-batch
calls keep their already-fetched flag value (no torn state)."
```

---

## Task 6: Gate `tailor_resume.py` heavy depth (~10 min)

**Files:**
- Modify: `lambdas/pipeline/tailor_resume.py` @ line ~348
- Modify: `tests/unit/test_tailoring_depth.py`

`tailor_resume.py:348-350` chooses between `light`/`moderate`/`heavy` (full-rewrite) tailoring depths. We gate `heavy` behind `tailor_full_rewrite`. When the flag is off, any request for `heavy` falls back to `moderate` — no full rewrite ever runs. This keeps the prompt-quality risk of the full-rewrite path off prod until we can quality-gate it (Phase 2.6 backlog item).

- [ ] **Step 1: Read the existing depth-selection block**

Verified 2026-04-27, `tailor_resume.py:345-393`:

```python
def handler(event, context):
    job_hash = event["job_hash"]
    user_id = event["user_id"]
    tailoring_depth = event.get("tailoring_depth")
    if tailoring_depth is None:
        tailoring_depth = "light" if event.get("light_touch") else "moderate"
    ...
    depth_note = {
        "light": _LIGHT_TOUCH_NOTE,
        "moderate": _MODERATE_NOTE,
        "heavy": _FULL_REWRITE_NOTE,
    }.get(tailoring_depth, _MODERATE_NOTE)
```

We add the gate immediately after `tailoring_depth` is computed (line 350).

- [ ] **Step 2: Write the failing test**

Add to `tests/unit/test_tailoring_depth.py`:

```python
def test_tailor_resume_falls_back_to_moderate_when_full_rewrite_flag_off(monkeypatch):
    """tailor_full_rewrite=off + event asking for heavy → moderate at execution time."""
    from unittest.mock import patch, MagicMock
    from lambdas.pipeline import tailor_resume

    monkeypatch.setattr(
        "config.feature_flags.is_enabled",
        lambda flag, user_id, default=False, _client=None: False,
    )

    captured = {}
    def fake_ai(prompt, system, **kw):
        captured["system"] = system
        return {"content": "% tailored", "provider": "p", "model": "m"}

    db_mock = MagicMock()
    db_mock.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"job_hash": "h1", "title": "X", "company": "Y", "description": "d"}
    ]
    db_mock.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {"tex_content": "\\documentclass{article}\n\\begin{document}\\section{X}\\end{document}"}
    ]
    db_mock.table.return_value.upsert.return_value.execute.return_value = MagicMock()

    with patch.object(tailor_resume, "get_supabase", return_value=db_mock), \
         patch.object(tailor_resume, "ai_complete_cached", side_effect=fake_ai), \
         patch("boto3.client"):
        tailor_resume.handler(
            {"job_hash": "h1", "user_id": "u-1", "tailoring_depth": "heavy"}, None,
        )

    # _MODERATE_NOTE prefix from the module — when downgraded, the system prompt
    # starts with the moderate-depth instructions, NOT _FULL_REWRITE_NOTE.
    assert "FULL REWRITE" not in captured["system"]


def test_tailor_resume_runs_heavy_when_flag_on(monkeypatch):
    """tailor_full_rewrite=on + event asking for heavy → full-rewrite path runs."""
    from unittest.mock import patch, MagicMock
    from lambdas.pipeline import tailor_resume

    monkeypatch.setattr(
        "config.feature_flags.is_enabled",
        lambda flag, user_id, default=False, _client=None: True,
    )

    captured = {}
    def fake_ai(prompt, system, **kw):
        captured["system"] = system
        return {"content": "% tailored", "provider": "p", "model": "m"}

    db_mock = MagicMock()
    db_mock.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"job_hash": "h1", "title": "X", "company": "Y", "description": "d"}
    ]
    db_mock.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {"tex_content": "\\documentclass{article}\n\\begin{document}\\section{X}\\end{document}"}
    ]
    db_mock.table.return_value.upsert.return_value.execute.return_value = MagicMock()

    with patch.object(tailor_resume, "get_supabase", return_value=db_mock), \
         patch.object(tailor_resume, "ai_complete_cached", side_effect=fake_ai), \
         patch("boto3.client"):
        tailor_resume.handler(
            {"job_hash": "h1", "user_id": "u-1", "tailoring_depth": "heavy"}, None,
        )

    assert "FULL REWRITE" in captured["system"]
```

Run: `pytest tests/unit/test_tailoring_depth.py -v -k flag` — expected FAIL.

- [ ] **Step 3: Wire the gate**

Edit `lambdas/pipeline/tailor_resume.py`. Add at top with other imports (after the existing imports):

```python
from config.feature_flags import is_enabled
```

Modify the depth-selection block — after the existing line (around 350):

```python
    if tailoring_depth is None:
        tailoring_depth = "light" if event.get("light_touch") else "moderate"
```

add immediately below:

```python
    # Gate: full-rewrite is risky (prompt-quality drift) — kept dark until
    # Phase 2.6 quality gates land. When off, downgrade heavy → moderate.
    if tailoring_depth == "heavy" and not is_enabled(
        "tailor_full_rewrite", user_id, default=False
    ):
        logger.info(
            "[tailor] tailor_full_rewrite flag off for %s — downgrading heavy → moderate",
            user_id,
        )
        tailoring_depth = "moderate"
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/unit/test_tailoring_depth.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add lambdas/pipeline/tailor_resume.py tests/unit/test_tailoring_depth.py
git commit -m "feat(flags): gate heavy/full-rewrite tailoring behind tailor_full_rewrite

Default off — any handler invocation requesting depth='heavy' is silently
downgraded to 'moderate' until the flag is flipped on per-user. Hides the
full-rewrite prompt path (which carries fabrication risk per CLAUDE.md
Phase 2.6 backlog) from prod traffic. Light/moderate paths unchanged."
```

---

## Task 7: Frontend wrapper + provider — TDD (~20 min)

**Files:**
- Modify: `web/package.json`
- Create: `web/src/lib/featureFlags.ts`
- Create: `web/src/lib/featureFlags.test.tsx`
- Modify: `web/src/main.jsx`

The frontend wrapper exposes a single hook `useFeatureFlag(flag)` returning `boolean`. Internally it calls `posthog-js/react`'s `useFeatureFlagEnabled`. We mount `<PostHogProvider>` in `main.jsx`, identifying the user inside an effect that fires after `AuthProvider` resolves the Supabase session.

- [ ] **Step 1: Add the dependency**

Edit `web/package.json`. In the `dependencies` block, add `posthog-js`:

```json
  "dependencies": {
    "@supabase/supabase-js": "^2.100.0",
    "lucide-react": "^1.7.0",
    "posthog-js": "^1.180.0",
    "react": "^19.2.4",
    "react-dom": "^19.2.4",
    "react-router-dom": "^7.13.2",
    "zustand": "^5.0.12"
  },
```

In the `devDependencies` block, add `@testing-library/react`, `vitest`, `jsdom`:

```json
  "devDependencies": {
    "@eslint/js": "^9.39.4",
    "@tailwindcss/vite": "^4.2.2",
    "@testing-library/react": "^16.0.0",
    "@types/react": "^19.2.14",
    "@types/react-dom": "^19.2.3",
    "@vitejs/plugin-react": "^6.0.1",
    "eslint": "^9.39.4",
    "eslint-plugin-react-hooks": "^7.0.1",
    "eslint-plugin-react-refresh": "^0.5.2",
    "globals": "^17.4.0",
    "jsdom": "^25.0.0",
    "tailwindcss": "^4.2.2",
    "vite": "^8.0.1",
    "vitest": "^2.1.0"
  }
```

Add a `test` script:

```json
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "lint": "eslint .",
    "preview": "vite preview",
    "test": "vitest run"
  },
```

Install:

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/web && npm install
```

Expected: `added N packages`. No peer dep warnings on `posthog-js`.

- [ ] **Step 2: Write the failing test**

Create `web/src/lib/featureFlags.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { useFeatureFlag } from './featureFlags';

vi.mock('posthog-js/react', () => ({
  PostHogProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useFeatureFlagEnabled: vi.fn(),
}));

import { useFeatureFlagEnabled } from 'posthog-js/react';

function Probe({ flag }: { flag: string }) {
  const enabled = useFeatureFlag(flag);
  return <div data-testid="probe">{enabled ? 'on' : 'off'}</div>;
}

describe('useFeatureFlag', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns true when posthog reports the flag enabled', () => {
    (useFeatureFlagEnabled as ReturnType<typeof vi.fn>).mockReturnValue(true);
    render(<Probe flag="auto_apply" />);
    expect(screen.getByTestId('probe').textContent).toBe('on');
  });

  it('returns false when posthog reports the flag disabled', () => {
    (useFeatureFlagEnabled as ReturnType<typeof vi.fn>).mockReturnValue(false);
    render(<Probe flag="auto_apply" />);
    expect(screen.getByTestId('probe').textContent).toBe('off');
  });

  it('returns false when posthog returns undefined (flag not loaded yet)', () => {
    (useFeatureFlagEnabled as ReturnType<typeof vi.fn>).mockReturnValue(undefined);
    render(<Probe flag="auto_apply" />);
    expect(screen.getByTestId('probe').textContent).toBe('off');
  });
});
```

Add a vitest config at `web/vitest.config.ts`:

```typescript
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
  },
});
```

Run:

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/web && npm test
```

Expected: 3 FAIL with "Cannot find module './featureFlags'".

- [ ] **Step 3: Implement the wrapper**

Create `web/src/lib/featureFlags.ts`:

```typescript
/**
 * Frontend feature flag wrapper around posthog-js.
 *
 * - <FeatureFlagProvider> mounts <PostHogProvider> with the project key.
 *   When the env var is missing it renders children directly — flags
 *   default to off via the hook's nullish coalesce.
 * - useFeatureFlag(flag) returns boolean. Never undefined. Defaults to
 *   false when posthog hasn't loaded yet OR when the flag isn't defined.
 * - identifyUser(userId, email) ties events to the Supabase auth UUID
 *   so per-user rollouts work. Call after the Supabase session resolves.
 */
import posthog from 'posthog-js';
import { PostHogProvider, useFeatureFlagEnabled } from 'posthog-js/react';
import { useEffect, type ReactNode } from 'react';

const POSTHOG_KEY = import.meta.env.VITE_POSTHOG_KEY as string | undefined;
const POSTHOG_HOST = (import.meta.env.VITE_POSTHOG_HOST as string | undefined)
  ?? 'https://us.i.posthog.com';

let _initialized = false;

function ensureInit() {
  if (_initialized || !POSTHOG_KEY || typeof window === 'undefined') return;
  posthog.init(POSTHOG_KEY, {
    api_host: POSTHOG_HOST,
    // capture_pageview is handled by PostHogProvider; turn off auto-init churn.
    capture_pageview: false,
    // We don't need session replay yet — keeps bundle slim.
    disable_session_recording: true,
    persistence: 'localStorage+cookie',
  });
  _initialized = true;
}

export function FeatureFlagProvider({ children }: { children: ReactNode }) {
  ensureInit();
  if (!POSTHOG_KEY) {
    // No key configured — render children directly. useFeatureFlag returns false.
    return <>{children}</>;
  }
  return <PostHogProvider client={posthog}>{children}</PostHogProvider>;
}

/**
 * Identify the current user to PostHog. Call once per session, after Supabase
 * has resolved a user. Safe to call repeatedly with the same id.
 */
export function identifyUser(userId: string, email?: string) {
  if (!POSTHOG_KEY) return;
  ensureInit();
  posthog.identify(userId, email ? { email } : undefined);
}

/** Reset on logout so the next user doesn't inherit the previous identity. */
export function resetUser() {
  if (!POSTHOG_KEY) return;
  posthog.reset();
}

/**
 * React hook returning the boolean state of a feature flag.
 *
 * Always returns boolean. Returns false when:
 * - PostHog hasn't loaded yet
 * - The flag isn't defined in the project
 * - VITE_POSTHOG_KEY isn't configured (e.g. local dev without a PostHog account)
 */
export function useFeatureFlag(flag: string): boolean {
  const value = useFeatureFlagEnabled(flag);
  return value === true;
}

/**
 * Drop-in effect for AuthProvider: identify or reset based on session change.
 * Pass the Supabase user object (or null on logout).
 */
export function useIdentifyOnAuthChange(
  user: { id: string; email?: string } | null,
) {
  useEffect(() => {
    if (user?.id) {
      identifyUser(user.id, user.email);
    } else {
      resetUser();
    }
  }, [user?.id, user?.email]);
}
```

- [ ] **Step 4: Run tests to verify pass**

```bash
npm test
```

Expected: 3 PASS.

- [ ] **Step 5: Mount the provider in main.jsx**

Edit `web/src/main.jsx`:

```jsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { FeatureFlagProvider } from './lib/featureFlags'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <FeatureFlagProvider>
      <App />
    </FeatureFlagProvider>
  </StrictMode>,
)
```

- [ ] **Step 6: Wire `identifyUser` into the existing AuthProvider**

Edit `web/src/auth/AuthProvider.jsx`. Add the import:

```jsx
import { useIdentifyOnAuthChange } from '../lib/featureFlags'
```

Inside the component, after the existing `useState`/`useEffect` block, add:

```jsx
  useIdentifyOnAuthChange(user)
```

immediately before the `return (`.

- [ ] **Step 7: Smoke-build the frontend to verify the module resolves**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/web && npm run build
```

Expected: Vite build succeeds; no `posthog-js` resolution errors. Bundle size grows by ~80–100KB gzipped (the posthog-js client) — acceptable.

- [ ] **Step 8: Commit**

```bash
git add web/package.json web/package-lock.json web/src/lib/featureFlags.ts web/src/lib/featureFlags.test.tsx web/vitest.config.ts web/src/main.jsx web/src/auth/AuthProvider.jsx
git commit -m "feat(flags): add frontend useFeatureFlag hook + PostHogProvider

Mounts <FeatureFlagProvider> at app root. After Supabase resolves a user,
AuthProvider calls posthog.identify(user.id, {email}) so per-user rollouts
work. useFeatureFlag(flag) returns boolean, defaults to false when PostHog
hasn't loaded or VITE_POSTHOG_KEY isn't configured (graceful local-dev).

Bundle delta: ~80KB gzip (posthog-js). 3 vitest unit tests pass."
```

---

## Task 8: AutoApplyButton component (~10 min)

**Files:**
- Create: `web/src/components/AutoApplyButton.jsx`

The roadmap calls for `AutoApplyButton.jsx` to render disabled-with-tooltip when the flag is off. The component doesn't exist yet (Plan 3c will fully wire its callback into the WS flow). We create the shell here so the gate is in place from day one.

- [ ] **Step 1: Create the component**

Create `web/src/components/AutoApplyButton.jsx`:

```jsx
import { useFeatureFlag } from '../lib/featureFlags'

/**
 * Auto-apply button.
 *
 * Hidden visual treatment: when the auto_apply flag is off, the button
 * still renders so the layout doesn't shift, but it's disabled and shows
 * a tooltip explaining the gate. When on, click-handler runs.
 *
 * Plan 3c will wire onClick into the WS apply flow. For now the prop is
 * passed through.
 */
export default function AutoApplyButton({ jobId, onClick, disabled = false }) {
  const flagOn = useFeatureFlag('auto_apply')
  const effectivelyDisabled = !flagOn || disabled

  return (
    <button
      type="button"
      onClick={effectivelyDisabled ? undefined : () => onClick?.(jobId)}
      disabled={effectivelyDisabled}
      title={
        !flagOn
          ? 'Auto-apply is rolling out — your account will be enabled soon.'
          : undefined
      }
      className={
        effectivelyDisabled
          ? 'cursor-not-allowed rounded border-2 border-stone-300 bg-stone-100 px-4 py-2 text-stone-400'
          : 'cursor-pointer rounded border-2 border-black bg-yellow-300 px-4 py-2 font-bold text-black hover:bg-yellow-400'
      }
      data-testid="auto-apply-button"
      data-flag-on={flagOn ? 'true' : 'false'}
    >
      Auto-Apply
    </button>
  )
}
```

- [ ] **Step 2: Verify the build still passes**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/web && npm run build
```

Expected: build succeeds. The component is not yet imported anywhere — that wiring is Plan 3c — but it must compile cleanly so the merge is dark and safe.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/AutoApplyButton.jsx
git commit -m "feat(flags): add AutoApplyButton component gated on auto_apply

Renders a disabled button with explanatory tooltip when the flag is off,
active button when on. onClick is passed through; Plan 3c will wire it
into the WS apply flow. Component sits unimported for now — merging is
purely additive and dark."
```

---

## Task 9: CI / deploy.yml — pass PostHog env to Lambda (~10 min)

**Files:**
- Modify: `.github/workflows/deploy.yml`
- Modify: `template.yaml`

The Lambda code reads `POSTHOG_PROJECT_KEY` + `POSTHOG_PERSONAL_API_KEY` + `POSTHOG_HOST` from env. SAM must declare these as parameters and pass them through to every function that uses the wrapper.

- [ ] **Step 1: Add SAM parameters and globals**

Edit `template.yaml`. Find the existing `Parameters:` block and add (after the last existing parameter):

```yaml
  PostHogProjectKey:
    Type: String
    Description: PostHog project API key (phc_…); public, OK to ship
    NoEcho: false
    Default: ""
  PostHogPersonalApiKey:
    Type: String
    Description: PostHog personal API key for local flag eval (private)
    NoEcho: true
    Default: ""
  PostHogHost:
    Type: String
    Description: PostHog API host
    Default: "https://us.i.posthog.com"
```

Find `Globals.Function.Environment.Variables` and add three new keys at the end of the existing list:

```yaml
        POSTHOG_PROJECT_KEY: !Ref PostHogProjectKey
        POSTHOG_PERSONAL_API_KEY: !Ref PostHogPersonalApiKey
        POSTHOG_HOST: !Ref PostHogHost
```

(If the template has no `Globals.Function.Environment` block today, create one. Check first with `grep -n "^Globals:" template.yaml`.)

- [ ] **Step 2: Pass secrets through deploy.yml**

Edit `.github/workflows/deploy.yml`. In the `SAM Deploy` step's `env:` block, add:

```yaml
          PH_KEY: ${{ secrets.POSTHOG_PROJECT_KEY }}
          PH_PERSONAL: ${{ secrets.POSTHOG_PERSONAL_API_KEY }}
          PH_HOST: ${{ secrets.POSTHOG_HOST }}
```

In the same step's `--parameter-overrides` block, append:

```bash
              "PostHogProjectKey=${PH_KEY}" \
              "PostHogPersonalApiKey=${PH_PERSONAL}" \
              "PostHogHost=${PH_HOST}"
```

(If the existing trailing line is `"BrowserSubnetIds=${BROWSER_SUBNETS}"`, swap its trailing newline for ` \` and append the three new lines after it.)

- [ ] **Step 3: Validate the SAM template**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca && sam validate --lint
```

Expected: `template.yaml is a valid SAM Template`. Lint warnings on unrelated existing patterns are OK; new parameters must not be flagged.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/deploy.yml template.yaml
git commit -m "chore(flags): wire POSTHOG_* secrets through SAM deploy

Adds PostHogProjectKey, PostHogPersonalApiKey, PostHogHost as SAM parameters
and propagates them to every Lambda's environment via Globals.Function.
GitHub Actions reads them from repo secrets (POSTHOG_PROJECT_KEY,
POSTHOG_PERSONAL_API_KEY, POSTHOG_HOST) and passes via --parameter-overrides.

VITE_POSTHOG_KEY for the frontend is set per-site in Netlify (no CI change
needed — Netlify reads its own env on every build)."
```

---

## Task 10: Documentation — CLAUDE.md + ADR (~15 min)

**Files:**
- Modify: `CLAUDE.md`
- Create: `docs/superpowers/specs/2026-04-27-feature-flags-decision.md`

- [ ] **Step 1: Append the "Feature Flags" section to CLAUDE.md**

Open `CLAUDE.md`. After the existing `## Important Notes` section and before `## Backlog`, insert:

````markdown
## Feature Flags

NaukriBaba uses **PostHog** for feature flags. Five boolean flags are defined:

| Flag | Default | Gate point |
|---|---|---|
| `auto_apply` | off | `app.py:/api/apply/eligibility,/preview` + `lambdas/browser/ws_route.py` + `web/src/components/AutoApplyButton.jsx` |
| `council_scoring` | off | `lambdas/pipeline/score_batch.py` line 136 |
| `tailor_full_rewrite` | off | `lambdas/pipeline/tailor_resume.py` heavy-depth path |
| `scraper_glassdoor` | off | (gate added when scraper revives — currently dormant) |
| `scraper_gradireland` | off | (gate added when scraper fix lands — currently 0-job) |

Backend usage:

```python
from config.feature_flags import is_enabled

if is_enabled("auto_apply", user_id, default=False):
    ...
```

The `config.feature_flags.is_enabled` wrapper is local-eval (the SDK polls
PostHog every 30s). Per-call latency is zero-network. **Flag flips take up
to 30s to propagate** — tolerable for kill-switches; not for sub-second
controls. Wrapper returns `default` on any error (network, init, missing
keys) and never raises.

Frontend usage:

```tsx
import { useFeatureFlag } from '@/lib/featureFlags'

function MyComponent() {
  const enabled = useFeatureFlag('auto_apply')
  return enabled ? <RealUI /> : <FallbackUI />
}
```

`useFeatureFlag` always returns boolean. Returns `false` when PostHog
hasn't loaded yet, when the flag isn't defined, or when `VITE_POSTHOG_KEY`
isn't set (graceful local-dev fallback).

**Operating it:**
- Toggle a flag at <https://us.posthog.com/project/<your-project>/feature_flags>.
- For per-user rollouts, set the rollout condition to `email = '254utkarsh@gmail.com'` (or `id = <supabase uuid>`) and rollout=100% inside that condition.
- The kill-switch is rollout=0%. Within 30s every backend evaluator returns false.

**Tests:** unit tests stub the SDK by passing `_client=…` or via `monkeypatch.setattr("config.feature_flags.is_enabled", lambda …: <bool>)`. CI sets `NAUKRIBABA_FLAGS_DISABLED=1` so no test ever hits the network.

ADR: [docs/superpowers/specs/2026-04-27-feature-flags-decision.md](docs/superpowers/specs/2026-04-27-feature-flags-decision.md)
````

- [ ] **Step 2: Write the ADR**

Create `docs/superpowers/specs/2026-04-27-feature-flags-decision.md`:

```markdown
# ADR — Feature Flag Provider Selection (PostHog)

**Date:** 2026-04-27
**Status:** Accepted
**Context:** Phase 1 of the deployment-safety roadmap (`2026-04-27-deployment-safety-roadmap.md`) requires a feature-flag layer to decouple deploy from release. The roadmap names PostHog without a comparison; this ADR captures that selection.

## Options Considered

### 1. PostHog (chosen)
- **Pricing:** Free up to 1M events/mo and unlimited flags. NaukriBaba is currently a 1-user MVP; we're <1% of the free quota and likely will be for 6+ months.
- **SDK quality:** Official Python and JS SDKs with **local evaluation** (the Python SDK pulls flag definitions every 30s and evaluates rules in-process). Per-call latency is zero-network — critical for the score_batch and tailor_resume hot paths that run thousands of times per pipeline run.
- **Rollout primitives:** boolean + multivariate flags, percentage rollouts, conditional rollouts on user properties (email, ID, custom). Sufficient for our taxonomy.
- **Bundling:** posthog-js is ~80KB gzipped. Painful but tolerable.
- **Adjacent products:** product analytics + session replay + LLM analytics on the same project. We can plug in product analytics for free later without provider-shopping.
- **Auth model:** Two keys — public project key (frontend) + private personal key (backend, scoped `feature_flag:read`). Standard.
- **Vendor lock-in:** moderate — flag-API is straightforward (`feature_enabled(flag, distinct_id)`), abstracting it behind `config.feature_flags.is_enabled` keeps the migration cost low.

### 2. LaunchDarkly
- **Pricing:** Starter plan is **$10/seat/mo**, ~$120/year solo, scaling per developer. For a 1-user MVP this is the most expensive option by 1–2 orders of magnitude.
- **SDK quality:** Best-in-class. Streaming flag updates (sub-second propagation) instead of poll. Local-eval as well.
- **Rollout primitives:** the most sophisticated of the three (segment targeting, prerequisite flags, scheduled rollouts).
- **When to revisit:** if NaukriBaba ever reaches a paying-customer team-of-engineers stage where the per-developer pricing makes sense and sub-second flag propagation matters (it doesn't today).

### 3. Unleash (self-hosted)
- **Pricing:** Free if self-hosted; OSS cloud is $80/mo entry tier.
- **SDK quality:** Solid. Local eval. Slightly less polished docs than the above.
- **Operating cost:** running a Postgres-backed proxy on Fly.io or our own infra adds an availability dependency we don't want during phase 1. AWS-hosted Unleash on Fargate is ~$15/mo idle plus on-call burden.
- **When to revisit:** if PostHog's pricing model ever changes adversely or we hit data-residency requirements PostHog can't meet.

## Decision

**Choose PostHog.** The operating cost is zero, the SDK has local eval (matching LaunchDarkly's hot-path latency), the wrapper layer (`config.feature_flags`) keeps vendor lock-in cheap to escape, and we get product-analytics / LLM-analytics on the same project for free as side effects.

## Consequences

- **Positive:** $0/mo, one bag of credentials, one dashboard for flags + analytics.
- **Negative:** 30-second poll interval means kill-switches aren't sub-second — acceptable for our risk profile (every gated path also has a frontend disable, so the user experience is "click does nothing for ≤30s" at worst).
- **Operational:** rotation of the personal API key is manual — track in a 1Password rotation reminder. The public project key is, by design, in the frontend bundle and doesn't need rotation unless the project itself is compromised.

## Rollback Plan

If PostHog turns out wrong, replace `config.feature_flags.py` and `web/src/lib/featureFlags.ts` with implementations against the new vendor; call sites stay unchanged (they only know the wrapper API). Estimated migration: <1 day.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-04-27-feature-flags-decision.md
git commit -m "docs(flags): document PostHog usage + write ADR

CLAUDE.md gets a 'Feature Flags' section listing the 5 flags, their gate
points, and operator instructions. ADR documents the PostHog vs LaunchDarkly
vs Unleash trade-off — chose PostHog for free tier + local eval + product
analytics on the same project."
```

---

## Task 11: PR + deploy + manual smoke test (~30 min, includes user action)

- [ ] **Step 1: Push branch and open PR**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca
git push -u origin claude/objective-sanderson-eeedca
gh pr create --title "feat(flags): Phase 1 — PostHog feature flags (5 gates, default off)" --body "$(cat <<'EOF'
## Summary
- Backend wrapper `config/feature_flags.py` with `is_enabled()` + `@flag_gated()` (local-eval, never raises, env-aware no-op for tests)
- Frontend wrapper `web/src/lib/featureFlags.ts` with `<FeatureFlagProvider>` + `useFeatureFlag()` hook; identifies users via Supabase JWT
- Five gates wired: `auto_apply` (app.py + ws_route.py + AutoApplyButton.jsx), `council_scoring` (score_batch.py), `tailor_full_rewrite` (tailor_resume.py); `scraper_glassdoor` + `scraper_gradireland` flags defined in PostHog UI but gates land with their respective scraper revivals
- All flags default-off — merging is fully dark
- ADR: `docs/superpowers/specs/2026-04-27-feature-flags-decision.md`
- CLAUDE.md updated with Feature Flags section

Roadmap: `docs/superpowers/plans/2026-04-27-deployment-safety-roadmap.md` Phase 1.
Plan: `docs/superpowers/plans/2026-04-27-deployment-safety-phase1-flags.md`.

## Test plan
- [ ] CI green (unit + contract)
- [ ] After merge, deploy: `gh workflow run deploy.yml --ref main`
- [ ] Verify `POSTHOG_*` env vars set on `naukribaba-ws-route` Lambda via console
- [ ] curl `/api/apply/eligibility/<job_id>` with valid JWT → expect 503 `feature_disabled` (flag off)
- [ ] Flip `auto_apply` flag on for `254utkarsh@gmail.com` in PostHog UI
- [ ] Wait ≤30s, re-curl → expect 200 with eligibility response
- [ ] Flip flag off in PostHog UI, wait ≤30s, re-curl → expect 503

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: After CI green, merge via gh API**

Per session memory (Apr 26): `gh pr merge` from a worktree-owned branch fails. Use the API:

```bash
gh api -X PUT "repos/UT07/daily-job-hunt/pulls/<NUMBER>/merge" -f merge_method=squash
```

(Replace `<NUMBER>` with the PR number from step 1.)

- [ ] **Step 3: Trigger deploy on main**

```bash
gh workflow run deploy.yml --ref main
gh run watch $(gh run list --workflow=deploy.yml --branch=main --limit=1 --json databaseId -q '.[0].databaseId') --exit-status
```

Expected: deploy succeeds (~5–8 min). Layer rebuilt; all 28 Lambdas redeployed with PostHog env vars.

- [ ] **Step 4: Manual end-to-end smoke (the success criterion from the roadmap)**

Get a fresh JWT from your browser. Pick any S/A-tier job_id from the dashboard.

```bash
JWT='<paste fresh token>'
JOB_ID='<paste job_id>'
API='https://paie9w92c1.execute-api.eu-west-1.amazonaws.com/prod'

# 4a. Flag off (default) → expect 503 feature_disabled
curl -sS -H "Authorization: Bearer $JWT" "$API/api/apply/eligibility/$JOB_ID" | python3 -m json.tool
```

Expected output:
```json
{ "detail": "feature_disabled" }
```

Now go to <https://us.posthog.com/project/<your-project>/feature_flags/auto_apply>, click `Edit`, set rollout condition `email = '254utkarsh@gmail.com'` to `100%`, save. Wait 30 seconds.

```bash
# 4b. Flag on for you → expect normal eligibility response
curl -sS -H "Authorization: Bearer $JWT" "$API/api/apply/eligibility/$JOB_ID" | python3 -m json.tool
```

Expected output:
```json
{ "eligible": true, "platform": "greenhouse" | null, "board_token": "...", "posting_id": "..." }
```

Now flip the flag back off (rollout=0%) and wait 30 seconds.

```bash
# 4c. Flag off again → expect 503 again
curl -sS -H "Authorization: Bearer $JWT" "$API/api/apply/eligibility/$JOB_ID" | python3 -m json.tool
```

Expected output: `{ "detail": "feature_disabled" }` again.

If all three steps return the expected payloads within their windows, **Phase 1 is shipped**.

- [ ] **Step 5: Update memory**

Append to `~/.claude/projects/-Users-ut-code-naukribaba/memory/MEMORY.md`:

```
- [Session Apr 27 — Phase 1 Flags](session_2026_04_27_flags.md) — PostHog wrapper shipped, 5 flags defined, auto_apply gate verified live; council_scoring + tailor_full_rewrite default-off in prod
```

---

## Future Enhancements (Cross-Phase Notes — do NOT build here)

- **Phase 4 (Observability):** the structured logger will emit a `flags_evaluated` field per request listing which flags were checked and their resolved values. Implementation lives in Phase 4's `observability.py`; do not add log lines here. The wrapper's `is_enabled` already returns a clean boolean — Phase 4 just needs to wrap call sites with a context binding.
- **Phase 5 (Sentry):** the Supabase user identification pattern from Task 7 (`useIdentifyOnAuthChange`) is the same shape Sentry needs. Phase 5 will add a sibling `useSentryIdentify(user)` and call it next to this one. Do not import sentry here — keep wrappers independent.
- **Multivariate flags:** PostHog supports `get_feature_flag_payload` for multi-value rollouts (e.g., a model-name string per user). Out of scope for v1; revisit when we have an A-B testing use case with stat-sig traffic (post-launch, per Phase 2.5 roadmap).
- **Cleanup of stale flags:** PostHog has a built-in stale-flag detector. Add a quarterly review cadence to `docs/runbooks/quarterly-flag-cleanup.md` once Phase 6 lands the rollback runbook.

---

## Self-Review

**1. Spec coverage check** (against the roadmap's Phase 1 task list):

| Roadmap task | Plan task |
|---|---|
| Create the PostHog project + capture API keys | Manual Prerequisite block |
| Backend wrapper + tests | Tasks 1, 2 |
| Wire flag-gates in 4 lambdas + `app.py` | Tasks 3, 4, 5, 6 |
| Frontend provider + hook + tests | Task 7 |
| Wire `AutoApplyButton` | Task 8 |
| CI env vars (`deploy.yml` + Netlify) | Task 9 + Manual Prerequisite |
| Documentation (CLAUDE.md + ADR) | Task 10 |

All seven roadmap items mapped. Smoke verification (success criteria) covered in Task 11. ✓

**2. Placeholder scan:** Searched for "TBD", "TODO", "fill in", "implement appropriate", "etc." — none present in any code block. The two `<NUMBER>` and `<paste …>` placeholders in Task 11 are user-action substitutions, expected. ✓

**3. Type/name consistency:**
- Flag names spelled identically across backend (`auto_apply`, `council_scoring`, `tailor_full_rewrite`) and frontend (`useFeatureFlag('auto_apply')`).
- `is_enabled(flag, user_id, default, _client)` signature matches across Tasks 1, 2, 3, 4, 5, 6.
- `useFeatureFlag(flag): boolean` consistent across Tasks 7, 8.
- `@flag_gated('flag_name')` not actually used in the FastAPI sites (Tasks 3 use the inline form because `Depends(get_current_user)` doesn't expose `user_id` as a kwarg directly); it remains tested in Task 1 for future internal-helper use cases.
- `POSTHOG_PROJECT_KEY` / `POSTHOG_PERSONAL_API_KEY` / `POSTHOG_HOST` env names match across CI (Task 9), `template.yaml` (Task 9), and `config/feature_flags.py` (Task 2). ✓

**4. Dependency ordering:** Tasks 1→2→{3,4,5,6}→{7,8}→9→10→11 — backend wrapper before any gate site, frontend wrapper before button, CI wiring before deploy, deploy before live smoke. Tasks 3-6 (the four gate sites) can run in parallel after Task 2 lands; Task 7 is independent of 3-6. ✓

**5. Realism:** ~2.5 hours of focused work for a fresh agent (15+20+10+10+10+10+20+10+10+15 = ~130 min implementation), plus ~30 min for the manual prereq + smoke verification. Roadmap budgeted "~1 day"; this fits comfortably. ✓
