#!/usr/bin/env python3
"""Recompute `jobs.score_tier` for rows where it contradicts `match_score`.

Pure DB operation -- does NOT re-call AI and does NOT change any score. Only
the denormalised `score_tier` label is rewritten, from the score already stored
on the same row.

Usage:
    python scripts/backfill_score_tier_consistency.py             # dry-run by default
    python scripts/backfill_score_tier_consistency.py --commit    # apply updates

WHY THIS EXISTS. `score_tier` is stored alongside `match_score` and nothing
enforces that they agree. The dashboard's tier filter queries the STORED column
(app.py ~2778 `.in_("score_tier", tiers)`), so a stale label means filtering by
A returns jobs that are not A -- the "tier pollution" a user sees.

Measured 2026-09-30, after scripts/backfill_geo_score_cap.py had run: 52 of
1,313 scored rows held a tier their own score does not support, e.g. "S" stored
against 89 (which is A), "A" against 75 (B), "B" against 68 (C).

The current pipeline does NOT produce this: score_batch.py computes
`score_tier: score_to_tier(match_score)` from the POST-cap score, so new rows
are consistent by construction. These 52 are historical -- written before the
geo/work-auth cap existed, or by a partial update. backfill_geo_score_cap.py
corrects the label as a side effect, but only on rows whose score the cap
changes, so a stale label on an Irish or unlocatable job is never reached.

Idempotent: re-running changes nothing, because the tier is derived from a value
this script never writes.

This is a one-off repair. The recurrence guard is the `score-tier-matches-score`
check in scripts/smoke_prod.py, which fails the deploy if drift ever returns.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "lambdas", "pipeline"))

# Explicit .env load, the same way smoke_prod.py and backfill_geo_score_cap.py
# do it (CLAUDE.md rule 8: configuration is explicit, never an import side
# effect). CI has no .env and passes real environment variables.
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(_ROOT, ".env"))
except ImportError:
    pass

# The pipeline's own function, imported rather than reimplemented. Three copies
# of these boundaries already exist (score_batch.score_to_tier, app._score_tier,
# backfill_geo_score_cap._score_to_tier); they agree today, verified across 18
# score points, but a fourth copy is a fourth thing that can drift from the
# column it is supposed to describe.
from score_batch import score_to_tier  # noqa: E402


def _get_supabase():
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY env required")
    return create_client(url, key)


def find_inconsistent(rows: list[dict]) -> list[dict]:
    """Rows whose stored tier is not what their own score implies.

    A row with no score is skipped rather than assigned a tier: absent data is
    not evidence of a low tier, and inventing one here would write a label the
    pipeline never derived.
    """
    out = []
    for row in rows:
        score = row.get("match_score")
        if score is None:
            continue
        expected = score_to_tier(score)
        if (row.get("score_tier") or "") != expected:
            out.append({**row, "_expected_tier": expected})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true",
                        help="apply updates (default is a dry run)")
    args = parser.parse_args()

    db = _get_supabase()

    rows, offset = [], 0
    while True:
        page = (
            db.table("jobs")
            .select("job_id, job_hash, match_score, score_tier, title")
            .not_.is_("match_score", "null")
            .range(offset, offset + 999)
            .execute()
        ).data or []
        rows += page
        if len(page) < 1000:
            break
        offset += 1000

    bad = find_inconsistent(rows)
    moves = Counter(f"{r.get('score_tier') or 'None'}->{r['_expected_tier']}" for r in bad)

    for row in bad[:8]:
        print(f"    {'UPDATE' if args.commit else 'DRY-RUN would update'} "
              f"{str(row['job_id'])[:8]}: {row.get('score_tier')!r} -> "
              f"{row['_expected_tier']!r} (score {row['match_score']})")
    if len(bad) > 8:
        print(f"    ... and {len(bad) - 8} more")

    if args.commit:
        for row in bad:
            db.table("jobs").update({"score_tier": row["_expected_tier"]}) \
                .eq("job_id", row["job_id"]).execute()

    print(f"\n[backfill] inspected {len(rows)} scored jobs, "
          f"{'updated' if args.commit else 'would update'} {len(bad)}")
    print(f"[backfill] tier corrections: {dict(moves)}")
    print(f"[backfill] mode: {'COMMIT' if args.commit else 'DRY-RUN (use --commit to write)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
