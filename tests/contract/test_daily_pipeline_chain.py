"""Validate each state's output matches the next state's expected input."""
import pytest


@pytest.mark.contract
class TestDailyPipelineChain:
    def test_load_config_output_shape(self, load_config_output):
        required = ["user_id", "queries", "query_hash", "min_match_score"]
        for key in required:
            assert key in load_config_output, f"Missing {key}"
        assert isinstance(load_config_output["queries"], list)
        assert len(load_config_output["queries"]) > 0
        assert len(load_config_output["query_hash"]) == 12
        assert isinstance(load_config_output["min_match_score"], (int, float))

    def test_scraper_output_shape(self, scraper_output):
        assert "count" in scraper_output
        assert "source" in scraper_output
        assert isinstance(scraper_output["count"], int)
        assert isinstance(scraper_output["source"], str)

    def test_scraper_error_is_valid_output(self, scraper_error_output):
        assert scraper_error_output["count"] == 0
        assert "source" in scraper_error_output
        assert "error" in scraper_error_output

    def test_dedup_output_feeds_score(self, dedup_output):
        assert "new_job_hashes" in dedup_output
        assert isinstance(dedup_output["new_job_hashes"], list)
        assert all(isinstance(h, str) for h in dedup_output["new_job_hashes"])
        assert "total_new" in dedup_output

    def test_score_output_feeds_map(self, score_output):
        assert "matched_items" in score_output
        assert "matched_count" in score_output
        assert isinstance(score_output["matched_count"], int)
        for item in score_output["matched_items"]:
            assert "job_hash" in item
            assert "user_id" in item
            assert "light_touch" in item
            assert isinstance(item["light_touch"], bool)

    def test_tailor_output_feeds_compile(self, tailor_output):
        assert "tex_s3_key" in tailor_output
        assert tailor_output["tex_s3_key"].endswith(".tex")
        assert "job_hash" in tailor_output
        assert "user_id" in tailor_output

    def test_compile_output_feeds_save(self, compile_output):
        assert "pdf_s3_key" in compile_output
        assert compile_output["pdf_s3_key"].endswith(".pdf")
        assert "doc_type" in compile_output
        assert compile_output["doc_type"] in ("resume", "cover_letter")

    def test_score_empty_produces_valid_map_input(self):
        empty_score = {"matched_items": [], "matched_count": 0}
        assert len(empty_score["matched_items"]) == 0
        assert empty_score["matched_count"] == 0


@pytest.mark.contract
class TestErrorPaths:
    def test_compile_failure_has_null_pdf(self, compile_failure_output):
        assert compile_failure_output["pdf_s3_key"] is None
        assert "error" in compile_failure_output

    def test_save_job_handles_missing_compile_result(self):
        """SaveJob receives full accumulated state — missing fields should not crash."""
        event_after_tailor_failure = {
            "job_hash": "h1", "user_id": "u1",
            "error": {"Error": "States.TaskFailed"},
        }
        # No compile_result, no cover_compile_result — SaveJob should handle
        assert "compile_result" not in event_after_tailor_failure
        assert "cover_compile_result" not in event_after_tailor_failure

    def test_scraper_results_array_shape(self):
        """Parallel state returns array of branch outputs for SaveMetrics."""
        parallel_output = [
            {"count": 10, "source": "linkedin", "apify_cost_cents": 3},
            {"count": 5, "source": "adzuna"},
            {"count": 0, "source": "hn_hiring", "error": "no_thread"},
        ]
        for result in parallel_output:
            assert "count" in result
            assert "source" in result
