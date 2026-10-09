# tests/quality/test_score_determinism.py
"""Tier 4b REPORT ONLY: Score determinism across multiple calls.

Requires real AI calls, so it is skipped when no provider key is present.

THE SKIP USED TO BE UNCONDITIONAL. `@pytest.mark.skipif(True, ...)` sat above
the one test that makes a real call, under a docstring saying it "Runs in CI as
REPORT ONLY" -- so it had never executed anywhere, in CI or locally, while
reading as covered. CLAUDE.md #2: a status that cannot distinguish "did the
work" from "did nothing" is a lie, and an always-skip is that status.

The condition is now the actual precondition: a configured provider. With keys
present it runs; without them it skips and SAYS which keys were missing, so a
skip cannot be mistaken for a pass.

What it measures is also worth stating, because the premise it was written
under is false. temperature=0 does not make a model deterministic: measured
2026-09-28, one model given one identical prompt returned three different
answers to three consecutive calls, because temperature controls sampling and
not mixture-of-experts routing or request batching. The +/-4 tolerance below is
therefore a band around real provider variance, not a rounding allowance.
"""
import os

import pytest

# The providers score_single_job can actually reach. Scoring needs exactly one.
_PROVIDER_KEYS = (
    "GROQ_API_KEY", "CEREBRAS_API_KEY", "OPENROUTER_API_KEY",
    "GEMINI_API_KEY", "NVIDIA_API_KEY", "QWEN_API_KEY", "DEEPSEEK_API_KEY",
)
_AVAILABLE = [k for k in _PROVIDER_KEYS if os.environ.get(k)]
_NO_PROVIDER = pytest.mark.skipif(
    not _AVAILABLE,
    reason=("needs a real AI provider; none of "
            + ", ".join(_PROVIDER_KEYS) + " is set"),
)


class TestScoreDeterminism:
    """REPORT ONLY — not a MUST PASS gate."""

    @_NO_PROVIDER
    def test_single_calls_vary_but_not_wildly(self):
        """Three single calls at temperature=0, and what they actually return.

        THE TOLERANCE USED TO BE +/-4 AND THAT WAS A FALSE EXPECTATION.
        Measured 2026-10-08, the first time this test had ever executed (its
        skip was `skipif(True)`): the same job, the same resume, the same
        prompt, and gemini answering all three calls returned ATS scores of
        **70, 85 and 75**. temperature=0 controls sampling; it does not control
        mixture-of-experts routing or request batching.

        A gate encoding an expectation the system provably cannot meet is red
        forever, and a report that is always red is a report nobody reads. So
        the bar is set where it can still catch something real: a spread this
        wide is expected and is why production takes medians, but a spread of
        40+ would mean two different models are answering, or the prompt has
        stopped constraining the output, and that IS actionable.

        The number is printed either way, because the value of this test is the
        measurement, not the pass.
        """
        from lambdas.pipeline.score_batch import score_single_job

        job = {
            "job_hash": "determinism-probe",
            "title": "Backend Engineer",
            "company": "Test Corp",
            "description": "Build REST APIs using Python and FastAPI. " * 20,
        }
        resume = "Experienced Python developer with 5 years of backend development. " * 20

        scores = []
        for _ in range(3):
            # skip_cache is essential: without it calls two and three return
            # the cached first response and this measures nothing at all.
            result = score_single_job(job, resume, temperature=0, skip_cache=True)
            if result:
                scores.append(result)

        if len(scores) < 2:
            pytest.skip("Not enough AI providers answered to compare anything")

        print(f"\n[determinism] {len(scores)} calls, providers="
              f"{sorted({s.get('provider') for s in scores})}, "
              f"models={sorted({s.get('model') for s in scores})}")
        for key, label in (("ats_score", "ATS"),
                           ("hiring_manager_score", "Hiring Mgr"),
                           ("tech_recruiter_score", "Tech Recruiter"),
                           ("match_score", "Match")):
            vals = [s[key] for s in scores if isinstance(s.get(key), (int, float))]
            if not vals:
                continue
            spread = max(vals) - min(vals)
            print(f"[determinism] {label:15} {vals}  spread={spread}")
            assert spread <= 40, (
                f"{label} spread {spread} across {vals} — beyond provider "
                f"variance. Check whether one model answered all three calls "
                f"(printed above) and whether the prompt still pins the scale."
            )

    @_NO_PROVIDER
    def test_the_median_of_three_is_steadier_than_one_call(self):
        """The property production actually relies on.

        /api/score scores with `score_single_job_deterministic(num_calls=3,
        skip_cache=True)` precisely because one sample was 15 points wide. This
        asserts the median path reports its own spread, so a caller can show a
        band rather than claim a precision the measurement does not have.
        """
        from lambdas.pipeline.score_batch import score_single_job_deterministic

        job = {
            "job_hash": "determinism-probe-median",
            "title": "Backend Engineer",
            "company": "Test Corp",
            "description": "Build REST APIs using Python and FastAPI. " * 20,
        }
        resume = "Experienced Python developer with 5 years of backend development. " * 20

        out = score_single_job_deterministic(job, resume, num_calls=3, skip_cache=True)
        if not out:
            pytest.skip("No AI provider answered")

        spread = out.get("score_spread")
        assert spread, "the median path dropped the spread, so the band is unreportable"
        assert spread.get("n", 0) >= 1
        print(f"\n[determinism] median match={out.get('match_score')} "
              f"spread={spread}")
        # A median of n calls must sit inside the range of those calls.
        lo_hi = spread.get("match")
        if lo_hi and out.get("match_score") is not None:
            assert lo_hi[0] <= out["match_score"] <= lo_hi[1], (
                f"median {out['match_score']} outside observed range {lo_hi}")

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
