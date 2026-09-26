"""Unit tests for evals/harness.py.

These pin down the corrections this harness makes against the original
plan's stale draft (see evals/harness.py's module docstring): scoring
against a real resume (never ""), bypassing the ai_cache table only when
repeats > 1 so score_variance is measurable, embedding the base resume in
the tailor prompt so the guard checks are testing something real, and the
checkpoint / consecutive-failure-circuit-breaker cost-discipline behaviour.
Everything here is mocked — no real network, no real AI provider calls, no
real DB.
"""
from unittest.mock import MagicMock, patch

import pytest

from evals import harness
from lambdas.pipeline import score_batch


def _case(id_="x", task="score", **expected):
    base = {
        "id": id_, "task": task, "title": "Engineer", "company": "Acme",
        "description": "Python AWS role",
    }
    if task == "score":
        base["expected"] = expected or {"tier": "A"}
    else:
        base["expected"] = expected or {
            "must_contain": ["Python"], "must_pass_guards": True, "no_fabrication": True,
        }
    return base


BASE_RESUME = (
    "\\documentclass{article}\n\\begin{document}\n"
    "\\section*{Skills}\nPython, AWS, Docker\n"
    "\\section*{Experience}\nStuff\n"
    "\\end{document}\n"
)


class TestLoadBaseResume:
    def test_raises_loudly_when_no_resume_row_found(self):
        mock_db = MagicMock()
        mock_db.client.table.return_value.select.return_value.eq.return_value \
            .order.return_value.limit.return_value.execute.return_value.data = []
        with patch("db_client.SupabaseClient.from_env", return_value=mock_db):
            with pytest.raises(RuntimeError, match="No base resume"):
                harness._load_base_resume()

    def test_raises_loudly_when_tex_content_is_empty_string(self):
        mock_db = MagicMock()
        mock_db.client.table.return_value.select.return_value.eq.return_value \
            .order.return_value.limit.return_value.execute.return_value.data = [{"tex_content": ""}]
        with patch("db_client.SupabaseClient.from_env", return_value=mock_db):
            with pytest.raises(RuntimeError, match="No base resume"):
                harness._load_base_resume()

    def test_returns_tex_content_when_present(self):
        mock_db = MagicMock()
        mock_db.client.table.return_value.select.return_value.eq.return_value \
            .order.return_value.limit.return_value.execute.return_value.data = [
                {"tex_content": BASE_RESUME}
            ]
        with patch("db_client.SupabaseClient.from_env", return_value=mock_db):
            assert harness._load_base_resume() == BASE_RESUME


class TestSplitTexAndSkills:
    def test_split_tex_separates_preamble_and_body(self):
        preamble, body = harness._split_tex(BASE_RESUME)
        assert "documentclass" in preamble
        assert "documentclass" not in body
        assert "Skills" in body

    def test_base_skills_text_extracts_skills_section_only(self):
        _, body = harness._split_tex(BASE_RESUME)
        skills = harness._base_skills_text(body)
        assert "Python" in skills
        assert "Stuff" not in skills


class TestRunScoreCase:
    def test_single_repeat_never_touches_the_cache(self):
        case = _case(task="score", tier="A")
        original = score_batch.ai_complete_cached
        seen = []

        def fake(job, resume_tex, temperature=0):
            seen.append(score_batch.ai_complete_cached is original)
            return {"match_score": 82}

        with patch.object(score_batch, "score_single_job", side_effect=fake):
            result = harness._run_score_case(case, resume_tex="RESUME", repeats=1)

        assert seen == [True]
        assert result == {
            "id": "x", "task": "score", "expected_tier": "A", "actual_tier": "A",
            "scores": [82], "guards_passed": True, "fabricated": False,
            "latency_s": result["latency_s"], "ok": True, "n_failed_calls": 0,
        }

    def test_repeats_greater_than_one_bypasses_cache_only_during_the_call(self):
        case = _case(task="score", tier="A")
        original = score_batch.ai_complete_cached
        seen_during_call = []

        def fake(job, resume_tex, temperature=0):
            seen_during_call.append(score_batch.ai_complete_cached is harness._uncached_ai_complete)
            return {"match_score": 80}

        with patch.object(score_batch, "score_single_job", side_effect=fake):
            harness._run_score_case(case, resume_tex="RESUME", repeats=3)

        assert seen_during_call == [True, True, True]
        # Patch must be undone once the case finishes.
        assert score_batch.ai_complete_cached is original

    def test_job_dict_passed_downstream_includes_job_hash(self):
        """Regression test: score_batch.score_single_job's own exception
        handlers do `job["job_hash"]` when logging a failure (it's a
        production Lambda module we don't get to edit). A job dict built
        without that key turns a real provider failure into a masking
        KeyError("job_hash") the first time every provider is exhausted for
        a call — which is exactly the failure mode the golden-set run needs
        to report honestly. Assert on the actual contract (the callee can
        safely do `job["job_hash"]`), not just that our own code doesn't
        raise.
        """
        case = _case(task="score", tier="A")
        seen_jobs = []

        def fake(job, resume_tex, temperature=0):
            seen_jobs.append(job)
            job["job_hash"]  # would raise KeyError before the fix
            return {"match_score": 82}

        with patch.object(score_batch, "score_single_job", side_effect=fake):
            harness._run_score_case(case, resume_tex="RESUME", repeats=1)

        assert seen_jobs[0]["job_hash"] == "x"

    def test_all_repeats_failing_marks_case_not_ok_and_worst_tier(self):
        case = _case(task="score", tier="A")
        with patch.object(score_batch, "score_single_job", return_value=None):
            result = harness._run_score_case(case, resume_tex="RESUME", repeats=2)
        assert result["ok"] is False
        assert result["actual_tier"] == "D"
        assert result["n_failed_calls"] == 2
        assert result["scores"] == []

    def test_partial_failures_still_average_the_successes(self):
        case = _case(task="score", tier="A")
        outcomes = iter([{"match_score": 90}, None, {"match_score": 80}])
        with patch.object(score_batch, "score_single_job", side_effect=lambda *a, **k: next(outcomes)):
            result = harness._run_score_case(case, resume_tex="RESUME", repeats=3)
        assert result["ok"] is True
        assert result["scores"] == [90, 80]
        assert result["n_failed_calls"] == 1


class TestRunTailorCase:
    def test_flags_missing_keywords_even_when_guards_pass(self):
        case = _case(task="tailor", must_contain=["Python", "Kubernetes"],
                     must_pass_guards=True, no_fabrication=True)
        guard_result = MagicMock(passed=True, violations=[])
        with patch("evals.harness.council_complete",
                   return_value={"content": "I know Python well.", "provider": "p", "model": "m"}), \
             patch("evals.harness.check_output", return_value=guard_result):
            result = harness._run_tailor_case(case, resume_tex=BASE_RESUME)
        assert result["ok"] is True
        assert result["missing_keywords"] == ["Kubernetes"]
        assert result["guards_passed"] is False  # missing keyword overrides a clean guard result

    def test_all_keywords_present_and_guards_clean_passes(self):
        case = _case(task="tailor", must_contain=["Python"], must_pass_guards=True, no_fabrication=True)
        guard_result = MagicMock(passed=True, violations=[])
        with patch("evals.harness.council_complete",
                   return_value={"content": "Extensive Python experience.", "provider": "p", "model": "m"}), \
             patch("evals.harness.check_output", return_value=guard_result):
            result = harness._run_tailor_case(case, resume_tex=BASE_RESUME)
        assert result["guards_passed"] is True
        assert result["missing_keywords"] == []

    def test_provider_exception_marks_case_not_ok_instead_of_crashing(self):
        case = _case(task="tailor")
        with patch("evals.harness.council_complete", side_effect=RuntimeError("all providers failed")):
            result = harness._run_tailor_case(case, resume_tex=BASE_RESUME)
        assert result["ok"] is False
        assert "all providers failed" in result["error"]

    def test_fabrication_violation_sets_fabricated_flag(self):
        from lambdas.pipeline.guardrails.types import GuardResult, Violation
        guard_result = GuardResult(violations=[Violation("fabrication", "'Java' not in base resume", "warn")])
        case = _case(task="tailor", must_contain=["Python"], must_pass_guards=True, no_fabrication=True)
        with patch("evals.harness.council_complete",
                   return_value={"content": "Python and Java expert.", "provider": "p", "model": "m"}), \
             patch("evals.harness.check_output", return_value=guard_result):
            result = harness._run_tailor_case(case, resume_tex=BASE_RESUME)
        assert result["fabricated"] is True


class TestRunGoldenResumability:
    def _patch_common(self, monkeypatch, tmp_path, cases):
        monkeypatch.setattr(harness, "CHECKPOINT", tmp_path / "checkpoint.json")
        monkeypatch.setattr(harness, "load_golden", lambda: cases)
        monkeypatch.setattr(harness, "_load_base_resume", lambda: "RESUME")

    def test_second_run_skips_cases_already_checkpointed(self, tmp_path, monkeypatch):
        cases = [_case("a", tier="A"), _case("b", tier="B")]
        self._patch_common(monkeypatch, tmp_path, cases)
        calls = []

        def fake_score(case, resume_tex, repeats):
            calls.append(case["id"])
            return {"id": case["id"], "task": "score", "ok": True, "actual_tier": "A",
                    "scores": [90], "guards_passed": True, "fabricated": False,
                    "latency_s": 0.1, "expected_tier": case["expected"]["tier"]}

        monkeypatch.setattr(harness, "_run_score_case", fake_score)

        first = harness.run_golden(repeats=1)
        assert calls == ["a", "b"]
        assert len(first) == 2

        calls.clear()
        second = harness.run_golden(repeats=1)
        assert calls == [], "both cases should have been served from the checkpoint"
        assert [r["id"] for r in second] == ["a", "b"]

    def test_different_repeats_value_invalidates_the_checkpoint(self, tmp_path, monkeypatch):
        cases = [_case("a", tier="A")]
        self._patch_common(monkeypatch, tmp_path, cases)
        calls = []

        def fake_score(case, resume_tex, repeats):
            calls.append(repeats)
            return {"id": case["id"], "task": "score", "ok": True, "actual_tier": "A",
                    "scores": [90] * repeats, "guards_passed": True, "fabricated": False,
                    "latency_s": 0.1, "expected_tier": case["expected"]["tier"]}

        monkeypatch.setattr(harness, "_run_score_case", fake_score)
        harness.run_golden(repeats=1)
        harness.run_golden(repeats=3)
        assert calls == [1, 3], "a different repeats value must not be served from the old checkpoint"

    def test_stops_after_three_consecutive_failures_instead_of_retry_looping(self, tmp_path, monkeypatch):
        cases = [_case(str(i), tier="A") for i in range(6)]
        self._patch_common(monkeypatch, tmp_path, cases)

        def failing(case, resume_tex, repeats):
            return {"id": case["id"], "task": "score", "ok": False, "actual_tier": "D",
                    "scores": [], "guards_passed": False, "fabricated": False,
                    "latency_s": 0.0, "expected_tier": "A", "error": "all providers failed"}

        monkeypatch.setattr(harness, "_run_score_case", failing)
        results = harness.run_golden(repeats=1)
        assert len(results) == harness.CONSECUTIVE_FAILURE_LIMIT

    def test_a_case_level_exception_is_recorded_not_raised(self, tmp_path, monkeypatch):
        cases = [_case("a", tier="A"), _case("b", tier="B")]
        self._patch_common(monkeypatch, tmp_path, cases)

        def boom(case, resume_tex, repeats):
            raise RuntimeError("boom")

        def ok(case, resume_tex, repeats):
            return {"id": case["id"], "task": "score", "ok": True, "actual_tier": "B",
                    "scores": [80], "guards_passed": True, "fabricated": False,
                    "latency_s": 0.1, "expected_tier": "B"}

        monkeypatch.setattr(harness, "_run_score_case", lambda c, r, n: boom(c, r, n) if c["id"] == "a" else ok(c, r, n))
        results = harness.run_golden(repeats=1)
        assert results[0]["ok"] is False
        assert "boom" in results[0]["error"]
        assert results[1]["ok"] is True
