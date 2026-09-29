"""Ashby HQ Boards API scraper.

Fetches jobs from companies using Ashby ATS via their free public posting API.
No authentication needed. Company slugs are configurable via SSM parameter.

API: GET https://api.ashbyhq.com/posting-api/job-board/{company}
Board UI: https://jobs.ashbyhq.com/{company}
"""
import html
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from utils.canonical_hash import canonical_hash

import boto3
import httpx

from shared.location_policy import build_location_policy, location_verdict
from shared.scrape_budget import cache_ttl_hours as _ttl

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

# Default companies — used ONLY when /naukribaba/ASHBY_COMPANIES is unreadable.
# Mirrors the production SSM list, so an SSM outage degrades to the boards
# production actually scrapes rather than to a separately-rotting set.
#
# Audited 2026-09-28: the previous defaults had rotted unnoticed. `anthropic`,
# `figma` and `retool` all returned 404 (no such board) and `vercel` returned
# 200 with 0 postings, so an SSM failure would have silently dropped this
# scraper to 2 working boards out of 6 while still reporting success.
#
# A slug belongs here only if
#   GET https://api.ashbyhq.com/posting-api/job-board/{slug}
# returns 200 with a non-empty `jobs` array. Re-verify before editing:
#   NAUKRIBABA_LIVE_BOARD_CHECK=1 pytest tests/unit/test_scraper_default_boards.py
DEFAULT_COMPANIES = [
    "linear", "notion", "ramp", "benchling", "abridge", "livekit",
]

def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


def _clean(text):
    """Strip HTML tags and decode entities."""
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _posting_location(job: dict) -> str:
    """Ashby returns `location` as a bare string or an object, by API version."""
    loc = job.get("location", "")
    if isinstance(loc, dict):
        return loc.get("name", "") or ""
    return loc or ""


def handler(event, context):
    query_hash = event.get("query_hash", "")
    cache_ttl_hours = event.get("cache_ttl_hours", _ttl(24))
    # load_config through the state machine ("locations.$": "$.locations");
    # before 2026-09-28 this scraper carried its own hardcoded
    # LOCATION_KEYWORDS = {"ireland", "dublin", "remote", "emea", "europe",
    # "anywhere"} and the user's configured locations reached nothing.
    policy = build_location_policy(event.get("locations"))
    logger.info(
        f"[ashby] location policy: source={policy.source} "
        f"locations={list(policy.locations)} regions={sorted(policy.target_regions)}"
    )

    db = get_supabase()

    # Check cache
    cached = db.table("jobs_raw").select("job_hash", count="exact") \
        .eq("source", "ashby").eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.now(timezone.utc) - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count and cached.count > 0:
        return {"count": cached.count, "source": "ashby", "cached": True}

    # Read company slugs from SSM (fallback to defaults).
    #
    # This fallback used to be entirely silent — `except Exception: companies =
    # DEFAULT_COMPANIES` with no log line — so a run on defaults was
    # indistinguishable from a normal run in CloudWatch. Which list a run used
    # is the first thing you need when the job count drops.
    try:
        import json
        companies_json = get_param("/naukribaba/ASHBY_COMPANIES")
        companies = json.loads(companies_json)
        config_source = "ssm"
    except Exception as e:
        companies = list(DEFAULT_COMPANIES)
        config_source = "default"
        logger.error(
            f"[ashby] SSM_FALLBACK: /naukribaba/ASHBY_COMPANIES unreadable "
            f"({type(e).__name__}: {e}) — using the {len(companies)} built-in "
            f"defaults {companies}"
        )

    all_jobs = []
    boards_ok = []
    boards_failed = []
    client = httpx.Client(timeout=30)

    for company in companies:
        try:
            url = f"https://api.ashbyhq.com/posting-api/job-board/{company}"
            resp = client.get(url)
            if resp.status_code == 404:
                # A configured board that 404s is a config error, not a blip:
                # the slug is wrong or the board was taken down, and it will
                # never recover on its own. ERROR + a greppable marker, so it
                # can carry a log metric filter and an alarm.
                logger.error(
                    f"[ashby] BOARD_NOT_FOUND: {company} returned HTTP 404 — slug is "
                    f"wrong or the board was removed (config={config_source})"
                )
                boards_failed.append({"company": company, "status": 404, "reason": "not_found"})
                continue
            if resp.status_code != 200:
                # Everything else (429, 5xx) is plausibly transient.
                logger.warning(f"[ashby] BOARD_UNAVAILABLE: {company} returned HTTP {resp.status_code}")
                boards_failed.append({"company": company, "status": resp.status_code, "reason": "http_error"})
                continue

            data = resp.json()
            jobs = data.get("jobs", [])
            if not jobs:
                # 200 with an empty board reads as healthy but yields nothing —
                # the shape `vercel` was in during the 2026-09-28 audit.
                logger.warning(f"[ashby] BOARD_EMPTY: {company} returned 200 with 0 postings")

            # Filter by location, against this user's own policy. Ashby's
            # per-posting `isRemote` is passed through rather than used as an
            # unconditional admit, which is what it was until 2026-09-28: it
            # let 983 "New York, NY (HQ)" postings into jobs_raw untouched by
            # any location check. location_verdict treats it as "this role is
            # not tied to a place", which still admits a genuinely
            # location-free posting and no longer admits an office address.
            matched = []
            rejects = Counter()
            for j in jobs:
                admit, reason = location_verdict(
                    _posting_location(j), policy, bool(j.get("isRemote", False))
                )
                if admit:
                    matched.append(j)
                else:
                    rejects[reason.split(":", 1)[0]] += 1
            logger.info(
                f"[ashby] {company}: {len(matched)}/{len(jobs)} match location filter"
                + (f" (rejected: {dict(rejects)})" if rejects else "")
            )

            for j in matched:
                title = (j.get("title") or "").strip()

                location = _posting_location(j)
                if j.get("isRemote"):
                    location = location or "Remote"

                description = _clean(j.get("descriptionHtml") or j.get("description") or "")
                apply_url = j.get("jobUrl") or j.get("applyUrl") or f"https://jobs.ashbyhq.com/{company}"

                if not title:
                    continue

                # Use the company slug to derive a readable company name; the API
                # also returns organizationName at the top level of the response.
                company_name = (data.get("organizationName") or company.replace("-", " ").title())

                # Ashby's job-board API exposes posting timestamp as
                # `publishedAt` (canonical) or `updatedAt` (fallback).
                from normalizers import _parse_posted_date
                posted_date = _parse_posted_date(
                    j.get("publishedAt") or j.get("updatedAt") or j.get("createdAt")
                )

                all_jobs.append({
                    "title": title[:500],
                    "company": company_name[:200],
                    "description": description[:10000],
                    "location": location[:200],
                    "apply_url": apply_url[:1000],
                    "source": "ashby",
                    "job_hash": canonical_hash(company_name, title, description),
                    "query_hash": query_hash,
                    "posting_id": str(j.get("id", "")),
                    "company_slug": company,
                    "posted_date": posted_date,
                })

            # Counted OK only here, once the whole body completed — so
            # boards_ok and boards_failed stay disjoint and sum to the
            # configured count.
            boards_ok.append(company)

        except Exception as e:
            logger.error(f"[ashby] BOARD_ERROR: {company} failed: {e}")
            boards_failed.append({"company": company, "status": None, "reason": type(e).__name__})

    client.close()

    # Dedup within batch
    seen = set()
    unique = []
    for j in all_jobs:
        if j["job_hash"] not in seen:
            seen.add(j["job_hash"])
            unique.append(j)
    all_jobs = unique

    # Write to jobs_raw
    if all_jobs:
        now = datetime.now(timezone.utc).isoformat()
        for job in all_jobs:
            job["scraped_at"] = now
        for i in range(0, len(all_jobs), 50):
            chunk = all_jobs[i:i + 50]
            db.table("jobs_raw").upsert(chunk, on_conflict="job_hash").execute()

    # A partially-dead board list is the failure mode this scraper actually
    # hits: it still returns jobs, so nothing downstream notices. self_improve
    # only flags a scraper after 3 consecutive days of ZERO jobs, which a
    # half-working list never reaches. So say it on the summary line — which
    # used to read "N jobs from 6 companies" whether 6 or 2 of them answered —
    # and put it in the return value, where it lands in the Step Functions
    # execution output alongside `count`.
    summary = (
        f"[ashby] {len(all_jobs)} jobs from {len(boards_ok)}/{len(companies)} boards "
        f"(config={config_source})"
    )
    not_found = [b for b in boards_failed if b["reason"] == "not_found"]
    if not_found:
        logger.error(
            f"{summary} — {len(boards_failed)} board(s) FAILED, {len(not_found)} of them "
            f"permanently (404): {boards_failed}"
        )
    elif boards_failed:
        # Transient-looking only (429/5xx/network) — worth seeing, not paging.
        logger.warning(f"{summary} — {len(boards_failed)} board(s) FAILED: {boards_failed}")
    else:
        logger.info(summary)

    return {
        "count": len(all_jobs),
        "source": "ashby",
        "config_source": config_source,
        "boards_configured": len(companies),
        "boards_ok": len(boards_ok),
        "boards_failed": boards_failed,
        "new_job_hashes": [j["job_hash"] for j in all_jobs],
    }
