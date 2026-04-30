"""Unit tests for save_metrics Lambda — Phase B.5 ArtifactsCompiled metric.

Background — Phase B.5 silent-success fix: before this change, save_metrics
only wrote DB rows and `runs.resumes_generated` was hardcoded to 0. The
"daily pipeline ran but produced 0 PDFs" pattern (today's empty-tectonic-
layer bug) was completely invisible.

These tests pin the new behavior:
- _count_compiled_artifacts handles missing/empty/null processed_jobs
- The handler emits the right number of CloudWatch metric data points
- runs.resumes_generated reflects the actual count (no longer hardcoded)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lambdas" / "pipeline"))


def _make_supabase():
    mock = MagicMock()
    chain = MagicMock()
    chain.insert.return_value = chain
    chain.execute.return_value = MagicMock()
    mock.table.return_value = chain
    return mock


class TestCountCompiledArtifacts:
    def test_empty_list_returns_zero(self):
        from save_metrics import _count_compiled_artifacts
        assert _count_compiled_artifacts([]) == {"resumes": 0, "cover_letters": 0}

    def test_none_returns_zero(self):
        from save_metrics import _count_compiled_artifacts
        assert _count_compiled_artifacts(None) == {"resumes": 0, "cover_letters": 0}

    def test_resume_compiled_counts(self):
        from save_metrics import _count_compiled_artifacts
        jobs = [
            {"compile_result": {"pdf_s3_key": "a.pdf"}},
            {"compile_result": {"pdf_s3_key": "b.pdf"}},
            {"compile_result": {"error": "tectonic_not_available", "pdf_s3_key": None}},
        ]
        assert _count_compiled_artifacts(jobs)["resumes"] == 2

    def test_cover_letter_counted_independently(self):
        from save_metrics import _count_compiled_artifacts
        jobs = [
            {
                "compile_result": {"pdf_s3_key": "a.pdf"},
                "cover_compile_result": {"pdf_s3_key": "a-cl.pdf"},
            },
            {
                "compile_result": {"pdf_s3_key": "b.pdf"},
                "cover_compile_result": {"error": "compilation_failed"},
            },
        ]
        result = _count_compiled_artifacts(jobs)
        assert result["resumes"] == 2
        assert result["cover_letters"] == 1

    def test_skips_non_dict_entries(self):
        from save_metrics import _count_compiled_artifacts
        jobs = [None, "garbage", {"compile_result": {"pdf_s3_key": "ok.pdf"}}]
        assert _count_compiled_artifacts(jobs)["resumes"] == 1


class TestHandler:
    def _event(self, processed_jobs=None):
        return {
            "user_id": "user-1",
            "scraper_results": [{"source": "linkedin", "count": 100}],
            "score_result": {"matched_count": 5},
            "dedup_result": {"total_new": 80},
            "processed_jobs": processed_jobs or [],
        }

    def test_handler_emits_cloudwatch_metrics(self):
        sb = _make_supabase()
        cw = MagicMock()
        with patch("save_metrics.get_supabase", return_value=sb), \
             patch("save_metrics.cloudwatch", cw):
            import save_metrics
            save_metrics.handler(self._event(processed_jobs=[
                {"compile_result": {"pdf_s3_key": "a.pdf"},
                 "cover_compile_result": {"pdf_s3_key": "a-cl.pdf"}},
                {"compile_result": {"pdf_s3_key": "b.pdf"}},
            ]), None)

        cw.put_metric_data.assert_called_once()
        call = cw.put_metric_data.call_args.kwargs
        assert call["Namespace"] == "Naukribaba/Pipeline"
        names = [m["MetricName"] for m in call["MetricData"]]
        assert "PipelineRun" in names
        assert "JobsMatched" in names
        assert "ArtifactsCompiled" in names

        artifact_metrics = [m for m in call["MetricData"] if m["MetricName"] == "ArtifactsCompiled"]
        resume_metric = next(m for m in artifact_metrics
                             if any(d["Value"] == "resume" for d in m.get("Dimensions", [])))
        cl_metric = next(m for m in artifact_metrics
                         if any(d["Value"] == "cover_letter" for d in m.get("Dimensions", [])))
        assert resume_metric["Value"] == 2
        assert cl_metric["Value"] == 1

    def test_handler_writes_resumes_generated_count_to_runs(self):
        sb = _make_supabase()
        with patch("save_metrics.get_supabase", return_value=sb), \
             patch("save_metrics.cloudwatch", MagicMock()):
            import save_metrics
            save_metrics.handler(self._event(processed_jobs=[
                {"compile_result": {"pdf_s3_key": "a.pdf"}},
                {"compile_result": {"pdf_s3_key": "b.pdf"}},
                {"compile_result": {"pdf_s3_key": "c.pdf"}},
            ]), None)

        # runs.insert was called with resumes_generated=3 (no longer hardcoded 0)
        runs_inserts = [c for c in sb.table.return_value.insert.call_args_list
                        if c[0] and isinstance(c[0][0], dict) and "raw_jobs" in c[0][0]]
        assert len(runs_inserts) == 1
        assert runs_inserts[0][0][0]["resumes_generated"] == 3

    def test_handler_emits_zero_when_no_artifacts(self):
        """The whole point of B.5 — a run with 0 PDFs must emit a 0 to
        the CW metric, otherwise the alarm sees 'missing data' and may
        not fire (depending on TreatMissingData)."""
        sb = _make_supabase()
        cw = MagicMock()
        with patch("save_metrics.get_supabase", return_value=sb), \
             patch("save_metrics.cloudwatch", cw):
            import save_metrics
            save_metrics.handler(self._event(processed_jobs=[
                {"compile_result": {"error": "tectonic_not_available", "pdf_s3_key": None}},
                {"compile_result": {"error": "tectonic_not_available", "pdf_s3_key": None}},
            ]), None)

        call = cw.put_metric_data.call_args.kwargs
        resume_metric = next(m for m in call["MetricData"]
                             if m["MetricName"] == "ArtifactsCompiled" and
                             any(d["Value"] == "resume" for d in m.get("Dimensions", [])))
        assert resume_metric["Value"] == 0

    def test_handler_does_not_fail_if_cloudwatch_throws(self):
        """Pipeline must complete even if CW PutMetricData fails. DB write
        is the load-bearing path for the dashboard; metrics are best-effort."""
        sb = _make_supabase()
        cw = MagicMock()
        cw.put_metric_data.side_effect = Exception("CW outage")
        with patch("save_metrics.get_supabase", return_value=sb), \
             patch("save_metrics.cloudwatch", cw):
            import save_metrics
            result = save_metrics.handler(self._event(processed_jobs=[]), None)
        assert result["saved"] == 1
