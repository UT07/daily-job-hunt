# Phase 6 — Smoke Tests + Rollback Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Author backend (pytest+httpx) and frontend (Playwright) synthetic smoke tests, run them automatically against staging on every PR and against prod after each canary, and wire a CodeDeploy `PreTraffic` Lambda hook so a failed smoke aborts the deployment before traffic shifts. End state: a bad merge to `main` is auto-reverted within 10 minutes with no human in the loop.

**Architecture:** Three independent test surfaces, one orchestration layer.
1. `tests/smoke/` — pytest suite hitting live HTTP/WebSocket/boto3 against `${SMOKE_TARGET}` (staging|prod). Single conftest reads `SMOKE_TARGET` env var and resolves `BASE_URL`, `WS_URL`, `SUPABASE_URL`, fixture user JWT.
2. `web/tests/smoke/` — Playwright single-spec critical path (login → dashboard → preview → apply-button-visible).
3. `lambdas/pipeline/canary_prehook.py` — CodeDeploy `PreTraffic` Lambda that runs the *health* subset inline (≈5s), reports back via `boto3 codedeploy.put_lifecycle_event_hook_execution_status`. Deeper smoke runs from `.github/workflows/smoke.yml` (called from `deploy.yml` after staging deploy and after prod canary completes).

**Tech Stack:** Python 3.12, pytest, httpx, websockets (Python lib), supabase-py, boto3, Playwright (Chromium only), GitHub Actions (`workflow_call`), AWS CodeDeploy lifecycle hooks, AWS SAM `DeploymentPreference.Hooks.PreTraffic`.

**Spec:** [2026-04-27-deployment-safety-roadmap.md § Phase 6](2026-04-27-deployment-safety-roadmap.md#phase-6--smoke-tests--rollback-wiring)

**Phase dependencies (assumed merged before this plan executes):**
- **Phase 2** (canary): every critical-tier `AWS::Serverless::Function` in `template.yaml` already has `AutoPublishAlias: live` and a `DeploymentPreference` block with `Type:` and `Alarms:`. Phase 6 adds **only** the `Hooks.PreTraffic` field — never touches `Type` or `Alarms`. Critical-tier functions per Phase 2 spec: `WsRouteFunction`, `TailorResumeFunction`, `CompileLatexFunction`, `SaveJobFunction`, `GenerateCoverLetterFunction`.
- **Phase 3** (staging): `Stage` parameter exists in `template.yaml`, stack name is `naukribaba-${Stage}`, `STAGING_URL` and `PROD_URL` GitHub env vars are populated, `web/supabase/seed.sql` exists. Phase 6 *appends* canonical fixture rows; does not create the file.
- **Phase 4** (observability): `Naukribaba/${Stage}` EMF namespace and composite alarms exist. **Soft dependency** — if Phase 4 has not landed when Phase 6 starts, `test_apply_synthetic.py`'s metric-based assertions fall back to default Lambda metrics; the EMF assertion is gated behind a `_phase4_available()` check (see Task 4) and skipped otherwise. The plan ships either way.

**Pulled out of scope (sent to backlog):**
- "Post-Traffic" hook variant for deeper smoke after traffic flip — Phase 7 candidate.
- Smoke-failure budget tracking dashboard (informally tracked in PR review until enough signal exists).
- Slack alerting on smoke failures (email + GitHub PR check tab is enough for v1).

---

## File Structure

```
tests/smoke/
  __init__.py                                 (CREATE) marker, no code
  conftest.py                                 (CREATE) base_url, http_client, supabase_admin, ws_client fixtures, env-aware
  fixtures.py                                 (CREATE) canonical fixture IDs (FIXTURE_USER_EMAIL, FIXTURE_JOB_ID, ...) referenced by tests + seed.sql
  test_health.py                              (CREATE) /healthz + /api/auth/health + latency budget
  test_apply_synthetic.py                     (CREATE) end-to-end apply over WS against fixture job, asserts apply_attempts row
  test_pipeline_smoke.py                      (CREATE) boto3 invoke naukribaba-${Stage}-score-batch with 1-job event
  requirements.txt                            (CREATE) pinned smoke-only deps
web/tests/smoke/
  playwright.config.ts                        (CREATE) base URL from env, chromium only, traces on failure
  critical-paths.spec.ts                      (CREATE) login → dashboard → preview → apply-visible (no click)
web/package.json                              (MODIFY) add @playwright/test devDep + smoke scripts
lambdas/pipeline/
  canary_prehook.py                           (CREATE) CodeDeploy PreTraffic handler — inline health smoke, ≈5s
  requirements-canary-prehook.txt             (CREATE) httpx-only (lightweight Lambda layer)
template.yaml                                 (MODIFY) add CanaryPrehookFunction + Hooks.PreTraffic on 5 critical-tier functions + IAM permission
.github/workflows/
  smoke.yml                                   (CREATE) reusable workflow_call: setup → pytest tests/smoke → upload artifacts on failure
  deploy.yml                                  (MODIFY) call smoke.yml after staging deploy (block PR merge) and after prod deploy (post-canary fuller coverage)
web/supabase/
  seed.sql                                    (MODIFY) append canonical fixture user + fixture job rows referenced by fixtures.py
docs/runbooks/
  rollback.md                                 (CREATE) 5-step rollback ladder from roadmap, with exact commands per step
```

---

## Decision: PreTraffic hook strategy

Two options from the roadmap for `canary_prehook.py`:

- **A. Inline httpx health probe in Lambda** — ≈5s round trip, no external auth, fully self-contained.
- **B. Trigger `smoke.yml` via GitHub workflow_dispatch + poll** — 60–180s, needs a PAT in SSM.

**Pick A for v1.** Health-only smoke is fast and self-contained. B becomes attractive later as a "post-traffic" hook running the full suite — out of scope here.

This decision affects ONE file (`canary_prehook.py`). It does not propagate.

---

## Task 0: Pre-flight verification of phase dependencies

**Files:** none (read-only)

- [ ] **Step 1: Confirm Phase 2 canary block is present on at least one critical-tier function**

Run: `grep -A 5 "AutoPublishAlias: live" /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/template.yaml | head -30`

Expected: at least one match showing `AutoPublishAlias: live` followed by a `DeploymentPreference:` block with `Type:` and `Alarms:`. If zero matches, **stop** — Phase 2 hasn't landed; this plan cannot wire `Hooks.PreTraffic` onto something that doesn't exist.

- [ ] **Step 2: Confirm Phase 3 `Stage` parameter and staging URL exist**

Run: `grep -n "^  Stage:" /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/template.yaml`

Expected: a match at the top-level `Parameters:` section. If absent, stop — staging stack split hasn't happened.

Run: `gh secret list --repo UT07/daily-job-hunt | grep -E "STAGING_URL|PROD_URL"`

Expected: both `STAGING_URL` and `PROD_URL` present. If either is missing, stop — Phase 3 hasn't published its outputs.

- [ ] **Step 3: Confirm `web/supabase/seed.sql` exists**

Run: `ls -la /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/web/supabase/seed.sql`

Expected: file present (created by Phase 3). If missing, stop — Phase 6 appends to this file rather than creating it.

- [ ] **Step 4: Note Phase 4 status (soft dependency)**

Run: `aws cloudwatch list-metrics --namespace "Naukribaba/staging" --max-items 5 2>/dev/null | head -20`

Expected: either some metrics listed (Phase 4 done) **or** empty result (Phase 4 not yet done). Either is fine for this plan; just record which case applies. If Phase 4 is done, the optional metric-based assertions in Task 4 are enabled; otherwise they're auto-skipped.

Estimate: 5 min

---

## Task 1: Scaffold `tests/smoke/` directory + fixtures.py

**Files:**
- Create: `tests/smoke/__init__.py`
- Create: `tests/smoke/fixtures.py`
- Create: `tests/smoke/requirements.txt`

- [ ] **Step 1: Create `tests/smoke/__init__.py`**

```python
"""Synthetic smoke tests run against a live deployment (staging or prod).

Run: SMOKE_TARGET=staging pytest tests/smoke -v

Required env vars (set by CI in smoke.yml):
    {STAGING,PROD}_URL                  — API Gateway base URL
    {STAGING,PROD}_WS_URL               — WebSocket URL (wss://...)
    {STAGING,PROD}_SUPABASE_URL         — assertion-side reads
    {STAGING,PROD}_SUPABASE_SERVICE_KEY — service-role key (read-only use)
    {STAGING,PROD}_FIXTURE_JWT          — pre-minted JWT for fixture user

Flake-allergic: a red smoke means real prod issue, not test infra trouble.
3x flakes in a quarter without real cause → retire the test.
"""
```

- [ ] **Step 2: Create `tests/smoke/fixtures.py`**

```python
"""Canonical IDs for smoke-test fixture rows seeded into staging Supabase.

These values MUST stay in sync with web/supabase/seed.sql (Task 9 appends
the matching INSERTs). Treat both files as a single contract: changing an ID
here without updating seed.sql breaks every smoke test on the next deploy.
"""
from __future__ import annotations

# Fixture user. Email is the primary lookup; UUID is stable across re-seeds.
FIXTURE_USER_EMAIL = "test+smoke@naukribaba.com"
FIXTURE_USER_ID = "00000000-0000-4000-8000-000000000001"

# Fixture job. apply_url points to a public test endpoint that returns 200
# without actually accepting an application — safe for CI to hit.
FIXTURE_JOB_ID = "00000000-0000-4000-8000-0000000000a1"
FIXTURE_JOB_TITLE = "Smoke Test Engineer"
FIXTURE_JOB_COMPANY = "NaukriBaba Smoke Co"
FIXTURE_JOB_APPLY_URL = "https://example.com/apply"

# Resume version that the seed pipeline pre-tailors. resume_s3_key is implicitly
# set by the seed so apply eligibility passes without running the tailoring chain.
FIXTURE_RESUME_VERSION = 1
FIXTURE_RESUME_S3_KEY = "smoke/test-smoke/resume-v1.pdf"
```

- [ ] **Step 3: Create `tests/smoke/requirements.txt`**

Pin to versions matching parent project where possible. The repo's `requirements.txt` already has `httpx>=0.27.0` and `supabase>=2.6.0`; reuse those constraints.

```text
# Smoke-test only dependencies. Installed in CI via:
#   pip install -r tests/smoke/requirements.txt
# Must NOT pin app code — these run against the deployed system, not the local checkout.
pytest>=8.0.0
pytest-timeout>=2.3.0
httpx>=0.27.0
websockets>=12.0
supabase>=2.6.0
boto3>=1.34.0
```

- [ ] **Step 4: Commit**

```bash
git add tests/smoke/__init__.py tests/smoke/fixtures.py tests/smoke/requirements.txt
git commit -m "feat(smoke): scaffold tests/smoke directory with canonical fixtures

Phase 6 of the deployment safety roadmap. fixtures.py declares the canonical
IDs (user, job, resume version) seeded into staging via seed.sql.
requirements.txt pins minimal smoke deps separate from app deps."
```

Estimate: 10 min

---

## Task 2: Build `tests/smoke/conftest.py`

**Files:**
- Create: `tests/smoke/conftest.py`

The conftest is the smoke layer's only piece of shared infrastructure. Five fixtures, all session-scoped (smoke runs in <3 min, no point re-instantiating clients per test).

- [ ] **Step 1: Create the conftest with all fixtures**

```python
"""Shared fixtures for smoke tests. Session-scoped — clients reused across tests.

Skips entire module if SMOKE_TARGET is not set (avoids accidentally running
smoke against the wrong env when developers run `pytest tests/`).
"""
from __future__ import annotations

import os
from typing import Iterator

import boto3
import httpx
import pytest

# Optional imports: only loaded when fixtures are actually requested.
# Keeps `pytest --collect-only` cheap.


def _target() -> str:
    target = os.environ.get("SMOKE_TARGET")
    if not target:
        pytest.skip("SMOKE_TARGET env var not set — refusing to run smoke", allow_module_level=True)
    if target not in ("staging", "prod"):
        pytest.fail(f"SMOKE_TARGET must be 'staging' or 'prod', got {target!r}")
    return target


def _envvar(target: str, key: str) -> str:
    """Read a per-target env var: staging→STAGING_<KEY>, prod→PROD_<KEY>."""
    full = f"{target.upper()}_{key}"
    val = os.environ.get(full)
    if not val:
        pytest.fail(f"Required env var {full} is not set for SMOKE_TARGET={target}")
    return val


@pytest.fixture(scope="session")
def smoke_target() -> str:
    return _target()


@pytest.fixture(scope="session")
def base_url(smoke_target: str) -> str:
    return _envvar(smoke_target, "URL").rstrip("/")


@pytest.fixture(scope="session")
def ws_url(smoke_target: str) -> str:
    return _envvar(smoke_target, "WS_URL").rstrip("/")


@pytest.fixture(scope="session")
def fixture_jwt(smoke_target: str) -> str:
    return _envvar(smoke_target, "FIXTURE_JWT")


@pytest.fixture(scope="session")
def http_client(base_url: str, fixture_jwt: str) -> Iterator[httpx.Client]:
    """Authenticated httpx.Client. 10s default timeout — smoke is allergic to slow."""
    with httpx.Client(
        base_url=base_url,
        timeout=httpx.Timeout(10.0, connect=5.0),
        headers={"Authorization": f"Bearer {fixture_jwt}"},
    ) as client:
        yield client


@pytest.fixture(scope="session")
def supabase_admin(smoke_target: str):
    """Admin Supabase client for assertion-side reads.

    Uses service-role key — read-only conventions enforced by code review,
    not by the client itself. Tests must NOT write via this fixture except
    in cleanup paths explicitly noted as such.
    """
    from supabase import create_client

    url = _envvar(smoke_target, "SUPABASE_URL")
    key = _envvar(smoke_target, "SUPABASE_SERVICE_KEY")
    return create_client(url, key)


@pytest.fixture(scope="session")
def stage_name(smoke_target: str) -> str:
    """Maps SMOKE_TARGET to the SAM Stage parameter (Phase 3 convention)."""
    return smoke_target  # 'staging' | 'prod'


@pytest.fixture(scope="session")
def lambda_client():
    """boto3 Lambda client. Region eu-west-1 per project convention."""
    return boto3.client("lambda", region_name="eu-west-1")


@pytest.fixture(scope="session")
def cloudwatch_client():
    return boto3.client("cloudwatch", region_name="eu-west-1")
```

- [ ] **Step 2: Commit**

```bash
git add tests/smoke/conftest.py
git commit -m "feat(smoke): conftest with env-driven base_url, http_client, supabase_admin

Skips the whole module when SMOKE_TARGET is unset (developer-friendly:
running plain 'pytest tests/' won't accidentally hit staging or prod)."
```

Estimate: 15 min

---

## Task 3: `tests/smoke/test_health.py` (3 tests, ≈30 LOC)

**Files:**
- Create: `tests/smoke/test_health.py`

Health checks are the *only* smoke that runs inside the PreTraffic Lambda (Task 7), so this file's bytes are also part of `canary_prehook.py`'s deps. Keep it imports-light.

- [ ] **Step 1: Write the file**

```python
"""Health-tier smoke. These three tests also run inline in canary_prehook
Lambda — keep them httpx-only and no fixtures beyond http_client + base_url.
"""
from __future__ import annotations

import time

import httpx
import pytest


@pytest.mark.timeout(15)
def test_healthz_returns_200(http_client: httpx.Client):
    """Public health endpoint. Phase 4 may rename to /healthz; today /api/health."""
    r = http_client.get("/api/health")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert body.get("status") == "ok", f"Expected status=ok, got {body!r}"


@pytest.mark.timeout(15)
def test_auth_health_returns_200(http_client: httpx.Client):
    """Authenticated probe. /api/profile is the cheapest auth-required endpoint
    that exercises Supabase JWT verification + DB roundtrip."""
    r = http_client.get("/api/profile")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text[:200]}"


@pytest.mark.timeout(15)
def test_health_latency_under_1s(base_url: str, fixture_jwt: str):
    """No fixture http_client — we want a fresh connection to measure cold path."""
    start = time.perf_counter()
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        r = c.get("/api/health")
    elapsed = time.perf_counter() - start
    assert r.status_code == 200
    assert elapsed < 1.0, f"/api/health took {elapsed:.3f}s, budget is 1.0s"
```

- [ ] **Step 2: Commit**

```bash
git add tests/smoke/test_health.py
git commit -m "feat(smoke): health-tier checks (200 + auth probe + <1s budget)

These three tests are also packaged into canary_prehook Lambda for
PreTraffic gating — keeping them httpx-only is a hard constraint."
```

Estimate: 15 min

---

## Task 4: `tests/smoke/test_apply_synthetic.py` (≈90 LOC, hardest test)

**Files:**
- Create: `tests/smoke/test_apply_synthetic.py`

This test exercises the full auto-apply path: HTTP POST → WebSocket subscription → DB row inserted. Highest-risk for flakiness, so it gets explicit timeouts at every layer plus a deterministic cleanup.

- [ ] **Step 1: Confirm fixtures.py exports the IDs the test needs**

Re-read `tests/smoke/fixtures.py` (Task 1). The test references `FIXTURE_USER_ID`, `FIXTURE_JOB_ID`, `FIXTURE_JOB_APPLY_URL`. All present.

- [ ] **Step 2: Write the test**

```python
"""End-to-end apply smoke: HTTP /api/apply/start-session → WS → DB row.

Crosses 4 systems (FastAPI / Fargate / DynamoDB sessions / Supabase
apply_attempts). Cleanup deletes the inserted row before returning.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest
import websockets
from websockets.sync.client import connect as ws_connect

from tests.smoke.fixtures import FIXTURE_JOB_ID, FIXTURE_USER_ID

WS_RECEIVE_TIMEOUT = 30.0  # seconds — generous for Fargate cold start
TOTAL_TIMEOUT = 90.0       # hard ceiling for the whole test


def _delete_apply_row(supabase_admin, attempt_id: str) -> None:
    """Cleanup helper. Service-key delete; runs even if assertions failed."""
    supabase_admin.table("apply_attempts").delete().eq("id", attempt_id).execute()


@pytest.mark.timeout(TOTAL_TIMEOUT)
def test_apply_synthetic_round_trip(
    http_client: httpx.Client,
    ws_url: str,
    fixture_jwt: str,
    supabase_admin,
):
    # 1. Start session — HTTP returns session_id + ws_token
    start_resp = http_client.post(
        "/api/apply/start-session",
        json={"job_id": FIXTURE_JOB_ID},
    )
    assert start_resp.status_code == 200, (
        f"start-session failed: {start_resp.status_code} {start_resp.text[:300]}"
    )
    body = start_resp.json()
    session_id = body["session_id"]
    ws_token = body["ws_token"]
    assert session_id, "Missing session_id in start-session response"
    assert ws_token, "Missing ws_token in start-session response"

    # 2. Connect WebSocket and wait for apply.completed event
    full_ws_url = f"{ws_url}?token={ws_token}"
    completed_event: dict[str, Any] | None = None
    deadline = time.monotonic() + WS_RECEIVE_TIMEOUT

    try:
        with ws_connect(full_ws_url, open_timeout=10) as ws:
            while time.monotonic() < deadline:
                try:
                    raw = ws.recv(timeout=5.0)
                except TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed as e:
                    pytest.fail(f"WS closed before apply.completed: {e}")
                msg = json.loads(raw)
                if msg.get("event") == "apply.completed":
                    completed_event = msg
                    break
                # Other events (apply.progress, apply.field_filled, ...) are
                # informational; ignore and keep listening.
    except Exception as e:
        pytest.fail(f"WS connection or recv failed: {type(e).__name__}: {e}")

    assert completed_event is not None, (
        f"No apply.completed event received within {WS_RECEIVE_TIMEOUT}s "
        f"(session_id={session_id})"
    )

    # 3. Assert the apply_attempts row exists
    rows = (
        supabase_admin.table("apply_attempts")
        .select("id, user_id, job_id, status")
        .eq("session_id", session_id)
        .execute()
    )
    assert rows.data, f"No apply_attempts row for session_id={session_id}"
    assert len(rows.data) == 1, f"Expected 1 row, got {len(rows.data)}"
    row = rows.data[0]
    assert row["status"] == "completed", f"Expected status=completed, got {row['status']!r}"
    assert row["user_id"] == FIXTURE_USER_ID
    assert row["job_id"] == FIXTURE_JOB_ID

    # 4. Cleanup — even if a later assertion fails, the test framework would
    # not run cleanup; instead we delete BEFORE checks that could throw
    # (already passed above) and the test is now safe to re-run.
    _delete_apply_row(supabase_admin, row["id"])


def _phase4_available(cloudwatch_client, stage_name: str) -> bool:
    """Detect Phase 4 EMF namespace presence. Cheap list call."""
    try:
        resp = cloudwatch_client.list_metrics(
            Namespace=f"Naukribaba/{stage_name}", MaxRecords=1,
        )
        return bool(resp.get("Metrics"))
    except Exception:
        return False


@pytest.mark.timeout(30)
def test_apply_metric_emitted_when_phase4_present(
    cloudwatch_client, stage_name: str,
):
    """Optional: if Phase 4 EMF is live, assert the apply_attempted counter
    has at least one datapoint in the last 5 min. Skipped otherwise."""
    if not _phase4_available(cloudwatch_client, stage_name):
        pytest.skip("Phase 4 EMF namespace not present — assertion not yet enabled")

    from datetime import datetime, timedelta, timezone
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=5)
    resp = cloudwatch_client.get_metric_statistics(
        Namespace=f"Naukribaba/{stage_name}",
        MetricName="apply_attempted",
        StartTime=start,
        EndTime=end,
        Period=60,
        Statistics=["Sum"],
    )
    points = resp.get("Datapoints", [])
    assert points, "Expected at least one apply_attempted datapoint in last 5 min"
    assert any(p["Sum"] >= 1 for p in points), f"All datapoints zero: {points!r}"
```

- [ ] **Step 3: Run against staging from local machine to validate**

```bash
cd /Users/ut/code/naukribaba && source .venv/bin/activate
pip install -r tests/smoke/requirements.txt
export SMOKE_TARGET=staging
export STAGING_URL='<from staging stack output>'
export STAGING_WS_URL='<from staging stack output>'
export STAGING_SUPABASE_URL='<from 1Password>'
export STAGING_SUPABASE_SERVICE_KEY='<from 1Password>'
export STAGING_FIXTURE_JWT='<minted via supabase auth or pre-baked into seed>'
pytest tests/smoke/test_apply_synthetic.py -v
```

Expected: PASS (assuming Task 9's seed.sql has been applied to staging — if seed isn't applied yet, this validates the test's error messaging instead). The phase-4-gated test should `SKIP` if Phase 4 hasn't merged.

- [ ] **Step 4: Commit**

```bash
git add tests/smoke/test_apply_synthetic.py
git commit -m "feat(smoke): synthetic apply round-trip (HTTP → WS → DB)

Hardest smoke test. Crosses FastAPI / WS / Fargate / Supabase. Cleanup runs
inline before the test returns. Phase 4 EMF assertion is opt-in via a runtime
check on Naukribaba/\${Stage} namespace presence."
```

Estimate: 60 min

---

## Task 5: `tests/smoke/test_pipeline_smoke.py` (≈40 LOC)

**Files:**
- Create: `tests/smoke/test_pipeline_smoke.py`

- [ ] **Step 1: Write the test**

```python
"""Pipeline smoke: invoke the score-batch Lambda directly with a 1-job
fixture event and assert a score row landed in Supabase.

Why direct invoke (not API): score_batch is normally called by Step Functions,
not API Gateway. Synchronous invoke from boto3 is the cheapest way to prove
the function works end-to-end (cold start + AI provider call + DB write).
"""
from __future__ import annotations

import json
import uuid

import pytest

from tests.smoke.fixtures import FIXTURE_USER_ID

PIPELINE_TIMEOUT = 60


@pytest.mark.timeout(PIPELINE_TIMEOUT)
def test_score_batch_invoke_round_trip(
    lambda_client, supabase_admin, stage_name: str,
):
    function_name = f"naukribaba-{stage_name}-score-batch"
    test_job_hash = f"smoke-{uuid.uuid4().hex[:12]}"
    payload = {
        "user_id": FIXTURE_USER_ID,
        "jobs": [{
            "job_hash": test_job_hash,
            "title": "Smoke Test Backend Engineer",
            "company": "NaukriBaba Smoke Co",
            "description": "Python, FastAPI, AWS Lambda. 3+ years experience.",
            "location": "Dublin",
            "apply_url": "https://example.com/apply",
            "source": "smoke",
        }],
    }
    resp = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode(),
    )
    assert resp["StatusCode"] == 200, f"Lambda invoke status {resp['StatusCode']}"
    response_payload = json.loads(resp["Payload"].read())
    assert response_payload.get("FunctionError") is None, (
        f"Lambda raised: {response_payload}"
    )

    # Assert the score landed
    rows = (
        supabase_admin.table("jobs")
        .select("job_id, match_score, match_reasoning")
        .eq("job_hash", test_job_hash)
        .execute()
    )
    assert rows.data, f"No row for job_hash={test_job_hash}"
    assert rows.data[0]["match_score"] is not None, "match_score is null"

    # Cleanup
    supabase_admin.table("jobs").delete().eq("job_hash", test_job_hash).execute()
```

- [ ] **Step 2: Commit**

```bash
git add tests/smoke/test_pipeline_smoke.py
git commit -m "feat(smoke): pipeline-tier — boto3 invoke score-batch with 1-job fixture

Proves cold start + AI scoring + DB write. Cleanup deletes the row by
job_hash so the test is re-runnable."
```

Estimate: 20 min

---

## Task 6: Frontend Playwright smoke

**Files:**
- Create: `web/tests/smoke/playwright.config.ts`
- Create: `web/tests/smoke/critical-paths.spec.ts`
- Modify: `web/package.json`

- [ ] **Step 1: Add `@playwright/test` to `web/package.json`**

Open `web/package.json`. Find `devDependencies` block (lines 20-32):

```json
  "devDependencies": {
    "@eslint/js": "^9.39.4",
    "@tailwindcss/vite": "^4.2.2",
    "@types/react": "^19.2.14",
    "@types/react-dom": "^19.2.3",
    "@vitejs/plugin-react": "^6.0.1",
    "eslint": "^9.39.4",
    "eslint-plugin-react-hooks": "^7.0.1",
    "eslint-plugin-react-refresh": "^0.5.2",
    "globals": "^17.4.0",
    "tailwindcss": "^4.2.2",
    "vite": "^8.0.1"
  }
```

Add `@playwright/test` (alphabetical ordering preserved):

```json
  "devDependencies": {
    "@eslint/js": "^9.39.4",
    "@playwright/test": "^1.48.0",
    "@tailwindcss/vite": "^4.2.2",
    "@types/react": "^19.2.14",
    "@types/react-dom": "^19.2.3",
    "@vitejs/plugin-react": "^6.0.1",
    "eslint": "^9.39.4",
    "eslint-plugin-react-hooks": "^7.0.1",
    "eslint-plugin-react-refresh": "^0.5.2",
    "globals": "^17.4.0",
    "tailwindcss": "^4.2.2",
    "vite": "^8.0.1"
  },
```

Then in the `scripts` block (lines 6-11), add a smoke runner:

Find:

```json
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "lint": "eslint .",
    "preview": "vite preview"
  },
```

Replace with:

```json
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "lint": "eslint .",
    "preview": "vite preview",
    "smoke": "playwright test --config=tests/smoke/playwright.config.ts",
    "smoke:install": "playwright install chromium --with-deps"
  },
```

- [ ] **Step 2: Run `npm install` to update lockfile**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/web && npm install
```

Expected: `package-lock.json` updated with `@playwright/test` and transitive deps.

- [ ] **Step 3: Create `web/tests/smoke/playwright.config.ts`**

```typescript
/**
 * Playwright smoke config — chromium-only, fast, traces on failure.
 *
 * Base URL is read from STAGING_URL or PROD_URL depending on SMOKE_TARGET.
 * The frontend smoke target is the Netlify branch deploy URL, NOT the API
 * Gateway URL — those differ per Phase 3.
 */
import { defineConfig, devices } from '@playwright/test';

const target = process.env.SMOKE_TARGET ?? 'staging';
const baseURL =
  target === 'prod'
    ? process.env.PROD_FRONTEND_URL
    : process.env.STAGING_FRONTEND_URL;

if (!baseURL) {
  throw new Error(
    `Frontend smoke needs ${target === 'prod' ? 'PROD_FRONTEND_URL' : 'STAGING_FRONTEND_URL'} set`,
  );
}

export default defineConfig({
  testDir: '.',
  timeout: 60_000,
  fullyParallel: false,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
```

- [ ] **Step 4: Create `web/tests/smoke/critical-paths.spec.ts`**

```typescript
/**
 * Frontend critical-path smoke. Login → dashboard → preview → apply visible.
 * NEVER click apply (would launch real Fargate task).
 */
import { test, expect, type ConsoleMessage } from '@playwright/test';

const FIXTURE_EMAIL = process.env.FIXTURE_USER_EMAIL ?? 'test+smoke@naukribaba.com';
const FIXTURE_PASSWORD = process.env.FIXTURE_USER_PASSWORD;

test.describe('critical paths', () => {
  test('login → dashboard → preview → apply visible', async ({ page }) => {
    if (!FIXTURE_PASSWORD) {
      throw new Error('FIXTURE_USER_PASSWORD env var must be set');
    }

    const consoleErrors: string[] = [];
    page.on('console', (msg: ConsoleMessage) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    // 1. Visit login page
    await page.goto('/login');
    await expect(page).toHaveTitle(/NaukriBaba/i);

    // 2. Login
    await page.getByLabel(/email/i).fill(FIXTURE_EMAIL);
    await page.getByLabel(/password/i).fill(FIXTURE_PASSWORD);
    await page.getByRole('button', { name: /sign in|log in/i }).click();

    // 3. Dashboard renders
    await page.waitForURL(/\/(dashboard|jobs)/, { timeout: 15_000 });
    await expect(page.getByRole('heading', { name: /jobs|dashboard/i })).toBeVisible();

    // 4. First job tile clickable
    const firstTile = page.locator('[data-testid="job-tile"]').first();
    await expect(firstTile).toBeVisible({ timeout: 10_000 });
    await firstTile.click();

    // 5. Preview renders
    await expect(
      page.getByText(/apply|preview|tailored resume/i).first(),
    ).toBeVisible({ timeout: 10_000 });

    // 6. Apply button visible — NEVER click it
    const applyBtn = page.getByRole('button', { name: /^apply$|auto.?apply/i });
    await expect(applyBtn).toBeVisible();

    // 7. No console errors throughout
    expect(consoleErrors, `Console errors during smoke: ${consoleErrors.join(' | ')}`).toEqual([]);
  });
});
```

- [ ] **Step 5: Run locally against staging**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/web
npm run smoke:install   # one-time chromium download
export SMOKE_TARGET=staging
export STAGING_FRONTEND_URL='https://staging--naukribaba.netlify.app'
export FIXTURE_USER_EMAIL='test+smoke@naukribaba.com'
export FIXTURE_USER_PASSWORD='<from 1Password>'
npm run smoke
```

Expected: PASS, with one chromium browser launching, navigating, asserting visible elements.

- [ ] **Step 6: Commit**

```bash
git add web/package.json web/package-lock.json web/tests/smoke/playwright.config.ts web/tests/smoke/critical-paths.spec.ts
git commit -m "feat(smoke): Playwright critical-path smoke for frontend

Single chromium spec — login + dashboard + preview + apply-visible (no click).
Trace + video + screenshot on failure. Apply button is asserted but never
clicked (a click would launch a real Fargate browser session)."
```

Estimate: 45 min

---

## Task 7: `lambdas/pipeline/canary_prehook.py` — CodeDeploy PreTraffic Lambda

**Files:**
- Create: `lambdas/pipeline/canary_prehook.py`
- Create: `lambdas/pipeline/requirements-canary-prehook.txt`

The Lambda runs the *health* tier inline (decision: strategy A from the top of this plan). It must be self-contained — no imports from the rest of the project, no shared layer dependencies that aren't already in the runtime.

- [ ] **Step 1: Create `lambdas/pipeline/requirements-canary-prehook.txt`**

```text
# Canary prehook Lambda needs ONLY httpx (and certifi pulled transitively).
# This file is bundled into the function's CodeUri at deploy time so the
# Lambda's package stays under 5 MB.
httpx>=0.27.0
```

- [ ] **Step 2: Create `lambdas/pipeline/canary_prehook.py`**

```python
"""CodeDeploy PreTraffic hook — inline health smoke (≈5s round trip).

Event shape: {DeploymentId, LifecycleEventHookExecutionId}. Reports
Succeeded/Failed via codedeploy.put_lifecycle_event_hook_execution_status.

Env: SMOKE_TARGET (staging|prod), STAGING_URL, PROD_URL,
     PROBE_TIMEOUT_SECONDS (default '8').
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import boto3
import httpx

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_codedeploy = boto3.client("codedeploy")


def _target_url() -> str:
    target = os.environ.get("SMOKE_TARGET", "staging")
    key = f"{target.upper()}_URL"
    url = os.environ.get(key)
    if not url:
        raise RuntimeError(f"Required env var {key} not set")
    return url.rstrip("/")


def _run_health_probes(base_url: str, timeout: float) -> tuple[bool, str]:
    """Returns (passed, reason). reason is empty on success."""
    with httpx.Client(base_url=base_url, timeout=timeout) as c:
        # Probe 1: /api/health (public, fast, no auth needed)
        try:
            r = c.get("/api/health")
        except httpx.HTTPError as e:
            return False, f"/api/health connection error: {type(e).__name__}: {e}"
        if r.status_code != 200:
            return False, f"/api/health status {r.status_code}: {r.text[:200]}"
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if body.get("status") != "ok":
            return False, f"/api/health body status != ok: {body!r}"

        # Probe 2: latency budget
        start = time.perf_counter()
        c.get("/api/health")
        elapsed = time.perf_counter() - start
        if elapsed > 1.0:
            return False, f"/api/health latency {elapsed:.3f}s exceeds 1.0s budget"

    return True, ""


def _report(execution_id: str, deployment_id: str, status: str) -> None:
    """status must be 'Succeeded' or 'Failed' per CodeDeploy API."""
    logger.info(
        "Reporting %s for deployment=%s execution=%s",
        status, deployment_id, execution_id,
    )
    _codedeploy.put_lifecycle_event_hook_execution_status(
        deploymentId=deployment_id,
        lifecycleEventHookExecutionId=execution_id,
        status=status,
    )


def handler(event: dict[str, Any], _context) -> dict[str, str]:
    """Lambda entry point. CodeDeploy event shape:
        {"DeploymentId": "...", "LifecycleEventHookExecutionId": "..."}
    """
    deployment_id = event["DeploymentId"]
    execution_id = event["LifecycleEventHookExecutionId"]
    timeout = float(os.environ.get("PROBE_TIMEOUT_SECONDS", "8"))

    try:
        base_url = _target_url()
    except RuntimeError as e:
        logger.error("Config error: %s", e)
        _report(execution_id, deployment_id, "Failed")
        return {"status": "Failed", "reason": str(e)}

    passed, reason = _run_health_probes(base_url, timeout)
    status = "Succeeded" if passed else "Failed"
    if not passed:
        logger.error("Health probe FAILED: %s", reason)
    _report(execution_id, deployment_id, status)
    return {"status": status, "reason": reason}
```

- [ ] **Step 3: Local syntax check**

```bash
cd /Users/ut/code/naukribaba && source .venv/bin/activate
python -c "import ast; ast.parse(open('lambdas/pipeline/canary_prehook.py').read())"
```

Expected: no output (no syntax errors).

- [ ] **Step 4: Commit**

```bash
git add lambdas/pipeline/canary_prehook.py lambdas/pipeline/requirements-canary-prehook.txt
git commit -m "feat(canary): PreTraffic hook Lambda — inline health smoke

Strategy A from phase6 plan: run health probes inline (~5s round trip),
report Succeeded/Failed via codedeploy.put_lifecycle_event_hook_execution_status.
GitHub-Actions-dispatch strategy deferred to a post-traffic hook expansion."
```

Estimate: 30 min

---

## Task 8: Wire `CanaryPrehookFunction` + `Hooks.PreTraffic` into `template.yaml`

**Files:**
- Modify: `template.yaml`

This task adds three things:
1. A new `AWS::Serverless::Function` for the prehook.
2. An IAM permission allowing CodeDeploy to invoke it.
3. The `Hooks.PreTraffic: !GetAtt CanaryPrehookFunction.Arn` field on each of the 5 critical-tier functions' existing `DeploymentPreference` blocks (Phase 2 created the blocks; Phase 6 only adds the `Hooks` field).

- [ ] **Step 1: Confirm critical-tier functions and their line numbers**

```bash
grep -n "FunctionName: naukribaba-\(ws-route\|tailor-resume\|compile-latex\|save-job\|generate-cover-letter\)" /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/template.yaml
```

Expected output (line numbers from current template.yaml; Phase 2 may have shifted them slightly — re-check before editing):

```
278:      FunctionName: naukribaba-score-batch     [NOT critical-tier; ignore]
312:      FunctionName: naukribaba-tailor-resume
328:      FunctionName: naukribaba-compile-latex
344:      FunctionName: naukribaba-generate-cover-letter
374:      FunctionName: naukribaba-save-job
1618:      FunctionName: naukribaba-ws-route
```

Note `score-batch` is *not* in the critical-tier 5 (Phase 2 spec). The 5 are: `ws-route`, `tailor-resume`, `compile-latex`, `save-job`, `generate-cover-letter`.

- [ ] **Step 2: Add `CanaryPrehookFunction` resource**

The cleanest insertion point is just before the `# --- HTTP API Gateway ---` section (around line 1444). Open `template.yaml` and find:

```yaml
    Metadata:
      DockerTag: latest
      DockerContext: .
      Dockerfile: Dockerfile.lambda

  # --- HTTP API Gateway ---
  HttpApi:
```

Replace with:

```yaml
    Metadata:
      DockerTag: latest
      DockerContext: .
      Dockerfile: Dockerfile.lambda

  # --- CodeDeploy PreTraffic Hook (Phase 6) ---
  CanaryPrehookFunction:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: !Sub naukribaba-${Stage}-canary-prehook
      CodeUri: lambdas/pipeline/
      Handler: canary_prehook.handler
      Runtime: python3.11
      Timeout: 30
      MemorySize: 256
      Environment:
        Variables:
          SMOKE_TARGET: !Ref Stage
          STAGING_URL: !Ref StagingUrl
          PROD_URL: !Ref ProdUrl
          PROBE_TIMEOUT_SECONDS: "8"
      Policies:
        - Statement:
            - Effect: Allow
              Action: codedeploy:PutLifecycleEventHookExecutionStatus
              Resource: "*"

  CanaryPrehookCodeDeployPermission:
    Type: AWS::Lambda::Permission
    Properties:
      FunctionName: !GetAtt CanaryPrehookFunction.Arn
      Action: lambda:InvokeFunction
      Principal: codedeploy.amazonaws.com

  # --- HTTP API Gateway ---
  HttpApi:
```

- [ ] **Step 3: Add `StagingUrl` and `ProdUrl` parameters**

Open `template.yaml`. Find the `Parameters:` block (around line 10):

```yaml
  BrowserSubnetIds:
    Type: CommaDelimitedList
    Description: Public subnet IDs for Fargate browser tasks (discover via aws ec2 describe-subnets)
    Default: ""
```

Add immediately after (preserving 2-space indent):

```yaml
  BrowserSubnetIds:
    Type: CommaDelimitedList
    Description: Public subnet IDs for Fargate browser tasks (discover via aws ec2 describe-subnets)
    Default: ""

  StagingUrl:
    Type: String
    Description: Base URL of the staging API Gateway (used by CanaryPrehook)
    Default: ""

  ProdUrl:
    Type: String
    Description: Base URL of the prod API Gateway (used by CanaryPrehook)
    Default: ""
```

(Phase 3 introduced the `Stage` parameter; this task assumes it already exists. If `Stage` is missing, stop and resolve the Phase 3 dependency.)

- [ ] **Step 4: Add `Hooks.PreTraffic` to the 5 critical-tier functions**

Phase 2 added `DeploymentPreference` blocks to each. Phase 6 only adds the `Hooks` sub-key. For *each* of the 5 functions, find the existing `DeploymentPreference:` block (created by Phase 2; will look like the snippet below) and add the `Hooks` sub-key.

Example existing Phase 2 block (verbatim — will be present on each of the 5 critical functions):

```yaml
      DeploymentPreference:
        Type: Canary10Percent5Minutes
        Alarms:
          - !Ref WsRouteErrorsAlarm
          - !Ref WsRouteThrottlesAlarm
          - !Ref WsRouteDurationP99Alarm
```

Replace each with (function-name varies; structure identical):

```yaml
      DeploymentPreference:
        Type: Canary10Percent5Minutes
        Alarms:
          - !Ref WsRouteErrorsAlarm
          - !Ref WsRouteThrottlesAlarm
          - !Ref WsRouteDurationP99Alarm
        Hooks:
          PreTraffic: !GetAtt CanaryPrehookFunction.Arn
```

Apply the same `Hooks.PreTraffic` addition to:
- `TailorResumeFunction.Properties.DeploymentPreference`
- `CompileLatexFunction.Properties.DeploymentPreference`
- `SaveJobFunction.Properties.DeploymentPreference`
- `GenerateCoverLetterFunction.Properties.DeploymentPreference`
- `WsRouteFunction.Properties.DeploymentPreference`

(Note: alarm `!Ref` names vary per function — Phase 2 named them `<FunctionName>ErrorsAlarm` etc. Don't touch those references; only append the `Hooks:` sub-key.)

- [ ] **Step 5: Validate the template**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca && sam validate --lint
```

Expected: `<path>/template.yaml is a valid SAM Template`. If `cfn-lint` warns about the new IAM `Resource: "*"`, that's expected — `codedeploy:PutLifecycleEventHookExecutionStatus` does not support resource-level permissions.

- [ ] **Step 6: Commit**

```bash
git add template.yaml
git commit -m "feat(canary): wire CanaryPrehookFunction + PreTraffic hooks on 5 critical fns

CanaryPrehookFunction is the inline-health-probe Lambda from Task 7.
Each of ws-route, tailor-resume, compile-latex, save-job, generate-cover-letter
gets DeploymentPreference.Hooks.PreTraffic pointing at it. A 5xx in any of
those during traffic shift now aborts the deploy before users see it."
```

Estimate: 35 min

---

## Task 9: Append fixture rows to `web/supabase/seed.sql`

**Files:**
- Modify: `web/supabase/seed.sql` (Phase 3 created)

The fixture user, fixture job, and fixture resume row must exist on staging for `test_apply_synthetic.py` and `test_pipeline_smoke.py` to pass. We use the canonical IDs from `tests/smoke/fixtures.py`.

- [ ] **Step 1: Read the current seed.sql to find an insertion anchor**

```bash
tail -20 /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/web/supabase/seed.sql
```

Note where the file ends (the new INSERTs append after the last existing block).

- [ ] **Step 2: Append the smoke fixture block**

Append to `web/supabase/seed.sql`. IDs MUST match `tests/smoke/fixtures.py`. `ON CONFLICT DO NOTHING` keeps it idempotent.

```sql
-- ============================================================
-- Phase 6 — Smoke-test fixtures (IDs locked in tests/smoke/fixtures.py)
-- ============================================================

INSERT INTO auth.users (id, email, encrypted_password, email_confirmed_at, created_at, updated_at)
VALUES (
    '00000000-0000-4000-8000-000000000001',
    'test+smoke@naukribaba.com',
    crypt('smoke-test-password', gen_salt('bf')),
    NOW(), NOW(), NOW()
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO profiles (id, email, first_name, last_name, phone, location, visa_status, onboarding_complete)
VALUES (
    '00000000-0000-4000-8000-000000000001',
    'test+smoke@naukribaba.com',
    'Smoke', 'Tester', '+353000000000', 'Dublin, Ireland', 'eligible_eu', true
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO jobs (
    job_id, user_id, job_hash, title, company, description, location,
    apply_url, source, match_score, score_tier, resume_s3_key, resume_version,
    canonical_hash, created_at, updated_at
)
VALUES (
    '00000000-0000-4000-8000-0000000000a1',
    '00000000-0000-4000-8000-000000000001',
    'smoke-fixture-job-hash-v1',
    'Smoke Test Engineer',
    'NaukriBaba Smoke Co',
    'Python, FastAPI, AWS Lambda. 3+ years experience.',
    'Dublin',
    'https://example.com/apply',
    'smoke', 92, 'S',
    'smoke/test-smoke/resume-v1.pdf', 1,
    'smoke-canonical-hash-v1',
    NOW(), NOW()
)
ON CONFLICT (job_id) DO NOTHING;
```

- [ ] **Step 3: Apply to staging**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/web
supabase db reset --project-ref ${STAGING_PROJECT_REF} --linked  # only if you want a clean staging
# OR, less destructively, run only the new block:
psql "$STAGING_SUPABASE_DB_URL" -f supabase/seed.sql
```

Expected: 3 rows inserted (auth.users, profiles, jobs) or 0 if already present (idempotent).

- [ ] **Step 4: Mint a JWT for the fixture user**

```bash
curl -X POST "$STAGING_SUPABASE_URL/auth/v1/token?grant_type=password" \
  -H "apikey: $STAGING_SUPABASE_ANON_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email":"test+smoke@naukribaba.com","password":"smoke-test-password"}' \
  | python3 -m json.tool
```

Copy the `access_token` field. Store it as a GitHub secret: `STAGING_FIXTURE_JWT`. Repeat for prod once Phase 3's prod stack is up: `PROD_FIXTURE_JWT`.

```bash
gh secret set STAGING_FIXTURE_JWT --body "<access_token>"
```

(Note: Supabase JWTs expire — typically 1 hour. The CI workflow re-mints on each run via the same curl call, using `STAGING_SUPABASE_ANON_KEY` + `FIXTURE_USER_PASSWORD` secrets. See Task 10.)

- [ ] **Step 5: Commit**

```bash
git add web/supabase/seed.sql
git commit -m "feat(smoke): append canonical smoke fixture rows to seed.sql

Fixture user + fixture job + pre-tailored resume row. IDs match
tests/smoke/fixtures.py. ON CONFLICT DO NOTHING so seed remains idempotent."
```

Estimate: 25 min

---

## Task 10: `.github/workflows/smoke.yml` — reusable workflow_call

**Files:**
- Create: `.github/workflows/smoke.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: Smoke

on:
  workflow_call:
    inputs:
      target_env:
        description: "staging | prod"
        required: true
        type: string
      target_url:
        description: "Base URL of the API Gateway for this env"
        required: true
        type: string
    secrets:
      SUPABASE_URL: { required: true }
      SUPABASE_ANON_KEY: { required: true }
      SUPABASE_SERVICE_KEY: { required: true }
      WS_URL: { required: true }
      FIXTURE_USER_PASSWORD: { required: true }
      AWS_ACCESS_KEY_ID: { required: true }
      AWS_SECRET_ACCESS_KEY: { required: true }

jobs:
  backend-smoke:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    env:
      SMOKE_TARGET: ${{ inputs.target_env }}
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install smoke deps
        run: pip install -r tests/smoke/requirements.txt

      - name: Mint fixture JWT
        id: mint
        run: |
          set -euo pipefail
          TOKEN=$(curl -fsS -X POST "${{ secrets.SUPABASE_URL }}/auth/v1/token?grant_type=password" \
            -H "apikey: ${{ secrets.SUPABASE_ANON_KEY }}" \
            -H "Content-Type: application/json" \
            -d "{\"email\":\"test+smoke@naukribaba.com\",\"password\":\"${{ secrets.FIXTURE_USER_PASSWORD }}\"}" \
            | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
          echo "::add-mask::$TOKEN"
          echo "jwt=$TOKEN" >> "$GITHUB_OUTPUT"

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: eu-west-1

      - name: Run pytest smoke
        env:
          STAGING_URL: ${{ inputs.target_url }}
          PROD_URL: ${{ inputs.target_url }}
          STAGING_WS_URL: ${{ secrets.WS_URL }}
          PROD_WS_URL: ${{ secrets.WS_URL }}
          STAGING_SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          PROD_SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          STAGING_SUPABASE_SERVICE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}
          PROD_SUPABASE_SERVICE_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}
          STAGING_FIXTURE_JWT: ${{ steps.mint.outputs.jwt }}
          PROD_FIXTURE_JWT: ${{ steps.mint.outputs.jwt }}
        run: pytest tests/smoke -v --tb=short

      - name: Upload artifacts on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: smoke-pytest-${{ inputs.target_env }}
          path: |
            .pytest_cache/
            /tmp/pytest-*

  frontend-smoke:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    needs: backend-smoke   # if backend is broken, frontend smoke is moot
    env:
      SMOKE_TARGET: ${{ inputs.target_env }}
    defaults:
      run:
        working-directory: web
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: "npm"
          cache-dependency-path: web/package-lock.json

      - run: npm ci

      - name: Install Playwright browsers
        run: npx playwright install chromium --with-deps

      - name: Run Playwright smoke
        env:
          STAGING_FRONTEND_URL: ${{ vars.STAGING_FRONTEND_URL }}
          PROD_FRONTEND_URL: ${{ vars.PROD_FRONTEND_URL }}
          FIXTURE_USER_EMAIL: test+smoke@naukribaba.com
          FIXTURE_USER_PASSWORD: ${{ secrets.FIXTURE_USER_PASSWORD }}
        run: npm run smoke

      - name: Upload Playwright artifacts on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: smoke-playwright-${{ inputs.target_env }}
          path: |
            web/playwright-report/
            web/test-results/
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/smoke.yml
git commit -m "feat(ci): reusable smoke workflow_call (backend pytest + frontend Playwright)

Single entry point used by deploy.yml after staging deploy and after prod
canary completes. Mints a fresh fixture JWT per run so token expiry never
turns smoke red. Uploads pytest + Playwright traces on failure."
```

Estimate: 30 min

---

## Task 11: Wire `smoke.yml` into `deploy.yml`

**Files:**
- Modify: `.github/workflows/deploy.yml`

The current `deploy.yml` is a single-job workflow_dispatch that deploys to one stack. Phase 3 split it into staging + prod jobs; Phase 6 attaches `smoke.yml` calls after each.

This task assumes Phase 3 has converted `deploy.yml` into a two-job (or reusable-call) flow. We add ONE `uses:` step after the staging job, ONE after the prod job. The detailed structure of those jobs is owned by Phase 3 — this task only adds the `smoke-*` jobs that depend on them.

- [ ] **Step 1: Read the current Phase-3-modified deploy.yml**

```bash
cat /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/.github/workflows/deploy.yml
```

Identify the job names for staging and prod deploy. Phase 3's spec calls them `deploy-staging` and `deploy-prod`. Confirm before editing.

- [ ] **Step 2: Add staging smoke job**

Append after the `deploy-staging` job (preserving its outputs — Phase 3 should expose `staging_url` as a job output):

```yaml
  smoke-staging:
    needs: deploy-staging
    uses: ./.github/workflows/smoke.yml
    with:
      target_env: staging
      target_url: ${{ needs.deploy-staging.outputs.staging_url }}
    secrets:
      SUPABASE_URL: ${{ secrets.STAGING_SUPABASE_URL }}
      SUPABASE_ANON_KEY: ${{ secrets.STAGING_SUPABASE_ANON_KEY }}
      SUPABASE_SERVICE_KEY: ${{ secrets.STAGING_SUPABASE_SERVICE_KEY }}
      WS_URL: ${{ secrets.STAGING_WS_URL }}
      FIXTURE_USER_PASSWORD: ${{ secrets.FIXTURE_USER_PASSWORD }}
      AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
      AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
```

- [ ] **Step 3: Add prod smoke job (post-canary)**

Append after the `deploy-prod` job. Critical: this runs *after* the canary's PreTraffic hook has already gated traffic shift, so prod smoke is *additional* coverage (the hardest tests — apply round-trip, pipeline invoke — that the prehook can't run inline).

```yaml
  smoke-prod:
    needs: deploy-prod
    uses: ./.github/workflows/smoke.yml
    with:
      target_env: prod
      target_url: ${{ needs.deploy-prod.outputs.prod_url }}
    secrets:
      SUPABASE_URL: ${{ secrets.PROD_SUPABASE_URL }}
      SUPABASE_ANON_KEY: ${{ secrets.PROD_SUPABASE_ANON_KEY }}
      SUPABASE_SERVICE_KEY: ${{ secrets.PROD_SUPABASE_SERVICE_KEY }}
      WS_URL: ${{ secrets.PROD_WS_URL }}
      FIXTURE_USER_PASSWORD: ${{ secrets.FIXTURE_USER_PASSWORD }}
      AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
      AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
```

- [ ] **Step 4: Block PR merge on staging-smoke failure**

PRs targeting `main` should require `smoke-staging` to pass. After merging this plan, configure branch protection in GitHub Settings → Branches → main → "Require status checks to pass before merging" → check `smoke-staging`. (This is a UI step, not a file edit.)

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "feat(ci): call smoke.yml after staging deploy and after prod deploy

smoke-staging blocks PR merge (branch protection enforced via GH UI).
smoke-prod is fuller coverage post-canary — the prehook ran health-only
inline; this runs apply round-trip + pipeline invoke against live prod."
```

Estimate: 20 min

---

## Task 12: `docs/runbooks/rollback.md`

**Files:**
- Create: `docs/runbooks/rollback.md`

The roadmap defines the 5-step ladder; this task captures it as an executable runbook.

- [ ] **Step 1: Confirm the directory exists or create it**

```bash
mkdir -p /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/docs/runbooks
```

- [ ] **Step 2: Write the runbook**

```markdown
# Rollback Runbook

> Work down this ladder. Stop at the first step that resolves the incident.

## Step 1 — Auto (CodeDeploy alarm trip) — under 2 min, no action

Canary alarm (Errors / Throttles / DurationP99) on the 5 critical-tier
functions trips during traffic shift. Alias auto-reverts.

Verify:
```bash
aws codedeploy list-deployments --max-items 5 --region eu-west-1
aws codedeploy get-deployment --deployment-id <id> --region eu-west-1 \
  --query 'deploymentInfo.{Status:status, Rollback:rollbackInfo}'
```
Expect `Status: Stopped` with `rollbackInfo.rollbackTriggeringDeploymentId` set.

## Step 2 — Auto (PreTraffic smoke fail) — 0 min, no action

`naukribaba-${Stage}-canary-prehook` Lambda returned Failed; deploy aborted
before any traffic shift.

Verify: `aws logs tail /aws/lambda/naukribaba-prod-canary-prehook --since 30m --region eu-west-1`
— look for `Health probe FAILED:` lines.

## Step 3 — Flag kill-switch — 30s, one click

Logical bug not tripping alarms. Open
https://app.posthog.com/project/<project>/feature_flags, find the flag
(`auto_apply`, `council_scoring`, `tailor_full_rewrite`, ...), set rollout to 0%.
Effect within 30s globally.

## Step 4 — Frontend revert (Netlify) — 30s

Frontend-only regression. Open
https://app.netlify.com/sites/naukribaba/deploys, find prior green deploy,
click "Publish deploy". CDN propagation ~30s.

## Step 5 — Manual full revert — 10–15 min

When 1–4 don't help (bad migration, flag-check itself broken, prior deploy
also broken):

```bash
git checkout main && git pull
git revert <bad_sha>          # NEW commit, never --amend
git push origin main          # triggers deploy.yml
```

If a Supabase migration is in the bad change:

```bash
cd web
supabase migration list --linked
supabase migration repair --status reverted <migration_id>
# Then write a forward-rolling repair migration.
```

NEVER `supabase db reset --linked` against prod.

## After any rollback

1. Open Sentry incident or GH issue with root cause.
2. Add a `tests/smoke/` test that would have caught this.
3. `SMOKE_TARGET=prod pytest tests/smoke -v` — confirm green.
4. Postmortem within 48h, even if user-impact was zero.
```

- [ ] **Step 3: Commit**

```bash
git add docs/runbooks/rollback.md
git commit -m "docs(runbook): rollback ladder — 5 steps from auto to manual revert

Captures the cross-phase rollback strategy from the deployment safety
roadmap into an oncall-runnable runbook with exact commands per step."
```

Estimate: 25 min

---

## Task 13: Validation — manual smoke against staging

**Files:** none (validation only)

- [ ] **Step 1: Run pytest smoke against staging from local machine**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca && source .venv/bin/activate
pip install -r tests/smoke/requirements.txt

export SMOKE_TARGET=staging
export STAGING_URL='<staging API GW URL from Phase 3 stack output>'
export STAGING_WS_URL='<staging wss:// URL from stack output>'
export STAGING_SUPABASE_URL='<from 1Password>'
export STAGING_SUPABASE_SERVICE_KEY='<from 1Password>'
export STAGING_FIXTURE_JWT='<minted via curl from Task 9 step 4>'

pytest tests/smoke -v --tb=short
```

Expected: all green. `test_apply_metric_emitted_when_phase4_present` either passes (Phase 4 done) or skips (Phase 4 pending).

- [ ] **Step 2: Run Playwright smoke against staging**

```bash
cd web
export SMOKE_TARGET=staging
export STAGING_FRONTEND_URL='https://staging--naukribaba.netlify.app'
export FIXTURE_USER_EMAIL='test+smoke@naukribaba.com'
export FIXTURE_USER_PASSWORD='smoke-test-password'
npm run smoke
```

Expected: 1/1 passing in chromium.

- [ ] **Step 3: Note timing**

Record total wall-clock time of `pytest tests/smoke -v`. Roadmap target is < 3 minutes mean. If we're over, drop to: log the slow test, see if a fixture cleanup is mid-test causing serial waits, consider parallelism with `pytest-xdist`.

Estimate: 15 min

---

## Task 14: Validation — deliberate /healthz break in PR

**Files:** temporary, reverted at end of task

- [ ] **Step 1: Create a PR that breaks /api/health**

```bash
git checkout -b smoke/validate-pr-block
```

In `app.py`, find:

```python
@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "resumes_loaded": list(_resumes.keys()),
        "ai_providers": len(_ai_client.providers) if _ai_client else 0,
    }
```

Replace with:

```python
@app.get("/api/health")
def health():
    raise HTTPException(500, "DELIBERATE SMOKE VALIDATION — REVERT BEFORE MERGE")
```

```bash
git add app.py
git commit -m "test: deliberate /api/health 500 to validate smoke gating (DO NOT MERGE)"
git push -u origin smoke/validate-pr-block
gh pr create --title "[VALIDATION] deliberate /api/health break — DO NOT MERGE" \
  --body "Validating that smoke-staging blocks PR merge per Phase 6 Task 14. Revert before close."
```

- [ ] **Step 2: Watch the PR check tab**

```bash
gh pr checks $(gh pr view --json number -q .number)
```

Expected after ~15 min: `deploy-staging` green, `smoke-staging` red. The `test_healthz_returns_200` failure should be visible in the smoke-staging job log.

- [ ] **Step 3: Verify GitHub blocks the merge**

Open the PR in browser. The "Merge pull request" button should be **disabled**, with a red "Required check `smoke-staging` failed" notice. If the button is still clickable, branch protection isn't configured — go back to Task 11 step 4.

- [ ] **Step 4: Close the PR without merging**

```bash
gh pr close --delete-branch $(gh pr view --json number -q .number)
```

Estimate: 25 min

---

## Task 15: Validation — deliberate prod-breaking change + auto-rollback

**Files:** temporary, reverted at end of task

This is the load-bearing end-to-end validation: a bad merge to `main` must be auto-reverted within 10 min.

- [ ] **Step 1: Create a PR that 500s in `WsRouteFunction`**

```bash
git checkout main
git pull
git checkout -b smoke/validate-canary-rollback
```

In `lambdas/browser/ws_route.py`, find the handler entry point (top of `def handler`):

```python
def handler(event, context):
```

Add as the first line of the function body:

```python
def handler(event, context):
    raise RuntimeError("DELIBERATE CANARY VALIDATION — alarms should fire")
```

Crucially, do *not* break `/api/health` — the prehook must pass so the deploy proceeds *into* canary, where the alarms then trip.

```bash
git add lambdas/browser/ws_route.py
git commit -m "test: deliberate ws_route exception to validate canary rollback"
git push -u origin smoke/validate-canary-rollback
gh pr create --title "[VALIDATION] deliberate ws_route 500 — auto-rollback test" \
  --body "Verifies CodeDeploy reverts alias when alarms trip during canary."
```

- [ ] **Step 2: Force-merge to main bypassing smoke-staging temporarily**

This validation requires the bad code to actually deploy to prod canary. Either:

- Temporarily admin-merge bypassing branch protection (preferred — record in incident channel before doing this), OR
- Mark the staging smoke job as `continue-on-error: true` for this PR only via a one-off commit that you also revert.

Whichever path, the goal is: bad code lands on `main`, `deploy-prod` runs, the canary's *alarms* trip on the new alias version (since `ws_route` 500s on every WS message), CodeDeploy auto-reverts.

- [ ] **Step 3: Watch the canary in CodeDeploy console**

```bash
DEPLOYMENT_ID=$(aws deploy list-deployments --max-items 1 --region eu-west-1 \
  --query 'deployments[0]' --output text)
aws deploy get-deployment --deployment-id $DEPLOYMENT_ID --region eu-west-1 \
  --query 'deploymentInfo.{status:status, rollback:rollbackInfo}'
```

Expected within 10 min: `status: Stopped`, `rollback.rollbackTriggeringDeploymentId` populated. The alias should remain on the prior version:

```bash
aws lambda get-alias --function-name naukribaba-prod-ws-route --name live --region eu-west-1
```

Expected `FunctionVersion` is the version *before* this deploy.

- [ ] **Step 4: Verify smoke goes green again post-rollback**

```bash
SMOKE_TARGET=prod pytest tests/smoke -v
```

Expected: green. Auto-rollback has restored a working version.

- [ ] **Step 5: Revert the bad change and re-deploy clean**

```bash
git checkout main
git pull
git revert <bad_sha>
git push origin main
```

Watch `deploy-prod` go green to fully restore `main` to a clean state.

- [ ] **Step 6: File post-validation incident note**

Add a single line to memory: `[Session Apr 27]: Phase 6 validated — deliberate ws_route break auto-reverted by CodeDeploy in <N> min, alias remained on prior version.`

Estimate: 45 min

---

## Task 16: Open the PR + summarize

**Files:** none (PR action)

- [ ] **Step 1: Push the cumulative branch**

```bash
git push -u origin claude/objective-sanderson-eeedca
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "feat(deploy): Phase 6 — smoke tests + CodeDeploy PreTraffic rollback" \
  --body "$(cat <<'EOF'
## Summary
- **Backend smoke** (`tests/smoke/`): pytest+httpx suite — health, apply synthetic round-trip (HTTP→WS→DB), pipeline boto3 invoke. Conftest is env-aware via `SMOKE_TARGET=staging|prod`.
- **Frontend smoke** (`web/tests/smoke/`): Playwright single chromium spec — login → dashboard → preview → apply-button-visible.
- **PreTraffic Lambda** (`lambdas/pipeline/canary_prehook.py`): runs health-only smoke inline (≈5s), reports back to CodeDeploy. Wired into 5 critical-tier functions.
- **Reusable smoke workflow** (`.github/workflows/smoke.yml`): called from `deploy.yml` after staging deploy (blocks PR merge) and after prod deploy (post-canary fuller coverage).
- **Fixtures** (`web/supabase/seed.sql`): canonical fixture user + job rows; IDs locked in `tests/smoke/fixtures.py`.
- **Rollback runbook** (`docs/runbooks/rollback.md`): 5-step ladder from auto-CodeDeploy → manual revert.

Spec: `docs/superpowers/plans/2026-04-27-deployment-safety-roadmap.md` § Phase 6.

## Test plan
- [x] `pytest tests/smoke -v` against staging — all green (Task 13)
- [x] `npm run smoke` against staging — green (Task 13)
- [x] PR with deliberate `/api/health` 500 — `smoke-staging` blocks merge (Task 14)
- [x] Bad `main` merge — CodeDeploy auto-reverts within 10 min, alias on prior version (Task 15)
- [x] Smoke goes green again after rollback (Task 15 step 4)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Estimate: 10 min

---

## Self-Review Checklist

(Author note 2026-04-27 — completed before save)

- ✅ **Spec coverage:** every file in roadmap Phase 6 § "Files" maps to a task. T1: `__init__.py`/`fixtures.py`/`requirements.txt`. T2: `conftest.py`. T3: `test_health.py`. T4: `test_apply_synthetic.py`. T5: `test_pipeline_smoke.py`. T6: `playwright.config.ts`/`critical-paths.spec.ts`/`web/package.json`. T7: `canary_prehook.py`. T8: `template.yaml`. T9: `seed.sql`. T10: `smoke.yml`. T11: `deploy.yml`. T12: `rollback.md`. T13–T15: 4 validation tasks.
- ✅ **No placeholders:** every code block is complete. Deferred behavior (Phase 4 EMF assertion) is gated at runtime via `_phase4_available`, not a TODO.
- ✅ **Type/name consistency:** function names match `template.yaml`. `naukribaba-${Stage}-<name>` suffix consistent across `canary_prehook.py`, `test_pipeline_smoke.py`, CFN. Fixture IDs declared in `fixtures.py` (T1) and referenced by T4/T5/T9 with identical UUIDs.
- ✅ **Phase deps called out:** P2 (canary block), P3 (Stage / staging URL / seed.sql), P4 (EMF — soft) all checked in T0.
- ✅ **PreTraffic strategy:** documented up-front. Strategy A inline. B noted as future expansion.
- ✅ **Validation introduces + reverts breakage:** T14 breaks /api/health on throwaway PR; T15 breaks ws_route on main with explicit revert step.
- ✅ **Idempotent fixtures:** `ON CONFLICT DO NOTHING`; tests delete their own writes.
- ✅ **Flake-allergic:** every test has `@pytest.mark.timeout`, every WS recv has a deadline.
