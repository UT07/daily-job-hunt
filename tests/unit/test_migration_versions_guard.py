"""Guard against new migration version collisions.

Supabase CLI derives a migration's version from ALL leading digits of its filename.
Two migrations with identical leading digits -> primary key collision in schema_migrations.

Example collisions (pre-existing, already in remote database):
- 20260409_add_posted_date.sql and 20260409_fix_job_id_type.sql both extract to version "20260409"
- 20260430_add_failure_reason.sql, 20260430_add_posted_date_jobs_raw.sql,
  20260430_resume_versions_unique.sql all extract to version "20260430"

Prevention: New migrations MUST use unique leading-digit sequences. The recommended form is
YYYYMMDDHHMMSS (14 digits from `date +%Y%m%d%H%M%S`) as used in 20260401200000_scrape_runs.sql.

Grandfathered collisions:
- 20260409: Already recorded in remote schema_migrations; new files with this prefix are blocked.
- 20260430: Already recorded in remote schema_migrations; new files with this prefix are blocked.
"""
import pathlib
from collections import Counter


MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "supabase/migrations"

# Pre-existing collisions already recorded in the remote database.
# Supabase will reject any attempt to apply a new migration with the same version (prefix).
GRANDFATHERED_COLLISIONS = {
    "20260409",  # 2 files; pre-existing
    "20260430",  # 3 files; pre-existing
}


def test_no_new_migration_version_collisions():
    """Fail if any two migration files have identical leading-digit sequences,
    EXCEPT for grandfathered pre-existing collisions."""
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

    # Extract leading digits from each filename.
    # "20260922000000_pgvector.sql" -> "20260922000000"
    # "20260409_add_posted_date.sql" -> "20260409"
    versions = []
    for f in migration_files:
        name = f.name
        # Extract all leading digits
        leading_digits = ""
        for char in name:
            if char.isdigit():
                leading_digits += char
            else:
                break
        if leading_digits:
            versions.append(leading_digits)

    # Count occurrences of each version.
    version_counts = Counter(versions)

    # Find collisions (count > 1).
    collisions = {v: count for v, count in version_counts.items() if count > 1}

    # Remove grandfathered collisions.
    new_collisions = {
        v: count
        for v, count in collisions.items()
        if v not in GRANDFATHERED_COLLISIONS
    }

    assert not new_collisions, (
        f"New migration version collisions detected: {new_collisions}. "
        f"Use unique leading-digit sequences, e.g., YYYYMMDDHHMMSS."
    )
