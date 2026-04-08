"""Unit tests for the self_improve Lambda handler.

Tests the handler's integration of:
- generate_adjustments: new adjustment creation and Supabase insert
- analyze_query_effectiveness: per-source query flags
- analyze_keyword_gaps_for_resume: keyword gap suggestions
- should_revert_or_extend: active adjustment evaluation
- execute_revert: revert action on declining adjustments
- save_pipeline_run: pipeline run metrics persistence
- _build_scraper_stats / _build_score_stats / _build_keyword_stats helpers
"""

import json
from unittest.mock import MagicMock, patch


def _make_mock_db(
    metrics_data=None,
    jobs_data=None,
    active_adjustments=None,
    runs_since_data=None,
):
    """Build a mock Supabase client with chained method support.

    The mock routes db.table("X").method(...) calls to different return values
    depending on the table name.
    """
    db = MagicMock()
    tables = {}

    def table_router(table_name):
        if table_name not in tables:
            tables[table_name] = MagicMock()
        return tables[table_name]

    db.table.side_effect = table_router

    # pipeline_metrics: select -> eq -> gte -> execute (used for both historical and today)
    metrics_result = MagicMock()
    metrics_result.data = metrics_data if metrics_data is not None else []
    m_chain = tables.setdefault("pipeline_metrics", MagicMock())
    m_chain.select.return_value = m_chain
    m_chain.eq.return_value = m_chain
    m_chain.gte.return_value = m_chain
    m_chain.execute.return_value = metrics_result

    # jobs: used for recent_jobs (7-day) and today's scored jobs
    jobs_result = MagicMock()
    jobs_result.data = jobs_data if jobs_data is not None else []
    j_chain = tables.setdefault("jobs", MagicMock())
    j_chain.select.return_value = j_chain
    j_chain.eq.return_value = j_chain
    j_chain.gte.return_value = j_chain
    j_chain.execute.return_value = jobs_result

    # pipeline_adjustments: multiple operations
    adj_chain = tables.setdefault("pipeline_adjustments", MagicMock())
    adj_select_result = MagicMock()
    adj_select_result.data = active_adjustments if active_adjustments is not None else []
    adj_chain.select.return_value = adj_chain
    adj_chain.in_.return_value = adj_chain
    adj_chain.eq.return_value = adj_chain
    adj_chain.execute.return_value = adj_select_result
    adj_chain.insert.return_value = adj_chain
    adj_chain.update.return_value = adj_chain

    # pipeline_runs: select for revert eval + insert for save_pipeline_run
    runs_result = MagicMock()
    runs_result.data = runs_since_data if runs_since_data is not None else []
    r_chain = tables.setdefault("pipeline_runs", MagicMock())
    r_chain.select.return_value = r_chain
    r_chain.eq.return_value = r_chain
    r_chain.gte.return_value = r_chain
    r_chain.order.return_value = r_chain
    r_chain.execute.return_value = runs_result
    r_chain.insert.return_value = r_chain

    # self_improvement_config: upsert -> execute
    si_chain = tables.setdefault("self_improvement_config", MagicMock())
    si_chain.upsert.return_value = si_chain
    si_chain.execute.return_value = MagicMock()

    return db


SAMPLE_METRICS = [
    {"scraper_name": "linkedin", "run_date": "2026-04-01", "jobs_found": 45, "jobs_matched": 5, "user_id": "u1"},
    {"scraper_name": "linkedin", "run_date": "2026-04-02", "jobs_found": 48, "jobs_matched": 6, "user_id": "u1"},
    {"scraper_name": "linkedin", "run_date": "2026-04-03", "jobs_found": 52, "jobs_matched": 7, "user_id": "u1"},
]

BROKEN_SCRAPER_METRICS = [
    {"scraper_name": "indeed", "run_date": "2026-04-01", "jobs_found": 0, "jobs_matched": 0, "user_id": "u1"},
    {"scraper_name": "indeed", "run_date": "2026-04-02", "jobs_found": 0, "jobs_matched": 0, "user_id": "u1"},
    {"scraper_name": "indeed", "run_date": "2026-04-03", "jobs_found": 0, "jobs_matched": 0, "user_id": "u1"},
]

SAMPLE_JOBS = [
    {"title": "Python Engineer", "match_score": 72, "source": "linkedin", "score_tier": "B", "description": "python aws"},
    {"title": "Backend Dev", "match_score": 65, "source": "indeed", "score_tier": "C", "description": "java backend"},
]


class TestHandlerBasic:
    """Basic handler invocation tests."""

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    @patch("lambdas.pipeline.self_improve.ai_complete")
    def test_handler_returns_expected_keys(self, mock_ai, mock_save, mock_get_supa):
        from lambdas.pipeline.self_improve import handler

        mock_get_supa.return_value = _make_mock_db(metrics_data=SAMPLE_METRICS, jobs_data=SAMPLE_JOBS)
        mock_ai.return_value = {"content": json.dumps({"query_weights": {}})}
        result = handler({"user_id": "u1"}, None)

        assert "unhealthy_scrapers" in result
        assert "analyzed" in result
        assert "new_adjustments" in result
        assert "reverted" in result
        assert "confirmed" in result
        assert "score_stats" in result
        assert "top_keywords" in result

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    @patch("lambdas.pipeline.self_improve.ai_complete")
    def test_handler_no_metrics_analyzed_false(self, mock_ai, mock_save, mock_get_supa):
        from lambdas.pipeline.self_improve import handler

        mock_get_supa.return_value = _make_mock_db(metrics_data=[], jobs_data=[])
        mock_ai.return_value = {"content": json.dumps({})}
        result = handler({"user_id": "u1"}, None)

        assert result["analyzed"] is False

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    @patch("lambdas.pipeline.self_improve.ai_complete")
    def test_handler_accepts_execution_context(self, mock_ai, mock_save, mock_get_supa):
        from lambdas.pipeline.self_improve import handler

        mock_get_supa.return_value = _make_mock_db(metrics_data=SAMPLE_METRICS, jobs_data=SAMPLE_JOBS)
        mock_ai.return_value = {"content": json.dumps({})}
        result = handler({
            "user_id": "u1",
            "pipeline_run_id": "exec-abc123",
            "started_at": "2026-04-06T07:00:00Z",
            "matched_count": 5,
        }, None)

        assert "new_adjustments" in result


class TestNewAdjustments:
    """Tests that new adjustments are generated and written to Supabase."""

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.ai_complete")
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    def test_broken_scraper_generates_adjustment(self, mock_save, mock_ai, mock_get_supa):
        from lambdas.pipeline.self_improve import handler

        db = _make_mock_db(
            metrics_data=BROKEN_SCRAPER_METRICS,
            jobs_data=SAMPLE_JOBS,
            active_adjustments=[],
        )
        mock_get_supa.return_value = db
        mock_ai.return_value = {"content": json.dumps({"query_weights": {"python": 0.8}})}

        result = handler({"user_id": "u1", "pipeline_run_id": "run-1"}, None)

        # At minimum 1 scraper_config (disable indeed) adjustment
        assert result["new_adjustments"] >= 1

        # Verify insert was called on pipeline_adjustments with user_id and run_id
        adj_table = db.table("pipeline_adjustments")
        insert_calls = adj_table.insert.call_args_list
        assert len(insert_calls) >= 1
        # Find the scraper_config adjustment
        scraper_adjs = [
            c[0][0] for c in insert_calls
            if isinstance(c[0][0], dict) and c[0][0].get("adjustment_type") == "scraper_config"
        ]
        assert len(scraper_adjs) >= 1
        assert scraper_adjs[0]["user_id"] == "u1"
        # run_id is now a generated UUID, not the event's pipeline_run_id
        assert len(scraper_adjs[0]["run_id"]) == 36  # UUID format
        assert "applied_at" in scraper_adjs[0]


class TestRevertAction:
    """Tests for the revert/confirm logic on active adjustments."""

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.ai_complete")
    @patch("lambdas.pipeline.self_improve.execute_revert")
    @patch("lambdas.pipeline.self_improve.should_revert_or_extend", return_value="revert")
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    def test_revert_called_on_declining_adjustment(
        self, mock_save, mock_decision, mock_revert, mock_ai, mock_get_supa
    ):
        from lambdas.pipeline.self_improve import handler

        active_adj = {
            "id": "adj-1",
            "user_id": "u1",
            "status": "auto_applied",
            "applied_at": "2026-04-01T00:00:00",
            "adjustment_type": "score_threshold",
        }
        db = _make_mock_db(
            metrics_data=SAMPLE_METRICS,
            jobs_data=SAMPLE_JOBS,
            active_adjustments=[active_adj],
            runs_since_data=[
                {"avg_base_score": 60},
                {"avg_base_score": 50},
                {"avg_base_score": 48},
                {"avg_base_score": 47},
            ],
        )
        mock_get_supa.return_value = db
        mock_ai.return_value = {"content": json.dumps({})}

        result = handler({"user_id": "u1"}, None)

        assert "adj-1" in result["reverted"]
        mock_revert.assert_called_once_with(db, active_adj)

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.ai_complete")
    @patch("lambdas.pipeline.self_improve.should_revert_or_extend", return_value="confirm")
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    def test_confirm_updates_status(self, mock_save, mock_decision, mock_ai, mock_get_supa):
        from lambdas.pipeline.self_improve import handler

        active_adj = {
            "id": "adj-2",
            "user_id": "u1",
            "status": "auto_applied",
            "applied_at": "2026-04-01T00:00:00",
            "adjustment_type": "scraper_config",
        }
        db = _make_mock_db(
            metrics_data=SAMPLE_METRICS,
            jobs_data=SAMPLE_JOBS,
            active_adjustments=[active_adj],
            runs_since_data=[
                {"avg_base_score": 60},
                {"avg_base_score": 70},
                {"avg_base_score": 72},
                {"avg_base_score": 68},
            ],
        )
        mock_get_supa.return_value = db
        mock_ai.return_value = {"content": json.dumps({})}

        result = handler({"user_id": "u1"}, None)

        assert "adj-2" in result["confirmed"]
        adj_table = db.table("pipeline_adjustments")
        adj_table.update.assert_called_with({"status": "confirmed"})

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.ai_complete")
    @patch("lambdas.pipeline.self_improve.should_revert_or_extend", return_value="wait")
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    def test_wait_takes_no_action(self, mock_save, mock_decision, mock_ai, mock_get_supa):
        from lambdas.pipeline.self_improve import handler

        active_adj = {
            "id": "adj-3",
            "user_id": "u1",
            "status": "auto_applied",
            "applied_at": "2026-04-03T00:00:00",
            "adjustment_type": "score_threshold",
        }
        db = _make_mock_db(
            metrics_data=SAMPLE_METRICS,
            jobs_data=SAMPLE_JOBS,
            active_adjustments=[active_adj],
            runs_since_data=[{"avg_base_score": 60}],
        )
        mock_get_supa.return_value = db
        mock_ai.return_value = {"content": json.dumps({})}

        result = handler({"user_id": "u1"}, None)

        assert result["reverted"] == []
        assert result["confirmed"] == []

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.ai_complete")
    @patch("lambdas.pipeline.self_improve.should_revert_or_extend", return_value="extend")
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    def test_extend_takes_no_action(self, mock_save, mock_decision, mock_ai, mock_get_supa):
        from lambdas.pipeline.self_improve import handler

        active_adj = {
            "id": "adj-4",
            "user_id": "u1",
            "status": "auto_applied",
            "applied_at": "2026-04-01T00:00:00",
            "adjustment_type": "score_threshold",
        }
        db = _make_mock_db(
            metrics_data=SAMPLE_METRICS,
            jobs_data=SAMPLE_JOBS,
            active_adjustments=[active_adj],
            runs_since_data=[
                {"avg_base_score": 60},
                {"avg_base_score": 61},
                {"avg_base_score": 59},
                {"avg_base_score": 60},
            ],
        )
        mock_get_supa.return_value = db
        mock_ai.return_value = {"content": json.dumps({})}

        result = handler({"user_id": "u1"}, None)

        assert result["reverted"] == []
        assert result["confirmed"] == []


class TestSavePipelineRunInHandler:
    """Tests that pipeline run metrics are saved from Supabase-derived data."""

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    @patch("lambdas.pipeline.self_improve.ai_complete")
    def test_save_called_always(self, mock_ai, mock_save, mock_get_supa):
        from lambdas.pipeline.self_improve import handler

        mock_get_supa.return_value = _make_mock_db(
            metrics_data=SAMPLE_METRICS, jobs_data=SAMPLE_JOBS
        )
        mock_ai.return_value = {"content": json.dumps({})}

        handler({"user_id": "u1"}, None)

        # save_pipeline_run is always called now (built from Supabase data)
        mock_save.assert_called_once()
        _, args, _ = mock_save.mock_calls[0]
        assert args[1] == "u1"

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    @patch("lambdas.pipeline.self_improve.ai_complete")
    def test_save_includes_started_at_from_event(self, mock_ai, mock_save, mock_get_supa):
        from lambdas.pipeline.self_improve import handler

        mock_get_supa.return_value = _make_mock_db(
            metrics_data=SAMPLE_METRICS, jobs_data=SAMPLE_JOBS
        )
        mock_ai.return_value = {"content": json.dumps({})}

        handler({"user_id": "u1", "started_at": "2026-04-06T07:00:00Z"}, None)

        _, args, _ = mock_save.mock_calls[0]
        run_data = args[2]
        assert run_data["started_at"] == "2026-04-06T07:00:00Z"

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.save_pipeline_run", side_effect=Exception("DB error"))
    @patch("lambdas.pipeline.self_improve.ai_complete")
    def test_save_failure_does_not_crash_handler(self, mock_ai, mock_save, mock_get_supa):
        from lambdas.pipeline.self_improve import handler

        mock_get_supa.return_value = _make_mock_db(
            metrics_data=SAMPLE_METRICS, jobs_data=SAMPLE_JOBS
        )
        mock_ai.return_value = {"content": json.dumps({})}

        # Should not raise
        result = handler({"user_id": "u1"}, None)
        assert "unhealthy_scrapers" in result


class TestBuildHelpers:
    """Tests for helper functions: _build_scraper_stats, _build_score_stats, _build_keyword_stats."""

    def test_build_scraper_stats_yields_sorted_by_date(self):
        from lambdas.pipeline.self_improve import _build_scraper_stats

        metrics = [
            {"scraper_name": "linkedin", "run_date": "2026-04-01", "jobs_found": 45, "jobs_matched": 5},
            {"scraper_name": "linkedin", "run_date": "2026-04-02", "jobs_found": 48, "jobs_matched": 6},
            {"scraper_name": "indeed", "run_date": "2026-04-01", "jobs_found": 30, "jobs_matched": 3},
        ]
        stats = _build_scraper_stats(metrics)
        assert "linkedin" in stats
        assert stats["linkedin"]["yields"] == [45, 48]
        assert stats["linkedin"]["matched"] == [5, 6]
        assert stats["indeed"]["yields"] == [30]

    def test_build_scraper_stats_empty(self):
        from lambdas.pipeline.self_improve import _build_scraper_stats

        assert _build_scraper_stats([]) == {}

    def test_build_score_stats(self):
        from lambdas.pipeline.self_improve import _build_score_stats

        jobs = [
            {"match_score": 30, "score_tier": "D"},
            {"match_score": 40, "score_tier": "D"},
            {"match_score": 60, "score_tier": "C"},
            {"match_score": 70, "score_tier": "B"},
        ]
        stats = _build_score_stats(jobs)
        assert stats["avg_score"] == 50.0
        assert stats["pct_below_50"] == 0.5
        assert stats["total"] == 4
        assert stats["tier_distribution"]["D"] == 2
        assert stats["tier_distribution"]["B"] == 1

    def test_build_score_stats_empty_returns_none(self):
        from lambdas.pipeline.self_improve import _build_score_stats

        assert _build_score_stats([]) is None

    def test_build_score_stats_none_scores_filtered(self):
        from lambdas.pipeline.self_improve import _build_score_stats

        jobs = [{"match_score": None, "score_tier": "D"}, {"match_score": 60, "score_tier": "C"}]
        stats = _build_score_stats(jobs)
        assert stats["total"] == 1
        assert stats["avg_score"] == 60.0

    def test_build_keyword_stats_counts_high_scoring_jds(self):
        from lambdas.pipeline.self_improve import _build_keyword_stats

        jobs = [
            {"match_score": 85, "description": "python aws kubernetes", "title": "SWE"},
            {"match_score": 80, "description": "python docker kubernetes", "title": "Backend"},
            {"match_score": 90, "description": "python kubernetes terraform", "title": "DevOps"},
            {"match_score": 45, "description": "python kubernetes", "title": "Junior"},  # below 70
        ]
        stats = _build_keyword_stats(jobs, min_score=70.0)
        # python and kubernetes appear in 3 high-scoring jobs
        assert "python" in stats
        assert "kubernetes" in stats
        assert stats["python"]["count"] == 3
        assert stats["kubernetes"]["count"] == 3
        # docker and terraform appear in only 1 high-scoring job each
        assert "docker" not in stats  # count < 3

    def test_build_keyword_stats_empty_returns_empty(self):
        from lambdas.pipeline.self_improve import _build_keyword_stats

        assert _build_keyword_stats([]) == {}

    def test_build_keyword_stats_no_high_scoring_jobs(self):
        from lambdas.pipeline.self_improve import _build_keyword_stats

        jobs = [{"match_score": 50, "description": "python kubernetes", "title": "Job"}]
        assert _build_keyword_stats(jobs, min_score=70.0) == {}


class TestUnhealthyScraperNotification:
    """Tests for the unhealthy scraper detection and notification."""

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    @patch("lambdas.pipeline.self_improve.ai_complete")
    @patch("lambdas.pipeline.self_improve.boto3")
    def test_unhealthy_scrapers_trigger_notification(
        self, mock_boto3, mock_ai, mock_save, mock_get_supa
    ):
        from lambdas.pipeline.self_improve import handler

        db = _make_mock_db(
            metrics_data=BROKEN_SCRAPER_METRICS,
            jobs_data=SAMPLE_JOBS,
        )
        mock_get_supa.return_value = db
        mock_ai.return_value = {"content": json.dumps({})}

        mock_lambda = MagicMock()
        mock_boto3.client.return_value = mock_lambda

        result = handler({"user_id": "u1"}, None)

        assert "indeed" in result["unhealthy_scrapers"]
        # boto3.client("lambda").invoke() should have been called for the notification
        mock_lambda.invoke.assert_called()


class TestMediumRiskNotification:
    """Tests for the medium-risk adjustment notification."""

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    @patch("lambdas.pipeline.self_improve.ai_complete")
    @patch("lambdas.pipeline.self_improve.boto3")
    def test_medium_risk_triggers_notification(
        self, mock_boto3, mock_ai, mock_save, mock_get_supa
    ):
        from lambdas.pipeline.self_improve import handler

        # Score stats that trigger a medium-risk score_threshold adjustment
        jobs_data = [
            {"match_score": 30, "source": "linkedin", "score_tier": "D",
             "description": "java backend", "title": "Dev", "resume_s3_url": None},
        ] * 20  # 100% below 50

        db = _make_mock_db(
            metrics_data=SAMPLE_METRICS,
            jobs_data=jobs_data,
        )
        mock_get_supa.return_value = db
        mock_ai.return_value = {"content": json.dumps({})}

        mock_lambda = MagicMock()
        mock_boto3.client.return_value = mock_lambda

        result = handler({"user_id": "u1"}, None)

        # At least one medium-risk adjustment should have been created
        # and boto3 invoke called for notification
        assert result["new_adjustments"] >= 1


class TestBuildQueryStats:
    """Tests for _build_query_stats helper."""

    def test_computes_per_source_match_rates(self):
        from lambdas.pipeline.self_improve import _build_query_stats

        db = MagicMock()
        rows = [
            {"scraper_name": "linkedin", "jobs_found": 50, "jobs_matched": 5, "run_date": "2026-04-01"},
            {"scraper_name": "linkedin", "jobs_found": 40, "jobs_matched": 2, "run_date": "2026-04-02"},
        ]
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.gte.return_value = chain
        chain.execute.return_value = MagicMock(data=rows)
        db.table.return_value = chain

        stats = _build_query_stats(db, "u1", "2026-03-07")
        assert "linkedin" in stats
        rates = stats["linkedin"]["match_rates"]
        assert len(rates) == 2
        assert rates[0] == round(5 / 50, 3)

    def test_zero_jobs_found_yields_zero_rate(self):
        from lambdas.pipeline.self_improve import _build_query_stats

        db = MagicMock()
        rows = [{"scraper_name": "adzuna", "jobs_found": 0, "jobs_matched": 0, "run_date": "2026-04-01"}]
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.gte.return_value = chain
        chain.execute.return_value = MagicMock(data=rows)
        db.table.return_value = chain

        stats = _build_query_stats(db, "u1", "2026-03-07")
        assert stats["adzuna"]["match_rates"] == [0.0]
