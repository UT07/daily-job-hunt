# Auto-Apply Plan 3c — Frontend UI (Stub)

> Stub plan. Per-task TDD steps will be written when this enters execution.
> Created 2026-04-26 to capture frontend scope deferred from the apply-platform-classifier spec.

**Goal:** Wire up the React frontend so users can actually trigger the auto-apply flow that PR #8 shipped on the backend (HTTP + WebSocket).

**Why a separate plan:** A 2026-04-26 audit showed `web/src/` has zero references to `/api/apply/*`, `BROWSER_WS_URL`, `ws_token`, or `browser_session`. The entire backend contract from PR #8 is talking to nothing. Building this UI is a substantial chunk (~4-6 hr, multiple components) and should not be bundled into the small classifier PR.

## Dependencies

- **Prerequisite:** Apply Platform Classifier ships (spec [2026-04-26-apply-platform-classifier-design.md](../specs/2026-04-26-apply-platform-classifier-design.md)) — without it, every job is `not_supported_platform`-ineligible and the UI has nothing to do
- **Optional but useful:** Plan 3b (AI preview) — without it, the preview modal will show empty `questions`/`answers` arrays. UI can ship around an empty preview, but the experience is leaner with 3b done.

## Expected tasks

1. **`useAutoApplyEligibility(jobId)` hook** — wraps `GET /api/apply/eligibility/{job_id}`. Returns `{eligible, reason, platform, isLoading}`. SWR-style cache. Used to enable/disable the Apply button per job.

2. **`<AutoApplyButton>` component** — renders on each row in `web/src/components/JobTable.jsx` (and on `JobWorkspace.jsx`'s job header). Disabled with tooltip when `eligible: false`. Shows the reason: `"Apply via auto-fill"` (eligible) | `"Profile incomplete — finish in Settings"` (`profile_incomplete`) | `"No tailored resume yet"` (`no_resume`) | `"Already applied"` (`already_applied`).

3. **`<AutoApplyModal>` component** — opens on button click. Two-pane layout:
   - Left: preview snapshot (`GET /api/apply/preview/{job_id}` → `questions`/`answers` table). Empty state if Plan 3b hasn't shipped: "Preview not yet available — proceed to launch session anyway?"
   - Right: live screenshot stream from the WS session.
   - Bottom: `Confirm & Submit` / `Cancel` actions. `Confirm` calls `POST /api/apply/record` with `accepted_at`.

4. **`<BrowserSessionView>` component** — manages the WS lifecycle:
   - On mount: `POST /api/apply/start-session` → receives `{session_id, ws_url, ws_token}`
   - Opens WS to `ws_url?session={session_id}&role=frontend` with `Authorization: Bearer ws_token` header
   - Renders incoming `screenshot` frames as `<img src="data:image/png;base64,...">` with smooth transitions
   - Reverse channel: `mouse_click`, `keyboard_input`, `text_input` events for user-overrides
   - On unmount or modal close: `POST /api/apply/stop-session`
   - Reconnect with backoff if WS drops mid-session

5. **`AutoApplyContext` provider** — global state for the active session (so `<AutoApplyModal>` and `<JobTable>` can both reflect "this user has an active session for job X — clicking Apply on job Y should prompt to stop the existing one first" — backed by the 409 `session_active_for_different_job` response from `start-session`).

6. **Settings tile: "Auto-apply preferences"** — small UX surface in `web/src/pages/Settings.jsx` for things like default cover letter inclusion, max captcha-solve cost cap, "always show preview" vs. "auto-submit on confirm". Optional. Could be deferred to its own micro-PR.

7. **Telemetry / observability hooks** — capture session start, WS reconnects, captcha solves, submit success/failure into PostHog (if wired) or stdout for now. Optional but valuable for diagnosing the long tail of "auto-apply got stuck" failures.

## Design considerations

- **Empty preview state**: Plan 3b populates `questions`/`answers`. Until then, modal shows "Preview not yet available — you can still launch the session and watch it run." The UX should not block on 3b.
- **Streaming bandwidth**: screenshots arrive at ~1-2 Hz from the Fargate container per Plan 2's `_screenshot_loop`. Component must throttle React renders, not naively re-render on every frame.
- **Modal-vs-page tradeoff**: a modal is fine for first iteration, but if users want to background the auto-apply while continuing dashboard work, we may want a dedicated `/apply/{session_id}` route later. Defer that decision.
- **Mobile**: out of scope for v1 (desktop-only is fine — auto-apply is supervision-heavy).

## Estimated size

~4-6 hours of frontend work. ~500-800 LOC across 5-6 new components/hooks plus modifications to `JobTable`, `JobWorkspace`, `Dashboard`. Should be one focused session.

## References

- Backend contract: [2026-04-24-auto-apply-plan3a-websocket-backend.md](2026-04-24-auto-apply-plan3a-websocket-backend.md)
- Cloud-browser design: [2026-04-12-auto-apply-cloud-browser-design.md](../specs/2026-04-12-auto-apply-cloud-browser-design.md) §8 (UX flow)
- Browser session client (the Python equivalent we're building a JS twin of): `browser/browser_session.py`
- Master grand plan: [2026-04-03-unified-grand-plan.md](../specs/2026-04-03-unified-grand-plan.md) Stage 3.4 Apply
