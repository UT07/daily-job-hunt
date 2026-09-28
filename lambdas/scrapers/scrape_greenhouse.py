"""Greenhouse Boards API scraper.

Fetches jobs from companies using Greenhouse ATS via their free public API.
No authentication needed. Board slugs are configurable via SSM parameter.

API: GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
"""
import html
import logging
import re
from datetime import datetime, timedelta, timezone

from utils.canonical_hash import canonical_hash

import boto3
import httpx

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

# Default boards — used ONLY when /naukribaba/GREENHOUSE_BOARDS is unreadable.
# Dublin/Ireland-relevant companies on Greenhouse.
#
# Audited 2026-09-28 alongside the Ashby defaults (which had rotted to 3x 404):
# all ten of these answered 200 with a non-empty `jobs` array, so the list is
# unchanged. It is pinned by tests/unit/test_scraper_default_boards.py so a
# future edit has to state its own verification.
#
# A slug belongs here only if
#   GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
# returns 200 with a non-empty `jobs` array. Re-verify before editing:
#   NAUKRIBABA_LIVE_BOARD_CHECK=1 pytest tests/unit/test_scraper_default_boards.py
DEFAULT_BOARDS = [
    "stripe", "intercom", "mongodb", "twilio", "datadog",
    "pagerduty", "toast", "cloudflare", "elastic", "ripple",
]

# Location keywords to filter for (case-insensitive)
LOCATION_KEYWORDS = {"ireland", "dublin", "remote", "emea", "europe", "anywhere"}


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


def _location_matches(location_name: str) -> bool:
    """Check if job location matches our target regions."""
    loc_lower = (location_name or "").lower()
    return any(kw in loc_lower for kw in LOCATION_KEYWORDS)


def handler(event, context):
    query_hash = event.get("query_hash", "")
    cache_ttl_hours = event.get("cache_ttl_hours", 24)

    db = get_supabase()

    # Check cache
    cached = db.table("jobs_raw").select("job_hash", count="exact") \
        .eq("source", "greenhouse").eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.now(timezone.utc) - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count and cached.count > 0:
        return {"count": cached.count, "source": "greenhouse", "cached": True}

    # Read board slugs from SSM (fallback to defaults).
    #
    # This fallback used to be entirely silent — `except Exception: boards =
    # DEFAULT_BOARDS` with no log line — so a run on defaults was
    # indistinguishable from a normal run in CloudWatch. Which list a run used
    # is the first thing you need when the job count drops.
    try:
        import json
        boards_json = get_param("/naukribaba/GREENHOUSE_BOARDS")
        boards = json.loads(boards_json)
        config_source = "ssm"
    except Exception as e:
        boards = list(DEFAULT_BOARDS)
        config_source = "default"
        logger.error(
            f"[greenhouse] SSM_FALLBACK: /naukribaba/GREENHOUSE_BOARDS unreadable "
            f"({type(e).__name__}: {e}) — using the {len(boards)} built-in "
            f"defaults {boards}"
        )

    all_jobs = []
    boards_ok = []
    boards_failed = []
    client = httpx.Client(timeout=30)

    for slug in boards:
        try:
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
            resp = client.get(url)
            if resp.status_code == 404:
                # A configured board that 404s is a config error, not a blip:
                # the slug is wrong or the board was taken down, and it will
                # never recover on its own. ERROR + a greppable marker, so it
                # can carry a log metric filter and an alarm.
                logger.error(
                    f"[greenhouse] BOARD_NOT_FOUND: {slug} returned HTTP 404 — slug is "
                    f"wrong or the board was removed (config={config_source})"
                )
                boards_failed.append({"board": slug, "status": 404, "reason": "not_found"})
                continue
            if resp.status_code != 200:
                # Everything else (429, 5xx) is plausibly transient.
                logger.warning(f"[greenhouse] BOARD_UNAVAILABLE: {slug} returned HTTP {resp.status_code}")
                boards_failed.append({"board": slug, "status": resp.status_code, "reason": "http_error"})
                continue

            data = resp.json()
            jobs = data.get("jobs", [])
            if not jobs:
                # 200 with an empty board reads as healthy but yields nothing.
                logger.warning(f"[greenhouse] BOARD_EMPTY: {slug} returned 200 with 0 postings")

            # Filter by location
            matched = [j for j in jobs if _location_matches(j.get("location", {}).get("name", ""))]
            logger.info(f"[greenhouse] {slug}: {len(matched)}/{len(jobs)} match location filter")

            for j in matched:
                title = (j.get("title") or "").strip()
                location = j.get("location", {}).get("name", "") if isinstance(j.get("location"), dict) else ""
                description = _clean(j.get("content") or "")
                apply_url = j.get("absolute_url") or ""

                if not title:
                    continue

                # Greenhouse exposes the posting timestamp as `updated_at`
                # in their public boards API (no separate posted_at). It's
                # the right signal — gets bumped when a posting is edited
                # AND on initial create.
                from normalizers import _parse_posted_date
                posted_date = _parse_posted_date(j.get("updated_at") or j.get("created_at"))

                all_jobs.append({
                    "title": title[:500],
                    "company": slug.replace("-", " ").title()[:200],
                    "description": description[:10000],
                    "location": location[:200],
                    "apply_url": apply_url[:1000],
                    "source": "greenhouse",
                    "job_hash": canonical_hash(slug, title, description),
                    "query_hash": query_hash,
                    "posting_id": str(j.get("id", "")),
                    "board_token": slug,
                    "posted_date": posted_date,
                })

            # Counted OK only here, once the whole body completed — so
            # boards_ok and boards_failed stay disjoint and sum to the
            # configured count.
            boards_ok.append(slug)

        except Exception as e:
            logger.error(f"[greenhouse] BOARD_ERROR: {slug} failed: {e}")
            boards_failed.append({"board": slug, "status": None, "reason": type(e).__name__})

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
    # used to read "N jobs from 10 boards" whether 10 or 2 of them answered —
    # and put it in the return value, where it lands in the Step Functions
    # execution output alongside `count`.
    summary = (
        f"[greenhouse] {len(all_jobs)} jobs from {len(boards_ok)}/{len(boards)} boards "
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
        "source": "greenhouse",
        "config_source": config_source,
        "boards_configured": len(boards),
        "boards_ok": len(boards_ok),
        "boards_failed": boards_failed,
        "new_job_hashes": [j["job_hash"] for j in all_jobs],
    }
