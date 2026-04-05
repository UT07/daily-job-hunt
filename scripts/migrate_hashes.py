#!/usr/bin/env python3
"""One-time: recompute canonical hashes for all existing jobs and jobs_raw records.

The canonical hash (utils/canonical_hash.py) uses a different formula than the
old hashes.  Existing Supabase records have old (or missing) canonical_hash
values.  This script backfills them so cross-run dedup works correctly for
historical jobs.

Usage:
    python scripts/migrate_hashes.py               # Migrate both tables
    python scripts/migrate_hashes.py --dry-run      # Show counts without writing
    python scripts/migrate_hashes.py --table jobs   # Only migrate jobs table
    python scripts/migrate_hashes.py --table raw    # Only migrate jobs_raw table

Requires:
    SUPABASE_URL, SUPABASE_SERVICE_KEY in .env or environment.
"""
import argparse
import os
import sys
from pathlib import Path

# ── Bootstrap: project root on sys.path + load .env ────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

env_path = ROOT / ".env"
if env_path.exists():
    for line in open(env_path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            val = val.strip().strip('"').strip("'")
            if key.strip() and val:
                os.environ.setdefault(key.strip(), val)

from utils.canonical_hash import canonical_hash
from db_client import SupabaseClient


# Supabase PostgREST limits rows per request; fetch in pages.
PAGE_SIZE = 1000


def _fetch_all(table_query):
    """Paginate through a Supabase table, returning all rows."""
    rows = []
    offset = 0
    while True:
        batch = table_query.range(offset, offset + PAGE_SIZE - 1).execute()
        rows.extend(batch.data)
        if len(batch.data) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def migrate_jobs(db: SupabaseClient, dry_run: bool = False) -> int:
    """Recompute canonical_hash for every row in the jobs table."""
    print("Fetching jobs...")
    rows = _fetch_all(
        db.client.table("jobs").select("job_id, company, title, description")
    )
    print(f"  Found {len(rows)} jobs")

    if dry_run:
        print("  [DRY RUN] Would update all rows — skipping writes.")
        return len(rows)

    updated = 0
    errors = 0
    for row in rows:
        new_hash = canonical_hash(
            row.get("company", ""),
            row.get("title", ""),
            row.get("description", ""),
        )
        try:
            db.client.table("jobs").update(
                {"canonical_hash": new_hash}
            ).eq("job_id", row["job_id"]).execute()
            updated += 1
        except Exception as exc:
            errors += 1
            print(f"  Error updating job {row['job_id']}: {exc}")

    print(f"  Updated {updated} jobs ({errors} errors)")
    return updated


def migrate_jobs_raw(db: SupabaseClient, dry_run: bool = False) -> int:
    """Recompute canonical_hash for every row in the jobs_raw table."""
    print("Fetching jobs_raw...")
    rows = _fetch_all(
        db.client.table("jobs_raw").select("job_hash, company, title, description")
    )
    print(f"  Found {len(rows)} raw jobs")

    if dry_run:
        print("  [DRY RUN] Would update all rows — skipping writes.")
        return len(rows)

    updated = 0
    errors = 0
    for row in rows:
        new_hash = canonical_hash(
            row.get("company", ""),
            row.get("title", ""),
            row.get("description", ""),
        )
        try:
            db.client.table("jobs_raw").update(
                {"canonical_hash": new_hash}
            ).eq("job_hash", row["job_hash"]).execute()
            updated += 1
        except Exception as exc:
            errors += 1
            print(f"  Error updating raw job {row['job_hash']}: {exc}")

    print(f"  Updated {updated} raw jobs ({errors} errors)")
    return updated


def migrate(table: str = "both", dry_run: bool = False):
    """Run the hash migration."""
    db = SupabaseClient.from_env()

    total = 0
    if table in ("both", "jobs"):
        total += migrate_jobs(db, dry_run=dry_run)
    if table in ("both", "raw"):
        total += migrate_jobs_raw(db, dry_run=dry_run)

    print(f"\nMigration complete — {total} records processed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Recompute canonical hashes for existing Supabase records"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without writing to Supabase",
    )
    parser.add_argument(
        "--table", choices=["both", "jobs", "raw"], default="both",
        help="Which table(s) to migrate (default: both)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("NaukriBaba — Canonical Hash Migration")
    print(f"  Table:   {args.table}")
    print(f"  Dry run: {args.dry_run}")
    print("=" * 60)

    migrate(table=args.table, dry_run=args.dry_run)
