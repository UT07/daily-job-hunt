r"""A fabrication that survives every repair must stop the document.

THE DEFECT (audit, 2026-10-08). `check_fabrication` is block-severity in
guardrails.output_guards, and nothing downstream honoured that:

  * the council's `quality_gate` finalizes best-effort once its two repair
    rounds are spent — 87 of 212 runs (41%) in the 2026-10-07 batch;
  * `tailor_resume.handler` put the finding into `quality_warnings` and
    shipped the body, `used_fallback: False`;
  * shared.resume_verdict graded `writing` non-blocking, on the stated ground
    that fabrication "already blocks upstream". It did not.

So a résumé claiming a language the candidate has never listed was a
warn-grade tailor. CLAUDE.md #13: a guard's severity is part of its
implementation, and a test asserting the detector fires passes identically
whether the finding stops anything. Every test below asserts an OUTCOME — the
bytes written to S3 and the return value that describes them.

Nothing here widens the detector (CLAUDE.md #16). Same `_check_fabrication`,
same union baseline the council's guard uses.

UPDATED 2026-10-09. A fabrication that is a plain list entry is now STRIPPED
and the tailored body ships (tests/unit/test_fabrication_is_stripped.py). The
corpus fallback is the last resort for a claim that cannot be cut out cleanly,
so the fallback tests below use `_UNSTRIPPABLE` -- the same Rust claim written
as prose inside the Skills section -- where they used to use `_FABRICATED`,
whose ", Rust" list entry the stripper now removes.
"""
from __future__ import annotations

import logging
import pathlib
import sys
from unittest.mock import MagicMock, patch

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "lambdas" / "pipeline"))

import tailor_resume  # noqa: E402
from shared.resume_verdict import from_step_results  # noqa: E402

from tests.unit.realistic_resume_body import body as realistic_body  # noqa: E402

_PREAMBLE = (
    "\\documentclass{article}\n"
    "\\newcommand{\\jobentry}[4]{#1 #2 #3 #4}\n"
    "\\newcommand{\\projectentry}[3]{#1 #2 #3}\n"
)
_BASE_TEX = (f"{_PREAMBLE}\\begin{{document}}\n"
             f"{realistic_body(summary='THE UNTAILORED CORPUS SUMMARY.')}\n"
             "\\end{document}\n")


def _with_rust(b: str) -> str:
    return b.replace("Python, AWS, Docker", "Python, AWS, Docker, Rust")


_FABRICATED = _with_rust(realistic_body(summary="Payments engineer on the council."))
# The same claim, but in a sentence: no list entry to remove, so it cannot be
# stripped and the corpus fallback must still fire.
_UNSTRIPPABLE = realistic_body(summary="Payments engineer on the council.").replace(
    "Python, AWS, Docker", "Python, AWS, Docker; built payment services in Rust daily")
_CLEAN = realistic_body(summary="Payments engineer from the retry.")


def _db():
    def table(name):
        chain = MagicMock()
        for attr in ("select", "eq", "order", "limit", "update", "or_"):
            getattr(chain, attr).return_value = chain
        result = MagicMock()
        result.data = {
            "jobs_raw": [{"job_hash": "abc123", "title": "SRE", "company": "Acme",
                          "description": "Rust and Kubernetes for payments."}],
            "user_resumes": [{"tex_content": _BASE_TEX}],
            "users": [{"name": "Utkarsh Singh", "email": "254utkarsh@gmail.com"}],
            "jobs": [{"job_hash": "abc123"}],
        }.get(name, [])
        chain.execute.return_value = result
        return chain
    db = MagicMock()
    db.table.side_effect = table
    return db


def _run(council_body, retries):
    responses = iter(retries)
    ai = MagicMock(side_effect=lambda *a, **k: next(
        responses, {"content": "", "provider": "p", "truncated": False}))
    with patch.object(tailor_resume, "get_supabase", return_value=_db()), \
         patch.object(tailor_resume, "boto3", MagicMock()) as mock_boto3, \
         patch.object(tailor_resume, "ai_complete", ai), \
         patch.object(tailor_resume, "council_complete",
                      return_value={"content": council_body, "provider": "p",
                                    "model": "m", "critique_outcome": "adjudicated",
                                    "guard_report": {"passed": False,
                                                     "violations": [], "blocking": []}}):
        out = tailor_resume.handler({"job_hash": "abc123", "user_id": "u-1"}, None)
    written = mock_boto3.client.return_value.put_object.call_args.kwargs["Body"].decode()
    return written, out, ai


def _retry(content):
    return {"content": content, "provider": "p", "truncated": False}


class TestTheDoubleIsSound:
    def test_the_fabricated_body_clears_every_hard_gate(self):
        """Otherwise the fallback below is a hard-gate fallback and proves
        nothing about fabrication."""
        tex = tailor_resume._assemble(_PREAMBLE, _FABRICATED)
        assert tailor_resume._validation_errors(
            tex, _FABRICATED, ["Utkarsh Singh", "254utkarsh@gmail.com"]) == []

    def test_the_real_detector_fires_on_it_against_the_corpus(self):
        assert tailor_resume._check_fabrication(_BASE_TEX, _FABRICATED)

    def test_and_not_on_the_clean_body(self):
        assert tailor_resume._check_fabrication(_BASE_TEX, _CLEAN) == []

    def test_the_unstrippable_body_clears_every_hard_gate_and_still_fabricates(self):
        tex = tailor_resume._assemble(_PREAMBLE, _UNSTRIPPABLE)
        assert tailor_resume._validation_errors(
            tex, _UNSTRIPPABLE, ["Utkarsh Singh", "254utkarsh@gmail.com"]) == []
        assert tailor_resume._check_fabrication(_BASE_TEX, _UNSTRIPPABLE)

    def test_and_the_stripper_refuses_it(self):
        assert tailor_resume._strip_fabrications(_BASE_TEX, _UNSTRIPPABLE).tex is None


class TestWhatShipsWhenItFires:
    def test_a_fabrication_the_retry_cannot_remove_ships_the_corpus(self):
        written, out, ai = _run(_UNSTRIPPABLE, [_retry(_UNSTRIPPABLE)])
        assert ai.call_count >= 1, "the corrective retry never ran"
        assert "Rust" not in written, "a fabricated claim reached S3"
        assert "THE UNTAILORED CORPUS SUMMARY" in written
        assert out["used_fallback"] is True

    def test_it_is_not_reported_as_a_clean_or_warn_grade_tailor(self):
        _, out, _ = _run(_UNSTRIPPABLE, [_retry(_UNSTRIPPABLE)])
        assert out["quality_warnings"] is None, (
            "the corpus shipped; nothing was measured on it, and reporting the "
            "refused body's findings would describe a document that did not ship")
        verdict = from_step_results(out, {"page_violations": [], "ats_violations": []})
        assert verdict.grade not in ("pass", "warn")

    def test_the_summary_line_names_the_fabrication(self, caplog):
        with caplog.at_level(logging.INFO):
            _run(_UNSTRIPPABLE, [_retry(_UNSTRIPPABLE)])
        summary = [r.getMessage() for r in caplog.records
                   if "tailor for abc123" in r.getMessage()]
        assert len(summary) == 1
        assert "fallback" in summary[0] and "Rust" in summary[0], summary[0]


class TestTheRecoveryPathStillWorks:
    def test_a_retry_that_removes_it_ships_tailored(self):
        written, out, _ = _run(_FABRICATED, [_retry(_CLEAN)])
        assert "Payments engineer from the retry." in written
        assert "Rust" not in written
        assert out["used_fallback"] is False

    def test_dropping_the_fabrication_outranks_gaining_a_banned_phrase(self):
        """A count would refuse this retry (1 finding -> 1 finding) and the
        corpus would ship; the block-first ranking accepts it."""
        styled = realistic_body(summary="A results-driven payments engineer.")
        assert len(tailor_resume._quality_warnings(styled, _BASE_TEX, _BASE_TEX)) >= \
            len(tailor_resume._quality_warnings(_FABRICATED, _BASE_TEX, _BASE_TEX)), (
            "double: the retry must not win on a raw count")
        written, out, _ = _run(_FABRICATED, [_retry(styled)])
        assert "results-driven" in written
        assert out["used_fallback"] is False

    def test_a_clean_council_body_is_untouched(self):
        written, out, ai = _run(_CLEAN, [])
        assert ai.call_count == 0
        assert out["used_fallback"] is False
        assert "Payments engineer from the retry." in written
