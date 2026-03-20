"""Google Docs template engine for resume/cover letter generation.

Clones a template Google Doc, replaces {{PLACEHOLDER}} markers with
tailored content, and exports as PDF. The cloned doc remains editable
in Google Docs so the user can make changes before applying.
"""
from __future__ import annotations
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
    drive.permissions().create(
        fileId=doc_id,
        body={"type": "user", "role": "writer", "emailAddress": email},
        sendNotificationEmail=False,
    ).execute()
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
