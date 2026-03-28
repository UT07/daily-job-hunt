#!/usr/bin/env python3
"""Send a test email using REAL data from the latest pipeline run.

Reads run_metadata.json from the GitHub Actions artifact and sends
the email using the new format (with Excel attachment, asset links, etc.).

Run: python3 test_email.py
"""
import json
import os
import sys
from pathlib import Path

# Load .env
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                val = val.strip().strip("'\"")
                if key.strip() and val:
                    os.environ.setdefault(key.strip(), val)

from scrapers.base import Job
from excel_tracker import create_or_update_tracker
from email_notifier import send_summary_email

# --- Auto-detect latest artifact directory ---
ARTIFACTS_ROOT = Path("/tmp/job-hunt-artifacts")
if not ARTIFACTS_ROOT.exists():
    print(f"ERROR: {ARTIFACTS_ROOT} not found. Download artifacts first:")
    print("  gh run list --limit 3")
    print("  gh run download <RUN_ID> --dir /tmp/job-hunt-artifacts")
    sys.exit(1)

# Find the latest job-hunt-* folder
artifact_dirs = sorted(ARTIFACTS_ROOT.glob("job-hunt-*"), reverse=True)
if not artifact_dirs:
    print(f"ERROR: No job-hunt-* folders in {ARTIFACTS_ROOT}")
    sys.exit(1)

ARTIFACT_DIR = artifact_dirs[0]

# Find run_metadata.json inside the date subfolder
metadata_files = list(ARTIFACT_DIR.glob("*/run_metadata.json"))
if not metadata_files:
    print(f"ERROR: No run_metadata.json found in {ARTIFACT_DIR}")
    sys.exit(1)

metadata_path = metadata_files[0]
date_dir = metadata_path.parent
run_date = date_dir.name

print(f"Using artifact: {ARTIFACT_DIR.name} / {run_date}")

with open(metadata_path) as f:
    meta = json.load(f)

# Handle both old format (matched_details) and new format (matched_jobs)
matched_list = meta.get("matched_details") or meta.get("matched_jobs", [])
raw_count = meta.get("raw_jobs", meta.get("jobs_scraped", 0))
unique_count = meta.get("unique_jobs", meta.get("jobs_unique", 0))

print(f"Loaded run metadata: {len(matched_list)} matched jobs from {meta['run_date']}")

# --- Build Job objects from real metadata ---
jobs = []
for detail in matched_list:
    company = detail.get("company", "") or "Unknown"

    # Check if resume/cover letter PDFs exist in artifacts
    safe_company = "".join(c for c in company if c.isalnum() or c in " _-")[:20].strip().replace(" ", "_")

    resume_pdf = ""
    resumes_dir = date_dir / "resumes"
    if resumes_dir.exists():
        for f in resumes_dir.glob("*.pdf"):
            if safe_company[:15].lower().replace(" ", "_") in f.name.lower():
                resume_pdf = str(f)
                break

    cl_pdf = ""
    cl_dir = date_dir / "cover_letters"
    if cl_dir.exists():
        for f in cl_dir.glob("*.pdf"):
            if safe_company[:15].lower().replace(" ", "_") in f.name.lower():
                cl_pdf = str(f)
                break

    job = Job(
        title=detail["title"],
        company=company,
        location="Ireland",
        description=detail.get("description", ""),
        apply_url=detail.get("apply_url", ""),
        source="test",
        posted_date=meta["run_date"],
        match_score=detail.get("score", 0),
        ats_score=detail.get("ats_score", 0),
        hiring_manager_score=detail.get("hiring_manager_score", 0),
        tech_recruiter_score=detail.get("tech_recruiter_score", 0),
        initial_match_score=detail.get("score", 0),
        initial_ats_score=detail.get("ats_score", 0),
        initial_hm_score=detail.get("hiring_manager_score", 0),
        initial_tr_score=detail.get("tech_recruiter_score", 0),
        matched_resume=detail.get("resume_type", "sre_devops"),
        tailored_pdf_path=resume_pdf,
        cover_letter_pdf_path=cl_pdf,
        linkedin_contacts=json.dumps([
            {
                "role": "Engineering Manager",
                "search_url": f"https://www.linkedin.com/search/results/people/?keywords=engineering+manager+{company.split()[0].lower()}",
                "message": f"Hi! I'm applying for the {detail['title']} role and would love to connect.",
                "why": "Likely hiring manager",
            },
            {
                "role": "Senior SRE",
                "search_url": f"https://www.linkedin.com/search/results/people/?keywords=senior+engineer+{company.split()[0].lower()}",
                "message": f"Hi! I'd love to learn about the team working on {detail['title']}.",
                "why": "Potential peer",
            },
            {
                "role": "Technical Recruiter",
                "search_url": f"https://www.linkedin.com/search/results/people/?keywords=recruiter+{company.split()[0].lower()}",
                "message": f"Hi! I've applied for the {detail['title']} role and wanted to connect.",
                "why": "Can expedite application",
            },
        ]),
    )
    jobs.append(job)

print(f"Built {len(jobs)} Job objects from real data")

# --- Generate test tracker with real data ---
tracker_path = Path("/tmp/test_email_tracker.xlsx")
create_or_update_tracker(jobs, str(tracker_path), meta["run_date"])
print(f"Tracker created: {tracker_path}")

# --- Send test email ---
gmail_addr = os.environ.get("GMAIL_ADDRESS", "")
gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "")
notify_email = os.environ.get("NOTIFY_EMAIL", gmail_addr)

if not gmail_addr or not gmail_pass:
    print("ERROR: GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env")
    sys.exit(1)

print(f"Sending test email to {notify_email}...")
success = send_summary_email(
    matched_jobs=jobs,
    raw_count=raw_count,
    unique_count=unique_count,
    gmail_address=gmail_addr,
    gmail_app_password=gmail_pass,
    recipient=notify_email,
    tracker_path=str(tracker_path),
    tracker_url=None,
)

if success:
    print("Test email sent! Check your inbox.")
else:
    print("Failed to send email. Check credentials in .env")
