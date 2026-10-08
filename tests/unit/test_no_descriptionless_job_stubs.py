"""A job with no description cannot be scored or tailored, so do not create it.

There is nothing to tailor AGAINST. A row created for one is a permanent stub:
it can never acquire an artifact, and smoke_prod's artifact-completeness check
flags it on every deploy with no action that would clear it.

Measured 2026-09-30 against production: 2 such rows existed, both left by Add
Job attempts that failed partway on 2026-09-29 when the council gave up (#141).
One of them — d1ad2affe913, Viatel — carried score_tier A with a zero-length
description and was the single failure in the post-deploy gate.

Note on the related dead end: job_hash is NULL on these rows, and setting it
looks like the obvious fix. It is not. `jobs.job_hash` has a FOREIGN KEY to
`jobs_raw.job_hash`, and a manual job never passes through jobs_raw (the scrape
landing table), so the insert fails with

    violates foreign key constraint "jobs_job_hash_fkey"
    Key (job_hash)=(...) is not present in table "jobs_raw"

and because that insert is wrapped in try/except-and-log, Add Job would have
silently created nothing at all. Unit tests against a mocked DB passed that
change happily; only running it against the real database rejected it.
"""
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
import app  # noqa: E402

FULL = {"company": "Acme Ltd", "job_title": "SRE",
        "job_description": "AWS, Kubernetes, Terraform.", "location": "Dublin"}


def _db_with_no_existing_job():
    db = MagicMock()
    chain = MagicMock()
    for m in ("select", "eq", "maybe_single", "update", "insert"):
        getattr(chain, m).return_value = chain
    chain.execute.return_value = MagicMock(data=None)
    # An INSERT answers with the row it wrote, as supabase-py's default
    # returning=representation does. Answering data=None here would model an
    # insert that wrote nothing, and _find_or_create_job now (correctly)
    # refuses to report a job_id for that.
    chain.insert.side_effect = lambda row: MagicMock(
        execute=MagicMock(return_value=MagicMock(data=[row])))
    db.client.table.return_value = chain
    return db, chain


def test_an_empty_description_creates_no_row():
    db, chain = _db_with_no_existing_job()
    with patch.object(app, "_db", db):
        out = app._find_or_create_job("u1", {**FULL, "job_description": ""})
    assert out == ""
    assert not chain.insert.called, "created a row that can never be tailored"


def test_a_whitespace_only_description_creates_no_row():
    db, chain = _db_with_no_existing_job()
    with patch.object(app, "_db", db):
        out = app._find_or_create_job("u1", {**FULL, "job_description": "   \n\t "})
    assert out == ""
    assert not chain.insert.called


def test_a_missing_description_key_creates_no_row():
    db, chain = _db_with_no_existing_job()
    payload = {k: v for k, v in FULL.items() if k != "job_description"}
    with patch.object(app, "_db", db):
        assert app._find_or_create_job("u1", payload) == ""
    assert not chain.insert.called


def test_a_real_description_still_creates_a_row():
    """Guard the guard: the refusal must not swallow legitimate jobs."""
    db, chain = _db_with_no_existing_job()
    with patch.object(app, "_db", db):
        out = app._find_or_create_job("u1", FULL)
    assert out, "a job with a description was refused"
    assert chain.insert.called
    row = chain.insert.call_args[0][0]
    assert row["description"] == FULL["job_description"]


def test_job_hash_is_deliberately_not_set():
    r"""jobs.job_hash has a FK to jobs_raw.job_hash and a manual job never
    passes through jobs_raw, so writing it fails the insert — which is
    swallowed by try/except-and-log, creating nothing at all.

    Pinned so the "obvious fix" is not attempted again without reading why.
    """
    db, chain = _db_with_no_existing_job()
    with patch.object(app, "_db", db):
        app._find_or_create_job("u1", FULL)
    row = chain.insert.call_args[0][0]
    assert "job_hash" not in row, (
        "job_hash was added to the manual insert; it has a foreign key to "
        "jobs_raw and manual jobs are not in jobs_raw, so this insert will "
        "fail and Add Job will silently create nothing"
    )
