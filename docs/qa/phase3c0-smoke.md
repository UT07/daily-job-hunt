# Smart Apply Phase 1 — Pre-merge Smoke Checklist

Run against the Netlify staging preview from this PR (PR #41 wired Netlify
deploy previews). Report results in the PR description.

## Repo setup (one-time, before first smoke)

Layer A floor + Layer B golden tests skip silently without these GitHub Actions secrets configured. **Configure them in the repo's Actions secrets before relying on the `Apply Answer Quality (REPORT)` job's signal.**

| Secret | Purpose | How to get |
|---|---|---|
| `FLOOR_TEST_JOB_IDS` | Comma-separated list of staging-API job IDs to gate (~5 jobs) | Pick 5 representative jobs from staging dashboard; copy their `job_id`s |
| `FLOOR_TEST_TOKEN` | Bearer token for staging API auth | Sign into staging as the smoke user, copy session JWT |

Without these, the `Apply Answer Quality (REPORT)` CI job reports green forever and provides zero signal — the gate is dormant. **The job passing is necessary but not sufficient — the secrets must be configured for the gate to actually fire.**

To verify the gates are live: trigger a manual workflow run after configuring secrets, then check the job summary for `N tests ran, M skipped` lines.

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
- [ ] Click — modal opens; Network tab shows GET `/api/apply/preview/{id}`
- [ ] Modal shows: header with company + role, Resume + Cover Letter chips, question table with 1+ rows, profile snapshot collapsible
- [ ] Click the copy icon on one row → toast (or browser alert) confirms; paste into a text editor — the AI answer is on the clipboard
- [ ] Click "Open ATS in new tab" → real Greenhouse URL opens
- [ ] Back on NaukriBaba, primary button now says "I submitted — mark applied"
- [ ] Click "I submitted — mark applied" → modal closes; Network tab shows POST `/api/apply/record`
- [ ] Reload the JobWorkspace page → status badge shows `"Applied"` (Title-cased; backend `app.py:3189` writes `application_status="Applied"`)
- [ ] Reload Dashboard → status pill on the row reads `"Applied"`
  - **Known case mismatch (follow-up):** `useApplyEligibility.js:6` compares `application_status === 'applied'` (lowercase) but backend writes `"Applied"`. Until that's reconciled, the EligibilityBadge dot may stay green/amber instead of going grey after a successful record. Treat the status pill (which renders the raw value) as the authoritative signal for this smoke step.

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
- [ ] Confirm 6 distinct event names captured: `apply_modal_opened`, `apply_field_copied`, `apply_ats_opened`, `apply_marked_applied`, `apply_modal_dismissed`, `apply_ineligible_action_taken`
- [ ] Each event has the documented properties (job_id, platform, etc.)

## Done
- [ ] Paste this completed checklist into the PR description
- [ ] Flip PR from draft to ready-for-review
