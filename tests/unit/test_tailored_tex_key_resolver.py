"""The Studio's .tex key must use the hash the pipeline tailored under.

tailor_resume.py writes `users/{uid}/resumes/{job_hash}_tailored.tex`, where
job_hash is the single-job pipeline's input -- resolve_tailor_hash(row), i.e.
canonical_hash for a manual row. `_tailored_tex_key` fell back to job_id
instead, which only coincides with canonical_hash for rows whose job_id
happens to BE that hash.
"""
import pytest

import app as app_module
from tests.unit.postgrest_double import FakeSupabase


@pytest.fixture
def db(monkeypatch):
    fake = FakeSupabase({"jobs": [
        {"job_id": "uuid-1", "user_id": "alice", "job_hash": None, "canonical_hash": "c-manual"},
        {"job_id": "uuid-2", "user_id": "alice", "job_hash": "h-scraped", "canonical_hash": "c-2"},
        {"job_id": "uuid-1", "user_id": "bob", "job_hash": "h-bob", "canonical_hash": "c-bob"},
    ]})
    monkeypatch.setattr(app_module, "_db", fake)
    return fake


def test_a_manual_row_uses_its_canonical_hash(db):
    assert app_module._tailored_tex_key("alice", "uuid-1") == \
        "users/alice/resumes/c-manual_tailored.tex"


def test_a_scraped_row_uses_job_hash(db):
    assert app_module._tailored_tex_key("alice", "uuid-2") == \
        "users/alice/resumes/h-scraped_tailored.tex"


def test_the_read_is_scoped_to_the_caller(db):
    """bob's row shares job_id uuid-1; alice must never get bob's hash."""
    assert "h-bob" not in app_module._tailored_tex_key("alice", "uuid-1")


def test_an_unreadable_row_falls_back_to_job_id(db):
    assert app_module._tailored_tex_key("alice", "missing") == \
        "users/alice/resumes/missing_tailored.tex"
