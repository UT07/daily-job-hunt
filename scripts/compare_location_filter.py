#!/usr/bin/env python3
"""Measure the scrape-time location filter against real data, per job.

Read-only. Never writes to the database.

Two corpora, because neither alone can answer the question:

  --corpus db      Every row in jobs_raw. This is what a run actually
                   produced, so it shows exactly which stored rows the new
                   filter would no longer store, and (with --downstream) how
                   many of those would have survived merge_dedup's prefilter
                   and reached scoring. It CANNOT show the other direction:
                   a posting the old filter rejected was never written, so
                   the widening is invisible here.

  --corpus boards  A live fetch of the configured Greenhouse and Ashby
                   boards. The whole population, including what the old
                   filter rejected, so this is the one that can show what the
                   change newly ADMITS. Public APIs, no auth, no writes.

Sibling of scripts/compare_prefilter_change.py, which does the same job for
merge_dedup's relevance filter; same per-job set-difference discipline,
because two aggregate admit rates can hide an equal-sized swap.

Usage:
    .venv/bin/python scripts/compare_location_filter.py
    .venv/bin/python scripts/compare_location_filter.py --corpus boards
    .venv/bin/python scripts/compare_location_filter.py --downstream
"""
import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent

def _load_env() -> None:
    """Read .env from the repo root, or from the main checkout when this is
    running inside a git worktree (which does not get its own gitignored
    .env). Silently does nothing if neither exists — the caller's environment
    may already carry the credentials."""
    candidates = [ROOT / ".env"]
    git_file = ROOT / ".git"
    if git_file.is_file():  # a worktree: .git is a file pointing at the main repo
        text = git_file.read_text().strip()
        if text.startswith("gitdir:"):
            gitdir = Path(text.split(":", 1)[1].strip())
            for parent in gitdir.parents:
                if parent.name == ".git":
                    candidates.append(parent.parent / ".env")
                    break
    for env_path in candidates:
        if not env_path.exists():
            continue
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
        return


_load_env()

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lambdas" / "pipeline"))
sys.path.insert(0, str(ROOT / "scripts"))

from shared.location_policy import (  # noqa: E402
    _contains_token,
    _normalized,
    build_location_policy,
    location_verdict,
)

# The filter this change replaces, reproduced verbatim from
# lambdas/scrapers/scrape_greenhouse.py:30 and scrape_ashby.py:30 as they
# stood at 602b08c. Kept here rather than imported so the comparison survives
# their deletion.
LEGACY_KEYWORDS = {"ireland", "dublin", "remote", "emea", "europe", "anywhere"}

# Only these two scrapers filter client-side on location; the rest either take
# a location in their search URL (LinkedIn/Indeed/Glassdoor) or have no
# location gate at all (HN, YC, Adzuna, the Irish portals).
FILTERED_SOURCES = ("greenhouse", "ashby")

GREENHOUSE_PARAM = "/naukribaba/GREENHOUSE_BOARDS"
ASHBY_PARAM = "/naukribaba/ASHBY_COMPANIES"


def _legacy_admit(location: str, source: str = "", is_remote: bool = False) -> bool:
    if source == "ashby" and is_remote:
        return True  # the old isRemote short-circuit
    return any(kw in (location or "").lower() for kw in LEGACY_KEYWORDS)


def _get_db():
    from supabase import create_client
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _live_config(db):
    rows = db.table("user_search_configs").select("*").execute().data or []
    if not rows:
        print("  WARNING: no user_search_configs row — this does not reflect production")
        return {}
    return rows[0]


def _report(rows, policy, label):
    """rows: iterable of (source, location, is_remote, title, company)."""
    print(f"\n=== {label}: per-source admit rate ===")
    print(f"{'source':14s} {'n':>6s} {'old':>6s} {'new':>6s} {'kept':>6s} "
          f"{'dropped':>8s} {'added':>6s}")
    dropped_rows, added_rows = [], []
    totals = Counter()
    for source in sorted({r[0] for r in rows}):
        sub = [r for r in rows if r[0] == source]
        counts = Counter()
        for src, loc, is_remote, title, company in sub:
            old = _legacy_admit(loc, src, is_remote)
            new, reason = location_verdict(loc, policy, is_remote)
            counts["old"] += old
            counts["new"] += new
            counts["kept"] += old and new
            if old and not new:
                counts["dropped"] += 1
                dropped_rows.append((src, loc, title, company, reason))
            if new and not old:
                counts["added"] += 1
                added_rows.append((src, loc, title, company, reason))
        totals.update(counts)
        totals["n"] += len(sub)
        print(f"{source:14s} {len(sub):6d} {counts['old']:6d} {counts['new']:6d} "
              f"{counts['kept']:6d} {counts['dropped']:8d} {counts['added']:6d}")
    print(f"{'TOTAL':14s} {totals['n']:6d} {totals['old']:6d} {totals['new']:6d} "
          f"{totals['kept']:6d} {totals['dropped']:8d} {totals['added']:6d}")

    print("\n--- newly DROPPED, by location ---")
    for loc, n in Counter(r[1] for r in dropped_rows).most_common(20):
        print(f"  {n:5d}  {loc!r}")

    print("\n--- newly ADMITTED, by location ---")
    if not added_rows:
        print("  (none — this corpus cannot show them; try --corpus boards)")
    for loc, n in Counter(r[1] for r in added_rows).most_common(20):
        print(f"  {n:5d}  {loc!r}")

    # Uses the policy's own word-boundary matcher, not a substring test: a
    # substring check here reports "Leuven, Belgium" as naming the EU.
    bad = [r for r in dropped_rows
           if any(_contains_token(_normalized(r[1]), t) for t in policy.accept_tokens)]
    print(f"\n--- SAFETY: dropped rows naming a location the user asked for: {len(bad)} ---")
    for r in bad[:15]:
        print(f"  {r}")
    return dropped_rows, added_rows


def _corpus_db(db):
    rows, offset, page = [], 0, 1000
    while True:
        batch = (db.table("jobs_raw")
                 .select("job_hash, source, location, title, company, description, "
                         "posted_date, scraped_at")
                 .order("job_hash").range(offset, offset + page - 1).execute().data or [])
        rows.extend(batch)
        if len(batch) < page:
            break
        offset += page
    exact = db.table("jobs_raw").select("job_hash", count="exact").limit(1).execute().count
    print(f"jobs_raw: fetched {len(rows)} rows; count(exact)={exact}")
    if len(rows) != exact:
        print("  MISMATCH — pagination incomplete, stop and investigate")
        sys.exit(1)
    return rows


def _corpus_boards(db):
    """Live fetch of the configured Greenhouse/Ashby boards. No auth, no writes."""
    import json

    import httpx

    def _boards(param, default):
        try:
            import boto3
            value = boto3.client("ssm").get_parameter(
                Name=param, WithDecryption=True)["Parameter"]["Value"]
            return json.loads(value)
        except Exception as exc:
            print(f"  {param} unreadable ({type(exc).__name__}); using the "
                  f"scraper's own DEFAULT list")
            return default

    sys.path.insert(0, str(ROOT / "lambdas" / "scrapers"))
    gh_default = ["stripe", "intercom", "mongodb", "twilio", "datadog",
                  "pagerduty", "toast", "cloudflare", "elastic", "ripple"]
    ashby_default = ["anthropic", "linear", "vercel", "notion", "figma", "retool"]

    rows = []
    client = httpx.Client(timeout=60, follow_redirects=True)
    for slug in _boards(GREENHOUSE_PARAM, gh_default):
        try:
            resp = client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
            if resp.status_code != 200:
                print(f"  greenhouse/{slug}: HTTP {resp.status_code}")
                continue
            jobs = resp.json().get("jobs", [])
            print(f"  greenhouse/{slug}: {len(jobs)}")
            for j in jobs:
                loc = (j.get("location") or {}).get("name", "") or ""
                rows.append(("greenhouse", loc, False, j.get("title", ""), slug))
        except Exception as exc:
            print(f"  greenhouse/{slug}: {exc}")
    for company in _boards(ASHBY_PARAM, ashby_default):
        try:
            resp = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{company}")
            if resp.status_code != 200:
                print(f"  ashby/{company}: HTTP {resp.status_code}")
                continue
            jobs = resp.json().get("jobs", [])
            print(f"  ashby/{company}: {len(jobs)}")
            for j in jobs:
                loc = j.get("location", "")
                if isinstance(loc, dict):
                    loc = loc.get("name", "")
                rows.append(("ashby", loc or "", bool(j.get("isRemote")),
                             j.get("title", ""), company))
        except Exception as exc:
            print(f"  ashby/{company}: {exc}")
    client.close()
    return rows


def _downstream(db, rows, policy, cfg):
    """How many rows that merge_dedup admits TODAY would no longer be scraped.

    This is the owner-visible number: jobs_raw volume is a cost, but what the
    owner actually sees is what survives the prefilter and competes for the
    MAX_JOBS_PER_RUN scoring budget.
    """
    import merge_dedup as md
    from tune_prefilter import _replay_posted_date

    profile = md.build_prefilter_profile(
        queries=cfg.get("queries") or [],
        experience_levels=cfg.get("experience_levels") or [],
        geo_regions=cfg.get("geo_regions") or [],
        locations=md._config_locations(cfg.get("locations")),
        include_internships=bool(cfg.get("include_internships", False)),
    )
    now = datetime.now(timezone.utc)

    before, after, lost = 0, 0, []
    scored_before, scored_after = [], []
    for row in rows:
        passes, _reason = md._prefilter_job(_replay_posted_date(row, now), profile)
        if not passes:
            continue
        before += 1
        scored_before.append(row)
        if row["source"] in FILTERED_SOURCES and not location_verdict(
            row.get("location") or "", policy
        )[0]:
            lost.append(row)
            continue
        after += 1
        scored_after.append(row)

    print("\n=== merge_dedup prefilter (the owner-visible outcome) ===")
    print(f"  admitted today                 : {before}")
    print(f"  admitted after this change     : {after}")
    print(f"  lost (admitted, now not scraped): {len(lost)}")

    # What the owner actually sees: only MAX_JOBS_PER_RUN of the admitted pool
    # is scored each run, chosen by _job_relevance_rank, which is
    # location-blind. So the sharp question is how much of that budget is
    # currently spent on postings the user cannot take.
    cap = md.MAX_JOBS_PER_RUN
    today_top = sorted(
        scored_before, key=lambda j: md._job_relevance_rank(j, profile), reverse=True
    )[:cap]
    wasted = [
        j for j in today_top
        if j["source"] in FILTERED_SOURCES
        and not location_verdict(j.get("location") or "", policy)[0]
    ]
    print(f"  of today's top-{cap} scoring budget, {len(wasted)} go to postings "
          f"this change would not have scraped")
    for loc, n in Counter((j.get("location") or "").strip() for j in wasted).most_common(10):
        print(f"      {n:4d}  {loc!r}")

    print("\n--- the lost rows, by location ---")
    for loc, n in Counter((r.get("location") or "").strip() for r in lost).most_common(20):
        print(f"  {n:5d}  {loc!r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", choices=("db", "boards"), default="db")
    ap.add_argument("--downstream", action="store_true",
                    help="also run merge_dedup's prefilter over the db corpus")
    args = ap.parse_args()

    db = _get_db()
    cfg = _live_config(db)
    print("\n=== live user_search_configs row (raw) ===")
    for key in ("queries", "locations", "geo_regions", "experience_levels"):
        print(f"  {key:20s} = {cfg.get(key)!r}")

    policy = build_location_policy(cfg.get("locations"))
    print(f"\nPolicy: source={policy.source} locations={list(policy.locations)} "
          f"regions={sorted(policy.target_regions)} search_term={policy.search_term!r}")
    print(f"        accept_tokens={sorted(policy.accept_tokens)}")

    if args.corpus == "boards":
        rows = _corpus_boards(db)
        print(f"\nlive postings fetched: {len(rows)}")
        _report(rows, policy, "LIVE BOARDS")
        return

    raw = _corpus_db(db)
    _report(
        [(r["source"], r.get("location") or "", False, r.get("title") or "",
          r.get("company") or "") for r in raw],
        policy,
        "jobs_raw (NOTE: greenhouse/ashby rows here already passed the OLD "
        "filter, so 'added' is structurally 0 for them — use --corpus boards)",
    )
    if args.downstream:
        _downstream(db, raw, policy, cfg)


if __name__ == "__main__":
    main()
