"""/api/score must SAVE, not just score.

The button is labelled "Save & Score". Until 2026-09-28 the endpoint computed a
score, returned it, and persisted nothing -- so a manually added job never
reached the dashboard, while Tailor Resume and Cover Letter (which go through
_find_or_create_job) did save. The most obviously-named save button was the
only one that did not save.
"""
import pytest

import app as app_module


@pytest.mark.parametrize("score,tier", [
    (100, "S"), (90, "S"), (89.9, "A"), (80, "A"), (79, "B"),
    (70, "B"), (69, "C"), (60, "C"), (59, "D"), (0, "D"), (None, "D"),
])
def test_score_tier_bands(score, tier):
    assert app_module._score_tier(score) == tier


def test_score_tier_matches_the_pipeline():
    """A manual score and a pipeline score must land in the SAME tier.

    They are separate implementations in separate deployment artifacts (API
    container vs pipeline zip), so nothing but this test stops them drifting.
    """
    import sys
    sys.path.insert(0, "lambdas/pipeline")
    from score_batch import score_to_tier

    for s in [None, 0, 12.5, 59, 59.9, 60, 69.9, 70, 79.9, 80, 89.9, 90, 100]:
        assert app_module._score_tier(s) == score_to_tier(s), f"drift at {s}"


def test_score_response_exposes_the_save_outcome():
    """saved=False must be distinguishable from a successful save: the UI has
    to be able to say 'scored but not saved' rather than silently implying it
    landed."""
    fields = app_module.ScoreResponse.model_fields
    assert "job_id" in fields
    assert "saved" in fields
    assert fields["saved"].default is False


def test_score_endpoint_calls_the_shared_save_path():
    """Must reuse _find_or_create_job -- the same canonical_hash dedupe the
    tailor/cover-letter actions use -- so scoring one JD twice updates a single
    row instead of creating a duplicate."""
    import inspect
    # The save lives in _score_fresh, which the "score" task runs, since a
    # fresh score moved out of the request (72.6s measured vs ~30s gateway).
    src = inspect.getsource(app_module._score_fresh)
    assert "_find_or_create_job" in src
