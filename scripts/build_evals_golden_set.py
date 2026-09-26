#!/usr/bin/env python3
"""Build evals/golden/*.json — the stratified golden set for evals/harness.py.

This is a data-prep script, not part of the `evals` package itself: it is
run by hand (or re-run when production data shifts) to (re)generate the
fixture files that `evals.load_golden()` reads. It is NOT imported by any
Lambda and is not part of any deploy path.

Ground truth this corrects relative to the original plan (Task 23, Step 3;
see docs/superpowers/plans/2026-09-22-ey-genai-platform-upgrade.md and
.superpowers/sdd/2026-09-22-ey-genai-platform-upgrade/task-23-25-report.md):

1. Stratifies on `match_score` / `score_tier`, NOT `final_score`. Nothing
   reliably writes `final_score` or `score_status` in production (they are
   dead columns from an earlier scoring scheme) — the live signal the
   dashboard renders and the API sorts on is `match_score`, alongside
   `ats_score` / `hiring_manager_score` / `tech_recruiter_score`. Tier is
   RE-DERIVED from `match_score` via score_batch.score_to_tier() for every
   row rather than trusted from the stored `score_tier` column, because
   `score_tier` was only added by a later migration (20260405_add_score_tier)
   and older rows can have match_score set with score_tier still NULL.
2. Paginates. A single PostgREST request is capped at 1000 rows regardless
   of `.limit()` server-side config — this repo has been bitten by that four
   times already (see task report). `_paginate` below pages with `.range()`
   until a page comes back short.
3. Reports real row totals, including the server-computed exact `count`
   (via `count="exact"`, which PostgREST returns from Content-Range
   independent of how many rows the query actually returns), and the
   post-pagination row count actually fetched, so a silent 1000-row
   truncation would show up as those two numbers disagreeing.
4. D-tier (score < 60) is structurally rare-to-absent in `jobs`: the
   production score_batch.handler() only inserts a `jobs` row when
   `match_score >= min_score` (default 60) — sub-60 scores are filtered
   before a row is ever written. Rows that later got RE-scored downward by
   scripts/rescore_batch.py are the only route to a D-tier row existing at
   all. This script reports whatever the live data actually supports per
   tier instead of assuming 5-per-tier is achievable, per the eval task's
   explicit instruction not to fake stratification.

Reads SUPABASE_URL / SUPABASE_SERVICE_KEY from `.env` via db_client.py —
the same direct-env pattern scripts/rescore_batch.py and
scripts/backfill_tailoring.py already use for one-off local DB access. This
is read-only: the only queries below are SELECTs.

Usage:
  python scripts/build_evals_golden_set.py               # write fixtures + manifest
  python scripts/build_evals_golden_set.py --dry-run      # report distribution only, write nothing
  python scripts/build_evals_golden_set.py --per-tier 5   # override the per-tier target (default 5)
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Load .env the same way scripts/rescore_batch.py does — this is a local
# one-off script, not a Lambda, so it uses the direct env-var Supabase
# client (db_client.py) rather than ai_helper.get_supabase()'s SSM lookup.
_env_path = REPO_ROOT / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _value = _line.partition("=")
        os.environ.setdefault(_key.strip(), _value.strip().strip("\"'"))

sys.path.insert(0, str(REPO_ROOT / "lambdas" / "pipeline"))

from db_client import SupabaseClient  # noqa: E402
from score_batch import score_to_tier  # noqa: E402  (flat import — needs lambdas/pipeline on sys.path)

USER_ID = os.environ.get("EVAL_USER_ID", "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39")
GOLDEN_DIR = REPO_ROOT / "evals" / "golden"
MANIFEST_PATH = GOLDEN_DIR / "manifest.json"
TIERS = ["S", "A", "B", "C", "D"]
PAGE_SIZE = 1000  # PostgREST's hard per-request row cap.
JOB_COLUMNS = (
    "job_hash, title, company, description, match_score, "
    "ats_score, hiring_manager_score, tech_recruiter_score, score_tier"
)


def _paginate(db, description_min_chars: int) -> tuple[list[dict], int]:
    """Fetch every `jobs` row for USER_ID with a non-null match_score,
    paging past PostgREST's 1000-row cap. Returns (rows, server_exact_count).
    """
    exact_count = (
        db.table("jobs")
        .select("job_hash", count="exact")
        .eq("user_id", USER_ID)
        .not_.is_("match_score", "null")
        .execute()
        .count
    )

    rows: list[dict] = []
    offset = 0
    while True:
        page = (
            db.table("jobs")
            .select(JOB_COLUMNS)
            .eq("user_id", USER_ID)
            .not_.is_("match_score", "null")
            .order("job_hash")  # stable order across pages
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return rows, exact_count


def _get_base_resume(db) -> str:
    result = (
        db.table("user_resumes")
        .select("tex_content")
        .eq("user_id", USER_ID)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    tex = (result.data[0].get("tex_content") if result.data else None) or ""
    if not tex:
        raise RuntimeError(
            f"No base resume (user_resumes.tex_content) found for user_id={USER_ID}. "
            "Refusing to build a golden set without one."
        )
    return tex


def _pick_tailor_keywords(description: str, n: int = 2) -> list[str]:
    """Pick up to n tech keywords that genuinely appear (case-insensitively)
    in this JD, so the fixture's must_contain list is actually testable —
    per Task 23's own warning: "a keyword the job never mentions makes the
    fixture untestable."
    """
    candidates = [
        "Python", "AWS", "React", "TypeScript", "JavaScript", "Node",
        "Docker", "Kubernetes", "SQL", "PostgreSQL", "REST", "API",
        "CI/CD", "Terraform", "GCP", "Azure", "Java", "Go", "microservices",
        "Linux", "Git",
    ]
    desc_lower = description.lower()
    found = [kw for kw in candidates if kw.lower() in desc_lower]
    return found[:n]


def main(dry_run: bool, per_tier: int, description_min_chars: int, target_total: int, tailor_count: int) -> None:
    db = SupabaseClient.from_env().client

    print(f"Fetching base resume for user_id={USER_ID}...")
    base_resume = _get_base_resume(db)
    print(f"  resume: {len(base_resume)} chars")

    print(f"Paginating jobs table for user_id={USER_ID} (page size {PAGE_SIZE})...")
    rows, exact_count = _paginate(db, description_min_chars)
    print(f"  server-reported exact count (match_score not null): {exact_count}")
    print(f"  rows actually fetched after pagination:              {len(rows)}")
    if exact_count is not None and len(rows) != exact_count:
        print(
            f"  WARNING: fetched count != server exact count "
            f"({len(rows)} != {exact_count}) — pagination may be incomplete."
        )

    long_enough = [
        r for r in rows
        if len(r.get("description") or "") >= description_min_chars and r.get("job_hash")
    ]
    no_hash = sum(1 for r in rows if not r.get("job_hash"))
    print(
        f"  rows with description >= {description_min_chars} chars and a job_hash: "
        f"{len(long_enough)}/{len(rows)}"
        + (f"  ({no_hash} excluded for missing job_hash)" if no_hash else "")
    )

    buckets: dict[str, list[dict]] = {t: [] for t in TIERS}
    tier_mismatches = 0
    for r in long_enough:
        derived_tier = score_to_tier(r["match_score"])
        stored_tier = r.get("score_tier")
        if stored_tier and stored_tier != derived_tier:
            tier_mismatches += 1
        buckets[derived_tier].append(r)

    for tier in TIERS:
        buckets[tier].sort(key=lambda r: (-(r["match_score"] or 0), r["job_hash"]))

    print("\nFull-population tier distribution (description-filtered, all eligible rows):")
    for tier in TIERS:
        print(f"  {tier}: {len(buckets[tier])}")
    if tier_mismatches:
        print(
            f"  NOTE: {tier_mismatches} rows' stored score_tier column disagreed with "
            f"score_to_tier(match_score) — used the derived value, per ground truth "
            f"that score_tier predates a migration and can be stale."
        )

    selected: dict[str, list[dict]] = {t: buckets[t][:per_tier] for t in TIERS}
    shortfalls: dict[str, int] = {
        t: per_tier - len(selected[t]) for t in TIERS if len(selected[t]) < per_tier
    }
    topped_up: dict[str, int] = {t: 0 for t in TIERS}

    # A tier with fewer than `per_tier` real examples (D, structurally,
    # per this script's docstring) is reported honestly via `shortfalls`
    # above rather than padded — but rather than just shipping fewer than
    # target_total fixtures overall, top up the OTHER tiers (which do have
    # surplus) round-robin until target_total is reached or every tier is
    # exhausted. This keeps the set at the size the verbatim spec test
    # (test_golden_set_has_twenty_five_fixtures) expects while keeping the
    # spread honest: no tier is invented, and the manifest records exactly
    # how many of each tier's fixtures are "top-up" picks beyond per_tier.
    total_selected = sum(len(v) for v in selected.values())
    round_robin = [t for t in TIERS if t not in shortfalls] or list(TIERS)
    i = 0
    while total_selected < target_total and round_robin:
        tier = round_robin[i % len(round_robin)]
        next_idx = per_tier + topped_up[tier]
        if next_idx < len(buckets[tier]):
            selected[tier].append(buckets[tier][next_idx])
            topped_up[tier] += 1
            total_selected += 1
        else:
            round_robin.remove(tier)
            if not round_robin:
                break
            continue
        i += 1

    print(f"\nSelected {total_selected}/{target_total} requested (base target {per_tier}/tier x {len(TIERS)} tiers):")
    for tier in TIERS:
        bits = []
        if tier in shortfalls:
            bits.append(f"short by {shortfalls[tier]} vs base target")
        if topped_up[tier]:
            bits.append(f"+{topped_up[tier]} topped up from surplus")
        note = f"  ({'; '.join(bits)})" if bits else ""
        print(f"  {tier}: {len(selected[tier])}{note}")
    if total_selected < target_total:
        print(
            f"  NOTE: live data (after filters) cannot support {target_total} fixtures at all "
            f"— only {total_selected} distinct eligible jobs exist across every tier combined."
        )

    if dry_run:
        print("\n[DRY RUN] no files written.")
        return

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for existing in GOLDEN_DIR.glob("*.json"):
        if existing.name != "manifest.json":
            existing.unlink()

    written = []
    tailor_ids: list[str] = []
    # Convert up to `tailor_count` fixtures to task="tailor", ROUND-ROBIN
    # across tiers (one per tier per pass) rather than tier-by-tier, so the
    # conversion doesn't gut one tier's score-task sample size. Taking them
    # in strict tier order (all from S first) would have left S with only 1
    # remaining score-task fixture while A/B/C kept 6 each — skewing the
    # tier_accuracy metric's statistical power away from exactly the tiers
    # (S, D) where it matters most. Only convert a case whose JD actually
    # contains >=1 extractable keyword — an untestable fixture is worse
    # than one fewer tailor case.
    max_len = max(len(selected[t]) for t in TIERS)
    for idx in range(max_len):
        if len(tailor_ids) >= tailor_count:
            break
        for tier in TIERS:
            if len(tailor_ids) >= tailor_count:
                break
            if idx >= len(selected[tier]):
                continue
            # Never convert a tier's only (or last remaining) example to a
            # tailor fixture — that would zero out tier_accuracy's coverage
            # of that band entirely (this is exactly what happens to D,
            # which has a single eligible row in the whole population).
            # Score-task tier coverage takes priority over tailor-task
            # count; tailor cases are pulled only from tiers with spares.
            if len(selected[tier]) <= 1:
                continue
            r = selected[tier][idx]
            keywords = _pick_tailor_keywords(r.get("description") or "")
            if keywords:
                tailor_ids.append(r["job_hash"])
                r["_tailor_keywords"] = keywords

    for tier in TIERS:
        for r in selected[tier]:
            case_id = r["job_hash"][:12]
            is_tailor = r["job_hash"] in tailor_ids
            case = {
                "id": case_id,
                "task": "tailor" if is_tailor else "score",
                "title": r["title"],
                "company": r["company"],
                "description": r["description"],
            }
            if is_tailor:
                case["expected"] = {
                    "must_contain": r["_tailor_keywords"],
                    "must_pass_guards": True,
                    "no_fabrication": True,
                }
            else:
                case["expected"] = {"tier": tier}
            (GOLDEN_DIR / f"{case_id}.json").write_text(json.dumps(case, indent=2) + "\n")
            written.append({"id": case_id, "tier": tier, "task": case["task"]})

    manifest = {
        "_comment": (
            "Provenance for evals/golden/*.json. Regenerate with "
            "scripts/build_evals_golden_set.py. See that script's docstring "
            "for why this stratifies on match_score/score_tier (not "
            "final_score) and why D-tier is structurally sparse."
        ),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "user_id": USER_ID,
        "source_table": "jobs",
        "description_min_chars": description_min_chars,
        "per_tier_target": per_tier,
        "target_total": target_total,
        "server_exact_count_match_score_not_null": exact_count,
        "rows_fetched_after_pagination": len(rows),
        "rows_after_description_filter": len(long_enough),
        "full_population_tier_distribution": {t: len(buckets[t]) for t in TIERS},
        "selected_tier_distribution": {t: len(selected[t]) for t in TIERS},
        "shortfalls_vs_base_per_tier_target": shortfalls,
        "topped_up_from_surplus_tiers": {t: n for t, n in topped_up.items() if n},
        "total_fixtures_written": len(written),
        "tailor_fixture_ids": sorted(tailor_ids),
        "fixtures": written,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\nWrote {len(written)} fixtures + manifest.json to {GOLDEN_DIR}")
    if shortfalls:
        print(
            "NOTE: live data could not fill every tier to the target — see "
            "shortfalls in manifest.json. This is reported, not hidden."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--per-tier", type=int, default=5)
    parser.add_argument("--description-min-chars", type=int, default=300)
    parser.add_argument("--target-total", type=int, default=25)
    parser.add_argument("--tailor-count", type=int, default=5)
    args = parser.parse_args()
    main(args.dry_run, args.per_tier, args.description_min_chars, args.target_total, args.tailor_count)
