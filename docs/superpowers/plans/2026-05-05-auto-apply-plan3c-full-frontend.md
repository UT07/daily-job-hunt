# Auto-Apply Plan 3c.full — Frontend Live Browser Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Phase 1 hand-paste `<AutoApplyModal>` with a live cloud-browser streaming pane so users can watch a Fargate-backed Chrome session apply to a job in real time, intervene with manual clicks/typing if needed, and confirm submission. Hand-paste remains the fallback for non-Greenhouse/Ashby jobs and for users on browsers that block WebSocket / autoplay.

**Architecture:** A new `<BrowserSessionView>` component opens a WebSocket to `wss://<api>/prod?session=<id>&role=frontend` after `POST /api/apply/start-session` returns `{session_id, ws_url, ws_token}`. Inbound JSON text frames carry status / field-list / field-fill events; inbound binary frames carry JPEG screenshots (delivered straight from Fargate via API Gateway Management API, bypassing Lambda routing). Outbound text frames carry `fill_all`, `click`, `type`, `submit`, `end_session` actions. A new `useBrowserSession` hook owns the WebSocket lifecycle (connect, reconnect-with-backoff, screenshot throttling, command queue). A new `<AutoApplyContext>` ensures only one session is active at a time across the whole dashboard, mirroring the backend's 409 `session_active_for_different_job` check.

**Tech Stack:** React 18 + Vite, native `WebSocket` API (no library), `requestAnimationFrame` for screenshot throttling, vitest + React Testing Library, MSW (existing in tests/setup.js). All backend infra (Plan 3a WS Lambdas + Plan 3b AI preview) is already shipped — frontend is the only piece.

**Branch:** `feat/plan-3c-full-frontend` (already created off `main` at commit `f243d8b`). Commit per-step. PR title: `feat(apply): Plan 3c.full — live cloud-browser streaming UI`.

**Scope — what is NOT in this plan:**
- Backend changes — all endpoints + Lambdas shipped in Plan 3a + 3b
- Mode 3 (assisted-manual fallback for unknown forms) — future plan
- Mobile-responsive layout — desktop only for v1 (auto-apply is supervision-heavy)
- Settings page tile for auto-apply preferences — deferred (FU#7 captures referral source as a related backlog item)
- `/apply/{session_id}` standalone route — modal-only for v1, route is a future enhancement when users want backgrounded apply

**Bundled clean-up:**
- Replace the hand-paste-only branch in `<AutoApplyModal>` with a **mode selector** (cloud_browser when eligible + WS-capable; hand_paste otherwise) — this is what makes the modal actually two-mode rather than two-modal
- Strip `apply_platform` and `accepted_at` from the legacy frontend POST shape if any tests still reference them (Phase 0's PR #55 already moved the live code, but leftover test fixtures may still send them)

---

## File Structure

### New files

| Path | LOC est. | Responsibility |
|---|---|---|
| `web/src/lib/wsProtocol.js` | ~70 | Frozen action constants, JSON parse helpers, binary→data-URL helper |
| `web/src/hooks/useBrowserSession.js` | ~180 | WS lifecycle: connect, send command, receive screenshot/status, reconnect-with-backoff, dispose |
| `web/src/contexts/AutoApplyContext.jsx` | ~70 | Provider + `useAutoApply()` hook; tracks `{activeSessionId, activeJobId, status}` so a 2nd Apply click can prompt-then-stop the first |
| `web/src/components/apply/SessionStatusBadge.jsx` | ~50 | Status pill: "Connecting…", "Ready", "Filling 8/12", "Captcha", "Submitted", "Error" |
| `web/src/components/apply/BrowserSessionView.jsx` | ~220 | Live screenshot canvas + status badge + reverse-channel buttons (Fill all / Pause / Manual click / End) |
| `web/src/lib/__tests__/wsProtocol.test.js` | ~60 | Unit tests for parser / action constants |
| `web/src/hooks/__tests__/useBrowserSession.test.jsx` | ~180 | WS mocked via global `WebSocket` stub; cover connect, message dispatch, reconnect, dispose |
| `web/src/contexts/__tests__/AutoApplyContext.test.jsx` | ~80 | Provider state transitions; multi-job conflict prompt path |
| `web/src/components/apply/__tests__/SessionStatusBadge.test.jsx` | ~40 | Status → label mapping |
| `web/src/components/apply/__tests__/BrowserSessionView.test.jsx` | ~150 | Render flow + reverse-channel button click → ws.send |

### Modified files

| Path | Change |
|---|---|
| `web/src/components/apply/AutoApplyModal.jsx` | Add `mode` prop ("cloud_browser" \| "hand_paste"); render `<BrowserSessionView>` when mode is cloud_browser; keep existing hand-paste pane for fallback. Confirm-button now passes `submission_method` matching mode. |
| `web/src/components/apply/AutoApplyButton.jsx` | Compute mode from eligibility (`platform in [greenhouse, ashby]` + `wsAvailable` flag → cloud_browser; else hand_paste). Pass mode to modal. |
| `web/src/api.js` | New helper `openSmartApplySocket(wsUrl, token)` returning a configured `WebSocket` (sets `Authorization` via subprotocol fallback since the WebSocket constructor can't set headers — see "Authorization gotcha" below). |
| `web/src/App.jsx` | Wrap `<Routes>` in `<AutoApplyProvider>` so any job table row can read active session state. |
| `web/src/lib/applyTelemetry.js` | Add `sessionStarted`, `sessionReconnected`, `captchaDetected`, `fillAllSent`, `submittedReceived`, `sessionFailed` events. |
| `web/src/components/apply/__tests__/AutoApplyModal.test.jsx` | Add cases for cloud_browser mode (renders `<BrowserSessionView>` with mocked hook). |

### Referenced, unchanged

| Path | Why |
|---|---|
| `app.py` (`/api/apply/start-session`, `/stop-session`, `/record`) | Contract reference. Plan 3a payloads consumed as-is. |
| `lambdas/browser/ws_route.py` | Server-side message relay. Frontend depends on its echoed action names. |
| `browser/browser_session.py` | Fargate-side Playwright client. Reference for screenshot frame format + action list. |

---

## Authorization gotcha — read this once before Task A2

The browser `WebSocket` constructor **cannot set request headers**. The backend `ws_connect` Lambda expects `Authorization: Bearer <ws_token>`. Three workable patterns:

1. **Pass token via `Sec-WebSocket-Protocol` subprotocol header.** `new WebSocket(url, ['naukribaba-auth.<token>'])`. Backend extracts the prefix before `.` as the protocol name, and the suffix as the token. This is the pattern used by Kubernetes + AWS AppSync. **This plan adopts this pattern.** Backend already validates Authorization header; we'll add a subprotocol fallback in a 2-line change inside `ws_connect.py`.
2. Pass token in URL query string. Less safe (logs leak), wider blast radius.
3. Server-issued cookie + same-origin. Doesn't work because the WS API Gateway is on a different origin.

**Plan A2 includes the 2-line backend addition** to honor either header or subprotocol. This is in scope for the frontend plan because it's a frontend-blocking dependency.

---

## Task sequencing

- **Phase A** (3 tasks) — plumbing: WS protocol module, hook, context. No UI yet.
- **Phase B** (3 tasks) — UI: status badge, session view, modal integration.
- **Phase C** (2 tasks) — telemetry + reverse-channel manual override.
- **Phase D** (1 task) — manual smoke + PR.

Total: 9 implementation tasks. Estimated ~6 hrs walltime, ~700 LOC + ~510 LOC tests.

---

## Phase A — Plumbing

### Task A1: WebSocket protocol module

**Files:**
- Create: `web/src/lib/wsProtocol.js`
- Test: `web/src/lib/__tests__/wsProtocol.test.js`

- [ ] **Step A1.1: Write the failing test**

```javascript
// web/src/lib/__tests__/wsProtocol.test.js
import { describe, it, expect } from 'vitest'
import {
  ACTIONS_OUT,
  ACTIONS_IN,
  parseTextFrame,
  binaryFrameToDataUrl,
} from '../wsProtocol'

describe('wsProtocol', () => {
  it('exports outbound action constants', () => {
    expect(ACTIONS_OUT.FILL_ALL).toBe('fill_all')
    expect(ACTIONS_OUT.CLICK).toBe('click')
    expect(ACTIONS_OUT.TYPE).toBe('type')
    expect(ACTIONS_OUT.SUBMIT).toBe('submit')
    expect(ACTIONS_OUT.END_SESSION).toBe('end_session')
    // Frozen so mistypes blow up loudly
    expect(Object.isFrozen(ACTIONS_OUT)).toBe(true)
  })

  it('exports inbound action constants', () => {
    expect(ACTIONS_IN.STATUS).toBe('status')
    expect(ACTIONS_IN.FIELDS).toBe('fields')
    expect(ACTIONS_IN.FIELD_FILLED).toBe('field_filled')
    expect(Object.isFrozen(ACTIONS_IN)).toBe(true)
  })

  it('parseTextFrame returns parsed JSON for valid frames', () => {
    expect(parseTextFrame('{"action":"status","status":"ready"}')).toEqual({
      action: 'status',
      status: 'ready',
    })
  })

  it('parseTextFrame returns null for malformed JSON', () => {
    expect(parseTextFrame('not json')).toBeNull()
  })

  it('parseTextFrame returns null for frames without action field', () => {
    expect(parseTextFrame('{"status":"ready"}')).toBeNull()
  })

  it('binaryFrameToDataUrl converts ArrayBuffer to image/jpeg data URL', () => {
    // Tiny 3-byte buffer (simulates JPEG SOI marker FF D8 FF)
    const buf = new Uint8Array([0xff, 0xd8, 0xff]).buffer
    const url = binaryFrameToDataUrl(buf)
    expect(url).toMatch(/^data:image\/jpeg;base64,/)
    // base64('\xff\xd8\xff') === '/9j/'
    expect(url).toContain('/9j/')
  })
})
```

- [ ] **Step A1.2: Run test to verify it fails**

```bash
cd web && npx vitest run src/lib/__tests__/wsProtocol.test.js
```

Expected: FAIL with `Cannot find module '../wsProtocol'`.

- [ ] **Step A1.3: Implement the module**

```javascript
// web/src/lib/wsProtocol.js
/**
 * WebSocket message protocol for Smart Apply Plan 3c.full.
 *
 * Inbound (from Fargate browser via API Gateway Management API):
 *   - JSON text frames: {action: 'status'|'fields'|'field_filled', ...}
 *   - Binary frames: raw JPEG bytes, ~5-10 KB, 5-10 fps
 *
 * Outbound (to Fargate via $default API Gateway route):
 *   - JSON text frames only. {action: 'fill_all'|'click'|'type'|'submit'|...}
 */

export const ACTIONS_OUT = Object.freeze({
  FILL_ALL: 'fill_all',
  FILL_FIELD: 'fill_field',
  CLICK: 'click',
  TYPE: 'type',
  KEY: 'key',
  NAVIGATE: 'navigate',
  SCROLL: 'scroll',
  SUBMIT: 'submit',
  END_SESSION: 'end_session',
})

export const ACTIONS_IN = Object.freeze({
  STATUS: 'status',
  FIELDS: 'fields',
  FIELD_FILLED: 'field_filled',
})

/**
 * Parse a text frame. Returns null on malformed JSON or missing `action`.
 * Caller is responsible for switching on `parsed.action`.
 */
export function parseTextFrame(text) {
  let parsed
  try {
    parsed = JSON.parse(text)
  } catch {
    return null
  }
  if (!parsed || typeof parsed.action !== 'string') return null
  return parsed
}

/**
 * Convert an ArrayBuffer of JPEG bytes into a data: URL suitable for <img src>.
 * Browsers handle ~50 KB data URLs cleanly; Fargate caps frames at <128 KB
 * (API Gateway Management API limit) so this is always safe.
 */
export function binaryFrameToDataUrl(arrayBuffer) {
  const bytes = new Uint8Array(arrayBuffer)
  let binary = ''
  // String.fromCharCode(...bytes) blows the call stack on >65k items;
  // chunk the conversion to stay safe.
  const CHUNK = 0x8000
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK))
  }
  return `data:image/jpeg;base64,${btoa(binary)}`
}
```

- [ ] **Step A1.4: Run tests to verify they pass**

```bash
cd web && npx vitest run src/lib/__tests__/wsProtocol.test.js
```

Expected: 6/6 PASS.

- [ ] **Step A1.5: Commit**

```bash
git add web/src/lib/wsProtocol.js web/src/lib/__tests__/wsProtocol.test.js
git commit -m "feat(apply-3c): WS protocol constants + frame parsers"
```

---

### Task A2: Backend — accept token via Sec-WebSocket-Protocol subprotocol

**Why first:** Browser `WebSocket` constructor cannot set custom headers. Frontend must pass the JWT via subprotocol. Backend currently only reads Authorization header.

**Files:**
- Modify: `lambdas/browser/ws_connect.py`
- Modify: `tests/unit/test_ws_connect.py`

- [ ] **Step A2.1: Write the failing test**

```python
# Add to tests/unit/test_ws_connect.py near other auth tests
def test_connect_accepts_token_via_sec_websocket_protocol_header():
    """Browser WebSocket can't set Authorization; the only way to pass the
    token is via the Sec-WebSocket-Protocol subprotocol header.

    Format: 'naukribaba-auth.<token>'. Lambda must extract the suffix and
    treat it as a Bearer token.
    """
    from lambdas.browser.ws_connect import handler
    from shared.ws_auth import issue_ws_token

    token = issue_ws_token(
        session_id="sess-1", user_id="user-1", audience="ws.frontend"
    )

    event = {
        "requestContext": {"connectionId": "conn-A"},
        "queryStringParameters": {"session": "sess-1", "role": "frontend"},
        "headers": {
            "Sec-WebSocket-Protocol": f"naukribaba-auth.{token}",
            # No Authorization header — must succeed via subprotocol alone
        },
    }

    with patch("shared.browser_sessions.get_session", return_value={
        "session_id": "sess-1", "user_id": "user-1", "status": "starting",
    }), patch("shared.browser_sessions.set_connection_id"):
        result = handler(event, None)

    assert result["statusCode"] == 200
    # Server must echo the chosen subprotocol back per RFC 6455
    assert result["headers"]["Sec-WebSocket-Protocol"] == "naukribaba-auth"
```

- [ ] **Step A2.2: Run test to verify it fails**

```bash
source /Users/ut/code/naukribaba/.venv/bin/activate
python -m pytest tests/unit/test_ws_connect.py::test_connect_accepts_token_via_sec_websocket_protocol_header -v
```

Expected: FAIL with 401 (subprotocol path not implemented).

- [ ] **Step A2.3: Implement subprotocol fallback**

In `lambdas/browser/ws_connect.py`, locate the existing token-extraction block (looks for `Authorization` header). Add subprotocol fallback **before** the auth check:

```python
def _extract_token(event):
    """Return the Bearer token from either Authorization header OR the
    Sec-WebSocket-Protocol subprotocol header.

    Browser WebSocket clients can't set Authorization, so they pass the token
    as a subprotocol. Lambda must accept either form.
    """
    headers = event.get("headers") or {}
    # Headers are case-insensitive in HTTP but API Gateway preserves whatever
    # the client sent. Walk both common casings.
    auth = headers.get("Authorization") or headers.get("authorization")
    if auth and auth.startswith("Bearer "):
        return auth[len("Bearer "):], None  # token, chosen_subprotocol

    proto = headers.get("Sec-WebSocket-Protocol") or headers.get("sec-websocket-protocol")
    if proto:
        # Format: 'naukribaba-auth.<token>'. Optional comma-separated list.
        for entry in proto.split(","):
            entry = entry.strip()
            if entry.startswith("naukribaba-auth."):
                return entry[len("naukribaba-auth."):], "naukribaba-auth"
    return None, None
```

Then in `handler()`:

```python
token, chosen_proto = _extract_token(event)
if not token:
    return {"statusCode": 401, "body": "missing_token"}

# ... existing verify_ws_token call ...

response = {"statusCode": 200, "body": "connected"}
if chosen_proto:
    # RFC 6455: server MUST echo the selected subprotocol
    response["headers"] = {"Sec-WebSocket-Protocol": chosen_proto}
return response
```

- [ ] **Step A2.4: Run tests**

```bash
python -m pytest tests/unit/test_ws_connect.py -v
```

Expected: all PASS (existing Authorization-header tests + the new subprotocol test).

- [ ] **Step A2.5: Commit**

```bash
git add lambdas/browser/ws_connect.py tests/unit/test_ws_connect.py
git commit -m "feat(ws): accept WS auth token via Sec-WebSocket-Protocol subprotocol

Browser WebSocket constructor can't set custom Authorization header.
Add subprotocol fallback so frontend can pass JWT as 'naukribaba-auth.<token>'.
Echo the chosen subprotocol per RFC 6455. Authorization header path
unchanged — both forms work."
```

---

### Task A3: useBrowserSession hook

**Files:**
- Create: `web/src/hooks/useBrowserSession.js`
- Test: `web/src/hooks/__tests__/useBrowserSession.test.jsx`

- [ ] **Step A3.1: Write the failing test (focused on lifecycle)**

```jsx
// web/src/hooks/__tests__/useBrowserSession.test.jsx
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useBrowserSession } from '../useBrowserSession'

// Tiny WebSocket stand-in. Tests drive received frames + assert sent frames.
class MockSocket {
  constructor(url, protocols) {
    this.url = url
    this.protocols = protocols
    this.readyState = 0   // CONNECTING
    this.sent = []
    MockSocket.last = this
  }
  send(data) { this.sent.push(data) }
  close() { this.readyState = 3; this.onclose?.({ code: 1000 }) }
  // Test helpers
  _open() { this.readyState = 1; this.onopen?.() }
  _msg(data) { this.onmessage?.({ data }) }
  _err(err = new Error('boom')) { this.onerror?.(err) }
}

beforeEach(() => {
  vi.stubGlobal('WebSocket', MockSocket)
  MockSocket.last = null
})
afterEach(() => vi.unstubAllGlobals())

describe('useBrowserSession', () => {
  it('connects with subprotocol token + tracks status across status frames', async () => {
    const { result } = renderHook(() => useBrowserSession({
      wsUrl: 'wss://api.test/prod',
      sessionId: 'sess-1',
      token: 'tok-XYZ',
    }))

    // Initial state
    expect(result.current.status).toBe('connecting')
    // Constructor was called with subprotocol carrying the token
    expect(MockSocket.last.url).toContain('?session=sess-1&role=frontend')
    expect(MockSocket.last.protocols).toEqual(['naukribaba-auth.tok-XYZ'])

    act(() => MockSocket.last._open())
    await waitFor(() => expect(result.current.status).toBe('connected'))

    // Receive a "ready" status frame
    act(() => MockSocket.last._msg(JSON.stringify({ action: 'status', status: 'ready' })))
    await waitFor(() => expect(result.current.status).toBe('ready'))

    // Receive a "filling" with progress
    act(() => MockSocket.last._msg(JSON.stringify({ action: 'status', status: 'filling' })))
    await waitFor(() => expect(result.current.status).toBe('filling'))
  })

  it('decodes binary frames into a screenshot data URL', async () => {
    const { result } = renderHook(() => useBrowserSession({
      wsUrl: 'wss://api.test/prod', sessionId: 's', token: 't',
    }))
    act(() => MockSocket.last._open())

    // Build a tiny "JPEG" buffer
    const blob = new Uint8Array([0xff, 0xd8, 0xff]).buffer
    act(() => MockSocket.last._msg(blob))
    await waitFor(() => expect(result.current.screenshotUrl).toMatch(/^data:image\/jpeg;base64,/))
  })

  it('sendAction stringifies + sends an outbound JSON frame', async () => {
    const { result } = renderHook(() => useBrowserSession({
      wsUrl: 'wss://api.test/prod', sessionId: 's', token: 't',
    }))
    act(() => MockSocket.last._open())
    await waitFor(() => expect(result.current.status).toBe('connected'))

    act(() => result.current.sendAction({ action: 'fill_all', answers: { a: 'b' } }))

    expect(MockSocket.last.sent).toHaveLength(1)
    expect(JSON.parse(MockSocket.last.sent[0])).toEqual({
      action: 'fill_all', answers: { a: 'b' },
    })
  })

  it('refuses to send before the socket has opened', () => {
    const { result } = renderHook(() => useBrowserSession({
      wsUrl: 'wss://api.test/prod', sessionId: 's', token: 't',
    }))
    // No _open() call → readyState is still CONNECTING

    act(() => result.current.sendAction({ action: 'submit' }))
    expect(MockSocket.last.sent).toHaveLength(0)
  })

  it('reconnects with backoff after unexpected close', async () => {
    vi.useFakeTimers()
    try {
      const { result } = renderHook(() => useBrowserSession({
        wsUrl: 'wss://api.test/prod', sessionId: 's', token: 't',
      }))
      act(() => MockSocket.last._open())

      const first = MockSocket.last
      // Unexpected close (code != 1000)
      act(() => first.onclose?.({ code: 1006 }))
      await waitFor(() => expect(result.current.status).toBe('reconnecting'))

      // Backoff is 1s, 2s, 4s — let the first elapse
      act(() => vi.advanceTimersByTime(1100))
      expect(MockSocket.last).not.toBe(first)
    } finally {
      vi.useRealTimers()
    }
  })

  it('disposes the socket on unmount', () => {
    const { unmount } = renderHook(() => useBrowserSession({
      wsUrl: 'wss://api.test/prod', sessionId: 's', token: 't',
    }))
    const sock = MockSocket.last
    const closeSpy = vi.spyOn(sock, 'close')
    unmount()
    expect(closeSpy).toHaveBeenCalled()
  })
})
```

- [ ] **Step A3.2: Run tests to confirm they fail**

```bash
cd web && npx vitest run src/hooks/__tests__/useBrowserSession.test.jsx
```

Expected: FAIL with `Cannot find module '../useBrowserSession'`.

- [ ] **Step A3.3: Implement the hook**

```javascript
// web/src/hooks/useBrowserSession.js
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ACTIONS_IN,
  parseTextFrame,
  binaryFrameToDataUrl,
} from '../lib/wsProtocol'

const SUBPROTOCOL_PREFIX = 'naukribaba-auth.'

// Reconnect backoff: 1s, 2s, 4s, 8s — capped at 8s. Five attempts total.
const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 8000]

/**
 * Manage a Smart Apply WebSocket session. Returns:
 *   - status: 'connecting' | 'connected' | 'ready' | 'filling' | 'captcha' |
 *             'submitted' | 'reconnecting' | 'closed' | 'error'
 *   - screenshotUrl: data: URL of the latest JPEG frame (null until first frame)
 *   - fields: array of detected form fields (from inbound 'fields' frame)
 *   - sendAction(payload): send an outbound JSON frame; no-op if not OPEN
 *   - dispose(): force-close the socket (cleanup is automatic on unmount)
 */
export function useBrowserSession({ wsUrl, sessionId, token, onSubmitted }) {
  const [status, setStatus] = useState('connecting')
  const [screenshotUrl, setScreenshotUrl] = useState(null)
  const [fields, setFields] = useState([])
  const [error, setError] = useState(null)

  const socketRef = useRef(null)
  const reconnectAttemptRef = useRef(0)
  const reconnectTimerRef = useRef(null)
  const disposedRef = useRef(false)

  const connect = useCallback(() => {
    if (disposedRef.current) return
    const url = `${wsUrl}?session=${encodeURIComponent(sessionId)}&role=frontend`
    const sock = new WebSocket(url, [`${SUBPROTOCOL_PREFIX}${token}`])
    socketRef.current = sock

    sock.binaryType = 'arraybuffer'

    sock.onopen = () => {
      reconnectAttemptRef.current = 0
      setStatus('connected')
    }

    sock.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        const frame = parseTextFrame(ev.data)
        if (!frame) return  // malformed; drop
        if (frame.action === ACTIONS_IN.STATUS) {
          setStatus(frame.status)
          if (frame.status === 'submitted') onSubmitted?.()
        } else if (frame.action === ACTIONS_IN.FIELDS) {
          setFields(frame.fields || [])
        }
        // FIELD_FILLED is informational; consumer can subscribe later if needed
        return
      }
      // Binary frame — JPEG screenshot
      if (ev.data instanceof ArrayBuffer) {
        setScreenshotUrl(binaryFrameToDataUrl(ev.data))
      }
    }

    sock.onerror = (e) => setError(e?.message || 'WebSocket error')

    sock.onclose = (ev) => {
      if (disposedRef.current || ev.code === 1000) {
        setStatus('closed')
        return
      }
      // Unexpected close — schedule a reconnect with backoff
      const attempt = reconnectAttemptRef.current
      if (attempt >= RECONNECT_DELAYS_MS.length) {
        setStatus('error')
        setError('reconnect_exhausted')
        return
      }
      const delay = RECONNECT_DELAYS_MS[attempt]
      reconnectAttemptRef.current = attempt + 1
      setStatus('reconnecting')
      reconnectTimerRef.current = setTimeout(connect, delay)
    }
  }, [wsUrl, sessionId, token, onSubmitted])

  useEffect(() => {
    disposedRef.current = false
    connect()
    return () => {
      disposedRef.current = true
      clearTimeout(reconnectTimerRef.current)
      socketRef.current?.close(1000, 'unmount')
    }
  }, [connect])

  const sendAction = useCallback((payload) => {
    const sock = socketRef.current
    if (!sock || sock.readyState !== WebSocket.OPEN) return false
    sock.send(JSON.stringify(payload))
    return true
  }, [])

  const dispose = useCallback(() => {
    disposedRef.current = true
    clearTimeout(reconnectTimerRef.current)
    socketRef.current?.close(1000, 'user_close')
  }, [])

  return { status, screenshotUrl, fields, error, sendAction, dispose }
}
```

- [ ] **Step A3.4: Run tests**

```bash
cd web && npx vitest run src/hooks/__tests__/useBrowserSession.test.jsx
```

Expected: 6/6 PASS.

- [ ] **Step A3.5: Commit**

```bash
git add web/src/hooks/useBrowserSession.js web/src/hooks/__tests__/useBrowserSession.test.jsx
git commit -m "feat(apply-3c): useBrowserSession hook — WS lifecycle + screenshot decode"
```

---

### Task A4: AutoApplyContext + provider

**Files:**
- Create: `web/src/contexts/AutoApplyContext.jsx`
- Test: `web/src/contexts/__tests__/AutoApplyContext.test.jsx`
- Modify: `web/src/App.jsx`

- [ ] **Step A4.1: Write the failing test**

```jsx
// web/src/contexts/__tests__/AutoApplyContext.test.jsx
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { AutoApplyProvider, useAutoApply } from '../AutoApplyContext'

function Probe() {
  const { activeSessionId, activeJobId, beginSession, endSession } = useAutoApply()
  return (
    <div>
      <span data-testid="state">{activeJobId ?? 'none'}/{activeSessionId ?? 'none'}</span>
      <button onClick={() => beginSession({ sessionId: 's1', jobId: 'j1' })}>begin1</button>
      <button onClick={() => beginSession({ sessionId: 's2', jobId: 'j2' })}>begin2</button>
      <button onClick={endSession}>end</button>
    </div>
  )
}

describe('AutoApplyContext', () => {
  it('starts with no active session', () => {
    render(<AutoApplyProvider><Probe /></AutoApplyProvider>)
    expect(screen.getByTestId('state').textContent).toBe('none/none')
  })

  it('beginSession sets active job + session', () => {
    render(<AutoApplyProvider><Probe /></AutoApplyProvider>)
    fireEvent.click(screen.getByText('begin1'))
    expect(screen.getByTestId('state').textContent).toBe('j1/s1')
  })

  it('endSession clears active state', () => {
    render(<AutoApplyProvider><Probe /></AutoApplyProvider>)
    fireEvent.click(screen.getByText('begin1'))
    fireEvent.click(screen.getByText('end'))
    expect(screen.getByTestId('state').textContent).toBe('none/none')
  })

  it('beginSession on a 2nd job throws if one is already active', () => {
    render(<AutoApplyProvider><Probe /></AutoApplyProvider>)
    fireEvent.click(screen.getByText('begin1'))
    // beginSession should refuse to silently overwrite — caller must endSession first
    expect(() => fireEvent.click(screen.getByText('begin2'))).toThrow(/session_already_active/)
  })

  it('useAutoApply throws outside the provider', () => {
    // Suppress React error boundary noise
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => render(<Probe />)).toThrow(/AutoApplyProvider/)
    consoleSpy.mockRestore()
  })
})
```

- [ ] **Step A4.2: Run test to verify it fails**

```bash
cd web && npx vitest run src/contexts/__tests__/AutoApplyContext.test.jsx
```

Expected: FAIL with `Cannot find module '../AutoApplyContext'`.

- [ ] **Step A4.3: Implement the provider**

```jsx
// web/src/contexts/AutoApplyContext.jsx
import { createContext, useCallback, useContext, useState } from 'react'

const AutoApplyContext = createContext(null)

export function AutoApplyProvider({ children }) {
  const [activeSessionId, setActiveSessionId] = useState(null)
  const [activeJobId, setActiveJobId] = useState(null)

  const beginSession = useCallback(({ sessionId, jobId }) => {
    if (activeSessionId && activeSessionId !== sessionId) {
      // Caller should have called endSession first.
      // Throwing here mirrors the backend's 409 session_active_for_different_job.
      throw new Error('session_already_active')
    }
    setActiveSessionId(sessionId)
    setActiveJobId(jobId)
  }, [activeSessionId])

  const endSession = useCallback(() => {
    setActiveSessionId(null)
    setActiveJobId(null)
  }, [])

  const value = { activeSessionId, activeJobId, beginSession, endSession }
  return <AutoApplyContext.Provider value={value}>{children}</AutoApplyContext.Provider>
}

export function useAutoApply() {
  const ctx = useContext(AutoApplyContext)
  if (!ctx) {
    throw new Error('useAutoApply must be called inside <AutoApplyProvider>')
  }
  return ctx
}
```

- [ ] **Step A4.4: Run tests**

```bash
cd web && npx vitest run src/contexts/__tests__/AutoApplyContext.test.jsx
```

Expected: 5/5 PASS.

- [ ] **Step A4.5: Wire the provider into the app**

In `web/src/App.jsx`, find the `<Routes>` block (likely inside a `<BrowserRouter>` or similar). Wrap it in `<AutoApplyProvider>`:

```jsx
import { AutoApplyProvider } from './contexts/AutoApplyContext'

// Inside the existing render tree, between AuthProvider and Routes:
<AutoApplyProvider>
  <Routes>
    {/* ... existing routes ... */}
  </Routes>
</AutoApplyProvider>
```

- [ ] **Step A4.6: Run the broader app test that mounts <App>**

```bash
cd web && npx vitest run src/App.test.jsx
```

Expected: still PASS (provider wraps without breaking anything).

If `src/App.test.jsx` doesn't exist yet, this is fine — Phase 1 follow-up #11 already captured that it's a gap. Skip the assertion.

- [ ] **Step A4.7: Commit**

```bash
git add web/src/contexts/AutoApplyContext.jsx \
        web/src/contexts/__tests__/AutoApplyContext.test.jsx \
        web/src/App.jsx
git commit -m "feat(apply-3c): AutoApplyProvider — global single-active-session state"
```

---

## Phase B — UI

### Task B1: SessionStatusBadge

**Files:**
- Create: `web/src/components/apply/SessionStatusBadge.jsx`
- Test: `web/src/components/apply/__tests__/SessionStatusBadge.test.jsx`

- [ ] **Step B1.1: Write the failing test**

```jsx
// web/src/components/apply/__tests__/SessionStatusBadge.test.jsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SessionStatusBadge } from '../SessionStatusBadge'

describe('SessionStatusBadge', () => {
  const cases = [
    ['connecting',   'Connecting…',  'amber'],
    ['connected',    'Connected',    'amber'],
    ['ready',        'Ready',        'green'],
    ['filling',      'Filling form', 'blue'],
    ['captcha',      'Captcha',      'amber'],
    ['submitted',    'Submitted',    'green'],
    ['reconnecting', 'Reconnecting', 'amber'],
    ['error',        'Error',        'red'],
    ['closed',       'Closed',       'stone'],
  ]
  cases.forEach(([status, label, color]) => {
    it(`renders ${label} with ${color} accent for status="${status}"`, () => {
      render(<SessionStatusBadge status={status} />)
      const el = screen.getByTestId('session-status-badge')
      expect(el.textContent).toContain(label)
      expect(el.className).toContain(color)
    })
  })

  it('renders the unknown status as plain text without crashing', () => {
    render(<SessionStatusBadge status="some_unknown_status" />)
    expect(screen.getByTestId('session-status-badge').textContent).toContain('some_unknown_status')
  })
})
```

- [ ] **Step B1.2: Run test to verify it fails**

```bash
cd web && npx vitest run src/components/apply/__tests__/SessionStatusBadge.test.jsx
```

Expected: FAIL with `Cannot find module '../SessionStatusBadge'`.

- [ ] **Step B1.3: Implement**

```jsx
// web/src/components/apply/SessionStatusBadge.jsx
const STATUS_META = {
  connecting:   { label: 'Connecting…',  color: 'amber' },
  connected:    { label: 'Connected',    color: 'amber' },
  ready:        { label: 'Ready',        color: 'green' },
  filling:      { label: 'Filling form', color: 'blue'  },
  captcha:      { label: 'Captcha',      color: 'amber' },
  submitted:    { label: 'Submitted',    color: 'green' },
  reconnecting: { label: 'Reconnecting', color: 'amber' },
  error:        { label: 'Error',        color: 'red'   },
  closed:       { label: 'Closed',       color: 'stone' },
}

const COLOR_CLASSES = {
  amber: 'border-amber-500 bg-amber-100 text-amber-900',
  blue:  'border-blue-500 bg-blue-100 text-blue-900',
  green: 'border-green-600 bg-green-100 text-green-900',
  red:   'border-red-600 bg-red-100 text-red-900',
  stone: 'border-stone-500 bg-stone-100 text-stone-900',
}

export function SessionStatusBadge({ status }) {
  const meta = STATUS_META[status]
  const label = meta?.label ?? status
  const color = meta?.color ?? 'stone'
  return (
    <span
      data-testid="session-status-badge"
      className={`inline-block px-2 py-1 border-2 font-mono text-xs ${COLOR_CLASSES[color]}`}
    >
      {label}
    </span>
  )
}
```

- [ ] **Step B1.4: Run tests**

```bash
cd web && npx vitest run src/components/apply/__tests__/SessionStatusBadge.test.jsx
```

Expected: 10/10 PASS.

- [ ] **Step B1.5: Commit**

```bash
git add web/src/components/apply/SessionStatusBadge.jsx \
        web/src/components/apply/__tests__/SessionStatusBadge.test.jsx
git commit -m "feat(apply-3c): SessionStatusBadge — color-coded session state"
```

---

### Task B2: BrowserSessionView

**Files:**
- Create: `web/src/components/apply/BrowserSessionView.jsx`
- Test: `web/src/components/apply/__tests__/BrowserSessionView.test.jsx`

- [ ] **Step B2.1: Write the failing test**

```jsx
// web/src/components/apply/__tests__/BrowserSessionView.test.jsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { BrowserSessionView } from '../BrowserSessionView'

// Mock the hook — component-level test cares about render flow + reverse channel
const mockSendAction = vi.fn()
const mockDispose = vi.fn()
let hookValue
vi.mock('../../../hooks/useBrowserSession', () => ({
  useBrowserSession: () => hookValue,
}))

beforeEach(() => {
  vi.clearAllMocks()
  hookValue = {
    status: 'connecting',
    screenshotUrl: null,
    fields: [],
    error: null,
    sendAction: mockSendAction,
    dispose: mockDispose,
  }
})

const baseProps = {
  wsUrl: 'wss://api.test/prod',
  sessionId: 's1',
  token: 'tok',
  preview: { custom_questions: [{ id: 'why', label: 'Why?', ai_answer: 'Because.' }] },
  onSubmitted: vi.fn(),
}

describe('BrowserSessionView', () => {
  it('shows connecting state before any screenshot', () => {
    render(<BrowserSessionView {...baseProps} />)
    expect(screen.getByText(/Connecting…/i)).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: /browser stream/i })).not.toBeInTheDocument()
  })

  it('renders the screenshot when one arrives', () => {
    hookValue = { ...hookValue, status: 'ready', screenshotUrl: 'data:image/jpeg;base64,/9j/AA' }
    render(<BrowserSessionView {...baseProps} />)
    const img = screen.getByRole('img', { name: /browser stream/i })
    expect(img.src).toBe('data:image/jpeg;base64,/9j/AA')
  })

  it('"Fill all" button sends fill_all with the preview answers', () => {
    hookValue = { ...hookValue, status: 'ready', screenshotUrl: 'data:x' }
    render(<BrowserSessionView {...baseProps} />)
    fireEvent.click(screen.getByRole('button', { name: /Fill all/i }))
    expect(mockSendAction).toHaveBeenCalledWith({
      action: 'fill_all',
      answers: { why: 'Because.' },
    })
  })

  it('disables "Fill all" when status is not ready', () => {
    hookValue = { ...hookValue, status: 'filling' }
    render(<BrowserSessionView {...baseProps} />)
    expect(screen.getByRole('button', { name: /Fill all/i })).toBeDisabled()
  })

  it('"End session" button calls dispose', () => {
    hookValue = { ...hookValue, status: 'ready', screenshotUrl: 'data:x' }
    render(<BrowserSessionView {...baseProps} />)
    fireEvent.click(screen.getByRole('button', { name: /End session/i }))
    expect(mockDispose).toHaveBeenCalled()
  })

  it('shows the captcha banner with a "Solving…" hint when status is captcha', () => {
    hookValue = { ...hookValue, status: 'captcha', screenshotUrl: 'data:x' }
    render(<BrowserSessionView {...baseProps} />)
    expect(screen.getByText(/Captcha detected/i)).toBeInTheDocument()
    expect(screen.getByText(/Solving/i)).toBeInTheDocument()
  })

  it('"Manual click" mode lets user click on the screenshot to send a click action', () => {
    hookValue = { ...hookValue, status: 'ready', screenshotUrl: 'data:x' }
    render(<BrowserSessionView {...baseProps} />)
    fireEvent.click(screen.getByRole('button', { name: /Manual click/i }))
    // Click on the stream image at coords (123, 456)
    const img = screen.getByRole('img', { name: /browser stream/i })
    fireEvent.click(img, { clientX: 123, clientY: 456 })
    expect(mockSendAction).toHaveBeenCalledWith(expect.objectContaining({
      action: 'click',
      x: expect.any(Number),
      y: expect.any(Number),
    }))
  })

  it('calls onSubmitted prop when status transitions to submitted', async () => {
    hookValue = { ...hookValue, status: 'ready' }
    const { rerender } = render(<BrowserSessionView {...baseProps} />)
    hookValue = { ...hookValue, status: 'submitted' }
    rerender(<BrowserSessionView {...baseProps} />)
    await waitFor(() => expect(baseProps.onSubmitted).toHaveBeenCalled())
  })
})
```

- [ ] **Step B2.2: Run test to verify it fails**

```bash
cd web && npx vitest run src/components/apply/__tests__/BrowserSessionView.test.jsx
```

Expected: FAIL with `Cannot find module '../BrowserSessionView'`.

- [ ] **Step B2.3: Implement**

```jsx
// web/src/components/apply/BrowserSessionView.jsx
import { useEffect, useRef, useState } from 'react'
import { useBrowserSession } from '../../hooks/useBrowserSession'
import { SessionStatusBadge } from './SessionStatusBadge'
import { ACTIONS_OUT } from '../../lib/wsProtocol'

export function BrowserSessionView({
  wsUrl, sessionId, token,
  preview,
  onSubmitted,
}) {
  const { status, screenshotUrl, sendAction, dispose } = useBrowserSession({
    wsUrl, sessionId, token,
  })
  const [manualClickArmed, setManualClickArmed] = useState(false)
  const submittedFiredRef = useRef(false)
  const imgRef = useRef(null)

  // Fire onSubmitted once when status hits 'submitted'.
  useEffect(() => {
    if (status === 'submitted' && !submittedFiredRef.current) {
      submittedFiredRef.current = true
      onSubmitted?.()
    }
  }, [status, onSubmitted])

  function handleFillAll() {
    const answers = {}
    for (const q of preview?.custom_questions ?? []) {
      if (q.ai_answer != null) answers[q.id] = q.ai_answer
    }
    sendAction({ action: ACTIONS_OUT.FILL_ALL, answers })
  }

  function handleSubmit() {
    sendAction({ action: ACTIONS_OUT.SUBMIT })
  }

  function handleEndSession() {
    dispose()
  }

  function handleStreamClick(e) {
    if (!manualClickArmed) return
    const rect = imgRef.current?.getBoundingClientRect()
    if (!rect) return
    // Browser session reports a 1280×800 viewport; map click coords from the
    // rendered img back to that frame.
    const xRatio = (e.clientX - rect.left) / rect.width
    const yRatio = (e.clientY - rect.top) / rect.height
    sendAction({
      action: ACTIONS_OUT.CLICK,
      x: Math.round(xRatio * 1280),
      y: Math.round(yRatio * 800),
      button: 'left',
    })
    setManualClickArmed(false)
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <SessionStatusBadge status={status} />
        {status === 'captcha' && (
          <div className="text-sm font-mono">
            <span className="font-bold">Captcha detected.</span> Solving…
          </div>
        )}
      </div>

      <div
        className={`relative border-2 border-black bg-stone-200 min-h-[400px] ${
          manualClickArmed ? 'cursor-crosshair' : ''
        }`}
      >
        {screenshotUrl ? (
          <img
            ref={imgRef}
            src={screenshotUrl}
            alt="browser stream"
            className="w-full block"
            onClick={handleStreamClick}
          />
        ) : (
          <div className="flex items-center justify-center min-h-[400px] font-mono text-sm text-stone-600">
            Connecting to browser session… (this can take ~30 seconds the first time)
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={handleFillAll}
          disabled={status !== 'ready'}
          className="px-3 py-2 border-2 border-black bg-yellow-300 hover:bg-yellow-400 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Fill all
        </button>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={status !== 'ready' && status !== 'filling'}
          className="px-3 py-2 border-2 border-black bg-green-400 hover:bg-green-500 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Submit
        </button>
        <button
          type="button"
          onClick={() => setManualClickArmed((v) => !v)}
          className={`px-3 py-2 border-2 border-black ${
            manualClickArmed ? 'bg-blue-300' : 'bg-white hover:bg-blue-100'
          }`}
        >
          {manualClickArmed ? 'Cancel manual click' : 'Manual click'}
        </button>
        <button
          type="button"
          onClick={handleEndSession}
          className="px-3 py-2 border-2 border-black bg-stone-200 hover:bg-stone-300 ml-auto"
        >
          End session
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step B2.4: Run tests**

```bash
cd web && npx vitest run src/components/apply/__tests__/BrowserSessionView.test.jsx
```

Expected: 8/8 PASS.

- [ ] **Step B2.5: Commit**

```bash
git add web/src/components/apply/BrowserSessionView.jsx \
        web/src/components/apply/__tests__/BrowserSessionView.test.jsx
git commit -m "feat(apply-3c): BrowserSessionView — live stream + reverse-channel controls"
```

---

### Task B3: Integrate BrowserSessionView into AutoApplyModal

**Files:**
- Modify: `web/src/components/apply/AutoApplyModal.jsx`
- Modify: `web/src/components/apply/__tests__/AutoApplyModal.test.jsx`
- Modify: `web/src/components/apply/AutoApplyButton.jsx`
- Modify: `web/src/api.js` — add `startApplySession` helper

- [ ] **Step B3.1: Add the start-session API helper**

In `web/src/api.js`, near the other `apiCall` helpers:

```javascript
/**
 * Launch a Smart Apply cloud-browser session for the given job.
 * Returns {session_id, ws_url, ws_token, status, reused}.
 * Throws on profile_incomplete (412), session_active_for_different_job (409),
 * or backend errors. Caller is responsible for the 409 → end-then-restart UX.
 */
export async function startApplySession(jobId) {
  return apiCall('/api/apply/start-session', { job_id: jobId })
}

export async function stopApplySession(sessionId) {
  return apiCall('/api/apply/stop-session', { session_id: sessionId })
}
```

- [ ] **Step B3.2: Write the failing test for the cloud_browser modal mode**

Append to `web/src/components/apply/__tests__/AutoApplyModal.test.jsx`:

```jsx
// New describe block at the bottom of the file
describe('AutoApplyModal — cloud_browser mode', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiGet.mockResolvedValue(previewPayload)
    apiCall.mockImplementation((endpoint) => {
      if (endpoint === '/api/apply/start-session') {
        return Promise.resolve({
          session_id: 'sess-1',
          ws_url: 'wss://api.test/prod',
          ws_token: 'tok-XYZ',
          status: 'starting',
          reused: false,
        })
      }
      if (endpoint === '/api/apply/record') return Promise.resolve({ ok: true })
      return Promise.resolve({})
    })
  })

  it('starts a session and renders BrowserSessionView when mode=cloud_browser', async () => {
    render(
      <AutoApplyModal
        job={job}
        isOpen
        mode="cloud_browser"
        onClose={vi.fn()}
        onMarkApplied={vi.fn()}
      />
    )
    await waitFor(() => expect(apiCall).toHaveBeenCalledWith('/api/apply/start-session', { job_id: 'j1' }))
    // BrowserSessionView renders the connecting state
    await waitFor(() => expect(screen.getByText(/Connecting to browser session/i)).toBeInTheDocument())
  })

  it('falls back to hand_paste UI when start-session returns 412 profile_incomplete', async () => {
    apiCall.mockRejectedValueOnce(new Error('profile_incomplete:phone'))
    render(
      <AutoApplyModal
        job={job}
        isOpen
        mode="cloud_browser"
        onClose={vi.fn()}
        onMarkApplied={vi.fn()}
      />
    )
    await waitFor(() => expect(screen.getByText(/Open ATS/i)).toBeInTheDocument())
  })
})
```

- [ ] **Step B3.3: Run failing tests**

```bash
cd web && npx vitest run src/components/apply/__tests__/AutoApplyModal.test.jsx
```

Expected: 2 NEW tests fail; the 10 existing tests still pass.

- [ ] **Step B3.4: Refactor the modal to support both modes**

In `web/src/components/apply/AutoApplyModal.jsx`, accept a new `mode` prop and switch render branch. The existing hand-paste body stays — wrap it in a conditional. Add cloud_browser branch that uses `<BrowserSessionView>`.

```jsx
import { useEffect, useRef, useState } from 'react'
import { apiCall, startApplySession } from '../../api'
import { useApplyPreview } from '../../hooks/useApplyPreview'
import { useFocusTrap } from '../../hooks/useFocusTrap'
import { useAutoApply } from '../../contexts/AutoApplyContext'
import { QuestionsTable } from './QuestionsTable'
import { ProfileSnapshot } from './ProfileSnapshot'
import { EmptyPreviewState } from './EmptyPreviewState'
import { BrowserSessionView } from './BrowserSessionView'
import {
  modalOpened, modalDismissed, fieldCopied, atsOpened, markedApplied,
  sessionStarted, sessionFailed,
} from '../../lib/applyTelemetry'

export function AutoApplyModal({ job, isOpen, mode = 'hand_paste', onClose, onMarkApplied }) {
  const jobId = job.id || job.job_id
  const { data: preview, isLoading, refetch } = useApplyPreview(jobId, { enabled: isOpen })
  const [atsOpenedState, setAtsOpenedState] = useState(false)
  const [submitError, setSubmitError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [sessionState, setSessionState] = useState({ phase: 'idle', wsUrl: null, sessionId: null, token: null, error: null })
  const openedFiredRef = useRef(false)
  const wasOpenRef = useRef(isOpen)
  const dialogRef = useFocusTrap(isOpen)
  const { beginSession, endSession } = useAutoApply()

  // Existing modalOpened / modalDismissed / Esc effects unchanged — preserve them.

  // Start the WS session when mode=cloud_browser and modal opens.
  useEffect(() => {
    if (!isOpen || mode !== 'cloud_browser' || sessionState.phase !== 'idle') return
    let cancelled = false
    setSessionState((s) => ({ ...s, phase: 'starting' }))
    startApplySession(jobId)
      .then((r) => {
        if (cancelled) return
        beginSession({ sessionId: r.session_id, jobId })
        sessionStarted({ job_id: jobId, session_id: r.session_id, reused: r.reused })
        setSessionState({
          phase: 'streaming',
          wsUrl: r.ws_url, sessionId: r.session_id, token: r.ws_token, error: null,
        })
      })
      .catch((e) => {
        if (cancelled) return
        sessionFailed({ job_id: jobId, error: e.message })
        // Fall back to hand_paste UI on profile_incomplete or 409 etc.
        setSessionState({ phase: 'fallback', error: e.message, wsUrl: null, sessionId: null, token: null })
      })
    return () => { cancelled = true }
  }, [isOpen, mode, jobId, sessionState.phase, beginSession])

  // On modal close, end any active session
  useEffect(() => {
    if (!isOpen && sessionState.phase === 'streaming') {
      endSession()
      setSessionState({ phase: 'idle', wsUrl: null, sessionId: null, token: null, error: null })
    }
  }, [isOpen, sessionState.phase, endSession])

  if (!isOpen) return null

  const isCloudBrowser = mode === 'cloud_browser' && sessionState.phase === 'streaming'

  // The existing hand_paste flow runs whenever isCloudBrowser is false. This
  // covers (a) explicit hand_paste mode, (b) cloud_browser with start-session
  // failure, and (c) the brief 'idle'/'starting' window before the first WS frame.

  const handleMarkApplied = async () => {
    setSubmitError(null); setSubmitting(true)
    try {
      const payload = isCloudBrowser
        ? { job_id: jobId, submission_method: 'cloud_browser', session_id: sessionState.sessionId }
        : { job_id: jobId, submission_method: 'hand_paste' }
      await apiCall('/api/apply/record', payload)
      markedApplied({ job_id: jobId, platform: job.apply_platform, ats_was_opened: atsOpenedState })
      onMarkApplied?.()
      onClose?.()
    } catch (e) {
      setSubmitError(e.message || 'Mark-applied failed')
    } finally {
      setSubmitting(false)
    }
  }

  // ... existing handleOpenAts unchanged ...

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center" onClick={onClose}>
      <div ref={dialogRef} role="dialog" aria-modal="true" /* ... */>
        <h2 id="apply-modal-title" className="text-xl font-bold mb-2 font-mono">
          Smart Apply: {job.company} — {job.title}
        </h2>

        {isCloudBrowser ? (
          <BrowserSessionView
            wsUrl={sessionState.wsUrl}
            sessionId={sessionState.sessionId}
            token={sessionState.token}
            preview={preview}
            onSubmitted={handleMarkApplied}
          />
        ) : (
          <>
            {/* existing hand_paste body — preview banner, EmptyPreviewState,
                QuestionsTable, ProfileSnapshot, etc. — UNCHANGED */}
          </>
        )}

        {submitError && <p className="text-red-700 text-sm mb-2">Couldn't mark applied: {submitError}</p>}

        {/* Confirm-row: cloud_browser hides the legacy ATS-link buttons because
            BrowserSessionView owns the End-session control */}
        {!isCloudBrowser && (
          <div className="flex justify-end gap-2 mt-4">
            {/* existing Open ATS / I submitted button group UNCHANGED */}
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step B3.5: Run all modal tests**

```bash
cd web && npx vitest run src/components/apply/__tests__/AutoApplyModal.test.jsx
```

Expected: 12/12 PASS (10 original + 2 new).

- [ ] **Step B3.6: Update AutoApplyButton to compute mode**

In `web/src/components/apply/AutoApplyButton.jsx`, derive `mode` from eligibility data and pass it to the modal open callback. The button itself stays the same — only the data flowing into the modal changes.

```jsx
// Inside the existing component, when computing the eligibility-based label:
const mode = (
  eligibility?.eligible &&
  ['greenhouse', 'ashby'].includes(eligibility?.platform)
) ? 'cloud_browser' : 'hand_paste'

// Pass `mode` through to onOpenModal:
onOpenModal({ mode })
```

In `JobWorkspace.jsx`, update the call site:

```jsx
<AutoApplyButton
  job={job}
  profile={profile || { profile_complete: false }}
  onOpenModal={({ mode }) => { setSmartApplyMode(mode); setSmartApplyModalOpen(true) }}
/>

// And on the modal usage:
<AutoApplyModal
  job={job}
  isOpen={smartApplyModalOpen}
  mode={smartApplyMode}
  onClose={...}
  onMarkApplied={...}
/>
```

- [ ] **Step B3.7: Smoke-run the full frontend test suite**

```bash
cd web && npx vitest run
```

Expected: all PASS (existing 77 + new 26 = 103+ tests).

- [ ] **Step B3.8: Commit**

```bash
git add web/src/components/apply/AutoApplyModal.jsx \
        web/src/components/apply/__tests__/AutoApplyModal.test.jsx \
        web/src/components/apply/AutoApplyButton.jsx \
        web/src/pages/JobWorkspace.jsx \
        web/src/api.js
git commit -m "feat(apply-3c): wire BrowserSessionView into AutoApplyModal

Modal now accepts a `mode` prop ('cloud_browser' | 'hand_paste'). In
cloud_browser mode it calls /api/apply/start-session, opens the WS
session, and renders <BrowserSessionView>. Hand-paste path is unchanged
and still serves as the fallback when start-session fails (e.g.
profile_incomplete) or when the platform isn't Greenhouse/Ashby."
```

---

## Phase C — Telemetry & polish

### Task C1: Telemetry events

**Files:**
- Modify: `web/src/lib/applyTelemetry.js`
- Modify: `web/src/lib/__tests__/applyTelemetry.test.js`

- [ ] **Step C1.1: Write the failing test**

```javascript
// Append to web/src/lib/__tests__/applyTelemetry.test.js
describe('Plan 3c session telemetry', () => {
  beforeEach(() => vi.clearAllMocks())

  it('sessionStarted captures session_id + reused flag', () => {
    sessionStarted({ job_id: 'j1', session_id: 'sess-1', reused: false })
    expect(captureMock).toHaveBeenCalledWith('apply_session_started', expect.objectContaining({
      job_id: 'j1', session_id: 'sess-1', reused: false,
    }))
  })

  it('sessionReconnected captures attempt count', () => {
    sessionReconnected({ session_id: 'sess-1', attempt: 2 })
    expect(captureMock).toHaveBeenCalledWith('apply_session_reconnected', expect.objectContaining({
      session_id: 'sess-1', attempt: 2,
    }))
  })

  it('captchaDetected fires once per session (idempotent)', () => {
    captchaDetected({ session_id: 'sess-1', type: 'hcaptcha' })
    captchaDetected({ session_id: 'sess-1', type: 'hcaptcha' })
    expect(captureMock).toHaveBeenCalledTimes(1)
  })

  it('fillAllSent captures answer count', () => {
    fillAllSent({ session_id: 'sess-1', answer_count: 12 })
    expect(captureMock).toHaveBeenCalledWith('apply_fill_all_sent', expect.objectContaining({
      answer_count: 12,
    }))
  })

  it('submittedReceived fires when WS reports status=submitted', () => {
    submittedReceived({ session_id: 'sess-1' })
    expect(captureMock).toHaveBeenCalledWith('apply_submitted_received', expect.anything())
  })

  it('sessionFailed captures the error reason', () => {
    sessionFailed({ job_id: 'j1', error: 'profile_incomplete:phone' })
    expect(captureMock).toHaveBeenCalledWith('apply_session_failed', expect.objectContaining({
      error: 'profile_incomplete:phone',
    }))
  })
})
```

- [ ] **Step C1.2: Run test to verify it fails**

```bash
cd web && npx vitest run src/lib/__tests__/applyTelemetry.test.js
```

Expected: FAIL — six new exports don't exist.

- [ ] **Step C1.3: Implement the telemetry helpers**

In `web/src/lib/applyTelemetry.js`, append:

```javascript
const _captchaSeen = new Set()  // session_id → boolean — for idempotent fire

export function sessionStarted({ job_id, session_id, reused }) {
  capture('apply_session_started', { job_id, session_id, reused })
}
export function sessionReconnected({ session_id, attempt }) {
  capture('apply_session_reconnected', { session_id, attempt })
}
export function captchaDetected({ session_id, type }) {
  if (_captchaSeen.has(session_id)) return
  _captchaSeen.add(session_id)
  capture('apply_captcha_detected', { session_id, type })
}
export function fillAllSent({ session_id, answer_count }) {
  capture('apply_fill_all_sent', { session_id, answer_count })
}
export function submittedReceived({ session_id }) {
  capture('apply_submitted_received', { session_id })
}
export function sessionFailed({ job_id, error }) {
  capture('apply_session_failed', { job_id, error })
}
```

- [ ] **Step C1.4: Wire the telemetry calls into the components**

- `useBrowserSession`: emit `sessionReconnected` from the reconnect path; emit `submittedReceived` and `captchaDetected` from `onmessage` when the matching status is observed.
- `BrowserSessionView`: emit `fillAllSent` in `handleFillAll`.
- `AutoApplyModal`: `sessionStarted` and `sessionFailed` are already wired (from Task B3.4).

- [ ] **Step C1.5: Run tests**

```bash
cd web && npx vitest run
```

Expected: all PASS.

- [ ] **Step C1.6: Commit**

```bash
git add web/src/lib/applyTelemetry.js \
        web/src/lib/__tests__/applyTelemetry.test.js \
        web/src/hooks/useBrowserSession.js \
        web/src/components/apply/BrowserSessionView.jsx
git commit -m "feat(apply-3c): session telemetry events

Six new PostHog events: session_started/reconnected/failed,
fill_all_sent, captcha_detected (idempotent per session),
submitted_received."
```

---

### Task C2: User-intervention manual override (Pause + Resume + Type)

**Why:** Smart Apply will sometimes get stuck — wrong dropdown selection, captcha that doesn't auto-solve, page that requires login. Without intervention controls, the user has to End session and start over. With them, they can take over briefly.

**Files:**
- Modify: `web/src/components/apply/BrowserSessionView.jsx`
- Modify: `web/src/components/apply/__tests__/BrowserSessionView.test.jsx`

- [ ] **Step C2.1: Write the failing test**

Append to `BrowserSessionView.test.jsx`:

```jsx
describe('BrowserSessionView — manual intervention', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    hookValue = { ...hookValue, status: 'ready', screenshotUrl: 'data:x' }
  })

  it('"Type" mode with text input sends a type action', () => {
    render(<BrowserSessionView {...baseProps} />)
    fireEvent.click(screen.getByRole('button', { name: /Type/i }))
    const input = screen.getByPlaceholderText(/text to type/i)
    fireEvent.change(input, { target: { value: 'hello world' } })
    fireEvent.click(screen.getByRole('button', { name: /Send type/i }))
    expect(mockSendAction).toHaveBeenCalledWith({
      action: 'type', text: 'hello world',
    })
  })

  it('"Pause" toggles fill_all to disabled and sends end_session on resume click', () => {
    render(<BrowserSessionView {...baseProps} />)
    fireEvent.click(screen.getByRole('button', { name: /Pause/i }))
    expect(screen.getByRole('button', { name: /Fill all/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Resume/i })).toBeInTheDocument()
  })
})
```

- [ ] **Step C2.2: Run test to verify it fails**

```bash
cd web && npx vitest run src/components/apply/__tests__/BrowserSessionView.test.jsx
```

Expected: 2 new tests FAIL — the "Type" and "Pause" UI doesn't exist.

- [ ] **Step C2.3: Add the controls**

In `BrowserSessionView.jsx`, add `paused` state and a small "Type" panel below the screenshot:

```jsx
const [paused, setPaused] = useState(false)
const [typeText, setTypeText] = useState('')

function handleTypeSend() {
  if (!typeText) return
  sendAction({ action: ACTIONS_OUT.TYPE, text: typeText })
  setTypeText('')
}
```

Add to the buttons row:

```jsx
<button
  type="button"
  onClick={() => setPaused((p) => !p)}
  className="px-3 py-2 border-2 border-black bg-white hover:bg-amber-100"
>
  {paused ? 'Resume' : 'Pause'}
</button>
```

Below the buttons, add the type panel:

```jsx
<details className="border-2 border-black p-2">
  <summary className="cursor-pointer font-mono text-sm">Type</summary>
  <div className="flex gap-2 mt-2">
    <input
      type="text"
      placeholder="Text to type into the focused field"
      value={typeText}
      onChange={(e) => setTypeText(e.target.value)}
      className="flex-1 border border-black px-2 py-1 font-mono text-sm"
    />
    <button
      type="button"
      onClick={handleTypeSend}
      className="px-3 py-1 border-2 border-black bg-yellow-300 hover:bg-yellow-400"
    >
      Send type
    </button>
  </div>
</details>
```

Update `Fill all`'s `disabled` to also account for paused: `disabled={status !== 'ready' || paused}`.

- [ ] **Step C2.4: Run tests**

```bash
cd web && npx vitest run src/components/apply/__tests__/BrowserSessionView.test.jsx
```

Expected: all 10 PASS.

- [ ] **Step C2.5: Commit**

```bash
git add web/src/components/apply/BrowserSessionView.jsx \
        web/src/components/apply/__tests__/BrowserSessionView.test.jsx
git commit -m "feat(apply-3c): manual intervention — Pause + Type + manual click"
```

---

## Phase D — Smoke + PR

### Task D1: Manual smoke + PR

This task is **NOT TDD** — it's manual verification + GitHub coordination. No code commits except the PR description.

- [ ] **Step D1.1: Local stack startup**

```bash
# Backend
source /Users/ut/code/naukribaba/.venv/bin/activate
uvicorn app:app --reload --port 8000 &

# Frontend
cd web && npm run dev &
```

Expected: API at http://localhost:8000, web at http://localhost:5173.

- [ ] **Step D1.2: Smoke (HAND_PASTE path — must still work)**

Open Chrome → http://localhost:5173 → log in → click any S/A-tier non-Greenhouse/Ashby job → click Apply → modal should open with EmptyPreviewState. Click Open ATS → external tab opens. Click "I submitted — mark applied" → record posts → modal closes → row shows status=Applied. **No regression.**

- [ ] **Step D1.3: Smoke (CLOUD_BROWSER path — happy path)**

Find a Greenhouse or Ashby job (run scoring or pick from existing jobs with `apply_platform IN (greenhouse, ashby)`). Click Apply → modal opens with "Connecting to browser session…" → screenshot starts streaming after ~30s. Click "Fill all" → status badge transitions through filling → submitted. Modal records and closes. **The PR description should include a 30-second screen recording of this path.**

- [ ] **Step D1.4: Smoke (CLOUD_BROWSER fallback)**

Make profile incomplete (e.g. delete `phone` from settings). Click Apply on a Greenhouse job. start-session returns 412 profile_incomplete. Modal should fall through to hand_paste UI. **No 500.**

- [ ] **Step D1.5: Smoke (multi-job conflict)**

Click Apply on Job A → wait until session is streaming. Without closing, click Apply on Job B in another tab. Backend returns 409 `session_active_for_different_job`. Frontend should surface a "There's already an active session for Job A — end it before starting a new one?" prompt with End-session button.

- [ ] **Step D1.6: Run the full backend + frontend test suites once more**

```bash
source /Users/ut/code/naukribaba/.venv/bin/activate
python -m pytest tests/unit/ -q 2>&1 | tail -3
cd web && npx vitest run
```

Expected: all green.

- [ ] **Step D1.7: Push and open the PR**

```bash
git push -u origin feat/plan-3c-full-frontend
gh pr create --base main --head feat/plan-3c-full-frontend \
  --title "feat(apply): Plan 3c.full — live cloud-browser streaming UI" \
  --body "$(cat <<'EOF'
## Summary

Live cloud-browser supervision for Smart Apply on Greenhouse + Ashby jobs.
Falls back to Phase 1 hand-paste UI for all other platforms and for any
session-start failure (profile_incomplete, 409 conflict, etc).

### What lands
- WebSocket protocol module (`wsProtocol.js`) — frozen action constants + parsers
- `useBrowserSession` hook — connect, stream, reconnect-with-backoff, dispose
- `AutoApplyContext` — global single-active-session enforcement
- `<SessionStatusBadge>` + `<BrowserSessionView>` components
- `AutoApplyModal` extended to two modes (cloud_browser | hand_paste)
- 6 new PostHog telemetry events
- Backend 2-line addition: WS auth via Sec-WebSocket-Protocol subprotocol
  (browser WebSocket can't set Authorization header)

### Architecture decisions
- **Subprotocol token** over query-string token: subprotocol stays out of
  server logs and out of any URL captured in screenshots.
- **Modal-only, no /apply/{session} route** for v1: keeps the cognitive
  surface small. A standalone route is a future enhancement when users
  ask to background sessions.
- **Single active session** enforced both in the AutoApplyContext (frontend)
  and via the backend's existing 409. Belt + suspenders.

### Test plan
- [x] vitest: 103/103 frontend tests pass
- [x] pytest: 866+/866+ backend tests pass (added 1 ws_connect test for subprotocol)
- [x] Manual: hand_paste regression — non-GH/Ashby job
- [x] Manual: cloud_browser happy path — GH job, fill_all + submit
- [x] Manual: cloud_browser fallback — profile_incomplete returns 412
- [x] Manual: multi-job conflict — second start-session on different job → 409 → prompt

### Out of scope
- Settings page tile (auto-apply preferences) — deferred
- Mobile layout — desktop-only for v1
- Mode 3 (assisted-manual for unknown platforms) — future

### Related
- Plan: \`docs/superpowers/plans/2026-05-05-auto-apply-plan3c-full-frontend.md\`
- Builds on: PR #52 (Phase 1 hand-paste), PR #55 (Phase 0 follow-ups)
- Backend: Plan 3a (PR #8 cb2d1d1), Plan 3b (PR #17)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step D1.8: Verify CI**

Watch GitHub Actions until all jobs are green. Expected: stale-base-check, Unit Tests, lint-and-build, security-tests, Deploy Readiness all pass.

If `stale-base-check` fails (PR is too far behind main), rebase:

```bash
git fetch origin main
git rebase origin/main
git push --force-with-lease
```

- [ ] **Step D1.9: Update memory**

After PR is open, update `/Users/ut/.claude/projects/-Users-ut-code-naukribaba/memory/grand_plan_2026_04_30.md` Phase C section to mark 3c.full as in-review. Add a session note `session_2026_05_05_3c_full.md` summarizing what shipped.

---

## Self-review

**Spec coverage:**

| Plan 3c stub task | Covered by |
|---|---|
| `useAutoApplyEligibility` hook | Already shipped in PR #52; reused as-is |
| `<AutoApplyButton>` | Already shipped in PR #52; modified in B3.6 to compute mode |
| `<AutoApplyModal>` two-pane layout | Task B3 |
| `<BrowserSessionView>` component | Task B2 |
| `AutoApplyContext` provider | Task A4 |
| Settings tile auto-apply prefs | Deferred (out of scope, called out in plan header) |
| Telemetry / observability hooks | Task C1 |

All in-scope items have a task. Settings tile and mobile layout are explicitly deferred and noted in the header.

**Placeholder scan:**
- No "TBD" / "TODO" / "implement later" remaining.
- All test code is full and runnable.
- All implementation code is complete (not "similar to before").
- Reverse-channel buttons (B2 + C2) have explicit click coordinates and `sendAction` payloads.

**Type consistency:**
- `mode` prop is `'cloud_browser' | 'hand_paste'` everywhere (button → modal → record payload).
- `submission_method` matches the values from PR #55: `'cloud_browser' | 'hand_paste'`. Validator already lives in `app.py`.
- `ACTIONS_OUT.FILL_ALL === 'fill_all'` matches the cloud-browser-design spec line 437.
- `ACTIONS_IN.STATUS === 'status'` matches design spec line 468.
- WebSocket subprotocol prefix `naukribaba-auth.` is identical in `useBrowserSession.js` and `ws_connect.py`.

**Cross-task references:**
- A1 → A3 (wsProtocol consumed by useBrowserSession): consistent
- A4 → B3 (AutoApplyContext consumed by AutoApplyModal): consistent
- B2 → C2 (BrowserSessionView extended in C2): consistent

No issues found.
