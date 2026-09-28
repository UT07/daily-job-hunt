"""save_job must not overwrite the user's application_status.

It used to write "failed", "ready" and "scored" into that column — pipeline
states, not user actions. Two live consequences on 2026-09-28:

  1. None are in app.py's _VALID_STATUSES, so the API would 400 a user setting
     them while the pipeline wrote them freely, and the dashboard's Status
     filter could never match the ~68% of rows holding one (scored 236,
     ready 611, failed 7, against New 118 / Applied 1 / Rejected 2 /
     Withdrawn 35).
  2. Writing one ERASED a real status: a job marked "Applied" reverted to
     "ready" on the next pipeline run.
"""
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "lambdas/pipeline")
import save_job  # noqa: E402

PIPELINE_STATES = {"ready", "scored", "failed"}


def _db():
    db = MagicMock()
    chain = MagicMock()
    for m in ("update", "eq"):
        getattr(chain, m).return_value = chain
    chain.execute.return_value = MagicMock(data=[])
    db.table.return_value = chain
    return db, chain


def _written(chain):
    """The dict passed to .update(), or None if update was never called."""
    return chain.update.call_args[0][0] if chain.update.call_args else None


# The handler derives its state from compile_result, not from flat keys —
# an earlier version of this file invented the event shape and the tests
# passed for the wrong reason (the handler returned before touching the DB).
FAILED_COMPILE = {"compile_result": {"error": "tectonic_error", "stderr": "tectonic exploded"}}
GOOD_COMPILE = {"compile_result": {"pdf_s3_key": "users/u/resumes/abc_tailored.pdf"}}


@pytest.mark.parametrize("event,label", [
    (FAILED_COMPILE, "a compile failure"),
    (GOOD_COMPILE, "a successful resume"),
])
def test_never_writes_application_status(event, label):
    db, chain = _db()
    with patch("save_job.get_supabase", return_value=db), \
         patch("save_job.boto3", MagicMock()):
        save_job.handler({"job_hash": "h1", "user_id": "u1", **event}, None)
    written = _written(chain) or {}
    assert "application_status" not in written, (
        f"{label} wrote application_status={written.get('application_status')!r} — "
        "that column belongs to the user"
    )


def test_a_compile_failure_is_still_recorded_somewhere():
    """Dropping the status must not drop the information."""
    db, chain = _db()
    with patch("save_job.get_supabase", return_value=db), \
         patch("save_job.boto3", MagicMock()):
        save_job.handler({"job_hash": "h1", "user_id": "u1", **FAILED_COMPILE}, None)
    assert "tectonic exploded" in (_written(chain) or {}).get("failure_reason", "")


def test_a_successful_rerun_clears_a_prior_failure():
    db, chain = _db()
    with patch("save_job.get_supabase", return_value=db), \
         patch("save_job.boto3", MagicMock()):
        save_job.handler({"job_hash": "h1", "user_id": "u1", **GOOD_COMPILE}, None)
    assert (_written(chain) or {})["failure_reason"] is None


def test_nothing_to_say_issues_no_update_at_all():
    """The old code wrote application_status='scored' purely to avoid an empty
    dict. An empty PATCH is the thing to avoid, not a reason to invent data."""
    db, chain = _db()
    with patch("save_job.get_supabase", return_value=db), \
         patch("save_job.boto3", MagicMock()):
        result = save_job.handler({"job_hash": "h1", "user_id": "u1"}, None)
    assert chain.update.call_args is None, "issued an update with nothing to update"
    assert result["saved"] is False, "nothing was written; saved must not claim otherwise"


def test_the_pipeline_vocabulary_is_disjoint_from_the_user_vocabulary():
    """Guards the root confusion: these are two different concepts and must
    never share a column again."""
    sys.path.insert(0, ".")
    import app as app_module
    assert not (PIPELINE_STATES & app_module._VALID_STATUSES), (
        "a pipeline state has been added to the user status vocabulary"
    )
