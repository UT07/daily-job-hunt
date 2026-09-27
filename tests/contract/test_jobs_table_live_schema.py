"""Database-shape contract tests against the LIVE Supabase schema.

Every check in tests/unit/test_authoritative_columns.py is static: it parses
app.py and JobWorkspace.jsx and proves they agree with each other about
which column name to use. That can't catch the other half of this bug
class — the column one of them agrees on no longer existing on the actual
table (a migration that never ran, a rename applied to staging but not
prod, a typo in a backfill script). This file asks the live database
directly, the same way `send_followup_reminders` querying a column that
had never existed silently 100%-failed for weeks: PostgREST would have
told anyone who asked.

Skips (never fails) when Supabase credentials are absent or the database is
unreachable, so this file cannot break CI for someone without access to the
live project — see `_live_db_or_skip`. When it CAN reach the database, a
genuinely missing column is a hard failure, not a skip.

Read-only. No writes, no deletes, no LLM calls.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = REPO_ROOT / "lambdas" / "pipeline"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from db_client import SupabaseClient, _missing_optional_column  # noqa: E402

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Skip-cleanly plumbing
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def live_db():
    try:
        db = SupabaseClient.from_env()
    except RuntimeError as e:
        pytest.skip(f"Supabase credentials not configured — skipping live schema checks: {e}")
    try:
        db.client.table("jobs").select("job_id").limit(1).execute()
    except Exception as e:  # noqa: BLE001 - any failure here means "can't verify", not "verified broken"
        pytest.skip(f"Supabase unreachable from this environment — skipping live schema checks: {e}")
    return db


# A live SELECT against an unknown column comes back from PostgREST/Postgres
# as `{'message': 'column jobs.foo does not exist', 'code': '42703', ...}` —
# confirmed by actually triggering it against production while building this
# file (an injected bogus column name, reverted after capturing the error).
# That shape is UNQUOTED table.column, code 42703, and is NOT one of the two
# patterns db_client.py's `_missing_optional_column` recognizes (both of
# which are quoted-identifier shapes seen from write paths) — so a SELECT-
# based check relying on that helper alone would silently SKIP a real
# missing column instead of failing it, exactly the false-negative this
# whole file exists to avoid. Postgres error code 42703 ("undefined_column")
# is checked first and is authoritative on its own; the message-regex
# fallbacks (including db_client.py's) cover clients/shapes that don't
# surface a `code` field.
_UNDEFINED_COLUMN_SQLSTATE = "42703"
_SELECT_MISSING_COLUMN_RE = re.compile(r"column\s+[\w.]+\.([A-Za-z_][A-Za-z0-9_]*)\s+does not exist")


def _missing_column_error(exc: Exception) -> str | None:
    """Return the missing column name if `exc` looks like ANY known
    "unknown column" shape (this file's own, freshly-confirmed-live one,
    plus db_client.py's two known write-path shapes), else None."""
    args = getattr(exc, "args", None)
    payload = args[0] if args else None
    if isinstance(payload, dict) and payload.get("code") == _UNDEFINED_COLUMN_SQLSTATE:
        match = _SELECT_MISSING_COLUMN_RE.search(str(payload.get("message", "")))
        if match:
            return match.group(1)
    text = str(exc)
    match = _SELECT_MISSING_COLUMN_RE.search(text)
    if match:
        return match.group(1)
    return _missing_optional_column(exc)


def _assert_columns_exist(db, table: str, columns: set[str], context: str) -> None:
    """Ask PostgREST to SELECT exactly `columns` from `table` (1 row).

    A named column that doesn't exist is detected via `_missing_column_error`
    above — treat that shape alone as a real, fail-worthy schema-contract
    break. Anything else (RLS, auth, a transient network blip) is "can't
    verify right now", not "verified broken", so it skips instead of failing.
    """
    col_list = ",".join(sorted(columns))
    try:
        db.client.table(table).select(col_list).limit(1).execute()
    except Exception as e:  # noqa: BLE001
        missing = _missing_column_error(e)
        if missing:
            pytest.fail(
                f"Column '{missing}' (referenced by {context}) does not "
                f"exist on the live '{table}' table. Queried columns: {col_list}. "
                f"Raw error: {e}"
            )
        pytest.skip(f"Could not verify columns {sorted(columns)} on '{table}' right now: {e}")


def _columns_referenced(table: str, files: list[Path]) -> set[str]:
    """Every column name in an explicit (non-'*') `.table(table).select("a, b")`
    call across `files`, found by regex rather than a hand-typed list.

    Deliberately scoped to `.select(...)` calls only — `.eq()/.gte()/...`
    filter column names are a real but separate risk (also 400s live on a
    renamed column) not covered here; scoping to `.select()` keeps the
    "which table does this belong to" attribution unambiguous, which
    matters more than maximum coverage for a test that must not have false
    positives dragging down its signal.
    """
    pattern = re.compile(
        r'\.table\(\s*["\']' + re.escape(table) + r'["\']\s*\)'
        r'(?:(?!\.execute\().)*?'
        r'\.select\(\s*["\']([^"\']*)["\']',
        re.DOTALL,
    )
    cols: set[str] = set()
    for f in files:
        text = f.read_text()
        for m in pattern.finditer(text):
            raw = m.group(1)
            if raw.strip() == "*":
                continue
            for part in raw.split(","):
                part = part.strip()
                if part and re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", part):
                    cols.add(part)
    return cols


def _code_files() -> list[Path]:
    return [REPO_ROOT / "app.py", REPO_ROOT / "db_client.py"] + sorted(PIPELINE_DIR.glob("*.py"))


# ---------------------------------------------------------------------------
# The exact ground truth from docs/ROADMAP.md's "Which columns are
# authoritative" table (re-verified 2026-09-26, pinned statically by
# tests/unit/test_authoritative_columns.py) — both the authoritative AND the
# legacy columns must still exist, because scripts/backfill_*.py and
# rescore tooling still read/write several of the legacy ones on purpose.
# ---------------------------------------------------------------------------

AUTHORITATIVE_JOBS_COLUMNS = {"match_score", "resume_s3_url", "cover_letter_s3_url"}
LEGACY_JOBS_COLUMNS = {
    "score_status", "score_version", "final_score", "scored_at",
    "tailored_pdf_path", "cover_letter_pdf_path", "resume_doc_url",
}


def test_authoritative_and_legacy_jobs_columns_exist_live(live_db):
    _assert_columns_exist(
        live_db, "jobs", AUTHORITATIVE_JOBS_COLUMNS | LEGACY_JOBS_COLUMNS,
        context="docs/ROADMAP.md's authoritative-columns table",
    )


def test_jobs_columns_referenced_by_explicit_selects_exist_live(live_db):
    cols = _columns_referenced("jobs", _code_files())
    assert cols, "No explicit (non-'*') SELECT column list found referencing 'jobs' — extraction regex may be broken"
    _assert_columns_exist(live_db, "jobs", cols, context="explicit SELECT column lists in app.py/db_client.py/lambdas/pipeline")


def test_jobs_raw_columns_referenced_by_explicit_selects_exist_live(live_db):
    cols = _columns_referenced("jobs_raw", _code_files())
    assert cols, "No explicit (non-'*') SELECT column list found referencing 'jobs_raw' — extraction regex may be broken"
    _assert_columns_exist(live_db, "jobs_raw", cols, context="explicit SELECT column lists in app.py/db_client.py/lambdas/pipeline")


# ---------------------------------------------------------------------------
# Pagination: PostgREST caps a single request at 1,000 rows. The `jobs`
# table has ~1,251 rows (ROADMAP.md, 2026-09-26) — comfortably over that
# cap — so a naive unpaginated fetch silently returns a truncated table.
# This test proves the paginated fetch actually gets everything by
# cross-checking it against PostgREST's own exact server-side count.
# ---------------------------------------------------------------------------

def _exact_count(db, table: str, apply_filters=None) -> int:
    q = db.client.table(table).select("job_id", count="exact")
    if apply_filters:
        q = apply_filters(q)
    result = q.limit(1).execute()
    return result.count if result.count is not None else len(result.data or [])


def _fetch_all_job_ids(db, page_size: int = 500) -> list[str]:
    ids: list[str] = []
    start = 0
    while True:
        batch = (
            db.client.table("jobs").select("job_id")
            .range(start, start + page_size - 1)
            .execute().data or []
        )
        ids.extend(row["job_id"] for row in batch)
        if len(batch) < page_size:
            break
        start += page_size
    return ids


def test_paginated_full_scan_matches_exact_server_side_count(live_db):
    total = _exact_count(live_db, "jobs")
    ids = _fetch_all_job_ids(live_db, page_size=500)

    assert len(ids) == total, (
        f"Paginated fetch (page_size=500) returned {len(ids)} rows but "
        f"PostgREST's exact count says {total} — pagination is dropping or "
        f"duplicating rows across pages."
    )
    assert len(set(ids)) == len(ids), "Paginated fetch returned duplicate job_id values across pages"
    assert total >= 1000, (
        f"Expected >= 1000 jobs (docs/ROADMAP.md pinned 1,251 on 2026-09-26, "
        f"and this project's own policy is to never destructively clear job "
        f"data — see memory feedback_no_destructive_data); got {total}. "
        "Either a lot of jobs were deleted, or this ran against the wrong project."
    )


# ---------------------------------------------------------------------------
# Comparative aggregate checks — the exact "which column is real" ground
# truth from docs/ROADMAP.md, expressed as ROBUST-TO-GROWTH comparisons
# (not the specific row counts from one day, which only ever go up as the
# live pipeline keeps running) so this doesn't flake as the table grows.
# ---------------------------------------------------------------------------

def test_match_score_is_populated_for_the_vast_majority_of_jobs(live_db):
    total = _exact_count(live_db, "jobs")
    scored = _exact_count(live_db, "jobs", lambda q: q.gt("match_score", 0))
    assert total > 0
    ratio = scored / total
    assert ratio >= 0.9, (
        f"Only {scored}/{total} ({ratio:.0%}) jobs have match_score > 0 — "
        "docs/ROADMAP.md documents match_score as authoritative and "
        "reliably populated (1,251/1,251 on 2026-09-26). If this is newly "
        "failing, either scoring broke or match_score stopped being the "
        "right column to trust."
    )


def test_resume_s3_url_is_the_live_artifact_column_not_tailored_pdf_path(live_db):
    """The exact bug shape this pins: someone reverting to (or a new code
    path accidentally writing) the legacy `tailored_pdf_path` as if it were
    still the artifact source of truth. ROADMAP.md: 921 rows on
    resume_s3_url vs 39 on tailored_pdf_path — expressed here as a
    comparison, not the literal counts, so it holds as both numbers grow."""
    with_resume_url = _exact_count(
        live_db, "jobs", lambda q: q.neq("resume_s3_url", None).neq("resume_s3_url", "")
    )
    with_legacy_pdf_path = _exact_count(
        live_db, "jobs", lambda q: q.neq("tailored_pdf_path", None).neq("tailored_pdf_path", "")
    )
    assert with_resume_url > 0, "No job has resume_s3_url populated — is it still the live artifact column?"
    assert with_legacy_pdf_path < with_resume_url, (
        f"tailored_pdf_path is populated on {with_legacy_pdf_path} rows vs "
        f"{with_resume_url} on resume_s3_url. docs/ROADMAP.md documents "
        f"tailored_pdf_path as a dead legacy column (local paths from "
        f"main.py dry-runs) — this inversion means either something started "
        f"writing it again, or resume_s3_url stopped being populated."
    )


def test_score_status_does_not_reliably_reflect_scoring(live_db):
    """Encodes the exact trap documented in docs/ROADMAP.md: score_status
    sits at its default 'pending' for the great majority of jobs that DO
    have a real match_score, because nothing in the live code path writes
    it. If this assertion ever fails, read it as good news to verify by
    hand, not a regression: it means something started keeping
    score_status in sync with match_score, and this test (plus the
    ROADMAP.md section it mirrors) should be updated to match the new
    reality — the alternative, silently leaving this test describing a bug
    that got fixed, is exactly the stale-doc failure mode ROADMAP.md itself
    was written to stop.
    """
    total = _exact_count(live_db, "jobs")
    scored = _exact_count(live_db, "jobs", lambda q: q.gt("match_score", 0))
    scored_but_pending = _exact_count(
        live_db, "jobs", lambda q: q.gt("match_score", 0).eq("score_status", "pending")
    )
    assert total > 0 and scored > 0
    ratio = scored_but_pending / scored
    assert ratio >= 0.5, (
        f"{scored_but_pending}/{scored} ({ratio:.0%}) fully-scored jobs "
        f"have score_status='pending' — expected the great majority to, per "
        f"docs/ROADMAP.md (1,132/1,243 on 2026-09-25). Re-verify by hand "
        "before treating this as broken."
    )
