# Phase 2E: n8n Migration — Design Specification

**Date**: 2026-03-31 (last updated: 2026-03-31 15:30)
**Status**: Approved
**Depends on**: Phase 2A (complete)
**Unblocks**: Phase 2B (editor gets EC2 compilation), Phase 2D (enrichment runs as n8n workflow), Phase 2G (observability)

---

## 1. Why n8n, Why Now

The GitHub Actions pipeline is the #1 pain point:
- **Timeout kills**: 90-minute limit causes cancelled runs (last 4 runs all cancelled)
- **Sequential execution**: scrapers run one-by-one; one slow scraper blocks everything
- **Cost**: burning GH Actions minutes on contact finding, Playwright installs, retries
- **No Indeed/Glassdoor**: bot-detection sites need managed browsers (Apify), but Apify calls eat the timeout
- **No visibility**: pipeline health is invisible — you only know it failed when jobs stop appearing
- **No retry logic**: a transient API failure kills the entire run

n8n fixes all of this: unlimited execution time, parallel branches, visual debugging, built-in retry, webhook triggers, and a self-hosted EC2 that also serves as a tectonic compilation server for the Phase 2B editor.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────┐
│  EC2 t3.small (Docker Compose)                   │
│                                                   │
│  ┌───────────┐  ┌───────────┐  ┌──────────────┐ │
│  │   n8n     │  │ PostgreSQL│  │  tectonic     │ │
│  │  :5678    │  │  :5432    │  │  HTTP sidecar │ │
│  │           │  │ (n8n DB)  │  │  :8081        │ │
│  └───────────┘  └───────────┘  └──────────────┘ │
│                                                   │
│  Caddy reverse proxy (optional, for SSL later)    │
└─────────────────────────────────────────────────┘
         │                           │
         │ HTTP Request nodes        │ POST /compile
         ▼                           ▼
  ┌──────────────┐           ┌──────────────┐
  │ Lambda API   │           │ React Frontend│
  │ (AI logic)   │           │ (Netlify)     │
  └──────────────┘           └──────────────┘
```

### What Lives Where

| Component | Location | Why |
|-----------|----------|-----|
| **Orchestration** (scheduling, fan-out, retries) | n8n on EC2 | Visual debugging, no timeout limits |
| **API-based scrapers** (Adzuna, JSearch, GradIreland, YC, HN, IrishJobs, Jobs.ie) | n8n HTTP Request nodes | Direct HTTP, no Python needed |
| **Bot-detection scrapers** (Indeed, LinkedIn, Glassdoor) | JSearch (primary) → Apify (fallback) via n8n | Three-tier failover |
| **AI matching/scoring** | Lambda (existing) | Complex prompt logic, council pattern |
| **Resume tailoring** | Lambda (existing) | LaTeX generation + council |
| **Cover letter generation** | Lambda (existing) | Council-driven, LaTeX output |
| **LaTeX compilation** | tectonic sidecar on EC2 | Sub-second, no cold starts |
| **Contact finding** | Lambda (existing) | AI + Apify Google search |
| **Self-improvement** | n8n (analysis) + Lambda (AI) | n8n stores/reads adjustments |
| **Email notifications** | n8n Gmail node | Simpler than Python SMTP |
| **Pipeline DB** | Supabase (existing) | Shared with frontend |

### Key Principle: n8n Orchestrates, Lambda Computes

n8n **replaces** the Python scrapers and pipeline orchestrator entirely:
- `scrapers/` directory → dead code after cutover (replaced by n8n HTTP Request nodes)
- `main.py` pipeline → dead code (replaced by n8n workflow)
- `seen_jobs.json` → removed (dedup happens against Supabase `jobs` table directly)

n8n handles: scheduling, fan-out, retries, data routing, notifications, scraping.
Lambda handles: AI calls, LaTeX compilation, scoring — the compute-heavy stuff.

n8n calls Lambda endpoints via HTTP Request nodes. This means:
- No Python scraper code runs anywhere — n8n calls APIs directly
- Lambda endpoints stay unchanged
- Frontend continues calling the same API
- The only data store is Supabase (no local JSON files)

---

## 3. n8n Cloud Setup

**Workspace**: https://naukribaba.app.n8n.cloud (already provisioned)

No self-hosted EC2 needed — n8n Cloud handles hosting, SSL, updates, and backups. This eliminates Docker Compose, EC2 provisioning, and security group management.

### What n8n Cloud provides:
- Hosted n8n instance with SSL
- Built-in nodes (HTTP Request, Gmail, Schedule, Webhook, Merge, IF, Code, etc.)
- Community node installation via UI
- REST API access for MCP integration
- Webhook URLs (auto-generated, HTTPS)

### LaTeX Compilation
Since there's no EC2 to host the tectonic sidecar, LaTeX compilation stays on Lambda via the new `POST /api/compile-latex` endpoint. Add a CloudWatch warmer (~$2-3/mo) in Phase 2B to eliminate cold starts for the editor use case. For the pipeline, cold starts are acceptable since compilation isn't interactive.

### Cost
- **n8n Cloud free tier**: 5 active workflows, 50 executions/day — sufficient for daily pipeline + nudges + reminders
- **EC2**: $0 (not needed)
- **CloudWatch warmer** (Phase 2B): ~$2-3/mo
- **Total incremental**: ~$0/mo (free tier)

---

## 4. n8n Workflows

### 4.1 Daily Pipeline Workflow

**Trigger**: Schedule Trigger (weekdays 7:00 UTC) + Webhook (manual "Run Now" from frontend)

```
Schedule Trigger (7:00 UTC weekday)
        │
        ▼
┌── Fan-Out (parallel branches) ──────────────────────┐
│                                                       │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌───────────┐ │
│  │ Adzuna  │ │ JSearch  │ │LinkedIn │ │ HN/YC/    │ │
│  │ HTTP    │ │ HTTP    │ │ Apify   │ │ GradIrl   │ │
│  └────┬────┘ └────┬────┘ └────┬────┘ └─────┬─────┘ │
│       │           │           │             │        │
└───────┴───────────┴───────────┴─────────────┘        │
        │                                               │
        ▼                                               │
   Merge Results (aggregate all scraped jobs)           │
        │                                               │
        ▼                                               │
   Deduplicate (call Lambda POST /api/deduplicate)      │
        │                                               │
        ▼                                               │
   Match & Score (call Lambda POST /api/score-batch)    │
        │                                               │
        ▼                                               │
   Filter (match_score >= threshold)                    │
        │                                               │
        ▼                                               │
┌── Fan-Out (per matched job) ──────────────────┐      │
│  Tailor Resume (Lambda POST /api/tailor)       │      │
│  Compile PDF (EC2 POST /compile)               │      │
│  Upload S3                                     │      │
│  Cover Letter (Lambda POST /api/cover-letter)  │      │
│  Compile CL PDF (EC2 POST /compile)            │      │
│  Upload CL S3                                  │      │
│  Save to Supabase                              │      │
└────────────────────────────────────────────────┘      │
        │                                               │
        ▼                                               │
   Email Summary (Gmail node)                           │
        │                                               │
        ▼                                               │
   Self-Improvement Analysis                            │
```

### 4.2 Scraper Nodes Detail

**Adzuna** (HTTP Request node):
- GET `https://api.adzuna.com/v1/api/jobs/ie/search/1?app_id=...&app_key=...&what=<query>&max_days_old=3`
- Parse JSON response → extract jobs array
- No authentication beyond API keys

**JSearch / RapidAPI** (HTTP Request node):
- GET `https://jsearch.p.rapidapi.com/search?query=<query>&page=1&num_pages=1&date_posted=today`
- Header: `X-RapidAPI-Key: <key>`
- Covers Indeed, LinkedIn, ZipRecruiter, Glassdoor in one API

**LinkedIn / Indeed / Glassdoor** (three-tier failover):
1. **Primary — JSearch** (HTTP Request node): `GET jsearch.p.rapidapi.com/search` — single API covers Indeed, LinkedIn, ZipRecruiter, Glassdoor. Free 500 req/mo.
2. **Fallback — Apify** (HTTP Request node): `POST api.apify.com/v2/acts/apify~linkedin-jobs-scraper/runs` — managed browsers for bot-detection sites. Free $5/mo credit.
3. n8n's "Continue On Fail" routes to the next tier automatically.

**IrishJobs** (HTTP Request node):
- GET with search params, parse HTML response
- Previously timed out on GH Actions — no timeout limit in n8n

**Jobs.ie** (HTTP Request node):
- GET with search params, parse HTML response
- Previously timed out on GH Actions — no timeout limit in n8n

**HN Hiring** (HTTP Request node):
- GET Algolia HN API for latest "Who is hiring?" thread
- Parse comment bodies for job postings
- Extract company, title, description, location from comment text

**YC Jobs** (HTTP Request node):
- GET `https://www.workatastartup.com/companies` with Inertia.js headers
- Parse SSR JSON response

**GradIreland** (HTTP Request node):
- GET with search params, parse HTML response

### 4.3 Lambda API Endpoints (new/modified)

New endpoints needed for n8n integration:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /api/score-batch` | New | Score a batch of jobs (replaces `matcher.py` CLI call) |
| `POST /api/deduplicate` | New | Deduplicate a list of jobs against existing DB |
| `POST /api/tailor` | Exists | Tailor resume for a job (already works) |
| `POST /api/cover-letter` | Exists | Generate cover letter (already works) |
| `POST /api/contacts` | Exists | Find LinkedIn contacts (already works) |
| `POST /api/compile-latex` | New | Compile LaTeX → PDF (for editor, Phase 2B) |
| `POST /api/self-improve` | New | Run self-improvement analysis |
| `GET /api/pipeline/status` | New | Last run status, scraper health, next run time |
| `PATCH /api/dashboard/jobs/{job_id}` | Modified | Extended to support editing location, apply_url (not just status) |

### 4.8 n8n Community Nodes

Install these community/built-in nodes on the EC2 n8n instance:

| Node | Purpose | Type |
|------|---------|------|
| HTTP Request | All scraper API calls, Lambda calls | Built-in |
| Gmail | Email notifications, nudges, reminders | Built-in |
| Schedule Trigger | Daily pipeline, weekly nudges | Built-in |
| Webhook | "Run Pipeline Now" from frontend | Built-in |
| Supabase | Direct DB reads/writes (jobs, config) | Community: `n8n-nodes-supabase` |
| S3 | Upload PDFs after compilation | Community: `n8n-nodes-aws` or built-in AWS S3 |
| Merge | Combine parallel scraper results | Built-in |
| IF | Filter matched jobs, route errors | Built-in |
| SplitInBatches | Process jobs one at a time | Built-in |
| Code | Custom JS transforms (parse HTML, normalize job data) | Built-in |

Install community nodes via n8n UI: Settings → Community Nodes → Install.

### 4.9 MCP Server for n8n

A lightweight MCP server that lets Claude Code interact with n8n directly from the terminal:

**Tools exposed:**
| Tool | Purpose |
|------|---------|
| `n8n_trigger_pipeline` | Trigger the daily pipeline workflow manually |
| `n8n_get_executions` | List recent workflow executions with status |
| `n8n_get_execution` | Get details of a specific execution (nodes, errors, data) |
| `n8n_list_workflows` | List all workflows with active/inactive status |
| `n8n_toggle_workflow` | Activate/deactivate a workflow |
| `n8n_get_pipeline_status` | Get latest pipeline run results (jobs found/matched/tailored) |

**Implementation:** Python FastMCP server that wraps n8n's REST API (`http://<ec2-ip>:5678/api/v1/`). n8n's API uses API key auth (`X-N8N-API-KEY` header).

**Configuration:** Add to `.mcp.json` or Claude Code settings:
```json
{
  "mcpServers": {
    "n8n": {
      "command": "python",
      "args": ["infra/mcp_n8n_server.py"],
      "env": {
        "N8N_URL": "http://<ec2-ip>:5678",
        "N8N_API_KEY": "<api-key>"
      }
    }
  }
}
```

**Use cases:**
- "Run my pipeline" → triggers webhook
- "How did today's pipeline go?" → fetches latest execution
- "Which scrapers failed?" → parses execution node results
- "Pause the pipeline" → deactivates the schedule trigger

### 4.10 Scoring Consistency Fix

**Problem**: A job scored 90 on the dashboard can score differently when the same JD is pasted in Add Job. This is because:
- Pipeline `matcher.py` sends jobs in batches of 5 with both resumes
- Add Job `/api/score` scores a single job with one resume
- Different AI providers may win the council each time

**Fix**: Both the n8n pipeline AND the Add Job flow must use the same `POST /api/score-batch` endpoint. The existing `/api/score` endpoint in the Add Job flow should be refactored to call `score-batch` internally so the prompt, batching, and council behavior are identical.

### 4.9 Manual Job Editing

Users can edit `location` and `apply_url` on any job from the Job Workspace overview tab. The `PATCH /api/dashboard/jobs/{job_id}` endpoint now accepts any combination of `{application_status, location, apply_url}`. Frontend shows inline edit fields on the overview tab for manual jobs or any job with missing fields.

### 4.4 Self-Improvement Workflow

Runs after each pipeline completion. Analyzes the run and adjusts parameters for the next one.

**Analysis** (Lambda `POST /api/self-improve`):
- Input: today's matched jobs, scores, scraper results
- AI council analyzes:
  - Which scrapers produced the most/best matches?
  - Which search queries had high match rates?
  - What score patterns emerged? (e.g., all HN jobs score low → deprioritize)
  - What skills/keywords appeared most in high-scoring jobs?
- Output: JSON adjustments

**Adjustments stored in Supabase** (`self_improvement_config` table):
- Search query modifications (add/remove/reweight)
- Scraper weights (deprioritize low-value sources)
- Scoring threshold adjustments
- Keyword emphasis list (inform tailoring prompts)

**Applied on next run**: n8n reads the config before starting scrapers, applies weights and query modifications.

### 4.5 Stale Job Nudges Workflow

**Trigger**: Schedule Trigger (every Monday 9:00 UTC)

Queries Supabase for jobs where:
- `application_status = 'New'`
- `first_seen < NOW() - INTERVAL '7 days'`

Sends a Gmail digest: "You have X jobs discovered 7+ days ago that you haven't applied to yet — oldest is Y days old." Includes a table of top 5 stale jobs with links.

### 4.6 Follow-Up Reminders Workflow

**Trigger**: Schedule Trigger (daily 10:00 UTC)

Queries Supabase for jobs where:
- `application_status = 'Applied'`
- No update in 7+ days

Sends a Gmail reminder: "You applied to X at Y company Z days ago — consider following up." Includes LinkedIn contacts if available.

### 4.7 Pipeline Status Endpoint

New Lambda endpoint: `GET /api/pipeline/status`

Returns:
```json
{
  "last_run": "2026-04-01T07:00:00Z",
  "status": "completed",
  "jobs_found": 35,
  "jobs_matched": 12,
  "jobs_tailored": 12,
  "scraper_health": {
    "adzuna": { "status": "ok", "jobs": 8 },
    "jsearch": { "status": "ok", "jobs": 15 },
    "hn_hiring": { "status": "ok", "jobs": 5 },
    "irishjobs": { "status": "error", "error": "timeout" }
  },
  "next_run": "2026-04-02T07:00:00Z"
}
```

The dashboard's hardcoded "Pipeline active — runs daily at 7:00 UTC" bar gets replaced with live status from this endpoint.

---

## 5. Migration Plan (4 phases)

### Phase 1: Setup (Day 1)
1. Provision EC2 t3.small in eu-west-1
2. Install Docker + Docker Compose
3. Deploy n8n + PostgreSQL + tectonic via docker-compose
4. Configure n8n credentials (API keys, Supabase, Apify, Gmail)
5. Verify n8n UI accessible at `http://<ec2-ip>:5678`
6. Verify tectonic sidecar at `http://<ec2-ip>:8081/compile`

### Phase 2: Build Workflows (Days 2-4)
1. Build scraper nodes (Adzuna, JSearch, HN, YC, GradIreland, IrishJobs, Jobs.ie, LinkedIn/Apify failover)
2. Build dedup + scoring integration (calls Lambda)
3. Build tailoring fan-out (per-job: tailor → compile → upload → save)
4. Build email summary node
5. Add new Lambda endpoints (`/api/score-batch`, `/api/deduplicate`, `/api/self-improve`, `/api/pipeline/status`)
6. Build self-improvement sub-workflow
7. Build stale job nudges workflow (weekly)
8. Build follow-up reminders workflow (daily)
9. Update dashboard pipeline status bar to use live `/api/pipeline/status`

### Phase 3: Shadow Mode (Days 4-5)
1. Run n8n pipeline alongside GitHub Actions (both at 7:00 UTC)
2. Compare results: same jobs found? Same scores? Same artifacts?
3. Fix any discrepancies
4. Run for 2-3 days to build confidence

### Phase 4: Cutover (Day 6)
1. Disable GitHub Actions cron (keep workflow for manual fallback)
2. Activate n8n Schedule Trigger
3. Add "Run Pipeline Now" webhook to React frontend
4. Monitor first 3 automated runs
5. Remove `SKIP_CONTACTS` (n8n has no timeout, contacts can run)

---

## 6. New Supabase Tables

```sql
-- Self-improvement configuration
CREATE TABLE self_improvement_config (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id) NOT NULL,
  config_type TEXT NOT NULL, -- 'query_weights', 'scraper_weights', 'scoring', 'keywords'
  config_data JSONB NOT NULL,
  applied_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, config_type)
);

-- Pipeline run metrics (replaces pipeline_tasks for richer tracking)
CREATE TABLE pipeline_metrics (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id) NOT NULL,
  run_date DATE NOT NULL,
  scraper_name TEXT NOT NULL,
  jobs_found INT DEFAULT 0,
  jobs_matched INT DEFAULT 0,
  jobs_tailored INT DEFAULT 0,
  duration_seconds INT,
  error_message TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 7. Cost Estimate

| Component | Monthly Cost |
|-----------|-------------|
| n8n Cloud (free tier) | $0 |
| Lambda (existing) | ~$1 |
| S3 (existing) | ~$0.50 |
| **Total incremental** | **~$0/mo** |

If n8n Cloud free tier limits are hit (50 executions/day), upgrade to Starter (~$20/mo) or migrate to self-hosted EC2 (~$20/mo).

---

## 8. Security

- n8n Cloud handles auth (email/password login, n8n manages SSL)
- All API keys stored as n8n credentials (encrypted at rest by n8n Cloud)
- Supabase service key stored as n8n credential (not in workflow JSON)
- n8n API key for MCP server: store in local `.env`, don't commit
- Webhook URLs are HTTPS (n8n Cloud provides SSL)

---

## 9. Rollback Plan

If n8n fails after cutover:
1. Re-enable GitHub Actions cron (`0 7 * * 1-5`)
2. Push to main to trigger deploy
3. Pipeline runs on GH Actions as before
4. Debug n8n issues without time pressure

The GitHub Actions workflow stays in the repo as a cold backup. Never delete it.

---

## 10. Future: Multi-Tenant via Kubernetes (NOT in scope)

The long-term vision is that every NaukriBaba user gets their own n8n automations. This phase builds for one user only, but makes choices that don't block migration to Kubernetes:

**Architectural choices that enable K8s migration:**
- All DB queries scoped by `user_id` (already the case)
- Per-user config in Supabase `self_improvement_config` (not hardcoded)
- n8n workflows parameterized — no hardcoded API keys or user IDs in workflow JSON
- tectonic sidecar is stateless (shared across users, no per-user state)
- Docker Compose services map 1:1 to K8s pods (n8n, postgres, tectonic)
- Environment variables for all config (easy to template as K8s ConfigMaps/Secrets)
- No local file storage — everything in Supabase or S3 (stateless containers)

**K8s migration path:**
1. Docker Compose → Helm chart (each service becomes a Deployment + Service)
2. Per-user n8n: each user gets an n8n Deployment with their credentials injected via Secrets
3. Shared services: tectonic sidecar + PostgreSQL (or RDS) shared across users
4. Ingress controller routes `<user-slug>.n8n.naukribaba.com` to the right pod
5. Horizontal scaling: add users = add n8n pods, no infra redesign

---

## 11. Success Criteria

- [ ] Pipeline runs daily without cancellation
- [ ] Scraping completes in < 30 minutes (parallel branches)
- [ ] Indeed + LinkedIn jobs appear via JSearch/Apify
- [ ] Self-improvement adjustments visible in Supabase config table
- [ ] Email summary sent after each run
- [ ] tectonic sidecar compiles LaTeX in < 2 seconds
- [ ] "Run Pipeline Now" button works from React frontend
- [ ] Shadow mode shows parity with GitHub Actions results
