"""Uploading a PDF must produce a TAILORABLE resume, or change nothing.

The owner's master resume exists only as a PDF; the LaTeX source is gone. The
pipeline rewrites and recompiles the document, so it needs LaTeX. Every piece
needed to bridge that already existed and was never connected:

    extract_text_from_pdf  ->  parse_resume_sections  ->  rebuild_tex_from_sections

The upload ran the first two and threw the result away, storing raw text in a
column named tex_content.

The trap, measured 2026-09-28 against the real resume: with no AI client the
parser returns only raw_text, and the renderer turns 16,953 characters into
1,807 characters of valid, EMPTY LaTeX. is_latex_document() returns True for
it, because it checks for \\documentclass and \\begin{document} — markers, not
substance. Storing that would have replaced a working resume with a blank one
and reported success.
"""
import pytest

from shared.resume_format import is_latex_document, sections_have_content

EMPTY_PARSE = {"raw_text": "x" * 16953}          # what the real failure produced
REAL_PARSE = {
    "raw_text": "...",
    "header": {"name": "Utkarsh Singh"},
    "summary": "Full-stack software engineer with 3+ years...",
    "skills": ["Python", "AWS", "Kubernetes"],
    "experience": [{"company": "Clover IT Services", "bullets": ["Owned..."]}],
}


def test_the_measured_failure_is_rejected():
    """16,953 chars in, only raw_text out — must not be accepted."""
    assert sections_have_content(EMPTY_PARSE) is False


def test_a_real_parse_is_accepted():
    assert sections_have_content(REAL_PARSE) is True


@pytest.mark.parametrize("sections", [None, {}, {"raw_text": ""}, {"summary": "   "},
                                      {"skills": []}, {"experience": []}])
def test_nothing_substantive_is_rejected(sections):
    assert sections_have_content(sections) is False


@pytest.mark.parametrize("key,value", [
    ("summary", "A real summary."),
    ("skills", ["Python"]),
    ("experience", [{"company": "X"}]),
    ("projects", [{"name": "Y"}]),
    ("education", [{"school": "Z"}]),
])
def test_any_one_substantive_section_is_enough(key, value):
    """A resume with only work history, or only skills, is still a resume."""
    assert sections_have_content({"raw_text": "...", key: value}) is True


def test_structural_validity_is_not_content():
    """The whole reason sections_have_content exists: this passes the LaTeX
    check and contains nothing."""
    empty_doc = r"\documentclass{article}\begin{document}\end{document}"
    assert is_latex_document(empty_doc) is True
    assert sections_have_content({"raw_text": "..."}) is False


def test_a_tex_upload_needs_no_conversion():
    real_tex = r"\documentclass{article}" "\n" r"\begin{document}" "\nHi\n" r"\end{document}"
    assert is_latex_document(real_tex) is True
