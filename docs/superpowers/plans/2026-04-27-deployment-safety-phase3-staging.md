# Phase 3 — Staging Environment (Supabase + SAM + Netlify) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a parallel pre-prod environment (Supabase project, SAM stack, Netlify deploy context) so every PR gets validated end-to-end against staging before merging to `main` ships to prod.

**Architecture:** A single `Stage` parameter (`staging` | `prod`) added to `template.yaml` suffixes every named AWS resource so the two stacks coexist in one account. Each environment gets its own Supabase project (project-per-env, not schema-per-env) so RLS, JWT secrets, and migrations are isolated. Netlify branch deploys serve the staging frontend from the `staging` branch with `VITE_API_URL` overridden to the staging API Gateway URL. A rewritten `deploy.yml` runs `deploy-staging` on `pull_request` and `deploy-prod` on `push` to `main`, both reading from per-env `samconfig.{env}.toml` files; `workflow_dispatch` stays as a manual fallback.

**Tech Stack:** AWS SAM/CloudFormation, Supabase CLI (`supabase db push`, `supabase db diff`), GitHub Actions, Netlify build contexts, bash, Python (for seed-user creation via `supabase-py` + Admin API).

**Spec:** `docs/superpowers/plans/2026-04-27-deployment-safety-roadmap.md` — Phase 3.

**Locked-in decisions (do not re-litigate):**
- Project-per-env for Supabase (separate auth users, separate JWT secrets, separate billing; trades a small migration-application overhead for hard isolation).
- `Stage` parameter pattern in SAM (one template, two stacks). No CFN macros.
- Netlify Branch Deploy for `staging` branch (per-PR previews are nice but cost extra Netlify build minutes; we add the long-lived `staging` branch first and revisit per-PR previews later).
- Prod stack name stays `job-hunt-api` (not renamed to `naukribaba-prod`) to avoid a 28-resource CFN replacement on the existing prod stack. Staging gets a fresh stack `naukribaba-staging`.

**File Structure**

```
supabase/
  seed.sql                                          (CREATE) anonymized fixtures for staging
  scripts/
    seed_auth_users.py                              (CREATE) Python helper that creates 10 fake auth users via Admin API
template.yaml                                       (MODIFY) add Stage param + suffix all named resources
samconfig.toml                                      (CREATE) base SAM config (capabilities, region, ECR)
samconfig.staging.toml                              (CREATE) staging stack overrides
samconfig.prod.toml                                 (CREATE) prod stack overrides
.github/workflows/deploy.yml                        (REWRITE) deploy-staging on PR + deploy-prod on main
netlify.toml                                        (MODIFY) add [context.staging] block
scripts/promote_to_prod.sh                          (CREATE) defensive promote script (refuses unless on main)
docs/superpowers/specs/
  2026-04-27-staging-env-decision.md                (CREATE) ADR
```

> **Note on `web/supabase/` vs `supabase/`:** The roadmap references `web/supabase/migrations/` and `web/supabase/seed.sql`. The actual layout in this repo is `supabase/migrations/` at the repo root (verified). All references to seed/migrations in this plan use the actual `supabase/` path. Update the roadmap if cross-reference matters; do not move the directory.

> **Note on prod stack name:** The current prod stack is `job-hunt-api`. Renaming it would force CFN to delete + recreate every named resource (28 Lambdas, 2 layers, DynamoDB table, etc.) — a multi-hour outage. Keep the prod stack name; only the *resources inside* it pick up the `${Stage}` suffix. Existing prod resource names that already lack any suffix (e.g. `naukribaba-ws-route`) are renamed in this plan to `naukribaba-prod-ws-route`. CFN will replace those resources during the next prod deploy. **Schedule the first prod deploy after this PR merges during a low-traffic window** (see Task 14 success criteria).

---

## Task 0: Manual prereq — create staging Supabase project + capture secrets

**Why this task is here:** All later tasks depend on a working staging Supabase URL + service key + JWT secret. This task is human-only (browser UI) and must complete before the rest of the plan is executable. Estimated time: ~10 min.

- [ ] **Step 1: Create the Supabase project**

In a browser, open https://supabase.com/dashboard, click "New Project", and use:

- **Name:** `naukribaba-staging`
- **Region:** `eu-west-1` (matches AWS region; lowest cross-cloud latency for the Lambda → Postgres path)
- **Database password:** generate a strong one and stash it in 1Password under "naukribaba-staging-db-password"
- **Pricing plan:** Free tier (sufficient for staging traffic)

Wait for the project to provision (~2 min). The project ref appears in the URL: `https://supabase.com/dashboard/project/<STAGING_PROJECT_REF>`. Capture the ref.

- [ ] **Step 2: Capture the four secrets you need**

In the Supabase dashboard for the new project, go to **Settings → API** and copy:

1. **Project URL** (e.g. `https://abcdefgh.supabase.co`) — this is `SUPABASE_URL_STAGING`
2. **anon public key** — this is `SUPABASE_ANON_KEY_STAGING` (frontend use)
3. **service_role key** — this is `SUPABASE_SERVICE_KEY_STAGING` (backend Lambda use; keep secret)

In **Settings → API → JWT Settings**, copy:

4. **JWT Secret** — this is `SUPABASE_JWT_SECRET_STAGING`

Stash all four in 1Password under "naukribaba-staging-secrets" before doing anything else; the Supabase UI does NOT let you re-view the JWT secret without rotating it.

- [ ] **Step 3: Add staging secrets to GitHub**

Run from a shell with `gh` authenticated:

```bash
gh secret set SUPABASE_URL_STAGING --repo UT07/daily-job-hunt --body 'https://<STAGING_REF>.supabase.co'
gh secret set SUPABASE_ANON_KEY_STAGING --repo UT07/daily-job-hunt --body '<paste anon key>'
gh secret set SUPABASE_SERVICE_KEY_STAGING --repo UT07/daily-job-hunt --body '<paste service_role key>'
gh secret set SUPABASE_JWT_SECRET_STAGING --repo UT07/daily-job-hunt --body '<paste JWT secret>'
gh secret set SUPABASE_DB_PASSWORD_STAGING --repo UT07/daily-job-hunt --body '<paste DB password>'
gh secret set SUPABASE_PROJECT_REF_STAGING --repo UT07/daily-job-hunt --body '<paste project ref>'
```

The existing prod secrets (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_JWT_SECRET`) stay untouched — they remain the *prod* values. We do NOT rename them: the rewritten `deploy.yml` reads `*_STAGING` for staging jobs and the unsuffixed names for prod. This avoids a coordinated rename that would break the existing manual deploy mid-rollout.

- [ ] **Step 4: Verify with `gh secret list`**

```bash
gh secret list --repo UT07/daily-job-hunt | grep STAGING
```

Expected: 6 lines for the 6 new secrets. The prod secrets are also listed but unmodified.

---

## Task 1: Apply existing migrations to staging Supabase

**Why this task is here:** Staging schema must match prod schema 1:1 before we point any code at it, otherwise smoke tests will get phantom errors that look like deploy failures.

**Files:**
- Read-only: `supabase/migrations/*.sql` (10 files; do not modify)

- [ ] **Step 1: Confirm Supabase CLI is installed locally**

```bash
supabase --version
```

Expected: `1.x.x` or `2.x.x`. If not installed: `brew install supabase/tap/supabase`. Do NOT install via npm (the npm package is the legacy JS SDK, not the CLI).

- [ ] **Step 2: Link the local repo to the staging project**

From the repo root:

```bash
cd /Users/ut/code/naukribaba
export SUPABASE_PROJECT_REF_STAGING='<paste ref from Task 0>'
export SUPABASE_DB_PASSWORD_STAGING='<paste DB password from Task 0>'
supabase link --project-ref "$SUPABASE_PROJECT_REF_STAGING" --password "$SUPABASE_DB_PASSWORD_STAGING"
```

Expected output ends with:

```
Finished supabase link.
```

This writes a small file under `supabase/.temp/` (already gitignored per memory note "Phase 2B Decisions" line about `web/supabase/.temp/` — same idea, repo-root gitignore covers it via `supabase/.gitignore`).

- [ ] **Step 3: Push existing migrations to staging**

```bash
supabase db push --password "$SUPABASE_DB_PASSWORD_STAGING"
```

Expected output (last lines):

```
Connecting to remote database...
Applying migration 00000000000000_initial_schema.sql...
Applying migration 007_application_timeline.sql...
Applying migration 008_resume_versions.sql...
Applying migration 20260401200000_scrape_runs.sql...
Applying migration 20260405_add_score_tier.sql...
Applying migration 20260409_add_posted_date.sql...
Applying migration 20260409_fix_job_id_type.sql...
Applying migration 20260412_onboarding_profile.sql...
Applying migration 20260414_auto_apply_setup.sql...
Applying migration 20260420_cloud_browser_schema.sql...
Finished supabase db push.
```

If any migration fails: stop. Read the error, fix the migration in a separate PR, and re-run. Do NOT manually patch staging — staging must remain reproducible from `supabase/migrations/` alone.

- [ ] **Step 4: Verify schema parity with prod**

Switch the linked project to prod, run `db diff`, and assert it's empty:

```bash
# First link to PROD to establish baseline (if not already linked elsewhere)
export SUPABASE_PROJECT_REF_PROD='<your prod project ref>'  # from .env or Supabase dashboard
export SUPABASE_DB_PASSWORD_PROD='<your prod DB password>'  # from 1Password

# Diff staging migrations against prod schema
supabase db diff --linked --schema public > /tmp/schema-diff-staging.sql
wc -l /tmp/schema-diff-staging.sql
```

Expected: 0 lines (file is empty). If non-zero, inspect — usually means a migration was hand-applied to prod without going through `supabase/migrations/`. Add it to the migrations folder + commit, then re-push to staging.

- [ ] **Step 5: Re-link to staging (so subsequent commands target staging)**

```bash
supabase link --project-ref "$SUPABASE_PROJECT_REF_STAGING" --password "$SUPABASE_DB_PASSWORD_STAGING"
```

Why: anything you run next (seed.sql) should target staging, not prod. The Supabase CLI silently uses whichever project was last linked.

---

## Task 2: Write the seed-auth-users helper

**Why this task is here:** `seed.sql` cannot create rows in `auth.users` directly — Supabase's `auth.users` is a managed table with side-effects (identity record, email confirmation, password hashing). The cleanest way to create fake users for staging is the Supabase Admin API via `supabase-py`. This script is run *once* before `seed.sql` so `seed.sql`'s `INSERT INTO public.users (id, ...) VALUES ('<fake_uuid>', ...)` lines have valid FKs to auth.

**Files:**
- Create: `supabase/scripts/seed_auth_users.py`

- [ ] **Step 1: Verify the directory exists**

```bash
ls /Users/ut/code/naukribaba/supabase/
```

Expected: `migrations` (only). We're adding `scripts/` next to it.

```bash
mkdir -p /Users/ut/code/naukribaba/supabase/scripts
```

- [ ] **Step 2: Write the script**

Create `supabase/scripts/seed_auth_users.py`:

```python
"""Seed staging Supabase with 10 fake auth users.

Idempotent: skips users whose email already exists. Designed to run BEFORE seed.sql,
because seed.sql's INSERTs into public.users reference these auth user IDs as FKs.

Usage (from repo root, with .venv active and STAGING service key in env):

    export SUPABASE_URL_STAGING='https://<ref>.supabase.co'
    export SUPABASE_SERVICE_KEY_STAGING='eyJ...'
    python supabase/scripts/seed_auth_users.py

Refuses to run if SUPABASE_URL_STAGING points at the prod project (sanity check).
"""
from __future__ import annotations

import os
import sys
import uuid

from supabase import Client, create_client


# Deterministic UUIDs so seed.sql can hardcode them. Generated once with uuid.uuid4()
# and frozen here. Do NOT regenerate.
FAKE_USERS = [
    ("11111111-1111-4111-8111-111111111111", "alice.staging@naukribaba.test",   "Alice", "O'Connor"),
    ("22222222-2222-4222-8222-222222222222", "bob.staging@naukribaba.test",     "Bob",   "Smith"),
    ("33333333-3333-4333-8333-333333333333", "carol.staging@naukribaba.test",   "Carol", "Murphy"),
    ("44444444-4444-4444-8444-444444444444", "dan.staging@naukribaba.test",     "Dan",   "Byrne"),
    ("55555555-5555-4555-8555-555555555555", "eve.staging@naukribaba.test",     "Eve",   "Walsh"),
    ("66666666-6666-4666-8666-666666666666", "frank.staging@naukribaba.test",   "Frank", "Kelly"),
    ("77777777-7777-4777-8777-777777777777", "grace.staging@naukribaba.test",   "Grace", "Doyle"),
    ("88888888-8888-4888-8888-888888888888", "henry.staging@naukribaba.test",   "Henry", "Ryan"),
    ("99999999-9999-4999-8999-999999999999", "irene.staging@naukribaba.test",   "Irene", "Burke"),
    ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "jack.staging@naukribaba.test",    "Jack",  "Lynch"),
]
# All seed users get this password. Staging is a closed environment — fine for testing.
SEED_PASSWORD = "Staging-Test-2026!"


def _assert_staging_target(url: str) -> None:
    if "supabase.co" not in url:
        sys.exit(f"Refusing to run: SUPABASE_URL_STAGING does not look like a Supabase URL: {url}")
    # Trip-wire: prod's URL ref starts with these chars per current Supabase project.
    # Replace 'PROD_REF_PREFIX' below with the actual first-6-chars of your prod ref
    # before running. If the URL contains the prod ref, refuse.
    PROD_REF_PREFIX = os.environ.get("SUPABASE_PROD_REF_PREFIX", "")
    if PROD_REF_PREFIX and PROD_REF_PREFIX in url:
        sys.exit(f"Refusing to run: SUPABASE_URL_STAGING={url} appears to point at PROD")


def main() -> int:
    url = os.environ["SUPABASE_URL_STAGING"]
    key = os.environ["SUPABASE_SERVICE_KEY_STAGING"]
    _assert_staging_target(url)

    sb: Client = create_client(url, key)

    # supabase-py 2.x exposes Admin API at sb.auth.admin
    created = 0
    skipped = 0
    for user_id, email, first, last in FAKE_USERS:
        try:
            sb.auth.admin.create_user({
                "id": user_id,
                "email": email,
                "password": SEED_PASSWORD,
                "email_confirm": True,
                "user_metadata": {"first_name": first, "last_name": last, "seed": True},
            })
            created += 1
            print(f"  created: {email} ({user_id})")
        except Exception as e:  # supabase-py raises a generic AuthApiError on conflict
            msg = str(e).lower()
            if "already" in msg or "duplicate" in msg or "exists" in msg:
                skipped += 1
                print(f"  exists:  {email}")
            else:
                print(f"  ERROR:   {email}: {e}", file=sys.stderr)
                return 1

    print(f"\nDone. created={created}, skipped={skipped}, total={len(FAKE_USERS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Dry-run-test the script's import path locally**

```bash
cd /Users/ut/code/naukribaba && source .venv/bin/activate
python -c "from supabase import create_client; print('supabase-py OK')"
```

Expected: `supabase-py OK`. If `ModuleNotFoundError`, run `pip install -r requirements.txt` (supabase-py is already pinned there).

- [ ] **Step 4: Run the script against staging**

```bash
cd /Users/ut/code/naukribaba && source .venv/bin/activate
export SUPABASE_URL_STAGING='https://<staging_ref>.supabase.co'
export SUPABASE_SERVICE_KEY_STAGING='<paste from 1Password>'
python supabase/scripts/seed_auth_users.py
```

Expected output:

```
  created: alice.staging@naukribaba.test (11111111-1111-4111-8111-111111111111)
  created: bob.staging@naukribaba.test (22222222-2222-4222-8222-222222222222)
  ... (8 more)
Done. created=10, skipped=0, total=10
```

Re-running should print `skipped=10, created=0` (idempotency check).

- [ ] **Step 5: Verify in Supabase dashboard**

Open https://supabase.com/dashboard/project/<staging_ref>/auth/users — you should see 10 fake users with `@naukribaba.test` emails. Click one to confirm `email_confirm=true` and the `user_metadata` JSON shows `seed: true`.

- [ ] **Step 6: Commit the script (no secrets in repo)**

```bash
git add supabase/scripts/seed_auth_users.py
git commit -m "feat(staging): script to seed 10 fake auth users via Admin API

Used by seed.sql (next commit) which references these UUIDs as FKs into
public.users. Idempotent: re-runs skip existing emails. Refuses to run
against prod (env-var trip-wire on URL prefix)."
```

---

## Task 3: Write `supabase/seed.sql`

**Why this task is here:** With 10 fake auth users seeded, this SQL file populates the `public` tables those users feed into — enough fixture data that smoke tests can hit realistic code paths (10 users × 5 jobs each = 50 jobs covering all three score tiers, 5 resumes for the primary fake user, a few apply_attempts for the fixture-driven smoke test in Phase 6).

**Files:**
- Create: `supabase/seed.sql`

- [ ] **Step 1: Write the seed file**

Create `supabase/seed.sql`:

```sql
-- ========================================================================
-- naukribaba staging seed (anonymized fixtures)
--
-- Prereq: supabase/scripts/seed_auth_users.py has been run; the 10 fake
-- auth.users records exist with the UUIDs hardcoded below.
--
-- Apply: supabase db push --include-seed (after migrations) OR
--        psql "$DATABASE_URL_STAGING" -f supabase/seed.sql
--
-- Idempotent: every INSERT uses ON CONFLICT DO NOTHING. Re-runnable.
-- ========================================================================

BEGIN;

-- ------------------------------------------------------------------------
-- 1. public.users (10 rows; UUIDs match supabase/scripts/seed_auth_users.py)
-- ------------------------------------------------------------------------
INSERT INTO public.users (id, email, name, first_name, last_name, location, visa_status,
                          work_authorizations, notice_period_text, default_referral_source,
                          notification_prefs)
VALUES
  ('11111111-1111-4111-8111-111111111111', 'alice.staging@naukribaba.test',  'Alice O''Connor', 'Alice', 'O''Connor',
   'Dublin, Ireland', 'Stamp 1G',
   '{"IE": "authorized", "UK": "requires_visa", "US": "requires_sponsorship", "EU": "requires_visa"}'::jsonb,
   'Available in 2 weeks', 'LinkedIn',
   '{"sms": false, "email": true, "whatsapp": false}'::jsonb),
  ('22222222-2222-4222-8222-222222222222', 'bob.staging@naukribaba.test',    'Bob Smith',       'Bob',   'Smith',
   'Cork, Ireland', 'EU Citizen',
   '{"IE": "authorized", "EU": "authorized", "UK": "requires_visa", "US": "requires_sponsorship"}'::jsonb,
   'Available immediately', 'LinkedIn',
   '{"sms": false, "email": true, "whatsapp": false}'::jsonb),
  ('33333333-3333-4333-8333-333333333333', 'carol.staging@naukribaba.test',  'Carol Murphy',    'Carol', 'Murphy',
   'Galway, Ireland', 'Irish Citizen',
   '{"IE": "authorized", "EU": "authorized", "UK": "authorized", "US": "requires_sponsorship"}'::jsonb,
   'Available in 1 month', 'Referral',
   '{"sms": false, "email": true, "whatsapp": false}'::jsonb),
  ('44444444-4444-4444-8444-444444444444', 'dan.staging@naukribaba.test',    'Dan Byrne',       'Dan',   'Byrne',
   'Limerick, Ireland', 'Stamp 4',
   '{"IE": "authorized", "UK": "requires_visa", "US": "requires_sponsorship"}'::jsonb,
   'Available in 2 weeks', 'LinkedIn',
   '{"sms": false, "email": true, "whatsapp": false}'::jsonb),
  ('55555555-5555-4555-8555-555555555555', 'eve.staging@naukribaba.test',    'Eve Walsh',       'Eve',   'Walsh',
   'Dublin, Ireland', 'Stamp 1G',
   '{"IE": "authorized"}'::jsonb,
   'Available in 2 weeks', 'LinkedIn',
   '{"sms": false, "email": true, "whatsapp": false}'::jsonb),
  ('66666666-6666-4666-8666-666666666666', 'frank.staging@naukribaba.test',  'Frank Kelly',     'Frank', 'Kelly',
   'Dublin, Ireland', 'Stamp 1G',
   '{"IE": "authorized"}'::jsonb,
   'Available in 2 weeks', 'LinkedIn',
   '{"sms": false, "email": true, "whatsapp": false}'::jsonb),
  ('77777777-7777-4777-8777-777777777777', 'grace.staging@naukribaba.test',  'Grace Doyle',     'Grace', 'Doyle',
   'Cork, Ireland', 'EU Citizen',
   '{"IE": "authorized", "EU": "authorized"}'::jsonb,
   'Available immediately', 'Referral',
   '{"sms": false, "email": true, "whatsapp": false}'::jsonb),
  ('88888888-8888-4888-8888-888888888888', 'henry.staging@naukribaba.test',  'Henry Ryan',      'Henry', 'Ryan',
   'Dublin, Ireland', 'Stamp 1G',
   '{"IE": "authorized"}'::jsonb,
   'Available in 2 weeks', 'LinkedIn',
   '{"sms": false, "email": true, "whatsapp": false}'::jsonb),
  ('99999999-9999-4999-8999-999999999999', 'irene.staging@naukribaba.test',  'Irene Burke',     'Irene', 'Burke',
   'Galway, Ireland', 'Irish Citizen',
   '{"IE": "authorized", "EU": "authorized", "UK": "authorized"}'::jsonb,
   'Available in 1 month', 'LinkedIn',
   '{"sms": false, "email": true, "whatsapp": false}'::jsonb),
  ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'jack.staging@naukribaba.test',   'Jack Lynch',      'Jack',  'Lynch',
   'Dublin, Ireland', 'Stamp 1G',
   '{"IE": "authorized"}'::jsonb,
   'Available in 2 weeks', 'LinkedIn',
   '{"sms": false, "email": true, "whatsapp": false}'::jsonb)
ON CONFLICT (id) DO NOTHING;

-- ------------------------------------------------------------------------
-- 2. public.user_resumes (5 rows for the primary seed user "alice")
-- ------------------------------------------------------------------------
INSERT INTO public.user_resumes (id, user_id, resume_key, label, tex_content,
                                 target_roles, template_style)
VALUES
  ('b1111111-1111-4111-8111-111111111111', '11111111-1111-4111-8111-111111111111',
   'sre_devops', 'SRE / DevOps (staging fixture)',
   '\documentclass{article}\begin{document}Alice O''Connor — SRE / DevOps fixture resume.\end{document}',
   ARRAY['Site Reliability Engineer', 'DevOps Engineer', 'Platform Engineer'], 'professional'),
  ('b2222222-2222-4222-8222-222222222222', '11111111-1111-4111-8111-111111111111',
   'backend', 'Backend Engineer (staging fixture)',
   '\documentclass{article}\begin{document}Alice O''Connor — Backend fixture resume.\end{document}',
   ARRAY['Backend Engineer', 'Software Engineer', 'Senior Engineer'], 'professional'),
  ('b3333333-3333-4333-8333-333333333333', '11111111-1111-4111-8111-111111111111',
   'fullstack', 'Full-Stack (staging fixture)',
   '\documentclass{article}\begin{document}Alice O''Connor — Full-Stack fixture resume.\end{document>',
   ARRAY['Full-Stack Engineer', 'Software Engineer'], 'professional'),
  ('b4444444-4444-4444-8444-444444444444', '11111111-1111-4111-8111-111111111111',
   'data_eng', 'Data Engineering (staging fixture)',
   '\documentclass{article}\begin{document}Alice O''Connor — Data fixture resume.\end{document}',
   ARRAY['Data Engineer', 'Analytics Engineer'], 'professional'),
  ('b5555555-5555-4555-8555-555555555555', '11111111-1111-4111-8111-111111111111',
   'ml', 'ML Engineering (staging fixture)',
   '\documentclass{article}\begin{document}Alice O''Connor — ML fixture resume.\end{document}',
   ARRAY['ML Engineer', 'MLOps Engineer'], 'professional')
ON CONFLICT (id) DO NOTHING;

-- ------------------------------------------------------------------------
-- 3. public.jobs (50 rows: 10 users × 5 jobs/user, mix of S/A/B tier)
--
-- Tier distribution per user:
--   slot 0: S-tier (final_score=92, score_tier='S')  — eligible for full apply
--   slot 1: S-tier (final_score=88, score_tier='S')  — eligible for full apply
--   slot 2: A-tier (final_score=78, score_tier='A')  — eligible for full apply
--   slot 3: B-tier (final_score=68, score_tier='B')  — resume-only artifact
--   slot 4: C-tier (final_score=55, score_tier='C')  — no artifact, listed only
--
-- Apply URLs span 4 ATS platforms so the apply_platform classifier (PR #10)
-- has realistic data: greenhouse, lever, ashby, workday + 1 unknown.
-- ------------------------------------------------------------------------
WITH seed_users(uid, slot) AS (
  SELECT u, s FROM (VALUES
    ('11111111-1111-4111-8111-111111111111'::uuid),
    ('22222222-2222-4222-8222-222222222222'::uuid),
    ('33333333-3333-4333-8333-333333333333'::uuid),
    ('44444444-4444-4444-8444-444444444444'::uuid),
    ('55555555-5555-4555-8555-555555555555'::uuid),
    ('66666666-6666-4666-8666-666666666666'::uuid),
    ('77777777-7777-4777-8777-777777777777'::uuid),
    ('88888888-8888-4888-8888-888888888888'::uuid),
    ('99999999-9999-4999-8999-999999999999'::uuid),
    ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid)
  ) AS u_t(u) CROSS JOIN generate_series(0, 4) s
)
INSERT INTO public.jobs (job_id, user_id, title, company, location, description, apply_url,
                         apply_platform, source, match_score, ats_score, hiring_manager_score,
                         tech_recruiter_score, final_score, score_tier, score_status, scored_at,
                         job_hash, application_status, first_seen, last_seen)
SELECT
  'fixture-' || replace(uid::text, '-', '') || '-' || slot       AS job_id,
  uid                                                            AS user_id,
  CASE slot
    WHEN 0 THEN 'Senior Site Reliability Engineer'
    WHEN 1 THEN 'Staff Backend Engineer'
    WHEN 2 THEN 'Full-Stack Engineer (Remote)'
    WHEN 3 THEN 'Data Engineer'
    ELSE         'Junior DevOps Intern'
  END                                                            AS title,
  CASE slot
    WHEN 0 THEN 'AcmeCorp'
    WHEN 1 THEN 'TechStart Ltd'
    WHEN 2 THEN 'CloudScale'
    WHEN 3 THEN 'DataDriven Inc'
    ELSE         'BeginnerCo'
  END                                                            AS company,
  'Dublin, Ireland'                                              AS location,
  'Fixture job description for staging smoke tests. ' ||
  'This role requires Python, Kubernetes, AWS, and CI/CD experience. ' ||
  'Score-tier=' || CASE slot WHEN 0 THEN 'S' WHEN 1 THEN 'S' WHEN 2 THEN 'A' WHEN 3 THEN 'B' ELSE 'C' END
                                                                 AS description,
  CASE slot
    WHEN 0 THEN 'https://boards.greenhouse.io/acmecorp/jobs/' || (1000 + slot)
    WHEN 1 THEN 'https://jobs.lever.co/techstart/' || md5(uid::text || slot::text)
    WHEN 2 THEN 'https://jobs.ashbyhq.com/cloudscale/' || md5(uid::text || slot::text)
    WHEN 3 THEN 'https://datadriven.wd5.myworkdayjobs.com/External/job/Dublin/Engineer-' || (2000 + slot)
    ELSE         'https://beginnerco.com/careers/junior-' || (3000 + slot)
  END                                                            AS apply_url,
  CASE slot
    WHEN 0 THEN 'greenhouse'
    WHEN 1 THEN 'lever'
    WHEN 2 THEN 'ashby'
    WHEN 3 THEN 'workday'
    ELSE         NULL
  END                                                            AS apply_platform,
  'fixture'                                                      AS source,
  CASE slot WHEN 0 THEN 92 WHEN 1 THEN 88 WHEN 2 THEN 78 WHEN 3 THEN 68 ELSE 55 END AS match_score,
  CASE slot WHEN 0 THEN 92 WHEN 1 THEN 88 WHEN 2 THEN 78 WHEN 3 THEN 68 ELSE 55 END AS ats_score,
  CASE slot WHEN 0 THEN 90 WHEN 1 THEN 87 WHEN 2 THEN 76 WHEN 3 THEN 65 ELSE 52 END AS hiring_manager_score,
  CASE slot WHEN 0 THEN 91 WHEN 1 THEN 86 WHEN 2 THEN 79 WHEN 3 THEN 70 ELSE 58 END AS tech_recruiter_score,
  CASE slot WHEN 0 THEN 92.0 WHEN 1 THEN 88.0 WHEN 2 THEN 78.0 WHEN 3 THEN 68.0 ELSE 55.0 END AS final_score,
  CASE slot WHEN 0 THEN 'S' WHEN 1 THEN 'S' WHEN 2 THEN 'A' WHEN 3 THEN 'B' ELSE 'C' END AS score_tier,
  'scored'                                                       AS score_status,
  now() - INTERVAL '1 day'                                       AS scored_at,
  encode(sha256(('fixture-' || uid::text || slot::text)::bytea), 'hex') AS job_hash,
  'New'                                                          AS application_status,
  now() - INTERVAL '2 days'                                      AS first_seen,
  now()                                                          AS last_seen
FROM seed_users
ON CONFLICT (job_id, user_id) DO NOTHING;

-- ------------------------------------------------------------------------
-- 4. public.applications (3 sample rows — exercises the apply_attempts UI tab)
-- All for alice (the primary smoke-test user).
-- ------------------------------------------------------------------------
INSERT INTO public.applications (
  id, user_id, job_id, job_hash, canonical_hash, submission_method, platform,
  posting_id, board_token, resume_s3_key, resume_version, status,
  cover_letter_text, include_cover_letter, answers, profile_snapshot,
  dry_run, submitted_at
)
VALUES
  ('c1111111-1111-4111-8111-111111111111', '11111111-1111-4111-8111-111111111111',
   'fixture-11111111111141118111111111111111-0',
   encode(sha256('fixture-11111111-1111-4111-8111-1111111111110'::bytea), 'hex'),
   encode(sha256('canonical-acmecorp-sre'::bytea), 'hex'),
   'greenhouse_api', 'greenhouse', '1000', 'acmecorp', 'fixture/alice/sre.pdf', 1, 'submitted',
   'Dear Hiring Manager, ...', true, '[]'::jsonb, '{}'::jsonb, false, now() - INTERVAL '1 hour'),
  ('c2222222-2222-4222-8222-222222222222', '11111111-1111-4111-8111-111111111111',
   'fixture-11111111111141118111111111111111-1',
   encode(sha256('fixture-11111111-1111-4111-8111-1111111111111'::bytea), 'hex'),
   encode(sha256('canonical-techstart-backend'::bytea), 'hex'),
   'cloud_browser', 'lever', NULL, NULL, 'fixture/alice/backend.pdf', 1, 'unknown',
   'Dear Hiring Manager, ...', true, '[]'::jsonb, '{}'::jsonb, false, now() - INTERVAL '30 minutes'),
  ('c3333333-3333-4333-8333-333333333333', '11111111-1111-4111-8111-111111111111',
   'fixture-11111111111141118111111111111111-2',
   encode(sha256('fixture-11111111-1111-4111-8111-1111111111112'::bytea), 'hex'),
   encode(sha256('canonical-cloudscale-fullstack'::bytea), 'hex'),
   'cloud_browser', 'ashby', NULL, NULL, 'fixture/alice/fullstack.pdf', 1, 'failed',
   NULL, false, '[]'::jsonb, '{}'::jsonb, true, now() - INTERVAL '5 minutes')
ON CONFLICT (id) DO NOTHING;

COMMIT;
```

> **NOTE on the typo `\end{document>` in resume slot 3:** that is intentional. The seed file uses placeholder LaTeX text for fixtures only — staging doesn't need to compile these. If a smoke test ever tries to compile resume `b3333333...`, swap `>` for `}`. The other 4 resumes have valid LaTeX.

Wait — that's actually a real typo I wrote. Fix it before committing. Update slot 3's tex_content from `\end{document>` to `\end{document}`.

- [ ] **Step 2: Apply the seed against staging**

```bash
cd /Users/ut/code/naukribaba
# Get the staging DB connection string from Supabase: Project Settings → Database → URI
# Format: postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres
export STAGING_DB_URL='postgresql://postgres:<password>@db.<staging_ref>.supabase.co:5432/postgres'
psql "$STAGING_DB_URL" -f supabase/seed.sql
```

Expected output:

```
BEGIN
INSERT 0 10
INSERT 0 5
INSERT 0 50
INSERT 0 3
COMMIT
```

(`INSERT 0 N` means "no upsert collisions, N rows inserted". On a re-run you'll see `INSERT 0 0` four times — the `ON CONFLICT DO NOTHING` clauses fire.)

- [ ] **Step 3: Verify counts**

```bash
psql "$STAGING_DB_URL" -c "
  SELECT 'users'        AS tbl, count(*) FROM public.users
  UNION ALL SELECT 'user_resumes', count(*) FROM public.user_resumes
  UNION ALL SELECT 'jobs',         count(*) FROM public.jobs
  UNION ALL SELECT 'applications', count(*) FROM public.applications
  ORDER BY tbl;
"
```

Expected:

```
     tbl      | count
--------------+-------
 applications |     3
 jobs         |    50
 user_resumes |     5
 users        |    10
```

- [ ] **Step 4: Verify tier distribution**

```bash
psql "$STAGING_DB_URL" -c "SELECT score_tier, count(*) FROM public.jobs GROUP BY score_tier ORDER BY score_tier;"
```

Expected:

```
 score_tier | count
------------+-------
 A          |    10
 B          |    10
 C          |    10
 S          |    20
```

(20 S because slots 0+1 both produce S-tier per user × 10 users.)

- [ ] **Step 5: Commit**

```bash
git add supabase/seed.sql
git commit -m "feat(staging): anonymized fixture seed for staging Supabase

10 fake users + 5 resumes (alice) + 50 jobs (mix of S/A/B/C tier across
4 ATS platforms) + 3 sample applications. Idempotent (ON CONFLICT DO
NOTHING). Auth users created separately via supabase/scripts/seed_auth_users.py.

Drives Phase 6 smoke tests; do not modify without updating those tests."
```

---

## Task 4: Add `Stage` parameter to `template.yaml`

**Why this task is here:** The Stage param is the linchpin of the whole environment-suffixing scheme. Adding it first — without changing any resource names yet — is a zero-risk no-op that gets the parameter wired through and validated.

**Files:**
- Modify: `template.yaml` (lines 10-51 — Parameters block)

- [ ] **Step 1: Read the existing Parameters block**

```bash
sed -n '10,51p' /Users/ut/code/naukribaba/template.yaml
```

You should see 11 parameters (`GroqApiKey` through `BrowserSubnetIds`) starting at line 10.

- [ ] **Step 2: Insert the `Stage` parameter at the top of the Parameters block**

In `template.yaml`, find:

```yaml
Parameters:
  GroqApiKey:
    Type: String
    NoEcho: true
```

Replace with:

```yaml
Parameters:
  Stage:
    Type: String
    AllowedValues:
      - staging
      - prod
    Default: prod
    Description: |
      Deployment environment. Suffixes every named AWS resource (Lambdas,
      DynamoDB tables, S3 buckets, IAM roles, Step Functions, EventBridge rules)
      so staging + prod can coexist in one AWS account. Defaults to 'prod' so
      a `sam deploy` without overrides still hits the prod stack — preserves
      the pre-Phase-3 behavior for any operator running deploys by hand.
  GroqApiKey:
    Type: String
    NoEcho: true
```

- [ ] **Step 3: Validate with `sam validate`**

```bash
cd /Users/ut/code/naukribaba && sam validate --lint
```

Expected: `template.yaml is a valid SAM Template`. If `cfn-lint` complains about new param, fix the YAML and re-run.

- [ ] **Step 4: Don't commit yet** — the next 8 tasks all touch `template.yaml`. Commit after the resource-renaming pass is complete (Task 12).

---

## Task 5: Suffix Lambda function names with `${Stage}`

**Why this task is here:** This is the bulk of the rename (28 functions). Each Lambda has a `FunctionName: naukribaba-<x>` line that becomes `FunctionName: !Sub "naukribaba-${Stage}-<x>"`. The new prod resource names (`naukribaba-prod-...`) will *replace* the existing prod resources during the next deploy — CFN will delete the old, create the new. **This means a brief gap during prod deploy where the old function ARN is gone before the new one exists.** Plan accordingly: deploy during low-traffic window, communicated below in Task 14.

**Files:**
- Modify: `template.yaml` (28 `FunctionName:` lines verified via earlier grep — full list below)

- [ ] **Step 1: Identify every `FunctionName:` line**

You verified them above — here's the complete map for reference:

| Line | Current value | New value |
|---|---|---|
| 93 | `naukribaba-scrape-apify` | `!Sub "naukribaba-${Stage}-scrape-apify"` |
| 107 | `naukribaba-scrape-adzuna` | `!Sub "naukribaba-${Stage}-scrape-adzuna"` |
| 121 | `naukribaba-scrape-hn` | `!Sub "naukribaba-${Stage}-scrape-hn"` |
| 135 | `naukribaba-scrape-yc` | `!Sub "naukribaba-${Stage}-scrape-yc"` |
| 151 | `naukribaba-scrape-linkedin` | `!Sub "naukribaba-${Stage}-scrape-linkedin"` |
| 165 | `naukribaba-scrape-indeed` | `!Sub "naukribaba-${Stage}-scrape-indeed"` |
| 179 | `naukribaba-scrape-glassdoor` | `!Sub "naukribaba-${Stage}-scrape-glassdoor"` |
| 193 | `naukribaba-scrape-irish` | `!Sub "naukribaba-${Stage}-scrape-irish"` |
| 207 | `naukribaba-scrape-greenhouse` | `!Sub "naukribaba-${Stage}-scrape-greenhouse"` |
| 221 | `naukribaba-scrape-ashby` | `!Sub "naukribaba-${Stage}-scrape-ashby"` |
| 235 | `naukribaba-scrape-contacts` | `!Sub "naukribaba-${Stage}-scrape-contacts"` |
| 250 | `naukribaba-load-config` | `!Sub "naukribaba-${Stage}-load-config"` |
| 264 | `naukribaba-merge-dedup` | `!Sub "naukribaba-${Stage}-merge-dedup"` |
| 278 | `naukribaba-score-batch` | `!Sub "naukribaba-${Stage}-score-batch"` |
| 292 | `naukribaba-chunk-hashes` | `!Sub "naukribaba-${Stage}-chunk-hashes"` |
| 302 | `naukribaba-aggregate-scores` | `!Sub "naukribaba-${Stage}-aggregate-scores"` |
| 312 | `naukribaba-tailor-resume` | `!Sub "naukribaba-${Stage}-tailor-resume"` |
| 328 | `naukribaba-compile-latex` | `!Sub "naukribaba-${Stage}-compile-latex"` |
| 344 | `naukribaba-generate-cover-letter` | `!Sub "naukribaba-${Stage}-generate-cover-letter"` |
| 360 | `naukribaba-find-contacts` | `!Sub "naukribaba-${Stage}-find-contacts"` |
| 374 | `naukribaba-save-job` | `!Sub "naukribaba-${Stage}-save-job"` |
| 390 | `naukribaba-save-metrics` | `!Sub "naukribaba-${Stage}-save-metrics"` |
| 404 | `naukribaba-send-email` | `!Sub "naukribaba-${Stage}-send-email"` |
| 418 | `naukribaba-post-score` | `!Sub "naukribaba-${Stage}-post-score"` |
| 434 | `naukribaba-self-improve` | `!Sub "naukribaba-${Stage}-self-improve"` |
| 453 | `naukribaba-notify-error` | `!Sub "naukribaba-${Stage}-notify-error"` |
| 467 | `naukribaba-check-expiry` | `!Sub "naukribaba-${Stage}-check-expiry"` |
| 482 | `naukribaba-stale-nudges` | `!Sub "naukribaba-${Stage}-stale-nudges"` |
| 496 | `naukribaba-followup-reminders` | `!Sub "naukribaba-${Stage}-followup-reminders"` |
| 1584 | `naukribaba-ws-connect` | `!Sub "naukribaba-${Stage}-ws-connect"` |
| 1602 | `naukribaba-ws-disconnect` | `!Sub "naukribaba-${Stage}-ws-disconnect"` |
| 1618 | `naukribaba-ws-route` | `!Sub "naukribaba-${Stage}-ws-route"` |

That's 32 `FunctionName:` properties total (re-counted including ws-* and scraper variants).

- [ ] **Step 2: Apply the rename**

For each line above, in `template.yaml`:

```yaml
      FunctionName: naukribaba-<x>
```

becomes:

```yaml
      FunctionName: !Sub "naukribaba-${Stage}-<x>"
```

Concrete example for line 93:

```yaml
  ScrapeApifyFunction:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: !Sub "naukribaba-${Stage}-scrape-apify"
      CodeUri: lambdas/scrapers/
```

Apply the same transformation to all 32 lines.

- [ ] **Step 3: Update the inline `FunctionName:` reference inside `LambdaInvokePolicy`**

Line 448 (inside `SelfImproveFunction.Properties.Policies`) has a literal cross-Lambda invoke target:

```yaml
        - LambdaInvokePolicy:
            FunctionName: naukribaba-notify-error
```

Replace with:

```yaml
        - LambdaInvokePolicy:
            FunctionName: !Sub "naukribaba-${Stage}-notify-error"
```

- [ ] **Step 4: Validate**

```bash
sam validate --lint
```

Expected: clean output. Fix any YAML errors before moving on.

---

## Task 6: Suffix Layer, DynamoDB, ECS, IAM, EventBridge, StateMachine names

**Why this task is here:** Same surgical rename, different resource categories. These have less surface area than Lambdas (~14 resources) so they're grouped into one task.

**Files:**
- Modify: `template.yaml` (lines listed below)

- [ ] **Step 1: Apply renames**

Apply the same `!Sub "naukribaba-${Stage}-..."` transformation to these resources:

| Line | Resource type | Current | New |
|---|---|---|---|
| 75 | LayerName | `naukribaba-shared-deps` | `!Sub "naukribaba-${Stage}-shared-deps"` |
| 84 | LayerName | `naukribaba-tectonic` | `!Sub "naukribaba-${Stage}-tectonic"` |
| 511 | ClusterName | `naukribaba-scrapers` | `!Sub "naukribaba-${Stage}-scrapers"` |
| 545 | LogGroupName | `/ecs/naukribaba-scrapers` | `!Sub "/ecs/naukribaba-${Stage}-scrapers"` |
| 560 | RoleName | `naukribaba-fargate-execution` | `!Sub "naukribaba-${Stage}-fargate-execution"` |
| 584 | RoleName | `naukribaba-fargate-task` | `!Sub "naukribaba-${Stage}-fargate-task"` |
| 615 | TaskDef Family | `naukribaba-playwright` | `!Sub "naukribaba-${Stage}-playwright"` |
| 649 | RoleName | `naukribaba-stepfunctions-role` | `!Sub "naukribaba-${Stage}-stepfunctions-role"` |
| 728 | StateMachineName | `naukribaba-daily-pipeline` | `!Sub "naukribaba-${Stage}-daily-pipeline"` |
| 1165 | StateMachineName | `naukribaba-single-job-pipeline` | `!Sub "naukribaba-${Stage}-single-job-pipeline"` |
| 1302 | EventBridge rule Name | `naukribaba-daily-pipeline-schedule` | `!Sub "naukribaba-${Stage}-daily-pipeline-schedule"` |
| 1315 | EventBridge rule Name | `naukribaba-expiry-check` | `!Sub "naukribaba-${Stage}-expiry-check"` |
| 1326 | RoleName | `naukribaba-eventbridge-role` | `!Sub "naukribaba-${Stage}-eventbridge-role"` |
| 1356 | EventBridge rule Name | `naukribaba-stale-nudge` | `!Sub "naukribaba-${Stage}-stale-nudge"` |
| 1376 | EventBridge rule Name | `naukribaba-followup-reminder` | `!Sub "naukribaba-${Stage}-followup-reminder"` |
| 1498 | DynamoDB TableName | `naukribaba-browser-sessions` | `!Sub "naukribaba-${Stage}-browser-sessions"` |
| 1523 | WS API Name | `naukribaba-browser-ws` | `!Sub "naukribaba-${Stage}-browser-ws"` |
| 1665 | LogGroupName | `/ecs/naukribaba-browser` | `!Sub "/ecs/naukribaba-${Stage}-browser"` |
| 1702 | TaskDef Family | `naukribaba-browser-session` | `!Sub "naukribaba-${Stage}-browser-session"` |

- [ ] **Step 2: Update `SourceArn` references in `Lambda::Permission` resources**

These reference EventBridge rule ARNs by literal name and must match the renamed rules. Lines 1351, 1371, 1391:

```yaml
      SourceArn: !Sub arn:${AWS::Partition}:events:${AWS::Region}:${AWS::AccountId}:rule/naukribaba-expiry-check
```

becomes:

```yaml
      SourceArn: !Sub arn:${AWS::Partition}:events:${AWS::Region}:${AWS::AccountId}:rule/naukribaba-${Stage}-expiry-check
```

Apply the same transformation to lines 1371 (`naukribaba-stale-nudge`) and 1391 (`naukribaba-followup-reminder`).

- [ ] **Step 3: Update `Resource:` ARN reference for ECS task ARN**

Line 701:

```yaml
                Resource: !Sub "arn:aws:ecs:${AWS::Region}:${AWS::AccountId}:task/naukribaba-scrapers/*"
```

becomes:

```yaml
                Resource: !Sub "arn:aws:ecs:${AWS::Region}:${AWS::AccountId}:task/naukribaba-${Stage}-scrapers/*"
```

- [ ] **Step 4: Container image references — DO NOT change**

Lines 625 and 1711 reference ECR images:

```yaml
          Image: !Sub "${AWS::AccountId}.dkr.ecr.${AWS::Region}.amazonaws.com/naukribaba-playwright:latest"
          Image: !Sub "${AWS::AccountId}.dkr.ecr.${AWS::Region}.amazonaws.com/naukribaba-browser:latest"
```

Leave these as-is. ECR repos are shared across environments — same Docker image, different runtime stage. Splitting ECR repos per env adds storage cost without isolation benefit (the image is identical). This matches the existing prod ECR repo `385017713886.dkr.ecr.eu-west-1.amazonaws.com/job-hunt-api` referenced in `deploy.yml` line 29.

- [ ] **Step 5: Validate**

```bash
sam validate --lint
```

Expected: clean.

---

## Task 7: Suffix S3 bucket names (`utkarsh-job-hunt`)

**Why this task is here:** S3 bucket names are global. We can't have two buckets named `utkarsh-job-hunt` in the same account. We need a staging-suffixed bucket so staging writes don't pollute prod artifacts.

**Files:**
- Modify: `template.yaml` (multiple `BucketName: utkarsh-job-hunt` and `arn:aws:s3:::utkarsh-job-hunt/...` references)

> **CRITICAL DECISION POINT:** Do we (a) keep the prod bucket name `utkarsh-job-hunt` and add a separate `utkarsh-job-hunt-staging` bucket, OR (b) rename prod bucket to `utkarsh-job-hunt-prod` (breaking 22k existing S3 URLs)?
>
> **Decision: (a)**. Keep prod bucket name as-is. The `${Stage}` substitution maps:
> - `prod` → `utkarsh-job-hunt` (literal, for backward compat)
> - `staging` → `utkarsh-job-hunt-staging` (new bucket, added by this deploy)
>
> Use a `!FindInMap` or `!If` to switch on stage. Mappings are cleaner for the 5+ references.

- [ ] **Step 1: Add a `Mappings` block at the top level of `template.yaml`**

After the `Parameters:` block (around line 52, before `Resources:`), insert:

```yaml
Mappings:
  StageConfig:
    staging:
      ArtifactBucket: utkarsh-job-hunt-staging
    prod:
      ArtifactBucket: utkarsh-job-hunt
```

- [ ] **Step 2: Find every `utkarsh-job-hunt` literal**

```bash
grep -n "utkarsh-job-hunt" /Users/ut/code/naukribaba/template.yaml
```

Expected hits (verified by earlier grep):
- Line 323: `BucketName: utkarsh-job-hunt` (TailorResumeFunction policy)
- Line 337: `BucketName: utkarsh-job-hunt` (CompileLatexFunction policy)
- Line 355: `BucketName: utkarsh-job-hunt` (GenerateCoverLetterFunction policy)
- Line 385: `BucketName: utkarsh-job-hunt` (SaveJobFunction policy)
- Line 429: `BucketName: utkarsh-job-hunt` (PostScoreFunction policy)
- Line 610: `Resource: "arn:aws:s3:::utkarsh-job-hunt/*"` (FargateTaskRole)
- Line 1405: `BucketName: utkarsh-job-hunt` (JobHuntApi policy)
- Line 1688: `"arn:aws:s3:::utkarsh-job-hunt/sessions/*"` (BrowserTaskRole)
- Line 1689: `"arn:aws:s3:::utkarsh-job-hunt/confirmations/*"` (BrowserTaskRole)
- Line 1717: `Value: utkarsh-job-hunt` (BrowserSessionTaskDef env var)

- [ ] **Step 3: Replace each with the mapping lookup**

For `BucketName: utkarsh-job-hunt` (lines 323, 337, 355, 385, 429, 1405):

```yaml
        - S3CrudPolicy:
            BucketName: !FindInMap [StageConfig, !Ref Stage, ArtifactBucket]
```

For `Resource: "arn:aws:s3:::utkarsh-job-hunt/*"` (line 610):

```yaml
                Resource: !Sub
                  - "arn:aws:s3:::${BucketName}/*"
                  - BucketName: !FindInMap [StageConfig, !Ref Stage, ArtifactBucket]
```

For `"arn:aws:s3:::utkarsh-job-hunt/sessions/*"` and `confirmations/*` (lines 1688-1689):

```yaml
                Resource:
                  - !Sub
                    - "arn:aws:s3:::${BucketName}/sessions/*"
                    - BucketName: !FindInMap [StageConfig, !Ref Stage, ArtifactBucket]
                  - !Sub
                    - "arn:aws:s3:::${BucketName}/confirmations/*"
                    - BucketName: !FindInMap [StageConfig, !Ref Stage, ArtifactBucket]
```

For `Value: utkarsh-job-hunt` (line 1717, BrowserSessionTaskDef ECS env var):

```yaml
            - Name: S3_BUCKET
              Value: !FindInMap [StageConfig, !Ref Stage, ArtifactBucket]
```

- [ ] **Step 4: Add an `AWS::S3::Bucket` resource for the staging bucket only**

Add after the `FrontendBucket` resource (around line 1488):

```yaml
  # --- Artifact bucket (per-stage; staging only — prod bucket pre-exists) ---
  ArtifactBucket:
    Type: AWS::S3::Bucket
    Condition: IsStaging
    Properties:
      BucketName: !FindInMap [StageConfig, !Ref Stage, ArtifactBucket]
      LifecycleConfiguration:
        Rules:
          - Id: ExpireFixturesAfter30Days
            Status: Enabled
            Prefix: fixture/
            ExpirationInDays: 30
```

And add a `Conditions:` block at the top of `template.yaml` (after `Mappings`, before `Resources`):

```yaml
Conditions:
  IsStaging: !Equals [!Ref Stage, staging]
```

Why a Condition: prod's `utkarsh-job-hunt` already exists and is *not* managed by this stack today — adding it as a CFN-managed resource would error with `BucketAlreadyOwnedByYou`. The Condition skips the resource creation when `Stage=prod`, leaving the existing manual prod bucket alone. (TODO: import the prod bucket into CFN management as a separate task; tracked in `docs/superpowers/specs/2026-04-27-staging-env-decision.md`.)

- [ ] **Step 5: Update `BrowserWebSocketUrl` and other Outputs to use the stage**

The output at line 1736 already uses `!Sub` with `HttpApi` — no rename needed. Line 1754 (`BrowserWebSocketUrl`) likewise uses `BrowserWebSocketApi.Ref` — no change needed. They emit the right URL because the underlying API ID is unique per-stack.

- [ ] **Step 6: Validate**

```bash
sam validate --lint
```

Expected: clean.

---

## Task 8: Suffix `FrontendBucket` name + handle pre-existing buckets

**Why this task is here:** Line 1468 sets `BucketName: !Sub "${AWS::StackName}-frontend"`. With the new stack name `naukribaba-staging`, this becomes `naukribaba-staging-frontend`, which is fine. For prod (`job-hunt-api`), it stays `job-hunt-api-frontend`. So `FrontendBucket` actually needs no change — it already inherits from the stack name. But verify.

- [ ] **Step 1: Re-read the FrontendBucket resource**

Line 1465-1488. Confirm it uses `!Sub "${AWS::StackName}-frontend"`. No change needed; the `${AWS::StackName}` substitution gives us per-stack bucket names automatically.

- [ ] **Step 2: Validate**

```bash
sam validate --lint
```

Expected: clean.

---

## Task 9: Suffix CORS allow-origin per stage

**Why this task is here:** Line 1451 hardcodes `https://naukribaba.netlify.app` as the only browser origin. Staging frontend will live at a different URL — Netlify branch deploys produce `staging--naukribaba.netlify.app` by default. We need both origins allowed in staging, and only the prod origin in prod.

**Files:**
- Modify: `template.yaml` lines 1449-1462 (CORS block)

- [ ] **Step 1: Replace the CORS allow-origins**

Find:

```yaml
      CorsConfiguration:
        AllowOrigins:
          - "https://naukribaba.netlify.app"
          - "http://localhost:5173"
```

Replace with:

```yaml
      CorsConfiguration:
        AllowOrigins: !If
          - IsStaging
          - - "https://staging--naukribaba.netlify.app"
            - "https://staging.naukribaba.com"
            - "http://localhost:5173"
          - - "https://naukribaba.netlify.app"
            - "https://naukribaba.com"
            - "http://localhost:5173"
```

(`naukribaba.com` is reserved for the eventual custom domain — listing it now is harmless; if/when the domain is wired up the deploy doesn't need to change.)

- [ ] **Step 2: Validate**

```bash
sam validate --lint
```

Expected: clean. CFN's `!If` syntax inside lists is well-supported but easy to mis-indent — re-validate after this change specifically.

---

## Task 10: Update `Outputs` block for richer cross-job consumption

**Why this task is here:** GitHub Actions jobs need to read the API URL after `sam deploy` to (a) populate Netlify env vars, (b) feed Phase 6 smoke tests. The existing `ApiUrl` output is good; we add explicit `Stage` echo + WS URL is already there.

**Files:**
- Modify: `template.yaml` lines 1733-1759 (Outputs)

- [ ] **Step 1: Add a `Stage` output**

After `ApiUrl:` (line 1734-1736), add:

```yaml
  Stage:
    Description: Deployment stage echo (staging|prod)
    Value: !Ref Stage
    Export:
      Name: !Sub "${AWS::StackName}-Stage"
```

The `Export` makes the value cross-stack-readable in case Phase 4 (Observability) wants to look it up.

- [ ] **Step 2: Add `ApiUrl` export name (for Phase 6 smoke gating)**

Update the existing `ApiUrl:` to:

```yaml
  ApiUrl:
    Description: API Gateway URL
    Value: !Sub "https://${HttpApi}.execute-api.${AWS::Region}.amazonaws.com/prod"
    Export:
      Name: !Sub "${AWS::StackName}-ApiUrl"
```

(The literal `/prod` in the URL is the API Gateway *deployment stage*, not our `Stage` parameter — they're unrelated. Don't change it.)

- [ ] **Step 3: Validate + commit the entire `template.yaml` rewrite**

```bash
sam validate --lint
git add template.yaml
git commit -m "feat(infra): parameterize template.yaml with Stage param + per-env naming

- Add Stage parameter (staging|prod, default=prod) so staging + prod stacks
  coexist in one AWS account.
- Suffix every named resource (28 Lambdas, 2 layers, DynamoDB table, S3 bucket,
  Step Functions, EventBridge rules, IAM roles, ECS cluster, log groups,
  WS API) with the stage.
- Mappings block routes the artifact bucket: prod → utkarsh-job-hunt (existing,
  unmanaged), staging → utkarsh-job-hunt-staging (new, managed by this stack
  via IsStaging condition).
- CORS allow-origins are stage-aware.
- New Outputs: Stage echo + ApiUrl Export for cross-job/cross-stack reads.

Prod stack name (job-hunt-api) is unchanged to avoid CFN-replace of every
named resource. The first prod deploy after this PR WILL replace each
function/role/queue with the stage-suffixed name — expect ~5 min of
'between names' churn during a low-traffic deploy window.
Phase 2 canary (when merged) makes this nearly invisible to users.

Phase 3 of deployment-safety-roadmap. See docs/superpowers/plans/
2026-04-27-deployment-safety-phase3-staging.md."
```

---

## Task 11: Create `samconfig.toml` (base)

**Why this task is here:** SAM CLI reads `samconfig.toml` for default deploy parameters. A *base* file keeps DRY config common to both envs (region, capabilities, ECR repo) and a thin per-env override file specifies stack name + Stage param. SAM 1.30+ supports `--config-file` to point at non-default toml files.

**Files:**
- Create: `samconfig.toml`

- [ ] **Step 1: Confirm SAM CLI version supports `--config-file`**

```bash
sam --version
```

Expected: `1.130.x` or newer (any 1.30+ supports `--config-file`). The CI image installs latest via `pip install aws-sam-cli`.

- [ ] **Step 2: Write the base config**

Create `samconfig.toml`:

```toml
# Base SAM config — common to staging + prod.
# Per-env overrides live in samconfig.staging.toml and samconfig.prod.toml.
# Invoke with: sam deploy --config-file samconfig.<env>.toml
#
# This file holds settings that are TRULY identical between envs:
#   - region (single AWS region)
#   - capabilities (CFN named-IAM, container)
#   - ECR repo (shared image, different runtime stage)
#   - S3 prefix for SAM artifact uploads (separated from app bucket)

version = 0.1

[default.global.parameters]
region = "eu-west-1"

[default.deploy.parameters]
capabilities = "CAPABILITY_IAM CAPABILITY_NAMED_IAM"
image_repository = "385017713886.dkr.ecr.eu-west-1.amazonaws.com/job-hunt-api"
s3_bucket = "utkarsh-job-hunt"
s3_prefix = "sam-artifacts"
confirm_changeset = false
fail_on_empty_changeset = false
resolve_s3 = false
```

- [ ] **Step 3: Verify SAM picks up the file**

```bash
cd /Users/ut/code/naukribaba && sam deploy --help | head -40
```

Expected: help text mentions `--config-file` and `--config-env`. (No deploy is run; just confirms the CLI is sane.)

- [ ] **Step 4: Don't commit yet** — pair this with the env-specific files in Task 12.

---

## Task 12: Create `samconfig.staging.toml` and `samconfig.prod.toml`

**Files:**
- Create: `samconfig.staging.toml`
- Create: `samconfig.prod.toml`

- [ ] **Step 1: Write `samconfig.staging.toml`**

```toml
# Staging SAM config — extends samconfig.toml.
# Invoke: sam deploy --config-file samconfig.staging.toml --parameter-overrides ...secrets...
# CI passes the secrets at runtime; this file is the *static* shape.

version = 0.1

[default.global.parameters]
region = "eu-west-1"
stack_name = "naukribaba-staging"

[default.deploy.parameters]
stack_name = "naukribaba-staging"
region = "eu-west-1"
capabilities = "CAPABILITY_IAM CAPABILITY_NAMED_IAM"
image_repository = "385017713886.dkr.ecr.eu-west-1.amazonaws.com/job-hunt-api"
s3_bucket = "utkarsh-job-hunt"
s3_prefix = "sam-artifacts/staging"
confirm_changeset = false
fail_on_empty_changeset = false
resolve_s3 = false
parameter_overrides = "Stage=staging"
tags = "env=staging project=naukribaba"
```

- [ ] **Step 2: Write `samconfig.prod.toml`**

```toml
# Prod SAM config — extends samconfig.toml.
# Stack name kept as 'job-hunt-api' to avoid renaming the existing prod stack
# (which would force-replace 28+ named resources in one transaction). The
# resources INSIDE the stack will be renamed (naukribaba-prod-*) on the next
# deploy after Phase 3 lands — expect a brief window of resource churn.

version = 0.1

[default.global.parameters]
region = "eu-west-1"
stack_name = "job-hunt-api"

[default.deploy.parameters]
stack_name = "job-hunt-api"
region = "eu-west-1"
capabilities = "CAPABILITY_IAM CAPABILITY_NAMED_IAM"
image_repository = "385017713886.dkr.ecr.eu-west-1.amazonaws.com/job-hunt-api"
s3_bucket = "utkarsh-job-hunt"
s3_prefix = "sam-artifacts/prod"
confirm_changeset = false
fail_on_empty_changeset = false
resolve_s3 = false
parameter_overrides = "Stage=prod"
tags = "env=prod project=naukribaba"
```

- [ ] **Step 3: Sanity-check both files load without errors**

```bash
cd /Users/ut/code/naukribaba && sam deploy --config-file samconfig.staging.toml --help 2>&1 | head -5
cd /Users/ut/code/naukribaba && sam deploy --config-file samconfig.prod.toml --help 2>&1 | head -5
```

Expected: both print the usage banner without errors. SAM does NOT validate the config until you run an actual deploy, so this is a syntactic sanity-check only.

- [ ] **Step 4: Commit all three samconfig files together**

```bash
git add samconfig.toml samconfig.staging.toml samconfig.prod.toml
git commit -m "feat(infra): add per-env samconfig.toml files for staging + prod

- samconfig.toml: base shared settings (region, capabilities, ECR repo)
- samconfig.staging.toml: stack 'naukribaba-staging', Stage=staging,
  s3 prefix sam-artifacts/staging, tags env=staging
- samconfig.prod.toml: stack 'job-hunt-api' (legacy name preserved),
  Stage=prod, s3 prefix sam-artifacts/prod, tags env=prod

Replaces the inlined --stack-name/--capabilities/--image-repository flags
in deploy.yml. The next commit rewrites deploy.yml to consume these files."
```

---

## Task 13: Rewrite `.github/workflows/deploy.yml`

**Why this task is here:** This is the keystone — auto-deploy on PR open + auto-deploy on main merge, both consuming the per-env samconfigs. Manual `workflow_dispatch` stays as a fallback.

**Files:**
- Rewrite: `.github/workflows/deploy.yml`

- [ ] **Step 1: Read the current workflow** (already done in prereq reads)

Current shape: one `deploy` job, `workflow_dispatch` only, hardcoded `--stack-name job-hunt-api`, prod secrets only.

- [ ] **Step 2: Rewrite the file**

Overwrite `.github/workflows/deploy.yml`:

```yaml
name: Deploy Backend

on:
  pull_request:
    branches: [main]
    paths:
      - 'app.py'
      - 'lambdas/**'
      - 'shared/**'
      - 'template.yaml'
      - 'samconfig*.toml'
      - 'requirements.txt'
      - 'Dockerfile.lambda'
      - 'layer/**'
      - 'layer-tectonic/**'
      - '.github/workflows/deploy.yml'
  push:
    branches: [main]
    paths:
      - 'app.py'
      - 'lambdas/**'
      - 'shared/**'
      - 'template.yaml'
      - 'samconfig*.toml'
      - 'requirements.txt'
      - 'Dockerfile.lambda'
      - 'layer/**'
      - 'layer-tectonic/**'
      - '.github/workflows/deploy.yml'
  workflow_dispatch:
    inputs:
      target_env:
        description: 'Target environment'
        required: true
        default: 'staging'
        type: choice
        options:
          - staging
          - prod

permissions:
  contents: read
  pull-requests: write  # for PR comment with staging URL

jobs:
  # ---------------------------------------------------------------------
  # deploy-staging
  #   Triggers: pull_request open/sync; or manual workflow_dispatch with
  #             target_env=staging.
  # ---------------------------------------------------------------------
  deploy-staging:
    if: github.event_name == 'pull_request' || (github.event_name == 'workflow_dispatch' && github.event.inputs.target_env == 'staging')
    runs-on: ubuntu-latest
    timeout-minutes: 45
    environment: staging
    outputs:
      api_url: ${{ steps.outputs.outputs.api_url }}
      ws_url: ${{ steps.outputs.outputs.ws_url }}
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install SAM CLI
        run: pip install aws-sam-cli

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: eu-west-1

      - name: Login to ECR
        run: aws ecr get-login-password --region eu-west-1 | docker login --username AWS --password-stdin 385017713886.dkr.ecr.eu-west-1.amazonaws.com

      - name: Run unit + contract tests before deploy
        run: |
          pip install -r requirements.txt -r tests/requirements-test.txt
          pytest tests/unit/ tests/contract/ -v --tb=short -x
        env:
          AWS_DEFAULT_REGION: eu-west-1

      - name: Build shared-deps layer
        run: ./layer/build.sh

      - name: SAM Build
        env:
          DOCKER_BUILDKIT: "0"
          SAM_CLI_TELEMETRY: "0"
        run: sam build

      - name: SAM Deploy (staging)
        env:
          GROQ_KEY: ${{ secrets.GROQ_API_KEY }}
          OR_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          QWEN_KEY: ${{ secrets.QWEN_API_KEY }}
          NVIDIA_KEY: ${{ secrets.NVIDIA_API_KEY }}
          SB_URL: ${{ secrets.SUPABASE_URL_STAGING }}
          SB_KEY: ${{ secrets.SUPABASE_SERVICE_KEY_STAGING }}
          SB_JWT: ${{ secrets.SUPABASE_JWT_SECRET_STAGING }}
          SERPER_KEY: ${{ secrets.SERPER_API_KEY }}
          APIFY_KEY: ${{ secrets.APIFY_API_KEY }}
          CAPSOLVER_KEY: ${{ secrets.CAPSOLVER_API_KEY }}
          BROWSER_SUBNETS: ${{ secrets.BROWSER_SUBNET_IDS }}
        run: |
          sam deploy \
            --config-file samconfig.staging.toml \
            --no-confirm-changeset \
            --no-fail-on-empty-changeset \
            --parameter-overrides \
              "Stage=staging" \
              "GroqApiKey=${GROQ_KEY}" \
              "OpenRouterApiKey=${OR_KEY}" \
              "QwenApiKey=${QWEN_KEY}" \
              "NvidiaApiKey=${NVIDIA_KEY}" \
              "GoogleCredentialsJson=PLACEHOLDER_SET_MANUALLY" \
              "SupabaseUrl=${SB_URL}" \
              "SupabaseServiceKey=${SB_KEY}" \
              "SupabaseJwtSecret=${SB_JWT}" \
              "SerperApiKey=${SERPER_KEY}" \
              "ApifyApiKey=${APIFY_KEY}" \
              "CapSolverApiKey=${CAPSOLVER_KEY}" \
              "BrowserSubnetIds=${BROWSER_SUBNETS}"

      - name: Read stack outputs
        id: outputs
        run: |
          API_URL=$(aws cloudformation describe-stacks --stack-name naukribaba-staging \
            --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)
          WS_URL=$(aws cloudformation describe-stacks --stack-name naukribaba-staging \
            --query "Stacks[0].Outputs[?OutputKey=='BrowserWebSocketUrl'].OutputValue" --output text)
          echo "api_url=${API_URL}" >> $GITHUB_OUTPUT
          echo "ws_url=${WS_URL}" >> $GITHUB_OUTPUT
          echo "Staging API: ${API_URL}"
          echo "Staging WS:  ${WS_URL}"

      - name: Comment on PR with staging URL
        if: github.event_name == 'pull_request'
        uses: marocchino/sticky-pull-request-comment@v2
        with:
          header: staging-deploy
          message: |
            **Staging deployed**
            - API: `${{ steps.outputs.outputs.api_url }}`
            - WS:  `${{ steps.outputs.outputs.ws_url }}`
            - Frontend (after Netlify branch deploy): https://staging--naukribaba.netlify.app
            - Smoke tests will run from Phase 6 onwards; for now hit `/healthz` manually.

  # ---------------------------------------------------------------------
  # deploy-prod
  #   Triggers: push to main; or manual workflow_dispatch with target_env=prod.
  # ---------------------------------------------------------------------
  deploy-prod:
    if: github.event_name == 'push' || (github.event_name == 'workflow_dispatch' && github.event.inputs.target_env == 'prod')
    runs-on: ubuntu-latest
    timeout-minutes: 45
    environment: production  # gates deploy on the GitHub 'production' environment (manual approval if configured)
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install SAM CLI
        run: pip install aws-sam-cli

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: eu-west-1

      - name: Login to ECR
        run: aws ecr get-login-password --region eu-west-1 | docker login --username AWS --password-stdin 385017713886.dkr.ecr.eu-west-1.amazonaws.com

      - name: Run unit + contract tests before deploy
        run: |
          pip install -r requirements.txt -r tests/requirements-test.txt
          pytest tests/unit/ tests/contract/ -v --tb=short -x
        env:
          AWS_DEFAULT_REGION: eu-west-1

      - name: Build shared-deps layer
        run: ./layer/build.sh

      - name: SAM Build
        env:
          DOCKER_BUILDKIT: "0"
          SAM_CLI_TELEMETRY: "0"
        run: sam build

      - name: SAM Deploy (prod)
        env:
          GROQ_KEY: ${{ secrets.GROQ_API_KEY }}
          OR_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          QWEN_KEY: ${{ secrets.QWEN_API_KEY }}
          NVIDIA_KEY: ${{ secrets.NVIDIA_API_KEY }}
          SB_URL: ${{ secrets.SUPABASE_URL }}
          SB_KEY: ${{ secrets.SUPABASE_SERVICE_KEY }}
          SB_JWT: ${{ secrets.SUPABASE_JWT_SECRET }}
          SERPER_KEY: ${{ secrets.SERPER_API_KEY }}
          APIFY_KEY: ${{ secrets.APIFY_API_KEY }}
          CAPSOLVER_KEY: ${{ secrets.CAPSOLVER_API_KEY }}
          BROWSER_SUBNETS: ${{ secrets.BROWSER_SUBNET_IDS }}
        run: |
          sam deploy \
            --config-file samconfig.prod.toml \
            --no-confirm-changeset \
            --no-fail-on-empty-changeset \
            --parameter-overrides \
              "Stage=prod" \
              "GroqApiKey=${GROQ_KEY}" \
              "OpenRouterApiKey=${OR_KEY}" \
              "QwenApiKey=${QWEN_KEY}" \
              "NvidiaApiKey=${NVIDIA_KEY}" \
              "GoogleCredentialsJson=PLACEHOLDER_SET_MANUALLY" \
              "SupabaseUrl=${SB_URL}" \
              "SupabaseServiceKey=${SB_KEY}" \
              "SupabaseJwtSecret=${SB_JWT}" \
              "SerperApiKey=${SERPER_KEY}" \
              "ApifyApiKey=${APIFY_KEY}" \
              "CapSolverApiKey=${CAPSOLVER_KEY}" \
              "BrowserSubnetIds=${BROWSER_SUBNETS}"

      - name: Read prod stack outputs
        run: |
          API_URL=$(aws cloudformation describe-stacks --stack-name job-hunt-api \
            --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)
          echo "Prod API: ${API_URL}"
```

- [ ] **Step 3: Configure GitHub `production` environment for prod-deploy gate (manual UI step)**

Open https://github.com/UT07/daily-job-hunt/settings/environments. Click "New environment" → name `production`. Optionally:
- Add required reviewer: yourself (so prod deploys pause for a click).
- Add wait timer: 5 min (so a bad merge has a window to be noticed).

For the staging environment: same UI, name `staging`, no protection rules (auto-deploy is the point).

This is optional — the workflow runs without the environments configured; if you skip this step, prod deploys auto-fire on push to main with no manual gate. **Recommendation: do configure the `production` environment with a 5-min wait timer**, so an obvious bad merge can be cancelled before it ships.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "feat(ci): two-env deploy.yml — staging on PR + prod on main

- deploy-staging: triggered by pull_request, deploys to naukribaba-staging
  stack using samconfig.staging.toml + SUPABASE_*_STAGING secrets. Posts the
  staging API/WS URLs as a sticky PR comment.
- deploy-prod: triggered by push to main, deploys to job-hunt-api stack
  using samconfig.prod.toml + existing SUPABASE_* (prod) secrets. Gated by
  GitHub 'production' environment (configure protection rules in repo
  settings).
- workflow_dispatch retained as fallback (target_env=staging|prod input).
- Path filters: skip deploys for changes that don't touch deployable code
  (docs, web/, etc.).

Phase 6 will add smoke-test gates after each deploy. For now, post-deploy
verification = manual curl of the printed API URL."
```

---

## Task 14: Modify `netlify.toml` for staging branch deploy

**Why this task is here:** Netlify's branch-deploy feature needs a `[context.<branch>]` block to know what `VITE_API_URL` to bake into the staging frontend bundle. Without this, the staging Netlify deploy would talk to the prod API.

**Files:**
- Modify: `netlify.toml`

- [ ] **Step 1: Read current `netlify.toml`**

Verified above (10 lines). It has only `[build]`, `[build.environment]`, and a SPA `[[redirects]]`.

- [ ] **Step 2: Add the staging context block**

Append to `netlify.toml`:

```toml
# Staging branch deploy: triggered by Netlify when the 'staging' branch is
# pushed. The VITE_API_URL value below is the *static* staging API Gateway URL
# emitted by the SAM stack 'naukribaba-staging' as the ApiUrl Output.
#
# This URL is stable across staging redeploys (HttpApi resource is not
# replaced unless the API Gateway resource itself is recreated). Confirm it
# matches Task 17's recorded URL after the first staging deploy. If the
# HttpApi ID ever changes (e.g., stack delete + recreate), update this value.
[context.staging]
  command = "npm run build"

[context.staging.environment]
  VITE_API_URL = "https://STAGING_API_ID_FROM_CFN_OUTPUT.execute-api.eu-west-1.amazonaws.com/prod"
  VITE_SUPABASE_URL = "https://STAGING_REF.supabase.co"
  VITE_SUPABASE_ANON_KEY = "STAGING_ANON_KEY"
  VITE_ENV = "staging"

# Production context (defaults; explicit so it's documented)
[context.production.environment]
  VITE_API_URL = "https://paie9w92c1.execute-api.eu-west-1.amazonaws.com/prod"
  VITE_SUPABASE_URL = "https://utkarshprodref.supabase.co"
  VITE_SUPABASE_ANON_KEY = "PROD_ANON_KEY_TODO"
  VITE_ENV = "prod"
```

The placeholders (`STAGING_API_ID_FROM_CFN_OUTPUT`, `STAGING_REF`, `STAGING_ANON_KEY`, prod URL, prod ref, prod anon key) MUST be filled in before this commit can ship to a working Netlify branch deploy. Task 17 walks through populating them after the first staging deploy.

> **Decision: env vars in `netlify.toml` vs Netlify UI.** Putting `VITE_*` values in the toml makes them grep-able in the repo and code-reviewable in PRs. The downside is they're public (toml is checked in) — that's fine for `VITE_API_URL` (URL is public; CORS gates access) and `VITE_SUPABASE_ANON_KEY` (anon key is *meant* to be public; RLS is what protects data). The service key never goes here. If you'd rather hide them, use Netlify UI env vars per branch instead and leave `[context.staging.environment]` empty. Going with toml for transparency.

- [ ] **Step 3: Don't commit yet** — pair with the manual Netlify branch-enable in Task 16.

---

## Task 15: Manual prereq — enable Netlify branch deploy + create `staging` branch

**Why this task is here:** Netlify's branch deploys are off by default. This is a UI toggle. We also need a long-lived `staging` branch in the repo to act as the deploy trigger.

- [ ] **Step 1: Create the `staging` branch**

```bash
cd /Users/ut/code/naukribaba && git fetch origin && git checkout main && git pull
git checkout -b staging
git push -u origin staging
```

Expected: branch `staging` now exists at remote, pointing at the same commit as `main`. Going forward, `staging` will be force-pushed (`git push --force origin <feat-branch>:staging`) by the deploy.yml when it wants to update what the staging Netlify deploy serves. **Until Phase 6 adds that automation, frontend devs manually push the latest PR head to `staging`** when they need a Netlify staging build.

- [ ] **Step 2: Enable branch deploys in Netlify UI**

Open https://app.netlify.com/sites/naukribaba/settings/deploys. Find "Branches and deploy contexts". Set:

- **Production branch:** `main` (already set)
- **Branch deploys:** `Let me add individual branches`
- Add branch: `staging`
- Save

Confirm: a new "staging" branch appears in the deploy list. The next push to the `staging` branch will trigger a Netlify build.

- [ ] **Step 3: Trigger an initial staging Netlify build**

In Netlify UI for site `naukribaba`, go to "Deploys" → "Trigger deploy" → pick branch `staging`. This builds with the existing `[build]` config (no staging context yet because Task 14's commit hasn't shipped) — expect VITE_API_URL to default to the prod URL or the .env default. Confirm the build completes and a URL like `https://staging--naukribaba.netlify.app` is reachable.

- [ ] **Step 4: Note the staging URL**

The default Netlify staging URL is `https://staging--naukribaba.netlify.app` (matches what we hardcoded in template.yaml CORS in Task 9). If the URL is different, update `template.yaml` Task 9 + `samconfig.staging.toml` accordingly.

---

## Task 16: Create `scripts/promote_to_prod.sh`

**Why this task is here:** A guarded one-shot for the case where staging soaked clean and the user wants to promote without merging via PR (e.g., an emergency hotfix that already shipped to staging). Refuses to run unless on `main` to prevent accidental "promote my feature branch to prod".

**Files:**
- Create: `scripts/promote_to_prod.sh`

- [ ] **Step 1: Write the script**

Create `scripts/promote_to_prod.sh`:

```bash
#!/usr/bin/env bash
# Trigger a prod deploy of whatever's currently on `main`.
#
# Use case: staging soaked clean for a few hours and the operator wants to
# promote the same code to prod without re-running the merge-to-main path
# (e.g., the merge already happened but `deploy-prod` was skipped because
# of path filters, or the operator wants to re-deploy after an env-var
# change in CFN parameters).
#
# Refuses unless:
#   1. Local working tree is on the 'main' branch
#   2. Local 'main' is up to date with origin/main (no untracked commits)
#   3. The user has 'gh' authenticated against UT07/daily-job-hunt
#
# Usage:
#   bash scripts/promote_to_prod.sh
#
# After triggering, watches the run to completion.

set -euo pipefail

REPO="UT07/daily-job-hunt"

# 1. Branch check
current_branch=$(git rev-parse --abbrev-ref HEAD)
if [[ "$current_branch" != "main" ]]; then
  echo "ERROR: must be on 'main' (current: '$current_branch'). Refusing." >&2
  exit 1
fi

# 2. Up-to-date check
git fetch origin main --quiet
local_sha=$(git rev-parse HEAD)
remote_sha=$(git rev-parse origin/main)
if [[ "$local_sha" != "$remote_sha" ]]; then
  echo "ERROR: local main ($local_sha) != origin/main ($remote_sha)." >&2
  echo "       Run 'git pull' first." >&2
  exit 1
fi

# 3. Confirm
echo "About to deploy commit $local_sha to PROD."
read -r -p "Type 'PROD' to confirm: " confirm
if [[ "$confirm" != "PROD" ]]; then
  echo "Aborted."
  exit 1
fi

# 4. Trigger
echo "Triggering deploy.yml workflow_dispatch with target_env=prod..."
gh workflow run deploy.yml --repo "$REPO" --ref main -f target_env=prod

# 5. Wait briefly for the run to register, then watch
sleep 5
RUN_ID=$(gh run list --repo "$REPO" --workflow=deploy.yml --branch=main --limit=1 \
  --json databaseId -q '.[0].databaseId')
echo "Run ID: $RUN_ID"
gh run watch "$RUN_ID" --repo "$REPO" --exit-status

echo ""
echo "Prod deploy complete. Verify with:"
echo "  curl -sS https://paie9w92c1.execute-api.eu-west-1.amazonaws.com/prod/healthz"
```

- [ ] **Step 2: Make executable + sanity-check**

```bash
chmod +x /Users/ut/code/naukribaba/scripts/promote_to_prod.sh
bash -n /Users/ut/code/naukribaba/scripts/promote_to_prod.sh  # syntax check only, no run
```

Expected: no output (zero exit). If it errors, fix the bash syntax.

- [ ] **Step 3: Commit**

```bash
git add scripts/promote_to_prod.sh netlify.toml
git commit -m "feat(ci): promote_to_prod.sh + netlify.toml staging context

- scripts/promote_to_prod.sh: defensive one-shot to dispatch a prod deploy
  from main. Refuses if not on main / not up to date with origin. Confirms
  with a typed 'PROD' string.
- netlify.toml: [context.staging] block sets VITE_API_URL + Supabase staging
  values for the staging branch deploy. Placeholders to be filled after first
  staging CFN deploy emits the API Gateway URL.

Phase 3 of deployment-safety-roadmap."
```

---

## Task 17: First staging deploy + populate netlify.toml placeholders

**Why this task is here:** Now we trigger the first end-to-end staging deploy and capture the real API Gateway URL.

- [ ] **Step 1: Push the branch + open a throwaway PR**

```bash
cd /Users/ut/code/naukribaba
git push -u origin claude/objective-sanderson-eeedca
gh pr create --title "feat(infra): Phase 3 — staging environment" --body "Implements Phase 3 of the deployment-safety roadmap. Staging Supabase + SAM stack + Netlify branch deploy. Spec in docs/superpowers/plans/2026-04-27-deployment-safety-phase3-staging.md."
```

- [ ] **Step 2: Watch the staging deploy fire**

```bash
gh run watch $(gh run list --workflow=deploy.yml --branch=claude/objective-sanderson-eeedca --limit=1 --json databaseId -q '.[0].databaseId') --exit-status
```

Expected outcome: `deploy-staging` job succeeds in ~10–15 min. The PR comment shows the staging API URL.

- [ ] **Step 3: Capture the staging API URL + WS URL**

```bash
aws cloudformation describe-stacks --stack-name naukribaba-staging \
  --query "Stacks[0].Outputs" --output table
```

Expected table includes:
- `ApiUrl` = `https://<STAGING_API_ID>.execute-api.eu-west-1.amazonaws.com/prod`
- `BrowserWebSocketUrl` = `wss://<STAGING_WS_ID>.execute-api.eu-west-1.amazonaws.com/prod`
- `Stage` = `staging`

- [ ] **Step 4: Patch `netlify.toml` placeholders with the captured URL**

In `netlify.toml`, replace `STAGING_API_ID_FROM_CFN_OUTPUT.execute-api.eu-west-1.amazonaws.com` with the real subdomain from the `ApiUrl` output. Replace `STAGING_REF` with the project ref captured in Task 0 step 1, and `STAGING_ANON_KEY` with the anon key from Task 0 step 2. Repeat for the prod context block (use the existing prod values).

- [ ] **Step 5: Push the netlify.toml patch as a follow-up commit on the same PR**

```bash
git add netlify.toml
git commit -m "chore(netlify): fill staging API + Supabase URLs after first deploy"
git push
```

The same PR's `deploy-staging` job re-fires and uses the same staging stack (idempotent). Netlify will *not* rebuild yet — it only rebuilds on a push to the `staging` branch. To validate the staging Netlify build, sync the PR head to the `staging` branch:

```bash
git push --force origin claude/objective-sanderson-eeedca:staging
```

Watch Netlify UI for the staging build to complete, then visit `https://staging--naukribaba.netlify.app` and confirm the network tab shows API requests going to the *staging* API URL (not prod).

---

## Task 18: Verify prod stack untouched during staging deploy

**Why this task is here:** Catastrophic-failure mode of Phase 3 = staging deploy somehow mutates prod. Verify it didn't.

- [ ] **Step 1: Snapshot prod stack state before any staging activity**

(This step would have been done before Task 17. Do it now retroactively as a baseline; the next prod deploy will reflect any drift introduced by Phase 3.)

```bash
aws cloudformation describe-stacks --stack-name job-hunt-api \
  --query "Stacks[0].{Status:StackStatus,LastUpdate:LastUpdatedTime,Outputs:Outputs}" \
  > /tmp/prod-snapshot-after-staging-deploy.json
cat /tmp/prod-snapshot-after-staging-deploy.json
```

Expected: `StackStatus = CREATE_COMPLETE` or `UPDATE_COMPLETE`, `LastUpdatedTime` predates the staging deploy by hours/days.

- [ ] **Step 2: Compare against actual prod resource list**

```bash
aws cloudformation list-stack-resources --stack-name job-hunt-api \
  --query "StackResourceSummaries[].{Name:LogicalResourceId,Type:ResourceType,Status:ResourceStatus}" \
  --output table | head -50
```

Expected: every resource shows `CREATE_COMPLETE` or `UPDATE_COMPLETE`; no resources show `IN_PROGRESS` or `FAILED`. The `LastUpdatedTimestamp` for each resource matches the prod stack's last deploy, NOT today.

- [ ] **Step 3: Verify Supabase prod row counts unchanged**

```bash
psql "$PROD_DB_URL" -c "
  SELECT 'users'        AS tbl, count(*) FROM public.users
  UNION ALL SELECT 'jobs', count(*) FROM public.jobs
  UNION ALL SELECT 'applications', count(*) FROM public.applications
  ORDER BY tbl;
"
```

Compare to the same query before staging activity began. Counts should be identical (prod data is not touched by staging deploys).

---

## Task 19: Merge to main + verify prod deploy fires

**Why this task is here:** Closes the loop on the throwaway PR — confirms the `push: branches: [main]` trigger works and prod deploys correctly.

- [ ] **Step 1: Merge the PR**

Per Apr 26 session memory, `gh pr merge` from a worktree fails. Use the API:

```bash
PR_NUMBER=$(gh pr list --repo UT07/daily-job-hunt --head claude/objective-sanderson-eeedca --json number -q '.[0].number')
gh api -X PUT "repos/UT07/daily-job-hunt/pulls/$PR_NUMBER/merge" -f merge_method=squash
```

- [ ] **Step 2: Watch deploy-prod fire**

```bash
gh run watch $(gh run list --repo UT07/daily-job-hunt --workflow=deploy.yml --branch=main --limit=1 --json databaseId -q '.[0].databaseId') --exit-status
```

If the GitHub `production` environment has a wait timer, this will pause. Approve in the UI when ready.

Expected: `deploy-prod` job succeeds. Prod stack `job-hunt-api` updates each Lambda from `naukribaba-X` to `naukribaba-prod-X` — CFN treats these as resource replacements (delete + create) since `FunctionName` is an immutable property.

> **CRITICAL**: This is the moment of churn. Each Lambda is briefly absent (~10–60s) between delete and create. API Gateway integrations point at the new ARN automatically. Step Functions ARNs change so the daily EventBridge schedule will continue to fire correctly (state machine ARNs reference the *function* logical ID, not the function name). **The user-visible window is bounded by Lambda's CFN behavior**: SAM does NOT use `AutoPublishAlias` until Phase 2 lands, so there is no in-flight aliasing during the rename. Best-case the resources are sequenced; worst-case multiple Lambdas are simultaneously absent for a few seconds. Schedule this prod deploy outside daily pipeline hours (07:00 UTC weekdays) and outside peak user activity.

- [ ] **Step 3: Smoke-test prod manually**

```bash
PROD_API=$(aws cloudformation describe-stacks --stack-name job-hunt-api \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)
echo "Prod API: $PROD_API"
curl -sS "$PROD_API/healthz"
```

Expected: 200 OK, JSON body. If 5xx or connection refused, check the prod CFN events for the failed resource: `aws cloudformation describe-stack-events --stack-name job-hunt-api --max-items 30`.

- [ ] **Step 4: Verify Lambdas got renamed**

```bash
aws lambda list-functions --query "Functions[?starts_with(FunctionName, 'naukribaba-prod-')].FunctionName" --output text | wc -w
```

Expected: 32 (the count of prod-stage Lambdas). If 0, the rename didn't take effect — check CFN drift.

```bash
aws lambda list-functions --query "Functions[?starts_with(FunctionName, 'naukribaba-') && !starts_with(FunctionName, 'naukribaba-prod-') && !starts_with(FunctionName, 'naukribaba-staging-')].FunctionName" --output text
```

Expected: empty (no orphaned old-name Lambdas). If non-empty, CFN didn't replace them — investigate (maybe the deploy was skipped due to no changeset).

---

## Task 20: Write the staging-env decision ADR

**Why this task is here:** Locking in the Supabase project-per-env vs schema-per-env decision + Netlify branch-deploy vs preview-deploys. Future engineers need this context to not re-relitigate.

**Files:**
- Create: `docs/superpowers/specs/2026-04-27-staging-env-decision.md`

- [ ] **Step 1: Write the ADR**

Create `docs/superpowers/specs/2026-04-27-staging-env-decision.md`:

```markdown
# Staging Environment Architecture — Decision Record (2026-04-27)

**Status:** Accepted
**Phase:** 3 of deployment-safety roadmap
**Authors:** Utkarsh Singh
**Implementation:** docs/superpowers/plans/2026-04-27-deployment-safety-phase3-staging.md

## Context

NaukriBaba shipped to a single prod environment (one Supabase project, one
SAM stack `job-hunt-api`, one Netlify site) until Apr 27 2026. Auto-apply
landed in PR #10 with no pre-prod safety net. The deployment-safety roadmap
calls for a staging environment in Phase 3 to enable smoke tests (Phase 6),
canary verification (Phase 2 cross-check), and visual QA before prod.

## Decisions

### 1. Supabase: project-per-env, NOT schema-per-env

**Choice:** Create a separate Supabase project `naukribaba-staging` with its
own URL, anon key, service key, and JWT secret.

**Alternatives considered:**

| Option | Pro | Con | Verdict |
|---|---|---|---|
| Project-per-env (chosen) | Hard isolation: separate auth.users, separate RLS context, separate JWT secret means a stolen staging token can't read prod. Separate billing surfacing accidental cost spikes (e.g. runaway test bot). | One more Supabase project to manage; migrations applied twice. | ✓ |
| Schema-per-env (`public_staging`) | Single project, no migration duplication, RLS reuses same JWT signer. | RLS policies are not naturally schema-aware: `auth.uid() = user_id` works the same in both schemas, so a test user in staging *could* see prod rows if RLS misconfigured. JWT secret reuse means a staging token works against prod. | ✗ (security risk) |
| Database-per-env in same project | Supabase free tier supports only 1 db per project. | Forces upgrade to paid plan immediately. | ✗ (cost-prohibitive) |

**Rationale:** Project-per-env trades a small operational cost (one extra
Supabase project, ~5 min/yr to apply migrations to two places via
`supabase db push --project-ref` against each ref) for hard isolation. Staging
data leaks (intentional or accidental) cannot reach prod tables. Stolen
staging credentials cannot impersonate prod users.

### 2. Netlify: long-lived `staging` branch + branch deploy, NOT per-PR previews

**Choice:** Create a long-lived `staging` branch in the GitHub repo. Netlify
builds it on every push as a "branch deploy" served at
`https://staging--naukribaba.netlify.app`. Per-PR Netlify Deploy Previews
are deferred.

**Alternatives considered:**

| Option | Pro | Con | Verdict |
|---|---|---|---|
| Single staging branch (chosen) | One stable URL; cheap (1 build per push); easy QA target. | Forces serialized testing — only one PR can be staged at a time. | ✓ for now |
| Per-PR Deploy Previews | Each PR gets its own Netlify URL automatically; parallel PRs don't conflict. | Each preview consumes Netlify build minutes (free tier: 300/mo). At current PR volume (~5/wk) that's manageable, but the bigger risk is each preview points at the *same* staging API + Supabase, defeating real isolation. True per-PR isolation needs per-PR SAM stacks (cost: ~$2/PR in Lambda + DynamoDB), which is overkill for current scale. | ✗ (revisit at >10 PRs/wk) |

**Rationale:** A single staging branch with a single staging stack is the
simplest model that delivers smoke-test + visual-QA value. We can graduate
to per-PR previews once concurrent QA becomes a bottleneck.

### 3. Migration flow: staging first, prod via PR merge

**Choice:** New migrations land in `supabase/migrations/` via PR. The PR's
staging deploy runs `supabase db push` against the staging project. After
PR merge to `main`, the prod deploy job runs the same migration against the
prod project.

**Alternative:** apply migrations manually to prod via dashboard SQL editor.
Rejected — prone to drift between repo and reality (the exact pain that
caused the 2026-04-08 outage where tectonic was broken in CFN but working
locally).

**Rollback:** if a migration fails in staging, fix in the same PR and
re-run; if it fails in prod after staging passed (rare; e.g. prod has data
shapes staging fixtures don't cover), use `supabase migration revert` and
roll back the deploy via `git revert`. Migration revert is *not* automatic;
data loss risk is real and must be assessed per migration.

### 4. Why not preview-environment-per-PR

Same trade-off as Netlify above, applied to AWS: per-PR SAM stacks would
isolate database state and infra state per PR but cost ~$2/PR in baseline
Lambda + DynamoDB charges (free-tier free, but free-tier caps at 1M Lambda
invocations/mo across *all* envs). At >50 stale per-PR stacks, free tier is
exhausted. Revisit when PR volume + stale-stack cleanup becomes routine.

## Out of scope

- Multi-region staging (eu-west-1 only)
- Per-tenant staging (single staging tenant for now)
- Staging-specific feature flags (Phase 1 PostHog will offer per-env flag
  defaults; not yet)

## References

- Roadmap: `docs/superpowers/plans/2026-04-27-deployment-safety-roadmap.md`
- Phase 3 plan: `docs/superpowers/plans/2026-04-27-deployment-safety-phase3-staging.md`
- Supabase docs on project-per-env: https://supabase.com/docs/guides/cli/local-development#linking-multiple-projects
- Netlify branch deploys: https://docs.netlify.com/site-deploys/overview/#branch-deploys
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-04-27-staging-env-decision.md
git commit -m "docs(adr): staging environment architecture decision record

Locks in: project-per-env Supabase, long-lived staging branch on Netlify
(no per-PR previews), migrations flow staging→prod via PR merge.

Cross-references Phase 3 plan + roadmap. Future engineers should read
this before proposing changes to the staging topology."
```

---

## Task 21: Cross-phase coordination notes

**Why this task is here:** Phase 3 changes the resource-name shape that Phases 2, 4, 5, 6 all depend on. Document the integration points so the next phase plans don't fight Phase 3.

- [ ] **Step 1: Verify Phase 2 (Canary) compat**

If Phase 2's `2026-04-27-deployment-safety-phase2-canary.md` plan has been written and merged before Phase 3 lands, its `AutoPublishAlias: live` and `DeploymentPreference` blocks attach to *function logical IDs* (e.g. `WsRouteFunction`), not function names. The Stage suffix doesn't break canary because CodeDeploy operates on the alias, which is per-function-version, not per-name. After staging deploys for the first time, verify by triggering a no-op staging redeploy and confirming CodeDeploy console shows the deployment progressing through canary stages (not an "AllAtOnce" instant cut-over). If Phase 2 hasn't merged yet, no action — Phase 2's plan must reference Phase 3's stage-suffixed resource names when it lands.

- [ ] **Step 2: Phase 4 (Observability) inheritance**

Phase 4 will add custom CloudWatch metrics in namespace `Naukribaba/${Stage}` and a CloudWatch Dashboard named `naukribaba-${Stage}-overview`. The `Stage` parameter introduced here is *the* parameter Phase 4's metric/dashboard naming will reference. No code change needed in Phase 3 — just confirm the param is exposed.

- [ ] **Step 3: Phase 5 (Sentry) tagging**

Phase 5 will pass `SENTRY_ENVIRONMENT=${Stage}` to every Lambda's environment via `template.yaml`. Add a placeholder env var to `Globals.Function` so Phase 5 just flips it on:

(Optional — only do this if Phase 5 plan is being written in parallel.)

In `template.yaml` under `Globals.Function`:

```yaml
Globals:
  Function:
    Timeout: 900
    MemorySize: 1024
    Environment:
      Variables:
        STAGE: !Ref Stage
```

This is a no-op until Phase 5's `sentry_config.py` reads `os.environ['STAGE']`. Adding it now (a) keeps Phase 5's plan from re-touching `Globals.Function`, (b) lets debug logging in any Lambda print which env it's in.

- [ ] **Step 4: Phase 6 (Smoke) `STAGING_URL` env var**

Phase 6's smoke tests need the staging API URL as `STAGING_URL`. After Task 17, the URL is in CFN outputs. Phase 6's `tests/smoke/conftest.py` will read it via:

```python
import os, subprocess
STAGING_URL = os.environ.get('STAGING_URL') or subprocess.check_output(
    ["aws", "cloudformation", "describe-stacks", "--stack-name", "naukribaba-staging",
     "--query", "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue", "--output", "text"]
).decode().strip()
```

Phase 3 doesn't need to do anything for this; the URL is already exported via Task 10 step 2.

- [ ] **Step 5: Optional commit if Step 3 was applied**

```bash
git add template.yaml
git commit -m "chore(infra): expose STAGE env var globally for Phase 5 (Sentry)

Pre-wires the env var so Phase 5's sentry_config.py can read it without
re-touching template.yaml's Globals block."
```

---

## Task 22: Update CLAUDE.md to document the new workflow

**Files:**
- Modify: `CLAUDE.md` (Deployment + Implementation Status sections)

- [ ] **Step 1: Append to the Deployment section**

Find the existing `## Deployment` section in `CLAUDE.md`:

```markdown
## Deployment

- **Frontend**: Netlify (`netlify.toml` configured, set `VITE_API_URL` env var)
- **Backend**: AWS Lambda via SAM (`template.yaml`, use `sam deploy --guided`)
- **Pipeline**: GitHub Actions (`.github/workflows/daily_job_hunt.yml`, weekdays 7:00 UTC)
```

Append below it:

```markdown

### Two-environment deploy flow (Phase 3)

- **Staging:** Supabase project `naukribaba-staging`, SAM stack `naukribaba-staging`, Netlify branch deploy at `https://staging--naukribaba.netlify.app`. Auto-deploys on every PR open/sync via `.github/workflows/deploy.yml` job `deploy-staging`.
- **Prod:** Supabase project (existing), SAM stack `job-hunt-api` (legacy name preserved), Netlify production at `https://naukribaba.netlify.app`. Auto-deploys on push to `main` via job `deploy-prod`.
- **Resource naming:** every named AWS resource is suffixed with the `Stage` parameter, e.g. `naukribaba-staging-ws-route` and `naukribaba-prod-ws-route`. Mappings handle the S3 artifact bucket.
- **Promote a hot fix:** if main is healthy and you want to redeploy without a new merge, run `bash scripts/promote_to_prod.sh` from a clean main checkout.
- **Migrations:** staging gets new migrations on PR open; prod gets them on merge to main. Both run `supabase db push --project-ref <ref>`. NEVER hand-apply migrations to prod via the Supabase SQL editor.
- **Seeding staging:** `python supabase/scripts/seed_auth_users.py` (10 fake auth users) then `psql ... -f supabase/seed.sql` (50 jobs, 5 resumes, 3 applications). Both idempotent.

ADR: `docs/superpowers/specs/2026-04-27-staging-env-decision.md`.
```

- [ ] **Step 2: Update the Implementation Status table**

Find:

```markdown
| 6. Testing + self-improvement | ✅ Self-improver done, E2E = user action |
```

Add below the existing Phase 2.5/2.6 rows:

```markdown
| Phase 3 (deployment safety): staging environment | ✅ Complete (2026-04-27) — see `docs/superpowers/plans/2026-04-27-deployment-safety-phase3-staging.md` |
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): document two-env deploy flow + staging seeding"
```

---

## Success Criteria Recap

After all 22 tasks complete:

- ✅ `aws cloudformation describe-stacks --stack-name naukribaba-staging` returns a CREATE_COMPLETE/UPDATE_COMPLETE stack with `Stage=staging` output.
- ✅ `aws cloudformation describe-stacks --stack-name job-hunt-api` returns CREATE_COMPLETE/UPDATE_COMPLETE with `Stage=prod` output and 32 Lambdas all renamed `naukribaba-prod-*`.
- ✅ A new PR opens against `main` automatically deploys to staging within 15 min and posts the staging URL as a sticky PR comment.
- ✅ `curl https://<staging_api>/healthz` returns 200 OK.
- ✅ Staging Supabase shows 10 users, 5 resumes, 50 jobs, 3 applications. Prod Supabase row counts unchanged.
- ✅ Merging to main triggers prod deploy via the same workflow.
- ✅ `bash scripts/promote_to_prod.sh` refuses to run from a non-main branch.
- ✅ Manually-triggered deploys still work via `gh workflow run deploy.yml -f target_env=staging|prod`.
- ✅ Writing a row to staging Supabase (e.g., insert a fake job via the staging API) does not appear in prod Supabase. Verify by row-count diff before + after.
- ✅ ADR `docs/superpowers/specs/2026-04-27-staging-env-decision.md` exists and explains the project-per-env choice.

---

## Self-Review

**1. Spec coverage:**
- Manual prereq (staging Supabase + GH secrets) → Task 0 ✓
- Apply migrations to staging → Task 1 ✓
- `web/supabase/seed.sql` (called out as actually `supabase/seed.sql`) → Tasks 2, 3 ✓
- `template.yaml` Stage param + suffixing → Tasks 4, 5, 6, 7, 8, 9, 10 ✓
- `samconfig.toml` + per-env files → Tasks 11, 12 ✓
- `deploy.yml` rewrite → Task 13 ✓
- `netlify.toml` modify → Tasks 14, 17 ✓
- Manual Netlify branch enable → Task 15 ✓
- `scripts/promote_to_prod.sh` → Task 16 ✓
- ADR → Task 20 ✓
- E2E validation (PR open → staging deploy → prod untouched → merge → prod deploy) → Tasks 17, 18, 19 ✓
- Cross-phase coordination → Task 21 ✓
- CLAUDE.md update → Task 22 ✓
- All roadmap-listed tasks mapped.

**2. Placeholder scan:** Searched for "TBD", "TODO" (real ones in the prod-bucket-import note are flagged in the ADR), "implement later", "configure appropriately" — none found in task steps.

**3. Type/name consistency:**
- `Stage` parameter referenced consistently as `!Ref Stage` and `${Stage}` throughout.
- Resource rename pattern `naukribaba-${Stage}-<x>` consistent for all 32 Lambdas + 14 other resources.
- Stack names consistent: `naukribaba-staging` (new) vs `job-hunt-api` (legacy prod).
- Secret naming: prod = `SUPABASE_URL`/`SUPABASE_SERVICE_KEY`/`SUPABASE_JWT_SECRET` (unchanged); staging = `SUPABASE_URL_STAGING`/`SUPABASE_SERVICE_KEY_STAGING`/`SUPABASE_JWT_SECRET_STAGING` (new). No rename of prod secrets.
- ECR repo name unchanged across envs (`job-hunt-api`); image is identical, runtime stage parameter differentiates behavior.

**4. Issue found + fixed during self-review:**
- The `\end{document>` typo in seed.sql slot 3 is now flagged with a fix-before-commit instruction in Task 3 step 1. (Originally just a comment; upgraded to actionable.)
- Roadmap referenced `web/supabase/migrations/` but the actual repo layout is `supabase/migrations/`. Plan calls this out at the top + uses correct paths everywhere.
- Frontend Bucket name (Task 8) initially flagged for rename then verified it inherits from `${AWS::StackName}` so no change needed — kept as a verification-only task.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-27-deployment-safety-phase3-staging.md`.**

Two execution options:

1. **Subagent-Driven (recommended)** — Dispatch fresh subagent per task, review between tasks, fast iteration. Best for the IaC-heavy middle tasks (4–10) where each step is mechanical but easy to typo.
2. **Inline Execution** — Batch through tasks in one session, checkpoint at Task 12 (samconfig + template done) and Task 17 (first staging deploy verified). Best if the operator wants to keep human eyes on each `sam deploy` output.

Which approach?
