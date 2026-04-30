# NaukriBaba — Unified Grand Plan

**Date**: 2026-04-03 (last updated 2026-04-30)
**Status**: Approved · Layer 1 + Layer 2 (Reliability) + Layer 3 (Deploy) ✅ complete · Layer 2.5 (Stabilization & Deploy Safety) IN PROGRESS · Layer 4 (Product Features) partial — auto-apply backend live, frontend pending
**Supersedes**: Individual phase numbering from v2 design spec (2A-2G)
**Integration**: career-ops (github.com/santifer/career-ops) — adopted as reference architecture
**Active operational sequence**: see `~/.claude/projects/-Users-ut-code-naukribaba/memory/grand_plan_2026_04_30.md` (this doc owns the architectural narrative; the memory doc owns the current sprint's Phase A→B→C tasks).

---

## Status Snapshot — 2026-04-30

### Production state

- **Backend**: Live in `eu-west-1` since 2026-04-21. SAM deploys via `deploy.yml` from `main` after Deploy Readiness gate. Daily pipeline runs weekdays 07:00 UTC via EventBridge → Step Functions.
- **Frontend**: Live on Netlify, auto-deployed from `main`.
- **Database**: Supabase prod with RLS; ~850 jobs across all sources; auto-apply tables in place.
- **Auto-apply backend**: Cloud-browser pipeline shipped through Plan 3b (PR #17). Plan 3c frontend not started.

### Done since the last snapshot (2026-04-06 → 2026-04-30)

**Auto-apply / cloud-browser pipeline (Phase 3.4 sub-plans):**

- PR #5 (Apr 21) — CFN EventBridge ARN fix that unblocked the deploy workflow
- PR #7 (Apr 22) — **Plan 2 browser session** + Fargate task def
- PR #8 (Apr 24) — **Plan 3a WebSocket + apply endpoints** (3 WS Lambdas, 5 `/api/apply/*` endpoints, idempotent record)
- PRs #10/11/12 (Apr 27) — **Apply platform classifier** + Deploy Readiness CI gate + `Dockerfile.lambda` `shared/`/`lambdas/` COPY fix; eligibility gate flipped from `apply_platform` to `apply_url`; 831 jobs backfilled; live eligibility >0 for the first time
- PR #17 (Apr 29) — **Plan 3b backend**: AI preview, platform metadata fetchers (Greenhouse/Ashby), question classifier
- PR #21, #22 (Apr 29) — Plan 3b hotfixes (`lambdas/` COPY, JSONB string handling in `get_preview_cache`)

**Backlog clearances:**

- PR #23 (Apr 29) — Lazy boto3 SSM client in `ai_helper`, drops AWS_DEFAULT_REGION import-time dependency
- PR #24 (Apr 29) — Geography + work-auth aware score cap; demotes wrongly-S-tier UK/US-visa-required jobs

**Deploy-safety + prod-health initiative (parallel session):**

- PR #14 (Apr 28) — **Deployment-safety roadmap**: master spec + 6 phase sub-plans (canary, staging, observability, auto-rollback)
- PR #16 (Apr 29) — Bug 1: pipeline status ARN reconstruction (kills "Poll failed: HTTP 404")
- PR #18 (Apr 29) — Bug 3+4: JD location plumbing through 5 request models + `_Job`
- PR #19 (Apr 29) — Bug 2: rename "Score Resume" → "Save & Score"
- PR #20 (Apr 29) — Bug 5+6: SFN `job_hash` plumbing + `score_batch` prompt fields
- PR #25 (in review, Apr 29) — Postmortem of Apr 22-29 prod-health incident (doc-only)

### Findings still open (priority order)

1. **🔴 P0 SECURITY — AWS STS tokens leaked into `pipeline_tasks.error`** via `str(e)` flattening of boto3 `ClientError`; tokens were user-visible via `GET /api/tasks/{id}` and rendered as resume content. Code fix (creds sanitizer F1) is in `fix/comprehensive-prod-health/artifact-pipeline` branch. **Operator action required**: rotate Lambda execution-role creds + audit CloudTrail since 2026-04-22.
2. **🔴 EventBridge cron `Input` fix** — `template.yaml` daily cron uses `Input: {"user_id":"default"}`. New jobs land under synthetic user; real user never sees them. Root cause of "no artifacts since Apr 22." **Operator + template.yaml fix.**
3. **🔴 Bug X1 — silent `compile_latex` failures**: returns error dict (no `pdf_s3_key`) on tectonic failure → `save_job` silently sets `application_status="scored"` with no `resume_s3_url`. Pairs with Bug X2 (header-marker validation falling back to base resume) for "regenerate produces same resume." **Code fix not yet shipped.**
4. **8 cross-cutting bug patterns** documented in Session B's audit (`docs/audit/2026-04-29-deep-pass-2.md`, 45 findings: 11 P0, 25 P1):
   1. Pydantic `extra='ignore'` field-strip-on-undeclared
   2. `str(e)` AWS error body leakage into user-visible fields
   3. Frontend↔backend route drift (e.g. `ResumeEditor.jsx` typo'd `/api/resume/upload-pdf`)
   4. Silent UI error swallows (`.catch(err => console.error)` 12+ sites)
   5. Lambda↔local code drift (Lambda `tailor_resume` lacks guards from local `tailorer`)
   6. Hardcoded user info ("Utkarsh / Stamp 1G / 254utkarsh@gmail.com" in 3+ places — multi-tenant blockers)
   7. EventBridge↔SFN input-contract drift
   8. Missing IAM policies (`ecs:RunTask`, `ecs:StopTask`, `iam:PassRole`)
5. **Infrastructure follow-ups**: `JobHuntApi` IAM, `WsDisconnect` `ecs:StopTask` (Fargate task leak / cost runaway), `resume_versions` `UNIQUE(user_id, job_id, version_number)`, WS auth token TTL 60s → 5min.

### Active sequence

Layer 2.5 Phase A (Stabilization) → Layer 2.5 Phase B (Deploy Safety, B.1–B.7) → Layer 4 / Plan 3c (Frontend Auto-Apply UI). See **Layer 2.5** below for the architectural form; see `memory/grand_plan_2026_04_30.md` for tactical task breakdown.

---

## Why This Document Exists

Multiple overlapping specs accumulated with inconsistent phase numbering:

- v2 design spec (2026-03-30) defined phases 2A–2G
- Testing spec (2026-03-31) defined 7 QA tiers
- Playwright migration spec (2026-04-01) defined Phase 2.5
- Quality pipeline spec (2026-04-03) defined Phases 2.6–2.9
- Cloud-browser auto-apply spec (2026-04-12) added Plan 2/3a/3b/3c
- Deployment-safety spec (2026-04-28, PR #14) added Phases B.1–B.7

Actual work diverged from the original 2A–2G plan because the pipeline needed reliability fixes before features made sense, then a deploy-safety net before more product features could land safely. This document is the single source of truth for what's done, what's next, and how it all connects.

---

## Architecture

```
React Frontend (Netlify)
       │ REST + WebSocket
AWS Step Functions (orchestration)
       │
Lambda Functions (compute) ──── Fargate (cloud-browser auto-apply)
       │
Supabase PostgreSQL + S3 Storage
```

- No n8n. Step Functions orchestrates the daily pipeline.
- Lambda for all compute (scrapers, AI, compilation).
- Fargate hosts the cloud-browser auto-apply task; was originally scoped for Glassdoor scraping (deprioritized).
- `main.py` for local development runs.
- GitHub Actions for CI/CD; SAM-based deploy via `deploy.yml`.

---

## Grand Phase Structure

### Layer 1: Foundation — ✅ COMPLETE

| Phase | Name | Status | What Was Built |
|-------|------|--------|----------------|
| 1.0 | Core Pipeline | ✅ Done | Scrapers, AI matching, LaTeX PDFs, multi-provider failover, SQLite cache |
| 2.0 | Landing Page | ✅ Done | FastAPI backend, React frontend, SAM template, GCP/Drive integration |
| 2.5 | Web Unlocker | ✅ Done | LinkedIn, Indeed, Irish portals via Bright Data Web Unlocker on Lambda |

---

### Layer 2: Reliability — ✅ COMPLETE (2026-04-29)

| Sub-phase | What shipped | When |
|-----------|--------------|------|
| 2.5b Scraper fixes | OpenRouter free models (5 verified), AI council expanded 18 → 32 providers, IrishJobs JSON-LD descriptions, Groq IP-block workaround. GradIreland fix deferred. Glassdoor deprioritized (Greenhouse/Ashby/LinkedIn cover). | 2026-04-05 → ongoing |
| 2.6 Writing quality | Compilation fallback (page-length validation, header-marker check), AI council retry on dead providers, hard-gate substring matching, LaTeX brace/macro sanitization | 2026-04-09 (PR #2 marathon) |
| 2.7 Data quality | Canonical hash dedup (177 → 159 jobs), deterministic 3-call median scoring, `score_status` tracking, cross-source dedup audit | 2026-04-05 → 2026-04-06 |
| 2.8 QA foundation | 712+ tests in CI, fixtures, golden dataset; Tier 4b/4c data + writing tests | 2026-04-09 |
| 2.9 Self-improvement | Council retry logic, prompt versioning, pipeline metrics → Supabase, score recalibration | 2026-04-09 |
| 2.10 Score tiering | `score_tier` column + thresholds + index, downstream Lambda gating, **geography + work-auth post-score cap** | 2026-04-05 → 2026-04-29 (PR #24) |

Some sub-phases continue to receive incremental hardening as patterns surface (e.g. Lambda↔local code drift caught in Session B's audit lands in Layer 2.5 Phase A.2). The original Layer 2 parallel-execution diagram is preserved below for historical reference.

```
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  2.7 Data        │  │  2.6 Writing     │  │  2.8 QA          │
│  Quality ✅      │  │  Quality ✅      │  │  Foundation ✅   │
└────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
         │  PARALLEL           │  PARALLEL           │  PARALLEL
         └────────────┬────────┘─────────────────────┘
                      ▼
         ┌─────────────────────┐
         │  2.9 Self-           │
         │  Improvement ✅      │
         └──────────────────────┘

         ┌─────────────────────┐
         │  2.5b Scraper Fixes  │  ← INDEPENDENT — partial ✅
         └──────────────────────┘
```

---

### Phase 2.10: Score-Based Job Tiering & Prioritization — ✅ COMPLETE

**Status**: Column shipped 2026-04-05 (backfilled, `score_batch`+`rescore_batch` write tier). Downstream Lambda gating (tailor/cover/contacts) shipped 2026-04-09. **Geography + work-auth post-score cap shipped 2026-04-29 (PR #24)** — prevents wrongly-S-tier UK/US visa-required jobs.

**Score Tiers**:

| Tier | Score Range | Action | AI Cost |
|------|------------|--------|---------|
| S — Must Apply | 90-100 | Tailor resume, generate cover letter, find contacts, priority email | High (~10 calls/job) |
| A — Strong Match | 80-89 | Tailor resume, generate cover letter | Medium (~7 calls/job) |
| B — Worth Trying | 70-79 | Tailor resume only, no cover letter | Low (~4 calls/job) |
| C — Long Shot | 60-69 | Score only, no artifacts | Minimal |
| D — Skip | <60 | Score only, hide from default dashboard view | Minimal |

**Tier thresholds are user-configurable** per-user via `user_profiles.score_tier_config` JSON column.

**Self-improvement integration**: When thresholds shift (e.g. 80% of jobs below 70), Phase 2.9 generates a medium-risk adjustment to recalibrate.

---

### Layer 2.5: Stabilization & Deploy Safety — IN PROGRESS (2026-04-29 → ongoing)

**Why this layer exists.** Two forces made it necessary in late April:

1. **Stabilization (Phase A)** — by 2026-04-29, two parallel Claude sessions in one day shipped 10 PRs: 5 Plan 3b PRs from Session A and 5 prod-health PRs from Session B. The work surfaced a P0 security finding (AWS STS tokens in user-visible fields), the actual root cause of "no artifacts since Apr 22" (EventBridge cron `Input: {"user_id":"default"}`), and 45 audit findings across 8 cross-cutting bug patterns. Session B has 4 unmerged branches (3 fix branches + 1 audit doc). Phase A consolidates all of this.
2. **Deploy Safety (Phase B)** — PR #14 (2026-04-28) defined a 6-phase deploy-safety roadmap. With 3 prod 500s caught only by *manual* smoke testing on 2026-04-29 and 8 bug-class patterns discovered post-merge, ad-hoc smoke + CI green is no longer enough. Phase B turns the patterns into PR-time gates so the next 50 findings become predictable AND blocked.

The user's verbatim instruction (2026-04-30): *"integrate the other spec of other agent for deployment safety in the grand plan preferably after 3.2 is done as it is very important to catch the bugs on the website."* Plan 3b ≈ "3.2" in the user's numbering.

```
Plan 3a (done) → Plan 3b "3.2" (done 2026-04-29) → Layer 2.5 → Layer 4 / Plan 3c
                                                   ──────────
                                                   Phase A → Phase B
```

#### Phase A — Stabilization

| Sub-task | Type | Status | Notes |
|----------|------|--------|-------|
| A.1.1 Rotate Lambda exec-role creds + CloudTrail audit since 2026-04-22 | 🔴 P0 operator | Pending | Closes the STS-token leak window |
| A.1.2 Fix EventBridge cron `Input` to real user UUID (not "default") | 🔴 operator + `template.yaml` | Pending | Root cause of "no artifacts since Apr 22" |
| A.1.3 Run 3 Plan-3b backfills (eligibility recompute, apply slug, geo+work-auth cap) | 🟡 operator | Pending | Scripts in `scripts/`; commit-mode |
| A.1.4 Add IAM: `JobHuntApi` (`ecs:RunTask`/`StopTask`/`iam:PassRole`), `WsDisconnect` (`ecs:StopTask`) | 🟡 `template.yaml` | Pending | Apply session start/stop currently fail silently; Fargate task leak risk on disconnect |
| A.1.5 Add `UNIQUE(user_id, job_id, version_number)` to `resume_versions` | 🟡 DB migration | Pending | Root cause of "multiple v1 entries" |
| A.1.6 Extend WS auth token TTL 60s → 5min | 🟡 code | Pending | Token expires before Fargate cold-start completes |
| A.1.7 Run `scripts/backfill_missing_artifacts.py` reassign + retailor | 🟢 operator | Pending | Run after A.1.2 unblocks |
| A.2 Consolidate Session B's 4 branches into single `fix/comprehensive-prod-health` PR | 🟡 code | Pending | `artifact-pipeline` (3 commits: F1 creds sanitizer, X2 header markers, A1 apply_url backfill); `dashboard-state` (3 commits: F5 applied count, F6 URL filter persistence, F7 title search); `cluster-bc-cleanup` (12 commits: useApiMutation, Pydantic strict mode, contract route diff, require_db, pre-commit hook); `deep-audit-2` (audit doc — separate doc-only PR) |
| A.3 **Bug X1 fix** — `compile_latex` raises instead of returns error dict; `save_job` marks `application_status="failed"` with `failure_reason` | 🔴 code | Pending | **Highest remaining priority.** Pairs with X2 header-marker fix already in `artifact-pipeline` branch |
| A.4 PR #25 postmortem review + merge | 🟢 doc | In review | Doc-only, mergeable |
| A.5 Verify PR #23 (lazy SSM) unblocks Session B's F4 work on rebase | 🟢 verify | Pending | PR #23 already merged |

**Phase A exit criteria:** prod is healthy, no silent failures, backfills complete, all today's branches merged, no open PRs except deferred (#13 PostHog, #15 canary).

#### Phase B — Deploy Safety (PR #14 phase numbering)

| Sub-phase | What | Status | Source |
|-----------|------|--------|--------|
| **B.1** | Deploy Readiness CI gate (`sam validate` + `sam build` + layer build), runtime-import smoke (`docker run --entrypoint python` exercises lazy imports), `shared/` and `lambdas/` COPY in `Dockerfile.lambda` | ✅ Shipped | PRs #11/12/21 |
| **B.2** | Lambda canary deploys (CodeDeploy AllAtOnce/LinearShift on 13 read-only Lambdas, 12 pipeline-tier, 3 critical-tier WS Lambdas; CloudWatch alarms + auto-rollback) | Held | PR #15, blocked on Phase A clearing PR queue |
| **B.3** | Staging environment: Supabase staging project + SAM stack stage variable + Netlify branch deploys; E2E smoke against staging gates every PR | Pending | PR #14 Phase 3 |
| **B.4** | Pattern-catching CI gates: Pydantic `extra='forbid'` globally, contract route diff test, `useApiMutation` hook, `require_db` helper, pre-commit hook | Branch ready | Session B `cluster-bc-cleanup` (consolidates into A.2 PR) |
| **B.5** | Pipeline observability: Step Function ASL change `Catch → SucceedState` → `Catch → FailState`; alarm on `pipeline_metrics.artifacts_compiled = 0` for 24h; weekly email funnel summary | Pending | Backlog `pipeline_silent_success`; surfaced in Session A 2026-04-29 |
| **B.6** | Trimmed observability: structlog throughout pipeline lambdas, X-Ray on the API container, CloudWatch dashboard for the 5 most-watched infra metrics. **Note:** infra-dashboards only; PR #13 PostHog covers business analytics — no overlap. | Pending | PR #14 Phase 4 (trimmed) |
| **B.7** | Auto-rollback wiring: failing alarm during canary → CodeDeploy reverts; staging smoke gates prod deploy | Pending | PR #14 Phase 6 |

**Phase B exit criteria:** an engineer (or Claude) can ship a feature and have CI catch what would have been a prod incident. The 3 prod 500s on 2026-04-29 (PR #17 + 2 hotfixes) would never have hit prod with Phase B in place.

**Held PRs gating on this layer:**

- **PR #13** — PostHog full integration (analytics + flags + frontend), held until prod health is stable enough for the new event volume
- **PR #15** — Phase 2 Lambda canary deploys, held pending PR queue clear (consolidates into B.2)

---

### Layer 3: Deploy — ✅ LIVE (since 2026-04-21)

| Phase | Name | Status | Notes |
|-------|------|--------|-------|
| 3.0 | Go Live | ✅ Live | SAM deploys via `deploy.yml` from `main` after Deploy Readiness gate; EventBridge weekday cron 07:00 UTC; Netlify auto-deploys frontend; daily pipeline tested end-to-end |

What's actually running in prod:

- **30+ Lambdas**: scrapers (LinkedIn, Indeed, Adzuna, YC, HN, Irish portals); pipeline (`ScrapeRouter`, `ScoreBatch`, `MergeDedup`, `TailorResume`, `GenerateCoverLetter`, `FindContacts`, `EmailNotifier`, `NotifyError`); API (`JobHuntApi` container image); WS (`WsConnect`, `WsRoute`, `WsDisconnect`); auto-apply preview Lambdas
- **Step Functions**: `naukribaba-daily-pipeline` (orchestrator), `naukribaba-run-single-job` (manual JD)
- **API Gateway**: REST + WebSocket
- **Fargate**: cloud-browser auto-apply task definition
- **Supabase**: prod project with RLS on all user tables
- **S3**: artifact storage (resumes, cover letters, screenshots)

**Deploy-safety hardening for the deploy itself** (canary, staging, auto-rollback, observability) lives in Layer 2.5 above.

---

### Layer 4: Product Features — IN PROGRESS

These map to the 6 v2 product stages (Discover → Research → Tailor → Apply → Interview → Analytics).

| Phase | Name | v2 Stage | Was (old) | Key Features | Feeds From |
|-------|------|----------|-----------|--------------|------------|
| 3.1 | Discover+ | Stage 1 | Part of 2A | Manual JD submission, "+Add Job" button, enhanced dedup | 2.7 unified hash |
| 3.2 | Research | Stage 2 | 2D | CompanyLens, GDELT news, salary data, red flags, deeper AI job analysis | 2.6 keyword analysis |
| 3.3 | Tailor+ | Stage 3 | 2B + 2C | PDF-to-LaTeX conversion, Overleaf-style split-pane editor, resume version history | 2.6 quality gates, PDF validation |
| 3.4 | Apply | Stage 4 | Part of 2A | **Cloud-browser auto-apply** (Fargate Chrome + WS streaming + AI prefill), contact finder, follow-ups, outcome tracking → feeds 2.9 | 2.9 user feedback, career-ops apply mode. **Backend ✅; frontend (3c) pending.** |
| 3.5 | Interview Prep | Stage 5 | 2F | Coding bank (Blind 75), system design rubrics, STAR stories, mock AI | — |
| 3.6 | Analytics | Stage 6 | 2G | Funnel viz, score trends, scraper health dashboard, self-improvement viz | 2.9 pipeline_runs data |

**Dependency chain within Layer 4**:

```
3.1 Discover+ ──→ 3.2 Research ──→ 3.3 Tailor+ ──→ 3.4 Apply
                                                        │
                                                        ▼
3.5 Interview Prep (independent)              3.6 Analytics
                                              (needs data from 3.1-3.4)
```

#### Stage 3.4 Apply — Sub-Plan Index (updated 2026-04-30)

Stage 3.4 evolved beyond the original "semi-auto Playwright" framing into a cloud-browser auto-apply system. Sub-specs and sub-plans below; consult these (not the row in the Layer 4 table) for current status.

| Doc | Status | Description |
|-----|--------|-------------|
| Spec: [auto-apply mode 1 design](2026-04-11-auto-apply-mode-1-design.md) | Approved (superseded) | Original mode-1 design for known-ATS apply |
| Spec: [auto-apply cloud-browser design](2026-04-12-auto-apply-cloud-browser-design.md) | Approved | Universal cloud-browser approach (Fargate Chrome + WS streaming) — supersedes mode-1 framing |
| Plan 2: [browser session](../plans/2026-04-20-auto-apply-plan2-browser-session.md) | ✅ Shipped (PR #7, 2026-04-22) | `browser/browser_session.py` + Fargate task def |
| Plan 3a: [WebSocket + backend](../plans/2026-04-24-auto-apply-plan3a-websocket-backend.md) | ✅ Shipped (PR #8, 2026-04-24) | 3 WS Lambdas + 5 `/api/apply/*` endpoints + idempotent record |
| Spec: [apply platform classifier](2026-04-26-apply-platform-classifier-design.md) | ✅ Shipped (PRs #10/11/12, 2026-04-27) | URL → platform classifier; eligibility flag flipped from `apply_platform` to `apply_url`; 831 jobs backfilled; live eligibility >0 for the first time |
| Plan 3b: [AI preview](../plans/2026-04-24-auto-apply-plan3b-preview-ai.md) | ✅ Shipped (PR #17 + #21/#22 hotfixes, 2026-04-29) | AI answer prefill, platform metadata fetchers (greenhouse/ashby), question classifier |
| Plan 3c: [frontend UI](../plans/2026-04-26-auto-apply-plan3c-frontend-ui.md) | Stub (pending — **next after Layer 2.5**) | React UI: Apply button, modal, WS client, screenshot stream. Backend + WS contract live; no UI consumer yet. **Open product fork**: 3c.0 minimal (backend generates, user copy-pastes) vs 3c.full (cloud-browser supervision + WS streaming + screenshot updates) — re-ask at start of Layer 4 / Plan 3c. |

**Note on Layer placement:** Stage 3.4 originally lived in Layer 4 (AFTER DEPLOY). In practice it was built in Layer 2/3 timeframe alongside reliability work — the four-layer ordering in this doc is a logical narrative, not a strict execution schedule.

---

### Phase 3.2 Enhancement: Glassdoor Company Research

**Status**: Backlog — Glassdoor *job* scraping deprioritized (covered by LinkedIn/Greenhouse/Ashby), but their **company data** is unique.

**What Glassdoor uniquely provides**:

- Company ratings (overall, culture, compensation, career opportunities)
- Salary ranges by role and location
- Interview reviews and difficulty ratings
- Employee reviews (pros/cons/advice)
- CEO approval ratings

**Integration plan** (Phase 3.2 Research):

- Use Bright Data's Glassdoor dataset API or Web Unlocker for company pages
- Store company data in `company_intel` Supabase table
- Display on job cards as "Company Intel" section
- Feed into the A-F evaluation framework (Section D: compensation + market demand)

---

### Cross-Cutting Concerns (Not Phases)

| Concern | How It's Handled |
|---------|------------------|
| **UI Revamp** (was 2A) | Neo-Brutalist styling applied incrementally as each feature ships. Not a standalone phase. Design tokens already defined in Tailwind v4 `@theme`. |
| **Testing** (was 2E) | QA foundation (CI config, fixtures, golden dataset) shipped in 2.8. Tier 4b/4c tests with 2.6/2.7. Tier 4d tests with 2.9. Layer 2.5 Phase B.4 adds pattern-catching gates (Pydantic strict, contract route diff, `useApiMutation`). |
| **Security** | RLS on every user table. Layer 2.5 Phase A.1.1 closes the AWS STS token leak. Pydantic `extra='forbid'` (B.4) prevents future field-strip drift. |
| **Multi-tenancy** | Built for single user now. Hardcoded user info (Pattern #6 from the audit) cleared piecewise in Layer 2.5 A.2; cover-letter and matcher prompts still pending. RLS ensures isolation when multi-tenant. |
| **Career-ops integration** | Reference architecture from github.com/santifer/career-ops. ATS keyword extraction in 2.6, A-F evaluation framework feeds 3.2 Research, "filter not firehose" philosophy drives 2.10 tiering. |

---

## Career-Ops Integration Map

Reference: `github.com/santifer/career-ops` — 740+ job evaluations, 100+ tailored CVs.

Philosophy: **"A filter, not spray-and-pray."** Only top matches get full treatment.

| Our Phase | Career-Ops Feature | Integration | Status |
|-----------|-------------------|-------------|--------|
| 2.6 Writing Quality | ATS keyword injection, proof-point extraction | Extract 15-20 JD keywords → inject into existing bullets (never fabricate) | Partial |
| 2.7 Data Quality | Cross-source dedup | Description-independent `dedup_hash`, Tier 0 exact match | ✅ |
| 2.10 Tiering | "Don't apply below 4.0" | D-tier hidden, C-tier no artifacts, S+A get full treatment | ✅ |
| 3.1 Discover+ | 3-tier scanning (Playwright→API→WebSearch), 60+ companies | Greenhouse API + Lever API + company watchlist | Pending |
| 3.2 Research | A-F Evaluation (10 dimensions), compensation data | Multi-dimension scoring | Pending |
| 3.3 Tailor+ | ATS-optimized PDF, template system | Keyword-first tailoring, regen button | Partial |
| 3.4 Apply | Cloud-browser auto-apply | Fargate Chrome + WS streaming + AI prefill | Backend ✅, frontend pending |
| 3.5 Interview Prep | STAR+Reflection stories, behavioral mapping | Story bank in Supabase, per-job prep auto-generated | Pending |
| 3.6 Analytics | Application outcome tracking → feedback loop | Ground truth feeds scoring accuracy | Pending |

---

## How Current Spec Feeds Into Future Phases

| This Spec | Feeds Into | How |
|-----------|------------|-----|
| 2.7 Unified hash | 3.1 Discover+ | Manual JD submission uses same canonical dedup |
| 2.7 Before/after scoring | 3.3 Tailor+ | Resume version comparison in editor workspace |
| 2.6 Keyword analysis | 3.2 Research | Structured JD extraction feeds company intel |
| 2.6 PDF validation | 3.3 Tailor+ | Quality gates carry into editor + PDF-to-LaTeX |
| 2.10 Geo + work-auth cap | 3.4 Apply | Eligibility-aware tier prevents wasting AI on ineligible jobs |
| 2.5 (Layer 2.5) Phase A creds sanitizer | All future Lambda error paths | F1 sanitizer in shared error handler closes the str(e) leakage class |
| 2.5 (Layer 2.5) Phase B.4 gates | All future PRs | Pydantic strict + contract route diff + useApiMutation catch the 8 patterns at PR time |
| 2.9 Self-improvement loop | 3.6 Analytics | Scraper health + score trends power dashboard |
| 2.9 User feedback | 3.4 Apply | "Flag score" feeds back from application tracking |
| 2.9 Pipeline metrics | 3.6 Analytics | `pipeline_runs` table powers funnel viz |
| 2.8 QA tiers | All phases | Test infrastructure scales as stages are added |

---

## Old Phase Mapping (2A-2G → New)

For reference, how the original v2 phases map to the new structure:

| Old Phase | Old Scope | New Location | Notes |
|-----------|-----------|-------------|-------|
| 2A: UI Revamp | Neo-Brutalist redesign | Cross-cutting | Applied incrementally, not standalone |
| 2B: Editor | Overleaf-style LaTeX editor | 3.3 Tailor+ | Combined with PDF-to-LaTeX |
| 2C: PDF-to-LaTeX | Upload PDF → convert | 3.3 Tailor+ | Combined with editor |
| 2D: Company Intel | CompanyLens, GDELT, salary | 3.2 Research | Renamed to match v2 stage |
| 2E: Testing | 7-tier QA suite | 2.8 + cross-cutting | Foundation in 2.8, incremental after |
| 2F: Interview Prep | Coding, system design, STAR | 3.5 Interview Prep | Unchanged |
| 2G: Analytics | Funnel, trends, health | 3.6 Analytics | Unchanged |

**New additions** (not in original 2A-2G):

- 2.5b: Scraper fixes
- 2.6: Writing quality
- 2.7: Data quality
- 2.9: Self-improvement loop
- 2.10: Score-based tiering
- **2.5 (Layer 2.5): Stabilization & Deploy Safety**
- 3.0: Deploy
- 3.1: Discover+ (manual JD)
- 3.4: Apply (now cloud-browser auto-apply)

---

## Success Criteria Per Layer

**Layer 2 (Reliability) — ✅ MET (2026-04-29)**:

- ✅ Zero duplicate jobs in dashboard for same company+title (canonical hash dedup live)
- ✅ Same job scored twice within ±2 (deterministic 3-call median scoring)
- 🟡 Every tailored resume has before/after score delta — deferred to Layer 4 / 3.3 Tailor+
- ✅ Writing quality fallback gate prevents broken AI output reaching users
- ✅ Self-improvement loop running with tiered adjustments
- ✅ QA suite 712+ tests in CI
- 🟡 Glassdoor returning jobs via Fargate — deprioritized (Greenhouse/Ashby/LinkedIn cover)

**Layer 2.5 (Stabilization & Deploy Safety) — IN PROGRESS**:

- 🟡 Phase A.1 operator actions complete (creds rotation, EventBridge cron, IAM, DB constraints, WS TTL)
- 🟡 Phase A.2 Session B branch consolidation merged
- 🟡 Phase A.3 Bug X1 + X2 fixed (compile_latex visibility, header-marker fallback)
- ✅ Phase B.1 Deploy Readiness CI gate live (PRs #11/12/21)
- 🟡 Phase B.2 Lambda canary live for read-only + pipeline tier
- 🟡 Phase B.3 Staging environment: Supabase + SAM + Netlify branch deploys; E2E smoke gating prod
- 🟡 Phase B.4 Pattern-catching CI gates merged (Pydantic strict, contract route test, useApiMutation, require_db, pre-commit)
- 🟡 Phase B.5 Pipeline silent-success eliminated (Step Function ASL `FailState` + alarm on 0 artifacts)
- 🟡 Phase B.6 structlog + X-Ray + infra dashboards live
- 🟡 Phase B.7 Auto-rollback wired

**Layer 3 (Deploy) — ✅ MET (2026-04-21)**:

- ✅ Lambda functions deployed and responding
- ✅ Frontend live on Netlify with production API URL
- ✅ Daily pipeline triggered via EventBridge → Step Functions
- ✅ Email notifications (per-run summary)

**Layer 4 (Features) — partial**:

- 🟡 User can paste a JD and get same pipeline treatment (3.1 Discover+) — `run_single_job` SFN exists; UI partial
- 🟡 Company intel card on each job (3.2)
- 🟡 Split-pane LaTeX editor (3.3 Tailor+) — basic editor in dashboard, no split-pane yet
- 🟡 Application tracking with outcome feedback (3.4 Apply) — backend ✅, frontend (3c) pending
- 🟡 Interview prep for any job (3.5)
- 🟡 Analytics dashboard with funnel + trends (3.6)

---

## Cost Projection

| Layer | Monthly Cost | Notes |
|-------|--------------|-------|
| Foundation (Layer 1) | ~$1 | Free AI tiers, local pipeline, no infra |
| Layers 2 + 3 (current state) | ~$15-25 | Lambda (free tier mostly), Fargate (~$5 — auto-apply task on demand), Supabase (free), S3 (<$1), Netlify (free), Bright Data Web Unlocker (~$5-10) |
| After Layer 2.5 | +$5-10 | Staging Supabase project, CloudWatch dashboards, X-Ray traces. Largely free-tier eligible. |
| After Layer 4 | ~$30-50 | More AI calls (interview prep, deeper research), CompanyLens API, additional Lambda invocations from frontend Auto-Apply |

---

## Historical Status Snapshots

Preserved for reference — current state is in the "Status Snapshot — 2026-04-30" section at the top of this document.

### Status Snapshot — 2026-04-06 (historical)

**✅ Done that day:**
- 3.0 Deploy: SAM deployed (4×), EventBridge ENABLED (weekdays 07:00 UTC), Step Functions pipeline tested end-to-end
- ScoreBatch Map batching: 421 jobs split into 25-job chunks, 5 parallel, no timeout
- Data quality audit: 149 scores fixed, 59 expired, 205 dupes removed, 117 tiers realigned, 18 descriptions backfilled
- IrishJobs JSON-LD: detail page descriptions extracted via structured data
- API 500 fix: `utils/` added to `Dockerfile.lambda`
- Page length validation: fallback to base if AI output too short

**🔴 Issues found (since resolved unless noted):**
- Lambda tailoring quality gaps (Lambda↔local code drift) — became audit Pattern #5, ongoing in Layer 2.5
- 190 cross-source dupes — addressed via canonical hash dedup
- 687 → 467 jobs in DB — score-tier filtering shipped
- Cover letters: 99/467 jobs only — gating + S/A-tier focus shipped

**🎯 Immediate-next list at the time** (all addressed): cross-source dedup, port tailoring guards to Lambda (still partial — Layer 2.5 A.2 finishes), dashboard declutter, Greenhouse/Lever scrapers, career-ops A-F framework integration.

### Status Snapshot — 2026-04-05 evening (historical)

**✅ Done that week:**
- 2.5b Scraper Fixes (partial): OpenRouter 404 fixed (5 verified free models); AI council 18 → 32 providers; DeepSeek disabled (NVIDIA NIM alternative)
- 2.7 Data Quality: canonical hash dedup live (177 → 159 jobs); deterministic 3-call median scoring; `score_version=2`; `score_status` tracking
- 2.10 Score Tiering: `score_tier` column + CHECK + index shipped; all 159 jobs tiered (S=14, A=41, B=26, C=51, D=27)
- Council retry logic with fresh-provider failover
- Hard gate relaxed: substring matching for section completeness

**🔴 Discovered then (since addressed in Apr 9 marathon):**
- AI-generated LaTeX produced invalid output (undefined macros, unbalanced env blocks) — fixed via brace/macro sanitizer
- Tailoring prompt drops sections — fixed via prompt v2
- Groq IP-block from Singapore VPN — deprioritized in council

**⏸️ Blockers at the time:**
- SAM deploy: Docker daemon stuck — resolved (deploy live since 2026-04-21)
- Apify budget exhausted — Bright Data contact finder approach taken
