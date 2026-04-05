#!/usr/bin/env python3
"""End-to-end smoke test for the preamble-preservation tailoring fix.

Fetches top-N jobs by match_score, runs tailor_resume() + compile_tex_to_pdf(),
and asserts PDF structural integrity. Does NOT upload to S3 or update the DB.

Usage:
    python scripts/test_tailor_fix.py --n 3
"""
import argparse
import logging
import os
import sys
from pathlib import Path

import yaml

# Load .env
env_path = Path(__file__).parent.parent / ".env"
for line in open(env_path):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-1")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

from ai_client import AIClient
from db_client import SupabaseClient
from latex_compiler import compile_tex_to_pdf
from scrapers.base import Job
from tailorer import tailor_resume

USER_ID = "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39"
REQUIRED_SECTIONS = ["summary", "skills", "experience", "projects", "education", "certifications"]


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


def assert_pdf(pdf_path: str) -> tuple[bool, list[str]]:
    """Run structural assertions on a compiled PDF. Returns (ok, messages)."""
    import pdfplumber

    msgs = []
    path = Path(pdf_path)
    if not path.exists():
        return False, [f"PDF file missing: {pdf_path}"]

    size = path.stat().st_size
    if size < 10_000:
        msgs.append(f"WARN: PDF tiny ({size} bytes) — likely compile failure")

    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
        if page_count != 2:
            msgs.append(f"WARN: page_count={page_count} (expected 2)")

        # Extract all text
        full_text = "\n".join(p.extract_text() or "" for p in pdf.pages).lower()

        missing = [s for s in REQUIRED_SECTIONS if s not in full_text]
        if missing:
            return False, msgs + [f"FAIL: missing sections: {missing}"]

    return True, msgs + [f"OK: {page_count} pages, {size:,} bytes, all 6 sections"]


def main(n: int):
    db = SupabaseClient.from_env()
    config = yaml.safe_load(open(Path(__file__).parent.parent / "config.yaml"))
    ai_client = AIClient.from_config(config)

    # Load base resumes
    r = db.client.table("user_resumes").select("resume_key, tex_content").eq(
        "user_id", USER_ID
    ).execute()
    base_resumes = {row["resume_key"]: row["tex_content"] for row in r.data}
    if not base_resumes:
        for f in (Path(__file__).parent.parent / "resumes").glob("*.tex"):
            base_resumes[f.stem] = f.read_text()
    log.info(f"Loaded base resumes: {list(base_resumes.keys())}")

    # Fetch top N jobs by match_score
    jobs = (
        db.client.table("jobs")
        .select("*")
        .eq("user_id", USER_ID)
        .gte("match_score", 75)
        .order("match_score", desc=True)
        .limit(n)
        .execute()
        .data
    )
    log.info(f"Testing top {len(jobs)} jobs by match_score")

    out_dir = Path("output/test_tailor_fix")
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for i, jd in enumerate(jobs, 1):
        title = jd.get("title", "?")[:40]
        company = jd.get("company", "?")[:25]
        score = jd.get("match_score", 0)
        log.info(f"\n[{i}/{len(jobs)}] score={score} {title} @ {company}")

        job = job_from_row(jd)
        rtype = job.matched_resume if job.matched_resume in base_resumes else list(base_resumes.keys())[0]

        try:
            tex_path = tailor_resume(
                job=job,
                base_tex=base_resumes[rtype],
                ai_client=ai_client,
                output_dir=out_dir,
            )
        except Exception as e:
            log.error(f"  tailor raised: {e}")
            results.append((title, company, False, [f"tailor exception: {e}"]))
            continue

        if not tex_path or not Path(tex_path).exists():
            log.error(f"  tailor did not produce .tex")
            results.append((title, company, False, ["no .tex produced"]))
            continue
        log.info(f"  tex: {Path(tex_path).name}")

        pdf_path = compile_tex_to_pdf(tex_path, str(out_dir))
        if not pdf_path or not Path(pdf_path).exists():
            log.error(f"  compile failed — no PDF produced")
            results.append((title, company, False, ["compile failed"]))
            continue
        log.info(f"  pdf: {Path(pdf_path).name}")

        ok, msgs = assert_pdf(pdf_path)
        for m in msgs:
            log.info(f"  {m}")
        results.append((title, company, ok, msgs))

    # Summary
    log.info("\n" + "=" * 60)
    log.info("SUMMARY")
    log.info("=" * 60)
    passed = sum(1 for r in results if r[2])
    log.info(f"Passed: {passed}/{len(results)}")
    for title, company, ok, msgs in results:
        status = "PASS" if ok else "FAIL"
        log.info(f"  [{status}] {title[:35]:<35} @ {company[:20]:<20}  {msgs[-1] if msgs else ''}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=3, help="Number of top-scoring jobs to test")
    args = p.parse_args()
    sys.exit(main(args.n))
