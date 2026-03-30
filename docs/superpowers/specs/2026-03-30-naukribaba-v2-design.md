# NaukriBaba v2 — Complete Product Design Specification

**Date**: 2026-03-30
**Status**: Draft — Pending user review
**Scope**: Full product revamp from job scraper to all-in-one job search command center

---

## 1. Product Vision

NaukriBaba is an **all-in-one AI-powered job search platform** covering the full journey from job discovery to interview preparation. It combines automated pipeline scraping with user-submitted job descriptions, AI council evaluation, and a self-improving feedback loop.

**Core principle**: Both automated-discovered and user-submitted jobs produce identical job cards with the same scoring, artifacts, and tracking.

---

## 2. Architecture

### 2.1 Three-Layer Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  REACT FRONTEND (Netlify)                                    │
│  Neo-Brutalist Light UI · Space Grotesk + JetBrains Mono     │
│  Dashboard · Job Workspace · Interview Prep · Analytics      │
└───────────────────────────┬─────────────────────────────────┘
                            │ REST API + Webhooks
┌───────────────────────────┴─────────────────────────────────┐
│  n8n WORKFLOW ENGINE (EC2 Docker, ~$15/mo)                   │
│  Daily pipeline · Scraping · Enrichment · Self-improvement   │
│  Triggers: Cron, Webhook ("Run Now"), Supabase DB events     │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP Request nodes
┌───────────────────────────┴─────────────────────────────────┐
│  PYTHON API (AWS Lambda + SQS)                               │
│  AI Council: matching, scoring, tailoring, interview prep    │
│  LaTeX compilation · PDF-to-LaTeX · Contact finding          │
│  23 AI providers with failover                               │
└───────────────────────────┬─────────────────────────────────┘
                            │
                    ┌───────┴───────┐
                    │   SUPABASE    │
                    │   PostgreSQL  │
                    └───────────────┘
```

### 2.2 What Lives Where

| Component | Location | Why |
|-----------|----------|-----|
| **Orchestration** (scheduling, retries, fan-out) | n8n | Visual debugging, parallel branches, error isolation |
| **Scraping — API-based** (Adzuna, JSearch) | n8n HTTP nodes | Direct HTTP, no Python needed |
| **Scraping — bot-detection** (Indeed, LinkedIn, Glassdoor) | Apify via n8n | Managed headless browsers, anti-detection |
| **AI matching/scoring** | Lambda | Complex prompt logic, council pattern |
| **Resume tailoring** | Lambda | LaTeX generation + tectonic compilation |
| **Cover letter generation** | Lambda | Council-driven, LaTeX output |
| **PDF-to-LaTeX conversion** | Lambda | pymupdf4llm extraction + AI structuring |
| **Interview question generation** | Lambda | AI council with rubric-based evaluation |
| **Contact finding** | Lambda | AI + Google search URL generation |
| **Company enrichment** | n8n | Fan-out to multiple APIs (CompanyLens, OpenWeb Ninja, GDELT) |
| **Self-improvement analysis** | Lambda (analysis) + n8n (apply) | AI analyzes, n8n stores/reads adjustments |
| **Email notifications** | n8n Gmail node | Simpler than Python SMTP |
| **Frontend** | Netlify | Static deploy, CDN |
| **Database** | Supabase PostgreSQL | Auth, RLS, real-time subscriptions |
| **File storage** | S3 | Resume/cover letter PDFs |

### 2.3 n8n Hosting

**Self-hosted on EC2 t3.small with Docker** (~$15/mo):
- Community Edition: free, unlimited executions, no timeout limits
- Docker Compose: n8n + PostgreSQL + Caddy (reverse proxy with auto-SSL)
- Pipeline can run 90+ minutes without timeout constraints
- Webhook URL for "Run Pipeline Now" from React frontend

### 2.4 Migration Path (4 phases)

1. **Setup** (Day 1): Deploy n8n on EC2, configure credentials
2. **Shadow Mode** (Week 1): Run n8n scrapers alongside GitHub Actions, compare results
3. **Cutover** (Week 2): Disable GitHub Actions cron, activate n8n Schedule Trigger
4. **Enhance** (Week 3+): Add self-improvement, parallel scrapers, on-demand workflows

---

## 3. Product Stages

### Stage 1: Discover

**Automated pipeline** (n8n orchestrated, daily weekday runs):

| Scraper | Method | Source |
|---------|--------|--------|
| Adzuna | n8n HTTP Request (API) | adzuna.com |
| Indeed | Apify actor via n8n (`misceres/indeed-scraper`) | indeed.com |
| LinkedIn | Apify actor via n8n | linkedin.com |
| IrishJobs | n8n HTTP Request | irishjobs.ie |
| Jobs.ie | n8n HTTP Request | jobs.ie |
| GradIreland | n8n HTTP Request | gradireland.com |
| YC Jobs | n8n HTTP Request (parse Inertia.js SSR) | ycombinator.com |
| HN Hiring | n8n HTTP Request | news.ycombinator.com |

All scrapers run as **parallel branches** in n8n. Each has "Continue On Fail" enabled — one failure doesn't block others. Results merge via a Merge node → deduplicate → send to Lambda for AI council matching.

**User-submitted jobs** (same pipeline, source: "manual"):
- "+ Add Job" button on dashboard opens a form
- User pastes JD, enters title/company/URL
- Frontend POSTs to Lambda API
- Lambda runs the same scoring/tailoring/contacts pipeline
- Job appears in dashboard with source badge "manual"

### Stage 2: Research (Company Intelligence)

Automatic enrichment triggered when a new job enters the system.

**Data sources** (all within free/cheap tiers):

| Data | Source | Cost |
|------|--------|------|
| Reviews & ratings | OpenWeb Ninja / RapidAPI (Glassdoor data) | $0-25/mo |
| Company basics, tech stack | CompanyLens API | Free (500/mo) |
| Salary data | OpenWeb Ninja Job Salary API + Levels.fyi | $0-25/mo |
| Recent news | GDELT (free, unlimited) + GNews (100/day free) | $0 |
| Layoff signals | Layoffs.fyi scrape (daily) | $0 |
| Red flags | AI-computed from above data | $0 (existing AI providers) |

**Caching**: Supabase `company_intel` table with per-field TTLs:
- Company basics: 30 days
- Reviews: 7 days
- News: 24 hours
- Salary: 14 days

**Total cost**: $0-25/month using free tiers strategically.

### Stage 3: Tailor

**Existing** (already built):
- AI council 3-perspective scoring (ATS/HM/TR)
- LaTeX resume tailoring with iterative improvement
- Cover letter generation
- Google Drive + S3 upload

**New additions**:

#### PDF-to-LaTeX Conversion

Two-stage pipeline:
1. **Extract**: pymupdf4llm extracts structured Markdown from PDF (handles multi-column, bold/italic, headers by font size)
2. **Structure**: AI council parses Markdown into strict JSON schema (name, summary, skills, experience, projects, education, certifications)
3. **Render**: Deterministic template rendering — JSON sections → LaTeX via `render_template()` (no AI needed, reliable)
4. **Compile**: tectonic → PDF preview

Architecture: Synchronous endpoint (~12-20s total). Fits within Lambda 29s timeout.

Fallback for scanned PDFs: detect if extracted text < 100 chars → OCR via PyMuPDF Tesseract integration.

#### Overleaf-Style Editor (Phase 2B from original plan)

Split-pane editor: LaTeX sections on left, live PDF preview on right. Per-section AI "Improve" button.

### Stage 4: Apply

**Existing**: LinkedIn contacts with intro messages, status tracking (New/Applied/Interview/Offer/Rejected)

**New additions**:
- Follow-up reminders (n8n scheduled check)
- Application deadline tracking
- Email templates for outreach

### Stage 5: Interview Prep

Three pillars, each with AI-powered features:

#### Coding Questions (LeetCode-style)

- **Question bank**: Seed Blind 75 + Grind 75 + NeetCode 150 into `coding_questions` table
- **JD matching**: AI analyzes JD to select relevant categories (backend → graphs/DP, frontend → DOM/state, infra → system design)
- **Practice UI**: Monaco editor component (`@monaco-editor/react`) for code writing
- **AI evaluation**: No code execution engine initially — AI reviews code for correctness, complexity, patterns
- **Future**: Judge0 CE for automated test case execution

#### System Design

- **Question bank**: 20+ questions categorized by level (Junior/Senior/Staff)
- **AI-generated**: LLM creates company-specific questions based on tech stack from Research data
- **Evaluation rubric**: 5 categories (requirements gathering 15%, high-level design 25%, deep dive 25%, trade-offs 20%, communication 15%)
- **Follow-up questions**: AI probes deeper ("What if traffic doubles?")

#### Behavioral / STAR Stories

- **Story bank**: Users store 5-7 STAR stories with tags mapping to question categories
- **Question categories**: Leadership, Problem-Solving, Conflict, Failure, Adaptability, Communication
- **Mock interview mode**: Conversational AI that asks questions, follows up, scores responses
- **Company-specific prep**: AI generates "Why this company?" answers using Research stage data

#### Supabase Schema

New tables: `coding_questions`, `system_design_questions`, `behavioral_questions` (seeded, shared), `user_stories`, `interview_sessions`, `user_prep_progress` (per-user).

#### API Endpoints

```
GET/POST /api/interview/coding/questions     — list/recommend
GET/POST /api/interview/system-design/questions
GET/POST /api/interview/behavioral/questions
CRUD     /api/interview/stories              — user's STAR bank
POST     /api/interview/mock/start|respond|end — conversational mock
GET/PUT  /api/interview/progress             — track mastery
POST     /api/interview/company-prep         — generate prep plan from JD
```

### Stage 6: Analytics

- Application funnel visualization (Scraped → Matched → Applied → Interview → Offer)
- Score trends over time (line chart)
- AI model performance comparison (which council models produce highest scores)
- Scraper health dashboard (success rates, response times)
- Weekly email digest with stats (n8n Gmail node)

### Self-Improvement Loop (Level 2)

After each pipeline run:
1. n8n calls Lambda analysis endpoint
2. AI council reviews: score patterns, keyword gaps, scraper health, model performance
3. Generates adjustments stored in Supabase `pipeline_adjustments` table
4. Next run reads adjustments: disable failing scrapers, tune keyword weights, adjust prompts
5. Cycle repeats

---

## 4. UI/UX Design System

### 4.1 Visual Direction: Neo-Brutalist Light (V1)

**Core aesthetic**: Warm cream backgrounds, thick black borders, yellow accents, bold typography, zero decoration, high contrast.

### 4.2 Design Tokens (Tailwind v4 `@theme`)

```css
@theme {
  --color-cream: #fafaf9;
  --color-cream-dark: #f5f5f4;
  --color-stone-*: [stone palette from 100-900];
  --color-black: #000000;
  --color-yellow: #fbbf24;
  --color-yellow-light: #fde68a;
  --color-success: #22c55e;
  --color-error: #ef4444;
  --color-info: #3b82f6;
  --color-warning: #f97316;

  --font-heading: "Space Grotesk", system-ui, sans-serif;
  --font-body: "Space Grotesk", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, monospace;

  --border-width-default: 2px;
  --border-width-thick: 3px;

  --shadow-brutal: 4px 4px 0px 0px #000000;
  --shadow-brutal-sm: 2px 2px 0px 0px #000000;
  --shadow-brutal-yellow: 4px 4px 0px 0px #fbbf24;
}
```

### 4.3 Component Patterns

- **Cards**: `border-2 border-black shadow-brutal bg-cream`
- **Buttons (primary)**: `bg-black text-cream font-bold border-2 border-black shadow-brutal hover:translate-x-[2px] hover:translate-y-[2px]`
- **Buttons (accent)**: `bg-yellow text-black` with same shadow pattern
- **Active nav item**: `bg-yellow border-2 border-black shadow-brutal-sm`
- **KPI cards**: Thick borders, oversized monospace numbers
- **Tables**: Black header row, cream body, 1px stone dividers
- **Status badges**: Filled backgrounds (blue=new, yellow=applied, green=interview)
- **Form inputs**: `border-2 border-black focus:shadow-brutal-yellow`

### 4.4 Component Libraries for Reference

- **Neobrutalism.dev**: 45+ Shadcn-based components (best reference)
- **RetroUI**: 40+ React+Tailwind components, already Tailwind v4

### 4.5 Charts

**Recharts** (~40KB) styled with thick borders:
- Solid grid lines (not dashed), `strokeWidth: 2-3`
- Yellow fill bars with black `stroke: 2px`
- Tooltips with `border: 3px solid black, box-shadow: 4px 4px 0px black`
- Application funnel: custom div-based trapezoids (more brutalist than chart library funnels)

### 4.6 Responsive Design

- **Desktop (lg+)**: Fixed 256px sidebar
- **Tablet (md)**: Collapsed 64px icon sidebar
- **Mobile (< md)**: Bottom navigation bar (4-5 items), "More" tab for secondary pages
- **Tables on mobile**: Card view (not horizontal scroll)
- **Shadows on mobile**: Reduced from 4px to 2px offset

### 4.7 React Architecture

```
layouts/AppLayout.tsx       — sidebar + <Outlet>
layouts/AuthLayout.tsx      — login/onboarding
pages/
  Dashboard.tsx             — KPIs + job table
  JobWorkspace/             — tabbed: Overview, Research, Resume, CL, Contacts, Prep
  AddJob.tsx                — paste JD form
  UploadResume.tsx          — PDF upload + preview
  InterviewPrep.tsx         — question lists, mock interview
  Analytics.tsx             — charts, funnel
  Settings.tsx
  Login.tsx
components/ui/              — Button, Card, Badge, Input, Table, KPICard, Tabs, Modal
```

**State management**: Zustand for auth + job data + UI state. URL params for filters/pagination (shareable).

**Code splitting**: `React.lazy()` per route, Vite auto-splits.

**New dependencies**: `zustand`, `recharts`, `@monaco-editor/react` (interview prep)

---

## 5. Implementation Phases

### Phase 2A: UI Revamp (Neo-Brutalist)
1. Set up Tailwind v4 `@theme` with brutalist design tokens
2. Create shared UI components (Button, Card, Badge, Input, Table, KPICard)
3. Build AppLayout with sidebar navigation
4. Rebuild Dashboard page with new design
5. Rebuild Job Workspace (tabbed) page
6. Rebuild Add Job page (replaces Tailor page)
7. Rebuild Settings, Login, Onboarding
8. Mobile responsive (bottom nav, card view tables)

### Phase 2B: Resume Editor
1. Add `/api/compile-latex` endpoint
2. Build split-pane editor (Monaco + PDF iframe)
3. Per-section scoring and "Improve" button
4. Wire tailor → editor flow

### Phase 2C: PDF-to-LaTeX
1. Replace pdfplumber with pymupdf4llm in `resume_parser.py`
2. Add strict JSON schema extraction prompt
3. Build `sections_to_latex()` renderer
4. Update `/api/resumes/upload` endpoint
5. Add OCR fallback detection

### Phase 2D: Company Intelligence
1. Create `company_intel` Supabase table
2. Integrate CompanyLens API (basics + tech stack)
3. Integrate OpenWeb Ninja (Glassdoor reviews + salary)
4. Integrate GDELT (news) + Layoffs.fyi (layoff signals)
5. Build Research tab in Job Workspace
6. Add AI summarization for intel cards

### Phase 2E: n8n Migration
1. Deploy n8n on EC2 with Docker
2. Build scraping workflow (parallel branches, Apify for Indeed/LinkedIn)
3. Shadow mode alongside GitHub Actions
4. Build enrichment workflow (company intel)
5. Cutover: disable GitHub Actions, activate n8n
6. Add self-improvement sub-workflow

### Phase 2F: Interview Prep
1. Create interview prep Supabase tables
2. Seed coding question bank (Blind 75 + Grind 75)
3. Build question recommendation API
4. Build STAR story bank CRUD
5. Build mock interview conversational API
6. Build Interview Prep page with Monaco editor
7. Add system design question bank + rubric evaluation

### Phase 2G: Analytics
1. Build analytics API endpoints (funnel, trends, model stats)
2. Build Analytics page with Recharts
3. Application funnel visualization
4. Score trend line charts
5. Weekly email digest (n8n Gmail node)

---

## 6. Cost Projection (Monthly)

| Service | Current | After v2 |
|---------|---------|----------|
| AWS Lambda | ~$0 (free tier) | ~$5 (more invocations) |
| S3 | ~$1 | ~$2 |
| Supabase | Free tier | Free tier (or $25 Pro if needed) |
| Netlify | Free tier | Free tier |
| n8n (EC2) | $0 | ~$15 |
| Apify (Indeed/LinkedIn/Glassdoor) | $0 | ~$5-15 |
| OpenWeb Ninja (Glassdoor API) | $0 | $0-25 |
| CompanyLens | $0 | $0 (500 free/mo) |
| AI providers | $0 (free tiers) | $0 (free tiers) |
| **Total** | **~$1** | **~$25-60** |

---

## 7. Key Technical Decisions

1. **n8n over GitHub Actions**: Visual debugging, parallel branches, webhook triggers, no timeout limits
2. **Apify over custom scrapers for bot-detection sites**: Managed infra, anti-detection built-in, reliable
3. **pymupdf4llm over pdfplumber**: Better multi-column handling, structured Markdown output ideal for LLM processing
4. **Zustand over React Context**: Fine-grained reactivity for dashboard with many independent data widgets
5. **Recharts over Chart.js**: SVG-based, easy to style with thick borders for brutalist aesthetic
6. **Monaco over CodeMirror**: VS Code engine, better autocomplete, more professional for interview prep
7. **Synchronous PDF-to-LaTeX**: ~12-20s fits within Lambda timeout, simpler than async polling
8. **AI evaluation over code execution**: Avoids sandbox complexity for MVP, add Judge0 later
9. **Neo-Brutalist Light over dark theme**: User preference — bold, distinctive, stands out from generic SaaS
10. **Company data via APIs over scraping**: Legal safety, reliability, structured responses
