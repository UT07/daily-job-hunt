# Auto-Apply Plan 3a — WebSocket Lambdas + Backend Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the five 501-stub FastAPI `/api/apply/*` endpoints and three 200-stub WebSocket Lambdas with real implementations so a user can click "Apply" on a job, launch a cloud Chrome session on Fargate, connect a WebSocket for live streaming + commands, record the submitted application, and end the session. Preview is intentionally minimal in this plan — AI answer generation is Plan 3b.

**Architecture:** The frontend calls `POST /api/apply/start-session` → Lambda creates a DynamoDB session row + launches a Fargate task → returns `{session_id, ws_url, ws_token_frontend}`. Frontend opens the WebSocket at `wss://.../prod?session=<id>&role=frontend` with `Authorization: Bearer <ws_token_frontend>`; `ws_connect` authenticates, writes the connection id into the session row. The Fargate container — started by start-session with `WS_TOKEN=<ws_token_browser>` in its env — opens its own connection with `?session=<id>&role=browser` and the same Authorization header. `ws_route` relays text messages between the two connections by reading the peer's `connection_id` from DynamoDB and calling `PostToConnection`. `ws_disconnect` clears the connection id and, if both sides are gone, transitions status=ended and calls `ecs:StopTask`. Screenshots bypass Lambda entirely — Fargate posts them directly to the frontend connection via the API Gateway Management API.

**Tech Stack:** Python 3.11, FastAPI (backend), boto3 (DynamoDB + ECS + API Gateway Management API), python-jose (JWT), supabase-py, pytest + MagicMock, GitHub Actions (CI), AWS SAM (infra).

**Protocol alignment — critical:** This plan matches Plan 2's shipped code in [browser/browser_session.py:410](browser/browser_session.py:410), which connects with `{WS_URL}?session={SESSION_ID}&role=browser` and `Authorization: Bearer {WS_TOKEN}`. Session id and role travel as **query parameters**. Auth token travels as **Authorization header** (PR #7 change). DDB slot names are **`ws_connection_frontend`** and **`ws_connection_browser`** (spec §4 line 272-273). This plan does **not** invent a new protocol.

**Scope — what is NOT in Plan 3a:**
- AI answer generation in preview (`/api/apply/preview/{job_id}`) → **Plan 3b** (`docs/superpowers/plans/2026-04-24-auto-apply-plan3b-preview-ai.md` — stub written alongside this)
- Frontend `BrowserStream.jsx` + `AnswerPanel.jsx` → Plan 4
- Mode 3 (assisted-manual) fallback → future
- CapSolver key rotation → user action, no code

**Bundled follow-ups (small, still in this plan):**
- Async-ify `_screenshot_loop` boto3 call via `asyncio.to_thread` (Plan 2 carry-over TODO)
- Wire `CapSolverApiKey` + `BrowserSubnetIds` into `.github/workflows/deploy.yml` (`--parameter-overrides`)

**Security model — why two JWT audiences:** Previously I considered a single `aud=ws` token used by both frontend and browser. That lets a malicious frontend connect as `role=browser` before Fargate boots (~45s window) and intercept `fill_all` answers containing the candidate's PII. Fix: `start-session` issues **two distinct tokens** with `aud=ws.frontend` (returned to HTTP client) and `aud=ws.browser` (passed to Fargate via `WS_TOKEN` env var). `ws_connect` enforces `token.aud == "ws." + query.role`.

**Branch:** Work on the current worktree branch `claude/nostalgic-rhodes-f58e72` (worktree at `/Users/ut/code/naukribaba/.claude/worktrees/nostalgic-rhodes-f58e72`). Commit frequently. PR title: `feat: auto-apply Plan 3a — WebSocket Lambdas + backend endpoints`.

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `shared/browser_sessions.py` | DynamoDB helpers (get/put/update session, find active for user, PostToConnection wrapper) reused by WS Lambdas and FastAPI endpoints |
| `shared/ws_auth.py` | Issue + verify short-lived (60s) two-audience WS JWTs: `ws.frontend` and `ws.browser` |
| `shared/profile_completeness.py` | `check_profile_completeness()` — returns list of missing required field names |
| `shared/load_job.py` | `load_job(job_id, user_id, *, db) -> dict \| None` with RLS-style user scoping |
| `tests/unit/test_browser_sessions.py` | Unit tests for DDB helpers |
| `tests/unit/test_ws_auth.py` | Unit tests for JWT issue/verify |
| `tests/unit/test_profile_completeness.py` | Unit tests |
| `tests/unit/test_load_job.py` | Unit tests |
| `tests/unit/test_ws_connect.py` | Unit tests for `lambdas/browser/ws_connect.py` |
| `tests/unit/test_ws_disconnect.py` | Unit tests |
| `tests/unit/test_ws_route.py` | Unit tests |
| `tests/unit/test_apply_endpoints.py` | Unit tests for the 4 FastAPI apply endpoints shipped in 3a |
| `tests/contract/test_apply_happy_path.py` | Integration test threading start → ws_connect(×2) → ws_route → record → stop_session |
| `docs/superpowers/plans/2026-04-24-auto-apply-plan3b-preview-ai.md` | Stub / outline for the AI preview follow-up plan (Plan 3b) |

### Modified files

| Path | Change |
|---|---|
| `lambdas/browser/ws_connect.py` | Replace 200-stub with query-param session+role + header-Bearer auth + audience check + DDB session registration |
| `lambdas/browser/ws_disconnect.py` | Replace 200-stub with cleanup + conditional Fargate stop when both peers gone |
| `lambdas/browser/ws_route.py` | Replace 200-stub with message relay via `PostToConnection`; clear stale conn on `GoneException` |
| `app.py` | Replace the five 501 stubs (lines 2406-2433) with: eligibility, start-session, stop-session, record (new), and a **minimal** preview (no AI — 3b will fill in). Delete `/api/apply/submit/{job_id}` stub (legacy from abandoned API-POST spec). |
| `browser/browser_session.py` | Replace sync `apigwmgmt.post_to_connection` inside `_screenshot_loop` with `await asyncio.to_thread(...)`. Remove the `TODO(plan-3)` marker. |
| `.github/workflows/deploy.yml` | Add `--parameter-overrides CapSolverApiKey=... BrowserSubnetIds=...` in the SAM Deploy step. |

### Referenced, unchanged

| Path | Why |
|---|---|
| `auth.py` | API auth; reused for `get_current_user` on the apply endpoints |
| `db_client.py` | `_db` accessor pattern used throughout |
| `browser/browser_session.py` (Plan 2) | **Must keep its DDB schema exactly as-is** — field names: `status`, `last_activity_at`, `ws_connection_frontend`, `ws_connection_browser`, `fargate_task_arn`, `user_id`, `current_job_id`, `platform`, `ttl` |
| `template.yaml` | WS + DDB + Fargate task def all shipped in PR #7; no structural changes needed in 3a |

---

## Task sequencing

- **Phase A** (shared helpers, 4 tasks) — dependency for everything else
- **Phase B** (3 WS Lambdas, 3 tasks) — depends on A1 + A2
- **Phase C** (4 backend endpoints + 1 new, 5 tasks) — depends on A1 + A2 + A3 + A4
- **Phase D** (deploy.yml wiring, 1 task) — independent
- **Phase E** (async boto3 in browser_session, 1 task) — independent
- **Phase F** (contract test + PR, 1 task) — depends on B + C

Total: 15 implementation tasks + 1 PR step ≈ ~17 commits.

---

## Phase A — Shared helpers

### Task A1: DynamoDB session helpers

**Files:**
- Create: `shared/browser_sessions.py`
- Create: `shared/__init__.py` (if missing)
- Test: `tests/unit/test_browser_sessions.py`

- [ ] **Step A1.1: Write the failing test**

```python
# tests/unit/test_browser_sessions.py
"""Unit tests for shared.browser_sessions."""
import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"SESSIONS_TABLE": "test-sessions", "AWS_REGION": "eu-west-1"}):
        yield


def _mock_table():
    table = MagicMock()
    ddb = MagicMock()
    ddb.Table.return_value = table
    return ddb, table


def test_create_session_writes_ddb_row_with_ttl():
    ddb, table = _mock_table()
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import create_session
        row = create_session(
            session_id="sess-123",
            user_id="user-1",
            job_id="job-9",
            platform="greenhouse",
            fargate_task_arn="arn:aws:ecs:...:task/xyz",
            ttl_seconds=1800,
        )
    table.put_item.assert_called_once()
    item = table.put_item.call_args.kwargs["Item"]
    assert item["session_id"] == "sess-123"
    assert item["user_id"] == "user-1"
    assert item["current_job_id"] == "job-9"
    assert item["platform"] == "greenhouse"
    assert item["fargate_task_arn"].startswith("arn:aws:ecs")
    assert item["status"] == "starting"
    assert item["ttl"] > 0
    assert row == item


def test_get_session_returns_none_when_missing():
    ddb, table = _mock_table()
    table.get_item.return_value = {}
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import get_session
        assert get_session("sess-missing") is None


def test_get_session_returns_item_when_present():
    ddb, table = _mock_table()
    table.get_item.return_value = {"Item": {"session_id": "sess-1", "status": "ready"}}
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import get_session
        got = get_session("sess-1")
    assert got == {"session_id": "sess-1", "status": "ready"}


def test_set_connection_id_frontend_slot():
    ddb, table = _mock_table()
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import set_connection_id
        set_connection_id("sess-1", role="frontend", connection_id="abc123")
    kwargs = table.update_item.call_args.kwargs
    assert kwargs["Key"] == {"session_id": "sess-1"}
    assert "ws_connection_frontend" in kwargs["UpdateExpression"]
    assert kwargs["ExpressionAttributeValues"][":c"] == "abc123"


def test_set_connection_id_browser_slot():
    ddb, table = _mock_table()
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import set_connection_id
        set_connection_id("sess-1", role="browser", connection_id="abc456")
    kwargs = table.update_item.call_args.kwargs
    assert "ws_connection_browser" in kwargs["UpdateExpression"]


def test_set_connection_id_rejects_unknown_role():
    with patch("boto3.resource"):
        from shared.browser_sessions import set_connection_id
        with pytest.raises(ValueError, match="role must be"):
            set_connection_id("sess-1", role="attacker", connection_id="x")


def test_clear_connection_id_removes_slot():
    ddb, table = _mock_table()
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import clear_connection_id
        clear_connection_id("sess-1", role="browser")
    kwargs = table.update_item.call_args.kwargs
    assert kwargs["UpdateExpression"] == "REMOVE ws_connection_browser"


def test_find_active_session_for_user_uses_gsi():
    ddb, table = _mock_table()
    table.query.return_value = {"Items": [{"session_id": "sess-1", "status": "ready", "last_activity_at": 100}]}
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import find_active_session_for_user
        got = find_active_session_for_user("user-1")
    assert got is not None
    assert got["session_id"] == "sess-1"
    kwargs = table.query.call_args.kwargs
    assert kwargs["IndexName"] == "user-sessions-index"


def test_find_active_session_returns_none_when_none_active():
    ddb, table = _mock_table()
    table.query.return_value = {"Items": [{"session_id": "sess-1", "status": "ended"}]}
    with patch("boto3.resource", return_value=ddb):
        from shared.browser_sessions import find_active_session_for_user
        assert find_active_session_for_user("user-1") is None


def test_post_to_connection_uses_management_api():
    mgmt = MagicMock()
    with patch("boto3.client", return_value=mgmt) as m_client:
        from shared.browser_sessions import post_to_connection
        post_to_connection(
            api_id="abc123",
            region="eu-west-1",
            connection_id="conn-xyz",
            data=b'{"action":"click","x":10,"y":20}',
        )
    m_client.assert_called_once()
    client_kwargs = m_client.call_args.kwargs
    assert client_kwargs["endpoint_url"] == "https://abc123.execute-api.eu-west-1.amazonaws.com/prod"
    mgmt.post_to_connection.assert_called_once_with(
        ConnectionId="conn-xyz",
        Data=b'{"action":"click","x":10,"y":20}',
    )
```

- [ ] **Step A1.2: Run — FAIL**

```bash
pytest tests/unit/test_browser_sessions.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.browser_sessions'`

- [ ] **Step A1.3: Create package dir if missing**

```bash
test -f shared/__init__.py || { mkdir -p shared && touch shared/__init__.py; }
```

- [ ] **Step A1.4: Implement `shared/browser_sessions.py`**

```python
# shared/browser_sessions.py
"""DynamoDB helpers and API Gateway Management API wrapper for cloud browser sessions.

All cloud-browser Lambdas (ws_connect / ws_disconnect / ws_route) and the
FastAPI apply endpoints use these helpers so the DDB schema is defined in
exactly one place.

Schema (table: naukribaba-browser-sessions, PK: session_id, GSI: user-sessions-index on user_id):
    session_id              (S, PK)
    user_id                 (S, GSI hash)
    current_job_id          (S)
    platform                (S)
    fargate_task_arn        (S)
    status                  (S)  -- starting | ready | filling | submitted | ended
    ws_connection_frontend  (S, optional)
    ws_connection_browser   (S, optional)
    created_at              (S, ISO-8601)
    last_activity_at        (N, unix ts)
    ttl                     (N, unix ts — DynamoDB TTL-enabled)
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Optional

import boto3

AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")
SESSIONS_TABLE = os.environ.get("SESSIONS_TABLE", "naukribaba-browser-sessions")

_ALLOWED_ROLES = {"frontend", "browser"}
_ACTIVE_STATUSES = {"starting", "ready", "filling"}


def _table():
    return boto3.resource("dynamodb", region_name=AWS_REGION).Table(SESSIONS_TABLE)


def create_session(
    *,
    session_id: str,
    user_id: str,
    job_id: str,
    platform: str,
    fargate_task_arn: str,
    ttl_seconds: int = 1800,
) -> dict:
    now = int(time.time())
    item = {
        "session_id": session_id,
        "user_id": user_id,
        "current_job_id": job_id,
        "platform": platform,
        "fargate_task_arn": fargate_task_arn,
        "status": "starting",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_activity_at": now,
        "ttl": now + ttl_seconds,
    }
    _table().put_item(Item=item)
    return item


def get_session(session_id: str) -> Optional[dict]:
    resp = _table().get_item(Key={"session_id": session_id})
    return resp.get("Item")


def set_connection_id(session_id: str, *, role: str, connection_id: str) -> None:
    if role not in _ALLOWED_ROLES:
        raise ValueError(f"role must be one of {_ALLOWED_ROLES}, got {role!r}")
    _table().update_item(
        Key={"session_id": session_id},
        UpdateExpression=f"SET ws_connection_{role} = :c, last_activity_at = :t",
        ExpressionAttributeValues={":c": connection_id, ":t": int(time.time())},
    )


def clear_connection_id(session_id: str, *, role: str) -> None:
    if role not in _ALLOWED_ROLES:
        raise ValueError(f"role must be one of {_ALLOWED_ROLES}, got {role!r}")
    _table().update_item(
        Key={"session_id": session_id},
        UpdateExpression=f"REMOVE ws_connection_{role}",
    )


def update_status(session_id: str, status: str) -> None:
    _table().update_item(
        Key={"session_id": session_id},
        UpdateExpression="SET #s = :s, last_activity_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":t": int(time.time())},
    )


def find_active_session_for_user(user_id: str) -> Optional[dict]:
    """Returns the newest active session for `user_id`, or None."""
    resp = _table().query(
        IndexName="user-sessions-index",
        KeyConditionExpression=boto3.dynamodb.conditions.Key("user_id").eq(user_id),
    )
    active = [i for i in resp.get("Items", []) if i.get("status") in _ACTIVE_STATUSES]
    if not active:
        return None
    active.sort(key=lambda i: i.get("last_activity_at", 0), reverse=True)
    return active[0]


def find_session_by_connection(connection_id: str) -> Optional[tuple[dict, str]]:
    """Scan for a session whose frontend OR browser connection id matches.

    Scan is acceptable at current scale (≤ tens of concurrent sessions,
    TTL 30 min). Revisit with a connection_id → session_id pointer item
    (same table, PK=`conn#<id>`) when concurrent sessions cross ~100."""
    resp = _table().scan(
        FilterExpression=(
            "ws_connection_frontend = :c OR ws_connection_browser = :c"
        ),
        ExpressionAttributeValues={":c": connection_id},
    )
    items = resp.get("Items", [])
    if not items:
        return None
    s = items[0]
    role = "frontend" if s.get("ws_connection_frontend") == connection_id else "browser"
    return s, role


def post_to_connection(*, api_id: str, region: str, connection_id: str, data: bytes) -> None:
    """Send payload to a WebSocket connection via API Gateway Management API.

    Caller handles GoneException (stale connection id) — the right fallback
    varies by caller (ws_route wants to clear the slot; stop-session may
    want to ignore)."""
    endpoint = f"https://{api_id}.execute-api.{region}.amazonaws.com/prod"
    client = boto3.client("apigatewaymanagementapi", endpoint_url=endpoint, region_name=region)
    client.post_to_connection(ConnectionId=connection_id, Data=data)
```

- [ ] **Step A1.5: Run — PASS**

```bash
pytest tests/unit/test_browser_sessions.py -v
```
Expected: PASS (10 tests)

- [ ] **Step A1.6: Commit**

```bash
git add shared/__init__.py shared/browser_sessions.py tests/unit/test_browser_sessions.py
git commit -m "feat(browser): add shared DynamoDB session helpers"
```

---

### Task A2: Two-audience WebSocket JWT

**Files:**
- Create: `shared/ws_auth.py`
- Test: `tests/unit/test_ws_auth.py`

- [ ] **Step A2.1: Write the failing test**

```python
# tests/unit/test_ws_auth.py
"""Unit tests for shared.ws_auth — short-lived two-audience WebSocket JWTs."""
import os
import time
from unittest.mock import patch

import pytest

_SECRET = "unit-test-secret"


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {"SUPABASE_JWT_SECRET": _SECRET}):
        yield


def test_issue_frontend_then_verify_roundtrip():
    from shared.ws_auth import issue_ws_token, verify_ws_token
    token = issue_ws_token(user_id="user-1", session_id="sess-9", role="frontend")
    claims = verify_ws_token(token, expected_role="frontend")
    assert claims["sub"] == "user-1"
    assert claims["session"] == "sess-9"
    assert claims["aud"] == "ws.frontend"


def test_issue_browser_then_verify_roundtrip():
    from shared.ws_auth import issue_ws_token, verify_ws_token
    token = issue_ws_token(user_id="user-1", session_id="sess-9", role="browser")
    claims = verify_ws_token(token, expected_role="browser")
    assert claims["aud"] == "ws.browser"


def test_frontend_token_rejected_for_browser_role():
    """CRITICAL: a stolen frontend token must not let an attacker claim role=browser."""
    from shared.ws_auth import issue_ws_token, verify_ws_token
    token = issue_ws_token(user_id="user-1", session_id="sess-9", role="frontend")
    with pytest.raises(ValueError, match="audience"):
        verify_ws_token(token, expected_role="browser")


def test_browser_token_rejected_for_frontend_role():
    from shared.ws_auth import issue_ws_token, verify_ws_token
    token = issue_ws_token(user_id="user-1", session_id="sess-9", role="browser")
    with pytest.raises(ValueError, match="audience"):
        verify_ws_token(token, expected_role="frontend")


def test_ws_token_expires_quickly():
    from shared.ws_auth import issue_ws_token, verify_ws_token
    token = issue_ws_token(user_id="user-1", session_id="sess-9", role="frontend", ttl_seconds=1)
    time.sleep(1.1)
    with pytest.raises(ValueError, match="expired"):
        verify_ws_token(token, expected_role="frontend")


def test_verify_rejects_regular_supabase_audience():
    """A regular API JWT (aud='authenticated') must not be usable on WS."""
    from jose import jwt
    from shared.ws_auth import verify_ws_token
    bad = jwt.encode(
        {"sub": "user-1", "aud": "authenticated", "exp": int(time.time()) + 60},
        _SECRET, algorithm="HS256",
    )
    with pytest.raises(ValueError, match="audience"):
        verify_ws_token(bad, expected_role="frontend")


def test_verify_rejects_bad_signature():
    from jose import jwt
    from shared.ws_auth import verify_ws_token
    forged = jwt.encode(
        {"sub": "user-1", "aud": "ws.frontend", "session": "sess-1", "exp": int(time.time()) + 60},
        "WRONG-SECRET", algorithm="HS256",
    )
    with pytest.raises(ValueError):
        verify_ws_token(forged, expected_role="frontend")


def test_issue_rejects_unknown_role():
    from shared.ws_auth import issue_ws_token
    with pytest.raises(ValueError, match="role must be"):
        issue_ws_token(user_id="u", session_id="s", role="attacker")


def test_verify_rejects_unknown_expected_role():
    from shared.ws_auth import verify_ws_token
    with pytest.raises(ValueError, match="role must be"):
        verify_ws_token("any-token", expected_role="attacker")


def test_issue_rejects_missing_secret():
    with patch.dict(os.environ, {}, clear=True):
        from shared.ws_auth import issue_ws_token
        with pytest.raises(RuntimeError, match="SUPABASE_JWT_SECRET"):
            issue_ws_token(user_id="u", session_id="s", role="frontend")
```

- [ ] **Step A2.2: Run — FAIL**

```bash
pytest tests/unit/test_ws_auth.py -v
```

- [ ] **Step A2.3: Implement `shared/ws_auth.py`**

```python
# shared/ws_auth.py
"""Short-lived JWT for WebSocket upgrade, with split audience per role.

Two distinct audiences prevent a stolen token issued for one side
(frontend/browser) from being replayed as the other. start-session issues
both tokens up front — the frontend token goes back in the HTTP response,
the browser token goes to Fargate via WS_TOKEN env var."""

from __future__ import annotations

import os
import time

from jose import JWTError, jwt

_ALG = "HS256"
_ROLES = ("frontend", "browser")
_DEFAULT_TTL = 60


def _aud_for(role: str) -> str:
    if role not in _ROLES:
        raise ValueError(f"role must be one of {_ROLES}, got {role!r}")
    return f"ws.{role}"


def _secret() -> str:
    s = os.environ.get("SUPABASE_JWT_SECRET", "")
    if not s:
        raise RuntimeError("SUPABASE_JWT_SECRET not set")
    return s


def issue_ws_token(*, user_id: str, session_id: str, role: str, ttl_seconds: int = _DEFAULT_TTL) -> str:
    aud = _aud_for(role)
    now = int(time.time())
    payload = {
        "sub": user_id,
        "session": session_id,
        "aud": aud,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, _secret(), algorithm=_ALG)


def verify_ws_token(token: str, *, expected_role: str) -> dict:
    """Decode and verify. Raises ValueError on any failure."""
    expected_aud = _aud_for(expected_role)
    try:
        claims = jwt.decode(token, _secret(), algorithms=[_ALG], audience=expected_aud)
    except JWTError as e:
        msg = str(e).lower()
        if "expired" in msg:
            raise ValueError("token expired") from e
        if "audience" in msg:
            raise ValueError("invalid audience") from e
        raise ValueError(f"invalid token: {e}") from e
    if not claims.get("sub") or not claims.get("session"):
        raise ValueError("token missing required claims")
    return claims
```

- [ ] **Step A2.4: Run — PASS** (10 tests)

- [ ] **Step A2.5: Commit**

```bash
git add shared/ws_auth.py tests/unit/test_ws_auth.py
git commit -m "feat(auth): add two-audience WebSocket JWT helper"
```

---

### Task A3: Profile completeness checker

**Files:**
- Create: `shared/profile_completeness.py`
- Test: `tests/unit/test_profile_completeness.py`

- [ ] **Step A3.1: Write the failing test**

```python
# tests/unit/test_profile_completeness.py
"""Unit tests for shared.profile_completeness."""


def _complete():
    return {
        "first_name": "Utkarsh", "last_name": "Singh",
        "email": "u@example.com", "phone": "+353851234567",
        "linkedin": "https://linkedin.com/in/u",
        "visa_status": "stamp-1g",
        "work_authorizations": {"IE": "stamp1g"},
        "default_referral_source": "LinkedIn",
        "notice_period_text": "2 weeks",
    }


def test_complete_profile_returns_empty_list():
    from shared.profile_completeness import check_profile_completeness
    assert check_profile_completeness(_complete()) == []


def test_missing_single_field_reported():
    from shared.profile_completeness import check_profile_completeness
    p = _complete(); del p["phone"]
    assert check_profile_completeness(p) == ["phone"]


def test_multiple_missing_preserves_order():
    from shared.profile_completeness import check_profile_completeness
    assert check_profile_completeness({}) == [
        "first_name", "last_name", "email", "phone", "linkedin",
        "visa_status", "work_authorizations",
        "default_referral_source", "notice_period_text",
    ]


def test_empty_work_authorizations_dict_treated_as_missing():
    from shared.profile_completeness import check_profile_completeness
    p = _complete(); p["work_authorizations"] = {}
    assert "work_authorizations" in check_profile_completeness(p)


def test_whitespace_only_string_treated_as_missing():
    from shared.profile_completeness import check_profile_completeness
    p = _complete(); p["phone"] = "   "
    assert "phone" in check_profile_completeness(p)


def test_none_profile_returns_all_required():
    from shared.profile_completeness import check_profile_completeness
    assert len(check_profile_completeness(None)) == 9
```

- [ ] **Step A3.2: Run — FAIL**

- [ ] **Step A3.3: Implement `shared/profile_completeness.py`**

```python
# shared/profile_completeness.py
"""Required-field check for the auto-apply flow (design §7.2)."""

from __future__ import annotations
from typing import Optional

REQUIRED_FIELDS = [
    "first_name", "last_name", "email", "phone", "linkedin",
    "visa_status", "work_authorizations",
    "default_referral_source", "notice_period_text",
]


def _is_missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (dict, list, tuple)) and not value:
        return True
    return False


def check_profile_completeness(profile: Optional[dict]) -> list[str]:
    if profile is None:
        return list(REQUIRED_FIELDS)
    return [f for f in REQUIRED_FIELDS if _is_missing(profile.get(f))]
```

- [ ] **Step A3.4: Run — PASS**

- [ ] **Step A3.5: Commit**

```bash
git add shared/profile_completeness.py tests/unit/test_profile_completeness.py
git commit -m "feat(apply): add profile completeness checker"
```

---

### Task A4: `load_job` helper

**Files:**
- Create: `shared/load_job.py`
- Test: `tests/unit/test_load_job.py`

- [ ] **Step A4.1: Write the failing test**

```python
# tests/unit/test_load_job.py
"""Unit tests for shared.load_job."""
from unittest.mock import MagicMock


def _mock_db(row: dict | None):
    db = MagicMock()
    table = MagicMock()
    db.client.table.return_value = table
    chain = MagicMock()
    table.select.return_value = chain
    chain.eq.return_value = chain
    chain.maybe_single.return_value = chain
    result = MagicMock()
    result.data = row
    chain.execute.return_value = result
    return db, table, chain


def test_load_job_returns_row_when_present():
    from shared.load_job import load_job
    row = {"job_id": "j1", "user_id": "u1"}
    db, _, _ = _mock_db(row)
    assert load_job("j1", "u1", db=db) == row


def test_load_job_returns_none_when_missing():
    from shared.load_job import load_job
    db, _, _ = _mock_db(None)
    assert load_job("j1", "u1", db=db) is None


def test_load_job_filters_by_user_id_for_rls():
    from shared.load_job import load_job
    db, _, chain = _mock_db({"job_id": "j1"})
    load_job("j1", "u1", db=db)
    eq_calls = chain.eq.call_args_list
    assert len(eq_calls) == 2
    args = [c.args for c in eq_calls]
    assert ("job_id", "j1") in args
    assert ("user_id", "u1") in args
```

- [ ] **Step A4.2: Run — FAIL**

- [ ] **Step A4.3: Implement `shared/load_job.py`**

```python
# shared/load_job.py
"""Single-row job lookup with user scoping."""

from __future__ import annotations
from typing import Optional


def load_job(job_id: str, user_id: str, *, db) -> Optional[dict]:
    """Load a jobs row by (job_id, user_id). None if not found."""
    resp = (
        db.client.table("jobs")
        .select("*")
        .eq("job_id", job_id)
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    return resp.data
```

- [ ] **Step A4.4: Run — PASS**

- [ ] **Step A4.5: Commit**

```bash
git add shared/load_job.py tests/unit/test_load_job.py
git commit -m "feat(apply): add shared load_job helper"
```

---

## Phase B — WebSocket Lambdas

Event shape reference (API Gateway v2 WebSocket $connect):

```json
{
  "requestContext": {"connectionId": "conn-abc", "routeKey": "$connect"},
  "headers": {"Authorization": "Bearer <token>"},
  "queryStringParameters": {"session": "sess-1", "role": "frontend"}
}
```

### Task B1: `ws_connect` — query-param session/role + header Bearer + audience check

**Files:**
- Modify: `lambdas/browser/ws_connect.py`
- Test: `tests/unit/test_ws_connect.py`

- [ ] **Step B1.1: Write the failing test**

```python
# tests/unit/test_ws_connect.py
"""Unit tests for the WebSocket $connect handler."""
import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {
        "SUPABASE_JWT_SECRET": "test-secret",
        "SESSIONS_TABLE": "test-sessions",
    }):
        yield


def _event(*, authorization, session_qs, role_qs, connection_id="conn-abc"):
    headers = {"Authorization": authorization} if authorization is not None else {}
    qs = {}
    if session_qs is not None:
        qs["session"] = session_qs
    if role_qs is not None:
        qs["role"] = role_qs
    return {
        "headers": headers,
        "queryStringParameters": qs or None,
        "requestContext": {"connectionId": connection_id},
    }


def _token(*, session_id, role, user_id="user-1", ttl=60):
    from shared.ws_auth import issue_ws_token
    return issue_ws_token(user_id=user_id, session_id=session_id, role=role, ttl_seconds=ttl)


def test_rejects_missing_authorization():
    from lambdas.browser.ws_connect import handler
    resp = handler(_event(authorization=None, session_qs="sess-1", role_qs="frontend"), None)
    assert resp["statusCode"] == 401


def test_rejects_wrong_scheme():
    from lambdas.browser.ws_connect import handler
    resp = handler(_event(authorization="Basic foo", session_qs="sess-1", role_qs="frontend"), None)
    assert resp["statusCode"] == 401


def test_rejects_missing_session_qs():
    from lambdas.browser.ws_connect import handler
    t = _token(session_id="sess-1", role="frontend")
    resp = handler(_event(authorization=f"Bearer {t}", session_qs=None, role_qs="frontend"), None)
    assert resp["statusCode"] == 400


def test_rejects_missing_role_qs():
    from lambdas.browser.ws_connect import handler
    t = _token(session_id="sess-1", role="frontend")
    resp = handler(_event(authorization=f"Bearer {t}", session_qs="sess-1", role_qs=None), None)
    assert resp["statusCode"] == 400


def test_rejects_invalid_role_qs():
    from lambdas.browser.ws_connect import handler
    t = _token(session_id="sess-1", role="frontend")
    resp = handler(_event(authorization=f"Bearer {t}", session_qs="sess-1", role_qs="attacker"), None)
    assert resp["statusCode"] == 400


def test_rejects_session_mismatch_between_token_and_query():
    from lambdas.browser.ws_connect import handler
    t = _token(session_id="sess-token", role="frontend")
    resp = handler(_event(authorization=f"Bearer {t}", session_qs="sess-query", role_qs="frontend"), None)
    assert resp["statusCode"] == 403


def test_rejects_frontend_token_used_with_role_browser():
    """Critical: token audience must match the role query param."""
    from lambdas.browser.ws_connect import handler
    t = _token(session_id="sess-1", role="frontend")
    resp = handler(_event(authorization=f"Bearer {t}", session_qs="sess-1", role_qs="browser"), None)
    assert resp["statusCode"] == 401


def test_rejects_token_for_unknown_session():
    from lambdas.browser.ws_connect import handler
    t = _token(session_id="sess-1", role="frontend")
    with patch("shared.browser_sessions.get_session", return_value=None):
        resp = handler(_event(authorization=f"Bearer {t}", session_qs="sess-1", role_qs="frontend"), None)
    assert resp["statusCode"] == 404


def test_rejects_when_token_user_does_not_match_session_user():
    from lambdas.browser.ws_connect import handler
    t = _token(session_id="sess-1", role="frontend", user_id="user-A")
    with patch("shared.browser_sessions.get_session", return_value={"session_id": "sess-1", "user_id": "user-B"}):
        resp = handler(_event(authorization=f"Bearer {t}", session_qs="sess-1", role_qs="frontend"), None)
    assert resp["statusCode"] == 403


def test_accepts_frontend_and_registers_connection():
    from lambdas.browser.ws_connect import handler
    t = _token(session_id="sess-1", role="frontend", user_id="user-1")
    with patch("shared.browser_sessions.get_session", return_value={"session_id": "sess-1", "user_id": "user-1"}), \
         patch("shared.browser_sessions.set_connection_id") as m_set:
        resp = handler(_event(authorization=f"Bearer {t}", session_qs="sess-1", role_qs="frontend"), None)
    assert resp["statusCode"] == 200
    m_set.assert_called_once_with("sess-1", role="frontend", connection_id="conn-abc")


def test_accepts_browser_and_registers_connection():
    from lambdas.browser.ws_connect import handler
    t = _token(session_id="sess-1", role="browser", user_id="user-1")
    with patch("shared.browser_sessions.get_session", return_value={"session_id": "sess-1", "user_id": "user-1"}), \
         patch("shared.browser_sessions.set_connection_id") as m_set:
        resp = handler(_event(authorization=f"Bearer {t}", session_qs="sess-1", role_qs="browser"), None)
    assert resp["statusCode"] == 200
    m_set.assert_called_once_with("sess-1", role="browser", connection_id="conn-abc")
```

- [ ] **Step B1.2: Run — FAIL** (stub returns 200 always)

- [ ] **Step B1.3: Implement `lambdas/browser/ws_connect.py`**

```python
# lambdas/browser/ws_connect.py
"""WebSocket $connect handler.

Matches Plan 2's client contract (browser/browser_session.py:410):
  wss://.../prod?session={session_id}&role={frontend|browser}
  Authorization: Bearer <ws_token>"""

from __future__ import annotations

import logging

from shared.browser_sessions import get_session, set_connection_id
from shared.ws_auth import verify_ws_token

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_ROLES = {"frontend", "browser"}


def _resp(code: int, msg: str) -> dict:
    return {"statusCode": code, "body": msg}


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    authz = headers.get("authorization")
    qs = event.get("queryStringParameters") or {}
    session_qs = qs.get("session")
    role_qs = qs.get("role")

    if not authz or not authz.lower().startswith("bearer "):
        return _resp(401, "unauthorized")
    if not session_qs:
        return _resp(400, "missing session query param")
    if role_qs not in _ROLES:
        return _resp(400, "invalid role query param")

    token = authz[7:].strip()
    try:
        claims = verify_ws_token(token, expected_role=role_qs)
    except ValueError as e:
        logger.warning("token verification failed: %s", e)
        return _resp(401, "unauthorized")

    if claims["session"] != session_qs:
        return _resp(403, "forbidden")

    session = get_session(session_qs)
    if not session:
        return _resp(404, "session not found")
    if session.get("user_id") != claims["sub"]:
        return _resp(403, "forbidden")

    connection_id = event["requestContext"]["connectionId"]
    set_connection_id(session_qs, role=role_qs, connection_id=connection_id)
    logger.info("WS connect: session=%s role=%s conn=%s", session_qs, role_qs, connection_id)
    return _resp(200, "connected")
```

- [ ] **Step B1.4: Run — PASS** (11 tests)

- [ ] **Step B1.5: Commit**

```bash
git add lambdas/browser/ws_connect.py tests/unit/test_ws_connect.py
git commit -m "feat(browser): implement ws_connect with query-param routing and split-audience JWT"
```

---

### Task B2: `ws_disconnect` — cleanup + conditional stop

**Files:**
- Modify: `lambdas/browser/ws_disconnect.py`
- Test: `tests/unit/test_ws_disconnect.py`

- [ ] **Step B2.1: Write the failing test**

```python
# tests/unit/test_ws_disconnect.py
import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {
        "SESSIONS_TABLE": "test-sessions",
        "CLUSTER_ARN": "arn:aws:ecs:eu-west-1:1:cluster/c",
    }):
        yield


def _event(connection_id="conn-disc"):
    return {"requestContext": {"connectionId": connection_id}}


def test_noop_when_connection_matches_no_session():
    from lambdas.browser.ws_disconnect import handler
    with patch("shared.browser_sessions.find_session_by_connection", return_value=None):
        resp = handler(_event(), None)
    assert resp["statusCode"] == 200


def test_clears_frontend_leaves_browser_alive():
    from lambdas.browser.ws_disconnect import handler
    session = {
        "session_id": "sess-1",
        "ws_connection_frontend": "conn-disc",
        "ws_connection_browser": "conn-br",
    }
    with patch("shared.browser_sessions.find_session_by_connection", return_value=(session, "frontend")), \
         patch("shared.browser_sessions.clear_connection_id") as m_clear, \
         patch("shared.browser_sessions.update_status") as m_status, \
         patch("boto3.client") as m_boto:
        resp = handler(_event(), None)
    assert resp["statusCode"] == 200
    m_clear.assert_called_once_with("sess-1", role="frontend")
    m_status.assert_not_called()
    m_boto.return_value.stop_task.assert_not_called()


def test_stops_fargate_when_both_sides_disconnected():
    from lambdas.browser.ws_disconnect import handler
    session = {
        "session_id": "sess-1",
        "ws_connection_frontend": "conn-disc",
        "fargate_task_arn": "arn:ecs:task/xyz",
    }
    with patch("shared.browser_sessions.find_session_by_connection", return_value=(session, "frontend")), \
         patch("shared.browser_sessions.clear_connection_id"), \
         patch("shared.browser_sessions.update_status") as m_status, \
         patch("boto3.client") as m_boto:
        resp = handler(_event(), None)
    assert resp["statusCode"] == 200
    m_status.assert_called_once_with("sess-1", "ended")
    m_boto.return_value.stop_task.assert_called_once()


def test_swallows_stop_task_failure():
    from lambdas.browser.ws_disconnect import handler
    session = {
        "session_id": "sess-1",
        "ws_connection_frontend": "conn-disc",
        "fargate_task_arn": "arn:ecs:task/xyz",
    }
    ecs = MagicMock()
    ecs.stop_task.side_effect = Exception("ECS blew up")
    with patch("shared.browser_sessions.find_session_by_connection", return_value=(session, "frontend")), \
         patch("shared.browser_sessions.clear_connection_id"), \
         patch("shared.browser_sessions.update_status"), \
         patch("boto3.client", return_value=ecs):
        resp = handler(_event(), None)
    assert resp["statusCode"] == 200


def test_top_level_never_throws():
    from lambdas.browser.ws_disconnect import handler
    with patch("shared.browser_sessions.find_session_by_connection", side_effect=RuntimeError("boom")):
        resp = handler(_event(), None)
    assert resp["statusCode"] == 200
```

- [ ] **Step B2.2: Run — FAIL**

- [ ] **Step B2.3: Implement `lambdas/browser/ws_disconnect.py`**

```python
# lambdas/browser/ws_disconnect.py
"""WebSocket $disconnect handler.

Clears the matching slot on the session row; if both sides are now absent,
transitions status=ended and best-effort stops the Fargate task."""

from __future__ import annotations

import logging
import os

import boto3

from shared.browser_sessions import (
    clear_connection_id,
    find_session_by_connection,
    update_status,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CLUSTER_ARN = os.environ.get("CLUSTER_ARN", "")


def handler(event, context):
    connection_id = event.get("requestContext", {}).get("connectionId", "")
    try:
        found = find_session_by_connection(connection_id)
        if not found:
            return {"statusCode": 200, "body": "noop"}

        session, role = found
        session_id = session["session_id"]
        clear_connection_id(session_id, role=role)

        other_role = "browser" if role == "frontend" else "frontend"
        other_conn = session.get(f"ws_connection_{other_role}")
        if not other_conn:
            update_status(session_id, "ended")
            task_arn = session.get("fargate_task_arn")
            if task_arn and CLUSTER_ARN:
                try:
                    boto3.client("ecs").stop_task(
                        cluster=CLUSTER_ARN,
                        task=task_arn,
                        reason="Both WS peers disconnected",
                    )
                except Exception as e:
                    logger.warning("stop_task failed for %s: %s", task_arn, e)
        logger.info("WS disconnect: session=%s role=%s peer_left=%s",
                    session_id, role, not other_conn)
    except Exception as e:
        logger.exception("WS disconnect handler failed: %s", e)
    return {"statusCode": 200, "body": "disconnected"}
```

- [ ] **Step B2.4: Run — PASS** (5 tests)

- [ ] **Step B2.5: Commit**

```bash
git add lambdas/browser/ws_disconnect.py tests/unit/test_ws_disconnect.py
git commit -m "feat(browser): implement ws_disconnect cleanup + Fargate stop"
```

---

### Task B3: `ws_route` — relay text messages between peers

**Files:**
- Modify: `lambdas/browser/ws_route.py`
- Test: `tests/unit/test_ws_route.py`

- [ ] **Step B3.1: Write the failing test**

```python
# tests/unit/test_ws_route.py
import json
import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {
        "SESSIONS_TABLE": "test-sessions",
        "WEBSOCKET_API_ID": "ws-abc",
        "AWS_REGION": "eu-west-1",
    }):
        yield


def _event(*, connection_id, body):
    return {"requestContext": {"connectionId": connection_id}, "body": body}


def test_relays_frontend_message_to_browser_peer():
    from lambdas.browser.ws_route import handler
    session = {
        "session_id": "sess-1",
        "ws_connection_frontend": "conn-fe",
        "ws_connection_browser": "conn-br",
    }
    payload = json.dumps({"action": "click", "x": 10, "y": 20})
    with patch("shared.browser_sessions.find_session_by_connection", return_value=(session, "frontend")), \
         patch("shared.browser_sessions.post_to_connection") as m_post:
        resp = handler(_event(connection_id="conn-fe", body=payload), None)
    assert resp["statusCode"] == 200
    call = m_post.call_args.kwargs
    assert call["connection_id"] == "conn-br"
    assert call["data"] == payload.encode("utf-8")


def test_relays_browser_message_to_frontend_peer():
    from lambdas.browser.ws_route import handler
    session = {
        "session_id": "sess-1",
        "ws_connection_frontend": "conn-fe",
        "ws_connection_browser": "conn-br",
    }
    payload = json.dumps({"action": "status", "status": "ready"})
    with patch("shared.browser_sessions.find_session_by_connection", return_value=(session, "browser")), \
         patch("shared.browser_sessions.post_to_connection") as m_post:
        resp = handler(_event(connection_id="conn-br", body=payload), None)
    assert resp["statusCode"] == 200
    assert m_post.call_args.kwargs["connection_id"] == "conn-fe"


def test_drops_when_peer_absent():
    from lambdas.browser.ws_route import handler
    session = {"session_id": "sess-1", "ws_connection_frontend": "conn-fe"}
    with patch("shared.browser_sessions.find_session_by_connection", return_value=(session, "frontend")), \
         patch("shared.browser_sessions.post_to_connection") as m_post:
        resp = handler(_event(connection_id="conn-fe", body='{"action":"click"}'), None)
    assert resp["statusCode"] == 200
    m_post.assert_not_called()


def test_drops_when_no_session_found():
    from lambdas.browser.ws_route import handler
    with patch("shared.browser_sessions.find_session_by_connection", return_value=None), \
         patch("shared.browser_sessions.post_to_connection") as m_post:
        resp = handler(_event(connection_id="conn-ghost", body='{"a":1}'), None)
    assert resp["statusCode"] == 200
    m_post.assert_not_called()


def test_clears_peer_on_gone_exception():
    from botocore.exceptions import ClientError
    from lambdas.browser.ws_route import handler

    session = {
        "session_id": "sess-1",
        "ws_connection_frontend": "conn-fe",
        "ws_connection_browser": "conn-br",
    }
    gone = ClientError({"Error": {"Code": "GoneException"}}, "PostToConnection")
    with patch("shared.browser_sessions.find_session_by_connection", return_value=(session, "frontend")), \
         patch("shared.browser_sessions.post_to_connection", side_effect=gone), \
         patch("shared.browser_sessions.clear_connection_id") as m_clear:
        resp = handler(_event(connection_id="conn-fe", body='{"a":1}'), None)
    assert resp["statusCode"] == 200
    m_clear.assert_called_once_with("sess-1", role="browser")


def test_rejects_oversized_body():
    from lambdas.browser.ws_route import handler
    big = "x" * 200_000
    resp = handler(_event(connection_id="any", body=big), None)
    assert resp["statusCode"] == 413
```

- [ ] **Step B3.2: Run — FAIL**

- [ ] **Step B3.3: Implement `lambdas/browser/ws_route.py`**

```python
# lambdas/browser/ws_route.py
"""WebSocket $default handler — relays text messages between frontend and browser.

Screenshots bypass this Lambda (design §7.3): Fargate posts them directly
to the frontend's connection via the Management API. Only text/JSON control
messages (≤128KB) flow through here."""

from __future__ import annotations

import logging
import os

from botocore.exceptions import ClientError

from shared.browser_sessions import (
    clear_connection_id,
    find_session_by_connection,
    post_to_connection,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

WEBSOCKET_API_ID = os.environ.get("WEBSOCKET_API_ID", "")
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")

_MAX_BODY = 128 * 1024


def handler(event, context):
    body = event.get("body", "") or ""
    if len(body) > _MAX_BODY:
        return {"statusCode": 413, "body": "payload too large"}

    connection_id = event.get("requestContext", {}).get("connectionId", "")
    found = find_session_by_connection(connection_id)
    if not found:
        return {"statusCode": 200, "body": "noop"}

    session, sender_role = found
    peer_role = "browser" if sender_role == "frontend" else "frontend"
    peer_conn = session.get(f"ws_connection_{peer_role}")
    if not peer_conn:
        return {"statusCode": 200, "body": "no peer"}

    try:
        post_to_connection(
            api_id=WEBSOCKET_API_ID,
            region=AWS_REGION,
            connection_id=peer_conn,
            data=body.encode("utf-8"),
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "GoneException":
            clear_connection_id(session["session_id"], role=peer_role)
        else:
            logger.exception("PostToConnection failed: %s", e)
    return {"statusCode": 200, "body": "ok"}
```

- [ ] **Step B3.4: Run — PASS** (6 tests)

- [ ] **Step B3.5: Commit**

```bash
git add lambdas/browser/ws_route.py tests/unit/test_ws_route.py
git commit -m "feat(browser): implement ws_route message relay"
```

---

## Phase C — Backend endpoints

### Task C1: `GET /api/apply/eligibility/{job_id}`

**Files:**
- Modify: `app.py` (replace the 3-line stub at the current line 2406-2409)
- Test: `tests/unit/test_apply_endpoints.py`

- [ ] **Step C1.1: Write the failing tests**

```python
# tests/unit/test_apply_endpoints.py
"""Unit tests for /api/apply/* endpoints."""
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {
        "SUPABASE_JWT_SECRET": "test-secret",
        "SESSIONS_TABLE": "test-sessions",
        "CLUSTER_ARN": "arn:aws:ecs:eu-west-1:1:cluster/c",
        "TASK_DEF": "td",
        "SECURITY_GROUP": "sg-1",
        "BROWSER_SUBNET_IDS": "subnet-a,subnet-b",
        "BROWSER_WS_URL": "wss://ws.example/prod",
        "WEBSOCKET_API_ID": "ws-abc",
        "AWS_REGION": "eu-west-1",
    }):
        yield


@pytest.fixture
def client(monkeypatch):
    import app as app_module
    from auth import AuthUser, get_current_user

    db = MagicMock()
    monkeypatch.setattr(app_module, "_db", db)

    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id="user-1", email="u@example.com",
    )
    yield TestClient(app_module.app), db
    app_module.app.dependency_overrides.clear()


def _job_row(**over):
    row = {
        "job_id": "j1", "user_id": "user-1", "title": "Backend",
        "company": "Acme", "apply_platform": "greenhouse",
        "apply_board_token": "acme", "apply_posting_id": "12345",
        "apply_url": "https://boards.greenhouse.io/acme/jobs/12345",
        "canonical_hash": "h-abc",
        "resume_s3_key": "users/user-1/resumes/v1.pdf",
        "resume_version": 1, "job_hash": "jh-1",
    }
    row.update(over)
    return row


def _complete_user():
    return {
        "id": "user-1",
        "first_name": "U", "last_name": "S", "email": "u@e.com",
        "phone": "+353851234567", "linkedin": "https://linkedin.com/in/u",
        "visa_status": "stamp1g",
        "work_authorizations": {"IE": "stamp1g"},
        "default_referral_source": "LinkedIn",
        "notice_period_text": "2 weeks",
    }


def _no_existing_apps(db):
    chain = db.client.table.return_value.select.return_value.eq.return_value.eq.return_value.not_.in_.return_value
    chain.execute.return_value = MagicMock(data=[])


def _existing_app(db, row):
    chain = db.client.table.return_value.select.return_value.eq.return_value.eq.return_value.not_.in_.return_value
    chain.execute.return_value = MagicMock(data=[row])


# ---- Eligibility ----

def test_eligibility_job_not_found(client):
    c, _ = client
    with patch("shared.load_job.load_job", return_value=None):
        r = c.get("/api/apply/eligibility/j1")
    assert r.status_code == 404


def test_eligibility_platform_not_supported(client):
    c, _ = client
    with patch("shared.load_job.load_job", return_value=_job_row(apply_platform=None)):
        r = c.get("/api/apply/eligibility/j1")
    assert r.json() == {"eligible": False, "reason": "not_supported_platform"}


def test_eligibility_no_resume(client):
    c, _ = client
    with patch("shared.load_job.load_job", return_value=_job_row(resume_s3_key=None)):
        r = c.get("/api/apply/eligibility/j1")
    assert r.json()["reason"] == "no_resume"


def test_eligibility_already_applied(client):
    c, db = client
    _existing_app(db, {"id": "app-1", "status": "submitted", "submitted_at": "2026-04-24T10:00:00Z"})
    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.get("/api/apply/eligibility/j1")
    assert r.json()["reason"] == "already_applied"
    assert r.json()["application_id"] == "app-1"


def test_eligibility_profile_incomplete(client):
    c, db = client
    _no_existing_apps(db)
    db.get_user.return_value = {"id": "user-1"}
    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.get("/api/apply/eligibility/j1")
    body = r.json()
    assert body["reason"] == "profile_incomplete"
    assert "phone" in body["missing_required_fields"]


def test_eligibility_happy_path(client):
    c, db = client
    _no_existing_apps(db)
    db.get_user.return_value = _complete_user()
    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.get("/api/apply/eligibility/j1")
    assert r.json() == {
        "eligible": True, "platform": "greenhouse",
        "board_token": "acme", "posting_id": "12345",
    }
```

- [ ] **Step C1.2: Run — FAIL**

- [ ] **Step C1.3: Replace eligibility stub in `app.py`**

Locate `app.py:2406-2409` and replace the 3-line 501 stub with:

```python
@app.get("/api/apply/eligibility/{job_id}")
def apply_eligibility(job_id: str, user: AuthUser = Depends(get_current_user)):
    """Per-job eligibility — no AI, no network calls to platforms."""
    from shared.load_job import load_job
    from shared.profile_completeness import check_profile_completeness

    if not _db:
        raise HTTPException(503, "Database not configured")

    job = load_job(job_id, user.id, db=_db)
    if not job:
        raise HTTPException(404, "Job not found")
    if not job.get("apply_platform"):
        return {"eligible": False, "reason": "not_supported_platform"}
    if not job.get("resume_s3_key"):
        return {"eligible": False, "reason": "no_resume"}

    existing = (
        _db.client.table("applications")
        .select("id, status, submitted_at")
        .eq("user_id", user.id)
        .eq("canonical_hash", job.get("canonical_hash") or "")
        .not_.in_("status", ["unknown", "failed"])
        .execute()
    )
    if existing.data:
        return {
            "eligible": False,
            "reason": "already_applied",
            "application_id": existing.data[0]["id"],
            "applied_at": existing.data[0].get("submitted_at"),
        }

    missing = check_profile_completeness(_db.get_user(user.id))
    if missing:
        return {
            "eligible": False,
            "reason": "profile_incomplete",
            "missing_required_fields": missing,
        }

    return {
        "eligible": True,
        "platform": job["apply_platform"],
        "board_token": job.get("apply_board_token"),
        "posting_id": job.get("apply_posting_id"),
    }
```

- [ ] **Step C1.4: Run — PASS** (6 tests)

- [ ] **Step C1.5: Commit**

```bash
git add app.py tests/unit/test_apply_endpoints.py
git commit -m "feat(apply): implement GET /api/apply/eligibility/{job_id}"
```

---

### Task C2: `GET /api/apply/preview/{job_id}` (minimal — Plan 3b fills in AI)

**Files:**
- Modify: `app.py` (replace stub 2412-2415)
- Test: `tests/unit/test_apply_endpoints.py` (append)

- [ ] **Step C2.1: Add failing tests**

```python
# Append to tests/unit/test_apply_endpoints.py

# ---- Preview (minimal, Plan 3b fills AI) ----

def test_preview_job_not_found(client):
    c, _ = client
    with patch("shared.load_job.load_job", return_value=None):
        r = c.get("/api/apply/preview/j1")
    assert r.status_code == 404


def test_preview_returns_ineligible_for_unsupported_platform(client):
    c, _ = client
    with patch("shared.load_job.load_job", return_value=_job_row(apply_platform=None)):
        r = c.get("/api/apply/preview/j1")
    assert r.status_code == 200
    assert r.json() == {"eligible": False, "reason": "not_supported_platform"}


def test_preview_returns_already_applied(client):
    """Preview must apply the SAME eligibility gates as /eligibility."""
    c, db = client
    _existing_app(db, {"id": "app-1", "status": "submitted"})
    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.get("/api/apply/preview/j1")
    assert r.json()["reason"] == "already_applied"


def test_preview_returns_profile_incomplete(client):
    c, db = client
    _no_existing_apps(db)
    db.get_user.return_value = {"id": "user-1"}
    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.get("/api/apply/preview/j1")
    assert r.json()["reason"] == "profile_incomplete"


def test_preview_happy_path_returns_snapshot_without_ai_answers(client):
    c, db = client
    _no_existing_apps(db)
    db.get_user.return_value = _complete_user()
    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.get("/api/apply/preview/j1")
    body = r.json()
    assert r.status_code == 200
    assert body["eligible"] is True
    assert body["job"]["job_id"] == "j1"
    assert body["profile"]["first_name"] == "U"
    assert body["resume"]["s3_key"] == "users/user-1/resumes/v1.pdf"
    assert body["answers_generated"] is False
    assert body["answers"] == []
    assert body["questions"] == []
```

- [ ] **Step C2.2: Run — FAIL**

- [ ] **Step C2.3: Replace preview stub in `app.py`**

```python
@app.get("/api/apply/preview/{job_id}")
def apply_preview(job_id: str, user: AuthUser = Depends(get_current_user)):
    """Apply preview snapshot. Plan 3a returns no AI answers; Plan 3b will
    populate `questions` (platform metadata) and `answers` (AI-generated)
    without changing this response shape."""
    from shared.load_job import load_job
    from shared.profile_completeness import check_profile_completeness

    if not _db:
        raise HTTPException(503, "Database not configured")

    job = load_job(job_id, user.id, db=_db)
    if not job:
        raise HTTPException(404, "Job not found")

    if not job.get("apply_platform"):
        return {"eligible": False, "reason": "not_supported_platform"}
    if not job.get("resume_s3_key"):
        return {"eligible": False, "reason": "no_resume"}

    existing = (
        _db.client.table("applications")
        .select("id, status, submitted_at")
        .eq("user_id", user.id)
        .eq("canonical_hash", job.get("canonical_hash") or "")
        .not_.in_("status", ["unknown", "failed"])
        .execute()
    )
    if existing.data:
        return {
            "eligible": False,
            "reason": "already_applied",
            "application_id": existing.data[0]["id"],
        }

    profile = _db.get_user(user.id) or {}
    missing = check_profile_completeness(profile)
    if missing:
        return {
            "eligible": False,
            "reason": "profile_incomplete",
            "missing_required_fields": missing,
        }

    return {
        "eligible": True,
        "job": {
            "job_id": job["job_id"],
            "title": job.get("title"),
            "company": job.get("company"),
            "apply_url": job.get("apply_url"),
            "platform": job.get("apply_platform"),
        },
        "profile": {k: profile.get(k) for k in (
            "first_name", "last_name", "email", "phone", "linkedin",
            "github", "website", "location", "visa_status",
        )},
        "resume": {
            "s3_key": job.get("resume_s3_key"),
            "version": job.get("resume_version", 1),
        },
        "questions": [],
        "answers": [],
        "answers_generated": False,
    }
```

- [ ] **Step C2.4: Run — PASS** (5 tests)

- [ ] **Step C2.5: Commit**

```bash
git add app.py tests/unit/test_apply_endpoints.py
git commit -m "feat(apply): minimal preview endpoint (AI answers land in Plan 3b)"
```

---

### Task C3: `POST /api/apply/start-session`

**Files:**
- Modify: `app.py` (replace stub 2424-2427; delete legacy `/api/apply/submit/{job_id}` stub 2418-2421)
- Test: `tests/unit/test_apply_endpoints.py` (append)

- [ ] **Step C3.1: Add failing tests**

```python
# Append to tests/unit/test_apply_endpoints.py

# ---- Start Session ----

def test_start_session_rejects_incomplete_profile(client):
    c, db = client
    db.get_user.return_value = {"id": "user-1"}
    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.post("/api/apply/start-session", json={"job_id": "j1"})
    assert r.status_code == 412
    assert "profile_incomplete" in r.json()["detail"]


def test_start_session_reuses_warm_session(client):
    c, db = client
    db.get_user.return_value = _complete_user()
    existing = {"session_id": "warm-1", "status": "ready", "user_id": "user-1"}
    with patch("shared.load_job.load_job", return_value=_job_row()), \
         patch("shared.browser_sessions.find_active_session_for_user", return_value=existing), \
         patch("shared.browser_sessions.create_session") as m_create, \
         patch("boto3.client") as m_boto:
        r = c.post("/api/apply/start-session", json={"job_id": "j1"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == "warm-1"
    assert body["reused"] is True
    m_create.assert_not_called()
    m_boto.return_value.run_task.assert_not_called()
    from shared.ws_auth import verify_ws_token
    claims = verify_ws_token(body["ws_token"], expected_role="frontend")
    assert claims["session"] == "warm-1"


def test_start_session_launches_fargate_with_browser_token_in_env(client):
    c, db = client
    db.get_user.return_value = _complete_user()
    ecs = MagicMock()
    ecs.run_task.return_value = {
        "tasks": [{"taskArn": "arn:aws:ecs:eu-west-1:1:task/abc"}],
        "failures": [],
    }
    with patch("shared.load_job.load_job", return_value=_job_row()), \
         patch("shared.browser_sessions.find_active_session_for_user", return_value=None), \
         patch("shared.browser_sessions.create_session") as m_create, \
         patch("boto3.client", return_value=ecs):
        m_create.return_value = {"session_id": "sess-new", "user_id": "user-1"}
        r = c.post("/api/apply/start-session", json={"job_id": "j1"})
    assert r.status_code == 200
    body = r.json()
    task_kwargs = ecs.run_task.call_args.kwargs
    env = {e["name"]: e["value"] for e in task_kwargs["overrides"]["containerOverrides"][0]["environment"]}
    assert env["JOB_ID"] == "j1"
    assert env["USER_ID"] == "user-1"
    assert env["PLATFORM"] == "greenhouse"
    from shared.ws_auth import verify_ws_token
    # WS_TOKEN in Fargate env must be BROWSER-audience
    verify_ws_token(env["WS_TOKEN"], expected_role="browser")
    # Token returned in response must be FRONTEND-audience
    verify_ws_token(body["ws_token"], expected_role="frontend")
    assert task_kwargs["networkConfiguration"]["awsvpcConfiguration"]["subnets"] == ["subnet-a", "subnet-b"]


def test_start_session_503_when_ecs_returns_failures(client):
    c, db = client
    db.get_user.return_value = _complete_user()
    ecs = MagicMock()
    ecs.run_task.return_value = {"tasks": [], "failures": [{"reason": "RESOURCE:MEMORY"}]}
    with patch("shared.load_job.load_job", return_value=_job_row()), \
         patch("shared.browser_sessions.find_active_session_for_user", return_value=None), \
         patch("boto3.client", return_value=ecs):
        r = c.post("/api/apply/start-session", json={"job_id": "j1"})
    assert r.status_code == 503
```

- [ ] **Step C3.2: Run — FAIL**

- [ ] **Step C3.3: Replace start-session stub + delete submit stub in `app.py`**

Ensure `import uuid` is near the other top-of-file imports (add if missing). Then replace the start-session stub:

```python
class StartSessionRequest(BaseModel):
    job_id: str


class StartSessionResponse(BaseModel):
    session_id: str
    ws_url: str
    ws_token: str       # FRONTEND-audience token
    status: str
    reused: bool = False


@app.post("/api/apply/start-session", response_model=StartSessionResponse)
def apply_start_session(
    req: StartSessionRequest,
    user: AuthUser = Depends(get_current_user),
):
    """Launch a Fargate Chrome task for applying to a job."""
    import uuid
    from shared.load_job import load_job
    from shared.profile_completeness import check_profile_completeness
    from shared.browser_sessions import create_session, find_active_session_for_user
    from shared.ws_auth import issue_ws_token

    if not _db:
        raise HTTPException(503, "Database not configured")

    job = load_job(req.job_id, user.id, db=_db)
    if not job:
        raise HTTPException(404, "Job not found")

    profile = _db.get_user(user.id) or {}
    missing = check_profile_completeness(profile)
    if missing:
        raise HTTPException(412, f"profile_incomplete:{','.join(missing)}")

    existing = find_active_session_for_user(user.id)
    if existing:
        sid = existing["session_id"]
        return StartSessionResponse(
            session_id=sid,
            ws_url=os.environ.get("BROWSER_WS_URL", ""),
            ws_token=issue_ws_token(user_id=user.id, session_id=sid, role="frontend"),
            status=existing.get("status", "ready"),
            reused=True,
        )

    session_id = str(uuid.uuid4())
    frontend_token = issue_ws_token(user_id=user.id, session_id=session_id, role="frontend")
    browser_token = issue_ws_token(user_id=user.id, session_id=session_id, role="browser")

    subnet_ids = [s for s in os.environ.get("BROWSER_SUBNET_IDS", "").split(",") if s]
    if not subnet_ids:
        raise HTTPException(500, "BROWSER_SUBNET_IDS not configured")

    ecs = boto3.client("ecs", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
    result = ecs.run_task(
        cluster=os.environ["CLUSTER_ARN"],
        taskDefinition=os.environ["TASK_DEF"],
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": subnet_ids,
                "securityGroups": [os.environ.get("SECURITY_GROUP", "")],
                "assignPublicIp": "ENABLED",
            }
        },
        overrides={
            "containerOverrides": [{
                "name": "browser",
                "environment": [
                    {"name": "SESSION_ID", "value": session_id},
                    {"name": "USER_ID", "value": user.id},
                    {"name": "JOB_ID", "value": req.job_id},
                    {"name": "APPLY_URL", "value": job.get("apply_url", "")},
                    {"name": "PLATFORM", "value": job.get("apply_platform", "unknown")},
                    {"name": "WS_TOKEN", "value": browser_token},
                ],
            }],
        },
    )

    if result.get("failures") or not result.get("tasks"):
        logger.error("Fargate run_task failed: %s", result)
        raise HTTPException(503, "Failed to launch browser session")

    create_session(
        session_id=session_id,
        user_id=user.id,
        job_id=req.job_id,
        platform=job.get("apply_platform", "unknown"),
        fargate_task_arn=result["tasks"][0]["taskArn"],
    )

    return StartSessionResponse(
        session_id=session_id,
        ws_url=os.environ.get("BROWSER_WS_URL", ""),
        ws_token=frontend_token,
        status="starting",
        reused=False,
    )
```

Delete the legacy stub at `app.py:2418-2421`:

```python
@app.post("/api/apply/submit/{job_id}")
def apply_submit(job_id: str, user: AuthUser = Depends(get_current_user)):
    """Submit an application to the platform. Stub — returns 501."""
    raise HTTPException(501, "Auto-apply submission not yet implemented")
```

- [ ] **Step C3.4: Run — PASS** (4 tests)

- [ ] **Step C3.5: Commit**

```bash
git add app.py tests/unit/test_apply_endpoints.py
git commit -m "feat(apply): start-session launches Fargate with split-audience tokens"
```

---

### Task C4: `POST /api/apply/stop-session`

**Files:**
- Modify: `app.py` (replace stop-session stub)
- Test: `tests/unit/test_apply_endpoints.py` (append)

- [ ] **Step C4.1: Add failing tests**

```python
# Append to tests/unit/test_apply_endpoints.py

# ---- Stop Session ----

def test_stop_session_404_unknown(client):
    c, _ = client
    with patch("shared.browser_sessions.get_session", return_value=None):
        r = c.post("/api/apply/stop-session", json={"session_id": "sess-x"})
    assert r.status_code == 404


def test_stop_session_403_wrong_user(client):
    c, _ = client
    with patch("shared.browser_sessions.get_session", return_value={
        "session_id": "sess-1", "user_id": "someone-else", "fargate_task_arn": "arn:x",
    }):
        r = c.post("/api/apply/stop-session", json={"session_id": "sess-1"})
    assert r.status_code == 403


def test_stop_session_happy_path(client):
    c, _ = client
    ecs = MagicMock()
    with patch("shared.browser_sessions.get_session", return_value={
        "session_id": "sess-1", "user_id": "user-1", "fargate_task_arn": "arn:x",
    }), patch("boto3.client", return_value=ecs), \
         patch("shared.browser_sessions.update_status") as m_status:
        r = c.post("/api/apply/stop-session", json={"session_id": "sess-1"})
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"
    ecs.stop_task.assert_called_once()
    m_status.assert_called_once_with("sess-1", "ended")


def test_stop_session_still_marks_ended_if_ecs_fails(client):
    c, _ = client
    ecs = MagicMock()
    ecs.stop_task.side_effect = Exception("ECS down")
    with patch("shared.browser_sessions.get_session", return_value={
        "session_id": "sess-1", "user_id": "user-1", "fargate_task_arn": "arn:x",
    }), patch("boto3.client", return_value=ecs), \
         patch("shared.browser_sessions.update_status") as m_status:
        r = c.post("/api/apply/stop-session", json={"session_id": "sess-1"})
    assert r.status_code == 200
    m_status.assert_called_once_with("sess-1", "ended")
```

- [ ] **Step C4.2: Run — FAIL**

- [ ] **Step C4.3: Replace stop-session stub in `app.py`**

```python
class StopSessionRequest(BaseModel):
    session_id: str


@app.post("/api/apply/stop-session")
def apply_stop_session(
    req: StopSessionRequest,
    user: AuthUser = Depends(get_current_user),
):
    """Stop a cloud browser session — ecs:StopTask + mark session ended."""
    from shared.browser_sessions import get_session, update_status

    session = get_session(req.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.get("user_id") != user.id:
        raise HTTPException(403, "Not your session")

    task_arn = session.get("fargate_task_arn")
    if task_arn:
        try:
            ecs = boto3.client("ecs", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
            ecs.stop_task(
                cluster=os.environ["CLUSTER_ARN"],
                task=task_arn,
                reason="User ended session",
            )
        except Exception as e:
            logger.warning("ECS stop_task failed for %s: %s", task_arn, e)

    update_status(req.session_id, "ended")
    return {"status": "stopped"}
```

- [ ] **Step C4.4: Run — PASS** (4 tests)

- [ ] **Step C4.5: Commit**

```bash
git add app.py tests/unit/test_apply_endpoints.py
git commit -m "feat(apply): implement POST /api/apply/stop-session"
```

---

### Task C5: `POST /api/apply/record` (idempotent)

**Files:**
- Modify: `app.py` (add new endpoint after `apply_stop_session`)
- Test: `tests/unit/test_apply_endpoints.py` (append)

- [ ] **Step C5.1: Add failing tests**

```python
# Append to tests/unit/test_apply_endpoints.py

# ---- Record Application (idempotent) ----

def test_record_404_when_job_missing(client):
    c, _ = client
    with patch("shared.load_job.load_job", return_value=None):
        r = c.post("/api/apply/record", json={"session_id": "s1", "job_id": "j-gone"})
    assert r.status_code == 404


def test_record_inserts_application_with_cloud_browser_method(client):
    c, db = client
    apps = MagicMock()
    # idempotency check — no existing row
    chain = apps.select.return_value.eq.return_value.eq.return_value.not_.in_.return_value
    chain.execute.return_value = MagicMock(data=[])
    # insert path
    apps.insert.return_value = apps
    apps.execute.return_value = MagicMock(data=[{"id": "app-NEW"}])
    jobs = MagicMock()
    jobs.update.return_value = jobs; jobs.eq.return_value = jobs
    jobs.execute.return_value = MagicMock()
    timeline = MagicMock()
    timeline.insert.return_value = timeline
    timeline.execute.return_value = MagicMock(data=[{"id": "t-1"}])

    def router(name):
        return {"applications": apps, "jobs": jobs, "application_timeline": timeline}.get(name, MagicMock())
    db.client.table.side_effect = router

    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.post("/api/apply/record", json={
            "session_id": "sess-1", "job_id": "j1",
            "confirmation_screenshot_key": "c.png",
            "form_fields_detected": 12, "form_fields_filled": 11,
        })
    assert r.status_code == 200
    assert r.json() == {"status": "recorded", "application_id": "app-NEW", "idempotent": False}
    inserted = apps.insert.call_args.args[0]
    assert inserted["submission_method"] == "cloud_browser"
    assert inserted["browser_session_id"] == "sess-1"
    assert inserted["form_fields_detected"] == 12
    assert inserted["form_fields_filled"] == 11


def test_record_is_idempotent_on_duplicate(client):
    """If canonical_hash already has an active applications row, return
    existing id instead of double-inserting."""
    c, db = client
    apps = MagicMock()
    chain = apps.select.return_value.eq.return_value.eq.return_value.not_.in_.return_value
    chain.execute.return_value = MagicMock(data=[{"id": "app-EXISTING", "status": "submitted"}])
    apps.insert.return_value = apps
    apps.execute.side_effect = AssertionError("insert must NOT be called on duplicate")

    db.client.table.side_effect = lambda name: apps if name == "applications" else MagicMock()

    with patch("shared.load_job.load_job", return_value=_job_row()):
        r = c.post("/api/apply/record", json={"session_id": "sess-1", "job_id": "j1"})
    assert r.status_code == 200
    assert r.json() == {"status": "recorded", "application_id": "app-EXISTING", "idempotent": True}
```

- [ ] **Step C5.2: Run — FAIL**

- [ ] **Step C5.3: Add the `record` endpoint in `app.py`**

Place immediately after the `apply_stop_session` function:

```python
class RecordApplicationRequest(BaseModel):
    session_id: str
    job_id: str
    confirmation_screenshot_key: Optional[str] = None
    form_fields_detected: int = 0
    form_fields_filled: int = 0


@app.post("/api/apply/record")
def apply_record(
    req: RecordApplicationRequest,
    user: AuthUser = Depends(get_current_user),
):
    """Record a successful cloud-browser submission. Idempotent: if an
    active application for the same canonical_hash already exists, return
    the existing row instead of inserting."""
    from datetime import datetime, timezone
    from shared.load_job import load_job

    if not _db:
        raise HTTPException(503, "Database not configured")

    job = load_job(req.job_id, user.id, db=_db)
    if not job:
        raise HTTPException(404, "Job not found")

    canonical = job.get("canonical_hash") or ""

    existing = (
        _db.client.table("applications")
        .select("id, status")
        .eq("user_id", user.id)
        .eq("canonical_hash", canonical)
        .not_.in_("status", ["unknown", "failed"])
        .execute()
    )
    if existing.data:
        return {
            "status": "recorded",
            "application_id": existing.data[0]["id"],
            "idempotent": True,
        }

    app_row = {
        "user_id": user.id,
        "job_id": req.job_id,
        "job_hash": job.get("job_hash", ""),
        "canonical_hash": canonical or None,
        "submission_method": "cloud_browser",
        "platform": job.get("apply_platform", "unknown"),
        "posting_id": job.get("apply_posting_id"),
        "board_token": job.get("apply_board_token"),
        "resume_s3_key": job.get("resume_s3_key", ""),
        "resume_version": job.get("resume_version", 1),
        "status": "submitted",
        "browser_session_id": req.session_id,
        "confirmation_screenshot_s3_key": req.confirmation_screenshot_key,
        "form_fields_detected": req.form_fields_detected,
        "form_fields_filled": req.form_fields_filled,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": False,
    }
    result = _db.client.table("applications").insert(app_row).execute()
    application_id = (result.data or [{}])[0].get("id")

    if canonical:
        _db.client.table("jobs").update(
            {"application_status": "Applied"},
        ).eq("user_id", user.id).eq("canonical_hash", canonical).execute()

    _db.client.table("application_timeline").insert({
        "user_id": user.id,
        "job_id": req.job_id,
        "status": "Applied",
        "notes": f"Cloud browser via {job.get('apply_platform', 'unknown')}",
    }).execute()

    return {"status": "recorded", "application_id": application_id, "idempotent": False}
```

- [ ] **Step C5.4: Run — PASS** (3 tests)

- [ ] **Step C5.5: Commit**

```bash
git add app.py tests/unit/test_apply_endpoints.py
git commit -m "feat(apply): idempotent POST /api/apply/record endpoint"
```

---

## Phase D — CI wiring

### Task D1: Pass CapSolverApiKey + BrowserSubnetIds to `sam deploy`

**Files:**
- Modify: `.github/workflows/deploy.yml`

Prereqs (user-owned, one-time):

```bash
gh secret list --repo UT07/daily-job-hunt | grep -E "CAPSOLVER|BROWSER"
# If BROWSER_SUBNET_IDS missing:
gh secret set BROWSER_SUBNET_IDS --repo UT07/daily-job-hunt \
  --body "subnet-0dbb2076340038cd4,subnet-0e971c681a2e05070,subnet-033ecb7e7ba78197a"
```

- [ ] **Step D1.1: Edit `.github/workflows/deploy.yml`**

In the `SAM Deploy` step, extend `env:` with:

```yaml
          CAPSOLVER_KEY: ${{ secrets.CAPSOLVER_API_KEY }}
          BROWSER_SUBNETS: ${{ secrets.BROWSER_SUBNET_IDS }}
```

And extend `--parameter-overrides` with:

```yaml
              "CapSolverApiKey=${CAPSOLVER_KEY}" \
              "BrowserSubnetIds=${BROWSER_SUBNETS}"
```

- [ ] **Step D1.2: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "fix(ci): pass CapSolverApiKey + BrowserSubnetIds to sam deploy"
```

- [ ] **Step D1.3: Post-merge verification (user action)**

```bash
gh workflow run deploy.yml --ref main --repo UT07/daily-job-hunt
gh run watch --repo UT07/daily-job-hunt
aws cloudformation describe-stacks --stack-name job-hunt-api \
  --query "Stacks[0].Parameters[?ParameterKey=='CapSolverApiKey' || ParameterKey=='BrowserSubnetIds']" \
  --region eu-west-1 --output table
```

---

## Phase E — Async screenshot loop

### Task E1: `asyncio.to_thread` for PostToConnection

**Files:**
- Modify: `browser/browser_session.py` — `_screenshot_loop` function

- [ ] **Step E1.1: Locate the sync call**

```bash
grep -n "post_to_connection\|_screenshot_loop\|TODO.*plan.3\|TODO.*plan-3" browser/browser_session.py
```

- [ ] **Step E1.2: Replace the sync call inside `_screenshot_loop`**

Change:

```python
apigwmgmt.post_to_connection(ConnectionId=frontend_conn, Data=jpeg_bytes)
```

to:

```python
await asyncio.to_thread(
    apigwmgmt.post_to_connection,
    ConnectionId=frontend_conn,
    Data=jpeg_bytes,
)
```

Delete any `TODO(plan-3)` comment in the same block.

- [ ] **Step E1.3: Verification**

Manual (not automated): after next deploy, click→screenshot latency should drop under 80ms. Smoke gates:

```bash
ruff check browser/
```

- [ ] **Step E1.4: Commit**

```bash
git add browser/browser_session.py
git commit -m "perf(browser): run PostToConnection off the event loop with asyncio.to_thread"
```

---

## Phase F — Integration + PR

### Task F1: Happy-path contract test + Plan 3b stub + PR

**Files:**
- Create: `tests/contract/test_apply_happy_path.py`
- Create: `docs/superpowers/plans/2026-04-24-auto-apply-plan3b-preview-ai.md`

- [ ] **Step F1.1: Write the contract test**

```python
# tests/contract/test_apply_happy_path.py
"""Contract test: start-session → ws_connect (frontend) → ws_connect (browser)
→ ws_route → record → stop-session. All AWS mocked."""
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _env():
    with patch.dict(os.environ, {
        "SUPABASE_JWT_SECRET": "test-secret",
        "SESSIONS_TABLE": "test-sessions",
        "CLUSTER_ARN": "arn:aws:ecs:eu-west-1:1:cluster/c",
        "TASK_DEF": "td",
        "SECURITY_GROUP": "sg-1",
        "BROWSER_SUBNET_IDS": "subnet-a,subnet-b",
        "BROWSER_WS_URL": "wss://ws.example/prod",
        "WEBSOCKET_API_ID": "ws-abc",
        "AWS_REGION": "eu-west-1",
    }):
        yield


def test_end_to_end_apply_flow():
    import app as app_module
    from auth import AuthUser, get_current_user
    from lambdas.browser import ws_connect, ws_route

    db = MagicMock()
    db.get_user.return_value = {
        "id": "user-1", "first_name": "U", "last_name": "S", "email": "u@e.com",
        "phone": "+353851234567", "linkedin": "https://linkedin.com/in/u",
        "visa_status": "stamp1g", "work_authorizations": {"IE": "ok"},
        "default_referral_source": "LinkedIn", "notice_period_text": "2w",
    }
    apps_chain = db.client.table.return_value.select.return_value.eq.return_value.eq.return_value.not_.in_.return_value
    apps_chain.execute.return_value = MagicMock(data=[])
    db.client.table.return_value.insert.return_value.execute.return_value = MagicMock(
        data=[{"id": "app-NEW"}],
    )

    app_module._db = db
    app_module.app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id="user-1", email="u@e.com",
    )

    sessions_store: dict = {}
    posted: list = []

    def fake_create_session(**kw):
        row = {**kw, "status": "starting"}
        sessions_store[kw["session_id"]] = row
        return row

    def fake_get_session(sid): return sessions_store.get(sid)
    def fake_set_conn(sid, *, role, connection_id): sessions_store[sid][f"ws_connection_{role}"] = connection_id
    def fake_update_status(sid, status): sessions_store[sid]["status"] = status
    def fake_find_active(uid):
        for r in sessions_store.values():
            if r.get("user_id") == uid and r.get("status") in ("starting", "ready"):
                return r
        return None
    def fake_post(**kw): posted.append(kw)
    def fake_find_by_conn(cid):
        for r in sessions_store.values():
            if r.get("ws_connection_frontend") == cid:
                return r, "frontend"
            if r.get("ws_connection_browser") == cid:
                return r, "browser"
        return None

    ecs = MagicMock()
    ecs.run_task.return_value = {"tasks": [{"taskArn": "arn:task:abc"}], "failures": []}

    with patch("shared.browser_sessions.create_session", side_effect=fake_create_session), \
         patch("shared.browser_sessions.get_session", side_effect=fake_get_session), \
         patch("shared.browser_sessions.set_connection_id", side_effect=fake_set_conn), \
         patch("shared.browser_sessions.find_active_session_for_user", side_effect=fake_find_active), \
         patch("shared.browser_sessions.update_status", side_effect=fake_update_status), \
         patch("shared.browser_sessions.post_to_connection", side_effect=fake_post), \
         patch("shared.browser_sessions.find_session_by_connection", side_effect=fake_find_by_conn), \
         patch("boto3.client", return_value=ecs), \
         patch("shared.load_job.load_job", return_value={
             "job_id": "j1", "user_id": "user-1", "title": "Backend",
             "apply_platform": "greenhouse",
             "apply_url": "https://boards.greenhouse.io/x/y",
             "apply_board_token": "x", "apply_posting_id": "12345",
             "canonical_hash": "h-abc", "resume_s3_key": "u/r.pdf",
             "resume_version": 1, "job_hash": "jh-1",
         }):

        client = TestClient(app_module.app)

        # 1. start-session
        r = client.post("/api/apply/start-session", json={"job_id": "j1"})
        assert r.status_code == 200, r.text
        body = r.json()
        session_id = body["session_id"]
        frontend_token = body["ws_token"]

        # Derive browser token as Fargate would (in prod, it's in WS_TOKEN env)
        from shared.ws_auth import issue_ws_token
        browser_token = issue_ws_token(user_id="user-1", session_id=session_id, role="browser")

        # 2. ws_connect (frontend)
        fe_event = {
            "headers": {"Authorization": f"Bearer {frontend_token}"},
            "queryStringParameters": {"session": session_id, "role": "frontend"},
            "requestContext": {"connectionId": "conn-fe"},
        }
        assert ws_connect.handler(fe_event, None)["statusCode"] == 200

        # 3. ws_connect (browser)
        br_event = {
            "headers": {"Authorization": f"Bearer {browser_token}"},
            "queryStringParameters": {"session": session_id, "role": "browser"},
            "requestContext": {"connectionId": "conn-br"},
        }
        assert ws_connect.handler(br_event, None)["statusCode"] == 200

        # 4. ws_route frontend → browser
        route_event = {
            "requestContext": {"connectionId": "conn-fe"},
            "body": json.dumps({"action": "click", "x": 10, "y": 20}),
        }
        assert ws_route.handler(route_event, None)["statusCode"] == 200
        assert any(p["connection_id"] == "conn-br" for p in posted)

        # 5. record
        r = client.post("/api/apply/record", json={
            "session_id": session_id, "job_id": "j1",
            "confirmation_screenshot_key": "c.png",
            "form_fields_detected": 5, "form_fields_filled": 5,
        })
        assert r.status_code == 200
        assert r.json()["application_id"] == "app-NEW"
        assert r.json()["idempotent"] is False

        # 6. stop-session
        r = client.post("/api/apply/stop-session", json={"session_id": session_id})
        assert r.status_code == 200
        assert sessions_store[session_id]["status"] == "ended"

    app_module.app.dependency_overrides.clear()
```

- [ ] **Step F1.2: Run — PASS**

```bash
pytest tests/contract/test_apply_happy_path.py -v
```

- [ ] **Step F1.3: Full suite + lint gate**

```bash
pytest tests/unit/ tests/contract/ -q
ruff check lambdas/ tests/ app.py browser/ shared/
```

- [ ] **Step F1.4: Create Plan 3b stub** (see next section)

- [ ] **Step F1.5: Commit tests + stub**

```bash
git add tests/contract/test_apply_happy_path.py docs/superpowers/plans/2026-04-24-auto-apply-plan3b-preview-ai.md
git commit -m "test(apply): e2e happy-path contract test + add Plan 3b stub"
```

- [ ] **Step F1.6: Push + open PR**

```bash
git push -u origin claude/nostalgic-rhodes-f58e72
gh pr create --repo UT07/daily-job-hunt --base main \
  --title "feat: auto-apply Plan 3a — WebSocket Lambdas + backend endpoints" \
  --body "$(cat <<'EOF'
## Summary
- 3 WebSocket Lambdas (ws_connect, ws_disconnect, ws_route) — query-param session/role + header-Bearer auth + split-audience JWTs (ws.frontend / ws.browser) + DDB session relay
- 4 replaced FastAPI stubs (eligibility, preview[minimal], start-session, stop-session) + 1 new (record, idempotent)
- Deleted legacy /api/apply/submit/{job_id} stub (belonged to abandoned API-POST spec)
- deploy.yml: CapSolverApiKey + BrowserSubnetIds now passed to sam deploy
- Perf: _screenshot_loop boto3 call moved to asyncio.to_thread

## Out of scope (Plan 3b)
Full AI answer generation in /api/apply/preview. Endpoint ships with `answers_generated=false`. Plan 3b at docs/superpowers/plans/2026-04-24-auto-apply-plan3b-preview-ai.md adds the AI layer without changing the response shape.

## Security note
Two-audience JWT (ws.frontend vs ws.browser) prevents a stolen frontend token from being replayed as role=browser and intercepting PII-bearing fill_all messages during the Fargate boot window.

## Test plan
- [ ] `pytest tests/unit/ tests/contract/ -q` — green
- [ ] `ruff check lambdas/ tests/ app.py browser/ shared/` — clean
- [ ] Post-merge: trigger deploy.yml; verify CFN params show non-empty CapSolver + Subnets
- [ ] Post-merge: manual websocat + curl smoke test against wss URL

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Plan 3b stub file contents

Create at `docs/superpowers/plans/2026-04-24-auto-apply-plan3b-preview-ai.md`:

````markdown
# Auto-Apply Plan 3b — AI-Powered Preview Implementation Plan

> Execute AFTER Plan 3a merges. This file is a scoping stub; per-task TDD
> steps will be written at execution time.

**Goal:** Replace the minimal `GET /api/apply/preview/{job_id}` from Plan
3a with a full AI version per design spec
`docs/superpowers/specs/2026-04-11-auto-apply-mode-1-design.md` §7.3.

**Contract preservation:** Response shape from Plan 3a stays identical;
`answers_generated` flips to `true` and `questions` / `answers` arrays get
populated. Frontend code written against 3a keeps working.

## Expected tasks

1. **Platform metadata fetchers**
   - `shared/platform_metadata/greenhouse.py` — `GET boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}?questions=true`
   - `shared/platform_metadata/ashby.py` — `GET api.ashbyhq.com/posting-api/job-posting/{uuid}`
   - 404 handling: mark `jobs.is_expired=true`, surface `reason=job_no_longer_available`
   - `follow_redirects=False` to prevent URL spoofing

2. **Question classifier** — `shared/question_classifier.py`
   Regex into `custom | eeo | confirmation | marketing | referral` per §7.3 step 5.

3. **AI answer generator** — `shared/answer_generator.py`
   Wrap `ai_complete_cached` from `ai_client.py`; per-category branching:
   - `confirmation`: skip AI, return `ai_answer=False, requires_user_action=True`
   - `eeo`: skip AI, set "Decline to self-identify" or equivalent
   - `marketing`: skip AI, set False
   - `referral`: match `user.default_referral_source` to closest option
   - `custom`: AI with 7-day cache, 0.3 temp, 300 max_tokens, qwen/nvidia/groq
   - Post-process: fuzzy-match dropdown options; safer default for yes/no

4. **Cover letter loader** — `shared/cover_letter_loader.py`
   - Try `users/{uid}/cover_letters/{job_hash}.tex` from S3
   - Run through `tex_to_plaintext()`
   - Fall back to config default CL template
   - `max_length`: platform metadata value or per-platform default (GH 10000, Ashby 5000)
   - `include_by_default`: platform `cover_letter_required` OR tier-based (S/A on, B/C off)

5. **Preview cache** — extend `ai_cache.db`
   - Key: `apply_preview:{job_id}:{resume_version}`
   - TTL: 10 min
   - Cache hit returns payload with `cache_hit=true`

6. **Swap preview endpoint** — replace Plan 3a's minimal body with:
   eligibility re-check → cache check → fetch metadata → classify questions → load resume meta → load cover letter → generate answers → build response → write cache → return

## Dependencies

- Plan 3a must be merged (endpoint + response shape exist)
- Existing `ai_client.py` (AI council) used without modification
- Existing `resume_versions` table (3.3) for resume metadata

## Reference

- Design spec: `docs/superpowers/specs/2026-04-11-auto-apply-mode-1-design.md` §7.3
- Master plan context: `docs/superpowers/specs/2026-04-03-unified-grand-plan.md` Stage 3.4 Apply
````

---

## Self-review

**Spec coverage (Plan 3a scope):**
- [x] Design §7.1 / §7.2 WS message types — ws_route is a transparent byte relay; semantics handled Fargate-side
- [x] Design §7.3 screenshot Management API — Phase E async fix
- [x] Design §7.4 connection lifecycle — B1 + B2 + B3 + C3
- [x] Design §9.1 eligibility unchanged — C1
- [x] Design §9.1 preview unchanged shape — C2 (minimal; 3b fills AI)
- [x] Design §9.2 start-session — C3
- [x] Design §9.3 record — C5 with idempotency added
- [x] Design §9.4 stop-session — C4
- [x] next_session_apr24 — CapSolverApiKey + BrowserSubnetIds → deploy.yml — D1
- [x] next_session_apr24 — screenshot_loop async boto3 — E1

**Protocol alignment with Plan 2 (was broken in v1 of this plan, fixed here):**
- [x] `?session=` query param (matches browser_session.py:410) — not `x-session-id` header
- [x] `?role=` query param with values `browser` / `frontend` — not `x-role` header, not `fargate`
- [x] `Authorization: Bearer` header (matches PR #7)
- [x] DDB slot names: `ws_connection_frontend`, `ws_connection_browser` (matches spec §4)

**Security hardening added in this revision:**
- [x] Two-audience JWTs (`ws.frontend` / `ws.browser`) prevent role takeover
- [x] Record endpoint idempotency — duplicate retries return existing id not a 500

**Deferred to Plan 3b:** AI answer generation, platform metadata fetch, question classifier, cover letter loader, preview cache.

**Placeholder scan:** No TBD / TODO / "similar to" / "handle edge cases" in implementation tasks.

**Type consistency verified:** `create_session` / `set_connection_id` / `clear_connection_id` / `find_session_by_connection` / `issue_ws_token` / `verify_ws_token` / `post_to_connection` — all callers use matching kwargs. JWT audiences `ws.frontend` / `ws.browser` consistent across issuer, verifier, and tests.

---

## Execution

**Plan complete and saved to `docs/superpowers/plans/2026-04-24-auto-apply-plan3a-websocket-backend.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks, fast iteration.

**2. Inline Execution** — execute in this session using superpowers:executing-plans with checkpoints.

**Which approach?**
