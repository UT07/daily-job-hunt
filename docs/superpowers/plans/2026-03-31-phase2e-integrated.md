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
| A2 | Unit tests: normalizers + ai_helper | Test | Yes (after A1) |
| A3 | Unit tests: scrapers | Test | Yes (after A1) |
| A4 | Unit tests: pipeline Lambdas | Test | Yes (after A1) |
| A5 | Contract tests (state machine I/O) | Test | Yes (after A1) |
| A6 | CI/CD GitHub Actions workflow (test.yml + deploy gate) | Infra | After A2-A5 |
| A7 | Fix CompileLatex: tectonic Lambda Layer or Docker | Fix | After A1 |
| A8 | Fix EventBridge user_id + Gmail SSM param names | Fix | Before A9 |
| A9 | Create SSM parameters + deploy + smoke test | Deploy | After A6-A8 |

### PHASE 2: INTEGRATION + FRONTEND

| # | Task | Type | Parallel? |
|---|------|------|-----------|
| B1 | API endpoints (pipeline run, status, polling, compile-latex) | Backend | No |
| B2 | Security tests (RLS, auth, sanitization) | Test | Yes (after B1) |
| B3 | Frontend: pipeline status + Run Pipeline | Frontend | After B1 |
| B4 | Frontend: Add Job refactor (async) | Frontend | After B1 |
| B5 | Frontend: skills, cards, PDF preview, AI model | Frontend | Yes |
| B6 | Frontend: onboarding wizard | Frontend | Yes |
| B7 | Frontend: in-app notifications (badge + toast) | Frontend | Yes |
| B8 | Frontend: source control in Settings | Frontend | Yes |
| B9 | Frontend: job expiry + rejected dimming | Frontend | After B1 |
| B10 | Frontend: manual job editing | Frontend | Yes |
| B11 | Playwright E2E tests | Test | After B3-B10 |
| B12 | Scoring consistency fix | Backend | Yes |
| B13 | Score-first tailoring | Backend | Yes |
| B14 | Resume versioning | Full-stack | Yes |
| B15 | User profile from resume | Full-stack | Yes |
| B16 | Stale nudge + follow-up reminder Lambdas + EventBridge | Backend | Yes |

### PHASE 3: QUALITY VALIDATION

| # | Task | Type | Parallel? |
|---|------|------|-----------|
| C1 | AI scoring quality (golden dataset, 25 pairs) | Test | Yes |
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
**Also modifies** `.github/workflows/deploy.yml` to require test.yml passing before deploy.

---

## Task A7: Fix CompileLatex — Tectonic Lambda Layer

**Problem:** `CompileLatexFunction` uses `Runtime: python3.11` but `tectonic` is only in the Docker image. Current code returns `pdf_s3_key: null` — meaning zero PDFs in production.

**Fix:** Create a tectonic Lambda Layer with the static musl binary (same approach as `Dockerfile.lambda` stage 1).

- [ ] **Step 1: Create tectonic layer build script**

Create `layer-tectonic/build.sh`:
```bash
#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
rm -rf bin/
mkdir -p bin/
wget -qO /tmp/tectonic.tar.gz https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.15.0/tectonic-0.15.0-x86_64-unknown-linux-musl.tar.gz
tar xzf /tmp/tectonic.tar.gz -C bin/
chmod +x bin/tectonic
echo "Tectonic layer built: $(ls -la bin/tectonic)"
```

- [ ] **Step 2: Add tectonic layer to template.yaml**

```yaml
  TectonicLayer:
    Type: AWS::Serverless::LayerVersion
    Properties:
      LayerName: naukribaba-tectonic
      ContentUri: layer-tectonic/
      CompatibleRuntimes:
        - python3.11
```

- [ ] **Step 3: Add TectonicLayer to CompileLatexFunction**

Add `!Ref TectonicLayer` to CompileLatexFunction's Layers list.

- [ ] **Step 4: Update compile_latex.py to find tectonic in /opt/bin/**

Lambda layers extract to `/opt/`. Update the subprocess call to use `/opt/bin/tectonic` and add `/opt/bin` to PATH.

- [ ] **Step 5: Commit**

---

## Task A8: Fix EventBridge User ID + Gmail SSM Param Names

- [ ] **Step 1: Fix EventBridge DailyPipelineSchedule input**

Change `Input: '{"user_id": "default"}'` in template.yaml to use the actual user UUID from Supabase. Query for it:
```bash
npx supabase db query "SELECT id FROM users LIMIT 1" --linked
```
Then update the EventBridge rule. (For multi-tenant later, this becomes a dispatch Lambda.)

- [ ] **Step 2: Fix Gmail SSM param names**

The Lambdas use `/naukribaba/GMAIL_USER` and `/naukribaba/GMAIL_APP_PASSWORD`.
The .env has `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD`.
In A9 SSM creation, use `$GMAIL_ADDRESS` for the GMAIL_USER parameter:
```bash
aws ssm put-parameter --name "/naukribaba/GMAIL_USER" --value "$GMAIL_ADDRESS" --type SecureString --region eu-west-1 --overwrite
```

- [ ] **Step 3: Commit**

---

## Task A9: Create SSM Parameters + SAM Deploy + Smoke Test

- [ ] **Step 1: Source .env and create all SSM parameters**

```bash
source .env

# AI providers (5-provider failover chain)
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

# Email (note: .env has GMAIL_ADDRESS, Lambdas expect GMAIL_USER)
aws ssm put-parameter --name "/naukribaba/GMAIL_USER" --value "$GMAIL_ADDRESS" --type SecureString --region eu-west-1 --overwrite
aws ssm put-parameter --name "/naukribaba/GMAIL_APP_PASSWORD" --value "$GMAIL_APP_PASSWORD" --type SecureString --region eu-west-1 --overwrite
```

- [ ] **Step 2: Verify all unit + contract tests pass**

```bash
pytest tests/unit/ tests/contract/ -v
```

- [ ] **Step 3: Build and deploy**

```bash
sam build && sam deploy --guided
```

- [ ] **Step 4: Smoke test Adzuna scraper**

```bash
aws lambda invoke --function-name naukribaba-scrape-adzuna --payload '{"queries":["software engineer"],"query_hash":"test"}' /tmp/out.json --region eu-west-1
cat /tmp/out.json
```

- [ ] **Step 5: Smoke test daily pipeline**

Start Step Functions execution with `{"user_id": "<actual-user-uuid>"}`.

---

## Tasks B1-B16: Integration + Frontend

These follow the original Phase 2E plan Tasks 7-19 but with key changes.
See `docs/superpowers/plans/2026-03-31-phase2e-step-functions-migration.md` for code details.

**Key changes from original plan:**
- B1 adds `POST /api/compile-latex` endpoint (needed for Phase 2B editor, spec section 5)
- B2 (Security tests) runs alongside B1 — tests auth, RLS, rate limiting
- B7-B9 split from original bundled "notifications + source control + expiry" into 3 tasks
- B11 (Playwright E2E) runs after all frontend tasks — tests critical user journeys
- B16 (NEW) adds stale nudge + follow-up reminder Lambdas and EventBridge rules (spec section 9)

---

## Task B16: Stale Nudge + Follow-Up Reminder Lambdas

**Spec section 9 — not yet implemented.**

- [ ] **Step 1: Create `lambdas/pipeline/send_stale_nudges.py`**

Lambda that queries jobs with `application_status = 'New'` older than 7 days, sends digest email.

- [ ] **Step 2: Create `lambdas/pipeline/send_followup_reminders.py`**

Lambda that queries jobs with `application_status = 'Applied'` and no status change in 7+ days.

- [ ] **Step 3: Add both Lambdas to template.yaml**

- [ ] **Step 4: Add EventBridge rules**

```yaml
  StaleNudgeSchedule:
    Type: AWS::Events::Rule
    Properties:
      Name: naukribaba-stale-nudge
      Description: Weekly stale job nudges (Monday 9:00 UTC)
      ScheduleExpression: "cron(0 9 ? * MON *)"
      State: DISABLED

  FollowUpSchedule:
    Type: AWS::Events::Rule
    Properties:
      Name: naukribaba-followup-reminder
      Description: Daily follow-up reminders (10:00 UTC)
      ScheduleExpression: "cron(0 10 ? * * *)"
      State: DISABLED
```

- [ ] **Step 5: Commit**

---

## Tasks C1-C3: Quality Validation

See `docs/superpowers/plans/2026-03-31-testing-qa-suite.md` Tasks 7, 8, 10.

Run AFTER all features are built but BEFORE shadow mode. This is the quality gate.

---

## Tasks D1-D2: Shadow Mode + Cutover

See `docs/superpowers/plans/2026-03-31-phase2e-step-functions-migration.md` Tasks 20-21.

Only proceed when all quality tests pass.
