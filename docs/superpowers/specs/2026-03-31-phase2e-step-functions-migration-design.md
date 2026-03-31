# Phase 2E: Step Functions Pipeline Migration — Design Specification

**Date**: 2026-03-31
**Status**: Approved
**Replaces**: `2026-03-31-phase2e-n8n-migration-design.md` (moved to archive)
**Depends on**: Phase 2A (complete)
**Unblocks**: Phase 2B (editor), Phase 2D (enrichment), Phase 2G (observability)

---

## 1. Why Step Functions + Lambda

The pipeline needs to be rebuilt from scratch. Current problems:
- **Scrapers are fragile**: HTML parsing breaks when sites change markup
- **Sequential execution**: one slow scraper blocks everything (90-min timeout)
- **Scoring inconsistency**: different code paths give different scores for same JD
- **No retry logic**: transient API failures kill the entire run
- **No shared scraping**: every user re-scrapes the same jobs (multi-tenant blocker)
- **File-based state**: `seen_jobs.json` is fragile, not multi-tenant

Step Functions + Lambda solves all of this:
- **Serverless**: $0/mo base cost, pay per execution
- **Parallel**: scrapers run simultaneously in Parallel state
- **Per-job fan-out**: Map state processes each matched job independently
- **Built-in retry**: configurable per state with exponential backoff
- **Multi-tenant native**: each user's pipeline is just an execution with `user_id` input
- **No server**: no EC2, no Docker, no maintenance
- **AI builds it**: defined as SAM YAML, not drag-and-drop — perfect for Claude Code

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  AWS (Serverless)                                            │
│                                                              │
│  EventBridge Scheduler ──► Step Functions State Machine       │
│  API Gateway ────────────► (same state machine)              │
│                                                              │
│  ┌──────────────────────────────────────────────────┐       │
│  │  Step Functions: daily-pipeline                   │       │
│  │                                                    │       │
│  │  load_config → check_cache → Parallel(scrapers)   │       │
│  │  → merge_dedup → score_batch → Map(tailor_job)    │       │
│  │  → email_summary → self_improve                   │       │
│  └──────────────────────────────────────────────────┘       │
│                                                              │
│  ┌──────────────────────────────────────────────────┐       │
│  │  Step Functions: single-job-pipeline              │       │
│  │                                                    │       │
│  │  save_raw → score → tailor → compile → cover      │       │
│  │  → contacts → save → return_result                │       │
│  └──────────────────────────────────────────────────┘       │
│                                                              │
│  Lambda Functions (compute):                                 │
│    scrape_adzuna, scrape_hn, scrape_yc,                     │
│    scrape_apify_linkedin, scrape_apify_indeed, ...          │
│    score_batch, tailor_resume, compile_latex,                │
│    generate_cover_letter, find_contacts,                     │
│    self_improve, send_email                                  │
│                                                              │
│  Supabase (data):     S3 (files):      Apify (scraping):   │
│    jobs_raw (shared)    Resume PDFs      LinkedIn scraper    │
│    jobs (per-user)      Cover letters    Indeed scraper      │
│    users                                 Web scraper         │
│    self_improvement                                          │
│    pipeline_metrics                                          │
└─────────────────────────────────────────────────────────────┘

React Frontend (Netlify):
  Dashboard → POST /api/pipeline/run → triggers daily-pipeline
  Add Job → POST /api/pipeline/run-single → triggers single-job-pipeline
  Status → GET /api/pipeline/status → reads pipeline_metrics
```

### Two State Machines

**1. `daily-pipeline`** — full automated run (scheduled + manual trigger)
**2. `single-job-pipeline`** — user pastes a JD (Add Job flow)

Both share the same Lambda functions for scoring, tailoring, compiling, contacts. The only difference is the entry point: daily-pipeline starts with scraping, single-job starts with a user-provided JD.

### Key Principle: Scrape Once, Score Per-User

```
jobs_raw (SHARED — no user_id)          jobs (PER-USER — has user_id)
┌──────────────────────────┐           ┌──────────────────────────┐
│ job_hash (PK)            │           │ job_id (PK)              │
│ title                    │  Score    │ user_id (FK)             │
│ company                  │  against  │ job_hash (FK → jobs_raw) │
│ description              │──each──►  │ match_score              │
│ location                 │  user's   │ ats_score                │
│ apply_url                │  resume   │ hiring_manager_score     │
│ source                   │           │ tech_recruiter_score     │
│ scraped_at               │           │ matched_resume           │
│ experience_level         │           │ tailored_pdf_path        │
│ job_type                 │           │ resume_s3_url            │
│                          │           │ cover_letter_s3_url      │
│ Cache: skip if < 24h     │           │ linkedin_contacts        │
└──────────────────────────┘           │ application_status       │
                                       │ tailoring_model          │
  source="manual" entries              │ first_seen               │
  go here too                          └──────────────────────────┘
```

---

## 3. Scraping Strategy

### Sources (API or Apify only — no HTTP scraping)

| Source | Method | Lambda | Cost |
|--------|--------|--------|------|
| LinkedIn | Apify LinkedIn Jobs Scraper (managed Playwright) | `scrape_apify_linkedin` | ~$0.50/1K |
| Indeed | Apify Indeed Scraper (managed Playwright) | `scrape_apify_indeed` | ~$0.50/1K |
| Glassdoor | Apify Glassdoor Scraper | `scrape_apify_glassdoor` | ~$0.50/1K |
| Adzuna | REST API (API key) | `scrape_adzuna` | Free |
| HN Hiring | Algolia API | `scrape_hn` | Free |
| YC Jobs | WorkAtAStartup JSON API | `scrape_yc` | Free |
| GradIreland | Apify Web Scraper | `scrape_apify_gradireland` | ~$0.25/1K |
| IrishJobs | Apify Web Scraper | `scrape_apify_irishjobs` | ~$0.25/1K |
| Jobs.ie | Apify Web Scraper | `scrape_apify_jobsie` | ~$0.25/1K |

### Each Scraper Lambda

Every scraper Lambda follows the same contract:

```
Input:  { queries: [...], locations: [...], experience_levels: [...], job_types: [...] }
Output: { jobs: [{ title, company, description, location, apply_url, source, experience_level, job_type }] }
```

Apify scrapers: Lambda calls `ApifyClient.actor(actor_id).call(run_input)`, waits for results, normalizes to the output schema.

API scrapers: Lambda calls the REST API directly, normalizes response.

### Apify Budget Control

- Track Apify spending per run in `pipeline_metrics` table
- Each scraper Lambda checks `APIFY_MONTHLY_BUDGET` env var (default: $5)
- If monthly spend > budget, skip Apify scrapers and log warning
- Pipeline continues with API-only sources (Adzuna, HN, YC)
- Email summary includes budget warning

---

## 4. Caching & Rate Limiting

### Scraper Cache (`jobs_raw`)
- Before running scrapers, `check_cache` Lambda queries `jobs_raw` by source
- If `source=linkedin` has `scraped_at > NOW() - 24h`, skip LinkedIn scraper
- For multi-tenant: User 2 in Dublin benefits from User 1's scrape earlier that day
- Cache TTL configurable per source (LinkedIn: 24h, HN: 12h, Adzuna: 24h)

### AI Response Cache
- Migrate from SQLite (`output/.ai_cache.db`) to Supabase `ai_cache` table
- Key: hash of (prompt + system + model)
- TTL: 72h
- Multi-tenant safe: cache entries are prompt-based, not user-based
- Same JD scored twice = cache hit (solves scoring inconsistency too)

### Rate Limiting
| Resource | Limit | Implementation |
|----------|-------|----------------|
| Apify monthly spend | $5 default, configurable | Track in `pipeline_metrics`, check before scraping |
| Lambda concurrency | 10 reserved per scraper | SAM `ReservedConcurrentExecutions` |
| Step Functions | 1 execution per user at a time | Check before starting, reject if already running |
| AI providers | Existing council failover handles this | Groq → Qwen → OpenRouter → Claude chain |
| Manual triggers | 5 per user per day | Counter in Supabase, check in API |

---

## 5. State Machine Definitions

### 5.1 Daily Pipeline State Machine

```json
{
  "StartAt": "LoadUserConfig",
  "States": {
    "LoadUserConfig": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:...:load_user_config",
      "Next": "CheckScraperCache"
    },
    "CheckScraperCache": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:...:check_scraper_cache",
      "Next": "RunScrapers"
    },
    "RunScrapers": {
      "Type": "Parallel",
      "Branches": [
        { "StartAt": "ScrapeAdzuna", "States": { "ScrapeAdzuna": { "Type": "Task", "Resource": "...:scrape_adzuna", "End": true, "Retry": [{"ErrorEquals": ["States.ALL"], "MaxAttempts": 2, "BackoffRate": 2}], "Catch": [{"ErrorEquals": ["States.ALL"], "Next": "ScrapeAdzunaFailed"}] }, "ScrapeAdzunaFailed": { "Type": "Pass", "Result": {"jobs": [], "error": "scraper failed"}, "End": true } } },
        { "StartAt": "ScrapeLinkedIn", "States": { "ScrapeLinkedIn": { "Type": "Task", "Resource": "...:scrape_apify_linkedin", "End": true, "Retry": [{"ErrorEquals": ["States.ALL"], "MaxAttempts": 2}], "Catch": [{"ErrorEquals": ["States.ALL"], "Next": "ScrapeLinkedInFailed"}] }, "ScrapeLinkedInFailed": { "Type": "Pass", "Result": {"jobs": [], "error": "scraper failed"}, "End": true } } },
        { "StartAt": "ScrapeIndeed", "States": { "ScrapeIndeed": { "Type": "Task", "Resource": "...:scrape_apify_indeed", "End": true, "Retry": [{"ErrorEquals": ["States.ALL"], "MaxAttempts": 2}], "Catch": [{"ErrorEquals": ["States.ALL"], "Next": "ScrapeIndeedFailed"}] }, "ScrapeIndeedFailed": { "Type": "Pass", "Result": {"jobs": [], "error": "scraper failed"}, "End": true } } },
        { "StartAt": "ScrapeHN", "States": { "ScrapeHN": { "Type": "Task", "Resource": "...:scrape_hn", "End": true, "Retry": [{"ErrorEquals": ["States.ALL"], "MaxAttempts": 2}], "Catch": [{"ErrorEquals": ["States.ALL"], "Next": "ScrapeHNFailed"}] }, "ScrapeHNFailed": { "Type": "Pass", "Result": {"jobs": [], "error": "scraper failed"}, "End": true } } },
        { "StartAt": "ScrapeYC", "States": { "ScrapeYC": { "Type": "Task", "Resource": "...:scrape_yc", "End": true, "Retry": [{"ErrorEquals": ["States.ALL"], "MaxAttempts": 2}], "Catch": [{"ErrorEquals": ["States.ALL"], "Next": "ScrapeYCFailed"}] }, "ScrapeYCFailed": { "Type": "Pass", "Result": {"jobs": [], "error": "scraper failed"}, "End": true } } }
      ],
      "Next": "MergeAndDedup"
    },
    "MergeAndDedup": {
      "Type": "Task",
      "Resource": "...:merge_and_dedup",
      "Next": "ScoreBatch"
    },
    "ScoreBatch": {
      "Type": "Task",
      "Resource": "...:score_batch",
      "Next": "FilterMatched"
    },
    "FilterMatched": {
      "Type": "Task",
      "Resource": "...:filter_matched",
      "Next": "ProcessMatchedJobs"
    },
    "ProcessMatchedJobs": {
      "Type": "Map",
      "ItemsPath": "$.matched_jobs",
      "MaxConcurrency": 3,
      "Iterator": {
        "StartAt": "TailorResume",
        "States": {
          "TailorResume": { "Type": "Task", "Resource": "...:tailor_resume", "Next": "CompileResume" },
          "CompileResume": { "Type": "Task", "Resource": "...:compile_latex", "Next": "GenerateCoverLetter" },
          "GenerateCoverLetter": { "Type": "Task", "Resource": "...:generate_cover_letter", "Next": "CompileCoverLetter" },
          "CompileCoverLetter": { "Type": "Task", "Resource": "...:compile_latex", "Next": "UploadArtifacts" },
          "UploadArtifacts": { "Type": "Task", "Resource": "...:upload_s3", "Next": "FindContacts" },
          "FindContacts": { "Type": "Task", "Resource": "...:find_contacts", "Next": "SaveJob" },
          "SaveJob": { "Type": "Task", "Resource": "...:save_job_to_supabase", "End": true }
        }
      },
      "Next": "SendEmailSummary"
    },
    "SendEmailSummary": {
      "Type": "Task",
      "Resource": "...:send_email_summary",
      "Next": "SelfImprove"
    },
    "SelfImprove": {
      "Type": "Task",
      "Resource": "...:self_improve",
      "End": true
    }
  }
}
```

### 5.2 Single-Job Pipeline State Machine (Add Job)

```json
{
  "StartAt": "SaveToJobsRaw",
  "States": {
    "SaveToJobsRaw": {
      "Type": "Task",
      "Resource": "...:save_raw_job",
      "Next": "ScoreSingleJob"
    },
    "ScoreSingleJob": {
      "Type": "Task",
      "Resource": "...:score_batch",
      "Comment": "Same Lambda, just one job — consistent scoring",
      "Next": "CheckScoreThreshold"
    },
    "CheckScoreThreshold": {
      "Type": "Choice",
      "Choices": [
        {
          "Variable": "$.match_score",
          "NumericGreaterThanEquals": 85,
          "Next": "TailorLightTouch"
        }
      ],
      "Default": "TailorFullRewrite"
    },
    "TailorLightTouch": {
      "Type": "Task",
      "Resource": "...:tailor_resume",
      "Parameters": { "light_touch": true },
      "Next": "CompileResume"
    },
    "TailorFullRewrite": {
      "Type": "Task",
      "Resource": "...:tailor_resume",
      "Parameters": { "light_touch": false },
      "Next": "CompileResume"
    },
    "CompileResume": { "Type": "Task", "Resource": "...:compile_latex", "Next": "GenerateCoverLetter" },
    "GenerateCoverLetter": { "Type": "Task", "Resource": "...:generate_cover_letter", "Next": "CompileCoverLetter" },
    "CompileCoverLetter": { "Type": "Task", "Resource": "...:compile_latex", "Next": "UploadArtifacts" },
    "UploadArtifacts": { "Type": "Task", "Resource": "...:upload_s3", "Next": "FindContacts" },
    "FindContacts": { "Type": "Task", "Resource": "...:find_contacts", "Next": "SaveJob" },
    "SaveJob": { "Type": "Task", "Resource": "...:save_job_to_supabase", "End": true }
  }
}
```

Note the **score-first branching**: `CheckScoreThreshold` routes to light-touch or full rewrite based on base score. This is built into the state machine, not buried in Python code.

---

## 6. New Supabase Tables

```sql
-- Shared raw job data (scraped once, used by all users)
CREATE TABLE jobs_raw (
  job_hash TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  company TEXT NOT NULL,
  description TEXT,
  location TEXT,
  apply_url TEXT,
  source TEXT NOT NULL,
  experience_level TEXT,
  job_type TEXT,
  scraped_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_jobs_raw_source_scraped ON jobs_raw(source, scraped_at);

-- Modify existing jobs table to reference jobs_raw
-- (add job_hash FK, keep existing columns for backward compat)
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS job_hash TEXT REFERENCES jobs_raw(job_hash);

-- Self-improvement configuration (per-user)
CREATE TABLE self_improvement_config (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id) NOT NULL,
  config_type TEXT NOT NULL,
  config_data JSONB NOT NULL,
  applied_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, config_type)
);

-- Pipeline run metrics (per-user, per-scraper)
CREATE TABLE pipeline_metrics (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id) NOT NULL,
  run_date DATE NOT NULL,
  scraper_name TEXT NOT NULL,
  jobs_found INT DEFAULT 0,
  jobs_matched INT DEFAULT 0,
  jobs_tailored INT DEFAULT 0,
  duration_seconds INT,
  apify_cost_cents INT DEFAULT 0,
  error_message TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- AI response cache (migrated from SQLite, shared across users)
CREATE TABLE ai_cache (
  cache_key TEXT PRIMARY KEY,
  response TEXT NOT NULL,
  provider TEXT,
  model TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_ai_cache_expires ON ai_cache(expires_at);
```

---

## 7. API Endpoints (new/modified)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /api/pipeline/run` | New | Trigger daily pipeline for authenticated user |
| `POST /api/pipeline/run-single` | New | Trigger single-job pipeline (Add Job) |
| `GET /api/pipeline/status` | New | Latest run status, scraper health, cost |
| `POST /api/score-batch` | New | Score jobs (used by both pipelines) |
| `POST /api/compile-latex` | New | Compile LaTeX → PDF |
| `POST /api/tailor` | Modified | Refactored to call score-batch internally |
| `PATCH /api/dashboard/jobs/{id}` | Existing | Extended for location, apply_url editing |

### Manual Trigger Endpoint

```python
@app.post("/api/pipeline/run")
def trigger_pipeline(user: AuthUser = Depends(get_current_user)):
    """Trigger the daily pipeline for the authenticated user."""
    # Check if already running
    # Check daily limit (5 manual triggers per day)
    # Start Step Functions execution with user's config
    import boto3
    sfn = boto3.client("stepfunctions")
    response = sfn.start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        input=json.dumps({"user_id": user.id}),
    )
    return {"execution_id": response["executionArn"].split(":")[-1], "status": "started"}
```

### Add Job (Single-Job Pipeline)

```python
@app.post("/api/pipeline/run-single")
def trigger_single_job(body: dict, user: AuthUser = Depends(get_current_user)):
    """Trigger the single-job pipeline for a user-submitted JD."""
    sfn = boto3.client("stepfunctions")
    response = sfn.start_execution(
        stateMachineArn=SINGLE_JOB_STATE_MACHINE_ARN,
        input=json.dumps({
            "user_id": user.id,
            "title": body.get("title", ""),
            "company": body.get("company", ""),
            "description": body["description"],
            "location": body.get("location", ""),
            "apply_url": body.get("apply_url", ""),
            "source": "manual",
        }),
    )
    return {
        "execution_id": response["executionArn"].split(":")[-1],
        "poll_url": f"/api/pipeline/status/{response['executionArn'].split(':')[-1]}",
    }
```

---

## 8. Email Workflows

### Daily Summary
Lambda `send_email_summary` runs at end of daily pipeline. Uses existing `email_notifier.py` logic (Gmail SMTP) or SES. Sends HTML table of matched jobs with scores, links, and artifacts.

### Stale Job Nudges (weekly)
EventBridge rule: every Monday 9:00 UTC → Lambda `send_stale_nudges`
- Query Supabase: `application_status = 'New' AND first_seen < 7 days ago`
- Send Gmail digest if count > 0

### Follow-Up Reminders (daily)
EventBridge rule: daily 10:00 UTC → Lambda `send_followup_reminders`
- Query Supabase: `application_status = 'Applied' AND last update > 7 days ago`
- Send Gmail reminder with job list and contacts

---

## 9. Frontend Changes

### Pipeline Status Bar
Replace hardcoded "runs daily at 7:00 UTC" with live status from `GET /api/pipeline/status`:
- Last run date, jobs found/matched/tailored
- Scraper health badges (green/red per source)
- Apify budget usage

### Run Pipeline Button
Dashboard header: "Run Pipeline" button → `POST /api/pipeline/run` → polls status

### Add Job Flow (refactored)
Current: synchronous `POST /api/tailor` → wait 30-60s → show result
New: `POST /api/pipeline/run-single` → get `poll_url` → poll every 2s → show result
Same UX, but uses the same Step Functions pipeline as automated runs.

### Additional Frontend Polish
- Skills tags (extract from JD, show as chips)
- Job card grid view toggle
- Inline PDF preview (iframe)
- Actual AI model name display
- Manual job editing (location, apply_url)

---

## 10. Cost Estimate

| Component | Monthly Cost (1 user) | Monthly Cost (100 users) |
|-----------|----------------------|--------------------------|
| Step Functions | ~$0.05 | ~$5 |
| Lambda compute | ~$1 | ~$10-20 |
| Apify (shared cache) | ~$5-10 | ~$10-25 |
| S3 storage | ~$0.50 | ~$5 |
| EventBridge | ~$0.01 | ~$0.10 |
| **Total** | **~$7-12/mo** | **~$30-55/mo** |

Compare: n8n on EC2 = $20/mo for 1 user, $20/user for multi-tenant.

---

## 11. Security

- All Lambda functions use IAM roles (no hardcoded credentials)
- Apify API key in SSM Parameter Store (not env vars)
- Step Functions execution input contains user_id (validated by Lambda)
- Rate limiting: 5 manual triggers/day/user, 1 concurrent execution/user
- Supabase RLS on all per-user tables
- S3 paths scoped by user_id: `users/{user_id}/...`

---

## 12. Migration Plan

### Phase 1: Foundation (Days 1-2)
- Create Supabase tables (jobs_raw, ai_cache, self_improvement_config, pipeline_metrics)
- Write all scraper Lambda functions
- Define both Step Functions state machines in SAM template.yaml
- `sam deploy`

### Phase 2: Integration (Days 3-4)
- Wire frontend: pipeline status, Run Pipeline button, Add Job refactor
- Test each scraper individually
- Test full pipeline end-to-end
- Frontend polish (skills tags, card view, PDF preview)

### Phase 3: Shadow Mode (Days 5-6)
- Run Step Functions pipeline alongside GitHub Actions
- Compare results: same jobs? Same scores?
- Fix discrepancies

### Phase 4: Cutover (Day 7)
- Disable GitHub Actions cron
- Activate EventBridge scheduler
- Set up stale nudge + follow-up reminder schedules
- Monitor first 3 automated runs

---

## 13. Multi-Tenant Scaling (architectural decisions made now)

| Decision | Why |
|----------|-----|
| `jobs_raw` shared table | Scrape once, score per-user. 100 users = 1x scrape cost |
| `ai_cache` in Supabase | Shared cache, same JD scored twice = cache hit |
| Step Functions per-user execution | Just pass `user_id`, no per-user infrastructure |
| Apify budget tracking | Prevent runaway costs as users scale |
| User search config in Supabase | Each user customizes queries, locations, experience levels |
| S3 paths by user_id | Clean isolation, no cross-user data leaks |
| EventBridge per-user schedules | Each user can set their preferred pipeline time |

### Multi-Domain (engineering, marketing, design, etc.)
Pipeline is domain-agnostic. Users set their own queries and upload domain-specific resumes. AI council scores based on actual resume content — handles any domain naturally.

### Internships
Just another `experience_level` filter. Users set `["internship", "entry_level"]` in their search config. Passed to Apify/API scraper parameters.

---

## 14. How Future Phases Fit In

| Phase | How it integrates with Step Functions |
|-------|--------------------------------------|
| **2B Editor** | `/api/compile-latex` already in this spec. Add CloudWatch warmer for fast compilation. Frontend-only — no pipeline changes. |
| **2C PDF-to-LaTeX** | New Lambda endpoint. Feeds into editor. No pipeline changes. |
| **2D Company Intel** | Add `enrich_company` Lambda step between scoring and tailoring in the daily pipeline. OR: separate on-demand Step Functions triggered when user views Research tab. Enrichment data cached in `company_intel` Supabase table. |
| **2F Interview Prep** | Pure Lambda + frontend. No pipeline dependency. |
| **2G Analytics** | `pipeline_metrics` table (this spec) feeds the dashboard. CloudWatch alarms replace n8n alerting. EventBridge + SNS for error notifications. |

The Step Functions architecture is **extensible by adding Lambda functions and states** — no infrastructure changes needed for any future phase.

---

## 15. Rollback Plan

GitHub Actions workflow stays in the repo (cron commented out, `workflow_dispatch` active). If Step Functions fails:
1. Re-enable GitHub Actions cron
2. Pipeline runs on GH Actions as before
3. Debug Step Functions without time pressure

---

## 15. Success Criteria

- [ ] Pipeline runs daily without failure
- [ ] Parallel scraping completes in < 15 minutes
- [ ] LinkedIn + Indeed jobs appear via Apify
- [ ] Manual "Add Job" uses same scoring as automated pipeline
- [ ] Score-first tailoring: light tweaks when base >= 85
- [ ] Email summary sent after each run
- [ ] Stale nudges sent weekly
- [ ] "Run Pipeline" button works from dashboard
- [ ] Scraper cache prevents redundant Apify calls
- [ ] Apify budget tracking prevents overspend
- [ ] Self-improvement adjustments applied to next run
