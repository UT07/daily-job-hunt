"""A band needs the spread, and the spread was being thrown away.

score_single_job_deterministic took the median of each perspective across
num_calls and discarded everything else. The Studio needs min/max to show
"84-88" rather than "86" — a single integer claims a precision the measurement
does not have. Measured 2026-09-28: one model, one identical prompt,
temperature=0, three consecutive calls, three different answers. Temperature
controls sampling; it does not control mixture-of-experts routing or request
batching, neither of which the caller can pin.
"""
import sys
from unittest.mock import patch

sys.path.insert(0, "lambdas/pipeline")
import score_batch  # noqa: E402

JOB = {"job_hash": "h1", "title": "SRE", "company": "Acme",
       "description": "Kubernetes and Terraform.", "location": "Dublin"}
RESUME = r"\documentclass{article}\begin{document}Jane\end{document}"


def _result(ats, hm, tr):
    return {"ats_score": ats, "hiring_manager_score": hm, "tech_recruiter_score": tr,
            "match_score": round((ats + hm + tr) / 3), "reasoning": "r",
            "key_matches": [], "gaps": [], "provider": "p", "model": "m"}


def test_spread_reports_min_and_max_per_perspective():
    calls = [_result(84, 80, 90), _result(88, 82, 86), _result(86, 81, 88)]
    with patch.object(score_batch, "score_single_job", side_effect=calls):
        out = score_batch.score_single_job_deterministic(JOB, RESUME, num_calls=3)
    assert out["ats_score"] == 86                      # median of 84/88/86
    assert out["score_spread"]["ats"] == [84, 88]
    assert out["score_spread"]["hiring_manager"] == [80, 82]
    assert out["score_spread"]["n"] == 3


def test_agreeing_calls_collapse_the_band_to_a_point():
    """When the model agrees with itself, that is itself information."""
    calls = [_result(85, 85, 85)] * 3
    with patch.object(score_batch, "score_single_job", side_effect=calls):
        out = score_batch.score_single_job_deterministic(JOB, RESUME, num_calls=3)
    assert out["score_spread"]["ats"] == [85, 85]


def test_a_single_call_reports_n_1_so_the_ui_can_say_so():
    """num_calls=1 gives no variance information at all. A band from one sample
    must not be presented as three calls agreeing."""
    with patch.object(score_batch, "score_single_job", side_effect=[_result(85, 85, 85)]):
        out = score_batch.score_single_job_deterministic(JOB, RESUME, num_calls=1)
    assert out["score_spread"]["n"] == 1


def test_partial_failure_reports_the_calls_that_landed():
    with patch.object(score_batch, "score_single_job",
                      side_effect=[_result(84, 80, 90), None, _result(88, 82, 86)]):
        out = score_batch.score_single_job_deterministic(JOB, RESUME, num_calls=3)
    assert out["score_spread"]["n"] == 2
    assert out["score_spread"]["ats"] == [84, 88]


def test_all_calls_failing_still_returns_none():
    with patch.object(score_batch, "score_single_job", side_effect=[None, None, None]):
        assert score_batch.score_single_job_deterministic(JOB, RESUME, num_calls=3) is None


def test_repeat_calls_bypass_the_cache():
    """Without this the three calls are one cached answer returned three times."""
    seen = []

    def fake(job, resume_tex, temperature=0, skip_cache=False, untrusted_input=False):
        seen.append(skip_cache)
        return _result(85, 85, 85)

    with patch.object(score_batch, "score_single_job", side_effect=fake):
        score_batch.score_single_job_deterministic(JOB, RESUME, num_calls=3, skip_cache=True)
    assert seen == [True, True, True]


def test_batch_default_is_unchanged():
    """One call, cache on. The batch pipeline scores ~58 jobs a run against an
    8k tokens/minute ceiling; 3x uncached would be a real regression."""
    seen = []

    def fake(job, resume_tex, temperature=0, skip_cache=False, untrusted_input=False):
        seen.append((skip_cache, untrusted_input))
        return _result(85, 85, 85)

    with patch.object(score_batch, "score_single_job", side_effect=fake):
        score_batch.score_single_job_deterministic(JOB, RESUME)
    # (skip_cache, untrusted_input) — the guarded path fences the description,
    # which changes the prompt and therefore the cache key, so the pipeline's
    # default has to stay off here as well as cached.
    assert seen == [(False, False)]


def test_the_non_numeric_fields_still_come_through():
    """The dict must keep score_single_job's shape — callers index these."""
    with patch.object(score_batch, "score_single_job",
                      side_effect=[_result(84, 80, 90), _result(88, 82, 86)]):
        out = score_batch.score_single_job_deterministic(JOB, RESUME, num_calls=2)
    for key in ("reasoning", "key_matches", "gaps", "provider", "model"):
        assert key in out, f"{key} was dropped by the merge"
