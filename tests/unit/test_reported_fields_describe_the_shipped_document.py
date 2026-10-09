r"""Every field tailor_resume reports must describe the document it shipped.

THE DEFECTS (audit, 2026-10-08):

1. A generated body that broke a composition PREFERENCE was replaced with
   `compose_from_corpus(base_tex)` in the trimming block, AFTER the recording
   branch. `validation_errors` stayed empty, so the run returned
   used_fallback False and logged "(ok)" for a document whose tailoring had
   been thrown away — the same lie the deleted word-count gate told
   (tests/unit/test_tailor_fallback_is_recorded.py).

2. After `_recover_past_validation` or the quality retry replaced the council's
   body, `guard_violations` still carried the council guard's verdict on the
   DISCARDED body, and save_job persisted it to jobs.resume_verdict. A corpus
   fallback recorded the council's critique_outcome ("adjudicated") and
   tailoring_model for a document no council wrote.

Asserted on outcomes: the return value, the jobs UPDATE payload, and the bytes
in S3 — never on whether a branch was taken (CLAUDE.md #13).
"""
from __future__ import annotations

import logging
import pathlib
import sys
from unittest.mock import MagicMock, patch

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "lambdas" / "pipeline"))

import tailor_resume  # noqa: E402

from tests.unit.realistic_resume_body import body as realistic_body  # noqa: E402

_PREAMBLE = (
    "\\documentclass{article}\n"
    "\\newcommand{\\jobentry}[4]{#1 #2 #3 #4}\n"
    "\\newcommand{\\projectentry}[3]{#1 #2 #3}\n"
)
_BASE_TEX = (f"{_PREAMBLE}\\begin{{document}}\n"
             f"{realistic_body(summary='THE UNTAILORED CORPUS SUMMARY.')}\n"
             "\\end{document}\n")
_COUNCIL = realistic_body(summary="Payments engineer from the council.")
_GUARD = {"passed": False, "violations": ["prompt_echo: x"], "blocking": ["prompt_echo: x"]}
_EXCLUDE_KRAKEN = {"prefer": [{"exclude": "Seattle Kraken"}]}


class _DB:
    def __init__(self, policy=None):
        self.policy = policy
        self.updates: list[dict] = []

    def table(self, name):
        chain = MagicMock()
        for attr in ("select", "eq", "order", "limit", "or_"):
            getattr(chain, attr).return_value = chain

        def update(payload):
            self.updates.append(payload)
            return chain
        chain.update.side_effect = update
        result = MagicMock()
        result.data = {
            "jobs_raw": [{"job_hash": "abc123", "title": "SRE", "company": "Acme",
                          "description": "Kubernetes for payments."}],
            "user_resumes": [{"tex_content": _BASE_TEX}],
            "users": [{"name": "Utkarsh Singh", "email": "254utkarsh@gmail.com",
                       "composition_policy": self.policy}],
            "jobs": [{"job_hash": "abc123"}],
        }.get(name, [])
        chain.execute.return_value = result
        return chain


def _run(council_body, retries=(), policy=None):
    db = _DB(policy)
    responses = iter(retries)
    ai = MagicMock(side_effect=lambda *a, **k: next(
        responses, {"content": "", "provider": "p", "truncated": False}))
    with patch.object(tailor_resume, "get_supabase", return_value=db), \
         patch.object(tailor_resume, "boto3", MagicMock()) as mock_boto3, \
         patch.object(tailor_resume, "ai_complete", ai), \
         patch.object(tailor_resume, "council_complete",
                      return_value={"content": council_body, "provider": "council-p",
                                    "model": "council-m",
                                    "critique_outcome": "adjudicated",
                                    "guard_report": _GUARD}):
        out = tailor_resume.handler({"job_hash": "abc123", "user_id": "u-1"}, None)
    written = mock_boto3.client.return_value.put_object.call_args.kwargs["Body"].decode()
    jobs_update = next(u for u in db.updates if "tailoring_model" in u)
    return written, out, jobs_update


class TestThePreferenceBreakIsAReportedFallback:
    def test_the_double_breaks_the_preference_and_the_corpus_can_be_composed(self):
        from shared.composition_policy import (check_output, check_preferences,
                                               compose_from_corpus)
        assert check_preferences(_COUNCIL, _EXCLUDE_KRAKEN)
        composed, _ = compose_from_corpus(_BASE_TEX, _EXCLUDE_KRAKEN)
        assert check_output(composed, _EXCLUDE_KRAKEN) == []

    def test_it_ships_the_composed_corpus_and_says_so(self, caplog):
        with caplog.at_level(logging.INFO):
            written, out, update = _run(_COUNCIL, policy=_EXCLUDE_KRAKEN)
        assert "THE UNTAILORED CORPUS SUMMARY" in written
        assert "Seattle Kraken" not in written
        assert out["used_fallback"] is True, (
            "the tailoring was discarded and the run reported it as tailored")
        assert out["shipped_from"] == "corpus_fallback"
        summary = [r.getMessage() for r in caplog.records
                   if "tailor for abc123" in r.getMessage()]
        assert len(summary) == 1 and "composition preference" in summary[0], summary
        assert out["composition_violations"] == []

    def test_a_compliant_body_is_not_touched(self):
        no_kraken = _COUNCIL.replace(
            r"\jobentry{Seattle Kraken}{Remote}{2022 -- 2023}{Data Engineer}"
            r"\begin{itemize}"
            r"\item Built ingestion pipelines in Python and Airflow over Snowflake; cut "
            r"nightly runtime by 42\%."
            r"\end{itemize}", "")
        assert "Seattle Kraken" not in no_kraken, "double"
        written, out, _ = _run(no_kraken, policy=_EXCLUDE_KRAKEN)
        assert out["used_fallback"] is False
        assert "Payments engineer from the council." in written


class TestProvenance:
    def test_the_council_body_reports_the_council(self):
        _, out, update = _run(_COUNCIL)
        assert out["shipped_from"] == "council"
        assert out["guard_violations"] == ["prompt_echo: x"]
        assert update["critique_outcome"] == "adjudicated"
        assert update["tailoring_model"] == "council-p:council-m"

    def test_a_recovered_body_does_not_carry_the_council_guard_verdict(self):
        """`_recover_past_validation` replaces a gate-failing council body."""
        recovered = realistic_body(summary="Recovered by the corrective retry.")
        written, out, update = _run(
            r"\section*{Summary}Let's start.",
            [{"content": recovered, "provider": "retry-p", "model": "retry-m",
              "truncated": False}])
        assert "Recovered by the corrective retry." in written
        assert out["used_fallback"] is False
        assert out["guard_violations"] is None, (
            "the guard verdict on the DISCARDED council body was reported for "
            "the retry body, and save_job would persist it")
        assert update["critique_outcome"] == "single_call_retry"
        assert update["tailoring_model"] == "retry-p:retry-m"

    def test_a_quality_retry_body_does_not_carry_it_either(self):
        styled = realistic_body(summary="A highly motivated team player.")
        clean = realistic_body(summary="Clean body from the quality retry.")
        written, out, update = _run(styled, [
            {"content": clean, "provider": "q-p", "model": "q-m", "truncated": False}])
        assert "Clean body from the quality retry." in written
        assert out["guard_violations"] is None
        assert update["critique_outcome"] == "single_call_retry"

    def test_a_composition_repair_body_reports_the_repair(self):
        one_project = realistic_body(summary="Repaired to one project.").replace(
            r"\projectentry{UTWorld}{2024}{Next.js, Netlify}"
            r"\begin{itemize}\item Static site with Lighthouse 98.\end{itemize}", "")
        written, out, update = _run(_COUNCIL, [
            {"content": one_project, "provider": "rep-p", "model": "rep-m",
             "truncated": False}], policy={"max_projects": 1})
        assert "Repaired to one project." in written, "repair did not land; blind"
        assert out["guard_violations"] is None
        assert out["shipped_from"] == "single_call_retry"
        assert update["tailoring_model"] == "rep-p:rep-m"

    def test_a_corpus_fallback_reports_no_council_at_all(self):
        written, out, update = _run(r"\section*{Summary}Only one section.")
        assert out["used_fallback"] is True
        assert out["guard_violations"] is None
        assert update["critique_outcome"] == "corpus_fallback", (
            "a corpus fallback recorded the council's outcome for a document no "
            "council produced")
        assert update["tailoring_model"] == "corpus:composed"
