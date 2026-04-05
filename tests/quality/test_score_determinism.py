# tests/quality/test_score_determinism.py
"""Tier 4b REPORT ONLY: Score determinism across multiple calls.

Requires real AI calls — cached after first run. Runs in CI as REPORT ONLY.
These tests are expected to be skipped in normal test runs (no AI providers).
"""
import pytest


class TestScoreDeterminism:
    """REPORT ONLY — not a MUST PASS gate."""

    @pytest.mark.skipif(True, reason="Requires real AI providers — run manually or in CI REPORT ONLY")
    def test_same_job_scored_three_times_within_tolerance(self):
        """Same job scored 3x with temp=0 → all within +/-4 points."""
        from lambdas.pipeline.score_batch import score_single_job

        job = {
            "title": "Backend Engineer",
            "company": "Test Corp",
            "description": "Build REST APIs using Python and FastAPI. " * 20,
        }
        resume = "Experienced Python developer with 5 years of backend development. " * 20

        scores = []
        for _ in range(3):
            result = score_single_job(job, resume, temperature=0)
            if result:
                scores.append(result)

        if len(scores) < 2:
            pytest.skip("Not enough AI providers available")

        ats_scores = [s["ats_score"] for s in scores]
        assert max(ats_scores) - min(ats_scores) <= 4, f"ATS scores too spread: {ats_scores}"

        hm_scores = [s["hiring_manager_score"] for s in scores]
        assert max(hm_scores) - min(hm_scores) <= 4, f"HM scores too spread: {hm_scores}"

        tr_scores = [s["tech_recruiter_score"] for s in scores]
        assert max(tr_scores) - min(tr_scores) <= 4, f"TR scores too spread: {tr_scores}"

    def test_score_single_job_accepts_temperature(self):
        """Verify score_single_job function signature accepts temperature param."""
        import inspect
        from lambdas.pipeline.score_batch import score_single_job
        sig = inspect.signature(score_single_job)
        assert "temperature" in sig.parameters
        assert sig.parameters["temperature"].default == 0

    def test_deterministic_function_exists(self):
        """Verify score_single_job_deterministic is importable."""
        from lambdas.pipeline.score_batch import score_single_job_deterministic
        assert callable(score_single_job_deterministic)
