"""Normalize scraper output to standard jobs_raw schema."""
import html
import re

from utils.canonical_hash import canonical_hash

def normalize_job(raw: dict, source: str, query_hash: str = "") -> dict:
    """Normalize a raw job dict to jobs_raw schema."""
    title = html.unescape(raw.get("title") or raw.get("positionName") or "").strip()
    company = html.unescape(raw.get("company") or raw.get("companyName") or "").strip()
    description = html.unescape(raw.get("description") or raw.get("text") or "").strip()
    description = re.sub(r'<[^>]+>', '\n', description).strip()
    location = raw.get("location") or raw.get("city") or ""
    apply_url = raw.get("url") or raw.get("applyUrl") or raw.get("apply_url") or ""

    if not title or not company:
        return None

    job_hash = canonical_hash(company, title, description)

    return {
        "job_hash": job_hash,
        "title": title[:500],
        "company": company[:200],
        "description": description[:10000],
        "location": location[:200],
        "apply_url": apply_url[:1000],
        "source": source,
        "experience_level": raw.get("experienceLevel") or raw.get("experience_level"),
        "job_type": raw.get("jobType") or raw.get("job_type"),
        "query_hash": query_hash,
    }


def normalize_linkedin(items: list, query_hash: str) -> list:
    """Normalize LinkedIn Jobs Scraper output."""
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
        }, source="linkedin", query_hash=query_hash)
        if job:
            jobs.append(job)
    return jobs


def normalize_indeed(items: list, query_hash: str) -> list:
    """Normalize Indeed Scraper output."""
    jobs = []
    for item in items:
        job = normalize_job({
            "title": item.get("positionName") or item.get("title"),
            "company": item.get("company"),
            "description": item.get("description"),
            "location": item.get("location"),
            "url": item.get("url") or item.get("externalApplyLink"),
            "jobType": item.get("jobType"),
        }, source="indeed", query_hash=query_hash)
        if job:
            jobs.append(job)
    return jobs


def normalize_adzuna(items: list, query_hash: str) -> list:
    """Normalize Adzuna API response."""
    jobs = []
    for item in items:
        job = normalize_job({
            "title": item.get("title"),
            "company": (item.get("company") or {}).get("display_name"),
            "description": item.get("description"),
            "location": (item.get("location") or {}).get("display_name"),
            "url": item.get("redirect_url"),
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
