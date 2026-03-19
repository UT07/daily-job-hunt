# Implementation Plan — Updated

**Date**: 2026-03-19
**Spec**: Google Docs Migration + Landing Page (Revised)

## Status Summary

The original 6-phase plan called for migrating from LaTeX to Google Docs templates.
**Phase 1-2 were revised**: LaTeX + tectonic is kept as the PDF engine (faster, more
reliable, better ATS formatting). Google Drive is used for shareable PDF links only.

## Phases

### Phase 1: GCP Setup + Google Drive Upload ✅ COMPLETE
*Credentials, Drive API, shareable links.*

1. ✅ GCP project created (`job-automation-490716`)
2. ✅ Service account credentials (`google_credentials.json`)
3. ✅ `drive_uploader.py` — uploads PDFs to Drive, returns shareable links
4. ✅ `scripts/setup_gcp_drive.sh` — GCP setup automation
5. ✅ `scripts/test_drive_connection.py` — smoke test
6. ✅ GitHub Actions workflow decodes `GOOGLE_CREDENTIALS_JSON` secret

**Decision**: Google Docs template approach was reverted in favor of LaTeX + tectonic.
LaTeX gives pixel-perfect PDFs, tectonic compiles in ~15s (vs ~8min texlive), and
Google Drive provides permanent shareable links. No Google Docs API needed.

### Phase 2: Pipeline Integration ✅ COMPLETE
*Automated pipeline uses Google Drive for PDF hosting.*

4. ✅ `tailorer.py` — generates tailored LaTeX resumes (with cache invalidation via resume hash)
5. ✅ `cover_letter.py` — generates LaTeX cover letters
6. ✅ `main.py` — full pipeline: scrape → match → tailor → score → compile → upload → email
7. ✅ `email_notifier.py` — includes Google Drive links (permanent) + S3 presigned URLs (fallback)
8. ✅ `.github/workflows/daily_job_hunt.yml` — Google credentials setup, tectonic caching

### Phase 3: Landing Page Backend (FastAPI + Lambda) ✅ COMPLETE
*Self-service API reusing shared core modules.*

9. ✅ **`app.py` (FastAPI)**
   - `POST /api/score` — score JD against base resume (3-perspective)
   - `POST /api/tailor` — tailor resume, compile PDF, return Drive URL
   - `POST /api/cover-letter` — generate cover letter, compile PDF, return Drive URL
   - `POST /api/contacts` — find LinkedIn contacts + intro messages
   - `GET /api/health` — health check
   - Loads config.yaml and credentials on cold start
   - Mangum handler for Lambda compatibility
   - CORS enabled for frontend

10. ✅ **`requirements-web.txt`**
    - fastapi, mangum, uvicorn, pydantic
    - google-api-python-client, google-auth

### Phase 4: Landing Page Frontend (React + Netlify) ✅ COMPLETE
*The user-facing web page. Upgraded from vanilla HTML to React.*

11. ✅ **React + Vite + Tailwind frontend (`web/`)**
    - `src/App.jsx` — main app with textarea, dropdowns, action buttons
    - `src/components/ScoreBadge.jsx` — color-coded score circles (green/yellow/red)
    - `src/components/ScoreCard.jsx` — 3-perspective score display
    - `src/components/TailorCard.jsx` — tailored resume with Drive download link
    - `src/components/CoverLetterCard.jsx` — cover letter with Drive link
    - `src/components/ContactsCard.jsx` — LinkedIn contacts with copy-to-clipboard
    - `src/components/ErrorBanner.jsx` — error display
    - `src/api.js` — API client with configurable base URL

12. ✅ **Netlify deployment config**
    - `netlify.toml` — build settings + API proxy redirects
    - `web/.env.example` — environment variable template

### Phase 5: AWS Deployment ✅ COMPLETE
*Ship it serverless.*

13. ✅ **`template.yaml` (AWS SAM)**
    - Lambda function: FastAPI + Mangum, Python 3.12 runtime
    - API Gateway: HTTP API with CORS
    - S3 bucket for static frontend files with public access
    - Environment variables: API keys, Google credentials as parameters

14. ⬚ **Deploy script / CI** — user action required
    - `sam build && sam deploy --guided` for Lambda + API Gateway
    - `netlify deploy --prod` for frontend
    - Documented in README.md

### Phase 6: Testing + Polish — PARTIALLY COMPLETE
*Verify everything works end-to-end.*

15. ✅ **Self-improvement loop (`self_improver.py`)**
    - Analyzes score distributions across runs (ATS vs HM vs TR)
    - Detects keyword gaps from matched JDs
    - Monitors scraper health and auto-disables broken scrapers
    - Tracks match rates and flags low-yield runs
    - Generates actionable improvement suggestions
    - Integrated into main.py as final pipeline step

16. ⬚ **End-to-end test** — requires running pipeline with live API keys
    - Run automated pipeline with Google Drive (GitHub Actions)
    - Verify PDFs generated, no compilation failures
    - Verify email with Google Drive links
    - Test landing page: paste JD → score → tailor → cover letter → contacts
    - Verify all PDFs are 2 pages, ATS-friendly, no AI language

## Dependencies

```
Phase 1 ──→ Phase 2 ──→ Phase 6
│                         ↑
└──→ Phase 3 ──→ Phase 4 ──→ Phase 5 ──→
```

Phase 2 (pipeline) and Phase 3-5 (landing page) can proceed in parallel after Phase 1.

## Estimated Effort

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | GCP setup + Drive upload | ✅ COMPLETE |
| 2 | Pipeline integration | ✅ COMPLETE |
| 3 | FastAPI backend | ✅ COMPLETE |
| 4 | React frontend + Netlify | ✅ COMPLETE |
| 5 | AWS SAM template | ✅ COMPLETE (deploy = user action) |
| 6 | Testing + self-improvement | ✅ Self-improver done, E2E = user action |
