# CLAUDE.md — Project Context for Claude Code

## Project Overview

**NaukriBaba** is an automated job search pipeline + self-service web app.
It scrapes 7 job boards, matches jobs using 3-perspective AI scoring, generates
tailored LaTeX resumes and cover letters, uploads PDFs to Google Drive, and
sends email summaries. A React landing page lets users paste any JD and get
tailored resumes on demand.

## Architecture

- **Pipeline** (`main.py`): 10-step orchestrator run daily via GitHub Actions
- **API** (`app.py`): FastAPI backend with 5 endpoints, deployable to AWS Lambda via Mangum
- **Frontend** (`web/`): React + Vite + Tailwind, deployable to Netlify
- **Self-improvement** (`self_improver.py`): Post-run analysis that detects weak spots

## Key Design Decisions

- **LaTeX over Google Docs**: LaTeX + tectonic gives pixel-perfect ATS-friendly PDFs in ~15s. Google Docs approach was tried and reverted (commit abc0fe9).
- **Multi-provider AI**: Groq → DeepSeek → OpenRouter → Claude failover chain. All free tiers. SQLite response cache with 72h TTL.
- **3-perspective scoring**: Every resume is evaluated as ATS (keyword match), Hiring Manager (impact), and Technical Recruiter (skills depth). All 3 must score 85+ or the resume is iteratively improved.
- **Google Drive for sharing**: Service account uploads PDFs, shares with user's Gmail. Permanent links (unlike S3 presigned URLs which expire in 30 days).

## Module Map

| Module | Purpose |
|--------|---------|
| `main.py` | Pipeline orchestrator |
| `app.py` | FastAPI backend (5 REST endpoints) |
| `ai_client.py` | Multi-provider AI with failover, rate limiting, caching |
| `matcher.py` | Batch job matching (5 jobs/prompt), 3-score evaluation |
| `tailorer.py` | LaTeX resume tailoring with cache-invalidating resume hash |
| `resume_scorer.py` | Score + iterative improvement loop (up to 3 rounds) |
| `cover_letter.py` | LaTeX cover letter generation |
| `contact_finder.py` | LinkedIn contact finder with intro messages |
| `latex_compiler.py` | LaTeX → PDF via tectonic (fallback: pdflatex) |
| `excel_tracker.py` | Excel tracker with color-coded scores |
| `drive_uploader.py` | Google Drive upload with shareable links |
| `s3_uploader.py` | S3 upload with 30-day presigned URLs |
| `email_notifier.py` | Gmail HTML notification with top 15 jobs table |
| `self_improver.py` | Post-run analysis: scores, keywords, scraper health |
| `scrapers/` | 7 job scrapers (Adzuna, LinkedIn, IrishJobs, Jobs.ie, GradIreland, YC, HN) |

## Config

- `config.yaml`: Profiles, search queries, API keys (via `${ENV_VAR}`), scraper settings, Google Drive config
- `.env`: Local environment variables (gitignored)
- `google_credentials.json`: GCP service account (gitignored)

## Development Commands

```bash
# Run pipeline
python main.py                    # Full run
python main.py --dry-run          # Scrape + match only
python main.py --scrape-only      # Just scrape

# Run API locally
uvicorn app:app --reload --port 8000

# Run frontend locally
cd web && npm run dev

# Build frontend
cd web && npm run build

# Test Google Drive connection
python scripts/test_drive_connection.py

# Run self-improvement analysis
python self_improver.py
```

## Deployment

- **Frontend**: Netlify (`netlify.toml` configured, set `VITE_API_URL` env var)
- **Backend**: AWS Lambda via SAM (`template.yaml`, use `sam deploy --guided`)
- **Pipeline**: GitHub Actions (`.github/workflows/daily_job_hunt.yml`, weekdays 7:00 UTC)

## Implementation Status

See `docs/superpowers/specs/2026-03-19-google-docs-landing-page-plan.md` for the full 6-phase plan.

| Phase | Status |
|-------|--------|
| 1. GCP + Drive upload | ✅ Complete |
| 2. Pipeline integration | ✅ Complete |
| 3. FastAPI backend | ✅ Complete |
| 4. React frontend | ✅ Complete |
| 5. AWS SAM template | ✅ Complete (deploy = user action) |
| 6. Testing + self-improvement | ✅ Self-improver done, E2E = user action |

## Previous Design Spec

The original pipeline overhaul (3 phases: Foundation, Quality, Production-Grade) is at
`docs/superpowers/specs/2026-03-17-pipeline-overhaul-design.md`. All items from Phases 1-2
are complete. Phase 3 items 3.1 (logging) and 3.4 (retry backoff) are done. Items 3.2
(checkpointing) and 3.3 (SQLite job database integration) remain as future work.

## Important Notes

- The `Job` class is defined in `scrapers/base.py` — all modules use it
- AI responses are cached in SQLite (`output/.ai_cache.db`) — delete to force fresh calls
- `seen_jobs.json` tracks processed jobs across runs — don't delete unless you want full re-processing
- Service account is from GCP project `job-automation-490716` (owned by utkarsh45689@gmail.com), shares files with 254utkarsh@gmail.com
