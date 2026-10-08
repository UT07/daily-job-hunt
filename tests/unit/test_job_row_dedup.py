"""One job must not become two rows.

Measured 2026-10-08 on a job added through Add Job minutes earlier:

    job_id b3026c307c74   job_hash NULL   canonical_hash b3026c307c74
                          pending, score 0        <- app._find_or_create_job
    job_id dedc376d-...   job_hash b3026c307c74   canonical_hash NULL
                          scored, A/85            <- score_batch

Same job, two rows, two key conventions, and nothing could match them because
one populates `canonical_hash` and the other `job_hash`. The user sees a scored
job beside an unscored duplicate of itself; the deploy smoke test reports both
a missing artifact and a tier contradicting its own score.

Latent until tonight only because Add Job itself had been broken since
2026-09-29 — fixing that made this reachable on every manual add.
"""
import ast
import pathlib
import sys
from unittest.mock import MagicMock

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lambdas/pipeline"))

import score_batch  # noqa: E402


def _db(existing=None):
    """A Supabase double whose select() returns `existing`."""
    db = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.or_.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = MagicMock(data=([existing] if existing else []))
    chain.insert.return_value = chain
    chain.update.return_value = chain
    db.table.return_value = chain
    return db, chain


RECORD = {"job_id": "new-uuid", "user_id": "u1", "job_hash": "h1",
          "title": "DevOps Engineer", "match_score": 85, "score_tier": "A"}


def test_a_new_job_is_inserted():
    db, chain = _db(existing=None)
    score_batch._write_job_row(db, dict(RECORD))
    assert chain.insert.called
    assert not chain.update.called


def test_an_existing_job_is_updated_not_duplicated():
    """The whole point."""
    db, chain = _db(existing={"job_id": "already-here"})
    score_batch._write_job_row(db, dict(RECORD))
    assert chain.update.called, "a second row was inserted for a job that exists"
    assert not chain.insert.called


def test_the_update_keeps_the_rows_own_identity():
    db, chain = _db(existing={"job_id": "already-here"})
    score_batch._write_job_row(db, dict(RECORD))
    written = chain.update.call_args[0][0]
    for field in ("job_id", "user_id", "job_hash"):
        assert field not in written, f"{field} must not be rewritten on an existing row"


def test_the_update_never_touches_what_the_user_owns():
    """`application_status` is the user's column. Writing it here would revert a
    job they marked Applied on the next scoring pass — the exact defect
    save_job was fixed for on 2026-09-28. `first_seen` is the row's age, not
    this run's clock."""
    db, chain = _db(existing={"job_id": "already-here"})
    rec = {**RECORD, "application_status": "scored", "first_seen": "2026-10-08"}
    score_batch._write_job_row(db, rec)
    written = chain.update.call_args[0][0]
    assert "application_status" not in written
    assert "first_seen" not in written


def test_the_update_does_carry_the_scores():
    """It must still be an update — excluding too much makes it a no-op."""
    db, chain = _db(existing={"job_id": "already-here"})
    score_batch._write_job_row(db, dict(RECORD))
    written = chain.update.call_args[0][0]
    assert written["match_score"] == 85
    assert written["score_tier"] == "A"


def test_a_failed_lookup_falls_back_to_insert():
    """Scoring a job and losing the result is worse than one duplicate row."""
    db, chain = _db(existing=None)
    chain.execute.side_effect = [RuntimeError("PostgREST down"), MagicMock(data=[])]
    score_batch._write_job_row(db, dict(RECORD))
    assert chain.insert.called


def test_the_api_row_is_findable_without_writing_job_hash():
    """The two paths must share a key, and it must NOT be job_hash.

    The obvious fix — have `_find_or_create_job` write job_hash too — was
    tried and reverted. `jobs.job_hash` carries a foreign key to `jobs_raw`,
    and a manually added job has no jobs_raw row when that insert runs, so the
    insert fails and Add Job silently creates nothing. That is the exact
    failure repaired hours earlier, reintroduced by its own fix;
    tests/unit/test_no_descriptionless_job_stubs.py caught it.

    So the API keeps writing only `canonical_hash`, and the pipeline matches on
    EITHER column — both hold the same value.
    """
    tree = ast.parse((ROOT / "app.py").read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_find_or_create_job")
    keys = {k.value for node in ast.walk(fn) if isinstance(node, ast.Dict)
            for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    assert "canonical_hash" in keys, "the API row has no key the pipeline can match"
    assert "job_hash" not in keys, (
        "_find_or_create_job writes job_hash, which has a foreign key to "
        "jobs_raw that a manual job cannot satisfy — this insert will fail and "
        "Add Job will silently create nothing")

    src = (ROOT / "lambdas/pipeline/score_batch.py").read_text()
    assert "canonical_hash.eq." in src and "job_hash.eq." in src, (
        "score_batch matches on only one column, so it cannot find the row the "
        "API created and will insert a duplicate")


def test_a_shape_that_is_not_a_row_is_not_treated_as_one():
    """Truthiness is not enough. Routing a NEW job into the update branch makes
    its `.eq(job_id, ...)` match nothing and the score is lost silently —
    strictly worse than the duplicate this function exists to prevent."""
    for junk in (MagicMock(), {"no_job_id": 1}, {"job_id": None}, "a string", 42):
        db, chain = _db()
        chain.execute.return_value = MagicMock(data=[junk])
        score_batch._write_job_row(db, dict(RECORD))
        assert chain.insert.called, f"{junk!r} was accepted as an existing row"
        assert not chain.update.called
