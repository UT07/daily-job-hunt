"""Google Docs template engine for resume/cover letter generation.

Clones a template Google Doc, replaces {{PLACEHOLDER}} markers with
tailored content, and exports as PDF. The cloned doc remains editable
in Google Docs so the user can make changes before applying.
"""
from __future__ import annotations
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _get_credentials(credentials_path: str = "google_credentials.json"):
    """Load credentials — supports OAuth token, service account, and an env var.

    Priority:
    1. oauth_token.json (user's personal Google account — has Drive storage)
    2. GOOGLE_CREDENTIALS_JSON env var, written to a temp file
    3. Service account JSON at `credentials_path`

    Branch 2 is fed by `.github/workflows/daily_job_hunt.yml`, which runs
    `main.py`. It is NOT fed by the deployed stack, and must not be again.
    template.yaml declared a `GoogleCredentialsJson` parameter and wired it into
    JobHuntApi's environment from 5e21293 until 2026-09-30; abc0fe9 reverted the
    Google Docs approach the same day the parameter landed and never removed it,
    so deploy.yml passed the inline literal `GoogleCredentialsJson=
    PLACEHOLDER_SET_MANUALLY` on every deploy for six months. The live Lambda
    really did hold GOOGLE_CREDENTIALS_JSON=PLACEHOLDER_SET_MANUALLY.

    That was worse than the variable being unset, because branch 2 is reached on
    the strength of the variable being non-empty, not on its being credentials.
    Anything reaching here in the Lambda wrote the placeholder to /tmp and failed
    in branch 3 with `JSONDecodeError: Expecting value: line 1 column 1`, which
    reads as corrupt credentials. Unset, the same call raises FileNotFoundError
    naming google_credentials.json, which is the truth. See
    tests/unit/test_deploy_parameter_overrides.py for the guards that now keep a
    NoEcho parameter from being supplied as a hardcoded literal.
    """
    import json

    # 1. Try OAuth token first (user's personal account)
    oauth_path = Path(credentials_path).parent / "oauth_token.json"
    if oauth_path.exists():
        from google.oauth2.credentials import Credentials as OAuthCredentials
        with open(oauth_path) as f:
            token = json.load(f)
        creds = OAuthCredentials(
            token=token.get("token"),
            refresh_token=token.get("refresh_token"),
            token_uri=token.get("token_uri"),
            client_id=token.get("client_id"),
            client_secret=token.get("client_secret"),
            scopes=token.get("scopes"),
        )
        return creds

    # 2. Env var fallback (daily_job_hunt.yml; never the deployed stack)
    if not Path(credentials_path).exists() and os.environ.get("GOOGLE_CREDENTIALS_JSON"):
        credentials_path = "/tmp/google_credentials.json"
        with open(credentials_path, "w") as f:
            f.write(os.environ["GOOGLE_CREDENTIALS_JSON"])

    # 3. Service account JSON
    from google.oauth2.service_account import Credentials
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
    drive.permissions().create(
        fileId=doc_id,
        body={"type": "user", "role": "writer", "emailAddress": email},
        sendNotificationEmail=False,
    ).execute()
    doc = drive.files().get(fileId=doc_id, fields="webViewLink").execute()
    link = doc.get("webViewLink", "")
    logger.info(f"[GDOCS] Shared {doc_id} with {email}")
    return link


def format_resume_doc(doc_id: str,
                      credentials_path: str = "google_credentials.json"):
    """Apply formatting to a resume doc after placeholder replacement.

    Fixes formatting lost by replaceAllText:
    - Bold skill category names (e.g., "Cloud & Infrastructure:")
    - Bold project titles (e.g., "Cloud-Native Monitoring Platform")
    - Make URLs clickable with styled hyperlinks
    """
    docs, _ = _get_services(credentials_path)

    # Re-read the document to get current content and character indices
    doc = docs.documents().get(documentId=doc_id).execute()
    body = doc.get("body", {}).get("content", [])

    requests: List[dict] = []

    for element in body:
        if "paragraph" not in element:
            continue
        para = element["paragraph"]

        # Reconstruct full paragraph text and its start index
        text = ""
        for elem in para.get("elements", []):
            if "textRun" in elem:
                text += elem["textRun"].get("content", "")

        start_index = element.get("startIndex", 0)

        # 1. Bold skill category names — lines like "Category Name: item1, item2"
        #    Pattern: starts with capitalized word(s), may include &/-, followed by ":"
        skill_match = re.match(r'^([A-Z][A-Za-z &/\-]+):\s', text)
        if skill_match:
            cat_text = skill_match.group(1) + ":"
            requests.append({
                "updateTextStyle": {
                    "range": {
                        "startIndex": start_index,
                        "endIndex": start_index + len(cat_text),
                    },
                    "textStyle": {"bold": True},
                    "fields": "bold",
                }
            })

        # 2. Bold project titles — lines containing a parenthesised URL,
        #    e.g., "Cloud-Native Monitoring Platform (github.com/...)"
        project_match = re.match(r'^(.+?)\s*\((?:https?://)?[a-z0-9]', text)
        if project_match and not skill_match:
            title_text = project_match.group(1)
            # Only bold if the title portion is reasonably short (< 80 chars)
            # to avoid false positives on long prose lines
            if len(title_text) < 80:
                requests.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": start_index,
                            "endIndex": start_index + len(title_text),
                        },
                        "textStyle": {"bold": True},
                        "fields": "bold",
                    }
                })

        # 3. Make URLs clickable
        for url_match in re.finditer(r'https?://[^\s\)\]>]+', text):
            url = url_match.group()
            url_start = start_index + url_match.start()
            url_end = start_index + url_match.end()
            requests.append({
                "updateTextStyle": {
                    "range": {
                        "startIndex": url_start,
                        "endIndex": url_end,
                    },
                    "textStyle": {
                        "link": {"url": url},
                        "foregroundColor": {
                            "color": {
                                "rgbColor": {
                                    "red": 0.02,
                                    "green": 0.35,
                                    "blue": 0.75,
                                }
                            }
                        },
                        "underline": True,
                    },
                    "fields": "link,foregroundColor,underline",
                }
            })

    if requests:
        # Batch update in chunks of 100 to stay within API limits
        chunk_size = 100
        for i in range(0, len(requests), chunk_size):
            chunk = requests[i:i + chunk_size]
            docs.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": chunk},
            ).execute()
        logger.info(f"[GDOCS] Applied {len(requests)} formatting fixes to {doc_id}")
    else:
        logger.info(f"[GDOCS] No formatting fixes needed for {doc_id}")


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
    format_resume_doc(doc_id, credentials_path)
    pdf_path = export_pdf(doc_id, output_pdf_path, credentials_path)

    doc_url = ""
    if share_with:
        doc_url = share_doc(doc_id, share_with, credentials_path)

    return {"doc_id": doc_id, "doc_url": doc_url, "pdf_path": pdf_path}
