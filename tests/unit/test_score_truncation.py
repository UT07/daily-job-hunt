"""Truncated scoring responses lose the job, not just the prose.

Measured in the CI AI Eval Gate, 2026-09-28: 10 of 25 score cases failed, every
one of them as a JSON parse error rather than a provider failure —

    [score_batch] JSON parse error for 03f0420586cf: Unterminated string
        starting at: line 1 column 786 (char 785)
    [score_batch] JSON parse error for 756b911dd607: Expecting property name
        enclosed in double quotes: line 56 column 10 (char 2266)
    [score_batch] JSON parse error for 3704e55d2ac9: Expecting value:
        line 1 column 1 (char 0)

A provider answered in each case. The answer was cut off, and score_single_job
returned None, so the job went unscored entirely.

Root cause is the schema, not the constant. `requirement_map` asks for one
object per key JD requirement, each carrying two prose strings, so the output
length is unbounded and no max_tokens can be sized against it. Raising the
budget is not available either: Groq bills prompt + max_tokens against an 8k
tokens/minute ceiling, and the prompt is already ~3.8k.

The four scores are declared FIRST in the schema, so a response truncated in
the trailing prose still carries every number the dashboard needs. Discarding
them throws away work the model actually did.
"""
import json
from unittest.mock import patch

import pytest

import sys
sys.path.insert(0, "lambdas/pipeline")
import score_batch  # noqa: E402

RESUME = r"\documentclass{article}\begin{document}Jane Doe, SRE\end{document}"
JOB = {
    "job_hash": "trunc-1",
    "title": "Platform Engineer",
    "company": "Acme",
    "description": "Kubernetes, Terraform, Go. Run production incident response.",
    "location": "Dublin",
    "apply_url": "https://acme.example/1",
    "source": "linkedin",
}


def _run(content):
    with patch.object(score_batch, "ai_complete_cached",
                      return_value={"content": content, "provider": "groq",
                                    "model": "gpt-oss-120b"}):
        return score_batch.score_single_job(JOB, RESUME)


# A real response shape, cut off mid-way through the requirement_map prose.
TRUNCATED = (
    '{"ats_score": 82, "hiring_manager_score": 74, "tech_recruiter_score": 79, '
    '"match_score": 78, "archetype": "Platform/Cloud", "seniority": "Mid-Level", '
    '"remote": "Hybrid", "reasoning": "Strong Kubernetes and Terraform coverage '
    'with clear production ownership, but no evidence of incident response.", '
    '"key_matches": ["Kubernetes", "Terraform"], "gaps": ["incident response"], '
    '"requirement_map": [{"requirement": "operate multi-tenant Kubernetes", '
    '"evidence": "ran a 40-node cluster for'      # <-- cut here, mid-string
)


def test_truncated_response_keeps_the_scores():
    """The numbers survived the truncation; the job should too."""
    with pytest.raises(json.JSONDecodeError):
        json.loads(TRUNCATED)          # prove the input really is unparseable

    result = _run(TRUNCATED)
    assert result is not None, "a truncated response lost the job entirely"
    assert result["ats_score"] == 82
    assert result["hiring_manager_score"] == 74
    assert result["tech_recruiter_score"] == 79


def test_salvaged_result_is_flagged_not_silent():
    """A partial result must announce itself.

    Recording a salvaged score as though it were a clean parse is the same
    class of bug as a pipeline reporting SUCCEEDED having written nothing.
    """
    result = _run(TRUNCATED)
    assert result.get("truncated") is True, (
        "salvaged scores were returned indistinguishable from a clean parse"
    )


def test_truncation_before_the_scores_is_not_salvaged():
    """Never invent a score. If the numbers did not arrive, the job is unscored."""
    assert _run('{"archetype": "Platform/Cloud", "seniority": "Mid-') is None


def test_out_of_range_values_are_not_salvaged():
    """A malformed number is corruption, not a score."""
    bad = ('{"ats_score": 820, "hiring_manager_score": 74, '
           '"tech_recruiter_score": 79, "reasoning": "cut')
    assert _run(bad) is None


def test_empty_content_does_not_crash():
    """`Expecting value: line 1 column 1 (char 0)` — the empty-response case."""
    assert _run("") is None
    assert _run("   \n  ") is None


def test_clean_json_is_untouched_and_unflagged():
    """The happy path must not acquire a truncated flag."""
    clean = json.dumps({
        "ats_score": 88, "hiring_manager_score": 91, "tech_recruiter_score": 89,
        "match_score": 89, "reasoning": "good", "key_matches": ["k8s"],
        "gaps": [], "requirement_map": [],
    })
    result = _run(clean)
    assert result["ats_score"] == 88
    assert not result.get("truncated")


def test_salvage_recomputes_match_score_when_it_was_cut():
    """match_score is the mean of the three perspectives, so it is derivable."""
    cut_before_match = ('{"ats_score": 90, "hiring_manager_score": 80, '
                        '"tech_recruiter_score": 70, "reasoning": "trunc')
    result = _run(cut_before_match)
    assert result["match_score"] == 80, f"expected mean 80, got {result['match_score']}"


# ---------------------------------------------------------------------------
# The schema itself
# ---------------------------------------------------------------------------

def test_requirement_map_is_bounded():
    """An unbounded output field makes the token budget unsizeable.

    The prompt must cap how many requirement_map entries the model returns;
    otherwise a JD with many requirements overruns max_tokens no matter what
    it is set to.
    """
    p = score_batch.SCORE_SYSTEM_PROMPT
    assert "requirement_map" in p
    import re
    assert re.search(r"(at most|no more than|maximum of|up to)\s+\d+", p, re.I), (
        "requirement_map has no explicit cap — output length is unbounded"
    )
