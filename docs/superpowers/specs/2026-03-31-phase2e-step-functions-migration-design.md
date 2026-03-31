# Phase 2E: Step Functions Pipeline Migration — Design Specification

**Date**: 2026-03-31 (v5 — final review, all issues resolved)
**Status**: Approved
**Replaces**: `2026-03-31-phase2e-n8n-migration-design.md` (archived)
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
- **AI builds it**: defined as SAM YAML — perfect for Claude Code

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  AWS (Serverless)                                                │
│                                                                  │
│  EventBridge Scheduler ──► Step Functions: daily-pipeline         │
│  API Gateway ────────────► Step Functions: daily-pipeline (manual)│
│  API Gateway ────────────► Step Functions: single-job-pipeline    │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  DATA BUS: Supabase                                       │   │
│  │  (Step Functions passes only IDs/hashes between states,   │   │
│  │   Lambdas read/write full data from Supabase)             │   │
│  │                                                            │   │
│  │  jobs_raw ──► jobs ──► pipeline_metrics                    │   │
│  │  (shared)    (per-user)  (per-run)                         │   │
│  │  ai_cache    self_improvement_config                       │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  Lambda Functions (compute layer):                               │
│    Scrapers: scrape_adzuna, scrape_hn, scrape_yc,               │
│              scrape_apify (generic, actor_id as param)           │
│    Pipeline: score_batch, tailor_resume, compile_latex,          │
│              generate_cover_letter, find_contacts                │
│    Support:  load_config, check_cache, merge_dedup,             │
│              filter_matched, save_job, upload_s3,                │
│              send_email, self_improve, notify_error              │
│                                                                  │
│  Lambda Layer: shared deps (apify-client, supabase, boto3)      │
│                                                                  │
│  S3: resume PDFs, cover letter PDFs                              │
│  Apify: managed Playwright for LinkedIn/Indeed/Glassdoor/etc     │
│  SSM Parameter Store: API keys (Apify, Groq, etc.)              │
└─────────────────────────────────────────────────────────────────┘

React Frontend (Netlify):
  Dashboard  → POST /api/pipeline/run           → triggers daily-pipeline
  Add Job    → POST /api/pipeline/run-single    → triggers single-job-pipeline
  Poll       → GET  /api/pipeline/status/:execId → Step Functions describeExecution
  Dashboard  → GET  /api/pipeline/status         → latest run from pipeline_metrics
```

### Two State Machines

| State Machine | Type | Trigger | Purpose |
|--------------|------|---------|---------|
| `daily-pipeline` | Standard | EventBridge cron (weekdays 7:00 UTC) + API Gateway (manual) | Full automated run: scrape → score → tailor → email |
| `single-job-pipeline` | Standard | API Gateway (Add Job form) | User-submitted JD: save → score → tailor → return |

Both share the same Lambda functions. Standard type for both (Express doesn't support Map state well for long iterations).

### Key Principle 1: Supabase Is the Data Bus

**Step Functions has a 256KB payload limit per state transition.** With 9 scrapers returning 50+ jobs with descriptions, full payloads would overflow immediately.

Solution: **Lambdas read from and write to Supabase. Step Functions only passes references (IDs, hashes, counts).**

```
Scraper Lambda:
  reads: search config from execution input
  writes: jobs to jobs_raw table
  returns: { count: 50, source: "linkedin" }     ← tiny payload

MergeDedup Lambda:
  reads: jobs_raw (today's scrape)
  writes: nothing (dedup is a filter)
  returns: { new_job_hashes: ["abc", "def"] }     ← array of strings

ScoreBatch Lambda:
  reads: jobs_raw by hashes, user's resumes from Supabase
  writes: scored jobs to jobs table
  returns: { matched_hashes: ["abc"] }             ← filtered array

Map iterator (per job):
  receives: { job_hash: "abc", user_id: "..." }    ← single hash
  each Lambda reads full job data from Supabase
```

**No state transition ever exceeds a few KB.**

### Key Principle 2: Scrape Once, Score Per-User

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
│ experience_level         │           │ resume_s3_url            │
│ job_type                 │           │ cover_letter_s3_url      │
│ query_hash               │           │ linkedin_contacts        │
│                          │           │ application_status       │
│ Cache key:               │           │ tailoring_model          │
│ source + query_hash      │           │ first_seen               │
└──────────────────────────┘           └──────────────────────────┘

Manual jobs (source="manual") go into jobs_raw too — same flow from scoring onward.
```

---

## 3. Scraping Strategy

### Sources (API or Apify only — no HTTP scraping)

| Source | Method | Apify Actor ID | Cost |
|--------|--------|---------------|------|
| LinkedIn | Apify LinkedIn Jobs Scraper | `hMvNSpz3JnHgl5jkh` | ~$0.50/1K |
| Indeed | Apify Indeed Scraper | `misceres/indeed-scraper` | ~$0.50/1K |
| Glassdoor | Apify Glassdoor Scraper | `bebity/glassdoor-scraper` | ~$0.50/1K |
| Adzuna | REST API (API key) | N/A | Free |
| HN Hiring | Algolia API | N/A | Free |
| YC Jobs | WorkAtAStartup JSON API | N/A | Free |
| GradIreland | Apify Web Scraper | `apify/web-scraper` | ~$0.25/1K |
| IrishJobs | Apify Web Scraper | `apify/web-scraper` | ~$0.25/1K |
| Jobs.ie | Apify Web Scraper | `apify/web-scraper` | ~$0.25/1K |

### Generic Apify Scraper Lambda

Instead of 9 separate scraper Lambdas, use ONE `scrape_apify` Lambda that takes `actor_id` and `run_input` as parameters. This reduces code duplication:

```python
def handler(event, context):
    actor_id = event["actor_id"]       # e.g., "hMvNSpz3JnHgl5jkh"
    run_input = event["run_input"]     # search queries, location filters
    source_name = event["source"]      # e.g., "linkedin"
    normalizer = event["normalizer"]   # e.g., "linkedin" → picks normalization logic

    client = ApifyClient(ssm.get("APIFY_API_KEY"))
    run = client.actor(actor_id).call(run_input=run_input, timeout_secs=300)
    items = client.dataset(run["defaultDatasetId"]).list_items().items

    # Normalize to standard schema, write to jobs_raw
    jobs = normalize(items, source_name, normalizer)
    write_to_jobs_raw(jobs)
    return {"count": len(jobs), "source": source_name}
```

API scrapers (Adzuna, HN, YC) are separate Lambdas since they don't use Apify.

### Apify Web Scraper Config for Irish Sites

GradIreland, IrishJobs, Jobs.ie use the generic `apify/web-scraper` actor with custom page functions:

```javascript
// Page function for IrishJobs (passed as run_input.pageFunction)
async function pageFunction(context) {
    const { request, jQuery: $ } = context;
    const jobs = [];
    $('.job-listing').each((i, el) => {
        jobs.push({
            title: $(el).find('.job-title').text().trim(),
            company: $(el).find('.company-name').text().trim(),
            location: $(el).find('.location').text().trim(),
            description: $(el).find('.job-description').text().trim(),
            apply_url: $(el).find('a.job-title').attr('href'),
        });
    });
    return jobs;
}
```

Each site has a config stored in SSM Parameter Store: `start_urls`, `page_function`, `selectors`. When a site changes their HTML, update the SSM config — no Lambda redeployment needed.

### Lambda Timeouts

| Lambda | Timeout | Why |
|--------|---------|-----|
| `scrape_apify` | 300s (5 min) | Apify actor runs take 2-5 min |
| `scrape_adzuna` | 30s | Simple API call |
| `scrape_hn` | 60s | API call + comment parsing |
| `scrape_yc` | 30s | Simple JSON API |
| `score_batch` | 300s | AI council with 5-job batches (50 jobs = 10 batches × 30s) |
| `tailor_resume` | 120s | AI council generation |
| `compile_latex` | 60s | tectonic compilation |
| `generate_cover_letter` | 120s | AI council generation |
| `find_contacts` | 120s | Apify Google search |
| All others | 30s | Simple DB operations |

### Scraper Cache

Cache key: **`source + query_hash`** (not just source).

```python
query_hash = hashlib.md5(f"{query}|{location}|{experience_level}".encode()).hexdigest()[:12]

# Check cache before scraping
cached = supabase.table("jobs_raw") \
    .select("count", count="exact") \
    .eq("source", source) \
    .eq("query_hash", query_hash) \
    .gte("scraped_at", now - timedelta(hours=cache_ttl)) \
    .execute()

if cached.count > 0:
    return {"count": cached.count, "source": source, "cached": True}
```

Cache TTLs per source:
| Source | TTL | Why |
|--------|-----|-----|
| LinkedIn | 24h | Job posts stay up for weeks |
| Indeed | 24h | Similar to LinkedIn |
| Adzuna | 24h | API results stable within a day |
| HN Hiring | 168h (1 week) | Monthly thread, posts don't change |
| YC | 48h | Startups update infrequently |
| Irish sites | 24h | Daily refresh sufficient |

### Apify Budget Control

```python
# In each Apify scraper Lambda
monthly_spent = supabase.table("pipeline_metrics") \
    .select("apify_cost_cents") \
    .gte("created_at", first_of_month) \
    .execute()

total_cents = sum(r["apify_cost_cents"] for r in monthly_spent.data)
budget_cents = int(os.environ.get("APIFY_MONTHLY_BUDGET_CENTS", "500"))  # $5 default

if total_cents >= budget_cents:
    return {"count": 0, "source": source, "skipped": "budget_exceeded"}
```

---

## 4. State Machine Definitions

### 4.1 Daily Pipeline (Standard)

```
LoadUserConfig
    │ reads: search config + self-improvement adjustments from Supabase
    │ returns: { user_id, queries, locations, sources, min_score, ... }
    ▼
RunScrapers (Parallel)
    │ Each branch: check own cache → scrape if needed → write to jobs_raw → return {count, source}
    │ Cache check is INSIDE each scraper Lambda (not a separate state)
    │ Retry: 2 attempts, exponential backoff
    │ Catch: return {count: 0, error: "..."} (pipeline continues)
    ▼
MergeAndDedup
    │ reads: today's jobs_raw entries
    │ cross-source dedup: keeps richest version (longest description)
    │ dedup against ALL existing jobs_raw (not just today's)
    │ returns: { new_job_hashes: ["abc", ...], total_new: 25 }
    ▼
ScoreBatch
    │ reads: jobs_raw by hashes + user's resumes from Supabase
    │ scores using AI council (SAME code path as Add Job)
    │ filters by min_score (from config, default 60)
    │ writes: scored entries to jobs table
    │ returns: { matched_items: [{job_hash, user_id}, ...], matched_count: 8 }
    ▼
ProcessMatchedJobs (Map, MaxConcurrency: 3)
    │ Each iteration receives: { job_hash: "abc", user_id: "..." }
    │
    │   ├── CheckScoreForTailoring (Choice)
    │   │   score >= 85 → TailorLightTouch
    │   │   score < 85  → TailorFullRewrite
    │   ├── CompileResume
    │   ├── GenerateCoverLetter
    │   ├── CompileCoverLetter
    │   ├── UploadArtifacts (S3)
    │   ├── FindContacts
    │   └── SaveJob (update jobs table with S3 URLs, contacts)
    ▼
SavePipelineMetrics
    │ writes: per-scraper metrics to pipeline_metrics table
    ▼
SendEmailSummary
    │ reads: today's matched jobs from jobs table
    │ sends: HTML email via Gmail SMTP
    ▼
SelfImprove
    │ reads: today's metrics + historical data
    │ AI analyzes and suggests adjustments
    │ writes: to self_improvement_config table
    └── End

TOP-LEVEL CATCH:
    Any unhandled error → NotifyError Lambda → sends error email → End
```

### 4.2 Single-Job Pipeline (Standard)

```
SaveToJobsRaw
    │ writes: user's JD to jobs_raw with source="manual"
    │ returns: { job_hash: "abc" }
    ▼
ScoreSingleJob
    │ reads: job from jobs_raw + user's resumes
    │ uses SAME score_batch Lambda (consistent scoring)
    │ writes: to jobs table
    │ returns: { job_hash, match_score, matched_resume }
    ▼
CheckScoreThreshold (Choice)
    │ match_score >= 85 → TailorLightTouch
    │ match_score < 85  → TailorFullRewrite
    ▼
TailorLightTouch / TailorFullRewrite
    │ reads: job from jobs_raw, base resume from Supabase
    │ AI tailors (light or full)
    │ writes: tailored_tex to S3 temp location
    │ returns: { job_hash, tex_s3_key }
    ▼
CompileResume
    │ reads: tex from S3
    │ compiles with tectonic
    │ writes: PDF to S3
    │ returns: { job_hash, pdf_s3_key }
    ▼
GenerateCoverLetter → CompileCoverLetter
    │ same pattern as resume
    ▼
UploadArtifacts
    │ moves PDFs to permanent S3 paths (users/{user_id}/...)
    │ generates presigned URLs
    ▼
FindContacts
    │ Apify Google search for LinkedIn profiles
    ▼
SaveJob
    │ updates jobs table with S3 URLs, contacts, model name
    │ returns: full job object for frontend
    └── End

TOP-LEVEL CATCH:
    Any error → return error details (no email, frontend shows error)
```

---

## 5. API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /api/pipeline/run` | New | Trigger daily pipeline (manual) |
| `POST /api/pipeline/run-single` | New | Trigger single-job pipeline (Add Job) |
| `GET /api/pipeline/status` | New | Latest run metrics from pipeline_metrics |
| `GET /api/pipeline/status/{executionId}` | New | Poll specific execution (Step Functions describeExecution) |
| `POST /api/score-batch` | New | Score jobs against user's resumes |
| `POST /api/compile-latex` | New | Compile LaTeX → PDF |
| `POST /api/tailor` | Modified | Calls score-batch internally for consistency |
| `PATCH /api/dashboard/jobs/{id}` | Existing | Extended for location, apply_url editing |

### Execution Polling

```python
@app.get("/api/pipeline/status/{execution_id}")
def get_execution_status(execution_id: str, user: AuthUser = Depends(get_current_user)):
    """Poll a Step Functions execution. Used by Add Job and Run Pipeline UI."""
    sfn = boto3.client("stepfunctions")
    # Reconstruct ARN from execution_id
    arn = f"arn:aws:states:{REGION}:{ACCOUNT_ID}:execution:{STATE_MACHINE_NAME}:{execution_id}"
    response = sfn.describe_execution(executionArn=arn)

    status = response["status"]  # RUNNING, SUCCEEDED, FAILED, TIMED_OUT
    result = None
    if status == "SUCCEEDED":
        result = json.loads(response["output"])
    elif status == "FAILED":
        result = {"error": response.get("cause", "Unknown error")}

    return {
        "execution_id": execution_id,
        "status": status.lower(),
        "result": result,
        "started_at": response["startDate"].isoformat(),
    }
```

Frontend polls this every 2 seconds until status is `succeeded` or `failed`.

### Rate Limiting

```python
@app.post("/api/pipeline/run")
def trigger_pipeline(user: AuthUser = Depends(get_current_user)):
    # 1. Check concurrent execution limit (1 per user)
    running = sfn.list_executions(
        stateMachineArn=STATE_MACHINE_ARN,
        statusFilter="RUNNING",
    )
    user_running = [e for e in running["executions"] 
                    if json.loads(e.get("input", "{}")).get("user_id") == user.id]
    if user_running:
        raise HTTPException(409, "Pipeline already running")

    # 2. Check daily limit (5 manual triggers per user per day)
    today_count = supabase.table("pipeline_metrics") \
        .select("count", count="exact") \
        .eq("user_id", user.id) \
        .eq("run_date", date.today().isoformat()) \
        .execute()
    if today_count.count >= 5:
        raise HTTPException(429, "Daily limit reached (5 runs/day)")

    # 3. Start execution
    response = sfn.start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        input=json.dumps({"user_id": user.id}),
        name=f"{user.id}-{int(time.time())}",
    )
    return {"execution_id": response["executionArn"].split(":")[-1], "status": "started"}
```

---

## 6. Self-Improvement: How It Works

### What Gets Analyzed (end of each pipeline run)

```python
def self_improve(event, context):
    user_id = event["user_id"]
    run_metrics = event["metrics"]  # from SavePipelineMetrics

    # Read historical data
    last_30_days = supabase.table("pipeline_metrics") \
        .select("*").eq("user_id", user_id) \
        .gte("run_date", thirty_days_ago).execute()

    recent_jobs = supabase.table("jobs") \
        .select("source,match_score,title").eq("user_id", user_id) \
        .gte("first_seen", seven_days_ago).execute()

    current_config = supabase.table("self_improvement_config") \
        .select("*").eq("user_id", user_id).execute()

    # AI analysis
    adjustments = ai_council.analyze(metrics=last_30_days, jobs=recent_jobs, config=current_config)
    # Returns: { query_weights, scraper_weights, scoring_threshold, keyword_emphasis }

    # Save adjustments
    for config_type, config_data in adjustments.items():
        supabase.table("self_improvement_config").upsert({
            "user_id": user_id,
            "config_type": config_type,
            "config_data": config_data,
        }).execute()
```

### How Adjustments Apply (start of next pipeline run)

```python
def load_user_config(event, context):
    user_id = event["user_id"]

    # 1. Load base search config
    search_config = supabase.table("user_search_configs") \
        .select("*").eq("user_id", user_id).execute()

    # 2. Load self-improvement adjustments
    adjustments = supabase.table("self_improvement_config") \
        .select("*").eq("user_id", user_id).execute()

    # 3. Merge: adjustments modify the base config
    config = search_config.data[0] if search_config.data else DEFAULT_CONFIG

    for adj in adjustments.data:
        if adj["config_type"] == "query_weights":
            # Reorder queries by weight (higher weight = scraped first)
            config["queries"] = sorted(config["queries"],
                key=lambda q: adj["config_data"].get(q, 0.5), reverse=True)
        elif adj["config_type"] == "scraper_weights":
            # Skip scrapers with weight < 0.1 (deemed unhelpful)
            config["skip_scrapers"] = [s for s, w in adj["config_data"].items() if w < 0.1]
        elif adj["config_type"] == "scoring_threshold":
            config["min_match_score"] = adj["config_data"].get("threshold", 60)
        elif adj["config_type"] == "keyword_emphasis":
            # Pass to tailoring prompts as priority keywords
            config["emphasis_keywords"] = adj["config_data"].get("keywords", [])

    return config
```

---

## 7. Supabase Schema

```sql
-- Shared raw job data (scraped once, used by all users)
CREATE TABLE jobs_raw (
  job_hash TEXT PRIMARY KEY,            -- hash of (company + title + description[:500])
  title TEXT NOT NULL,
  company TEXT NOT NULL,
  description TEXT,
  location TEXT,
  apply_url TEXT,
  source TEXT NOT NULL,                 -- linkedin, indeed, adzuna, hn, manual, etc.
  experience_level TEXT,                -- entry_level, mid_level, senior, internship
  job_type TEXT,                        -- full_time, part_time, contract, internship
  query_hash TEXT,                      -- hash of (query + location + filters) for cache key
  scraped_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_jobs_raw_source_query ON jobs_raw(source, query_hash, scraped_at);
CREATE INDEX idx_jobs_raw_scraped ON jobs_raw(scraped_at);

-- Add job_hash FK to existing jobs table
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS job_hash TEXT REFERENCES jobs_raw(job_hash);

-- Self-improvement configuration (per-user)
CREATE TABLE self_improvement_config (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id) NOT NULL,
  config_type TEXT NOT NULL,            -- query_weights, scraper_weights, scoring_threshold, keyword_emphasis
  config_data JSONB NOT NULL,
  applied_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, config_type)
);

-- Pipeline run metrics (per-user, per-scraper, per-run)
CREATE TABLE pipeline_metrics (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id) NOT NULL,
  run_date DATE NOT NULL,
  execution_id TEXT,                    -- Step Functions execution ID
  scraper_name TEXT NOT NULL,
  jobs_found INT DEFAULT 0,
  jobs_matched INT DEFAULT 0,
  jobs_tailored INT DEFAULT 0,
  duration_seconds INT,
  apify_cost_cents INT DEFAULT 0,
  error_message TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_metrics_user_date ON pipeline_metrics(user_id, run_date);

-- AI response cache (migrated from SQLite, shared across users)
CREATE TABLE ai_cache (
  cache_key TEXT PRIMARY KEY,           -- hash of (prompt + system + model)
  response TEXT NOT NULL,
  provider TEXT,
  model TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_ai_cache_expires ON ai_cache(expires_at);

-- RLS policies
ALTER TABLE jobs_raw ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Anyone can read jobs_raw" ON jobs_raw FOR SELECT USING (true);
CREATE POLICY "Service role writes jobs_raw" ON jobs_raw FOR ALL USING (auth.role() = 'service_role');

ALTER TABLE self_improvement_config ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users read own config" ON self_improvement_config FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Service role full access" ON self_improvement_config FOR ALL USING (auth.role() = 'service_role');

ALTER TABLE pipeline_metrics ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users read own metrics" ON pipeline_metrics FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Service role full access" ON pipeline_metrics FOR ALL USING (auth.role() = 'service_role');

ALTER TABLE ai_cache ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role only" ON ai_cache FOR ALL USING (auth.role() = 'service_role');
```

### Data Migration (existing 95 jobs)

```sql
-- Backfill jobs_raw from existing jobs
INSERT INTO jobs_raw (job_hash, title, company, description, location, apply_url, source, scraped_at)
SELECT
  md5(company || '|' || title || '|' || left(coalesce(description, ''), 500)) as job_hash,
  title, company, description, location, apply_url, source, first_seen
FROM jobs
ON CONFLICT (job_hash) DO NOTHING;

-- Link existing jobs to jobs_raw
UPDATE jobs SET job_hash = md5(company || '|' || title || '|' || left(coalesce(description, ''), 500));
```

---

## 8. Lambda Layer (shared dependencies)

Package shared dependencies as a Lambda Layer to avoid duplicating them across 15+ functions:

```
layer/
  python/
    apify_client/
    supabase/
    postgrest/
    httpx/
    yaml/
```

SAM template:
```yaml
SharedDepsLayer:
  Type: AWS::Serverless::LayerVersion
  Properties:
    ContentUri: layer/
    CompatibleRuntimes:
      - python3.11
```

All Lambda functions reference this layer. Reduces individual function package size from ~50MB to ~5MB.

---

## 9. Email Workflows

### Daily Summary
Lambda `send_email_summary` at end of daily pipeline. Uses existing Gmail SMTP (from `email_notifier.py`). Sends HTML table of matched jobs with scores, artifact links.

### Stale Job Nudges (weekly)
EventBridge rule: every Monday 9:00 UTC → Lambda `send_stale_nudges`
- Query: `application_status = 'New' AND first_seen < 7 days ago`
- Send Gmail digest if count > 0

### Follow-Up Reminders (daily)
EventBridge rule: daily 10:00 UTC → Lambda `send_followup_reminders`
- Query: `application_status = 'Applied'` with no status change in 7+ days
- Send Gmail reminder with contacts

### Error Notification
Top-level Catch on both state machines → Lambda `notify_error`
- Sends email: "Pipeline failed at step X: error message"
- Records failure in `pipeline_metrics`

---

## 10. Frontend Changes

### Pipeline Status Bar
Replace hardcoded text with live `GET /api/pipeline/status`:
- Last run date, jobs found/matched/tailored
- Scraper health badges (green/red per source)
- Apify budget usage bar

### Run Pipeline Button
Dashboard header: "Run Pipeline" button → `POST /api/pipeline/run` → shows spinner → polls `GET /api/pipeline/status/{execId}` every 5s → shows results when done.

### Add Job Flow (refactored)
Current: synchronous `POST /api/tailor` → wait 30-60s
New: `POST /api/pipeline/run-single` → get `poll_url` → poll `GET /api/pipeline/status/{execId}` every 2s → show result
Same UX (progress indicator already built), consistent scoring.

### Additional Frontend Polish
- Skills tags (extract from JD, show as chips)
- Job card grid view toggle
- Inline PDF preview (iframe)
- Actual AI model name display
- Manual job editing (location, apply_url)
- User profile auto-generated from resume upload

---

## 11. Cost Estimate

| Component | Monthly Cost (1 user) | Monthly Cost (100 users) |
|-----------|----------------------|--------------------------|
| Step Functions | ~$0.05 | ~$5 |
| Lambda compute | ~$1 | ~$10-20 |
| Apify (shared cache) | ~$5-10 | ~$10-25 |
| S3 storage | ~$0.50 | ~$5 |
| EventBridge | ~$0.01 | ~$0.10 |
| SSM Parameter Store | Free | Free |
| Lambda Layer | Free | Free |
| **Total** | **~$7-12/mo** | **~$30-55/mo** |

---

## 12. Security

- All Lambda functions use IAM roles (no hardcoded credentials)
- API keys in SSM Parameter Store (encrypted, versioned)
- Step Functions execution input validated by Lambda (user_id must match JWT)
- Rate limiting: 5 manual triggers/day/user, 1 concurrent execution/user
- Supabase RLS on all per-user tables
- S3 paths scoped by user_id: `users/{user_id}/...`
- Presigned URLs for PDF access (30-day expiry)

---

## 13. Migration Plan

### Phase 1: Foundation (Days 1-4)
- Create Supabase tables (`jobs_raw`, `ai_cache`, `self_improvement_config`, `pipeline_metrics`) + migrate existing 95 jobs
- Build Lambda Layer with shared deps (`apify-client`, `supabase`, etc.)
- Write scraper Lambdas: generic Apify scraper + Adzuna/HN/YC API scrapers
- Write pipeline Lambdas: `load_config`, `check_cache`, `merge_dedup`, `score_batch`, `filter_matched`, `save_job`, `upload_s3`, `send_email`, `self_improve`, `notify_error`, `check_job_expiry`
- Refactor existing Lambdas: `tailor_resume`, `compile_latex`, `generate_cover_letter`, `find_contacts` to read/write Supabase (not receive full payloads)
- Define both Step Functions state machines in `template.yaml`
- `sam deploy`

### Phase 2: Integration + Frontend (Days 5-8)
- Wire frontend: pipeline status bar, Run Pipeline button, Add Job refactor to async polling
- Keep `/api/tailor` working for backward compat during transition
- Test each scraper individually (invoke Lambda directly)
- Test full daily pipeline end-to-end (manual Step Functions execution)
- Test single-job pipeline (Add Job flow)
- Frontend polish: skills tags, card view, PDF preview, user profile, onboarding wizard, source control, in-app notifications

### Phase 3: Shadow Mode (Days 9-11)
- Run Step Functions pipeline alongside GitHub Actions
- **Step Functions writes to `jobs_raw` only** — does NOT write to `jobs` table during shadow mode (prevents duplicate dashboard entries)
- Compare: do both find the same jobs? Similar counts per source?
- Fix discrepancies
- Run for 3 days to build confidence

### Phase 4: Cutover (Day 12)
- Disable GitHub Actions cron (keep `workflow_dispatch` for fallback)
- Enable Step Functions to write to `jobs` table (remove shadow-mode flag)
- Activate EventBridge scheduler (daily pipeline)
- Set up EventBridge rules for stale nudges (weekly) + follow-up reminders (daily) + expiry check (weekly)
- Monitor first 3 automated runs
- **Remove dead code**: `scrapers/` directory, `main.py`, `seen_jobs.json`, `self_improver.py` (keep in git history, delete from main branch)
- Remove old `/api/tailor` synchronous endpoint (all traffic now goes through Step Functions)

---

## 14. Multi-Tenant Scaling

| Decision | Why |
|----------|-----|
| `jobs_raw` shared table | Scrape once, score per-user. 100 users = 1x scrape cost |
| `ai_cache` in Supabase | Shared cache, same JD scored twice = cache hit |
| Step Functions per-user execution | Just pass `user_id`, no per-user infrastructure |
| Apify budget tracking | Prevent runaway costs as users scale |
| User search config in Supabase | Each user customizes queries, locations, experience levels |
| S3 paths by user_id | Clean isolation, no cross-user data leaks |
| Lambda Layer shared deps | One layer, all functions, all users |

### Multi-Tenant Apify Budget

Single user: one Apify API key, one budget ($5-10/mo).
Multi-tenant: ALL users share one Apify API key. Budget management:
- **Global cap**: total Apify spend across all users (matches Apify plan limit)
- **Per-user fair share**: `global_budget / active_users` = per-user daily Apify allocation
- **Shared cache reduces cost**: if User 1 scrapes "software engineer Dublin" at 7:00, User 2 at 7:30 gets cache hit — no Apify cost
- Track in `pipeline_metrics.apify_cost_cents` per user per run

### Multi-User Scheduling

For 1-10 users: one EventBridge rule triggers a `dispatch_pipelines` Lambda that loops through active users and starts an execution for each.

For 100+ users: EventBridge rule → SQS queue with user_ids → Lambda consumes queue → starts Step Functions execution per user. This handles concurrency and throttling naturally.

### Multi-Domain & Internships

Pipeline is domain-agnostic. Each user's `user_search_configs` table row defines:
```json
{
  "queries": ["software engineer", "devops"],
  "locations": ["Dublin", "Remote"],
  "experience_levels": ["entry_level", "mid_level"],
  "job_types": ["full_time"],
  "excluded_companies": ["Temu"]
}
```

A marketing intern would have:
```json
{
  "queries": ["digital marketing intern", "social media"],
  "locations": ["Dublin"],
  "experience_levels": ["internship"],
  "job_types": ["internship", "part_time"]
}
```

Same scrapers, same pipeline, different inputs. AI scores against whatever resume the user uploaded.

---

## 15. Product Features (built alongside pipeline migration)

### 15.1 Onboarding Wizard

New user flow: **Upload Resume → Set Location → Pick Roles → Choose Sources → First Run**

Step 1: Upload resume PDF → AI auto-extracts profile (name, location, skills, experience level)
Step 2: Confirm/edit location, toggle "include remote jobs"
Step 3: AI suggests search queries from resume (e.g., "Software Engineer", "DevOps"). User can add/remove.
Step 4: Toggle job sources on/off (default: all enabled)
Step 5: "Start Searching" → triggers first Step Functions pipeline execution

Requires: `/api/resumes/upload` already exists. New: AI query suggestion Lambda, onboarding page refactor.

### 15.2 Resume Versioning & Re-Tailoring

When a user uploads a new resume:
- New jobs use the latest resume automatically
- Existing jobs show: "⚠ Newer resume available — [Re-tailor with v2]"
- User clicks to re-tailor individual jobs on demand
- Re-tailoring triggers `single-job-pipeline` with the new resume
- Track `resume_version` on each job to detect stale tailorings

### 15.3 Job Expiry

**Active checking** (weekly EventBridge → Lambda `check_job_expiry`):
- HTTP HEAD request to each job's `apply_url`
- 404/redirect/error → mark `is_expired = true`
- LinkedIn URLs older than 45 days → auto-mark expired (LinkedIn blocks HEAD requests)
- Cost: minimal (HTTP HEAD is free, no Apify needed for most sites)

**Visual treatment**:
- Expired jobs: red "EXPIRED" badge, dimmed row, "Archive" button
- Rejected jobs: grey "REJECTED" badge, dimmed row (separate styling from expired)
- Both filterable: "Show expired" / "Show rejected" toggles on dashboard
- Neither auto-deleted — user controls their data

Requires: `is_expired BOOLEAN DEFAULT false` column on `jobs` table.

### 15.4 Source Control

User's `user_search_configs` includes a `sources` array:

```json
{
  "sources": ["linkedin", "indeed", "adzuna", "glassdoor", "gradireland"],
  "queries": ["software engineer", "devops"],
  "locations": ["Dublin", "Remote"],
  "experience_levels": ["entry_level", "mid_level"],
  "job_types": ["full_time"]
}
```

Settings page: checkbox grid of all available sources. `CheckScraperCache` Lambda reads this and only runs scrapers the user has enabled.

### 15.5 Notifications

**Email**: daily summary (existing) + stale nudges (weekly) + follow-up reminders (daily) + pipeline error alerts.

**SMS / WhatsApp** (future, not Phase 2E):
- SNS for SMS ($0.00645/msg to Ireland)
- WhatsApp Business API via Twilio ($0.005/msg)
- Add as notification preferences in Settings: email (always), SMS (opt-in), WhatsApp (opt-in)
- For Phase 2E: email only. SMS/WhatsApp deferred to when multi-tenant launches (justifies the cost).

**In-app badge + toast**:
- Store `last_pipeline_run` timestamp in Supabase per user
- Store `last_seen_at` timestamp (updated on each dashboard visit)
- If `last_pipeline_run > last_seen_at` → show badge on Dashboard nav item + toast notification
- Badge shows count of new jobs since last visit
- Toast: "Pipeline ran at 7:00 AM — 5 new jobs matched. [View] [Dismiss]"

### 15.6 Cross-Source Deduplication

Same job posted on LinkedIn AND Indeed → keep the **richest version** (longest description, most fields populated):

```python
def dedup_cross_source(jobs):
    """Dedup across sources. Keep the version with the richest data."""
    grouped = {}  # key: (normalized_company, normalized_title)
    for job in jobs:
        key = (normalize(job.company), normalize(job.title))
        if key in grouped:
            existing = grouped[key]
            # Keep the one with longer description
            if len(job.description or '') > len(existing.description or ''):
                grouped[key] = job
        else:
            grouped[key] = job
    return list(grouped.values())
```

This runs inside `merge_and_dedup` Lambda BEFORE writing to `jobs_raw`.

### 15.7 Scraper Schema Resilience

Apify actors can change their output format without warning. Protection:

1. **Schema validation**: each scraper Lambda validates Apify output against expected schema before normalizing. Missing fields → use defaults, don't crash.
2. **Alerting**: if a scraper returns 0 results for 3 consecutive days, trigger error notification email: "LinkedIn scraper hasn't found jobs in 3 days — actor may have changed."
3. **SSM-stored selectors**: for Apify Web Scraper (Irish sites), CSS selectors stored in SSM Parameter Store. Update selectors without redeploying Lambda.
4. **Graceful degradation**: scraper failure in the Parallel state returns `{count: 0, error: "..."}` — pipeline continues with other sources.

---

## 16. Additional Schema Changes

```sql
-- Resume versioning
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS resume_version INT DEFAULT 1;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS is_expired BOOLEAN DEFAULT false;

-- In-app notification tracking
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_pipeline_run TIMESTAMPTZ;

-- Source control in search config
-- (sources array already part of user_search_configs JSONB)

-- Notification preferences
ALTER TABLE users ADD COLUMN IF NOT EXISTS notification_prefs JSONB DEFAULT '{"email": true, "sms": false, "whatsapp": false}';
```

---

## 17. How Future Phases Fit In

| Phase | Integration |
|-------|-------------|
| **2B Editor** | `/api/compile-latex` already in this spec. Add CloudWatch warmer for sub-second compilation. Frontend-only. |
| **2C PDF-to-LaTeX** | New Lambda endpoint. Feeds into editor. No pipeline changes. |
| **2D Company Intel** | Add `enrich_company` step in daily pipeline (between scoring and tailoring). OR: separate on-demand Lambda triggered from Research tab. Cache in `company_intel` table. |
| **2F Interview Prep** | Pure Lambda + frontend. No pipeline dependency. |
| **2G Analytics** | `pipeline_metrics` feeds dashboard. CloudWatch alarms for error alerting. EventBridge + SNS for notifications. |

The architecture is **extensible by adding Lambda functions and Step Functions states** — no infrastructure changes for any future phase.

---

## 18. Rollback Plan

GitHub Actions workflow stays in the repo (cron commented out, `workflow_dispatch` active):
1. Re-enable cron: `0 7 * * 1-5`
2. Push to trigger GH Actions deploy
3. Pipeline runs on GH Actions as before
4. Debug Step Functions without time pressure

---

## 19. Success Criteria

- [ ] Pipeline runs daily without failure
- [ ] Parallel scraping completes in < 15 minutes
- [ ] LinkedIn + Indeed jobs appear via Apify with full descriptions
- [ ] Manual "Add Job" uses same scoring as automated pipeline (consistent scores)
- [ ] Score-first tailoring: light tweaks when base >= 85, full rewrite when low
- [ ] No Step Functions state exceeds 256KB payload (Supabase data bus verified)
- [ ] Email summary sent after each successful run
- [ ] Error notification sent on pipeline failure
- [ ] Stale nudges sent weekly, follow-up reminders daily
- [ ] "Run Pipeline" button works from dashboard
- [ ] Scraper cache prevents redundant Apify calls (verified with same-day re-run)
- [ ] Apify budget tracking prevents overspend
- [ ] Self-improvement adjustments visible and applied to next run
- [ ] Existing 95 jobs migrated to jobs_raw + job_hash FK
- [ ] Onboarding wizard: resume upload → auto-profile → queries → sources → first run
- [ ] Re-tailor button shows on jobs with older resume version
- [ ] Expired jobs checked weekly, dimmed on dashboard
- [ ] Rejected jobs dimmed separately from expired
- [ ] Source toggles work in Settings (pipeline respects user's source selection)
- [ ] In-app badge + toast for new jobs since last visit
- [ ] Cross-source dedup keeps richest version
- [ ] Scraper schema validation prevents crashes on Apify changes
