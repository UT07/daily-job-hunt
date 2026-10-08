r"""The cover letter's retries are judged by the checks that judged the first try.

THE DEFECTS (audit, 2026-10-08), all in lambdas/pipeline/generate_cover_letter.py:

1. The first attempt's errors were `_validate_cover_letter` PLUS
   `_check_opening_quality` and `_check_metric_fabrication`. Each retry ran
   `_validate_cover_letter` alone. So a retry claiming "cut costs by 42%" —
   a number the resume does not contain — was "valid", replaced the first
   attempt, and shipped. CLAUDE.md #14.
2. Only `& % # _` were escaped, so a `$`, `~`, `^` or brace in the prose broke
   the compile.
3. Name, email, phone and links were hardcoded to the owner, for every user.
"""
from __future__ import annotations

import ast
import pathlib
import sys
from unittest.mock import MagicMock, patch

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "lambdas" / "pipeline"))

import generate_cover_letter as gcl  # noqa: E402

from tests.unit.realistic_resume_body import body as realistic_body  # noqa: E402

_BASE_TEX = ("\\documentclass{article}\n\\begin{document}\n"
             f"{realistic_body()}\n\\end{{document}}\n")
_SENTENCE = "Building reliable payment systems at scale is what I did at Clover for years. "


def _letter(extra: str = "", n: int = 24) -> str:
    return _SENTENCE * n + extra


_SHORT = _SENTENCE * 5                      # one error: word count
_FABRICATED = _letter("I cut costs by 42% there.")  # one error: metric


class _DB:
    def __init__(self, user_row, unknown_column=None):
        self.user_row = user_row
        self.unknown_column = unknown_column

    def table(self, name):
        chain = MagicMock()
        for attr in ("select", "eq", "order", "limit"):
            getattr(chain, attr).return_value = chain

        def select(columns="*"):
            if name == "users" and self.unknown_column and self.unknown_column in columns:
                raise RuntimeError(f"PGRST204 Could not find the '{self.unknown_column}' column")
            return chain
        chain.select.side_effect = select
        result = MagicMock()
        result.data = {
            "jobs_raw": [{"job_hash": "abc123", "title": "SRE", "company": "Acme",
                          "description": "Run payments."}],
            "user_resumes": [{"tex_content": _BASE_TEX}],
            "users": [self.user_row] if self.user_row else [],
        }.get(name, [])
        chain.execute.return_value = result
        return chain


def _run(bodies, user_row=None, unknown_column=None):
    it = iter(bodies)
    council = MagicMock(side_effect=lambda **k: {
        "content": next(it), "provider": "p", "model": "m"})
    with patch.object(gcl, "get_supabase", return_value=_DB(user_row, unknown_column)), \
         patch.object(gcl, "boto3", MagicMock()) as mock_boto3, \
         patch.object(gcl, "extract_keywords", return_value=[]), \
         patch.object(gcl, "council_complete", council):
        out = gcl.handler({"job_hash": "abc123", "user_id": "u-1"}, None)
    written = mock_boto3.client.return_value.put_object.call_args.kwargs["Body"].decode()
    return written, out


class TestTheDoublesAreSound:
    def test_the_clean_letter_passes_every_check(self):
        assert gcl._letter_errors(_letter(), "Acme") == []

    def test_the_short_letter_has_exactly_one_error(self):
        assert len(gcl._letter_errors(_SHORT, "Acme")) == 1

    def test_the_fabricated_letter_passes_the_old_retry_rubric(self):
        """Otherwise the old code would have refused it anyway."""
        assert gcl._validate_cover_letter(_FABRICATED)["valid"]
        assert gcl._check_metric_fabrication(_FABRICATED)


class TestRetries:
    def test_a_retry_with_a_fabricated_metric_does_not_replace_the_first_try(self):
        written, out = _run([_SHORT, _FABRICATED, _FABRICATED])
        assert "42" not in written, (
            "a retry was accepted on fewer checks than the first attempt and "
            "shipped a metric the resume does not contain")
        assert out["validation_errors"], "the shipped letter's problems are not reported"

    def test_a_clean_retry_still_wins(self):
        written, out = _run([_SHORT, _letter("Clean retry marker.")])
        assert "Clean retry marker." in written
        assert out["validation_errors"] == []


class TestEscaping:
    def test_every_latex_special_in_the_prose_is_escaped(self):
        written, _ = _run([_letter("Saved $5M with C~ and x^2 {really}.")])
        assert r"\$5M" in written
        assert r"\textasciitilde{}" in written and r"\textasciicircum{}" in written
        assert r"\{really\}" in written
        assert "$5M" not in written.replace(r"\$5M", "")

    def test_the_escaper_is_the_shared_one_not_a_copy(self):
        from parse_sections import _escape_tex
        assert gcl._escape_tex is _escape_tex


class TestTheHeaderIsTheUsers:
    def test_another_user_gets_their_own_name_and_none_of_the_owners_details(self):
        written, _ = _run([_letter()], user_row={
            "name": "Ada Lovelace", "email": "ada@example.com", "phone": "+44 1",
            "location": "London", "github": None, "linkedin": None})
        assert "Ada Lovelace" in written and "ada@example.com" in written
        assert "+44 1" in written and "London" in written
        for owner in ("Utkarsh", "254utkarsh", "892515620", "UT07"):
            assert owner not in written, f"owner detail {owner!r} in another user's letter"

    def test_a_missing_optional_column_does_not_hand_them_the_owners_header(self):
        written, _ = _run([_letter()], unknown_column="linkedin",
                          user_row={"name": "Ada Lovelace", "email": "ada@example.com"})
        assert "Ada Lovelace" in written and "Utkarsh" not in written

    def test_no_profile_row_falls_back_to_the_documented_owner_header(self):
        written, _ = _run([_letter()], user_row=None)
        assert "Utkarsh Singh" in written and "254utkarsh@gmail.com" in written

    def test_profile_values_are_escaped(self):
        written, _ = _run([_letter()], user_row={"name": "A & B_C", "email": "a@b.c"})
        assert r"\textbf{A \& B\_C}" in written, "header name unescaped"
        assert written.count(r"A \& B\_C") == 2, "header and signature"


def test_both_sides_of_the_retry_use_one_check_builder():
    """Structural (CLAUDE.md #14): the handler calls `_letter_errors` for the first
    attempt and the retries, and no individual check directly."""
    tree = ast.parse(pathlib.Path(gcl.__file__).read_text())
    handler = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "handler")
    called = [c.func.id for c in ast.walk(handler)
              if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
    assert called.count("_letter_errors") >= 2
    direct = {"_validate_cover_letter", "_check_opening_quality",
              "_check_metric_fabrication"} & set(called)
    assert not direct, f"{direct} called directly beside the comparison"
    builder = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_letter_errors")
    inner = {c.func.id for c in ast.walk(builder)
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert {"_validate_cover_letter", "_check_opening_quality",
            "_check_metric_fabrication"} <= inner
