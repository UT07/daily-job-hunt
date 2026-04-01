# Phase 2E: Integrated Implementation + Testing Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete Phase 2E Step Functions migration with comprehensive test coverage at every stage. Tests are written alongside features, not bolted on after.

**Principle:** Validate before deploy. Test alongside develop. Gate merges with CI.

**Specs:**
- `docs/superpowers/specs/2026-03-31-phase2e-step-functions-migration-design.md` (pipeline)
- `docs/superpowers/specs/2026-03-31-testing-qa-suite-design.md` (testing)

---

## Progress So Far

| Task | Status |
|------|--------|
| Supabase tables + data migration | **DONE** (96 jobs backfilled) |
| Lambda Layer (shared deps) | **DONE** |
| Scraper Lambdas (4 + normalizers) | **DONE** (reviewed + fixed) |
| Pipeline Lambdas (13 functions) | **DONE** (reviewed + fixed) |
| Step Functions state machines | **DONE** (reviewed + fixed) |
| ai_helper.py 5-provider failover | **DONE** (Groq→NVIDIA→DeepSeek→OpenRouter→Qwen) |
| Code review fixes (8 critical) | **DONE** |

---

## Remaining Tasks (execute in this order)

### PHASE 1.5: VALIDATE BEFORE DEPLOY

| # | Task | Type | Parallel? |
|---|------|------|-----------|
| A1 | Test infrastructure + conftest | Setup | No (foundation) |
| A2 | Unit tests: normalizers | Test | Yes (after A1) |
| A3 | Unit tests: scrapers | Test | Yes (after A1) |
| A4 | Unit tests: pipeline Lambdas | Test | Yes (after A1) |
| A5 | Contract tests (state machine I/O) | Test | Yes (after A1) |
| A6 | CI/CD GitHub Actions workflow | Infra | After A2-A5 |
| A7 | Create SSM parameters + deploy | Deploy | After A6 |

### PHASE 2: INTEGRATION + FRONTEND

| # | Task | Type | Parallel? |
|---|------|------|-----------|
| B1 | API endpoints (pipeline run, status, polling) | Backend | No |
| B2 | Security tests (RLS, auth, sanitization) | Test | Yes (after B1) |
| B3 | Frontend: pipeline status + Run Pipeline | Frontend | After B1 |
| B4 | Frontend: Add Job refactor (async) | Frontend | After B1 |
| B5 | Frontend: skills, cards, PDF preview, AI model | Frontend | Yes |
| B6 | Frontend: onboarding wizard | Frontend | Yes |
| B7 | Frontend: notifications, source control, expiry | Frontend | Yes |
| B8 | Frontend: manual job editing | Frontend | Yes |
| B9 | Playwright E2E tests | Test | After B3-B8 |
| B10 | Scoring consistency fix | Backend | Yes |
| B11 | Score-first tailoring | Backend | Yes |
| B12 | Resume versioning | Full-stack | Yes |
| B13 | User profile from resume | Full-stack | Yes |

### PHASE 3: QUALITY VALIDATION

| # | Task | Type | Parallel? |
|---|------|------|-----------|
| C1 | AI scoring quality (golden dataset) | Test | Yes |
| C2 | Integration tests (data integrity) | Test | Yes |
| C3 | Stress tests | Test | Yes |

### PHASE 4: SHADOW MODE + CUTOVER

| # | Task | Type |
|---|------|------|
| D1 | Shadow mode testing (3 days) | Testing |
| D2 | Cutover + cleanup | Deploy |

---

## Task A1: Test Infrastructure

See `docs/superpowers/plans/2026-03-31-testing-qa-suite.md` Task 1 for full details.

Creates: `tests/conftest.py`, `tests/unit/conftest.py`, `pytest.ini`, `tests/requirements-test.txt`, all `__init__.py` files.

---

## Tasks A2-A5: Unit + Contract Tests

See `docs/superpowers/plans/2026-03-31-testing-qa-suite.md` Tasks 2-4 and 6.

These can all run in parallel after A1. ~70 unit tests + ~15 contract tests.

---

## Task A6: CI/CD GitHub Actions

See `docs/superpowers/plans/2026-03-31-testing-qa-suite.md` Task 11.

Creates `.github/workflows/test.yml` with MUST PASS gates (unit + security + lint + build).

---

## Task A7: SSM Parameters + SAM Deploy

- [ ] **Step 1: Create SSM parameters for all API keys**

```bash
# AI providers
aws ssm put-parameter --name "/naukribaba/GROQ_API_KEY" --value "$GROQ_API_KEY" --type SecureString --region eu-west-1 --overwrite
aws ssm put-parameter --name "/naukribaba/NVIDIA_API_KEY" --value "$NVIDIA_API_KEY" --type SecureString --region eu-west-1 --overwrite
aws ssm put-parameter --name "/naukribaba/DEEPSEEK_API_KEY" --value "$DEEPSEEK_API_KEY" --type SecureString --region eu-west-1 --overwrite
aws ssm put-parameter --name "/naukribaba/OPENROUTER_API_KEY" --value "$OPENROUTER_API_KEY" --type SecureString --region eu-west-1 --overwrite
aws ssm put-parameter --name "/naukribaba/QWEN_API_KEY" --value "$QWEN_API_KEY" --type SecureString --region eu-west-1 --overwrite

# Scraper keys
aws ssm put-parameter --name "/naukribaba/APIFY_API_KEY" --value "$APIFY_API_KEY" --type SecureString --region eu-west-1 --overwrite
aws ssm put-parameter --name "/naukribaba/ADZUNA_APP_ID" --value "$ADZUNA_APP_ID" --type SecureString --region eu-west-1 --overwrite
aws ssm put-parameter --name "/naukribaba/ADZUNA_APP_KEY" --value "$ADZUNA_APP_KEY" --type SecureString --region eu-west-1 --overwrite

# Supabase
aws ssm put-parameter --name "/naukribaba/SUPABASE_URL" --value "$SUPABASE_URL" --type SecureString --region eu-west-1 --overwrite
aws ssm put-parameter --name "/naukribaba/SUPABASE_SERVICE_KEY" --value "$SUPABASE_SERVICE_KEY" --type SecureString --region eu-west-1 --overwrite

# Email
aws ssm put-parameter --name "/naukribaba/GMAIL_USER" --value "$GMAIL_USER" --type SecureString --region eu-west-1 --overwrite
aws ssm put-parameter --name "/naukribaba/GMAIL_APP_PASSWORD" --value "$GMAIL_APP_PASSWORD" --type SecureString --region eu-west-1 --overwrite
```

- [ ] **Step 2: Verify all unit tests pass**

```bash
pytest tests/unit/ tests/contract/ -v
```

- [ ] **Step 3: Build and deploy**

```bash
sam build && sam deploy --guided
```

- [ ] **Step 4: Smoke test scrapers**

```bash
aws lambda invoke --function-name naukribaba-scrape-adzuna --payload '{"queries":["software engineer"],"query_hash":"test"}' /tmp/out.json --region eu-west-1
cat /tmp/out.json
```

- [ ] **Step 5: Smoke test daily pipeline**

Start a Step Functions execution via AWS Console or CLI with `{"user_id": "<your-user-id>"}`.

---

## Tasks B1-B13: Integration + Frontend

These follow the original Phase 2E plan Tasks 7-19. See `docs/superpowers/plans/2026-03-31-phase2e-step-functions-migration.md` for full details.

**Key changes from original plan:**
- B2 (Security tests) runs alongside B1 (API endpoints) — tests auth, RLS, rate limiting
- B9 (Playwright E2E) runs after all frontend tasks — tests critical user journeys
- Each frontend task should include a basic Playwright test for the feature it adds

---

## Tasks C1-C3: Quality Validation

See `docs/superpowers/plans/2026-03-31-testing-qa-suite.md` Tasks 7, 8, 10.

Run AFTER all features are built but BEFORE shadow mode. This is the quality gate.

---

## Tasks D1-D2: Shadow Mode + Cutover

See `docs/superpowers/plans/2026-03-31-phase2e-step-functions-migration.md` Tasks 20-21.

Only proceed when all quality tests pass.
