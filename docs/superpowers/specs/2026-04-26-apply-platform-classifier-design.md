# Apply Platform Classifier — Design Spec

**Date:** 2026-04-26
**Status:** Approved, ready for implementation plan
**Stage:** 3.4 Apply (master grand plan)
**Predecessor:** PR #8 (Plan 3a — WebSocket Lambdas + apply endpoints)
**Successors:** Plan 3b (AI preview), Plan 3c (frontend auto-apply UI)

## Context

PR #8 shipped 5 `/api/apply/*` endpoints that read `jobs.apply_platform` and gate on it. A direct Supabase audit on 2026-04-26 confirmed:

- **0 / 832** jobs have `apply_platform` set
- **831 / 832** have an `apply_url`
- The literal column is only ever written in test fixtures (`tests/unit/test_apply_endpoints.py`, `tests/contract/test_apply_happy_path.py`) — no production writer exists anywhere

Consequence: every auto-apply endpoint shipped in Plan 3a is structurally dead in prod. The eligibility check `if not job.get("apply_platform")` returns `not_supported_platform` for 100% of jobs.

## Goal

Make the cloud-browser auto-apply flow usable end-to-end on the backend by:

1. Removing the `apply_platform` gate that blocks every job
2. Building a small, pure URL → platform classifier that tags recognized ATS platforms (informational, not gating)
3. Backfilling existing jobs and wiring the classifier into the scrape pipeline so new jobs are tagged on insert

After this ships, the user's auto-apply experience is gated only by `resume_s3_key` (which the existing tailoring pipeline writes for S/A/B-tier jobs only — providing a natural ≤B-tier guardrail per product decision on 2026-04-26) and the standard `already_applied` / `profile_completeness` checks.

## Non-Goals (delegated)

| Out of scope | Where it lives |
|---|---|
| Frontend UI for auto-apply (Apply button, modal, WS client, screenshot stream, status states) | **New plan stub: [Plan 3c — Frontend Auto-Apply UI](../plans/2026-04-26-auto-apply-plan3c-frontend-ui.md)** |
| Extracting `apply_board_token` / `apply_posting_id` from URLs (e.g., `boards.greenhouse.io/{board}/jobs/{posting_id}`) | **[Plan 3b — AI Preview](../plans/2026-04-24-auto-apply-plan3b-preview-ai.md)** — added as a prereq step (the platform metadata fetchers in 3b can't call greenhouse/ashby APIs without these IDs) |
| AI fallback classification for unmatched URLs | Indefinitely deferred. Classifier returns `None` for unknowns; auto-apply still works because `apply_platform` is no longer gating. Revisit only if `unknown` rate proves noisy in 3b. |
| `easy_apply_eligible` column population | Out of scope. Still NULL after this ships. Plan 3c can address if/when LinkedIn easy-apply is supported. |

## Design

### Architecture

One pure function plus three integration touch-points. The classifier itself has no dependencies (no DB, no AI, no HTTP). It can be unit-tested without mocks.

```
apply_url ──► classify_apply_platform() ──► str | None
                       │
       ┌───────────────┼───────────────┐
       │               │               │
   Backfill        Scraper          (future: tests)
   script         base.Job
                  constructor
```

### Components

| Path | Purpose | LOC |
|---|---|---|
| `shared/apply_platform.py` (new) | `classify_apply_platform(url: str) -> str \| None` — pure regex dispatch | ~40 |
| `app.py` (modify lines 2418, 2472) | Replace `if not apply_platform` gate with `if not apply_url`. Add comment noting that `resume_s3_key` is the implicit ≤B-tier filter. | -6 / +8 |
| `scrapers/base.py` (modify) | Call classifier in the `Job` dataclass `__post_init__` (or equivalent insert hook), so all 7 scrapers benefit without per-scraper changes | ~5 |
| `scripts/backfill_apply_platform.py` (new) | One-shot: page through `jobs WHERE apply_platform IS NULL`, classify, batch-update via Supabase service role | ~50 |
| `tests/unit/test_apply_platform.py` (new) | One test per platform pattern (10) + one for `None` (unknown URL) | ~60 |
| `tests/unit/test_apply_endpoints.py` (modify) | Update fixtures + expectations to reflect `apply_url`-based gate | ~10 lines changed |

### Platforms (Standard tier — 10)

Each entry is `(platform_name, url_substring_or_regex)`. Order matters when patterns overlap (e.g., LinkedIn before generic).

```
greenhouse              boards.greenhouse.io
lever                   jobs.lever.co
workday                 myworkdayjobs.com OR *.wd*.myworkday*.com
ashby                   jobs.ashbyhq.com
smartrecruiters         jobs.smartrecruiters.com
workable                apply.workable.com
taleo                   *.taleo.net
icims                   *.icims.com
personio                *.jobs.personio.com (EU/IE prevalent)
linkedin_easy_apply     linkedin.com/jobs/view/* AND ?easy_apply=true OR /jobs/collections/easy-apply/
```

Unmatched URLs (portal listings like `jobs.ie/...`, custom company pages, Indeed deep-links, raw LinkedIn without easy-apply marker) → return `None`. Auto-apply still works on these because the gate is now `apply_url`-based.

### Data flow

**New job (live):**
```
scraper.scrape() → Job(apply_url="boards.greenhouse.io/foo/jobs/123")
  → Job.__post_init__ calls classify_apply_platform()
  → apply_platform="greenhouse" persisted with row
```

**Existing job (one-shot):**
```
scripts/backfill_apply_platform.py
  → SELECT job_id, apply_url FROM jobs WHERE apply_platform IS NULL (831 rows)
  → for each: classify locally
  → batch UPDATE in chunks of 100 via Supabase upsert
  → log final counts per platform + unmatched
```

**Eligibility request (post-change):**
```
GET /api/apply/eligibility/{job_id}
  → load_job(...)
  → if not apply_url: 400  (was: if not apply_platform)
  → if not resume_s3_key: not_eligible (implicit ≤B-tier filter)
  → if already_applied: not_eligible
  → if profile_incomplete: not_eligible
  → return {eligible: true, platform: <str | null>, board_token: null, posting_id: null}
```

`platform`, `board_token`, `posting_id` may all be `null` in the response — that's expected and fine. Frontend treats `platform=null` as "we don't know which ATS, browser will improvise."

### Error handling

- `classify_apply_platform` never raises. On any input (bad URL, empty, None) it returns `None`.
- Backfill is idempotent: only touches rows where `apply_platform IS NULL`. Re-runnable.
- Backfill writes `null` (not `'unknown'`) for unmatched URLs. Distinguishes "never classified" from "classified as unknown" — useful if we later add AI fallback that wants to retry only never-classified rows.

### Testing

| Layer | Coverage |
|---|---|
| Unit (new) | 10 platforms × 1 matching URL + 1 unmatched URL = 11 cases |
| Unit (modified) | `test_apply_endpoints.py` — flip eligibility expectation: `apply_platform=None` no longer returns `not_supported_platform`; instead returns eligible if other gates pass |
| Contract | `test_apply_happy_path.py` — verify response shape unchanged (just allow null `platform`) |
| Manual smoke | After deploy: re-run `start-session` against a real S/A/B job; expect `eligible: true` for the first time |

### Rollout

1. Land PR (classifier + gate flip + scraper integration + tests)
2. Deploy via `gh workflow run deploy.yml --ref main`
3. Run backfill script locally with prod Supabase service key
4. Manual smoke test: hit `/api/apply/eligibility/{any S-tier job_id}` — expect `eligible: true`

No rollback complexity:
- Gate change is a one-line revert
- Backfill writes are additive (NULL → string); reverting would just leave classified data in place (no harm, still informational)

### Defensive notes (added inline in code)

- `app.py:2418` comment: `# resume_s3_key is the implicit ≤B-tier gate — pipeline only writes it when tailoring runs, which is restricted to S/A/B per pipeline policy. Do not remove without re-instating an explicit tier filter.`
- `shared/apply_platform.py` module docstring: lists the 10 supported platforms and explicitly marks the function as `informational only — never raise, never gate behavior`.

## References

- Master grand plan: [2026-04-03-unified-grand-plan.md](2026-04-03-unified-grand-plan.md) Stage 3.4 Apply
- Predecessor (consumer code): [2026-04-24-auto-apply-plan3a-websocket-backend.md](../plans/2026-04-24-auto-apply-plan3a-websocket-backend.md)
- Successor (AI preview, depends on board_token/posting_id which we'll add to 3b's task list): [2026-04-24-auto-apply-plan3b-preview-ai.md](../plans/2026-04-24-auto-apply-plan3b-preview-ai.md)
- Successor (frontend UI): [2026-04-26-auto-apply-plan3c-frontend-ui.md](../plans/2026-04-26-auto-apply-plan3c-frontend-ui.md) (stub created with this spec)
- Cloud-browser design where the column was first introduced: [2026-04-12-auto-apply-cloud-browser-design.md](2026-04-12-auto-apply-cloud-browser-design.md) §6
