#!/usr/bin/env python3
"""Quick smoke test for Google Drive API credentials.

Run after setting up the service account to verify everything works:
    python scripts/test_drive_connection.py

What it checks:
    1. google_credentials.json exists and is valid JSON
    2. Service account can authenticate with Drive API
    3. Can create, list, and delete a test folder
    4. Reports the service account email (for sharing folders)
"""

import json
import sys
from pathlib import Path


def main():
    creds_path = Path("google_credentials.json")

    # 1. Check credentials file
    print("[1/4] Checking credentials file...")
    if not creds_path.exists():
        print(f"  FAIL: {creds_path} not found")
        print("  Run: ./scripts/setup_gcp_drive.sh <project-id>")
        sys.exit(1)

    try:
        creds_data = json.loads(creds_path.read_text())
        sa_email = creds_data.get("client_email", "???")
        project = creds_data.get("project_id", "???")
        print(f"  OK: Service account: {sa_email}")
        print(f"      Project: {project}")
    except json.JSONDecodeError:
        print(f"  FAIL: {creds_path} is not valid JSON")
        sys.exit(1)

    # 2. Authenticate
    print("[2/4] Authenticating with Google Drive API...")
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        creds = Credentials.from_service_account_file(
            str(creds_path),
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        print("  OK: Authenticated successfully")
    except Exception as e:
        print(f"  FAIL: {e}")
        print("  Check that the Drive API is enabled in your GCP project:")
        print(f"  https://console.cloud.google.com/apis/library/drive.googleapis.com?project={project}")
        sys.exit(1)

    # 3. Create and delete a test folder
    print("[3/4] Testing folder create + list...")
    try:
        folder = drive.files().create(
            body={
                "name": "_drive_test_delete_me",
                "mimeType": "application/vnd.google-apps.folder",
            },
            fields="id,name",
        ).execute()
        folder_id = folder["id"]
        print(f"  OK: Created test folder (id: {folder_id})")

        # List to verify
        results = drive.files().list(
            q=f"name='_drive_test_delete_me' and trashed=false",
            fields="files(id)",
            pageSize=1,
        ).execute()
        assert len(results.get("files", [])) > 0
        print("  OK: Folder visible in listing")

        # Clean up
        drive.files().delete(fileId=folder_id).execute()
        print("  OK: Cleaned up test folder")
    except Exception as e:
        print(f"  FAIL: {e}")
        sys.exit(1)

    # 4. Summary
    print("[4/4] Summary")
    print(f"  Service account: {sa_email}")
    print(f"  Project: {project}")
    print("")
    print("  Everything works! Next steps:")
    print("  1. base64 -w 0 < google_credentials.json | pbcopy")
    print("  2. Add GOOGLE_CREDENTIALS_JSON secret in GitHub repo settings")
    print("  3. The pipeline will upload PDFs to Drive on next run")
    print("")
    print(f"  NOTE: Files uploaded by the service account are owned by {sa_email}.")
    print(f"  The pipeline shares them with 254utkarsh@gmail.com (from config.yaml).")


if __name__ == "__main__":
    main()
