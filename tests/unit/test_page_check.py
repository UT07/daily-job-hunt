r"""The two-page rule, measured against a compiled PDF.

`tailor_resume.py` has told the model "The resume MUST be exactly TWO PAGES."
since it was written, and nothing read the PDF. A resume shipped with a blank
page in the middle and passed every gate. Entry counts are countable from the
LaTeX source and `shared/composition_policy.check_output` now counts them; page
count is not — it only exists once the document is typeset.

THE FIXTURES ARE REAL COMPILED OUTPUT. Each `.pdf` in tests/fixtures/pdf was
produced from the `.tex` beside it by the same engine production uses:

    tectonic -X compile --outdir tests/fixtures/pdf tests/fixtures/pdf/<name>.tex

and each `.tex` is `resumes/fullstack.tex` with one transformation applied:

    two_page_ok             projects trimmed to the policy cap of 3
    two_page_blank_second   every \projectentry removed, so line 84's
                            \clearpage fires with nothing behind it
    three_page_blank_middle same, with Education/Certifications after it —
                            the shape the user reported
    four_page_empty_middle  a page break with nothing extractable at all

They are committed because CI installs requirements.txt, which has pdfplumber
but no LaTeX engine (tectonic is downloaded only in deploy.yml, for the Lambda
layer). Reading a real artifact beats building a PDF double that might fail the
way the bug fails.

MEASURED, 2026-09-30, non-space extractable characters per page:

    two_page_ok.pdf              3678  2538
    two_page_blank_second.pdf    3678    16            <- page count is CORRECT
    three_page_blank_middle.pdf  3678    16   823
    four_page_empty_middle.pdf   3678   616     0  823
    resumes/fullstack.tex (the master corpus, 3 pages)
                                 3678  3650   106
    resumes/sre_devops.tex (also 3 pages)
                                 3713   395  3282
    fullstack minus one certification bullet
                                 3678  3650    54

The floor lives in the gap between those two groups: the largest degenerate
page measured is 16 characters ("FeaturedProjects", a heading alone), and the
thinnest page carrying real content is 54 (one certification bullet).
"""
import logging
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "pdf"

from shared.page_check import (  # noqa: E402
    MIN_PAGE_CHARS,
    check_pdf,
    describe_violations,
    page_text_lengths,
)

# What each fixture measured when it was committed. Asserted below so a
# regenerated fixture cannot quietly stop being the document these tests
# believe it is.
MEASURED = {
    "two_page_ok.pdf": [3678, 2538],
    "two_page_blank_second.pdf": [3678, 16],
    "three_page_blank_middle.pdf": [3678, 16, 823],
    "four_page_empty_middle.pdf": [3678, 616, 0, 823],
}


# ---------------------------------------------------------------------------
#  The fixtures are what they claim to be
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(MEASURED))
def test_fixture_exists(name):
    assert (FIXTURES / name).is_file(), f"missing fixture {name}"


@pytest.mark.parametrize("name,expected", sorted(MEASURED.items()))
def test_fixture_still_measures_what_it_did_when_committed(name, expected):
    """A fixture that has drifted must fail loudly, not quietly pass."""
    assert page_text_lengths(FIXTURES / name) == expected


# ---------------------------------------------------------------------------
#  page_text_lengths
# ---------------------------------------------------------------------------

def test_page_text_lengths_counts_every_page():
    lengths = page_text_lengths(FIXTURES / "three_page_blank_middle.pdf")
    assert len(lengths) == 3


def test_page_text_lengths_reports_zero_for_a_page_with_no_text():
    """pdfplumber returns None, not "", for a page with nothing on it."""
    assert page_text_lengths(FIXTURES / "four_page_empty_middle.pdf")[2] == 0


# ---------------------------------------------------------------------------
#  The threshold is justified by the two measurements it sits between
# ---------------------------------------------------------------------------

def test_floor_sits_between_the_measured_degenerate_and_legitimate_pages():
    # 16 = "Featured Projects" alone, the largest blank page measured.
    # 54 = one real certification bullet, the thinnest real page measured.
    assert 16 < MIN_PAGE_CHARS < 54


# ---------------------------------------------------------------------------
#  A compliant resume passes
# ---------------------------------------------------------------------------

def test_a_two_page_resume_with_content_on_both_pages_passes():
    assert check_pdf(FIXTURES / "two_page_ok.pdf", expected_pages=2) == []


# ---------------------------------------------------------------------------
#  The bug: right page count, blank page
# ---------------------------------------------------------------------------

def test_page_count_alone_would_not_have_caught_the_blank_page():
    """two_page_blank_second.pdf IS two pages. That is the whole point."""
    assert len(page_text_lengths(FIXTURES / "two_page_blank_second.pdf")) == 2
    violations = check_pdf(FIXTURES / "two_page_blank_second.pdf", expected_pages=2)
    assert violations, "a blank second page was reported as compliant"
    assert not any("pages" in v and "policy" in v for v in violations), (
        f"page count should not be a violation here: {violations}"
    )


def test_blank_page_violation_names_the_page_and_the_count():
    violations = check_pdf(FIXTURES / "two_page_blank_second.pdf", expected_pages=2)
    assert len(violations) == 1
    assert "page 2 of 2" in violations[0]
    assert "16" in violations[0]


def test_blank_middle_page_is_caught_alongside_the_wrong_page_count():
    violations = check_pdf(FIXTURES / "three_page_blank_middle.pdf", expected_pages=2)
    assert len(violations) == 2
    assert any("3 pages" in v and "2" in v for v in violations)
    assert any("page 2 of 3" in v for v in violations)


def test_a_page_with_nothing_extractable_is_caught():
    violations = check_pdf(FIXTURES / "four_page_empty_middle.pdf", expected_pages=2)
    assert any("page 3 of 4" in v and "0 characters" in v for v in violations)


# ---------------------------------------------------------------------------
#  Scope: the corpus is not a tailored resume
# ---------------------------------------------------------------------------

def test_page_count_is_not_asserted_when_the_caller_gives_no_expectation():
    """A master corpus may legitimately run longer than a tailored resume.

    Callers that hold no page policy get the blank-page floor only, rather
    than a page-count violation invented from a default.
    """
    violations = check_pdf(FIXTURES / "three_page_blank_middle.pdf")
    assert not any("pages" in v and "policy" in v for v in violations)
    assert any("page 2 of 3" in v for v in violations), (
        "the blank-page floor must still run without a page expectation"
    )


def test_policy_supplies_the_page_count_when_no_override_is_given():
    from shared.composition_policy import resolve

    assert resolve(None)["pages"] == 2
    violations = check_pdf(FIXTURES / "three_page_blank_middle.pdf", policy={})
    assert any("3 pages" in v for v in violations)


def test_an_explicit_expectation_beats_the_policy():
    violations = check_pdf(
        FIXTURES / "three_page_blank_middle.pdf", policy={}, expected_pages=3
    )
    assert not any("pages" in v and "policy" in v for v in violations)


# ---------------------------------------------------------------------------
#  A check that cannot run says so
# ---------------------------------------------------------------------------

def test_a_missing_pdf_is_a_violation_not_silence():
    violations = check_pdf(FIXTURES / "does_not_exist.pdf", expected_pages=2)
    assert violations
    assert "could not" in violations[0]


def test_an_unreadable_pdf_is_a_violation_not_silence(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\nthis is not a pdf\n")
    violations = check_pdf(broken, expected_pages=2)
    assert violations
    assert "could not" in violations[0]


def test_the_check_reports_its_own_absence_rather_than_passing(monkeypatch):
    """utils/pdf_validator.py degrades to valid=True when pymupdf is missing.

    That is why its page-count check has never run in production. This one
    reports a violation instead: an uninstalled checker is a failed check,
    not a passed one.
    """
    import shared.page_check as page_check

    monkeypatch.setattr(page_check, "pdfplumber", None)
    violations = check_pdf(FIXTURES / "two_page_ok.pdf", expected_pages=2)
    assert violations
    assert "pdfplumber" in violations[0]


# ---------------------------------------------------------------------------
#  describe_violations — the repair instruction carries the numbers
# ---------------------------------------------------------------------------

def test_describe_violations_is_empty_when_there_is_nothing_to_say():
    assert describe_violations([]) == ""


def test_describe_violations_repeats_the_measured_numbers():
    violations = check_pdf(FIXTURES / "three_page_blank_middle.pdf", expected_pages=2)
    text = describe_violations(violations)
    assert "3 pages" in text
    assert "page 2 of 3" in text


# ---------------------------------------------------------------------------
#  Logging
# ---------------------------------------------------------------------------

def test_violations_are_logged_at_error_level(caplog):
    from shared.page_check import log_violations

    with caplog.at_level(logging.ERROR):
        log_violations(
            check_pdf(FIXTURES / "two_page_blank_second.pdf", expected_pages=2),
            label="job-abc123",
        )
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert "job-abc123" in caplog.text
    assert "page 2 of 2" in caplog.text


def test_nothing_is_logged_for_a_compliant_pdf(caplog):
    from shared.page_check import log_violations

    with caplog.at_level(logging.WARNING):
        log_violations(check_pdf(FIXTURES / "two_page_ok.pdf", expected_pages=2),
                       label="job-ok")
    assert caplog.records == []
