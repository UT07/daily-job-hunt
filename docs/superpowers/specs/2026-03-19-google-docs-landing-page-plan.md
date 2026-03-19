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

### Phase 3: Landing Page Backend (FastAPI + Lambda)
*Self-service API reusing shared core modules.*

9. **Build `app.py` (FastAPI)**
   - `POST /api/score` — score JD against base resume (3-perspective)
   - `POST /api/tailor` — tailor resume, compile PDF, return Drive URL
   - `POST /api/cover-letter` — generate cover letter, compile PDF, return Drive URL
   - `POST /api/contacts` — find LinkedIn contacts + intro messages
   - `GET /api/health` — health check
   - Loads config.yaml and credentials on cold start
   - Mangum handler for Lambda compatibility
   - CORS enabled for S3-hosted frontend

10. **Add `requirements-web.txt`**
    - fastapi, mangum, uvicorn
    - google-api-python-client, google-auth
    - (ai_client deps already in requirements.txt)

### Phase 4: Landing Page Frontend (Static S3)
*The user-facing web page.*

11. **Build `web/index.html`**
    - Textarea for job description
    - Dropdown for resume type (SRE/DevOps, Full-Stack)
    - "Score Resume" button → displays score card
    - "Tailor Resume" button → shows loading, then download/open links
    - "Generate Cover Letter" button → same
    - LinkedIn contacts section with search URLs + copyable messages
    - Tailwind CSS via CDN, clean professional design
    - Vanilla JS fetch calls to API Gateway

12. **Build `web/app.js`**
    - fetch() calls to each API endpoint
    - Loading states / spinners during AI processing
    - Error handling / retry UI
    - Copy-to-clipboard for LinkedIn messages

### Phase 5: AWS Deployment
*Ship it serverless.*

13. **Build `template.yaml` (AWS SAM)**
    - Lambda function: FastAPI + Mangum, Python 3.12 runtime
    - API Gateway: HTTP API with CORS
    - S3 bucket for static frontend files
    - CloudFront distribution (optional, for custom domain + HTTPS)
    - Environment variables: API keys, Google credentials (from SSM/Secrets Manager)

14. **Deploy script / CI**
    - `sam build && sam deploy` for Lambda + API Gateway
    - `aws s3 sync web/ s3://bucket-name/` for static frontend
    - Document the deployment in README

### Phase 6: Testing + Polish
*Verify everything works end-to-end.*

15. **End-to-end test**
    - Run automated pipeline with Google Drive (GitHub Actions)
    - Verify 14/14 PDFs generated, no compilation failures
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
| 3 | FastAPI backend | 🔧 IN PROGRESS |
| 4 | Static frontend | ⬚ NOT STARTED |
| 5 | AWS deployment | ⬚ NOT STARTED |
| 6 | Testing | ⬚ NOT STARTED |
