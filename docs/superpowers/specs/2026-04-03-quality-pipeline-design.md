# Quality Pipeline Design — Phases 2.6, 2.7, 2.8, 2.9, 2.5b

**Date**: 2026-04-03
**Status**: Approved
**Scope**: Data quality, writing quality, QA integration, self-improvement loop, scraper fixes
**Depends on**: Phase 2.5 (Web Unlocker scrapers — complete)

---

## 1. Overview

Five interconnected data quality issues were found during Phase 2.5 testing (Apr 3, 2026):
duplicate jobs with different scores, 18 jobs with missing descriptions, non-deterministic AI scoring,
no before/after score comparison, and user-reported score inaccuracy. Additionally, resume and cover
letter writing quality needs improvement, and the self-improvement loop is analysis-only with no
feedback mechanism.

This spec addresses all of these systematically across 7 sections, mapped to Phases 2.6–2.9 and 2.5b.

**Approach**: "Quality Pipeline" — refactor the core pipeline with unified dedup, deterministic scoring,
quality gates, v2 prompts, and a tiered self-improvement loop. Data quality tests integrated into the
QA suite.

**Execution order**: 2.7 + 2.6 + 2.8 in parallel → 2.9 after BOTH 2.7 and 2.6 complete → 2.5b independent.

**Note on 2.5b**: IrishJobs description enrichment (cross-referencing with Indeed/LinkedIn) depends on 2.7 unified hash. All other 2.5b items (Glassdoor Fargate, GradIreland, DeepSeek removal, OpenRouter fix) are truly independent.

---

## 2. Section 1: Unified Deduplication (Phase 2.7)

### Problem

Two hash formulas exist:
- `lambdas/scrapers/normalizers.py:18-20` — `md5(company|title|description[:500])`
- `scrapers/base.py:76-78` — `md5(title|company|location|source)`

Same job scraped from different queries or sources gets different hashes, creating duplicate entries
with different scores.

### Design

**Single canonical hash**:
```
md5(normalize(company) + "|" + normalize(title) + "|" + normalize_whitespace(description).lower())
```

- `normalize()` = lowercase, strip whitespace, strip legal suffixes (Ltd, Inc, GmbH, LLC)
- `normalize_whitespace()` = collapse whitespace runs to single space, strip leading/trailing
- Full description — no truncation anywhere (hash, scoring, display, storage)
- Location and source **excluded** — same job from Indeed vs LinkedIn should match

**Three-tier dedup** (extend existing `merge_dedup.py`):
1. **Exact hash** — identical canonical hash → keep version with longest description (exists)
2. **Fuzzy match** — same company (>=0.8 similarity) + same title (>=0.7 similarity) after stripping seniority prefixes → merge, keep richest version (exists). "Richest" defined as: longest description → most fields populated → most recent scrape date (in that priority order).
3. **Cross-run dedup (NEW)** — before scoring, check `jobs` table in Supabase for existing job with same canonical hash. If found and scored within 7 days, skip re-scoring AND re-tailoring — reuse existing base scores, tailored scores, and artifacts. Before/after delta carries forward from original scoring. Saves AI calls and prevents score drift.

**Manual JD hashing**: User-submitted jobs are hashed with the same canonical formula. If a manual JD matches a scraped job, merge and keep manual as canonical source (user-submitted takes priority — richer description, explicit intent).

**No truncation anywhere**:
- Hash: full description
- Scoring prompts: full JD sent to AI (remove 2000-char limit in `score_batch.py:130`)
- Resume context: full resume sent to AI (remove 3000-char limit in `score_batch.py:132`)
- Dashboard display: full JD shown
- Storage: full description in Supabase

**`seen_jobs.json` → Supabase migration**:
- New `seen_jobs` table: `job_id`, `user_id`, `canonical_hash`, `first_seen`, `last_seen`, `score`, `matched`
- Pipeline queries this table instead of reading/writing local JSON
- Eliminates inconsistency between local, Lambda, and CI environments

**Migration**: One-time script to recompute hashes for all existing `jobs_raw` and `jobs` records. Add `canonical_hash` column to both tables.

---

## 3. Section 2: Deterministic Scoring (Phase 2.7)

### Problem

Same job gets different scores across runs. AI scoring is non-deterministic (temperature, provider
variance, prompt wording). No before/after comparison when resumes are tailored. User reports scores
feel inaccurate.

### Design

**Score determinism**:
- `temperature=0` for all scoring calls (currently 0.3 in matcher, 0.5 in resume_scorer)
- **Multi-call median**: Score each job 3 times, take median of each perspective (ATS, HM, TR). Dampens provider variance. Cached after first full scoring.
- If only 1 provider available (others dead), fall back to single call with `temperature=0`

**Before/after score comparison (NEW)**:
- **Step 1**: Score base resume against JD → `base_ats`, `base_hm`, `base_tr`
- **Step 2**: Tailor resume
- **Step 3**: Score tailored resume against same JD → `tailored_ats`, `tailored_hm`, `tailored_tr`
- **Delta display**: Dashboard shows `+12 ATS`, `+8 HM`, `+5 TR` per job
- If tailored score is lower than base on any perspective → flag for review

**Score storage schema change** (jobs table):
```sql
base_ats_score          INTEGER,
base_hm_score           INTEGER,
base_tr_score           INTEGER,
tailored_ats_score      INTEGER,
tailored_hm_score       INTEGER,
tailored_tr_score       INTEGER,
match_score             FLOAT,     -- avg of base scores (for initial ranking)
final_score             FLOAT,     -- avg of tailored scores (for dashboard)
score_version           INTEGER,   -- tracks prompt version for drift detection
scored_at               TIMESTAMPTZ,
score_status            TEXT       -- 'scored', 'insufficient_data', 'incomplete'
```

**Skip scoring for bad data**:
- Jobs with <100 char descriptions → `score_status: 'insufficient_data'`, not scored
- Jobs missing company name → `score_status: 'incomplete'`
- Dashboard shows "Missing data" badge instead of fake scores

**Scoring prompt review**:
- Audit the 0-100 calibration scale against real outcomes
- Review whether criteria are well-anchored (what does 85 actually mean?)
- Validate scoring prompt against golden dataset: `tests/quality/golden_dataset.json` — 25 JD+resume pairs, human-labeled into 4 categories (strong_match 80-100, good_match 60-79, weak_match 30-59, no_match 0-29). Dataset to be created as part of Phase 2.8 QA Foundation — pairs selected from existing 177 dashboard jobs with user-verified scoring accuracy.

---

## 4. Section 3: Resume & Cover Letter Writing Quality (Phase 2.6)

### Problem

AI-generated resumes have recurring issues. Typo map in `tailorer.py` fixes 11 known AI LaTeX
mistakes. Improvement prompt is vague (21 lines). Cover letter validation is post-hoc in
`self_improver.py`. Output quality isn't good enough to send.

### Design

**Resume tailoring prompt v2**:
- **Keyword analysis step**: Before tailoring, extract top 10 keywords/skills from JD. Pass explicitly: "These are the key requirements: [list]. Ensure each is addressed."
- **Project handling**: Purrrfect Keys always included. Select 2 most JD-relevant other projects. All project descriptions rewritten to emphasize aspects relevant to the JD (same project highlights different strengths for different jobs).
- **Structured feedback in improvement loop**:
  ```
  FEEDBACK FROM SCORING:
  - ATS (score: 72): Missing keywords: Kubernetes, GraphQL. Skills section doesn't match JD priorities.
  - Hiring Manager (score: 68): Impact statements lack metrics. "Improved performance" → needs numbers.
  - Tech Recruiter (score: 75): 3/5 required skills present, missing: distributed systems, event-driven.

  APPLY THESE SPECIFIC CHANGES:
  1. Add Kubernetes and GraphQL to Skills section
  2. Quantify the performance improvement in Kraken role
  3. Add distributed systems experience from [specific project]
  ```
- **No fabrication** — keep existing guardrails (fabrication detection, -20 ATS penalty)

**Dynamic tailoring depth** (replaces binary light-touch):
- Base score 85+: light touch (surgical keyword additions, description tweaks)
- Base score 70-84: moderate rewrite (restructure bullets, rewrite summary, reorder skills)
- Base score <70: heavy rewrite (full project description rewrites, summary overhaul, skills reprioritization)
- **Fallbacks**: If base score is missing (`score_status: 'insufficient_data'`), don't tailor — job lacks enough data. If resume is new with no previous tailoring, "base score" = first score of unmodified base resume against the JD. If scoring fails entirely (all providers down), skip tailoring and queue for next run.

**Cover letter improvements**:
- Same keyword analysis as resume — cover letter references the same top keywords
- **Early validation**: word count (280-380), banned phrase check, dash check all happen at generation time
- Max 2 retries if validation fails, then accept best attempt and flag for review

**LaTeX quality gates** (before compilation):
- Brace balance check becomes a **hard gate** — unbalanced → don't compile, retry generation
- Section completeness check — all required sections present
- Size bounds — output must be 60-150% of input size (tighter than current 50-200%)

**LaTeX-aware sanitization**:
- Replace 11-entry typo map with systematic command validation against known-good command list
- Regex scan for `\commandname` patterns, validate against whitelist

**Compilation rollback**:
- Work on a copy of .tex file, preserve original
- If compilation fails, original is intact for debugging

**PDF output validation**:
- Page count: resume must be exactly 2 pages (flag if 1 or 3+)
- File size: 10KB-500KB (below = empty/broken, above = bloat)
- Text extraction: use pymupdf to verify key sections present (name, skills, experience headers)
- Content overflow: extract page 2 bottom — if ends mid-sentence, content was cut off

**Council critic rubric**:
- Critic evaluates generators against: keyword coverage, section completeness, writing quality, no fabrication

**Writing quality scoring (NEW metric)**:
- After tailoring, lightweight AI check: rate 1-10 on specificity, impact language, authenticity, readability
- Store as `writing_quality_score` in jobs table
- Self-improver tracks trends over time

---

## 5. Section 4: Self-Improvement Loop — Tiered (Phase 2.9)

### Problem

`self_improver.py` detects 6 issue types but only auto-fixes one (disable broken scrapers).
Suggestions are logged but never applied. No feedback loop.

### Design

**Pipeline adjustments table** (new Supabase table):
```sql
CREATE TABLE pipeline_adjustments (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID REFERENCES auth.users(id),
  adjustment_type TEXT NOT NULL,  -- scraper_config, model_swap, score_threshold,
                                  -- keyword_weight, prompt_change, quality_flag
  risk_level      TEXT NOT NULL,  -- low, medium, high
  status          TEXT NOT NULL DEFAULT 'pending',
                                  -- pending, auto_applied, awaiting_approval,
                                  -- approved, rejected, reverted
  payload         JSONB NOT NULL,
  previous_state  JSONB,          -- snapshot for rollback
  reason          TEXT NOT NULL,
  evidence        JSONB,
  created_at      TIMESTAMPTZ DEFAULT now(),
  applied_at      TIMESTAMPTZ,
  reverted_at     TIMESTAMPTZ,
  reviewed_by     UUID,
  run_id          UUID            -- which pipeline run created this
);
```

**Tiered risk system**:

| Risk | Auto-applies? | Examples | Notification |
|------|--------------|----------|-------------|
| Low | Yes | Disable 3-day zero-yield scraper. Swap model with <40 avg score. Skip scoring for <100 char descriptions. | Silent log |
| Medium | Yes + notify | Adjust score threshold if >80% below 50. Update keyword weights from JD analysis. Blacklist >50% error rate model. | Email + dashboard "undo" |
| High | Awaits approval | Prompt version changes. Scoring criteria mods. Tailoring depth threshold changes. New scraper activation. | Dashboard "Pending Adjustments" card |

**Feedback loop wiring**:
```
Pipeline Run N completes
  → self_improve Lambda analyzes:
    - scraper yield rates
    - score distribution + drift from baseline
    - writing quality trends
    - keyword gap analysis (weighted by job score)
    - model performance comparison
    - artifact quality (PDF validation, cover letter checks)
  → writes adjustments to pipeline_adjustments table

Pipeline Run N+1 starts:
  → load_config reads pending adjustments
  → applies low + medium (auto-applied)
  → skips high (awaiting_approval)
  → logs which adjustments were active

Run N+1 results compared to Run N:
  → metrics improved → adjustment confirmed
  → metrics worsened → auto-revert (medium), flag for review (low)
```

**Prompt versioning**:
```sql
CREATE TABLE prompt_versions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID REFERENCES auth.users(id),
  prompt_name TEXT NOT NULL,     -- 'scoring_system', 'tailoring_system', 'cover_letter_system'
  version     INTEGER NOT NULL,
  content     TEXT NOT NULL,
  active_from TIMESTAMPTZ DEFAULT now(),
  active_to   TIMESTAMPTZ,      -- null = currently active
  metrics     JSONB,            -- avg scores, quality metrics during this version
  created_by  TEXT              -- 'auto' or 'manual'
);
```

**Rollback mechanism**:
- Each adjustment stores `previous_state` in payload
- Revert = create new adjustment with old state, mark original as `reverted`
- Prompt rollback = set `active_to` on current version, reactivate previous

**Anti-thrash cooldown**:
- Once an adjustment is reverted, blacklisted for 5 runs before re-proposal
- Stored as `cooldown_until` field in adjustment record

**Cross-run statistical significance**:
- Adjustments evaluated over minimum 3 runs before confirming or reverting
- Running average comparison: adjustment confirmed if target metric improves by >=5% over 3-run average
- Adjustment reverted if target metric worsens by >=5% over 3-run average
- Inconclusive (within +/-5%): extend evaluation to 5 runs, then force a decision

**User feedback integration**:
- "Flag score" action on dashboard → creates ground truth entry
- "This resume is bad" → flags tailoring prompt/model combination
- Feeds into self-improvement as high-confidence signal

**Base resume improvement suggestions** (medium-risk):
- If keyword gap analysis across 50+ jobs consistently shows a missing skill
- Generate: "Consider adding Kubernetes to base resume — appeared in 34 of top 50 JDs"
- User approves → update base resume → all future tailoring benefits

**Pipeline metrics to Supabase**:
```sql
CREATE TABLE pipeline_runs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID REFERENCES auth.users(id),
  started_at      TIMESTAMPTZ,
  completed_at    TIMESTAMPTZ,
  jobs_scraped    INTEGER,
  jobs_new        INTEGER,
  jobs_scored     INTEGER,
  jobs_matched    INTEGER,
  jobs_tailored   INTEGER,
  avg_base_score  FLOAT,
  avg_final_score FLOAT,
  avg_writing_quality FLOAT,
  active_adjustments JSONB,     -- which adjustments were active this run
  scraper_stats   JSONB,        -- per-scraper yield, errors, latency
  model_stats     JSONB,        -- per-model usage, scores, errors
  status          TEXT           -- 'completed', 'failed', 'partial'
);
```

**Model A/B testing**:
- When multiple providers available, randomly assign 20% of jobs to alternate model
- Compare scores. Best model gets promoted (low-risk auto-apply).

**Query optimization**:
- Track match rate per search query across runs
- Queries with <5% match rate for 3+ consecutive runs → suggest modification (medium-risk)

---

## 6. Section 5: QA Suite Integration (Phase 2.8)

### Relationship to Phases 2.6 and 2.7

Tests are written **incrementally alongside features**, not as a batch after. As each piece of 2.6 or 2.7 lands, its corresponding test(s) are added to the appropriate tier. Phase 2.8 runs in parallel with 2.6/2.7 — the "QA Foundation" is the test infrastructure (CI config, fixtures, golden dataset), while individual tests are added as their features are implemented.

Tier 4d (self-improvement tests) validates Phase 2.9 and is written during/after 2.9 implementation.

### Design

**Tier 4b: Data Quality Tests**

| Test | Validates | CI Gate |
|------|-----------|---------|
| Hash consistency | Same job through normalizers.py and scrapers/base.py → identical canonical_hash | MUST PASS |
| Dedup correctness | 5 synthetic duplicate pairs (same job, different sources) → all merged | MUST PASS |
| Fuzzy dedup boundaries | Similar-but-different jobs → NOT merged | MUST PASS |
| No truncation | 5000-char description → stored, scored, displayed in full | MUST PASS |
| Description-less handling | Empty description → marked insufficient_data, not scored | MUST PASS |
| Score determinism | Same job scored 3x with temp=0 → all within +/-2 | REPORT ONLY |
| Before/after delta | Base + tailored scores stored, delta computed correctly | MUST PASS |
| Cross-run dedup | Job scored in Run N → same job in Run N+1 → skips re-scoring | MUST PASS |

**Tier 4c: Writing Quality Tests**

| Test | Validates | CI Gate |
|------|-----------|---------|
| Keyword coverage | Tailored resume contains >=7 of top 10 JD keywords | REPORT ONLY |
| Section completeness | All 6 required resume sections present | MUST PASS |
| PDF page count | Compiled resume PDF is exactly 2 pages | MUST PASS |
| PDF text extraction | Key sections extractable via pymupdf | MUST PASS |
| Cover letter word count | 280-380 words, validated at generation time | MUST PASS |
| Cover letter banned phrases | None of 11 banned phrases present | MUST PASS |
| Brace balance | Balanced braces before compilation | MUST PASS |
| Writing quality floor | AI quality score >= 6/10 on all dimensions | REPORT ONLY |

**Tier 4d: Self-Improvement Tests**

| Test | Validates | CI Gate |
|------|-----------|---------|
| Low-risk auto-apply | Broken scraper → auto_applied adjustment | MUST PASS |
| Medium-risk notification | Score threshold shift → notification flag set | MUST PASS |
| High-risk blocks | Prompt change → awaiting_approval status | MUST PASS |
| Rollback trigger | Worsened metrics → previous_state restored | MUST PASS |
| Cooldown enforcement | Reverted adjustment → not re-proposed for 5 runs | MUST PASS |
| Conflict detection | Contradictory adjustments → flagged | REPORT ONLY |
| User feedback ingestion | Flagged score → ground truth entry created | MUST PASS |

**Updated CI architecture**:
```
MUST PASS (parallel, ~5 min)
├── lint-check
├── unit-tests
├── security-tests
├── frontend-build
├── data-quality-tests (4b)
├── writing-quality-tests (4c, structural only)
└── self-improvement-tests (4d)

REPORT ONLY (parallel, ~15 min)
├── contract-tests
├── integration-tests
├── e2e-tests
├── ai-quality (golden dataset)
├── score-determinism (4b)
├── writing-quality-ai (4c, AI-scored)
└── conflict-detection (4d)
```

---

## 7. Section 6: Fargate / Glassdoor + Scraper Fixes (Phase 2.5b)

### Design

**Glassdoor via Fargate + Playwright**:
- Docker image: `Dockerfile.playwright` with Chromium + Scrapling
- ECR push to `385017713886.dkr.ecr.eu-west-1.amazonaws.com`
- Activate existing `PlaywrightTaskDef` in template.yaml
- Step Functions: Fargate task runs in parallel alongside Lambda scrapers
- Auth: dedicated Glassdoor service account, credentials in SSM SecureString. Playwright logs in, scrapes, logs out. Session cookie cached in S3 (~24h reuse).
- Rate limit: max 50 detail pages per run
- Fallback: Fargate failure → pipeline continues without Glassdoor ("Continue On Fail")

**IrishJobs 403 fix**:
- Detail pages return 403 → 18 jobs with empty descriptions
- Try: different User-Agent, cookie handling, Web Unlocker route
- Fallback: cross-reference with Indeed/LinkedIn for same title+company to enrich descriptions

**GradIreland fix**:
- Drupal template changed → 0 jobs returned
- Fresh HTML inspection, update selectors
- If still failing after fix, self-improvement auto-disables (3-day zero-yield)

**DeepSeek removal**:
- Remove from failover chain entirely (accessible via NVIDIA, OpenRouter)
- Remove SSM parameter `/naukribaba/deepseek_api_key`
- Update `ai_client.py` provider list

**OpenRouter fix**:
- Check SSM `/naukribaba/openrouter_api_key` and model name config
- Likely model rename — update config or remove if deprecated

**Scraper architecture after this phase**:
```
Step Functions (parallel)
├── Lambda: LinkedIn (Web Unlocker)      ✅
├── Lambda: Indeed (Web Unlocker)        ✅
├── Lambda: Jobs.ie (Web Unlocker)       ✅
├── Lambda: IrishJobs (Web Unlocker)     ✅ (+ 403 fix)
├── Lambda: Adzuna (API)                 ✅
├── Lambda: YC (HTTP)                    ✅
├── Lambda: HN (HTTP)                    ✅
├── Fargate: Glassdoor (Playwright)      NEW
├── Lambda: GradIreland (HTTP)           FIX
└── Unified Dedup (canonical hash)
```

---

## 8. Section 7: Unified v2 Roadmap

See companion document: `2026-04-03-unified-grand-plan.md`

---

## 9. Backlog (Out of Scope)

- **Contact finder quality**: Finds real people but wrong role, bad intro messages. Phase 3.4.
- **Application outcome feedback**: Applied → Interview → Offer/Rejected as ground truth for scoring. Phase 3.4 → feeds back to 2.9.
- **Deeper AI job analysis**: Structured JD extraction (required vs nice-to-have skills, seniority, tech stack, red flags). Deferred to Phase 3.2 (Research). The keyword analysis in Section 3 is a lighter version that extracts top 10 skills — full structured extraction is a Research stage concern.
