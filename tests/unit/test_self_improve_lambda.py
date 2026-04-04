"""Unit tests for the self_improve Lambda handler.

Tests the handler's integration of:
- generate_adjustments: new adjustment creation and Supabase insert
- should_revert_or_extend: active adjustment evaluation
- execute_revert: revert action on declining adjustments
- save_pipeline_run: pipeline run metrics persistence
"""

import json
import sys
from unittest.mock import MagicMock, patch, call

import pytest


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

    # pipeline_metrics: select -> eq -> gte -> execute
    metrics_result = MagicMock()
    metrics_result.data = metrics_data if metrics_data is not None else []
    m_chain = tables.setdefault("pipeline_metrics", MagicMock())
    m_chain.select.return_value = m_chain
    m_chain.eq.return_value = m_chain
    m_chain.gte.return_value = m_chain
    m_chain.execute.return_value = metrics_result

    # jobs: select -> eq -> gte -> execute
    jobs_result = MagicMock()
    jobs_result.data = jobs_data if jobs_data is not None else []
    j_chain = tables.setdefault("jobs", MagicMock())
    j_chain.select.return_value = j_chain
    j_chain.eq.return_value = j_chain
    j_chain.gte.return_value = j_chain
    j_chain.execute.return_value = jobs_result

    # pipeline_adjustments: multiple operations
    adj_chain = tables.setdefault("pipeline_adjustments", MagicMock())
    # select -> in_ -> eq -> execute (for active query)
    adj_select_result = MagicMock()
    adj_select_result.data = active_adjustments if active_adjustments is not None else []
    adj_chain.select.return_value = adj_chain
    adj_chain.in_.return_value = adj_chain
    adj_chain.eq.return_value = adj_chain
    adj_chain.execute.return_value = adj_select_result
    # insert -> execute
    adj_chain.insert.return_value = adj_chain
    # update -> eq -> execute
    adj_chain.update.return_value = adj_chain

    # pipeline_runs: select -> eq -> gte -> order -> execute
    runs_result = MagicMock()
    runs_result.data = runs_since_data if runs_since_data is not None else []
    r_chain = tables.setdefault("pipeline_runs", MagicMock())
    r_chain.select.return_value = r_chain
    r_chain.eq.return_value = r_chain
    r_chain.gte.return_value = r_chain
    r_chain.order.return_value = r_chain
    r_chain.execute.return_value = runs_result
    # insert -> execute (for save_pipeline_run)
    r_chain.insert.return_value = r_chain

    # self_improvement_config: upsert -> execute
    si_chain = tables.setdefault("self_improvement_config", MagicMock())
    si_chain.upsert.return_value = si_chain
    si_chain.execute.return_value = MagicMock()

    return db


SAMPLE_METRICS = [
    {"scraper_name": "linkedin", "run_date": "2026-04-01", "jobs_found": 45, "user_id": "u1"},
    {"scraper_name": "linkedin", "run_date": "2026-04-02", "jobs_found": 48, "user_id": "u1"},
    {"scraper_name": "linkedin", "run_date": "2026-04-03", "jobs_found": 52, "user_id": "u1"},
]

BROKEN_SCRAPER_METRICS = [
    {"scraper_name": "glassdoor", "run_date": "2026-04-01", "jobs_found": 0, "user_id": "u1"},
    {"scraper_name": "glassdoor", "run_date": "2026-04-02", "jobs_found": 0, "user_id": "u1"},
    {"scraper_name": "glassdoor", "run_date": "2026-04-03", "jobs_found": 0, "user_id": "u1"},
]

SAMPLE_JOBS = [
    {"title": "Python Engineer", "match_score": 72, "source": "linkedin"},
    {"title": "Backend Dev", "match_score": 65, "source": "indeed"},
]


class TestHandlerBasic:
    """Basic handler invocation tests."""

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.generate_adjustments", return_value=[])
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    def test_handler_returns_expected_keys(self, mock_save, mock_gen, mock_get_supa):
        from lambdas.pipeline.self_improve import handler

        mock_get_supa.return_value = _make_mock_db(metrics_data=SAMPLE_METRICS, jobs_data=SAMPLE_JOBS)
        result = handler({"user_id": "u1"}, None)

        assert "unhealthy_scrapers" in result
        assert "analyzed" in result
        assert "new_adjustments" in result
        assert "reverted" in result
        assert "confirmed" in result

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.generate_adjustments", return_value=[])
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    def test_handler_no_metrics_no_analysis(self, mock_save, mock_gen, mock_get_supa):
        from lambdas.pipeline.self_improve import handler

        mock_get_supa.return_value = _make_mock_db(metrics_data=[], jobs_data=[])
        result = handler({"user_id": "u1"}, None)

        assert result["analyzed"] is False
        assert result["new_adjustments"] == 0
        mock_gen.assert_not_called()


class TestNewAdjustments:
    """Tests that new adjustments are generated and written to Supabase."""

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.ai_complete")
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    def test_new_adjustments_inserted(self, mock_save, mock_ai, mock_get_supa):
        from lambdas.pipeline.self_improve import handler

        db = _make_mock_db(
            metrics_data=BROKEN_SCRAPER_METRICS,
            jobs_data=SAMPLE_JOBS,
            active_adjustments=[],
        )
        mock_get_supa.return_value = db
        mock_ai.return_value = {"content": json.dumps({"query_weights": {"python": 0.8}})}

        result = handler({"user_id": "u1", "pipeline_run_id": "run-1"}, None)

        assert result["new_adjustments"] == 1  # broken glassdoor scraper
        # Verify insert was called on pipeline_adjustments
        adj_table = db.table("pipeline_adjustments")
        insert_calls = adj_table.insert.call_args_list
        assert len(insert_calls) >= 1
        inserted = insert_calls[0][0][0]
        assert inserted["user_id"] == "u1"
        assert inserted["run_id"] == "run-1"
        assert "applied_at" in inserted

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.ai_complete")
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    def test_no_adjustments_for_healthy_pipeline(self, mock_save, mock_ai, mock_get_supa):
        from lambdas.pipeline.self_improve import handler

        db = _make_mock_db(
            metrics_data=SAMPLE_METRICS,
            jobs_data=SAMPLE_JOBS,
            active_adjustments=[],
        )
        mock_get_supa.return_value = db
        mock_ai.return_value = {"content": json.dumps({"query_weights": {"python": 0.8}})}

        result = handler({"user_id": "u1"}, None)

        assert result["new_adjustments"] == 0


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
        # Verify update was called with confirmed status
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
    """Tests that pipeline run metrics are saved at the end."""

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.generate_adjustments", return_value=[])
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    def test_save_called_when_run_data_provided(self, mock_save, mock_gen, mock_get_supa):
        from lambdas.pipeline.self_improve import handler

        db = _make_mock_db(metrics_data=SAMPLE_METRICS, jobs_data=SAMPLE_JOBS)
        mock_get_supa.return_value = db
        run_data = {"started_at": "2026-04-03T07:00:00", "jobs_scraped": 100}

        handler({"user_id": "u1", "run_data": run_data}, None)

        mock_save.assert_called_once_with(db, "u1", run_data)

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.generate_adjustments", return_value=[])
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    def test_save_not_called_without_run_data(self, mock_save, mock_gen, mock_get_supa):
        from lambdas.pipeline.self_improve import handler

        mock_get_supa.return_value = _make_mock_db(metrics_data=[], jobs_data=[])

        handler({"user_id": "u1"}, None)

        mock_save.assert_not_called()

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.generate_adjustments", return_value=[])
    @patch("lambdas.pipeline.self_improve.save_pipeline_run", side_effect=Exception("DB error"))
    def test_save_failure_does_not_crash_handler(self, mock_save, mock_gen, mock_get_supa):
        from lambdas.pipeline.self_improve import handler

        mock_get_supa.return_value = _make_mock_db(metrics_data=SAMPLE_METRICS, jobs_data=SAMPLE_JOBS)
        run_data = {"started_at": "2026-04-03T07:00:00", "jobs_scraped": 100}

        # Should not raise
        result = handler({"user_id": "u1", "run_data": run_data}, None)
        assert "unhealthy_scrapers" in result


class TestBuildHelpers:
    """Tests for _build_scraper_stats and _build_score_stats."""

    def test_build_scraper_stats(self):
        from lambdas.pipeline.self_improve import _build_scraper_stats

        metrics = [
            {"scraper_name": "linkedin", "run_date": "2026-04-01", "jobs_found": 45},
            {"scraper_name": "linkedin", "run_date": "2026-04-02", "jobs_found": 48},
            {"scraper_name": "indeed", "run_date": "2026-04-01", "jobs_found": 30},
        ]
        stats = _build_scraper_stats(metrics)
        assert "linkedin" in stats
        assert stats["linkedin"]["yields"] == [45, 48]
        assert stats["indeed"]["yields"] == [30]

    def test_build_score_stats(self):
        from lambdas.pipeline.self_improve import _build_score_stats

        jobs = [
            {"match_score": 30},
            {"match_score": 40},
            {"match_score": 60},
            {"match_score": 70},
        ]
        stats = _build_score_stats(jobs)
        assert stats["avg_score"] == 50.0
        assert stats["pct_below_50"] == 0.5
        assert stats["total"] == 4

    def test_build_score_stats_empty(self):
        from lambdas.pipeline.self_improve import _build_score_stats

        assert _build_score_stats([]) is None

    def test_build_score_stats_none_scores_filtered(self):
        from lambdas.pipeline.self_improve import _build_score_stats

        jobs = [
            {"match_score": None},
            {"match_score": 60},
        ]
        stats = _build_score_stats(jobs)
        assert stats["total"] == 1
        assert stats["avg_score"] == 60.0


class TestUnhealthyScraperNotification:
    """Tests for the unhealthy scraper detection and notification."""

    @patch("lambdas.pipeline.self_improve.get_supabase")
    @patch("lambdas.pipeline.self_improve.generate_adjustments", return_value=[])
    @patch("lambdas.pipeline.self_improve.save_pipeline_run")
    @patch("lambdas.pipeline.self_improve.boto3")
    def test_unhealthy_scrapers_trigger_notification(
        self, mock_boto3, mock_save, mock_gen, mock_get_supa
    ):
        from lambdas.pipeline.self_improve import handler

        db = _make_mock_db(
            metrics_data=BROKEN_SCRAPER_METRICS,
            jobs_data=SAMPLE_JOBS,
        )
        mock_get_supa.return_value = db

        mock_lambda = MagicMock()
        mock_boto3.client.return_value = mock_lambda

        result = handler({"user_id": "u1"}, None)

        assert "glassdoor" in result["unhealthy_scrapers"]
        mock_lambda.invoke.assert_called_once()
