# Phase 2E: n8n Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the GitHub Actions pipeline with n8n on EC2 — parallel scrapers, self-improvement, email workflows, and a tectonic compilation sidecar.

**Architecture:** n8n Cloud (https://naukribaba.app.n8n.cloud) orchestrates (scheduling, fan-out, retries, scraping via HTTP nodes), Lambda computes (AI scoring, tailoring, contacts). No EC2 needed — n8n Cloud handles hosting. Python scrapers and `main.py` become dead code after cutover.

**Tech Stack:** n8n (workflow engine), Docker Compose, EC2 t3.small, tectonic (LaTeX), FastAPI (Lambda), Supabase (PostgreSQL), S3

**Spec:** `docs/superpowers/specs/2026-03-31-phase2e-n8n-migration-design.md`

**Scoring fix note:** The new `/api/score-batch` endpoint must be used by BOTH the n8n pipeline AND the existing `/api/tailor` Add Job flow so scores are consistent. Currently the pipeline's `matcher.py` and the API's `/api/score` use different prompts/batching, causing the same JD to score differently.

---

## File Structure

### New Files
| File | Purpose |
|------|---------|
| `web/src/components/SkillTags.jsx` | Tech skill chip display extracted from JD |
| `web/src/components/JobCard.jsx` | Card grid view component |
| `web/src/components/PipelineStatus.jsx` | Live pipeline status bar |

### Modified Files
| File | Changes |
|------|---------|
| `app.py` | Add 5 new endpoints: `/api/score-batch`, `/api/deduplicate`, `/api/self-improve`, `/api/pipeline/status`, `/api/compile-latex` |
| `db_client.py` | Add methods: `get_pipeline_metrics()`, `save_pipeline_metrics()`, `get_self_improvement_config()`, `save_self_improvement_config()`, `deduplicate_jobs()` |
| `web/src/pages/Dashboard.jsx` | Replace hardcoded pipeline status, add card/table view toggle |
| `web/src/components/PipelineStatus.jsx` | New component for live pipeline status bar |
| `web/src/components/JobCard.jsx` | New component for card grid view |
| `web/src/components/SkillTags.jsx` | New component for tech skill chip display |
| `web/src/pages/JobWorkspace.jsx` | Add inline PDF preview, skills tags, editable fields |
| `web/src/pages/Settings.jsx` | Auto-populate profile from uploaded resume |
| `template.yaml` | Add new Lambda timeout/memory if needed |

### n8n Workflows (configured in n8n UI, exported as JSON)
| Workflow | Purpose |
|----------|---------|
| `Daily Pipeline` | Schedule trigger → parallel scrapers → dedup → score → tailor → email |
| `Stale Job Nudges` | Weekly Monday check for unactioned jobs → Gmail |
| `Follow-Up Reminders` | Daily check for Applied jobs without updates → Gmail |

---

## Task 1: Supabase Tables

**Files:**
- Modify: Supabase SQL editor (no local file — run via Supabase dashboard or CLI)

- [ ] **Step 1: Create self_improvement_config table**

Run in Supabase SQL editor:

```sql
CREATE TABLE IF NOT EXISTS self_improvement_config (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID REFERENCES users(id) NOT NULL,
  config_type TEXT NOT NULL,
  config_data JSONB NOT NULL,
  applied_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, config_type)
);

ALTER TABLE self_improvement_config ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users can read own config" ON self_improvement_config FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Service role full access" ON self_improvement_config FOR ALL USING (auth.role() = 'service_role');
```

- [ ] **Step 2: Create pipeline_metrics table**

```sql
CREATE TABLE IF NOT EXISTS pipeline_metrics (
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

ALTER TABLE pipeline_metrics ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users can read own metrics" ON pipeline_metrics FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Service role full access" ON pipeline_metrics FOR ALL USING (auth.role() = 'service_role');
```

- [ ] **Step 3: Verify tables exist**

```sql
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' AND table_name IN ('self_improvement_config', 'pipeline_metrics');
```

Expected: 2 rows.

- [ ] **Step 4: Commit migration notes**

```bash
git add -A && git commit -m "docs: add Phase 2E Supabase migration SQL"
```

---

## Task 2: Database Client Methods

**Files:**
- Modify: `db_client.py`
- Test: manual verification via Python REPL

- [ ] **Step 1: Add pipeline_metrics methods to db_client.py**

Add after the existing `get_job_stats` method in `db_client.py`:

```python
def save_pipeline_metrics(self, user_id: str, run_date: str, scraper_name: str,
                          jobs_found: int = 0, jobs_matched: int = 0,
                          jobs_tailored: int = 0, duration_seconds: int = 0,
                          error_message: str = None) -> Dict[str, Any]:
    """Record metrics for a single scraper in a pipeline run."""
    data = {
        "user_id": user_id,
        "run_date": run_date,
        "scraper_name": scraper_name,
        "jobs_found": jobs_found,
        "jobs_matched": jobs_matched,
        "jobs_tailored": jobs_tailored,
        "duration_seconds": duration_seconds,
    }
    if error_message:
        data["error_message"] = error_message
    result = self.client.table("pipeline_metrics").insert(data).execute()
    return result.data[0] if result.data else {}

def get_pipeline_status(self, user_id: str) -> Dict[str, Any]:
    """Get latest pipeline run status and scraper health for a user."""
    # Get the most recent run date
    latest = (
        self.client.table("pipeline_metrics")
        .select("run_date")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not latest.data:
        return {"last_run": None, "status": "never_run", "scraper_health": {}}

    run_date = latest.data[0]["run_date"]

    # Get all scraper metrics for that run
    metrics = (
        self.client.table("pipeline_metrics")
        .select("*")
        .eq("user_id", user_id)
        .eq("run_date", run_date)
        .execute()
    )

    scraper_health = {}
    total_found = total_matched = total_tailored = 0
    has_error = False
    for m in (metrics.data or []):
        name = m["scraper_name"]
        if m.get("error_message"):
            scraper_health[name] = {"status": "error", "error": m["error_message"]}
            has_error = True
        else:
            scraper_health[name] = {"status": "ok", "jobs": m["jobs_found"]}
        total_found += m.get("jobs_found", 0)
        total_matched += m.get("jobs_matched", 0)
        total_tailored += m.get("jobs_tailored", 0)

    return {
        "last_run": run_date,
        "status": "completed_with_errors" if has_error else "completed",
        "jobs_found": total_found,
        "jobs_matched": total_matched,
        "jobs_tailored": total_tailored,
        "scraper_health": scraper_health,
    }
```

- [ ] **Step 2: Add self-improvement config methods**

```python
def get_self_improvement_config(self, user_id: str) -> Dict[str, Any]:
    """Get all self-improvement configs for a user, keyed by config_type."""
    result = (
        self.client.table("self_improvement_config")
        .select("config_type,config_data,applied_at")
        .eq("user_id", user_id)
        .execute()
    )
    return {r["config_type"]: r["config_data"] for r in (result.data or [])}

def save_self_improvement_config(self, user_id: str, config_type: str,
                                  config_data: dict) -> Dict[str, Any]:
    """Upsert a self-improvement config entry."""
    from datetime import datetime
    data = {
        "user_id": user_id,
        "config_type": config_type,
        "config_data": config_data,
        "applied_at": datetime.utcnow().isoformat(),
    }
    result = (
        self.client.table("self_improvement_config")
        .upsert(data, on_conflict="user_id,config_type")
        .execute()
    )
    return result.data[0] if result.data else {}
```

- [ ] **Step 3: Add deduplicate_jobs method**

```python
def deduplicate_jobs(self, user_id: str, jobs: List[Dict]) -> List[Dict]:
    """Filter out jobs that already exist in the database.
    
    Compares by company+title similarity against existing jobs.
    Returns only the new (non-duplicate) jobs.
    """
    from difflib import SequenceMatcher
    
    existing = (
        self.client.table("jobs")
        .select("title,company,description")
        .eq("user_id", user_id)
        .execute()
    )
    existing_jobs = existing.data or []
    
    new_jobs = []
    for job in jobs:
        is_dupe = False
        j_title = (job.get("title") or "").lower().strip()
        j_company = (job.get("company") or "").lower().strip()
        j_desc = (job.get("description") or "").lower().strip()
        
        for ej in existing_jobs:
            e_company = (ej.get("company") or "").lower().strip()
            if not e_company or not j_company:
                continue
            company_sim = SequenceMatcher(None, j_company, e_company).ratio()
            if company_sim > 0.80:
                e_title = (ej.get("title") or "").lower().strip()
                title_sim = SequenceMatcher(None, j_title, e_title).ratio()
                if title_sim > 0.85:
                    # Same company+title — check if JD is also similar
                    e_desc = (ej.get("description") or "").lower().strip()
                    if j_desc and e_desc:
                        desc_sim = SequenceMatcher(None, j_desc[:500], e_desc[:500]).ratio()
                        if desc_sim > 0.60:
                            is_dupe = True
                            break
                    else:
                        is_dupe = True
                        break
        
        if not is_dupe:
            new_jobs.append(job)
    
    logger.info(f"[DEDUP] {len(jobs)} input → {len(new_jobs)} new, {len(jobs) - len(new_jobs)} duplicates")
    return new_jobs
```

- [ ] **Step 4: Verify methods work**

```bash
python3 -c "
import os
for line in open('.env'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k,_,v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip().strip('\"'))
from db_client import SupabaseClient
db = SupabaseClient.from_env()
uid = '7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39'
print('Pipeline status:', db.get_pipeline_status(uid))
print('Self-improvement config:', db.get_self_improvement_config(uid))
print('Dedup test:', len(db.deduplicate_jobs(uid, [{'title':'Test','company':'Test'}])))
"
```

- [ ] **Step 5: Commit**

```bash
git add db_client.py && git commit -m "feat: add pipeline metrics, self-improvement config, and dedup methods to db_client"
```

---

## Task 3: New Lambda API Endpoints

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Add POST /api/score-batch endpoint**

This replaces `matcher.py` CLI usage. n8n sends a batch of raw jobs, Lambda returns scored results. Add to `app.py` after the existing `/api/score` endpoint:

```python
@app.post("/api/score-batch")
def score_batch(body: dict, user: AuthUser = Depends(get_current_user)):
    """Score a batch of jobs against the user's resumes.
    
    Used by n8n pipeline AND the Add Job flow for consistent scoring.
    Body: { "jobs": [{"title", "company", "description", "location", ...}] }
    Returns: { "scored_jobs": [{"title", "company", ..., "match_score", "ats_score", ...}] }
    """
    if _db is None:
        raise HTTPException(503, "Database not configured")
    
    raw_jobs = body.get("jobs", [])
    if not raw_jobs:
        raise HTTPException(400, "jobs array required")
    
    # Load user's resumes
    resumes_data = _db.get_resumes(user.id)
    if not resumes_data:
        raise HTTPException(400, "No resumes uploaded. Upload a resume first.")
    
    resumes = {}
    for r in resumes_data:
        resumes[r.get("resume_key", r.get("id", "default"))] = r.get("parsed_text", "")
    
    # Convert to Job objects
    from scrapers.base import Job
    job_objects = []
    for j in raw_jobs:
        job = Job(
            title=j.get("title", ""),
            company=j.get("company", ""),
            description=j.get("description", ""),
            location=j.get("location", ""),
            apply_url=j.get("apply_url", ""),
            source=j.get("source", "pipeline"),
        )
        job_objects.append(job)
    
    # Score using matcher
    from matcher import match_jobs
    ai = _get_ai_client()
    min_score = body.get("min_score", 60)
    matched = match_jobs(job_objects, resumes, ai, min_score=min_score)
    
    # Return all jobs with scores (not just matched ones)
    scored = []
    for job in job_objects:
        scored.append({
            "title": job.title,
            "company": job.company,
            "description": job.description,
            "location": job.location,
            "apply_url": job.apply_url,
            "source": job.source,
            "match_score": getattr(job, "match_score", 0) or 0,
            "ats_score": getattr(job, "ats_score", 0) or 0,
            "hiring_manager_score": getattr(job, "hiring_manager_score", 0) or 0,
            "tech_recruiter_score": getattr(job, "tech_recruiter_score", 0) or 0,
            "matched_resume": getattr(job, "matched_resume", ""),
            "match_reasoning": getattr(job, "match_reasoning", ""),
        })
    
    return {"scored_jobs": scored, "matched_count": len(matched), "total_count": len(job_objects)}
```

- [ ] **Step 2: Add POST /api/deduplicate endpoint**

```python
@app.post("/api/deduplicate")
def deduplicate_jobs_endpoint(body: dict, user: AuthUser = Depends(get_current_user)):
    """Deduplicate a list of scraped jobs against the existing database.
    
    Body: { "jobs": [{"title", "company", "description", ...}] }
    Returns: { "new_jobs": [...], "duplicates_removed": N }
    """
    if _db is None:
        raise HTTPException(503, "Database not configured")
    
    raw_jobs = body.get("jobs", [])
    new_jobs = _db.deduplicate_jobs(user.id, raw_jobs)
    
    return {
        "new_jobs": new_jobs,
        "duplicates_removed": len(raw_jobs) - len(new_jobs),
        "total_input": len(raw_jobs),
    }
```

- [ ] **Step 3: Add POST /api/self-improve endpoint**

```python
@app.post("/api/self-improve")
def self_improve(body: dict, user: AuthUser = Depends(get_current_user)):
    """Analyze pipeline run results and generate improvement recommendations.
    
    Body: { "run_date": "2026-04-01", "metrics": [...] }
    Returns: { "adjustments": {...} }
    """
    if _db is None:
        raise HTTPException(503, "Database not configured")
    
    ai = _get_ai_client()
    run_date = body.get("run_date", "")
    
    # Get recent job data for analysis
    recent_jobs = _db.client.table("jobs").select(
        "source,match_score,ats_score,hiring_manager_score,tech_recruiter_score,title,company"
    ).eq("user_id", user.id).gte("first_seen", run_date).execute()
    
    # Get current config
    current_config = _db.get_self_improvement_config(user.id)
    
    # AI analysis
    prompt = f"""Analyze these pipeline results and suggest improvements for the next run.

JOBS FOUND TODAY ({len(recent_jobs.data or [])} jobs):
{json.dumps(recent_jobs.data or [], indent=2)[:3000]}

CURRENT CONFIG:
{json.dumps(current_config, indent=2)}

Suggest adjustments as JSON:
{{
  "query_weights": {{"<query>": <weight 0-1>, ...}},
  "scraper_weights": {{"<scraper>": <weight 0-1>, ...}},
  "scoring_threshold": <int>,
  "keyword_emphasis": ["<keyword>", ...]
}}

Focus on: which scrapers produced the best matches, which queries had high match rates, what skills/keywords appeared most in high-scoring jobs."""

    import json
    info = ai.complete_with_info(prompt=prompt, system="You are a pipeline optimization analyst. Return only valid JSON.", temperature=0.3)
    
    try:
        from matcher import extract_json
        adjustments = extract_json(info["response"])
    except Exception:
        adjustments = {}
    
    # Save adjustments to Supabase
    for config_type, config_data in adjustments.items():
        if isinstance(config_data, (dict, list)):
            _db.save_self_improvement_config(user.id, config_type, config_data if isinstance(config_data, dict) else {"values": config_data})
    
    return {"adjustments": adjustments, "model": info.get("model", "")}
```

- [ ] **Step 4: Add GET /api/pipeline/status endpoint**

```python
@app.get("/api/pipeline/status")
def get_pipeline_status(user: AuthUser = Depends(get_current_user)):
    """Get latest pipeline run status and scraper health."""
    if _db is None:
        return {"last_run": None, "status": "not_configured"}
    
    return _db.get_pipeline_status(user.id)
```

- [ ] **Step 5: Add POST /api/compile-latex endpoint**

```python
@app.post("/api/compile-latex")
def compile_latex(body: dict, user: AuthUser = Depends(get_current_user)):
    """Compile LaTeX source to PDF. Returns the PDF as binary.
    
    Body: { "tex_source": "\\documentclass..." }
    Returns: PDF binary (application/pdf)
    """
    from fastapi.responses import Response
    import tempfile
    
    tex_source = body.get("tex_source", "")
    if not tex_source:
        raise HTTPException(400, "tex_source required")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = Path(tmpdir) / "document.tex"
        tex_path.write_text(tex_source, encoding="utf-8")
        
        from latex_compiler import compile_tex_to_pdf
        pdf_path = compile_tex_to_pdf(str(tex_path), tmpdir)
        
        if not pdf_path or not Path(pdf_path).exists():
            raise HTTPException(500, "LaTeX compilation failed")
        
        pdf_bytes = Path(pdf_path).read_bytes()
        return Response(content=pdf_bytes, media_type="application/pdf")
```

- [ ] **Step 6: Update app.py docstring**

Add the new endpoints to the docstring at the top of `app.py`.

- [ ] **Step 7: Test endpoints locally**

```bash
uvicorn app:app --reload --port 8000
# In another terminal:
curl -X POST http://localhost:8000/api/compile-latex \
  -H "Content-Type: application/json" \
  -d '{"tex_source": "\\documentclass{article}\\begin{document}Hello\\end{document}"}' \
  -o test.pdf
```

- [ ] **Step 8: Commit**

```bash
git add app.py && git commit -m "feat: add score-batch, deduplicate, self-improve, pipeline-status, compile-latex API endpoints"
```

---

## Task 4: Build n8n Workflows

This task is done in the n8n UI at `http://<ec2-ip>:5678`. Export workflows as JSON and save to `infra/workflows/` for version control.

**Files:**
- Create: `infra/workflows/daily-pipeline.json` (exported from n8n)
- Create: `infra/workflows/stale-nudges.json`
- Create: `infra/workflows/follow-up-reminders.json`

- [ ] **Step 1: Configure n8n credentials**

In n8n UI → Settings → Credentials, add:
- **Supabase**: URL + service key (for direct Supabase REST API calls)
- **Lambda API**: base URL of your Lambda (e.g., `https://xxx.execute-api.eu-west-1.amazonaws.com/Prod`)
- **Adzuna**: app_id + app_key
- **RapidAPI (JSearch)**: API key
- **Apify**: API key
- **Gmail**: OAuth2 or App Password

- [ ] **Step 2: Build Daily Pipeline workflow**

In n8n UI, create the workflow following the flow chart in the spec:

1. **Schedule Trigger**: weekdays 7:00 UTC
2. **Webhook Trigger**: for manual "Run Now" (alternative trigger)
3. **Parallel branches** (use n8n's "Execute Workflow" or parallel paths):
   - Adzuna HTTP Request → parse JSON → normalize to `{title, company, description, location, apply_url, source}`
   - JSearch HTTP Request → parse → normalize
   - HN Hiring HTTP Request (Algolia API) → parse comments → normalize
   - YC Jobs HTTP Request → parse Inertia.js → normalize
   - GradIreland HTTP Request → parse HTML → normalize
   - IrishJobs HTTP Request → parse HTML → normalize
   - Jobs.ie HTTP Request → parse HTML → normalize
4. **Merge node**: combine all scraper results into one array
5. **HTTP Request**: POST to Lambda `/api/deduplicate` with merged jobs
6. **HTTP Request**: POST to Lambda `/api/score-batch` with deduplicated jobs
7. **IF node**: filter where `match_score >= 60`
8. **SplitInBatches**: process matched jobs one at a time
9. For each job:
   - HTTP Request: POST to Lambda `/api/tailor`
   - Save job to Supabase via REST API
10. **Gmail node**: send summary email with results table
11. **HTTP Request**: POST to Lambda `/api/self-improve` with run metrics
12. **Supabase REST**: save pipeline_metrics entries

Each scraper node should have **"Continue On Fail"** enabled and a **retry** of 2 attempts with 30s wait.

- [ ] **Step 3: Build Stale Job Nudges workflow**

1. **Schedule Trigger**: Monday 9:00 UTC
2. **Supabase REST**: GET jobs where `application_status=New` AND `first_seen < 7 days ago`
3. **IF node**: if count > 0
4. **Gmail node**: send digest email with stale jobs table

- [ ] **Step 4: Build Follow-Up Reminders workflow**

1. **Schedule Trigger**: daily 10:00 UTC
2. **Supabase REST**: GET jobs where `application_status=Applied` AND last update > 7 days ago
3. **IF node**: if count > 0
4. **Gmail node**: send reminder with job list and contacts

- [ ] **Step 5: Export workflows as JSON**

In n8n UI → each workflow → ... menu → Download → save to `infra/workflows/`

- [ ] **Step 6: Commit workflow exports**

```bash
mkdir -p infra/workflows
# Copy exported JSONs to infra/workflows/
git add infra/workflows/ && git commit -m "feat: add n8n workflow exports (daily pipeline, nudges, reminders)"
```

---

## Task 7: Frontend — Live Pipeline Status

**Files:**
- Create: `web/src/components/PipelineStatus.jsx`
- Modify: `web/src/pages/Dashboard.jsx`

- [ ] **Step 1: Create PipelineStatus component**

Create `web/src/components/PipelineStatus.jsx`:

```jsx
import { useState, useEffect } from 'react';
import { apiGet } from '../api';

export default function PipelineStatus() {
  const [status, setStatus] = useState(null);

  useEffect(() => {
    async function fetch() {
      try {
        const data = await apiGet('/api/pipeline/status');
        setStatus(data);
      } catch {
        setStatus(null);
      }
    }
    fetch();
  }, []);

  if (!status || !status.last_run) {
    return (
      <div className="bg-stone-100 border-2 border-stone-300 px-4 py-2.5 mb-6 flex items-center gap-2">
        <span className="inline-block w-2 h-2 bg-stone-400 rounded-full" />
        <span className="text-sm font-medium text-stone-500">
          Pipeline not yet configured
        </span>
      </div>
    );
  }

  const isHealthy = status.status === 'completed';
  const hasErrors = status.status === 'completed_with_errors';
  const lastRun = new Date(status.last_run).toLocaleDateString('en-IE', {
    day: 'numeric', month: 'short', year: 'numeric',
  });

  return (
    <div className={`border-2 px-4 py-2.5 mb-6 flex items-center justify-between ${
      isHealthy ? 'bg-success-light border-success' :
      hasErrors ? 'bg-yellow-light border-yellow-dark' :
      'bg-error-light border-error'
    }`}>
      <div className="flex items-center gap-2">
        <span className={`inline-block w-2 h-2 rounded-full ${
          isHealthy ? 'bg-success animate-pulse' :
          hasErrors ? 'bg-yellow-dark' :
          'bg-error'
        }`} />
        <span className={`text-sm font-medium ${
          isHealthy ? 'text-success' : hasErrors ? 'text-yellow-dark' : 'text-error'
        }`}>
          Last run: {lastRun} — {status.jobs_found} found, {status.jobs_matched} matched, {status.jobs_tailored} tailored
        </span>
      </div>
      <div className="flex items-center gap-1">
        {Object.entries(status.scraper_health || {}).map(([name, health]) => (
          <span
            key={name}
            className={`text-[9px] font-mono font-bold px-1.5 py-0.5 border ${
              health.status === 'ok'
                ? 'border-success text-success'
                : 'border-error text-error'
            }`}
            title={health.error || `${health.jobs} jobs`}
          >
            {name}
          </span>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Replace hardcoded status bar in Dashboard**

In `web/src/pages/Dashboard.jsx`, replace:

```jsx
{/* Pipeline status bar */}
<div className="bg-success-light border-2 border-success px-4 py-2.5 mb-6 flex items-center gap-2">
  <span className="inline-block w-2 h-2 bg-success rounded-full animate-pulse" />
  <span className="text-sm font-medium text-success">
    Pipeline active — runs daily at 7:00 UTC
  </span>
</div>
```

With:

```jsx
<PipelineStatus />
```

Add the import at the top: `import PipelineStatus from '../components/PipelineStatus';`

- [ ] **Step 3: Build and verify**

```bash
cd web && npm run build
```

Expected: Build passes with no errors.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/PipelineStatus.jsx web/src/pages/Dashboard.jsx
git commit -m "feat: replace hardcoded pipeline status with live API-driven component"
```

---

## Task 8: Shadow Mode & Testing

- [ ] **Step 1: Run n8n pipeline manually**

In n8n UI, click "Execute Workflow" on the Daily Pipeline. Watch each node execute. Check:
- All scrapers return results (or gracefully fail)
- Deduplicate removes known duplicates
- Scoring returns valid scores
- At least one job gets tailored

- [ ] **Step 2: Compare with GitHub Actions results**

```bash
# Check new jobs in Supabase from today
python3 -c "
import os
for line in open('.env'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k,_,v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip().strip('\"'))
from db_client import SupabaseClient
db = SupabaseClient.from_env()
uid = '7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39'
from datetime import date
today = date.today().isoformat()
r = db.client.table('jobs').select('title,company,source,match_score', count='exact').eq('user_id', uid).gte('first_seen', today).execute()
print(f'Jobs from n8n today: {r.count}')
for j in (r.data or []):
    print(f'  [{j[\"source\"]}] {j[\"company\"]} - {j[\"title\"][:40]} (score: {j[\"match_score\"]})')
"
```

- [ ] **Step 3: Run for 2-3 days in shadow mode**

Keep both GitHub Actions (7:00 UTC) and n8n (7:30 UTC, offset by 30 min) running. Compare results daily.

- [ ] **Step 4: Verify email workflows**

Manually trigger the Stale Job Nudges and Follow-Up Reminders workflows. Verify Gmail receives the emails.

---

## Task 9: Cutover

- [ ] **Step 1: Disable GitHub Actions cron**

In `.github/workflows/daily_job_hunt.yml`, comment out the schedule trigger:

```yaml
on:
  # schedule:
  #   - cron: "0 7 * * 1-5"
  workflow_dispatch:
    inputs:
      mode:
        description: "Run mode"
        required: true
        default: "full"
        type: choice
        options:
          - full
          - dry-run
          - scrape-only
```

Keep `workflow_dispatch` so you can still trigger manually as a fallback.

- [ ] **Step 2: Activate n8n Schedule Trigger**

In n8n UI, ensure the Daily Pipeline workflow's Schedule Trigger is set to weekdays 7:00 UTC and the workflow is **Active** (toggle on).

- [ ] **Step 3: Remove SKIP_CONTACTS from pipeline**

n8n has no timeout limit, so contact finding can run. Remove the `SKIP_CONTACTS` env var from the n8n workflow (or don't set it — it defaults to running contacts).

- [ ] **Step 4: Add "Run Pipeline Now" button to frontend**

Add a button in the Dashboard that triggers the n8n webhook:

In `web/src/pages/Dashboard.jsx`, add next to the "+ Add Job" button:

```jsx
<Button
  variant="primary"
  size="sm"
  onClick={async () => {
    try {
      await fetch(`${import.meta.env.VITE_N8N_WEBHOOK_URL}`, { method: 'POST' });
      alert('Pipeline triggered! Check back in 15-20 minutes.');
    } catch {
      alert('Failed to trigger pipeline');
    }
  }}
>
  Run Pipeline
</Button>
```

Set `VITE_N8N_WEBHOOK_URL` in Netlify environment variables to the n8n webhook URL.

- [ ] **Step 5: Commit and deploy**

```bash
git add .github/workflows/daily_job_hunt.yml web/src/pages/Dashboard.jsx
git commit -m "feat: cutover to n8n — disable GH Actions cron, add Run Pipeline button"
```

- [ ] **Step 6: Monitor first 3 automated runs**

Check n8n execution logs and Supabase for new jobs each morning for 3 days.

---

## Task 10: Scoring Consistency Fix

**Files:**
- Modify: `app.py` (refactor `/api/tailor` to use `score-batch` internally)

- [ ] **Step 1: Refactor Add Job scoring to use score-batch**

In `app.py`, find the `/api/tailor` endpoint's scoring logic. Currently it calls `matcher._match_single()` directly. Refactor it to call the same `match_jobs()` function that `score-batch` uses, so both paths go through the same prompt, batching, and council behavior.

Find the scoring call in the tailor endpoint and replace it with a call to the batch scoring function:

```python
# In the tailor endpoint, replace direct _match_single call with:
from matcher import match_jobs
scored = match_jobs([job], resumes, ai_client, min_score=0)  # min_score=0 to always return scores
if scored:
    job = scored[0]  # Use the scored job object
```

- [ ] **Step 2: Verify scoring consistency**

Score a job via the dashboard (existing score) then paste the same JD via Add Job. The scores should be identical or very close (within ±5 due to AI temperature).

- [ ] **Step 3: Commit**

```bash
git add app.py && git commit -m "fix: use same scoring path for pipeline and Add Job to ensure consistent scores"
```

---

## Task 11: Manual Job Editing UI

**Files:**
- Modify: `web/src/pages/JobWorkspace.jsx`

The `PATCH /api/dashboard/jobs/{job_id}` endpoint already accepts `location` and `apply_url` (updated in this session). This task adds the UI.

- [ ] **Step 1: Add editable fields to overview tab**

In `web/src/pages/JobWorkspace.jsx`, inside the overview tab content, add editable location and apply_url fields below the score cards:

```jsx
// Add state at the top of the JobWorkspace component:
const [editing, setEditing] = useState(false);
const [editLocation, setEditLocation] = useState('');
const [editApplyUrl, setEditApplyUrl] = useState('');
const [saving, setSaving] = useState(false);

// Add this function:
async function handleSaveDetails() {
  setSaving(true);
  try {
    const updates = {};
    if (editLocation !== (job.location || '')) updates.location = editLocation;
    if (editApplyUrl !== (job.apply_url || '')) updates.apply_url = editApplyUrl;
    if (Object.keys(updates).length > 0) {
      await apiPatch(`/api/dashboard/jobs/${job.job_id}`, updates);
      setJob({ ...job, ...updates });
    }
    setEditing(false);
  } catch (err) {
    console.error('Save failed:', err);
  } finally {
    setSaving(false);
  }
}
```

Add in the overview tab, between the score cards and the job description:

```jsx
{/* Editable details */}
<div className="grid grid-cols-2 gap-4 mb-6">
  <div>
    <p className="text-[10px] font-bold text-stone-400 uppercase tracking-wider mb-1">Location</p>
    {editing ? (
      <input
        value={editLocation}
        onChange={(e) => setEditLocation(e.target.value)}
        placeholder="e.g. Dublin, Ireland"
        className="w-full bg-white border-2 border-black px-3 py-2 text-sm"
      />
    ) : (
      <p className="text-sm text-stone-700">{job.location || <span className="text-stone-400">Not set</span>}</p>
    )}
  </div>
  <div>
    <p className="text-[10px] font-bold text-stone-400 uppercase tracking-wider mb-1">Apply URL</p>
    {editing ? (
      <input
        value={editApplyUrl}
        onChange={(e) => setEditApplyUrl(e.target.value)}
        placeholder="https://..."
        className="w-full bg-white border-2 border-black px-3 py-2 text-sm"
      />
    ) : (
      job.apply_url ? (
        <a href={job.apply_url} target="_blank" rel="noopener noreferrer" className="text-sm text-info hover:underline truncate block">{job.apply_url}</a>
      ) : (
        <p className="text-sm text-stone-400">Not set</p>
      )
    )}
  </div>
</div>
{editing ? (
  <div className="flex gap-2 mb-6">
    <Button variant="primary" size="sm" loading={saving} onClick={handleSaveDetails}>Save</Button>
    <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>Cancel</Button>
  </div>
) : (
  <Button variant="ghost" size="sm" onClick={() => { setEditing(true); setEditLocation(job.location || ''); setEditApplyUrl(job.apply_url || ''); }} className="mb-6">
    Edit Details
  </Button>
)}
```

Add the import: `import { apiPatch } from '../api';`

- [ ] **Step 2: Build and verify**

```bash
cd web && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/JobWorkspace.jsx && git commit -m "feat: add inline editing for job location and apply_url in Job Workspace"
```

---

## Task 12: n8n Community Nodes + MCP Server

**Files:**
- Create: `infra/mcp_n8n_server.py`
- Modify: `infra/docker-compose.yml` (add N8N_API_KEY env)

- [ ] **Step 1: Enable n8n API and set API key**

SSH into EC2 and add to the n8n environment in `docker-compose.yml`:

```yaml
environment:
  # ... existing vars ...
  - N8N_API_KEY=${N8N_API_KEY}
```

Add to `infra/.env`:
```
N8N_API_KEY=your-secret-api-key-here
```

Restart: `docker-compose up -d`

- [ ] **Step 2: Install community nodes via n8n UI**

In n8n UI → Settings → Community Nodes → Install:
- `n8n-nodes-supabase` (direct Supabase reads/writes)

The AWS S3, Gmail, HTTP Request, Schedule, Webhook, Merge, IF, SplitInBatches, and Code nodes are all built-in.

- [ ] **Step 3: Create MCP server for n8n**

Create `infra/mcp_n8n_server.py`:

```python
"""MCP server for n8n — lets Claude Code trigger pipelines, check status, manage workflows."""
import json
import os
import httpx
from mcp.server.fastmcp import FastMCP

N8N_URL = os.environ.get("N8N_URL", "http://localhost:5678")
N8N_API_KEY = os.environ.get("N8N_API_KEY", "")
HEADERS = {"X-N8N-API-KEY": N8N_API_KEY, "Content-Type": "application/json"}

mcp = FastMCP("n8n")


@mcp.tool()
def n8n_list_workflows() -> str:
    """List all n8n workflows with their active/inactive status."""
    r = httpx.get(f"{N8N_URL}/api/v1/workflows", headers=HEADERS, timeout=10)
    r.raise_for_status()
    workflows = r.json().get("data", [])
    lines = []
    for w in workflows:
        status = "ACTIVE" if w.get("active") else "inactive"
        lines.append(f"[{status}] {w['name']} (id: {w['id']})")
    return "\n".join(lines) or "No workflows found"


@mcp.tool()
def n8n_trigger_pipeline(workflow_name: str = "Daily Pipeline") -> str:
    """Trigger an n8n workflow by name. Defaults to 'Daily Pipeline'."""
    # Find workflow by name
    r = httpx.get(f"{N8N_URL}/api/v1/workflows", headers=HEADERS, timeout=10)
    r.raise_for_status()
    workflows = r.json().get("data", [])
    match = next((w for w in workflows if workflow_name.lower() in w["name"].lower()), None)
    if not match:
        return f"Workflow '{workflow_name}' not found. Available: {[w['name'] for w in workflows]}"

    # Trigger execution
    r2 = httpx.post(
        f"{N8N_URL}/api/v1/workflows/{match['id']}/execute",
        headers=HEADERS, timeout=30,
    )
    r2.raise_for_status()
    exec_data = r2.json().get("data", {})
    return f"Triggered '{match['name']}' — execution ID: {exec_data.get('id', 'unknown')}"


@mcp.tool()
def n8n_get_executions(limit: int = 5) -> str:
    """Get recent n8n workflow executions with status."""
    r = httpx.get(
        f"{N8N_URL}/api/v1/executions",
        headers=HEADERS, params={"limit": limit}, timeout=10,
    )
    r.raise_for_status()
    executions = r.json().get("data", [])
    lines = []
    for e in executions:
        status = e.get("status", "unknown")
        started = e.get("startedAt", "?")[:19]
        workflow = e.get("workflowData", {}).get("name", "?")
        duration = ""
        if e.get("stoppedAt") and e.get("startedAt"):
            from datetime import datetime
            try:
                start = datetime.fromisoformat(e["startedAt"].replace("Z", "+00:00"))
                stop = datetime.fromisoformat(e["stoppedAt"].replace("Z", "+00:00"))
                secs = int((stop - start).total_seconds())
                duration = f" ({secs}s)"
            except Exception:
                pass
        lines.append(f"[{status}] {workflow} — {started}{duration} (id: {e['id']})")
    return "\n".join(lines) or "No executions found"


@mcp.tool()
def n8n_get_execution(execution_id: str) -> str:
    """Get details of a specific execution — node results, errors, data."""
    r = httpx.get(
        f"{N8N_URL}/api/v1/executions/{execution_id}",
        headers=HEADERS, timeout=10,
    )
    r.raise_for_status()
    data = r.json().get("data", r.json())
    
    status = data.get("status", "unknown")
    workflow = data.get("workflowData", {}).get("name", "?")
    
    # Summarize node results
    node_summary = []
    for node_name, node_data in (data.get("data", {}).get("resultData", {}).get("runData", {}) or {}).items():
        if isinstance(node_data, list) and node_data:
            last_run = node_data[-1]
            items = len(last_run.get("data", {}).get("main", [[]])[0]) if last_run.get("data") else 0
            error = last_run.get("error", {}).get("message", "") if last_run.get("error") else ""
            node_summary.append(f"  {node_name}: {items} items" + (f" ERROR: {error}" if error else ""))
    
    result = f"Execution {execution_id}\nWorkflow: {workflow}\nStatus: {status}\n\nNodes:\n"
    result += "\n".join(node_summary) or "  (no node data)"
    return result


@mcp.tool()
def n8n_toggle_workflow(workflow_id: str, active: bool) -> str:
    """Activate or deactivate an n8n workflow."""
    r = httpx.patch(
        f"{N8N_URL}/api/v1/workflows/{workflow_id}",
        headers=HEADERS, json={"active": active}, timeout=10,
    )
    r.raise_for_status()
    name = r.json().get("name", workflow_id)
    return f"Workflow '{name}' is now {'ACTIVE' if active else 'INACTIVE'}"


if __name__ == "__main__":
    mcp.run()
```

- [ ] **Step 4: Add MCP server to project config**

Create or update `.mcp.json` in the project root:

```json
{
  "mcpServers": {
    "n8n": {
      "command": "python",
      "args": ["infra/mcp_n8n_server.py"],
      "env": {
        "N8N_URL": "http://<EC2_IP>:5678",
        "N8N_API_KEY": "<your-api-key>"
      }
    }
  }
}
```

- [ ] **Step 5: Test MCP tools**

After EC2 is running, verify from Claude Code:
- "List my n8n workflows" → calls `n8n_list_workflows`
- "Run my pipeline" → calls `n8n_trigger_pipeline`
- "How did today's pipeline go?" → calls `n8n_get_executions`

- [ ] **Step 6: Commit**

```bash
git add infra/mcp_n8n_server.py infra/docker-compose.yml .mcp.json
git commit -m "feat: add n8n MCP server + community node config"
```

---

## Task 13: Frontend Polish Bundle

**Files:**
- Create: `web/src/components/SkillTags.jsx`
- Create: `web/src/components/JobCard.jsx`
- Modify: `web/src/pages/JobWorkspace.jsx`
- Modify: `web/src/pages/Dashboard.jsx`
- Modify: `web/src/components/JobTable.jsx`

- [ ] **Step 1: Create SkillTags component**

Extracts and displays tech skills from `key_matches` (stored during matching) or parses common skills from the JD text. Create `web/src/components/SkillTags.jsx`:

```jsx
const COMMON_SKILLS = [
  'python', 'javascript', 'typescript', 'react', 'node', 'go', 'rust', 'java',
  'kubernetes', 'docker', 'aws', 'gcp', 'azure', 'terraform', 'jenkins',
  'postgresql', 'mongodb', 'redis', 'kafka', 'graphql', 'rest',
  'ci/cd', 'linux', 'git', 'agile', 'microservices', 'serverless',
  'machine learning', 'ai', 'deep learning', 'nlp', 'computer vision',
  'vue', 'angular', 'svelte', 'next.js', 'fastapi', 'django', 'flask',
  'elasticsearch', 'prometheus', 'grafana', 'datadog', 'splunk',
];

export default function SkillTags({ description }) {
  if (!description) return null;
  const descLower = description.toLowerCase();
  const found = COMMON_SKILLS.filter((s) => descLower.includes(s));
  if (!found.length) return null;

  return (
    <div className="flex flex-wrap gap-1.5 mt-3">
      {found.map((skill) => (
        <span
          key={skill}
          className="border-2 border-black bg-yellow-light text-black font-mono text-[10px] font-bold px-2 py-0.5"
        >
          {skill}
        </span>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Add SkillTags to Job Workspace overview tab**

In `web/src/pages/JobWorkspace.jsx`, import and add below the JD:

```jsx
import SkillTags from '../components/SkillTags';
// Inside the overview tab, after the description <p>:
<SkillTags description={job.description} />
```

- [ ] **Step 3: Add inline PDF preview to Resume and Cover Letter tabs**

In `web/src/pages/JobWorkspace.jsx`, replace the download-only resume tab:

```jsx
{activeTab === 'resume' && (
  <div>
    {job.resume_s3_url ? (
      <div>
        <p className="text-sm text-stone-500 mb-4">
          AI Model: <span className="font-mono font-bold text-black">{job.tailoring_model || '--'}</span>
        </p>
        <iframe
          src={job.resume_s3_url}
          className="w-full h-[600px] border-2 border-black mb-4"
          title="Resume PDF"
        />
        <a href={job.resume_s3_url} target="_blank" rel="noopener noreferrer">
          <Button variant="primary" size="sm">Download PDF</Button>
        </a>
      </div>
    ) : (
      <p className="text-stone-400">No tailored resume yet.</p>
    )}
  </div>
)}
```

Same pattern for cover letter tab with `job.cover_letter_s3_url`.

- [ ] **Step 4: Show actual AI model name instead of "council:consensus"**

In `tailorer.py`, update the model recording to store the winning model name:

Find where `job.tailoring_model` is set and change from `"council:consensus"` to the actual winner:

```python
# After council generates, the winner info is in the council response
job.tailoring_model = info.get("model", "council:consensus")
```

Verify the `complete_with_info` method in `ai_client.py` returns the actual winning model name in the `model` field.

- [ ] **Step 5: Create JobCard component for grid view**

Create `web/src/components/JobCard.jsx`:

```jsx
import { useNavigate } from 'react-router-dom';
import { ScoreBadge } from './ui/Badge';
import Badge from './ui/Badge';
import SkillTags from './SkillTags';

export default function JobCard({ job, onDelete }) {
  const navigate = useNavigate();

  return (
    <div
      className="bg-white border-2 border-black shadow-brutal-sm p-5 cursor-pointer
        hover:translate-x-[1px] hover:translate-y-[1px] hover:shadow-none transition-all"
      onClick={() => navigate(`/jobs/${job.job_id}`)}
    >
      <div className="flex justify-between items-start mb-3">
        <div className="flex-1 min-w-0">
          <p className="font-heading font-bold text-black truncate">{job.title}</p>
          <p className="text-xs text-stone-500 mt-0.5">{job.company}</p>
          <p className="text-[10px] text-stone-400">{job.location || 'Remote'}</p>
        </div>
        <ScoreBadge score={job.match_score} className="text-xl ml-3" />
      </div>
      <SkillTags description={job.description} />
      <div className="flex items-center justify-between mt-3 pt-3 border-t border-stone-200">
        <Badge status={job.application_status || 'New'} />
        <div className="flex items-center gap-2">
          <span className="border border-stone-300 text-stone-500 font-mono text-[9px] font-bold px-1.5 py-0.5">
            {job.source}
          </span>
          {job.resume_s3_url && (
            <span className="w-2 h-2 bg-success rounded-full" title="Has resume" />
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Add card/table view toggle to Dashboard**

In `web/src/pages/Dashboard.jsx`, add a view toggle state and render either JobTable or a card grid:

```jsx
const [viewMode, setViewMode] = useState('table'); // 'table' | 'cards'

// In the filter bar, add a toggle:
<div className="flex items-center gap-2">
  <label className="block text-xs font-bold text-stone-500 uppercase tracking-wider">View</label>
  <div className="flex border-2 border-black">
    <button
      onClick={() => setViewMode('table')}
      className={`px-2 py-1 text-xs font-bold ${viewMode === 'table' ? 'bg-black text-cream' : 'bg-white text-stone-500'} cursor-pointer`}
    >
      Table
    </button>
    <button
      onClick={() => setViewMode('cards')}
      className={`px-2 py-1 text-xs font-bold ${viewMode === 'cards' ? 'bg-black text-cream' : 'bg-white text-stone-500'} cursor-pointer`}
    >
      Cards
    </button>
  </div>
</div>

// Replace the job table render:
{!loading && viewMode === 'table' && <JobTable jobs={jobs} onStatusChange={handleStatusChange} onDelete={handleDelete} />}
{!loading && viewMode === 'cards' && (
  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
    {jobs.map((job) => (
      <JobCard key={job.job_id} job={job} onDelete={handleDelete} />
    ))}
  </div>
)}
```

Import: `import JobCard from '../components/JobCard';`

- [ ] **Step 7: Build and verify**

```bash
cd web && npm run build
```

- [ ] **Step 8: Commit**

```bash
git add web/src/ && git commit -m "feat: add skill tags, inline PDF preview, card view, AI model display"
```

---

## Task 13: User Profile from Resume

**Files:**
- Modify: `app.py` (extend resume upload to extract profile fields)
- Modify: `web/src/pages/Settings.jsx`

- [ ] **Step 1: Extend resume upload to extract profile**

In `app.py`, find the `/api/resumes/upload` endpoint. After extracting text from the PDF, use AI to extract profile fields and auto-update the user's profile:

```python
# After PDF text extraction in the upload endpoint, add:
if parsed_text and len(parsed_text) > 100:
    try:
        ai = _get_ai_client()
        profile_prompt = f"""Extract the following from this resume text. Return JSON only:
{{
  "name": "full name",
  "location": "city, country",
  "skills": ["skill1", "skill2", ...],
  "experience_years": <number>,
  "education": "highest degree and institution",
  "summary": "2-3 sentence professional summary"
}}

Resume text:
{parsed_text[:3000]}"""
        
        info = ai.complete_with_info(prompt=profile_prompt, system="Extract structured data from resumes. Return only valid JSON.", temperature=0.1)
        from matcher import extract_json
        profile_data = extract_json(info["response"])
        
        # Auto-update user profile with extracted data
        if profile_data and isinstance(profile_data, dict):
            _db.update_user_profile(user.id, {
                "name": profile_data.get("name", ""),
                "location": profile_data.get("location", ""),
                "skills": profile_data.get("skills", []),
                "experience_summary": profile_data.get("summary", ""),
            })
    except Exception as e:
        logger.warning(f"Profile extraction failed: {e}")
```

- [ ] **Step 2: Add profile display section to Settings page**

In `web/src/pages/Settings.jsx`, add a section that shows the auto-extracted profile with edit capability. Show name, location, skills as tags, experience summary.

- [ ] **Step 3: Build and verify**

```bash
cd web && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add app.py web/src/pages/Settings.jsx && git commit -m "feat: auto-extract user profile from uploaded resume"
```

---

## Task 14: Score-First Tailoring

**Files:**
- Modify: `tailorer.py`
- Modify: `app.py` (tailor endpoint)

- [ ] **Step 1: Add score-first logic to tailor flow**

In the tailor endpoint or `tailorer.py`, before doing a full rewrite:
1. Score the base resume against the JD first
2. If average score >= 85, do light-touch tweaks only (reorder skills, adjust summary)
3. If average score < 85, do full AI tailoring rewrite

```python
def tailor_resume_smart(job, base_tex, ai_client, resumes, output_dir):
    """Score-first tailoring: light tweaks if base is strong, full rewrite if not."""
    from matcher import match_jobs
    
    # Score base resume first
    scored = match_jobs([job], resumes, ai_client, min_score=0)
    base_score = job.match_score or 0
    
    if base_score >= 85:
        # Light touch: only reorder skills and tweak summary
        logger.info(f"[TAILOR] Base score {base_score} >= 85 — light-touch mode")
        return tailor_resume(job, base_tex, ai_client, output_dir, light_touch=True)
    else:
        logger.info(f"[TAILOR] Base score {base_score} < 85 — full rewrite mode")
        return tailor_resume(job, base_tex, ai_client, output_dir, light_touch=False)
```

Add `light_touch` parameter to `tailor_resume()` that adjusts the system prompt to only make minimal changes.

- [ ] **Step 2: Commit**

```bash
git add tailorer.py app.py && git commit -m "feat: score-first tailoring — light tweaks when base score >= 85"
```

---

## Task 15: Deploy Lambda + Frontend Updates

- [ ] **Step 1: Deploy updated Lambda with new endpoints**

```bash
cd /Users/ut/code/naukribaba
sam build && sam deploy
```

- [ ] **Step 2: Verify deployed endpoints**

```bash
LAMBDA_URL=$(aws cloudformation describe-stacks --stack-name naukribaba --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' --output text --region eu-west-1)
curl "$LAMBDA_URL/api/pipeline/status" -H "Authorization: Bearer <token>"
curl -X POST "$LAMBDA_URL/api/compile-latex" -H "Content-Type: application/json" -d '{"tex_source":"\\documentclass{article}\\begin{document}Test\\end{document}"}' -o test.pdf
```

- [ ] **Step 3: Commit**

```bash
git commit -m "chore: deploy Lambda with Phase 2E endpoints"
```
