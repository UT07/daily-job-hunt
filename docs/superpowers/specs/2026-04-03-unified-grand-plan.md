# NaukriBaba — Unified Grand Plan

**Date**: 2026-04-03 (last updated 2026-04-06)
**Status**: Approved
**Supersedes**: Individual phase numbering from v2 design spec (2A-2G)
**Integration**: career-ops (github.com/santifer/career-ops) — adopted as reference architecture

---

## Status Snapshot — 2026-04-06

### ✅ Done today (Apr 6)
- **3.0 Deploy**: SAM deployed (4x), EventBridge ENABLED (weekdays 07:00 UTC), Step Functions pipeline tested end-to-end
- **ScoreBatch Map batching**: 421 jobs split into 25-job chunks, 5 parallel, no timeout
- **Data quality audit**: 149 scores fixed, 59 expired, 205 dupes removed, 117 tiers realigned, 18 descriptions backfilled
- **IrishJobs JSON-LD**: Detail page descriptions now extracted via structured data
- **API 500 fix**: `utils/` added to Dockerfile.lambda
- **Page length validation**: Fallback to base if AI output too short (1 page)

### 🔴 Issues found
- **Lambda tailoring quality**: Cut-off summaries, incomplete education, missing certifications — Lambda `tailor_resume.py` lacks guards from local `tailorer.py`
- **190 cross-source dupes**: Same job from LinkedIn+Indeed gets different hash → Tier 0 dedup needed
- **687→467 jobs in DB**: Still too many — user wants only top matches (filter, not firehose)
- **Cover letters**: Only 99/467 jobs have cover letters

### 🎯 Immediate next (Priority 0 from career-ops integration plan)
1. Fix cross-source dedup (Tier 0 exact company+title match)
2. Port tailoring guards to Lambda
3. Dashboard declutter (tier filter, hide expired/C/D)
4. Greenhouse + Lever API scrapers
5. Update grand plan with career-ops A-F evaluation framework

---

## Career-Ops Integration Map

Reference: github.com/santifer/career-ops — 740+ job evaluations, 100+ tailored CVs.

Philosophy: **"A filter, not spray-and-pray."** Only top matches get full treatment.

| Our Phase | Career-Ops Feature | Integration |
|-----------|-------------------|-------------|
| 2.6 Writing Quality | ATS keyword injection, proof-point extraction | Extract 15-20 JD keywords → inject into existing bullets (never fabricate) |
| 2.7 Data Quality | Cross-source dedup | Description-independent `dedup_hash`, Tier 0 exact match |
| 2.10 Tiering | "Don't apply below 4.0" | D-tier never enters DB, C-tier no artifacts, S+A get full treatment |
| 3.1 Discover+ | 3-tier scanning (Playwright→API→WebSearch), 60+ companies | Greenhouse API + Lever API + company watchlist |
| 3.2 Research | A-F Evaluation (10 dimensions), compensation data | Multi-dimension scoring: role fit, CV alignment, seniority, compensation, personalization plan, interview prep |
| 3.3 Tailor+ | ATS-optimized PDF, template system | Keyword-first tailoring, regen button per job |
| **3.4 Apply** | **Semi-auto apply: Playwright extracts form → AI generates STAR answers → user confirms** | **NEW FEATURE: Smart form-filling with human-in-the-loop** |
| 3.5 Interview Prep | STAR+Reflection stories, behavioral mapping | Story bank in Supabase, per-job prep auto-generated |
| 3.6 Analytics | Application outcome tracking → feedback loop | Ground truth feeds scoring accuracy |

---

## Status Snapshot — 2026-04-05 evening

### ✅ Done this week
- **2.5b Scraper Fixes (partial)**: OpenRouter 404 fixed (dead Gemini model replaced with 5 verified free models); AI council expanded 18→32 providers; DeepSeek left disabled (accessible via NVIDIA NIM anyway).
- **2.7 Data Quality**: Canonical hash dedup live (18 duplicates removed from 177→159 jobs); deterministic 3-call median scoring rolled out; `score_version=2` set on all 159 live jobs; `score_status` tracking.
- **2.10 Score Tiering**: `score_tier` column + CHECK constraint + filter index shipped; all 159 jobs tiered from backfill SQL. Distribution: S=14 (8.8%), A=41 (25.8%), B=26 (16.4%), C=51 (32.1%), D=27 (17.0%).
- **Council retry logic**: `council_generate()` now retries with fresh providers after dead ones are marked (was failing when top-2 were both Groq during IP block).
- **Hard gate relaxed**: `check_section_completeness` now accepts substring matches ("Technical Skills" satisfies "skills") so AI-generated resumes with reasonable headings aren't rejected.

### 🔴 Newly discovered this session (add to 2.6 Writing Quality)
- **AI-generated LaTeX produces invalid output**: Tailoring outputs use undefined custom commands like `\projectentryurl` not in base template, and produce unbalanced `\begin{itemize}` / `\end{itemize}` blocks. `\iffalse` left unclosed at line 76 of one generated resume. Compilation blocked at hard gate; pdflatex fallback also fails.
- **Tailoring prompt drops sections**: One A-tier job generated a resume with dropped `\section` entirely; hard gate (now relaxed) was the backstop.
- **Groq API key appears IP-blocked**: 403 "Access denied. Please check your network settings" — not rate-limit, not auth. May require new API key or different egress IP.

### ⏸️ Known blockers
- **SAM deploy**: Docker Desktop daemon stuck/unresponsive. Need to restart Docker or run from a working docker host.
- **Apify budget exhausted**: contact_finder.py old path no longer usable. Serper rejected as an alt (quality concerns). **Plan**: build Bright Data–based contact finder (reuse existing proxy infra from scrapers).

### 🎯 Immediate next tasks
1. **Fix tailoring prompt quality** (2.6): constrain AI to only use commands defined in base template; validate brace balance before output; reject outputs with undefined macros.
2. **Bright Data contact finder** (3.4 prep): build new module that searches google.com for LinkedIn profiles via Web Unlocker proxy, add quality tests (profile accuracy, message personalisation).
3. **Regenerate 60 artifacts** (blocked on #1): resumes + cover letters for all jobs with `match_score >= 75` (14 jobs at 90+, 40 at 85-89, 6 at 75-84). Skip the 99 jobs <75; they stay with user's default artifacts.
4. **GradIreland scraper fix** (2.5b): Drupal template changed, 0 jobs returned. Needs fresh HTML inspection + selector update.

---

## Why This Document Exists

Multiple overlapping specs existed with inconsistent phase numbering:
- v2 design spec (2026-03-30) defined phases 2A-2G
- Testing spec (2026-03-31) defined 7 QA tiers
- Playwright migration spec (2026-04-01) defined Phase 2.5
- Quality pipeline spec (2026-04-03) defined Phases 2.6-2.9

Actual work diverged from the original 2A-2G plan because the pipeline needed reliability
fixes before features made sense. This document is the single source of truth for what's
done, what's next, and how it all connects.

---

## Architecture

```
React Frontend (Netlify)
       │ REST API
AWS Step Functions (orchestration)
       │
Lambda Functions (compute) ──── Fargate (Glassdoor only)
       │
Supabase PostgreSQL + S3 Storage
```

- No n8n. Step Functions orchestrates the daily pipeline.
- Lambda for all compute (scrapers, AI, compilation).
- Fargate only for Glassdoor (JS rendering behind login wall).
- main.py for local development runs.
- GitHub Actions for CI/CD.

---

## Grand Phase Structure

### Layer 1: Foundation — COMPLETE

| Phase | Name | Status | What Was Built |
|-------|------|--------|----------------|
| 1.0 | Core Pipeline | ✅ Done | Scrapers, AI matching, LaTeX PDFs, multi-provider failover, SQLite cache |
| 2.0 | Landing Page | ✅ Done | FastAPI backend, React frontend, SAM template, GCP/Drive integration |
| 2.5 | Web Unlocker | ✅ Done | LinkedIn, Indeed, Irish portals via Bright Data Web Unlocker on Lambda |

**Result**: Working pipeline that scrapes 7 sources, scores jobs, tailors resumes, compiles PDFs.
177 jobs in dashboard, 123 scraped in last run, 58 matched.

---

### Layer 2: Reliability — CURRENT

All phases defined in: `2026-04-03-quality-pipeline-design.md`

```
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  2.7 Data        │  │  2.6 Writing     │  │  2.8 QA          │
│  Quality         │  │  Quality         │  │  Foundation      │
│                  │  │                  │  │                  │
│  • Unified hash  │  │  • Prompt v2     │  │  • Tier 4b data  │
│  • Deterministic │  │  • Keyword       │  │  • Tier 4c write │
│    scoring       │  │    analysis      │  │  • Tier 4d self  │
│  • Before/after  │  │  • PDF validate  │  │  • CI pipeline   │
│  • No truncation │  │  • Dynamic depth │  │                  │
│  • seen_jobs     │  │  • Cover letter  │  │  Validates 2.6   │
│    → Supabase    │  │    early check   │  │  and 2.7 as      │
│  • Skip bad data │  │  • LaTeX sanit.  │  │  they're built   │
│                  │  │  • Compilation   │  │                  │
│                  │  │    rollback      │  │                  │
└────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
         │  PARALLEL           │  PARALLEL           │  PARALLEL
         └────────────┬────────┘─────────────────────┘
                      ▼
         ┌─────────────────────┐
         │  2.9 Self-           │
         │  Improvement         │
         │                      │
         │  • Tiered risk       │
         │  • Prompt versioning │
         │  • Rollback          │
         │  • Cooldown          │
         │  • User feedback     │
         │  • Base resume sug.  │
         │  • Model A/B test    │
         │  • Query optimization│
         │  • Pipeline metrics  │
         │    → Supabase        │
         └──────────────────────┘

         ┌─────────────────────┐
         │  2.5b Scraper Fixes  │  ← INDEPENDENT (anytime)
         │                      │
         │  • Glassdoor Fargate │
         │  • IrishJobs 403     │
         │  • GradIreland fix   │
         │  • DeepSeek removal  │
         │  • OpenRouter fix    │
         └──────────────────────┘
```

**Execution order**:
1. 2.7 + 2.6 + 2.8 in parallel (data quality + writing quality + QA tests)
2. 2.9 after 2.7 + 2.6 complete (self-improvement needs quality metrics to analyze)
3. 2.5b independent (can run anytime, no dependencies)

---

### Phase 2.10: Score-Based Job Tiering & Prioritization (NEW)

**Status**: ✅ Complete 2026-04-05 — column shipped, backfilled, score_batch+rescore_batch write tier. Tier-gating in downstream lambdas (tailor/cover/contacts) still TODO.

**Why**: After Phase 2.9 deterministic rescoring produced calibrated scores, we
need to prioritize artifact generation (tailored resumes, cover letters) to avoid
burning AI credits on low-value jobs. Not every job deserves a tailored resume.

**Score Tiers**:

| Tier | Score Range | Action | AI Cost |
|------|------------|--------|---------|
| S — Must Apply | 90-100 | Tailor resume, generate cover letter, find contacts, priority email | High (~10 calls/job) |
| A — Strong Match | 80-89 | Tailor resume, generate cover letter | Medium (~7 calls/job) |
| B — Worth Trying | 70-79 | Tailor resume only, no cover letter | Low (~4 calls/job) |
| C — Long Shot | 60-69 | Score only, no artifacts | Minimal |
| D — Skip | <60 | Score only, hide from default dashboard view | Minimal |

**Tier thresholds are user-configurable** per-user via `user_profiles.score_tier_config` JSON column:
```json
{
  "must_apply_min": 90,
  "strong_match_min": 80,
  "worth_trying_min": 70,
  "long_shot_min": 60
}
```

**Implementation**:

- Add `score_tier` TEXT column to `jobs` table (values: S, A, B, C, D)
- Compute tier from `match_score` at score time in `score_batch.py`
- Dashboard filters by tier (default: show S + A + B, hide C + D unless "Show all" toggled)
- `tailor_resume` Lambda checks tier before processing — skips if C or D
- `generate_cover_letter` Lambda checks tier — only runs for S and A
- `find_contacts` Lambda only runs for S

**Self-improvement integration**: When thresholds shift (e.g., 80% of jobs below 70),
Phase 2.9 generates a medium-risk adjustment to recalibrate tier thresholds.

**Scripts** (already implemented):
- `scripts/rescore_sample.py` — rescore N jobs with deterministic scoring
- `scripts/dedup_canonical_hashes.py` — remove duplicate job rows by canonical_hash

---

### Layer 3: Deploy — AFTER LAYER 2 (partial overlap OK)

| Phase | Name | Status | What Needs to Happen |
|-------|------|--------|---------------------|
| 3.0 | Go Live | Pending | SAM deploy (needs Docker), Netlify CD, GitHub Actions wired |

**Details**:
- SAM deploy: Start Docker Desktop → `sam build && sam deploy --guided` to eu-west-1
- Netlify: Configure GitHub → Netlify auto-deploy for `web/` directory
- GitHub Actions: Wire `daily_job_hunt.yml` to trigger deployed Step Functions
- Email template: Update to include score deltas, writing quality metrics
- Dashboard UI: Add "Flag score" button, "Pending Adjustments" card, before/after display, "Missing data" badges, writing quality scores

**Can partially overlap with Layer 2**: Deploy current code first, then deploy quality fixes incrementally as they land. Don't wait for all of Layer 2 to finish.

---

### Layer 4: Product Features — AFTER DEPLOY

These map to the 6 v2 product stages (Discover → Research → Tailor → Apply → Interview → Analytics).

| Phase | Name | v2 Stage | Was (old) | Key Features | Feeds From |
|-------|------|----------|-----------|-------------|------------|
| 3.1 | Discover+ | Stage 1 | Part of 2A | Manual JD submission, "+Add Job" button, enhanced dedup | 2.7 unified hash |
| 3.2 | Research | Stage 2 | 2D | CompanyLens, GDELT news, salary data, red flags, deeper AI job analysis | 2.6 keyword analysis |
| 3.3 | Tailor+ | Stage 3 | 2B + 2C | PDF-to-LaTeX conversion, Overleaf-style split-pane editor, resume version history | 2.6 quality gates, PDF validation |
| 3.4 | Apply | Stage 4 | Part of 2A | **Semi-auto apply** (Playwright form extraction → AI STAR answers → user confirms), contact finder, email templates, follow-ups, application outcome tracking → feeds 2.9 | 2.9 user feedback, career-ops apply mode |
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

---

### Cross-Cutting Concerns (Not Phases)

| Concern | How It's Handled |
|---------|-----------------|
| **UI Revamp** (was 2A) | Neo-Brutalist styling applied incrementally as each feature ships. Not a standalone phase. Design tokens already defined in Tailwind v4 `@theme`. |
| **Testing** (was 2E) | QA foundation (CI config, fixtures, golden dataset) built in 2.8. Tiers 4b/4c tests written incrementally alongside 2.6/2.7. Tier 4d tests written during/after 2.9. Each Layer 4 phase adds its own tests. |
| **Security** | RLS policies exist. Each new table gets RLS. API auth tests in Tier 2. |
| **Multi-tenancy** | Built for single user now. RLS ensures isolation when multi-tenant. |

---

## How Current Spec Feeds Into Future Phases

| This Spec | Feeds Into | How |
|-----------|-----------|-----|
| 2.7 Unified hash | 3.1 Discover+ | Manual JD submission uses same canonical dedup |
| 2.7 Before/after scoring | 3.3 Tailor+ | Resume version comparison in editor workspace |
| 2.6 Keyword analysis | 3.2 Research | Structured JD extraction feeds company intel |
| 2.6 PDF validation | 3.3 Tailor+ | Quality gates carry into editor + PDF-to-LaTeX |
| 2.6 Writing quality score | 3.6 Analytics | Tracked over time in analytics dashboard |
| 2.9 Self-improvement loop | 3.6 Analytics | Scraper health + score trends power dashboard |
| 2.9 User feedback | 3.4 Apply | "Flag score" feeds back from application tracking |
| 2.9 Pipeline metrics | 3.6 Analytics | pipeline_runs table powers funnel visualization |
| 2.8 QA tiers | All phases | Test infrastructure scales as stages are added |
| 2.5b Glassdoor Fargate | 3.1 Discover+ | More complete job discovery |
| Contact finder (backlog) | 3.4 Apply | Quality fix for contacts + intro messages |
| App outcome feedback (backlog) | 3.4 → 2.9 | Ground truth for scoring accuracy |
| Deeper AI job analysis (backlog) | 3.2 Research | Structured JD data feeds company intel |

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
- 2.5b: Scraper fixes + Glassdoor Fargate
- 2.6: Writing quality
- 2.7: Data quality
- 2.9: Self-improvement loop
- 3.0: Deploy
- 3.1: Discover+ (manual JD)
- 3.4: Apply (contacts, email, follow-ups)

---

## Success Criteria Per Layer

**Layer 2 (Reliability) is done when**:
- Zero duplicate jobs in dashboard for same company+title
- Same job scored twice produces scores within +/-2
- Every tailored resume has before/after score delta
- Writing quality score >= 6/10 on all dimensions
- Self-improvement loop running with tiered adjustments
- QA tiers 4b/4c/4d passing in CI
- Glassdoor returning jobs via Fargate

**Layer 3 (Deploy) is done when**:
- Lambda functions deployed and responding
- Frontend live on Netlify with production API URL
- Daily pipeline triggered via GitHub Actions → Step Functions
- Email notifications sending with score deltas

**Layer 4 (Features) is done when**:
- User can paste a JD and get same pipeline treatment (3.1)
- Company intel card on each job (3.2)
- Split-pane LaTeX editor working (3.3)
- Application tracking with outcome feedback (3.4)
- Interview prep for any job (3.5)
- Analytics dashboard with funnel + trends (3.6)

---

## Cost Projection

| Layer | Monthly Cost | Notes |
|-------|-------------|-------|
| Current (Layer 1) | ~$1 | Free AI tiers, local pipeline, no infra |
| After Layer 2+3 | ~$15-25 | Lambda (free tier), Fargate (~$5), Supabase (free), S3 (<$1), Netlify (free) |
| After Layer 4 | ~$25-40 | More AI calls (interview prep), CompanyLens API, additional Lambda invocations |
