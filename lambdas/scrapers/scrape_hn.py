"""HN Hiring scraper — fetches latest 'Who is hiring?' thread via Algolia API.

Uses search_by_date (not search) so we always get the most recent monthly
thread, and paginates comments to capture the full thread.
"""
import logging
import re
import time
from datetime import datetime, timedelta

import boto3
import httpx

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

# Matches "Ask HN: Who is hiring? (Month Year)"
_THREAD_TITLE_RE = re.compile(
    r"^Ask HN: Who is hiring\?", re.IGNORECASE
)

# Max comments per page from Algolia (their limit)
_COMMENTS_PAGE_SIZE = 200
# Max pages to paginate (safety cap: 5 * 200 = 1000 comments)
_MAX_COMMENT_PAGES = 5

# Match a bare URL (greedy until whitespace/quote/bracket). We strip
# trailing sentence punctuation (".,;:)]") in `_strip_url_punctuation`
# rather than excluding it in the regex — excluding it as a lookahead
# also rejects in-domain dots (e.g. acme.com).
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TRAILING_PUNCT = ".,;:)]>}"


def _strip_url_punctuation(url: str) -> str:
    """Remove trailing sentence punctuation that almost always isn't part
    of the URL: 'https://x.com/jobs.' -> 'https://x.com/jobs'."""
    return url.rstrip(_TRAILING_PUNCT)


def _find_urls(text: str) -> list[str]:
    """All URLs in text, with trailing punctuation stripped."""
    return [_strip_url_punctuation(u) for u in _URL_RE.findall(text)]

# Comments often introduce the apply path with one of these prefixes. Lines
# matching these get URL preference even if the URL itself is generic.
_APPLY_PREFIX_RE = re.compile(
    r"\b(apply|application[s]?|to\s+apply|how\s+to\s+apply|details?|more\s+info|jobs?|careers?|"
    r"submit|email\s+(?:me|us)|contact|reach\s+out|hiring|opening[s]?|opportunit(?:y|ies))\b"
    r"\s*[:\-]",
    re.IGNORECASE,
)

# Strong-signal substrings inside the URL itself — preferred when picking
# from a list of bare URLs.
_URL_HINT_RE = re.compile(
    r"/(apply|jobs?|careers?|hiring|positions?|openings?|workable|greenhouse|lever|ashby"
    r"|workday|smartrecruiters|breezy|recruitee|workatastartup)\b",
    re.IGNORECASE,
)


def extract_apply_url(text: str) -> str:
    """Pick the best apply URL from a HN hiring comment body.

    Priority (highest first):
        1. URL on the same line as "apply:" / "jobs:" / similar prefix
        2. URL whose path contains a hiring-related segment
           (e.g. /careers, /jobs, /apply)
        3. mailto: + first email address (so the user can at least cold-email)
        4. First plain http(s) URL anywhere in the body
        5. Empty string (caller falls back to "")

    HN comments are ~90% in patterns 1+2; the email fallback covers the
    "small bootstrapped startup, just email me" case which is otherwise
    uncovered.
    """
    if not text:
        return ""

    lines = [line.strip() for line in text.split("\n") if line.strip()]

    # Pass 1: prefix-introduced URLs ("Apply: https://...")
    for line in lines:
        if _APPLY_PREFIX_RE.search(line):
            urls = _find_urls(line)
            if urls:
                return urls[0]

    # Pass 2: URLs whose path screams "hiring page"
    body_urls = _find_urls(text)
    for url in body_urls:
        if _URL_HINT_RE.search(url):
            return url

    # Pass 3: email fallback
    emails = _EMAIL_RE.findall(text)
    if emails:
        return f"mailto:{emails[0]}"

    # Pass 4: first URL anywhere
    if body_urls:
        return body_urls[0]

    return ""


def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


def parse_hn_comment(text: str, comment_url: str = "") -> dict:
    """Parse a HN hiring comment into job fields.

    `comment_url` (when supplied by the caller — typically the Algolia hit's
    permalink) is used as a last-resort apply URL so the row at least
    points at the HN comment if no better URL was extractable.
    """
    import html as html_mod
    # Capture <a href="..."> links before stripping tags — HN renders most
    # apply URLs as anchor tags; the visible href is often shortened in the
    # text but the actual href has the full destination.
    href_urls = re.findall(r'<a\s+href="([^"]+)"', text or "", flags=re.IGNORECASE)

    text = html_mod.unescape(text)
    text = re.sub(r'<[^>]+>', '\n', text).strip()
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    if not lines:
        return None

    first_line = lines[0]
    parts = [p.strip() for p in first_line.split('|')]
    company = parts[0] if parts else ""
    title = parts[1] if len(parts) > 1 else ""
    location = parts[2] if len(parts) > 2 else ""

    description = '\n'.join(lines)

    if not company or not title:
        return None

    # Apply-URL extraction: prefer href tag-attributes from the original
    # comment HTML (most reliable), then fall back to text-based heuristics,
    # and finally to the comment permalink (so the row is at least usable).
    apply_url = ""
    for href in href_urls:
        if _URL_HINT_RE.search(href):
            apply_url = html_mod.unescape(href)
            break
    if not apply_url and href_urls:
        apply_url = html_mod.unescape(href_urls[0])
    if not apply_url:
        apply_url = extract_apply_url(description)
    if not apply_url and comment_url:
        apply_url = comment_url

    return {
        "title": title,
        "company": company,
        "description": description,
        "location": location,
        "url": apply_url,
    }


def _find_latest_thread() -> str | None:
    """Find the objectID of the latest 'Who is hiring?' monthly thread.

    Uses search_by_date (date-sorted) with a 35-day lookback so we always
    get the current month's thread rather than a years-old relevance hit.
    Returns None if no matching thread is found.
    """
    search_url = "https://hn.algolia.com/api/v1/search_by_date"
    cutoff = int(time.time()) - 35 * 86400  # 35 days ago
    params = {
        "query": '"Who is hiring"',
        "tags": "story",
        "numericFilters": f"created_at_i>{cutoff}",
        "hitsPerPage": 10,
    }
    resp = httpx.get(search_url, params=params, timeout=15)
    if resp.status_code != 200:
        logger.warning(f"[hn_hiring] Thread search: HTTP {resp.status_code}")
        return None

    hits = resp.json().get("hits", [])
    for hit in hits:
        title = hit.get("title", "")
        if _THREAD_TITLE_RE.search(title):
            logger.info(f"[hn_hiring] Found thread: {title} (id={hit['objectID']})")
            return hit["objectID"]

    logger.warning(f"[hn_hiring] No matching thread in {len(hits)} hits")
    return None


def _fetch_all_comments(thread_id: str) -> list[dict]:
    """Fetch all comments for a thread, paginating through results."""
    all_comments = []
    for page in range(_MAX_COMMENT_PAGES):
        url = "https://hn.algolia.com/api/v1/search"
        params = {
            "tags": f"comment,story_{thread_id}",
            "hitsPerPage": _COMMENTS_PAGE_SIZE,
            "page": page,
        }
        resp = httpx.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            logger.warning(f"[hn_hiring] Comments page {page}: HTTP {resp.status_code}")
            break
        data = resp.json()
        hits = data.get("hits", [])
        all_comments.extend(hits)
        # Stop if we got fewer than a full page (no more results)
        if len(hits) < _COMMENTS_PAGE_SIZE:
            break
    return all_comments


def handler(event, context):
    query_hash = event.get("query_hash", "")
    cache_ttl_hours = event.get("cache_ttl_hours", 168)  # 1 week for HN

    db = get_supabase()

    # Check cache
    cached = db.table("jobs_raw").select("job_hash", count="exact") \
        .eq("source", "hn_hiring").eq("query_hash", query_hash) \
        .gte("scraped_at", (datetime.utcnow() - timedelta(hours=cache_ttl_hours)).isoformat()) \
        .execute()
    if cached.count > 0:
        return {"count": cached.count, "source": "hn_hiring", "cached": True}

    # Find latest "Who is hiring?" thread (date-sorted, title-validated)
    thread_id = _find_latest_thread()
    if not thread_id:
        return {"count": 0, "source": "hn_hiring", "error": "no_thread_found"}

    # Fetch all comments with pagination
    comments = _fetch_all_comments(thread_id)

    from normalizers import normalize_hn
    parsed = []
    for c in comments:
        text = c.get("comment_text", "")
        if not text or len(text) < 50:
            continue
        # HN permalink — used as last-resort apply URL when the comment
        # body has no extractable link or email.
        comment_url = f"https://news.ycombinator.com/item?id={c.get('objectID', '')}"
        job = parse_hn_comment(text, comment_url=comment_url)
        if job:
            # Algolia returns the comment's posting time as epoch seconds in
            # `created_at_i`. normalize_job picks this up via its field
            # alias list and converts to UTC ISO.
            job["created_at_i"] = c.get("created_at_i")
            parsed.append(job)

    jobs = normalize_hn(parsed, query_hash)

    # Deduplicate by job_hash within batch
    seen = set()
    unique = []
    for j in jobs:
        if j["job_hash"] not in seen:
            seen.add(j["job_hash"])
            unique.append(j)
    jobs = unique

    if jobs:
        now = datetime.utcnow().isoformat()
        for job in jobs:
            job["scraped_at"] = now
        db.table("jobs_raw").upsert(jobs, on_conflict="job_hash").execute()

    logger.info(f"[hn_hiring] {len(jobs)} jobs from {len(comments)} comments (thread {thread_id})")
    return {"count": len(jobs), "source": "hn_hiring"}
