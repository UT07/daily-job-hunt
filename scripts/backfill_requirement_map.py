#!/usr/bin/env python3
"""Backfill requirement_map JSONB for existing S+A tier jobs.

Uses a lightweight AI prompt (NOT full re-scoring) to map the top 5-8 JD
requirements to specific resume evidence for each job.

Processes 3 jobs at a time with 5-second gaps to avoid rate limits.
Calls AI providers directly via httpx — no Lambda invocation.

Usage:
    python scripts/backfill_requirement_map.py
    python scripts/backfill_requirement_map.py --dry-run       # preview without updating
    python scripts/backfill_requirement_map.py --max 10        # limit to 10 jobs
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: load .env, set up paths
# ---------------------------------------------------------------------------

env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-1")

import httpx
from supabase import create_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("backfill_req_map")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_ID = "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39"
BATCH_SIZE = 3
BATCH_DELAY = 5  # seconds between batches

# Provider configs — try in order until one succeeds
PROVIDERS = [
    {
        "name": "groq",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "key_env": "GROQ_API_KEY",
        "model": "llama-3.3-70b-versatile",
        "timeout": 60,
    },
    {
        "name": "qwen",
        "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
        "key_env": "QWEN_API_KEY",
        "model": "qwen-plus",
        "timeout": 90,
    },
    {
        "name": "openrouter/qwen3.6-plus",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "key_env": "OPENROUTER_API_KEY",
        "model": "qwen/qwen3.6-plus:free",
        "timeout": 90,
        "extra_headers": {"HTTP-Referer": "https://github.com/UT07/daily-job-hunt"},
    },
    {
        "name": "openrouter/nemotron",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "key_env": "OPENROUTER_API_KEY",
        "model": "nvidia/nemotron-3-super-120b-a12b:free",
        "timeout": 90,
        "extra_headers": {"HTTP-Referer": "https://github.com/UT07/daily-job-hunt"},
    },
    {
        "name": "nvidia",
        "url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "key_env": "NVIDIA_API_KEY",
        "model": "meta/llama-3.3-70b-instruct",
        "timeout": 120,
    },
]

SYSTEM_PROMPT = """You are an expert job-candidate evaluator. Your task is to map the key requirements from a job description to specific evidence in the candidate's resume.

For each of the top 5-8 most important requirements in the JD:
1. State the requirement clearly
2. Cite the specific resume evidence that satisfies it (exact text/experience), or null if no evidence
3. Rate severity: "met" if evidence exists, "nice_to_have_gap" if it's a preferred/bonus skill they lack, "blocker_gap" if it's a hard requirement they lack

Focus on CONCRETE requirements: required skills, technologies, years of experience, certifications, domain knowledge. Skip generic filler like "team player" or "good communication".

Return ONLY valid JSON (no markdown, no code fences):
[
    {"requirement": "<JD requirement>", "evidence": "<resume evidence or null>", "severity": "<met|nice_to_have_gap|blocker_gap>"},
    ...
]"""


# ---------------------------------------------------------------------------
# AI call with failover
# ---------------------------------------------------------------------------

def call_ai(prompt: str) -> list | None:
    """Call AI providers with failover. Returns parsed requirement_map list or None."""
    for provider in PROVIDERS:
        api_key = os.environ.get(provider["key_env"], "")
        if not api_key:
            continue

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if "extra_headers" in provider:
            headers.update(provider["extra_headers"])

        try:
            resp = httpx.post(
                provider["url"],
                headers=headers,
                json={
                    "model": provider["model"],
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 2048,
                    "temperature": 0,
                },
                timeout=provider.get("timeout", 60),
            )

            if resp.status_code == 429:
                log.warning(f"  {provider['name']} rate limited, trying next...")
                continue
            if resp.status_code != 200:
                log.warning(f"  {provider['name']} returned {resp.status_code}, trying next...")
                continue

            content = resp.json()["choices"][0]["message"].get("content", "")
            if not content:
                log.warning(f"  {provider['name']} returned empty content")
                continue

            # Parse JSON — strip markdown fences if present
            text = content.strip()
            if "```" in text:
                parts = text.split("```")
                text = parts[1] if len(parts) >= 2 else text
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            # Find JSON array
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                text = match.group()

            result = json.loads(text)
            if not isinstance(result, list):
                log.warning(f"  {provider['name']} returned non-list JSON")
                continue

            # Validate structure
            valid = []
            for item in result:
                if isinstance(item, dict) and "requirement" in item and "severity" in item:
                    valid.append({
                        "requirement": str(item["requirement"]),
                        "evidence": item.get("evidence"),
                        "severity": item.get("severity", "met"),
                    })
            if not valid:
                log.warning(f"  {provider['name']} returned invalid structure")
                continue

            log.info(f"  AI: {provider['name']} -> {len(valid)} requirements mapped")
            return valid

        except httpx.TimeoutException:
            log.warning(f"  {provider['name']} timed out")
            continue
        except json.JSONDecodeError as e:
            log.warning(f"  {provider['name']} JSON parse error: {e}")
            continue
        except Exception as e:
            log.warning(f"  {provider['name']} error: {e}")
            continue

    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Backfill requirement_map for S+A tier jobs")
    parser.add_argument("--dry-run", action="store_true", help="Preview without updating DB")
    parser.add_argument("--max", type=int, default=None, help="Max jobs to process")
    args = parser.parse_args()

    # Connect to Supabase
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not supabase_url or not supabase_key:
        log.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env")
        sys.exit(1)
    db = create_client(supabase_url, supabase_key)

    # Fetch user's base resume
    resume_r = (
        db.table("user_resumes")
        .select("tex_content")
        .eq("user_id", USER_ID)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not resume_r.data or not resume_r.data[0].get("tex_content"):
        log.error("No resume found for user")
        sys.exit(1)
    base_resume = resume_r.data[0]["tex_content"]
    # Truncate resume to ~3000 chars to save tokens (keep the important parts)
    if len(base_resume) > 3000:
        base_resume = base_resume[:3000] + "\n... [truncated]"
    log.info(f"Resume loaded ({len(base_resume)} chars)")

    # Fetch S+A tier jobs where requirement_map is NULL
    query = (
        db.table("jobs")
        .select("job_id, title, company, description, match_score, score_tier, job_hash")
        .eq("user_id", USER_ID)
        .in_("score_tier", ["S", "A"])
        .is_("requirement_map", "null")
        .order("match_score", desc=True)
    )
    if args.max:
        query = query.limit(args.max)
    jobs = query.execute().data or []

    log.info(f"S+A tier jobs needing requirement_map: {len(jobs)}")
    if not jobs:
        log.info("Nothing to backfill. All S+A jobs already have requirement_map.")
        return

    # For jobs with no/short description, try to get it from jobs_raw
    enriched = 0
    for job in jobs:
        desc = job.get("description") or ""
        if len(desc) < 100:
            raw = (
                db.table("jobs_raw")
                .select("description")
                .eq("job_hash", job["job_hash"])
                .limit(1)
                .execute()
            )
            if raw.data and raw.data[0].get("description"):
                job["description"] = raw.data[0]["description"]
                enriched += 1
    if enriched:
        log.info(f"Enriched {enriched} job descriptions from jobs_raw")

    # Filter out jobs still without useful descriptions
    jobs = [j for j in jobs if len(j.get("description", "") or "") >= 100]
    log.info(f"Jobs with usable descriptions: {len(jobs)}")

    # Process in batches of BATCH_SIZE
    processed = 0
    failed = 0
    consecutive_failures = 0

    for batch_start in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[batch_start : batch_start + BATCH_SIZE]

        for job in batch:
            idx = batch_start + batch.index(job) + 1
            title = job["title"][:50]
            company = job["company"][:30]
            tier = job.get("score_tier", "?")
            score = job.get("match_score", 0)

            log.info(f"[{idx}/{len(jobs)}] {title} @ {company} (Tier {tier}, score={score})")

            # Build the prompt — lightweight, just requirement mapping
            desc = (job.get("description") or "")[:4000]
            prompt = f"""Map the key requirements from this job description to the candidate's resume evidence.

Job: {job['title']} at {job['company']}

Job Description:
{desc}

Candidate Resume:
{base_resume}"""

            req_map = call_ai(prompt)

            if req_map is None:
                log.warning(f"  FAILED: no AI provider returned valid result")
                failed += 1
                consecutive_failures += 1
                if consecutive_failures >= 5:
                    log.error("5 consecutive failures — AI providers exhausted. Stopping.")
                    break
                continue

            consecutive_failures = 0

            # Summary stats
            met = sum(1 for r in req_map if r["severity"] == "met")
            gaps = sum(1 for r in req_map if r["severity"] != "met")
            log.info(f"  Result: {len(req_map)} requirements — {met} met, {gaps} gaps")

            if args.dry_run:
                for r in req_map:
                    sev = r["severity"]
                    req = r["requirement"][:60]
                    ev = (r["evidence"] or "null")[:40]
                    log.info(f"    [{sev}] {req} -> {ev}")
                processed += 1
                continue

            # Update Supabase
            try:
                db.table("jobs").update({
                    "requirement_map": req_map,
                }).eq("job_id", job["job_id"]).execute()
                processed += 1
                log.info(f"  Updated requirement_map in DB")
            except Exception as e:
                log.error(f"  DB update failed: {e}")
                failed += 1

        else:
            # Only delay if we haven't broken out of the inner loop
            if batch_start + BATCH_SIZE < len(jobs):
                log.info(f"  --- Batch done. Waiting {BATCH_DELAY}s before next batch ---")
                time.sleep(BATCH_DELAY)
            continue
        # Inner loop broke (consecutive failures) — break outer too
        break

    log.info(f"\nDone! Processed: {processed}, Failed: {failed}, Total: {len(jobs)}")
    if args.dry_run:
        log.info("(dry-run mode — no DB updates were made)")


if __name__ == "__main__":
    main()
