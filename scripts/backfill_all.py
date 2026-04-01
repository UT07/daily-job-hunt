"""Comprehensive data backfill for all jobs on the dashboard.

Ensures every job has complete, consistent data across all fields the
frontend expects. Runs in 5 phases:

  Phase 1: Backfill descriptions from jobs_raw (for pipeline jobs with empty descriptions)
  Phase 2: Re-score jobs to populate key_matches/gaps (for jobs missing these fields)
  Phase 3: Generate tailored resumes (for jobs with descriptions but no resume PDF)
  Phase 4: Generate cover letters (for jobs with resumes but no cover letter)
  Phase 5: Find LinkedIn contacts (for jobs with resumes but no contacts)

Usage:
  python scripts/backfill_all.py                  # Run all phases
  python scripts/backfill_all.py --phase 2        # Run only phase 2
  python scripts/backfill_all.py --phase 1,2      # Run phases 1 and 2
  python scripts/backfill_all.py --dry-run        # Show what would be done
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

# Ensure project root is on path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("backfill")

# Load .env
env_path = ROOT / ".env"
if env_path.exists():
    for line in open(env_path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            val = val.strip().strip('"').strip("'")
            if key.strip() and val:
                os.environ.setdefault(key.strip(), val)

import yaml
from ai_client import AIClient
from db_client import SupabaseClient
from scrapers.base import Job

config = yaml.safe_load(open(ROOT / "config.yaml"))
ai_client = AIClient.from_config(config)
db = SupabaseClient.from_env()

USER_ID = "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39"
TODAY = time.strftime("%Y-%m-%d")
BUCKET = os.environ.get("S3_BUCKET_NAME", os.environ.get("S3_BUCKET", "utkarsh-job-hunt"))

# Load base resumes
base_resumes = {}
for f in (ROOT / "resumes").glob("*.tex"):
    base_resumes[f.stem] = f.read_text()
log.info(f"Loaded {len(base_resumes)} base resumes: {list(base_resumes.keys())}")


# ── Helpers ──────────────────────────────────────────────────────────────

def get_all_jobs():
    """Fetch all jobs for the user from Supabase."""
    result = db.client.table("jobs").select("*").eq("user_id", USER_ID).execute()
    return result.data or []


def update_job(job_id, updates):
    """Update a single job row in Supabase."""
    db.client.table("jobs").update(updates).eq("job_id", job_id).execute()


def make_job_obj(jd):
    """Create a Job object from a Supabase row."""
    job = Job(
        title=jd["title"],
        company=jd["company"],
        description=jd.get("description") or "",
        location=jd.get("location") or "",
        apply_url=jd.get("apply_url") or "",
        source=jd.get("source") or "unknown",
    )
    job.job_id = jd["job_id"]
    job.match_score = jd.get("match_score") or 0
    job.matched_resume = jd.get("matched_resume") or "sre_devops"
    return job


# ── Phase 1: Backfill descriptions from jobs_raw ────────────────────────

def phase1_backfill_descriptions(all_jobs, dry_run=False):
    """For jobs with empty/short descriptions, try to pull from jobs_raw."""
    missing = [j for j in all_jobs if not j.get("description") or len(j.get("description", "")) < 50]
    if not missing:
        log.info("[Phase 1] All jobs have descriptions. Nothing to do.")
        return 0

    log.info(f"[Phase 1] {len(missing)} jobs with empty/short descriptions")

    # Try matching by job_hash first
    hashes = [j["job_hash"] for j in missing if j.get("job_hash")]
    raw_by_hash = {}
    if hashes:
        raw_result = db.client.table("jobs_raw").select("job_hash, description").in_("job_hash", hashes).execute()
        raw_by_hash = {r["job_hash"]: r["description"] for r in (raw_result.data or []) if r.get("description")}

    # For jobs without job_hash, try title+company match
    updated = 0
    for jd in missing:
        jh = jd.get("job_hash")
        desc = None

        if jh and jh in raw_by_hash:
            desc = raw_by_hash[jh]
        else:
            # Fuzzy match by title + company
            try:
                result = db.client.table("jobs_raw").select("description, title, company") \
                    .ilike("company", f"%{jd['company'][:30]}%") \
                    .ilike("title", f"%{jd['title'][:30]}%") \
                    .limit(1).execute()
                if result.data and result.data[0].get("description"):
                    desc = result.data[0]["description"]
            except Exception as e:
                log.warning(f"  Fuzzy match failed for {jd['title']}: {e}")

        if desc and len(desc) > 50:
            if dry_run:
                log.info(f"  [DRY] Would backfill description for: {jd['title']} ({len(desc)} chars)")
            else:
                update_job(jd["job_id"], {"description": desc})
                log.info(f"  Backfilled description for: {jd['title']} ({len(desc)} chars)")
            updated += 1
        else:
            log.warning(f"  No description found for: {jd['title']} at {jd['company']}")

    log.info(f"[Phase 1] Done — {updated}/{len(missing)} descriptions backfilled")
    return updated


# ── Phase 2: Re-score jobs for key_matches/gaps ─────────────────────────

def phase2_rescore_jobs(all_jobs, dry_run=False):
    """Re-score jobs that are missing key_matches or gaps."""
    from matcher import _match_single, SINGLE_MATCH_SYSTEM_PROMPT, _DEFAULT_CANDIDATE_INFO_SHORT

    missing = [
        j for j in all_jobs
        if (not j.get("key_matches") or j["key_matches"] == [])
        and j.get("description") and len(j.get("description", "")) > 50
    ]
    if not missing:
        log.info("[Phase 2] All jobs have key_matches. Nothing to do.")
        return 0

    log.info(f"[Phase 2] {len(missing)} jobs need re-scoring for key_matches/gaps")

    # Build resume context
    resume_summaries = []
    for key, tex in base_resumes.items():
        # Extract first 1500 chars of each resume
        resume_summaries.append(f"--- {key} resume ---\n{tex[:1500]}")
    resume_context = "\n\n".join(resume_summaries)

    updated = 0
    for i, jd in enumerate(missing):
        if dry_run:
            log.info(f"  [DRY] Would re-score: {jd['title']} at {jd['company']}")
            updated += 1
            continue

        log.info(f"  [{i+1}/{len(missing)}] Scoring: {jd['title']} at {jd['company']}")
        job = make_job_obj(jd)
        try:
            result = _match_single(
                job, resume_context, ai_client,
                system_prompt=SINGLE_MATCH_SYSTEM_PROMPT,
                candidate_info=_DEFAULT_CANDIDATE_INFO_SHORT,
            )
            if result:
                updates = {
                    "key_matches": result.get("key_matches", []),
                    "gaps": result.get("gaps", []),
                    "match_reasoning": result.get("reasoning", ""),
                }
                # Also update scores if they were 0 or missing
                if not jd.get("ats_score"):
                    updates["ats_score"] = result.get("ats_score", 0)
                    updates["hiring_manager_score"] = result.get("hiring_manager_score", 0)
                    updates["tech_recruiter_score"] = result.get("tech_recruiter_score", 0)
                    avg = round((result.get("ats_score", 0) + result.get("hiring_manager_score", 0) + result.get("tech_recruiter_score", 0)) / 3)
                    updates["match_score"] = avg

                update_job(jd["job_id"], updates)
                km = result.get("key_matches", [])
                log.info(f"    key_matches={km[:3]}... gaps={result.get('gaps', [])[:3]}...")
                updated += 1
            else:
                log.warning(f"    Scoring returned None")
        except Exception as e:
            log.error(f"    Scoring failed: {e}")

    log.info(f"[Phase 2] Done — {updated}/{len(missing)} jobs re-scored")
    return updated


# ── Phase 3: Generate tailored resumes ──────────────────────────────────

def phase3_generate_resumes(all_jobs, dry_run=False):
    """Generate tailored resumes for jobs that have descriptions but no resume PDF."""
    from tailorer import tailor_resume
    from latex_compiler import compile_tex_to_pdf
    from s3_uploader import upload_file
    from resume_scorer import score_and_improve

    missing = [
        j for j in all_jobs
        if not j.get("resume_s3_url")
        and j.get("description") and len(j.get("description", "")) > 50
    ]
    if not missing:
        log.info("[Phase 3] All jobs have resumes. Nothing to do.")
        return 0

    log.info(f"[Phase 3] {len(missing)} jobs need tailored resumes")

    out_dir = Path(f"output/{TODAY}/resumes")
    out_dir.mkdir(parents=True, exist_ok=True)

    updated = 0
    for i, jd in enumerate(missing):
        if dry_run:
            log.info(f"  [DRY] Would generate resume for: {jd['title']} at {jd['company']}")
            updated += 1
            continue

        rtype = jd.get("matched_resume") or "sre_devops"
        if rtype not in base_resumes:
            rtype = list(base_resumes.keys())[0]

        log.info(f"  [{i+1}/{len(missing)}] {jd['title']} at {jd['company']} (resume: {rtype})")
        job = make_job_obj(jd)

        try:
            # Tailor
            tailor_resume(job=job, base_tex=base_resumes[rtype], ai_client=ai_client, output_dir=out_dir)
            if not job.tailored_tex_path or not Path(job.tailored_tex_path).exists():
                log.warning("    No .tex produced")
                continue

            # Score & improve
            tailored_tex = Path(job.tailored_tex_path).read_text()
            try:
                final_tex, scores = score_and_improve(tailored_tex, job, ai_client)
            except Exception as e:
                log.warning(f"    score_and_improve failed: {e} — using unscored resume")
                final_tex = tailored_tex
                scores = {}

            # Compile
            final_path = out_dir / f"final_{jd['job_id']}.tex"
            final_path.write_text(final_tex)
            pdf_path = compile_tex_to_pdf(str(final_path), str(out_dir))
            if not pdf_path or not Path(pdf_path).exists():
                log.warning("    LaTeX compile failed")
                continue

            # Upload to S3
            sc = re.sub(r"[^a-zA-Z0-9]", "_", jd["company"])[:30]
            st = re.sub(r"[^a-zA-Z0-9]", "_", jd["title"])[:30]
            fn = f"Utkarsh_Singh_{st}_{sc}_{TODAY}"
            s3_url = upload_file(pdf_path, f"users/{USER_ID}/{TODAY}/resumes/{fn}.pdf", BUCKET)
            if not s3_url:
                log.warning("    S3 upload failed")
                continue

            updates = {
                "resume_s3_url": s3_url,
                "tailoring_model": f"{getattr(job, 'tailoring_provider', 'council')}:{getattr(job, 'tailoring_model', 'consensus')}",
                "matched_resume": rtype,
            }
            if scores.get("ats_score"):
                updates["ats_score"] = scores["ats_score"]
                updates["hiring_manager_score"] = scores["hiring_manager_score"]
                updates["tech_recruiter_score"] = scores["tech_recruiter_score"]
                avg = round((scores["ats_score"] + scores["hiring_manager_score"] + scores["tech_recruiter_score"]) / 3)
                updates["match_score"] = avg

            update_job(jd["job_id"], updates)
            updated += 1
            log.info(f"    DONE — {s3_url}")
        except Exception as e:
            log.error(f"    ERROR: {e}")

    log.info(f"[Phase 3] Done — {updated}/{len(missing)} resumes generated")
    return updated


# ── Phase 4: Generate cover letters ─────────────────────────────────────

def phase4_generate_cover_letters(all_jobs, dry_run=False):
    """Generate cover letters for jobs that have resumes but no cover letter."""
    from cover_letter import generate_cover_letter
    from latex_compiler import compile_tex_to_pdf
    from s3_uploader import upload_file

    missing = [
        j for j in all_jobs
        if j.get("resume_s3_url")
        and not j.get("cover_letter_s3_url")
        and j.get("description") and len(j.get("description", "")) > 50
    ]
    if not missing:
        log.info("[Phase 4] All jobs with resumes have cover letters. Nothing to do.")
        return 0

    log.info(f"[Phase 4] {len(missing)} jobs need cover letters")

    out_dir = Path(f"output/{TODAY}/cover_letters")
    out_dir.mkdir(parents=True, exist_ok=True)

    updated = 0
    for i, jd in enumerate(missing):
        if dry_run:
            log.info(f"  [DRY] Would generate cover letter for: {jd['title']} at {jd['company']}")
            updated += 1
            continue

        rtype = jd.get("matched_resume") or "sre_devops"
        if rtype not in base_resumes:
            rtype = list(base_resumes.keys())[0]

        log.info(f"  [{i+1}/{len(missing)}] {jd['title']} at {jd['company']}")
        job = make_job_obj(jd)

        try:
            generate_cover_letter(job=job, resume_tex=base_resumes[rtype], ai_client=ai_client, output_dir=out_dir)
            if not job.cover_letter_tex_path or not Path(job.cover_letter_tex_path).exists():
                log.warning("    No cover letter .tex produced")
                continue

            pdf_path = compile_tex_to_pdf(job.cover_letter_tex_path, str(out_dir))
            if not pdf_path or not Path(pdf_path).exists():
                log.warning("    LaTeX compile failed")
                continue

            sc = re.sub(r"[^a-zA-Z0-9]", "_", jd["company"])[:30]
            st = re.sub(r"[^a-zA-Z0-9]", "_", jd["title"])[:30]
            fn = f"Utkarsh_Singh_{st}_{sc}_{TODAY}_CL"
            s3_url = upload_file(pdf_path, f"users/{USER_ID}/{TODAY}/cover_letters/{fn}.pdf", BUCKET)
            if not s3_url:
                log.warning("    S3 upload failed")
                continue

            update_job(jd["job_id"], {"cover_letter_s3_url": s3_url})
            updated += 1
            log.info(f"    DONE — {s3_url}")
        except Exception as e:
            log.error(f"    ERROR: {e}")

    log.info(f"[Phase 4] Done — {updated}/{len(missing)} cover letters generated")
    return updated


# ── Phase 5: Find LinkedIn contacts ─────────────────────────────────────

def phase5_find_contacts(all_jobs, dry_run=False):
    """Find contacts for jobs that have resumes but no contacts."""
    from contact_finder import find_contacts

    missing = [
        j for j in all_jobs
        if j.get("resume_s3_url")
        and (not j.get("linkedin_contacts") or j.get("linkedin_contacts") in ("[]", "null", None))
    ]
    if not missing:
        log.info("[Phase 5] All jobs with resumes have contacts. Nothing to do.")
        return 0

    log.info(f"[Phase 5] {len(missing)} jobs need contacts")

    updated = 0
    for i, jd in enumerate(missing):
        if dry_run:
            log.info(f"  [DRY] Would find contacts for: {jd['title']} at {jd['company']}")
            updated += 1
            continue

        log.info(f"  [{i+1}/{len(missing)}] {jd['title']} at {jd['company']}")
        job = make_job_obj(jd)

        try:
            contacts = find_contacts(job, ai_client)
            if contacts:
                contacts_json = json.dumps(contacts) if not isinstance(contacts, str) else contacts
                update_job(jd["job_id"], {"linkedin_contacts": contacts_json})
                updated += 1
                log.info(f"    DONE — {len(contacts) if isinstance(contacts, list) else '?'} contacts")
            else:
                log.warning(f"    No contacts found")
        except Exception as e:
            log.error(f"    ERROR: {e}")

    log.info(f"[Phase 5] Done — {updated}/{len(missing)} jobs got contacts")
    return updated


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Comprehensive data backfill")
    parser.add_argument("--phase", type=str, default="1,2,3,4,5",
                        help="Comma-separated phase numbers to run (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without making changes")
    args = parser.parse_args()

    phases = [int(p.strip()) for p in args.phase.split(",")]
    dry_run = args.dry_run

    log.info(f"{'='*60}")
    log.info(f"NaukriBaba Comprehensive Backfill")
    log.info(f"Phases: {phases} | Dry run: {dry_run}")
    log.info(f"{'='*60}")

    # Fetch all jobs once
    all_jobs = get_all_jobs()
    log.info(f"Total jobs in database: {len(all_jobs)}")

    results = {}

    if 1 in phases:
        results[1] = phase1_backfill_descriptions(all_jobs, dry_run)
        # Refresh after description backfill so later phases see updated data
        if results[1] > 0 and not dry_run:
            all_jobs = get_all_jobs()

    if 2 in phases:
        results[2] = phase2_rescore_jobs(all_jobs, dry_run)

    if 3 in phases:
        results[3] = phase3_generate_resumes(all_jobs, dry_run)
        # Refresh so phase 4/5 see new resume URLs
        if results.get(3, 0) > 0 and not dry_run:
            all_jobs = get_all_jobs()

    if 4 in phases:
        results[4] = phase4_generate_cover_letters(all_jobs, dry_run)

    if 5 in phases:
        results[5] = phase5_find_contacts(all_jobs, dry_run)

    # Summary
    log.info(f"\n{'='*60}")
    log.info("BACKFILL SUMMARY")
    log.info(f"{'='*60}")
    phase_names = {
        1: "Descriptions from jobs_raw",
        2: "Re-scored for key_matches/gaps",
        3: "Tailored resumes generated",
        4: "Cover letters generated",
        5: "LinkedIn contacts found",
    }
    for p in phases:
        count = results.get(p, 0)
        name = phase_names.get(p, f"Phase {p}")
        status = f"{count} updated" if count > 0 else "nothing to do"
        log.info(f"  Phase {p}: {name} — {status}")
    log.info(f"{'='*60}")


if __name__ == "__main__":
    main()
