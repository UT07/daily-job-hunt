"""Google Drive upload module for job automation pipeline.

Uploads compiled PDF artifacts (resumes + cover letters) to Google Drive
and returns shareable links. Complements S3 upload — Drive links don't expire.

Drive structure:
    Job Hunt/{date}/resumes/{filename}.pdf
    Job Hunt/{date}/cover-letters/{filename}.pdf

Required:
    google_credentials.json (service account with Drive API enabled)
    config.yaml → google_drive.enabled: true
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Dict, List, Optional

from scrapers.base import Job

logger = logging.getLogger(__name__)


def _authenticate(credentials_path: str):
    """Create a Google Drive service client from service account credentials."""
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _get_or_create_folder(drive_service, name: str,
                          parent_id: str = None) -> str:
    """Find or create a Drive folder. Returns the folder ID."""
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = drive_service.files().list(
        q=query, fields="files(id)", pageSize=1,
    ).execute()

    files = results.get("files", [])
    if files:
        return files[0]["id"]

    # Create the folder
    body = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        body["parents"] = [parent_id]

    folder = drive_service.files().create(
        body=body, fields="id",
    ).execute()

    logger.info(f"[DRIVE] Created folder: {name}")
    return folder["id"]


def _upload_file(drive_service, local_path: str, folder_id: str,
                 share_with: str = "") -> Optional[str]:
    """Upload a file to Drive and return a shareable view link.

    Returns the web view link on success, None on failure.
    """
    from googleapiclient.http import MediaFileUpload

    path = Path(local_path)
    if not path.exists():
        return None

    try:
        media = MediaFileUpload(str(path), mimetype="application/pdf")
        file_meta = {
            "name": path.name,
            "parents": [folder_id],
        }

        uploaded = drive_service.files().create(
            body=file_meta,
            media_body=media,
            fields="id,webViewLink",
        ).execute()

        file_id = uploaded["id"]
        web_link = uploaded.get("webViewLink", "")

        # Share with the user's personal email so they can view/download
        if share_with:
            try:
                drive_service.permissions().create(
                    fileId=file_id,
                    body={
                        "type": "user",
                        "role": "writer",
                        "emailAddress": share_with,
                    },
                    sendNotificationEmail=False,
                ).execute()
            except Exception as e:
                logger.warning(f"[DRIVE] Failed to share {path.name}: {e}")

        logger.info(f"[DRIVE] Uploaded {path.name}")
        return web_link

    except Exception as e:
        logger.error(f"[DRIVE] Failed to upload {local_path}: {e}")
        return None


def upload_artifacts(
    matched_jobs: List[Job],
    run_date: str,
    credentials_path: str,
    share_with: str = "",
    root_folder_id: str = "",
) -> Dict[str, Dict[str, str]]:
    """Upload all PDF artifacts for matched jobs to Google Drive.

    Args:
        matched_jobs: List of Job objects with tailored_pdf_path / cover_letter_pdf_path set.
        run_date: Date string (YYYY-MM-DD) for folder organization.
        credentials_path: Path to Google service account JSON.
        share_with: Email to share uploaded files with.
        root_folder_id: Optional root folder ID (creates "Job Hunt" folder if empty).

    Returns:
        Dict mapping job_id -> {"resume_drive_url": str, "cover_letter_drive_url": str}.
    """
    try:
        drive_service = _authenticate(credentials_path)
    except Exception as e:
        logger.error(f"[DRIVE] Authentication failed: {e}")
        return {}

    # Create folder hierarchy: Job Hunt/{date}/resumes, Job Hunt/{date}/cover-letters
    root_id = root_folder_id or _get_or_create_folder(drive_service, "Job Hunt")
    date_folder_id = _get_or_create_folder(drive_service, run_date, root_id)
    resumes_folder_id = _get_or_create_folder(drive_service, "resumes", date_folder_id)
    cls_folder_id = _get_or_create_folder(drive_service, "cover-letters", date_folder_id)

    # Share the date folder with user (inherits to children)
    if share_with:
        try:
            drive_service.permissions().create(
                fileId=date_folder_id,
                body={
                    "type": "user",
                    "role": "writer",
                    "emailAddress": share_with,
                },
                sendNotificationEmail=False,
            ).execute()
        except Exception:
            pass  # Folder-level share may fail if already shared

    results: Dict[str, Dict[str, str]] = {}

    for job in matched_jobs:
        job_urls: Dict[str, str] = {"resume_drive_url": "", "cover_letter_drive_url": ""}

        # Upload resume PDF
        if job.tailored_pdf_path and Path(job.tailored_pdf_path).exists():
            url = _upload_file(drive_service, job.tailored_pdf_path,
                               resumes_folder_id, share_with)
            if url:
                job_urls["resume_drive_url"] = url

        # Upload cover letter PDF
        if job.cover_letter_pdf_path and Path(job.cover_letter_pdf_path).exists():
            url = _upload_file(drive_service, job.cover_letter_pdf_path,
                               cls_folder_id, share_with)
            if url:
                job_urls["cover_letter_drive_url"] = url

        if job_urls["resume_drive_url"] or job_urls["cover_letter_drive_url"]:
            results[job.job_id] = job_urls

    uploaded_resumes = sum(1 for v in results.values() if v["resume_drive_url"])
    uploaded_cls = sum(1 for v in results.values() if v["cover_letter_drive_url"])
    logger.info(f"[DRIVE] Upload complete: {uploaded_resumes} resumes, {uploaded_cls} cover letters")

    return results


def upload_tracker(
    tracker_path: str,
    run_date: str,
    credentials_path: str,
    share_with: str = "",
    root_folder_id: str = "",
) -> Optional[str]:
    """Upload the Excel tracker to Google Drive.

    Returns the shareable web view link.
    """
    if not Path(tracker_path).exists():
        return None

    try:
        drive_service = _authenticate(credentials_path)
    except Exception as e:
        logger.error(f"[DRIVE] Authentication failed: {e}")
        return None

    from googleapiclient.http import MediaFileUpload

    root_id = root_folder_id or _get_or_create_folder(drive_service, "Job Hunt")

    try:
        media = MediaFileUpload(
            tracker_path,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        # Check if tracker already exists in root (update it)
        query = f"name='job_tracker_latest.xlsx' and '{root_id}' in parents and trashed=false"
        existing = drive_service.files().list(
            q=query, fields="files(id)", pageSize=1,
        ).execute().get("files", [])

        if existing:
            # Update existing file
            file_id = existing[0]["id"]
            updated = drive_service.files().update(
                fileId=file_id,
                media_body=media,
                fields="webViewLink",
            ).execute()
            link = updated.get("webViewLink", "")
        else:
            # Create new file
            uploaded = drive_service.files().create(
                body={
                    "name": "job_tracker_latest.xlsx",
                    "parents": [root_id],
                },
                media_body=media,
                fields="id,webViewLink",
            ).execute()
            file_id = uploaded["id"]
            link = uploaded.get("webViewLink", "")

            if share_with:
                drive_service.permissions().create(
                    fileId=file_id,
                    body={
                        "type": "user",
                        "role": "writer",
                        "emailAddress": share_with,
                    },
                    sendNotificationEmail=False,
                ).execute()

        logger.info(f"[DRIVE] Tracker uploaded → {link}")
        return link

    except Exception as e:
        logger.error(f"[DRIVE] Failed to upload tracker: {e}")
        return None
