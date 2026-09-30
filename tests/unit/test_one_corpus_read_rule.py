"""All three generators must read the user's base resume by the same rule.

`pick_latest_tailorable` was added after the 2026-09-28 incident (a PDF upload
landed in `user_resumes.tex_content` and broke tailoring) and wired into
tailor_resume.py only. The other two readers kept `.limit(1)` with no validity
check, so the repo held three different answers to "which resume is the
user's":

    tailor_resume.py:543           limit(10) + pick_latest_tailorable
    score_batch.py:203             limit(1),  no check
    generate_cover_letter.py:217   limit(1),  no check

They agreed only because both of this user's rows happen to be valid LaTeX.
The defect is DIVERGENCE, not LaTeX-ness: one non-LaTeX upload becomes the
newest row, and from then on tailoring uses the older LaTeX document while
scoring and the cover letter use the newer extracted text -- so the system
scores, and writes a letter about, a document it does not send. Every layer
reports success.

Two kinds of test here, deliberately:
  * behaviour, against the one accessor that now owns the rule
  * wiring, by source inspection rather than by mocking three handlers

The second kind exists because mocking three Lambda handlers to prove they
agree would mean three doubles of Supabase's fluent builder, and CLAUDE.md
rule 6 is explicit that a double which fails the way the bug fails is worse
than no test. The invariant "no handler has its own user_resumes read" is
structural, so it is checked structurally.
"""
import ast
from pathlib import Path

from shared.resume_format import BaseResume, fetch_tailorable_resume

ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / "lambdas" / "pipeline"
READERS = ("tailor_resume.py", "score_batch.py", "generate_cover_letter.py")

_LATEX = r"\documentclass{article}\begin{document}Real resume\end{document}"
_PDF_TEXT = "UTKARSH SINGH\nBackend Engineer\nExperience: built things"


class _FakeQuery:
    """Records what the caller asked for, so the double can be audited."""

    def __init__(self, rows, log):
        self._rows, self._log = rows, log
        self._limit = None

    def select(self, *a):
        self._log["select"] = a
        return self

    def eq(self, col, val):
        self._log.setdefault("eq", []).append((col, val))
        return self

    def order(self, col, desc=False):
        self._log["order"] = (col, desc)
        return self

    def limit(self, n):
        self._log["limit"] = n
        self._limit = n
        return self

    def execute(self):
        # The limit is HONOURED, not merely recorded. A stub that returns every
        # row whatever limit it was handed cannot tell limit(1) from limit(10) --
        # and limit(1) is precisely the bug (a bad newest row leaves no second
        # row to fall back to). Verified by mutation: with the limit ignored,
        # changing the accessor to limit(1) failed only the audit test below;
        # with it honoured, the behaviour tests fail too.
        self._log["executed"] = True
        rows = self._rows if self._limit is None else self._rows[: self._limit]
        return type("R", (), {"data": list(rows)})()


class _FakeDB:
    def __init__(self, rows):
        self._rows, self.log = rows, {}

    def table(self, name):
        self.log["table"] = name
        return _FakeQuery(self._rows, self.log)


# --- behaviour ---------------------------------------------------------------


def test_the_double_is_actually_exercised_as_production_queries():
    """Guard the double (rule 6). If the stub is not driven the way the real
    client is driven, everything below is about the stub, not the code."""
    db = _FakeDB([{"tex_content": _LATEX, "id": "a"}])
    fetch_tailorable_resume(db, "user-1")
    assert db.log["table"] == "user_resumes"
    assert db.log["order"] == ("created_at", True), "must be newest-first"
    assert db.log["limit"] == 10, "limit(1) cannot skip a bad newest row"
    assert ("user_id", "user-1") in db.log["eq"]
    assert db.log.get("executed") is True


def test_a_newer_non_latex_upload_does_not_win():
    """The 2026-09-28 shape: newest row is extracted PDF text."""
    db = _FakeDB([
        {"tex_content": _PDF_TEXT, "id": "newest-pdf"},
        {"tex_content": _LATEX, "id": "older-latex"},
    ])
    base = fetch_tailorable_resume(db, "user-1")
    assert base.row["id"] == "older-latex"
    assert base.skipped == 1
    assert base.tex == _LATEX


def test_nothing_tailorable_reports_why_rather_than_just_that():
    db = _FakeDB([{"tex_content": _PDF_TEXT, "id": "only-pdf"}])
    base = fetch_tailorable_resume(db, "user-1")
    assert base.row is None
    why = base.why_unusable("user-1")
    assert "documentclass" in why, why
    assert "extracted text" in why, "the message is for the uploader, not a log"


def test_no_rows_at_all_is_distinguishable_from_a_bad_upload():
    """Two different operator actions; they must not produce one message."""
    empty = fetch_tailorable_resume(_FakeDB([]), "user-1")
    bad = fetch_tailorable_resume(_FakeDB([{"tex_content": _PDF_TEXT}]), "user-1")
    assert empty.row is None and bad.row is None
    assert empty.why_unusable("user-1") != bad.why_unusable("user-1")
    assert "no resume found" in empty.why_unusable("user-1")


def test_tex_is_empty_string_not_none_when_unusable():
    """Callers concatenate this into prompts; None would raise deep in generation."""
    assert BaseResume(row=None, skipped=0, newest_tex=None).tex == ""
    assert BaseResume(row={}, skipped=0, newest_tex=None).tex == ""
    assert BaseResume(row={"tex_content": None}, skipped=0, newest_tex=None).tex == ""


# --- wiring, by source inspection -------------------------------------------


def _calls(path: Path, name: str) -> bool:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call):
            fn = node.func
            got = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if got == name:
                return True
    return False


def _reads_user_resumes_directly(path: Path) -> bool:
    """A `.table("user_resumes")` call in this module's own source."""
    for node in ast.walk(ast.parse(path.read_text())):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "table"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "user_resumes"):
            return True
    return False


def test_the_source_scan_is_not_vacuous():
    """Guard the guard: prove the detector sees a real direct read somewhere."""
    assert _reads_user_resumes_directly(ROOT / "app.py"), (
        "app.py has its own user_resumes reads; if this is False the AST matcher "
        "is broken and the assertions below prove nothing"
    )


def test_no_generator_keeps_its_own_user_resumes_read():
    offenders = [f for f in READERS if _reads_user_resumes_directly(PIPELINE / f)]
    assert not offenders, (
        f"{offenders} query user_resumes directly instead of going through "
        "shared.resume_format.fetch_tailorable_resume. A second read is a second "
        "rule, and the two only look equivalent until an upload is not LaTeX."
    )


def test_every_generator_goes_through_the_shared_accessor():
    missing = [f for f in READERS if not _calls(PIPELINE / f, "fetch_tailorable_resume")]
    assert not missing, (
        f"{missing} never call fetch_tailorable_resume, so they are not covered by "
        "the rule the other generators follow."
    )
