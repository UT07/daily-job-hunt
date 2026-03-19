"""Google Docs/Drive API client for resume and cover letter generation.

Replaces the LaTeX → pdflatex pipeline with:
  clone Google Doc template → replace {{PLACEHOLDER}}s → export PDF

Requires a GCP service account with Docs + Drive APIs enabled.
Credentials JSON path is configured in config.yaml → google_docs.credentials_path.
"""

from __future__ import annotations
import logging
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]


def authenticate(credentials_path: str) -> tuple:
    """Authenticate with Google APIs using a service account.

    Returns (docs_service, drive_service).
    """
    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    docs_service = build("docs", "v1", credentials=creds, cache_discovery=False)
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
    logger.info("[GDOCS] Authenticated with Google APIs")
    return docs_service, drive_service


def clone_template(drive_service, template_doc_id: str, title: str,
                   folder_id: str = None) -> str:
    """Copy a template Google Doc. Returns the new document ID."""
    body = {"name": title}
    if folder_id:
        body["parents"] = [folder_id]

    copy = drive_service.files().copy(
        fileId=template_doc_id,
        body=body,
        fields="id",
    ).execute()

    doc_id = copy["id"]
    logger.info(f"[GDOCS] Cloned template {template_doc_id[:8]}... → {doc_id[:8]}... ({title})")
    return doc_id


def replace_placeholders(docs_service, doc_id: str,
                         replacements: dict[str, str]) -> None:
    """Replace {{KEY}} placeholders in a Google Doc with actual content.

    Args:
        replacements: dict mapping placeholder names to replacement text.
            Keys should NOT include the {{ }} delimiters — they are added
            automatically. Example: {"SUMMARY": "My summary text..."}
    """
    requests = []
    for key, value in replacements.items():
        requests.append({
            "replaceAllText": {
                "containsText": {
                    "text": "{{" + key + "}}",
                    "matchCase": True,
                },
                "replaceText": value or "",
            }
        })

    if requests:
        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": requests},
        ).execute()
        logger.info(f"[GDOCS] Replaced {len(requests)} placeholders in {doc_id[:8]}...")


def export_pdf(drive_service, doc_id: str, output_path: str) -> str:
    """Export a Google Doc as PDF to a local file.

    Returns the output path on success, empty string on failure.
    """
    try:
        pdf_bytes = drive_service.files().export(
            fileId=doc_id,
            mimeType="application/pdf",
        ).execute()

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(pdf_bytes)
        logger.info(f"[GDOCS] Exported PDF → {output.name}")
        return str(output)

    except Exception as e:
        logger.error(f"[GDOCS] PDF export failed for {doc_id[:8]}...: {e}")
        return ""


def get_doc_url(doc_id: str) -> str:
    """Return the shareable Google Docs edit URL."""
    return f"https://docs.google.com/document/d/{doc_id}/edit"


def share_doc(drive_service, doc_id: str, email: str,
              role: str = "writer") -> None:
    """Share a Google Doc with an email address."""
    try:
        drive_service.permissions().create(
            fileId=doc_id,
            body={
                "type": "user",
                "role": role,
                "emailAddress": email,
            },
            sendNotificationEmail=False,
        ).execute()
        logger.debug(f"[GDOCS] Shared {doc_id[:8]}... with {email} ({role})")
    except Exception as e:
        logger.warning(f"[GDOCS] Failed to share {doc_id[:8]}... with {email}: {e}")


def get_doc_text(docs_service, doc_id: str) -> str:
    """Get the plain text content of a Google Doc (for scoring)."""
    doc = docs_service.documents().get(documentId=doc_id).execute()
    text_parts = []
    for element in doc.get("body", {}).get("content", []):
        paragraph = element.get("paragraph")
        if paragraph:
            for elem in paragraph.get("elements", []):
                text_run = elem.get("textRun")
                if text_run:
                    text_parts.append(text_run.get("content", ""))
    return "".join(text_parts)


def delete_doc(drive_service, doc_id: str) -> None:
    """Delete a Google Doc (for cleanup / error recovery)."""
    try:
        drive_service.files().delete(fileId=doc_id).execute()
        logger.debug(f"[GDOCS] Deleted {doc_id[:8]}...")
    except Exception as e:
        logger.warning(f"[GDOCS] Failed to delete {doc_id[:8]}...: {e}")
