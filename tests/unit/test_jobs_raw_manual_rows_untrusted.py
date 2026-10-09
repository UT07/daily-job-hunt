"""A MANUAL jobs_raw row is never a source of apply_url or location.

Third pass of the 2026-10-09 security review (cross-tenant data tampering,
now in lambdas/pipeline/score_batch.py). 0e469fa stopped NEW manual rows from
storing a submitter's apply_url/location, and made score_batch prefer the
user's own row. But it still FELL BACK to jobs_raw's values when the user had
none, and every manual row written before that commit still holds its first
submitter's link and location. So the fallback served an attacker's link from
legacy data -- to any user whose own row had no link, including users who
reach the job through the daily pipeline rather than by adding it.

Rule: apply_url and location may come from a jobs_raw row only when a scraper
wrote it (source != 'manual'). The same rule covers the cover letter prompt,
which also read location from jobs_raw. A migration that blanks the legacy
values exists (supabase/migrations/20261009120000_...), NOT applied; these
tests run against legacy rows exactly as they sit today, i.e. BEFORE it runs.

Also here: `_user_job_row` interpolated job_hash into a PostgREST `or=`
expression, a filter language where ',' separates conditions. A job_hash is
now validated as lowercase hex before it is interpolated, and an invalid one
means "no row" -- the same rule save_job and reconcile_resume_rows already
applied, now in one place (shared/job_hash_filter.py).
"""
import copy
import pathlib
import sys

import pytest

from tests.unit.postgrest_double import FakeSupabase
from utils.canonical_hash import canonical_hash

sys.path.insert(0, "lambdas/pipeline")
import score_batch  # noqa: E402

JD = ("We are hiring a Site Reliability Engineer to own our Kubernetes "
      "platform, SLOs, on-call rotation and incident response for a global "
      "payments product. ") * 3
HASH = canonical_hash("Acme", "Site Reliability Engineer", JD)
RESUME = "\\documentclass{article}\\begin{document}SRE, Kubernetes\\end{document}"
LEGACY_MANUAL = {"job_hash": HASH, "title": "Site Reliability Engineer", "company": "Acme",
                 "description": JD, "source": "manual",
                 "apply_url": "https://attacker.example/phish", "location": "Austin, Texas, USA"}
SCORE = {"match_score": 95, "ats_score": 95, "hiring_manager_score": 95,
         "tech_recruiter_score": 95, "reasoning": "fits", "gaps": [],
         "key_matches": ["Kubernetes: cut MTTR 40%"]}


@pytest.fixture
def db(monkeypatch):
    fake = FakeSupabase({
        "jobs": [], "jobs_raw": [copy.deepcopy(LEGACY_MANUAL)],
        "users": [{"id": "victim", "email": "v@x.test", "work_authorizations": {}}],
        "user_resumes": [{"user_id": "victim", "resume_key": "default",
                          "tex_content": RESUME, "created_at": "2026-10-01"}]})
    monkeypatch.setattr(score_batch, "get_supabase", lambda: fake)
    monkeypatch.setattr(score_batch, "score_single_job_deterministic",
                        lambda *a, **k: copy.deepcopy(SCORE))
    return fake


def _score(user="victim"):
    score_batch.handler({"user_id": user, "new_job_hashes": [HASH],
                         "min_match_score": 0}, None)


def test_a_legacy_manual_row_gives_the_victim_no_link_and_no_location(db):
    """The daily-pipeline shape: the victim has no row of their own."""
    _score()
    [row] = db.rows("jobs", user_id="victim")
    assert not row.get("apply_url"), row["apply_url"]
    assert not row.get("location"), row["location"]
    assert "attacker.example" not in repr(row) and "Austin" not in repr(row)


def test_a_legacy_manual_location_does_not_drive_the_geo_cap(db):
    _score()
    [row] = db.rows("jobs", user_id="victim")
    assert row["match_score"] == 95, "the attacker's location capped the victim's score"


def test_a_victim_row_with_empty_fields_is_not_filled_from_a_manual_row(db):
    db.tables["jobs"].append({"job_id": "j-1", "user_id": "victim", "canonical_hash": HASH,
                              "apply_url": "", "location": None})
    _score()
    [row] = db.rows("jobs", user_id="victim")
    assert not row.get("apply_url") and not row.get("location"), row


def test_a_scraped_row_still_provides_them(db):
    db.tables["jobs_raw"] = [{**LEGACY_MANUAL, "source": "linkedin",
                              "apply_url": "https://jobs.lever.co/acme/1",
                              "location": "Dublin, Ireland"}]
    _score()
    [row] = db.rows("jobs", user_id="victim")
    assert row["apply_url"] == "https://jobs.lever.co/acme/1"
    assert row["location"] == "Dublin, Ireland"


def test_the_trust_rule_itself():
    from shared.jobs_raw_trust import strip_untrusted_raw_fields
    manual = strip_untrusted_raw_fields(LEGACY_MANUAL)
    assert manual["apply_url"] is None and manual["location"] is None
    assert manual["description"] == JD, "only the submitter's fields are dropped"
    assert LEGACY_MANUAL["apply_url"], "the input row was mutated"
    scraped = {**LEGACY_MANUAL, "source": "greenhouse"}
    assert strip_untrusted_raw_fields(scraped) == scraped


def test_the_cover_letter_prompt_ignores_a_manual_rows_location():
    src = pathlib.Path("lambdas/pipeline/generate_cover_letter.py").read_text()
    assert "strip_untrusted_raw_fields(" in src, (
        "generate_cover_letter reads jobs_raw.location into its prompt without "
        "dropping a manual row's submitter-supplied value")


# ── or= filter injection ─────────────────────────────────────────────────────

INJECTED = "000000,user_id.eq.victim"


def test_a_non_hex_hash_never_reaches_the_or_expression():
    db = FakeSupabase({"jobs": [{"job_id": "other-job", "user_id": "victim",
                                 "job_hash": "ffffff", "apply_url": "x"}]})
    assert score_batch._user_job_row(db, "victim", INJECTED) is None, (
        "an injected or= term matched another of the user's rows")


def test_write_job_row_with_a_non_hex_hash_does_not_update_another_row():
    db = FakeSupabase({"jobs": [{"job_id": "other-job", "user_id": "victim",
                                 "job_hash": "ffffff", "match_score": 10}]})
    score_batch._write_job_row(db, {"job_id": "new", "user_id": "victim",
                                    "job_hash": INJECTED, "match_score": 99})
    other = db.rows("jobs", job_id="other-job")[0]
    assert other["match_score"] == 10


@pytest.mark.parametrize("value,ok", [
    ("3d8c1e0926ab", True), ("abcdef", True), ("a" * 64, True),
    ("ABCDEF", False), ("abcde", False), ("a" * 65, False), ("", False),
    (None, False), ("abc,def", False), ("abcdef\n", False), (INJECTED, False),
])
def test_the_shared_validator(value, ok):
    from shared.job_hash_filter import is_job_hash
    assert is_job_hash(value) is ok


def test_every_hash_or_filter_uses_the_shared_validator():
    """save_job and score_batch build the same or= filter; one rule."""
    for path in ("lambdas/pipeline/score_batch.py", "lambdas/pipeline/save_job.py"):
        src = pathlib.Path(path).read_text()
        assert "is_job_hash(" in src, f"{path} interpolates a hash without the shared check"


def test_the_migration_is_scoped_to_manual_rows_and_idempotent():
    [mig] = list(pathlib.Path("supabase/migrations").glob("*jobs_raw_manual_rows*.sql"))
    sql = " ".join(mig.read_text().split()).lower()
    assert "update public.jobs_raw" in sql
    assert "where source = 'manual'" in sql
    assert "apply_url = null" in sql and "location = null" in sql
    assert "is not null" in sql, "not idempotent: re-running rewrites every manual row"
