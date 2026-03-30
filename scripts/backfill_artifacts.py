"""Backfill artifacts for jobs that have descriptions but no resume/cover letter/contacts.

Usage: python scripts/backfill_artifacts.py

Processes all jobs in Supabase where resume_s3_url IS NULL but description is present.
For each job: tailor resume → compile LaTeX → upload S3 → cover letter → batch contacts → update Supabase.
"""
import importlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# Load .env
env_path = Path(__file__).parent.parent / ".env"
for line in open(env_path):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        if key.strip() and val:
            os.environ.setdefault(key.strip(), val)

import yaml

from ai_client import AIClient
from contact_finder import find_contacts_batch
from cover_letter import generate_cover_letter
from db_client import SupabaseClient
from latex_compiler import compile_tex_to_pdf
from s3_uploader import upload_file
from scrapers.base import Job
from tailorer import tailor_resume

db = SupabaseClient.from_env()
config = yaml.safe_load(open(Path(__file__).parent.parent / "config.yaml"))
ai_client = AIClient.from_config(config)
USER_ID = "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39"
TODAY = time.strftime("%Y-%m-%d")
BUCKET = os.environ.get("S3_BUCKET_NAME", os.environ.get("S3_BUCKET", "utkarsh-job-hunt"))

# Load base resumes
base_resumes = {}
for f in (Path(__file__).parent.parent / "resumes").glob("*.tex"):
    base_resumes[f.stem] = f.read_text()
log.info(f"Resumes: {list(base_resumes.keys())}")

# Get incomplete jobs
result = (
    db.client.table("jobs")
    .select("*")
    .eq("user_id", USER_ID)
    .is_("resume_s3_url", "null")
    .execute()
)
all_jobs = [j for j in result.data if j.get("description") and len(j.get("description", "")) > 50]
log.info(f"Jobs to process: {len(all_jobs)}")

out_resumes = Path(f"output/{TODAY}/resumes")
out_resumes.mkdir(parents=True, exist_ok=True)
out_cl = Path(f"output/{TODAY}/cover_letters")
out_cl.mkdir(parents=True, exist_ok=True)

processed = errors = 0
job_objects = []

for i, jd in enumerate(all_jobs):
    try:
        log.info(f"\n[{i+1}/{len(all_jobs)}] {jd['title']} at {jd['company']}")
        rtype = jd.get("matched_resume") or "sre_devops"
        if rtype not in base_resumes:
            rtype = list(base_resumes.keys())[0]

        job = Job(
            title=jd["title"],
            company=jd["company"],
            description=jd["description"],
            location=jd.get("location", ""),
            apply_url=jd.get("apply_url", ""),
            source=jd.get("source", "linkedin"),
        )
        job.job_id = jd["job_id"]
        job.match_score = jd.get("match_score", 0)
        job.matched_resume = rtype

        # Step 1: Tailor
        log.info("  Tailoring...")
        tailor_resume(
            job=job,
            base_tex=base_resumes[rtype],
            ai_client=ai_client,
            output_dir=out_resumes,
        )
        if not job.tailored_tex_path or not Path(job.tailored_tex_path).exists():
            log.warning("  No .tex produced")
            errors += 1
            continue

        # Step 2: Compile
        log.info("  Compiling LaTeX...")
        pdf_path = compile_tex_to_pdf(job.tailored_tex_path, str(out_resumes))
        if not pdf_path or not Path(pdf_path).exists():
            log.warning("  Compile failed")
            errors += 1
            continue
        job.tailored_pdf_path = pdf_path

        # Step 3: Upload resume
        log.info("  Uploading...")
        sc = re.sub(r"[^a-zA-Z0-9]", "_", jd["company"])[:30]
        st = re.sub(r"[^a-zA-Z0-9]", "_", jd["title"])[:30]
        fn = f"Utkarsh_Singh_{st}_{sc}_{TODAY}"
        s3_url = upload_file(
            pdf_path, f"users/{USER_ID}/{TODAY}/resumes/{fn}.pdf", BUCKET
        )
        if not s3_url:
            log.warning("  S3 upload failed")
            errors += 1
            continue

        # Step 4: Cover letter
        cl_s3 = None
        try:
            log.info("  Cover letter...")
            tailored_tex = Path(job.tailored_tex_path).read_text()
            generate_cover_letter(
                job=job,
                resume_tex=tailored_tex,
                ai_client=ai_client,
                output_dir=out_cl,
            )
            if hasattr(job, "cover_letter_tex_path") and job.cover_letter_tex_path and Path(job.cover_letter_tex_path).exists():
                cl_pdf = compile_tex_to_pdf(job.cover_letter_tex_path, str(out_cl))
                if cl_pdf and Path(cl_pdf).exists():
                    cl_s3 = upload_file(
                        cl_pdf,
                        f"users/{USER_ID}/{TODAY}/cover_letters/{fn}_CL.pdf",
                        BUCKET,
                    )
        except Exception as e:
            log.warning(f"  CL: {e}")

        # Step 5: Update Supabase immediately (save progress per job)
        update = {
            "resume_s3_url": s3_url,
            "tailoring_model": getattr(job, "tailoring_model", "council:consensus") or "council:consensus",
            "matched_resume": rtype,
        }
        if cl_s3:
            update["cover_letter_s3_url"] = cl_s3
        db.client.table("jobs").update(update).eq("job_id", jd["job_id"]).execute()
        processed += 1
        log.info(f"  DONE — saved to Supabase")
    except Exception as e:
        log.error(f"  ERROR: {e}")
        errors += 1

# Step 6: Batch contacts for all processed jobs, then update Supabase
result2 = db.client.table("jobs").select("*").eq("user_id", USER_ID).not_.is_("resume_s3_url", "null").is_("linkedin_contacts", "null").execute()
jobs_needing_contacts = result2.data
if jobs_needing_contacts:
    log.info(f"\nFinding contacts for {len(jobs_needing_contacts)} jobs...")
    contact_jobs = []
    for jd in jobs_needing_contacts:
        j = Job(title=jd["title"], company=jd["company"], description=jd.get("description",""), location=jd.get("location",""), apply_url=jd.get("apply_url",""), source=jd.get("source",""))
        j.job_id = jd["job_id"]
        contact_jobs.append(j)
    try:
        find_contacts_batch(contact_jobs, ai_client)
        for j in contact_jobs:
            if j.linkedin_contacts:
                contacts = j.linkedin_contacts if isinstance(j.linkedin_contacts, str) else json.dumps(j.linkedin_contacts)
                db.client.table("jobs").update({"linkedin_contacts": contacts}).eq("job_id", j.job_id).execute()
                log.info(f"  Contacts saved for {j.title}")
    except Exception as e:
        log.warning(f"Contacts batch failed: {e}")

log.info(f"\n=== RESULTS: {processed}/{len(all_jobs)} processed, {errors} errors ===")
