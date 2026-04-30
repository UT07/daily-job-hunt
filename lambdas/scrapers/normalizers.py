"""Normalize scraper output to standard jobs_raw schema."""
import html
import re
from datetime import datetime, timezone

from utils.canonical_hash import canonical_hash


def _parse_posted_date(value) -> str | None:
    """Coerce many shapes of "posted date" to a UTC ISO 8601 string.

    Sources hand us:
        - LinkedIn: epoch milliseconds (int) or "2026-04-22T08:30:00.000Z"
        - Greenhouse: "2026-04-22T08:30:00.000-04:00"
        - Ashby: ISO timestamp string
        - Adzuna: ISO date string
        - HN Algolia: epoch seconds (int)
        - Indeed/Apify: sometimes "3 days ago" — handled by callers, not here
        - Already-normalized ISO string: passed through

    Returns None when nothing reasonable parses; the column is nullable
    so we'd rather store NULL than a junk timestamp.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        # Heuristic: > 10^11 == milliseconds since epoch (Jan 2001 cutoff in
        # seconds is < 10^10). > 10^9 == seconds. Anything below is too old
        # to be a real posting timestamp — return None.
        try:
            if value > 1e11:  # ms
                return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
            if value > 1e9:  # seconds
                return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
        except (OSError, ValueError, OverflowError):
            return None
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Make a few common ISO variations parseable by fromisoformat.
        # "Z" is not yet supported by Python 3.10's fromisoformat.
        s_norm = s.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s_norm)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass
        # Last resort: bare YYYY-MM-DD
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            return None
    return None


def normalize_job(raw: dict, source: str, query_hash: str = "") -> dict:
    """Normalize a raw job dict to jobs_raw schema."""
    title = html.unescape(raw.get("title") or raw.get("positionName") or "").strip()
    company = html.unescape(raw.get("company") or raw.get("companyName") or "").strip()
    description = html.unescape(raw.get("description") or raw.get("text") or "").strip()
    description = re.sub(r'<[^>]+>', '\n', description).strip()
    location = raw.get("location") or raw.get("city") or ""
    apply_url = raw.get("url") or raw.get("applyUrl") or raw.get("apply_url") or ""

    # posted_date may arrive under a dozen field names depending on source;
    # _parse_posted_date handles the format normalization.
    posted_date = _parse_posted_date(
        raw.get("posted_date")
        or raw.get("postedAt")
        or raw.get("postedDate")
        or raw.get("publishedAt")
        or raw.get("published_at")
        or raw.get("listed_at")
        or raw.get("listedAt")
        or raw.get("created_at_i")  # Algolia HN
        or raw.get("created_at")
        or raw.get("created")
        or raw.get("updated_at")
    )

    if not title or not company:
        return None

    job_hash = canonical_hash(company, title, description)

    return {
        "job_hash": job_hash,
        "title": title[:500],
        "company": company[:200],
        "description": description,
        "location": location[:200],
        "apply_url": apply_url[:1000],
        "source": source,
        "experience_level": raw.get("experienceLevel") or raw.get("experience_level"),
        "job_type": raw.get("jobType") or raw.get("job_type"),
        "posted_date": posted_date,
        "query_hash": query_hash,
    }


def normalize_linkedin(items: list, query_hash: str) -> list:
    """Normalize LinkedIn Jobs Scraper output.

    LinkedIn provides `listed_at` as epoch ms (e.g. 1746543600000) or
    `postedAt` as ISO; either feeds through to posted_date.
    """
    jobs = []
    for item in items:
        job = normalize_job({
            "title": item.get("title"),
            "company": item.get("companyName"),
            "description": item.get("descriptionText") or item.get("description") or item.get("descriptionHtml"),
            "location": item.get("location"),
            "url": item.get("link") or item.get("url"),
            "experienceLevel": item.get("experienceLevel"),
            "jobType": item.get("employmentType") or item.get("contractType"),
            "listed_at": item.get("listedAt") or item.get("listed_at"),
            "postedAt": item.get("postedAt") or item.get("postedDate"),
        }, source="linkedin", query_hash=query_hash)
        if job:
            jobs.append(job)
    return jobs


def normalize_indeed(items: list, query_hash: str) -> list:
    """Normalize Indeed Scraper output.

    Indeed gives `pubDate` (ISO) or `formattedRelativeTime` ("3 days ago").
    Relative-time parsing isn't worth implementing — fall back to scrape
    time when only relative is available.
    """
    jobs = []
    for item in items:
        job = normalize_job({
            "title": item.get("positionName") or item.get("title"),
            "company": item.get("company"),
            "description": item.get("description"),
            "location": item.get("location"),
            "url": item.get("url") or item.get("externalApplyLink"),
            "jobType": item.get("jobType"),
            "postedAt": item.get("pubDate") or item.get("postedDate") or item.get("postingDateParsed"),
        }, source="indeed", query_hash=query_hash)
        if job:
            jobs.append(job)
    return jobs


def normalize_adzuna(items: list, query_hash: str) -> list:
    """Normalize Adzuna API response.

    Adzuna's `created` field is the posting timestamp (ISO 8601).
    """
    jobs = []
    for item in items:
        job = normalize_job({
            "title": item.get("title"),
            "company": (item.get("company") or {}).get("display_name"),
            "description": item.get("description"),
            "location": (item.get("location") or {}).get("display_name"),
            "url": item.get("redirect_url"),
            "created": item.get("created"),
        }, source="adzuna", query_hash=query_hash)
        if job:
            jobs.append(job)
    return jobs


def normalize_hn(items: list, query_hash: str) -> list:
    """Normalize HN Hiring comment-parsed jobs."""
    jobs = []
    for item in items:
        job = normalize_job(item, source="hn_hiring", query_hash=query_hash)
        if job:
            jobs.append(job)
    return jobs


def normalize_glassdoor(items: list, query_hash: str) -> list:
    """Normalize cheap_scraper/glassdoor-jobs-scraper-remove-duplicate-jobs output.

    Actor returns: title, company{companyName}, description_text, location_country,
    location_city, jobUrl, applyUrl, attributes, jobTypes, etc.
    """
    jobs = []
    for item in items:
        company = item.get("company", {})
        company_name = company.get("companyName") if isinstance(company, dict) else str(company)
        location_parts = [item.get("location_city"), item.get("location_state"), item.get("location_country")]
        location = ", ".join(p for p in location_parts if p)
        job = normalize_job({
            "title": item.get("title"),
            "company": company_name,
            "description": item.get("description_text") or item.get("description_html"),
            "location": location,
            "url": item.get("jobUrl") or item.get("applyUrl"),
        }, source="glassdoor", query_hash=query_hash)
        if job:
            jobs.append(job)
    return jobs


def normalize_generic_web(items: list, source: str, query_hash: str) -> list:
    """Normalize Apify Web Scraper output (GradIreland, IrishJobs, Jobs.ie)."""
    jobs = []
    for item in items:
        job = normalize_job(item, source=source, query_hash=query_hash)
        if job:
            jobs.append(job)
    return jobs
