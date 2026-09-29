"""The Studio's score must describe the resume on screen, not the one before it.

Phase 1 read scores off the jobs row, which describes the ORIGINAL tailored
resume. After an edit they are stale for the rest of the session and nothing
re-scores. Scoring the rebuilt .tex inside the compile task means the number
arrives with the PDF, on the same trigger, with no extra endpoint and no second
poll — and it cannot drift from the document it describes, because the two are
produced together.

Tested through two small seams (_job_for_scoring, _score_rebuilt_resume) rather
than by mocking S3, tempfiles and tectonic. Those seams are also what make a
scoring failure survivable: the PDF is the deliverable, and a missing score is
a missing number, not a reason to throw away a successful rebuild.
"""
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
import app  # noqa: E402

ROW = {
    "description": "Operate multi-tenant Kubernetes. Own Terraform modules.",
    "title": "Platform Engineer", "company": "Acme",
    "location": "Dublin", "remote": "Hybrid",
}
NEW_TEX = r"\documentclass{article}\begin{document}Jane\end{document}"


def _db_returning(data):
    db = MagicMock()
    chain = MagicMock()
    for m in ("select", "eq", "maybe_single"):
        getattr(chain, m).return_value = chain
    chain.execute.return_value = MagicMock(data=data)
    db.client.table.return_value = chain
    return db


# ---------------------------------------------------------------------------
# _job_for_scoring
# ---------------------------------------------------------------------------

def test_supplies_every_field_score_single_job_reads():
    """score_single_job hard-subscripts job['title'] and job['company'].

    A KeyError there would be swallowed by _score_rebuilt_resume's except and
    degrade silently to "no score" — indistinguishable from the feature simply
    not working. It also .get()s description, location and remote, and reads
    job['job_hash'] in its error paths.
    """
    with patch.object(app, "_db", _db_returning(ROW)):
        job = app._job_for_scoring("j1", "u1")
    for key in ("job_hash", "title", "company", "description", "location", "remote"):
        assert key in job, f"{key} missing — score_single_job would KeyError or score blind"
    assert job["title"] == "Platform Engineer"
    assert job["company"] == "Acme"


def test_no_description_means_nothing_to_score_against():
    with patch.object(app, "_db", _db_returning({**ROW, "description": ""})):
        assert app._job_for_scoring("j1", "u1") is None


def test_a_missing_row_is_not_an_error():
    with patch.object(app, "_db", _db_returning(None)):
        assert app._job_for_scoring("j1", "u1") is None


def test_a_lookup_failure_degrades_to_no_score():
    db = MagicMock()
    db.client.table.side_effect = RuntimeError("supabase down")
    with patch.object(app, "_db", db):
        assert app._job_for_scoring("j1", "u1") is None


# ---------------------------------------------------------------------------
# _score_rebuilt_resume
# ---------------------------------------------------------------------------

SCORED = {
    "ats_score": 86, "hiring_manager_score": 84, "tech_recruiter_score": 90,
    "match_score": 86.0, "reasoning": "r",
    "score_spread": {"ats": [84, 88], "match": [85, 87], "n": 3},
}


def test_it_asks_for_three_uncached_calls():
    """num_calls=1 or a warm cache makes the band meaningless — it would be one
    answer reported as three agreeing ones."""
    with patch.object(app, "_job_for_scoring", return_value={"job_hash": "j1"}), \
         patch.object(app, "score_single_job_deterministic", return_value=SCORED) as scorer:
        app._score_rebuilt_resume("j1", "u1", NEW_TEX)
    kwargs = scorer.call_args.kwargs
    assert kwargs["num_calls"] == 3
    assert kwargs["skip_cache"] is True


def test_it_returns_the_scores_and_the_spread():
    with patch.object(app, "_job_for_scoring", return_value={"job_hash": "j1"}), \
         patch.object(app, "score_single_job_deterministic", return_value=SCORED):
        out = app._score_rebuilt_resume("j1", "u1", NEW_TEX)
    assert out["ats_score"] == 86
    assert out["score_spread"]["n"] == 3


def test_a_scoring_exception_never_propagates():
    """The compile already succeeded and S3 is already written by this point."""
    with patch.object(app, "_job_for_scoring", return_value={"job_hash": "j1"}), \
         patch.object(app, "score_single_job_deterministic", side_effect=RuntimeError("boom")):
        assert app._score_rebuilt_resume("j1", "u1", NEW_TEX) is None


def test_all_scoring_calls_failing_returns_none():
    with patch.object(app, "_job_for_scoring", return_value={"job_hash": "j1"}), \
         patch.object(app, "score_single_job_deterministic", return_value=None):
        assert app._score_rebuilt_resume("j1", "u1", NEW_TEX) is None


def test_no_job_means_no_scoring_call_at_all():
    with patch.object(app, "_job_for_scoring", return_value=None), \
         patch.object(app, "score_single_job_deterministic") as scorer:
        assert app._score_rebuilt_resume("j1", "u1", NEW_TEX) is None
    scorer.assert_not_called()


def test_it_scores_the_REBUILT_tex_not_the_stored_one():
    """The whole point: the number must describe the document just produced."""
    seen = {}
    with patch.object(app, "_job_for_scoring", return_value={"job_hash": "j1"}), \
         patch.object(app, "score_single_job_deterministic",
                      side_effect=lambda job, tex, **kw: seen.update(tex=tex) or SCORED):
        app._score_rebuilt_resume("j1", "u1", NEW_TEX)
    assert seen["tex"] == NEW_TEX
