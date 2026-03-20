# Google Docs Template Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace LaTeX with Google Docs as the resume/cover letter template engine so output is editable, reliable, and fast.

**Architecture:** Google Doc templates with `{{PLACEHOLDER}}` markers are cloned per job. AI generates plain text per section. Docs API replaces placeholders (preserving formatting). PDF exported via Drive API. User can edit the doc before applying.

**Tech Stack:** Google Docs API, Google Drive API, `google-api-python-client`, `google-auth`, Python 3.11+

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `google_docs_client.py` | CREATE | Docs/Drive API: auth, clone, replace placeholders, export PDF, share |
| `create_templates.py` | CREATE | One-time script: creates Google Doc templates from resume content |
| `tailorer.py` | MODIFY | Output plain text dict instead of LaTeX. Keep LaTeX path as fallback |
| `cover_letter.py` | MODIFY | Output plain text body. Google Docs template for formatting |
| `resume_scorer.py` | MODIFY | Accept plain text resume content, not just LaTeX |
| `main.py` | MODIFY | Replace LaTeX compile step with Google Docs clone+replace+export |
| `app.py` | MODIFY | Use Google Docs flow instead of LaTeX for web endpoints |
| `config.yaml` | MODIFY | Add Google Doc template IDs |

## Resume Formatting Rules (Enforced in Templates)

- **Font:** Calibri 11pt body, 13pt section headings, 16pt name
- **Margins:** 0.7 inches all sides
- **No tables** for layout, no text boxes, no columns
- **Standard sections:** Summary, Technical Skills, Experience, Projects, Education, Certifications
- **Page 1:** Header + Summary + Skills + Clover IT Services (all bullets)
- **Page 2:** Seattle Kraken + Projects + Education + Certifications
- **Consistent spacing:** 6pt after each bullet, 12pt before section headings
- **Max 2 pages.** No stretched whitespace.

## Placeholder Schema

The Google Doc templates use these markers that get replaced per job:

```
{{TITLE_LINE}}          → "Site Reliability Engineer (Python, K8s, AWS, Observability)"
{{SUMMARY}}             → 3-4 sentence tailored summary
{{SKILLS}}              → Full skills section (bullet points as plain text lines)
{{CLOVER_BULLETS}}      → 7-8 bullet points for Clover experience
{{KRAKEN_BULLETS}}      → 2 bullet points for Seattle Kraken experience
{{PROJECT_1_TITLE}}     → Project name + tech stack
{{PROJECT_1_BULLETS}}   → 2 bullet points
{{PROJECT_2_TITLE}}     → ...
{{PROJECT_2_BULLETS}}   → ...
{{PROJECT_3_TITLE}}     → ...
{{PROJECT_3_BULLETS}}   → ...
```

Static content (name, contact info, education, certifications, dates, company names) stays in the template and is NOT replaced. Only the content that changes per job gets placeholders.

---

### Task 1: Google Docs Client Module

**Files:**
- Create: `google_docs_client.py`

- [ ] **Step 1: Create `google_docs_client.py` with auth and clone functions**

```python
"""Google Docs template engine for resume/cover letter generation.

Clones a template Google Doc, replaces {{PLACEHOLDER}} markers with
tailored content, and exports as PDF. The cloned doc remains editable
in Google Docs so the user can make changes before applying.
"""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def _get_credentials(credentials_path: str = "google_credentials.json"):
    """Load service account credentials. Handles Lambda env var fallback."""
    from google.oauth2.service_account import Credentials

    # Lambda: credentials passed as env var, write to temp file
    if not Path(credentials_path).exists() and os.environ.get("GOOGLE_CREDENTIALS_JSON"):
        credentials_path = "/tmp/google_credentials.json"
        with open(credentials_path, "w") as f:
            f.write(os.environ["GOOGLE_CREDENTIALS_JSON"])

    return Credentials.from_service_account_file(
        credentials_path,
        scopes=[
            "https://www.googleapis.com/auth/documents",
            "https://www.googleapis.com/auth/drive",
        ],
    )


def _get_services(credentials_path: str = "google_credentials.json"):
    """Create Docs and Drive service clients."""
    from googleapiclient.discovery import build
    creds = _get_credentials(credentials_path)
    docs = build("docs", "v1", credentials=creds, cache_discovery=False)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    return docs, drive


def clone_template(template_doc_id: str, title: str,
                   credentials_path: str = "google_credentials.json") -> str:
    """Clone a Google Doc template. Returns the new document ID."""
    _, drive = _get_services(credentials_path)
    copy = drive.files().copy(
        fileId=template_doc_id,
        body={"name": title},
    ).execute()
    doc_id = copy["id"]
    logger.info(f"[GDOCS] Cloned template -> {doc_id} ({title})")
    return doc_id


def replace_placeholders(doc_id: str, replacements: Dict[str, str],
                         credentials_path: str = "google_credentials.json"):
    """Replace all {{PLACEHOLDER}} markers in a Google Doc.

    Args:
        doc_id: The Google Doc ID
        replacements: Dict mapping placeholder names to replacement text
                      e.g., {"SUMMARY": "Tailored summary text..."}
    """
    docs, _ = _get_services(credentials_path)
    requests = []
    for key, value in replacements.items():
        placeholder = "{{" + key + "}}"
        requests.append({
            "replaceAllText": {
                "containsText": {"text": placeholder, "matchCase": True},
                "replaceText": value,
            }
        })
    if requests:
        docs.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": requests},
        ).execute()
        logger.info(f"[GDOCS] Replaced {len(requests)} placeholders in {doc_id}")


def export_pdf(doc_id: str, output_path: str,
               credentials_path: str = "google_credentials.json") -> str:
    """Export a Google Doc as PDF. Returns the output file path."""
    _, drive = _get_services(credentials_path)
    pdf_content = drive.files().export(
        fileId=doc_id,
        mimeType="application/pdf",
    ).execute()
    Path(output_path).write_bytes(pdf_content)
    logger.info(f"[GDOCS] Exported PDF -> {Path(output_path).name}")
    return output_path


def share_doc(doc_id: str, email: str,
              credentials_path: str = "google_credentials.json") -> str:
    """Share doc with a user and return the web view link."""
    _, drive = _get_services(credentials_path)
    # Share with user
    drive.permissions().create(
        fileId=doc_id,
        body={"type": "user", "role": "writer", "emailAddress": email},
        sendNotificationEmail=False,
    ).execute()
    # Get the web link
    doc = drive.files().get(fileId=doc_id, fields="webViewLink").execute()
    link = doc.get("webViewLink", "")
    logger.info(f"[GDOCS] Shared {doc_id} with {email}")
    return link


def create_resume_doc(
    template_doc_id: str,
    replacements: Dict[str, str],
    title: str,
    output_pdf_path: str,
    share_with: str = "",
    credentials_path: str = "google_credentials.json",
) -> Dict[str, str]:
    """Full pipeline: clone template, replace content, export PDF, share.

    Returns {"doc_id": ..., "doc_url": ..., "pdf_path": ...}
    """
    doc_id = clone_template(template_doc_id, title, credentials_path)
    replace_placeholders(doc_id, replacements, credentials_path)
    pdf_path = export_pdf(doc_id, output_pdf_path, credentials_path)

    doc_url = ""
    if share_with:
        doc_url = share_doc(doc_id, share_with, credentials_path)

    return {"doc_id": doc_id, "doc_url": doc_url, "pdf_path": pdf_path}
```

- [ ] **Step 2: Verify syntax**

Run: `python3 -m py_compile google_docs_client.py`
Expected: No output (success)

- [ ] **Step 3: Commit**

```bash
git add google_docs_client.py
git commit -m "feat: add Google Docs template client (clone, replace, export)"
```

---

### Task 2: Create Google Doc Templates

**Files:**
- Create: `create_templates.py`

- [ ] **Step 1: Create template creation script**

This script reads the existing LaTeX resume content and creates two Google Doc templates (SRE and fullstack) with proper formatting and placeholder markers. It should:

1. Create a new Google Doc with the Docs API
2. Insert formatted content: heading (name), subtitle, contact info, section headers
3. Insert `{{PLACEHOLDER}}` markers where tailored content goes
4. Set font to Calibri 11pt body, 13pt headings
5. Add a page break before Seattle Kraken section
6. Print the document IDs for config.yaml

Key sections of the SRE template:
- Header: "Utkarsh Singh" (16pt bold, centered) + title line as `{{TITLE_LINE}}`
- Contact: static (Dublin, phone, email, GitHub, LinkedIn)
- Summary: `{{SUMMARY}}`
- Technical Skills: `{{SKILLS}}`
- Experience — Clover IT Services heading (static) + `{{CLOVER_BULLETS}}`
- [PAGE BREAK]
- Experience — Seattle Kraken heading (static) + `{{KRAKEN_BULLETS}}`
- Featured Projects: 3 blocks with `{{PROJECT_N_TITLE}}` + `{{PROJECT_N_BULLETS}}`
- Education: static
- Certifications: static

The fullstack template follows the same structure with different title/summary emphasis.

- [ ] **Step 2: Run the script to create templates**

Run: `python3 create_templates.py`
Expected: Prints two Google Doc IDs and URLs

- [ ] **Step 3: Add template IDs to config.yaml**

Add `google_doc_id` under each resume entry in config.yaml.

- [ ] **Step 4: User reviews and refines templates in Google Docs**

Open the Google Doc URLs, adjust fonts/spacing/margins to look professional. This is the "hybrid" step where you get visual control.

- [ ] **Step 5: Commit**

```bash
git add create_templates.py config.yaml
git commit -m "feat: add template creation script, add doc IDs to config"
```

---

### Task 3: Modify Tailorer for Plain Text Output

**Files:**
- Modify: `tailorer.py`

- [ ] **Step 1: Add `tailor_resume_text()` function**

Keep the existing `tailor_resume()` for LaTeX fallback. Add a new function that returns a dict of plain text sections:

```python
def tailor_resume_text(
    job: Job,
    base_sections: Dict[str, str],
    ai_client: AIClient,
) -> Dict[str, str]:
    """Tailor resume content as plain text sections for Google Docs.

    Returns dict with keys: TITLE_LINE, SUMMARY, SKILLS, CLOVER_BULLETS,
    KRAKEN_BULLETS, PROJECT_1_TITLE, PROJECT_1_BULLETS, etc.
    """
```

The prompt instructs the AI to return JSON with each section as a key. Writing style rules are enforced here (no dashes, no AI filler, active voice, 2-page layout constraints).

- [ ] **Step 2: Verify syntax**

Run: `python3 -m py_compile tailorer.py`

- [ ] **Step 3: Commit**

```bash
git add tailorer.py
git commit -m "feat: add plain text tailoring for Google Docs templates"
```

---

### Task 4: Modify Cover Letter for Plain Text + Google Docs

**Files:**
- Modify: `cover_letter.py`

- [ ] **Step 1: Add `generate_cover_letter_doc()` function**

The existing `generate_cover_letter()` already generates plain text body and wraps it in LaTeX. Add a new function that uses `google_docs_client.create_resume_doc()` with a cover letter template instead.

The cover letter template has placeholders: `{{COMPANY_NAME}}`, `{{JOB_TITLE}}`, `{{BODY}}`, `{{DATE}}`.

- [ ] **Step 2: Commit**

```bash
git add cover_letter.py
git commit -m "feat: add Google Docs cover letter generation"
```

---

### Task 5: Modify Resume Scorer for Plain Text

**Files:**
- Modify: `resume_scorer.py`

- [ ] **Step 1: Update scorer prompts to accept plain text**

Change "LaTeX" references to "resume text" in prompts. The scoring logic doesn't depend on LaTeX — it just needs the content. Change `IMPROVE_SYSTEM_PROMPT` to return structured JSON improvements instead of full LaTeX source.

- [ ] **Step 2: Add `improve_resume_text()` that returns a dict of improved sections**

Instead of returning full LaTeX, return the same dict format as `tailor_resume_text()`.

- [ ] **Step 3: Commit**

```bash
git add resume_scorer.py
git commit -m "refactor: scorer accepts plain text, returns structured improvements"
```

---

### Task 6: Rewire Main Pipeline

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add Google Docs resume generation path**

In `run_pipeline()`, after matching, check if `google_doc_id` is configured. If yes:
1. Call `tailor_resume_text()` to get plain text sections
2. Call `google_docs_client.create_resume_doc()` to clone template, replace, export PDF
3. Store the Google Doc URL on the job object (`job.resume_doc_url`)
4. Store the PDF path (`job.tailored_pdf_path`)

If no `google_doc_id`, fall back to LaTeX flow.

Same for cover letters.

- [ ] **Step 2: Remove tectonic/pdflatex dependency for the Google Docs path**

The LaTeX compile step should be skipped when Google Docs is used. Keep it for fallback only.

- [ ] **Step 3: Update email notifier call to include doc URLs**

Pass `doc_url` alongside `drive_url` and `s3_url` for each job.

- [ ] **Step 4: Verify syntax**

Run: `python3 -m py_compile main.py`

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat: use Google Docs templates in pipeline, LaTeX as fallback"
```

---

### Task 7: Rewire FastAPI Backend

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Update tailor and cover-letter endpoints**

Use `tailor_resume_text()` + `google_docs_client.create_resume_doc()` instead of LaTeX. Return both `doc_url` (editable Google Doc link) and `pdf_url` (exported PDF).

- [ ] **Step 2: Add doc_url to response models**

```python
class TailorResponse(BaseModel):
    ...
    doc_url: str   # Editable Google Doc link
    pdf_url: str   # Exported PDF
```

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: web endpoints use Google Docs templates"
```

---

### Task 8: Update Workflow and Config

**Files:**
- Modify: `.github/workflows/daily_job_hunt.yml`
- Modify: `config.yaml`

- [ ] **Step 1: Remove tectonic install from workflow**

Delete the tectonic cache and install steps. They're no longer needed when Google Docs is the primary engine.

- [ ] **Step 2: Ensure Google Docs API is in the pip install**

`google-api-python-client` and `google-auth` should be in `requirements.txt`.

- [ ] **Step 3: Add `google_docs` section to config.yaml**

```yaml
google_docs:
  enabled: true
  credentials_path: "google_credentials.json"
  share_with: "254utkarsh@gmail.com"
```

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/daily_job_hunt.yml config.yaml requirements.txt
git commit -m "chore: remove tectonic, add Google Docs config"
```

---

### Task 9: End-to-End Test

- [ ] **Step 1: Run create_templates.py to create Google Doc templates**
- [ ] **Step 2: Refine templates visually in Google Docs**
- [ ] **Step 3: Update config.yaml with template IDs**
- [ ] **Step 4: Run pipeline locally with `--dry-run` to verify matching**
- [ ] **Step 5: Run full pipeline to generate Google Docs + PDFs**
- [ ] **Step 6: Verify: 2 pages, consistent spacing, no AI language, editable doc**
- [ ] **Step 7: Push and trigger GitHub Actions run**
- [ ] **Step 8: Verify email has Google Doc links + PDF attachments**

---

## Performance Comparison

| Step | LaTeX (current) | Google Docs (new) |
|------|----------------|-------------------|
| Install engine | ~60s (tectonic) | 0s (API calls) |
| Compile/generate per resume | ~5-10s (tectonic) | ~3s (clone+replace+export) |
| Failure rate | ~35% (special chars) | 0% (plain text) |
| Output | PDF only | Editable Google Doc + PDF |
| Total for 14 resumes | ~2-3 min | ~45s |
