"""data_retention.py must not destroy what the product still uses.

Three defects, each of which would have deleted live data on the first run:

  * no dry run: running the script to see what it would do did it.
  * purge_old_jobs deleted every job older than 90 days, including ones the
    user APPLIED to. The lifecycle rule (shared/job_lifecycle.py) is that an
    engaged job is kept forever.
  * purge_old_s3_artifacts deleted every users/ object older than 30 days,
    including the tailored .tex/.pdf the dashboard and Studio open for any
    job first seen more than a month ago.
"""
from __future__ import annotations

import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

_SCRIPT = pathlib.Path("scripts/data_retention.py")
_spec = importlib.util.spec_from_file_location("data_retention_under_test", _SCRIPT)
dr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dr)

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
OLD = (NOW - timedelta(days=200)).isoformat()


class FakeTable:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.op = db, name, [], "select"

    def select(self, *a, **k):
        return self

    def delete(self):
        self.op = "delete"
        return self

    def __getattr__(self, attr):
        if attr == "not_":
            return self

        def f(*args, **kwargs):
            self.filters.append((attr, args))
            return self
        return f

    def execute(self):
        rows = list(self.db.rows.get(self.name, []))
        for name, args in self.filters:
            if name == "range":
                lo, hi = args
                rows = rows[lo:hi + 1]
        if self.op == "delete":
            self.db.deletes.append((self.name, list(self.filters)))
            return MagicMock(data=[])
        return MagicMock(data=rows)


class FakeDB:
    def __init__(self, rows):
        self.rows, self.deletes = rows, []
        self.client = self

    def table(self, name):
        return FakeTable(self, name)


JOBS = [
    {"job_id": "old-unseen", "user_id": "u1", "job_hash": "h1", "first_seen": OLD, "application_status": None},
    {"job_id": "old-new", "user_id": "u1", "job_hash": "h2", "first_seen": OLD, "application_status": "New"},
    {"job_id": "old-applied", "user_id": "u1", "job_hash": "h3", "first_seen": OLD, "application_status": "Applied"},
    {"job_id": "old-interviewing", "user_id": "u1", "job_hash": "h4", "first_seen": OLD,
     "application_status": "Interviewing"},
    {"job_id": "old-offer", "user_id": "u1", "job_hash": "h5", "first_seen": OLD, "application_status": "Offer"},
]


def _deleted_ids(db):
    ids = []
    for table, filters in db.deletes:
        assert table == "jobs"
        for name, args in filters:
            if name == "in_" and args[0] == "job_id":
                ids += list(args[1])
    return ids


# --- jobs ----------------------------------------------------------------

def test_purge_is_a_dry_run_by_default():
    db = FakeDB({"jobs": JOBS})
    ids = dr.purge_old_jobs(db, days=90, now=NOW)
    assert db.deletes == [], "a dry run deleted rows"
    assert set(ids) == {"old-unseen", "old-new"}


def test_commit_deletes_only_unengaged_jobs():
    db = FakeDB({"jobs": JOBS})
    dr.purge_old_jobs(db, days=90, now=NOW, commit=True)
    assert set(_deleted_ids(db)) == {"old-unseen", "old-new"}


@pytest.mark.parametrize("status", ["Applied", "Interviewing", "Offer"])
def test_engaged_jobs_are_exempt(status):
    db = FakeDB({"jobs": [dict(JOBS[0], application_status=status)]})
    assert dr.purge_old_jobs(db, days=90, now=NOW, commit=True) == []
    assert db.deletes == []


def test_a_row_without_a_usable_first_seen_is_never_purged():
    rows = [dict(JOBS[0], first_seen=None), dict(JOBS[1], first_seen="garbage")]
    db = FakeDB({"jobs": rows})
    assert dr.purge_old_jobs(db, days=90, now=NOW, commit=True) == []


# --- S3 ------------------------------------------------------------------

class FakeS3:
    def __init__(self, keys):
        old = NOW - timedelta(days=100)
        self.objects = {k: old for k in keys}
        self.deleted = []

    def get_paginator(self, _op):
        s3 = self

        class P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k, "LastModified": t} for k, t in s3.objects.items()
                                    if k.startswith(Prefix)]}
        return P()

    def delete_objects(self, Bucket, Delete):
        self.deleted += [o["Key"] for o in Delete["Objects"]]


LIVE_JOB = {"job_id": "live", "user_id": "u1", "job_hash": "abc", "canonical_hash": "abc",
            "resume_s3_key": "users/u1/resumes/abc_tailored.pdf",
            "cover_letter_s3_key": None,
            "resume_s3_url": None,
            "cover_letter_s3_url": "https://b.s3.amazonaws.com/users/u1/cover_letters/abc_cover.pdf?X-Amz-Signature=x",
            "first_seen": (NOW - timedelta(days=45)).isoformat(), "application_status": None}

KEYS = [
    "users/u1/resumes/abc_tailored.pdf",      # resume_s3_key
    "users/u1/resumes/abc_tailored.tex",      # the tex key the Studio opens
    "users/u1/cover_letters/abc_cover.pdf",   # only in a presigned URL
    "users/u1/cover_letters/abc_cover.tex",
    "users/u1/resumes/orphan_tailored.pdf",   # no row references it
]


def test_s3_purge_is_a_dry_run_by_default():
    s3 = FakeS3(KEYS)
    db = FakeDB({"jobs": [LIVE_JOB], "resume_versions": []})
    n = dr.purge_old_s3_artifacts("b", db, days=30, s3=s3, now=NOW)
    assert s3.deleted == []
    assert n == 1


def test_s3_purge_never_deletes_a_referenced_artifact():
    s3 = FakeS3(KEYS)
    db = FakeDB({"jobs": [LIVE_JOB], "resume_versions": []})
    dr.purge_old_s3_artifacts("b", db, days=30, s3=s3, now=NOW, commit=True)
    assert s3.deleted == ["users/u1/resumes/orphan_tailored.pdf"]


BARE = {"job_id": "bare", "user_id": "u1", "job_hash": None, "canonical_hash": None,
        "resume_s3_key": None, "cover_letter_s3_key": None, "resume_s3_url": None,
        "cover_letter_s3_url": None}


def test_a_key_referenced_only_by_a_presigned_url_is_protected():
    s3 = FakeS3(["users/u1/resumes/legacy-name.pdf"])
    row = dict(BARE, resume_s3_url="https://b.s3.amazonaws.com/users/u1/resumes/legacy-name.pdf?X-Amz-Signature=s")
    db = FakeDB({"jobs": [row], "resume_versions": []})
    dr.purge_old_s3_artifacts("b", db, days=30, s3=s3, now=NOW, commit=True)
    assert s3.deleted == []


def test_the_studio_tex_is_protected_by_convention_alone():
    """No key column set: the tex the Studio opens is found from job_hash."""
    s3 = FakeS3(["users/u1/resumes/h9_tailored.tex", "users/u1/cover_letters/h9_cover.tex"])
    db = FakeDB({"jobs": [dict(BARE, job_hash="h9")], "resume_versions": []})
    dr.purge_old_s3_artifacts("b", db, days=30, s3=s3, now=NOW, commit=True)
    assert s3.deleted == []


def test_resume_versions_keys_are_protected_too():
    s3 = FakeS3(["users/u1/versions/v1.pdf"])
    db = FakeDB({"jobs": [], "resume_versions": [{"resume_s3_key": "users/u1/versions/v1.pdf",
                                                   "cover_letter_s3_key": None}]})
    dr.purge_old_s3_artifacts("b", db, days=30, s3=s3, now=NOW, commit=True)
    assert s3.deleted == []


def test_jobs_being_purged_do_not_protect_their_artifacts():
    s3 = FakeS3(["users/u1/resumes/abc_tailored.pdf"])
    db = FakeDB({"jobs": [LIVE_JOB], "resume_versions": []})
    n = dr.purge_old_s3_artifacts("b", db, days=30, s3=s3, now=NOW, exclude_job_ids={"live"})
    assert n == 1


def test_s3_purge_refuses_to_run_blind():
    """If the referenced set cannot be read, delete nothing."""
    s3 = FakeS3(KEYS)
    db = MagicMock()
    db.client.table.side_effect = RuntimeError("db down")
    with pytest.raises(RuntimeError):
        dr.purge_old_s3_artifacts("b", db, days=30, s3=s3, now=NOW, commit=True)
    assert s3.deleted == []


@pytest.mark.parametrize("url", [
    "https://utkarsh-job-hunt.s3.amazonaws.com/users/u1/resumes/x_tailored.pdf?X-Amz-Signature=s",
    "https://s3.eu-west-1.amazonaws.com/utkarsh-job-hunt/users/u1/resumes/x_tailored.pdf?X-Amz-Expires=1",
    "https://s3.eu-west-1.amazonaws.com/myusers/users/u1/resumes/x_tailored.pdf",
])
def test_a_presigned_url_yields_its_key(url):
    """A presigned URL is a key plus a signature (CLAUDE.md #9)."""
    assert dr._key_from_url(url) == "users/u1/resumes/x_tailored.pdf"


def test_the_cli_defaults_to_dry_run():
    assert dr.parse_args([]).commit is False
    assert dr.parse_args(["--commit"]).commit is True
