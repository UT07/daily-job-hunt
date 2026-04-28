# Deployment Safety + Observability Roadmap

> **For agentic workers:** This is a **multi-phase roadmap**, not a single executable plan. Each phase below should be expanded into its own detailed plan in `docs/superpowers/plans/2026-04-27-deployment-safety-phaseN-*.md` (with TDD-granular steps) before execution. Use superpowers:writing-plans for each phase. The roadmap itself is the spec.

**Goal:** Ship a 6-phase deployment safety stack for NaukriBaba — feature flags, Lambda canary deploys, staging environment, structured observability, error tracking, and post-deploy smoke tests — so every merge to `main` lands on prod with auto-rollback and zero accidental user-visible breakage.

**Architecture:** Layered, cheapest-to-leverage first. Phase 1 (PostHog flags) decouples *deploy* from *release* so risky code can ship dark. Phase 2 (SAM `DeploymentPreference` + CodeDeploy) protects the deploy moment with linear/canary traffic shifting and CloudWatch-alarm rollback. Phase 3 (staging Supabase + SAM stack + Netlify branch deploy) gives a real pre-prod target. Phases 4–5 add structured observability (structlog → CloudWatch Logs Insights, Embedded Metric Format, AWS X-Ray, Sentry) so canary alarms have rich signals. Phase 6 ties it all together with synthetic smoke tests gating each canary promotion. Each phase is shippable on its own — later phases assume earlier ones, but value compounds.

**Tech Stack:**
- AWS SAM/CloudFormation + CodeDeploy (deploy orchestration, canary)
- PostHog (feature flags + analytics; Python + JS SDKs)
- Sentry (error tracking; `sentry-sdk[fastapi]` + `@sentry/react`)
- structlog (Python structured JSON logging)
- AWS X-Ray (distributed tracing)
- Embedded Metric Format (CloudWatch custom metrics from Lambda logs)
- pytest + httpx (backend smoke tests)
- Playwright (frontend critical-path smoke tests)
- GitHub Actions (orchestration; `deploy.yml` extended)

**Spec:** This document. Each phase has its own implementation plan written when execution begins.

---

## Audit — Current State (2026-04-27)

| Concern | Status | Evidence |
|---|---|---|
| Feature flags | ❌ None | No `posthog` in `requirements.txt` or `web/package.json` |
| Lambda canary | ❌ None | `template.yaml` has no `AutoPublishAlias` or `DeploymentPreference` on any of the 28 functions |
| Staging environment | ❌ None | `deploy.yml` deploys `--stack-name job-hunt-api` to one prod stack; one Supabase project; one Netlify site |
| Auto-deploy on merge | ❌ Manual | `deploy.yml` is `workflow_dispatch` only — releases require a human button click |
| Structured logging | ❌ Plain stdlib | `app.py:41` `import logging` + `app.py:85` `logger = logging.getLogger(__name__)`. No JSON, no request IDs, no correlation |
| Distributed tracing | ❌ None | No `Tracing: Active` in any `AWS::Serverless::Function` |
| Custom metrics | ❌ None | No EMF emission, no CloudWatch namespace |
| Dashboards | ❌ None | No `AWS::CloudWatch::Dashboard` resource |
| CloudWatch alarms | ❌ None | No `AWS::CloudWatch::Alarm` for Lambda errors / latency / 5xx |
| Error tracker | ❌ None | No Sentry SDK installed; errors die in CloudWatch logs |
| Smoke tests | ❌ None | No `tests/smoke/` directory; `tests/unit/` and `tests/contract/` only |
| Rollback path | ❌ Manual | `git revert` + `gh workflow run deploy.yml` — typically 8-15 min during which prod is broken |

**Implication:** The auto-apply feature merged in PR #10 (cb2d1d1, 2026-04-24) shipped to prod with none of the above safety nets. Every subsequent change to scoring, tailoring, scrapers, or auto-apply lands the same way. The blast radius of a bad merge is the entire user base immediately.

---

## File Structure (consolidated across all 6 phases)

```
config/
  feature_flags.py                                (CREATE, P1) backend wrapper around posthog-python
  observability.py                                (CREATE, P4) structlog + X-Ray + EMF setup
  sentry_config.py                                (CREATE, P5) sentry-sdk init for Lambda + FastAPI
template.yaml                                     (MODIFY, P2/P3/P4) AutoPublishAlias + DeploymentPreference + Stage param + Tracing + Alarms + Dashboard
samconfig.toml                                    (CREATE, P3) base SAM config
samconfig.staging.toml                            (CREATE, P3) staging stack overrides
samconfig.prod.toml                               (CREATE, P3) prod stack overrides
.github/workflows/
  deploy.yml                                      (MODIFY, P2/P3/P6) auto-deploy on merge, two-env (staging→prod), smoke gate
  smoke.yml                                       (CREATE, P6) reusable smoke-test workflow_call
monitoring/
  alarms.yaml                                     (CREATE, P2) CloudWatch alarms (CFN snippet, included by template.yaml)
  dashboard.json                                  (CREATE, P4) CloudWatch Dashboard JSON
app.py                                            (MODIFY, P1/P4/P5) flag-gate auto-apply + structlog + Sentry init
lambdas/browser/
  ws_connect.py                                   (MODIFY, P4/P5) structlog + Sentry
  ws_disconnect.py                                (MODIFY, P4/P5) structlog + Sentry
  ws_route.py                                     (MODIFY, P1/P4/P5) flag-gate apply step + structlog + Sentry
lambdas/pipeline/
  score_batch.py                                  (MODIFY, P1) flag-gate council vs single-perspective
  tailor_resume.py                                (MODIFY, P1) flag-gate light-touch vs full-rewrite path
  utils/logging.py                                (CREATE, P4) structlog config shared by all pipeline lambdas
requirements.txt                                  (MODIFY, P1/P4/P5) add posthog, structlog, sentry-sdk[fastapi], aws-xray-sdk
web/package.json                                  (MODIFY, P1/P5) add posthog-js, @sentry/react, @sentry/vite-plugin
web/src/lib/
  featureFlags.ts                                 (CREATE, P1) usePostHogFlag hook + provider
  sentry.ts                                       (CREATE, P5) browser Sentry init
web/src/main.jsx                                  (MODIFY, P1/P5) PostHog provider + Sentry init at app boot
web/src/components/
  AutoApplyButton.jsx                             (MODIFY, P1) gate behind useFeatureFlagEnabled('auto_apply')
netlify.toml                                      (MODIFY, P3) branch context for staging
tests/smoke/
  __init__.py                                     (CREATE, P6)
  conftest.py                                     (CREATE, P6) httpx client fixtures, env-aware base URL
  test_health.py                                  (CREATE, P6) /healthz + auth probe
  test_apply_synthetic.py                         (CREATE, P6) end-to-end apply against fixture job
  test_pipeline_smoke.py                          (CREATE, P6) score_batch fixture invocation
web/tests/smoke/
  critical-paths.spec.ts                          (CREATE, P6) Playwright: login + dashboard + apply preview
  playwright.config.ts                            (CREATE, P6) base URL via STAGING_URL/PROD_URL env
docs/superpowers/plans/
  2026-04-27-deployment-safety-phase1-flags.md    (CREATE, P1) detailed sub-plan
  2026-04-27-deployment-safety-phase2-canary.md   (CREATE, P2) detailed sub-plan
  2026-04-27-deployment-safety-phase3-staging.md  (CREATE, P3) detailed sub-plan
  2026-04-27-deployment-safety-phase4-observability.md (CREATE, P4) detailed sub-plan
  2026-04-27-deployment-safety-phase5-sentry.md   (CREATE, P5) detailed sub-plan
  2026-04-27-deployment-safety-phase6-smoke.md    (CREATE, P6) detailed sub-plan
```

---

## Phase Overview

| # | Phase | Estimated Time | Depends On | Why This Order |
|---|---|---|---|---|
| 1 | Feature Flags (PostHog) | ~1 day | — | Highest safety/hour ratio. Lets you merge risky PRs to main without exposing users. |
| 2 | Lambda Canary (SAM `DeploymentPreference`) | ~half day | — | 6 lines of YAML per Lambda; uses default `Errors` metric so it can ship before P4. |
| 3 | Staging Environment | ~1 day | P2 (so canary config carries to staging) | Real pre-prod target for migrations + smoke tests + visual QA. |
| 4 | Observability (structlog, X-Ray, EMF, Dashboards) | ~1.5 days | — (parallel-safe with P1–P3) | Rich signals for canary alarms; Logs Insights queries for incidents. |
| 5 | Error Tracking (Sentry) | ~half day | — (parallel-safe) | Reactive bug capture; release-tagged for blame-by-deploy. |
| 6 | Smoke Tests + Rollback Wiring | ~1 day | P2 (canary hooks), P3 (staging URL), P4 (custom alarms) | Closes the loop: canary's PreTraffic hook runs smoke; alarm trip rolls back automatically. |
| **Total** | | **~5 days of focused work** | | Realistic over 1–2 weeks alongside feature work |

**Critical path:** P1 → P2 → P3 → P6. Observability (P4) and Sentry (P5) can run in parallel branches.

---

## Phase 1 — Feature Flags via PostHog

**Goal:** Decouple *deploying* code from *releasing* it to users. Every risky write path (auto-apply, council scoring, new scrapers) gets a flag check; flags default off; flip per-user from PostHog UI without redeploying.

**Why first:** Cheapest safety win per hour. Today, merging PR #10 immediately exposed every signed-up user to auto-apply. With a flag, the same merge would have shipped dark; you'd flip it on for `254utkarsh@gmail.com` first, then a 10% cohort, then full rollout — all without redeploys, with one-click kill.

**Architecture:**
- One PostHog cloud project (free tier: 1M events/mo, unlimited flags).
- Backend wrapper `config/feature_flags.py` exposes `is_enabled(flag, user_id, default=False)` with local evaluation (no network call per request — PostHog SDK polls every 30s).
- Frontend wrapper `web/src/lib/featureFlags.ts` exposes `useFeatureFlag(flag)` hook backed by `posthog-js`.
- Flag taxonomy: `auto_apply`, `council_scoring`, `tailor_full_rewrite`, `scraper_glassdoor`, `scraper_gradireland`. Boolean only for v1; multivariate later.
- Identification: backend uses `user_id` from Supabase JWT; frontend uses `posthog.identify(user.id)` after Supabase auth.
- Test strategy: dependency-inject the PostHog client so unit tests stub `is_enabled` returning whatever the test wants.

**Files:**
- `config/feature_flags.py` (CREATE) — `~80 LOC` wrapper, local eval, env-aware (no-op in test env)
- `requirements.txt` (MODIFY) — `posthog>=4.0.0`
- `app.py` (MODIFY @ ~lines 2418, 2472) — flag-gate the two `apply_*` endpoints
- `lambdas/browser/ws_route.py` (MODIFY) — flag-gate the apply-execution path
- `lambdas/pipeline/score_batch.py` (MODIFY) — flag-gate council vs fast-path
- `lambdas/pipeline/tailor_resume.py` (MODIFY) — flag-gate full-rewrite vs light-touch
- `web/package.json` (MODIFY) — `posthog-js@^1.180.0`
- `web/src/lib/featureFlags.ts` (CREATE) — provider + hook
- `web/src/main.jsx` (MODIFY) — wrap `<App />` in `<PostHogProvider>`
- `web/src/components/AutoApplyButton.jsx` (MODIFY) — render disabled-with-tooltip when flag off
- `tests/unit/test_feature_flags.py` (CREATE) — 6 cases: enabled/disabled, default fallback, network error → default, user_id propagation, kill-switch, env override

**Tasks:**
1. **Create the PostHog project + capture API keys** (manual, ~10 min)
2. **Backend wrapper + tests** — `config/feature_flags.py` with `is_enabled()`, decorator `@flag_gated('auto_apply')`, unit tests with stubbed client. TDD: write 6 failing tests, then implement.
3. **Wire flag-gates in 4 lambdas + `app.py`** — replace direct calls with `if is_enabled('auto_apply', user_id):` branches; default off.
4. **Frontend provider + hook + tests** — `featureFlags.ts`, mount provider in `main.jsx`, gate `AutoApplyButton`. Vitest unit test for the hook.
5. **CI env vars** — add `POSTHOG_API_KEY` to GitHub secrets, pass through `deploy.yml` as Lambda env var, expose `VITE_POSTHOG_KEY` to Netlify build.
6. **Documentation** — append a "Feature Flags" section to CLAUDE.md and write ADR `docs/superpowers/specs/2026-04-27-feature-flags-decision.md` (PostHog vs LaunchDarkly vs Unleash).

**Success criteria:**
- All 5 flags exist in PostHog UI, all default-off.
- `pytest tests/unit/test_feature_flags.py` passes (6/6).
- Toggling `auto_apply` flag in PostHog UI for `254utkarsh@gmail.com` enables the button on prod within 30s — no redeploy.
- Toggling it off disables auto-apply within 30s.
- `git grep "is_enabled\|useFeatureFlag"` returns at least one hit per gated module.

**Sub-plan to write:** `2026-04-27-deployment-safety-phase1-flags.md` (~15 TDD-granular tasks)

---

## Phase 2 — Lambda Canary via SAM `DeploymentPreference`

**Goal:** When a new Lambda version is deployed, AWS CodeDeploy shifts traffic incrementally (10% → 100% over 5 min) and auto-rolls back if a CloudWatch alarm trips during the window. Net effect: a regression that 5xx's is caught while only 10% of traffic is exposed to it.

**Why now:** Six lines of YAML per Lambda. Pure infra change, no application code. Uses Lambda's *default* `Errors` metric, so it can ship before observability (P4) lands.

**Architecture:**
- Add `AutoPublishAlias: live` to every `AWS::Serverless::Function` — this creates an alias `live` that always points at the deployed version, and SAM publishes a new version on every deploy.
- Add `DeploymentPreference` per function. Risk-tier the strategies:
  - **Critical write paths** (`naukribaba-ws-route`, `naukribaba-tailor-resume`, `naukribaba-compile-latex`, `naukribaba-save-job`): `Canary10Percent5Minutes` (10% for 5 min, then 100%)
  - **Pipeline batch jobs** (`naukribaba-score-batch`, `naukribaba-merge-dedup`, etc.): `Linear10PercentEvery1Minute` (linear 10-step over 10 min — gentler for long-running async)
  - **Read-only / idempotent** (`naukribaba-load-config`, `naukribaba-aggregate-scores`): `AllAtOnce` (no canary value, just complexity)
- API Gateway integrations point at `Function:live` instead of `Function`.
- Alarms in `monitoring/alarms.yaml`:
  - `${FunctionName}-Errors`: `>0` errors in 1-min window (canary period)
  - `${FunctionName}-Throttles`: `>0` throttles in 1-min window
  - `${FunctionName}-DurationP99`: `>30s` p99 over 5 min (catches death-spiral)
- These three alarms attach to each function's `DeploymentPreference.Alarms` list.
- Roll-forward: if all alarms green during shift, alias atomically swings to new version. If any alarm red, CodeDeploy auto-reverts alias to prior version within seconds.

**Files:**
- `template.yaml` (MODIFY) — `AutoPublishAlias: live` + `DeploymentPreference` block on each `AWS::Serverless::Function`. ~28 functions × ~6 lines = ~170 line additions. Use a CFN macro or copy-paste; macro is cleaner but adds deploy-time dependency.
- `monitoring/alarms.yaml` (CREATE) — CFN snippet defining alarm template, `Fn::Transform: AWS::Include` from template.yaml. Or inline if macro feels heavy.
- `.github/workflows/deploy.yml` (MODIFY) — add `--no-disable-rollback` to `sam deploy` and increase timeout from 30 → 45 min to accommodate canary windows.
- `docs/superpowers/specs/2026-04-27-canary-strategy-decision.md` (CREATE) — ADR explaining tier choices and why `AllAtOnce` is OK for read-only.

**Tasks:**
1. **Define alarm template** — write `monitoring/alarms.yaml` with three alarm types, parameterized on function name. Validate with `cfn-lint`.
2. **Add `AutoPublishAlias: live` to one function first** (`naukribaba-ws-route`) and deploy. Confirm CodeDeploy console shows the deployment as a CodeDeploy deployment, not a CloudFormation update.
3. **Add `DeploymentPreference: Canary10Percent5Minutes` + alarms to that one function**, deploy, watch the canary in CodeDeploy console, manually trigger a 5xx (e.g. via env var injection) and confirm rollback fires.
4. **Roll out to all critical-tier functions** (5 functions): ws-route, tailor-resume, compile-latex, save-job, generate-cover-letter.
5. **Roll out Linear-tier to pipeline functions** (~12 functions).
6. **Set AllAtOnce on read-only functions** explicitly (so the choice is documented, not implicit).
7. **Update `deploy.yml`** with extended timeout + ADR commit.

**Success criteria:**
- `aws lambda get-alias --function-name naukribaba-ws-route --name live` returns the alias for all 5 critical functions.
- A deliberate bad deploy (e.g., function that always raises) is auto-rolled-back by CodeDeploy within 6 minutes.
- API Gateway never serves the bad version to >10% of traffic.

**Sub-plan to write:** `2026-04-27-deployment-safety-phase2-canary.md` (~12 tasks; rollout-by-function is the bulk)

---

## Phase 3 — Staging Environment (Supabase + SAM + Netlify)

**Goal:** A separate, full-stack pre-prod environment where migrations run first, smoke tests fire, and visual QA happens — before a single byte hits prod.

**Why now:** Without staging, P6 smoke tests have nowhere safe to run, and Supabase migrations still go straight to prod. P3 enables P6.

**Architecture:**
- **Supabase**: create a second project `naukribaba-staging`. Same migrations, separate data. Seed via `web/supabase/seed.sql` (CREATE) with anonymized fixture data — 10 fake users, 50 jobs, 5 resumes.
- **SAM**: parameterize stack with `Stage` parameter (`staging` | `prod`). Stack name becomes `naukribaba-${Stage}`. All function names, S3 buckets, CloudFront distributions get `${Stage}` suffix to prevent collisions.
- **Netlify**: enable Branch Deploys for `staging` branch → `staging.naukribaba.com` (or `staging--naukribaba.netlify.app` if no custom domain yet). Set `VITE_API_URL` per branch in Netlify UI.
- **CI flow**:
  - PR open → `deploy.yml` deploys to staging stack + posts staging URL in PR comment
  - Merge to main → `deploy.yml` deploys to prod stack
  - Both runs include unit + contract tests as gate; smoke tests added in P6
- Stack outputs (API Gateway URL, WS URL, etc.) published to GitHub env so frontend builds pick correct backend.
- Supabase keys per env stored in separate GitHub secrets: `SUPABASE_URL_STAGING` / `SUPABASE_URL_PROD`.

**Files:**
- `template.yaml` (MODIFY) — add `Parameters.Stage`, suffix all `FunctionName`, `BucketName`, `TableName`-style resources with `${Stage}`. Likely ~50 surgical edits.
- `samconfig.toml` (CREATE) — base SAM config common to both envs
- `samconfig.staging.toml` (CREATE) — staging overrides
- `samconfig.prod.toml` (CREATE) — prod overrides
- `.github/workflows/deploy.yml` (MODIFY) — convert from `workflow_dispatch` to `on: pull_request` (staging) + `on: push: branches: [main]` (prod). Job matrix or two jobs.
- `netlify.toml` (MODIFY) — add `[context.staging]` block setting `VITE_API_URL` to staging API Gateway URL
- `web/supabase/seed.sql` (CREATE) — fixture seed for staging DB
- `web/supabase/migrations/` (no changes structurally; just runs against both envs now)
- `scripts/promote_to_prod.sh` (CREATE) — guarded script: only runnable from main, requires staging smoke pass
- `docs/superpowers/specs/2026-04-27-staging-env-decision.md` (CREATE) — ADR on Supabase project-per-env vs schema-per-env, why we chose the former

**Tasks:**
1. **Create staging Supabase project** (manual UI, ~5 min). Capture URL + anon key + service key into 1Password.
2. **Apply existing migrations to staging** via `supabase db push --project-ref ${STAGING_REF}`. Verify schema matches prod with `supabase db diff`.
3. **Write seed.sql** with 10 users, 50 jobs, 5 resumes. Run against staging.
4. **Parameterize template.yaml** — add `Stage` parameter, suffix resources. Validate with `sam validate`.
5. **Create samconfig.{base,staging,prod}.toml** — separate parameter overrides + tags.
6. **Rewrite deploy.yml** — two jobs, `deploy-staging` triggered on PR, `deploy-prod` on main. Smoke gate added in P6.
7. **Configure Netlify branch deploy** (manual UI). Set `VITE_API_URL` per context.
8. **End-to-end test**: open a throwaway PR, watch staging deploy, hit staging API, confirm staging Netlify URL shows expected backend.
9. **Document the workflow** in CLAUDE.md.

**Success criteria:**
- A PR opened against main automatically deploys to staging within 15 min.
- Staging URL serves a working app backed by staging Supabase.
- Merging to main triggers prod deploy.
- Manually-triggered prod deploys still work via `workflow_dispatch`.
- A migration run against staging *cannot* affect prod (verify by writing/deleting a row in staging).

**Sub-plan to write:** `2026-04-27-deployment-safety-phase3-staging.md` (~14 tasks)

---

## Phase 4 — Observability (structlog + X-Ray + EMF + Dashboards)

**Goal:** Every request has a trace ID, every log line is structured JSON queryable in Logs Insights, every business metric is a custom CloudWatch metric, and one dashboard answers "is the system healthy right now?".

**Why now:** Canary (P2) ships with stock alarms; P4 upgrades them to real signals. Smoke tests (P6) emit metrics that close the loop. P4 also pays back instantly when debugging — your Apr 8 root-cause hunt would have been an hour, not a day, with structured logs.

**Architecture:**
- **Logging**: `structlog` configured in `lambdas/pipeline/utils/logging.py` (shared) + `config/observability.py` (FastAPI). Output is JSON to stdout; CloudWatch ingests automatically. Standard fields: `request_id`, `user_id`, `function_name`, `cold_start`, `duration_ms`, `level`, `event`. Bind `request_id` from API Gateway request context per request.
- **Tracing**: `Tracing: Active` on every `AWS::Serverless::Function` enables X-Ray. Add `aws-xray-sdk` to requirements; auto-instrument boto3, requests, supabase client. End-to-end traces span: API Gateway → Lambda → Supabase → AI provider → response.
- **Metrics (EMF)**: emit Embedded Metric Format JSON from Lambda logs. Namespace `Naukribaba/${Stage}`. Metrics: `job_scored`, `apply_attempted`, `apply_succeeded`, `apply_failed`, `resume_tailored`, `pipeline_run_completed`, `ai_provider_failed{provider}`, `scraper_jobs_returned{source}`. Free (cheaper than direct PutMetricData), no extra SDK call latency.
- **Dashboards**: one CloudWatch dashboard per stage. Widgets: invocations, errors, p95 duration, throttles, business metrics (jobs/day, applies/day), alarm states. Defined as CFN resource in `template.yaml`.
- **Alarms (upgraded)**: replace generic `Errors > 0` from P2 with composite alarms — e.g., `apply_failed_rate > 20% over 5 min` is more actionable than raw error count for the WS Lambdas.

**Files:**
- `requirements.txt` (MODIFY) — `structlog>=24.0.0`, `aws-xray-sdk>=2.14.0`, `aws-lambda-powertools[tracer,metrics]>=2.40.0` (powertools wraps EMF + X-Ray ergonomically)
- `config/observability.py` (CREATE) — structlog processors + powertools setup for FastAPI
- `lambdas/pipeline/utils/logging.py` (CREATE) — shared lambda logger, mirrors above for non-FastAPI lambdas
- `app.py` (MODIFY) — `import structlog`; wrap each request with `request_id` binding; replace 30+ `logger.info(...)` calls with structured equivalents
- All `lambdas/**/*.py` handlers (MODIFY, ~25 files) — same; `from utils.logging import logger`; emit `metrics.add_metric(name='apply_attempted', unit='Count', value=1)` at decision points
- `template.yaml` (MODIFY) — add `Tracing: Active` to `Globals.Function`; add `AWS::CloudWatch::Dashboard` resource referencing `monitoring/dashboard.json`
- `monitoring/dashboard.json` (CREATE) — dashboard widget JSON
- `monitoring/alarms.yaml` (MODIFY) — add composite alarms on EMF metrics
- `docs/superpowers/specs/2026-04-27-observability-decision.md` (CREATE) — ADR on structlog vs loguru, EMF vs PutMetricData, X-Ray vs OpenTelemetry

**Tasks:**
1. **Bootstrap structlog in app.py + one Lambda** — write the config, replace 5–10 log calls, deploy, verify JSON in CloudWatch.
2. **Roll structlog across remaining lambdas** — mostly mechanical edits; can do as a script.
3. **Enable X-Ray globally** — `Tracing: Active` in `Globals`. Open one trace in console, confirm spans visible end-to-end.
4. **Define metrics taxonomy + emit from key code paths** — `apply_*`, `pipeline_*`, `ai_provider_failed`. Validate with Logs Insights filter for EMF.
5. **Build the dashboard JSON** — 8–12 widgets covering invocations, errors, p95 latency, throttles, business KPIs, alarm states.
6. **Replace P2 stock alarms with composite EMF-backed alarms** for the highest-traffic functions.
7. **Write Logs Insights query library** in `monitoring/queries.md` — common queries: "errors in last hour by user_id", "apply attempts by ATS", "AI provider failover events".

**Success criteria:**
- A CloudWatch Logs Insights query `fields @timestamp, request_id, user_id, event | filter event = "apply_attempted"` returns rows with all four fields populated.
- A trace in X-Ray console shows: API Gateway → Lambda (cold start visible) → Supabase RPC → response.
- The dashboard `https://eu-west-1.console.aws.amazon.com/cloudwatch/home#dashboards:name=naukribaba-prod` shows non-zero data on all widgets within 24h.
- An alarm trip on `apply_failed_rate` triggers SNS → email within 5 min.

**Sub-plan to write:** `2026-04-27-deployment-safety-phase4-observability.md` (~18 tasks)

---

## Phase 5 — Error Tracking via Sentry

**Goal:** Every uncaught exception in backend Lambdas, FastAPI, and the React frontend is captured to Sentry with stack trace, breadcrumbs, user context, release tag, and source maps. Alerts go to email + Slack on first occurrence and on regression after release.

**Why now:** Independent of P1–P4 — can run in parallel. Sentry catches the exceptions that *don't* trigger P4's metric alarms (the ones that 500 once for one user and disappear into logs).

**Architecture:**
- **Backend**: `sentry-sdk[fastapi]` in FastAPI, `sentry-sdk[aws_lambda]` in Lambda handlers. Both use the same DSN, separate `environment` tags (`staging` / `prod`).
- **Frontend**: `@sentry/react` + `@sentry/vite-plugin` for source map upload. Browser-side errors + React error boundaries + unhandled promise rejections.
- **Releases**: every deploy tags Sentry with the git SHA. Lets Sentry mark errors as "regression" if they reappear after being marked resolved on a prior release.
- **User context**: `Sentry.setUser({ id: user.id, email: user.email })` after Supabase auth. Errors are attributable to a real user for support.
- **PII discipline**: scrub email/phone from error messages before send; allowlist allowed fields. Important for Irish/EU GDPR.
- **Sample rates**: `traces_sample_rate=0.1` (10% of transactions get traced; full tracing is expensive). `replays_session_sample_rate=0.0` initially (no session replay until needed).
- **Alerts**: route to email for now; Slack later. One rule: "issue affects >10 users OR is a regression OR breaks auth/apply paths" → email immediately.

**Files:**
- `requirements.txt` (MODIFY) — `sentry-sdk[fastapi]>=2.20.0`
- `config/sentry_config.py` (CREATE) — init helpers for FastAPI + Lambda, with PII scrubber
- `app.py` (MODIFY) — `init_sentry_fastapi()` early in module load; user identification middleware
- `lambdas/browser/ws_*.py` (MODIFY, 3 files) — `init_sentry_lambda()` at module top
- `lambdas/pipeline/*.py` (MODIFY, ~22 files) — same
- `web/package.json` (MODIFY) — `@sentry/react`, `@sentry/vite-plugin`
- `web/src/lib/sentry.ts` (CREATE) — `initSentry()` + `<SentryErrorBoundary>` component
- `web/src/main.jsx` (MODIFY) — `initSentry()` before `<React.StrictMode>`
- `web/vite.config.js` (MODIFY) — Sentry vite plugin for source map upload during build
- `.github/workflows/deploy.yml` (MODIFY) — pass `SENTRY_RELEASE=$GITHUB_SHA` to Lambda env + Vite build
- `docs/superpowers/specs/2026-04-27-error-tracking-decision.md` (CREATE) — ADR Sentry vs Rollbar vs Honeycomb

**Tasks:**
1. **Create Sentry org + two projects** (backend, frontend). Capture DSNs.
2. **Wire backend Sentry in `app.py`** — init + middleware. Trigger a test 500 via `/sentry-debug`. Confirm event in Sentry UI.
3. **Wire Lambda Sentry** — init in shared module imported by every handler. Test via deliberate exception in one handler.
4. **Wire frontend Sentry** — `<SentryErrorBoundary>` wrapping `<App />`. Trigger test error via dev button. Confirm event with source maps.
5. **Source map upload in CI** — Vite plugin auth token. Confirm a frontend error in prod shows real source line, not minified.
6. **Release tagging** — pass `SENTRY_RELEASE` from CI. Verify error events are tagged with the deploy SHA.
7. **PII scrubber** — write + test the scrubber to drop emails/phone from error messages.
8. **Set up alert rules** — email + (later) Slack, per the policy above.

**Success criteria:**
- A deliberate 500 in `app.py` produces a Sentry issue tagged `environment=prod`, `release=<sha>`, with `user_id` populated.
- A deliberate frontend error shows the real source file + line (not minified).
- An issue marked resolved + reproduced after next deploy shows as "Regression" in Sentry UI.
- No email/phone strings appear in any captured event payload.

**Sub-plan to write:** `2026-04-27-deployment-safety-phase5-sentry.md` (~10 tasks)

---

## Phase 6 — Smoke Tests + Rollback Wiring

**Goal:** After each deploy (staging or prod), automatic synthetic tests hit the live system. Failures fail the canary's PreTraffic hook → CodeDeploy auto-rolls back the alias. End-to-end: a bad merge to main is reverted in <10 min with no human in the loop.

**Why last:** Needs canary (P2) to wire into, staging (P3) as a target, observability (P4) so failures are debuggable. Tests can be authored earlier but aren't useful until P2/P3 land.

**Architecture:**
- **Backend smoke (`tests/smoke/`)**: pytest + httpx. Hits real staging URL. Tests:
  - `test_health.py` — `GET /healthz` returns 200, `GET /api/auth/health` returns 200
  - `test_apply_synthetic.py` — POST to `/api/apply/start` against a known-good fixture job (seeded in staging Supabase). Polls WS for completion. Asserts apply_attempts row inserted.
  - `test_pipeline_smoke.py` — invoke `naukribaba-score-batch` directly via Lambda invoke API with a 1-job fixture; assert score lands in DB.
- **Frontend smoke (`web/tests/smoke/`)**: Playwright. Tests:
  - `critical-paths.spec.ts` — login → dashboard renders → click first job → preview renders → apply button visible (don't click, just visible)
- **Wiring into CodeDeploy**: the `DeploymentPreference.Hooks.PreTraffic` field accepts a Lambda function ARN. Create `naukribaba-canary-prehook` Lambda that invokes the smoke pytest in-cluster (or via SSM RunCommand). On non-zero exit, return failure → CodeDeploy aborts the deployment + reverts.
- **CI flow upgrade**:
  - PR → deploy staging → run smoke against staging → comment results on PR
  - main merge → deploy prod canary → PreTraffic hook runs smoke → if pass, traffic shifts; if fail, rollback
- **Failure budget**: smoke tests are flaky-allergic. If a smoke test goes red without a real prod issue 3x in a quarter, retire it. Don't tolerate "flaky" smoke — it normalizes ignored alerts.

**Files:**
- `tests/smoke/__init__.py` (CREATE)
- `tests/smoke/conftest.py` (CREATE) — base URL fixture from `SMOKE_TARGET=staging|prod`, httpx client with auth
- `tests/smoke/test_health.py` (CREATE)
- `tests/smoke/test_apply_synthetic.py` (CREATE) — uses fixture job from staging seed
- `tests/smoke/test_pipeline_smoke.py` (CREATE)
- `tests/smoke/requirements.txt` (CREATE) — pytest, httpx, websockets
- `web/tests/smoke/critical-paths.spec.ts` (CREATE) — Playwright suite
- `web/tests/smoke/playwright.config.ts` (CREATE)
- `web/package.json` (MODIFY) — devDeps: `@playwright/test`
- `lambdas/pipeline/canary_prehook.py` (CREATE) — orchestrates smoke run; returns success/failure to CodeDeploy
- `template.yaml` (MODIFY) — register the prehook Lambda; reference it in `DeploymentPreference.Hooks.PreTraffic` for critical functions
- `.github/workflows/smoke.yml` (CREATE) — `workflow_call` reusable workflow
- `.github/workflows/deploy.yml` (MODIFY) — call `smoke.yml` after staging deploy and after prod canary
- `web/supabase/seed.sql` (MODIFY) — ensure fixture job + fixture user exist for smoke tests

**Tasks:**
1. **Backend smoke fixtures** — define the canonical fixture job + user in `seed.sql`. Apply to staging.
2. **Write `test_health.py` and validate against staging** — should be green from day one.
3. **Write `test_apply_synthetic.py`** — including WS polling for completion. This is the hardest test; budget time for it.
4. **Write `test_pipeline_smoke.py`** — invoke score_batch via boto3 with 1-job event.
5. **Frontend Playwright smoke** — login + critical paths. Use a dedicated test user.
6. **Reusable `smoke.yml` workflow** — env-aware target, clean exit codes, upload artifacts on failure.
7. **Wire smoke into PR flow** — block merge if staging smoke fails.
8. **Build the `canary_prehook` Lambda** — invokes the smoke suite via boto3 + GitHub Actions API (or runs the tests inline if package is small enough). Returns `Succeeded`/`Failed` to CodeDeploy.
9. **Wire `PreTraffic` hook in `template.yaml`** for the 5 critical-tier functions.
10. **End-to-end test**: deliberately break `app.py`'s `/healthz`, deploy, watch CodeDeploy abort + roll back. Restore.

**Success criteria:**
- A PR with a deliberate health-check break shows red staging smoke in the PR check tab.
- A merge to main with a deliberate prod 500 in a critical Lambda is auto-rolled-back within 10 min, alias remains on prior version.
- Mean smoke run duration < 3 min.
- Zero false-positive smoke failures over the first month.

**Sub-plan to write:** `2026-04-27-deployment-safety-phase6-smoke.md` (~16 tasks)

---

## Cross-Phase: Rollback Strategy

After all 6 phases land, the rollback ladder is:

1. **Auto (CodeDeploy)**: Canary alarm trips → alias swings to prior Lambda version. Time to rollback: <2 min. No human action.
2. **Auto (PreTraffic smoke)**: Smoke fails → CodeDeploy aborts before traffic shift. Time to rollback: 0 min (never shifted). No human action.
3. **Flag kill-switch**: Bug missed by automated rollback (e.g., logical bug not breaking smoke) → flip PostHog flag off. Time to rollback: 30s. One human click.
4. **Frontend revert (Netlify)**: Frontend regression → Netlify "Revert" button on the prior deploy. Time to rollback: 30s.
5. **Manual full revert**: Edge case where 1–4 don't help (e.g., bad migration that broke schema invariants) → `git revert <sha> && git push origin main`. Triggers prod deploy with reverted code; migration rollback may need separate `supabase migration revert`. Time to rollback: 15 min.

Document this ladder in `docs/runbooks/rollback.md` (CREATE during P6).

---

## Backlog (Deferred — Not in This Roadmap)

- **Multi-region failover** — eu-west-1 → eu-west-2 active-passive. Worth doing once revenue justifies it; today it's overkill.
- **Database read replicas** — staging Supabase already gives us read isolation for QA; full replica is overkill for current load.
- **Chaos engineering** — periodic deliberate Lambda kills via FIS. Useful at scale; premature at a single-user-MVP stage.
- **A/B testing infra** — PostHog supports experiments natively but we don't have enough traffic for stat-sig results. Revisit after launch.
- **PagerDuty integration** — overkill while solo. Sentry email + Slack covers it.
- **Long-term log retention** — CloudWatch defaults to never-expire (cost trap). Add 30-day retention policy as a cleanup task.
- **Cost dashboards** — track Lambda + Supabase + AI provider spend per env. Useful once spend > $50/mo.

---

## Self-Review

**Spec coverage check:** The user asked for blue/green/rolling deployments + observability + error reporting. Mapped:
- Blue/green/rolling → P2 (canary via CodeDeploy)
- Healthy main + prod → P1 (flags decouple deploy from release) + P3 (staging gate) + P6 (smoke gate)
- Observability → P4 (logs, traces, metrics, dashboards)
- Error reporting → P5 (Sentry) + P4 (alarms)
- All requested concerns covered. ✓

**Placeholder scan:** Tasks within phases are coarse ("write the wrapper", "wire into CI") rather than TDD-granular by design — this is a roadmap, with a stated promise to write detailed sub-plans per phase before execution. No "TBD" / "fill in" / "implement later" text. ✓

**Type/name consistency:** Flag names (`auto_apply`, `council_scoring`, etc.) used consistently across P1, P5, and dashboards in P4. Function names match `template.yaml` actuals (`naukribaba-ws-route` etc., verified by grep). ✓

**Dependency ordering:** P2 launches with default Lambda metrics (no P4 dependency). P3 needs P2's canary baked in so the canary config carries to staging. P6 needs P2+P3+P4. ADR references all use today's date (2026-04-27). ✓

**Realism:** ~5 days of focused work over 1–2 calendar weeks alongside the user's existing PR cadence. Phases 1, 2, 5 are each <1 day and provide instant safety win — those should land this week if possible.

---

## Execution Handoff

**Roadmap saved.** Next step is one of:

1. **Detail Phase 1 (Feature Flags)** as an executable TDD plan — biggest immediate ROI, ~1 day to ship, makes auto-apply safe to iterate on. Recommended.
2. **Detail Phase 2 (Lambda Canary)** — half-day infra change, no app code, instant deploy safety.
3. **Detail Phase 5 (Sentry)** — half-day, parallel to feature work, captures whatever's broken in prod *today*.
4. **Detail all six in one batch** — ~3–4 hours of writing, then sequential execution. Heavyweight but unblocks parallel work.

Pick which phase to expand first.
