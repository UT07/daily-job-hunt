r"""Every body that can REPLACE another must clear the checks that judged it.

THE DEFECT (audit, 2026-10-08). `tailor_resume.handler` judges the council's
body with `_validation_errors` — brace balance, macro arity, required sections,
header markers, prompt echo, near-empty — and then, if it has quality warnings,
asks for one corrective retry. That retry was accepted when it was not
truncated, had all six sections, and had fewer quality warnings. Nothing re-ran
the other five hard gates on it. So a retry opening

    Let's start by reordering the skills to match the role.

with no banned phrase in it scored as an improvement over a body that said
"team player", replaced it, and shipped with `used_fallback: False`. A prompt
echo is the single largest cause of fallbacks (71 of 95 in the 2026-10-07
batch); the quality retry was a side door around the gate that catches it.

`_enforce_composition` had the same hole one step later: its repaired body was
checked for braces, arity and sections, but not for header markers, prompt
echo, near-empty output or fabrication.

CLAUDE.md #14: when a comparison decides whether to accept output, both sides
must count the same things. These tests pin it two ways — behaviourally, by
what reaches S3, and structurally, by which builder each acceptance site calls,
so the next replacement path cannot be added without it.
"""
from __future__ import annotations

import ast
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

# Two banned phrases: enough to arm the quality retry, nothing else wrong.
_COUNCIL_BODY = realistic_body(summary="A highly motivated team player on payments.")
_ECHO = "Let's start by reordering the skills to match the role.\n"
# No banned phrase, so it scores 0 quality warnings against the council's 2 —
# an "improvement" by the old rubric — and a prompt echo by the hard gates.
_ECHOING_RETRY = _ECHO + realistic_body(summary="Payments engineer, eight years.")


def _db(policy=None):
    def table(name):
        chain = MagicMock()
        for attr in ("select", "eq", "order", "limit", "update", "or_"):
            getattr(chain, attr).return_value = chain
        result = MagicMock()
        result.data = {
            "jobs_raw": [{"job_hash": "abc123", "title": "SRE", "company": "Acme",
                          "description": "Run Kubernetes at scale for payments."}],
            "user_resumes": [{"tex_content": _BASE_TEX}],
            "users": [{"name": "Utkarsh Singh", "email": "254utkarsh@gmail.com",
                       "composition_policy": policy}],
            "jobs": [{"job_hash": "abc123"}],
        }.get(name, [])
        chain.execute.return_value = result
        return chain
    db = MagicMock()
    db.table.side_effect = table
    return db


def _run(council_body, ai_responses, policy=None):
    responses = iter(ai_responses)
    ai = MagicMock(side_effect=lambda *a, **k: next(
        responses, {"content": "", "provider": "p", "truncated": False}))
    with patch.object(tailor_resume, "get_supabase", return_value=_db(policy)), \
         patch.object(tailor_resume, "boto3", MagicMock()) as mock_boto3, \
         patch.object(tailor_resume, "ai_complete", ai), \
         patch.object(tailor_resume, "council_complete",
                      return_value={"content": council_body, "provider": "p",
                                    "model": "m", "critique_outcome": "adjudicated"}):
        out = tailor_resume.handler({"job_hash": "abc123", "user_id": "u-1"}, None)
    written = mock_boto3.client.return_value.put_object.call_args.kwargs["Body"].decode()
    return written, out, ai


class TestTheDoublesAreSound:
    """CLAUDE.md #6. Each premise below is measured, not asserted in prose."""

    def test_the_council_body_clears_every_hard_gate(self):
        tex = tailor_resume._splice_tex(_PREAMBLE, _COUNCIL_BODY)
        assert tailor_resume._validation_errors(
            tex, _COUNCIL_BODY, ["Utkarsh Singh", "254utkarsh@gmail.com"]) == []

    def test_the_council_body_arms_the_quality_retry(self):
        assert tailor_resume._quality_warnings(_COUNCIL_BODY, _COUNCIL_BODY, "") != []

    def test_the_retry_is_an_improvement_by_the_quality_rubric_alone(self):
        """Otherwise the old code would have rejected it for an unrelated reason
        and this file would pass on the unfixed handler."""
        assert len(tailor_resume._quality_warnings(_ECHOING_RETRY, _COUNCIL_BODY, "")) < \
            len(tailor_resume._quality_warnings(_COUNCIL_BODY, _COUNCIL_BODY, ""))

    def test_the_retry_has_all_six_sections(self):
        assert tailor_resume._check_required_sections(_ECHOING_RETRY) == []

    def test_the_retry_is_a_prompt_echo(self):
        assert tailor_resume._check_prompt_echo(_ECHOING_RETRY)


class TestTheQualityRetry:
    def test_an_echoing_retry_does_not_replace_a_valid_body(self):
        written, out, ai = _run(_COUNCIL_BODY, [
            {"content": _ECHOING_RETRY, "provider": "p", "truncated": False}])
        assert ai.call_count >= 1, "the quality retry never ran; this test is blind"
        assert "Let's start" not in written, (
            "the quality retry shipped a prompt echo: it was judged on fewer "
            "checks than the body it replaced")
        assert "highly motivated team player on payments" in written
        assert out["used_fallback"] is False

    def test_a_clean_retry_is_still_accepted(self):
        """Over-correction guard: the retry must remain able to win."""
        clean = realistic_body(summary="Payments engineer, eight years.")
        written, out, _ = _run(_COUNCIL_BODY, [
            {"content": clean, "provider": "p", "truncated": False}])
        assert "Payments engineer, eight years." in written
        assert out["used_fallback"] is False
        assert out["quality_warnings"] is not None
        assert not any("team player" in w for w in out["quality_warnings"])

    def test_a_truncated_retry_is_refused_even_when_every_gate_passes(self):
        """Truncation stays an extra check on top of the hard gates: a body cut
        off at max_tokens can lose content without tripping any of them."""
        clean = realistic_body(summary="Payments engineer, cut off later.")
        written, _, _ = _run(_COUNCIL_BODY, [
            {"content": clean, "provider": "p", "truncated": True}])
        assert "cut off later" not in written
        assert "highly motivated team player on payments" in written

    def test_a_retry_missing_its_header_is_refused(self):
        """Header markers are a hard gate the old retry never looked at."""
        headless = realistic_body(summary="Payments engineer.").replace(
            "Utkarsh Singh", "A Candidate").replace("254utkarsh@gmail.com", "x@y.z")
        written, _, _ = _run(_COUNCIL_BODY, [
            {"content": headless, "provider": "p", "truncated": False}])
        assert "A Candidate" not in written


class TestTheCompositionRepair:
    _POLICY = {"max_projects": 1}

    def test_the_double_breaks_the_policy(self):
        """Two projects against a cap of one, or the repair never fires."""
        from shared.composition_policy import check_output
        assert check_output(_COUNCIL_BODY, self._POLICY)

    def test_an_echoing_repair_is_not_shipped(self):
        one_project = _ECHO + realistic_body(summary="Clean payments summary.").replace(
            r"\projectentry{UTWorld}{2024}{Next.js, Netlify}"
            r"\begin{itemize}\item Static site with Lighthouse 98.\end{itemize}", "")
        from shared.composition_policy import check_output
        assert not check_output(one_project, self._POLICY), "double does not comply"
        clean_council = realistic_body(summary="Payments engineer on the council.")
        written, _, _ = _run(clean_council, [
            {"content": one_project, "provider": "p", "truncated": False}],
            policy=self._POLICY)
        assert "Let's start" not in written, (
            "the composition repair shipped a prompt echo")

    def test_a_repair_that_fabricates_is_not_shipped(self):
        """Fabrication is block-severity (output_guards); a repair may not add one."""
        one_project = realistic_body(summary="Clean payments summary.").replace(
            r"\projectentry{UTWorld}{2024}{Next.js, Netlify}"
            r"\begin{itemize}\item Static site with Lighthouse 98.\end{itemize}",
            "").replace("Python, AWS, Docker", "Python, AWS, Docker, Rust")
        assert tailor_resume._check_fabrication(_BASE_TEX, one_project), "double"
        clean_council = realistic_body(summary="Payments engineer on the council.")
        written, _, _ = _run(clean_council, [
            {"content": one_project, "provider": "p", "truncated": False}],
            policy=self._POLICY)
        assert "Rust" not in written

    def test_the_reported_quality_describes_the_repaired_body(self):
        """`shipped_quality` used to be measured before the repair swapped the
        body, so the stored verdict described a document that did not ship."""
        one_project = realistic_body(
            summary="A results-driven payments engineer.").replace(
            r"\projectentry{UTWorld}{2024}{Next.js, Netlify}"
            r"\begin{itemize}\item Static site with Lighthouse 98.\end{itemize}", "")
        assert tailor_resume._check_banned_phrases(one_project), "double"
        clean_council = realistic_body(summary="Payments engineer on the council.")
        written, out, _ = _run(clean_council, [
            {"content": one_project, "provider": "p", "truncated": False}],
            policy=self._POLICY)
        assert "results-driven" in written, "the repair did not land; test is blind"
        assert any("results-driven" in w for w in out["quality_warnings"]), (
            "the returned quality warnings were measured on the pre-repair body")


# ---------------------------------------------------------------------------
# Structural: every acceptance site calls the one hard-gate builder
# ---------------------------------------------------------------------------

_TREE = ast.parse(pathlib.Path(tailor_resume.__file__).read_text())


def _fn(name):
    return next(n for n in ast.walk(_TREE)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def _calls(node, name):
    return [c for c in ast.walk(node) if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Name) and c.func.id == name]


def test_the_quality_retry_block_runs_the_hard_gates():
    """The `try:` that produces `retry_body` must also call `_validation_errors`
    on it. Scoped to that block, not to handler(), because handler() calls the
    builder for the first attempt too and a function-wide count would pass with
    the retry's call deleted."""
    handler = _fn("handler")
    blocks = [t for t in ast.walk(handler) if isinstance(t, ast.Try)
              and any(isinstance(n, ast.Name) and n.id == "retry_body"
                      for n in ast.walk(t))]
    assert blocks, "no try-block handles retry_body; the retry moved and this is blind"
    assert any(_calls(b, "_validation_errors") for b in blocks), (
        "the quality retry is accepted without running _validation_errors, so it "
        "is judged on fewer checks than the body it replaces (CLAUDE.md #14)")


def test_every_replacement_path_uses_the_same_builders():
    """Each function that can hand back a different body than it was given."""
    for name in ("_recover_past_validation", "_enforce_composition"):
        assert _calls(_fn(name), "_validation_errors"), (
            f"{name} accepts a body without running _validation_errors")
    assert _calls(_fn("_enforce_composition"), "_quality_warnings"), (
        "_enforce_composition accepts a repair without measuring its writing "
        "quality — fabrication included — so the shipped verdict describes the "
        "body it replaced")


def test_the_blocking_prefix_matches_the_real_detector():
    """`_blocking_findings` selects by prefix; prove the prefix against the real
    check_fabrication output rather than a string typed into a test (#6)."""
    found = tailor_resume._check_fabrication(
        "Python", r"\section*{Technical Skills} Python, Rust \section*{Experience}")
    assert found, "the detector did not fire; this test is blind"
    assert tailor_resume._blocking_findings(found) == found
    assert tailor_resume._blocking_findings(["banned_phrase: 'team player'"]) == []
