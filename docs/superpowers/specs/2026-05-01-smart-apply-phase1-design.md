# Smart Apply — Phase 1 (Hand-Paste UI) — Design Spec

**Status:** Draft → for user review
**Date:** 2026-05-01
**Supersedes the stub at:** `docs/superpowers/plans/2026-04-26-auto-apply-plan3c-frontend-ui.md` (Phase 1 portion only)
**Related backend specs:** [3a WS](../plans/2026-04-24-auto-apply-plan3a-websocket-backend.md), [3b AI preview](../plans/2026-04-24-auto-apply-plan3b-preview-ai.md), [3b execution](../plans/2026-04-28-auto-apply-plan3b-execution.md)

---

## 1. Goal

Wire the React frontend to the apply backend (`/api/apply/eligibility`, `/api/apply/preview`, `/api/apply/record`) so users can see which jobs are auto-applyable, view AI-generated answers per job, and hand-paste them into the real ATS form.

This is the first user-facing Phase 3c slice. It does **not** include cloud-browser supervision (Phase 2 of the apply UI work — separate plan, separate PR).

### Why "Smart Apply"

User-facing copy uses **"Smart Apply"** rather than "Auto-Apply". The naming sets honest expectations: AI helps, the user submits. This matches what the market-leader (Simplify Copilot) actually does — and avoids the Sonara/Massive failure pattern where unattended-submit promises produce 25-40% submission failures and account-restriction risk.

### North star

**Minimize user input over time, without losing answer quality or triggering anti-bot bans.** Phase 1 is the highest-input slice (manual copy-paste). Phase 2 reduces input to "review + click submit" via cloud-browser supervision.

---

## 2. Background

### Why this slice exists

A 2026-04-26 audit confirmed `web/src/` has zero references to `/api/apply/*`, `BROWSER_WS_URL`, `ws_token`, or `browser_session`. The full backend contract from PR #8 (Plan 3a) and PR #17 (Plan 3b) is talking to nothing. Phase 1 fixes that without committing to the larger cloud-browser flow.

### Why hand-paste before browser supervision

Phase 1 doubles as the **answer-quality gate** for Phase 2. If 3b's AI-generated answers don't match real ATS form fields when a user reads them side-by-side, no amount of cloud-browser polish helps. The PostHog drop-off metric — modal opens that never become mark-applied events — is the data point that drives the Phase 2 go/no-go decision.

### What's already built (and reused)

| Backend component | Plan | Reused by Phase 1 |
|---|---|---|
| `GET /api/apply/eligibility/{job_id}` | 3a | ✅ — defensive confirm before modal |
| `GET /api/apply/preview/{job_id}` | 3b | ✅ — populates the answers table |
| `POST /api/apply/record` | 3a | ✅ — Mark-applied action |
| Apply platform classifier | 3b | ✅ — drives `apply_platform` on each job |
| AI answer generator + fetchers (Greenhouse, Ashby) | 3b | ✅ |

WebSocket Lambdas + Fargate browser session + `start-session/stop-session` are **not used** by Phase 1.

### Backend dependency for Phase 1 (one small addition)

The current `ProfileResponse` in `app.py` exposes profile fields and `onboarding_completed_at` but does **not** expose a `profile_complete: bool` derived from `shared/profile_completeness.check_profile_completeness()`. Phase 1 requires this field added to `ProfileResponse` so the frontend has an authoritative completeness signal that doesn't drift from the per-job eligibility logic. ~5-line backend change, plus a contract test (covered in §6.2).

---

## 3. User journey

### Happy path — eligible Greenhouse job

1. Dashboard loads, `<JobTable>` renders rows. Each row shows `<EligibilityBadge>` (green / amber / grey) computed client-side from row data + the user profile fetched once and shared via `ProfileContext`.
2. User clicks a row → `JobWorkspace` page → `<AutoApplyButton>` shows state `eligible`, label *"Smart Apply"*.
3. User clicks button:
   - Defensive `GET /api/apply/eligibility/{job_id}` (catches client/server drift)
   - `<AutoApplyModal>` opens
   - `GET /api/apply/preview/{job_id}` populates the modal
4. Modal contents:
   - Header: `Smart Apply: {company} — {role}`
   - Resume + cover letter chips (download links to S3-presigned URLs)
   - Per-question table: `[Question | AI Answer | 📋 Copy]` for every `custom_questions` entry
   - Profile snapshot collapsible (work auth, salary, location)
   - Primary action: *"Open ATS in new tab"*
5. User clicks *"Open ATS in new tab"* → `window.open(apply_url, '_blank')` → primary button **swaps** to *"I submitted — mark applied"*.
6. User per-field copies into ATS in the other tab. Each `📋` triggers `navigator.clipboard.writeText()` + toast.
7. User submits on ATS, returns to NaukriBaba tab, clicks *"I submitted — mark applied"* → `POST /api/apply/record` → modal closes, row badge goes grey.

### Degraded paths

| Trigger | Behavior |
|---|---|
| Defensive eligibility check disagrees with client | Modal still opens with warning banner ("Eligibility changed — open ATS manually"); skips preview fetch; chips + Open ATS + Mark applied still work |
| Defensive eligibility call fails (network error / 5xx) | Permissive: open modal anyway with banner ("Couldn't confirm eligibility — proceeding offline-style"); proceed to preview fetch as normal. **Don't block the user on a flaky call.** |
| `eligible: true` but `custom_questions: []` (e.g., unsupported platform like HN Hiring with `apply_platform=null`, or supported platform whose form couldn't be parsed) | `<EmptyPreviewState>` replaces the table — chips and Open ATS still work; "Retry preview" link re-fetches |
| `POST /api/apply/record` fails | Modal stays open, error toast shown, optimistic row-status update reverted; user can retry |
| `profile_incomplete` (smart-button) | Click navigates to `/settings#profile` |
| `no_resume` (smart-button) | Click scrolls to existing `<TailorCard>` and focuses its primary button |
| `no_apply_url` (smart-button) | Click opens inline edit field (existing `PATCH /api/jobs/{id}`) |
| `already_applied` | Disabled button + "Applied ✓" status badge |

**Important:** The frontend does **not** maintain a `not_supported_platform` ineligibility state. The backend's `/api/apply/eligibility` endpoint never returns this reason — auto-apply is permissive about unknown platforms. Unsupported-platform UX is handled by the empty-`custom_questions` degraded path above (modal opens, chips + Open ATS still work).

---

## 4. Architecture

### File layout

```
web/src/
├── components/
│   ├── apply/                              ← new directory
│   │   ├── EligibilityBadge.jsx            ~40 LOC
│   │   ├── AutoApplyButton.jsx             ~120 LOC
│   │   ├── AutoApplyModal.jsx              ~150 LOC
│   │   ├── QuestionsTable.jsx              ~80 LOC
│   │   ├── ProfileSnapshot.jsx             ~50 LOC
│   │   ├── EmptyPreviewState.jsx           ~30 LOC
│   │   └── __tests__/                      one .test.jsx per component
│   └── (existing components untouched, except JobTable)
├── hooks/
│   ├── useApplyEligibility.js              ~50 LOC — pure compute fn + thin React wrapper
│   ├── useApplyPreview.js                  ~50 LOC — wraps GET /api/apply/preview
│   └── useUserProfile.js                   ~40 LOC — wraps GET /api/profile, exposes via ProfileContext
├── lib/
│   └── applyTelemetry.js                   ~50 LOC — 7 posthog.capture wrappers
└── pages/
    ├── JobWorkspace.jsx                    MODIFIED — embed <AutoApplyButton/>
    └── Dashboard.jsx                       MODIFIED — wrap children in <ProfileContext.Provider>

web/src/components/JobTable.jsx              MODIFIED — add <EligibilityBadge/> per row
web/src/layouts/AppLayout.jsx                MODIFIED — replace inline /api/profile call with useUserProfile() (eliminates the existing local heuristic that drifts from backend)
```

Backend:
```
app.py: ProfileResponse                      MODIFIED — add `profile_complete: bool` field
                                             (computed via shared.profile_completeness.check_profile_completeness)
```

### Component contracts

| Component | Props | Owns | Does not own |
|---|---|---|---|
| `<EligibilityBadge>` | `{eligible, reason, platform}` | Color, tooltip | No fetch, no state |
| `<AutoApplyButton>` | `{job, profile}` | Smart-button state machine, modal-open trigger, defensive eligibility call | Preview fetch (delegates to modal) |
| `<AutoApplyModal>` | `{job, isOpen, onClose}` | Preview fetch lifecycle, primary-button swap, optimistic record-applied + revert-on-failure | Eligibility logic |
| `<QuestionsTable>` | `{questions, onCopy}` | Per-row copy + toast UI | Telemetry call (delegates to parent) |
| `<ProfileSnapshot>` | `{snapshot}` | Collapse/expand state | Data fetching |
| `<EmptyPreviewState>` | `{onRetry}` | Retry button | Refetch logic (delegates) |

### Eligibility computation — client-side

Backend `/api/apply/eligibility/{job_id}` is **not called per row** (60+ rows = 60 fetches). Instead, eligibility is computed from data already on the job row (`apply_url`, `resume_s3_key`, `application_status`) plus the user profile fetched once via `useUserProfile()` and shared via `ProfileContext`.

```js
// hooks/useApplyEligibility.js
// Order matches app.py:2683-2734 server-side branches exactly so the two never
// disagree on which reason fires when multiple apply.
export function computeEligibility(job, profile) {
  if (job.application_status === 'applied') return { eligible: false, reason: 'already_applied' };
  if (!job.apply_url)                        return { eligible: false, reason: 'no_apply_url' };
  if (!job.resume_s3_key)                    return { eligible: false, reason: 'no_resume' };
  if (!profile.profile_complete)             return { eligible: false, reason: 'profile_incomplete' };
  return { eligible: true, platform: job.apply_platform || null };
}
```

The frontend mirrors the backend's reason-set exactly: `{already_applied, no_apply_url, no_resume, profile_incomplete}` for ineligible, plus `eligible: true`. The frontend does **not** invent additional reasons.

The defensive backend call happens once when the user clicks the button on `JobWorkspace` — not per row.

### Smart-button state machine

Five states, one per backend eligibility reason:

| Reason | Label | Action |
|---|---|---|
| `eligible: true` | *"Smart Apply"* | Open `<AutoApplyModal>` |
| `profile_incomplete` | *"Complete profile to apply"* | Navigate to `/settings#profile` |
| `no_resume` | *"Generate tailored resume first"* | Scroll to existing `<TailorCard>`, focus primary button |
| `no_apply_url` | *"Add apply URL"* | Open inline edit field (existing `PATCH /api/jobs/{id}`) |
| `already_applied` | *"Applied ✓"* | Disabled |

---

## 5. Telemetry — PostHog events

Seven events captured via `lib/applyTelemetry.js` wrappers around `posthog.capture()`:

| Event | When | Properties |
|---|---|---|
| `apply_eligibility_viewed` | Once per dashboard load | `{total_jobs, eligible, by_reason: {...}}` |
| `apply_modal_opened` | Modal opens | `{job_id, platform, reason}` |
| `apply_field_copied` | Per-field 📋 click | `{job_id, field_name}` |
| `apply_ats_opened` | Open ATS click | `{job_id, platform}` |
| `apply_marked_applied` | Mark-applied success | `{job_id, platform, ats_was_opened: bool}` |
| `apply_modal_dismissed` | Modal closed without marking applied | `{job_id, platform, ats_was_opened: bool}` |
| `apply_ineligible_action_taken` | Smart-button click for non-eligible state | `{job_id, reason}` |

Telemetry is colocated with user actions (button click handlers) — never inside fetch chains.

---

## 6. Testing strategy

### 6.1 Unit tests (Vitest, ~24 new tests)

```
EligibilityBadge.test.jsx          — color + tooltip per reason (5 tests)
useApplyEligibility.test.js        — computeEligibility() truth table (10 tests)
AutoApplyButton.test.jsx           — smart-button state × action mapping (5 tests, one per state)
QuestionsTable.test.jsx            — copy-button writes correct value to clipboard (1 test)
ProfileSnapshot.test.jsx           — collapse/expand state (1 test)
EmptyPreviewState.test.jsx         — retry triggers re-fetch (1 test)
applyTelemetry.test.js             — each capture() called with right event + props (1 test)
```

### 6.2 Backend contract tests (pytest, ~3 new tests)

Same pattern as PR #44's AddJob payload contract test. Closes the drift class:

```python
def test_eligibility_reasons_match_frontend():
    """Backend's eligibility branches in app.py == reasons enum in
    useApplyEligibility.js. Backend has 4 ineligibility reasons + eligible:true;
    frontend's reason set must equal that exactly (no inventing new reasons).
    """

def test_apply_preview_response_shape():
    """/api/apply/preview returns the exact keys the modal expects:
    resume_s3_url, cover_letter_s3_url, custom_questions[{question, answer, source}],
    profile_snapshot, eligible, reason, platform, apply_url.
    """

def test_profile_response_includes_profile_complete():
    """ProfileResponse exposes profile_complete: bool computed via
    check_profile_completeness(). Pin this so the field can't be silently dropped.
    """
```

### 6.3 Answer-quality verification (NEW — added per design review)

**Layer A — programmatic floor checks (CI):** illustrative pseudocode (real test goes in `tests/test_answer_quality_floor.py`):

```python
# Pseudocode — concrete test must be parameterized over real fixture jobs
async def test_answer_quality_floor():
    for job_id in FIXTURE_JOB_IDS:                 # 5 jobs
        preview = await get_apply_preview(job_id)
        for q in preview.custom_questions:
            assert len(q.answer) > 20                              # not blank/truncated
            assert "[" not in q.answer and "TODO" not in q.answer  # no placeholder leakage
            assert q.answer.lower() != q.question.lower()          # not echoed
        # at least one profile fact (skill, target role, or candidate_context excerpt)
        # appears across the answer set — checks the AI is using profile data, not
        # generating generic responses
        assert profile_facts_present(preview.custom_questions, profile)
```

The `profile_facts_present` helper checks that at least one of the candidate's listed skills, target roles, or short candidate-context phrases appears in the combined answer text. This is meaningfully softer than checking for the candidate's name (which rarely appears in real answers) and catches the failure mode where AI produces generic "I am excited about this opportunity" text with no personalization.

**Layer B — golden fixture comparison (CI on prompt changes):**

- 5 hand-curated jobs (3 Greenhouse + 2 Ashby) with hand-written ideal answers stored as JSON fixtures
- CI step re-runs `/api/apply/preview` and compares each free-text answer to the ideal:
  - Cosine similarity >0.6 → pass
  - 0.4–0.6 → warning (PR comment, not blocking)
  - <0.4 → fail (prompt regression)
- ~3 hours to build the fixture set; ~50 LOC pytest + similarity helper

**Layer C — AI-judge (manual sample, scriptable):**

```
"Score this answer against the JD on:
 - relevance, personalization, tone, length appropriateness
 Output: {scores, verdict: good|mediocre|bad, issues: [...]}"
```

Run on a sample of 20 jobs once per PR. Manual in Phase 1; automatable later. Critical: judge must be a **different model** than the answer generator.

**Layer D — behavioral signal (post-launch, via PostHog):**

Drop-off rate `apply_modal_dismissed / apply_modal_opened`. Threshold: >30% drop-off → AI answers aren't usable; revisit prompt before Phase 2.

### 6.4 Live smoke checklist

`docs/qa/phase3c0-smoke.md` — pre-merge checklist run against staging (Netlify preview from PR #41):

```
□ Dashboard loads — eligibility dots visible per row, distribution sane
□ Open eligible Greenhouse job → JobWorkspace shows "Smart Apply" enabled
□ Click "Smart Apply" → modal renders with chips + 8+ questions + collapsible profile
□ Click 📋 on a question → toast "Copied"
□ Click "Open ATS in new tab" → real Greenhouse URL opens in new tab
□ Primary button swaps to "I submitted — mark applied"
□ Click "Mark applied" → modal closes, row badge goes grey
□ Reload — application_status persists as 'applied'
□ Open ineligible-no_resume job → button shows "Generate tailored resume first"
□ Open eligible HN Hiring job (apply_platform=null) → modal opens, EmptyPreviewState shows + chips + Open ATS still work
□ Force a record-applied failure (DevTools network blocking) → modal stays open, error toast appears, row status reverts
□ PostHog dashboard shows the 7 events with correct properties
```

---

## 7. Scope

### In scope (this PR)

- `<EligibilityBadge>` on every JobTable row
- `<AutoApplyButton>` on JobWorkspace with all 5 smart-button states
- `<AutoApplyModal>` with chips, per-field copy, profile snapshot, primary-button swap, error handling for `/api/apply/record` failure
- `<EmptyPreviewState>` retry path
- `useApplyEligibility` + `useApplyPreview` + `useUserProfile` hooks (last one shared via `ProfileContext`)
- Backend: `profile_complete: bool` added to `ProfileResponse`
- AppLayout refactored to use `useUserProfile()` (replacing its current local heuristic that drifts from backend)
- 7 PostHog telemetry events
- 3 backend contract tests + ~24 frontend unit tests
- Layer A + Layer B answer-quality CI (programmatic floor + golden fixture)
- Manual Layer C judge run + Layer D telemetry threshold doc
- Smoke checklist

### Out of scope (deferred to Phase 2 / its own PR)

- Cloud-browser session: `start-session`, `stop-session`, WS connection, Fargate launch, screenshot streaming, `<BrowserSessionView>` — untouched in Phase 1
- Settings tile for "auto-apply preferences"
- Mobile / responsive design
- Optimistic UI animations beyond simple status flip
- Workday / Lever / iCIMS / etc. — Phase 1+ backend work (classifier already recognizes them; backend question-extraction does not)
- AI-judge automation (manual-only in Phase 1)
- Cross-job session conflict UI (no sessions in Phase 1)

### Captured-as-followup

- **Phase 2 plan refresh** — execute the original cloud-browser plan, informed by Phase 1 telemetry data (drop-off rate from Layer D)
- **Workday + Lever question-extraction** — backend extension to fetcher beyond Greenhouse/Ashby
- **AI-judge automation** — wire the manual Layer C check into CI

---

## 8. Risks & assumptions

| Risk | Mitigation |
|---|---|
| Backend eligibility logic drifts from client-side `computeEligibility()` | Contract test 6.2 #1 enforces enum parity (4 backend reasons must equal frontend's 4) |
| `/api/apply/preview` response shape changes silently | Contract test 6.2 #2 pins keys |
| `profile_complete` field gets dropped from `ProfileResponse` | Contract test 6.2 #3 pins the field |
| AI answers are bad enough that users dismiss the modal without using them | Layer A floor checks (CI) + Layer D drop-off telemetry (post-launch) — drop-off >30% blocks Phase 2 |
| Greenhouse / Ashby change form structure → questions don't match real fields | Surfaced by Layer D telemetry; manually patched per platform |
| PostHog initialization fails (per the recent prod-500 from PR #40) | Already mitigated by lifespan smoke (PR #43); telemetry calls are no-ops when SDK isn't ready |
| User pastes wrong answer into ATS (human error) | Out of scope — UX cannot prevent this |
| Defensive eligibility call fails on flaky network → blocks user | Permissive degraded path (open modal anyway with banner) |
| AppLayout refactor breaks existing `FinishSetupBanner` trigger | Covered by AppLayout integration tests; new `useUserProfile` hook returns same shape |

### Assumptions explicitly relied on

- Backend (`/api/apply/eligibility`, `/api/apply/preview`, `/api/apply/record`) is healthy on prod (verified per Apr 29 live smoke)
- `apply_platform` column is populated for Greenhouse + Ashby jobs (verified by classifier; backfilled per Apr 29 manual action)
- `apply_platform` enum values are lowercase strings: `greenhouse`, `lever`, `workday`, `ashby`, `smartrecruiters`, `workable`, `taleo`, `icims`, `personio`, `linkedin_easy_apply` (per `shared/apply_platform.py`)
- `resume_s3_key` is populated on S/A/B-tier jobs (verified by Apr 30 backfill — 60/60 S+A jobs have artifacts)
- PostHog SDK is initialized in the frontend (per PR #40)
- `shared/profile_completeness.check_profile_completeness()` is the authoritative completeness function — exposing its output via `ProfileResponse` is the only backend change needed

---

## 9. Estimated size

- Frontend: ~660 LOC across 6 components + 3 hooks + 1 lib (apply directory)
- Frontend modifications: ~150 LOC across `JobTable.jsx`, `JobWorkspace.jsx`, `Dashboard.jsx`, `AppLayout.jsx`
- Backend: ~10 LOC for `profile_complete` field + import wiring
- Tests: ~24 frontend unit tests + 3 backend contract tests + 5 fixture files (Layer B) + 1 floor test (Layer A)
- Smoke checklist + Layer C prompt template
- Estimated 2-3 focused sessions (~1.5 days)

This is bigger than the stub plan's 4-6 hour estimate because the answer-quality CI (Layer A + B), the contract tests, the new `useUserProfile` hook + `ProfileContext` plumbing, and the small backend change weren't in the stub. They're worth the extra day.

---

## 10. Phase 2 direction (preserved as captured)

Phase 2 = the original cloud-browser plan ([Plan 2 browser session](../plans/2026-04-20-auto-apply-plan2-browser-session.md), [Plan 3a WS](../plans/2026-04-24-auto-apply-plan3a-websocket-backend.md)). Frontend work for Phase 2: `<BrowserSessionView>`, WS lifecycle hooks, screenshot stream pane, session conflict UI.

Phase 1 telemetry data feeds the Phase 2 go/no-go: Layer D drop-off rate <30% → AI answers are good enough for the supervised cloud-browser flow; >30% → revisit prompt before investing in Phase 2 frontend.

---

## 11. References

- [Apply platform classifier spec](2026-04-26-apply-platform-classifier.md)
- [Plan 3a — WebSocket backend](../plans/2026-04-24-auto-apply-plan3a-websocket-backend.md)
- [Plan 3b — Preview AI](../plans/2026-04-24-auto-apply-plan3b-preview-ai.md)
- [Plan 3b execution](../plans/2026-04-28-auto-apply-plan3b-execution.md)
- [Plan 3c stub (superseded by this spec for Phase 1)](../plans/2026-04-26-auto-apply-plan3c-frontend-ui.md)
- [Cloud-browser design](2026-04-12-auto-apply-cloud-browser-design.md)
- Backend endpoints: `app.py:2683-3170` (eligibility, preview, start-session, stop-session, record)
- `shared/apply_platform.py` — classifier (10 platform values)
- `shared/profile_completeness.py` — REQUIRED_FIELDS list
