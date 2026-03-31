# Phase 2E Testing & QA Suite — Design Specification

**Date**: 2026-03-31
**Status**: Approved
**Depends on**: Phase 2E Lambda + State Machine implementation (Tasks 1-5)

---

## 1. Why This Matters

The Phase 2E pipeline has 17 Lambda functions, 2 state machines, 4 new Supabase tables, and a React frontend — all built by subagents without manual testing. Before deploying, we need automated verification across multiple dimensions: correctness, security, AI quality, data integrity, and user-facing behavior. This suite also integrates into CI/CD so every future change is validated.

---

## 2. Test Tiers

### Tier 1: Unit Tests (pytest + moto)

**Scope**: Every Lambda function in isolation.

**Mocking strategy**:
- AWS services: `moto` (SSM, S3, Step Functions)
- HTTP calls: `respx` (Apify, Adzuna, Algolia, Groq, WorkAtAStartup)
- Supabase: Lightweight mock client returning fixture data

**Tests per scraper Lambda** (4 scrapers x 4 tests = 16):
- Happy path: valid input → normalized jobs returned
- Cache hit: recent jobs_raw entries exist → returns `{cached: True}`, no scrape
- Budget exceeded (Apify scrapers): monthly cost >= limit → returns `{skipped: "budget_exceeded"}`
- Error handling: API/actor failure → returns `{count: 0, error: "..."}`, no crash

**Tests per pipeline Lambda** (13 Lambdas x 2-4 tests = ~35):
- `load_config`: returns merged config with query_hash, handles missing search config
- `merge_dedup`: cross-source dedup keeps richest version, filters already-scored jobs
- `score_batch`: bulk query, AI response parsing (valid JSON, malformed JSON, markdown-wrapped JSON), cache write, min_score filtering, uuid job_id generation
- `tailor_resume`: light_touch vs full rewrite prompt difference, tex_content field usage, S3 write
- `compile_latex`: tectonic FileNotFoundError fallback, successful compilation (mocked subprocess)
- `generate_cover_letter`: AI call, S3 write, handles missing resume
- `find_contacts`: Apify search, LinkedIn URL extraction, handles zero results
- `save_job`: presigned URL generation, handles missing compile_result/cover_compile_result
- `save_metrics`: writes per-scraper metrics, handles empty scraper_results
- `send_email`: HTML escaping, handles zero matches, SMTP mock
- `self_improve`: unhealthy scraper detection (3-day zero), AI adjustment parsing
- `notify_error`: sends error email, handles SMTP failure
- `check_expiry`: marks 404/410 as expired, ignores network errors, uses job_id not id

**Normalizer fuzz tests** (6 normalizer functions x 3 edge cases = 18):
- Missing required fields (no title, no company) → returns None
- HTML entities in all fields → properly unescaped
- Extremely long strings → truncated to schema limits
- Unicode edge cases (CJK, emoji, RTL text)
- Empty dict input → returns None
- Nested None values → no crash

**Run time**: < 2 minutes
**CI gate**: MUST PASS

### Tier 2: Security Tests

**Scope**: Multi-tenant isolation, auth, input sanitization.

**RLS policy validation** (4 tables x 2 tests = 8):
- User A's JWT cannot SELECT user B's rows (jobs, pipeline_metrics, self_improvement_config)
- Service role can read/write all tables
- Anonymous role can read jobs_raw but not write
- Test with real Supabase using two test users

**API auth tests** (6):
- No token → 401 on all protected endpoints
- Expired token → 401
- Valid token → 200 on owned resources
- Valid token → 403/404 on other user's resources
- Rate limiting: 6th manual pipeline trigger in a day → 429
- Concurrent execution limit: 2nd trigger while running → 409

**Input sanitization** (4):
- XSS in job description: `<script>alert(1)</script>` → stored safely, rendered escaped
- SQL injection in search query → no effect on Supabase (parameterized by default, but verify)
- SSRF in check_expiry: `apply_url` pointing to internal IP → should be blocked or timeout safely
- Oversized payload: 1MB job description → handled gracefully (truncated or rejected)

**Run time**: ~3 minutes
**CI gate**: MUST PASS

### Tier 3: Contract Tests (State Machine I/O)

**Scope**: Verify each state's output shape matches the next state's expected input.

**Implementation**: Python tests that import each Lambda handler, call it with fixture input, and validate the output against a JSON schema. Then feed that output as input to the next handler in the chain.

**Daily pipeline chain** (7 transitions):
1. `{}` + `{user_id}` → `load_config` → output has `user_id`, `queries[]`, `query_hash`, `min_match_score`
2. Config → each scraper → output has `count`, `source`, optional `cached`, `error`, `apify_cost_cents`
3. Scraper results + config → `merge_dedup` → output has `new_job_hashes[]`, `total_new`
4. Dedup result + config → `score_batch` → output has `matched_items[]` (each: `job_hash`, `user_id`, `light_touch`), `matched_count`
5. Each matched_item → `tailor_resume` → output has `job_hash`, `tex_s3_key`, `user_id`
6. Tailor result → `compile_latex` → output has `job_hash`, `pdf_s3_key`, `user_id`, `doc_type`
7. All results accumulated → `save_job` → output has `job_hash`, `user_id`, `saved`

**Single-job pipeline chain** (5 transitions):
1. `{user_id, job_hash}` → `score_batch` → `matched_items[0]` has `light_touch`
2. Matched item → `tailor_resume` → has `tex_s3_key`
3. → `compile_latex` → has `pdf_s3_key`
4. → `generate_cover_letter` → has `tex_s3_key` (cover letter)
5. → `compile_latex` → has `pdf_s3_key` (cover letter)

**Error path tests** (4):
- Scraper failure in Parallel → returns `{count: 0, error}`, pipeline continues
- score_batch returns `matched_count: 0` → Map state processes empty list (no crash)
- compile_latex returns `pdf_s3_key: null` → save_job handles gracefully
- cover_letter fails → save_job receives state without cover_compile_result

**Run time**: ~2 minutes
**CI gate**: REPORT ONLY (runs fast but may break during active development)

### Tier 4: AI Scoring Quality (Golden Dataset)

**Scope**: Scoring accuracy, consistency, and regression detection.

**Golden dataset** (`tests/quality/golden_dataset.json`):
- 25 JD+resume pairs, human-labeled into 4 categories:
  - `strong_match` (5 pairs): expected score 80-100
  - `good_match` (8 pairs): expected score 60-79
  - `weak_match` (7 pairs): expected score 30-59
  - `no_match` (5 pairs): expected score 0-29

**Tests**:
- Each pair scores within its expected range (±15 tolerance)
- Consistency: same pair scored twice → within ±5
- Distribution: at least 3 of 4 categories represented in results (not all the same score)
- Light-touch threshold: strong_match pairs get `light_touch: true`, others get `false`
- Score components: `ats_score`, `hiring_manager_score`, `tech_recruiter_score` all present and 0-100

**Scraper yield bounds**:
- Each scraper with test queries returns 1-500 jobs (not 0, not 10000)
- At least 80% of returned jobs have non-empty title + company + description

**Score drift detection**:
- Store baseline scores in `tests/quality/baseline_scores.json`
- If average score shifts by > 20% from baseline, flag as warning
- Update baseline after intentional prompt/model changes

**Run time**: ~5 minutes (real AI calls, cached after first run)
**CI gate**: REPORT ONLY (posts summary as PR comment)

### Tier 5: Integration Tests (Real Supabase)

**Scope**: Full Lambda chains with real database and S3.

**Setup/teardown**:
- Dedicated test user in Supabase (created in conftest.py, cleaned up after)
- Test data prefix in S3: `test/{run_id}/`
- Seed `jobs_raw` with 5 known test jobs before each test

**Data integrity tests** (6):
- Same job from 2 sources → dedup keeps version with longer description
- `md5(company|title|desc[:500])` produces identical hash across normalizers
- Every `jobs.job_hash` references a valid `jobs_raw` row (FK integrity)
- Partial pipeline failure → no orphaned rows in jobs without job_hash
- Backfill migration: jobs with NULL job_hash get populated correctly
- AI cache: same prompt → cache hit, expired cache → fresh call

**Pipeline chain tests** (4):
- `load_config → merge_dedup → score_batch` with seeded data → produces scored jobs
- `tailor_resume → compile_latex` → tex written to S3 (PDF depends on tectonic availability)
- `save_job` → presigned URLs are valid and accessible
- `self_improve` with seeded 3-day zero metrics → detects unhealthy scraper

**Shadow mode isolation** (2):
- Step Functions execution writes to `jobs_raw` but not `jobs` (when shadow flag set)
- Parallel old pipeline + new pipeline produce comparable job counts

**Run time**: ~10 minutes
**CI gate**: REPORT ONLY

### Tier 6: Frontend E2E Tests (Playwright)

**Scope**: Critical user journeys through the React app.

**Setup**: Vite dev server (port 5173) + API server (port 8000) started as fixtures.

**Test scenarios** (8 specs):
1. **Login flow**: email/password → dashboard loads → correct user name shown
2. **Dashboard**: job table renders, scores visible, status badges, filter toggles
3. **Add Job**: paste JD → trigger pipeline → poll for result → job appears in table
4. **Job Workspace**: click job → overview tab → scores card → resume tab → cover letter tab
5. **Settings**: view profile → update fields → save → refresh → changes persist
6. **Auth edge cases**: expired token → redirect to login, protected route without auth → redirect
7. **XSS defense**: paste `<script>alert(1)</script>` as job title → renders as text, not executed
8. **Responsive**: key flows work at mobile viewport (375px)

**Screenshots**: Captured on failure, attached to CI artifacts.

**Run time**: ~5 minutes
**CI gate**: REPORT ONLY

### Tier 7: Stress & Resilience Tests

**Scope**: System limits, error recovery, rollback.

**Tests** (6):
- 100 jobs through `score_batch` → completes within 300s timeout
- 3 concurrent pipeline triggers → rate limiter returns 409 for 2nd and 3rd
- Apify budget limit hit at job 25 of 50 → remaining 25 skipped, first 25 saved
- 1 of 6 scrapers throws exception → Parallel state catches, pipeline continues with 5
- AI provider returns 500 for all calls → score_batch returns meaningful error
- Rollback: disable EventBridge schedules → re-enable GitHub Actions cron → old pipeline runs

**Run time**: ~15 minutes
**Schedule**: Weekly (Sunday 5:00 UTC), not on every PR

---

## 3. CI/CD Architecture

### GitHub Actions Workflow: `test.yml`

Triggers: PR opened, push to any branch.

```
┌───────────────────────────────────────────────────┐
│  MUST PASS (parallel, ~3 min total)               │
│                                                    │
│  ┌─────────────┐ ┌──────────┐ ┌───────────────┐  │
│  │ lint-check   │ │unit-tests│ │security-tests │  │
│  │ ruff + eslint│ │pytest    │ │RLS + auth +   │  │
│  │ mypy + tsc   │ │moto+respx│ │sanitization   │  │
│  │ ~1 min       │ │~2 min    │ │~3 min         │  │
│  └─────────────┘ └──────────┘ └───────────────┘  │
│  ┌─────────────┐                                   │
│  │frontend-build│                                  │
│  │npm run build │                                  │
│  │~1 min        │                                  │
│  └─────────────┘                                   │
├───────────────────────────────────────────────────┤
│  REPORT ONLY (parallel, ~10 min total)            │
│                                                    │
│  ┌──────────────┐ ┌────────────┐ ┌─────────────┐ │
│  │contract-tests│ │integration │ │e2e-tests    │ │
│  │I/O chain     │ │real Supabase│ │Playwright   │ │
│  │~2 min        │ │~10 min     │ │~5 min       │ │
│  └──────────────┘ └────────────┘ └─────────────┘ │
│  ┌──────────────┐                                  │
│  │ai-quality    │                                  │
│  │golden dataset│                                  │
│  │~5 min        │                                  │
│  └──────────────┘                                  │
└───────────────────────────────────────────────────┘
```

### Deploy Workflow: `deploy.yml` (modified)

Triggers: Push to main.

Added gate: only deploys if `test.yml` MUST PASS jobs succeeded.

### Stress Workflow: `stress-test.yml`

Triggers: Weekly schedule (Sunday 5:00 UTC) + manual dispatch.

Posts summary to configured notification channel.

---

## 4. Test Dependencies

```
# tests/requirements-test.txt
pytest>=8.0
pytest-asyncio>=0.23
pytest-xdist>=3.5
pytest-cov>=5.0
moto[ssm,s3,stepfunctions]>=5.0
respx>=0.21
httpx>=0.27.0
supabase>=2.0.0
ruff>=0.4
mypy>=1.10
```

Frontend test deps (added to web/package.json devDependencies):
```json
{
  "@playwright/test": "^1.40",
  "typescript": "^5.4"
}
```

---

## 5. Directory Structure

```
tests/
├── conftest.py                    # Shared: mock SSM params, Supabase fixtures, test user
├── requirements-test.txt
├── unit/
│   ├── conftest.py                # moto setup, respx mocks
│   ├── test_normalizers.py        # Fuzz + edge cases for all 6 normalizers
│   ├── test_scrape_apify.py       # Cache hit, budget, happy path, error
│   ├── test_scrape_adzuna.py
│   ├── test_scrape_hn.py
│   ├── test_scrape_yc.py
│   ├── test_load_config.py
│   ├── test_merge_dedup.py
│   ├── test_score_batch.py
│   ├── test_tailor_resume.py
│   ├── test_compile_latex.py
│   ├── test_save_job.py
│   ├── test_send_email.py
│   ├── test_self_improve.py
│   └── test_check_expiry.py
├── security/
│   ├── conftest.py                # Two test users, JWT generation
│   ├── test_rls_policies.py       # User isolation per table
│   ├── test_api_auth.py           # Auth bypass, expired token, rate limiting
│   └── test_input_sanitization.py # XSS, SSRF, oversized payloads
├── contract/
│   ├── conftest.py                # Fixture data for each state
│   ├── test_daily_pipeline_chain.py
│   ├── test_single_job_chain.py
│   └── test_error_paths.py
├── quality/
│   ├── golden_dataset.json        # 25 JD+resume pairs with expected ranges
│   ├── baseline_scores.json       # Last known good scores for drift detection
│   └── test_scoring_quality.py
├── integration/
│   ├── conftest.py                # Real Supabase, test user, seed data
│   ├── test_data_integrity.py     # Dedup, hash, FK, orphans
│   ├── test_pipeline_chain.py     # Multi-Lambda chains
│   └── test_shadow_mode.py
├── e2e/
│   ├── playwright.config.ts
│   ├── test_login.spec.ts
│   ├── test_dashboard.spec.ts
│   ├── test_add_job.spec.ts
│   ├── test_job_workspace.spec.ts
│   ├── test_settings.spec.ts
│   ├── test_auth_edge.spec.ts
│   ├── test_xss_defense.spec.ts
│   └── test_responsive.spec.ts
└── stress/
    ├── test_batch_scoring.py
    ├── test_concurrent_pipelines.py
    ├── test_budget_limit.py
    ├── test_scraper_failure.py
    ├── test_ai_failure.py
    └── test_rollback.py
```

---

## 6. Success Criteria

1. **Unit tests**: 100% Lambda handler coverage, all pass
2. **Security**: Zero RLS bypass, zero auth bypass, zero XSS rendering
3. **Contract**: All 12 state transitions produce valid output for next state
4. **AI quality**: 80%+ of golden dataset pairs score within expected range
5. **Integration**: All data integrity tests pass, no orphaned rows
6. **E2E**: All 8 critical user journeys complete without error
7. **Stress**: Pipeline handles 100 jobs within timeout, rate limiting works
8. **CI/CD**: PR merges blocked when MUST PASS jobs fail, deploy only after green

---

## 7. Not In Scope

- Performance benchmarking (Lambda cold start times, P99 latency)
- Cross-browser testing beyond Chromium (Safari, Firefox)
- Accessibility auditing (WCAG compliance)
- Load testing beyond single-user stress (multi-tenant load)
- Visual regression testing (screenshot diffing)

These are valuable but can be added in a future testing phase.
