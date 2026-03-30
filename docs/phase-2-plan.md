# Phase 2 — NaukriBaba Complete Execution Plan

## Vision
NaukriBaba becomes a full job search copilot: scrape → match → tailor (editable) → apply → prep for interview. Self-improving AI quality. Multi-user. Automated via n8n.

---

## Architecture Decisions (Resolved)

| Decision | Choice | Reasoning |
|----------|--------|-----------|
| Resume editor | Section-by-section text editor (NOT LaTeX) | Users edit plain text per section, backend converts to LaTeX |
| n8n hosting | n8n Cloud free tier (5 workflows) → Railway if needed | Start free, scale when needed |
| External data | Apify for everything (Glassdoor, LeetCode, LinkedIn) | Reliable, unified API, $5/mo budget |
| Multi-user pipeline | **See WS-6 below** — key architecture decision |
| Interview prep | Full scope: questions + YouTube + courses + concepts | Differentiated value |
| Test runner | pytest + GitHub Actions CI + pre-commit for lint | Layered quality gates |

---

## WS-1: QA & Testing Infrastructure (Week 1)

### Why First
Every subsequent workstream needs tests. Writing code without tests is how we got 4 pipeline errors in Phase 1.

### Test Architecture
```
tests/
  conftest.py                 — shared fixtures (db, ai_client, auth tokens)
  unit/
    test_ai_client.py         — failover, caching, council, complete_with_info
    test_matcher.py            — JSON extraction, score parsing, batch
    test_tailorer.py           — LaTeX validation, code fences, structural checks
    test_resume_scorer.py      — score validation, improvement loop, fabrication
    test_cover_letter.py       — LaTeX escaping, template rendering
    test_contact_finder.py     — Apify/Serper fallback, message truncation
    test_latex_compiler.py     — brace balance, escape logic
    test_db_client.py          — CRUD, upsert idempotency, FK handling
  integration/
    test_scrapers.py           — each scraper returns >0 jobs, valid Job schema
    test_api_endpoints.py      — all FastAPI endpoints with JWT auth
    test_supabase_roundtrip.py — create user → jobs → runs → query → cleanup
    test_pipeline_dryrun.py    — full scrape → match → (skip tailor) end-to-end
  quality/
    test_scraper_health.py     — daily: each enabled scraper returns results
    test_ai_quality.py         — score distribution, fabrication rate, format compliance
    test_latex_output.py       — compile samples, verify PDF valid + page count
  e2e/
    test_frontend.py           — Playwright: login → dashboard → tailor → editor → verify
```

### CI Pipeline
```yaml
# .github/workflows/ci.yml
on: [push, pull_request]
jobs:
  lint:              # ruff + eslint, <10s
  unit-tests:        # pytest tests/unit/, <30s, no external deps
  integration-tests: # pytest tests/integration/, <5min, needs API keys
    if: github.event_name == 'pull_request' || github.ref == 'refs/heads/main'

# .github/workflows/quality.yml
on:
  schedule: [{cron: "30 7 * * 1-5"}]  # after daily pipeline
jobs:
  scraper-health:    # test each scraper individually
  ai-quality:        # analyze quality_log from latest run
  latex-quality:     # compile + validate latest outputs
```

### Quality Gates
- **PR merge:** Unit + integration tests must pass
- **Daily:** Scraper health → alert if <3 scrapers return results
- **Post-pipeline:** AI quality → flag if avg score drops >5 points
- **All LaTeX:** Must compile without errors

### Deliverables
- [ ] `tests/conftest.py` — shared fixtures
- [ ] Unit tests for all core modules
- [ ] Integration tests for scrapers + API + DB
- [ ] `.github/workflows/ci.yml`
- [ ] `.github/workflows/quality.yml`
- [ ] `pytest.ini` / `pyproject.toml` test config

---

## WS-2: Scraper Reliability & Self-Healing (Week 2)

### Scraper Audit

| Scraper | Status | Fix Plan |
|---------|--------|----------|
| **Adzuna** | ✅ Working | Keep as-is, API-based |
| **GradIreland** | ✅ Working | Keep, always returns 6/query |
| **LinkedIn** | ⚠️ Partial | Cards work. Use Apify `apify/linkedin-scraper` for descriptions |
| **Jobs.ie** | ❌ Down | Health probe → auto-disable if down 3 days. Apify fallback |
| **IrishJobs** | ❌ Down | Same health probe pattern |
| **JobSurface** | ❌ Broken | Inspect HTML, fix selectors. Disable if unfixable |
| **Glassdoor** | 🔒 Not enabled | Enable with Apify `epctex/glassdoor-scraper` (own scraper too fragile) |
| **Indeed** | 🔒 Not enabled | Enable with Apify `epctex/indeed-scraper` (blocks direct scraping) |
| **JSearch** | 🔒 Not enabled | Enable — API-based (JSEARCH_API_KEY already in secrets) |
| **SerpAPI** | 🔒 Not enabled | Enable if key set — Google Jobs aggregator |
| **YC/WorkAtAStartup** | ⚠️ Unknown | Test + verify |
| **HN Hiring** | ⚠️ Unknown | Test + verify |

### Self-Healing Loop
```
After each pipeline run:
  1. self_improver analyzes scraper_stats
  2. Per scraper: jobs_found, response_time, errors
  3. If 0 jobs for 3 consecutive runs → auto-disable, alert
  4. Track in Supabase `scraper_health` table
  5. Weekly health report: which scrapers are reliable
  6. If disabled scraper passes manual health check → re-enable
```

### New Table: `scraper_health`
```sql
scraper_name TEXT PRIMARY KEY,
last_success TIMESTAMPTZ,
consecutive_failures INT DEFAULT 0,
total_jobs_7d INT DEFAULT 0,
avg_response_ms INT,
is_enabled BOOLEAN DEFAULT true,
disabled_reason TEXT
```

### Deliverables
- [ ] Fix/enable each scraper (with tests from WS-1)
- [ ] Health probe pattern for unreliable scrapers
- [ ] `scraper_health` table + self_improver integration
- [ ] Apify actor wrappers for Indeed, Glassdoor, LinkedIn descriptions

---

## WS-3: AI Council Self-Improvement (Week 2, parallel with WS-2)

### Current State
- 23 providers, fixed failover order
- Council: N generators + M critics → best wins
- Quality log tracks provider/model per artifact
- self_improver detects score inflation + keyword gaps

### Self-Improvement Additions

**A. Model Performance Tracking**
```python
# Per model, tracked in quality_logger:
{
  "reliability_score": weighted(avg_quality, acceptance_rate, -error_rate),
  "avg_score": 87.3,
  "acceptance_rate": 0.42,  # how often this model's output wins council
  "error_rate": 0.03,
  "avg_latency_ms": 2100,
  "tasks": {"match": 45, "tailor": 30, "score": 30, "cover_letter": 15}
}
```

**B. Dynamic Provider Ordering**
- Weekly: self_improver reorders providers by reliability_score
- Best-performing model goes first in failover chain
- Logged: "Reordered providers: groq (0.89) → deepseek (0.85) → qwen (0.82)"

**C. Prompt Evolution**
- Detect patterns: "When model X scores <80, the tailored resume is missing keywords Y, Z"
- AI analyzes its own failures → suggests prompt tweaks
- Human approval required before applying changes

**D. Score Calibration**
- Detect rubber-stamping (>30% identical scores)
- Detect harsh scoring (all <70)
- Auto-adjust temperature for over/under-scorers

### Deliverables
- [ ] `analyze_model_performance()` in self_improver
- [ ] `suggest_provider_reordering()` with config update
- [ ] `detect_scoring_patterns()` per model
- [ ] Quality dashboard shows model reliability rankings

---

## WS-4: Dashboard Redesign — Job Cards (Week 3)

### Design
User sees a grid of job cards (not a table). Each card shows everything about a job at a glance.

```
┌─────────────────────────────────────────────────────┐
│ Stripe                                   [Applied ▾]│
│ Senior SRE — Dublin · via LinkedIn · 2d ago         │
│                                                     │
│  ┌───┐ ┌───┐ ┌───┐                                │
│  │94 │ │96 │ │97 │  Average: 95.7                  │
│  │ATS│ │HM │ │TR │  ████████████████████░░         │
│  └───┘ └───┘ └───┘                                │
│                                                     │
│ Must know Kubernetes, Terraform, Python.            │
│ 5+ years experience in cloud infrastructure...      │
│ [Show more]                                         │
│                                                     │
│ 📄 sre_devops resume                                │
│                                                     │
│ Contacts:                                           │
│   Bridget Lane · Eng Manager      [View Profile →] │
│   Brandon Pearman · Eng Manager   [View Profile →] │
│                                                     │
│ [View Job ↗]  [Tailor Resume]  [Cover Letter]      │
└─────────────────────────────────────────────────────┘
```

### Layout
- **Top:** StatsBar (total jobs, avg score, scrapers active)
- **Filter bar:** Status, source, score range, company search, sort
- **Grid:** 2-col desktop, 1-col mobile
- **Pagination:** 25/page, load more button

### Components
| New | Purpose |
|-----|---------|
| `JobCard.jsx` | Single job card with all data |
| `JobCardGrid.jsx` | Responsive grid + empty state |
| `FilterBar.jsx` | Filters + sort controls |
| `ScoreGauge.jsx` | Visual score (colored badge or bar) |

### Deliverables
- [ ] JobCard component
- [ ] JobCardGrid with filters/sort
- [ ] FilterBar component
- [ ] Replace JobTable with JobCardGrid in Dashboard
- [ ] Mobile responsive
- [ ] Tests: Playwright tests for card interactions

---

## WS-5: Resume Editor — Section-Based (Week 3, parallel with WS-4)

### Core Concept
**User never sees LaTeX.** After tailoring, the resume is displayed as editable text sections. User edits plain text → backend recompiles to LaTeX → PDF updates.

### Flow
```
1. User pastes JD, clicks "Tailor Resume"
2. AI tailors the resume (existing flow)
3. Result displayed as section breakdown:
   ┌──────────────────────────┬──────────────────────┐
   │  SECTION EDITOR          │  PDF PREVIEW          │
   │                          │                       │
   │  ▼ Summary [Score: 92]   │  ┌─────────────────┐ │
   │  ┌──────────────────┐    │  │                 │ │
   │  │ Experienced SRE  │    │  │  Utkarsh Singh  │ │
   │  │ with 5+ years... │    │  │  ─────────────  │ │
   │  └──────────────────┘    │  │  Summary        │ │
   │  [Improve ✨]             │  │  ...            │ │
   │                          │  │                 │ │
   │  ▼ Experience [Score: 88]│  │  Experience     │ │
   │  ┌──────────────────┐    │  │  ...            │ │
   │  │ • Led migration  │    │  │                 │ │
   │  │ • Reduced latency│    │  └─────────────────┘ │
   │  └──────────────────┘    │                       │
   │  [Improve ✨]             │  [Download PDF]       │
   │                          │                       │
   │  ▼ Skills [Score: 95]    │                       │
   │  ...                     │                       │
   └──────────────────────────┴──────────────────────┘
```

4. User edits text in any section
5. Backend: text → LaTeX template → tectonic → PDF
6. PDF preview updates (debounced, ~2s)
7. User can click "Improve" per section → AI council improves just that section
8. Cover letter: same pattern — editable paragraphs, live preview

### Data Model
Resume sections stored in `user_resumes.sections` (JSONB):
```json
{
  "summary": "Experienced SRE with 5+ years...",
  "experience": [
    {
      "title": "Senior SRE",
      "company": "Acme Corp",
      "dates": "2022 — Present",
      "location": "Dublin",
      "bullets": [
        "Led migration of 200+ services to Kubernetes",
        "Reduced P99 latency by 40% through..."
      ]
    }
  ],
  "skills": {
    "languages": ["Python", "Go", "Bash"],
    "cloud": ["AWS", "GCP", "Terraform"],
    "tools": ["Kubernetes", "Docker", "Prometheus"]
  },
  "education": [...],
  "projects": [...],
  "certifications": [...]
}
```

### API Endpoints
| Endpoint | Purpose |
|----------|---------|
| `POST /api/compile-sections` | Sections JSON → LaTeX → PDF URL |
| `POST /api/score-section` | Score one section against JD |
| `POST /api/improve-section` | AI council improves one section |
| `PUT /api/resumes/:id/sections` | Save edited sections to Supabase |

### Deliverables
- [ ] Section parser: extract sections from tailored LaTeX → JSON
- [ ] Section compiler: JSON sections → LaTeX → PDF
- [ ] `POST /api/compile-sections` endpoint
- [ ] `POST /api/score-section` endpoint
- [ ] `POST /api/improve-section` endpoint
- [ ] `SectionEditor.jsx` — per-section text editor with score
- [ ] `PdfPreview.jsx` — live PDF preview panel
- [ ] `ResumeEditor.jsx` — split-pane page
- [ ] Wire tailor flow → editor
- [ ] Same for cover letter sections
- [ ] Tests for all endpoints + Playwright for editor UI

---

## WS-6: Multi-User Pipeline (Week 4)

### The Big Question: Where Does It Run?

**Options:**

| Option | Cost | Pros | Cons |
|--------|------|------|------|
| **A. GitHub Actions** | Free (2000 min/mo) | Already works, no infra | 45min/run × N users = quota burn. Sequential. |
| **B. AWS Lambda** | ~$0 (free tier) | Existing infra, per-user triggers | 15min timeout, cold starts, memory limits |
| **C. AWS ECS Fargate** | ~$5/mo | No timeout, scalable | New infra to manage |
| **D. n8n + Lambda** | ~$5/mo (n8n) | n8n orchestrates, Lambda executes per-user | Best of both worlds |
| **E. Railway/Render** | $5-7/mo | Simple, no timeout | Another service to manage |

**Recommended: Option D — n8n orchestrates, Lambda executes**
```
n8n (scheduler + orchestration):
  1. Cron: daily 7am
  2. Fetch active users from Supabase
  3. For each user:
     a. POST /api/pipeline/run {user_id}  → Lambda
     b. Lambda runs scrape+match (shared cache) + tailor+score (per user)
     c. n8n polls /api/pipeline/status/:run_id
     d. On complete: trigger notification workflow
  4. After all users: run self-improvement analysis
```

Why this works:
- Scraping is shared (once per query/location, cached)
- Matching + tailoring is per-user (10-15 min each)
- Lambda handles per-user runs within 15min timeout
- n8n handles sequencing, retries, notifications
- GitHub Actions stays for CI/CD only

### Changes
- `main.py`: add `--user-id` flag to run for a specific user
- `app.py`: add `/api/pipeline/run` and `/api/pipeline/status` endpoints
- Load user's resumes from Supabase instead of disk
- Shared scrape cache (Redis or S3) across user runs

### Deliverables
- [ ] `POST /api/pipeline/run` endpoint (async, returns run_id)
- [ ] `GET /api/pipeline/status/:run_id` endpoint
- [ ] `main.py --user-id` mode (load config from Supabase)
- [ ] Shared scrape cache
- [ ] n8n workflow: daily pipeline orchestration
- [ ] n8n workflow: new user onboarding → first run
- [ ] Per-user email notifications

---

## WS-7: n8n Automation Workflows (Week 4, parallel with WS-6)

### Workflows

**1. Daily Pipeline (cron)**
```
Trigger: 7:00 AM weekdays
→ GET /api/pipeline/users (active users with search config)
→ For each user:
    → POST /api/pipeline/run {user_id, mode: "full"}
    → Wait: poll /api/pipeline/status/:run_id every 30s
    → On success: send email via Gmail API
    → On failure: send alert to admin
→ After all: POST /api/self-improve
```

**2. New User Onboarding**
```
Trigger: Supabase webhook (new row in user_search_configs)
→ Wait 5 minutes (let user finish setup)
→ POST /api/pipeline/run {user_id, mode: "dry-run"}
→ Send welcome email with first results
```

**3. Scraper Health Alert**
```
Trigger: Daily after pipeline
→ GET /api/scraper-health
→ If any scraper failed 3+ days: send Slack/email alert
```

**4. Quality Regression Alert**
```
Trigger: After each pipeline run
→ GET /api/quality-stats
→ Compare avg scores to 7-day rolling average
→ If drop >5 points: alert admin
```

---

## WS-8: Glassdoor Reviews (Week 5)

### Source: Apify `epctex/glassdoor-reviews-scraper`

### Data Per Company
- Overall rating (1-5)
- Recommend to friend %
- CEO approval %
- Top 5 pros (from reviews)
- Top 5 cons
- Interview difficulty (1-5)
- Interview experience (positive/neutral/negative %)
- Salary ranges per role

### New Table: `company_reviews`
```sql
company_name TEXT,
glassdoor_url TEXT,
overall_rating FLOAT,
recommend_pct INT,
pros TEXT[],
cons TEXT[],
interview_difficulty FLOAT,
salary_range JSONB,
updated_at TIMESTAMPTZ
```

### Frontend
- Company info card in job card (expandable)
- Rating badge + quick pros/cons
- "Interview at [Company]" link to prep questions

---

## WS-9: Interview Prep Suite (Week 5, parallel with WS-8)

### Full Scope — Not Just Questions

For each target role + company, provide:

#### A. Coding Questions
- **Source:** Apify LeetCode scraper (company-tagged problems) + AI-generated
- Top 15 problems per company (sorted by frequency)
- Difficulty distribution: 5 easy, 7 medium, 3 hard
- Each with: problem link, difficulty, topics, hints
- Track: practiced/not practiced

#### B. System Design Scenarios
- AI-generated based on company's tech stack (from JD analysis)
- 5 scenarios per role: e.g. "Design Stripe's payment processing pipeline"
- Each with: requirements, key decisions, scaling considerations
- Reference materials: YouTube links, blog posts

#### C. Behavioral Questions (STAR Format)
- 10 most common per company (from Glassdoor interview data)
- AI generates STAR framework prompts per question
- Example answers tailored to user's experience (from their resume)

#### D. Learning Resources
- **YouTube playlists:** Curated per topic
  - System Design: "Gaurav Sen", "System Design Interview", "ByteByteGo"
  - Coding: "NeetCode", "take U forward", company-specific playlists
  - Behavioral: "Dan Croitor", "Jeff H Sipe"
- **Free courses:** Links to relevant Coursera/edX/MIT OCW
- **Concepts to study:** Key topics extracted from JD, mapped to resources

#### E. Company-Specific Intel
- From Glassdoor: interview process, difficulty, common questions
- From JD analysis: required skills → mapped to study resources
- "Prep score": how ready the user is based on resume match + practice progress

### Data Model

**Table: `interview_questions`**
```sql
id UUID PRIMARY KEY,
role_key TEXT,           -- "sre", "fullstack"
company TEXT,            -- "Stripe", or NULL for generic
category TEXT,           -- "coding", "system_design", "behavioral"
question TEXT,
difficulty TEXT,         -- "easy", "medium", "hard"
external_url TEXT,       -- LeetCode link, YouTube link
hints TEXT[],
sample_answer TEXT,
tags TEXT[],             -- ["arrays", "dp", "kubernetes"]
source TEXT,             -- "leetcode", "glassdoor", "ai_generated"
resources JSONB          -- {youtube: [...], courses: [...], concepts: [...]}
```

**Table: `user_prep_progress`**
```sql
user_id UUID REFERENCES users,
question_id UUID REFERENCES interview_questions,
status TEXT,             -- "not_started", "practicing", "confident"
notes TEXT,
completed_at TIMESTAMPTZ
```

### Frontend: `/interview-prep`
- **Role selector** (from user's search config)
- **Company selector** (from matched jobs)
- **Tabs:** Coding | System Design | Behavioral | Resources
- **Per question:** Card with difficulty badge, topic tags, expand for hints/answer
- **Progress tracker:** Practiced X of Y questions
- **"Prep for this job"** button on each job card → generates company-specific prep

### Deliverables
- [ ] Apify LeetCode actor integration
- [ ] AI question generation per role + company
- [ ] YouTube/course resource database (curated JSON + AI enrichment)
- [ ] `interview_questions` + `user_prep_progress` tables
- [ ] `/interview-prep` page with tabs
- [ ] "Prep for this job" button in job cards
- [ ] Prep score calculation
- [ ] Tests for question generation quality

---

## Execution Timeline

```
Week 1: WS-1 (QA Infrastructure)
  ├── Test framework setup
  ├── Unit tests for all modules
  ├── CI pipeline
  └── Quality gates

Week 2: WS-2 (Scrapers) + WS-3 (AI Self-Improvement)
  ├── Fix broken scrapers
  ├── Enable inactive scrapers via Apify
  ├── Scraper self-healing loop
  ├── Model performance tracking
  └── Dynamic provider ordering

Week 3: WS-4 (Dashboard Cards) + WS-5 (Resume Editor)
  ├── Job card components
  ├── Filter/sort UI
  ├── Section-based resume editor
  ├── Live PDF preview
  └── Per-section AI improvement

Week 4: WS-6 (Multi-User) + WS-7 (n8n)
  ├── Pipeline per-user mode
  ├── n8n daily orchestration
  ├── Shared scrape cache
  └── Per-user notifications

Week 5: WS-8 (Glassdoor) + WS-9 (Interview Prep)
  ├── Company reviews via Apify
  ├── LeetCode question bank
  ├── System design + behavioral questions
  ├── YouTube/course resources
  └── Full E2E testing
```

---

## QA Philosophy (Runs Continuously)

```
Every push:     Unit tests (<30s)
Every PR:       Unit + integration (<5min)
Daily:          Scraper health + AI quality + LaTeX quality
Post-pipeline:  Self-improver → scraper healing + model reordering + prompt suggestions
Weekly:         Full E2E suite + regression report
```

The self-improvement loop is the key differentiator: NaukriBaba gets better at finding jobs AND writing resumes over time, without manual intervention.
