# NaukriBaba — Unified Grand Plan

**Date**: 2026-04-03
**Status**: Approved
**Supersedes**: Individual phase numbering from v2 design spec (2A-2G)

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
| 3.4 | Apply | Stage 4 | Part of 2A | Contact finder fix, email templates, follow-ups, application outcome tracking → feeds 2.9 | 2.9 user feedback |
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
