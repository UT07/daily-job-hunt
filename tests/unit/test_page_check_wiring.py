r"""The page check has to be ON the paths that produce resumes, not beside them.

Two paths compile a tailored resume today:

    latex_compiler.compile_tex_to_pdf   app.py (on-demand JD -> resume),
                                        main.py, scripts/*
    lambdas/pipeline/compile_latex      CompileResume in both state machines,
                                        which the dashboard's Regenerate hits
                                        via SINGLE_JOB_PIPELINE_ARN

Both are covered here. The doubles hand the check a REAL compiled PDF from
tests/fixtures/pdf rather than a mock of one, because the thing being tested is
whether a measurement reaches the result — a mocked PDF could only confirm what
this file already believes.
"""
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lambdas" / "pipeline"))

FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "pdf"
GOOD = FIXTURES / "two_page_ok.pdf"
BLANK_SECOND = FIXTURES / "two_page_blank_second.pdf"
BLANK_MIDDLE = FIXTURES / "three_page_blank_middle.pdf"

import latex_compiler  # noqa: E402


@pytest.fixture(autouse=True)
def patch_boto3_ssm():
    """Opt out of tests/unit/conftest.py's autouse `patch("boto3.client")`.

    That fixture returns a MagicMock for every client, so moto's S3 never gets
    a look in and `obj["Body"].read()` hands back a MagicMock — a failure that
    looks like a broken handler and is only a broken double. Overriding the
    name here disables it for this module; `mock_s3` supplies the real moto
    bucket instead.
    """
    yield


# ---------------------------------------------------------------------------
#  latex_compiler
# ---------------------------------------------------------------------------

def _stub_compiler(monkeypatch, pdf: Path):
    """Replace the tectonic/pdflatex subprocess seam with a real PDF.

    `_compile_work_copy` is the function that shells out. Everything above it
    — the sanitizer, the hard gates, the page check being added — still runs.
    """
    monkeypatch.setattr(
        latex_compiler, "_compile_work_copy",
        lambda work_copy, original_path, out_dir: str(pdf),
    )


def _resume_tex(tmp_path: Path, name: str = "resume.tex") -> Path:
    """A .tex that clears latex_compiler's existing hard gates."""
    tex = tmp_path / name
    tex.write_text(
        "\\documentclass{article}\n\\begin{document}\n"
        "\\section*{Summary}\nEngineer.\n"
        "\\section*{Skills}\nPython.\n"
        "\\section*{Experience}\nAcme.\n"
        "\\section*{Projects}\nNaukriBaba.\n"
        "\\section*{Education}\nMSc.\n"
        "\\end{document}\n"
    )
    return tex


class TestCompileTexToPdfWithReport:
    def test_a_compliant_resume_reports_no_violations(self, tmp_path, monkeypatch):
        _stub_compiler(monkeypatch, GOOD)
        pdf, violations = latex_compiler.compile_tex_to_pdf_with_report(
            str(_resume_tex(tmp_path)), str(tmp_path)
        )
        assert pdf == str(GOOD)
        assert violations == []

    def test_a_blank_page_reaches_the_caller(self, tmp_path, monkeypatch):
        _stub_compiler(monkeypatch, BLANK_SECOND)
        _pdf, violations = latex_compiler.compile_tex_to_pdf_with_report(
            str(_resume_tex(tmp_path)), str(tmp_path)
        )
        assert any("page 2 of 2" in v for v in violations), violations

    def test_the_wrong_page_count_reaches_the_caller(self, tmp_path, monkeypatch):
        _stub_compiler(monkeypatch, BLANK_MIDDLE)
        _pdf, violations = latex_compiler.compile_tex_to_pdf_with_report(
            str(_resume_tex(tmp_path)), str(tmp_path)
        )
        assert any("3 pages" in v for v in violations), violations

    def test_violations_are_logged_at_error_level(self, tmp_path, monkeypatch, caplog):
        _stub_compiler(monkeypatch, BLANK_SECOND)
        with caplog.at_level(logging.ERROR):
            latex_compiler.compile_tex_to_pdf_with_report(
                str(_resume_tex(tmp_path)), str(tmp_path)
            )
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errors, "a blank page was not logged as an error"
        assert "page 2 of 2" in caplog.text

    def test_an_explicit_page_count_overrides_the_policy(self, tmp_path, monkeypatch):
        _stub_compiler(monkeypatch, BLANK_MIDDLE)
        _pdf, violations = latex_compiler.compile_tex_to_pdf_with_report(
            str(_resume_tex(tmp_path)), str(tmp_path), expected_pages=3
        )
        assert not any("pages" in v and "policy" in v for v in violations), violations

    def test_a_cover_letter_has_no_page_count_asserted(self, tmp_path, monkeypatch):
        """Nothing here has measured what a cover letter should compile to.

        The existing `is_cover_letter` branch already exempts cover letters
        from the required-sections gate; the page COUNT is exempt for the same
        reason — there is no measured policy for it. The blank-page floor still
        applies, because no document benefits from an empty page.
        """
        _stub_compiler(monkeypatch, BLANK_MIDDLE)
        _pdf, violations = latex_compiler.compile_tex_to_pdf_with_report(
            str(_resume_tex(tmp_path, "cover_letter.tex")), str(tmp_path)
        )
        assert not any("pages" in v and "policy" in v for v in violations), violations
        assert any("page 2 of 3" in v for v in violations), violations

    def test_the_check_does_not_block_the_pdf(self, tmp_path, monkeypatch):
        """A violation is a flag, not a hard gate — same as composition."""
        _stub_compiler(monkeypatch, BLANK_SECOND)
        pdf, violations = latex_compiler.compile_tex_to_pdf_with_report(
            str(_resume_tex(tmp_path)), str(tmp_path)
        )
        assert violations
        assert pdf == str(BLANK_SECOND)

    def test_a_failed_compile_reports_no_page_violations(self, tmp_path, monkeypatch):
        """Nothing was compiled, so there is nothing to measure."""
        monkeypatch.setattr(
            latex_compiler, "_compile_work_copy",
            lambda work_copy, original_path, out_dir: "",
        )
        pdf, violations = latex_compiler.compile_tex_to_pdf_with_report(
            str(_resume_tex(tmp_path)), str(tmp_path)
        )
        assert pdf == ""
        assert violations == []

    def test_the_original_entry_point_keeps_returning_only_a_path(
        self, tmp_path, monkeypatch
    ):
        """app.py, main.py and scripts/ all do `pdf = compile_tex_to_pdf(...)`."""
        _stub_compiler(monkeypatch, BLANK_SECOND)
        result = latex_compiler.compile_tex_to_pdf(
            str(_resume_tex(tmp_path)), str(tmp_path)
        )
        assert isinstance(result, str)
        assert result == str(BLANK_SECOND)


@pytest.mark.skipif(shutil.which("tectonic") is None,
                    reason="tectonic is not installed (CI does not have it)")
class TestAgainstRealTectonic:
    """No fixture PDF, no stub: compile it here and read what comes out."""

    def _compile(self, tmp_path, fixture_tex):
        tex = tmp_path / "resume.tex"
        tex.write_text((FIXTURES / fixture_tex).read_text())
        return latex_compiler.compile_tex_to_pdf_with_report(str(tex), str(tmp_path))

    def test_the_blank_middle_page_case_end_to_end(self, tmp_path):
        """The shape the existing gates pass and only this check catches.

        three_page_blank_middle.tex has all five required section headings, so
        `check_section_completeness` is satisfied; the Featured Projects
        heading is simply followed by nothing. It compiles, and the page check
        is the only thing between it and the user.
        """
        pdf, violations = self._compile(tmp_path, "three_page_blank_middle.tex")
        assert pdf, "fixture source failed to compile"
        assert any("3 pages" in v for v in violations), violations
        assert any("page 2 of 3" in v for v in violations), violations

    def test_the_empty_page_case_end_to_end(self, tmp_path):
        pdf, violations = self._compile(tmp_path, "four_page_empty_middle.tex")
        assert pdf, "fixture source failed to compile"
        assert any("4 pages" in v for v in violations), violations
        assert any("page 3 of 4" in v and "0 characters" in v
                   for v in violations), violations

    def test_a_normal_two_page_resume_passes_end_to_end(self, tmp_path):
        pdf, violations = self._compile(tmp_path, "two_page_ok.tex")
        assert pdf, "fixture source failed to compile"
        assert violations == []

    def test_a_dropped_section_is_still_the_older_gate_s_job(self, tmp_path):
        """two_page_blank_second.tex never reaches the page check here.

        Its Education section is gone, so `check_section_completeness` blocks
        the compile first. That shape is unreachable through latex_compiler —
        but `lambdas/pipeline/compile_latex.py` has no section gate at all, so
        it IS reachable there, which is what TestCompileLatexHandler covers.
        """
        pdf, violations = self._compile(tmp_path, "two_page_blank_second.tex")
        assert pdf == ""
        assert violations == []


# ---------------------------------------------------------------------------
#  lambdas/pipeline/compile_latex.py — the state machines' CompileResume
# ---------------------------------------------------------------------------

def _fake_tectonic(pdf: Path):
    """Stand in for the tectonic subprocess by producing a real PDF.

    compile_latex writes document.tex into a TemporaryDirectory and expects
    document.pdf beside it afterwards, so the stub does exactly that.
    """
    def run(cmd, *args, **kwargs):
        tex_path = Path(cmd[-1])
        shutil.copyfile(pdf, tex_path.with_suffix(".pdf"))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    return run


class TestCompileLatexHandler:
    def _invoke(self, mock_s3, monkeypatch, pdf: Path, doc_type="resume", **event):
        import compile_latex

        key = "users/u1/resumes/abc123_tailored.tex"
        mock_s3.put_object(Bucket="utkarsh-job-hunt", Key=key,
                           Body=b"\\documentclass{article}\\begin{document}x\\end{document}")
        monkeypatch.setattr(compile_latex.subprocess, "run", _fake_tectonic(pdf))
        return compile_latex.handler(
            {"tex_s3_key": key, "job_hash": "abc123", "user_id": "u1",
             "doc_type": doc_type, **event},
            None,
        )

    def test_a_blank_page_is_surfaced_on_the_result(self, mock_s3, monkeypatch):
        result = self._invoke(mock_s3, monkeypatch, BLANK_SECOND)
        assert any("page 2 of 2" in v for v in result["page_violations"]), result

    def test_the_wrong_page_count_is_surfaced_on_the_result(self, mock_s3, monkeypatch):
        result = self._invoke(mock_s3, monkeypatch, BLANK_MIDDLE)
        assert any("3 pages" in v for v in result["page_violations"]), result

    def test_a_compliant_resume_carries_an_empty_flag(self, mock_s3, monkeypatch):
        result = self._invoke(mock_s3, monkeypatch, GOOD)
        assert result["page_violations"] == []

    def test_the_flag_is_always_present_so_absence_cannot_read_as_success(
        self, mock_s3, monkeypatch
    ):
        result = self._invoke(mock_s3, monkeypatch, GOOD)
        assert "page_violations" in result

    def test_the_pdf_is_still_uploaded_when_a_page_is_blank(self, mock_s3, monkeypatch):
        result = self._invoke(mock_s3, monkeypatch, BLANK_SECOND)
        assert result["pdf_s3_key"] == "users/u1/resumes/abc123_tailored.pdf"
        mock_s3.head_object(Bucket="utkarsh-job-hunt", Key=result["pdf_s3_key"])

    def test_violations_are_logged_at_error_level(self, mock_s3, monkeypatch, caplog):
        with caplog.at_level(logging.ERROR):
            self._invoke(mock_s3, monkeypatch, BLANK_SECOND)
        assert any(r.levelno == logging.ERROR for r in caplog.records)
        assert "page 2 of 2" in caplog.text

    def test_an_explicit_page_count_in_the_event_is_honoured(self, mock_s3, monkeypatch):
        result = self._invoke(mock_s3, monkeypatch, BLANK_MIDDLE, pages=3)
        assert not any("pages" in v and "policy" in v
                       for v in result["page_violations"]), result

    def test_a_cover_letter_has_no_page_count_asserted(self, mock_s3, monkeypatch):
        result = self._invoke(mock_s3, monkeypatch, BLANK_MIDDLE,
                              doc_type="cover_letter")
        assert not any("pages" in v and "policy" in v
                       for v in result["page_violations"]), result
        assert any("page 2 of 3" in v for v in result["page_violations"]), result
