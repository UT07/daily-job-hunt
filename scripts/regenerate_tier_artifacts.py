#!/usr/bin/env python3
"""Regenerate artifacts (resume/cover letter/contacts) for jobs by tier.

Per Phase 2.10 spec with user override (contacts for all tiers):
  S: resume + cover letter + contacts
  A: resume + cover letter + contacts
  B: resume only           + contacts
  C: contacts only (no new resume/cover)
  D: contacts only (no new resume/cover)

Usage:
  python scripts/regenerate_tier_artifacts.py --action contacts --tier ALL
  python scripts/regenerate_tier_artifacts.py --action resumes  --tier S
  python scripts/regenerate_tier_artifacts.py --action covers   --tier A
  python scripts/regenerate_tier_artifacts.py --action all      --tier B --max 5
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

# Load .env
env_path = Path(__file__).parent.parent / ".env"
for line in open(env_path):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "lambdas" / "pipeline"))
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-1")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

import yaml

from ai_client import AIClient
from contact_finder import find_contacts_batch
from cover_letter import generate_cover_letter
from db_client import SupabaseClient
from latex_compiler import compile_tex_to_pdf
from s3_uploader import upload_file
from scrapers.base import Job
from tailorer import tailor_resume

USER_ID = "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39"
TODAY = time.strftime("%Y-%m-%d")
BUCKET = os.environ.get("S3_BUCKET_NAME", os.environ.get("S3_BUCKET", "utkarsh-job-hunt"))

# Tier rules: what actions to run per tier
TIER_ACTIONS = {
    "S": {"resume": True,  "cover": True,  "contacts": True},
    "A": {"resume": True,  "cover": True,  "contacts": True},
    "B": {"resume": True,  "cover": False, "contacts": True},
    "C": {"resume": False, "cover": False, "contacts": True},
    "D": {"resume": False, "cover": False, "contacts": True},
}


def job_from_row(row: dict) -> Job:
    j = Job(
        title=row["title"],
        company=row["company"],
        description=row.get("description", ""),
        location=row.get("location", ""),
        apply_url=row.get("apply_url", ""),
        source=row.get("source", "linkedin"),
    )
    j.job_id = row["job_id"]
    j.match_score = row.get("match_score", 0)
    j.ats_score = row.get("ats_score", 0)
    j.hiring_manager_score = row.get("hiring_manager_score", 0)
    j.tech_recruiter_score = row.get("tech_recruiter_score", 0)
    j.matched_resume = row.get("matched_resume") or "sre_devops"
    return j


def safe_name(s: str, n: int = 30) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "_", s or "")[:n]


def process_resume(job, jd, base_tex, ai_client, out_resumes, db):
    tailor_resume(job=job, base_tex=base_tex, ai_client=ai_client, output_dir=out_resumes)
    if not job.tailored_tex_path or not Path(job.tailored_tex_path).exists():
        return None, None
    pdf_path = compile_tex_to_pdf(job.tailored_tex_path, str(out_resumes))
    if not pdf_path or not Path(pdf_path).exists():
        return None, None
    job.tailored_pdf_path = pdf_path
    fn = f"Utkarsh_Singh_{safe_name(jd['title'])}_{safe_name(jd['company'])}_{TODAY}"
    s3_url = upload_file(pdf_path, f"users/{USER_ID}/{TODAY}/resumes/{fn}.pdf", BUCKET)
    return pdf_path, s3_url


def process_cover_letter(job, ai_client, out_cl, fn_base):
    tailored_tex = Path(job.tailored_tex_path).read_text()
    generate_cover_letter(job=job, resume_tex=tailored_tex, ai_client=ai_client, output_dir=out_cl)
    if not (hasattr(job, "cover_letter_tex_path") and job.cover_letter_tex_path
            and Path(job.cover_letter_tex_path).exists()):
        return None
    cl_pdf = compile_tex_to_pdf(job.cover_letter_tex_path, str(out_cl))
    if not (cl_pdf and Path(cl_pdf).exists()):
        return None
    return upload_file(cl_pdf, f"users/{USER_ID}/{TODAY}/cover_letters/{fn_base}_CL.pdf", BUCKET)


def main(action: str, tier_filter: str, max_jobs: int | None, skip_existing: bool):
    db = SupabaseClient.from_env()
    config = yaml.safe_load(open(Path(__file__).parent.parent / "config.yaml"))
    ai_client = AIClient.from_config(config)

    # Load base resumes from user_resumes table
    r = db.client.table("user_resumes").select("resume_key, tex_content").eq(
        "user_id", USER_ID
    ).execute()
    base_resumes = {row["resume_key"]: row["tex_content"] for row in r.data}
    if not base_resumes:
        # Fallback to filesystem
        for f in (Path(__file__).parent.parent / "resumes").glob("*.tex"):
            base_resumes[f.stem] = f.read_text()
    log.info(f"Loaded base resumes: {list(base_resumes.keys())}")

    # Fetch jobs for target tier(s) or score threshold
    query = db.client.table("jobs").select("*").eq("user_id", USER_ID)
    if tier_filter.startswith(">="):
        min_score = float(tier_filter[2:])
        query = query.gte("match_score", min_score)
        filter_desc = f"match_score >= {min_score}"
    else:
        tiers = ["S", "A", "B", "C", "D"] if tier_filter == "ALL" else [tier_filter]
        query = query.in_("score_tier", tiers)
        filter_desc = f"tiers {tiers}"
    jobs = query.execute().data
    # Sort by score desc so highest-value work happens first
    jobs.sort(key=lambda j: j.get("match_score") or 0, reverse=True)
    log.info(f"Fetched {len(jobs)} jobs for {filter_desc}")

    if max_jobs:
        jobs = jobs[:max_jobs]
        log.info(f"Limited to first {max_jobs} jobs")

    # Output dirs
    out_resumes = Path(f"output/{TODAY}/resumes"); out_resumes.mkdir(parents=True, exist_ok=True)
    out_cl = Path(f"output/{TODAY}/cover_letters"); out_cl.mkdir(parents=True, exist_ok=True)

    results = {"resume": 0, "cover": 0, "contacts_batched": 0, "errors": 0, "skipped": 0}
    contact_jobs = []

    for i, jd in enumerate(jobs, 1):
        tier = jd.get("score_tier") or "D"
        rules = TIER_ACTIONS[tier]
        title = jd.get("title", "?")[:40]
        log.info(f"[{i}/{len(jobs)}] tier={tier} {title} @ {jd.get('company','?')[:25]}")

        job = job_from_row(jd)
        fn_base = f"Utkarsh_Singh_{safe_name(jd['title'])}_{safe_name(jd['company'])}_{TODAY}"

        # Resume
        if action in ("resumes", "all") and rules["resume"]:
            if skip_existing and jd.get("resume_s3_url"):
                log.info(f"  skip resume (exists)")
                results["skipped"] += 1
            else:
                rtype = job.matched_resume if job.matched_resume in base_resumes else list(base_resumes.keys())[0]
                try:
                    pdf_path, s3_url = process_resume(job, jd, base_resumes[rtype], ai_client, out_resumes, db)
                    if s3_url:
                        update = {
                            "resume_s3_url": s3_url,
                            "tailoring_model": f"{getattr(job, 'tailoring_provider', 'council')}:{getattr(job, 'tailoring_model', 'consensus')}",
                            "matched_resume": rtype,
                        }
                        db.client.table("jobs").update(update).eq("job_id", jd["job_id"]).execute()
                        results["resume"] += 1
                        log.info(f"  ✓ resume uploaded")
                    else:
                        log.warning(f"  ✗ resume compile/upload failed")
                        results["errors"] += 1
                        continue
                except Exception as e:
                    log.error(f"  ✗ resume error: {e}")
                    results["errors"] += 1
                    continue

        # Cover letter (depends on tailored resume existing)
        if action in ("covers", "all") and rules["cover"]:
            if skip_existing and jd.get("cover_letter_s3_url"):
                log.info(f"  skip cover (exists)")
                results["skipped"] += 1
                continue
            # Try to discover tailored .tex from disk if not set on job object
            if not (hasattr(job, "tailored_tex_path") and job.tailored_tex_path):
                tex_candidate = out_resumes / f"{fn_base}.tex"
                if tex_candidate.exists():
                    job.tailored_tex_path = str(tex_candidate)
                    log.info(f"  found existing .tex on disk")
                else:
                    # Glob for partial match (title may be truncated differently)
                    matches = list(out_resumes.glob(
                        f"Utkarsh_Singh_*{safe_name(jd.get('company',''), 15)}*{TODAY}.tex"
                    ))
                    if matches:
                        job.tailored_tex_path = str(matches[0])
                        log.info(f"  found existing .tex via glob: {matches[0].name}")
            if not (hasattr(job, "tailored_tex_path") and job.tailored_tex_path):
                log.warning(f"  skip cover — no tailored .tex (run resume action first)")
            else:
                try:
                    cl_s3 = process_cover_letter(job, ai_client, out_cl, fn_base)
                    if cl_s3:
                        db.client.table("jobs").update({"cover_letter_s3_url": cl_s3}).eq(
                            "job_id", jd["job_id"]
                        ).execute()
                        results["cover"] += 1
                        log.info(f"  ✓ cover letter uploaded")
                    else:
                        log.warning(f"  ✗ cover letter failed")
                except Exception as e:
                    log.error(f"  ✗ cover letter error: {e}")
                    results["errors"] += 1

        # Contacts (batched at end)
        if action in ("contacts", "all") and rules["contacts"]:
            if skip_existing and jd.get("linkedin_contacts"):
                log.info(f"  skip contacts (exists)")
                results["skipped"] += 1
            else:
                contact_jobs.append((job, jd["job_id"]))

    # Batch contact lookup
    if contact_jobs:
        log.info(f"\nFinding contacts for {len(contact_jobs)} jobs...")
        try:
            find_contacts_batch([j for j, _ in contact_jobs], ai_client)
            for job, job_id in contact_jobs:
                if hasattr(job, "linkedin_contacts") and job.linkedin_contacts:
                    db.client.table("jobs").update({
                        "linkedin_contacts": job.linkedin_contacts
                    }).eq("job_id", job_id).execute()
                    results["contacts_batched"] += 1
            log.info(f"  ✓ {results['contacts_batched']} contact sets saved")
        except Exception as e:
            log.error(f"contact batch error: {e}")
            results["errors"] += 1

    log.info(f"\n=== Done ===")
    log.info(f"  Resumes: {results['resume']}")
    log.info(f"  Covers:  {results['cover']}")
    log.info(f"  Contacts: {results['contacts_batched']}")
    log.info(f"  Skipped: {results['skipped']}")
    log.info(f"  Errors:  {results['errors']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--action", required=True, choices=["resumes", "covers", "contacts", "all"])
    p.add_argument("--tier", required=True,
                   help="Tier letter (S/A/B/C/D), ALL, or score threshold like '>=75'")
    p.add_argument("--max", type=int, default=None, dest="max_jobs")
    p.add_argument("--skip-existing", action="store_true",
                   help="Skip jobs that already have this artifact (default: regenerate)")
    args = p.parse_args()
    main(args.action, args.tier, args.max_jobs, args.skip_existing)
