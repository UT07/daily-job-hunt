"""The board scrapers filter on the USER's locations, not a hardcoded set.

End-to-end at the Lambda-handler level, because the unit tests for
shared/location_policy.py can only prove the policy is right — not that the
scraper actually consults it with the event's `locations`. The bug being
pinned was exactly that gap: the policy value existed in the config all along
and simply never reached the code that filtered.
"""
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import respx

SCRAPERS_DIR = Path(__file__).resolve().parents[2] / "lambdas" / "scrapers"


def _make_db():
    db = MagicMock()
    table = MagicMock()
    db.table.return_value = table
    for method in ("select", "eq", "gte", "in_", "order", "limit",
                   "insert", "update", "upsert", "delete"):
        getattr(table, method).return_value = table
    result = MagicMock()
    result.count = 0
    result.data = []
    table.execute.return_value = result
    return db


def _saved_locations(db):
    """Every `location` the handler tried to persist to jobs_raw."""
    saved = []
    for call in db.table.return_value.insert.call_args_list:
        rows = call.args[0] if call.args else []
        saved.extend(r.get("location") for r in (rows if isinstance(rows, list) else [rows]))
    for call in db.table.return_value.upsert.call_args_list:
        rows = call.args[0] if call.args else []
        saved.extend(r.get("location") for r in (rows if isinstance(rows, list) else [rows]))
    return saved


def _desc(tag):
    """Distinct per posting: the scrapers dedup on
    canonical_hash(company, title, description), so identical bodies would
    collapse four fixtures into one row and the test would pass vacuously."""
    return f"<p>We need an SRE for the {tag} team. " + ("Build pipelines. " * 25) + "</p>"


_GREENHOUSE_JOBS = {
    "jobs": [
        {"id": 1, "title": "SRE", "location": {"name": "Dublin, Ireland"},
         "content": _desc("dublin"), "absolute_url": "https://x/1",
         "updated_at": "2026-09-27T10:00:00Z"},
        {"id": 2, "title": "SRE", "location": {"name": "Remote - US"},
         "content": _desc("us"), "absolute_url": "https://x/2",
         "updated_at": "2026-09-27T10:00:00Z"},
        {"id": 3, "title": "SRE", "location": {"name": "Remote"},
         "content": _desc("anywhere"), "absolute_url": "https://x/3",
         "updated_at": "2026-09-27T10:00:00Z"},
        {"id": 4, "title": "SRE", "location": {"name": "Bangalore, India"},
         "content": _desc("blr"), "absolute_url": "https://x/4",
         "updated_at": "2026-09-27T10:00:00Z"},
    ]
}

_ASHBY_JOBS = {
    "organizationName": "Acme",
    "jobs": [
        {"title": "SRE", "location": "Dublin, Ireland", "isRemote": False,
         "descriptionHtml": _desc("dublin"), "jobUrl": "https://a/1",
         "publishedAt": "2026-09-27T10:00:00Z"},
        {"title": "SRE", "location": "New York, NY (HQ)", "isRemote": True,
         "descriptionHtml": _desc("nyc"), "jobUrl": "https://a/2",
         "publishedAt": "2026-09-27T10:00:00Z"},
    ]
}


def _greenhouse_routes():
    respx.get(url__regex=r"https://boards-api\.greenhouse\.io/.*").mock(
        return_value=httpx.Response(200, json=_GREENHOUSE_JOBS)
    )


def _ashby_routes():
    respx.get(url__regex=r"https://api\.ashbyhq\.com/.*").mock(
        return_value=httpx.Response(200, json=_ASHBY_JOBS)
    )


# ---------------------------------------------------------------------------
# Greenhouse
# ---------------------------------------------------------------------------


@respx.mock
@patch("scrape_greenhouse.get_param", side_effect=Exception("no SSM"))
@patch("scrape_greenhouse.get_supabase")
def test_greenhouse_honours_the_users_locations(mock_db_fn, _param):
    import scrape_greenhouse
    db = _make_db()
    mock_db_fn.return_value = db
    _greenhouse_routes()

    scrape_greenhouse.handler(
        {"query_hash": "h", "locations": ["Dublin", "Ireland"]}, None
    )

    saved = set(_saved_locations(db))
    assert "Dublin, Ireland" in saved
    assert "Remote" in saved, "an unqualified-remote posting is open to anyone"
    assert "Remote - US" not in saved, (
        "a US-only remote role is not takeable from Dublin; admitting it on "
        "the bare word 'remote' is the old hardcoded LOCATION_KEYWORDS bug"
    )
    assert "Bangalore, India" not in saved


@respx.mock
@patch("scrape_greenhouse.get_param", side_effect=Exception("no SSM"))
@patch("scrape_greenhouse.get_supabase")
def test_greenhouse_follows_a_different_user(mock_db_fn, _param):
    """Same corpus, Mumbai-based user, no special-casing anywhere."""
    import scrape_greenhouse
    db = _make_db()
    mock_db_fn.return_value = db
    _greenhouse_routes()

    scrape_greenhouse.handler({"query_hash": "h", "locations": ["Mumbai"]}, None)

    saved = set(_saved_locations(db))
    assert "Bangalore, India" in saved
    assert "Dublin, Ireland" not in saved


@respx.mock
@patch("scrape_greenhouse.get_param", side_effect=Exception("no SSM"))
@patch("scrape_greenhouse.get_supabase")
def test_greenhouse_with_no_locations_falls_back_visibly(mock_db_fn, _param):
    import scrape_greenhouse
    db = _make_db()
    mock_db_fn.return_value = db
    _greenhouse_routes()

    scrape_greenhouse.handler({"query_hash": "h"}, None)

    saved = set(_saved_locations(db))
    assert "Dublin, Ireland" in saved
    assert "Bangalore, India" not in saved


# ---------------------------------------------------------------------------
# Ashby
# ---------------------------------------------------------------------------


@respx.mock
@patch("scrape_ashby.get_param", side_effect=Exception("no SSM"))
@patch("scrape_ashby.get_supabase")
def test_ashby_is_remote_no_longer_bypasses_the_location_check(mock_db_fn, _param):
    import scrape_ashby
    db = _make_db()
    mock_db_fn.return_value = db
    _ashby_routes()

    scrape_ashby.handler({"query_hash": "h", "locations": ["Dublin", "Ireland"]}, None)

    saved = set(_saved_locations(db))
    assert "Dublin, Ireland" in saved
    assert "New York, NY (HQ)" not in saved, (
        "isRemote used to short-circuit the filter entirely, which is how 983 "
        "'New York, NY (HQ)' rows reached jobs_raw"
    )


# ---------------------------------------------------------------------------
# Audit: no scraper may reintroduce a private location vocabulary
# ---------------------------------------------------------------------------


def test_no_scraper_hardcodes_its_own_location_vocabulary():
    """The thing that made this bug survive: two scrapers each owning a
    byte-identical private copy of the owner's geography, invisible from the
    config that was supposed to control it."""
    offenders = []
    for path in sorted(SCRAPERS_DIR.rglob("*.py")):
        text = path.read_text()
        if re.search(r"^LOCATION_KEYWORDS\s*=", text, re.MULTILINE):
            offenders.append(f"{path.name}: defines LOCATION_KEYWORDS")
        # A literal country/city set assigned at module scope is the same
        # smell under a different name.
        for match in re.finditer(
            r"^([A-Z_]+)\s*=\s*\{[^}]*\"(ireland|dublin|emea)\"", text, re.MULTILINE
        ):
            offenders.append(f"{path.name}: {match.group(1)} hardcodes geography")
    assert not offenders, (
        "location vocabulary belongs in shared/location_policy.py, derived "
        "from the user's config:\n  " + "\n  ".join(offenders)
    )
