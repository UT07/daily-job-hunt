"""Unit tests for the send_followup_reminders Lambda.

Regression coverage for the production bug confirmed via CloudWatch
(naukribaba-followup-reminders, Aug 12 - Sep 1 2026, repeated on every
scheduled run): the handler selected `jobs.updated_at`, a column that has
never existed on `jobs` (see
supabase/migrations/00000000000000_initial_schema.sql — jobs has
`first_seen`/`last_seen`, never `updated_at`), causing PostgREST to reject
every request with `column jobs.updated_at does not exist` (42703).

The fix does not just swap in `jobs.last_seen` (which tracks when a
*scraper* last re-found the posting, not when the *user*'s application
status last changed — using it would nudge on a job applied to
yesterday but not rescraped since, and stay silent on a genuinely stale
application for a posting that happens to still be listed). Instead it
reads `application_timeline`, the event log app.py's
`add_timeline_event` writes on every status change (see app.py's
`add_timeline_event`, which inserts a timeline row *and* syncs
`jobs.application_status` in the same request), falling back to
`first_seen` only for jobs marked Applied before any such event exists.
"""
import pathlib
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
SEND_FOLLOWUP_SRC = REPO_ROOT / "lambdas" / "pipeline" / "send_followup_reminders.py"

_CONSTRAINT_KEYWORDS = {"constraint", "primary", "foreign", "unique", "check", "exclude"}


def _find_matching_paren(text: str, open_idx: int) -> int:
    """Return the index of the ')' that closes the '(' at open_idx."""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError(f"Unbalanced parens starting at {open_idx}")


def _split_top_level(body: str) -> list[str]:
    """Split a column-def list on commas that aren't nested inside parens."""
    parts, current, depth = [], [], 0
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def parse_schema_columns() -> dict[str, set[str]]:
    """Derive {table_name: {column, ...}} straight from supabase/migrations/*.sql.

    Applies each migration's CREATE TABLE / ADD COLUMN / DROP COLUMN
    statements in filename order (this repo's filenames sort
    chronologically — numeric date/sequence prefixes) so the result
    reflects the schema as of the latest migration, i.e. the same thing
    PostgREST enforces live. This is deliberately a small, repo-specific
    parser (not a general SQL parser) covering only the statement shapes
    these migrations actually use — the alternative would be hand-keeping
    a column list here that could drift from the schema exactly the way
    the buggy `jobs.updated_at` query did.
    """
    tables: dict[str, set[str]] = {}
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        sql = path.read_text()

        for m in re.finditer(
            r'create\s+table\s+if\s+not\s+exists\s+(?:"?public"?\.)?"?(\w+)"?\s*\(',
            sql,
            re.IGNORECASE,
        ):
            table = m.group(1)
            close = _find_matching_paren(sql, m.end() - 1)
            body = sql[m.end() : close]
            cols = tables.setdefault(table, set())
            for frag in _split_top_level(body):
                frag = frag.strip()
                if not frag:
                    continue
                first_word = frag.split()[0].strip('"').lower()
                if first_word in _CONSTRAINT_KEYWORDS:
                    continue
                col_match = re.match(r'"?(\w+)"?', frag)
                if col_match:
                    cols.add(col_match.group(1))

        for m in re.finditer(
            r'alter\s+table\s+(?:only\s+)?(?:"?public"?\.)?"?(\w+)"?\s+(.*?);',
            sql,
            re.IGNORECASE | re.DOTALL,
        ):
            table = m.group(1)
            clause = m.group(2)
            cols = tables.setdefault(table, set())
            for add_m in re.finditer(
                r'add\s+column\s+(?:if\s+not\s+exists\s+)?"?(\w+)"?', clause, re.IGNORECASE
            ):
                cols.add(add_m.group(1))
            for drop_m in re.finditer(
                r'drop\s+column\s+(?:if\s+exists\s+)?"?(\w+)"?', clause, re.IGNORECASE
            ):
                cols.discard(drop_m.group(1))

    return tables


def extract_supabase_column_refs(source_path: pathlib.Path) -> list[tuple[str, str]]:
    """Return [(table, column), ...] referenced by `.table("X")...` chains.

    Looks inside each `db.table("X")` ... `.execute()` chain for column
    names passed to `.select(...)` (a comma-separated string) and to the
    single-column filter/order methods this file (and its likely
    variations) would use.
    """
    src = source_path.read_text()
    refs: list[tuple[str, str]] = []
    for m in re.finditer(r'\.table\(\s*["\'](\w+)["\']\s*\)', src):
        table = m.group(1)
        start = m.end()
        end = src.find(".execute(", start)
        if end == -1:
            end = len(src)
        chain = src[start:end]

        for sel_m in re.finditer(r'\.select\(\s*["\']([^"\']*)["\']', chain):
            for col in sel_m.group(1).split(","):
                col = col.strip()
                if col:
                    refs.append((table, col))

        for filt_m in re.finditer(
            r"\.(?:eq|lt|gt|gte|lte|order|in_)\(\s*[\"'](\w+)[\"']", chain
        ):
            refs.append((table, filt_m.group(1)))

    return refs


def test_schema_parser_finds_the_real_tables():
    """Sanity check on the parser itself, independent of send_followup_reminders.py."""
    schema = parse_schema_columns()
    assert "jobs" in schema
    assert "application_timeline" in schema
    # jobs has ~35 columns as of the pgvector migration; a low count here
    # would mean the CREATE TABLE parse silently broke.
    assert len(schema["jobs"]) > 20
    assert "updated_at" not in schema["jobs"], (
        "jobs has never had an updated_at column — if this now fails, either "
        "a migration legitimately added one (update send_followup_reminders.py's "
        "assumptions) or the parser is over-matching."
    )
    assert {"job_id", "user_id", "status", "created_at"} <= schema["application_timeline"]


def test_queries_only_reference_columns_that_exist_in_the_live_schema():
    """The actual regression test: every column send_followup_reminders.py
    queries must exist on the table it queries, per supabase/migrations/.

    Fails red against the pre-fix source (which selected/filtered/ordered
    on `jobs.updated_at`) and green after it, without hardcoding jobs'
    column list anywhere in this file.
    """
    schema = parse_schema_columns()
    refs = extract_supabase_column_refs(SEND_FOLLOWUP_SRC)
    assert refs, "expected to find Supabase column references to check"

    missing = [
        (table, col) for table, col in refs if table in schema and col not in schema[table]
    ]
    assert missing == [], (
        f"send_followup_reminders.py references column(s) not in the live schema "
        f"(per supabase/migrations/): {missing}"
    )


def _make_mock_db(applied_jobs=None, timeline_rows=None):
    """Mock Supabase client that routes by table name, like the pattern
    used in tests/unit/test_self_improve_lambda.py."""
    db = MagicMock()
    tables: dict[str, MagicMock] = {}

    def table_router(name):
        return tables.setdefault(name, MagicMock())

    db.table.side_effect = table_router

    jobs_chain = tables.setdefault("jobs", MagicMock())
    jobs_result = MagicMock(data=applied_jobs if applied_jobs is not None else [])
    jobs_chain.select.return_value = jobs_chain
    jobs_chain.eq.return_value = jobs_chain
    jobs_chain.execute.return_value = jobs_result

    timeline_chain = tables.setdefault("application_timeline", MagicMock())
    timeline_result = MagicMock(data=timeline_rows if timeline_rows is not None else [])
    timeline_chain.select.return_value = timeline_chain
    timeline_chain.eq.return_value = timeline_chain
    timeline_chain.in_.return_value = timeline_chain
    timeline_chain.order.return_value = timeline_chain
    timeline_chain.execute.return_value = timeline_result

    return db


def _iso_days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


APPLIED_JOB = {
    "job_id": "job-1",
    "title": "Backend Engineer",
    "company": "Acme",
    "match_score": 82,
    "apply_url": "https://example.com/jobs/1",
    "first_seen": _iso_days_ago(20),
}


class TestFetchFollowupJobs:
    """Tests for the query-building/selection logic, independent of email sending."""

    def test_no_applied_jobs_short_circuits(self):
        from send_followup_reminders import _fetch_followup_jobs

        db = _make_mock_db(applied_jobs=[])
        cutoff = _iso_days_ago(7)
        result = _fetch_followup_jobs(db, "default", cutoff)
        assert result == []
        # Should not even query application_timeline when there's nothing applied.
        db.table.assert_any_call("jobs")

    def test_stale_application_is_included(self):
        from send_followup_reminders import _fetch_followup_jobs

        applied_at = _iso_days_ago(10)
        db = _make_mock_db(
            applied_jobs=[APPLIED_JOB],
            timeline_rows=[{"job_id": "job-1", "created_at": applied_at}],
        )
        cutoff = _iso_days_ago(7)
        result = _fetch_followup_jobs(db, "default", cutoff)
        assert len(result) == 1
        assert result[0]["job_id"] == "job-1"
        assert result[0]["_applied_at"] == applied_at

    def test_recent_application_is_excluded(self):
        from send_followup_reminders import _fetch_followup_jobs

        db = _make_mock_db(
            applied_jobs=[APPLIED_JOB],
            timeline_rows=[{"job_id": "job-1", "created_at": _iso_days_ago(2)}],
        )
        cutoff = _iso_days_ago(7)
        result = _fetch_followup_jobs(db, "default", cutoff)
        assert result == []

    def test_falls_back_to_first_seen_without_timeline_row(self):
        """A job marked Applied with no application_timeline event (e.g.
        predates that table, or was set via a path that didn't log one)
        still gets a usable timestamp instead of being silently dropped."""
        from send_followup_reminders import _fetch_followup_jobs

        old_job = dict(APPLIED_JOB, job_id="job-2", first_seen=_iso_days_ago(15))
        db = _make_mock_db(applied_jobs=[old_job], timeline_rows=[])
        cutoff = _iso_days_ago(7)
        result = _fetch_followup_jobs(db, "default", cutoff)
        assert len(result) == 1
        assert result[0]["_applied_at"] == old_job["first_seen"]

    def test_uses_most_recent_applied_event_when_multiple_exist(self):
        """A job re-applied to (Applied -> Withdrawn -> Applied again) must
        use the latest Applied timestamp, not the oldest."""
        from send_followup_reminders import _fetch_followup_jobs

        db = _make_mock_db(
            applied_jobs=[APPLIED_JOB],
            timeline_rows=[
                # order(desc=True) means the mock should return newest first,
                # matching what a real ORDER BY created_at DESC would give.
                {"job_id": "job-1", "created_at": _iso_days_ago(3)},
                {"job_id": "job-1", "created_at": _iso_days_ago(30)},
            ],
        )
        cutoff = _iso_days_ago(7)
        result = _fetch_followup_jobs(db, "default", cutoff)
        # Most recent Applied event (3 days ago) is within the 7-day
        # cutoff, so this job should NOT be flagged yet.
        assert result == []

    def test_results_sorted_oldest_first_and_capped(self):
        from send_followup_reminders import _fetch_followup_jobs

        jobs = [dict(APPLIED_JOB, job_id=f"job-{i}") for i in range(3)]
        timeline = [
            {"job_id": "job-0", "created_at": _iso_days_ago(8)},
            {"job_id": "job-1", "created_at": _iso_days_ago(20)},
            {"job_id": "job-2", "created_at": _iso_days_ago(10)},
        ]
        db = _make_mock_db(applied_jobs=jobs, timeline_rows=timeline)
        cutoff = _iso_days_ago(7)
        result = _fetch_followup_jobs(db, "default", cutoff, limit=10)
        assert [j["job_id"] for j in result] == ["job-1", "job-2", "job-0"]


class TestHandler:
    """End-to-end handler tests with SMTP mocked out."""

    @patch("send_followup_reminders.smtplib.SMTP_SSL")
    @patch("send_followup_reminders.get_param")
    @patch("send_followup_reminders.get_supabase")
    def test_sends_email_for_stale_application(self, mock_get_supa, mock_get_param, mock_smtp):
        import send_followup_reminders

        mock_get_supa.return_value = _make_mock_db(
            applied_jobs=[APPLIED_JOB],
            timeline_rows=[{"job_id": "job-1", "created_at": _iso_days_ago(10)}],
        )
        mock_get_param.side_effect = lambda name: {
            "/naukribaba/GMAIL_USER": "test@gmail.com",
            "/naukribaba/GMAIL_APP_PASSWORD": "app-password",
        }[name]

        result = send_followup_reminders.handler({"user_id": "default"}, None)

        assert result == {"sent": True, "count": 1}
        mock_smtp.return_value.__enter__.return_value.send_message.assert_called_once()

    @patch("send_followup_reminders.get_supabase")
    def test_no_reminders_needed_skips_email(self, mock_get_supa):
        import send_followup_reminders

        mock_get_supa.return_value = _make_mock_db(applied_jobs=[])

        result = send_followup_reminders.handler({"user_id": "default"}, None)

        assert result == {"sent": False, "count": 0}
