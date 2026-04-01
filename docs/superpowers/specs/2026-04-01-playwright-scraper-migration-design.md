# Phase 2.5: Playwright Scraper Migration — Design Spec

**Date:** 2026-04-01
**Status:** Draft
**Author:** Utkarsh + Claude
**Replaces:** Apify-based scrapers (Phase 2E)

## Problem

The current scraper architecture depends on Apify ($5/account, two accounts = $10/month) which:
- Burns through budget in 1-2 scrape runs ($3-4 per LinkedIn run alone)
- Produces truncated job descriptions (search page snippets, not full JDs)
- Has unreliable actors (Glassdoor actor removed from marketplace)
- Makes the contacts finder expensive ($0.03-0.05 per job)
- Three scrapers are completely dropped (Jobs.ie, IrishJobs, GradIreland)
- Two API scrapers are broken (Adzuna returning 0, YC returning 0)

**Goal:** Replace Apify with self-hosted scrapers delivering 10-15 high-quality, relevant job matches per day at <$10/month total cost.

## Architecture

### Overview

```
Step Functions (daily trigger, Mon-Fri 7:00 UTC)
  │
  ├── Parallel Branch A: API Scrapers (Lambda, ~10s each)
  │     ├── Adzuna (REST API, free key)
  │     ├── HN Hiring (Algolia API)
  │     └── YC Work at Startup (HTTP/Inertia.js)
  │
  ├── Parallel Branch B: Playwright Scrapers (Fargate Spot, ~3-5 min each)
  │     ├── Task 1: LinkedIn Jobs (Scrapling StealthyFetcher + proxy)
  │     ├── Task 2: Indeed Jobs (Scrapling StealthyFetcher + proxy)
  │     ├── Task 3: Glassdoor (Scrapling StealthyFetcher + proxy)
  │     └── Task 4: Irish Portals (Jobs.ie + IrishJobs + GradIreland, Scrapling Fetcher, no proxy)
  │
  ├── merge_dedup (Lambda) — 3-tier dedup + relevance pre-filter
  ├── score_batch (Lambda) — AI scoring on pre-filtered jobs only
  ├── tailor/compile/upload pipeline (unchanged)
  │
  └── Post-scoring: Contacts Finder (Fargate Spot, StealthyFetcher + proxy)
        └── Google search for LinkedIn profiles at matched companies
```

### Key Architecture Decisions

1. **Scrapling over raw Patchright** — Scrapling wraps Patchright as its `StealthyFetcher` engine and adds tiered fetching (basic HTTP → stealth browser). One library handles all sources with automatic escalation. Research shows Scrapling handles LinkedIn and Reddit while passing bot detection where vanilla Playwright fails.

2. **Residential proxy for hard sources only** — LinkedIn, Indeed, Glassdoor route through Bright Data (or IPRoyal) residential proxy. Irish portals and API sources go direct. AWS Fargate IPs are permanently blacklisted by LinkedIn/Indeed/Glassdoor (confirmed by research).

3. **Parallel Fargate Spot tasks** — Each hard source gets its own Fargate task (own IP, own fingerprint, independent failure). Irish portals grouped into one task. If LinkedIn gets blocked, Indeed/Glassdoor still succeed.

4. **Shared Docker image** — Single `naukribaba-playwright` image with Scrapling + Xvfb + normalizers. Different entrypoints per source via environment variables.

5. **Supabase as integration point** — All scrapers write to `jobs_raw` (no user_id). Per-user scoring pipeline reads from `jobs_raw`. This scales to multi-tenant without changing scrapers.

6. **Future OpenClaw integration point** — The `jobs` table (user-scoped, scored matches) is the interface for a future auto-apply agent. OpenClaw reads matched jobs, applies, writes status back. No coupling to the scraper layer.

## Scraper Engine: Scrapling

### Why Scrapling Over Patchright Directly

| Feature | Patchright | Scrapling |
|---------|-----------|-----------|
| Anti-bot engine | Patched Chromium (no CDP leaks) | Modified Chromium (v0.4+, formerly Camoufox/Firefox) |
| Tiered fetching | No — always launches browser | Yes — HTTP first, browser only if needed |
| Built-in selectors | Playwright API | Enhanced with auto-retry + adaptive tracking |
| Cloudflare bypass | Partial | Built-in for standard Cloudflare |
| LinkedIn tested | Community reports | Confirmed working in tests |
| API compatibility | Playwright drop-in | Own API, simpler |
| Docker images | Build your own | Official images published per release |

**Note (v0.4 changes):** StealthyFetcher moved from Camoufox (modified Firefox) to a lighter Chromium-based engine. It's 101% faster, uses less memory, and is more stable. Several arguments were removed (`humanize`, `block_images`, `addons`, `os_randomize`). Human-like delays must be implemented in our scraper loop, not via Scrapling args.

### Usage Pattern Per Source

```python
from scrapling import Fetcher, StealthyFetcher

PROXY = "http://user:pass@brd.superproxy.io:33335"

# Tier 1: API sources (Lambda, no browser)
# Adzuna, HN, YC — use httpx directly

# Tier 2: Simple HTML sites (Fargate, no proxy)
page = Fetcher().get("https://www.jobs.ie/search?q=software+engineer")

# Tier 3: Anti-bot protected sites (Fargate, proxy)
page = StealthyFetcher(proxy=PROXY).get(
    "https://www.linkedin.com/jobs/search?keywords=software+engineer&location=Ireland"
)
```

### Known Limitations (from GitHub issues)

1. **Cloudflare Turnstile hang (issue #100):** StealthyFetcher can hang indefinitely on embedded Cloudflare Turnstile when `solve_cloudflare=True`. Mitigation: set a timeout on all fetches, fall back to skipping the source on hang.
2. **Python 3.14 issue:** CamoufoxConfig has a known issue on Python 3.14. Pin to Python 3.11 or 3.12 in the Docker image.
3. **Removed args:** `humanize`, `block_images`, `addons`, `os_randomize`, `disable_ads`, `geoip` all removed in v0.4. Implement delays in our own scraper loop code.

### Docker Setup

Scrapling publishes official Docker images with every release. Use theirs as a base.

```dockerfile
# Use Scrapling's official image as base (includes all browsers)
FROM ghcr.io/d4vinci/scrapling:latest

# Add our dependencies
RUN pip install supabase httpx boto3

# CRITICAL: Fargate /dev/shm is limited to 64MB, cannot be resized
# Chromium must use /tmp instead — pass --disable-dev-shm-usage at browser launch

# Scraper code
COPY scrapers/playwright/ /app/
COPY lambdas/scrapers/normalizers.py /app/

WORKDIR /app

# Pin Python 3.12 (Python 3.14 has known Camoufox issues)
ENTRYPOINT ["python3", "main.py"]
```

**Browser launch args (mandatory for Fargate):**
```python
# In every StealthyFetcher call, pass extra browser args
page = StealthyFetcher(
    proxy=PROXY,
    extra_headers={"Accept-Language": "en-US,en;q=0.9"},
).get(url, disable_resources=True)

# For DynamicFetcher, pass Chromium args directly:
# --disable-dev-shm-usage --no-sandbox
```

## Per-Source Scraper Design

### LinkedIn Jobs (hardest)

- **Method:** StealthyFetcher + residential proxy
- **Target:** Public job search page (no login required)
- **URL pattern:** `linkedin.com/jobs/search?keywords={query}&location={location}`
- **Extraction:** Parse job cards from search results, then navigate to `/jobs/view/{id}` for full JD
- **Anti-bot handling:** Auth wall circuit breaker (stop after 3 consecutive auth walls per session)
- **Volume cap:** 50 listings per run
- **Rate limiting:** 3-5 second random delay between page loads
- **Fallback:** If blocked, log warning and skip LinkedIn for this run. Pipeline continues with other sources.

### Indeed Jobs

- **Method:** StealthyFetcher + residential proxy
- **Target:** Job search results page
- **Extraction:** Extract from hidden JSON `window.mosaic.providerData["mosaic-provider-jobcards"]` which contains full JDs (avoids navigating to each detail page)
- **Anti-bot handling:** Cloudflare Turnstile — Scrapling's stealth mode handles standard Cloudflare
- **Volume cap:** 50 listings per run
- **Rate limiting:** 2-4 second delay between pages

### Glassdoor

- **Method:** StealthyFetcher + residential proxy
- **Target:** Job search results
- **Extraction:** Navigate to job detail page, extract `[data-test="jobDescriptionContent"]`
- **Anti-bot handling:** DataDome + Cloudflare (hardest after LinkedIn). If login overlay appears, stop.
- **Volume cap:** 30 listings per run
- **Rate limiting:** 4-6 second delay (most aggressive anti-bot)

### Jobs.ie / IrishJobs.ie / GradIreland

- **Method:** Scrapling `Fetcher` (basic HTTP) or `StealthyFetcher` without proxy
- **Target:** Job search pages
- **Extraction:** Standard CSS selectors, follow apply URLs for full JDs
- **Anti-bot handling:** None expected (simple HTML sites)
- **Volume cap:** 50 per source per run
- **Grouped:** All three run in one Fargate task (shared browser instance)

### Adzuna (Lambda, API)

- **Method:** REST API via httpx (existing Lambda, fix query params)
- **Current issue:** Returning 0 results — likely wrong country code or query format
- **Fix:** Debug API params, verify API key is active, test with broader queries
- **No Playwright needed**

### HN Who's Hiring (Lambda, API)

- **Method:** Algolia API (existing Lambda, already working)
- **No changes needed**

### YC Work at Startup (Lambda, HTTP)

- **Method:** HTTP requests to workatastartup.com Inertia.js API (existing Lambda)
- **Current issue:** Returning 0 — endpoint may have changed
- **Fix:** Verify Inertia.js version extraction, check if API contract changed
- **No Playwright needed**

### Contacts Finder (Fargate)

- **Method:** StealthyFetcher + residential proxy
- **Target:** Google search for LinkedIn profiles (e.g., `site:linkedin.com/in "Company Name" "Engineering Manager"`)
- **Runs:** After scoring, only for matched jobs (10-15 per day, not all 400 scraped)
- **Volume:** ~30-45 Google searches per run (3 searches per matched job)
- **Rate limit:** 5-10 second delay between searches, sticky proxy session
- **Replaces:** Current Apify Google Search actor ($0.03-0.05 per job)

## Scraper Output Contract

### Problem: Fargate Can't Return Data to Step Functions

Lambda scrapers return `{"new_job_hashes": [...]}` which `merge_dedup` reads directly. Fargate tasks communicate only via exit code (0/1). We need a way for Fargate scrapers to tell the pipeline what they found.

### Solution: Supabase `scrape_runs` Table

Each Fargate scraper writes a summary row to a new `scrape_runs` table when it finishes:

```sql
CREATE TABLE scrape_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline_run_id TEXT NOT NULL,       -- links to Step Functions execution ID
    source TEXT NOT NULL,                -- 'linkedin', 'indeed', etc.
    status TEXT DEFAULT 'running',       -- 'running', 'completed', 'failed', 'blocked'
    jobs_found INTEGER DEFAULT 0,
    jobs_new INTEGER DEFAULT 0,          -- new (not cached)
    new_job_hashes JSONB DEFAULT '[]',   -- list of hashes for merge_dedup
    error_message TEXT,
    blocked_reason TEXT,                 -- 'auth_wall', 'captcha', 'rate_limit', null
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
```

### Flow

1. Step Functions passes `pipeline_run_id` to each Fargate task as an env var
2. Fargate scraper creates a `scrape_runs` row with `status: 'running'` on start
3. Scraper writes jobs to `jobs_raw` (as before) and collects `new_job_hashes`
4. On completion, updates the row: `status: 'completed'`, `jobs_found`, `new_job_hashes`
5. On error/block, updates: `status: 'failed'` or `status: 'blocked'`, `error_message`
6. `merge_dedup` Lambda queries `scrape_runs` for this `pipeline_run_id` to get all `new_job_hashes`

### Lambda Scrapers (Adzuna, HN, YC)

Same contract — they also write to `scrape_runs`. This unifies the output format across Lambda and Fargate scrapers.

### Human-Like Delays (our responsibility, not Scrapling's)

Since Scrapling v0.4 removed the `humanize` argument, each scraper must implement delays:

```python
import random
import time

def human_delay(min_s=2, max_s=5):
    """Gaussian-distributed delay for human-like browsing."""
    delay = random.gauss((min_s + max_s) / 2, (max_s - min_s) / 4)
    time.sleep(max(min_s, min(max_s, delay)))

def scrape_with_delays(fetcher, urls, max_jobs=50):
    """Scrape URLs with rate limiting and circuit breaking."""
    results = []
    consecutive_failures = 0
    for url in urls[:max_jobs]:
        try:
            page = fetcher.get(url, timeout=30)
            results.append(page)
            consecutive_failures = 0
            human_delay()
        except Exception as e:
            consecutive_failures += 1
            if consecutive_failures >= 3:
                logger.warning(f"Circuit breaker: 3 consecutive failures, stopping")
                break
            human_delay(5, 10)  # longer delay after failure
    return results
```

## Data Quality Layer

### Full JD Fetching

After scraping search results, follow detail page URLs to get complete job descriptions:

| Source | Search gives | Detail page strategy |
|--------|-------------|---------------------|
| LinkedIn | ~200 char snippet | Navigate to `/jobs/view/{id}`, extract `.description__text` |
| Indeed | Paragraph snippet | Extract from hidden JSON (full JD in search page data) |
| Glassdoor | Rating + snippet | Navigate to detail, extract `[data-test="jobDescriptionContent"]` |
| Irish portals | Title + snippet | Follow apply URL, extract JD from employer page |
| Adzuna | Full text via API | Already complete |
| HN | Full comment | Already complete |
| YC | Company + snippet | Navigate to company page for full role description |

**Fallback chain per job:**
1. Detail page fetch (Scrapling browser)
2. Apply URL direct fetch (httpx, no browser)
3. Keep snippet, flag `description_quality: "partial"`

**Schema addition to `jobs_raw`:**
```sql
ALTER TABLE jobs_raw ADD COLUMN description_quality TEXT DEFAULT 'full';
-- Values: 'full', 'partial', 'snippet_only'
```

### 3-Tier Deduplication

**Tier 1: Exact hash (current, runs during scraping)**
- `md5(company.lower() | title.lower() | description[:500].lower())`
- Catches identical reposts within same source

**Tier 2: Fuzzy title+company (runs in merge_dedup Lambda)**
- Normalize titles: strip "Senior/Junior/Lead/Staff/Principal/I/II/III"
- `SequenceMatcher(company_a, company_b) > 0.8 AND SequenceMatcher(title_a, title_b) > 0.7`
- Cross-source dedup (LinkedIn posting = Indeed posting)
- Keep version with longest description + most metadata

**Tier 3: Semantic similarity (runs post-scoring)**
- If two jobs at same company have >80% overlap in `key_matches`, likely same role
- Nearly free — uses already-computed key_matches data

### Relevance Pre-Filter

Runs in `merge_dedup` Lambda BEFORE AI scoring. Cheap keyword/location checks to reduce AI token usage.

**Rules (configurable per user via `user_search_configs`):**

1. **Location compatibility** — reject if location is incompatible with user's work authorizations
2. **Seniority filter** — reject Director/VP/Principal/Head Of titles (too senior)
3. **Minimum skill overlap** — extract tech keywords from JD via regex, reject if <2 overlap with user's skills
4. **Description quality gate** — reject if description < 100 chars (not enough for meaningful scoring)
5. **Salary floor** — reject India roles below ₹10 LPA

**Expected funnel:**
```
~400 scraped/day → pre-filter removes ~60-70% → ~80-120 scored → threshold → 10-15 matched
```

### Freshness & Staleness

- `last_seen` updated on every re-scrape
- `is_stale: true` after 14 days without re-scrape
- `is_expired: true` after 30 days
- `is_evergreen: true` if continuously re-scraped for 30+ days (standing requisition)
- Expired jobs excluded from default dashboard view

## Infrastructure

### Fargate Task Definition

```yaml
PlaywrightTaskDef:
  Type: AWS::ECS::TaskDefinition
  Properties:
    Family: naukribaba-playwright
    NetworkMode: awsvpc
    RequiresCompatibilities: [FARGATE]
    Cpu: 512        # 0.5 vCPU
    Memory: 2048     # 2 GB (minimum for Chromium + Xvfb)
    ExecutionRoleArn: !GetAtt FargateExecutionRole.Arn
    TaskRoleArn: !GetAtt FargateTaskRole.Arn
    ContainerDefinitions:
      - Name: scraper
        Image: !Sub "${AWS::AccountId}.dkr.ecr.${AWS::Region}.amazonaws.com/naukribaba-playwright:latest"
        Essential: true
        Environment:
          - Name: SCRAPER_SOURCE
            Value: linkedin  # overridden per task
          - Name: PROXY_URL
            Value: !Sub "{{resolve:ssm:/naukribaba/PROXY_URL}}"
        LogConfiguration:
          LogDriver: awslogs
          Options:
            awslogs-group: /ecs/naukribaba-playwright
            awslogs-region: !Ref AWS::Region
            awslogs-stream-prefix: scraper
```

### Networking

- **VPC:** Default VPC (`vpc-0bfb7f8052eb3968b`)
- **Subnets:** 3 public subnets in eu-west-1a/b/c (auto-assign public IP)
- **`assignPublicIp: ENABLED`** — free, no NAT gateway needed
- **Security group:** Allow all outbound (scraping), no inbound

### Step Functions Integration

Fargate tasks invoked via `arn:aws:states:::ecs:runTask.sync` with:
- `capacityProviderStrategy: [{capacityProvider: FARGATE_SPOT, weight: 1}]`
- `launchType` OMITTED (mutually exclusive with capacityProviderStrategy)
- Retry on Spot interruption: `MaxAttempts: 2, BackoffRate: 2`
- Catch fallback: on 2 Spot failures, retry with `FARGATE` (on-demand, guaranteed)

### Proxy Setup

- **Provider:** Bright Data or IPRoyal (residential, PAYG)
- **Credentials:** SSM Parameter Store (`/naukribaba/PROXY_URL`)
- **Format:** `http://user-zone-session_rand:pass@brd.superproxy.io:33335`
- **Sticky sessions:** `-session-{random_id}` suffix for consistent IP per browser session
- **SSL:** `ignore_https_errors=True` on browser context (required for Bright Data MITM cert)
- **Usage:** Only LinkedIn, Indeed, Glassdoor, contacts finder. Irish portals go direct.

### Docker Image

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.50.0-noble

# Stealth scraping engine
RUN pip install scrapling patchright supabase httpx boto3

# Virtual display for headless=False anti-detection
RUN apt-get update && apt-get install -y xvfb && rm -rf /var/lib/apt/lists/*

# Scraper code
COPY scrapers/playwright/ /app/
COPY lambdas/scrapers/normalizers.py /app/

WORKDIR /app
ENTRYPOINT ["xvfb-run", "--auto-servernum", "python3", "main.py"]
```

**Estimated image size:** ~500MB-1GB (Chromium + Python deps)
**ECR:** Same repo (`385017713886.dkr.ecr.eu-west-1.amazonaws.com`)

### Docker Build CI

Add to `.github/workflows/deploy.yml`:
```yaml
- name: Build and push Playwright image
  run: |
    docker build -t naukribaba-playwright -f Dockerfile.playwright .
    docker tag naukribaba-playwright $ECR_REPO:playwright-latest
    docker push $ECR_REPO:playwright-latest
```

## Cost Estimate

| Component | Monthly Cost |
|-----------|-------------|
| Fargate Spot (5 tasks × 5 min × 22 weekdays) | ~$0.50 |
| Residential proxy (~1-2 GB/month) | $4-8 |
| Lambda (API scrapers + pipeline) | ~$0.50 |
| ECR storage | ~$0.10 |
| S3 (resumes/cover letters) | ~$0.50 |
| AI scoring (free tier providers) | ~$0 |
| **Total** | **~$6-10/month** |

**vs. current Apify:** ~$5/day = ~$100/month at daily usage

## Multi-Tenant Scalability

### Shared Scraping, Per-User Pipeline

- `jobs_raw` has NO `user_id` — shared pool across all users
- Scrapers search broadly (all tech roles in configured locations)
- Per-user pipeline: pre-filter → score → match → tailor
- 10 users sharing the same scraped jobs = 1× scraping cost, 10× scoring cost

### Scaling Triggers

| Users | Scrape volume | Proxy cost | Compute change |
|-------|-------------|-----------|----------------|
| 1 | ~400 jobs/day | ~$8/mo | 5 Fargate tasks |
| 10 | ~600 jobs/day | ~$12/mo | Same 5 tasks, broader queries |
| 100 | ~1,500 jobs/day | ~$25-30/mo | 8-12 tasks, Step Functions Map state |
| 1,000 | ~5,000 jobs/day | ~$100-200/mo | Queue-based, multiple waves |

### User-Scoped Configuration

Pre-filter reads from user's `user_search_configs` and `users` table:
- `locations` → location compatibility filter
- `experience_levels` → seniority filter
- User's resume skills → skill overlap filter
- `work_authorizations` → visa compatibility filter

## Implementation Tiers

### Tier 1 (Week 1): Playwright Foundation + LinkedIn

- [ ] `Dockerfile.playwright` with Scrapling + Xvfb
- [ ] ECS Cluster + Fargate task definition in `template.yaml`
- [ ] Security group (all outbound, no inbound)
- [ ] Bright Data account + proxy credentials in SSM
- [ ] LinkedIn scraper: search page → detail page → full JD → normalize → `jobs_raw`
- [ ] Step Functions wired to invoke Fargate task
- [ ] ECR push workflow in CI
- [ ] Test: run LinkedIn scraper, verify jobs in `jobs_raw` with full descriptions

### Tier 2 (Week 2): Remaining Playwright Scrapers

- [ ] Indeed scraper (hidden JSON extraction from search page)
- [ ] Glassdoor scraper
- [ ] Irish portals scraper (Jobs.ie + IrishJobs + GradIreland, grouped, no proxy)
- [ ] Contacts finder migrated to Scrapling + proxy (replaces Apify Google Search)
- [ ] All sources wired in Step Functions parallel branches

### Tier 3 (Week 3): Quality Layer + API Fixes

- [ ] Full JD fetching with fallback chain
- [ ] 3-tier dedup in merge_dedup (hash → fuzzy → semantic)
- [ ] Relevance pre-filter (location, seniority, skills, description length)
- [ ] `description_quality` column in `jobs_raw`
- [ ] Fix Adzuna API (query params, country code)
- [ ] Fix YC scraper (Inertia.js endpoint)
- [ ] Remove Apify dependency from codebase

### Tier 4 (Week 4+): Resilience + Monitoring

- [ ] Spot interruption handling (retry → fallback to on-demand)
- [ ] Scraper health dashboard (which sources succeeded/failed)
- [ ] Alert on block/CAPTCHA detection
- [ ] Proxy bandwidth monitoring
- [ ] `is_stale` / `is_expired` / `is_evergreen` freshness tracking

## Future: OpenClaw Auto-Apply Agent (Phase 3)

**Not part of this spec, but architecturally prepared for.**

Integration point: the `jobs` table (user-scoped, scored matches).

```
Phase 2.5 (this spec):
  Scrapers → jobs_raw → score → jobs → dashboard

Phase 3 (future):
  Dashboard → user clicks "Auto Apply" → OpenClaw agent
  → reads job from Supabase → opens apply URL → fills form
  → uploads tailored resume → sends LinkedIn connection request
  → updates application_status in Supabase
```

OpenClaw runs as a separate service (self-hosted daemon or Docker container). It reads from and writes to Supabase. No coupling to the scraper or scoring pipeline.

## Risks

1. **LinkedIn blocks despite stealth + proxy** — Mitigation: graceful degradation, pipeline continues with other sources. LinkedIn is highest value but not the only source.
2. **Fargate Spot interruption during scrape** — Mitigation: retry 2× on Spot, fallback to on-demand Fargate.
3. **Bright Data pricing changes** — Mitigation: proxy config is in SSM, swap provider without code changes. IPRoyal is a backup at similar pricing.
4. **Cloudflare Turnstile hang (Scrapling issue #100)** — StealthyFetcher can hang indefinitely on embedded Turnstile. Mitigation: 30-second timeout on all fetches, catch and skip on timeout. Indeed is the most likely source to trigger this.
4. **Scrapling library abandoned** — Mitigation: Scrapling wraps Patchright. If Scrapling dies, we drop to raw Patchright with minimal code changes.
5. **Indeed/Glassdoor change HTML structure** — Mitigation: Scrapling's adaptive element tracking handles minor changes. Major redesigns require selector updates.
6. **Proxy cost exceeds estimate** — Mitigation: monitor bandwidth in Bright Data dashboard. At current volume (~1-2 GB/month), even 3× growth stays under $25/month.

## References

- [Patchright GitHub](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)
- [Scrapling Docs](https://scrapling.readthedocs.io)
- [Scrapling vs Patchright comparison](https://kahtaf.com/blog/browser-automation-compared/)
- [LinkedIn Scraping in 2026 — Scrapfly](https://scrapfly.io/blog/posts/how-to-scrape-linkedin)
- [Indeed Scraping in 2026 — Scrapfly](https://scrapfly.io/blog/posts/how-to-scrape-indeedcom)
- [Glassdoor Scraping in 2026 — Scrapfly](https://scrapfly.io/blog/posts/how-to-scrape-glassdoor)
- [OpenClaw Auto-Apply Guide](https://www.autoapplier.com/blog/openclaw)
- [OpenClaw × Scrapling Controversy](https://wilico.co.jp/en/blog/openclaw-scrapling-bypass-tools-latest-tactics)
- [Fargate Spot + Step Functions Patterns](https://github.com/toricls/aws-fargate-with-step-functions)
- [Bright Data Playwright Integration](https://docs.brightdata.com/integrations/playwright)
