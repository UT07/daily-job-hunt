# Google Docs Migration + Self-Service Landing Page

**Date:** 2026-03-19
**Status:** Approved

## Problem

The current LaTeX-based resume pipeline has reliability and performance issues:
- 5/14 resumes failed PDF compilation in the latest run (unescaped special characters)
- texlive installation adds ~8 minutes to every GitHub Actions run
- S3 presigned URLs expire after 7 days (AWS max)
- AI tailoring produces overly verbose, AI-sounding text with em-dashes
- No way to generate artifacts for a single job found manually outside the pipeline
- Matched jobs skew too senior; freshness decays past 3 days

## Solution

1. Replace LaTeX with Google Docs API for resume/cover letter generation
2. Build a self-service landing page (Next.js + FastAPI) for on-demand job applications
3. Improve writing quality, job freshness, and role targeting in the automated pipeline

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Shared Core                        │
│  matcher.py · tailorer.py · cover_letter.py          │
│  contact_finder.py · google_docs_client.py           │
│  ai_client.py · s3_uploader.py                       │
└──────────┬──────────────────────┬────────────────────┘
           │                      │
    ┌──────▼──────┐      ┌───────▼────────┐
    │  Automated   │      │  Landing Page   │
    │  Pipeline    │      │  (FastAPI +     │
    │  (main.py)   │      │   Next.js)      │
    │              │      │                 │
    │ GitHub       │      │ Paste JD →      │
    │ Actions      │      │ Score →         │
    │ daily cron   │      │ Tailor →        │
    │              │      │ Cover Letter →  │
    │ 7 scrapers → │      │ Contacts →      │
    │ match →      │      │ Download PDFs   │
    │ tailor →     │      │                 │
    │ email        │      │ Local/deployed  │
    └─────────────┘      └────────────────┘
```

Both paths share the same core: AI scoring, resume tailoring, cover letter generation, contact finding, and Google Docs export. The automated pipeline runs on GitHub Actions daily. The landing page runs locally or on a server for on-demand use.

## Part 1: Google Docs Resume Generation

### Template Strategy (Hybrid)

1. Pipeline creates a well-structured Google Doc with full resume content
2. User refines fonts, spacing, margins visually in Google Docs
3. That doc becomes the "master template" referenced by ID in config.yaml
4. Each pipeline run clones the template, replaces content via Docs API, exports PDF

### Template IDs in Config

```yaml
resumes:
  sre_devops:
    google_doc_id: "<template doc ID>"
    tex_path: "resumes/sre_devops.tex"  # kept as fallback
    label: "SRE / DevOps Engineer"
  fullstack:
    google_doc_id: "<template doc ID>"
    tex_path: "resumes/fullstack.tex"   # kept as fallback
    label: "Full-Stack Software Engineer"
```

### Google Docs Client (`google_docs_client.py`)

Responsibilities:
- Authenticate via service account JSON
- Clone a template doc (Drive API: copy)
- Replace placeholder text in a cloned doc (Docs API: batchUpdate with replaceAllText)
- Export doc as PDF (Drive API: export)
- Share doc with user's email (Drive API: permissions)
- Delete temporary docs after PDF export (optional, configurable)

The template uses named placeholders like `{{SUMMARY}}`, `{{SKILLS}}`, `{{CLOVER_BULLETS}}`, `{{KRAKEN_BULLETS}}`, `{{PROJECTS}}`, `{{EDUCATION}}`, `{{CERTIFICATIONS}}`. The AI generates plain text for each placeholder. The Docs API replaces text while preserving the template's formatting.

### ATS-Friendly Template Rules

- Font: Calibri or Arial (11pt body, 13pt headings)
- No tables for layout, no text boxes, no columns, no headers/footers with critical info
- Standard section headings: Summary, Technical Skills, Experience, Projects, Education, Certifications
- Simple bullet points (standard list markers)
- Margins: 0.7 inches all sides
- Page 1: Header + Summary + Skills + Clover IT Services experience
- Page 2: Seattle Kraken + Projects + Education + Certifications
- Max 2 pages, consistent spacing, no stretched gaps

### Writing Style Rules

Enforced in the tailoring prompt:
- No em-dashes (—, --, ---) as clause connectors. Use periods.
- No AI filler phrases ("directly transferable to", "aligned with", "outcomes relevant to", "leveraging", "utilizing")
- Short, active voice sentences. Lead with the action verb.
- Quantify impact with numbers and percentages.
- Match job posting terminology naturally by weaving it into existing bullet points, not by appending qualifier phrases.
- Do not fabricate experience. Only reword, reorder, and emphasize existing content.

### Resume Layout (2 pages)

**Page 1:**
- Header: Name, title line, contact info, links
- Summary: 3-4 sentences, tailored per job
- Technical Skills: 7-8 categorized bullet points
- Experience — Clover IT Services (Jun 2022 – Jul 2024): 7-8 bullets

**Page 2:**
- Experience — Seattle Kraken (Jun 2021 – May 2022): 2 bullets
- Featured Projects: 3 projects, 2 bullets each
- Education: 3 entries
- Certifications: 3 entries

Content must fill pages naturally with consistent spacing. No stretched whitespace between sections.

## Part 2: Self-Service Landing Page

### Stack

- **Frontend:** Static HTML + Tailwind CSS (CDN) + vanilla JS → hosted on S3 + CloudFront
- **Backend:** FastAPI + Mangum adapter → deployed as AWS Lambda behind API Gateway
- **Resume generation:** Google Docs API (same GCP project as the automated pipeline)
- No Node.js, no dedicated server, scales to zero, ~$0/month at personal usage
- Run locally with `uvicorn app:app`, deploy with SAM/CDK to Lambda
- Designed as single-user for now, with clean API separation so multi-user can be added later

### Deployment Architecture

```
Browser → CloudFront → S3 (static HTML/CSS/JS)
                    ↘
              API Gateway → Lambda (FastAPI + Mangum)
                                ↓
                         Google Docs API (clone, edit, export PDF)
                                ↓
                         S3 (store generated PDFs)
```

### API Endpoints

```
POST /api/score
  Input:  { jd: string, resume_type: "sre_devops" | "fullstack" }
  Output: { ats_score, hm_score, tr_score, avg_score, reasoning, key_matches, gaps, tailoring_suggestions }

POST /api/tailor
  Input:  { jd: string, resume_type: "sre_devops" | "fullstack" }
  Output: { pdf_url, doc_url, scores: { ats, hm, tr } }

POST /api/cover-letter
  Input:  { jd: string, resume_type: "sre_devops" | "fullstack" }
  Output: { pdf_url, doc_url }

POST /api/contacts
  Input:  { jd: string, company: string, title: string }
  Output: [{ role, search_url, message, why }]
```

### UI Flow

1. User pastes job description into textarea
2. Selects base resume from dropdown (SRE/DevOps or Full-Stack)
3. Clicks "Score Resume" → shows score card (ATS/HM/TR) with reasoning and gaps
4. Clicks "Tailor Resume" → generates tailored Google Doc + PDF, shows download links
5. Clicks "Generate Cover Letter" → same flow, separate doc
6. Below the outputs: LinkedIn contact cards with search URLs and copy-able intro messages

### UI Design

Clean, professional single-page layout. Static HTML served from S3/CloudFront. No sidebar, no navigation complexity. Card-based result display. Tailwind CSS via CDN. Vanilla JS fetch calls to API Gateway endpoints. Loading states for AI operations (5-15 seconds). No JavaScript framework needed.

## Part 3: Pipeline Improvements (Already Applied)

### Job Freshness
- `days_back: 7` (was 3) — searches a full week
- Stronger recency gradient in local ranking: today +25, yesterday +15, 3 days +10, 5 days +5
- Fresher jobs fill more of the max_jobs cutoff

### Role Targeting
- Junior/graduate titles get +20 bonus in local ranking
- Senior titles get -15 penalty (deprioritized, not excluded)
- Lead/staff titles get -25 penalty
- Added "staff engineer", "distinguished", "fellow" to hard reject list
- Added "7+ years of experience" to description reject patterns

### S3 Presigned URLs
- Fixed expiry from 30 days (2,592,000s) to 7 days (604,800s) — AWS maximum
- Google Drive share links will replace presigned URLs as primary sharing mechanism

### Resume Writing Quality
- Improved SRE summary: shorter, no em-dashes, direct statements
- Tailoring prompt will enforce writing style rules (see Part 1)

## Files: New

| File | Purpose |
|------|---------|
| `google_docs_client.py` | Google Docs/Drive API wrapper |
| `app.py` | FastAPI backend for landing page |
| `web/index.html` | Static landing page (S3 hosted) |
| `web/style.css` | Minimal custom CSS |
| `web/app.js` | Vanilla JS: fetch calls to API Gateway |
| `template.yaml` | AWS SAM template for Lambda + API Gateway + S3 |

## Files: Modified

| File | Change |
|------|--------|
| `tailorer.py` | Output plain text sections instead of LaTeX |
| `cover_letter.py` | Output plain text instead of LaTeX |
| `config.yaml` | Add Google Doc template IDs, days_back: 7 |
| `main.py` | Replace LaTeX compile with Google Docs export; ranking improvements |
| `s3_uploader.py` | 7-day presign expiry (done) |
| `.github/workflows/daily_job_hunt.yml` | Remove texlive, add GOOGLE_CREDENTIALS secret |

## Files: Kept (fallback)

| File | Reason |
|------|--------|
| `latex_compiler.py` | Fallback if Google Docs API is unavailable |
| `resumes/*.tex` | Source of truth for resume content; used to populate initial Google Doc templates |

## GCP Setup Required

1. Create new GCP project (e.g., "job-automation-pipeline")
2. Enable Google Docs API + Google Drive API
3. Create service account, download JSON key
4. Add JSON key as `GOOGLE_CREDENTIALS` GitHub Actions secret
5. Store locally as `google_credentials.json` (gitignored)

## Success Criteria

- [ ] Automated pipeline produces 14/14 PDFs (zero compilation failures)
- [ ] Pipeline runtime drops from ~44 min to ~30 min (no texlive install)
- [ ] Landing page: paste JD → get scored resume + cover letter + contacts in <60 seconds
- [ ] All generated resumes are 2 pages, consistent formatting, no AI-sounding language
- [ ] Google Drive links work permanently (no 7-day expiry)
