# Smart Apply Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the React frontend to the apply backend (`/api/apply/eligibility`, `/api/apply/preview`, `/api/apply/record`) so the user can see eligibility per job, view AI-generated answers, copy them field-by-field, and record applications.

**Architecture:** Five new components + three new hooks + one telemetry lib + one minor backend addition (`profile_complete` field on `ProfileResponse`) + a shared eligibility-reasons enum file. Eligibility is computed client-side from row data; modal opens only on JobWorkspace; primary-button swap (Open ATS → Mark applied) gates the record-applied call.

**Tech Stack:** React 19 + Vite, Tailwind v4, Zustand (existing — not used in Phase 1), `posthog-js` (already wired in `src/lib/posthog.js`), Vitest + @testing-library/react (NEW — installed in Task 2.1), FastAPI + Pydantic (backend), pytest (backend tests).

**Branch:** `feat/smart-apply-phase1`. Open the PR as a draft after Group 1 lands; flip to ready after Group 5 smoke passes.

**Spec:** [docs/superpowers/specs/2026-05-01-smart-apply-phase1-design.md](../specs/2026-05-01-smart-apply-phase1-design.md)

---

## File Structure

### Files to create

| Path | Responsibility |
|---|---|
| `shared/eligibility_reasons.json` | Single-source-of-truth enum: 4 ineligibility reasons + `eligible`. Read by backend pytest + frontend at runtime via Vite's JSON import. |
| `web/src/components/apply/EligibilityBadge.jsx` | Read-only badge per JobTable row — green/amber/grey per reason |
| `web/src/components/apply/AutoApplyButton.jsx` | Smart-button on JobWorkspace; 5-state machine; opens modal only on `eligible` |
| `web/src/components/apply/AutoApplyModal.jsx` | Modal orchestrator — fetch preview, primary-button swap, optimistic record-applied with revert |
| `web/src/components/apply/QuestionsTable.jsx` | Per-question copy table with toast |
| `web/src/components/apply/ProfileSnapshot.jsx` | Collapsible profile-snapshot view |
| `web/src/components/apply/EmptyPreviewState.jsx` | Empty `custom_questions` retry message |
| `web/src/components/apply/__tests__/*.test.jsx` | One test file per component |
| `web/src/hooks/useUserProfile.js` | Wraps `GET /api/profile`; exposes `{profile, isLoading}` via `ProfileContext` |
| `web/src/hooks/useApplyEligibility.js` | Pure `computeEligibility(job, profile)` + thin React wrapper |
| `web/src/hooks/useApplyPreview.js` | Wraps `GET /api/apply/preview/{job_id}`; SWR-style |
| `web/src/lib/applyTelemetry.js` | 7 `posthog.capture()` wrappers |
| `web/vitest.config.js` | Vitest configuration + jsdom + setup file |
| `web/src/test/setup.js` | Test environment setup (window.matchMedia stubs etc.) |
| `tests/contract/test_apply_eligibility_reasons.py` | Pin enum parity (backend reasons == frontend reasons) |
| `tests/contract/test_apply_preview_response_shape.py` | Pin `/api/apply/preview` response keys |
| `tests/contract/test_profile_response_includes_profile_complete.py` | Pin `profile_complete` field on `ProfileResponse` |
| `tests/quality/test_answer_quality_floor.py` | Layer A — programmatic floor checks |
| `tests/quality/fixtures/golden_apply_answers.json` | Layer B — 5 fixture jobs with hand-written ideal answers |
| `tests/quality/test_answer_quality_golden.py` | Layer B — cosine-similarity comparison harness |
| `docs/qa/phase3c0-smoke.md` | Pre-merge smoke checklist |

### Files to modify

| Path | Change |
|---|---|
| `app.py:303-321` | Add `profile_complete: bool` to `ProfileResponse` |
| `app.py:1384-1413` | Update GET `/api/profile` handler to compute `profile_complete` via `check_profile_completeness` |
| `app.py:1415-1465` | Same update on PUT `/api/profile` handler (returns same shape) |
| `web/src/components/JobTable.jsx` | Render `<EligibilityBadge/>` per row |
| `web/src/pages/JobWorkspace.jsx` | Mount `<AutoApplyButton/>` in the action area |
| `web/src/pages/Dashboard.jsx` | Wrap children in `<ProfileContext.Provider>` |
| `web/src/layouts/AppLayout.jsx` | Replace local profile-completeness heuristic with `useUserProfile()` |
| `web/package.json` | Add `vitest`, `@testing-library/react`, `@testing-library/jest-dom`, `jsdom`, `@vitest/ui` to devDependencies; add `test` script |

---

## Task Group 1 — Backend prerequisites + contract tests (Session 1)

**Estimated time:** ~3 hours.
**Outcome:** Backend exposes `profile_complete: bool`, shared enum file exists, three contract tests pass. PR opens as draft.

### Task 1.0: Set up branch + open draft PR

**Files:** none

- [ ] **Step 1: Create feature branch**

```bash
git checkout main && git pull --ff-only
git checkout -b feat/smart-apply-phase1
git push -u origin feat/smart-apply-phase1
```

- [ ] **Step 2: Open draft PR**

```bash
gh pr create --draft --title "feat(apply): Phase 1 — Smart Apply hand-paste UI" --body "$(cat <<'EOF'
## Summary
Implements Phase 1 of Smart Apply per [spec](docs/superpowers/specs/2026-05-01-smart-apply-phase1-design.md).
Wires the React frontend to the existing /api/apply/* backend so users can see
eligibility per job, view AI-generated answers, copy them field-by-field, and
record applications.

Cloud-browser supervision (Phase 2) is out of scope.

## Test plan
- [ ] All 3 backend contract tests pass
- [ ] All ~24 frontend unit tests pass
- [ ] Layer A floor test passes against 5 fixture jobs
- [ ] Layer B golden-answer comparison runs (informational on first PR)
- [ ] Smoke checklist in docs/qa/phase3c0-smoke.md passes against staging

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed.

---

### Task 1.1: Create shared eligibility-reasons enum

**Files:**
- Create: `shared/eligibility_reasons.json`

- [ ] **Step 1: Write the file**

```json
{
  "comment": "Single source of truth for eligibility reasons. Backend pytest contract test reads this; frontend imports it via Vite's JSON import at hooks/useApplyEligibility.js. Order matches the precedence in app.py:2683-2734.",
  "ineligibility_reasons": [
    "already_applied",
    "no_apply_url",
    "no_resume",
    "profile_incomplete"
  ],
  "eligible_value": "eligible",
  "all_response_values": [
    "eligible",
    "already_applied",
    "no_apply_url",
    "no_resume",
    "profile_incomplete"
  ]
}
```

- [ ] **Step 2: Commit**

```bash
git add shared/eligibility_reasons.json
git commit -m "feat(apply): shared eligibility-reasons enum file"
```

---

### Task 1.2: Add `profile_complete` field to `ProfileResponse`

**Files:**
- Modify: `app.py:303-321` (ProfileResponse class)
- Modify: `app.py:1384-1465` (GET + PUT handlers)

- [ ] **Step 1: Write the failing test first**

Create `tests/contract/test_profile_response_includes_profile_complete.py`:

```python
"""Contract test: ProfileResponse must expose `profile_complete: bool` derived
from shared.profile_completeness.check_profile_completeness().

Why: Phase 1 of Smart Apply (spec §4) needs an authoritative completeness
signal on /api/profile. Without this, frontend AppLayout has its own local
heuristic (full_name && phone && location) that drifts from the backend's
9-required-fields check.
"""
from __future__ import annotations
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    app_module = importlib.import_module("app")
    return TestClient(app_module.app)


def test_profile_response_model_has_profile_complete_field():
    """The Pydantic model must declare the field — not just inject it ad hoc."""
    from app import ProfileResponse

    fields = ProfileResponse.model_fields
    assert "profile_complete" in fields, (
        "ProfileResponse must declare profile_complete: bool. "
        "Phase 1 frontend reads this — see docs/superpowers/specs/2026-05-01-smart-apply-phase1-design.md §2 backend dependency."
    )
    annotation = fields["profile_complete"].annotation
    assert annotation is bool, f"profile_complete must be bool, got {annotation}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source .venv/bin/activate
pytest tests/contract/test_profile_response_includes_profile_complete.py::test_profile_response_model_has_profile_complete_field -v
```

Expected: FAIL with `'profile_complete' not in fields`.

- [ ] **Step 3: Add the field to `ProfileResponse`**

Edit `app.py` around line 303-321. Add the field at the end of the class:

```python
class ProfileResponse(BaseModel):
    id: str
    email: str
    full_name: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    github_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    website: Optional[str] = None
    visa_status: Optional[str] = None
    work_authorizations: Optional[dict] = None
    candidate_context: Optional[str] = None
    plan: str = "free"
    created_at: Optional[str] = None
    gdpr_consent_at: Optional[str] = None
    salary_expectation_notes: str = ""
    notice_period_text: str = ""
    onboarding_completed_at: Optional[str] = None
    profile_complete: bool = False  # NEW — set by handlers via check_profile_completeness()
```

- [ ] **Step 4: Update GET handler**

Find the GET `/api/profile` handler at app.py:1384. Edit the `ProfileResponse(...)` construction to compute `profile_complete`:

```python
@app.get("/api/profile", response_model=ProfileResponse)
def get_profile(user: AuthUser = Depends(get_current_user)):
    from shared.profile_completeness import check_profile_completeness

    if not _db:
        raise HTTPException(503, "Database not configured")
    profile = _db.get_user(user.id)
    if not profile:
        raise HTTPException(404, "Profile not found")

    missing = check_profile_completeness(profile)
    return ProfileResponse(
        # ... existing fields unchanged ...
        profile_complete=not missing,
    )
```

(Apply the same `profile_complete=not missing` line to the PUT handler at app.py:1415 too — it constructs the same response.)

- [ ] **Step 5: Run the test to verify it passes**

```bash
pytest tests/contract/test_profile_response_includes_profile_complete.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/contract/test_profile_response_includes_profile_complete.py
git commit -m "feat(api): expose profile_complete on ProfileResponse

Adds an authoritative completeness signal to GET/PUT /api/profile
derived from shared.profile_completeness.check_profile_completeness().
Required by Smart Apply Phase 1 frontend (spec §2 backend dependency)
to eliminate the AppLayout local-heuristic drift."
```

---

### Task 1.3: Contract test — frontend reasons match backend

**Files:**
- Create: `tests/contract/test_apply_eligibility_reasons.py`

- [ ] **Step 1: Write the failing test**

```python
"""Contract test: every reason returned by /api/apply/eligibility must match
the enum in shared/eligibility_reasons.json. The frontend reads the same
JSON file at hooks/useApplyEligibility.js — pinning here means a backend
reason added without updating the JSON breaks CI before it reaches users.

Why: Smart Apply Phase 1 spec §6.2 requires this pinning; same drift class
as PR #44's AddJob payload contract test.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
ENUM_FILE = PROJECT_ROOT / "shared" / "eligibility_reasons.json"
APP_PY = PROJECT_ROOT / "app.py"


def test_backend_reasons_match_shared_enum():
    """Scrape every `return {"eligible": False, "reason": "..."}` literal in
    the apply_eligibility endpoint and assert each is in the enum file."""
    enum = json.loads(ENUM_FILE.read_text())
    declared = set(enum["ineligibility_reasons"])

    src = APP_PY.read_text()
    # Slice from `def apply_eligibility` to the next `def` to scope the scan.
    start = src.index("def apply_eligibility")
    end = src.index("\ndef ", start + 1)
    fn_src = src[start:end]

    pattern = re.compile(r'"reason":\s*"([a-z_]+)"')
    found = set(pattern.findall(fn_src))

    extra_in_code = found - declared
    missing_in_enum = declared - found

    assert not extra_in_code, (
        f"app.py:apply_eligibility returns reason(s) not in shared/eligibility_reasons.json: {extra_in_code}. "
        f"Add them to the JSON file or remove them from the endpoint."
    )
    assert not missing_in_enum, (
        f"shared/eligibility_reasons.json declares reason(s) the backend never returns: {missing_in_enum}. "
        f"Either add the branch in app.py:apply_eligibility or remove from the JSON."
    )
```

- [ ] **Step 2: Run test to verify it fails or passes correctly**

```bash
source .venv/bin/activate
pytest tests/contract/test_apply_eligibility_reasons.py -v
```

Expected: PASS (since Task 1.1 created the JSON to match the existing 4 backend reasons).

If it fails, the diagnostic message tells you which side to fix. The test is now load-bearing for future drift.

- [ ] **Step 3: Commit**

```bash
git add tests/contract/test_apply_eligibility_reasons.py
git commit -m "test(contract): pin /api/apply/eligibility reasons to shared enum"
```

---

### Task 1.4: Contract test — `/api/apply/preview` response shape

**Files:**
- Create: `tests/contract/test_apply_preview_response_shape.py`

- [ ] **Step 1: Write the failing test**

```python
"""Contract test: /api/apply/preview response must include the exact keys
the Phase 1 modal expects.

Pinned keys: eligible, reason, profile_complete, missing_required_fields,
job, platform, platform_metadata, resume, profile, cover_letter,
custom_questions, already_applied, existing_application_id, cache_hit.

Why: Smart Apply Phase 1 spec §6.2. Same pattern as PR #44.
"""
from __future__ import annotations
from app import _build_shell_response

# These are the keys Phase 1's <AutoApplyModal> reads. If you remove any of
# these from the response, update the modal first and bump this test.
REQUIRED_KEYS = {
    "eligible",
    "reason",
    "profile_complete",
    "missing_required_fields",
    "job",
    "platform",
    "platform_metadata",
    "resume",
    "profile",
    "cover_letter",
    "custom_questions",
    "already_applied",
    "existing_application_id",
    "cache_hit",
}


def test_shell_response_has_all_required_keys():
    """The shell response (the degraded path) is the canonical shape — any
    key the full path returns is also returned by the shell. Assert here."""
    shell = _build_shell_response("no_apply_url", missing=[])
    actual_keys = set(shell.keys())
    missing = REQUIRED_KEYS - actual_keys
    assert not missing, (
        f"_build_shell_response is missing keys the Phase 1 modal expects: {missing}. "
        f"Either add to _build_shell_response or update REQUIRED_KEYS + the modal."
    )
```

- [ ] **Step 2: Run test to verify it passes**

```bash
pytest tests/contract/test_apply_preview_response_shape.py -v
```

Expected: PASS — `_build_shell_response` already returns these keys (verified at app.py:2737-2752).

- [ ] **Step 3: Commit**

```bash
git add tests/contract/test_apply_preview_response_shape.py
git commit -m "test(contract): pin /api/apply/preview response keys"
```

---

### Task 1.5: Run full backend test suite + push

- [ ] **Step 1: Run all backend tests**

```bash
source .venv/bin/activate
pytest tests/ -x --tb=short
```

Expected: all green (~870+ tests).

- [ ] **Step 2: Push branch**

```bash
git push
```

Expected: PR auto-updates with the 4 new commits.

---

## Task Group 2 — Frontend test infrastructure + hooks (Session 2)

**Estimated time:** ~3 hours.
**Outcome:** Vitest installed, three hooks ship with green tests, telemetry lib ready.

### Task 2.1: Install Vitest + testing-library

**Files:**
- Modify: `web/package.json`
- Create: `web/vitest.config.js`
- Create: `web/src/test/setup.js`

- [ ] **Step 1: Install dev deps**

```bash
cd web
npm install --save-dev vitest @testing-library/react @testing-library/jest-dom jsdom
```

- [ ] **Step 2: Add test script to package.json**

Edit `web/package.json` — under `"scripts"`, add:

```json
"scripts": {
  "dev": "vite",
  "build": "vite build",
  "lint": "eslint .",
  "preview": "vite preview",
  "test": "vitest run",
  "test:watch": "vitest"
}
```

- [ ] **Step 3: Create vitest config**

`web/vitest.config.js`:

```js
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.js'],
    globals: true,
  },
})
```

- [ ] **Step 4: Create test setup**

`web/src/test/setup.js`:

```js
import '@testing-library/jest-dom'

// posthog-js is initialized in main.jsx with a key from import.meta.env. In
// tests we don't initialize it; lib/applyTelemetry.js must no-op when posthog
// isn't ready, so no global stub is needed here.
```

- [ ] **Step 5: Smoke test — write a trivial test**

`web/src/test/setup.test.js`:

```js
import { describe, it, expect } from 'vitest'

describe('vitest setup', () => {
  it('runs', () => {
    expect(1 + 1).toBe(2)
  })
})
```

- [ ] **Step 6: Run it**

```bash
cd web && npm test
```

Expected: 1 test passes.

- [ ] **Step 7: Delete the smoke test (not committed)**

```bash
rm web/src/test/setup.test.js
```

- [ ] **Step 8: Commit**

```bash
git add web/package.json web/package-lock.json web/vitest.config.js web/src/test/setup.js
git commit -m "chore(web): install vitest + @testing-library/react"
```

---

### Task 2.2: `useUserProfile` hook + `ProfileContext`

**Files:**
- Create: `web/src/hooks/useUserProfile.js`
- Create: `web/src/hooks/__tests__/useUserProfile.test.jsx`

- [ ] **Step 1: Write the failing test**

`web/src/hooks/__tests__/useUserProfile.test.jsx`:

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { ProfileProvider, useUserProfile } from '../useUserProfile'

vi.mock('../../api', () => ({
  apiGet: vi.fn(),
}))
import { apiGet } from '../../api'

const wrapper = ({ children }) => <ProfileProvider>{children}</ProfileProvider>

describe('useUserProfile', () => {
  beforeEach(() => vi.clearAllMocks())

  it('starts loading, then exposes profile from /api/profile', async () => {
    apiGet.mockResolvedValueOnce({
      id: 'u1', email: 'a@b.com', profile_complete: true, full_name: 'Daisy',
    })

    const { result } = renderHook(() => useUserProfile(), { wrapper })

    expect(result.current.isLoading).toBe(true)
    expect(result.current.profile).toBeNull()

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.profile.profile_complete).toBe(true)
    expect(apiGet).toHaveBeenCalledWith('/api/profile')
  })

  it('exposes profile_complete=false when backend says incomplete', async () => {
    apiGet.mockResolvedValueOnce({ id: 'u1', email: 'a@b.com', profile_complete: false })

    const { result } = renderHook(() => useUserProfile(), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.profile.profile_complete).toBe(false)
  })

  it('exposes profile=null on fetch error', async () => {
    apiGet.mockRejectedValueOnce(new Error('network down'))

    const { result } = renderHook(() => useUserProfile(), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.profile).toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd web && npm test -- useUserProfile
```

Expected: FAIL with "Cannot find module '../useUserProfile'".

- [ ] **Step 3: Implement the hook + provider**

`web/src/hooks/useUserProfile.js`:

```jsx
import { createContext, useContext, useEffect, useState } from 'react'
import { apiGet } from '../api'

const ProfileContext = createContext({ profile: null, isLoading: true })

export function ProfileProvider({ children }) {
  const [profile, setProfile] = useState(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    apiGet('/api/profile')
      .then((data) => setProfile(data))
      .catch(() => setProfile(null))
      .finally(() => setIsLoading(false))
  }, [])

  return (
    <ProfileContext.Provider value={{ profile, isLoading }}>
      {children}
    </ProfileContext.Provider>
  )
}

export function useUserProfile() {
  return useContext(ProfileContext)
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd web && npm test -- useUserProfile
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/useUserProfile.js web/src/hooks/__tests__/useUserProfile.test.jsx
git commit -m "feat(web): useUserProfile hook + ProfileContext"
```

---

### Task 2.3: `useApplyEligibility` — pure function + truth table

**Files:**
- Create: `web/src/hooks/useApplyEligibility.js`
- Create: `web/src/hooks/__tests__/useApplyEligibility.test.js`

- [ ] **Step 1: Write the failing test**

`web/src/hooks/__tests__/useApplyEligibility.test.js`:

```js
import { describe, it, expect } from 'vitest'
import { computeEligibility } from '../useApplyEligibility'
import enumFile from '../../../../shared/eligibility_reasons.json'

const completeProfile = { profile_complete: true }
const incompleteProfile = { profile_complete: false }
const validJob = {
  apply_url: 'https://boards.greenhouse.io/acme/jobs/123',
  resume_s3_key: 's3://bucket/key.pdf',
  apply_platform: 'greenhouse',
  application_status: 'scored',
}

describe('computeEligibility — order matches app.py:2683-2734', () => {
  it('eligible when all gates pass', () => {
    expect(computeEligibility(validJob, completeProfile)).toEqual({
      eligible: true,
      platform: 'greenhouse',
    })
  })

  it('already_applied wins over everything else', () => {
    const r = computeEligibility({ ...validJob, application_status: 'applied' }, incompleteProfile)
    expect(r).toEqual({ eligible: false, reason: 'already_applied' })
  })

  it('no_apply_url when apply_url missing', () => {
    const r = computeEligibility({ ...validJob, apply_url: null }, completeProfile)
    expect(r).toEqual({ eligible: false, reason: 'no_apply_url' })
  })

  it('no_apply_url when apply_url empty string', () => {
    const r = computeEligibility({ ...validJob, apply_url: '' }, completeProfile)
    expect(r).toEqual({ eligible: false, reason: 'no_apply_url' })
  })

  it('no_resume when resume_s3_key missing', () => {
    const r = computeEligibility({ ...validJob, resume_s3_key: null }, completeProfile)
    expect(r).toEqual({ eligible: false, reason: 'no_resume' })
  })

  it('profile_incomplete when profile.profile_complete=false', () => {
    expect(computeEligibility(validJob, incompleteProfile)).toEqual({
      eligible: false,
      reason: 'profile_incomplete',
    })
  })

  it('eligible:true with platform=null when apply_platform unknown (HN Hiring)', () => {
    const r = computeEligibility({ ...validJob, apply_platform: null }, completeProfile)
    expect(r).toEqual({ eligible: true, platform: null })
  })

  it('eligible:true for any apply_platform (no client-side platform gate)', () => {
    const r = computeEligibility({ ...validJob, apply_platform: 'workday' }, completeProfile)
    expect(r).toEqual({ eligible: true, platform: 'workday' })
  })

  it('returns null-safe defaults when profile is null/undefined', () => {
    const r = computeEligibility(validJob, null)
    expect(r).toEqual({ eligible: false, reason: 'profile_incomplete' })
  })

  it('every returned reason is in shared/eligibility_reasons.json', () => {
    const reasons = enumFile.ineligibility_reasons
    const all = [
      computeEligibility({ ...validJob, application_status: 'applied' }, completeProfile),
      computeEligibility({ ...validJob, apply_url: null }, completeProfile),
      computeEligibility({ ...validJob, resume_s3_key: null }, completeProfile),
      computeEligibility(validJob, incompleteProfile),
    ]
    for (const r of all) {
      expect(reasons).toContain(r.reason)
    }
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd web && npm test -- useApplyEligibility
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the hook**

`web/src/hooks/useApplyEligibility.js`:

```js
// Order matches app.py:2683-2734 exactly so frontend and backend never disagree
// on which reason fires when multiple apply.
export function computeEligibility(job, profile) {
  if (job.application_status === 'applied') {
    return { eligible: false, reason: 'already_applied' }
  }
  if (!job.apply_url) {
    return { eligible: false, reason: 'no_apply_url' }
  }
  if (!job.resume_s3_key) {
    return { eligible: false, reason: 'no_resume' }
  }
  if (!profile || !profile.profile_complete) {
    return { eligible: false, reason: 'profile_incomplete' }
  }
  return { eligible: true, platform: job.apply_platform || null }
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd web && npm test -- useApplyEligibility
```

Expected: 10 tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/useApplyEligibility.js web/src/hooks/__tests__/useApplyEligibility.test.js
git commit -m "feat(web): computeEligibility — client-side eligibility check"
```

---

### Task 2.4: `useApplyPreview` hook

**Files:**
- Create: `web/src/hooks/useApplyPreview.js`
- Create: `web/src/hooks/__tests__/useApplyPreview.test.jsx`

- [ ] **Step 1: Write the failing test**

`web/src/hooks/__tests__/useApplyPreview.test.jsx`:

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { useApplyPreview } from '../useApplyPreview'

vi.mock('../../api', () => ({ apiGet: vi.fn() }))
import { apiGet } from '../../api'

describe('useApplyPreview', () => {
  beforeEach(() => vi.clearAllMocks())

  it('starts idle until enabled, then fetches on mount', async () => {
    const payload = { eligible: true, custom_questions: [], cache_hit: false }
    apiGet.mockResolvedValueOnce(payload)

    const { result } = renderHook(() => useApplyPreview('job-1', { enabled: true }))

    expect(result.current.isLoading).toBe(true)
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.data).toEqual(payload)
    expect(apiGet).toHaveBeenCalledWith('/api/apply/preview/job-1')
  })

  it('does not fetch when enabled=false', () => {
    renderHook(() => useApplyPreview('job-1', { enabled: false }))
    expect(apiGet).not.toHaveBeenCalled()
  })

  it('exposes error on fetch failure', async () => {
    apiGet.mockRejectedValueOnce(new Error('500 server error'))

    const { result } = renderHook(() => useApplyPreview('job-1', { enabled: true }))

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.error).toBeTruthy()
    expect(result.current.data).toBeNull()
  })

  it('refetch() re-calls the endpoint', async () => {
    apiGet.mockResolvedValue({ eligible: true, custom_questions: [] })

    const { result } = renderHook(() => useApplyPreview('job-1', { enabled: true }))
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(apiGet).toHaveBeenCalledTimes(1)

    await act(async () => {
      await result.current.refetch()
    })
    expect(apiGet).toHaveBeenCalledTimes(2)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd web && npm test -- useApplyPreview
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the hook**

`web/src/hooks/useApplyPreview.js`:

```js
import { useEffect, useState, useCallback } from 'react'
import { apiGet } from '../api'

export function useApplyPreview(jobId, { enabled = true } = {}) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [isLoading, setIsLoading] = useState(enabled)

  const fetcher = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      const result = await apiGet(`/api/apply/preview/${jobId}`)
      setData(result)
    } catch (e) {
      setError(e)
      setData(null)
    } finally {
      setIsLoading(false)
    }
  }, [jobId])

  useEffect(() => {
    if (!enabled) return
    fetcher()
  }, [enabled, fetcher])

  return { data, error, isLoading, refetch: fetcher }
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd web && npm test -- useApplyPreview
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/src/hooks/useApplyPreview.js web/src/hooks/__tests__/useApplyPreview.test.jsx
git commit -m "feat(web): useApplyPreview hook"
```

---

### Task 2.5: `applyTelemetry` lib

**Files:**
- Create: `web/src/lib/applyTelemetry.js`
- Create: `web/src/lib/__tests__/applyTelemetry.test.js`

- [ ] **Step 1: Write the failing test**

`web/src/lib/__tests__/applyTelemetry.test.js`:

```js
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('posthog-js', () => ({
  default: { capture: vi.fn() },
}))
import posthog from 'posthog-js'
import * as t from '../applyTelemetry'

describe('applyTelemetry', () => {
  beforeEach(() => vi.clearAllMocks())

  it('eligibilityViewed captures with summary props', () => {
    t.eligibilityViewed({ total_jobs: 60, eligible: 12, by_reason: { profile_incomplete: 0, no_resume: 5 } })
    expect(posthog.capture).toHaveBeenCalledWith('apply_eligibility_viewed', {
      total_jobs: 60, eligible: 12, by_reason: { profile_incomplete: 0, no_resume: 5 },
    })
  })

  it('modalOpened captures job_id, platform, reason', () => {
    t.modalOpened({ job_id: 'j1', platform: 'greenhouse', reason: 'eligible' })
    expect(posthog.capture).toHaveBeenCalledWith('apply_modal_opened', {
      job_id: 'j1', platform: 'greenhouse', reason: 'eligible',
    })
  })

  it('fieldCopied captures job_id + field_name', () => {
    t.fieldCopied({ job_id: 'j1', field_name: 'why_interested' })
    expect(posthog.capture).toHaveBeenCalledWith('apply_field_copied', {
      job_id: 'j1', field_name: 'why_interested',
    })
  })

  it('atsOpened captures job_id + platform', () => {
    t.atsOpened({ job_id: 'j1', platform: 'greenhouse' })
    expect(posthog.capture).toHaveBeenCalledWith('apply_ats_opened', {
      job_id: 'j1', platform: 'greenhouse',
    })
  })

  it('markedApplied captures job_id, platform, ats_was_opened', () => {
    t.markedApplied({ job_id: 'j1', platform: 'greenhouse', ats_was_opened: true })
    expect(posthog.capture).toHaveBeenCalledWith('apply_marked_applied', {
      job_id: 'j1', platform: 'greenhouse', ats_was_opened: true,
    })
  })

  it('modalDismissed captures job_id, platform, ats_was_opened', () => {
    t.modalDismissed({ job_id: 'j1', platform: 'greenhouse', ats_was_opened: false })
    expect(posthog.capture).toHaveBeenCalledWith('apply_modal_dismissed', {
      job_id: 'j1', platform: 'greenhouse', ats_was_opened: false,
    })
  })

  it('ineligibleActionTaken captures job_id + reason', () => {
    t.ineligibleActionTaken({ job_id: 'j1', reason: 'profile_incomplete' })
    expect(posthog.capture).toHaveBeenCalledWith('apply_ineligible_action_taken', {
      job_id: 'j1', reason: 'profile_incomplete',
    })
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd web && npm test -- applyTelemetry
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the lib**

`web/src/lib/applyTelemetry.js`:

```js
import posthog from 'posthog-js'

// All wrappers no-op silently if posthog isn't initialized (key missing in dev).
function capture(event, props) {
  try {
    posthog.capture(event, props)
  } catch {
    // posthog-js no-ops when uninitialized; this catch is for the test mock case.
  }
}

export const eligibilityViewed = (props) => capture('apply_eligibility_viewed', props)
export const modalOpened       = (props) => capture('apply_modal_opened', props)
export const fieldCopied       = (props) => capture('apply_field_copied', props)
export const atsOpened         = (props) => capture('apply_ats_opened', props)
export const markedApplied     = (props) => capture('apply_marked_applied', props)
export const modalDismissed    = (props) => capture('apply_modal_dismissed', props)
export const ineligibleActionTaken = (props) => capture('apply_ineligible_action_taken', props)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd web && npm test -- applyTelemetry
```

Expected: 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/applyTelemetry.js web/src/lib/__tests__/applyTelemetry.test.js
git commit -m "feat(web): applyTelemetry — 7 PostHog event wrappers"
```

---

## Task Group 3 — EligibilityBadge + JobTable wiring + AppLayout refactor (Session 3a)

**Estimated time:** ~2 hours.
**Outcome:** Dashboard shows eligibility dots per row; AppLayout uses authoritative `profile_complete` signal.

### Task 3.1: `<EligibilityBadge>` component

**Files:**
- Create: `web/src/components/apply/EligibilityBadge.jsx`
- Create: `web/src/components/apply/__tests__/EligibilityBadge.test.jsx`

- [ ] **Step 1: Write the failing test**

`web/src/components/apply/__tests__/EligibilityBadge.test.jsx`:

```jsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { EligibilityBadge } from '../EligibilityBadge'

describe('EligibilityBadge', () => {
  it('renders green with "Smart Apply available" tooltip when eligible', () => {
    const { container } = render(<EligibilityBadge eligible reason={null} platform="greenhouse" />)
    const badge = container.querySelector('[data-testid="eligibility-badge"]')
    expect(badge).toHaveAttribute('data-state', 'eligible')
    expect(badge).toHaveAttribute('title', expect.stringMatching(/Smart Apply available/i))
  })

  it('renders amber with "Profile incomplete" tooltip', () => {
    const { container } = render(<EligibilityBadge eligible={false} reason="profile_incomplete" />)
    const badge = container.querySelector('[data-testid="eligibility-badge"]')
    expect(badge).toHaveAttribute('data-state', 'recoverable')
    expect(badge).toHaveAttribute('title', expect.stringMatching(/Profile incomplete/i))
  })

  it('renders amber with "No tailored resume yet" tooltip', () => {
    const { container } = render(<EligibilityBadge eligible={false} reason="no_resume" />)
    expect(container.querySelector('[data-testid="eligibility-badge"]'))
      .toHaveAttribute('title', expect.stringMatching(/No tailored resume/i))
  })

  it('renders amber with "No apply URL" tooltip', () => {
    const { container } = render(<EligibilityBadge eligible={false} reason="no_apply_url" />)
    expect(container.querySelector('[data-testid="eligibility-badge"]'))
      .toHaveAttribute('title', expect.stringMatching(/No apply URL/i))
  })

  it('renders grey with "Already applied" tooltip', () => {
    const { container } = render(<EligibilityBadge eligible={false} reason="already_applied" />)
    const badge = container.querySelector('[data-testid="eligibility-badge"]')
    expect(badge).toHaveAttribute('data-state', 'terminal')
    expect(badge).toHaveAttribute('title', expect.stringMatching(/Already applied/i))
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd web && npm test -- EligibilityBadge
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the component**

`web/src/components/apply/EligibilityBadge.jsx`:

```jsx
const TOOLTIPS = {
  eligible: 'Smart Apply available',
  profile_incomplete: 'Profile incomplete — finish in Settings',
  no_resume: 'No tailored resume yet',
  no_apply_url: 'No apply URL on this job',
  already_applied: 'Already applied',
}

const STATE_BY_REASON = {
  eligible: 'eligible',
  profile_incomplete: 'recoverable',
  no_resume: 'recoverable',
  no_apply_url: 'recoverable',
  already_applied: 'terminal',
}

const COLOR_CLASS = {
  eligible: 'bg-green-500',
  recoverable: 'bg-amber-400',
  terminal: 'bg-gray-400',
}

export function EligibilityBadge({ eligible, reason, platform }) {
  const key = eligible ? 'eligible' : reason
  const state = STATE_BY_REASON[key] ?? 'terminal'
  const tooltip = TOOLTIPS[key] ?? 'Eligibility unknown'

  return (
    <span
      data-testid="eligibility-badge"
      data-state={state}
      data-platform={platform || ''}
      title={tooltip}
      className={`inline-block w-2.5 h-2.5 rounded-full ${COLOR_CLASS[state]}`}
      aria-label={tooltip}
    />
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd web && npm test -- EligibilityBadge
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/apply/EligibilityBadge.jsx web/src/components/apply/__tests__/EligibilityBadge.test.jsx
git commit -m "feat(web): EligibilityBadge — read-only row badge"
```

---

### Task 3.2: Hoist `<ProfileProvider>` to app root + wire badge into `JobTable`

**Files:**
- Modify: `web/src/main.jsx`
- Modify: `web/src/components/JobTable.jsx`

- [ ] **Step 1: Hoist `<ProfileProvider>` to `main.jsx`**

Edit `web/src/main.jsx`. The current tree wraps `<App />` in `<StrictMode>`. Add `<ProfileProvider>` inside `<StrictMode>`:

```jsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { PostHogProvider } from 'posthog-js/react'
import './index.css'
import App from './App.jsx'
import { initPostHog } from './lib/posthog'
import { ProfileProvider } from './hooks/useUserProfile'  // NEW

const ph = initPostHog()

const tree = (
  <StrictMode>
    <ProfileProvider>
      <App />
    </ProfileProvider>
  </StrictMode>
)

createRoot(document.getElementById('root')).render(
  ph ? <PostHogProvider client={ph}>{tree}</PostHogProvider> : tree,
)
```

The provider wraps `<App />` and is inside `<PostHogProvider>` (which stays outermost so PostHog is initialized before any consumer renders).

- [ ] **Step 2: Add `<EligibilityBadge>` per row in `JobTable.jsx`**

Open `web/src/components/JobTable.jsx`. At the top of the file, add imports:

```jsx
import { EligibilityBadge } from './apply/EligibilityBadge'
import { useUserProfile } from '../hooks/useUserProfile'
import { computeEligibility } from '../hooks/useApplyEligibility'
```

Inside the row-rendering code, compute eligibility and render the badge in the first cell of each row (or a dedicated narrow column — match the existing column layout):

```jsx
function Row({ job, ... }) {
  const { profile } = useUserProfile()
  const eligibility = computeEligibility(job, profile || { profile_complete: false })

  return (
    <tr ...>
      <td className="w-6 px-2"><EligibilityBadge {...eligibility} platform={job.apply_platform} /></td>
      {/* ... existing cells ... */}
    </tr>
  )
}
```

- [ ] **Step 3: Boot the dev server and verify visually**

```bash
cd web && npm run dev
```

Open browser to http://localhost:5173 + log in. Expected: every row has a small dot in the leftmost column. Hover shows the tooltip text.

If profile is loaded but profile_complete is false on your account, every row will show amber (`profile_incomplete`). Toggle by editing the database row or completing onboarding.

- [ ] **Step 4: Commit**

```bash
git add web/src/main.jsx web/src/components/JobTable.jsx
git commit -m "feat(web): hoist ProfileProvider + wire EligibilityBadge into JobTable"
```

---

### Task 3.3: AppLayout refactor — replace local heuristic with `useUserProfile`

**Files:**
- Modify: `web/src/layouts/AppLayout.jsx`

- [ ] **Step 1: Read the current state**

Current state (`AppLayout.jsx:11-25`):
```jsx
const [profileComplete, setProfileComplete] = useState(true);
useEffect(() => {
  if (!user) return;
  apiGet('/api/profile').then(data => {
    setOnboardingDone(!!data.onboarding_completed_at || !!data.full_name);
    setProfileComplete(!!(data.full_name && data.phone && data.location));  // local heuristic — DRIFTS from backend
  }).catch(() => setOnboardingDone(false));
}, [user]);
```

The 3-field heuristic disagrees with the backend's 9-field `check_profile_completeness()` — that's the drift PR #44 was about, just on a different surface. `<ProfileProvider>` was already hoisted to `main.jsx` in Task 3.2 so AppLayout can consume the hook directly.

- [ ] **Step 2: Refactor AppLayout to consume the hook**

Edit `web/src/layouts/AppLayout.jsx`:

```jsx
import { useUserProfile } from '../hooks/useUserProfile'

export default function AppLayout() {
  const { user, loading } = useAuth()
  const { profile, isLoading: profileLoading } = useUserProfile()

  if (loading || (user && profileLoading)) {
    return <div className="min-h-screen bg-cream flex items-center justify-center"><span className="spinner" /></div>
  }
  if (!user) return <Navigate to="/login" replace />

  const onboardingDone = !!(profile?.onboarding_completed_at || profile?.full_name)
  const profileComplete = !!profile?.profile_complete

  // ... existing JSX unchanged, but read profileComplete from above
  return (
    <>
      {!profileComplete && <FinishSetupBanner />}
      {/* ... rest unchanged ... */}
    </>
  )
}
```

Remove the now-dead `useState`/`useEffect` for profile and the inline `apiGet('/api/profile')`.

- [ ] **Step 3: Smoke test**

```bash
cd web && npm run dev
```

Log in with an account whose backend `profile_complete=false`. The `<FinishSetupBanner>` must still appear. Log in with a fully-completed account; banner must disappear.

If a regression appears (e.g., the banner shows for someone who used to be considered complete by the 3-field heuristic but is incomplete by the 9-field backend check), that's the **correct** new behavior — surface as a bug to operator only if false-positive on actually-complete accounts.

- [ ] **Step 4: Run frontend tests**

```bash
cd web && npm test
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add web/src/layouts/AppLayout.jsx
git commit -m "refactor(web): AppLayout uses authoritative profile_complete from backend

Replaces the local 3-field heuristic (full_name && phone && location)
with the backend's check_profile_completeness() output exposed via
ProfileResponse.profile_complete. Eliminates a known drift class
documented in spec §2 backend dependency."
```

---

## Task Group 4 — Smart-button + modal (Session 3b/4a)

**Estimated time:** ~3 hours.
**Outcome:** Smart Apply button on JobWorkspace + modal with copy-table + record-applied flow + all degraded paths.

### Task 4.1: `<AutoApplyButton>` 5-state machine

**Files:**
- Create: `web/src/components/apply/AutoApplyButton.jsx`
- Create: `web/src/components/apply/__tests__/AutoApplyButton.test.jsx`

- [ ] **Step 1: Write the failing test**

`web/src/components/apply/__tests__/AutoApplyButton.test.jsx`:

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { AutoApplyButton } from '../AutoApplyButton'

vi.mock('../../../lib/applyTelemetry', () => ({
  ineligibleActionTaken: vi.fn(),
}))
import * as t from '../../../lib/applyTelemetry'

const baseJob = { id: 'j1', apply_url: 'https://x.io', resume_s3_key: 'k', apply_platform: 'greenhouse', application_status: 'scored' }
const completeProfile = { profile_complete: true }

// AutoApplyButton uses useNavigate(); tests must render inside a Router.
function renderWithProfile(props, profile = completeProfile) {
  return render(
    <MemoryRouter>
      <AutoApplyButton job={baseJob} profile={profile} onOpenModal={vi.fn()} {...props} />
    </MemoryRouter>
  )
}

function renderWithJob(job) {
  return render(
    <MemoryRouter>
      <AutoApplyButton job={job} profile={completeProfile} onOpenModal={vi.fn()} />
    </MemoryRouter>
  )
}

describe('AutoApplyButton smart-button states', () => {
  beforeEach(() => vi.clearAllMocks())

  it('eligible → shows "Smart Apply" enabled', () => {
    renderWithProfile()
    const btn = screen.getByRole('button', { name: /Smart Apply/i })
    expect(btn).toBeEnabled()
  })

  it('eligible → click invokes onOpenModal', () => {
    const onOpenModal = vi.fn()
    renderWithProfile({ onOpenModal })
    fireEvent.click(screen.getByRole('button', { name: /Smart Apply/i }))
    expect(onOpenModal).toHaveBeenCalledTimes(1)
  })

  it('profile_incomplete → label changes, captures telemetry', () => {
    renderWithProfile({}, { profile_complete: false })
    expect(screen.getByRole('button', { name: /Complete profile to apply/i })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: /Complete profile to apply/i }))
    expect(t.ineligibleActionTaken).toHaveBeenCalledWith({ job_id: 'j1', reason: 'profile_incomplete' })
  })

  it('no_resume → label changes', () => {
    renderWithJob({ ...baseJob, resume_s3_key: null })
    expect(screen.getByRole('button', { name: /Generate tailored resume first/i })).toBeEnabled()
  })

  it('no_apply_url → label changes', () => {
    renderWithJob({ ...baseJob, apply_url: null })
    expect(screen.getByRole('button', { name: /Add apply URL/i })).toBeEnabled()
  })

  it('already_applied → "Applied ✓" disabled', () => {
    renderWithJob({ ...baseJob, application_status: 'applied' })
    const btn = screen.getByRole('button', { name: /Applied/i })
    expect(btn).toBeDisabled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd web && npm test -- AutoApplyButton
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the component**

`web/src/components/apply/AutoApplyButton.jsx`:

```jsx
import { useNavigate } from 'react-router-dom'
import { computeEligibility } from '../../hooks/useApplyEligibility'
import { ineligibleActionTaken } from '../../lib/applyTelemetry'

const STATE_CONFIG = {
  eligible:           { label: 'Smart Apply',                       disabled: false },
  profile_incomplete: { label: 'Complete profile to apply',         disabled: false },
  no_resume:          { label: 'Generate tailored resume first',    disabled: false },
  no_apply_url:       { label: 'Add apply URL',                     disabled: false },
  already_applied:    { label: 'Applied ✓',                         disabled: true  },
}

export function AutoApplyButton({ job, profile, onOpenModal }) {
  const navigate = useNavigate()
  const eligibility = computeEligibility(job, profile)
  const stateKey = eligibility.eligible ? 'eligible' : eligibility.reason
  const cfg = STATE_CONFIG[stateKey]

  const onClick = () => {
    if (stateKey === 'eligible') {
      onOpenModal()
      return
    }
    ineligibleActionTaken({ job_id: job.id, reason: stateKey })
    if (stateKey === 'profile_incomplete') {
      navigate('/settings#profile')
    } else if (stateKey === 'no_resume') {
      const tailorCard = document.querySelector('[data-testid="tailor-card"]')
      tailorCard?.scrollIntoView({ behavior: 'smooth' })
      tailorCard?.querySelector('button')?.focus()
    } else if (stateKey === 'no_apply_url') {
      const editField = document.querySelector('[data-testid="apply-url-edit"]')
      editField?.click()
    }
  }

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={cfg.disabled}
      data-testid="auto-apply-button"
      data-state={stateKey}
      className="px-4 py-2 border-2 border-black bg-yellow-300 hover:bg-yellow-400 disabled:bg-gray-200 disabled:cursor-not-allowed font-mono"
    >
      {cfg.label}
    </button>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd web && npm test -- AutoApplyButton
```

Expected: 6 tests pass (5 state assertions + 1 click-eligible).

- [ ] **Step 5: Commit**

```bash
git add web/src/components/apply/AutoApplyButton.jsx web/src/components/apply/__tests__/AutoApplyButton.test.jsx
git commit -m "feat(web): AutoApplyButton — 5-state smart-button"
```

---

### Task 4.2: `<QuestionsTable>` — per-field copy

**Files:**
- Create: `web/src/components/apply/QuestionsTable.jsx`
- Create: `web/src/components/apply/__tests__/QuestionsTable.test.jsx`

- [ ] **Step 1: Write the failing test**

`web/src/components/apply/__tests__/QuestionsTable.test.jsx`:

```jsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QuestionsTable } from '../QuestionsTable'

// Match the actual /api/apply/preview shape (app.py:2884-2894):
// { id, label, type, required, options, max_length, ai_answer, requires_user_action, category }
const Q = [
  { id: 'why_interested', label: 'Why are you interested?', ai_answer: 'Because I love payments.', required: true, requires_user_action: false, category: 'custom' },
  { id: 'experience_years', label: 'Years of experience?', ai_answer: '5+ years', required: true, requires_user_action: false, category: 'profile' },
  { id: 'salary_neg', label: 'Salary expectation?', ai_answer: null, required: false, requires_user_action: true, category: 'custom' },
]

describe('QuestionsTable', () => {
  it('writes the ai_answer to clipboard on copy click and calls onCopy with label', async () => {
    const onCopy = vi.fn()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })

    render(<QuestionsTable questions={Q} onCopy={onCopy} />)

    const buttons = screen.getAllByRole('button', { name: /copy/i })
    fireEvent.click(buttons[0])

    expect(writeText).toHaveBeenCalledWith('Because I love payments.')
    expect(onCopy).toHaveBeenCalledWith({ field_name: 'Why are you interested?' })
  })

  it('disables copy when ai_answer is null (AI failed for that question)', () => {
    render(<QuestionsTable questions={Q} onCopy={vi.fn()} />)
    const buttons = screen.getAllByRole('button', { name: /copy/i })
    // 3rd row has ai_answer: null
    expect(buttons[2]).toBeDisabled()
  })

  it('shows fallback text in answer column when ai_answer is null', () => {
    render(<QuestionsTable questions={Q} onCopy={vi.fn()} />)
    expect(screen.getByText(/no AI answer/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd web && npm test -- QuestionsTable
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the component**

`web/src/components/apply/QuestionsTable.jsx`:

```jsx
export function QuestionsTable({ questions, onCopy }) {
  const copy = async (q) => {
    if (!q.ai_answer) return
    await navigator.clipboard.writeText(q.ai_answer)
    onCopy({ field_name: q.label })
  }

  return (
    <table className="w-full font-mono text-sm">
      <thead>
        <tr className="border-b-2 border-black">
          <th className="text-left p-2">Question</th>
          <th className="text-left p-2">AI Answer</th>
          <th className="w-12"></th>
        </tr>
      </thead>
      <tbody>
        {questions.map((q) => (
          <tr key={q.id} className="border-b border-black/30">
            <td className="p-2 align-top">
              {q.label}{q.required && <span className="text-red-700"> *</span>}
            </td>
            <td className="p-2">
              {q.ai_answer
                ? q.ai_answer
                : <span className="italic text-gray-500">(no AI answer — fill manually)</span>}
            </td>
            <td className="p-2">
              <button
                type="button"
                onClick={() => copy(q)}
                disabled={!q.ai_answer}
                className="px-2 py-1 border border-black hover:bg-yellow-200 disabled:opacity-30 disabled:cursor-not-allowed"
              >
                📋 Copy
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd web && npm test -- QuestionsTable
```

Expected: 1 test passes.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/apply/QuestionsTable.jsx web/src/components/apply/__tests__/QuestionsTable.test.jsx
git commit -m "feat(web): QuestionsTable — per-field copy"
```

---

### Task 4.3: `<ProfileSnapshot>` collapsible

**Files:**
- Create: `web/src/components/apply/ProfileSnapshot.jsx`
- Create: `web/src/components/apply/__tests__/ProfileSnapshot.test.jsx`

- [ ] **Step 1: Write the failing test**

`web/src/components/apply/__tests__/ProfileSnapshot.test.jsx`:

```jsx
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ProfileSnapshot } from '../ProfileSnapshot'

const SNAP = {
  full_name: 'Daisy', visa_status: 'EU citizen',
  salary_expectation_notes: '€85k', notice_period_text: '4 weeks',
}

describe('ProfileSnapshot', () => {
  it('starts collapsed, expands on click', () => {
    render(<ProfileSnapshot snapshot={SNAP} />)
    expect(screen.queryByText('€85k')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Profile snapshot/i }))
    expect(screen.getByText('€85k')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd web && npm test -- ProfileSnapshot
```

Expected: FAIL.

- [ ] **Step 3: Implement**

`web/src/components/apply/ProfileSnapshot.jsx`:

```jsx
import { useState } from 'react'

export function ProfileSnapshot({ snapshot }) {
  const [open, setOpen] = useState(false)
  if (!snapshot) return null

  const rows = Object.entries(snapshot).filter(([, v]) => v != null && v !== '')

  return (
    <div className="border-2 border-black">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full p-2 text-left font-mono hover:bg-yellow-100"
      >
        {open ? '▾' : '▸'} Profile snapshot
      </button>
      {open && (
        <dl className="p-2 font-mono text-sm">
          {rows.map(([k, v]) => (
            <div key={k} className="flex gap-2 py-1">
              <dt className="font-bold w-48">{k}</dt>
              <dd>{String(v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd web && npm test -- ProfileSnapshot
```

Expected: 1 test passes.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/apply/ProfileSnapshot.jsx web/src/components/apply/__tests__/ProfileSnapshot.test.jsx
git commit -m "feat(web): ProfileSnapshot — collapsible profile view"
```

---

### Task 4.4: `<EmptyPreviewState>` retry message

**Files:**
- Create: `web/src/components/apply/EmptyPreviewState.jsx`
- Create: `web/src/components/apply/__tests__/EmptyPreviewState.test.jsx`

- [ ] **Step 1: Write the failing test**

`web/src/components/apply/__tests__/EmptyPreviewState.test.jsx`:

```jsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { EmptyPreviewState } from '../EmptyPreviewState'

describe('EmptyPreviewState', () => {
  it('renders message and calls onRetry on click', () => {
    const onRetry = vi.fn()
    render(<EmptyPreviewState onRetry={onRetry} />)

    expect(screen.getByText(/AI prefill not available/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Retry preview/i }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd web && npm test -- EmptyPreviewState
```

Expected: FAIL.

- [ ] **Step 3: Implement**

`web/src/components/apply/EmptyPreviewState.jsx`:

```jsx
export function EmptyPreviewState({ onRetry }) {
  return (
    <div className="p-4 border-2 border-amber-500 bg-amber-50 font-mono text-sm">
      <p className="mb-2">AI prefill not available for this posting. You'll fill the form manually.</p>
      <button type="button" onClick={onRetry} className="px-3 py-1 border border-black hover:bg-yellow-200">
        Retry preview
      </button>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd web && npm test -- EmptyPreviewState
```

Expected: 1 test passes.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/apply/EmptyPreviewState.jsx web/src/components/apply/__tests__/EmptyPreviewState.test.jsx
git commit -m "feat(web): EmptyPreviewState — retry message"
```

---

### Task 4.5: `<AutoApplyModal>` orchestrator

**Files:**
- Create: `web/src/components/apply/AutoApplyModal.jsx`
- Create: `web/src/components/apply/__tests__/AutoApplyModal.test.jsx`

- [ ] **Step 1: Write the failing test (lifecycle + happy path)**

`web/src/components/apply/__tests__/AutoApplyModal.test.jsx`:

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { AutoApplyModal } from '../AutoApplyModal'

vi.mock('../../../api', () => ({ apiGet: vi.fn(), apiCall: vi.fn() }))
vi.mock('../../../lib/applyTelemetry', () => ({
  modalOpened: vi.fn(),
  modalDismissed: vi.fn(),
  fieldCopied: vi.fn(),
  atsOpened: vi.fn(),
  markedApplied: vi.fn(),
}))
import { apiGet, apiCall } from '../../../api'
import * as t from '../../../lib/applyTelemetry'

const job = { id: 'j1', title: 'SRE', company: 'Acme', apply_url: 'https://acme.com/apply', apply_platform: 'greenhouse' }
// Match the actual /api/apply/preview shape (app.py:2896-2927).
const previewPayload = {
  eligible: true,
  job: { title: 'SRE', company: 'Acme', apply_url: 'https://acme.com/apply' },
  resume: { s3_url: 'https://r.s3', filename: 'resume.pdf', resume_version: 1, s3_key: 'k', is_default: false },
  cover_letter: { text: 'Dear hiring team,\nI am writing about your SRE role...', editable: true, max_length: 10000, source: 'ai_generated', include_by_default: true },
  custom_questions: [{ id: 'why', label: 'Why?', type: 'textarea', required: true, ai_answer: 'Because.', requires_user_action: false, category: 'custom' }],
  profile: { first_name: 'Daisy', last_name: 'X', email: 'd@x.io', phone: '+353', linkedin: 'in/daisy', github: 'gh/daisy', website: '', location: 'Dublin' },
  platform: 'greenhouse',
}

describe('AutoApplyModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiGet.mockResolvedValue(previewPayload)
    apiCall.mockResolvedValue({ ok: true })
  })

  it('opens, fetches preview, fires modalOpened telemetry', async () => {
    render(<AutoApplyModal job={job} isOpen onClose={vi.fn()} onMarkApplied={vi.fn()} />)
    expect(t.modalOpened).toHaveBeenCalledWith({ job_id: 'j1', platform: 'greenhouse', reason: 'eligible' })
    await waitFor(() => expect(apiGet).toHaveBeenCalledWith('/api/apply/preview/j1'))
  })

  it('shows EmptyPreviewState when custom_questions is empty', async () => {
    apiGet.mockResolvedValueOnce({ ...previewPayload, custom_questions: [] })
    render(<AutoApplyModal job={job} isOpen onClose={vi.fn()} onMarkApplied={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/AI prefill not available/i)).toBeInTheDocument())
  })

  it('Open ATS swaps primary to "I submitted — mark applied" + fires atsOpened', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    render(<AutoApplyModal job={job} isOpen onClose={vi.fn()} onMarkApplied={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Open ATS/i })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Open ATS/i }))
    expect(openSpy).toHaveBeenCalledWith('https://acme.com/apply', '_blank')
    expect(t.atsOpened).toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /I submitted/i })).toBeInTheDocument()
    openSpy.mockRestore()
  })

  it('Mark applied calls /api/apply/record then onMarkApplied + onClose', async () => {
    const onMarkApplied = vi.fn()
    const onClose = vi.fn()
    render(<AutoApplyModal job={job} isOpen onClose={onClose} onMarkApplied={onMarkApplied} />)
    await waitFor(() => screen.getByRole('button', { name: /Open ATS/i }))
    vi.spyOn(window, 'open').mockImplementation(() => null)
    fireEvent.click(screen.getByRole('button', { name: /Open ATS/i }))
    fireEvent.click(screen.getByRole('button', { name: /I submitted/i }))
    await waitFor(() => expect(apiCall).toHaveBeenCalledWith('/api/apply/record', expect.objectContaining({ job_id: 'j1' })))
    await waitFor(() => expect(onMarkApplied).toHaveBeenCalledTimes(1))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('Mark applied failure keeps modal open + does not call onMarkApplied', async () => {
    apiCall.mockRejectedValueOnce(new Error('500 server'))
    const onMarkApplied = vi.fn()
    const onClose = vi.fn()
    render(<AutoApplyModal job={job} isOpen onClose={onClose} onMarkApplied={onMarkApplied} />)
    await waitFor(() => screen.getByRole('button', { name: /Open ATS/i }))
    vi.spyOn(window, 'open').mockImplementation(() => null)
    fireEvent.click(screen.getByRole('button', { name: /Open ATS/i }))
    fireEvent.click(screen.getByRole('button', { name: /I submitted/i }))
    await waitFor(() => expect(apiCall).toHaveBeenCalled())
    expect(onMarkApplied).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByText(/Couldn't mark applied/i)).toBeInTheDocument()
  })

  it('dismiss without marking applied fires modalDismissed', async () => {
    const onClose = vi.fn()
    const { rerender } = render(<AutoApplyModal job={job} isOpen onClose={onClose} onMarkApplied={vi.fn()} />)
    await waitFor(() => screen.getByRole('button', { name: /Open ATS/i }))
    rerender(<AutoApplyModal job={job} isOpen={false} onClose={onClose} onMarkApplied={vi.fn()} />)
    expect(t.modalDismissed).toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd web && npm test -- AutoApplyModal
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the modal**

`web/src/components/apply/AutoApplyModal.jsx`:

```jsx
import { useEffect, useRef, useState } from 'react'
import { apiCall } from '../../api'
import { useApplyPreview } from '../../hooks/useApplyPreview'
import { QuestionsTable } from './QuestionsTable'
import { ProfileSnapshot } from './ProfileSnapshot'
import { EmptyPreviewState } from './EmptyPreviewState'
import { modalOpened, modalDismissed, fieldCopied, atsOpened, markedApplied } from '../../lib/applyTelemetry'

export function AutoApplyModal({ job, isOpen, onClose, onMarkApplied }) {
  const { data: preview, isLoading, refetch } = useApplyPreview(job.id, { enabled: isOpen })
  const [atsOpenedState, setAtsOpenedState] = useState(false)
  const [submitError, setSubmitError] = useState(null)
  const openedFiredRef = useRef(false)
  const wasOpenRef = useRef(isOpen)

  // Modal-opened telemetry (fire once per open)
  useEffect(() => {
    if (isOpen && !openedFiredRef.current) {
      modalOpened({ job_id: job.id, platform: job.apply_platform, reason: 'eligible' })
      openedFiredRef.current = true
    }
  }, [isOpen, job])

  // Modal-dismissed telemetry (fire when modal transitions from open → closed without mark-applied)
  useEffect(() => {
    if (wasOpenRef.current && !isOpen) {
      modalDismissed({ job_id: job.id, platform: job.apply_platform, ats_was_opened: atsOpenedState })
      openedFiredRef.current = false
      setAtsOpenedState(false)
    }
    wasOpenRef.current = isOpen
  }, [isOpen, job, atsOpenedState])

  if (!isOpen) return null

  const handleOpenAts = () => {
    window.open(job.apply_url, '_blank')
    atsOpened({ job_id: job.id, platform: job.apply_platform })
    setAtsOpenedState(true)
  }

  const handleMarkApplied = async () => {
    setSubmitError(null)
    try {
      await apiCall('/api/apply/record', {
        job_id: job.id,
        platform: job.apply_platform,
        accepted_at: new Date().toISOString(),
      })
      markedApplied({ job_id: job.id, platform: job.apply_platform, ats_was_opened: atsOpenedState })
      onMarkApplied?.()
      onClose?.()
    } catch (e) {
      setSubmitError(e.message || 'Mark-applied failed')
    }
  }

  const questions = preview?.custom_questions ?? []

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center" onClick={onClose}>
      <div className="bg-cream border-4 border-black p-6 max-w-3xl w-full max-h-[90vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-xl font-bold mb-2 font-mono">
          Smart Apply: {job.company} — {job.title}
        </h2>

        {isLoading && <p className="text-sm">Loading preview…</p>}

        {!isLoading && preview && (
          <>
            <div className="flex gap-2 mb-4">
              {preview.resume?.s3_url && (
                <a href={preview.resume.s3_url} target="_blank" rel="noopener" className="px-3 py-1 border border-black bg-white">
                  📄 Tailored Resume ({preview.resume.filename})
                </a>
              )}
            </div>

            {/* Cover letter is INLINE TEXT (not a URL) — copy-paste flow */}
            {preview.cover_letter?.text && (
              <div className="mb-4 border-2 border-black p-3">
                <div className="flex justify-between items-center mb-2">
                  <span className="font-bold font-mono">Cover letter</span>
                  <button
                    type="button"
                    onClick={async () => {
                      await navigator.clipboard.writeText(preview.cover_letter.text)
                      fieldCopied({ job_id: job.id, field_name: '__cover_letter__' })
                    }}
                    className="px-2 py-1 border border-black hover:bg-yellow-200"
                  >
                    📋 Copy
                  </button>
                </div>
                <pre className="text-sm whitespace-pre-wrap font-mono max-h-48 overflow-auto">{preview.cover_letter.text}</pre>
              </div>
            )}

            {questions.length === 0 ? (
              <EmptyPreviewState onRetry={refetch} />
            ) : (
              <QuestionsTable
                questions={questions}
                onCopy={({ field_name }) => fieldCopied({ job_id: job.id, field_name })}
              />
            )}

            <div className="my-4">
              <ProfileSnapshot snapshot={preview.profile} />
            </div>
          </>
        )}

        {submitError && <p className="text-red-700 text-sm mb-2">Couldn't mark applied: {submitError}</p>}

        <div className="flex justify-end gap-2 mt-4">
          <button type="button" onClick={onClose} className="px-4 py-2 border-2 border-black bg-white">Cancel</button>
          {atsOpenedState ? (
            <button type="button" onClick={handleMarkApplied} className="px-4 py-2 border-2 border-black bg-yellow-300 hover:bg-yellow-400">
              I submitted — mark applied
            </button>
          ) : (
            <button type="button" onClick={handleOpenAts} className="px-4 py-2 border-2 border-black bg-yellow-300 hover:bg-yellow-400">
              Open ATS in new tab
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd web && npm test -- AutoApplyModal
```

Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/apply/AutoApplyModal.jsx web/src/components/apply/__tests__/AutoApplyModal.test.jsx
git commit -m "feat(web): AutoApplyModal — orchestrates preview, copy, record-applied"
```

---

### Task 4.6: Wire button + modal into JobWorkspace

**Files:**
- Modify: `web/src/pages/JobWorkspace.jsx`
- Modify: `web/src/components/TailorCard.jsx` (add `data-testid="tailor-card"`)

- [ ] **Step 0: Add `data-testid` to `<TailorCard>`'s root element**

`AutoApplyButton`'s `no_resume` smart-button action does `document.querySelector('[data-testid="tailor-card"]')` then scrolls. The querySelector returns null silently if absent — bad UX. Open `web/src/components/TailorCard.jsx` and add the attribute to the outermost element returned by the component:

```jsx
export default function TailorCard({ data, company }) {
  return (
    <section data-testid="tailor-card" className="...">
      {/* existing content unchanged */}
    </section>
  )
}
```

(If TailorCard's root is a `<div>`, keep the tag — only add the `data-testid`. Don't change other props.)

- [ ] **Step 1: Add imports + state to JobWorkspace**

Edit `web/src/pages/JobWorkspace.jsx`. At the top, add:

```jsx
import { useState } from 'react'
import { AutoApplyButton } from '../components/apply/AutoApplyButton'
import { AutoApplyModal } from '../components/apply/AutoApplyModal'
import { useUserProfile } from '../hooks/useUserProfile'
```

In the component body, add state + render the button and modal in the action area (find an appropriate slot near other action buttons):

```jsx
function JobWorkspace() {
  // ... existing state/queries unchanged
  const { profile } = useUserProfile()
  const [modalOpen, setModalOpen] = useState(false)

  // ... when rendering action area:
  return (
    <>
      {/* ... existing JSX ... */}
      <div className="action-area">
        {/* ... other action buttons ... */}
        <AutoApplyButton
          job={job}
          profile={profile || { profile_complete: false }}
          onOpenModal={() => setModalOpen(true)}
        />
      </div>

      <AutoApplyModal
        job={job}
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        onMarkApplied={() => {
          // Optimistic refresh: just refetch the job to update application_status
          // (existing JobWorkspace must already have a job-refetch mechanism)
          refetchJob?.()
        }}
      />
    </>
  )
}
```

(The exact insertion point and `refetchJob` reference depend on the existing JobWorkspace structure. Read the file first; match the pattern.)

- [ ] **Step 2: Run frontend tests**

```bash
cd web && npm test
```

Expected: all green.

- [ ] **Step 3: Boot dev server, manually walk happy path on a real Greenhouse job**

```bash
cd web && npm run dev
```

In browser:
1. Log in
2. Navigate to a Greenhouse job in the dashboard
3. Click into JobWorkspace
4. Confirm "Smart Apply" button is enabled
5. Click — modal opens
6. Resume + cover letter chips visible
7. Question list visible (if backend returns them)
8. Click 📋 on one — confirm clipboard contains the answer (paste somewhere to verify)
9. Click "Open ATS in new tab" — Greenhouse page opens in new tab
10. Return to NaukriBaba, primary button now says "I submitted — mark applied"
11. Click — modal closes, job's `application_status` updates to `applied` after refetch

If any step fails, fix before proceeding to Group 5.

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/JobWorkspace.jsx web/src/components/TailorCard.jsx
git commit -m "feat(web): wire AutoApplyButton + AutoApplyModal into JobWorkspace"
```

---

## Task Group 5 — Answer-quality CI + smoke + PR (Sessions 4-5)

**Estimated time:** ~3 hours.
**Outcome:** Layer A floor test in CI, Layer B fixture format ready (operator action: write 5 ideal answers), smoke checklist, PR ready for review.

### Task 5.1: Layer A — programmatic floor test

**Files:**
- Create: `tests/quality/test_answer_quality_floor.py`

- [ ] **Step 1: Write the test**

`tests/quality/test_answer_quality_floor.py`:

```python
"""Layer A — programmatic floor checks for AI-generated apply answers.

Catches the obvious-bad cases before any human review:
- empty/truncated answers
- placeholder leakage ([fill me], TODO, etc.)
- echoed questions
- generic answers with zero personalization

Per Smart Apply Phase 1 spec §6.3 Layer A.
"""
from __future__ import annotations
import os

import pytest
from fastapi.testclient import TestClient

# Replace these IDs with real S/A-tier job IDs in your prod DB. They must
# have apply_url + resume_s3_key set so /api/apply/preview returns a full
# response. The 5 IDs are stored as an env var so this file can be re-used
# in different envs; for local dev set FLOOR_TEST_JOB_IDS to a comma-list.
FIXTURE_JOB_IDS = os.environ.get("FLOOR_TEST_JOB_IDS", "").split(",")
FIXTURE_USER_ID = os.environ.get("FLOOR_TEST_USER_ID", "")

PLACEHOLDER_PATTERNS = ["[", "TODO", "FILL ME", "FIXME", "...", "Lorem ipsum"]


@pytest.fixture(scope="module")
def client():
    from app import app
    return TestClient(app)


def _profile_facts(profile: dict) -> list[str]:
    """Return short strings drawn from the profile that the AI should reference."""
    facts = []
    for skill in (profile.get("skills") or [])[:5]:
        if isinstance(skill, str) and len(skill) >= 3:
            facts.append(skill.lower())
    for role in (profile.get("target_roles") or [])[:5]:
        if isinstance(role, str):
            facts.append(role.lower())
    ctx = profile.get("candidate_context") or ""
    if ctx:
        # take the first significant phrase (4+ words)
        words = ctx.split()
        if len(words) >= 4:
            facts.append(" ".join(words[:4]).lower())
    return facts


@pytest.mark.skipif(not FIXTURE_JOB_IDS or not FIXTURE_USER_ID, reason="FLOOR_TEST_JOB_IDS / FLOOR_TEST_USER_ID not set")
@pytest.mark.parametrize("job_id", FIXTURE_JOB_IDS)
def test_answer_floor_per_job(client, job_id):
    """For each fixture job: every AI-generated answer passes the floor checks."""
    # Auth: floor test runs against staging with a fixture user — set a session
    # token via env var FLOOR_TEST_TOKEN.
    token = os.environ.get("FLOOR_TEST_TOKEN", "")
    if not token:
        pytest.skip("FLOOR_TEST_TOKEN not set")

    resp = client.get(f"/api/apply/preview/{job_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, f"preview {job_id} returned {resp.status_code}"
    data = resp.json()

    questions = data.get("custom_questions", [])
    if not questions:
        pytest.skip(f"job {job_id} returned empty custom_questions — platform unsupported, skip floor checks")

    profile = data.get("profile") or {}
    facts = _profile_facts(profile)

    fact_appearances = 0
    for q in questions:
        answer = (q.get("answer") or "").strip()
        question = (q.get("question") or "").strip()

        assert len(answer) >= 20, f"answer too short for {q['question']!r}: {answer!r}"
        assert answer.lower() != question.lower(), f"answer echoes question: {q['question']!r}"
        for pat in PLACEHOLDER_PATTERNS:
            assert pat.lower() not in answer.lower(), f"placeholder {pat!r} in answer: {answer!r}"

        if any(f in answer.lower() for f in facts):
            fact_appearances += 1

    assert fact_appearances >= 1, (
        f"job {job_id}: no profile fact ({facts!r}) appeared in any of {len(questions)} answers — "
        f"AI is producing generic responses with no personalization"
    )
```

- [ ] **Step 2: Run it locally (will skip without env vars)**

```bash
source .venv/bin/activate
pytest tests/quality/test_answer_quality_floor.py -v
```

Expected: all SKIPPED (no env vars set) — that's correct. The test runs in CI with secrets configured later.

- [ ] **Step 3: Commit**

```bash
git add tests/quality/test_answer_quality_floor.py
git commit -m "test(quality): Layer A — programmatic answer-floor checks

Skipped by default — needs FLOOR_TEST_JOB_IDS, FLOOR_TEST_USER_ID,
FLOOR_TEST_TOKEN env vars (set in staging CI)."
```

---

### Task 5.2: Layer B — golden fixture format + harness (operator writes ideal answers)

**Files:**
- Create: `tests/quality/fixtures/golden_apply_answers.json` (operator fills)
- Create: `tests/quality/test_answer_quality_golden.py`

- [ ] **Step 1: Create the fixture template**

`tests/quality/fixtures/golden_apply_answers.json`:

```json
{
  "comment": "OPERATOR: fill the 5 entries below with real job IDs from prod (3 Greenhouse + 2 Ashby) and write hand-crafted ideal answers. The harness compares AI answers to these via cosine similarity. Until filled, the harness skips.",
  "fixtures": [
    {
      "job_id": "REPLACE_WITH_GREENHOUSE_JOB_ID_1",
      "platform": "greenhouse",
      "ideal_answers": {
        "Why are you interested in this role?": "REPLACE WITH HAND-WRITTEN IDEAL ANSWER",
        "Years of relevant experience?": "REPLACE WITH HAND-WRITTEN IDEAL ANSWER"
      }
    },
    {
      "job_id": "REPLACE_WITH_GREENHOUSE_JOB_ID_2",
      "platform": "greenhouse",
      "ideal_answers": {}
    },
    {
      "job_id": "REPLACE_WITH_GREENHOUSE_JOB_ID_3",
      "platform": "greenhouse",
      "ideal_answers": {}
    },
    {
      "job_id": "REPLACE_WITH_ASHBY_JOB_ID_1",
      "platform": "ashby",
      "ideal_answers": {}
    },
    {
      "job_id": "REPLACE_WITH_ASHBY_JOB_ID_2",
      "platform": "ashby",
      "ideal_answers": {}
    }
  ]
}
```

- [ ] **Step 2: Implement the comparison harness**

`tests/quality/test_answer_quality_golden.py`:

```python
"""Layer B — golden fixture comparison.

For each fixture job, fetch /api/apply/preview and compare each AI answer to
its hand-written ideal via cosine similarity over TF-IDF vectors.

Thresholds (per Smart Apply Phase 1 spec §6.3 Layer B):
- similarity >0.6 → pass
- 0.4-0.6     → emit a warning but don't fail (so prompt iteration isn't blocked)
- <0.4        → fail (prompt regression)
"""
from __future__ import annotations
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "golden_apply_answers.json"


def _cosine_similarity(a: str, b: str) -> float:
    """TF-IDF cosine similarity. Lazy-import sklearn so tests skip cleanly
    when it's not installed."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        pytest.skip("scikit-learn not installed — pip install scikit-learn")

    if not a.strip() or not b.strip():
        return 0.0
    v = TfidfVectorizer().fit_transform([a, b])
    return float(cosine_similarity(v[0:1], v[1:2])[0, 0])


def _load_fixtures():
    data = json.loads(FIXTURE_PATH.read_text())
    return [f for f in data["fixtures"] if f["ideal_answers"]]  # only filled ones


@pytest.fixture(scope="module")
def client():
    from app import app
    return TestClient(app)


@pytest.mark.parametrize("fixture", _load_fixtures(), ids=lambda f: f["job_id"])
def test_golden_answer_similarity(client, fixture):
    token = os.environ.get("FLOOR_TEST_TOKEN", "")
    if not token:
        pytest.skip("FLOOR_TEST_TOKEN not set")

    resp = client.get(f"/api/apply/preview/{fixture['job_id']}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()

    ai_answers = {q["question"]: q.get("answer", "") for q in data.get("custom_questions", [])}

    failures = []
    warnings = []
    for question, ideal in fixture["ideal_answers"].items():
        ai = ai_answers.get(question, "")
        if not ai:
            failures.append(f"AI did not produce an answer for {question!r}")
            continue
        sim = _cosine_similarity(ai, ideal)
        if sim < 0.4:
            failures.append(f"{question!r}: similarity {sim:.2f} < 0.4 (regression)\n  ai:    {ai}\n  ideal: {ideal}")
        elif sim < 0.6:
            warnings.append(f"{question!r}: similarity {sim:.2f} (warning)")

    if warnings:
        print("\nWARNINGS:\n" + "\n".join(warnings))
    assert not failures, "\n".join(failures)
```

- [ ] **Step 3: Run locally (skips with empty fixtures)**

```bash
pytest tests/quality/test_answer_quality_golden.py -v
```

Expected: SKIPPED — no fixtures filled yet.

- [ ] **Step 4: Commit**

```bash
git add tests/quality/fixtures/golden_apply_answers.json tests/quality/test_answer_quality_golden.py
git commit -m "test(quality): Layer B — golden-answer comparison harness

Fixture JSON is template-only; operator fills with 5 real job IDs and
hand-written ideal answers per spec §6.3 Layer B."
```

---

### Task 5.3: Smoke checklist doc

**Files:**
- Create: `docs/qa/phase3c0-smoke.md`

- [ ] **Step 1: Write the checklist**

`docs/qa/phase3c0-smoke.md`:

```markdown
# Smart Apply Phase 1 — Pre-merge Smoke Checklist

Run against the Netlify staging preview from this PR (PR #41 wired Netlify
deploy previews). Report results in the PR description.

## Setup
- [ ] Open Netlify deploy preview URL from the PR
- [ ] Log in as the test account
- [ ] Open browser DevTools → Network tab → enable "Preserve log"

## Dashboard (eligibility badges)
- [ ] Dashboard loads without errors
- [ ] Every JobTable row has a small dot in the leftmost column
- [ ] Hover a green dot → tooltip says "Smart Apply available"
- [ ] Hover an amber dot → tooltip names the missing piece (e.g., "No tailored resume yet")
- [ ] Hover a grey dot → tooltip says "Already applied"
- [ ] Network tab shows ONE call to `/api/profile` and NO calls to `/api/apply/eligibility/*` (per-row eligibility is client-side)

## JobWorkspace — eligible Greenhouse job
- [ ] Click into an eligible (green-dot) Greenhouse job
- [ ] "Smart Apply" button is visible and enabled
- [ ] Click — modal opens; Network tab shows GET `/api/apply/eligibility/{id}` then GET `/api/apply/preview/{id}`
- [ ] Modal shows: header with company + role, Resume + Cover Letter chips, question table with 1+ rows, profile snapshot collapsible
- [ ] Click 📋 on one row → toast (or browser alert) confirms; paste into a text editor — the AI answer is on the clipboard
- [ ] Click "Open ATS in new tab" → real Greenhouse URL opens
- [ ] Back on NaukriBaba, primary button now says "I submitted — mark applied"
- [ ] Click "I submitted — mark applied" → modal closes; Network tab shows POST `/api/apply/record`
- [ ] Reload the JobWorkspace page → application_status shows as "applied"; row badge in dashboard now grey

## JobWorkspace — ineligible (no_resume)
- [ ] Click into a job with `application_status='scored'` but `resume_s3_key=null`
- [ ] Button label is "Generate tailored resume first"
- [ ] Click — page scrolls to TailorCard

## JobWorkspace — eligible HN Hiring (apply_platform=null)
- [ ] Click into an eligible HN Hiring job
- [ ] "Smart Apply" button enabled
- [ ] Click — modal opens; question table shows `EmptyPreviewState` ("AI prefill not available for this posting")
- [ ] Resume + cover letter chips still visible
- [ ] "Open ATS in new tab" still works
- [ ] "I submitted — mark applied" still works

## Mark-applied failure path
- [ ] DevTools → Network tab → right-click POST `/api/apply/record` row → Block request URL
- [ ] Open the modal again on a different eligible job
- [ ] Click Open ATS, then "I submitted — mark applied"
- [ ] Modal stays open; error message visible
- [ ] DevTools → unblock the URL → click again → succeeds

## Settings refactor (no regressions)
- [ ] Log in with an account whose backend `profile_complete=false`: `<FinishSetupBanner>` appears
- [ ] Complete onboarding → banner disappears

## PostHog telemetry
- [ ] Open PostHog dashboard → Live Events stream
- [ ] Reproduce: dashboard load, modal open, copy a field, open ATS, mark applied, dismiss
- [ ] Confirm 7 distinct event names captured: `apply_eligibility_viewed`, `apply_modal_opened`, `apply_field_copied`, `apply_ats_opened`, `apply_marked_applied`, `apply_modal_dismissed`, `apply_ineligible_action_taken`
- [ ] Each event has the documented properties (job_id, platform, etc.)

## Done
- [ ] Paste this completed checklist into the PR description
- [ ] Flip PR from draft to ready-for-review
```

- [ ] **Step 2: Commit**

```bash
git add docs/qa/phase3c0-smoke.md
git commit -m "docs(qa): Smart Apply Phase 1 smoke checklist"
```

---

### Task 5.4: Final verification + PR ready

- [ ] **Step 1: Run all tests once more**

```bash
source .venv/bin/activate
pytest tests/ -x --tb=short
```

Expected: green.

```bash
cd web && npm test
```

Expected: green.

```bash
cd web && npm run build
```

Expected: build succeeds.

- [ ] **Step 2: Push final branch state**

```bash
git push
```

- [ ] **Step 3: Run smoke checklist**

Open `docs/qa/phase3c0-smoke.md` and check every item against the Netlify preview. Paste results into the PR description.

- [ ] **Step 4: Flip PR to ready**

```bash
gh pr ready
```

Expected: PR moves from draft to ready for review.

---

## Self-review against the spec

Mapping each spec section to tasks that implement it:

| Spec section | Tasks |
|---|---|
| §1 Goal — wire frontend to apply backend | All Group 4 tasks |
| §1 "Smart Apply" naming | Task 4.1 (`STATE_CONFIG.eligible.label = 'Smart Apply'`) |
| §2 Backend dependency: `profile_complete` field | Task 1.2 |
| §3 Happy path | Tasks 4.5, 4.6 (modal flow + JobWorkspace integration) |
| §3 Degraded paths — defensive eligibility / record fail / empty preview | Tasks 4.4, 4.5 (5th + 6th tests in modal) |
| §3 Smart-button states (5 of them) | Task 4.1 |
| §4 File layout | All Group 2/3/4 component creation tasks |
| §4 Component contracts | Each component task includes the contract |
| §4 Eligibility computation | Task 2.3 |
| §4 Smart-button state machine | Task 4.1 |
| §5 Telemetry — 7 events | Task 2.5 |
| §6.1 Unit tests (~24) | Tasks 2.2-4.5 (each component has a `__tests__` test file) |
| §6.2 Backend contract tests (3) | Tasks 1.2, 1.3, 1.4 |
| §6.3 Layer A floor checks | Task 5.1 |
| §6.3 Layer B golden fixtures | Task 5.2 |
| §6.3 Layer C AI judge | (manual, not automated in plan — captured-as-followup per spec §7) |
| §6.3 Layer D drop-off telemetry | Wired by Task 2.5 events; threshold checked manually post-launch |
| §6.4 Smoke checklist | Task 5.3 |
| §7 In scope | Implemented across all groups |
| §7 Out of scope (cloud browser, settings tile, mobile, etc.) | Not implemented (correctly) |
| §8 Risks: drift, AppLayout heuristic, defensive call failure, record failure | Mitigated by Tasks 1.2-1.4, 3.3, 4.5 |

**Gaps found in self-review:** None. Every spec requirement maps to a task.

**Estimated total work:** ~13 hours of focused work split across 5 sessions over ~1 week of calendar.

---

## How to execute

**Recommended next step:** invoke `superpowers:subagent-driven-development` (fresh subagent per task with review between tasks) **or** `superpowers:executing-plans` (batch execution with checkpoints in this session).

**For each task:** verify the "Expected" output matches reality. If it doesn't, stop and diagnose — do not commit until tests are green.

**On any task failure during a TDD step:** the failing test is the spec. Fix the implementation, not the test, unless the test itself has a typo.
