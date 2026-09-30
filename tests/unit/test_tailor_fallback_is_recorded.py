r"""Every corpus fallback in `tailor_resume.handler()` must be a reported one.

THE DEFECT. `handler()` had a short-body branch that did this:

    word_count = len(body_text.split())
    if word_count < 500:
        logger.warning("[tailor] body too short ...")
        tailored_tex = base_tex

and then built `validation_errors` from scratch below it. The hard gates ran
against `tailored_tex`, which was now the base resume, so they all passed. The
run therefore reported:

    handler() -> {"used_fallback": False}          for a run that fell back
    log       -> "[tailor] Moderate tailor for <hash> (ok)"
    and the quality retry went on to score `ai_body` -- the body already
    thrown away -- and could re-splice it back over the fallback.

CLAUDE.md rule 2: a status that cannot distinguish "did the work" from "did
nothing" is a lie. `scripts/bench_retrieval.py` reports per-arm fallback rates
off this same field, so the lie was being averaged into benchmark results.

WHY THESE TESTS ARE SHAPED THIS WAY. CLAUDE.md rule 13 -- a test asserting
that a branch is taken passes identically whether the branch changes anything.
The defect was discovered by an assertion `used_fallback is False` that was
GREEN while the body had been discarded. So nothing below asserts that a gate
fired. Every test asserts one of:

  * the bytes `handler()` puts to S3, and the return value that describes them,
    are different for a fallback than for a tailored ship;
  * the summary log line names which gate fired;
  * or, structurally over the AST, that no future edit can reintroduce a
    corpus swap outside the branch that records one.

The structural tests are the ones that would have caught this. A behavioural
suite can only cover the swaps someone thought to write a case for; the defect
was a swap nobody had written a case for.
"""
from __future__ import annotations

import ast
import logging
import pathlib
import re
import sys
from unittest.mock import MagicMock, patch

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "lambdas" / "pipeline"))

import tailor_resume  # noqa: E402

from tests.unit.realistic_resume_body import (  # noqa: E402
    assert_realistic,
    body as realistic_body,
)


# ===========================================================================
# 0. The deleted gate's instrument, kept here so its verdict can be asserted.
# ===========================================================================

def deleted_word_gate_count(ai_body: str) -> int:
    r"""Verbatim the two `re.sub` lines removed from `handler()`.

    Kept so the fixtures below can state their premise as a measurement --
    "this is a body the old gate discarded" -- rather than as a claim in a
    docstring. CLAUDE.md rule 6: prove the double is sound before concluding
    anything from it.

    The bug in it is the first pattern: `(\{[^}]*\})*` consumes the macro's
    braced ARGUMENTS along with its name, so `\textbf{Python}` contributes
    zero words. Measured over this repo's real resumes, counted words over
    true words -- resumes/fullstack.tex 861/1097, resumes/sre_devops.tex
    846/1033, realistic_resume_body.BODY 150/201. A floor advertised as 500
    was enforcing 611-670 true words, worst on the densest documents.
    """
    body_text = re.sub(r"\\[a-zA-Z]+\*?(\{[^}]*\})*", " ", ai_body)
    body_text = re.sub(r"[{}\\%&$#_^~]", " ", body_text)
    return len(body_text.split())


# ===========================================================================
# 1. Structural: only a recorded fallback may swap in the corpus.
# ===========================================================================

def _handler_ast() -> ast.FunctionDef:
    tree = ast.parse(pathlib.Path(tailor_resume.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "handler":
            return node
    raise AssertionError("tailor_resume.handler not found — this test is blind")


def _corpus_swaps(fn: ast.FunctionDef) -> list[ast.Assign]:
    """Every `tailored_tex = base_tex` in the function."""
    return [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "tailored_tex" for t in n.targets)
        and isinstance(n.value, ast.Name) and n.value.id == "base_tex"
    ]


def _recording_branch(fn: ast.FunctionDef) -> ast.If:
    """The `if validation_errors:` block — the one place a fallback is logged
    AND reflected in the return value."""
    for node in ast.walk(fn):
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Name)
                and node.test.id == "validation_errors"):
            return node
    raise AssertionError(
        "handler() no longer has an `if validation_errors:` branch; the "
        "reporting contract these tests describe has been rewritten"
    )


def _word_gates_in(path: pathlib.Path) -> list[int]:
    r"""Line numbers of `if <something about words> < 500:` in one module.

    An AST match, not a text match, for the reason this whole file exists: a
    detector that cannot tell a comment about the gate from the gate would
    report the same thing either way. `status_code < 500` in the scrapers is
    excluded by requiring the left operand to mention words.
    """
    try:
        tree = ast.parse(path.read_text(errors="ignore"))
    except SyntaxError:
        return []
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        test = node.test
        if not (len(test.ops) == 1 and isinstance(test.ops[0], ast.Lt)):
            continue
        right = test.comparators[0]
        if not (isinstance(right, ast.Constant) and right.value == 500):
            continue
        if "word" in ast.unparse(test.left).lower():
            hits.append(node.lineno)
    return hits


def _word_gates_under(root: pathlib.Path) -> list[str]:
    return sorted(
        f"{p.relative_to(REPO)}:{line}"
        for p in root.rglob("*.py") for line in _word_gates_in(p)
    )


def _summary_log_call(fn: ast.FunctionDef) -> ast.Call:
    """The single end-of-run line: `[tailor] <Depth> tailor for <hash> (...)`."""
    matches = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and "tailor for {job_hash}" in ast.unparse(n)
    ]
    assert len(matches) == 1, (
        f"expected exactly one summary log call, found {len(matches)}"
    )
    return matches[0]


class TestOnlyARecordedFallbackCanShipTheCorpus:
    """The regression guard. Not "the short-body branch is gone" -- that is
    one instance. The invariant is that no statement anywhere in `handler()`
    may assign the corpus to `tailored_tex` outside the branch whose
    condition is also what the return value and the log report.
    """

    def test_the_swap_exists_at_all(self):
        """Non-vacuity. If `tailored_tex = base_tex` is ever renamed, every
        other test in this class starts passing over an empty set -- exactly
        the failure mode CLAUDE.md rule 2 describes, one level up."""
        assert _corpus_swaps(_handler_ast()), (
            "no `tailored_tex = base_tex` found in handler(); if the fallback "
            "was renamed, retarget these tests rather than deleting them"
        )

    def test_every_corpus_swap_is_inside_the_branch_that_records_it(self):
        fn = _handler_ast()
        recorded = {id(n) for stmt in _recording_branch(fn).body
                    for n in ast.walk(stmt)}
        stray = [n for n in _corpus_swaps(fn) if id(n) not in recorded]
        assert not stray, (
            "handler() swaps in the base resume at line(s) "
            + ", ".join(str(n.lineno) for n in stray)
            + " without appending to `validation_errors` first. That run will "
            "report used_fallback: False and log \"(ok)\" for a document it "
            "threw away — the defect this file exists for. Append a reason to "
            "`validation_errors` and let the branch below do the swap."
        )

    def test_used_fallback_is_derived_from_the_same_name_that_guards_the_swap(self):
        r"""CLAUDE.md rule 14, applied to a report rather than a comparison.

        `used_fallback` and the guard on the swap must be the same expression,
        structurally — not two expressions that happen to agree today. The
        original defect is precisely what it looks like when they drift.
        """
        fn = _handler_ast()
        returns = [n for n in ast.walk(fn)
                   if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)]
        assert len(returns) == 1, "expected one dict return from handler()"
        keys = returns[0].value.keys
        values = returns[0].value.values
        idx = next(
            (i for i, k in enumerate(keys)
             if isinstance(k, ast.Constant) and k.value == "used_fallback"),
            None,
        )
        assert idx is not None, "handler() no longer returns used_fallback"
        assert ast.unparse(values[idx]) == "bool(validation_errors)", (
            "used_fallback is computed from "
            f"`{ast.unparse(values[idx])}`, but the corpus swap is guarded by "
            "`validation_errors`. Two expressions for one fact is how the "
            "return value came to disagree with what shipped."
        )

    def test_the_summary_line_is_derived_from_the_same_name(self):
        assert "validation_errors" in ast.unparse(_summary_log_call(_handler_ast())), (
            "the end-of-run log line no longer reads `validation_errors`, so "
            "it can report (ok) for a run that shipped the corpus"
        )

    def test_the_short_body_word_gate_is_gone_from_the_pipeline(self):
        r"""CLAUDE.md rule 10: search the whole repo for a guard before
        trusting it. Written as a text search first, which immediately proved
        the point by finding a second copy nobody had mentioned — see
        `test_the_one_remaining_copy_is_the_one_we_know_about`. Matched over
        the AST rather than the source text so that prose ABOUT the removed
        gate (this file, `output_guards.py`'s docstring, the comment in
        `tailor_resume.py` recording why it went) does not read as the gate.
        """
        assert _word_gates_under(REPO / "lambdas") == [], (
            "a short-body word gate is back in the pipeline. It undercounts "
            "LaTeX-dense bodies (see deleted_word_gate_count) and fired on 52 "
            "of 740 real tailored resumes; check_near_empty is the instrument "
            "for this question."
        )

    def test_the_one_remaining_copy_is_the_one_we_know_about(self):
        r"""`tailorer.py:_check_page_length` is the same defect, same regex,
        same floor, reached from `main.py` and from four backfill/regeneration
        scripts. It is NOT fixed here, deliberately: it returns `base_tex` to
        a caller that never claimed otherwise (`tailor_resume()` returns a
        path, and no `used_fallback` contract exists on that path), so it is
        the broken instrument without the false status. Removing it needs its
        own measurement of the population those scripts write, which is not
        this change's population (rule 7).

        Pinned as a set rather than tolerated, so a THIRD copy fails CI and
        this one cannot quietly become two.
        """
        known = {"tailorer.py"}
        found = {
            str(p.relative_to(REPO))
            for p in REPO.rglob("*.py")
            if not any(part in {".venv", "node_modules", ".git", "output"}
                       for part in p.parts)
            and _word_gates_in(p)
        }
        assert found <= known, (
            f"new short-body word gate(s): {sorted(found - known)}"
        )
        assert "tailorer.py" in found, (
            "tailorer.py no longer carries the gate — good; delete it from "
            "`known` so this test keeps meaning what it says"
        )


# ===========================================================================
# 2. Behavioural: what reaches S3, and what the run says about it.
# ===========================================================================

_PREAMBLE = (
    "\\documentclass{article}\n"
    "\\newcommand{\\header}{Utkarsh Singh \\\\ 254utkarsh@gmail.com}\n"
    "\\newcommand{\\jobentry}[4]{#1 #2 #3 #4}\n"
    "\\newcommand{\\projectentry}[3]{#1 #2 #3}\n"
)

# The corpus. Distinguishable from any tailored body on sight, and it clears
# every gate itself — so "S3 received the base resume" is a real observation
# and not just "S3 received something that looks valid".
_BASE_BODY = realistic_body(summary="THE UNTAILORED CORPUS SUMMARY.")
_BASE_TEX = f"{_PREAMBLE}\\begin{{document}}\n{_BASE_BODY}\n\\end{{document}}\n"

# The body at the heart of this change: real production anchor density, and
# 150 words by the deleted gate's regex. The old branch discarded it and said
# nothing. TestTheDeletedGateWouldHaveDiscardedThis pins both halves.
_SHORT_VALID_BODY = realistic_body(
    summary="Engineer tailoring toward Kubernetes at scale for payments.")
_TAILORED_MARKER = "Kubernetes at scale for payments"


def _db():
    def table(name):
        chain = MagicMock()
        for attr in ("select", "eq", "order", "limit", "update"):
            getattr(chain, attr).return_value = chain
        result = MagicMock()
        result.data = {
            "jobs_raw": [{"job_hash": "abc123", "title": "SRE", "company": "Acme",
                          "description": "Run Kubernetes at scale for payments."}],
            "user_resumes": [{"tex_content": _BASE_TEX}],
            "users": [{"name": "Utkarsh Singh", "email": "254utkarsh@gmail.com"}],
        }.get(name, [])
        chain.execute.return_value = result
        return chain
    db = MagicMock()
    db.table.side_effect = table
    return db


def _ship(council_body: str):
    """Run handler(); return (bytes written to S3, return value, ai_complete).

    `ai_complete` is handed back so a test can assert that NO second
    generation happened — the quality retry and the composition repair are
    the two callers, and both are supposed to be unreachable once the corpus
    has been chosen.
    """
    with patch.object(tailor_resume, "get_supabase", return_value=_db()), \
         patch.object(tailor_resume, "boto3", MagicMock()) as mock_boto3, \
         patch.object(tailor_resume, "ai_complete",
                      return_value={"content": "", "provider": "p",
                                    "truncated": False}) as mock_ai, \
         patch.object(tailor_resume, "council_complete",
                      return_value={"content": council_body, "provider": "p",
                                    "model": "m"}):
        out = tailor_resume.handler({"job_hash": "abc123", "user_id": "u-1"}, None)
    put = mock_boto3.client.return_value.put_object
    assert put.call_args, "nothing was written to S3"
    return put.call_args.kwargs["Body"].decode(), out, mock_ai


class TestTheDeletedGateWouldHaveDiscardedThis:
    """The premise, measured. Without this the ship test below proves only
    that some body ships, not that the one at issue does.
    """

    def test_the_fixture_is_under_the_old_five_hundred_word_floor(self):
        counted = deleted_word_gate_count(_SHORT_VALID_BODY)
        assert counted < 500, (
            f"the fixture now counts {counted} words by the deleted gate's "
            "regex, so it no longer represents the population that gate threw "
            "away and this file's ship test has stopped meaning anything"
        )

    def test_and_yet_it_is_a_real_resume_by_content(self):
        """Not short: dense. ~200 identity anchors against a corpus range of
        102-289, and a floor of 40 in check_near_empty."""
        found = assert_realistic()
        assert found > 4 * deleted_word_gate_count(_SHORT_VALID_BODY) / 10, (
            "sanity: this fixture is supposed to be anchor-rich relative to "
            "its de-macro'd word count, which is what the old regex missed"
        )

    def test_the_undercount_is_the_regex_eating_macro_arguments(self):
        r"""The mechanism, not just the symptom. `\textbf{Python}` should
        contribute the word Python and contributes nothing."""
        assert deleted_word_gate_count(r"\textbf{Python} \textbf{Kubernetes}") == 0

    def test_it_would_have_discarded_this_repository_s_own_fixture_too(self):
        """resumes/*.tex are the real base resumes. They survive the floor,
        but only after the regex has eaten 18-25% of their words — which is
        the margin that put 52 of 740 real outputs under it."""
        for name in ("fullstack.tex", "sre_devops.tex"):
            tex = (REPO / "resumes" / name).read_text()
            body = tex.split("\\begin{document}", 1)[1].split("\\end{document}", 1)[0]
            counted = deleted_word_gate_count(body)
            true_words = len([w for w in re.sub(r"\\[a-zA-Z]+\*?", " ", body)
                             .replace("{", " ").replace("}", " ").split()
                             if any(c.isalnum() for c in w)])
            assert counted < true_words * 0.9, (
                f"{name}: the deleted regex counted {counted} of ~{true_words} "
                "words. If this ratio is now ~1.0 the regex was fixed rather "
                "than removed, and the numbers in tailor_resume.py's comment "
                "no longer describe it."
            )


class TestTheReturnValueDistinguishesFallbackFromTailoredShip:
    """CLAUDE.md rule 2, asked of `used_fallback`: what would it report on a
    run that did nothing? It must not be the same thing it reports on a run
    that shipped tailored output.
    """

    def test_a_short_but_valid_body_ships_and_is_reported_as_tailored(self):
        written, out, _ = _ship(_SHORT_VALID_BODY)
        assert out["used_fallback"] is False
        assert _TAILORED_MARKER in written, (
            "the tailored body did not reach S3 — the removed gate, or "
            "something like it, is discarding it again"
        )
        assert written != _BASE_TEX

    def test_a_failing_body_ships_the_corpus_and_is_reported_as_a_fallback(self):
        written, out, _ = _ship(r"\section*{Summary}Only one section here.")
        assert out["used_fallback"] is True
        assert written == _BASE_TEX

    def test_the_two_runs_do_not_write_the_same_bytes(self):
        """The distinction has to be observable in the artifact, not only in
        the flag. Both halves of the old defect — flag and artifact — have to
        move together or the flag is decoration."""
        tailored, tailored_out, _ = _ship(_SHORT_VALID_BODY)
        fell_back, fallback_out, _ = _ship(r"\section*{Summary}Only one section.")
        assert tailored != fell_back
        assert tailored_out["used_fallback"] is not fallback_out["used_fallback"]

    @pytest.mark.parametrize("body,expected_fallback", [
        (_SHORT_VALID_BODY, False),
        (r"\section*{Summary}Only one section here.", True),         # sections
        ("and", True),                                                # near-empty
        (_SHORT_VALID_BODY + r"\section*{Extra}{unclosed", True),     # braces
    ])
    def test_the_flag_and_the_artifact_never_disagree(self, body, expected_fallback):
        written, out, _ = _ship(body)
        assert out["used_fallback"] is expected_fallback
        assert (written == _BASE_TEX) is expected_fallback, (
            "used_fallback says one thing and the bytes in S3 say the other"
        )


class TestTheLogNamesTheGateThatFired:

    def test_the_summary_line_carries_the_reason_on_a_fallback(self, caplog):
        with caplog.at_level(logging.INFO):
            _ship(r"\section*{Summary}Only one section here.")
        summary = [r.getMessage() for r in caplog.records
                   if "tailor for abc123" in r.getMessage()]
        assert len(summary) == 1, "expected exactly one end-of-run summary line"
        assert "fallback" in summary[0]
        assert "missing sections" in summary[0], (
            f"the summary line does not say WHICH gate fired: {summary[0]!r}"
        )

    def test_the_summary_line_says_ok_only_when_the_tailored_body_shipped(self, caplog):
        with caplog.at_level(logging.INFO):
            written, out, _ = _ship(_SHORT_VALID_BODY)
        summary = [r.getMessage() for r in caplog.records
                   if "tailor for abc123" in r.getMessage()]
        assert len(summary) == 1
        assert summary[0].endswith("(ok)")
        assert "fallback" not in summary[0]
        assert _TAILORED_MARKER in written, (
            "'(ok)' was logged for a run whose tailored body did not ship — "
            "the exact reading the deleted branch produced"
        )

    def test_a_near_empty_body_is_named_as_near_empty_not_merely_rejected(self, caplog):
        """The removed gate's one genuine catch, now reported by the
        instrument built for it. A repair told "8 anchors, floor is 40" can
        act; "body too short (3 words)" on a document whose words were eaten
        by a regex cannot."""
        with caplog.at_level(logging.INFO):
            _, out, _ = _ship("and")
        assert out["used_fallback"] is True
        assert "near-empty output" in caplog.text
        assert "near_empty: only" in caplog.text


class TestNothingRunsOnABodyTheRunAlreadyThrewAway:
    """The third symptom of the original defect, and the one that could have
    changed what shipped rather than only what was reported.

    With `validation_errors` still empty after a silent swap, control reached
    the `else:` arm and scored the DISCARDED `ai_body` for quality. If that
    retry "improved", the handler re-spliced the retry body over the fallback
    (`tailored_tex = _splice_tex(base_preamble, ai_body)`) and shipped a body
    that had never been re-checked for length. A fallback could therefore
    un-fall-back itself.
    """

    def test_no_second_generation_once_the_corpus_has_been_chosen(self):
        _, out, mock_ai = _ship(
            r"\section*{Summary}Only one section, and a team player at that.")
        assert out["used_fallback"] is True
        assert mock_ai.call_count == 0, (
            "ai_complete ran after the corpus was chosen: either the quality "
            "retry is scoring a body that was thrown away, or composition "
            "repair is rewriting a document the run already rejected"
        )

    def test_the_quality_retry_cannot_overwrite_a_corpus_fallback(self):
        """Drive the retry as hard as the code allows: a banned phrase in the
        body, and a retry that would look like an improvement. The corpus must
        still be what lands in S3."""
        improved = realistic_body(summary="A clean rewrite with no filler.")
        with patch.object(tailor_resume, "get_supabase", return_value=_db()), \
             patch.object(tailor_resume, "boto3", MagicMock()) as mock_boto3, \
             patch.object(tailor_resume, "ai_complete",
                          return_value={"content": improved, "provider": "p",
                                        "truncated": False}), \
             patch.object(tailor_resume, "council_complete",
                          return_value={"content": r"\section*{Summary}A highly "
                                                   r"motivated team player.",
                                        "provider": "p", "model": "m"}):
            out = tailor_resume.handler(
                {"job_hash": "abc123", "user_id": "u-1"}, None)
        written = mock_boto3.client.return_value.put_object \
            .call_args.kwargs["Body"].decode()
        assert out["used_fallback"] is True
        assert written == _BASE_TEX
        assert "A clean rewrite" not in written
