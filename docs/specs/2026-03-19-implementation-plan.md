# Implementation Plan

**Date:** 2026-03-19
**Spec:** [Google Docs Migration + Landing Page](./2026-03-19-google-docs-landing-page-design.md)

## Phases

### Phase 1: GCP Setup + Google Docs Client (Foundation)
*Unblocks everything else. No pipeline changes yet.*

1. **User action: Create GCP project**
   - Create project "job-automation-pipeline" in Google Cloud Console
   - Enable Google Docs API and Google Drive API
   - Create service account → download JSON key
   - Save as `google_credentials.json` in project root (gitignored)
   - Add as `GOOGLE_CREDENTIALS_JSON` GitHub Actions secret (base64-encoded)

2. **Build `google_docs_client.py`**
   - `authenticate(credentials_path)` → returns service objects
   - `clone_template(doc_id)` → creates copy, returns new doc ID
   - `replace_placeholders(doc_id, replacements: dict)` → batchUpdate replaceAllText
   - `export_pdf(doc_id, output_path)` → Drive export as PDF
   - `share_doc(doc_id, email)` → grants viewer/editor access
   - `delete_doc(doc_id)` → cleanup after PDF export (optional)
   - Unit test with a throwaway test doc

3. **Create Google Doc resume templates**
   - Write a script `create_templates.py` that:
     - Reads the existing LaTeX resume content (resumes/*.tex)
     - Creates two Google Docs with proper formatting and placeholder markers
     - Outputs the document IDs to add to config.yaml
   - Template structure uses `{{PLACEHOLDER}}` markers:
     - `{{TITLE_LINE}}`, `{{SUMMARY}}`, `{{SKILLS}}`
     - `{{CLOVER_TITLE}}`, `{{CLOVER_BULLETS}}`
     - `{{KRAKEN_TITLE}}`, `{{KRAKEN_BULLETS}}`
     - `{{PROJECT_1}}`, `{{PROJECT_2}}`, `{{PROJECT_3}}`
     - `{{EDUCATION}}`, `{{CERTIFICATIONS}}`
   - ATS formatting: Calibri/Arial 11pt, standard headings, no tables for layout
   - Page 1: Header through Clover. Page 2: Kraken through Certifications.
   - **User reviews and refines** the templates visually in Google Docs
   - Add template doc IDs to `config.yaml`

### Phase 2: Rewire Pipeline (LaTeX → Google Docs)
*Automated pipeline now uses Google Docs. LaTeX kept as fallback.*

4. **Modify `tailorer.py`**
   - New prompt: generates plain text per section (not full LaTeX)
   - Returns a dict: `{ "summary": "...", "skills": "...", "clover_bullets": "...", ... }`
   - Writing style rules baked into prompt (no dashes, no AI filler, active voice)
   - Keep the old LaTeX tailoring as a fallback function

5. **Modify `cover_letter.py`**
   - Generate plain text cover letter (not LaTeX)
   - Same Google Docs template approach: clone template, replace placeholders, export PDF

6. **Modify `main.py`**
   - Replace LaTeX compile step with:
     1. Clone Google Doc template
     2. Call tailorer for plain text sections
     3. Replace placeholders in cloned doc
     4. Export PDF
     5. Upload PDF to S3
   - Fall back to LaTeX if Google Docs API fails
   - Generate Google Drive share links alongside S3 presigned URLs

7. **Modify `email_notifier.py`**
   - Include Google Drive links (permanent) as primary
   - S3 presigned URLs as fallback

8. **Modify `.github/workflows/daily_job_hunt.yml`**
   - Remove texlive-latex-extra installation step (~8 min saved)
   - Add GOOGLE_CREDENTIALS_JSON secret → write to file at runtime
   - Add google-api-python-client + google-auth to pip install

### Phase 3: Landing Page Backend (FastAPI + Lambda)
*Self-service API reusing shared core.*

9. **Build `app.py` (FastAPI)**
   - `POST /api/score` — score JD against base resume (3-perspective)
   - `POST /api/tailor` — tailor resume, create Google Doc + PDF, return URLs
   - `POST /api/cover-letter` — generate cover letter doc + PDF
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

15. **End-to-end test**
    - Run automated pipeline with Google Docs (GitHub Actions)
    - Verify 14/14 PDFs generated, no compilation failures
    - Verify email with Google Drive links
    - Test landing page: paste JD → score → tailor → cover letter → contacts
    - Verify all PDFs are 2 pages, ATS-friendly, no AI language

## Dependencies

```
Phase 1 ──→ Phase 2 ──→ Phase 6
   │                       ↑
   └──→ Phase 3 ──→ Phase 4 ──→ Phase 5 ──→ Phase 6
```

Phase 2 (pipeline) and Phase 3-5 (landing page) can proceed in parallel after Phase 1.

## Estimated Effort

| Phase | Description | Size |
|-------|-------------|------|
| 1 | GCP setup + Google Docs client | Medium (needs user action for GCP) |
| 2 | Rewire pipeline | Medium |
| 3 | FastAPI backend | Small (reuses existing modules) |
| 4 | Static frontend | Small |
| 5 | AWS deployment | Small (SAM template) |
| 6 | Testing | Small |
