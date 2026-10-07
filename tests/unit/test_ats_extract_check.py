"""Nothing ever checked whether our own PDF survives an ATS.

The pipeline validates the LaTeX source (braces, required sections, macro
arity) and the compiled PDF's page count, then ships a document whose entire
purpose is to be parsed by software nobody here has pointed at it. A resume can
be perfect in source and arrive at Workday as interleaved columns.

These tests compile REAL PDFs with tectonic, because the property under test is
what a PDF extractor does with a layout, and no fixture can tell you that. A
hand-written "extracted text" string would only confirm what its author already
believed (CLAUDE.md rule 5).

The two-column case matters most: this repo's layout is single-column, so the
order check passes today. A check whose first run is green needs proof it CAN
fail, or it is decoration (rule 6's cousin — prove the instrument works before
trusting its reading).
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.ats_extract_check import (  # noqa: E402
    MIN_EXTRACTED_CHARS,
    check_ats_extraction,
    extract_text,
)

pytestmark = pytest.mark.skipif(
    shutil.which("tectonic") is None, reason="tectonic not installed")

# Mirrors the real corpus preamble's font setup. Without T1/lmodern the
# default OT1 Computer Modern encoding extracts without word spaces --
# "SiteReliabilityEngineerwitheightyears..." -- which made a fixture look like
# a detector bug. The documents under test are the ones the pipeline produces,
# so the harness has to share their font stack or it measures something else.
PREAMBLE = (
    "\\documentclass[10pt,a4paper]{article}\n"
    "\\usepackage[utf8]{inputenc}\n"
    # cmap exists to make the PDF's text extractable -- without it the glyph
    # stream carries no character map and pdfplumber returns words run
    # together. The real corpus loads it; a harness that omits it is testing a
    # different document.
    "\\usepackage{cmap}\n"
    "\\usepackage[T1]{fontenc}\n"
    "\\usepackage{lmodern}\n"
    "\\usepackage[margin=0.6in]{geometry}\n"
    "\\usepackage{multicol}\n"
    "\\pagestyle{empty}\n"
    "\\raggedright\n"
    "\\begin{document}\n"
)

FILLER = (" Designed and operated a serverless platform across thirty-four "
          "functions, cutting p99 latency by forty percent and halving cost. ")


def _body(section, n=6):
    return f"\\section*{{{section}}}\n" + (FILLER * n) + "\n\n"


SINGLE_COLUMN = (
    PREAMBLE
    + "\\section*{Experience}\nAcme Corp \\hfill Jun 2022 - Jul 2024\n\n" + FILLER * 6 + "\n\n"
    + _body("Projects")
    + _body("Education")
    + _body("Certifications")
    + "\\end{document}\n"
)


def _compile(tex, tmp_path, name="doc"):
    f = tmp_path / f"{name}.tex"
    f.write_text(tex)
    r = subprocess.run(["tectonic", "-X", "compile", str(f), "--outdir", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-700:]
    return tmp_path / f"{name}.pdf"


def test_a_single_column_resume_passes(tmp_path):
    pdf = _compile(SINGLE_COLUMN, tmp_path)
    assert check_ats_extraction(pdf, SINGLE_COLUMN) == []


def test_extraction_actually_returns_the_text(tmp_path):
    """Guards the harness: if extraction returned nothing the suite would pass
    trivially on the negative cases."""
    pdf = _compile(SINGLE_COLUMN, tmp_path)
    text = extract_text(pdf)
    assert len(text) > MIN_EXTRACTED_CHARS
    assert "Experience" in text


def test_a_two_column_layout_is_caught(tmp_path):
    """The failure mode the check exists for, and proof it can fail at all.

    multicol flows sections side by side. It reads fine to a human and extracts
    as the sections in a different order from the source, which is exactly what
    an ATS would store.
    """
    two_col = (
        PREAMBLE
        + "\\begin{multicols}{2}\n"
        + _body("Experience", 10) + _body("Projects", 10)
        + _body("Education", 10) + _body("Certifications", 10)
        + "\\end{multicols}\n\\end{document}\n"
    )
    pdf = _compile(two_col, tmp_path, "twocol")
    violations = check_ats_extraction(pdf, two_col)
    assert violations, "a two-column layout produced no ATS violation"
    assert any("out of order" in v or "not findable" in v for v in violations), violations


def test_text_rendered_as_a_drawing_is_caught(tmp_path):
    """A PDF that LOOKS like a resume and extracts as nothing."""
    drawn = (PREAMBLE
             + "\\section*{Experience}\n"
             + "\\rule{10cm}{6cm}\n"
             + "\\end{document}\n")
    pdf = _compile(drawn, tmp_path, "drawn")
    violations = check_ats_extraction(pdf, drawn)
    assert any("may be an image" in v for v in violations), violations


def test_a_lost_date_range_is_reported(tmp_path):
    """Employment dates an ATS cannot parse become gaps on the timeline."""
    pdf = _compile(SINGLE_COLUMN, tmp_path)
    # The source claims a range the document does not contain.
    claimed = SINGLE_COLUMN.replace("\\end{document}",
                                    "% Jan 2015 - Dec 2016\n\\end{document}")
    violations = check_ats_extraction(pdf, claimed)
    assert any("date range" in v for v in violations), violations


def test_a_missing_section_is_reported_once_not_twice(tmp_path):
    """Order is judged over the sections that were FOUND, so a missing one is
    a missing-section violation and not also an ordering violation."""
    pdf = _compile(SINGLE_COLUMN, tmp_path)
    claimed = SINGLE_COLUMN.replace(
        "\\section*{Projects}", "\\section*{Publications}")
    violations = check_ats_extraction(pdf, claimed)
    assert any("not findable" in v for v in violations)
    assert not any("out of order" in v for v in violations), violations


def test_prose_mentioning_a_section_name_is_not_a_heading(tmp_path):
    """The false positive this check produced on its first real document.

    Production resume 4dd96462666e, single-column and correctly ordered, was
    reported as:

        claims:   summary, experience, technical skills, featured projects, ...
        document: summary, technical skills, experience, featured projects, ...

    because its summary paragraph reads "...years of experience...", and the
    first version located headings with `flat.index(name)` — the word anywhere
    in the document. An extractor puts a heading on its own line, so a line
    that IS the heading is the signal and a line that merely contains the word
    is prose. CLAUDE.md rule 16: measure a detector's false-positive rate
    before shipping it, not after.
    """
    tex = (
        PREAMBLE
        + "\\section*{Summary}\nSite Reliability Engineer with eight years of "
          "experience building platforms, and projects spanning education "
          "technology and certifications management.\n\n"
        + _body("Technical Skills")
        + "\\section*{Experience}\nAcme Corp \\hfill Jun 2022 - Jul 2024\n\n" + FILLER * 6 + "\n\n"
        + _body("Education")
        + _body("Certifications")
        + "\\end{document}\n"
    )
    pdf = _compile(tex, tmp_path, "prose")
    extracted = extract_text(pdf)
    assert "years of experience" in extracted, "fixture lost the prose mention"
    assert check_ats_extraction(pdf, tex) == [], (
        "prose mentioning a section name was read as that section's heading")
