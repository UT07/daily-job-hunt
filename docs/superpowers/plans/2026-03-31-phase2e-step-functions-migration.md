# Phase 2E: Step Functions Pipeline Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the GitHub Actions pipeline with AWS Step Functions + Lambda — parallel Apify/API scrapers, self-improvement, email workflows, score-first tailoring, and product features (onboarding, expiry, notifications).

**Architecture:** Step Functions orchestrates, Lambda computes, Supabase is the data bus (256KB payload limit solved), Apify handles Playwright scraping. No EC2, no n8n. Everything defined in SAM `template.yaml`.

**Tech Stack:** AWS Step Functions, Lambda (Python 3.11), SAM, Apify, Supabase, S3, EventBridge, SSM Parameter Store

**Spec:** `docs/superpowers/specs/2026-03-31-phase2e-step-functions-migration-design.md` (v5, approved)

---

## Task Overview (execute in this order)

### Phase 1: Foundation (Days 1-4)

| # | Task | Type | Can Parallel? |
|---|------|------|--------------|
| 1 | Supabase tables + data migration | DB | Yes |
| 2 | Lambda Layer (shared deps) | Infra | Yes |
| 3 | Scraper Lambdas (Apify generic + API scrapers) | Backend | Yes (after Layer) |
| 4 | Pipeline Lambdas (score, tailor, compile, etc.) | Backend | Yes (after Layer) |
| 5 | Step Functions state machines in SAM | Infra | After 3+4 |
| 6 | SAM deploy + smoke test | Deploy | After 5 |

### Phase 2: Integration + Frontend (Days 5-8)

| # | Task | Type | Can Parallel? |
|---|------|------|--------------|
| 7 | API endpoints (run, run-single, status, polling) | Backend | Yes |
| 8 | Frontend: pipeline status + Run Pipeline button | Frontend | Yes |
| 9 | Frontend: Add Job refactor (async Step Functions) | Frontend | After 7 |
| 10 | Frontend: skills tags, card view, PDF preview, AI model | Frontend | Yes |
| 11 | Frontend: onboarding wizard refactor | Frontend | Yes |
| 12 | Frontend: in-app notifications (badge + toast) | Frontend | Yes |
| 13 | Frontend: source control in Settings | Frontend | Yes |
| 14 | Frontend: job expiry + rejected dimming | Frontend | After 7 |
| 15 | Scoring consistency fix | Backend | Yes |
| 16 | Score-first tailoring | Backend | Yes |
| 17 | Resume versioning (re-tailor on demand) | Full-stack | Yes |
| 18 | User profile from resume | Full-stack | Yes |

### Phase 3: Shadow Mode (Days 9-11)

| # | Task | Type |
|---|------|------|
| 19 | Shadow mode testing | Testing |

### Phase 4: Cutover (Day 12)

| # | Task | Type |
|---|------|------|
| 20 | Cutover + cleanup | Deploy |

---

## Task 1: Supabase Tables + Data Migration

**Files:**
- Run in: Supabase SQL editor or CLI

- [ ] **Step 1: Create jobs_raw table**

```sql
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
  query_hash TEXT,
  scraped_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_jobs_raw_source_query ON jobs_raw(source, query_hash, scraped_at);
CREATE INDEX idx_jobs_raw_scraped ON jobs_raw(scraped_at);

ALTER TABLE jobs_raw ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Anyone can read jobs_raw" ON jobs_raw FOR SELECT USING (true);
CREATE POLICY "Service role writes jobs_raw" ON jobs_raw FOR ALL USING (auth.role() = 'service_role');
```

- [ ] **Step 2: Create ai_cache table**

```sql
CREATE TABLE ai_cache (
  cache_key TEXT PRIMARY KEY,
  response TEXT NOT NULL,
  provider TEXT,
  model TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_ai_cache_expires ON ai_cache(expires_at);

ALTER TABLE ai_cache ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role only" ON ai_cache FOR ALL USING (auth.role() = 'service_role');
```

- [ ] **Step 3: Create self_improvement_config table**

```sql
CREATE TABLE self_improvement_config (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id) NOT NULL,
  config_type TEXT NOT NULL,
  config_data JSONB NOT NULL,
  applied_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, config_type)
);

ALTER TABLE self_improvement_config ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users read own config" ON self_improvement_config FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Service role full access" ON self_improvement_config FOR ALL USING (auth.role() = 'service_role');
```

- [ ] **Step 4: Create pipeline_metrics table**

```sql
CREATE TABLE pipeline_metrics (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id) NOT NULL,
  run_date DATE NOT NULL,
  execution_id TEXT,
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

ALTER TABLE pipeline_metrics ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users read own metrics" ON pipeline_metrics FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Service role full access" ON pipeline_metrics FOR ALL USING (auth.role() = 'service_role');
```

- [ ] **Step 5: Add new columns to existing tables**

```sql
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS job_hash TEXT REFERENCES jobs_raw(job_hash);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS resume_version INT DEFAULT 1;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS is_expired BOOLEAN DEFAULT false;

ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_pipeline_run TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS notification_prefs JSONB DEFAULT '{"email": true, "sms": false, "whatsapp": false}';
```

- [ ] **Step 6: Migrate existing jobs to jobs_raw**

```sql
INSERT INTO jobs_raw (job_hash, title, company, description, location, apply_url, source, scraped_at)
SELECT
  md5(company || '|' || title || '|' || left(coalesce(description, ''), 500)) as job_hash,
  title, company, description, location, apply_url, source, first_seen
FROM jobs
ON CONFLICT (job_hash) DO NOTHING;

UPDATE jobs SET job_hash = md5(company || '|' || title || '|' || left(coalesce(description, ''), 500));
```

- [ ] **Step 7: Verify migration**

```sql
SELECT count(*) FROM jobs_raw;  -- Should match job count
SELECT count(*) FROM jobs WHERE job_hash IS NOT NULL;  -- All jobs linked
```

- [ ] **Step 8: Commit**

```bash
git commit --allow-empty -m "feat: create Supabase tables for Step Functions pipeline (jobs_raw, ai_cache, metrics, config)"
```

---

## Task 2: Lambda Layer (Shared Dependencies)

**Files:**
- Create: `layer/requirements.txt`
- Create: `layer/build.sh`
- Modify: `template.yaml`

- [ ] **Step 1: Create layer requirements**

Create `layer/requirements.txt`:

```
apify-client>=1.6.0
supabase>=2.0.0
httpx>=0.27.0
pyyaml>=6.0
```

- [ ] **Step 2: Create layer build script**

Create `layer/build.sh`:

```bash
#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
rm -rf python/
pip install -r requirements.txt -t python/ --quiet
echo "Layer built: $(du -sh python/ | cut -f1)"
```

- [ ] **Step 3: Add layer to SAM template**

In `template.yaml`, add under Resources:

```yaml
  SharedDepsLayer:
    Type: AWS::Serverless::LayerVersion
    Properties:
      LayerName: naukribaba-shared-deps
      ContentUri: layer/
      CompatibleRuntimes:
        - python3.11
    Metadata:
      BuildMethod: python3.11
```

- [ ] **Step 4: Build and verify**

```bash
cd layer && bash build.sh && cd ..
sam build
```

- [ ] **Step 5: Commit**

```bash
git add layer/ template.yaml
git commit -m "feat: add Lambda Layer for shared dependencies (apify, supabase, httpx)"
```

---

## Task 3: Scraper Lambdas

**Files:**
- Create: `lambdas/scrapers/scrape_apify.py`
- Create: `lambdas/scrapers/scrape_adzuna.py`
- Create: `lambdas/scrapers/scrape_hn.py`
- Create: `lambdas/scrapers/scrape_yc.py`
- Create: `lambdas/scrapers/normalizers.py`
- Modify: `template.yaml`

- [ ] **Step 1: Create normalizers module**

Create `lambdas/scrapers/normalizers.py` — maps raw Apify/API output to the standard `jobs_raw` schema:

```python
"""Normalize scraper output to standard jobs_raw schema."""
import hashlib
import html
import re

def normalize_job(raw: dict, source: str, query_hash: str = "") -> dict:
    """Normalize a raw job dict to jobs_raw schema."""
    title = html.unescape(raw.get("title") or raw.get("positionName") or "").strip()
    company = html.unescape(raw.get("company") or raw.get("companyName") or "").strip()
    description = html.unescape(raw.get("description") or raw.get("text") or "").strip()
    # Strip HTML tags from description
    description = re.sub(r'<[^>]+>', '\n', description).strip()
    location = raw.get("location") or raw.get("city") or ""
    apply_url = raw.get("url") or raw.get("applyUrl") or raw.get("apply_url") or ""

    if not title or not company:
        return None

    job_hash = hashlib.md5(
        f"{company.lower()}|{title.lower()}|{description[:500].lower()}".encode()
    ).hexdigest()

    return {
        "job_hash": job_hash,
        "title": title[:500],
        "company": company[:200],
        "description": description[:10000],
        "location": location[:200],
        "apply_url": apply_url[:1000],
        "source": source,
        "experience_level": raw.get("experienceLevel") or raw.get("experience_level"),
        "job_type": raw.get("jobType") or raw.get("job_type"),
        "query_hash": query_hash,
    }


def normalize_linkedin(items: list, query_hash: str) -> list:
    """Normalize LinkedIn Jobs Scraper output."""
    jobs = []
    for item in items:
        job = normalize_job({
            "title": item.get("title"),
            "company": item.get("companyName"),
            "description": item.get("description") or item.get("descriptionHtml"),
            "location": item.get("location"),
            "url": item.get("url") or item.get("link"),
            "experienceLevel": item.get("experienceLevel"),
            "jobType": item.get("contractType"),
        }, source="linkedin", query_hash=query_hash)
        if job:
            jobs.append(job)
    return jobs


def normalize_indeed(items: list, query_hash: str) -> list:
    """Normalize Indeed Scraper output."""
    jobs = []
    for item in items:
        job = normalize_job({
            "title": item.get("positionName") or item.get("title"),
            "company": item.get("company"),
            "description": item.get("description"),
            "location": item.get("location"),
            "url": item.get("url") or item.get("externalApplyLink"),
            "jobType": item.get("jobType"),
        }, source="indeed", query_hash=query_hash)
        if job:
            jobs.append(job)
    return jobs


def normalize_adzuna(items: list, query_hash: str) -> list:
    """Normalize Adzuna API response."""
    jobs = []
    for item in items:
        job = normalize_job({
            "title": item.get("title"),
            "company": (item.get("company") or {}).get("display_name"),
            "description": item.get("description"),
            "location": (item.get("location") or {}).get("display_name"),
            "url": item.get("redirect_url"),
        }, source="adzuna", query_hash=query_hash)
        if job:
            jobs.append(job)
    return jobs


def normalize_hn(items: list, query_hash: str) -> list:
    """Normalize HN Hiring comment-parsed jobs."""
    jobs = []
    for item in items:
        job = normalize_job(item, source="hn_hiring", query_hash=query_hash)
        if job:
            jobs.append(job)
    return jobs


def normalize_generic_web(items: list, source: str, query_hash: str) -> list:
    """Normalize Apify Web Scraper output (GradIreland, IrishJobs, Jobs.ie)."""
    jobs = []
    for item in items:
        job = normalize_job(item, source=source, query_hash=query_hash)
        if job:
            jobs.append(job)
    return jobs
```

- [ ] **Step 2: Create generic Apify scraper Lambda**

Create `lambdas/scrapers/scrape_apify.py`:

```python
"""Generic Apify scraper Lambda. Called with actor_id and run_input as params."""
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta

import boto3
from apify_client import ApifyClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]

def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))

def handler(event, context):
    actor_id = event["actor_id"]
    run_input = event["run_input"]
    source = event["source"]
    normalizer_name = event.get("normalizer", source)
    query_hash = event.get("query_hash", "")
    cache_ttl_hours = event.get("cache_ttl_hours", 24)

    db = get_supabase()

    # Check cache
    cached = db.table("jobs_raw").select("*", count="exact") \
        .eq("source", source).eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.utcnow() - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count > 0:
        logger.info(f"[{source}] Cache hit: {cached.count} jobs from last {cache_ttl_hours}h")
        return {"count": cached.count, "source": source, "cached": True}

    # Check Apify budget
    first_of_month = datetime.utcnow().replace(day=1).date().isoformat()
    monthly = db.table("pipeline_metrics").select("apify_cost_cents") \
        .gte("created_at", first_of_month).execute()
    total_cents = sum(r.get("apify_cost_cents", 0) for r in (monthly.data or []))
    budget = int(os.environ.get("APIFY_MONTHLY_BUDGET_CENTS", "500"))
    if total_cents >= budget:
        logger.warning(f"[{source}] Apify budget exceeded: {total_cents}/{budget} cents")
        return {"count": 0, "source": source, "skipped": "budget_exceeded"}

    # Run Apify actor
    apify_key = get_param("/naukribaba/APIFY_API_KEY")
    client = ApifyClient(apify_key)
    logger.info(f"[{source}] Running actor {actor_id}")
    run = client.actor(actor_id).call(run_input=run_input, timeout_secs=240)
    items = client.dataset(run["defaultDatasetId"]).list_items().items
    logger.info(f"[{source}] Got {len(items)} raw items")

    # Normalize
    from normalizers import (normalize_linkedin, normalize_indeed,
                              normalize_generic_web)
    normalizer_map = {
        "linkedin": normalize_linkedin,
        "indeed": normalize_indeed,
        "glassdoor": normalize_generic_web,
        "gradireland": normalize_generic_web,
        "irishjobs": normalize_generic_web,
        "jobsie": normalize_generic_web,
    }
    normalize_fn = normalizer_map.get(normalizer_name, normalize_generic_web)
    if normalizer_name in ("glassdoor", "gradireland", "irishjobs", "jobsie"):
        jobs = normalize_fn(items, source, query_hash)
    else:
        jobs = normalize_fn(items, query_hash)

    # Validate schema
    valid_jobs = [j for j in jobs if j and j.get("title") and j.get("company")]
    if not valid_jobs:
        logger.warning(f"[{source}] 0 valid jobs after normalization")
        return {"count": 0, "source": source, "error": "no_valid_jobs"}

    # Write to jobs_raw (upsert)
    for job in valid_jobs:
        job["scraped_at"] = datetime.utcnow().isoformat()
        db.table("jobs_raw").upsert(job, on_conflict="job_hash").execute()

    # Estimate Apify cost (rough)
    cost_cents = max(1, len(items) // 20)  # ~$0.50/1K = ~0.05 cents per item
    logger.info(f"[{source}] Wrote {len(valid_jobs)} jobs, est cost: {cost_cents} cents")

    return {"count": len(valid_jobs), "source": source, "apify_cost_cents": cost_cents}
```

- [ ] **Step 3: Create Adzuna API scraper Lambda**

Create `lambdas/scrapers/scrape_adzuna.py`:

```python
"""Adzuna REST API scraper."""
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta

import boto3
import httpx

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]

def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))

def handler(event, context):
    queries = event.get("queries", ["software engineer"])
    locations = event.get("locations", ["ireland"])
    query_hash = event.get("query_hash", "")
    cache_ttl_hours = event.get("cache_ttl_hours", 24)

    db = get_supabase()

    # Check cache
    cached = db.table("jobs_raw").select("*", count="exact") \
        .eq("source", "adzuna").eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.utcnow() - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count > 0:
        return {"count": cached.count, "source": "adzuna", "cached": True}

    app_id = get_param("/naukribaba/ADZUNA_APP_ID")
    app_key = get_param("/naukribaba/ADZUNA_APP_KEY")

    from normalizers import normalize_adzuna
    all_jobs = []

    for query in queries:
        url = f"https://api.adzuna.com/v1/api/jobs/ie/search/1"
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "what": query,
            "max_days_old": 3,
            "results_per_page": 50,
        }
        resp = httpx.get(url, params=params, timeout=20)
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            jobs = normalize_adzuna(results, query_hash)
            all_jobs.extend(jobs)
            logger.info(f"[adzuna] Query '{query}': {len(jobs)} jobs")

    # Write to jobs_raw
    for job in all_jobs:
        job["scraped_at"] = datetime.utcnow().isoformat()
        db.table("jobs_raw").upsert(job, on_conflict="job_hash").execute()

    return {"count": len(all_jobs), "source": "adzuna"}
```

- [ ] **Step 4: Create HN Hiring scraper Lambda**

Create `lambdas/scrapers/scrape_hn.py`:

```python
"""HN Hiring scraper — fetches latest 'Who is hiring?' thread via Algolia API."""
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta

import boto3
import httpx

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]

def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))

def parse_hn_comment(text: str) -> dict:
    """Parse a HN hiring comment into job fields."""
    import html as html_mod
    text = html_mod.unescape(text)
    text = re.sub(r'<[^>]+>', '\n', text).strip()
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if not lines:
        return None

    # First line usually has: Company | Role | Location | Type
    first_line = lines[0]
    parts = [p.strip() for p in first_line.split('|')]
    company = parts[0] if parts else ""
    title = parts[1] if len(parts) > 1 else ""
    location = parts[2] if len(parts) > 2 else ""

    description = '\n'.join(lines)

    if not company or not title:
        return None

    return {
        "title": title,
        "company": company,
        "description": description,
        "location": location,
    }

def handler(event, context):
    query_hash = event.get("query_hash", "")
    cache_ttl_hours = event.get("cache_ttl_hours", 168)  # 1 week for HN

    db = get_supabase()

    # Check cache
    cached = db.table("jobs_raw").select("*", count="exact") \
        .eq("source", "hn_hiring").eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.utcnow() - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count > 0:
        return {"count": cached.count, "source": "hn_hiring", "cached": True}

    # Find latest "Who is hiring?" thread
    search_url = "https://hn.algolia.com/api/v1/search"
    params = {"query": "Ask HN: Who is hiring?", "tags": "story", "hitsPerPage": 1}
    resp = httpx.get(search_url, params=params, timeout=15)
    hits = resp.json().get("hits", [])
    if not hits:
        return {"count": 0, "source": "hn_hiring", "error": "no_thread_found"}

    thread_id = hits[0]["objectID"]

    # Fetch comments
    comments_url = f"https://hn.algolia.com/api/v1/search"
    params = {"tags": f"comment,story_{thread_id}", "hitsPerPage": 200}
    resp = httpx.get(comments_url, params=params, timeout=30)
    comments = resp.json().get("hits", [])

    from normalizers import normalize_hn
    parsed = []
    for c in comments:
        text = c.get("comment_text", "")
        if not text or len(text) < 50:
            continue
        job = parse_hn_comment(text)
        if job:
            parsed.append(job)

    jobs = normalize_hn(parsed, query_hash)

    for job in jobs:
        job["scraped_at"] = datetime.utcnow().isoformat()
        db.table("jobs_raw").upsert(job, on_conflict="job_hash").execute()

    logger.info(f"[hn_hiring] {len(jobs)} jobs from {len(comments)} comments")
    return {"count": len(jobs), "source": "hn_hiring"}
```

- [ ] **Step 5: Create YC Jobs scraper Lambda**

Create `lambdas/scrapers/scrape_yc.py`:

```python
"""YC Jobs scraper — fetches from WorkAtAStartup."""
import hashlib
import json
import logging
from datetime import datetime, timedelta

import boto3
import httpx

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]

def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))

def handler(event, context):
    queries = event.get("queries", ["software engineer"])
    query_hash = event.get("query_hash", "")
    cache_ttl_hours = event.get("cache_ttl_hours", 48)

    db = get_supabase()

    cached = db.table("jobs_raw").select("*", count="exact") \
        .eq("source", "yc").eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.utcnow() - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count > 0:
        return {"count": cached.count, "source": "yc", "cached": True}

    # WorkAtAStartup uses Inertia.js — fetch with proper headers
    headers = {
        "X-Inertia": "true",
        "X-Inertia-Version": "",
        "Accept": "text/html, application/xhtml+xml",
    }

    from normalizers import normalize_generic_web
    all_jobs = []

    for query in queries:
        url = f"https://www.workatastartup.com/companies?query={query}"
        resp = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
        if resp.status_code == 200:
            try:
                data = resp.json()
                companies = data.get("props", {}).get("companies", [])
                for co in companies:
                    for job in co.get("jobs", []):
                        all_jobs.append({
                            "title": job.get("title", ""),
                            "company": co.get("name", ""),
                            "description": job.get("description", ""),
                            "location": job.get("location", ""),
                            "apply_url": f"https://www.workatastartup.com/jobs/{job.get('id', '')}",
                        })
            except Exception as e:
                logger.warning(f"[yc] Parse error: {e}")

    jobs = normalize_generic_web(all_jobs, "yc", query_hash)
    for job in jobs:
        job["scraped_at"] = datetime.utcnow().isoformat()
        db.table("jobs_raw").upsert(job, on_conflict="job_hash").execute()

    logger.info(f"[yc] {len(jobs)} jobs")
    return {"count": len(jobs), "source": "yc"}
```

- [ ] **Step 6: Add all scraper Lambdas to SAM template**

In `template.yaml`, add each scraper function:

```yaml
  ScrapeApifyFunction:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: naukribaba-scrape-apify
      Handler: lambdas/scrapers/scrape_apify.handler
      Runtime: python3.11
      Timeout: 300
      MemorySize: 256
      Layers: [!Ref SharedDepsLayer]
      Policies:
        - SSMParameterReadPolicy:
            ParameterName: /naukribaba/*

  ScrapeAdzunaFunction:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: naukribaba-scrape-adzuna
      Handler: lambdas/scrapers/scrape_adzuna.handler
      Runtime: python3.11
      Timeout: 30
      MemorySize: 128
      Layers: [!Ref SharedDepsLayer]
      Policies:
        - SSMParameterReadPolicy:
            ParameterName: /naukribaba/*

  ScrapeHNFunction:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: naukribaba-scrape-hn
      Handler: lambdas/scrapers/scrape_hn.handler
      Runtime: python3.11
      Timeout: 60
      MemorySize: 128
      Layers: [!Ref SharedDepsLayer]
      Policies:
        - SSMParameterReadPolicy:
            ParameterName: /naukribaba/*

  ScrapeYCFunction:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: naukribaba-scrape-yc
      Handler: lambdas/scrapers/scrape_yc.handler
      Runtime: python3.11
      Timeout: 30
      MemorySize: 128
      Layers: [!Ref SharedDepsLayer]
      Policies:
        - SSMParameterReadPolicy:
            ParameterName: /naukribaba/*
```

- [ ] **Step 7: Store API keys in SSM Parameter Store**

```bash
aws ssm put-parameter --name "/naukribaba/APIFY_API_KEY" --value "$APIFY_API_KEY" --type SecureString --region eu-west-1
aws ssm put-parameter --name "/naukribaba/ADZUNA_APP_ID" --value "$ADZUNA_APP_ID" --type SecureString --region eu-west-1
aws ssm put-parameter --name "/naukribaba/ADZUNA_APP_KEY" --value "$ADZUNA_APP_KEY" --type SecureString --region eu-west-1
aws ssm put-parameter --name "/naukribaba/SUPABASE_URL" --value "$SUPABASE_URL" --type SecureString --region eu-west-1
aws ssm put-parameter --name "/naukribaba/SUPABASE_SERVICE_KEY" --value "$SUPABASE_SERVICE_KEY" --type SecureString --region eu-west-1
aws ssm put-parameter --name "/naukribaba/GROQ_API_KEY" --value "$GROQ_API_KEY" --type SecureString --region eu-west-1
```

- [ ] **Step 8: Commit**

```bash
git add lambdas/scrapers/ template.yaml
git commit -m "feat: add scraper Lambdas (Apify generic, Adzuna, HN, YC) with normalizers"
```

---

## Task 4: Pipeline Lambdas

**Files:**
- Create: `lambdas/pipeline/load_config.py`
- Create: `lambdas/pipeline/check_cache.py`
- Create: `lambdas/pipeline/merge_dedup.py`
- Create: `lambdas/pipeline/score_batch.py`
- Create: `lambdas/pipeline/filter_matched.py`
- Create: `lambdas/pipeline/save_job.py`
- Create: `lambdas/pipeline/send_email.py`
- Create: `lambdas/pipeline/self_improve.py`
- Create: `lambdas/pipeline/notify_error.py`
- Create: `lambdas/pipeline/check_expiry.py`
- Refactor: existing `tailor_resume`, `compile_latex`, `generate_cover_letter`, `find_contacts` to read/write Supabase
- Modify: `template.yaml`

Each Lambda follows the pattern: read input IDs/hashes → fetch full data from Supabase → process → write results to Supabase → return only IDs/hashes.

See spec sections 4, 5, 6 for detailed logic of each Lambda. Code follows the same patterns as the scraper Lambdas (SSM for secrets, Supabase for data).

- [ ] **Step 1: Create load_config Lambda** — reads user's search config + self-improvement adjustments from Supabase, merges them, returns the merged config (queries, locations, sources, thresholds).

- [ ] **Step 2: Create check_cache Lambda** — for each source × query combination, checks if `jobs_raw` has recent enough entries. Returns list of scrapers to run vs skip.

- [ ] **Step 3: Create merge_dedup Lambda** — reads today's `jobs_raw` entries, deduplicates cross-source (keep richest version), returns array of new job_hashes.

- [ ] **Step 4: Create score_batch Lambda** — reads jobs by hashes from `jobs_raw`, reads user's resumes from Supabase, calls existing `match_jobs()` function, writes scored jobs to `jobs` table, returns matched hashes. Timeout: 300s.

- [ ] **Step 5: Create filter_matched Lambda** — reads scored jobs from `jobs` table, filters by min_score, returns `{job_items: [{job_hash, user_id}]}` for Map state.

- [ ] **Step 6: Refactor tailor_resume** — accept `{job_hash, user_id, light_touch}` as input, read job from `jobs_raw` + resume from Supabase, tailor, write tex to S3 temp path, return `{job_hash, tex_s3_key}`.

- [ ] **Step 7: Refactor compile_latex** — accept `{tex_s3_key}`, read tex from S3, compile with tectonic, write PDF to S3, return `{pdf_s3_key}`.

- [ ] **Step 8: Refactor generate_cover_letter** — same pattern as tailor_resume.

- [ ] **Step 9: Refactor find_contacts** — accept `{job_hash, user_id}`, read job from Supabase, find contacts via Apify, write contacts to `jobs` table.

- [ ] **Step 10: Create save_job Lambda** — accept all artifact S3 keys, generate presigned URLs, update `jobs` table with final S3 URLs, contacts, model name.

- [ ] **Step 11: Create send_email Lambda** — read today's matched jobs from `jobs` table, format HTML email, send via Gmail SMTP.

- [ ] **Step 12: Create self_improve Lambda** — see spec section 6 for full logic.

- [ ] **Step 13: Create notify_error Lambda** — send error notification email with step name and error message.

- [ ] **Step 14: Create check_expiry Lambda** — HTTP HEAD to apply_urls, mark expired jobs. For EventBridge weekly trigger.

- [ ] **Step 15: Add all to SAM template with appropriate timeouts**

- [ ] **Step 16: Commit**

```bash
git add lambdas/pipeline/ template.yaml
git commit -m "feat: add pipeline Lambdas (load, cache, dedup, score, filter, save, email, improve, error, expiry)"
```

---

## Task 5: Step Functions State Machines in SAM

**Files:**
- Modify: `template.yaml`

- [ ] **Step 1: Define daily-pipeline state machine** — See spec section 4.1 for the full ASL definition. Add as `AWS::StepFunctions::StateMachine` in `template.yaml` with `DefinitionString` or `DefinitionUri`.

- [ ] **Step 2: Define single-job-pipeline state machine** — See spec section 4.2 for the full ASL. Includes score-first branching (Choice state).

- [ ] **Step 3: Add EventBridge schedule rule** — weekdays 7:00 UTC triggers daily-pipeline.

- [ ] **Step 4: Add EventBridge rules for email workflows** — stale nudges (Monday 9:00), follow-up reminders (daily 10:00), expiry check (Sunday 6:00).

- [ ] **Step 5: Add IAM roles** — Step Functions needs permission to invoke all Lambda functions. Each Lambda needs SSM read + S3 write + Supabase access.

- [ ] **Step 6: Commit**

```bash
git add template.yaml
git commit -m "feat: define Step Functions state machines (daily + single-job) with EventBridge schedules"
```

---

## Task 6: SAM Deploy + Smoke Test

- [ ] **Step 1: Build and deploy**

```bash
sam build && sam deploy --guided
```

- [ ] **Step 2: Smoke test scrapers** — invoke each scraper Lambda directly via AWS CLI and verify it writes to `jobs_raw`.

- [ ] **Step 3: Smoke test daily pipeline** — start a Step Functions execution manually via console, watch each state execute.

- [ ] **Step 4: Smoke test single-job pipeline** — start with a test JD, verify the full flow produces a tailored resume.

- [ ] **Step 5: Commit any fixes**

---

## Task 7: API Endpoints

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Add `POST /api/pipeline/run`** — starts daily-pipeline execution, checks rate limits (1 concurrent, 5/day). See spec section 5.

- [ ] **Step 2: Add `POST /api/pipeline/run-single`** — starts single-job-pipeline execution for Add Job. Returns `poll_url`.

- [ ] **Step 3: Add `GET /api/pipeline/status`** — reads latest metrics from `pipeline_metrics` table.

- [ ] **Step 4: Add `GET /api/pipeline/status/{executionId}`** — calls Step Functions `describeExecution`, returns status + output.

- [ ] **Step 5: Add `POST /api/compile-latex`** — compile LaTeX to PDF, return binary. For Phase 2B editor.

- [ ] **Step 6: Refactor `/api/tailor` for backward compat** — keep working during transition, internally call score-batch for consistent scoring.

- [ ] **Step 7: Commit**

```bash
git add app.py && git commit -m "feat: add pipeline trigger, status, polling, compile-latex endpoints"
```

---

## Task 8: Frontend — Pipeline Status + Run Pipeline

**Files:**
- Create: `web/src/components/PipelineStatus.jsx`
- Modify: `web/src/pages/Dashboard.jsx`

- [ ] **Step 1: Create PipelineStatus component** — calls `GET /api/pipeline/status`, shows last run info, scraper health badges, Apify budget bar. See spec section 10.

- [ ] **Step 2: Add Run Pipeline button** — `POST /api/pipeline/run`, polls `GET /api/pipeline/status/{execId}` every 5s, shows progress.

- [ ] **Step 3: Replace hardcoded status bar with PipelineStatus component**

- [ ] **Step 4: Build and commit**

```bash
cd web && npm run build
git add web/src/ && git commit -m "feat: live pipeline status bar + Run Pipeline button"
```

---

## Task 9: Frontend — Add Job Refactor (Async)

**Files:**
- Modify: `web/src/pages/AddJob.jsx`

- [ ] **Step 1: Refactor Add Job to use `POST /api/pipeline/run-single`** — replace synchronous `/api/tailor` call with async Step Functions trigger + polling. Keep the existing progress indicator UI.

- [ ] **Step 2: Poll `GET /api/pipeline/status/{execId}` every 2s** — show steps as they complete. Display result when execution succeeds.

- [ ] **Step 3: Build and commit**

---

## Task 10: Frontend — Skills Tags, Card View, PDF Preview, AI Model

**Files:**
- Create: `web/src/components/SkillTags.jsx`
- Create: `web/src/components/JobCard.jsx`
- Modify: `web/src/pages/Dashboard.jsx`
- Modify: `web/src/pages/JobWorkspace.jsx`
- Modify: `web/src/components/JobTable.jsx`

- [ ] **Step 1: Create SkillTags** — extract common tech skills from JD text, display as brutalist tag chips.

- [ ] **Step 2: Create JobCard** — card component for grid view with score, company, skills, status.

- [ ] **Step 3: Add card/table toggle to Dashboard**

- [ ] **Step 4: Add inline PDF preview** — iframe in Resume and Cover Letter tabs.

- [ ] **Step 5: Fix AI model display** — show actual winning model name from `tailoring_model` field.

- [ ] **Step 6: Build and commit**

---

## Task 11: Frontend — Onboarding Wizard

**Files:**
- Modify: `web/src/pages/Onboarding.jsx`

- [ ] **Step 1: Refactor to guided wizard** — Step 1: Upload Resume (auto-extract profile), Step 2: Confirm Location, Step 3: Pick Roles (AI-suggested from resume), Step 4: Choose Sources, Step 5: Start Searching (trigger first pipeline).

- [ ] **Step 2: Build and commit**

---

## Task 12: Frontend — In-App Notifications

**Files:**
- Modify: `web/src/layouts/AppLayout.jsx` (sidebar badge)
- Create: `web/src/components/NotificationToast.jsx`

- [ ] **Step 1: Add badge to Dashboard nav item** — compare `last_pipeline_run` vs `last_seen_at`, show count of new jobs.

- [ ] **Step 2: Add toast on page load** — "Pipeline ran at 7:00 AM — 5 new jobs."

- [ ] **Step 3: Update `last_seen_at`** on each dashboard visit.

- [ ] **Step 4: Build and commit**

---

## Task 13: Frontend — Source Control in Settings

**Files:**
- Modify: `web/src/pages/Settings.jsx`

- [ ] **Step 1: Add source toggle grid** — checkboxes for each job source, saved to `user_search_configs.sources`.

- [ ] **Step 2: Build and commit**

---

## Task 14: Frontend — Job Expiry + Rejected Dimming

**Files:**
- Modify: `web/src/components/JobTable.jsx`
- Modify: `web/src/components/JobCard.jsx`
- Modify: `web/src/pages/Dashboard.jsx`

- [ ] **Step 1: Add expired badge + dimmed styling** — red "EXPIRED" badge, reduced opacity for expired jobs.

- [ ] **Step 2: Add rejected dimming** — grey "REJECTED" badge, reduced opacity (separate from expired).

- [ ] **Step 3: Add filter toggles** — "Show expired" / "Show rejected" toggles on dashboard.

- [ ] **Step 4: Build and commit**

---

## Task 15: Scoring Consistency Fix

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Refactor `/api/tailor` to use `score_batch` internally** — so Add Job (old path) and pipeline (new path) use identical scoring.

- [ ] **Step 2: Verify** — score a job via dashboard, paste same JD in Add Job, scores should be within ±5.

- [ ] **Step 3: Commit**

---

## Task 16: Score-First Tailoring

**Files:**
- Modify: `lambdas/pipeline/tailor_resume.py` (or existing `tailorer.py`)

- [ ] **Step 1: Add `light_touch` parameter** — when `True`, use a modified system prompt that only reorders skills and tweaks summary (no full rewrite).

- [ ] **Step 2: Verify** — job with base score 90 should get minimal changes. Job with score 60 should get full rewrite.

- [ ] **Step 3: Commit**

---

## Task 17: Resume Versioning (Re-Tailor on Demand)

**Files:**
- Modify: `web/src/pages/JobWorkspace.jsx`
- Modify: `app.py`

- [ ] **Step 1: Track resume_version** — when user uploads new resume, increment version. Store version on each tailored job.

- [ ] **Step 2: Add "Re-tailor" button** — shows when `job.resume_version < user.current_resume_version`. Triggers `single-job-pipeline` with new resume.

- [ ] **Step 3: Commit**

---

## Task 18: User Profile from Resume

**Files:**
- Modify: `app.py` (resume upload endpoint)
- Modify: `web/src/pages/Settings.jsx`

- [ ] **Step 1: Auto-extract profile on upload** — AI extracts name, location, skills, experience summary from resume text. Updates user profile.

- [ ] **Step 2: Show extracted profile in Settings** — editable fields pre-populated from resume.

- [ ] **Step 3: Commit**

---

## Task 19: Shadow Mode Testing

- [ ] **Step 1: Run Step Functions daily pipeline manually** — verify all scrapers produce jobs, scoring works, tailoring produces PDFs.

- [ ] **Step 2: Compare with GitHub Actions** — run both, compare job counts per source, score distributions.

- [ ] **Step 3: Step Functions writes to `jobs_raw` only during shadow mode** — verify no duplicate dashboard entries.

- [ ] **Step 4: Run for 3 days** — fix discrepancies.

- [ ] **Step 5: Verify email workflows** — trigger stale nudges and follow-up reminders manually.

---

## Task 20: Cutover + Cleanup

- [ ] **Step 1: Disable GitHub Actions cron** — comment out `schedule` in `daily_job_hunt.yml`, keep `workflow_dispatch`.

- [ ] **Step 2: Enable Step Functions to write to `jobs` table** — remove shadow-mode flag.

- [ ] **Step 3: Activate EventBridge schedules** — daily pipeline, stale nudges, follow-up reminders, expiry check.

- [ ] **Step 4: Monitor first 3 automated runs**

- [ ] **Step 5: Remove dead code** — delete `scrapers/` directory, `main.py`, `seen_jobs.json`, `self_improver.py` from main branch (preserved in git history).

- [ ] **Step 6: Remove old `/api/tailor` synchronous endpoint** — all traffic now through Step Functions.

- [ ] **Step 7: Final commit**

```bash
git add -A && git commit -m "feat: Phase 2E complete — cutover to Step Functions pipeline"
```
