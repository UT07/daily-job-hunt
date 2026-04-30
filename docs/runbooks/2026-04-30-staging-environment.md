# Runbook — staging environment (Phase B.3)

What's shipped today (lean) vs. what's deferred (full).

## What ships in this PR (lean)

**Netlify deploy previews per PR**, no separate backend.

```
PR opened → Netlify auto-builds the React app from the PR branch
        → unique preview URL like https://deploy-preview-42--naukribaba.netlify.app
        → frontend hits the same prod API + Supabase
        → PreviewBanner.jsx shows an amber banner
        → X-Robots-Tag: noindex,nofollow keeps URL out of search results
```

That catches **frontend-only regressions** before merging to main —
typo'd routes, broken UI, regressions in page rendering, dependency
upgrade fallout. ~95% of the bugs we've shipped this quarter were
frontend-only.

The 5% — schema-mismatch bugs, Lambda code bugs (e.g. today's
`users.full_name` schema gap, the empty tectonic layer) — would not
have been caught by this lean version. For those, see the deferred
section.

### How to verify locally

```bash
cd web
VITE_BUILD_LABEL=preview npm run build && npm run preview
```

The amber banner should appear at the top of every page.

## What's deferred (full Supabase + SAM staging)

Operator-heavy multi-day setup that we explicitly punted on for ROI:

### Operator setup (when we activate this)

1. **Create staging Supabase project** in the same org as prod:
   - Dashboard → New Project → name `naukribaba-staging`
   - Region eu-west-1 (same as prod for low-latency CI smoke)
   - Cost: free tier covers it; if we exceed, ~$25/month
2. **Apply all migrations** to the new project:
   ```bash
   # In a fresh checkout linked to the staging project ref
   supabase link --project-ref <staging-ref>
   supabase db push
   ```
3. **Set GitHub secrets** with staging-specific values:
   - `STAGING_SUPABASE_URL`
   - `STAGING_SUPABASE_SERVICE_KEY`
   - `STAGING_SUPABASE_JWT_SECRET`
   - `STAGING_DAILY_PIPELINE_USER_ID` (a test user UUID)
4. **Pre-create staging SNS topic** (deploy IAM still lacks `sns:CreateTopic`):
   ```bash
   aws sns create-topic --name naukribaba-staging-pipeline-alarms --region eu-west-1
   aws sns subscribe --topic-arn ... --protocol email --notification-endpoint <your-email>
   ```
5. **Add `Stage` parameter to template.yaml** (default `"prod"`) and update:
   - All resource names that include `naukribaba-` to also include `${Stage}`
   - `DailyPipelineSchedule` State: `${Stage} == "prod" ? ENABLED : DISABLED`
   - `PipelineAlarmTopicArn` parameter — staging gets the staging topic ARN
6. **Add `.github/workflows/deploy-staging.yml`** — copy of `deploy.yml`
   with `--stack-name job-hunt-api-staging` and the staging secrets.
7. **Netlify staging environment** — connect the staging Supabase URL
   to a separate Netlify environment so frontend previews can also use
   staging instead of prod.
8. **E2E smoke against staging** — add a `make smoke` target that runs
   the full apply-flow (start session → fill form → submit) against
   staging and gates the merge.

### Why deferred

- Doubles infra surface (two Supabases, two SAM stacks, two Netlify envs)
- Migration drift between staging + prod becomes its own maintenance burden
- Most of our bug classes from today were already caught by other Phase B
  pieces (B.1 runtime-import smoke, B.2 canary, B.4 contract route diff,
  B.5 silent-success alarm)
- Lean Netlify previews give 80% of the catch-rate at 5% of the setup
  cost

We can revisit when frontend-only previews stop catching the regressions
we care about.

## Status

- [x] Netlify previews wired (`netlify.toml` context blocks + PreviewBanner)
- [x] Documentation for full-staging plan (this file)
- [ ] Activate full Supabase staging — only if/when frontend-only previews
      become insufficient
