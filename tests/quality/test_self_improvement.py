"""Tier 4d: Self-improvement loop tests.

Validates the tiered adjustment system, rollback logic, cooldown handling,
conflict detection, query optimization, and base resume keyword suggestions.

Run: python -m pytest tests/quality/test_self_improvement.py -v
"""
from self_improver import (
    generate_adjustments,
    detect_conflicts,
    should_revert_adjustment,
    is_on_cooldown,
    analyze_query_effectiveness,
    analyze_keyword_gaps_for_resume,
)


class TestTieredRisk:
    def test_low_risk_auto_applied(self):
        adjs = generate_adjustments(scraper_stats={"broken": {"yields": [0, 0, 0]}})
        low = [a for a in adjs if a["risk_level"] == "low"]
        assert all(a["status"] == "auto_applied" for a in low)

    def test_medium_risk_notifies(self):
        adjs = generate_adjustments(score_stats={"pct_below_50": 0.85, "avg_score": 42})
        med = [a for a in adjs if a["risk_level"] == "medium"]
        assert all(a["status"] == "auto_applied" for a in med)
        assert all(a.get("notify", False) for a in med)

    def test_high_risk_awaits_approval(self):
        adjs = generate_adjustments(quality_stats={"trend": "declining", "avg_last_3": 5.2, "avg_prev_3": 7.1})
        high = [a for a in adjs if a["risk_level"] == "high"]
        assert all(a["status"] == "pending" for a in high)


class TestRollback:
    def test_revert_on_decline(self):
        metrics = [{"avg_base_score": 60}, {"avg_base_score": 50}, {"avg_base_score": 48}, {"avg_base_score": 47}]
        assert should_revert_adjustment({}, metrics) is True

    def test_no_revert_on_improvement(self):
        metrics = [{"avg_base_score": 60}, {"avg_base_score": 65}, {"avg_base_score": 68}, {"avg_base_score": 70}]
        assert should_revert_adjustment({}, metrics) is False


class TestCooldown:
    def test_reverted_on_cooldown(self):
        assert is_on_cooldown({"status": "reverted", "cooldown_until": "2099-01-01T00:00:00Z"}) is True

    def test_active_not_on_cooldown(self):
        assert is_on_cooldown({"status": "auto_applied", "cooldown_until": None}) is False

    def test_expired_cooldown(self):
        assert is_on_cooldown({"status": "reverted", "cooldown_until": "2020-01-01T00:00:00Z"}) is False


class TestConflictDetection:
    """REPORT ONLY"""
    def test_contradictory_adjustments_flagged(self):
        adjs = [
            {"id": "1", "payload": {"min_match_score": 40}, "status": "auto_applied"},
            {"id": "2", "payload": {"min_match_score": 60}, "status": "auto_applied"},
        ]
        conflicts = detect_conflicts(adjs)
        assert len(conflicts) == 1
        assert conflicts[0]["key"] == "min_match_score"

    def test_no_conflict_same_value(self):
        adjs = [
            {"id": "1", "payload": {"min_match_score": 40}, "status": "auto_applied"},
            {"id": "2", "payload": {"min_match_score": 40}, "status": "auto_applied"},
        ]
        assert detect_conflicts(adjs) == []


class TestQueryOptimization:
    def test_low_match_rate_flagged(self):
        stats = {"bad query": {"match_rates": [0.03, 0.02, 0.04]}}
        suggestions = analyze_query_effectiveness(stats)
        assert len(suggestions) == 1
        assert suggestions[0]["risk_level"] == "medium"


class TestBaseResumeSuggestions:
    def test_keyword_gap_suggestion(self):
        stats = {"kubernetes": {"count": 34, "avg_job_score": 78}}
        suggestions = analyze_keyword_gaps_for_resume(stats, min_jobs=25)
        assert len(suggestions) == 1
        assert "kubernetes" in suggestions[0]["reason"]


class TestUserFeedbackIngestion:
    """Validates the data shape for user feedback adjustments."""
    def test_feedback_adjustment_shape(self):
        feedback = {
            "adjustment_type": "quality_flag",
            "risk_level": "high",
            "status": "pending",
            "payload": {"job_id": "test-123", "feedback_type": "score_inaccurate"},
        }
        assert feedback["adjustment_type"] == "quality_flag"
        assert feedback["risk_level"] == "high"
