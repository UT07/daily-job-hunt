"""Unit tests for format_improvement_feedback in resume_scorer."""
from resume_scorer import format_improvement_feedback


def test_improvement_prompt_includes_structured_feedback():
    """Low scores produce structured feedback with perspective names and changes."""
    scores = {"ats_score": 72, "hiring_manager_score": 68, "tech_recruiter_score": 75}
    feedback = {
        "ats_feedback": "Missing keywords: Kubernetes, GraphQL",
        "hm_feedback": "Impact statements lack metrics",
        "tr_feedback": "3/5 required skills present",
    }

    result = format_improvement_feedback(scores, feedback)
    assert "ATS (score: 72)" in result
    assert "Kubernetes" in result
    assert "APPLY THESE SPECIFIC CHANGES" in result
    # All three are below 85, so all should appear as numbered changes
    assert "1." in result
    assert "2." in result
    assert "3." in result


def test_all_high_scores_no_changes():
    """When all scores are 85+, output suggests only minor polish."""
    scores = {"ats_score": 90, "hiring_manager_score": 88, "tech_recruiter_score": 92}
    feedback = {"ats_feedback": "Good", "hm_feedback": "Good", "tr_feedback": "Good"}

    result = format_improvement_feedback(scores, feedback)
    assert "minor polish" in result.lower()
    # No numbered changes should appear
    assert "1." not in result


def test_partial_low_scores():
    """Only perspectives below 85 appear in the changes list."""
    scores = {"ats_score": 90, "hiring_manager_score": 70, "tech_recruiter_score": 88}
    feedback = {
        "ats_feedback": "Pass",
        "hm_feedback": "Needs more impact metrics",
        "tr_feedback": "Pass",
    }

    result = format_improvement_feedback(scores, feedback)
    # All three should appear in the feedback summary
    assert "ATS (score: 90)" in result
    assert "Hiring Manager (score: 70)" in result
    assert "Tech Recruiter (score: 88)" in result
    # Only HM should be in the numbered changes
    assert "1. Needs more impact metrics" in result
    # No second change
    assert "2." not in result
    # Should NOT contain the minor polish message
    assert "minor polish" not in result.lower()


def test_missing_feedback_keys_uses_default():
    """Missing feedback keys fall back to 'No specific feedback'."""
    scores = {"ats_score": 60}
    feedback = {}  # no feedback keys at all

    result = format_improvement_feedback(scores, feedback)
    assert "No specific feedback" in result
    assert "ATS (score: 60)" in result


def test_missing_score_keys_default_to_zero():
    """Missing score keys default to 0, which is below 85."""
    scores = {}
    feedback = {"ats_feedback": "Fix ATS", "hm_feedback": "Fix HM", "tr_feedback": "Fix TR"}

    result = format_improvement_feedback(scores, feedback)
    assert "ATS (score: 0)" in result
    assert "Hiring Manager (score: 0)" in result
    assert "Tech Recruiter (score: 0)" in result
    # All three should appear as changes since 0 < 85
    assert "1." in result
    assert "2." in result
    assert "3." in result


def test_exactly_85_is_not_a_change():
    """Score of exactly 85 should NOT trigger a change entry."""
    scores = {"ats_score": 85, "hiring_manager_score": 85, "tech_recruiter_score": 85}
    feedback = {"ats_feedback": "Pass", "hm_feedback": "Pass", "tr_feedback": "Pass"}

    result = format_improvement_feedback(scores, feedback)
    assert "minor polish" in result.lower()
    assert "1." not in result


def test_feedback_header_present():
    """Output always starts with the FEEDBACK FROM SCORING header."""
    scores = {"ats_score": 50, "hiring_manager_score": 50, "tech_recruiter_score": 50}
    feedback = {"ats_feedback": "a", "hm_feedback": "b", "tr_feedback": "c"}

    result = format_improvement_feedback(scores, feedback)
    assert result.startswith("FEEDBACK FROM SCORING:")
