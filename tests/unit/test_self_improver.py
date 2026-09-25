"""Tests for self_improver: adjustments, conflicts, rollback, cooldown, pipeline runs."""

from unittest.mock import MagicMock


from self_improver import (
    analyze_keyword_gaps_for_resume,
    analyze_query_effectiveness,
    detect_conflicts,
    execute_revert,
    generate_adjustments,
    is_on_cooldown,
    save_pipeline_run,
    should_revert_adjustment,
    should_revert_or_extend,
)


class TestGenerateAdjustments:
    """Tests for the tiered adjustment generation logic."""

    def test_generates_low_risk_scraper_disable(self):
        scraper_stats = {"glassdoor": {"yields": [0, 0, 0]}}
        adjs = generate_adjustments(scraper_stats=scraper_stats)
        low = [a for a in adjs if a["risk_level"] == "low"]
        assert len(low) == 1
        assert low[0]["status"] == "auto_applied"
        assert low[0]["adjustment_type"] == "scraper_config"
        assert low[0]["payload"]["scraper"] == "glassdoor"
        assert low[0]["payload"]["action"] == "disable"

    def test_scraper_disable_requires_3_consecutive_zeros(self):
        # Only 2 zeros -- should not trigger
        scraper_stats = {"glassdoor": {"yields": [0, 0]}}
        adjs = generate_adjustments(scraper_stats=scraper_stats)
        assert len(adjs) == 0

    def test_scraper_disable_not_triggered_if_recent_yield(self):
        # Last 3 are not all zero
        scraper_stats = {"glassdoor": {"yields": [0, 0, 5]}}
        adjs = generate_adjustments(scraper_stats=scraper_stats)
        assert len(adjs) == 0

    def test_scraper_disable_with_longer_history(self):
        # Older yields had data, but last 3 are zero
        scraper_stats = {"indeed": {"yields": [40, 35, 0, 0, 0]}}
        adjs = generate_adjustments(scraper_stats=scraper_stats)
        low = [a for a in adjs if a["risk_level"] == "low"]
        assert len(low) == 1
        assert low[0]["payload"]["scraper"] == "indeed"

    def test_multiple_broken_scrapers(self):
        scraper_stats = {
            "glassdoor": {"yields": [0, 0, 0]},
            "gradireland": {"yields": [0, 0, 0]},
            "linkedin": {"yields": [45, 48, 52]},
        }
        adjs = generate_adjustments(scraper_stats=scraper_stats)
        low = [a for a in adjs if a["risk_level"] == "low"]
        assert len(low) == 2
        disabled = {a["payload"]["scraper"] for a in low}
        assert disabled == {"glassdoor", "gradireland"}

    def test_generates_medium_risk_score_threshold(self):
        score_stats = {"pct_below_50": 0.85, "avg_score": 42}
        adjs = generate_adjustments(score_stats=score_stats)
        med = [a for a in adjs if a["risk_level"] == "medium"]
        assert len(med) == 1
        assert med[0]["status"] == "auto_applied"
        assert med[0]["notify"] is True
        assert med[0]["adjustment_type"] == "score_threshold"
        # avg_score(42) - 10 = 32, which is > 30 so should be 32
        assert med[0]["payload"]["min_match_score"] == 32

    def test_medium_risk_score_threshold_floor_at_30(self):
        score_stats = {"pct_below_50": 0.9, "avg_score": 35}
        adjs = generate_adjustments(score_stats=score_stats)
        med = [a for a in adjs if a["risk_level"] == "medium"]
        assert len(med) == 1
        # avg_score(35) - 10 = 25, but floor is 30
        assert med[0]["payload"]["min_match_score"] == 30

    def test_medium_risk_not_triggered_below_threshold(self):
        # pct_below_50 is 0.79 (just under 0.8)
        score_stats = {"pct_below_50": 0.79, "avg_score": 42}
        adjs = generate_adjustments(score_stats=score_stats)
        med = [a for a in adjs if a["risk_level"] == "medium"]
        assert len(med) == 0

    def test_generates_high_risk_prompt_change(self):
        quality_stats = {"trend": "declining", "avg_last_3": 5.2, "avg_prev_3": 7.1}
        adjs = generate_adjustments(quality_stats=quality_stats)
        high = [a for a in adjs if a["risk_level"] == "high"]
        assert len(high) == 1
        assert high[0]["status"] == "pending"
        assert high[0]["adjustment_type"] == "prompt_change"
        assert high[0]["payload"]["target"] == "tailoring_prompt"

    def test_high_risk_not_triggered_if_not_declining(self):
        quality_stats = {"trend": "stable", "avg_last_3": 7.0, "avg_prev_3": 7.1}
        adjs = generate_adjustments(quality_stats=quality_stats)
        high = [a for a in adjs if a["risk_level"] == "high"]
        assert len(high) == 0

    def test_high_risk_not_triggered_if_drop_under_10pct(self):
        # 7.0 vs 7.5 = 6.7% drop, under 10% threshold
        quality_stats = {"trend": "declining", "avg_last_3": 7.0, "avg_prev_3": 7.5}
        adjs = generate_adjustments(quality_stats=quality_stats)
        high = [a for a in adjs if a["risk_level"] == "high"]
        assert len(high) == 0

    def test_high_risk_not_triggered_if_avg_prev_zero(self):
        quality_stats = {"trend": "declining", "avg_last_3": 5.0, "avg_prev_3": 0}
        adjs = generate_adjustments(quality_stats=quality_stats)
        high = [a for a in adjs if a["risk_level"] == "high"]
        assert len(high) == 0

    def test_no_adjustments_when_healthy(self):
        adjs = generate_adjustments(
            scraper_stats={"linkedin": {"yields": [45, 48, 52]}},
            score_stats={"pct_below_50": 0.2, "avg_score": 72},
        )
        assert len(adjs) == 0

    def test_no_adjustments_when_all_none(self):
        adjs = generate_adjustments()
        assert adjs == []

    def test_generates_source_order_when_matched_data_available(self):
        # Provide both yields and matched to trigger source_order adjustment
        scraper_stats = {
            "linkedin": {"yields": [50, 60], "matched": [10, 12]},  # ~20% rate
            "adzuna": {"yields": [40, 30], "matched": [1, 1]},      # ~2.9% rate
        }
        adjs = generate_adjustments(scraper_stats=scraper_stats)
        source_order_adjs = [a for a in adjs if a["adjustment_type"] == "source_order"]
        assert len(source_order_adjs) == 1
        assert source_order_adjs[0]["risk_level"] == "low"
        assert source_order_adjs[0]["status"] == "auto_applied"
        # linkedin should be first (higher match rate)
        ranked = source_order_adjs[0]["payload"]["sources_ranked"]
        assert ranked[0] == "linkedin"

    def test_source_order_not_generated_without_matched(self):
        # No matched key means source_rates stays empty, no adjustment
        scraper_stats = {"linkedin": {"yields": [50, 60]}}
        adjs = generate_adjustments(scraper_stats=scraper_stats)
        source_order_adjs = [a for a in adjs if a["adjustment_type"] == "source_order"]
        assert len(source_order_adjs) == 0

    def test_zero_sa_tier_generates_medium_risk(self):
        # 10+ jobs, all C/D tiers, no S/A
        score_stats = {
            "pct_below_50": 0.1,  # not triggering the pct_below_50 check
            "avg_score": 65,
            "total": 15,
            "tier_distribution": {"C": 10, "D": 5},
        }
        adjs = generate_adjustments(score_stats=score_stats)
        tier_adjs = [a for a in adjs if a["adjustment_type"] == "score_threshold"]
        assert len(tier_adjs) == 1
        assert tier_adjs[0]["risk_level"] == "medium"
        assert tier_adjs[0]["notify"] is True
        assert "Zero S/A" in tier_adjs[0]["reason"]

    def test_zero_sa_tier_not_triggered_when_few_jobs(self):
        # Only 9 total jobs — below the min_total=10 guard
        score_stats = {
            "pct_below_50": 0.1,
            "avg_score": 65,
            "total": 9,
            "tier_distribution": {"C": 5, "D": 4},
        }
        adjs = generate_adjustments(score_stats=score_stats)
        tier_adjs = [a for a in adjs if a["adjustment_type"] == "score_threshold"]
        assert len(tier_adjs) == 0

    def test_zero_sa_tier_not_triggered_when_has_high_tier(self):
        score_stats = {
            "pct_below_50": 0.1,
            "avg_score": 65,
            "total": 20,
            "tier_distribution": {"S": 1, "C": 10, "D": 9},
        }
        adjs = generate_adjustments(score_stats=score_stats)
        tier_adjs = [a for a in adjs if a["adjustment_type"] == "score_threshold"]
        assert len(tier_adjs) == 0

    def test_high_risk_from_compile_fail_rate(self):
        quality_stats = {
            "trend": "declining",
            "compile_fail_rate": 0.35,
            "avg_last_3": None,
            "avg_prev_3": None,
        }
        adjs = generate_adjustments(quality_stats=quality_stats)
        high = [a for a in adjs if a["risk_level"] == "high"]
        assert len(high) == 1
        assert "compilation failure" in high[0]["reason"].lower()

    def test_combined_low_and_medium(self):
        adjs = generate_adjustments(
            scraper_stats={"glassdoor": {"yields": [0, 0, 0]}},
            score_stats={"pct_below_50": 0.85, "avg_score": 42},
        )
        assert len(adjs) == 2
        risk_levels = {a["risk_level"] for a in adjs}
        assert risk_levels == {"low", "medium"}

    def test_all_three_tiers(self):
        adjs = generate_adjustments(
            scraper_stats={"glassdoor": {"yields": [0, 0, 0]}},
            score_stats={"pct_below_50": 0.85, "avg_score": 42},
            quality_stats={"trend": "declining", "avg_last_3": 5.0, "avg_prev_3": 7.5},
        )
        assert len(adjs) == 3
        risk_levels = {a["risk_level"] for a in adjs}
        assert risk_levels == {"low", "medium", "high"}

    def test_adjustment_has_evidence(self):
        scraper_stats = {"glassdoor": {"yields": [0, 0, 0]}}
        adjs = generate_adjustments(scraper_stats=scraper_stats)
        assert "evidence" in adjs[0]
        assert adjs[0]["evidence"]["yields"] == [0, 0, 0]

    def test_adjustment_has_reason(self):
        scraper_stats = {"glassdoor": {"yields": [0, 0, 0]}}
        adjs = generate_adjustments(scraper_stats=scraper_stats)
        assert "glassdoor" in adjs[0]["reason"]


class TestDetectConflicts:
    """Tests for the conflict detection logic."""

    def test_detect_conflicts_same_key_different_values(self):
        adjs = [
            {"id": "1", "payload": {"min_match_score": 40}, "status": "auto_applied"},
            {"id": "2", "payload": {"min_match_score": 60}, "status": "auto_applied"},
        ]
        conflicts = detect_conflicts(adjs)
        assert len(conflicts) == 1
        assert conflicts[0]["key"] == "min_match_score"
        assert conflicts[0]["value_a"] == 40
        assert conflicts[0]["value_b"] == 60

    def test_no_conflict_same_values(self):
        adjs = [
            {"id": "1", "payload": {"min_match_score": 40}, "status": "auto_applied"},
            {"id": "2", "payload": {"min_match_score": 40}, "status": "auto_applied"},
        ]
        conflicts = detect_conflicts(adjs)
        assert len(conflicts) == 0

    def test_ignores_pending_adjustments(self):
        adjs = [
            {"id": "1", "payload": {"min_match_score": 40}, "status": "auto_applied"},
            {"id": "2", "payload": {"min_match_score": 60}, "status": "pending"},
        ]
        conflicts = detect_conflicts(adjs)
        assert len(conflicts) == 0

    def test_considers_approved_adjustments(self):
        adjs = [
            {"id": "1", "payload": {"min_match_score": 40}, "status": "approved"},
            {"id": "2", "payload": {"min_match_score": 60}, "status": "auto_applied"},
        ]
        conflicts = detect_conflicts(adjs)
        assert len(conflicts) == 1

    def test_no_conflicts_empty_list(self):
        conflicts = detect_conflicts([])
        assert conflicts == []

    def test_no_conflicts_single_adjustment(self):
        adjs = [
            {"id": "1", "payload": {"min_match_score": 40}, "status": "auto_applied"},
        ]
        conflicts = detect_conflicts(adjs)
        assert len(conflicts) == 0

    def test_no_conflicts_different_keys(self):
        adjs = [
            {"id": "1", "payload": {"min_match_score": 40}, "status": "auto_applied"},
            {"id": "2", "payload": {"max_retries": 5}, "status": "auto_applied"},
        ]
        conflicts = detect_conflicts(adjs)
        assert len(conflicts) == 0

    def test_missing_id_defaults_to_new(self):
        adjs = [
            {"id": "1", "payload": {"min_match_score": 40}, "status": "auto_applied"},
            {"payload": {"min_match_score": 60}, "status": "auto_applied"},
        ]
        conflicts = detect_conflicts(adjs)
        assert len(conflicts) == 1
        assert conflicts[0]["adjustment_b"] == "new"

    def test_none_payload_handled(self):
        adjs = [
            {"id": "1", "payload": None, "status": "auto_applied"},
            {"id": "2", "payload": {"min_match_score": 60}, "status": "auto_applied"},
        ]
        conflicts = detect_conflicts(adjs)
        assert len(conflicts) == 0


class TestAnalyzeKeywordGapsForResume:
    """Tests for base resume improvement suggestions from keyword gaps."""

    def test_base_resume_suggestion_from_keyword_gaps(self):
        keyword_stats = {
            "kubernetes": {"count": 34, "avg_job_score": 78},
            "graphql": {"count": 28, "avg_job_score": 72},
            "react": {"count": 12, "avg_job_score": 65},
        }
        suggestions = analyze_keyword_gaps_for_resume(keyword_stats, min_jobs=25)
        assert len(suggestions) == 2  # kubernetes + graphql
        assert suggestions[0]["evidence"]["count"] == 34  # sorted by count desc
        assert "kubernetes" in suggestions[0]["reason"]

    def test_no_suggestions_when_all_below_threshold(self):
        stats = {"python": {"count": 10, "avg_job_score": 80}}
        assert analyze_keyword_gaps_for_resume(stats, min_jobs=25) == []

    def test_suggestion_fields(self):
        stats = {"docker": {"count": 30, "avg_job_score": 75}}
        suggestions = analyze_keyword_gaps_for_resume(stats, min_jobs=25)
        assert suggestions[0]["risk_level"] == "medium"
        assert suggestions[0]["payload"]["action"] == "add_to_base_resume"
        assert suggestions[0]["notify"] is True

    def test_empty_keyword_stats(self):
        assert analyze_keyword_gaps_for_resume({}, min_jobs=25) == []

    def test_exact_threshold_included(self):
        stats = {"kafka": {"count": 25, "avg_job_score": 70}}
        suggestions = analyze_keyword_gaps_for_resume(stats, min_jobs=25)
        assert len(suggestions) == 1
        assert suggestions[0]["payload"]["keyword"] == "kafka"

    def test_adjustment_type_is_quality_flag(self):
        stats = {"terraform": {"count": 40, "avg_job_score": 82}}
        suggestions = analyze_keyword_gaps_for_resume(stats, min_jobs=25)
        assert suggestions[0]["adjustment_type"] == "quality_flag"
        assert suggestions[0]["status"] == "auto_applied"


class TestAnalyzeQueryEffectiveness:
    """Tests for query effectiveness analysis."""

    def test_low_match_rate_query_flagged(self):
        query_stats = {
            "python backend dublin": {"match_rates": [0.03, 0.02, 0.04]},
            "software engineer dublin": {"match_rates": [0.35, 0.40, 0.38]},
        }
        suggestions = analyze_query_effectiveness(query_stats)
        flagged = [s for s in suggestions if "python backend" in s["reason"]]
        assert len(flagged) == 1
        assert flagged[0]["risk_level"] == "medium"

    def test_healthy_queries_not_flagged(self):
        query_stats = {"good query": {"match_rates": [0.20, 0.25, 0.18]}}
        assert analyze_query_effectiveness(query_stats) == []

    def test_not_enough_runs(self):
        query_stats = {"new query": {"match_rates": [0.01, 0.02]}}
        assert analyze_query_effectiveness(query_stats) == []  # Only 2 runs, need 3


class TestShouldRevertAdjustment:
    """Tests for the rollback decision logic."""

    def test_should_revert_on_decline(self):
        adj = {"id": "1", "payload": {}, "previous_state": {}}
        metrics = [
            {"avg_base_score": 60},
            {"avg_base_score": 50},
            {"avg_base_score": 48},
            {"avg_base_score": 47},
        ]
        assert should_revert_adjustment(adj, metrics) is True

    def test_no_revert_on_improvement(self):
        adj = {"id": "1"}
        metrics = [
            {"avg_base_score": 60},
            {"avg_base_score": 65},
            {"avg_base_score": 68},
            {"avg_base_score": 70},
        ]
        assert should_revert_adjustment(adj, metrics) is False

    def test_no_revert_with_insufficient_data(self):
        adj = {"id": "1"}
        metrics = [{"avg_base_score": 60}, {"avg_base_score": 50}]
        assert should_revert_adjustment(adj, metrics) is False

    def test_no_revert_when_baseline_is_zero(self):
        adj = {"id": "1"}
        metrics = [
            {"avg_base_score": 0},
            {"avg_base_score": 50},
            {"avg_base_score": 48},
            {"avg_base_score": 47},
        ]
        assert should_revert_adjustment(adj, metrics) is False

    def test_no_revert_when_decline_within_threshold(self):
        adj = {"id": "1"}
        # 60 -> avg 58 = -3.3% decline, under 5% threshold
        metrics = [
            {"avg_base_score": 60},
            {"avg_base_score": 59},
            {"avg_base_score": 58},
            {"avg_base_score": 57},
        ]
        assert should_revert_adjustment(adj, metrics) is False

    def test_custom_threshold(self):
        adj = {"id": "1"}
        # 60 -> avg 58 = -3.3% decline, now using 3% threshold
        metrics = [
            {"avg_base_score": 60},
            {"avg_base_score": 59},
            {"avg_base_score": 58},
            {"avg_base_score": 57},
        ]
        assert should_revert_adjustment(adj, metrics, threshold=0.03) is True


class TestShouldRevertOrExtend:
    """Tests for the extended revert/confirm/extend evaluation."""

    def test_wait_with_insufficient_data(self):
        assert should_revert_or_extend({}, [{"avg_base_score": 60}]) == "wait"

    def test_wait_when_baseline_is_zero(self):
        metrics = [
            {"avg_base_score": 0},
            {"avg_base_score": 50},
            {"avg_base_score": 48},
            {"avg_base_score": 47},
        ]
        assert should_revert_or_extend({}, metrics) == "wait"

    def test_confirm_on_improvement(self):
        metrics = [
            {"avg_base_score": 60},
            {"avg_base_score": 70},
            {"avg_base_score": 72},
            {"avg_base_score": 68},
        ]
        assert should_revert_or_extend({}, metrics) == "confirm"

    def test_revert_on_decline(self):
        metrics = [
            {"avg_base_score": 60},
            {"avg_base_score": 50},
            {"avg_base_score": 48},
            {"avg_base_score": 47},
        ]
        assert should_revert_or_extend({}, metrics) == "revert"

    def test_inconclusive_extends(self):
        # avg of 61, 59, 60 = 60, change = 0%, within threshold
        metrics = [
            {"avg_base_score": 60},
            {"avg_base_score": 61},
            {"avg_base_score": 59},
            {"avg_base_score": 60},
        ]
        assert should_revert_or_extend({}, metrics) == "extend"

    def test_inconclusive_with_6_runs_confirms(self):
        # 3-run avg = 60, inconclusive. 5-run avg = 60.4, still within threshold -> confirm
        metrics = [
            {"avg_base_score": 60},
            {"avg_base_score": 61},
            {"avg_base_score": 59},
            {"avg_base_score": 60},
            {"avg_base_score": 61},
            {"avg_base_score": 61},
        ]
        assert should_revert_or_extend({}, metrics) == "confirm"

    def test_inconclusive_with_6_runs_reverts(self):
        # 3-run avg ~59.67 (inconclusive). 5-run avg = 56 -> -6.7% -> revert
        metrics = [
            {"avg_base_score": 60},
            {"avg_base_score": 61},
            {"avg_base_score": 59},
            {"avg_base_score": 59},
            {"avg_base_score": 52},
            {"avg_base_score": 49},
        ]
        assert should_revert_or_extend({}, metrics) == "revert"


class TestShouldRevertOrExtendNoneHandling:
    """Regression tests for the production crash confirmed via CloudWatch:

    TypeError: unsupported operand type(s) for +: 'int' and 'NoneType'
      File "self_improver.py", line 198, in should_revert_or_extend
        after_3 = sum(r.get("avg_base_score", 0) for r in run_metrics[1:4]) / 3

    `avg_base_score` is None (not 0) whenever a run scored zero jobs (see
    self_improve.py:_build_current_run_stats: `avg_base = ... if scores
    else None`), which is a real and recurring condition, not a rare edge
    case. `.get("avg_base_score", 0)` only substitutes the default for a
    *missing* key, not an explicit `None` value, so `sum()` received a
    `None` and crashed trying to add it to the running int total.
    """

    def test_none_in_3_run_window_does_not_crash(self):
        # Confirmed shape: pipeline_runs.avg_base_score can be NULL.
        metrics = [
            {"avg_base_score": 60},
            {"avg_base_score": None},
            {"avg_base_score": 61},
            {"avg_base_score": 62},
        ]
        # Should not raise; None is excluded, avg of [61, 62] = 61.5, a
        # ~2.5% improvement — within the default 5% threshold, so neither
        # revert nor confirm at the 3-run stage, and no 6th run available.
        assert should_revert_or_extend({}, metrics) == "extend"

    def test_none_baseline_returns_wait_not_crash(self):
        metrics = [
            {"avg_base_score": None},
            {"avg_base_score": 50},
            {"avg_base_score": 48},
            {"avg_base_score": 47},
        ]
        assert should_revert_or_extend({}, metrics) == "wait"

    def test_all_none_in_3_run_window_extends_without_6th_run(self):
        metrics = [
            {"avg_base_score": 60},
            {"avg_base_score": None},
            {"avg_base_score": None},
            {"avg_base_score": None},
        ]
        assert should_revert_or_extend({}, metrics) == "extend"

    def test_none_3_run_window_falls_through_to_usable_5_run_window(self):
        # 3-run window (indices 1-3) is entirely None -> inconclusive.
        # 5-run window (indices 1-5) has 2 usable values: 30, 28 -> avg 29,
        # a decline from baseline 60 of over 50% -> revert.
        metrics = [
            {"avg_base_score": 60},
            {"avg_base_score": None},
            {"avg_base_score": None},
            {"avg_base_score": None},
            {"avg_base_score": 30},
            {"avg_base_score": 28},
        ]
        assert should_revert_or_extend({}, metrics) == "revert"

    def test_none_still_detects_real_decline(self):
        # A None mixed into an otherwise-declining window must not mask
        # the decline by pulling the average toward zero, nor crash.
        metrics = [
            {"avg_base_score": 60},
            {"avg_base_score": None},
            {"avg_base_score": 40},
            {"avg_base_score": 38},
        ]
        # avg of [40, 38] = 39, change = (39-60)/60 = -35% -> revert
        assert should_revert_or_extend({}, metrics) == "revert"


class TestShouldRevertAdjustmentNoneHandling:
    """Same None-safety regression, for the older should_revert_adjustment
    (identical `.get(key, 0)`-on-a-possibly-None-value bug pattern)."""

    def test_none_in_window_does_not_crash(self):
        metrics = [
            {"avg_base_score": 60},
            {"avg_base_score": None},
            {"avg_base_score": 50},
            {"avg_base_score": 48},
        ]
        # avg of [50, 48] = 49, change = (49-60)/60 = -18.3% -> True
        assert should_revert_adjustment({}, metrics) is True

    def test_none_baseline_returns_false_not_crash(self):
        metrics = [
            {"avg_base_score": None},
            {"avg_base_score": 50},
            {"avg_base_score": 48},
            {"avg_base_score": 47},
        ]
        assert should_revert_adjustment({}, metrics) is False

    def test_all_none_window_returns_false_not_crash(self):
        metrics = [
            {"avg_base_score": 60},
            {"avg_base_score": None},
            {"avg_base_score": None},
            {"avg_base_score": None},
        ]
        assert should_revert_adjustment({}, metrics) is False


class TestIsOnCooldown:
    """Tests for the cooldown check logic."""

    def test_on_cooldown_future_date(self):
        assert is_on_cooldown({"status": "reverted", "cooldown_until": "2099-01-01T00:00:00Z"}) is True

    def test_not_on_cooldown_if_not_reverted(self):
        assert is_on_cooldown({"status": "auto_applied"}) is False

    def test_cooldown_expired(self):
        assert is_on_cooldown({"status": "reverted", "cooldown_until": "2020-01-01T00:00:00Z"}) is False

    def test_no_cooldown_until_field(self):
        assert is_on_cooldown({"status": "reverted"}) is False

    def test_approved_status_not_on_cooldown(self):
        assert is_on_cooldown({"status": "approved", "cooldown_until": "2099-01-01T00:00:00Z"}) is False


class TestExecuteRevert:
    """Tests for the execute_revert function with mocked Supabase client."""

    def _make_mock_db(self):
        """Create a mock Supabase client with chained method support."""
        db = MagicMock()
        # Chain: db.table(...).update(...).eq(...).execute()
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = None
        # Chain: db.table(...).insert(...).execute()
        db.table.return_value.insert.return_value.execute.return_value = None
        return db

    def test_marks_adjustment_as_reverted(self):
        db = self._make_mock_db()
        adj = {"id": "adj-1", "user_id": "user-1", "adjustment_type": "scraper_config"}
        execute_revert(db, adj)

        # Verify update was called on pipeline_adjustments
        db.table.assert_any_call("pipeline_adjustments")
        update_call = db.table.return_value.update.call_args
        assert update_call[0][0]["status"] == "reverted"
        assert "reverted_at" in update_call[0][0]
        assert "cooldown_until" in update_call[0][0]

    def test_inserts_rollback_when_previous_state_exists(self):
        db = self._make_mock_db()
        adj = {
            "id": "adj-1",
            "user_id": "user-1",
            "adjustment_type": "score_threshold",
            "previous_state": {"min_match_score": 50},
        }
        execute_revert(db, adj)

        # Should have both an update (revert) and an insert (rollback)
        insert_call = db.table.return_value.insert.call_args
        assert insert_call is not None
        inserted = insert_call[0][0]
        assert inserted["payload"] == {"min_match_score": 50}
        assert inserted["status"] == "auto_applied"
        assert inserted["risk_level"] == "low"
        assert "adj-1" in inserted["reason"]

    def test_no_insert_without_previous_state(self):
        db = self._make_mock_db()
        adj = {"id": "adj-1", "user_id": "user-1", "adjustment_type": "scraper_config"}
        execute_revert(db, adj)

        # update should be called, but insert should NOT
        db.table.return_value.update.assert_called_once()
        db.table.return_value.insert.assert_not_called()


class TestSavePipelineRun:
    """Tests for the save_pipeline_run function with mocked Supabase client."""

    def test_inserts_run_data(self):
        db = MagicMock()
        db.table.return_value.insert.return_value.execute.return_value = None

        run_data = {
            "started_at": "2026-04-03T07:00:00",
            "jobs_scraped": 120,
            "jobs_new": 80,
            "jobs_scored": 80,
            "jobs_matched": 15,
            "jobs_tailored": 10,
            "avg_base_score": 65.5,
            "avg_final_score": 82.3,
            "avg_writing_quality": 7.2,
            "active_adjustments": ["adj-1"],
            "scraper_stats": {"linkedin": {"count": 45}},
            "model_stats": {"groq/llama3": {"avg_score": 72}},
        }
        save_pipeline_run(db, "user-1", run_data)

        db.table.assert_called_with("pipeline_runs")
        insert_call = db.table.return_value.insert.call_args[0][0]
        assert insert_call["user_id"] == "user-1"
        assert insert_call["jobs_scraped"] == 120
        assert insert_call["avg_base_score"] == 65.5
        assert insert_call["status"] == "completed"
        assert "completed_at" in insert_call

    def test_defaults_for_missing_fields(self):
        db = MagicMock()
        db.table.return_value.insert.return_value.execute.return_value = None

        save_pipeline_run(db, "user-1", {})

        insert_call = db.table.return_value.insert.call_args[0][0]
        assert insert_call["jobs_scraped"] == 0
        assert insert_call["jobs_new"] == 0
        assert insert_call["jobs_scored"] == 0
        assert insert_call["jobs_matched"] == 0
        assert insert_call["jobs_tailored"] == 0
        assert insert_call["avg_base_score"] is None
        assert insert_call["started_at"] is None
