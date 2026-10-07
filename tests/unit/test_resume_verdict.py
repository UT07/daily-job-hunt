"""The one stored answer to "is this resume any good".

Two things are asserted here that a test of this shape usually omits, and both
are in CLAUDE.md because leaving them out has cost this repo real defects:

  * #13 — a guard's severity is part of its implementation. Asserting that a
    check FIRES says nothing about whether it stops anything. So each check is
    tested for the grade it produces, not merely for appearing in the output.
  * #10 — search the whole repo for a guard before trusting it. The verdict is
    only as good as the keys it reads, and those keys are produced by two other
    modules. `TestTheKeysExistWhereTheyAreRead` walks those modules' ASTs, so
    renaming a key there without renaming it here fails CI instead of silently
    grading every resume `unmeasured` forever.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from shared.resume_verdict import (
    CHECKS,
    FAIL,
    PASS,
    UNMEASURED,
    WARN,
    from_step_results,
    grade,
)

ROOT = Path(__file__).resolve().parents[2]
ALL_CLEAN = {"pages": [], "composition": [], "ats": [], "writing": []}

# Derived from CHECKS so that a new check cannot be added without landing in
# one of the two severity tests below. They do NOT establish that the table
# holds the right values -- a test whose expectations are read from the thing
# under test is a tautology, and mutation testing caught exactly that here:
# flipping `composition` to advisory moved it from one parametrised test to the
# other and both still passed. TestTheSeverityTableItself does that job.
BLOCKING = sorted(n for n, b in CHECKS.items() if b)
ADVISORY = sorted(n for n, b in CHECKS.items() if not b)


class TestTheSeverityTableItself:
    """What blocks is written out as literals here, on purpose.

    CLAUDE.md #13: a guard's severity is part of its implementation, and one
    string literal is all it took to disarm check_fabrication's repair loop
    while every test asserting the detector fired kept passing.

    So this is the one place that states the intended severities rather than
    reading them from the module. Changing CHECKS now fails HERE -- which is a
    prompt to justify the change in a diff, not an obstacle. The parametrised
    tests below then prove `grade()` actually honours whatever the table says.
    """

    def test_the_blocking_checks_are_exactly_these(self):
        assert CHECKS == {
            # a resume that is the wrong length, breaks the user's stated
            # composition rules, or cannot be parsed by an ATS is not sendable
            "pages": True,
            "composition": True,
            "ats": True,
            # ...whereas writing findings are style, with an uncharacterised
            # false-positive rate (#16). The one disqualifying member of that
            # family, fabrication, already blocks upstream in
            # guardrails.output_guards -- blocking here as well would both
            # double-count it and let a banned phrase sink a document that
            # needs a repair, not a rejection.
            "writing": False,
        }


class TestGrades:
    def test_everything_measured_and_clean_is_a_pass(self):
        assert grade(**ALL_CLEAN).grade == PASS

    @pytest.mark.parametrize("check", BLOCKING)
    def test_a_blocking_check_on_its_own_fails_the_document(self, check):
        """The severity assertion. A test that only checked the violation was
        reported would pass identically if `blocking` were flipped to False —
        which is exactly how check_fabrication shipped disarmed."""
        v = grade(**{**ALL_CLEAN, check: [f"{check} is unhappy"]})
        assert v.grade == FAIL, f"{check} is in CHECKS as blocking but graded {v.grade}"
        assert f"{check}: {check} is unhappy" in v.reasons

    @pytest.mark.parametrize("check", ADVISORY)
    def test_an_advisory_check_warns_and_does_not_fail(self, check):
        """Equally a severity assertion, in the other direction: these must NOT
        block. Writing findings have an uncharacterised false-positive rate
        (#16) and the genuinely disqualifying one, fabrication, already blocks
        upstream in the council's own guard."""
        v = grade(**{**ALL_CLEAN, check: [f"{check} is unhappy"]})
        assert v.grade == WARN, f"{check} is advisory but graded {v.grade}"
        assert f"{check}: {check} is unhappy" in v.reasons

    @pytest.mark.parametrize("check", sorted(CHECKS))
    def test_an_unmeasured_check_is_never_a_pass(self, check):
        """CLAUDE.md #2, as a return value: a status that cannot tell "did the
        work" from "did nothing" is a lie. Every other check here is clean, so
        the ONLY reason this is not a pass is that one check did not run."""
        v = grade(**{**ALL_CLEAN, check: None})
        assert v.grade == UNMEASURED
        assert f"{check}: never measured" in v.reasons

    def test_a_broken_instrument_outranks_a_bad_document(self):
        """#12 — fix the instrument before trusting the reading. A verdict that
        reported `fail` here would be making a claim about the document on
        evidence it does not have."""
        v = grade(pages=["4 pages, expected 2"], composition=[], ats=None, writing=[])
        assert v.grade == UNMEASURED
        # ...and it still says what it DID find, so the reader is not sent back
        # to the logs for the half that was measured.
        assert "pages: 4 pages, expected 2" in v.reasons

    def test_nothing_measured_at_all_is_unmeasured_not_pass(self):
        assert grade().grade == UNMEASURED

    def test_reasons_put_blocking_findings_first(self):
        v = grade(pages=["two pages too many"], composition=[],
                  ats=None, writing=["a weak opener"])
        assert v.reasons[0].startswith("pages:")
        assert v.reasons[-1].endswith("never measured")


class TestEmptyIsNotAbsent:
    """The invariant the whole module rests on."""

    def test_an_empty_list_is_a_positive_claim_of_compliance(self):
        assert grade(**ALL_CLEAN).checks[0].measured is True

    def test_none_is_not_an_empty_list(self):
        measured = {c.name: c.measured for c in grade(**{**ALL_CLEAN, "ats": None}).checks}
        assert measured == {"pages": True, "composition": True, "ats": False, "writing": True}

    def test_a_clean_check_requires_having_run(self):
        by_name = {c.name: c for c in grade(**{**ALL_CLEAN, "ats": None}).checks}
        assert by_name["pages"].clean is True
        assert by_name["ats"].clean is False, "an unmeasured check must never read as clean"

    def test_a_non_list_is_not_evidence(self):
        """A bool cannot be read as either fact, so it must not be taken as a
        measurement. `True` meaning "it passed" and `True` meaning "one
        violation, truthy" are indistinguishable here."""
        assert grade(**{**ALL_CLEAN, "ats": True}).grade == UNMEASURED


class TestTheRowShape:
    def test_the_row_is_json_serialisable(self):
        """It is stored in a jsonb column, so anything numpy-ish or tuple-y in
        there fails at the database, after the work is done."""
        row = grade(pages=["too long"], composition=[], ats=[], writing=["meh"]).to_row()
        assert json.loads(json.dumps(row)) == row

    def test_the_row_carries_every_check_not_just_the_grade(self):
        """A row that says only "fail" sends the reader back to the logs, and
        the logs are what this replaces."""
        row = grade(**ALL_CLEAN).to_row()
        assert set(row["checks"]) == set(CHECKS)
        assert row["checks"]["pages"] == {"measured": True, "blocking": True, "violations": []}

    def test_every_check_in_the_table_is_actually_graded(self):
        """Structural, so adding a row to CHECKS without wiring it into
        `grade()` fails here rather than being silently ignored — the shape of
        #14, where two expressions were built from different sets of checks."""
        assert {c.name for c in grade().checks} == set(CHECKS)


class TestFromStepResults:
    def test_a_healthy_pair_of_step_results_passes(self):
        v = from_step_results(
            {"composition_violations": [], "quality_warnings": []},
            {"page_violations": [], "ats_violations": []},
        )
        assert v.grade == PASS

    def test_a_missing_step_result_is_not_four_clean_checks(self):
        assert from_step_results(None, None).grade == UNMEASURED
        assert from_step_results({"composition_violations": [], "quality_warnings": []},
                                 None).grade == UNMEASURED

    def test_the_fallback_path_reports_writing_as_unmeasured(self):
        """tailor_resume's fallback ships the base resume, which
        `_quality_warnings` never saw. `None` is the honest value and must not
        be smoothed into `[]`."""
        v = from_step_results(
            {"composition_violations": [], "quality_warnings": None, "used_fallback": True},
            {"page_violations": [], "ats_violations": []},
        )
        assert v.grade == UNMEASURED
        assert "writing: never measured" in v.reasons


class TestTheKeysExistWhereTheyAreRead:
    """The verdict reads four keys out of two other modules' return dicts.

    Nothing connected those two facts until this test. Renaming
    `quality_warnings` in tailor_resume, or dropping it back out of the return
    as it was before 2026-10-07, would otherwise grade every single resume
    `unmeasured` with no test failing anywhere.

    Walked as an AST rather than grepped: an earlier version of a test in this
    repo matched its own explanatory comment quoting the old code, and passed
    against source that no longer contained the thing it was asserting.
    """

    @staticmethod
    def _returned_dict_keys(path: Path, func: str) -> set[str]:
        tree = ast.parse(path.read_text())
        keys: set[str] = set()
        for node in ast.walk(tree):
            if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                    keys |= {k.value for k in sub.value.keys
                             if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        return keys

    def test_tailor_resume_returns_the_keys_the_verdict_reads(self):
        keys = self._returned_dict_keys(ROOT / "lambdas/pipeline/tailor_resume.py", "handler")
        assert keys, "no dict literal returned from tailor_resume.handler — did it move?"
        assert {"composition_violations", "quality_warnings"} <= keys, (
            f"shared.resume_verdict reads these from tailor_result; handler returns {sorted(keys)}")

    def test_compile_latex_returns_the_keys_the_verdict_reads(self):
        keys = self._returned_dict_keys(ROOT / "lambdas/pipeline/compile_latex.py", "handler")
        assert keys, "no dict literal returned from compile_latex.handler — did it move?"
        assert {"page_violations", "ats_violations"} <= keys, (
            f"shared.resume_verdict reads these from compile_result; handler returns {sorted(keys)}")
