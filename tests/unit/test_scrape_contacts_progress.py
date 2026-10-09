"""scrape_contacts must move on from jobs it has already searched.

It selected S/A jobs with linkedin_contacts IS NULL, ordered by score, limit
15, and wrote only when contacts were found. A job with no findable contacts
stayed NULL, so the next run selected the same top 15 again, searched them
again through the paid proxy, found nothing again — forever. Jobs ranked
16th and below were never reached.

Now every job whose searches actually ran is stamped contacts_attempted_at,
and the select skips jobs stamped within the retry window. A job whose
searches all FAILED (proxy down) is not stamped, so an outage does not
push real work back a week.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, "lambdas/scrapers")

import scrape_contacts  # noqa: E402


class FakeQuery:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.payload = db, table, [], None

    def select(self, *a, **k):
        return self

    def update(self, payload):
        self.payload = payload
        return self

    def __getattr__(self, name):  # eq, in_, is_, or_, order, limit
        def f(*args, **kwargs):
            self.filters.append((name, args))
            return self
        return f

    def execute(self):
        if self.payload is not None:
            self.db.updates.append((self.payload, list(self.filters)))
            return MagicMock(data=[])
        self.db.selects.append(list(self.filters))
        if self.db.select_error and any(n == "or_" for n, _ in self.filters):
            raise self.db.select_error
        return MagicMock(data=self.db.jobs)


class FakeDB:
    def __init__(self, jobs, select_error=None):
        self.jobs, self.select_error = jobs, select_error
        self.selects, self.updates = [], []

    def table(self, name):
        return FakeQuery(self, name)


JOBS = [{"job_id": "j1", "title": "SRE", "company": "Acme", "location": "Dublin, Ireland", "score_tier": "S"},
        {"job_id": "j2", "title": "SRE", "company": "Beta", "location": "Cork", "score_tier": "A"}]

PROFILE_HTML = '<a href="https://ie.linkedin.com/in/jane-doe-a1b2c3">x</a>'


def _resp(status, text=""):
    r = MagicMock()
    r.status_code, r.text = status, text
    return r


def _run(db, responder):
    with patch.object(scrape_contacts, "get_supabase", return_value=db), \
         patch.object(scrape_contacts, "get_param", return_value="proxy"), \
         patch.object(scrape_contacts.httpx, "get", side_effect=lambda *a, **k: responder()):
        return scrape_contacts.handler({"user_id": "u1"}, None)


def _updates_for(db, job_id):
    return [p for p, f in db.updates if ("eq", ("job_id", job_id)) in f]


def test_a_job_with_no_contacts_is_stamped_so_the_next_run_moves_on():
    db = FakeDB(JOBS)
    out = _run(db, lambda: _resp(200, "<html>no profiles</html>"))
    for jid in ("j1", "j2"):
        ups = _updates_for(db, jid)
        assert len(ups) == 1, (jid, db.updates)
        assert "contacts_attempted_at" in ups[0]
        assert "linkedin_contacts" not in ups[0], "an empty search must not fake a result"
    assert out["count"] == 0 and out["attempted"] == 2


def test_a_job_with_contacts_gets_both_the_contacts_and_the_stamp():
    db = FakeDB(JOBS[:1])
    _run(db, lambda: _resp(200, PROFILE_HTML))
    (up,) = _updates_for(db, "j1")
    assert up["linkedin_contacts"] and "contacts_attempted_at" in up


def test_failed_searches_do_not_stamp_the_job():
    """Proxy down: nothing was looked at, so nothing is marked as looked at."""
    db = FakeDB(JOBS)

    def boom():
        raise RuntimeError("proxy down")
    out = _run(db, boom)
    assert db.updates == []
    assert out["attempted"] == 0


def test_the_select_skips_jobs_attempted_inside_the_retry_window():
    db = FakeDB([])
    before = datetime.now(timezone.utc)
    _run(db, lambda: _resp(200))
    (filters,) = db.selects
    ors = [args[0] for name, args in filters if name == "or_"]
    assert len(ors) == 1, filters
    clause = ors[0]
    assert "contacts_attempted_at.is.null" in clause
    cutoff = datetime.fromisoformat(clause.split("contacts_attempted_at.lt.")[1])
    window = before - cutoff
    # An absolute floor, not just "matches the constant": a window of zero
    # days re-searches everything every run, which is the bug.
    assert window >= timedelta(days=1), window
    assert timedelta(days=scrape_contacts.RETRY_AFTER_DAYS) - timedelta(minutes=1) <= window \
        <= timedelta(days=scrape_contacts.RETRY_AFTER_DAYS) + timedelta(minutes=1)
    assert ("is_", ("linkedin_contacts", "null")) in filters


def test_before_the_migration_it_still_runs_and_says_why_it_cannot_progress():
    """Deployed ahead of the migration, the column does not exist yet."""
    err = Exception("{'code': 'PGRST204', 'message': \"Could not find the 'contacts_attempted_at' "
                    "column of 'jobs' in the schema cache\"}")
    db = FakeDB(JOBS[:1], select_error=err)
    out = _run(db, lambda: _resp(200, PROFILE_HTML))
    assert len(db.selects) == 2, "must fall back to the legacy select"
    assert "contacts_attempted_at" in out.get("error", ""), out
    (up,) = _updates_for(db, "j1")
    assert "contacts_attempted_at" not in up and up["linkedin_contacts"]


def test_an_unrelated_select_error_is_not_swallowed():
    db = FakeDB(JOBS, select_error=RuntimeError("connection reset"))
    try:
        _run(db, lambda: _resp(200))
    except RuntimeError:
        return
    raise AssertionError("an unrelated database error was swallowed")


def test_the_migration_adds_the_column():
    from pathlib import Path
    sql = "\n".join(p.read_text() for p in Path("supabase/migrations").glob("*.sql"))
    assert "contacts_attempted_at" in sql and "timestamptz" in sql
