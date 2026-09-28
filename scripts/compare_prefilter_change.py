#!/usr/bin/env python3
"""Measure a prefilter change against the real jobs_raw corpus, per job.

Read-only. Never writes to the database.

Runs the CURRENT lambdas/pipeline/merge_dedup.py and a BASELINE snapshot of
it over the same corpus rows and reports the exact set difference, not just
two admit rates. Aggregate rates can hide an equal-sized swap (N jobs newly
admitted, N newly rejected nets to zero), which is exactly the failure mode a
relevance-filter change is prone to.

Both modules see identical rows and the same scrape-time freshness replay as
scripts/tune_prefilter.py, so the only variable is the filter itself.

Usage:
    .venv/bin/python scripts/compare_prefilter_change.py \
        --baseline /path/to/merge_dedup_baseline.py
"""
import argparse
import importlib.util
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lambdas" / "pipeline"))

from db_client import SupabaseClient  # noqa: E402
from tune_prefilter import _fetch_all_jobs_raw, _replay_posted_date  # noqa: E402



def _load_baseline(path: Path):
    """Import the baseline snapshot under its own module name."""
    spec = importlib.util.spec_from_file_location("merge_dedup_baseline", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["merge_dedup_baseline"] = mod
    spec.loader.exec_module(mod)
    return mod


def main(baseline_path: str, samples: int) -> None:
    import merge_dedup as new

    base = _load_baseline(Path(baseline_path))
    db = SupabaseClient.from_env().client

    exact = db.table("jobs_raw").select("job_hash", count="exact").limit(1).execute().count
    rows = _fetch_all_jobs_raw(db)
    print(f"jobs_raw: fetched {len(rows)} rows; count(exact)={exact}")
    if len(rows) != exact:
        print("  MISMATCH -- pagination incomplete, stop and investigate")
        return

    cfg_rows = db.table("user_search_configs").select("*").execute().data or []
    cfg = cfg_rows[0] if cfg_rows else {}
    if not cfg_rows:
        print("  WARNING: no user_search_configs row -- this does not reflect production")

    # Print the raw row. Assuming this column's shape rather than looking at it
    # is precisely how Rule 4 ended up silently disabled in production.
    print("\n=== live user_search_configs row (relevant keys, raw) ===")
    for key in ("queries", "experience_levels", "locations", "geo_regions",
                "include_internships"):
        print(f"  {key:22s} = {cfg.get(key)!r}")

    profile = new.build_prefilter_profile(
        queries=cfg.get("queries") or [],
        experience_levels=cfg.get("experience_levels") or [],
        geo_regions=cfg.get("geo_regions") or [],
        locations=new._config_locations(cfg.get("locations")),
        include_internships=bool(cfg.get("include_internships", False)),
    )
    print(f"Profile: domain_tech={profile.is_domain_tech} skills={len(profile.skills)} "
          f"phrases={len(profile.query_phrases)} tiers={sorted(profile.excluded_tiers)} "
          f"remote_only={sorted(profile.remote_only_regions)} "
          f"exempt={sorted(profile.exempt_regions)}")

    # The baseline's pre-refactor signature: (job, user_skills, max_age_days,
    # query_phrases). Feed it the same user config the new profile was built
    # from, exactly as the old handler did, so this is a fair comparison of
    # the FILTER and not of how much config each side was handed.
    base_skills = set(base.DEFAULT_USER_SKILLS)
    for q in (cfg.get("queries") or []):
        base_skills |= {w.lower() for w in q.split() if len(w) > 2}
    base_phrases = base._query_phrases(cfg.get("queries") or [])

    now = datetime.now(timezone.utc)
    newly_admitted, newly_rejected = [], []
    base_admit = new_admit = 0
    new_reasons, base_reasons = Counter(), Counter()
    reason_changed = Counter()

    for row in rows:
        sim = _replay_posted_date(row, now)
        b_pass, b_reason = base._prefilter_job(sim, base_skills, query_phrases=base_phrases)
        n_pass, n_reason = new._prefilter_job(sim, profile)
        base_admit += b_pass
        new_admit += n_pass
        if not b_pass:
            base_reasons[b_reason.split(":", 1)[0]] += 1
        if not n_pass:
            new_reasons[n_reason.split(":", 1)[0]] += 1
        if b_pass and not n_pass:
            newly_rejected.append((row, b_reason, n_reason))
        elif n_pass and not b_pass:
            newly_admitted.append((row, b_reason, n_reason))
        elif not b_pass and not n_pass and b_reason != n_reason:
            reason_changed[f"{b_reason.split(':')[0]} -> {n_reason.split(':')[0]}"] += 1

    total = len(rows)
    print(f"\n=== Admit rate on {total} real rows ===")
    print(f"  baseline : {base_admit:6d} ({100*base_admit/total:.2f}%)")
    print(f"  new      : {new_admit:6d} ({100*new_admit/total:.2f}%)")
    print(f"  delta    : {new_admit-base_admit:+6d} ({100*(new_admit-base_admit)/total:+.2f} pp)")
    print(f"\n  newly ADMITTED (regression risk: extra scoring load): {len(newly_admitted)}")
    print(f"  newly REJECTED (regression risk: lost good jobs)     : {len(newly_rejected)}")

    print("\n=== Reject reason breakdown ===")
    print(f"  {'bucket':32s} {'baseline':>9s} {'new':>9s}")
    for bucket in sorted(set(base_reasons) | set(new_reasons)):
        print(f"  {bucket:32s} {base_reasons[bucket]:9d} {new_reasons[bucket]:9d}")

    if reason_changed:
        print("\n=== Still rejected, different reason (relabel only) ===")
        for change, n in reason_changed.most_common():
            print(f"  {n:6d}  {change}")

    for label, items in (("NEWLY REJECTED", newly_rejected), ("NEWLY ADMITTED", newly_admitted)):
        if not items:
            continue
        print(f"\n=== {min(samples, len(items))} sampled {label} ===")
        for row, b_reason, n_reason in items[:samples]:
            print(f"  [{row.get('source')}] {str(row.get('title'))[:62]!r} @ "
                  f"{str(row.get('company'))[:26]!r} | loc={str(row.get('location'))[:28]!r}")
            print(f"      baseline={b_reason}  ->  new={n_reason}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True, help="path to the pre-change merge_dedup.py")
    ap.add_argument("--samples", type=int, default=25)
    a = ap.parse_args()
    main(a.baseline, a.samples)
